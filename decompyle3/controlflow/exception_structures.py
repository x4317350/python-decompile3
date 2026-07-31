"""Structure CPython 3.11 exception-table regions as standard AST nodes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import List, Optional, Tuple

from decompyle3.controlflow.cfg import instruction_target
from decompyle3.parsers.p311.base import Python311ParseError


@dataclass(frozen=True)
class ExceptionState311:
    """One logical exception-state object used while decoding a handler."""

    handler_offset: int
    depth: int
    lasti: bool


class ExceptionStructureDecompiler311:
    """Recover try and with statements from protected ranges and handlers."""

    def __init__(self, owner):
        self.owner = owner
        self.tokens = owner.tokens
        self.offset_to_index = owner.offset_to_index
        self.entries = owner.exception_regions

    def _error(self, message):
        token = self.owner.current_token
        offset = token.offset if token is not None else "?"
        raise Python311ParseError(
            f"{message} ({self.owner.code.co_name!r}, offset {offset})"
        )

    def _handler_has_match(self, handler_index: int) -> bool:
        for token in self.tokens[handler_index + 1 :]:
            if token.kind == "CHECK_EXC_MATCH":
                return True
            if token.kind in ("RERAISE", "RETURN_VALUE"):
                return False
        return False

    def _handler_has_group_match(self, handler_index: int) -> bool:
        for token in self.tokens[handler_index + 1 :]:
            if token.kind == "CHECK_EG_MATCH":
                return True
            if token.kind in ("RERAISE", "RETURN_VALUE"):
                return False
        return False

    def _handler_is_bare(self, handler_index: int) -> bool:
        if not (
            handler_index + 1 < len(self.tokens)
            and self.tokens[handler_index].kind == "PUSH_EXC_INFO"
            and self.tokens[handler_index + 1].kind == "POP_TOP"
        ):
            return False
        return not (
            handler_index + 3 < len(self.tokens)
            and self.tokens[handler_index + 2].kind == "POP_EXCEPT"
            and (
                self.tokens[handler_index + 3].kind == "POP_TOP"
                or self.tokens[handler_index + 3].kind.startswith(
                    "JUMP_BACKWARD"
                )
            )
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

    def _capture_optional(self, start: int, end: int, loop) -> List[ast.stmt]:
        if start >= end:
            return []
        return self.owner._capture_region(start, end, loop)

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

    def _capture_protected_return(
        self,
        start: int,
        end: int,
        loop,
    ) -> Optional[List[ast.stmt]]:
        """Recover a return value kept on the VM stack across ``finally``."""
        expression_start = self.owner._latch_expression_start(start, end)
        if expression_start >= end:
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
                expression = self.owner._expression_slice(
                    expression_start,
                    end,
                )
            except Python311ParseError:
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

    def _capture_handler_clause(
        self,
        start: int,
        end: int,
        name: Optional[str],
        loop,
    ) -> List[ast.stmt]:
        offsets = self._handler_protocol_offsets(start, end, name)
        added = (
            offsets
            - self.owner._suppressed_exception_protocol_offsets
        )
        self.owner._suppressed_exception_protocol_offsets.update(added)
        try:
            return self._capture_optional(start, end, loop)
        finally:
            self.owner._suppressed_exception_protocol_offsets.difference_update(
                added
            )

    def _conditional_handler_transfer(
        self,
        start: int,
        end: int,
        loop,
    ) -> Optional[List[ast.stmt]]:
        """Recover ``if condition: break/continue; raise`` in a handler."""
        if loop is None or start >= end:
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
        target = instruction_target(self.tokens[transfer_index])
        if target == loop.break_target:
            transfer = ast.Break()
        elif target in loop.continue_targets:
            transfer = ast.Continue()
        else:
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
    ) -> List[ast.stmt]:
        if start < end and self.tokens[end - 1].kind == "JUMP_FORWARD":
            end -= 1
        conditional_transfer = self._conditional_handler_transfer(
            start,
            end,
            loop,
        )
        if conditional_transfer is not None:
            return conditional_transfer
        return self._capture_handler_clause(start, end, name, loop)

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
        candidates = [
            self.offset_to_index[entry.target]
            for entry in self.entries
            if entry.lasti
            and entry.start <= body_offset < entry.end
            and body_start
            < self.offset_to_index[entry.target]
            <= false_index
        ]
        if candidates:
            return min(candidates)
        return false_index

    def _parse_handlers(
        self,
        handler_index: int,
        loop,
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
            jump_index = check_index + 1
            if not self.tokens[jump_index].kind.startswith("POP_JUMP_"):
                self._error("CHECK_EXC_MATCH has no conditional jump")
            false_index = self.offset_to_index[
                instruction_target(self.tokens[jump_index])
            ]
            binding_index = jump_index + 1
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
            body = self._clause_body(body_start, normal_end, name, loop)
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
            join = instruction_target(self.tokens[body_end - 1])
        body = self._clause_body(body_start, body_end, None, loop)
        return (
            ast.ExceptHandler(type=None, name=None, body=body or [ast.Pass()]),
            cleanup_end,
            join,
        )

    def _try_except(self, entry, loop) -> Tuple[ast.Try, int]:
        start = self.offset_to_index[entry.start]
        try_end = self.offset_to_index[entry.end]
        handler_index = self.offset_to_index[entry.target]
        body_end = try_end
        crosses_with = False
        if (
            body_end < handler_index
            and self.tokens[body_end].kind == "RETURN_VALUE"
        ):
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
            if (
                cursor > body_end
                and cursor < handler_index
                and self.tokens[cursor].kind == "RETURN_VALUE"
            ):
                # The protected expression was already structured as the
                # source return; these physical loop-iterator cleanup pairs
                # belong to that same normal path, not to ``try`` orelse.
                body_end = cursor + 1
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
            outer_fragments = [
                candidate
                for candidate in self.entries
                if candidate.target == entry.target
                and candidate.depth == entry.depth
                and candidate.lasti == entry.lasti
                and start <= self.offset_to_index[candidate.start]
                < nested_handler_index
            ]
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
        body = self._capture_protected(start, body_end, loop)
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
        if normal_jump is None:
            orelse = self._capture_before_handler(
                body_end,
                handler_index,
                entry.target,
                loop,
            )
            normal_join = None
        else:
            orelse = self._capture_before_handler(
                body_end,
                normal_jump,
                entry.target,
                loop,
            )
            normal_join = instruction_target(self.tokens[normal_jump])

        handlers, cleanup_end, handler_join = self._parse_handlers(
            handler_index,
            loop,
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

        finally_targets = sorted(
            {
                region.target
                for region in self.entries
                if region.depth == 0
                and region.target > self.tokens[next_index - 1].offset
                and region.start < self.tokens[next_index].offset
                and self.tokens[self.offset_to_index[region.target]].kind
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
                if not next_index <= protected_end < handler_index:
                    self._error("Finally suite has no normal-path body")
                has_normal_return = any(
                    self.tokens[index].kind == "RETURN_VALUE"
                    for index in range(protected_end, handler_index)
                )
                expression_start = (
                    self.owner._latch_expression_start(
                        next_index,
                        protected_end,
                    )
                    if has_normal_return
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
                finalbody = self._capture_deferred_return_finally(
                    protected_end,
                    handler_index,
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
            if clause_region is None:
                self._error("except* clause body has no protected region")
            body_end = self.offset_to_index[clause_region.end]
            clause_body = self._capture_optional(
                body_start,
                body_end,
                loop,
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
            self._error("except* cleanup has no normal continuation")
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
        outer_finally = next(
            (
                candidate
                for candidate in self.entries
                if candidate is not entry
                and not candidate.lasti
                and self.tokens[prep_index].offset
                <= candidate.start
                < candidate.end
                <= self.tokens[join_index].offset
                and self.tokens[
                    self.offset_to_index[candidate.target]
                ].kind
                == "PUSH_EXC_INFO"
            ),
            None,
        )
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
        normal_return_index = next(
            (
                index
                for index in range(try_end, handler_index)
                if self.tokens[index].kind == "RETURN_VALUE"
            ),
            None,
        )
        body = (
            self._capture_protected_return(start, try_end, loop)
            if normal_return_index is not None
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
        enclosing_finally_targets = sorted(
            {
                region.target
                for region in self.entries
                if region.depth == 0
                and try_end <= self.offset_to_index[region.start]
                < handler_index
                and region.target > entry.target
                and region.target
                not in self.owner._suppressed_exception_handler_targets
                and self.tokens[
                    self.offset_to_index[region.target]
                ].kind
                == "PUSH_EXC_INFO"
                and not self._handler_has_match(
                    self.offset_to_index[region.target]
                )
            }
        )
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
                while (
                    cursor < finalbody_end
                    and self.tokens[cursor].kind == "RETURN_VALUE"
                ):
                    finalbody_protocol_offsets.add(
                        self.tokens[cursor].offset
                    )
                    cursor += 1
                finalbody_end = cursor
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
                outer_finalbody = self._capture_before_handler(
                    normal_outer_boundary,
                    handler_index,
                    outer_handler_offset,
                    loop,
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

    def try_statement(self, index: int, loop):
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
            or self._handler_is_bare(handler_index)
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
        fragments = [
            entry
            for entry in self.entries
            if entry.target == protected.target
            and entry.depth == protected.depth
            and entry.lasti == protected.lasti
            and start_index <= self.offset_to_index[entry.start] < limit_index
        ]
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
            return self._capture_optional(start, end, loop)
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
        capture_end = body_end
        if return_indexes and return_indexes[-1] >= body_end:
            capture_end = return_indexes[-1] + 1
        returning = bool(return_indexes)
        try:
            body = self._with_body(
                body_start,
                capture_end,
                loop,
                fragments,
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
