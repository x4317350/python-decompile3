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
        self.owner._suppressed_exception_starts.update(offsets)
        try:
            return self._capture_optional(start, end, loop)
        finally:
            self.owner._suppressed_exception_starts.difference_update(offsets)

    def _clause_body(
        self,
        start: int,
        end: int,
        name: Optional[str],
        loop,
    ) -> List[ast.stmt]:
        pop_index = next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].kind == "POP_EXCEPT"
            ),
            None,
        )
        if pop_index is None:
            return self._capture_optional(start, end, loop)

        body = self._capture_optional(start, pop_index, loop)
        cursor = pop_index + 1
        if (
            name is not None
            and cursor + 2 < end
            and self.tokens[cursor].kind == "LOAD_CONST"
            and self.tokens[cursor].attr is None
            and self.tokens[cursor + 1].kind.startswith("STORE_")
            and self.tokens[cursor + 2].kind.startswith("DELETE_")
        ):
            cursor += 3
        if cursor < end and self.tokens[end - 1].kind == "JUMP_FORWARD":
            end -= 1
        body.extend(self._capture_optional(cursor, end, loop))
        return body

    def _handler_cleanup_end(self, start: int) -> int:
        for index in range(start, len(self.tokens) - 2):
            if (
                self.tokens[index].kind == "COPY_STACK"
                and self.tokens[index + 1].kind == "POP_EXCEPT"
                and self.tokens[index + 2].kind == "RERAISE"
            ):
                return index + 3
        self._error("Exception handler has no COPY/POP_EXCEPT/RERAISE cleanup")

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

            normal_end = false_index
            for index in range(body_start, false_index):
                token = self.tokens[index]
                if token.kind == "JUMP_FORWARD":
                    joins.append(instruction_target(token))
                    normal_end = index + 1
                    break
                if token.kind == "RETURN_VALUE":
                    normal_end = index + 1
                    break
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
        cleanup_end = self._handler_cleanup_end(final_reraise + 1)
        join = min(joins) if joins else None
        return handlers, cleanup_end, join

    def _parse_bare_handler(
        self,
        cursor: int,
        loop,
    ) -> Tuple[ast.ExceptHandler, int, Optional[int]]:
        if self.tokens[cursor].kind != "POP_TOP":
            self._error("Bare exception handler has no POP_TOP")
        body_start = cursor + 1
        cleanup_end = self._handler_cleanup_end(body_start)
        body_end = cleanup_end - 3
        join = None
        for index in range(body_start, body_end):
            if self.tokens[index].kind == "JUMP_FORWARD":
                join = instruction_target(self.tokens[index])
                body_end = index + 1
                break
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
        if (
            body_end < handler_index
            and self.tokens[body_end].kind == "RETURN_VALUE"
        ):
            body_end += 1
        body = self._capture_protected(start, body_end, loop)

        normal_jump = next(
            (
                index
                for index in range(body_end, handler_index)
                if self.tokens[index].kind == "JUMP_FORWARD"
            ),
            None,
        )
        if normal_jump is None:
            orelse = []
            normal_join = None
        else:
            orelse = self._capture_suppressed(
                body_end,
                normal_jump,
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
                and not self._handler_has_match(
                    self.offset_to_index[region.target]
                )
            }
        )
        if finally_targets:
            handler_offset = finally_targets[0]
            handler_index = self.offset_to_index[handler_offset]
            jump_index = next(
                (
                    index
                    for index in range(next_index, handler_index)
                    if self.tokens[index].kind == "JUMP_FORWARD"
                ),
                None,
            )
            if jump_index is None:
                self._error("Finally suite has no normal-path jump")
            finalbody = self._capture_optional(next_index, jump_index, loop)
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
                final_handler_index + 1
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
        body = self._capture_protected(start, try_end, loop)
        cleanup_end = self._handler_cleanup_end(handler_index + 1)
        finalbody_end = handler_index
        next_index = cleanup_end

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
        finalbody = self._capture_optional(
            try_end,
            finalbody_end,
            loop,
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
        elif self._handler_has_match(handler_index):
            statement, next_index = self._try_except(entry, loop)
        else:
            statement, next_index = self._try_finally(entry, loop)
        self.owner.body.append(statement)
        return next_index

    def _with_body(self, start: int, end: int, returning: bool, loop):
        if not returning:
            return self._capture_optional(start, end, loop)
        try:
            expression = self.owner._expression_slice(start, end)
        except Python311ParseError:
            self._error("Returning with-body is not one expression")
        return [ast.Return(value=expression)]

    def with_statement(self, before_index: int, end: int, loop):
        before = self.tokens[before_index]
        if before.kind not in ("BEFORE_WITH", "BEFORE_ASYNC_WITH"):
            return None
        context = self.owner._pop_expr()
        groups = []
        cursor = before_index
        starts_nested_suite = True

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
                body_end = protected_end
                handler_targets = [protected.target]
                break
            starts_nested_suite = any(
                token.linestart is not None
                for token in self.tokens[body_start:nested_before]
            )
            context = self.owner._expression_slice(body_start, nested_before)
            cursor = nested_before

        first_handler = min(handler_targets)
        first_handler_index = self.offset_to_index[first_handler]
        returning = any(
            self.tokens[index].kind == "RETURN_VALUE"
            for index in range(body_end, first_handler_index)
        )
        body = self._with_body(body_start, body_end, returning, loop)
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

        jump = next(
            (
                instruction_target(self.tokens[index])
                for index in range(body_end, first_handler_index)
                if self.tokens[index].kind == "JUMP_FORWARD"
            ),
            None,
        )
        if jump is not None:
            return self.offset_to_index[jump]
        if returning:
            return end
        self._error("With cleanup has neither continuation nor return")


def recover_try_statement311(owner, index: int, loop):
    return ExceptionStructureDecompiler311(owner).try_statement(index, loop)


def recover_with_statement311(owner, index: int, end: int, loop):
    return ExceptionStructureDecompiler311(owner).with_statement(
        index,
        end,
        loop,
    )
