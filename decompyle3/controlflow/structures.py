"""Direct CFG-to-AST structuring for CPython 3.11.

Parser311 deliberately uses one control-flow recovery path: normalized
instructions are analyzed as a CFG and emitted directly as standard-library
``ast`` nodes. We do not synthesize legacy ``COME_FROM`` tokens.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from decompyle3.controlflow.cfg import (
    UNCONDITIONAL_JUMPS,
    build_cfg,
    instruction_target,
)
from decompyle3.controlflow.dominators import analyze_control_flow
from decompyle3.controlflow.exception_regions import build_exception_region_map
from decompyle3.controlflow.exceptiontable311 import decode_exception_table
from decompyle3.parsers.p311.base import (
    CO_ASYNC_GENERATOR,
    CO_COROUTINE,
    CO_GENERATOR,
    Python311ParseError,
    UnsupportedPython311ControlFlow,
    _COMPARE_OPERATORS,
    _IGNORED_INTERNAL,
    _StraightLineDecompiler,
)


_CONDITIONAL_JUMPS = {
    "POP_JUMP_BACKWARD_IF_FALSE",
    "POP_JUMP_BACKWARD_IF_NONE",
    "POP_JUMP_BACKWARD_IF_NOT_NONE",
    "POP_JUMP_BACKWARD_IF_TRUE",
    "POP_JUMP_FORWARD_IF_FALSE",
    "POP_JUMP_FORWARD_IF_NONE",
    "POP_JUMP_FORWARD_IF_NOT_NONE",
    "POP_JUMP_FORWARD_IF_TRUE",
}

_STATEMENT_BOUNDARIES = {
    "DELETE_ATTR",
    "DELETE_DEREF",
    "DELETE_FAST",
    "DELETE_GLOBAL",
    "DELETE_NAME",
    "DELETE_SUBSCR",
    "FOR_ITER",
    "GET_ITER",
    "POP_TOP",
    "PRINT_EXPR",
    "RAISE_VARARGS",
    "RETURN_VALUE",
    "STORE_ATTR",
    "STORE_DEREF",
    "STORE_FAST",
    "STORE_GLOBAL",
    "STORE_NAME",
    "STORE_SUBSCR",
}

_LATER_PHASE_OPS = set()


@dataclass
class _DecisionNode:
    start_index: int
    jump_index: int
    predicate: ast.expr
    true_offset: int
    false_offset: int


@dataclass
class _ConditionPlan:
    entry_offset: int
    nodes: Dict[int, _DecisionNode]
    endpoints: Tuple[int, int]
    true_endpoint: int
    false_endpoint: int
    expression: ast.expr


@dataclass(frozen=True)
class _TerminalIfPlan:
    test: ast.expr
    body_start: int
    body_end: int
    orelse_start: int
    orelse_end: int
    body_exit_kinds: FrozenSet[str]
    orelse_exit_kinds: FrozenSet[str]
    body_is_implicit_return_only: bool
    orelse_is_implicit_return_only: bool


@dataclass(frozen=True)
class _ImplicitReturnEpiloguePlan:
    test: ast.expr
    body_start: int
    region_end: int
    condition_blocks: FrozenSet[int]
    exit_blocks: FrozenSet[int]
    owned_offsets: FrozenSet[int]


@dataclass(frozen=True)
class _LoopContext:
    break_target: int
    continue_targets: frozenset


@dataclass(frozen=True)
class _RegionKey:
    start: int
    end: int
    trailing_return: bool
    break_target: Optional[int]
    continue_targets: FrozenSet[int]
    suppressed_exception_starts: FrozenSet[int]
    suppressed_exception_handlers: FrozenSet[int]
    suppressed_protocol_offsets: FrozenSet[int]
    suppressed_implicit_epilogue_offsets: FrozenSet[int]


def _negate(expression: ast.expr) -> ast.expr:
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        return expression.operand
    if isinstance(expression, ast.Compare) and len(expression.ops) == 1:
        inverse = {
            ast.Eq: ast.NotEq,
            ast.NotEq: ast.Eq,
            ast.Lt: ast.GtE,
            ast.LtE: ast.Gt,
            ast.Gt: ast.LtE,
            ast.GtE: ast.Lt,
            ast.In: ast.NotIn,
            ast.NotIn: ast.In,
            ast.Is: ast.IsNot,
            ast.IsNot: ast.Is,
        }
        operator = inverse.get(type(expression.ops[0]))
        if operator is not None:
            return ast.Compare(
                left=expression.left,
                ops=[operator()],
                comparators=expression.comparators,
            )
    return ast.UnaryOp(op=ast.Not(), operand=expression)


def _boolean_operation(operator, *expressions: ast.expr) -> ast.BoolOp:
    values = []
    for expression in expressions:
        if isinstance(expression, ast.BoolOp) and isinstance(
            expression.op, operator
        ):
            values.extend(expression.values)
        else:
            values.append(expression)
    return ast.BoolOp(op=operator(), values=values)


def _combine_decision(
    predicate: ast.expr, when_true: ast.expr, when_false: ast.expr
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
        return _boolean_operation(ast.And, predicate, when_true)
    if true_constant is False:
        return _boolean_operation(ast.And, _negate(predicate), when_false)
    if true_constant is True:
        return _boolean_operation(ast.Or, predicate, when_false)
    if false_constant is True:
        return _boolean_operation(ast.Or, _negate(predicate), when_true)
    return ast.IfExp(test=predicate, body=when_true, orelse=when_false)


class StructuredDecompiler311(_StraightLineDecompiler):
    """Extend the phase-3 stack parser with one CFG-backed structuring path."""

    def __init__(self, code, tokens, compile_mode="exec", is_class_body=False):
        super(StructuredDecompiler311, self).__init__(
            code,
            tokens,
            compile_mode=compile_mode,
            is_class_body=is_class_body,
        )
        self.offset_to_index = {
            token.offset: index for index, token in enumerate(self.tokens)
        }
        flow_tokens = self.tokens
        if flow_tokens and flow_tokens[0].kind == "RETURN_GENERATOR":
            flow_tokens = flow_tokens[2:]
        self.exception_regions = decode_exception_table(code)
        self.exception_region_map = build_exception_region_map(
            self.exception_regions
        )
        self.exception_states = {}
        self._suppressed_exception_starts = set()
        self._suppressed_exception_handler_targets = set()
        self._suppressed_exception_protocol_offsets = set()
        self._suppressed_implicit_epilogue_offsets = set()
        self._suppressed_loop_starts = set()
        self._active_regions: Set[_RegionKey] = set()
        self._region_work_count = 0
        self._region_work_limit = max(1024, len(self.tokens) * 64)
        self._latch_expression_memo: Dict[Tuple[int, int], int] = {}
        self.cfg = build_cfg(flow_tokens, self.exception_regions)
        self.control_flow = analyze_control_flow(self.cfg)

    def _validate_scope(self):
        for token in self.tokens:
            if token.kind in _LATER_PHASE_OPS:
                self.current_token = token
                self._error(
                    "This opcode is not supported by the CPython 3.11 "
                    "structure decompiler",
                    UnsupportedPython311ControlFlow,
                )

    def _await_protocol(self, index: int) -> int:
        value = self._pop_expr()
        send_index = next(
            (
                cursor
                for cursor in range(index + 1, len(self.tokens))
                if self.tokens[cursor].kind == "SEND"
            ),
            None,
        )
        if send_index is None:
            self._error("GET_AWAITABLE has no SEND protocol")
        target = instruction_target(self.tokens[send_index])
        target_index = self.offset_to_index[target]
        is_async_comprehension = isinstance(
            value,
            (ast.DictComp, ast.GeneratorExp, ast.ListComp, ast.SetComp),
        ) and any(generator.is_async for generator in value.generators)
        self.stack.append(
            value
            if is_async_comprehension
            else ast.Await(value=value)
        )
        return target_index

    def _yield_from_protocol(self, index: int, end: int) -> int:
        value = self._pop_expr()
        send_index = next(
            (
                cursor
                for cursor in range(index + 1, len(self.tokens))
                if self.tokens[cursor].kind == "SEND"
            ),
            None,
        )
        if send_index is None:
            self._error("GET_YIELD_FROM_ITER has no SEND protocol")
        target = instruction_target(self.tokens[send_index])
        target_index = self.offset_to_index[target]
        expression = ast.YieldFrom(value=value)
        if (
            target_index < end
            and self.tokens[target_index].kind == "POP_TOP"
        ):
            self.body.append(ast.Expr(value=expression))
            return target_index + 1
        self.stack.append(expression)
        return target_index

    def _yield_value(self, index: int, end: int) -> int:
        value = self._pop_expr()
        expression = ast.Yield(value=value)
        cursor = index + 1
        if (
            cursor < end
            and self.tokens[cursor].kind == "INTERNAL_RESUME"
        ):
            cursor += 1
        if cursor < end and self.tokens[cursor].kind == "POP_TOP":
            self.body.append(ast.Expr(value=expression))
            return cursor + 1
        self.stack.append(expression)
        return cursor

    def _expression_slice(self, start: int, end: int) -> ast.expr:
        from decompyle3.parsers.p311.expressions import recover_expression311

        return recover_expression311(
            self.code,
            self.tokens,
            start=start,
            end=end,
            terminal_kinds=frozenset(),
        )

    def _condition_jump(
        self,
        start: int,
        end: Optional[int] = None,
        stop_offsets: FrozenSet[int] = frozenset(),
    ) -> Optional[int]:
        limit = len(self.tokens) if end is None else min(end, len(self.tokens))
        for index in range(start, limit):
            if (
                self.tokens[index].offset
                in self._suppressed_exception_protocol_offsets
            ):
                continue
            if (
                index > start
                and self.tokens[index].offset in stop_offsets
            ):
                return None
            kind = self.tokens[index].kind
            if kind in _CONDITIONAL_JUMPS:
                return index
            if (
                kind
                in (
                    "STORE_DEREF",
                    "STORE_FAST",
                    "STORE_GLOBAL",
                    "STORE_NAME",
                )
                and index > start
                and self.tokens[index - 1].kind == "COPY_STACK"
                and self.tokens[index - 1].attr == 1
            ):
                continue
            if (
                kind == "GET_ITER"
                and index + 1 < len(self.tokens)
                and self.tokens[index + 1].kind != "FOR_ITER"
            ):
                # A comprehension function receives its hidden iterator as
                # part of a CALL expression; only GET_ITER/FOR_ITER starts a
                # source-level loop statement.
                continue
            if (
                kind in _STATEMENT_BOUNDARIES
                or kind in UNCONDITIONAL_JUMPS
                or kind.startswith("JUMP_IF_")
            ):
                return None
        return None

    def _predicate(self, start: int, jump_index: int) -> ast.expr:
        expression = self._expression_slice(start, jump_index)
        kind = self.tokens[jump_index].kind
        if "IF_NOT_NONE" in kind:
            return ast.Compare(
                left=expression,
                ops=[ast.IsNot()],
                comparators=[ast.Constant(value=None)],
            )
        if "IF_NONE" in kind:
            return ast.Compare(
                left=expression,
                ops=[ast.Is()],
                comparators=[ast.Constant(value=None)],
            )
        return expression

    def _jump_outcomes(self, jump_index: int) -> Tuple[int, int]:
        token = self.tokens[jump_index]
        target = instruction_target(token)
        following = self.tokens[jump_index + 1].offset
        if "IF_FALSE" in token.kind:
            return following, target
        if "IF_TRUE" in token.kind:
            return target, following
        if "IF_NOT_NONE" in token.kind or "IF_NONE" in token.kind:
            return target, following
        self.current_token = token
        self._error("Unknown conditional jump outcome")

    def _resolve_condition_endpoint(self, offset: int) -> int:
        """Resolve compiler-generated forward-jump trampolines."""
        seen = set()
        while offset not in seen:
            seen.add(offset)
            index = self.offset_to_index.get(offset)
            if index is None:
                return offset
            while (
                index < len(self.tokens)
                and self.tokens[index].kind in _IGNORED_INTERNAL
            ):
                index += 1
            if index >= len(self.tokens):
                return offset
            offset = self.tokens[index].offset
            if self.tokens[index].kind != "JUMP_FORWARD":
                return offset
            offset = instruction_target(self.tokens[index])
        return offset

    def _condition_endpoint_signature(self, offset: int) -> Optional[str]:
        """Describe a side-effect-free condition endpoint for merging."""
        start = self.offset_to_index.get(offset)
        if start is None:
            return None
        if (
            self.tokens[start].kind in ("RERAISE", "RETURN_VALUE")
            and self.tokens[start].offset
            in self._suppressed_exception_protocol_offsets
        ):
            return "__suppressed_exception_exit__"
        while (
            start < len(self.tokens)
            and (
                self.tokens[start].kind in _IGNORED_INTERNAL
                or self.tokens[start].offset
                in self._suppressed_exception_protocol_offsets
            )
        ):
            start += 1
        if start >= len(self.tokens):
            return None
        token = self.tokens[start]
        if token.kind in UNCONDITIONAL_JUMPS:
            target = instruction_target(token)
            if target is not None:
                return f"jump:{target}"
        for index in range(start, len(self.tokens)):
            kind = self.tokens[index].kind
            if kind == "RETURN_VALUE":
                try:
                    expression = self._expression_slice(start, index)
                except Python311ParseError:
                    return None
                return ast.dump(expression, include_attributes=False)
            if (
                kind in _CONDITIONAL_JUMPS
                or kind in UNCONDITIONAL_JUMPS
                or kind in _STATEMENT_BOUNDARIES
            ):
                return None
        return None

    def _equivalent_condition_endpoints(
        self,
        left: int,
        right: int,
    ) -> bool:
        left_signature = self._condition_endpoint_signature(left)
        return (
            left_signature is not None
            and left_signature == self._condition_endpoint_signature(right)
        )

    def _coalesce_condition_endpoints(
        self,
        nodes: Dict[int, _DecisionNode],
        endpoints: Set[int],
    ) -> Tuple[Dict[int, _DecisionNode], Set[int]]:
        if len(endpoints) <= 2:
            return nodes, endpoints
        canonical = {}
        by_signature = {}
        for endpoint in sorted(endpoints):
            signature = self._condition_endpoint_signature(endpoint)
            if signature is None:
                canonical[endpoint] = endpoint
                continue
            canonical[endpoint] = by_signature.setdefault(signature, endpoint)
        if all(endpoint == target for endpoint, target in canonical.items()):
            return nodes, endpoints
        remapped = {
            offset: _DecisionNode(
                start_index=node.start_index,
                jump_index=node.jump_index,
                predicate=node.predicate,
                true_offset=canonical.get(node.true_offset, node.true_offset),
                false_offset=canonical.get(node.false_offset, node.false_offset),
            )
            for offset, node in nodes.items()
        }
        return remapped, {canonical.get(endpoint, endpoint) for endpoint in endpoints}

    def _chained_condition_plan(
        self,
        start: int,
    ) -> Optional[_ConditionPlan]:
        """Recover a chained comparison whose first result is already stacked."""
        if (
            start >= len(self.tokens)
            or self.tokens[start].kind not in _CONDITIONAL_JUMPS
            or len(self.stack) < 2
            or not isinstance(self.stack[-1], ast.Compare)
            or len(self.stack[-1].ops) != 1
            or len(self.stack[-1].comparators) != 1
            or not isinstance(self.stack[-2], ast.expr)
            or ast.dump(
                self.stack[-2],
                include_attributes=False,
            )
            != ast.dump(
                self.stack[-1].comparators[0],
                include_attributes=False,
            )
        ):
            return None

        first = self.stack[-1]
        cleanup_offset = instruction_target(self.tokens[start])
        cleanup_index = self.offset_to_index.get(cleanup_offset)
        if (
            cleanup_index is None
            or self.tokens[cleanup_index].kind != "POP_TOP"
            or cleanup_index + 1 >= len(self.tokens)
        ):
            return None

        operators = list(first.ops)
        comparators = list(first.comparators)
        cursor = start + 1
        final_jump = None
        direct_none_jump = None
        while cursor < cleanup_index:
            while (
                cursor < cleanup_index
                and self.tokens[cursor].kind in _IGNORED_INTERNAL
            ):
                cursor += 1
            if (
                cursor < cleanup_index
                and self.tokens[cursor].kind.startswith("POP_JUMP_")
                and (
                    "IF_NONE" in self.tokens[cursor].kind
                    or "IF_NOT_NONE" in self.tokens[cursor].kind
                )
            ):
                final_jump = cursor
                direct_none_jump = cursor
                break
            marker = next(
                (
                    index
                    for index in range(cursor, cleanup_index)
                    if self.tokens[index].kind in _COMPARE_OPERATORS
                    or self.tokens[index].kind == "SWAP_STACK"
                ),
                None,
            )
            if marker is None or marker == cursor:
                return None
            try:
                comparator = self._expression_slice(cursor, marker)
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
                    or self.tokens[jump_index].kind
                    not in _CONDITIONAL_JUMPS
                    or instruction_target(self.tokens[jump_index])
                    != cleanup_offset
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
                jump_index >= len(self.tokens)
                or self.tokens[jump_index].kind not in _CONDITIONAL_JUMPS
            ):
                return None
            operators.append(_COMPARE_OPERATORS[self.tokens[marker].kind]())
            comparators.append(comparator)
            final_jump = jump_index
            break

        if final_jump is None:
            return None

        cleanup_endpoint = self._resolve_condition_endpoint(
            self.tokens[cleanup_index + 1].offset
        )
        if direct_none_jump is not None:
            token = self.tokens[direct_none_jump]
            target_endpoint = self._resolve_condition_endpoint(
                instruction_target(token)
            )
            following_endpoint = self._resolve_condition_endpoint(
                self.tokens[direct_none_jump + 1].offset
            )
            target_is_cleanup = (
                target_endpoint == cleanup_endpoint
                or self._equivalent_condition_endpoints(
                    target_endpoint,
                    cleanup_endpoint,
                )
            )
            following_is_cleanup = (
                following_endpoint == cleanup_endpoint
                or self._equivalent_condition_endpoints(
                    following_endpoint,
                    cleanup_endpoint,
                )
            )
            if target_is_cleanup == following_is_cleanup:
                return None
            token_operator = (
                ast.IsNot if "IF_NOT_NONE" in token.kind else ast.Is
            )
            if target_is_cleanup:
                operator = ast.Is if token_operator is ast.IsNot else ast.IsNot
                true_endpoint = following_endpoint
                false_endpoint = target_endpoint
            else:
                operator = token_operator
                true_endpoint = target_endpoint
                false_endpoint = following_endpoint
            operators.append(operator())
            comparators.append(ast.Constant(value=None))
        else:
            true_offset, false_offset = self._jump_outcomes(final_jump)
            true_endpoint = self._resolve_condition_endpoint(true_offset)
            false_endpoint = self._resolve_condition_endpoint(false_offset)
        if (
            true_endpoint == false_endpoint
            or (
                cleanup_endpoint != false_endpoint
                and not self._equivalent_condition_endpoints(
                    cleanup_endpoint,
                    false_endpoint,
                )
            )
        ):
            return None

        predicate = ast.Compare(
            left=first.left,
            ops=operators,
            comparators=comparators,
        )
        entry_offset = self.tokens[start].offset
        node = _DecisionNode(
            start_index=start,
            jump_index=final_jump,
            predicate=predicate,
            true_offset=true_endpoint,
            false_offset=false_endpoint,
        )
        del self.stack[-2:]
        return _ConditionPlan(
            entry_offset=entry_offset,
            nodes={entry_offset: node},
            endpoints=tuple(sorted((true_endpoint, false_endpoint))),
            true_endpoint=true_endpoint,
            false_endpoint=false_endpoint,
            expression=predicate,
        )

    def _condition_plan(self, start: int) -> Optional[_ConditionPlan]:
        return self._bounded_condition_plan(start, len(self.tokens))

    def _if_expression_condition_plan(
        self,
        start: int,
        end: int,
    ) -> Optional[_ConditionPlan]:
        """Recover ``if <left> if <test> else <right>`` decision graphs."""
        first_jump = self._condition_jump(start, end)
        if first_jump is None:
            return None
        _, alternate_offset = self._jump_outcomes(first_jump)
        alternate_index = self.offset_to_index.get(alternate_offset)
        if (
            alternate_index is None
            or not first_jump + 1 < alternate_index < end
        ):
            return None
        bridge = self._last_forward_jump(
            first_jump + 1,
            alternate_index,
            alternate_offset + 1,
        )
        if bridge is None:
            return None
        true_endpoint = self._resolve_condition_endpoint(
            instruction_target(self.tokens[bridge])
        )
        true_index = self.offset_to_index.get(true_endpoint)
        if true_index is None or not alternate_index < true_index <= end:
            return None

        alternate_jump = None
        for index in range(alternate_index, true_index):
            if self.tokens[index].kind in _CONDITIONAL_JUMPS:
                alternate_jump = index
        if alternate_jump is None:
            return None
        alternate_outcomes = tuple(
            self._resolve_condition_endpoint(offset)
            for offset in self._jump_outcomes(alternate_jump)
        )
        if true_endpoint not in alternate_outcomes:
            return None
        false_endpoint = next(
            (
                offset
                for offset in alternate_outcomes
                if offset != true_endpoint
            ),
            None,
        )
        if (
            false_endpoint is None
            or false_endpoint == true_endpoint
            or false_endpoint not in self.offset_to_index
        ):
            return None

        nodes: Dict[int, _DecisionNode] = {}
        active = set()

        def build(offset: int) -> ast.expr:
            offset = self._resolve_condition_endpoint(offset)
            if offset == true_endpoint:
                return ast.Constant(value=True)
            if offset == false_endpoint:
                return ast.Constant(value=False)
            if offset in active:
                raise ValueError("conditional expression graph has a cycle")
            index = self.offset_to_index.get(offset)
            if index is None or index >= end:
                raise ValueError("conditional expression has an unsafe sink")
            jump = self._condition_jump(index, end)
            if jump is None:
                raise ValueError("conditional expression branch has no jump")
            predicate = self._predicate(index, jump)
            true_offset, false_offset = (
                self._resolve_condition_endpoint(outcome)
                for outcome in self._jump_outcomes(jump)
            )
            nodes[offset] = _DecisionNode(
                start_index=index,
                jump_index=jump,
                predicate=predicate,
                true_offset=true_offset,
                false_offset=false_offset,
            )
            active.add(offset)
            try:
                return _combine_decision(
                    predicate,
                    build(true_offset),
                    build(false_offset),
                )
            finally:
                active.remove(offset)

        entry_offset = self.tokens[start].offset
        try:
            expression = build(entry_offset)
        except (Python311ParseError, ValueError):
            return None
        return _ConditionPlan(
            entry_offset=entry_offset,
            nodes=nodes,
            endpoints=tuple(sorted((true_endpoint, false_endpoint))),
            true_endpoint=true_endpoint,
            false_endpoint=false_endpoint,
            expression=expression,
        )

    def _bounded_condition_plan(
        self,
        start: int,
        end: int,
        stop_offsets: FrozenSet[int] = frozenset(),
    ) -> Optional[_ConditionPlan]:
        if_expression = self._if_expression_condition_plan(start, end)
        if if_expression is not None:
            return if_expression
        chained = self._chained_condition_plan(start)
        if chained is not None:
            return chained
        jump_index = self._condition_jump(start, end, stop_offsets)
        if jump_index is None:
            return None
        stacked_predicate = None
        if (
            jump_index == start
            and self.stack
            and isinstance(self.stack[-1], ast.expr)
        ):
            stacked_predicate = self.stack[-1]
        else:
            try:
                self._predicate(start, jump_index)
            except Python311ParseError:
                # A conditional expression may be nested inside an already
                # partially constructed call or collection.  In that case the
                # current instruction is a stack prefix, not the predicate
                # boundary.  Let the straight-line parser consume one
                # instruction and retry from the next logical value.
                return None
        condition_line = next(
            (
                self.tokens[index].linestart
                for index in range(start, jump_index + 1)
                if self.tokens[index].linestart is not None
            ),
            None,
        )
        if condition_line is None:
            condition_line = next(
                (
                    self.tokens[index].linestart
                    for index in range(start - 1, -1, -1)
                    if self.tokens[index].linestart is not None
                ),
                None,
            )

        def collect(allow_multiline: bool):
            nodes: Dict[int, _DecisionNode] = {}
            endpoints: Set[int] = set()
            pending = [start]

            while pending:
                node_start = pending.pop()
                offset = self.tokens[node_start].offset
                if offset in nodes:
                    continue
                node_jump = self._condition_jump(
                    node_start,
                    end,
                    stop_offsets,
                )
                if node_jump is None:
                    endpoints.add(offset)
                    continue
                if node_start == start and stacked_predicate is not None:
                    predicate = stacked_predicate
                else:
                    try:
                        predicate = self._predicate(node_start, node_jump)
                    except Python311ParseError:
                        endpoints.add(offset)
                        continue
                true_offset, false_offset = self._jump_outcomes(node_jump)
                nodes[offset] = _DecisionNode(
                    start_index=node_start,
                    jump_index=node_jump,
                    predicate=predicate,
                    true_offset=true_offset,
                    false_offset=false_offset,
                )
                for successor, sibling in (
                    (true_offset, false_offset),
                    (false_offset, true_offset),
                ):
                    successor_index = self.offset_to_index[successor]
                    if successor_index >= end:
                        endpoints.add(successor)
                        continue
                    successor_token = self.tokens[successor_index]
                    block = self.cfg.block_at(successor)
                    has_single_predecessor = (
                        len(self.cfg.predecessors(block.index)) == 1
                    )
                    successor_jump = self._condition_jump(
                        successor_index,
                        end,
                        stop_offsets,
                    )
                    same_condition_line = successor_token.linestart in (
                        None,
                        condition_line,
                    )
                    rejoins_sibling = False
                    if successor_jump is not None:
                        jump_line = self.tokens[
                            successor_jump
                        ].linestart
                        same_condition_line = (
                            same_condition_line
                            or jump_line == condition_line
                        )
                        successor_outcomes = self._jump_outcomes(
                            successor_jump
                        )
                        rejoins_sibling = sibling in successor_outcomes
                    if (
                        has_single_predecessor
                        and (
                            same_condition_line
                            or (allow_multiline and rejoins_sibling)
                        )
                        and successor_jump is not None
                    ):
                        pending.append(successor_index)
                    else:
                        endpoints.add(successor)
            return nodes, endpoints

        def reduce_decision_endpoints(
            nodes: Dict[int, _DecisionNode],
            endpoints: Set[int],
        ) -> Tuple[Dict[int, _DecisionNode], Set[int]]:
            """Expand pure nested decisions when they reduce to shared sinks."""

            while len(endpoints) > 2:
                reduced = False
                for candidate in sorted(endpoints):
                    candidate_index = self.offset_to_index.get(candidate)
                    if candidate_index is None or candidate_index >= end:
                        continue
                    candidate_block = self.cfg.block_at(candidate)
                    if len(self.cfg.predecessors(candidate_block.index)) != 1:
                        continue

                    extra_nodes = {}
                    leaves = set()
                    pending = [candidate]
                    work_count = 0
                    while pending:
                        work_count += 1
                        if work_count > max(32, len(self.tokens) * 2):
                            extra_nodes = {}
                            break
                        offset = pending.pop()
                        if offset in nodes or offset in extra_nodes:
                            continue
                        if offset != candidate and offset in endpoints:
                            leaves.add(offset)
                            continue
                        index = self.offset_to_index.get(offset)
                        if (
                            index is None
                            or index >= end
                            or offset in stop_offsets
                        ):
                            leaves.add(offset)
                            continue
                        jump = self._condition_jump(
                            index,
                            end,
                            stop_offsets,
                        )
                        if jump is None:
                            leaves.add(offset)
                            continue
                        try:
                            predicate = self._predicate(index, jump)
                        except Python311ParseError:
                            leaves.add(offset)
                            continue
                        true_offset, false_offset = self._jump_outcomes(jump)
                        extra_nodes[offset] = _DecisionNode(
                            start_index=index,
                            jump_index=jump,
                            predicate=predicate,
                            true_offset=true_offset,
                            false_offset=false_offset,
                        )
                        pending.extend((true_offset, false_offset))

                    revised = (set(endpoints) - {candidate}) | leaves
                    if (
                        extra_nodes
                        and leaves
                        and len(revised) < len(endpoints)
                    ):
                        nodes.update(extra_nodes)
                        endpoints = revised
                        reduced = True
                        break
                if not reduced:
                    break
            return nodes, endpoints

        collected = collect(allow_multiline=True)
        if collected is None:
            return None
        nodes, endpoints = collected
        nodes, endpoints = self._coalesce_condition_endpoints(
            nodes,
            endpoints,
        )
        nodes, endpoints = reduce_decision_endpoints(nodes, endpoints)
        if len(endpoints) != 2:
            collected = collect(allow_multiline=False)
            if collected is None:
                return None
            nodes, endpoints = collected
            nodes, endpoints = self._coalesce_condition_endpoints(
                nodes,
                endpoints,
            )
            nodes, endpoints = reduce_decision_endpoints(nodes, endpoints)
        if len(endpoints) != 2:
            return None

        def path_to_shared_endpoint(
            offset: int,
            stop: int,
            active: FrozenSet[int],
        ):
            if offset == stop:
                return {}, {stop}
            if offset in active:
                return None
            index = self.offset_to_index.get(offset)
            if index is None:
                return None
            block = self.cfg.block_at(offset)
            condition_predecessors = {
                self.cfg.block_at(node_offset).index
                for node_offset in active
                if node_offset in self.offset_to_index
            }
            incoming = self.cfg.incoming(block.index)
            if any(edge.kind == "exception" for edge in incoming):
                return None
            predecessors = {
                edge.source
                for edge in incoming
                if edge.kind != "exception"
            }
            if (
                not predecessors
                or not predecessors <= condition_predecessors
            ):
                return None
            if index >= end:
                return None
            node_jump = self._condition_jump(index, end, stop_offsets)
            if node_jump is None:
                return None
            try:
                predicate = self._predicate(index, node_jump)
            except Python311ParseError:
                return None
            true_offset, false_offset = self._jump_outcomes(node_jump)
            node = _DecisionNode(
                start_index=index,
                jump_index=node_jump,
                predicate=predicate,
                true_offset=true_offset,
                false_offset=false_offset,
            )
            for path_offset, side_offset in (
                (true_offset, false_offset),
                (false_offset, true_offset),
            ):
                path = path_to_shared_endpoint(
                    path_offset,
                    stop,
                    active | {offset},
                )
                if path is None:
                    continue
                path_nodes, path_endpoints = path
                return (
                    {offset: node, **path_nodes},
                    set(path_endpoints) | {side_offset},
                )
            return None

        # A parenthesized or backslash-continued boolean condition may span
        # several source lines.  Extend one fallback branch only when its
        # decision chain provably rejoins the sibling endpoint; this avoids
        # absorbing a following independent ``if`` statement.
        for branch, stop in (
            tuple(endpoints),
            tuple(reversed(tuple(endpoints))),
        ):
            extension = path_to_shared_endpoint(
                branch,
                stop,
                frozenset(nodes),
            )
            if extension is None:
                continue
            extra_nodes, extended_endpoints = extension
            extra_nodes, extended_endpoints = (
                self._coalesce_condition_endpoints(
                    extra_nodes,
                    extended_endpoints,
                )
            )
            if len(extended_endpoints) == 2:
                nodes.update(extra_nodes)
                endpoints = extended_endpoints
                break
        true_endpoint, false_endpoint = sorted(endpoints)

        entry_offset = self.tokens[start].offset
        expressions = {}
        active_offsets = set()
        pending = [(entry_offset, False)]
        work_count = 0
        work_limit = max(64, len(nodes) * 6 + 16)
        try:
            while pending:
                work_count += 1
                if work_count > work_limit:
                    raise ValueError("condition work limit exceeded")
                offset, expanded = pending.pop()
                if offset in expressions:
                    continue
                node = nodes.get(offset)
                if node is None:
                    expressions[offset] = ast.Constant(
                        value=offset == true_endpoint
                    )
                    continue
                if expanded:
                    if (
                        node.true_offset not in expressions
                        or node.false_offset not in expressions
                    ):
                        raise ValueError("condition dependency was not resolved")
                    expressions[offset] = _combine_decision(
                        node.predicate,
                        expressions[node.true_offset],
                        expressions[node.false_offset],
                    )
                    active_offsets.remove(offset)
                    continue
                if offset in active_offsets:
                    raise ValueError("condition decision graph contains a cycle")
                active_offsets.add(offset)
                pending.append((offset, True))
                pending.append((node.false_offset, False))
                pending.append((node.true_offset, False))
        except ValueError:
            return None

        plan = _ConditionPlan(
            entry_offset=entry_offset,
            nodes=nodes,
            endpoints=tuple(sorted(endpoints)),
            true_endpoint=true_endpoint,
            false_endpoint=false_endpoint,
            expression=expressions[entry_offset],
        )
        if stacked_predicate is not None:
            self.stack.pop()
        return plan

    def _capture_region(
        self,
        start: int,
        end: int,
        loop: Optional[_LoopContext],
        trailing_return: bool = False,
    ) -> List[ast.stmt]:
        key = _RegionKey(
            start=start,
            end=end,
            trailing_return=trailing_return,
            break_target=loop.break_target if loop is not None else None,
            continue_targets=(
                loop.continue_targets if loop is not None else frozenset()
            ),
            suppressed_exception_starts=frozenset(
                self._suppressed_exception_starts
            ),
            suppressed_exception_handlers=frozenset(
                self._suppressed_exception_handler_targets
            ),
            suppressed_protocol_offsets=frozenset(
                self._suppressed_exception_protocol_offsets
            ),
            suppressed_implicit_epilogue_offsets=frozenset(
                self._suppressed_implicit_epilogue_offsets
            ),
        )
        if key in self._active_regions:
            if 0 <= start < len(self.tokens):
                self.current_token = self.tokens[start]
            self._error(
                "Structured region cycle detected",
                UnsupportedPython311ControlFlow,
            )
        if self._region_work_count >= self._region_work_limit:
            if 0 <= start < len(self.tokens):
                self.current_token = self.tokens[start]
            self._error(
                "Structured region work limit exceeded",
                UnsupportedPython311ControlFlow,
            )
        self._region_work_count += 1
        self._active_regions.add(key)
        old_body = self.body
        old_stack = self.stack
        old_assignment = self.pending_assignment_value
        old_targets = self.pending_assignment_targets
        self.body = []
        self.stack = []
        self.pending_assignment_value = None
        self.pending_assignment_targets = []
        try:
            self._parse_region(start, end, loop)
            self._flush_assignment()
            result = self.body
            if (
                trailing_return
                and len(self.stack) == 1
                and isinstance(self.stack[-1], ast.expr)
            ):
                result.append(ast.Return(value=self.stack.pop()))
            if self.stack:
                self._error("Structured statement region left stack values")
            return result
        finally:
            self._active_regions.remove(key)
            self.body = old_body
            self.stack = old_stack
            self.pending_assignment_value = old_assignment
            self.pending_assignment_targets = old_targets

    def _last_forward_jump(
        self,
        start: int,
        end: int,
        minimum_target: int,
        excluded_target: Optional[int] = None,
    ) -> Optional[int]:
        for index in range(end - 1, start - 1, -1):
            token = self.tokens[index]
            target = instruction_target(token)
            if (
                token.kind == "JUMP_FORWARD"
                and target >= minimum_target
                and target != excluded_target
            ):
                return index
        return None

    def _region_end_offset(self, end: int) -> int:
        if end < len(self.tokens):
            return self.tokens[end].offset
        if not self.tokens:
            return 0
        return self.tokens[-1].offset + 2

    def _normal_edges_from(self, block_index: int):
        return tuple(
            edge
            for edge in self.cfg.outgoing(block_index)
            if edge.kind != "exception"
        )

    def _terminal_interval_exit_kinds(
        self,
        start: int,
        end: int,
        condition_blocks: FrozenSet[int],
    ) -> Optional[FrozenSet[str]]:
        """Prove that one physical branch interval terminates locally."""
        if not 0 <= start < end <= len(self.tokens):
            return None
        lower = self.tokens[start].offset
        upper = self._region_end_offset(end)
        start_block = self.cfg.offset_to_block.get(lower)
        if start_block is None:
            return None

        interval_blocks = {
            block.index
            for block in self.cfg.blocks
            if lower <= block.start < upper
        }
        if start_block not in interval_blocks:
            return None
        if any(
            edge.kind == "exception"
            and (
                edge.source in interval_blocks
                or edge.target in interval_blocks
            )
            for edge in self.cfg.edges
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
            for edge in self._normal_edges_from(block_index):
                if edge.target not in interval_blocks:
                    return None
                pending.append(edge.target)

        if visited != interval_blocks:
            return None
        for block_index in visited:
            outside_predecessors = {
                edge.source
                for edge in self.cfg.incoming(block_index)
                if edge.kind != "exception" and edge.source not in visited
            }
            if block_index == start_block:
                if not outside_predecessors:
                    return None
                if not outside_predecessors <= condition_blocks:
                    return None
            elif outside_predecessors:
                return None

        exits = {
            self.cfg.block(block_index).terminator
            for block_index in visited
            if not self._normal_edges_from(block_index)
        }
        if any(
            self.cfg.block(block_index).terminator == "RETURN_VALUE"
            and self._normal_edges_from(block_index)
            for block_index in visited
        ):
            return None
        if not exits or exits != {"RETURN_VALUE"}:
            return None
        return frozenset(exits)

    def _is_implicit_none_return_only(self, start: int, end: int) -> bool:
        semantic = [
            token
            for token in self.tokens[start:end]
            if token.kind not in _IGNORED_INTERNAL
        ]
        return (
            len(semantic) == 2
            and semantic[0].kind == "LOAD_CONST"
            and semantic[0].attr is None
            and semantic[1].kind == "RETURN_VALUE"
        )

    def _implicit_return_epilogue_plan(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
        region_end: int,
    ) -> Optional[_ImplicitReturnEpiloguePlan]:
        """Prove that duplicated terminal None returns are one fallthrough."""
        flags = int(getattr(self.code, "co_flags", 0))
        if (
            loop is not None
            or region_end != len(self.tokens)
            or self.is_class_body
            or getattr(self.code, "co_name", "<module>") == "<module>"
            or flags & (CO_GENERATOR | CO_COROUTINE | CO_ASYNC_GENERATOR)
        ):
            return None

        semantic_indices = [
            index
            for index in range(region_end)
            if self.tokens[index].kind not in _IGNORED_INTERNAL
        ]
        pairs = []
        cursor = len(semantic_indices)
        while cursor >= 2:
            load_index = semantic_indices[cursor - 2]
            return_index = semantic_indices[cursor - 1]
            load = self.tokens[load_index]
            returned = self.tokens[return_index]
            if not (
                load.kind == "LOAD_CONST"
                and load.attr is None
                and returned.kind == "RETURN_VALUE"
            ):
                break
            pairs.append((load_index, return_index))
            cursor -= 2
        if len(pairs) < 2:
            return None
        pairs.reverse()
        cluster_start = pairs[0][0]

        condition_block_indexes = tuple(
            self.cfg.offset_to_block.get(
                self.tokens[node.jump_index].offset
            )
            for node in plan.nodes.values()
        )
        if not condition_block_indexes or any(
            block_index is None
            for block_index in condition_block_indexes
        ):
            return None
        condition_blocks = frozenset(condition_block_indexes)

        pair_blocks = set()
        owned_offsets = set()
        for load_index, return_index in pairs:
            load = self.tokens[load_index]
            returned = self.tokens[return_index]
            load_block = self.cfg.offset_to_block.get(load.offset)
            return_block = self.cfg.offset_to_block.get(returned.offset)
            if load_block is None or load_block != return_block:
                return None
            block = self.cfg.block(load_block)
            if (
                block.terminator != "RETURN_VALUE"
                or self._normal_edges_from(load_block)
            ):
                return None
            pair_blocks.add(load_block)
            owned_offsets.update((load.offset, returned.offset))
        if len(pair_blocks) != len(pairs):
            return None

        entry_block = self.cfg.offset_to_block.get(plan.entry_offset)
        if entry_block is None:
            return None

        pending = [entry_block]
        visited = set()
        work_limit = max(64, len(self.cfg.blocks) * 4)
        work_count = 0
        while pending:
            work_count += 1
            if work_count > work_limit:
                return None
            block_index = pending.pop()
            if block_index in visited:
                continue
            visited.add(block_index)
            block = self.cfg.block(block_index)
            for edge in self._normal_edges_from(block_index):
                target = self.cfg.block(edge.target)
                if target.start <= block.start:
                    return None
                pending.append(edge.target)

        if not condition_blocks <= visited:
            return None
        if any(
            edge.kind == "exception"
            and (edge.source in visited or edge.target in visited)
            for edge in self.cfg.edges
        ):
            return None
        for block_index in visited:
            outside_predecessors = {
                edge.source
                for edge in self.cfg.incoming(block_index)
                if edge.kind != "exception" and edge.source not in visited
            }
            if outside_predecessors:
                if block_index != entry_block:
                    return None
                entry_start = self.cfg.block(entry_block).start
                if any(
                    self.cfg.block(source).start >= entry_start
                    for source in outside_predecessors
                ):
                    return None

        exit_blocks = {
            block_index
            for block_index in visited
            if not self._normal_edges_from(block_index)
        }
        if exit_blocks != pair_blocks:
            return None

        endpoint_indices = {
            endpoint: self.offset_to_index.get(endpoint)
            for endpoint in plan.endpoints
        }
        if any(index is None for index in endpoint_indices.values()):
            return None
        if any(
            endpoint not in self.cfg.offset_to_block
            or self.cfg.block_at(endpoint).start != endpoint
            for endpoint in plan.endpoints
        ):
            return None
        body_endpoints = [
            endpoint
            for endpoint, index in endpoint_indices.items()
            if index < cluster_start
        ]
        epilogue_endpoints = [
            endpoint
            for endpoint, index in endpoint_indices.items()
            if index >= cluster_start
        ]
        if len(body_endpoints) != 1 or len(epilogue_endpoints) != 1:
            return None
        body_endpoint = body_endpoints[0]
        body_start = endpoint_indices[body_endpoint]
        if body_start is None or not body_start < cluster_start:
            return None
        if not any(
            self.tokens[index].kind not in _IGNORED_INTERNAL
            for index in range(body_start, cluster_start)
        ):
            return None

        test = (
            plan.expression
            if body_endpoint == plan.true_endpoint
            else _negate(plan.expression)
        )
        return _ImplicitReturnEpiloguePlan(
            test=test,
            body_start=body_start,
            region_end=region_end,
            condition_blocks=condition_blocks,
            exit_blocks=frozenset(exit_blocks),
            owned_offsets=frozenset(owned_offsets),
        )

    def _terminal_if_plan(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
        region_end: int,
    ) -> Optional[_TerminalIfPlan]:
        """Match a CPython 3.11 conditional whose arms return independently."""
        if (
            loop is not None
            or self.is_class_body
            or getattr(self.code, "co_name", "<module>") == "<module>"
        ):
            return None

        true_index = self.offset_to_index.get(plan.true_endpoint)
        false_index = self.offset_to_index.get(plan.false_endpoint)
        if (
            true_index is None
            or false_index is None
            or true_index == false_index
        ):
            return None
        first = min(true_index, false_index)
        second = max(true_index, false_index)
        if not first < second < region_end <= len(self.tokens):
            return None

        condition_blocks = frozenset(
            self.cfg.offset_to_block[self.tokens[node.jump_index].offset]
            for node in plan.nodes.values()
            if self.tokens[node.jump_index].offset in self.cfg.offset_to_block
        )
        if len(condition_blocks) != len(
            {
                self.cfg.offset_to_block.get(
                    self.tokens[node.jump_index].offset
                )
                for node in plan.nodes.values()
            }
        ) or not condition_blocks:
            return None

        first_exits = self._terminal_interval_exit_kinds(
            first,
            second,
            condition_blocks,
        )
        second_exits = self._terminal_interval_exit_kinds(
            second,
            region_end,
            condition_blocks,
        )
        if first_exits is None or second_exits is None:
            return None

        if true_index < false_index:
            test = plan.expression
        else:
            test = _negate(plan.expression)
        return _TerminalIfPlan(
            test=test,
            body_start=first,
            body_end=second,
            orelse_start=second,
            orelse_end=region_end,
            body_exit_kinds=first_exits,
            orelse_exit_kinds=second_exits,
            body_is_implicit_return_only=self._is_implicit_none_return_only(
                first,
                second,
            ),
            orelse_is_implicit_return_only=self._is_implicit_none_return_only(
                second,
                region_end,
            ),
        )

    @staticmethod
    def _ends_in_control_transfer(body: List[ast.stmt]) -> bool:
        return bool(body) and isinstance(
            body[-1],
            (ast.Break, ast.Continue, ast.Raise, ast.Return),
        )

    def _preserve_terminal_none_return(
        self,
        body: List[ast.stmt],
        start: int,
        end: int,
    ) -> List[ast.stmt]:
        if (
            self.is_class_body
            or getattr(self.code, "co_name", "<module>") == "<module>"
        ):
            return body
        semantic = [
            token
            for token in self.tokens[start:end]
            if (
                token.kind not in _IGNORED_INTERNAL
                and token.offset
                not in self._suppressed_implicit_epilogue_offsets
            )
        ]
        if (
            len(semantic) >= 2
            and semantic[-2].kind == "LOAD_CONST"
            and semantic[-2].attr is None
            and semantic[-1].kind == "RETURN_VALUE"
            and not self._ends_in_control_transfer(body)
        ):
            body.append(ast.Return(value=None))
        return body

    def _emit_implicit_return_epilogue(
        self,
        plan: _ImplicitReturnEpiloguePlan,
        loop: Optional[_LoopContext],
    ) -> int:
        newly_suppressed = set(plan.owned_offsets).difference(
            self._suppressed_implicit_epilogue_offsets
        )
        self._suppressed_implicit_epilogue_offsets.update(newly_suppressed)
        try:
            body = self._capture_region(
                plan.body_start,
                plan.region_end,
                loop,
            )
        finally:
            self._suppressed_implicit_epilogue_offsets.difference_update(
                newly_suppressed
            )
        self.body.append(
            ast.If(
                test=plan.test,
                body=body or [ast.Pass()],
                orelse=[],
            )
        )
        return plan.region_end

    def _emit_terminal_if(
        self,
        plan: _TerminalIfPlan,
        loop: Optional[_LoopContext],
    ) -> int:
        if plan.body_is_implicit_return_only:
            self.body.append(
                ast.If(
                    test=plan.test,
                    body=[ast.Return(value=None)],
                    orelse=[],
                )
            )
            return plan.orelse_start

        body = self._capture_region(
            plan.body_start,
            plan.body_end,
            loop,
        )
        if len(body) == 1 and isinstance(body[0], ast.Return):
            self.body.append(
                ast.If(
                    test=plan.test,
                    body=body,
                    orelse=[],
                )
            )
            return plan.orelse_start
        if plan.orelse_is_implicit_return_only:
            self.body.append(
                ast.If(
                    test=plan.test,
                    body=body or [ast.Pass()],
                    orelse=[],
                )
            )
            return plan.orelse_end

        orelse = self._capture_region(
            plan.orelse_start,
            plan.orelse_end,
            loop,
        )
        self.body.append(
            ast.If(
                test=plan.test,
                body=body or [ast.Pass()],
                orelse=orelse or [ast.Pass()],
            )
        )
        return plan.orelse_end

    def _try_if_expression(
        self,
        plan: _ConditionPlan,
    ) -> Optional[int]:
        true_index = self.offset_to_index[plan.true_endpoint]
        false_index = self.offset_to_index[plan.false_endpoint]
        jump_index = self._last_forward_jump(
            true_index,
            false_index,
            plan.false_endpoint + 1,
        )
        if jump_index is None:
            return None
        join_offset = instruction_target(self.tokens[jump_index])
        join_index = self.offset_to_index[join_offset]
        entry_index = self.offset_to_index[plan.entry_offset]
        try:
            expression = self._expression_slice(
                entry_index,
                join_index,
            )
        except Python311ParseError:
            expression = None
        if expression is not None:
            self.stack.append(expression)
            return join_index
        try:
            body = self._expression_slice(true_index, jump_index)
            orelse = self._expression_slice(false_index, join_index)
        except Python311ParseError:
            return None
        self.stack.append(
            ast.IfExp(test=plan.expression, body=body, orelse=orelse)
        )
        return join_index

    def _try_inline_if_expression(
        self,
        start: int,
        end: int,
    ) -> Optional[int]:
        """Recover a conditional value embedded in a larger expression."""
        jump_index = self._condition_jump(start, end)
        if jump_index is None:
            return None
        target = instruction_target(self.tokens[jump_index])
        target_index = self.offset_to_index.get(target)
        if target_index is None or not jump_index < target_index < end:
            return None
        join_jump = self._last_forward_jump(
            jump_index + 1,
            target_index,
            target + 1,
        )
        if join_jump is None:
            return None
        join = instruction_target(self.tokens[join_jump])
        join_index = self.offset_to_index.get(join)
        if join_index is None or not target_index < join_index <= end:
            return None
        try:
            test = self._predicate(start, jump_index)
            fallthrough = self._expression_slice(
                jump_index + 1,
                join_jump,
            )
            targeted = self._expression_slice(
                target_index,
                join_index,
            )
        except Python311ParseError:
            return None
        if "IF_TRUE" in self.tokens[jump_index].kind:
            body, orelse = targeted, fallthrough
        else:
            body, orelse = fallthrough, targeted
        self.stack.append(ast.IfExp(test=test, body=body, orelse=orelse))
        return join_index

    def _try_assert_statement(self, start: int, end: int) -> Optional[int]:
        failure_index = next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].kind == "LOAD_ASSERTION_ERROR"
            ),
            None,
        )
        if failure_index is None:
            return None

        raise_index = next(
            (
                index
                for index in range(failure_index + 1, end)
                if self.tokens[index].kind == "RAISE_VARARGS"
            ),
            None,
        )
        if (
            raise_index is None
            or self.tokens[raise_index].attr != 1
        ):
            return None

        failure_offset = self.tokens[failure_index].offset
        success_offsets = set()
        active_offsets = set()
        saw_failure = [False]

        def build(offset: int) -> ast.expr:
            if offset == failure_offset:
                saw_failure[0] = True
                return ast.Constant(value=False)
            index = self.offset_to_index.get(offset)
            if index is None:
                raise ValueError("assert branch has no instruction target")
            if index > raise_index:
                success_offsets.add(offset)
                return ast.Constant(value=True)
            if index >= failure_index or offset in active_offsets:
                raise ValueError("assert condition has an unsafe branch")

            jump_index = self._condition_jump(index)
            if jump_index is None or jump_index >= failure_index:
                raise ValueError("assert condition has no decision jump")
            active_offsets.add(offset)
            try:
                predicate = self._predicate(index, jump_index)
                true_offset, false_offset = self._jump_outcomes(jump_index)
                return _combine_decision(
                    predicate,
                    build(true_offset),
                    build(false_offset),
                )
            finally:
                active_offsets.remove(offset)

        try:
            test = build(self.tokens[start].offset)
        except (Python311ParseError, ValueError):
            return None
        unconditional = failure_index == start
        if not saw_failure[0] or (not success_offsets and not unconditional):
            return None

        message = None
        message_start = failure_index + 1
        if message_start < raise_index:
            if (
                raise_index - message_start < 2
                or self.tokens[raise_index - 2].kind != "PRECALL"
                or self.tokens[raise_index - 1].kind != "CALL"
            ):
                return None
            message = self._expression_slice(
                message_start,
                raise_index - 2,
            )

        self.body.append(ast.Assert(test=test, msg=message))
        if unconditional:
            return raise_index + 1
        return min(
            self.offset_to_index[offset] for offset in success_offsets
        )

    def _if_statement(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
        region_end: int,
    ) -> int:
        true_index = self.offset_to_index[plan.true_endpoint]
        false_index = self.offset_to_index[plan.false_endpoint]
        if true_index > false_index:
            terminal = self._terminal_if_plan(plan, loop, region_end)
            if terminal is not None:
                return self._emit_terminal_if(terminal, loop)
            epilogue = self._implicit_return_epilogue_plan(
                plan,
                loop,
                region_end,
            )
            if epilogue is not None:
                return self._emit_implicit_return_epilogue(epilogue, loop)
            body = self._capture_region(false_index, true_index, loop)
            body = self._preserve_terminal_none_return(
                body,
                false_index,
                true_index,
            )
            self.body.append(
                ast.If(
                    test=_negate(plan.expression),
                    body=body or [ast.Pass()],
                    orelse=[],
                )
            )
            return true_index
        excluded = loop.break_target if loop is not None else None
        jump_index = self._last_forward_jump(
            true_index,
            false_index,
            plan.false_endpoint + 1,
            excluded_target=excluded,
        )
        if jump_index is None:
            terminal = self._terminal_if_plan(plan, loop, region_end)
            if terminal is not None:
                return self._emit_terminal_if(terminal, loop)
            epilogue = self._implicit_return_epilogue_plan(
                plan,
                loop,
                region_end,
            )
            if epilogue is not None:
                return self._emit_implicit_return_epilogue(epilogue, loop)
            body = self._capture_region(true_index, false_index, loop)
            body = self._preserve_terminal_none_return(
                body,
                true_index,
                false_index,
            )
            self.body.append(
                ast.If(test=plan.expression, body=body or [ast.Pass()], orelse=[])
            )
            return false_index

        join_offset = instruction_target(self.tokens[jump_index])
        join_index = self.offset_to_index[join_offset]
        body = self._capture_region(true_index, jump_index, loop)
        orelse = self._capture_region(false_index, join_index, loop)
        self.body.append(
            ast.If(
                test=plan.expression,
                body=body or [ast.Pass()],
                orelse=orelse,
            )
        )
        return join_index

    def _try_held_return_finally_condition(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
        end: int,
    ) -> Optional[int]:
        """Recover a branched finally suite that returns one held value."""
        if len(self.stack) != 1 or not isinstance(self.stack[-1], ast.expr):
            return None
        first = self.offset_to_index[plan.true_endpoint]
        second = self.offset_to_index[plan.false_endpoint]
        if first > second:
            first, second = second, first
        first_return = next(
            (
                index
                for index in range(first, second)
                if self.tokens[index].kind == "RETURN_VALUE"
            ),
            None,
        )
        second_return = next(
            (
                index
                for index in range(second, end)
                if self.tokens[index].kind == "RETURN_VALUE"
            ),
            None,
        )
        if first_return is None or second_return is None:
            return None
        if any(
            self.tokens[index].kind in _CONDITIONAL_JUMPS
            for index in range(first, first_return)
        ) or any(
            self.tokens[index].kind in _CONDITIONAL_JUMPS
            for index in range(second, second_return)
        ):
            return None
        try:
            first_body = self._capture_region(first, first_return, loop)
            second_body = self._capture_region(second, second_return, loop)
        except Python311ParseError:
            return None
        test = plan.expression
        if self.offset_to_index[plan.true_endpoint] == first:
            body, orelse = first_body, second_body
        else:
            body, orelse = second_body, first_body
        finalbody = [
            ast.If(
                test=test,
                body=body or [ast.Pass()],
                orelse=orelse,
            )
        ]
        self.body.append(
            ast.Try(
                body=[ast.Return(value=self.stack.pop())],
                handlers=[],
                orelse=[],
                finalbody=finalbody,
            )
        )
        return second_return + 1

    def _latch_expression_start(
        self, body_start: int, jump_index: int
    ) -> int:
        key = (body_start, jump_index)
        cached = self._latch_expression_memo.get(key)
        if cached is not None:
            return cached

        header_line = next(
            (
                self.tokens[index].linestart
                for index in range(body_start - 1, -1, -1)
                if self.tokens[index].linestart is not None
            ),
            None,
        )
        saw_body_line = False
        if header_line is not None:
            for index in range(body_start, jump_index):
                line = self.tokens[index].linestart
                if line is None:
                    continue
                if line > header_line:
                    saw_body_line = True
                elif saw_body_line and line <= header_line:
                    self._latch_expression_memo[key] = index
                    return index

        candidate_start = body_start
        saw_statement_boundary = False
        for index in range(body_start, jump_index):
            kind = self.tokens[index].kind
            target = instruction_target(self.tokens[index])
            is_condition_store = (
                kind
                in (
                    "STORE_DEREF",
                    "STORE_FAST",
                    "STORE_GLOBAL",
                    "STORE_NAME",
                )
                and index > body_start
                and self.tokens[index - 1].kind == "COPY_STACK"
                and self.tokens[index - 1].attr == 1
            )
            is_body_control_transfer = (
                kind in UNCONDITIONAL_JUMPS
                and target is not None
                and (
                    target <= self.tokens[body_start].offset
                    or target > self.tokens[jump_index].offset
                )
            )
            if (
                kind in _STATEMENT_BOUNDARIES and not is_condition_store
            ) or is_body_control_transfer:
                candidate_start = index + 1
                saw_statement_boundary = True

        if saw_statement_boundary and candidate_start < jump_index:
            self._latch_expression_memo[key] = candidate_start
            return candidate_start

        for candidate in range(candidate_start, jump_index):
            try:
                self._expression_slice(candidate, jump_index)
            except Python311ParseError:
                continue
            self._latch_expression_memo[key] = candidate
            return candidate
        # Chained comparisons use an intermediate POP_JUMP to discard their
        # duplicated middle operand, so the latch is not a single linear
        # expression slice.  CPython marks the first instruction of the
        # repeated source condition with its line number; use that boundary
        # only when the candidate-to-latch range contains the chained jump.
        line_start = next(
            (
                candidate
                for candidate in range(jump_index - 1, body_start - 1, -1)
                if self.tokens[candidate].linestart is not None
            ),
            None,
        )
        if (
            line_start is not None
            and any(
                self.tokens[index].kind in _CONDITIONAL_JUMPS
                for index in range(line_start, jump_index)
            )
        ):
            self._latch_expression_memo[key] = line_start
            return line_start
        self._latch_expression_memo[key] = jump_index
        return jump_index

    def _while_loop(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
        region_end: int,
    ) -> Optional[int]:
        body_start = self.offset_to_index[plan.true_endpoint]
        false_index = self.offset_to_index[plan.false_endpoint]
        back_jumps = []
        for index in range(body_start, min(false_index, region_end)):
            token = self.tokens[index]
            target = instruction_target(token)
            if (
                target is not None
                and "BACKWARD" in token.kind
                and target
                in (
                    plan.entry_offset,
                    plan.true_endpoint,
                )
            ):
                back_jumps.append(index)
        if not back_jumps:
            return None

        latch_jump = back_jumps[-1]
        if self.tokens[latch_jump].kind.startswith("POP_JUMP_"):
            body_end = self._latch_expression_start(body_start, latch_jump)
        else:
            body_end = latch_jump

        break_targets = [
            instruction_target(self.tokens[index])
            for index in range(body_start, body_end)
            if self.tokens[index].kind == "JUMP_FORWARD"
            and instruction_target(self.tokens[index]) >= plan.false_endpoint
        ]
        loop_end = (
            max(break_targets)
            if break_targets
            else plan.false_endpoint
        )
        loop_end_index = self.offset_to_index[loop_end]
        continue_targets = {
            plan.entry_offset,
            plan.true_endpoint,
            self.tokens[body_end].offset,
        }
        body = self._capture_region(
            body_start,
            body_end,
            _LoopContext(
                break_target=loop_end,
                continue_targets=frozenset(continue_targets),
            ),
        )
        orelse = (
            self._capture_region(false_index, loop_end_index, loop)
            if loop_end > plan.false_endpoint
            else []
        )
        self.body.append(
            ast.While(
                test=plan.expression,
                body=body or [ast.Pass()],
                orelse=orelse,
            )
        )
        return loop_end_index

    def _while_true_loop(
        self,
        start: int,
        end: int,
        loop: Optional[_LoopContext],
    ) -> Optional[int]:
        start_offset = self.tokens[start].offset
        if start_offset in self._suppressed_loop_starts:
            return None
        header_offsets = {start_offset}
        header_cursor = start + 1
        while (
            self.tokens[start].kind
            in ("INTERNAL_EXTENDED_ARG", "INTERNAL_RESUME", "NOP")
            and header_cursor < end
            and self.tokens[header_cursor].kind
            in ("INTERNAL_EXTENDED_ARG", "INTERNAL_RESUME", "NOP")
        ):
            header_offsets.add(self.tokens[header_cursor].offset)
            header_cursor += 1
        if (
            self.tokens[start].kind
            in ("INTERNAL_EXTENDED_ARG", "INTERNAL_RESUME", "NOP")
            and header_cursor < end
        ):
            # A 3.11 ``while True`` normally has a source-line NOP before its
            # first body instruction, while the loop latch targets that first
            # semantic instruction rather than the NOP itself.
            header_offsets.add(self.tokens[header_cursor].offset)
        protected_headers = [
            region.start
            for region in self.exception_regions
            if start_offset < region.start and region.start in header_offsets
        ]
        if protected_headers:
            protected_start = min(protected_headers)
            header_offsets = {
                offset for offset in header_offsets if offset < protected_start
            }
        latches = [
            index
            for index in range(start + 1, end)
            if self.tokens[index].kind == "JUMP_BACKWARD"
            and instruction_target(self.tokens[index]) in header_offsets
        ]
        if not latches:
            return None
        latch = latches[-1]
        break_targets = [
            instruction_target(self.tokens[index])
            for index in range(start, latch)
            if self.tokens[index].kind == "JUMP_FORWARD"
            and instruction_target(self.tokens[index])
            > self.tokens[latch].offset
        ]
        if break_targets:
            loop_end = max(break_targets)
            loop_end_index = self.offset_to_index.get(loop_end)
            if loop_end_index is None or loop_end_index <= latch:
                return None
        elif latch + 1 == end:
            loop_end_index = end
            loop_end = self.tokens[latch].offset + 2
        else:
            return None

        added_loop_starts = header_offsets - self._suppressed_loop_starts
        self._suppressed_loop_starts.update(added_loop_starts)
        try:
            body = self._capture_region(
                start,
                latch,
                _LoopContext(
                    break_target=loop_end,
                    continue_targets=frozenset(
                        header_offsets | {self.tokens[latch].offset}
                    ),
                ),
            )
        finally:
            self._suppressed_loop_starts.difference_update(
                added_loop_starts
            )
        self.body.append(
            ast.While(
                test=ast.Constant(value=True),
                body=body or [ast.Pass()],
                orelse=[],
            )
        )
        return loop_end_index

    def _for_target(
        self,
        start: int,
    ) -> Tuple[Optional[ast.expr], int]:
        while (
            start < len(self.tokens)
            and self.tokens[start].kind in _IGNORED_INTERNAL
        ):
            start += 1
        if start >= len(self.tokens):
            self._error("FOR_ITER has no store target")
        token = self.tokens[start]
        if token.kind == "POP_TOP":
            return None, start + 1
        return self._for_target_element(start)

    def _for_target_element(self, start: int) -> Tuple[ast.expr, int]:
        while (
            start < len(self.tokens)
            and self.tokens[start].kind in _IGNORED_INTERNAL
        ):
            start += 1
        if start >= len(self.tokens):
            self._error("Unpacking loop target ended before all stores")
        token = self.tokens[start]
        if token.kind.startswith("STORE_"):
            name = token.attr if isinstance(token.attr, str) else token.pattr
            return ast.Name(id=name, ctx=ast.Store()), start + 1
        if token.kind not in ("UNPACK_SEQUENCE", "UNPACK_EX"):
            self.current_token = token
            self._error("Unpacking loop target contains a non-store opcode")

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
            target, cursor = self._for_target_element(cursor)
            if after >= 0 and target_index == before:
                target = ast.Starred(value=target, ctx=ast.Store())
            targets.append(target)
        return ast.Tuple(elts=targets, ctx=ast.Store()), cursor

    def _next_semantic_index(
        self,
        start: int,
        end: Optional[int] = None,
    ) -> int:
        limit = len(self.tokens) if end is None else end
        while (
            start < limit
            and self.tokens[start].kind in _IGNORED_INTERNAL
        ):
            start += 1
        return start

    def _semantic_target_offset(self, token) -> Optional[int]:
        target = instruction_target(token)
        if target is None:
            return None
        target_index = self.offset_to_index.get(target)
        if target_index is None:
            return target
        semantic_index = self._next_semantic_index(target_index)
        if semantic_index >= len(self.tokens):
            return target
        return self.tokens[semantic_index].offset

    def _for_loop(
        self,
        get_iter_index: int,
        loop: Optional[_LoopContext],
    ) -> int:
        for_iter_index = self._next_semantic_index(get_iter_index + 1)
        if (
            for_iter_index >= len(self.tokens)
            or self.tokens[for_iter_index].kind != "FOR_ITER"
        ):
            self.current_token = self.tokens[get_iter_index]
            self._error("GET_ITER is not followed by FOR_ITER")
        iterable = self._pop_expr()
        else_offset = instruction_target(self.tokens[for_iter_index])
        else_index = self.offset_to_index[else_offset]
        target, body_start = self._for_target(for_iter_index + 1)
        if target is None:
            self._error("FOR_ITER has no assignment target")
        latch_candidates = [
            index
            for index in range(body_start, else_index)
            if self.tokens[index].kind.startswith("JUMP_BACKWARD")
            and self._semantic_target_offset(self.tokens[index])
            == self.tokens[for_iter_index].offset
        ]
        latch = latch_candidates[-1] if latch_candidates else None
        body_limit = latch if latch is not None else else_index
        break_targets = [
            instruction_target(self.tokens[index])
            for index in range(body_start, body_limit)
            if self.tokens[index].kind == "JUMP_FORWARD"
            and instruction_target(self.tokens[index]) >= else_offset
        ]
        if latch is None and not break_targets:
            terminators = {
                "RAISE_VARARGS",
                "RERAISE",
                "RETURN_VALUE",
            }
            if not any(
                self.tokens[index].kind in terminators
                for index in range(body_start, else_index)
            ):
                self.current_token = self.tokens[for_iter_index]
                self._error("FOR_ITER has neither a loop-back nor break edge")
        loop_end = max(break_targets) if break_targets else else_offset
        loop_end_index = self.offset_to_index[loop_end]
        continue_targets = {
            self.tokens[for_iter_index].offset,
            self.tokens[get_iter_index + 1].offset,
        }
        if latch is not None:
            continue_targets.add(self.tokens[latch].offset)
        body = self._capture_region(
            body_start,
            body_limit,
            _LoopContext(
                break_target=loop_end,
                continue_targets=frozenset(continue_targets),
            ),
        )
        orelse = (
            self._capture_region(else_index, loop_end_index, loop)
            if loop_end > else_offset
            else []
        )
        self.body.append(
            ast.For(
                target=target,
                iter=iterable,
                body=body or [ast.Pass()],
                orelse=orelse,
                type_comment=None,
            )
        )
        return loop_end_index

    def _async_for_loop(
        self,
        get_aiter_index: int,
        loop: Optional[_LoopContext],
    ) -> int:
        iterable = self._pop_expr()
        header = get_aiter_index + 1
        if (
            header >= len(self.tokens)
            or self.tokens[header].kind != "GET_ANEXT"
        ):
            self.current_token = self.tokens[get_aiter_index]
            self._error("GET_AITER is not followed by GET_ANEXT")
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
        target, body_start = self._for_target(body_start)
        if target is None:
            self._error("Async for has no assignment target")
        protected = next(
            (
                entry
                for entry in self.exception_regions
                if entry.start == self.tokens[header].offset
                and self.tokens[self.offset_to_index[entry.target]].kind
                == "END_ASYNC_FOR"
            ),
            None,
        )
        if protected is None:
            self._error("Async for has no END_ASYNC_FOR exception region")
        end_async = self.offset_to_index[protected.target]
        latch_candidates = [
            index
            for index in range(body_start, end_async)
            if self.tokens[index].kind.startswith("JUMP_BACKWARD")
            and instruction_target(self.tokens[index])
            == self.tokens[header].offset
        ]
        if not latch_candidates:
            self._error("Async for has no loop-back edge")
        latch = latch_candidates[-1]
        break_targets = [
            instruction_target(self.tokens[index])
            for index in range(body_start, latch)
            if self.tokens[index].kind == "JUMP_FORWARD"
            and instruction_target(self.tokens[index])
            > self.tokens[end_async].offset
        ]
        loop_end_offset = (
            max(break_targets)
            if break_targets
            else (
                self.tokens[end_async + 1].offset
                if end_async + 1 < len(self.tokens)
                else self.tokens[end_async].offset + 2
            )
        )
        loop_end_index = (
            self.offset_to_index[loop_end_offset]
            if loop_end_offset in self.offset_to_index
            else len(self.tokens)
        )
        body = self._capture_region(
            body_start,
            latch,
            _LoopContext(
                break_target=loop_end_offset,
                continue_targets=frozenset(
                    {
                        self.tokens[header].offset,
                        self.tokens[latch].offset,
                    }
                ),
            ),
        )
        else_start = end_async + 1
        orelse = (
            self._capture_region(else_start, loop_end_index, loop)
            if break_targets and else_start < loop_end_index
            else []
        )
        self.body.append(
            ast.AsyncFor(
                target=target,
                iter=iterable,
                body=body or [ast.Pass()],
                orelse=orelse,
                type_comment=None,
            )
        )
        return loop_end_index

    def _try_chained_compare(self, index: int) -> Optional[int]:
        if (
            self.tokens[index].kind != "SWAP_STACK"
            or self.tokens[index].attr != 2
            or index + 3 >= len(self.tokens)
            or self.tokens[index + 1].kind != "COPY_STACK"
            or self.tokens[index + 1].attr != 2
            or self.tokens[index + 2].kind not in _COMPARE_OPERATORS
            or self.tokens[index + 3].kind != "JUMP_IF_FALSE_OR_POP"
            or len(self.stack) < 2
        ):
            return None

        left = self.stack[-2]
        first_comparator = self.stack[-1]
        if not isinstance(left, ast.expr) or not isinstance(
            first_comparator, ast.expr
        ):
            return None
        del self.stack[-2:]
        operators = [_COMPARE_OPERATORS[self.tokens[index + 2].kind]()]
        comparators = [first_comparator]
        cleanup_offset = instruction_target(self.tokens[index + 3])
        cursor = index + 4

        while cursor < len(self.tokens):
            marker = cursor
            while marker < len(self.tokens):
                kind = self.tokens[marker].kind
                if kind in _COMPARE_OPERATORS or kind == "SWAP_STACK":
                    break
                marker += 1
            if marker >= len(self.tokens):
                return None
            comparator = self._expression_slice(cursor, marker)

            if self.tokens[marker].kind == "SWAP_STACK":
                if (
                    marker + 3 >= len(self.tokens)
                    or self.tokens[marker + 1].kind != "COPY_STACK"
                    or self.tokens[marker + 2].kind not in _COMPARE_OPERATORS
                    or self.tokens[marker + 3].kind != "JUMP_IF_FALSE_OR_POP"
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

            compare_kind = self.tokens[marker].kind
            if (
                marker + 1 >= len(self.tokens)
                or self.tokens[marker + 1].kind != "JUMP_FORWARD"
            ):
                return None
            operators.append(_COMPARE_OPERATORS[compare_kind]())
            comparators.append(comparator)
            join_offset = instruction_target(self.tokens[marker + 1])
            cleanup_index = self.offset_to_index[cleanup_offset]
            if (
                self.tokens[cleanup_index].kind != "SWAP_STACK"
                or self.tokens[cleanup_index + 1].kind != "POP_TOP"
            ):
                return None
            self.stack.append(
                ast.Compare(
                    left=left,
                    ops=operators,
                    comparators=comparators,
                )
            )
            return self.offset_to_index[join_offset]
        return None

    def _jump_control(
        self,
        index: int,
        end: int,
        loop: Optional[_LoopContext],
    ) -> Optional[int]:
        token = self.tokens[index]
        target = instruction_target(token)
        if loop is not None and token.kind == "JUMP_FORWARD":
            if target >= loop.break_target:
                self.body.append(ast.Break())
                return end
        if loop is not None and "BACKWARD" in token.kind:
            if target in loop.continue_targets:
                self.body.append(ast.Continue())
                return end
        if token.kind in UNCONDITIONAL_JUMPS:
            return end
        return None

    def _for_iterator_return_cleanup(
        self,
        index: int,
        end: int,
        loop: Optional[_LoopContext],
    ) -> Optional[int]:
        """Skip the physical iterator removal before a loop-body return."""
        if (
            loop is None
            or self.tokens[index].kind != "SWAP_STACK"
            or len(self.stack) != 1
            or not isinstance(self.stack[-1], ast.expr)
        ):
            return None

        cursor = index
        while (
            cursor + 1 < end
            and self.tokens[cursor].kind == "SWAP_STACK"
            and self.tokens[cursor].attr == 2
            and self.tokens[cursor + 1].kind == "POP_TOP"
        ):
            cursor += 2
        if cursor == index:
            return None
        if cursor >= end or self.tokens[cursor].kind != "RETURN_VALUE":
            if not self.exception_region_map.covering(
                self.tokens[index].offset
            ):
                return None
            # A return from a loop inside try/finally first removes the
            # physical iterator, then carries the return value across the
            # normal-path finally copy before RETURN_VALUE.  The source-level
            # return belongs at the cleanup boundary; the duplicated finally
            # instructions are structured separately by the exception-table
            # recovery path.  The current region is the returning control-flow
            # branch, so consume its remaining physical cleanup instructions.
            self.body.append(ast.Return(value=self._pop_expr()))
            return end

        # The active FOR_ITER value exists on CPython's physical operand
        # stack, but deliberately has no entry on the source-level AST stack.
        # Each SWAP 2 / POP_TOP pair removes one iterator while preserving the
        # return value.  Consume only a complete run ending at RETURN_VALUE;
        # ordinary SWAP operations must continue through the checked logical
        # stack implementation.
        return cursor

    def _for_iterator_cleanup_before_return(
        self,
        index: int,
        end: int,
        loop: Optional[_LoopContext],
    ) -> Optional[int]:
        """Skip iterators discarded before computing a constant return."""
        if (
            loop is None
            or self.stack
            or self.tokens[index].kind != "POP_TOP"
        ):
            return None

        cursor = index
        while cursor < end:
            if self.tokens[cursor].kind == "POP_TOP":
                cursor += 1
                continue
            if (
                self.tokens[cursor].offset
                in self._suppressed_exception_protocol_offsets
            ):
                cursor += 1
                continue
            break
        return_index = next(
            (
                candidate
                for candidate in range(cursor, end)
                if self.tokens[candidate].kind == "RETURN_VALUE"
            ),
            None,
        )
        if return_index is None or cursor == return_index:
            return None

        from decompyle3.parsers.p311.expressions import recover_expression311

        try:
            recover_expression311(
                self.code,
                self.tokens,
                start=cursor,
                end=return_index + 1,
                terminal_kinds=frozenset({"RETURN_VALUE"}),
            )
        except Python311ParseError:
            return None
        return cursor

    def _try_return_expression(
        self,
        start: int,
        end: int,
    ) -> Optional[int]:
        """Recover one closed conditional expression ending in ``return``."""
        if (
            self.stack
            or self.pending_assignment_value is not None
            or self.pending_assignment_targets
            or self.pending_booleans
            or self.pending_keywords
        ):
            return None

        return_index = next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].kind == "RETURN_VALUE"
            ),
            None,
        )
        if return_index is None:
            return None

        candidate = self.tokens[start : return_index + 1]
        if not any(
            token.kind.startswith(("JUMP_IF_", "POP_JUMP_"))
            for token in candidate
        ):
            return None
        if any("BACKWARD" in token.kind for token in candidate):
            return None

        candidate_offsets = {token.offset for token in candidate}
        if any(
            target is not None and target not in candidate_offsets
            for target in (
                instruction_target(token) for token in candidate[:-1]
            )
        ):
            return None
        if any(
            self.exception_region_map.covering(token.offset)
            for token in candidate
        ):
            return None

        from decompyle3.parsers.p311.expressions import recover_expression311

        try:
            expression = recover_expression311(
                self.code,
                self.tokens,
                start=start,
                end=return_index + 1,
                terminal_kinds=frozenset({"RETURN_VALUE"}),
            )
        except Python311ParseError:
            return None

        self.body.append(ast.Return(value=expression))
        return return_index + 1

    def _try_assignment_expression(
        self,
        start: int,
        end: int,
    ) -> Optional[int]:
        """Recover one closed conditional expression before a name store."""
        if (
            self.stack
            or self.pending_assignment_value is not None
            or self.pending_assignment_targets
            or self.pending_booleans
            or self.pending_keywords
        ):
            return None

        store_index = next(
            (
                index
                for index in range(start, end)
                if self.tokens[index].kind
                in (
                    "STORE_DEREF",
                    "STORE_FAST",
                    "STORE_GLOBAL",
                    "STORE_NAME",
                )
                and not (
                    index > start
                    and self.tokens[index - 1].kind == "COPY_STACK"
                    and self.tokens[index - 1].attr == 1
                )
            ),
            None,
        )
        if store_index is None:
            return None
        candidate = self.tokens[start:store_index]
        if not any(
            token.kind.startswith(("JUMP_IF_", "POP_JUMP_"))
            for token in candidate
        ):
            return None
        if any(
            token.kind
            in (
                "POP_TOP",
                "RAISE_VARARGS",
                "RETURN_VALUE",
                "STORE_ATTR",
                "STORE_SUBSCR",
            )
            for token in candidate
        ):
            return None

        try:
            expression = self._expression_slice(start, store_index)
        except Python311ParseError:
            return None
        self.stack.append(expression)
        return store_index

    def _parse_region(
        self,
        start: int,
        end: int,
        loop: Optional[_LoopContext],
    ):
        index = start
        store_kinds = {
            "STORE_ATTR",
            "STORE_DEREF",
            "STORE_FAST",
            "STORE_GLOBAL",
            "STORE_NAME",
            "STORE_SUBSCR",
        }
        while index < end:
            token = self.tokens[index]
            self.current_token = token

            if (
                token.offset
                in self._suppressed_exception_protocol_offsets
                or token.offset
                in self._suppressed_implicit_epilogue_offsets
            ):
                index += 1
                continue

            self._resolve_booleans(token.offset)
            if token.kind not in store_kinds:
                self._flush_assignment()

            if (
                self.exception_regions
                and token.offset not in self._suppressed_exception_starts
            ):
                from decompyle3.controlflow.exception_structures import (
                    recover_try_statement311,
                    recover_with_statement311,
                )

                try_index = index
                if (
                    token.kind == "NOP"
                    and index + 1 < end
                    and any(
                        region.start == self.tokens[index + 1].offset
                        for region in self.exception_regions
                    )
                    and self.tokens[index + 1].offset
                    not in self._suppressed_exception_starts
                ):
                    try_index += 1
                try_end = recover_try_statement311(
                    self,
                    try_index,
                    loop,
                )
                if try_end is not None:
                    index = try_end
                    continue
                if token.kind in ("BEFORE_WITH", "BEFORE_ASYNC_WITH"):
                    index = recover_with_statement311(
                        self,
                        index,
                        end,
                        loop,
                    )
                    continue

            while_true_end = self._while_true_loop(index, end, loop)
            if while_true_end is not None:
                index = while_true_end
                continue

            if token.kind in (
                "COPY_FREE_VARS",
                "INTERNAL_EXTENDED_ARG",
                "INTERNAL_RESUME",
                "MAKE_CELL",
            ):
                self._dispatch(token)
                index += 1
                continue

            if token.linestart is not None:
                from decompyle3.controlflow.match_structures import (
                    recover_match_statement311,
                )

                match_end = recover_match_statement311(
                    self,
                    index,
                    end,
                    loop,
                )
                if match_end is not None:
                    index = match_end
                    continue

            chained_end = self._try_chained_compare(index)
            if chained_end is not None:
                index = chained_end
                continue

            if (
                token.kind == "GET_ITER"
                and self._next_semantic_index(index + 1, end) < end
                and self.tokens[
                    self._next_semantic_index(index + 1, end)
                ].kind
                == "FOR_ITER"
            ):
                index = self._for_loop(index, loop)
                continue

            if (
                token.kind == "GET_AITER"
                and index + 1 < end
                and self.tokens[index + 1].kind == "GET_ANEXT"
            ):
                index = self._async_for_loop(index, loop)
                continue

            if token.kind == "GET_AWAITABLE":
                index = self._await_protocol(index)
                continue

            if token.kind == "GET_YIELD_FROM_ITER":
                index = self._yield_from_protocol(index, end)
                continue

            if token.kind == "ASYNC_GEN_WRAP":
                index += 1
                continue

            if token.kind == "YIELD_VALUE":
                index = self._yield_value(index, end)
                continue

            assert_end = self._try_assert_statement(index, end)
            if assert_end is not None:
                index = assert_end
                continue

            expression_end = self._try_assignment_expression(index, end)
            if expression_end is not None:
                index = expression_end
                continue

            return_end = self._try_return_expression(index, end)
            if return_end is not None:
                index = return_end
                continue

            inline_expression_end = self._try_inline_if_expression(
                index,
                end,
            )
            if inline_expression_end is not None:
                index = inline_expression_end
                continue

            condition = self._bounded_condition_plan(
                index,
                end,
                (
                    loop.continue_targets
                    if loop is not None
                    else frozenset()
                ),
            )
            if condition is not None:
                loop_end = self._while_loop(condition, loop, end)
                if loop_end is not None:
                    index = loop_end
                    continue
                held_return_end = self._try_held_return_finally_condition(
                    condition,
                    loop,
                    end,
                )
                if held_return_end is not None:
                    index = held_return_end
                    continue
                return_end = self._try_return_expression(index, end)
                if return_end is not None:
                    index = return_end
                    continue
                expression_end = self._try_if_expression(condition)
                if expression_end is not None:
                    index = expression_end
                    continue
                index = self._if_statement(condition, loop, end)
                continue

            if token.kind in UNCONDITIONAL_JUMPS:
                controlled = self._jump_control(index, end, loop)
                if controlled is not None:
                    index = controlled
                    continue

            cleanup_end = self._for_iterator_return_cleanup(
                index,
                end,
                loop,
            )
            if cleanup_end is not None:
                index = cleanup_end
                continue

            cleanup_end = self._for_iterator_cleanup_before_return(
                index,
                end,
                loop,
            )
            if cleanup_end is not None:
                index = cleanup_end
                continue

            if (
                token.kind == "POP_TOP"
                and loop is not None
                and not self.stack
            ):
                next_index = self._next_semantic_index(index + 1, end)
                if (
                    next_index < end
                    and self.tokens[next_index].kind == "JUMP_FORWARD"
                    and instruction_target(self.tokens[next_index])
                    >= loop.break_target
                ):
                    index += 1
                    continue
                if (
                    next_index >= end
                    and end < len(self.tokens)
                    and self.tokens[end].offset == loop.break_target
                ):
                    index += 1
                    continue
                if (
                    next_index == end
                    and end < len(self.tokens)
                    and self.tokens[end].kind == "JUMP_FORWARD"
                    and instruction_target(self.tokens[end])
                    == loop.break_target
                ):
                    index += 1
                    continue

            self._dispatch(token)
            index += 1

    def decompile_body(self) -> List[ast.stmt]:
        self._validate_scope()
        if self.compile_mode == "single" and any(
            token.kind == "PRINT_EXPR" for token in self.tokens
        ):
            from decompyle3.parsers.p311.expressions import recover_expression311

            expression = recover_expression311(
                self.code,
                self.tokens,
                terminal_kinds=frozenset({"PRINT_EXPR"}),
            )
            return [ast.Expr(value=expression)]
        start = 0
        while (
            start < len(self.tokens)
            and self.tokens[start].kind
            in (
                "COPY_FREE_VARS",
                "INTERNAL_EXTENDED_ARG",
                "MAKE_CELL",
            )
        ):
            start += 1
        if (
            start < len(self.tokens)
            and self.tokens[start].kind == "RETURN_GENERATOR"
        ):
            start += 1
            if (
                start < len(self.tokens)
                and self.tokens[start].kind == "POP_TOP"
            ):
                start += 1
        self.body = self._capture_region(start, len(self.tokens), None)
        if getattr(self.code, "co_name", "<module>") != "<module>" and not self.is_class_body:
            self._inject_function_docstring()
        self._prepend_scope_declarations()
        return self.body

    def decompile_expression(self) -> ast.expr:
        if int(getattr(self.code, "co_flags", 0)) & CO_GENERATOR:
            return super(StructuredDecompiler311, self).decompile_expression()

        from decompyle3.parsers.p311.expressions import recover_expression311

        return recover_expression311(
            self.code,
            self.tokens,
            terminal_kinds=frozenset({"RETURN_VALUE"}),
        )
