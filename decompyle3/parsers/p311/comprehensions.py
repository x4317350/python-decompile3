"""Recover CPython 3.11 comprehension code objects as standard AST nodes."""

from __future__ import annotations

import ast
from typing import List, Tuple

from decompyle3.controlflow.cfg import instruction_target
from decompyle3.parsers.p311.base import (
    Python311ParseError,
    _COMPARE_OPERATORS,
    _IGNORED_INTERNAL,
    _StraightLineDecompiler,
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

    def _expressions(self, start: int, end: int, count: int) -> Tuple[ast.expr, ...]:
        parser = _StraightLineDecompiler(
            self.code,
            self.tokens[start:end],
            compile_mode="expr",
        )
        for token in parser.tokens:
            parser.current_token = token
            parser._resolve_booleans(token.offset)
            parser._dispatch(token)
        parser._flush_assignment()
        if parser.body or parser.pending_booleans or len(parser.stack) != count:
            self._error(
                f"Instruction range {start}:{end} does not contain "
                f"{count} expression value(s)"
            )
        result = tuple(parser._expression_value(value) for value in parser.stack)
        return result

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
                or instruction_target(self.tokens[jump_index]) != loop_offset
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
            target_offset = instruction_target(current)
            if (
                current.kind.startswith("POP_JUMP_")
                and target_offset == token.offset
            ):
                generator.ifs.append(self._filter(expression_start, cursor))
                expression_start = cursor + 1
                cursor += 1
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
            if current.kind in ("LIST_APPEND", "SET_ADD", "MAP_ADD"):
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
            target_offset = instruction_target(current)
            if (
                current.kind.startswith("POP_JUMP_")
                and target_offset == self.tokens[header].offset
            ):
                generator.ifs.append(self._filter(expression_start, cursor))
                expression_start = cursor + 1
                cursor += 1
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
            if current.kind in ("LIST_APPEND", "SET_ADD", "MAP_ADD"):
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
