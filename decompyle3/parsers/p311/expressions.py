"""CFG-aware expression recovery for CPython 3.11 bytecode."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from decompyle3.controlflow.cfg import instruction_target
from decompyle3.parsers.p311.base import (
    Python311ParseError,
    _COMPARE_OPERATORS,
    _IGNORED_INTERNAL,
    _StraightLineDecompiler,
)


_VIRTUAL_EXIT = -1
_CONDITIONAL_PREFIXES = ("JUMP_IF_", "POP_JUMP_")
_UNCONDITIONAL_JUMPS = {
    "JUMP_FORWARD",
    "JUMP_BACKWARD",
    "JUMP_BACKWARD_NO_INTERRUPT",
}


@dataclass
class _ExpressionState:
    stack: List[Any]
    pending_keywords: Tuple[str, ...] = ()

    def clone(self):
        return _ExpressionState(
            stack=list(self.stack),
            pending_keywords=self.pending_keywords,
        )


def _same_expression(left: Any, right: Any) -> bool:
    if left is right:
        return True
    if isinstance(left, ast.AST) and isinstance(right, ast.AST):
        return ast.dump(left, include_attributes=False) == ast.dump(
            right, include_attributes=False
        )
    return left == right


def _negate(expression: ast.expr) -> ast.expr:
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        return expression.operand
    return ast.UnaryOp(op=ast.Not(), operand=expression)


def _combine_decision(
    predicate: ast.expr,
    when_true: ast.expr,
    when_false: ast.expr,
) -> ast.expr:
    true_constant = (
        when_true.value
        if isinstance(when_true, ast.Constant)
        and isinstance(when_true.value, bool)
        else None
    )
    false_constant = (
        when_false.value
        if isinstance(when_false, ast.Constant)
        and isinstance(when_false.value, bool)
        else None
    )
    if true_constant is True and false_constant is False:
        return predicate
    if true_constant is False and false_constant is True:
        return _negate(predicate)
    if false_constant is False:
        return ast.BoolOp(op=ast.And(), values=[predicate, when_true])
    if true_constant is False:
        return ast.BoolOp(
            op=ast.And(),
            values=[_negate(predicate), when_false],
        )
    if true_constant is True:
        return ast.BoolOp(op=ast.Or(), values=[predicate, when_false])
    if false_constant is True:
        return ast.BoolOp(
            op=ast.Or(),
            values=[_negate(predicate), when_true],
        )
    return ast.IfExp(test=predicate, body=when_true, orelse=when_false)


def _bool_expression(
    operator: ast.boolop,
    left: ast.expr,
    right: ast.expr,
) -> ast.expr:
    values = [left]
    if isinstance(right, ast.BoolOp) and isinstance(right.op, type(operator)):
        values.extend(right.values)
    else:
        values.append(right)
    return ast.BoolOp(op=operator, values=values)


class ExpressionDecompiler311:
    """Interpret one acyclic expression CFG and merge branch stack values."""

    def __init__(
        self,
        code,
        tokens,
        start: int = 0,
        end: Optional[int] = None,
        terminal_kinds: FrozenSet[str] = frozenset(
            {"PRINT_EXPR", "RETURN_VALUE"}
        ),
    ):
        self.code = code
        self.tokens = list(tokens)
        self.start = start
        self.end = len(self.tokens) if end is None else end
        self.terminal_kinds = terminal_kinds
        if self.start < 0 or self.end > len(self.tokens) or self.start >= self.end:
            self._error(
                f"Invalid expression instruction range {self.start}:{self.end}"
            )
        self.offset_to_index = {
            token.offset: index
            for index, token in enumerate(self.tokens)
            if self.start <= index < self.end
        }
        self.end_offset = (
            self.tokens[self.end].offset
            if self.end < len(self.tokens)
            else None
        )
        self.successors = self._build_successors()
        self.reachable = self._reachable_nodes()
        self.post_dominators = self._post_dominator_sets()
        self.immediate_post_dominators = self._immediate_post_dominators()

    def _error(self, message, index=None):
        token = (
            self.tokens[index]
            if index is not None and self.start <= index < self.end
            else None
        )
        raise Python311ParseError(
            message,
            version=(3, 11),
            code_name=getattr(self.code, "co_name", "<unknown>"),
            offset=getattr(token, "offset", None),
        )

    def _target_index(self, index: int) -> int:
        target = instruction_target(self.tokens[index])
        if target == self.end_offset:
            return _VIRTUAL_EXIT
        if target not in self.offset_to_index:
            self._error(
                f"{self.tokens[index].kind} targets outside the expression",
                index,
            )
        return self.offset_to_index[target]

    def _build_successors(self) -> Dict[int, Tuple[int, ...]]:
        result: Dict[int, Tuple[int, ...]] = {}
        for index in range(self.start, self.end):
            token = self.tokens[index]
            kind = token.kind
            following = (
                index + 1 if index + 1 < self.end else _VIRTUAL_EXIT
            )
            if kind in self.terminal_kinds:
                result[index] = (_VIRTUAL_EXIT,)
            elif kind.startswith(_CONDITIONAL_PREFIXES):
                result[index] = (self._target_index(index), following)
            elif kind in _UNCONDITIONAL_JUMPS:
                result[index] = (self._target_index(index),)
            else:
                result[index] = (following,)
        result[_VIRTUAL_EXIT] = ()
        return result

    def _reachable_nodes(self) -> FrozenSet[int]:
        pending = [self.start]
        seen = set()
        while pending:
            index = pending.pop()
            if index in seen:
                continue
            seen.add(index)
            pending.extend(self.successors.get(index, ()))
        return frozenset(seen)

    def _post_dominator_sets(self) -> Dict[int, FrozenSet[int]]:
        nodes = set(self.reachable)
        result = {
            node: (
                frozenset({_VIRTUAL_EXIT})
                if node == _VIRTUAL_EXIT
                else frozenset(nodes)
            )
            for node in nodes
        }
        changed = True
        while changed:
            changed = False
            # Expression CFG instruction indices normally flow from lower to
            # higher values.  Visiting them backwards propagates the virtual
            # exit through a straight-line region in one pass instead of one
            # instruction per pass, which otherwise makes large functions
            # effectively cubic here.
            for node in sorted(nodes, reverse=True):
                if node == _VIRTUAL_EXIT:
                    continue
                outgoing = [
                    result[successor]
                    for successor in self.successors[node]
                    if successor in nodes
                ]
                if outgoing:
                    shared = set(outgoing[0])
                    for members in outgoing[1:]:
                        shared.intersection_update(members)
                else:
                    shared = {_VIRTUAL_EXIT}
                updated = frozenset({node} | shared)
                if updated != result[node]:
                    result[node] = updated
                    changed = True
        return result

    def _immediate_post_dominators(self) -> Dict[int, Optional[int]]:
        result: Dict[int, Optional[int]] = {}
        for node, members in self.post_dominators.items():
            strict = set(members) - {node}
            immediate = None
            for candidate in strict:
                if all(
                    other == candidate
                    or other in self.post_dominators[candidate]
                    for other in strict
                ):
                    immediate = candidate
                    break
            result[node] = immediate
        return result

    def _dispatch(self, state: _ExpressionState, index: int):
        token = self.tokens[index]
        if token.kind in (
            "STORE_DEREF",
            "STORE_FAST",
            "STORE_GLOBAL",
            "STORE_NAME",
        ):
            name = token.attr if isinstance(token.attr, str) else token.pattr
            if (
                len(state.stack) < 2
                or not isinstance(name, str)
                or not isinstance(state.stack[-1], ast.expr)
                or not _same_expression(state.stack[-2], state.stack[-1])
            ):
                self._error(
                    "Expression STORE is not paired with COPY_STACK 1",
                    index,
                )
            value = state.stack.pop()
            state.stack[-1] = ast.NamedExpr(
                target=ast.Name(id=name, ctx=ast.Store()),
                value=value,
            )
            return

        parser = _StraightLineDecompiler(
            self.code,
            (),
            compile_mode="expr",
        )
        parser.stack = state.stack
        parser.pending_keywords = state.pending_keywords
        parser.current_token = token
        parser._dispatch(token)
        parser._flush_assignment()
        if parser.body or parser.pending_assignment_value is not None:
            self._error("Expression bytecode emitted a statement", index)
        state.stack = parser.stack
        state.pending_keywords = parser.pending_keywords

    def _try_chained_compare(
        self,
        state: _ExpressionState,
        index: int,
    ):
        if (
            self.tokens[index].kind != "SWAP_STACK"
            or self.tokens[index].attr != 2
            or index + 3 >= self.end
            or self.tokens[index + 1].kind != "COPY_STACK"
            or self.tokens[index + 1].attr != 2
            or self.tokens[index + 2].kind not in _COMPARE_OPERATORS
            or self.tokens[index + 3].kind != "JUMP_IF_FALSE_OR_POP"
            or len(state.stack) < 2
        ):
            return None

        left = state.stack[-2]
        first_comparator = state.stack[-1]
        if not isinstance(left, ast.expr) or not isinstance(
            first_comparator, ast.expr
        ):
            return None

        operators = [_COMPARE_OPERATORS[self.tokens[index + 2].kind]()]
        comparators = [first_comparator]
        cleanup_offset = instruction_target(self.tokens[index + 3])
        cleanup_index = self.offset_to_index.get(cleanup_offset)
        if (
            cleanup_index is None
            or cleanup_index + 1 >= self.end
            or self.tokens[cleanup_index].kind != "SWAP_STACK"
            or self.tokens[cleanup_index].attr != 2
            or self.tokens[cleanup_index + 1].kind != "POP_TOP"
        ):
            return None
        cursor = index + 4

        while cursor < self.end:
            marker = next(
                (
                    candidate
                    for candidate in range(cursor, self.end)
                    if self.tokens[candidate].kind in _COMPARE_OPERATORS
                    or self.tokens[candidate].kind == "SWAP_STACK"
                ),
                None,
            )
            if marker is None or marker == cursor:
                return None
            comparator = recover_expression311(
                self.code,
                self.tokens,
                start=cursor,
                end=marker,
                terminal_kinds=frozenset(),
            )
            if self.tokens[marker].kind == "SWAP_STACK":
                if (
                    marker + 3 >= self.end
                    or self.tokens[marker + 1].kind != "COPY_STACK"
                    or self.tokens[marker + 2].kind not in _COMPARE_OPERATORS
                    or self.tokens[marker + 3].kind
                    != "JUMP_IF_FALSE_OR_POP"
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

            operators.append(_COMPARE_OPERATORS[self.tokens[marker].kind]())
            comparators.append(comparator)
            following = marker + 1
            if following >= self.end:
                return None
            if self.tokens[following].kind == "JUMP_FORWARD":
                next_index = self._target_index(following)
            elif self.tokens[following].kind in self.terminal_kinds:
                next_index = following
            else:
                return None

            del state.stack[-2:]
            state.stack.append(
                ast.Compare(
                    left=left,
                    ops=operators,
                    comparators=comparators,
                )
            )
            return next_index
        return None

    def _resolve_forward_endpoint(self, index: int) -> int:
        seen = set()
        while index not in seen:
            seen.add(index)
            while (
                self.start <= index < self.end
                and self.tokens[index].kind in _IGNORED_INTERNAL
            ):
                index += 1
            if (
                index < self.start
                or index >= self.end
                or self.tokens[index].kind != "JUMP_FORWARD"
            ):
                return index
            index = self._target_index(index)
        return index

    def _try_chained_condition(
        self,
        state: _ExpressionState,
        index: int,
    ) -> Optional[int]:
        """Collapse POP_JUMP-based chained comparisons inside expressions."""
        token = self.tokens[index]
        if (
            not token.kind.startswith("POP_JUMP_")
            or len(state.stack) < 2
            or not isinstance(state.stack[-1], ast.Compare)
            or len(state.stack[-1].ops) != 1
            or len(state.stack[-1].comparators) != 1
            or not isinstance(state.stack[-2], ast.expr)
            or not _same_expression(
                state.stack[-2],
                state.stack[-1].comparators[0],
            )
        ):
            return None

        cleanup_index = self.offset_to_index.get(instruction_target(token))
        if (
            cleanup_index is None
            or cleanup_index + 1 >= self.end
            or self.tokens[cleanup_index].kind != "POP_TOP"
        ):
            return None

        first = state.stack[-1]
        operators = list(first.ops)
        comparators = list(first.comparators)
        cursor = index + 1
        final_jump = None
        while cursor < cleanup_index:
            marker = next(
                (
                    candidate
                    for candidate in range(cursor, cleanup_index)
                    if self.tokens[candidate].kind in _COMPARE_OPERATORS
                    or self.tokens[candidate].kind == "SWAP_STACK"
                ),
                None,
            )
            if marker is None or marker == cursor:
                return None
            try:
                comparator = recover_expression311(
                    self.code,
                    self.tokens,
                    start=cursor,
                    end=marker,
                    terminal_kinds=frozenset(),
                )
            except Python311ParseError:
                return None

            if self.tokens[marker].kind == "SWAP_STACK":
                jump_index = marker + 3
                while (
                    jump_index < cleanup_index
                    and self.tokens[jump_index].kind in _IGNORED_INTERNAL
                ):
                    jump_index += 1
                if (
                    jump_index >= cleanup_index
                    or self.tokens[marker].attr != 2
                    or self.tokens[marker + 1].kind != "COPY_STACK"
                    or self.tokens[marker + 1].attr != 2
                    or self.tokens[marker + 2].kind
                    not in _COMPARE_OPERATORS
                    or not self.tokens[jump_index].kind.startswith(
                        "POP_JUMP_"
                    )
                    or instruction_target(self.tokens[jump_index])
                    != instruction_target(token)
                ):
                    return None
                operators.append(
                    _COMPARE_OPERATORS[self.tokens[marker + 2].kind]()
                )
                comparators.append(comparator)
                cursor = jump_index + 1
                continue

            jump_index = marker + 1
            while (
                jump_index < cleanup_index
                and self.tokens[jump_index].kind in _IGNORED_INTERNAL
            ):
                jump_index += 1
            if (
                jump_index >= self.end
                or not self.tokens[jump_index].kind.startswith("POP_JUMP_")
            ):
                return None
            operators.append(_COMPARE_OPERATORS[self.tokens[marker].kind]())
            comparators.append(comparator)
            final_jump = jump_index
            break

        if final_jump is None:
            return None
        final_token = self.tokens[final_jump]
        target = self._target_index(final_jump)
        following = final_jump + 1
        false_index = (
            target
            if "IF_FALSE" in final_token.kind
            else following
        )
        cleanup_exit = self._resolve_forward_endpoint(cleanup_index + 1)
        false_exit = self._resolve_forward_endpoint(false_index)
        if cleanup_exit != false_exit:
            return None

        del state.stack[-2:]
        state.stack.append(
            ast.Compare(
                left=first.left,
                ops=operators,
                comparators=comparators,
            )
        )
        return final_jump

    def _predicate(self, token, value: ast.expr) -> ast.expr:
        if "IF_NOT_NONE" in token.kind:
            return ast.Compare(
                left=value,
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            )
        if "IF_NONE" in token.kind:
            return ast.Compare(
                left=value,
                ops=[ast.Is()],
                comparators=[ast.Constant(value=None)],
            )
        return value

    def _merge_prefix(
        self,
        left: _ExpressionState,
        right: _ExpressionState,
        expression: ast.expr,
        index: int,
    ) -> _ExpressionState:
        if left.pending_keywords != right.pending_keywords:
            self._error("Expression branches disagree on keyword-call state", index)
        if len(left.stack) != len(right.stack) or not left.stack:
            self._error("Expression branches have incompatible stack depths", index)
        for left_value, right_value in zip(left.stack[:-1], right.stack[:-1]):
            if not _same_expression(left_value, right_value):
                self._error("Expression branches disagree below stack top", index)
        return _ExpressionState(
            stack=list(left.stack[:-1]) + [expression],
            pending_keywords=left.pending_keywords,
        )

    def _merge_short_circuit(
        self,
        index: int,
        jump_state: _ExpressionState,
        fallthrough_state: _ExpressionState,
        left: ast.expr,
    ) -> _ExpressionState:
        if not jump_state.stack or not _same_expression(
            jump_state.stack[-1], left
        ):
            self._error(
                "Short-circuit jump did not preserve its left operand",
                index,
            )
        if not fallthrough_state.stack or not isinstance(
            fallthrough_state.stack[-1], ast.expr
        ):
            self._error("Short-circuit branch produced no expression", index)
        operator = (
            ast.And()
            if "IF_FALSE" in self.tokens[index].kind
            else ast.Or()
        )
        expression = _bool_expression(
            operator,
            left,
            fallthrough_state.stack[-1],
        )
        return self._merge_prefix(
            jump_state,
            fallthrough_state,
            expression,
            index,
        )

    def _merge_conditional(
        self,
        index: int,
        true_state: _ExpressionState,
        false_state: _ExpressionState,
        predicate: ast.expr,
    ) -> _ExpressionState:
        if not true_state.stack or not false_state.stack:
            self._error("Conditional expression branch produced no value", index)
        when_true = true_state.stack[-1]
        when_false = false_state.stack[-1]
        converter = _StraightLineDecompiler(
            self.code,
            (),
            compile_mode="expr",
        )
        try:
            when_true = converter._expression_value(when_true)
            when_false = converter._expression_value(when_false)
        except Python311ParseError:
            pass
        if not isinstance(when_true, ast.expr) or not isinstance(
            when_false, ast.expr
        ):
            self._error("Conditional expression produced a non-expression", index)
        true_state.stack[-1] = when_true
        false_state.stack[-1] = when_false
        expression = _combine_decision(predicate, when_true, when_false)
        return self._merge_prefix(
            true_state,
            false_state,
            expression,
            index,
        )

    def _execute(
        self,
        index: int,
        stop: int,
        state: _ExpressionState,
        active: FrozenSet[Tuple[int, int]],
    ) -> _ExpressionState:
        marker = (index, stop)
        if marker in active:
            self._error("Expression control flow contains a cycle", index)
        active = active | {marker}

        while index != stop:
            if index == _VIRTUAL_EXIT:
                if stop != _VIRTUAL_EXIT:
                    self._error("Expression terminated before its merge point")
                return state
            if index == self.end:
                if stop != _VIRTUAL_EXIT:
                    self._error("Expression ended before its merge point")
                return state
            if index < self.start or index >= self.end:
                self._error("Expression execution left its instruction range")

            token = self.tokens[index]
            kind = token.kind
            if kind in self.terminal_kinds:
                if not state.stack or not isinstance(state.stack[-1], ast.expr):
                    self._error("Expression terminal has no value", index)
                if stop != _VIRTUAL_EXIT:
                    self._error("Expression terminated before its merge point", index)
                return state

            chained_condition = self._try_chained_condition(
                state,
                index,
            )
            if chained_condition is not None:
                index = chained_condition
                continue

            if kind.startswith(_CONDITIONAL_PREFIXES):
                join = self.immediate_post_dominators.get(index)
                if join is None:
                    self._error("Conditional expression has no merge point", index)
                target = self._target_index(index)
                following = (
                    index + 1 if index + 1 < self.end else _VIRTUAL_EXIT
                )

                if kind.startswith("JUMP_IF_"):
                    if not state.stack or not isinstance(state.stack[-1], ast.expr):
                        self._error(
                            "Short-circuit jump has no expression operand",
                            index,
                        )
                    left = state.stack[-1]
                    jump_state = self._execute(
                        target,
                        join,
                        state.clone(),
                        active,
                    )
                    fallthrough = state.clone()
                    fallthrough.stack.pop()
                    fallthrough_state = self._execute(
                        following,
                        join,
                        fallthrough,
                        active,
                    )
                    state = self._merge_short_circuit(
                        index,
                        jump_state,
                        fallthrough_state,
                        left,
                    )
                else:
                    if not state.stack or not isinstance(state.stack[-1], ast.expr):
                        self._error(
                            "Conditional jump has no expression predicate",
                            index,
                        )
                    branch_state = state.clone()
                    value = branch_state.stack.pop()
                    predicate = self._predicate(token, value)
                    jump_state = self._execute(
                        target,
                        join,
                        branch_state.clone(),
                        active,
                    )
                    fallthrough_state = self._execute(
                        following,
                        join,
                        branch_state,
                        active,
                    )
                    if "IF_FALSE" in kind:
                        true_state = fallthrough_state
                        false_state = jump_state
                    elif "IF_TRUE" in kind:
                        true_state = jump_state
                        false_state = fallthrough_state
                    elif "IF_NONE" in kind:
                        true_state = jump_state
                        false_state = fallthrough_state
                    elif "IF_NOT_NONE" in kind:
                        true_state = jump_state
                        false_state = fallthrough_state
                    else:
                        self._error("Unknown conditional jump outcome", index)
                    state = self._merge_conditional(
                        index,
                        true_state,
                        false_state,
                        predicate,
                    )

                if join == _VIRTUAL_EXIT:
                    return state
                index = join
                continue

            if kind in _UNCONDITIONAL_JUMPS:
                if "BACKWARD" in kind:
                    self._error("Expression control flow contains a loop", index)
                index = self._target_index(index)
                continue

            chained_compare = self._try_chained_compare(state, index)
            if chained_compare is not None:
                index = chained_compare
                continue

            self._dispatch(state, index)
            index += 1

        return state

    def decompile_values(self, count: int) -> Tuple[ast.expr, ...]:
        if count <= 0:
            self._error("Expression value count must be positive")
        state = self._execute(
            self.start,
            _VIRTUAL_EXIT,
            _ExpressionState(stack=[]),
            frozenset(),
        )
        if state.pending_keywords:
            self._error("Expression ended with pending keyword-call metadata")
        if len(state.stack) != count or not all(
            isinstance(value, ast.expr) for value in state.stack
        ):
            self._error(
                f"Expression produced {len(state.stack)} final stack values"
            )
        return tuple(
            (
                ast.JoinedStr(values=[value])
                if isinstance(value, ast.FormattedValue)
                else value
            )
            for value in state.stack
        )

    def decompile(self) -> ast.expr:
        return self.decompile_values(1)[0]


def recover_expression311(
    code,
    tokens,
    start: int = 0,
    end: Optional[int] = None,
    terminal_kinds: FrozenSet[str] = frozenset(
        {"PRINT_EXPR", "RETURN_VALUE"}
    ),
) -> ast.expr:
    return ExpressionDecompiler311(
        code,
        tokens,
        start=start,
        end=end,
        terminal_kinds=terminal_kinds,
    ).decompile()


def recover_expressions311(
    code,
    tokens,
    count: int,
    start: int = 0,
    end: Optional[int] = None,
    terminal_kinds: FrozenSet[str] = frozenset(
        {"PRINT_EXPR", "RETURN_VALUE"}
    ),
) -> Tuple[ast.expr, ...]:
    return ExpressionDecompiler311(
        code,
        tokens,
        start=start,
        end=end,
        terminal_kinds=terminal_kinds,
    ).decompile_values(count)


__all__ = [
    "ExpressionDecompiler311",
    "recover_expression311",
    "recover_expressions311",
]
