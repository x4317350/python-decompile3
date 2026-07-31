"""Recover CPython 3.11 comprehension code objects as standard AST nodes."""

from __future__ import annotations

import ast
from typing import List, Tuple

from decompyle3.controlflow.cfg import instruction_target
from decompyle3.parsers.p311.base import (
    Python311ParseError,
    _COMPARE_OPERATORS,
    _IGNORED_INTERNAL,
)


_COMPREHENSION_NAMES = {
    "<dictcomp>",
    "<genexpr>",
    "<listcomp>",
    "<setcomp>",
}


def is_comprehension_code(code) -> bool:
    return getattr(code, "co_name", None) in _COMPREHENSION_NAMES


def _negate(expression: ast.expr) -> ast.expr:
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        return expression.operand
    return ast.UnaryOp(op=ast.Not(), operand=expression)


def _combine_decision(
    predicate: ast.expr,
    when_true: ast.expr,
    when_false: ast.expr,
) -> ast.expr:
    true_value = (
        when_true.value
        if isinstance(when_true, ast.Constant)
        and isinstance(when_true.value, bool)
        else None
    )
    false_value = (
        when_false.value
        if isinstance(when_false, ast.Constant)
        and isinstance(when_false.value, bool)
        else None
    )
    if true_value is True and false_value is False:
        return predicate
    if true_value is False and false_value is True:
        return _negate(predicate)
    if false_value is False:
        return ast.BoolOp(op=ast.And(), values=[predicate, when_true])
    if true_value is False:
        return ast.BoolOp(
            op=ast.And(),
            values=[_negate(predicate), when_false],
        )
    if true_value is True:
        return ast.BoolOp(op=ast.Or(), values=[predicate, when_false])
    if false_value is True:
        return ast.BoolOp(
            op=ast.Or(),
            values=[_negate(predicate), when_true],
        )
    return ast.IfExp(test=predicate, body=when_true, orelse=when_false)


class ComprehensionDecompiler311:
    """Decode the implicit ``.0`` loop nest in a comprehension code object."""

    def __init__(self, owner, function, outer_iterable: ast.expr):
        self.owner = owner
        self.function = function
        self.code = function.code
        self.tokens = list(owner._nested_tokens(self.code))
        self.offset_to_index = {
            token.offset: index for index, token in enumerate(self.tokens)
        }
        self.outer_iterable = outer_iterable
        self.generators: List[ast.comprehension] = []
        self.output: Tuple[ast.expr, ...] = ()

    def _error(self, message):
        raise Python311ParseError(
            f"{message} ({getattr(self.code, 'co_qualname', self.code.co_name)})"
        )

    def _resolved_target_offset(self, token):
        target = instruction_target(token)
        index = self.offset_to_index.get(target)
        seen = set()
        while index is not None and index not in seen:
            seen.add(index)
            while (
                index < len(self.tokens)
                and self.tokens[index].kind in _IGNORED_INTERNAL
            ):
                index += 1
            if index >= len(self.tokens):
                return target
            current = self.tokens[index]
            if current.kind not in (
                "JUMP_BACKWARD",
                "JUMP_BACKWARD_NO_INTERRUPT",
                "JUMP_FORWARD",
            ):
                return current.offset
            index = self.offset_to_index.get(instruction_target(current))
        return target

    def _expressions(self, start: int, end: int, count: int) -> Tuple[ast.expr, ...]:
        from decompyle3.parsers.p311.expressions import (
            recover_expressions311,
        )

        return recover_expressions311(
            self.code,
            self.tokens,
            count,
            start=start,
            end=end,
            terminal_kinds=frozenset(),
        )

    def _expression(self, start: int, end: int) -> ast.expr:
        return self._expressions(start, end, 1)[0]

    def _target(self, start: int) -> Tuple[ast.expr, int]:
        while (
            start < len(self.tokens)
            and self.tokens[start].kind in _IGNORED_INTERNAL
        ):
            start += 1
        if start >= len(self.tokens):
            self._error("Comprehension loop target ended before all stores")
        token = self.tokens[start]
        if token.kind.startswith("STORE_"):
            name = token.attr if isinstance(token.attr, str) else token.pattr
            return ast.Name(id=name, ctx=ast.Store()), start + 1
        if token.kind not in ("UNPACK_SEQUENCE", "UNPACK_EX"):
            self._error("Comprehension loop has no store target")

        if token.kind == "UNPACK_SEQUENCE":
            before = int(token.attr)
            after = -1
            count = before
        else:
            argument = int(token.attr)
            before = argument & 0xFF
            after = argument >> 8
            count = before + after + 1

        targets = []
        cursor = start + 1
        for target_index in range(count):
            target, cursor = self._target(cursor)
            if after >= 0 and target_index == before:
                target = ast.Starred(value=target, ctx=ast.Store())
            targets.append(target)
        return ast.Tuple(elts=targets, ctx=ast.Store()), cursor

    def _filter(self, start: int, jump_index: int) -> ast.expr:
        expression = self._expression(start, jump_index)
        kind = self.tokens[jump_index].kind
        if "IF_NONE" in kind:
            return ast.Compare(
                left=expression,
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            )
        if "IF_NOT_NONE" in kind:
            return ast.Compare(
                left=expression,
                ops=[ast.Is()],
                comparators=[ast.Constant(value=None)],
            )
        if "IF_TRUE" in kind:
            return _negate(expression)
        return expression

    def _jump_predicate(self, start: int, jump_index: int) -> ast.expr:
        expression = self._expression(start, jump_index)
        kind = self.tokens[jump_index].kind
        if "IF_NONE" in kind and "IF_NOT_NONE" not in kind:
            return ast.Compare(
                left=expression,
                ops=[ast.Is()],
                comparators=[ast.Constant(value=None)],
            )
        if "IF_NOT_NONE" in kind:
            return ast.Compare(
                left=expression,
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            )
        return expression

    def _boolean_filter(
        self,
        start: int,
        final_jump: int,
        loop_offset: int,
    ):
        success_index = final_jump + 1
        if success_index >= len(self.tokens):
            return None
        success_offset = self.tokens[success_index].offset
        jump_indices = [
            index
            for index in range(start, success_index)
            if self.tokens[index].kind.startswith("POP_JUMP_")
        ]
        if not jump_indices or jump_indices[-1] != final_jump:
            return None

        leaders = {start}
        for jump_index in jump_indices:
            if (
                jump_index + 1 < success_index
                and self.tokens[jump_index + 1].kind
                not in (
                    "JUMP_BACKWARD",
                    "JUMP_BACKWARD_NO_INTERRUPT",
                    "JUMP_FORWARD",
                )
            ):
                leaders.add(jump_index + 1)
            target_index = self.offset_to_index.get(
                instruction_target(self.tokens[jump_index])
            )
            if (
                target_index is not None
                and start <= target_index < success_index
            ):
                leaders.add(target_index)
        ordered_leaders = sorted(leaders)
        nodes = {}
        for position, leader in enumerate(ordered_leaders):
            limit = (
                ordered_leaders[position + 1]
                if position + 1 < len(ordered_leaders)
                else success_index
            )
            block_jumps = [
                index
                for index in jump_indices
                if leader <= index < limit
            ]
            if len(block_jumps) != 1:
                return None
            jump_index = block_jumps[0]
            if any(
                self.tokens[index].kind not in _IGNORED_INTERNAL
                and self.tokens[index].kind
                not in (
                    "JUMP_BACKWARD",
                    "JUMP_BACKWARD_NO_INTERRUPT",
                    "JUMP_FORWARD",
                )
                for index in range(jump_index + 1, limit)
            ):
                return None
            try:
                predicate = self._jump_predicate(leader, jump_index)
            except Python311ParseError:
                return None
            nodes[leader] = (predicate, jump_index)

        def endpoint(index: int):
            seen = set()
            while index is not None and index not in seen:
                seen.add(index)
                while (
                    index < len(self.tokens)
                    and self.tokens[index].kind in _IGNORED_INTERNAL
                ):
                    index += 1
                if index == success_index:
                    return True
                if index >= len(self.tokens):
                    return None
                offset = self.tokens[index].offset
                if offset == success_offset:
                    return True
                if offset == loop_offset:
                    return False
                if index in nodes:
                    return index
                if self.tokens[index].kind in (
                    "JUMP_BACKWARD",
                    "JUMP_BACKWARD_NO_INTERRUPT",
                    "JUMP_FORWARD",
                ):
                    index = self.offset_to_index.get(
                        instruction_target(self.tokens[index])
                    )
                    continue
                return None
            return None

        def build(reference, active=frozenset()):
            if isinstance(reference, bool):
                return ast.Constant(value=reference)
            if reference is None or reference in active:
                return None
            predicate, jump_index = nodes[reference]
            token = self.tokens[jump_index]
            target_index = self.offset_to_index.get(instruction_target(token))
            jump_reference = (
                endpoint(target_index)
                if target_index is not None
                else None
            )
            fallthrough_reference = endpoint(jump_index + 1)
            if "IF_FALSE" in token.kind:
                true_reference = fallthrough_reference
                false_reference = jump_reference
            else:
                true_reference = jump_reference
                false_reference = fallthrough_reference
            when_true = build(true_reference, active | {reference})
            when_false = build(false_reference, active | {reference})
            if when_true is None or when_false is None:
                return None
            return _combine_decision(predicate, when_true, when_false)

        expression = build(start)
        if expression is None:
            return None
        return expression, success_index

    def _has_pending_conditional_branch(
        self,
        start: int,
        jump_index: int,
    ) -> bool:
        for index in range(start, jump_index):
            if not self.tokens[index].kind.startswith("POP_JUMP_"):
                continue
            target_index = self.offset_to_index.get(
                instruction_target(self.tokens[index])
            )
            if target_index is not None and target_index > jump_index + 1:
                return True
        return False

    def _chained_filter(
        self,
        start: int,
        loop_offset: int,
        body_limit: int,
    ):
        swap_index = next(
            (
                index
                for index in range(start, body_limit)
                if self.tokens[index].kind == "SWAP_STACK"
            ),
            None,
        )
        if (
            swap_index is None
            or swap_index + 3 >= body_limit
            or self.tokens[swap_index].attr != 2
            or self.tokens[swap_index + 1].kind != "COPY_STACK"
            or self.tokens[swap_index + 1].attr != 2
            or self.tokens[swap_index + 2].kind not in _COMPARE_OPERATORS
            or not self.tokens[swap_index + 3].kind.startswith("POP_JUMP_")
        ):
            return None

        initial = self._expressions(start, swap_index, 2)
        left, first_comparator = initial
        operators = [_COMPARE_OPERATORS[self.tokens[swap_index + 2].kind]()]
        comparators = [first_comparator]
        cleanup_offset = instruction_target(self.tokens[swap_index + 3])
        cursor = swap_index + 4

        while cursor < body_limit:
            marker = next(
                (
                    index
                    for index in range(cursor, body_limit)
                    if self.tokens[index].kind in _COMPARE_OPERATORS
                    or self.tokens[index].kind == "SWAP_STACK"
                ),
                None,
            )
            if marker is None:
                return None
            comparator = self._expression(cursor, marker)
            marker_token = self.tokens[marker]
            if marker_token.kind == "SWAP_STACK":
                if (
                    marker + 3 >= body_limit
                    or self.tokens[marker + 1].kind != "COPY_STACK"
                    or self.tokens[marker + 2].kind not in _COMPARE_OPERATORS
                    or not self.tokens[marker + 3].kind.startswith("POP_JUMP_")
                    or instruction_target(self.tokens[marker + 3])
                    != cleanup_offset
                ):
                    return None
                operators.append(
                    _COMPARE_OPERATORS[self.tokens[marker + 2].kind]()
                )
                comparators.append(comparator)
                cursor = marker + 4
                continue

            jump_index = marker + 1
            continuation_index = marker + 2
            if (
                jump_index >= body_limit
                or continuation_index >= body_limit
                or not self.tokens[jump_index].kind.startswith("POP_JUMP_")
                or self._resolved_target_offset(self.tokens[jump_index])
                != loop_offset
                or self.tokens[continuation_index].kind != "JUMP_FORWARD"
            ):
                return None
            cleanup_index = self.offset_to_index.get(cleanup_offset)
            if (
                cleanup_index is None
                or self.tokens[cleanup_index].kind != "POP_TOP"
                or cleanup_index + 1 >= body_limit
                or self.tokens[cleanup_index + 1].kind != "JUMP_FORWARD"
            ):
                return None

            operators.append(_COMPARE_OPERATORS[marker_token.kind]())
            comparators.append(comparator)
            success_offset = instruction_target(self.tokens[continuation_index])
            success_index = self.offset_to_index.get(success_offset)
            if success_index is None:
                return None
            return (
                ast.Compare(
                    left=left,
                    ops=operators,
                    comparators=comparators,
                ),
                success_index,
            )
        return None

    def _record_output(self, start: int, end: int, kind: str):
        count = 2 if kind == "MAP_ADD" else 1
        self.output = self._expressions(start, end, count)

    def _sync_loop(self, header: int, iterable: ast.expr) -> int:
        token = self.tokens[header]
        if token.kind != "FOR_ITER":
            self._error("Synchronous comprehension loop has no FOR_ITER")
        exit_index = self.offset_to_index[instruction_target(token)]
        latch_candidates = [
            index
            for index in range(header + 1, exit_index)
            if self.tokens[index].kind.startswith("JUMP_BACKWARD")
            and instruction_target(self.tokens[index]) == token.offset
        ]
        if not latch_candidates:
            self._error("Synchronous comprehension loop has no back edge")
        body_limit = latch_candidates[-1]
        target, cursor = self._target(header + 1)
        generator = ast.comprehension(
            target=target,
            iter=iterable,
            ifs=[],
            is_async=0,
        )
        self.generators.append(generator)
        expression_start = cursor

        while cursor < body_limit:
            chained_filter = self._chained_filter(
                expression_start,
                token.offset,
                body_limit,
            )
            if chained_filter is not None:
                expression, cursor = chained_filter
                generator.ifs.append(expression)
                expression_start = cursor
                continue
            current = self.tokens[cursor]
            target_offset = self._resolved_target_offset(current)
            if (
                current.kind.startswith("POP_JUMP_")
                and target_offset == token.offset
            ):
                if self._has_pending_conditional_branch(
                    expression_start,
                    cursor,
                ):
                    cursor += 1
                    continue
                boolean_filter = self._boolean_filter(
                    expression_start,
                    cursor,
                    token.offset,
                )
                if boolean_filter is None:
                    expression = self._filter(expression_start, cursor)
                    cursor += 1
                else:
                    expression, cursor = boolean_filter
                generator.ifs.append(expression)
                expression_start = cursor
                continue
            if (
                current.kind == "GET_ITER"
                and cursor + 1 < body_limit
                and self.tokens[cursor + 1].kind == "FOR_ITER"
            ):
                nested_iterable = self._expression(expression_start, cursor)
                cursor = self._sync_loop(cursor + 1, nested_iterable)
                expression_start = cursor
                continue
            if current.kind == "GET_AITER":
                nested_iterable = self._expression(expression_start, cursor)
                async_header = cursor + 1
                while (
                    async_header < body_limit
                    and self.tokens[async_header].kind != "GET_ANEXT"
                ):
                    async_header += 1
                cursor = self._async_loop(async_header, nested_iterable)
                expression_start = cursor
                continue
            if (
                current.kind in ("LIST_APPEND", "SET_ADD", "MAP_ADD")
                and int(current.attr) >= 2
            ):
                self._record_output(expression_start, cursor, current.kind)
                cursor += 1
                expression_start = cursor
                continue
            if current.kind == "YIELD_VALUE":
                self._record_output(expression_start, cursor, current.kind)
                cursor += 1
                expression_start = cursor
                continue
            cursor += 1
        return exit_index

    def _async_loop(self, header: int, iterable: ast.expr) -> int:
        if header >= len(self.tokens) or self.tokens[header].kind != "GET_ANEXT":
            self._error("Asynchronous comprehension loop has no GET_ANEXT")
        send_index = next(
            (
                index
                for index in range(header + 1, len(self.tokens))
                if self.tokens[index].kind == "SEND"
            ),
            None,
        )
        if send_index is None:
            self._error("GET_ANEXT has no SEND protocol")
        body_start = self.offset_to_index[
            instruction_target(self.tokens[send_index])
        ]
        while (
            body_start < len(self.tokens)
            and self.tokens[body_start].kind in ("INTERNAL_RESUME", "NOP")
        ):
            body_start += 1
        latch_candidates = [
            index
            for index in range(body_start, len(self.tokens))
            if self.tokens[index].kind.startswith("JUMP_BACKWARD")
            and instruction_target(self.tokens[index]) == self.tokens[header].offset
        ]
        if not latch_candidates:
            self._error("Asynchronous comprehension loop has no back edge")
        body_limit = latch_candidates[-1]
        end_index = next(
            (
                index
                for index in range(body_limit + 1, len(self.tokens))
                if self.tokens[index].kind == "END_ASYNC_FOR"
            ),
            None,
        )
        if end_index is None:
            self._error("Asynchronous comprehension loop has no END_ASYNC_FOR")

        target, cursor = self._target(body_start)
        generator = ast.comprehension(
            target=target,
            iter=iterable,
            ifs=[],
            is_async=1,
        )
        self.generators.append(generator)
        expression_start = cursor
        while cursor < body_limit:
            chained_filter = self._chained_filter(
                expression_start,
                self.tokens[header].offset,
                body_limit,
            )
            if chained_filter is not None:
                expression, cursor = chained_filter
                generator.ifs.append(expression)
                expression_start = cursor
                continue
            current = self.tokens[cursor]
            target_offset = self._resolved_target_offset(current)
            if (
                current.kind.startswith("POP_JUMP_")
                and target_offset == self.tokens[header].offset
            ):
                if self._has_pending_conditional_branch(
                    expression_start,
                    cursor,
                ):
                    cursor += 1
                    continue
                boolean_filter = self._boolean_filter(
                    expression_start,
                    cursor,
                    self.tokens[header].offset,
                )
                if boolean_filter is None:
                    expression = self._filter(expression_start, cursor)
                    cursor += 1
                else:
                    expression, cursor = boolean_filter
                generator.ifs.append(expression)
                expression_start = cursor
                continue
            if (
                current.kind == "GET_ITER"
                and cursor + 1 < body_limit
                and self.tokens[cursor + 1].kind == "FOR_ITER"
            ):
                nested_iterable = self._expression(expression_start, cursor)
                cursor = self._sync_loop(cursor + 1, nested_iterable)
                expression_start = cursor
                continue
            if (
                current.kind in ("LIST_APPEND", "SET_ADD", "MAP_ADD")
                and int(current.attr) >= 2
            ):
                self._record_output(expression_start, cursor, current.kind)
                cursor += 1
                expression_start = cursor
                continue
            if current.kind == "YIELD_VALUE":
                self._record_output(expression_start, cursor, current.kind)
                cursor += 1
                expression_start = cursor
                continue
            cursor += 1
        return end_index + 1

    def decompile(self) -> ast.expr:
        header = next(
            (
                index
                for index, token in enumerate(self.tokens)
                if token.kind in ("FOR_ITER", "GET_ANEXT")
            ),
            None,
        )
        if header is None:
            self._error("Comprehension code has no loop header")
        if self.tokens[header].kind == "FOR_ITER":
            self._sync_loop(header, self.outer_iterable)
        else:
            self._async_loop(header, self.outer_iterable)
        if not self.output or not self.generators:
            self._error("Comprehension code has no output expression")

        name = getattr(self.code, "co_name", None)
        if name == "<listcomp>":
            return ast.ListComp(elt=self.output[0], generators=self.generators)
        if name == "<setcomp>":
            return ast.SetComp(elt=self.output[0], generators=self.generators)
        if name == "<dictcomp>":
            if len(self.output) != 2:
                self._error("Dictionary comprehension has no key/value pair")
            return ast.DictComp(
                key=self.output[0],
                value=self.output[1],
                generators=self.generators,
            )
        if name == "<genexpr>":
            return ast.GeneratorExp(
                elt=self.output[0],
                generators=self.generators,
            )
        self._error(f"Unknown comprehension code name {name!r}")


def build_comprehension311(owner, function, iterable: ast.expr) -> ast.expr:
    return ComprehensionDecompiler311(owner, function, iterable).decompile()
