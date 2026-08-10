"""Structure CPython 3.11 exception-table regions as standard AST nodes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Tuple

from decompyle3.controlflow.cfg import instruction_target
from decompyle3.parsers.p311.base import Python311ParseError, _IGNORED_INTERNAL


@dataclass(frozen=True)
class ExceptionState311:
    """One logical exception-state object used while decoding a handler."""

    handler_offset: int
    depth: int
    lasti: bool


@dataclass(frozen=True)
class _HandlerTerminalTransfer:
    """One CFG-proven control transfer owned by an except clause."""

    kind: str
    body_start: int
    body_end: int
    cleanup_start: int
    return_load_index: int
    return_index: int
    continuation_offset: int
    protocol_offsets: FrozenSet[int]


@dataclass(frozen=True)
class _ProtectedLoopTerminalFrontier:
    """CFG proof for non-raising loop transfers after a protected region."""

    end_index: int
    owned_blocks: FrozenSet[int]
    return_blocks: FrozenSet[int]
    continue_blocks: FrozenSet[int]
    break_blocks: FrozenSet[int]
    cleanup_offsets: FrozenSet[int]


class ExceptionStructureDecompiler311:
    """Recover try and with statements from protected ranges and handlers."""

    def __init__(self, owner):
        self.owner = owner
        self.tokens = owner.tokens
        self.offset_to_index = owner.offset_to_index
        self.entries = owner.exception_regions

    def _error(self, message, offset=None):
        if offset is None:
            token = self.owner.current_token
            offset = token.offset if token is not None else "?"
        raise Python311ParseError(
            f"{message} ({self.owner.code.co_name!r}, offset {offset})",
            version=(3, 11),
            code_name=self.owner.code.co_name,
            offset=offset if isinstance(offset, int) else None,
        )

    def _handler_has_match(self, handler_index: int) -> bool:
        handler_offset = self.tokens[handler_index].offset
        protected_ends = [
            self.offset_to_index[entry.end]
            for entry in self.entries
            if entry.start == handler_offset and entry.lasti
        ]
        probe_end = min(protected_ends) if protected_ends else len(self.tokens)
        for token in self.tokens[handler_index + 1 : probe_end]:
            if token.kind == "CHECK_EXC_MATCH":
                return True
            if token.kind in ("RERAISE", "RETURN_VALUE"):
                return False
        return False

    def _handler_has_group_match(self, handler_index: int) -> bool:
        handler_offset = self.tokens[handler_index].offset
        protected_ends = [
            self.offset_to_index[entry.end]
            for entry in self.entries
            if entry.start == handler_offset and entry.lasti
        ]
        probe_end = min(protected_ends) if protected_ends else len(self.tokens)
        for token in self.tokens[handler_index + 1 : probe_end]:
            if token.kind == "CHECK_EG_MATCH":
                return True
            if token.kind in ("RERAISE", "RETURN_VALUE"):
                return False
        return False

    def _handler_is_bare(self, entry) -> bool:
        handler_index = self.offset_to_index[entry.target]
        if not (
            handler_index + 1 < len(self.tokens)
            and self.tokens[handler_index].kind == "PUSH_EXC_INFO"
            and self.tokens[handler_index + 1].kind == "POP_TOP"
        ):
            return False
        ambiguous_transfer = (
            handler_index + 3 < len(self.tokens)
            and self.tokens[handler_index + 2].kind == "POP_EXCEPT"
            and (
                self.tokens[handler_index + 3].kind == "POP_TOP"
                or self.tokens[handler_index + 3].kind.startswith(
                    "JUMP_BACKWARD"
                )
            )
        )
        if not ambiguous_transfer:
            return True

        normal_index = self.offset_to_index.get(entry.end)
        if normal_index is not None and any(
            self.tokens[index].kind == "RETURN_VALUE"
            for index in range(normal_index, handler_index)
        ):
            # ``finally: continue`` overrides a pending return, so CPython
            # does not emit a RETURN_VALUE in its duplicated normal cleanup.
            # A surviving loop-iterator return before this unconditional
            # handler continue therefore belongs to ``except: continue``.
            return True

        # ``except: continue/break`` and ``finally: continue/break`` have
        # the same exceptional cleanup prefix.  A real except region has a
        # normal edge that jumps forward around the handler.  In a finally
        # region, normal execution instead runs a duplicated copy of the
        # cleanup/control transfer before reaching the exceptional handler.
        if normal_index is None:
            return False
        normal_index = self.owner._next_semantic_index(
            normal_index,
            handler_index,
        )
        if normal_index >= handler_index:
            return False
        normal_exit = self.tokens[normal_index]
        normal_target = instruction_target(normal_exit)
        return (
            normal_exit.kind == "JUMP_FORWARD"
            and normal_target is not None
            and normal_target > entry.target
        )

    def _remember_exception_state(self, entry) -> ExceptionState311:
        state = ExceptionState311(
            handler_offset=entry.target,
            depth=entry.depth,
            lasti=entry.lasti,
        )
        previous = self.owner.exception_states.setdefault(
            state.handler_offset,
            state,
        )
        if previous != state:
            self._error(
                "Handler target has inconsistent exception-stack state"
            )
        return state

    def _capture_optional(
        self,
        start: int,
        end: int,
        loop,
        trailing_return: bool = False,
    ) -> List[ast.stmt]:
        if start >= end:
            return []
        return self.owner._capture_region(
            start,
            end,
            loop,
            trailing_return=trailing_return,
        )

    def _capture_protected(
        self,
        start: int,
        end: int,
        loop,
    ) -> List[ast.stmt]:
        offset = self.tokens[start].offset
        self.owner._suppressed_exception_starts.add(offset)
        try:
            return self._capture_optional(start, end, loop)
        finally:
            self.owner._suppressed_exception_starts.remove(offset)

    def _capture_protected_fragments(
        self,
        start: int,
        end: int,
        loop,
        fragments,
        trailing_return: bool = False,
    ) -> List[ast.stmt]:
        """Capture one logical try suite split into table fragments."""
        offsets = {entry.start for entry in fragments}
        added = offsets - self.owner._suppressed_exception_starts
        self.owner._suppressed_exception_starts.update(added)
        try:
            return self._capture_optional(
                start,
                end,
                loop,
                trailing_return=trailing_return,
            )
        finally:
            self.owner._suppressed_exception_starts.difference_update(added)

    def _capture_protected_return(
        self,
        start: int,
        end: int,
        loop,
    ) -> Optional[List[ast.stmt]]:
        """Recover a return value kept on the VM stack across ``finally``."""
        expression_start = self.owner._latch_expression_start(start, end)
        if expression_start >= end:
            await_index = next(
                (
                    index
                    for index in range(end - 1, start - 1, -1)
                    if self.tokens[index].kind == "GET_AWAITABLE"
                ),
                None,
            )
            if await_index is not None:
                operand_start = self.owner._latch_expression_start(
                    start,
                    await_index,
                )
                if operand_start < await_index:
                    offset = self.tokens[start].offset
                    self.owner._suppressed_exception_starts.add(offset)
                    try:
                        try:
                            body = self._capture_optional(
                                start,
                                operand_start,
                                loop,
                            )
                            value = self.owner._expression_slice(
                                operand_start,
                                await_index,
                            )
                        except Python311ParseError:
                            body = None
                    finally:
                        self.owner._suppressed_exception_starts.remove(
                            offset
                        )
                    if body is not None:
                        body.append(
                            ast.Return(value=ast.Await(value=value))
                        )
                        return body
            yield_index = next(
                (
                    index
                    for index in range(end - 1, start - 1, -1)
                    if self.tokens[index].kind == "YIELD_VALUE"
                ),
                None,
            )
            if yield_index is None:
                return None
            cursor = yield_index + 1
            if (
                cursor < end
                and self.tokens[cursor].kind == "INTERNAL_RESUME"
            ):
                cursor += 1
            if cursor != end:
                return None
            expression_start = self.owner._latch_expression_start(
                start,
                yield_index,
            )
            if expression_start >= yield_index:
                return None
            offset = self.tokens[start].offset
            self.owner._suppressed_exception_starts.add(offset)
            try:
                try:
                    body = self._capture_optional(
                        start,
                        expression_start,
                        loop,
                    )
                    value = self.owner._expression_slice(
                        expression_start,
                        yield_index,
                    )
                except Python311ParseError:
                    return None
            finally:
                self.owner._suppressed_exception_starts.remove(offset)
            body.append(
                ast.Return(value=ast.Yield(value=value))
            )
            return body

        offset = self.tokens[start].offset
        self.owner._suppressed_exception_starts.add(offset)
        try:
            body = None
            expression = None
            for candidate in range(expression_start, end):
                try:
                    candidate_expression = self.owner._expression_slice(
                        candidate,
                        end,
                    )
                    candidate_body = self._capture_optional(
                        start,
                        candidate,
                        loop,
                    )
                except Python311ParseError:
                    continue
                expression = candidate_expression
                body = candidate_body
                break
            if body is None or expression is None:
                return None
        finally:
            self.owner._suppressed_exception_starts.remove(offset)
        body.append(ast.Return(value=expression))
        return body

    def _capture_suppressed(
        self,
        start: int,
        end: int,
        loop,
    ) -> List[ast.stmt]:
        offsets = {
            entry.start
            for entry in self.entries
            if start <= self.offset_to_index[entry.start] < end
        }
        added = offsets - self.owner._suppressed_exception_starts
        self.owner._suppressed_exception_starts.update(added)
        try:
            return self._capture_optional(start, end, loop)
        finally:
            self.owner._suppressed_exception_starts.difference_update(added)

    def _capture_before_handler(
        self,
        start: int,
        end: int,
        handler_offset: int,
        loop,
    ) -> List[ast.stmt]:
        """Capture normal code while hiding an enclosing cleanup region."""
        offsets = {
            entry.start
            for entry in self.entries
            if start <= self.offset_to_index[entry.start] < end
            and entry.target > handler_offset
        }
        added = offsets - self.owner._suppressed_exception_starts
        handler_added = (
            handler_offset
            not in self.owner._suppressed_exception_handler_targets
        )
        self.owner._suppressed_exception_starts.update(added)
        self.owner._suppressed_exception_handler_targets.add(
            handler_offset
        )
        try:
            try:
                return self._capture_optional(start, end, loop)
            except Python311ParseError as error:
                if not hasattr(error, "shape_hint"):
                    error.shape_hint = (
                        "realworld_exception_cleanup_control_transfer"
                    )
                raise
        finally:
            self.owner._suppressed_exception_starts.difference_update(added)
            if handler_added:
                self.owner._suppressed_exception_handler_targets.remove(
                    handler_offset
                )

    def _normal_completion_entry(
        self,
        fragments,
        normal_jump: int,
        handler_index: int,
    ) -> Optional[int]:
        """Find the protected exit that reaches the handler-skipping jump."""
        protected_blocks = {
            block.index
            for block in self.owner.cfg.blocks
            if any(
                fragment.start <= block.start < fragment.end
                for fragment in fragments
            )
        }
        normal_jump_offset = self.tokens[normal_jump].offset
        normal_jump_block = self.owner.cfg.offset_to_block.get(
            normal_jump_offset
        )
        if not protected_blocks or normal_jump_block is None:
            return None

        candidates = {
            edge.target
            for block_index in protected_blocks
            for edge in self.owner._normal_edges_from(block_index)
            if edge.target not in protected_blocks
            and self.owner.cfg.block(edge.target).start
            <= normal_jump_offset
        }
        if not candidates:
            return None

        handler_offset = self.tokens[handler_index].offset

        def reaches_normal_jump(start_block: int) -> bool:
            pending = [start_block]
            visited = set()
            work_limit = max(32, len(self.owner.cfg.blocks) * 4)
            work_count = 0
            while pending:
                work_count += 1
                if work_count > work_limit:
                    return False
                block_index = pending.pop()
                if block_index == normal_jump_block:
                    return True
                if block_index in visited:
                    continue
                visited.add(block_index)
                if block_index in protected_blocks:
                    # This is a non-raising gap between exception-table
                    # fragments, not code which completed the logical try.
                    # A real normal-completion suite cannot leave the
                    # protected body and then re-enter one of its fragments.
                    continue
                block = self.owner.cfg.block(block_index)
                # A source ``else`` may contain its own closed try/with
                # protocol.  Its handled exception edges are part of the
                # path to the outer handler-skipping jump, while an edge to
                # the outer handler itself remains excluded by the bound.
                for edge in self.owner.cfg.outgoing(block_index):
                    target = self.owner.cfg.block(edge.target)
                    if not block.start < target.start < handler_offset:
                        continue
                    pending.append(edge.target)
            return False

        viable = {
            block_index
            for block_index in candidates
            if reaches_normal_jump(block_index)
        }
        if len(viable) != 1:
            return None
        entry_offset = self.owner.cfg.block(viable.pop()).start
        return self.offset_to_index.get(entry_offset)

    def _protected_terminal_return_frontier_end(
        self,
        fragments,
        body_end: int,
        handler_index: int,
    ) -> Optional[int]:
        """Own constant-return sinks reached only by the protected body.

        CPython may stop protecting the final, non-raising ``LOAD_CONST`` /
        ``RETURN_VALUE`` pairs before the exception handler starts.  Those
        blocks are still terminal exits of the source ``try`` body; treating
        them as a normal-completion suite invents ``try/else``.  Keep this
        proof deliberately narrow: the complete physical gap must consist of
        one or more constant-return blocks and every normal predecessor must
        already belong to the protected region (or to the proven frontier).
        """
        if not 0 <= body_end < handler_index:
            return None

        return_blocks = set()
        cursor = body_end
        while cursor < handler_index:
            while (
                cursor < handler_index
                and self.tokens[cursor].kind in _IGNORED_INTERNAL
            ):
                cursor += 1
            if cursor >= handler_index:
                break
            if (
                cursor + 1 >= handler_index
                or self.tokens[cursor].kind != "LOAD_CONST"
                or self.tokens[cursor + 1].kind != "RETURN_VALUE"
            ):
                return None
            block_index = self.owner.cfg.offset_to_block.get(
                self.tokens[cursor].offset
            )
            if (
                block_index is None
                or self.owner.cfg.block(block_index).start
                != self.tokens[cursor].offset
                or self.owner.cfg.block(block_index).terminator
                != "RETURN_VALUE"
                or self.owner._normal_edges_from(block_index)
            ):
                return None
            return_blocks.add(block_index)
            cursor += 2
        if not return_blocks:
            return None

        protected_blocks = set()
        for block in self.owner.cfg.blocks:
            for fragment in fragments:
                if fragment.start <= block.start < fragment.end:
                    protected_blocks.add(block.index)
                    break
        has_protected_entry = False
        owned_blocks = protected_blocks | return_blocks
        for return_block in return_blocks:
            incoming = self.owner.cfg.incoming(return_block)
            if any(edge.kind == "exception" for edge in incoming):
                return None
            normal_incoming = [
                edge for edge in incoming if edge.kind != "exception"
            ]
            if not normal_incoming or any(
                edge.source not in owned_blocks for edge in normal_incoming
            ):
                return None
            has_protected_entry = has_protected_entry or any(
                edge.source in protected_blocks for edge in normal_incoming
            )
        return handler_index if has_protected_entry else None

    def _protected_loop_terminal_frontier(
        self,
        fragments,
        body_end: int,
        handler_index: int,
        loop,
    ) -> Optional[_ProtectedLoopTerminalFrontier]:
        """Own loop return/break/continue sinks after protected fragments.

        CPython leaves non-raising iterator cleanup and control transfers out
        of exception-table ranges.  They still belong to the source try body
        when every physical gap block is a proven terminal transfer reached
        only from that protected body.  Reject the candidate on any business
        instruction, foreign predecessor, exception edge, or loop target
        mismatch.
        """
        if loop is None or not 0 <= body_end < handler_index:
            return None

        lower = self.tokens[body_end].offset
        upper = self.tokens[handler_index].offset
        first_block = self.owner.cfg.offset_to_block.get(lower)
        if (
            first_block is None
            or self.owner.cfg.block(first_block).start != lower
        ):
            return None

        frontier_blocks = {
            block.index
            for block in self.owner.cfg.blocks
            if lower <= block.start < upper
        }
        if not frontier_blocks:
            return None
        if any(
            self.owner.cfg.block(block_index).end > upper
            for block_index in frontier_blocks
        ):
            return None

        return_blocks = set()
        continue_blocks = set()
        break_blocks = set()
        cleanup_offsets = set()

        for block_index in sorted(
            frontier_blocks,
            key=lambda index: self.owner.cfg.block(index).start,
        ):
            block = self.owner.cfg.block(block_index)
            start_index = self.offset_to_index.get(block.start)
            end_index = self.offset_to_index.get(block.end)
            if start_index is None or end_index is None:
                return None
            semantic = [
                self.tokens[index]
                for index in range(start_index, end_index)
                if self.tokens[index].kind not in _IGNORED_INTERNAL
            ]
            if not semantic:
                return None
            incoming = self.owner.cfg.incoming(block_index)
            outgoing = self.owner.cfg.outgoing(block_index)
            if any(
                edge.kind == "exception"
                for edge in incoming + outgoing
            ):
                return None
            normal_outgoing = self.owner._normal_edges_from(block_index)

            pop_count = 0
            while (
                pop_count < len(semantic)
                and semantic[pop_count].kind == "POP_TOP"
            ):
                cleanup_offsets.add(semantic[pop_count].offset)
                pop_count += 1
            remaining = semantic[pop_count:]

            if (
                len(remaining) == 2
                and remaining[0].kind == "LOAD_CONST"
                and remaining[1].kind == "RETURN_VALUE"
                and block.terminator == "RETURN_VALUE"
                and not normal_outgoing
            ):
                return_blocks.add(block_index)
                continue

            if (
                pop_count == 0
                and len(remaining) == 1
                and remaining[0].kind
                in ("JUMP_BACKWARD", "JUMP_BACKWARD_NO_INTERRUPT")
                and instruction_target(remaining[0])
                in loop.continue_targets
                and block.terminator == remaining[0].kind
                and len(normal_outgoing) == 1
                and self.owner.cfg.block(normal_outgoing[0].target).start
                == instruction_target(remaining[0])
            ):
                continue_blocks.add(block_index)
                continue

            if (
                len(remaining) == 1
                and remaining[0].kind == "JUMP_FORWARD"
                and instruction_target(remaining[0]) == loop.break_target
                and block.terminator == "JUMP_FORWARD"
                and len(normal_outgoing) == 1
                and self.owner.cfg.block(normal_outgoing[0].target).start
                == loop.break_target
            ):
                break_blocks.add(block_index)
                continue

            return None

        if not return_blocks and not continue_blocks and not break_blocks:
            return None

        protected_blocks = set()
        for block in self.owner.cfg.blocks:
            if any(
                fragment.start <= block.start < fragment.end
                for fragment in fragments
            ):
                protected_blocks.add(block.index)
        if not protected_blocks:
            return None

        owned_blocks = protected_blocks | frontier_blocks
        entry_blocks = set()
        for block_index in frontier_blocks:
            incoming = self.owner.cfg.incoming(block_index)
            normal_incoming = [
                edge for edge in incoming if edge.kind != "exception"
            ]
            if not normal_incoming or any(
                edge.source not in owned_blocks for edge in normal_incoming
            ):
                return None
            if any(
                edge.source in protected_blocks for edge in normal_incoming
            ):
                entry_blocks.add(block_index)
        if not entry_blocks:
            return None

        pending = list(entry_blocks)
        visited = set()
        work_limit = max(32, len(self.owner.cfg.blocks) * 2)
        work_count = 0
        while pending:
            work_count += 1
            if work_count > work_limit:
                return None
            block_index = pending.pop()
            if block_index in visited:
                continue
            if block_index not in frontier_blocks:
                return None
            visited.add(block_index)
            pending.extend(
                edge.target
                for edge in self.owner._normal_edges_from(block_index)
                if edge.target in frontier_blocks
            )
        if visited != frontier_blocks:
            return None

        classified = return_blocks | continue_blocks | break_blocks
        if classified != frontier_blocks:
            return None
        return _ProtectedLoopTerminalFrontier(
            end_index=handler_index,
            owned_blocks=frozenset(frontier_blocks),
            return_blocks=frozenset(return_blocks),
            continue_blocks=frozenset(continue_blocks),
            break_blocks=frozenset(break_blocks),
            cleanup_offsets=frozenset(cleanup_offsets),
        )

    def _enclosing_fragmented_handler(
        self,
        entry,
        next_index: int,
    ):
        """Find an outer handler whose protected fragment owns continuation."""
        if not 0 <= next_index < len(self.tokens):
            return None
        continuation_offset = self.tokens[next_index].offset
        covering = []
        for candidate in self.entries:
            if (
                candidate.target != entry.target
                and candidate.depth == entry.depth
                and candidate.lasti == entry.lasti
                and candidate.start
                not in self.owner._suppressed_exception_starts
                and candidate.start <= continuation_offset < candidate.end
                and candidate.target > continuation_offset
                and candidate.target
                not in self.owner._suppressed_exception_handler_targets
                and self.tokens[
                    self.offset_to_index[candidate.target]
                ].kind
                == "PUSH_EXC_INFO"
                and (
                    self._handler_has_match(
                        self.offset_to_index[candidate.target]
                    )
                    or self._handler_is_bare(candidate)
                )
            ):
                covering.append(candidate)
        targets = set()
        for candidate in covering:
            targets.add(candidate.target)
        if len(targets) != 1:
            return None
        target = targets.pop()
        fragments = []
        for candidate in self.entries:
            if (
                candidate.target == target
                and candidate.depth == entry.depth
                and candidate.lasti == entry.lasti
                and candidate.start < target
            ):
                fragments.append(candidate)
        if not fragments:
            return None
        return target, fragments

    def _capture_deferred_return_finally(
        self,
        start: int,
        end: int,
        handler_offset: int,
        loop,
        protected_statement: ast.stmt,
    ) -> List[ast.stmt]:
        """Capture a finally copy that consumes an earlier return value."""
        has_protected_return = any(
            isinstance(node, ast.Return)
            for node in ast.walk(protected_statement)
        )
        protocol_offsets = set()
        if has_protected_return:
            for index in range(start, end):
                if self.tokens[index].kind != "RETURN_VALUE":
                    continue
                swap_index = index - 2
                finally_overrides_return = (
                    swap_index >= start
                    and self.tokens[swap_index].kind == "SWAP_STACK"
                    and self.tokens[swap_index].attr == 2
                    and self.tokens[swap_index + 1].kind == "POP_TOP"
                )
                if not finally_overrides_return:
                    protocol_offsets.add(self.tokens[index].offset)
                    if (
                        index > start
                        and self.tokens[index - 1].kind == "LOAD_CONST"
                        and self.tokens[index - 1].attr is None
                    ):
                        protocol_offsets.add(
                            self.tokens[index - 1].offset
                        )

        added = (
            protocol_offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        self.owner._suppressed_exception_protocol_offsets.update(added)
        try:
            return self._capture_before_handler(
                start,
                end,
                handler_offset,
                loop,
            )
        finally:
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added
            )

    def _handler_protocol_offsets(
        self,
        start: int,
        end: int,
        name: Optional[str],
    ):
        """Return non-source exception-stack operations in one clause body."""
        offsets = set()
        for index in range(start, end):
            token = self.tokens[index]
            if token.kind != "POP_EXCEPT":
                continue
            offsets.add(token.offset)

            # Returning a value while an exception is active rotates that
            # value below the saved exception state before POP_EXCEPT.  The
            # rotation belongs to the handler protocol, not to the source
            # expression.
            marker = index - 1
            if (
                marker >= start
                and self.tokens[marker].kind == "SWAP_STACK"
            ):
                offsets.add(self.tokens[marker].offset)

            # CPython clears ``except ... as name`` bindings on every normal
            # exit.  The synthetic None assignment and deletion must not
            # appear in the recovered handler body.
            cursor = index + 1
            if name is None or cursor + 2 >= end:
                continue
            load, store, delete = self.tokens[cursor : cursor + 3]
            stored_name = (
                store.attr if isinstance(store.attr, str) else store.pattr
            )
            deleted_name = (
                delete.attr
                if isinstance(delete.attr, str)
                else delete.pattr
            )
            if (
                load.kind == "LOAD_CONST"
                and load.attr is None
                and store.kind.startswith("STORE_")
                and delete.kind.startswith("DELETE_")
                and stored_name == name
                and deleted_name == name
            ):
                offsets.update(
                    (load.offset, store.offset, delete.offset)
                )
        return offsets

    @staticmethod
    def _protocol_token_shape(token):
        if instruction_target(token) is not None:
            return (token.kind,)
        if token.kind == "INTERNAL_EXTENDED_ARG":
            return (token.kind,)
        if token.kind in ("CALL", "CALL_FUNCTION_EX"):
            call = token.attr
            return (
                token.kind,
                getattr(call, "argc", None),
                getattr(call, "positional_count", None),
                getattr(call, "keyword_names", None),
                getattr(call, "is_method", None),
                getattr(call, "has_null", None),
                getattr(call, "has_self", None),
                getattr(call, "receiver_mode", None),
                getattr(call, "uses_ex", None),
                getattr(call, "has_starargs", None),
                getattr(call, "has_kwargs", None),
            )
        return token.kind, token.attr, token.pattr

    def _exceptional_finally_body(self, handler_offset: int):
        """Return the simple source suite in an exceptional finally copy."""
        handler_index = self.offset_to_index[handler_offset]
        if self.tokens[handler_index].kind != "PUSH_EXC_INFO":
            return None
        reraise_index = next(
            (
                index
                for index in range(handler_index + 1, len(self.tokens))
                if self.tokens[index].kind == "RERAISE"
            ),
            None,
        )
        if (
            reraise_index is None
            or reraise_index == handler_index + 1
            or any(
                self.tokens[index].kind == "PUSH_EXC_INFO"
                for index in range(handler_index + 1, reraise_index)
            )
            or any(
                self.tokens[handler_index + 1].offset
                <= entry.start
                < self.tokens[reraise_index].offset
                for entry in self.entries
            )
        ):
            return None
        return handler_index + 1, reraise_index

    def _duplicated_finally_offsets(
        self,
        start: int,
        end: int,
        handler_offset: int,
    ):
        """Find normal-path copies of one simple exceptional finally suite."""
        exceptional = self._exceptional_finally_body(handler_offset)
        if exceptional is None:
            return set()
        body_start, body_end = exceptional
        signature = []
        for token in self.tokens[body_start:body_end]:
            signature.append(self._protocol_token_shape(token))
        width = len(signature)
        offsets = set()
        for index in range(start, end - width + 1):
            candidate = []
            for token in self.tokens[index : index + width]:
                candidate.append(self._protocol_token_shape(token))
            if candidate == signature:
                offsets.update(
                    token.offset
                    for token in self.tokens[index : index + width]
                )
                cursor = index + width
                saw_return = False
                while (
                    cursor < end
                    and self.tokens[cursor].kind
                    in ("INTERNAL_EXTENDED_ARG", "NOP", "RETURN_VALUE")
                ):
                    if self.tokens[cursor].kind == "RETURN_VALUE":
                        if saw_return:
                            offsets.add(self.tokens[cursor].offset)
                        saw_return = True
                    cursor += 1
        return offsets

    def _capture_exceptional_finally(
        self,
        handler_offset: int,
        loop,
    ) -> List[ast.stmt]:
        """Capture a simple exceptional copy without its re-raise exits."""
        handler_index = self.offset_to_index[handler_offset]
        cleanup_end = self._handler_cleanup_end(
            handler_index + 1,
            handler_index,
        )
        body_end = cleanup_end - 3
        protocol_offsets = {
            self.tokens[index].offset
            for index in range(handler_index + 1, body_end)
            if self.tokens[index].kind == "RERAISE"
        }
        added = (
            protocol_offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        self.owner._suppressed_exception_protocol_offsets.update(added)
        try:
            return self._capture_suppressed(
                handler_index + 1,
                body_end,
                loop,
            )
        finally:
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added
            )

    @staticmethod
    def _extract_handler_return_finally(statement: ast.Try):
        """Move a physical return-path finally copy around its try/except."""
        extracted = []
        for handler in statement.handlers:
            for index, node in enumerate(handler.body):
                if (
                    isinstance(node, ast.Try)
                    and not node.handlers
                    and not node.orelse
                    and len(node.body) == 1
                    and isinstance(node.body[0], ast.Return)
                    and node.finalbody
                ):
                    extracted.append((handler, index, node))
        if not extracted:
            return None
        signature = ast.dump(
            ast.Module(body=extracted[0][2].finalbody, type_ignores=[]),
            include_attributes=False,
        )
        if any(
            ast.dump(
                ast.Module(body=node.finalbody, type_ignores=[]),
                include_attributes=False,
            )
            != signature
            for _, _, node in extracted[1:]
        ):
            return None
        for handler, index, node in extracted:
            handler.body[index] = node.body[0]
        return extracted[0][2].finalbody

    def _capture_handler_clause(
        self,
        start: int,
        end: int,
        name: Optional[str],
        loop,
    ) -> List[ast.stmt]:
        offsets = self._handler_protocol_offsets(start, end, name)
        # A return/break/continue in an except clause can make CPython start
        # the enclosing finally protection at the clause's POP_EXCEPT.  Any
        # nested try recovered while capturing the clause must not consume
        # that outer finally; the containing _try_except call owns it after
        # all handler cleanup has been decoded.
        clause_offset = self.tokens[start].offset
        enclosing_cleanup_targets = {
            entry.target
            for entry in self.entries
            if entry.start == clause_offset
            and not entry.lasti
            and self.offset_to_index[entry.target] >= end
            and self.tokens[self.offset_to_index[entry.target]].kind
            == "PUSH_EXC_INFO"
            and not self._handler_has_match(
                self.offset_to_index[entry.target]
            )
        }
        frontier = [clause_offset]
        visited_offsets = set()
        while frontier:
            protected_offset = frontier.pop()
            if protected_offset in visited_offsets:
                continue
            visited_offsets.add(protected_offset)
            for entry in self.entries:
                if not (
                    entry.start <= protected_offset < entry.end
                    or entry.start == protected_offset
                ):
                    continue
                target_index = self.offset_to_index[entry.target]
                target = self.tokens[target_index]
                if (
                    target_index >= end
                    and target.kind == "PUSH_EXC_INFO"
                    and not self._handler_has_match(target_index)
                ):
                    enclosing_cleanup_targets.add(entry.target)
                elif target_index >= end:
                    frontier.append(entry.target)
        for target in enclosing_cleanup_targets:
            offsets.update(
                self._duplicated_finally_offsets(start, end, target)
            )
        added = (
            offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        added_handlers = (
            enclosing_cleanup_targets
            - self.owner._suppressed_exception_handler_targets
        )
        self.owner._suppressed_exception_protocol_offsets.update(added)
        self.owner._suppressed_exception_handler_targets.update(
            added_handlers
        )
        try:
            return self._capture_optional(start, end, loop)
        finally:
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added
            )
            self.owner._suppressed_exception_handler_targets.difference_update(
                added_handlers
            )

    def _handler_none_return_plan(
        self,
        start: int,
        end: int,
        name: Optional[str],
        continuation_offset: Optional[int],
        loop=None,
    ) -> Optional[_HandlerTerminalTransfer]:
        """Prove that one ordinary handler returns before a normal join."""
        if (
            continuation_offset is None
            or start >= end
            or continuation_offset not in self.offset_to_index
        ):
            return None

        semantic = [
            index
            for index in range(start, end)
            if self.tokens[index].kind != "INTERNAL_EXTENDED_ARG"
        ]
        if len(semantic) < 3:
            return None
        return_load_index, return_index = semantic[-2:]
        if (
            self.tokens[return_load_index].kind != "LOAD_CONST"
            or self.tokens[return_load_index].attr is not None
            or self.tokens[return_index].kind != "RETURN_VALUE"
        ):
            return None
        continuation_index = self.offset_to_index[continuation_offset]
        if continuation_index <= return_index:
            return None

        cleanup_position = next(
            (
                position
                for position in range(len(semantic) - 3, -1, -1)
                if self.tokens[semantic[position]].kind == "POP_EXCEPT"
            ),
            None,
        )
        if cleanup_position is None:
            return None
        cleanup_start = semantic[cleanup_position]
        protocol = semantic[cleanup_position:-2]
        protocol_cursor = 1
        if name is not None:
            if len(protocol) < 4:
                return None
            load, store, delete = (
                self.tokens[index] for index in protocol[1:4]
            )
            stored_name = (
                store.attr if isinstance(store.attr, str) else store.pattr
            )
            deleted_name = (
                delete.attr
                if isinstance(delete.attr, str)
                else delete.pattr
            )
            if (
                load.kind != "LOAD_CONST"
                or load.attr is not None
                or not store.kind.startswith("STORE_")
                or not delete.kind.startswith("DELETE_")
                or stored_name != name
                or deleted_name != name
            ):
                return None
            protocol_cursor = 4

        loop_cleanup_seen = False
        for index in protocol[protocol_cursor:]:
            token = self.tokens[index]
            if (
                token.offset
                in self.owner._suppressed_exception_protocol_offsets
            ):
                continue
            if (
                loop is not None
                and not loop_cleanup_seen
                and token.kind == "POP_TOP"
            ):
                loop_cleanup_seen = True
                continue
            return None

        cfg = self.owner.cfg
        return_offset = self.tokens[return_index].offset
        return_block = cfg.offset_to_block.get(return_offset)
        start_block = cfg.offset_to_block.get(self.tokens[start].offset)
        if return_block is None or start_block is None:
            return None
        if cfg.block(return_block).terminator != "RETURN_VALUE":
            return None
        if any(
            edge.kind != "exception"
            for edge in cfg.outgoing(return_block)
        ):
            return None
        if any(
            edge.kind == "exception"
            and (edge.source == return_block or edge.target == return_block)
            for edge in cfg.edges
        ):
            return None

        lower = cfg.block(start_block).start
        interval_blocks = {
            block.index
            for block in cfg.blocks
            if lower <= block.start <= return_offset
        }
        protocol_blocks = {
            cfg.offset_to_block.get(self.tokens[index].offset)
            for index in (*protocol, return_load_index, return_index)
        }
        if (
            start_block not in interval_blocks
            or return_block not in interval_blocks
            or None in protocol_blocks
            or not protocol_blocks <= interval_blocks
        ):
            return None
        pending = [start_block]
        visited = set()
        work_limit = max(32, len(interval_blocks) * 4)
        work_count = 0
        while pending:
            work_count += 1
            if work_count > work_limit:
                return None
            block_index = pending.pop()
            if block_index in visited:
                continue
            if block_index not in interval_blocks:
                return None
            visited.add(block_index)
            for edge in cfg.outgoing(block_index):
                if edge.kind == "exception":
                    if edge.target in interval_blocks:
                        pending.append(edge.target)
                    continue
                if edge.target not in interval_blocks:
                    return None
                pending.append(edge.target)
        if visited != interval_blocks or return_block not in visited:
            return None

        for block_index in visited:
            outside = [
                edge
                for edge in cfg.incoming(block_index)
                if edge.source not in visited
            ]
            if block_index == start_block:
                # The CFG materializes one exception edge per protected basic
                # block.  They are multiple physical predecessors but one
                # logical entry into this handler.  Ordinary predecessors
                # would make the terminal return reachable outside the
                # handler and therefore remain unsafe.
                handler_offset = cfg.block(start_block).start
                handler_entries = [
                    candidate
                    for candidate in self.entries
                    if candidate.target == handler_offset
                ]
                typed_clause_entry = (
                    len(outside) == 1
                    and outside[0].kind != "exception"
                )
                bare_clause_entry = (
                    bool(outside)
                    and all(edge.kind == "exception" for edge in outside)
                    and bool(handler_entries)
                    and all(
                        any(
                            candidate.start
                            <= cfg.block(edge.source).start
                            < candidate.end
                            for candidate in handler_entries
                        )
                        for edge in outside
                    )
                )
                if not (typed_clause_entry or bare_clause_entry):
                    return None
            elif any(edge.kind != "exception" for edge in outside):
                return None

        continuation_block = cfg.offset_to_block.get(continuation_offset)
        if continuation_block is None or not any(
            edge.kind != "exception"
            and edge.target == continuation_block
            and cfg.block(edge.source).start < lower
            and cfg.block(edge.source).terminator == "JUMP_FORWARD"
            for edge in cfg.edges
        ):
            return None

        protocol_offsets = frozenset(
            self.tokens[index].offset
            for index in (*protocol, return_load_index, return_index)
        )
        return _HandlerTerminalTransfer(
            kind="return_none",
            body_start=start,
            body_end=cleanup_start,
            cleanup_start=cleanup_start,
            return_load_index=return_load_index,
            return_index=return_index,
            continuation_offset=continuation_offset,
            protocol_offsets=protocol_offsets,
        )

    def _has_unresolved_handler_none_return(
        self,
        start: int,
        end: int,
        name: Optional[str],
        loop,
    ) -> bool:
        """Report a canonical None-return whose ownership was not proven."""
        semantic = [
            index
            for index in range(start, end)
            if self.tokens[index].kind != "INTERNAL_EXTENDED_ARG"
        ]
        if len(semantic) < 2:
            return False
        load_index, return_index = semantic[-2:]
        if not (
            self.tokens[load_index].kind == "LOAD_CONST"
            and self.tokens[load_index].attr is None
            and self.tokens[return_index].kind == "RETURN_VALUE"
        ):
            return False
        cleanup_position = next(
            (
                position
                for position in range(len(semantic) - 3, -1, -1)
                if self.tokens[semantic[position]].kind == "POP_EXCEPT"
            ),
            None,
        )
        if cleanup_position is None:
            return len(semantic) <= 6
        protocol = semantic[cleanup_position:-2]
        cursor = 1
        if name is not None:
            if len(protocol) < 4:
                return False
            load, store, delete = (
                self.tokens[index] for index in protocol[1:4]
            )
            stored_name = (
                store.attr if isinstance(store.attr, str) else store.pattr
            )
            deleted_name = (
                delete.attr
                if isinstance(delete.attr, str)
                else delete.pattr
            )
            if (
                load.kind != "LOAD_CONST"
                or load.attr is not None
                or not store.kind.startswith("STORE_")
                or not delete.kind.startswith("DELETE_")
                or stored_name != name
                or deleted_name != name
            ):
                return len(protocol) <= 6
            cursor = 4
        loop_cleanup_seen = False
        for index in protocol[cursor:]:
            token = self.tokens[index]
            if (
                token.offset
                in self.owner._suppressed_exception_protocol_offsets
            ):
                continue
            if (
                loop is not None
                and not loop_cleanup_seen
                and token.kind == "POP_TOP"
            ):
                loop_cleanup_seen = True
                continue
            return False
        return True

    def _conditional_handler_transfer(
        self,
        start: int,
        end: int,
        name: Optional[str],
        loop,
    ) -> Optional[List[ast.stmt]]:
        """Recover conditional break/continue paths in a handler."""
        if start >= end:
            return None

        def transfer_statement(index: int):
            token = self.tokens[index]
            target = instruction_target(token)
            if loop is not None:
                if target == loop.break_target:
                    return ast.Break()
                if target in loop.continue_targets:
                    return ast.Continue()
            if token.kind == "JUMP_BACKWARD":
                if target <= self.tokens[start].offset:
                    return ast.Continue()
                return None
            if token.kind != "JUMP_FORWARD":
                return None
            target_index = self.offset_to_index.get(target)
            if target_index is None or target_index == 0:
                return None
            latch_index = target_index - 1
            while (
                latch_index >= end
                and self.tokens[latch_index].kind
                in ("INTERNAL_EXTENDED_ARG", "NOP")
            ):
                latch_index -= 1
            if (
                latch_index >= end
                and self.tokens[latch_index].kind == "JUMP_BACKWARD"
                and instruction_target(self.tokens[latch_index])
                <= self.tokens[start].offset
            ):
                return ast.Break()
            return None

        jump_index = next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].kind
                in (
                    "POP_JUMP_FORWARD_IF_FALSE",
                    "POP_JUMP_FORWARD_IF_TRUE",
                )
            ),
            None,
        )
        if jump_index is None:
            return None
        false_index = self.offset_to_index[
            instruction_target(self.tokens[jump_index])
        ]
        transfer_jumps = [
            index
            for index in range(jump_index + 1, min(false_index, end))
            if self.tokens[index].kind
            in ("JUMP_FORWARD", "JUMP_BACKWARD")
        ]
        if len(transfer_jumps) == 1 and false_index < end:
            transfer_index = transfer_jumps[0]
            if all(
                self.tokens[index].kind
                in ("POP_EXCEPT", "JUMP_FORWARD", "JUMP_BACKWARD")
                for index in range(jump_index + 1, false_index)
            ):
                transfer = transfer_statement(transfer_index)
                if transfer is not None:
                    try:
                        test = self.owner._expression_slice(
                            start,
                            jump_index,
                        )
                    except Python311ParseError:
                        return None
                    alternate = self._capture_handler_clause(
                        false_index,
                        end,
                        name,
                        loop,
                    )
                    if self.tokens[jump_index].kind.endswith("_IF_TRUE"):
                        body = alternate
                        orelse = [transfer]
                    else:
                        body = [transfer]
                        orelse = alternate
                    return [
                        ast.If(
                            test=test,
                            body=body or [ast.Pass()],
                            orelse=orelse,
                        )
                    ]
        if (
            not jump_index < false_index < end
            or false_index != end - 1
            or self.tokens[false_index].kind != "RAISE_VARARGS"
            or self.tokens[false_index].attr != 0
        ):
            return None
        transfer_jumps = [
            index
            for index in range(jump_index + 1, false_index)
            if self.tokens[index].kind
            in ("JUMP_FORWARD", "JUMP_BACKWARD")
        ]
        if len(transfer_jumps) != 1:
            return None
        transfer_index = transfer_jumps[0]
        if any(
            self.tokens[index].kind
            not in ("POP_EXCEPT", "JUMP_FORWARD", "JUMP_BACKWARD")
            for index in range(jump_index + 1, false_index)
        ):
            return None
        transfer = transfer_statement(transfer_index)
        if transfer is None:
            return None
        test = self.owner._expression_slice(start, jump_index)
        if self.tokens[jump_index].kind.endswith("_IF_TRUE"):
            test = ast.UnaryOp(op=ast.Not(), operand=test)
        return [
            ast.If(test=test, body=[transfer], orelse=[]),
            ast.Raise(exc=None, cause=None),
        ]

    def _clause_body(
        self,
        start: int,
        end: int,
        name: Optional[str],
        loop,
        continuation_offset: Optional[int] = None,
    ) -> List[ast.stmt]:
        terminal = self._handler_none_return_plan(
            start,
            end,
            name,
            continuation_offset,
            loop,
        )
        if terminal is not None:
            body = self._capture_handler_clause(
                terminal.body_start,
                terminal.body_end,
                name,
                loop,
            )
            body.append(ast.Return(value=None))
            return body
        if start < end and self.tokens[end - 1].kind == "JUMP_FORWARD":
            target = instruction_target(self.tokens[end - 1])
            transfer = None
            if loop is not None and target == loop.break_target:
                transfer = ast.Break()
            elif (
                loop is not None
                and target in loop.continue_targets
            ):
                transfer = ast.Continue()
            if transfer is not None:
                body = self._capture_handler_clause(
                    start,
                    end - 1,
                    name,
                    loop,
                )
                body.append(transfer)
                return body
            end -= 1
        conditional_transfer = self._conditional_handler_transfer(
            start,
            end,
            name,
            loop,
        )
        if conditional_transfer is not None:
            return conditional_transfer
        body = self._capture_handler_clause(start, end, name, loop)
        if (
            continuation_offset is not None
            and self._has_unresolved_handler_none_return(
                start,
                end,
                name,
                loop,
            )
            and (not body or not isinstance(body[-1], ast.Return))
        ):
            self._error(
                "Cannot prove except handler None-return ownership",
                self.tokens[end - 2].offset,
            )
        return body

    def _handler_cleanup_end(
        self,
        start: int,
        handler_index: Optional[int] = None,
    ) -> int:
        if handler_index is not None:
            handler_offset = self.tokens[handler_index].offset
            cleanup_targets = sorted(
                {
                    entry.target
                    for entry in self.entries
                    if entry.start == handler_offset
                    and entry.lasti
                    and entry.target > handler_offset
                }
            )
            for target in cleanup_targets:
                index = self.offset_to_index[target]
                if (
                    index + 2 < len(self.tokens)
                    and self.tokens[index].kind == "COPY_STACK"
                    and self.tokens[index + 1].kind == "POP_EXCEPT"
                    and self.tokens[index + 2].kind == "RERAISE"
                ):
                    return index + 3
        for index in range(start, len(self.tokens) - 2):
            if (
                self.tokens[index].kind == "COPY_STACK"
                and self.tokens[index + 1].kind == "POP_EXCEPT"
                and self.tokens[index + 2].kind == "RERAISE"
            ):
                return index + 3
        self._error("Exception handler has no COPY/POP_EXCEPT/RERAISE cleanup")

    def _move_normal_return_before_finally(
        self,
        body: List[ast.stmt],
        finalbody: List[ast.stmt],
        handler_index: int,
        cleanup_end: int,
    ) -> None:
        """Restore a return duplicated only on the normal cleanup path."""
        if (
            not finalbody
            or not isinstance(finalbody[-1], ast.Return)
            or any(
                self.tokens[index].kind == "RETURN_VALUE"
                for index in range(handler_index + 1, cleanup_end - 3)
            )
        ):
            return
        body.append(finalbody.pop())

    def _clause_structural_end(
        self,
        body_start: int,
        false_index: int,
        name: Optional[str],
    ) -> int:
        """Exclude the exceptional name-cleanup copy from one clause."""
        if name is not None:
            for index in range(body_start, false_index - 3):
                load, store, delete, reraise = self.tokens[
                    index : index + 4
                ]
                stored_name = (
                    store.attr
                    if isinstance(store.attr, str)
                    else store.pattr
                )
                deleted_name = (
                    delete.attr
                    if isinstance(delete.attr, str)
                    else delete.pattr
                )
                if (
                    load.kind == "LOAD_CONST"
                    and load.attr is None
                    and store.kind.startswith("STORE_")
                    and delete.kind.startswith("DELETE_")
                    and stored_name == name
                    and deleted_name == name
                    and reraise.kind == "RERAISE"
                ):
                    return index

        body_offset = self.tokens[body_start].offset
        candidates = []
        for entry in self.entries:
            target_index = self.offset_to_index[entry.target]
            if (
                entry.lasti
                and entry.start <= body_offset < entry.end
                and body_start < target_index <= false_index
            ):
                candidates.append(target_index)
        if candidates:
            return min(candidates)
        return false_index

    def _parse_handlers(
        self,
        handler_index: int,
        loop,
        continuation_offset: Optional[int] = None,
    ) -> Tuple[List[ast.ExceptHandler], int, Optional[int]]:
        if self.tokens[handler_index].kind != "PUSH_EXC_INFO":
            self._error("Exception handler does not start with PUSH_EXC_INFO")
        state_entry = next(
            (
                entry
                for entry in self.entries
                if entry.target == self.tokens[handler_index].offset
            ),
            None,
        )
        if state_entry is not None:
            self._remember_exception_state(state_entry)

        handlers = []
        joins = []
        cursor = handler_index + 1
        final_reraise = None
        while cursor < len(self.tokens):
            if self.tokens[cursor].kind == "POP_TOP":
                bare, cleanup_end, bare_join = self._parse_bare_handler(
                    cursor,
                    loop,
                    handler_index,
                    continuation_offset,
                )
                handlers.append(bare)
                if bare_join is not None:
                    joins.append(bare_join)
                join = min(joins) if joins else None
                return handlers, cleanup_end, join
            if self.tokens[cursor].kind == "RERAISE":
                final_reraise = cursor
                break

            check_index = next(
                (
                    index
                    for index in range(cursor, len(self.tokens))
                    if self.tokens[index].kind in (
                        "CHECK_EXC_MATCH",
                        "RERAISE",
                    )
                ),
                None,
            )
            if check_index is None:
                self._error("Exception clause has no CHECK_EXC_MATCH")
            if self.tokens[check_index].kind == "RERAISE":
                final_reraise = check_index
                break
            exception_type = self.owner._expression_slice(cursor, check_index)
            jump_index = self.owner._next_semantic_index(
                check_index + 1,
                len(self.tokens),
            )
            if not self.tokens[jump_index].kind.startswith("POP_JUMP_"):
                self._error("CHECK_EXC_MATCH has no conditional jump")
            false_index = self.offset_to_index[
                instruction_target(self.tokens[jump_index])
            ]
            binding_index = self.owner._next_semantic_index(
                jump_index + 1,
                len(self.tokens),
            )
            binding = self.tokens[binding_index]
            if binding.kind.startswith("STORE_"):
                name = (
                    binding.attr
                    if isinstance(binding.attr, str)
                    else binding.pattr
                )
                body_start = binding_index + 1
            elif binding.kind == "POP_TOP":
                name = None
                body_start = binding_index + 1
            else:
                self._error("Exception clause has no name or POP_TOP binding")

            normal_end = self._clause_structural_end(
                body_start,
                false_index,
                name,
            )
            body = self._clause_body(
                body_start,
                normal_end,
                name,
                loop,
                continuation_offset,
            )
            handlers.append(
                ast.ExceptHandler(
                    type=exception_type,
                    name=name,
                    body=body or [ast.Pass()],
                )
            )
            cursor = false_index

        if final_reraise is None:
            self._error("Exception handler has no final RERAISE")
        cleanup_end = self._handler_cleanup_end(
            final_reraise + 1,
            handler_index,
        )
        join = min(joins) if joins else None
        return handlers, cleanup_end, join

    def _parse_bare_handler(
        self,
        cursor: int,
        loop,
        handler_index: int,
        continuation_offset: Optional[int] = None,
    ) -> Tuple[ast.ExceptHandler, int, Optional[int]]:
        if self.tokens[cursor].kind != "POP_TOP":
            self._error("Bare exception handler has no POP_TOP")
        body_start = cursor + 1
        cleanup_end = self._handler_cleanup_end(
            body_start,
            handler_index,
        )
        body_end = cleanup_end - 3
        if (
            body_start < body_end
            and self.tokens[body_end - 1].kind == "RERAISE"
        ):
            body_end -= 1
        join = None
        if (
            body_start < body_end
            and self.tokens[body_end - 1].kind == "JUMP_FORWARD"
        ):
            target = instruction_target(self.tokens[body_end - 1])
            if loop is not None and target == loop.break_target:
                body = self._capture_handler_clause(
                    body_start,
                    body_end - 1,
                    None,
                    loop,
                )
                body.append(ast.Break())
                return (
                    ast.ExceptHandler(
                        type=None,
                        name=None,
                        body=body,
                    ),
                    cleanup_end,
                    None,
                )
            if (
                loop is not None
                and target in loop.continue_targets
            ):
                body = self._capture_handler_clause(
                    body_start,
                    body_end - 1,
                    None,
                    loop,
                )
                body.append(ast.Continue())
                return (
                    ast.ExceptHandler(
                        type=None,
                        name=None,
                        body=body,
                    ),
                    cleanup_end,
                    None,
                )
            join = target
        body = self._clause_body(
            body_start,
            body_end,
            None,
            loop,
            continuation_offset,
        )
        return (
            ast.ExceptHandler(type=None, name=None, body=body or [ast.Pass()]),
            cleanup_end,
            join,
        )

    def _try_except(self, entry, loop) -> Tuple[ast.Try, int]:
        start = self.offset_to_index[entry.start]
        try_end = self.offset_to_index[entry.end]
        handler_index = self.offset_to_index[entry.target]
        except_handler_index = handler_index
        fragments = []
        for candidate in self.entries:
            candidate_start = self.offset_to_index[candidate.start]
            if (
                candidate.target == entry.target
                and candidate.depth == entry.depth
                and candidate.lasti == entry.lasti
                and start <= candidate_start < handler_index
            ):
                fragments.append(candidate)
        body_end = max(
            (self.offset_to_index[candidate.end] for candidate in fragments),
            default=try_end,
        )
        crosses_with = False
        held_return_through_handler = False
        if (
            body_end < handler_index
            and self.tokens[body_end].kind == "RETURN_VALUE"
        ):
            held_return_through_handler = (
                body_end > start
                and self.tokens[body_end - 1].kind
                in ("POP_TOP", "POP_EXCEPT")
            )
            if not held_return_through_handler:
                body_end += 1
        elif body_end < handler_index:
            cursor = body_end
            while (
                cursor + 1 < handler_index
                and self.tokens[cursor].kind == "SWAP_STACK"
                and self.tokens[cursor].attr == 2
                and self.tokens[cursor + 1].kind == "POP_TOP"
            ):
                cursor += 2
            while (
                cursor < handler_index
                and self.tokens[cursor].offset
                in self.owner._suppressed_exception_protocol_offsets
            ):
                cursor += 1
            if (
                cursor > body_end
                and cursor < handler_index
                and self.tokens[cursor].kind == "RETURN_VALUE"
            ):
                # The protected expression was already structured as the
                # source return; these physical loop-iterator cleanup pairs
                # belong to that same normal path, not to ``try`` orelse.
                body_end = cursor + 1
        terminal_return_end = self._protected_terminal_return_frontier_end(
            fragments,
            body_end,
            handler_index,
        )
        if terminal_return_end is None:
            loop_frontier = self._protected_loop_terminal_frontier(
                fragments,
                body_end,
                handler_index,
                loop,
            )
            if loop_frontier is not None:
                terminal_return_end = loop_frontier.end_index
        if terminal_return_end is not None:
            body_end = terminal_return_end
        if (
            0 < body_end < handler_index
            and self.tokens[body_end - 1].kind
            in ("BEFORE_WITH", "BEFORE_ASYNC_WITH")
            and self.tokens[body_end].kind.startswith(("STORE_", "POP_TOP"))
        ):
            nested_protected = next(
                (
                    candidate
                    for candidate in self.entries
                    if candidate.start == self.tokens[body_end].offset
                    and candidate.lasti
                ),
                None,
            )
            if nested_protected is None:
                self.owner.current_token = self.tokens[body_end]
                self.owner._error(
                    "With cleanup crosses an enclosing exception region"
                )
            nested_handler_index = self.offset_to_index[
                nested_protected.target
            ]
            outer_fragments = []
            for candidate in self.entries:
                candidate_start = self.offset_to_index[candidate.start]
                if (
                    candidate.target == entry.target
                    and candidate.depth == entry.depth
                    and candidate.lasti == entry.lasti
                    and start <= candidate_start < nested_handler_index
                ):
                    outer_fragments.append(candidate)
            if not outer_fragments:
                self.owner.current_token = self.tokens[body_end]
                self.owner._error(
                    "With cleanup crosses an enclosing exception region"
                )
            body_end = max(
                self.offset_to_index[candidate.end]
                for candidate in outer_fragments
            )
            crosses_with = True

        held_value = (
            self.owner.stack[-1]
            if len(self.owner.stack) == 1
            and isinstance(self.owner.stack[-1], ast.expr)
            and self.owner.pending_assignment_value is None
            and not self.owner.pending_assignment_targets
            and not self.owner.pending_booleans
            and not self.owner.pending_keywords
            else None
        )
        if held_value is not None and self._handler_is_bare(entry):
            cleanup_end = self._handler_cleanup_end(
                handler_index + 1,
                handler_index,
            )
            normal_returns = [
                index
                for index in range(body_end, handler_index)
                if self.tokens[index].kind == "RETURN_VALUE"
            ]
            handler_returns = [
                index
                for index in range(handler_index, cleanup_end)
                if self.tokens[index].kind == "RETURN_VALUE"
            ]
            normal_gap_is_protocol = all(
                self.tokens[index].kind
                in ("INTERNAL_EXTENDED_ARG", "NOP", "RETURN_VALUE")
                for index in range(body_end, handler_index)
            )
            if (
                normal_returns
                and handler_returns
                and normal_gap_is_protocol
            ):
                held_value = self.owner.stack.pop()
                return_offsets = {
                    self.tokens[index].offset
                    for index in range(start, cleanup_end)
                    if self.tokens[index].kind == "RETURN_VALUE"
                }
                added_returns = (
                    return_offsets
                    - self.owner._suppressed_exception_protocol_offsets
                )
                self.owner._suppressed_exception_protocol_offsets.update(
                    added_returns
                )
                try:
                    cleanup_body = self._capture_protected_fragments(
                        start,
                        body_end,
                        loop,
                        fragments,
                    )
                    handlers, parsed_end, handler_join = (
                        self._parse_handlers(
                            handler_index,
                            loop,
                            None,
                        )
                    )
                except Python311ParseError:
                    self.owner.stack.append(held_value)
                    raise
                finally:
                    self.owner._suppressed_exception_protocol_offsets.difference_update(
                        added_returns
                    )
                if handler_join is not None or parsed_end != cleanup_end:
                    self.owner._error(
                        "Held return cleanup has an unexpected handler join"
                    )
                cleanup_try = ast.Try(
                    body=cleanup_body or [ast.Pass()],
                    handlers=handlers,
                    orelse=[],
                    finalbody=[],
                )
                return (
                    ast.Try(
                        body=[ast.Return(value=held_value)],
                        handlers=[],
                        orelse=[],
                        finalbody=[cleanup_try],
                    ),
                    cleanup_end,
                )

        deferred_return_body = (
            self._capture_protected_return(start, body_end, loop)
            if not held_return_through_handler
            and any(
                self.tokens[index].kind == "RETURN_VALUE"
                for index in range(body_end, handler_index)
            )
            else None
        )
        protected_return = deferred_return_body is not None
        body = (
            deferred_return_body
            if protected_return
            else self._capture_protected_fragments(
                start,
                body_end,
                loop,
                fragments,
            )
        )
        if crosses_with:
            enclosing_jump = next(
                (
                    index
                    for index in range(handler_index - 1, body_end - 1, -1)
                    if self.tokens[index].kind == "JUMP_FORWARD"
                ),
                None,
            )
            if enclosing_jump is not None:
                body_end = enclosing_jump

        normal_jump = (
            handler_index - 1
            if body_end < handler_index
            and self.tokens[handler_index - 1].kind == "JUMP_FORWARD"
            else None
        )
        normal_body_start = body_end
        if (
            normal_jump is not None
            and not protected_return
            and not held_return_through_handler
            and any(
                self.tokens[index].kind == "RETURN_VALUE"
                for index in range(body_end, normal_jump)
            )
        ):
            normal_body_start = self._normal_completion_entry(
                fragments,
                normal_jump,
                handler_index,
            )
            if normal_body_start is None:
                self._error(
                    "Cannot prove try normal-completion ownership",
                    self.tokens[body_end].offset,
                )
        if protected_return or held_return_through_handler:
            orelse = []
            normal_join = None
        elif normal_jump is None:
            orelse = self._capture_before_handler(
                body_end,
                handler_index,
                entry.target,
                loop,
            )
            normal_join = None
        else:
            orelse = self._capture_before_handler(
                normal_body_start,
                normal_jump,
                entry.target,
                loop,
            )
            normal_join = instruction_target(self.tokens[normal_jump])

        held_return_offsets = set()
        if held_return_through_handler:
            predicted_cleanup_end = self._handler_cleanup_end(
                handler_index + 1,
                handler_index,
            )
            held_return_offsets = {
                self.tokens[index].offset
                for index in range(body_end, predicted_cleanup_end)
                if self.tokens[index].kind == "RETURN_VALUE"
            }
        added_held_returns = (
            held_return_offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        self.owner._suppressed_exception_protocol_offsets.update(
            added_held_returns
        )
        try:
            handlers, cleanup_end, handler_join = self._parse_handlers(
                handler_index,
                loop,
                normal_join,
            )
        finally:
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added_held_returns
            )
        join = normal_join or handler_join
        next_index = (
            self.offset_to_index[join]
            if join is not None
            else cleanup_end
        )
        statement = ast.Try(
            body=body or [ast.Pass()],
            handlers=handlers,
            orelse=orelse,
            finalbody=[],
        )

        enclosing_fragmented = self._enclosing_fragmented_handler(
            entry,
            next_index,
        )
        if enclosing_fragmented is not None:
            outer_target, outer_fragments = enclosing_fragmented
            outer_handler_index = self.offset_to_index[outer_target]
            continuation = self._capture_protected_fragments(
                next_index,
                outer_handler_index,
                loop,
                outer_fragments,
            )
            outer_handlers, outer_end, outer_join = self._parse_handlers(
                outer_handler_index,
                loop,
            )
            statement = ast.Try(
                body=[statement] + continuation,
                handlers=outer_handlers,
                orelse=[],
                finalbody=[],
            )
            next_index = (
                self.offset_to_index[outer_join]
                if outer_join is not None
                else outer_end
            )

        if (
            next_index < len(self.tokens)
            and self.tokens[next_index].kind == "PUSH_EXC_INFO"
            and self._handler_has_match(next_index)
        ):
            outer_handlers, outer_end, outer_join = self._parse_handlers(
                next_index,
                loop,
            )
            statement = ast.Try(
                body=[statement],
                handlers=outer_handlers,
                orelse=[],
                finalbody=[],
            )
            next_index = (
                self.offset_to_index[outer_join]
                if outer_join is not None
                else outer_end
            )

        if next_index < len(self.tokens):
            continuation_offset = self.tokens[next_index].offset
            enclosing_handler_targets = sorted(
                {
                    region.target
                    for region in self.entries
                    if region.end == continuation_offset
                    and region.start < continuation_offset
                    and self.offset_to_index[region.target] > next_index
                    and self.tokens[
                        self.offset_to_index[region.target]
                    ].kind
                    == "PUSH_EXC_INFO"
                    and self._handler_has_match(
                        self.offset_to_index[region.target]
                    )
                    and region.target
                    not in self.owner._suppressed_exception_handler_targets
                }
            )
            if enclosing_handler_targets:
                outer_target = enclosing_handler_targets[0]
                outer_handler_index = self.offset_to_index[outer_target]
                outer_orelse = self._capture_before_handler(
                    next_index,
                    outer_handler_index,
                    outer_target,
                    loop,
                )
                outer_handlers, outer_end, outer_join = self._parse_handlers(
                    outer_handler_index,
                    loop,
                )
                statement = ast.Try(
                    body=[statement],
                    handlers=outer_handlers,
                    orelse=outer_orelse,
                    finalbody=[],
                )
                next_index = (
                    self.offset_to_index[outer_join]
                    if outer_join is not None
                    else outer_end
                )

        enclosing_with_targets = {
            region.target
            for region in self.entries
            if region.lasti
            and region.start in self.owner._suppressed_exception_starts
            and self.offset_to_index[region.target] > next_index
            and self.offset_to_index[region.target] + 1 < len(self.tokens)
            and self.tokens[
                self.offset_to_index[region.target] + 1
            ].kind
            == "WITH_EXCEPT_START"
        }
        with_handler_limit = (
            min(enclosing_with_targets)
            if enclosing_with_targets
            else None
        )
        finally_targets = sorted(
            {
                region.target
                for region in self.entries
                if region.depth == entry.depth
                and region.start
                not in self.owner._suppressed_exception_starts
                and region.target > self.tokens[next_index - 1].offset
                and region.start < self.tokens[next_index].offset
                and region.end > entry.start
                and (
                    with_handler_limit is None
                    or region.target < with_handler_limit
                )
                and self.tokens[
                    self.offset_to_index[region.target]
                ].kind
                == "PUSH_EXC_INFO"
                and region.target
                not in self.owner._suppressed_exception_handler_targets
                and not self._handler_has_match(
                    self.offset_to_index[region.target]
                )
            }
        )
        if finally_targets:
            handler_offset = finally_targets[0]
            handler_index = self.offset_to_index[handler_offset]
            jump_index = (
                handler_index - 1
                if next_index < handler_index
                and self.tokens[handler_index - 1].kind
                == "JUMP_FORWARD"
                else None
            )
            if jump_index is None:
                protected_ends = [
                    self.offset_to_index[region.end]
                    for region in self.entries
                    if region.depth == 0
                    and region.target == handler_offset
                ]
                protected_end = (
                    max(protected_ends)
                    if protected_ends
                    else next_index
                )
                if (
                    protected_end == handler_index
                    and protected_return
                    and body_end < except_handler_index
                ):
                    # Every source path exits the try/except, so CPython has
                    # no separate normal finally copy after the handler
                    # cleanup.  Reuse the copy placed between the protected
                    # return expression and the except handler.
                    finalbody = self._capture_deferred_return_finally(
                        body_end,
                        except_handler_index,
                        handler_offset,
                        loop,
                        statement,
                    )
                    statement = ast.Try(
                        body=[statement],
                        handlers=[],
                        orelse=[],
                        finalbody=finalbody or [ast.Pass()],
                    )
                    next_index = self._handler_cleanup_end(
                        handler_index + 1,
                        handler_index,
                    )
                    return statement, next_index
                exceptional_finally = self._exceptional_finally_body(
                    handler_offset
                )
                duplicated_finally = self._duplicated_finally_offsets(
                    next_index,
                    handler_index,
                    handler_offset,
                )
                if (
                    protected_end == handler_index
                    and exceptional_finally is not None
                    and duplicated_finally
                ):
                    # All remaining paths return or raise, so the only
                    # canonical copy of the source finally suite is its
                    # exceptional handler.  Hide duplicated normal-path
                    # copies while recovering the protected continuation.
                    added_protocol = (
                        duplicated_finally
                        - self.owner._suppressed_exception_protocol_offsets
                    )
                    handler_added = (
                        handler_offset
                        not in self.owner._suppressed_exception_handler_targets
                    )
                    self.owner._suppressed_exception_protocol_offsets.update(
                        added_protocol
                    )
                    self.owner._suppressed_exception_handler_targets.add(
                        handler_offset
                    )
                    try:
                        continuation = self._capture_optional(
                            next_index,
                            handler_index,
                            loop,
                        )
                    finally:
                        self.owner._suppressed_exception_protocol_offsets.difference_update(
                            added_protocol
                        )
                        if handler_added:
                            self.owner._suppressed_exception_handler_targets.remove(
                                handler_offset
                            )
                    finalbody = self._capture_exceptional_finally(
                        handler_offset,
                        loop,
                    )
                    statement = ast.Try(
                        body=[statement] + continuation,
                        handlers=[],
                        orelse=[],
                        finalbody=finalbody or [ast.Pass()],
                    )
                    next_index = self._handler_cleanup_end(
                        handler_index + 1,
                        handler_index,
                    )
                    return statement, next_index
                if (
                    next_index == protected_end == handler_index
                ):
                    embedded_finally = (
                        self._extract_handler_return_finally(statement)
                    )
                    if embedded_finally is not None:
                        statement = ast.Try(
                            body=[statement],
                            handlers=[],
                            orelse=[],
                            finalbody=embedded_finally,
                        )
                        next_index = self._handler_cleanup_end(
                            handler_index + 1,
                            handler_index,
                        )
                        return statement, next_index
                if not next_index <= protected_end < handler_index:
                    self._error("Finally suite has no normal-path body")
                normal_return_index = next(
                    (
                        index
                        for index in range(protected_end, handler_index)
                        if self.tokens[index].kind == "RETURN_VALUE"
                    ),
                    None,
                )
                held_return = (
                    normal_return_index is not None
                    and self.owner._latch_expression_start(
                        protected_end,
                        normal_return_index,
                    )
                    >= normal_return_index
                )
                expression_start = (
                    self.owner._latch_expression_start(
                        next_index,
                        protected_end,
                    )
                    if held_return
                    else protected_end
                )
                if expression_start < protected_end:
                    continuation = self._capture_before_handler(
                        next_index,
                        expression_start,
                        handler_offset,
                        loop,
                    )
                    continuation.append(
                        ast.Return(
                            value=self.owner._expression_slice(
                                expression_start,
                                protected_end,
                            )
                        )
                    )
                else:
                    continuation = self._capture_before_handler(
                        next_index,
                        protected_end,
                        handler_offset,
                        loop,
                    )
                protected_statement = ast.Try(
                    body=[statement] + continuation,
                    handlers=[],
                    orelse=[],
                    finalbody=[],
                )
                finalbody_end = handler_index
                if (
                    not any(
                        isinstance(node, ast.Return)
                        for node in ast.walk(protected_statement)
                    )
                    and finalbody_end >= protected_end + 2
                    and self.tokens[finalbody_end - 2].kind == "LOAD_CONST"
                    and self.tokens[finalbody_end - 2].attr is None
                    and self.tokens[finalbody_end - 2].linestart is None
                    and self.tokens[finalbody_end - 1].kind
                    == "RETURN_VALUE"
                ):
                    # An implicit function return follows the normal finally
                    # copy.  It is not a source statement in the finalbody
                    # and must not suppress an exception raised by the try.
                    finalbody_end -= 2
                finalbody = self._capture_deferred_return_finally(
                    protected_end,
                    finalbody_end,
                    handler_offset,
                    loop,
                    protected_statement,
                )
                statement = ast.Try(
                    body=[statement] + continuation,
                    handlers=[],
                    orelse=[],
                    finalbody=finalbody or [ast.Pass()],
                )
                next_index = self._handler_cleanup_end(
                    handler_index + 1,
                    handler_index,
                )
            else:
                finalbody = self._capture_optional(
                    next_index,
                    jump_index,
                    loop,
                )
                statement = ast.Try(
                    body=statement.body,
                    handlers=statement.handlers,
                    orelse=statement.orelse,
                    finalbody=finalbody or [ast.Pass()],
                )
                next_index = self.offset_to_index[
                    instruction_target(self.tokens[jump_index])
                ]
        return statement, next_index

    def _match_empty_except_star_clause(
        self,
        body_start: int,
        false_index: int,
        prep_index: int,
        name: Optional[str],
    ) -> bool:
        """Match only CPython 3.11's canonical empty ``except*`` body."""
        if not (
            0 < body_start < false_index < prep_index < len(self.tokens)
            and self.tokens[prep_index].kind == "PREP_RERAISE_STAR"
            and self.tokens[false_index].kind == "POP_TOP"
        ):
            return False

        continuation = false_index + 1
        if continuation >= prep_index:
            return False
        continuation_token = self.tokens[continuation]
        if continuation_token.kind == "LIST_APPEND":
            if continuation_token.attr != 1 or continuation + 1 != prep_index:
                return False
        elif not any(
            token.kind == "CHECK_EG_MATCH"
            for token in self.tokens[continuation:prep_index]
        ):
            return False

        def forward_jump_to(index: int, target_index: int) -> bool:
            if not (0 <= index < target_index < prep_index):
                return False
            token = self.tokens[index]
            if token.kind != "JUMP_FORWARD":
                return False
            target = instruction_target(token)
            return (
                isinstance(target, int)
                and target > token.offset
                and self.offset_to_index.get(target) == target_index
            )

        binding = self.tokens[body_start - 1]
        if name is None:
            normal_join = body_start + 4
            if false_index != body_start + 5:
                return False
            if binding.kind != "POP_TOP":
                return False
            if [
                token.kind
                for token in self.tokens[body_start : false_index + 1]
            ] != [
                "JUMP_FORWARD",
                "LIST_APPEND",
                "POP_TOP",
                "JUMP_FORWARD",
                "JUMP_FORWARD",
                "POP_TOP",
            ]:
                return False
            if self.tokens[body_start + 1].attr != 3:
                return False
            return (
                forward_jump_to(body_start, normal_join)
                and forward_jump_to(body_start + 3, continuation)
                and forward_jump_to(normal_join, continuation)
            )

        normal_join = body_start + 10
        if false_index != body_start + 11:
            return False
        expected_kinds = [
            "LOAD_CONST",
            None,
            None,
            "JUMP_FORWARD",
            "LOAD_CONST",
            None,
            None,
            "LIST_APPEND",
            "POP_TOP",
            "JUMP_FORWARD",
            "JUMP_FORWARD",
            "POP_TOP",
        ]
        clause_tokens = self.tokens[body_start : false_index + 1]
        if len(clause_tokens) != len(expected_kinds):
            return False
        if any(
            expected is not None and token.kind != expected
            for token, expected in zip(clause_tokens, expected_kinds)
        ):
            return False
        if (
            binding.kind not in (
                "STORE_FAST",
                "STORE_DEREF",
                "STORE_GLOBAL",
                "STORE_NAME",
            )
            or self.tokens[body_start].attr is not None
            or self.tokens[body_start + 4].attr is not None
            or self.tokens[body_start + 7].attr != 3
        ):
            return False

        def token_name(token) -> Optional[str]:
            value = token.attr if isinstance(token.attr, str) else token.pattr
            return value if isinstance(value, str) else None

        delete_kind = "DELETE_" + binding.kind[len("STORE_") :]
        for store_index, delete_index in (
            (body_start + 1, body_start + 2),
            (body_start + 5, body_start + 6),
        ):
            store = self.tokens[store_index]
            delete = self.tokens[delete_index]
            if (
                store.kind != binding.kind
                or delete.kind != delete_kind
                or token_name(store) != name
                or token_name(delete) != name
            ):
                return False
        if token_name(binding) != name:
            return False
        return (
            forward_jump_to(body_start + 3, normal_join)
            and forward_jump_to(body_start + 9, continuation)
            and forward_jump_to(normal_join, continuation)
        )

    def _match_terminal_except_star_cleanup(
        self,
        prep_index: int,
        handler_index: int,
    ) -> Optional[int]:
        """Match CPython 3.11's implicit-return ``except*`` cleanup."""
        terminal_end = prep_index + 13
        if terminal_end != len(self.tokens):
            return None
        cleanup = self.tokens[prep_index:terminal_end]
        expected_kinds = (
            "PREP_RERAISE_STAR",
            "COPY_STACK",
            "POP_JUMP_FORWARD_IF_NOT_NONE",
            "POP_TOP",
            "POP_EXCEPT",
            "LOAD_CONST",
            "RETURN_VALUE",
            "SWAP_STACK",
            "POP_EXCEPT",
            "RERAISE",
            "COPY_STACK",
            "POP_EXCEPT",
            "RERAISE",
        )
        for token, expected_kind in zip(cleanup, expected_kinds):
            if token.kind != expected_kind:
                return None
        if (
            cleanup[1].attr != 1
            or cleanup[5].attr is not None
            or cleanup[5].linestart is not None
            or cleanup[6].linestart is not None
            or cleanup[7].attr != 2
            or cleanup[9].attr != 0
            or cleanup[10].attr != 3
            or cleanup[12].attr != 1
        ):
            return None
        if instruction_target(cleanup[2]) != cleanup[7].offset:
            return None

        handler_offset = self.tokens[handler_index].offset
        protected_end = cleanup[4].offset
        outer_cleanup = cleanup[10].offset
        cleanup_regions = []
        first_protected = None
        last_protected = None
        for region in self.entries:
            if not (
                region.depth == 1
                and region.lasti
                and handler_offset <= region.start < protected_end
                and region.end <= protected_end
            ):
                continue
            if region.target != outer_cleanup:
                return None
            cleanup_regions.append(region)
            first_protected = (
                region.start
                if first_protected is None
                else min(first_protected, region.start)
            )
            last_protected = (
                region.end
                if last_protected is None
                else max(last_protected, region.end)
            )
        if (
            not cleanup_regions
            or first_protected != handler_offset
            or last_protected != protected_end
        ):
            return None
        return terminal_end

    def _try_except_star(self, entry, loop) -> Tuple[ast.TryStar, int]:
        start = self.offset_to_index[entry.start]
        try_end = self.offset_to_index[entry.end]
        handler_index = self.offset_to_index[entry.target]
        self._remember_exception_state(entry)
        body = self._capture_protected(start, try_end, loop)

        cursor = handler_index + 1
        while (
            cursor < len(self.tokens)
            and self.tokens[cursor].kind
            in ("BUILD_LIST", "COPY_STACK", "SWAP_STACK")
        ):
            cursor += 1

        handlers = []
        prep_index = next(
            (
                index
                for index in range(cursor, len(self.tokens))
                if self.tokens[index].kind == "PREP_RERAISE_STAR"
            ),
            None,
        )
        if prep_index is None:
            self._error("except* handler has no PREP_RERAISE_STAR")

        while cursor < prep_index:
            while (
                cursor < prep_index
                and self.tokens[cursor].kind == "LIST_APPEND"
            ):
                cursor += 1
            if cursor >= prep_index:
                break
            check_index = next(
                (
                    index
                    for index in range(cursor, prep_index)
                    if self.tokens[index].kind == "CHECK_EG_MATCH"
                ),
                None,
            )
            if check_index is None:
                self._error("except* clause has no CHECK_EG_MATCH")
            exception_type = self.owner._expression_slice(
                cursor,
                check_index,
            )
            if (
                check_index + 3 >= len(self.tokens)
                or self.tokens[check_index + 1].kind != "COPY_STACK"
                or not self.tokens[check_index + 2].kind.startswith(
                    "POP_JUMP_"
                )
            ):
                self._error("CHECK_EG_MATCH has no subgroup branch")
            false_index = self.offset_to_index[
                instruction_target(self.tokens[check_index + 2])
            ]
            binding = self.tokens[check_index + 3]
            if binding.kind.startswith("STORE_"):
                name = (
                    binding.attr
                    if isinstance(binding.attr, str)
                    else binding.pattr
                )
            elif binding.kind == "POP_TOP":
                name = None
            else:
                self._error("except* clause has no binding or POP_TOP")
            body_start = check_index + 4
            clause_region = next(
                (
                    region
                    for region in self.entries
                    if region.start == self.tokens[body_start].offset
                    and region.depth >= 4
                ),
                None,
            )
            if clause_region is not None:
                body_end = self.offset_to_index[clause_region.end]
                clause_body = self._capture_optional(
                    body_start,
                    body_end,
                    loop,
                )
            elif self._match_empty_except_star_clause(
                body_start,
                false_index,
                prep_index,
                name,
            ):
                clause_body = [ast.Pass()]
            else:
                self._error(
                    "except* clause body has neither a protected region "
                    "nor a valid empty-body protocol",
                    offset=self.tokens[body_start].offset,
                )
            handlers.append(
                ast.ExceptHandler(
                    type=exception_type,
                    name=name,
                    body=clause_body or [ast.Pass()],
                )
            )
            cursor = false_index + 1

        if not handlers:
            self._error("except* protocol contains no clauses")
        join_jump = next(
            (
                index
                for index in range(prep_index, len(self.tokens))
                if self.tokens[index].kind == "JUMP_FORWARD"
            ),
            None,
        )
        if join_jump is None:
            terminal_end = self._match_terminal_except_star_cleanup(
                prep_index,
                handler_index,
            )
            if terminal_end is None:
                self._error(
                    "except* cleanup has neither a normal continuation "
                    "nor a valid terminal protocol",
                    offset=self.tokens[prep_index].offset,
                )
            return (
                ast.TryStar(
                    body=body or [ast.Pass()],
                    handlers=handlers,
                    orelse=[],
                    finalbody=[],
                ),
                terminal_end,
            )
        join_offset = instruction_target(self.tokens[join_jump])
        join_index = self.offset_to_index[join_offset]

        normal_jump = next(
            (
                index
                for index in range(try_end, handler_index)
                if self.tokens[index].kind == "JUMP_FORWARD"
            ),
            None,
        )
        orelse = []
        if normal_jump is not None:
            else_offset = instruction_target(self.tokens[normal_jump])
            if else_offset != join_offset:
                else_index = self.offset_to_index.get(else_offset)
                if (
                    else_index is None
                    or not prep_index < else_index < join_index
                ):
                    self._error(
                        "except* else suite has an invalid normal-path jump"
                    )
                orelse = self._capture_suppressed(
                    else_index,
                    join_index,
                    loop,
                )

        finalbody = []
        outer_finally = None
        for candidate in self.entries:
            target_index = self.offset_to_index[candidate.target]
            if (
                candidate is not entry
                and not candidate.lasti
                and self.tokens[prep_index].offset
                <= candidate.start
                < candidate.end
                <= self.tokens[join_index].offset
                and self.tokens[target_index].kind == "PUSH_EXC_INFO"
            ):
                outer_finally = candidate
                break
        if outer_finally is not None:
            final_handler_index = self.offset_to_index[
                outer_finally.target
            ]
            finalbody_end = final_handler_index
            if finalbody_end <= join_index:
                self._error("except* finally suite has no normal-path body")
            next_index = self._handler_cleanup_end(
                final_handler_index + 1,
                final_handler_index,
            )
            terminal = self.tokens[finalbody_end - 1]
            if terminal.kind == "JUMP_FORWARD":
                target = instruction_target(terminal)
                is_break = (
                    loop is not None and target == loop.break_target
                )
                if target > terminal.offset and not is_break:
                    finalbody_end -= 1
                    next_index = self.offset_to_index[target]
            finalbody = self._capture_suppressed(
                join_index,
                finalbody_end,
                loop,
            )
            join_index = next_index

        return (
            ast.TryStar(
                body=body or [ast.Pass()],
                handlers=handlers,
                orelse=orelse,
                finalbody=finalbody,
            ),
            join_index,
        )

    def _try_finally(self, entry, loop) -> Tuple[ast.Try, int]:
        start = self.offset_to_index[entry.start]
        try_end = self.offset_to_index[entry.end]
        handler_index = self.offset_to_index[entry.target]
        self._remember_exception_state(entry)
        fragments = []
        for candidate in self.entries:
            candidate_start = self.offset_to_index[candidate.start]
            if (
                candidate.target == entry.target
                and candidate.depth == entry.depth
                and candidate.lasti == entry.lasti
                and start <= candidate_start < handler_index
            ):
                fragments.append(candidate)
        fragments = sorted(
            fragments,
            key=lambda candidate: self.offset_to_index[candidate.start],
        )
        fragmented_end = (
            max(
                self.offset_to_index[candidate.end]
                for candidate in fragments
            )
            if fragments
            else try_end
        )
        exceptional_finally = self._exceptional_finally_body(entry.target)
        duplicated_finally = self._duplicated_finally_offsets(
            start,
            handler_index,
            entry.target,
        )
        if (
            len(fragments) > 1
            and exceptional_finally is not None
            and duplicated_finally
        ):
            added_protocol = (
                duplicated_finally
                - self.owner._suppressed_exception_protocol_offsets
            )
            self.owner._suppressed_exception_protocol_offsets.update(
                added_protocol
            )
            try:
                body = self._capture_protected_fragments(
                    start,
                    handler_index,
                    loop,
                    fragments,
                )
            finally:
                self.owner._suppressed_exception_protocol_offsets.difference_update(
                    added_protocol
                )
            finalbody = self._capture_exceptional_finally(
                entry.target,
                loop,
            )
            cleanup_end = self._handler_cleanup_end(
                handler_index + 1,
                handler_index,
            )
            return (
                ast.Try(
                    body=body or [ast.Pass()],
                    handlers=[],
                    orelse=[],
                    finalbody=finalbody or [ast.Pass()],
                ),
                cleanup_end,
            )
        if len(fragments) > 1 and any(
            self.tokens[index].kind
            in ("BEFORE_WITH", "BEFORE_ASYNC_WITH", "PUSH_EXC_INFO")
            for index in range(start, fragmented_end)
        ):
            held_return = any(
                self.tokens[index].kind == "RETURN_VALUE"
                for index in range(fragmented_end, handler_index)
            )
            body = self._capture_protected_fragments(
                start,
                fragmented_end,
                loop,
                fragments,
                trailing_return=held_return,
            )
            cleanup_end = self._handler_cleanup_end(
                handler_index + 1,
                handler_index,
            )
            normal_end = handler_index
            next_index = cleanup_end
            terminal = self.tokens[handler_index - 1]
            if terminal.kind == "JUMP_FORWARD":
                target = instruction_target(terminal)
                is_break = (
                    loop is not None and target == loop.break_target
                )
                if target > terminal.offset and not is_break:
                    normal_end -= 1
                    next_index = self.offset_to_index[target]
            protected_statement = ast.Try(
                body=body or [ast.Pass()],
                handlers=[],
                orelse=[],
                finalbody=[],
            )
            finalbody = self._capture_deferred_return_finally(
                fragmented_end,
                normal_end,
                entry.target,
                loop,
                protected_statement,
            )
            return (
                ast.Try(
                    body=body or [ast.Pass()],
                    handlers=[],
                    orelse=[],
                    finalbody=finalbody or [ast.Pass()],
                ),
                next_index,
            )
        if len(fragments) > 1:
            body = []
            for position, fragment in enumerate(fragments):
                fragment_start = self.offset_to_index[fragment.start]
                fragment_end = self.offset_to_index[fragment.end]
                boundary = (
                    self.offset_to_index[fragments[position + 1].start]
                    if position + 1 < len(fragments)
                    else handler_index
                )
                has_deferred_return = any(
                    self.tokens[index].kind == "RETURN_VALUE"
                    for index in range(fragment_end, boundary)
                )
                fragment_body = None
                if has_deferred_return:
                    return_index = max(
                        index
                        for index in range(fragment_end, boundary)
                        if self.tokens[index].kind == "RETURN_VALUE"
                    )
                    iterator_return_constant = (
                        return_index - 1
                        if loop is not None
                        and return_index > fragment_end
                        and self.tokens[return_index - 1].kind
                        == "LOAD_CONST"
                        and any(
                            self.tokens[index].kind == "POP_TOP"
                            for index in range(
                                fragment_end,
                                return_index - 1,
                            )
                        )
                        else None
                    )
                    protocol_offsets = {
                        self.tokens[index].offset
                        for index in range(fragment_end, boundary)
                        if index
                        not in (return_index, iterator_return_constant)
                    }
                    added_protocol = (
                        protocol_offsets
                        - self.owner._suppressed_exception_protocol_offsets
                    )
                    added_start = (
                        fragment.start
                        not in self.owner._suppressed_exception_starts
                    )
                    self.owner._suppressed_exception_protocol_offsets.update(
                        added_protocol
                    )
                    self.owner._suppressed_exception_starts.add(
                        fragment.start
                    )
                    try:
                        fragment_body = self._capture_optional(
                            fragment_start,
                            boundary,
                            loop,
                        )
                    finally:
                        self.owner._suppressed_exception_protocol_offsets.difference_update(
                            added_protocol
                        )
                        if added_start:
                            self.owner._suppressed_exception_starts.remove(
                                fragment.start
                            )
                if fragment_body is None:
                    fragment_body = self._capture_protected(
                        fragment_start,
                        fragment_end,
                        loop,
                    )
                body.extend(fragment_body)

            cleanup_end = self._handler_cleanup_end(
                handler_index + 1,
                handler_index,
            )
            normal_end = handler_index
            next_index = cleanup_end
            terminal = self.tokens[handler_index - 1]
            if terminal.kind == "JUMP_FORWARD":
                target = instruction_target(terminal)
                is_break = (
                    loop is not None and target == loop.break_target
                )
                if target > terminal.offset and not is_break:
                    normal_end -= 1
                    next_index = self.offset_to_index[target]
            protected_statement = ast.Try(
                body=body or [ast.Pass()],
                handlers=[],
                orelse=[],
                finalbody=[],
            )
            finalbody = self._capture_deferred_return_finally(
                self.offset_to_index[fragments[-1].end],
                normal_end,
                entry.target,
                loop,
                protected_statement,
            )
            return (
                ast.Try(
                    body=body or [ast.Pass()],
                    handlers=[],
                    orelse=[],
                    finalbody=finalbody or [ast.Pass()],
                ),
                next_index,
            )
        normal_return_index = next(
            (
                index
                for index in range(try_end, handler_index)
                if self.tokens[index].kind == "RETURN_VALUE"
            ),
            None,
        )
        finalbody_expression_start = (
            self.owner._latch_expression_start(
                try_end,
                normal_return_index,
            )
            if normal_return_index is not None
            else None
        )
        has_local_finalbody_return = False
        if (
            finalbody_expression_start is not None
            and finalbody_expression_start < normal_return_index
        ):
            try:
                self.owner._expression_slice(
                    finalbody_expression_start,
                    normal_return_index,
                )
            except Python311ParseError:
                pass
            else:
                has_local_finalbody_return = True
        return_uses_protected_value = (
            normal_return_index is not None
            and (
                not has_local_finalbody_return
                or self.tokens[normal_return_index - 1].kind
                in ("POP_TOP", "POP_EXCEPT")
            )
        )
        body = (
            self._capture_protected_return(start, try_end, loop)
            if return_uses_protected_value
            else None
        )
        protected_return = body is not None
        if body is None:
            body = self._capture_protected(start, try_end, loop)
        protected_return = protected_return or any(
            isinstance(node, ast.Return)
            for statement in body
            for node in ast.walk(statement)
        )
        cleanup_end = self._handler_cleanup_end(
            handler_index + 1,
            handler_index,
        )
        finalbody_end = handler_index
        next_index = cleanup_end
        enclosing_finally_targets = set()
        for region in self.entries:
            region_start = self.offset_to_index[region.start]
            target_index = self.offset_to_index[region.target]
            if (
                region.depth == 0
                and try_end <= region_start < handler_index
                and region.target > entry.target
                and region.target
                not in self.owner._suppressed_exception_handler_targets
                and self.tokens[target_index].kind == "PUSH_EXC_INFO"
                and not self._handler_has_match(target_index)
            ):
                enclosing_finally_targets.add(region.target)
        enclosing_finally_targets = sorted(enclosing_finally_targets)
        outer_handler_offset = (
            enclosing_finally_targets[0]
            if enclosing_finally_targets
            else None
        )
        normal_outer_boundary = None
        if outer_handler_offset is not None:
            normal_outer_boundary = max(
                self.offset_to_index[region.end]
                for region in self.entries
                if region.depth == 0
                and region.target == outer_handler_offset
                and self.offset_to_index[region.start] < handler_index
            )
            if normal_outer_boundary < handler_index:
                finalbody_end = min(
                    finalbody_end,
                    normal_outer_boundary,
                )

        # CPython duplicates the finally suite: one normal-path copy lies
        # immediately before PUSH_EXC_INFO and a second copy handles an active
        # exception.  A fall-through suite ends in a forward jump over the
        # exceptional copy; suites containing return/break/continue instead
        # terminate directly and have no such join jump.
        if try_end < handler_index:
            terminal = self.tokens[handler_index - 1]
            if terminal.kind == "JUMP_FORWARD":
                target = instruction_target(terminal)
                is_break = (
                    loop is not None and target == loop.break_target
                )
                if target > terminal.offset and not is_break:
                    finalbody_end -= 1
                    next_index = self.offset_to_index[target]
        finalbody_return = None
        finalbody_protocol_offsets = set()
        if (
            normal_return_index is not None
            and normal_return_index < finalbody_end
        ):
            swap_index = normal_return_index - 2
            if (
                swap_index >= try_end
                and self.tokens[swap_index].kind == "SWAP_STACK"
                and self.tokens[swap_index].attr == 2
                and self.tokens[swap_index + 1].kind == "POP_TOP"
            ):
                expression_start = self.owner._latch_expression_start(
                    try_end,
                    swap_index,
                )
                if expression_start < swap_index:
                    finalbody_return = self.owner._expression_slice(
                        expression_start,
                        swap_index,
                    )
                    finalbody_end = expression_start
                else:
                    finalbody_end = normal_return_index
            elif protected_return:
                # RETURN_VALUE consumes the value computed in the protected
                # region.  That source-level return was already restored to
                # ``body`` above, so it is not part of the finally suite.  A
                # conditional finally copy can have one RETURN_VALUE per
                # physical branch; retain the whole branch shape while hiding
                # those terminal stack operations.
                cursor = normal_return_index
                while cursor < handler_index:
                    if self.tokens[cursor].kind == "RETURN_VALUE":
                        swap_index = cursor - 2
                        finally_overrides_return = (
                            swap_index >= try_end
                            and self.tokens[swap_index].kind == "SWAP_STACK"
                            and self.tokens[swap_index].attr == 2
                            and self.tokens[swap_index + 1].kind == "POP_TOP"
                        )
                        if not finally_overrides_return:
                            finalbody_protocol_offsets.add(
                                self.tokens[cursor].offset
                            )
                    cursor += 1
                finalbody_end = normal_return_index + 1
            else:
                terminal_none_index = normal_return_index - 1
                terminal_implicit_none = (
                    terminal_none_index >= try_end
                    and self.tokens[terminal_none_index].kind
                    == "LOAD_CONST"
                    and self.tokens[terminal_none_index].attr is None
                    and (
                        self.tokens[terminal_none_index].linestart is None
                        or cleanup_end == len(self.tokens)
                    )
                )
                if terminal_implicit_none:
                    finalbody_end = terminal_none_index
                else:
                    expression_start = self.owner._latch_expression_start(
                        try_end,
                        normal_return_index,
                    )
                    if expression_start < normal_return_index:
                        finalbody_return = self.owner._expression_slice(
                            expression_start,
                            normal_return_index,
                        )
                        finalbody_end = expression_start
                    else:
                        finalbody_end = normal_return_index
        added_protocol_offsets = (
            finalbody_protocol_offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        self.owner._suppressed_exception_protocol_offsets.update(
            added_protocol_offsets
        )
        try:
            finalbody = self._capture_before_handler(
                try_end,
                finalbody_end,
                entry.target,
                loop,
            )
        finally:
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added_protocol_offsets
            )
        if finalbody_return is not None:
            finalbody.append(ast.Return(value=finalbody_return))
        self._move_normal_return_before_finally(
            body,
            finalbody,
            handler_index,
            cleanup_end,
        )
        statement = ast.Try(
            body=body or [ast.Pass()],
            handlers=[],
            orelse=[],
            finalbody=finalbody or [ast.Pass()],
        )

        # Nested cleanup protocols split an enclosing finally-protected body
        # into several exception-table entries.  Rejoin the remaining normal
        # body and its source-level finally suite before consuming the outer
        # exceptional copy.
        if outer_handler_offset is not None:
            outer_handler_index = self.offset_to_index[
                outer_handler_offset
            ]
            if normal_outer_boundary < handler_index:
                outer_finalbody = self._capture_deferred_return_finally(
                    normal_outer_boundary,
                    handler_index,
                    outer_handler_offset,
                    loop,
                    statement,
                )
                self._move_normal_return_before_finally(
                    statement.body,
                    outer_finalbody,
                    outer_handler_index,
                    self._handler_cleanup_end(
                        outer_handler_index + 1,
                        outer_handler_index,
                    ),
                )
                statement = ast.Try(
                    body=[statement],
                    handlers=[],
                    orelse=[],
                    finalbody=outer_finalbody or [ast.Pass()],
                )
                next_index = self._handler_cleanup_end(
                    outer_handler_index + 1,
                    outer_handler_index,
                )
            else:
                protected_end = max(
                    self.offset_to_index[region.end]
                    for region in self.entries
                    if region.depth == 0
                    and region.target == outer_handler_offset
                )
            if (
                normal_outer_boundary >= handler_index
                and next_index <= protected_end < outer_handler_index
            ):
                continuation = self._capture_before_handler(
                    next_index,
                    protected_end,
                    outer_handler_offset,
                    loop,
                )
                outer_finalbody = self._capture_before_handler(
                    protected_end,
                    outer_handler_index,
                    outer_handler_offset,
                    loop,
                )
                statement = ast.Try(
                    body=[statement] + continuation,
                    handlers=[],
                    orelse=[],
                    finalbody=outer_finalbody or [ast.Pass()],
                )
                next_index = self._handler_cleanup_end(
                    outer_handler_index + 1,
                    outer_handler_index,
                )

        # A source ``try: try: ... finally: ... except: ...`` is split into
        # adjacent exception-table protocols.  Once the inner finally cleanup
        # has been consumed, the enclosing except handler either begins
        # immediately or follows its normal-path jump-over edge.
        outer_handler_index = next_index
        normal_except_join = None
        if (
            next_index + 1 < len(self.tokens)
            and self.tokens[next_index].kind == "JUMP_FORWARD"
            and self.tokens[next_index + 1].kind == "PUSH_EXC_INFO"
            and self._handler_has_match(next_index + 1)
        ):
            outer_handler_index = next_index + 1
            normal_except_join = instruction_target(
                self.tokens[next_index]
            )
        if (
            outer_handler_index < len(self.tokens)
            and self.tokens[outer_handler_index].kind == "PUSH_EXC_INFO"
            and self._handler_has_match(outer_handler_index)
        ):
            outer_handlers, outer_end, outer_join = self._parse_handlers(
                outer_handler_index,
                loop,
                normal_except_join,
            )
            statement = ast.Try(
                body=[statement],
                handlers=outer_handlers,
                orelse=[],
                finalbody=[],
            )
            next_index = (
                self.offset_to_index[outer_join]
                if outer_join is not None
                else self.offset_to_index.get(
                    normal_except_join,
                    outer_end,
                )
            )

        return statement, next_index

    def _terminal_return_try_without_protected_entry(self, index: int, loop):
        """Recover ``try: return`` when CPython omits its empty range.

        A constant ``return`` cannot raise, so CPython 3.11 emits the source
        handler protocol but no exception-table entry targeting it.  Accept
        only the compiler's closed, unreachable-handler shape; arbitrary dead
        ``PUSH_EXC_INFO`` sequences must continue to fail closed.
        """
        if (
            self.tokens[index].kind != "NOP"
            or index + 3 >= len(self.tokens)
            or self.tokens[index + 1].kind != "LOAD_CONST"
            or self.tokens[index + 1].attr is not None
            or self.tokens[index + 2].kind != "RETURN_VALUE"
            or self.tokens[index + 3].kind != "PUSH_EXC_INFO"
        ):
            return None

        handler_index = index + 3
        handler_offset = self.tokens[handler_index].offset
        handler_block = self.owner.cfg.offset_to_block.get(handler_offset)
        if (
            handler_block is None
            or self.owner.cfg.incoming(handler_block)
            or any(entry.target == handler_offset for entry in self.entries)
            or not (
                self._handler_has_match(handler_index)
                or self.tokens[handler_index + 1].kind == "POP_TOP"
            )
        ):
            return None

        cleanup_entries = [
            entry
            for entry in self.entries
            if entry.lasti
            and handler_offset <= entry.start
            and entry.target > handler_offset
        ]
        if not cleanup_entries:
            return None
        handlers, cleanup_end, join = self._parse_handlers(
            handler_index,
            loop,
            None,
        )
        if join is not None:
            self._error(
                "Entry-less terminal try handler has an unexpected join",
                handler_offset,
            )
        cleanup_offset = (
            self.tokens[cleanup_end].offset
            if cleanup_end < len(self.tokens)
            else self.tokens[-1].offset + 2
        )
        if any(
            entry.end > cleanup_offset
            for entry in cleanup_entries
        ):
            self._error(
                "Entry-less terminal try handler crosses its cleanup",
                handler_offset,
            )
        return (
            ast.Try(
                body=[ast.Return(value=None)],
                handlers=handlers,
                orelse=[],
                finalbody=[],
            ),
            cleanup_end,
        )

    def try_statement(self, index: int, loop):
        terminal = self._terminal_return_try_without_protected_entry(
            index,
            loop,
        )
        if terminal is not None:
            statement, next_index = terminal
            self.owner.body.append(statement)
            return next_index
        offset = self.tokens[index].offset
        entries = [
            entry
            for entry in self.entries
            if entry.start == offset
            and not entry.lasti
            and self.tokens[self.offset_to_index[entry.target]].kind
            == "PUSH_EXC_INFO"
        ]
        if not entries:
            return None
        entry = min(entries, key=lambda item: (item.end, item.target))
        handler_index = self.offset_to_index[entry.target]
        if self._handler_has_group_match(handler_index):
            statement, next_index = self._try_except_star(entry, loop)
        elif (
            self._handler_has_match(handler_index)
            or self._handler_is_bare(entry)
        ):
            statement, next_index = self._try_except(entry, loop)
        else:
            statement, next_index = self._try_finally(entry, loop)
        self.owner.body.append(statement)
        return next_index

    def _with_cleanup_protocol_offsets(self, start: int, end: int):
        """Find normal-path ``__exit__``/``__aexit__`` call instructions."""
        offsets = set()
        cursor = start
        while cursor + 4 < end:
            constants = self.tokens[cursor : cursor + 3]
            if not all(
                token.kind == "LOAD_CONST"
                and token.attr is None
                for token in constants
            ):
                cursor += 1
                continue
            precall = cursor + 3
            call = cursor + 4
            if (
                self.tokens[precall].kind != "PRECALL"
                or self.tokens[call].kind != "CALL"
            ):
                cursor += 1
                continue

            protocol_start = cursor
            if (
                cursor > start
                and self.tokens[cursor - 1].kind == "SWAP_STACK"
                and self.tokens[cursor - 1].attr == 2
            ):
                protocol_start -= 1

            protocol_end = call + 1
            if (
                protocol_end < end
                and self.tokens[protocol_end].kind == "GET_AWAITABLE"
            ):
                pop_top = next(
                    (
                        index
                        for index in range(protocol_end + 1, end)
                        if self.tokens[index].kind == "POP_TOP"
                    ),
                    None,
                )
                if pop_top is None:
                    cursor += 1
                    continue
                protocol_end = pop_top + 1
            elif (
                protocol_end < end
                and self.tokens[protocol_end].kind == "POP_TOP"
            ):
                protocol_end += 1
            else:
                cursor += 1
                continue

            offsets.update(
                self.tokens[index].offset
                for index in range(protocol_start, protocol_end)
            )
            cursor = protocol_end
        return offsets

    def _with_fragments(self, protected, limit_index: int):
        """Return source-body fragments sharing one with-handler target."""
        start_index = self.offset_to_index[protected.start]
        fragments = []
        for entry in self.entries:
            entry_start = self.offset_to_index[entry.start]
            if (
                entry.target == protected.target
                and entry.depth == protected.depth
                and entry.lasti == protected.lasti
                and start_index <= entry_start < limit_index
            ):
                fragments.append(entry)
        return sorted(
            fragments,
            key=lambda entry: self.offset_to_index[entry.start],
        )

    def _with_suppressed_continuation(self, handler_index: int):
        """Return the continuation after a truthy ``__exit__`` result."""
        conditional = next(
            (
                index
                for index in range(
                    handler_index,
                    min(handler_index + 8, len(self.tokens)),
                )
                if self.tokens[index].kind.startswith(
                    "POP_JUMP_FORWARD_IF_TRUE"
                )
            ),
            None,
        )
        if conditional is None:
            return None
        suppressed = self.offset_to_index.get(
            instruction_target(self.tokens[conditional])
        )
        if suppressed is None:
            return None
        expected = ("POP_TOP", "POP_EXCEPT", "POP_TOP", "POP_TOP")
        if tuple(
            token.kind
            for token in self.tokens[suppressed : suppressed + 4]
        ) != expected:
            return None
        return suppressed + 4

    def _with_body(
        self,
        start: int,
        end: int,
        loop,
        fragments,
        trailing_return: bool = False,
    ):
        fragment_ranges = [
            (
                self.offset_to_index[entry.start],
                self.offset_to_index[entry.end],
            )
            for entry in fragments
        ]
        gaps = []
        for (_, left_end), (right_start, _) in zip(
            fragment_ranges,
            fragment_ranges[1:],
        ):
            if left_end < right_start:
                gaps.append((left_end, right_start))
        if fragment_ranges and fragment_ranges[-1][1] < end:
            gaps.append((fragment_ranges[-1][1], end))

        protocol_offsets = set()
        for gap_start, gap_end in gaps:
            protocol_offsets.update(
                self._with_cleanup_protocol_offsets(gap_start, gap_end)
            )
        fragment_starts = {
            entry.start for entry in fragments
        }
        added_starts = (
            fragment_starts - self.owner._suppressed_exception_starts
        )
        added_protocol = (
            protocol_offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        self.owner._suppressed_exception_starts.update(added_starts)
        self.owner._suppressed_exception_protocol_offsets.update(
            added_protocol
        )
        try:
            return self._capture_optional(
                start,
                end,
                loop,
                trailing_return=trailing_return,
            )
        finally:
            self.owner._suppressed_exception_starts.difference_update(
                added_starts
            )
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added_protocol
            )

    def with_statement(self, before_index: int, end: int, loop):
        before = self.tokens[before_index]
        if before.kind not in ("BEFORE_WITH", "BEFORE_ASYNC_WITH"):
            return None
        context = self.owner._pop_expr()
        groups = []
        contexts = []
        cursor = before_index
        starts_nested_suite = True
        fragment_limit = None

        while True:
            is_async = self.tokens[cursor].kind == "BEFORE_ASYNC_WITH"
            if is_async:
                send_index = next(
                    index
                    for index in range(cursor + 1, end)
                    if self.tokens[index].kind == "SEND"
                )
                store_index = self.offset_to_index[
                    instruction_target(self.tokens[send_index])
                ]
            else:
                store_index = cursor + 1
            target, body_start = self.owner._for_target(store_index)
            item = ast.withitem(
                context_expr=context,
                optional_vars=target,
            )
            if (
                not groups
                or starts_nested_suite
                or groups[-1][0] != is_async
            ):
                groups.append((is_async, [item]))
            else:
                groups[-1][1].append(item)
            protected = next(
                (
                    entry
                    for entry in self.entries
                    if entry.start == self.tokens[store_index].offset
                    and entry.lasti
                ),
                None,
            )
            if protected is None:
                self._error("With statement has no protected exception region")
            self._remember_exception_state(protected)
            contexts.append(protected)
            protected_end = self.offset_to_index[protected.end]
            nested_before = next(
                (
                    index
                    for index in range(body_start, protected_end)
                    if self.tokens[index].kind
                    in ("BEFORE_WITH", "BEFORE_ASYNC_WITH")
                ),
                None,
            )
            if nested_before is None:
                break
            starts_nested_suite = any(
                token.linestart is not None
                for token in self.tokens[body_start:nested_before]
            )
            nested_is_async = (
                self.tokens[nested_before].kind == "BEFORE_ASYNC_WITH"
            )
            if nested_is_async:
                nested_send = next(
                    index
                    for index in range(nested_before + 1, end)
                    if self.tokens[index].kind == "SEND"
                )
                nested_store = self.offset_to_index[
                    instruction_target(self.tokens[nested_send])
                ]
            else:
                nested_store = nested_before + 1
            nested_protected = next(
                (
                    entry
                    for entry in self.entries
                    if entry.start == self.tokens[nested_store].offset
                    and entry.lasti
                ),
                None,
            )
            if nested_protected is None:
                self._error(
                    "Nested with statement has no protected exception region"
                )
            if starts_nested_suite:
                fragment_limit = self.offset_to_index[
                    nested_protected.target
                ]
                break
            try:
                context = self.owner._expression_slice(
                    body_start,
                    nested_before,
                )
            except Python311ParseError as error:
                if hasattr(error, "shape_hint"):
                    raise
                self.owner._error(
                    "With cleanup nested context could not be structured "
                    f"safely: {error.message}"
                )
            cursor = nested_before

        handler_index = self.offset_to_index[protected.target]
        fragments = self._with_fragments(
            protected,
            fragment_limit if fragment_limit is not None else handler_index,
        )
        if not fragments:
            self._error("With statement has no source-body fragments")
        body_end = max(
            self.offset_to_index[entry.end] for entry in fragments
        )
        return_indexes = [
            index
            for index in range(body_start, handler_index)
            if self.tokens[index].kind == "RETURN_VALUE"
        ]
        deferred_return = bool(return_indexes) and any(
            self.tokens[index].kind == "PUSH_EXC_INFO"
            and self._handler_has_match(index)
            for index in range(body_end, handler_index)
        )
        capture_end = body_end
        if (
            return_indexes
            and return_indexes[-1] >= body_end
            and not deferred_return
        ):
            capture_end = return_indexes[-1] + 1
        returning = bool(return_indexes)
        try:
            body = self._with_body(
                body_start,
                capture_end,
                loop,
                fragments,
                trailing_return=deferred_return,
            )
        except Python311ParseError as error:
            if hasattr(error, "shape_hint"):
                raise
            self.owner._error(
                "With cleanup body could not be structured safely: "
                f"{error.message}"
            )
        suite = body or [ast.Pass()]
        for group_is_async, items in reversed(groups):
            statement_type = ast.AsyncWith if group_is_async else ast.With
            suite = [
                statement_type(
                    items=items,
                    body=suite,
                    type_comment=None,
                )
            ]
        self.owner.body.append(suite[0])

        outer_handler_index = self.offset_to_index[contexts[0].target]
        jumps = [
            instruction_target(self.tokens[index])
            for index in range(body_end, outer_handler_index)
            if self.tokens[index].kind == "JUMP_FORWARD"
        ]
        if jumps:
            continuation = self.offset_to_index[jumps[-1]]
            trailing_protocol = self._with_cleanup_protocol_offsets(
                continuation,
                outer_handler_index,
            )
            if trailing_protocol:
                continuation = max(
                    self.offset_to_index[offset]
                    for offset in trailing_protocol
                ) + 1
                if (
                    continuation < outer_handler_index
                    and self.tokens[continuation].kind == "JUMP_FORWARD"
                ):
                    continuation = self.offset_to_index[
                        instruction_target(self.tokens[continuation])
                    ]
                elif (
                    continuation + 1 < outer_handler_index
                    and self.tokens[continuation].kind == "LOAD_CONST"
                    and self.tokens[continuation].attr is None
                    and self.tokens[continuation + 1].kind
                    == "RETURN_VALUE"
                ):
                    return end
            return continuation
        if returning:
            return end
        suppressed_continuation = self._with_suppressed_continuation(
            handler_index
        )
        if suppressed_continuation is not None:
            return suppressed_continuation
        self._error("With cleanup has neither continuation nor return")


def recover_try_statement311(owner, index: int, loop):
    try:
        return ExceptionStructureDecompiler311(owner).try_statement(
            index,
            loop,
        )
    except Python311ParseError as error:
        # Once an exception-table entry has selected this recovery path, a
        # failure below it is an exception cleanup/control-transfer shape,
        # even when the first visible stack symptom is CALL, RETURN_VALUE, or
        # another ordinary expression opcode.  Preserve the exact parser
        # diagnostic and attach classification metadata rather than relaxing
        # the expression parser.
        if not hasattr(error, "shape_hint"):
            error.shape_hint = (
                "realworld_exception_cleanup_control_transfer"
            )
        raise


def recover_with_statement311(owner, index: int, end: int, loop):
    return ExceptionStructureDecompiler311(owner).with_statement(
        index,
        end,
        loop,
    )
