"""Direct CFG-to-AST structuring for CPython 3.11.

Parser311 deliberately uses one control-flow recovery path: normalized
instructions are analyzed as a CFG and emitted directly as standard-library
``ast`` nodes. We do not synthesize legacy ``COME_FROM`` tokens.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from decompyle3.controlflow.cfg import (
    UNCONDITIONAL_JUMPS,
    build_cfg,
    instruction_target,
)
from decompyle3.controlflow.dominators import analyze_control_flow
from decompyle3.controlflow.exception_regions import build_exception_region_map
from decompyle3.controlflow.exceptiontable311 import decode_exception_table
from decompyle3.parsers.p311.base import (
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

_LATER_PHASE_OPS = {
    "MAP_ADD",
    "SET_ADD",
}


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
class _LoopContext:
    break_target: int
    continue_targets: frozenset


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
        return ast.BoolOp(op=ast.And(), values=[predicate, when_true])
    if true_constant is False:
        return ast.BoolOp(
            op=ast.And(), values=[_negate(predicate), when_false]
        )
    if true_constant is True:
        return ast.BoolOp(op=ast.Or(), values=[predicate, when_false])
    if false_constant is True:
        return ast.BoolOp(
            op=ast.Or(), values=[_negate(predicate), when_true]
        )
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
        self._suppressed_loop_starts = set()
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

    def _condition_jump(self, start: int) -> Optional[int]:
        for index in range(start, len(self.tokens)):
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

    def _terminal_return_signature(self, offset: int) -> Optional[str]:
        """Describe a side-effect-free terminal return block for merging."""
        start = self.offset_to_index.get(offset)
        if start is None:
            return None
        if (
            self.tokens[start].kind == "RETURN_VALUE"
            and self.tokens[start].offset
            in self._suppressed_exception_protocol_offsets
        ):
            return "__suppressed_return_value__"
        while (
            start < len(self.tokens)
            and (
                self.tokens[start].kind in _IGNORED_INTERNAL
                or self.tokens[start].offset
                in self._suppressed_exception_protocol_offsets
            )
        ):
            start += 1
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
        left_signature = self._terminal_return_signature(left)
        return (
            left_signature is not None
            and left_signature == self._terminal_return_signature(right)
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
            signature = self._terminal_return_signature(endpoint)
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
        while cursor < cleanup_index:
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

        true_offset, false_offset = self._jump_outcomes(final_jump)
        true_endpoint = self._resolve_condition_endpoint(true_offset)
        false_endpoint = self._resolve_condition_endpoint(false_offset)
        cleanup_endpoint = self._resolve_condition_endpoint(
            self.tokens[cleanup_index + 1].offset
        )
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
        chained = self._chained_condition_plan(start)
        if chained is not None:
            return chained
        jump_index = self._condition_jump(start)
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
                node_jump = self._condition_jump(node_start)
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
                    successor_token = self.tokens[successor_index]
                    block = self.cfg.block_at(successor)
                    has_single_predecessor = (
                        len(self.cfg.predecessors(block.index)) == 1
                    )
                    successor_jump = self._condition_jump(
                        successor_index
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

        collected = collect(allow_multiline=True)
        if collected is None:
            return None
        nodes, endpoints = collected
        nodes, endpoints = self._coalesce_condition_endpoints(
            nodes,
            endpoints,
        )
        if len(endpoints) != 2:
            collected = collect(allow_multiline=False)
            if collected is None:
                return None
            nodes, endpoints = collected
            nodes, endpoints = self._coalesce_condition_endpoints(
                nodes,
                endpoints,
            )
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
            if len(self.cfg.predecessors(block.index)) != 1:
                return None
            node_jump = self._condition_jump(index)
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

        def build(offset: int) -> ast.expr:
            if offset not in nodes:
                return ast.Constant(value=offset == true_endpoint)
            node = nodes[offset]
            return _combine_decision(
                node.predicate,
                build(node.true_offset),
                build(node.false_offset),
            )

        entry_offset = self.tokens[start].offset
        plan = _ConditionPlan(
            entry_offset=entry_offset,
            nodes=nodes,
            endpoints=tuple(sorted(endpoints)),
            true_endpoint=true_endpoint,
            false_endpoint=false_endpoint,
            expression=build(entry_offset),
        )
        if stacked_predicate is not None:
            self.stack.pop()
        return plan

    def _capture_region(
        self,
        start: int,
        end: int,
        loop: Optional[_LoopContext],
    ) -> List[ast.stmt]:
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
            if self.stack:
                self._error("Structured statement region left stack values")
            return result
        finally:
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
    ) -> int:
        true_index = self.offset_to_index[plan.true_endpoint]
        false_index = self.offset_to_index[plan.false_endpoint]
        if true_index > false_index:
            body = self._capture_region(false_index, true_index, loop)
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
            body = self._capture_region(true_index, false_index, loop)
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

    def _latch_expression_start(
        self, body_start: int, jump_index: int
    ) -> int:
        for candidate in range(body_start, jump_index):
            try:
                self._expression_slice(candidate, jump_index)
            except Python311ParseError:
                continue
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
            return line_start
        return jump_index

    def _while_loop(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
    ) -> Optional[int]:
        body_start = self.offset_to_index[plan.true_endpoint]
        false_index = self.offset_to_index[plan.false_endpoint]
        back_jumps = []
        for index in range(body_start, false_index):
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
        latches = [
            index
            for index in range(start + 1, end)
            if self.tokens[index].kind == "JUMP_BACKWARD"
            and instruction_target(self.tokens[index]) == start_offset
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
        if not break_targets:
            return None
        loop_end = max(break_targets)
        loop_end_index = self.offset_to_index.get(loop_end)
        if loop_end_index is None or loop_end_index <= latch:
            return None

        self._suppressed_loop_starts.add(start_offset)
        try:
            body = self._capture_region(
                start,
                latch,
                _LoopContext(
                    break_target=loop_end,
                    continue_targets=frozenset(
                        {start_offset, self.tokens[latch].offset}
                    ),
                ),
            )
        finally:
            self._suppressed_loop_starts.remove(start_offset)
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

    def _for_loop(
        self,
        get_iter_index: int,
        loop: Optional[_LoopContext],
    ) -> int:
        for_iter_index = get_iter_index + 1
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
            and instruction_target(self.tokens[index])
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
            self.current_token = self.tokens[for_iter_index]
            self._error("FOR_ITER has neither a loop-back nor break edge")
        loop_end = max(break_targets) if break_targets else else_offset
        loop_end_index = self.offset_to_index[loop_end]
        continue_targets = {self.tokens[for_iter_index].offset}
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
            if target == loop.break_target:
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
        while cursor < end and self.tokens[cursor].kind == "POP_TOP":
            cursor += 1
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
            self._resolve_booleans(token.offset)
            if token.kind not in store_kinds:
                self._flush_assignment()

            if (
                token.offset
                in self._suppressed_exception_protocol_offsets
            ):
                index += 1
                continue

            while_true_end = self._while_true_loop(index, end, loop)
            if while_true_end is not None:
                index = while_true_end
                continue

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
                and index + 1 < end
                and self.tokens[index + 1].kind == "FOR_ITER"
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

            condition = self._condition_plan(index)
            if condition is not None:
                loop_end = self._while_loop(condition, loop)
                if loop_end is not None:
                    index = loop_end
                    continue
                return_end = self._try_return_expression(index, end)
                if return_end is not None:
                    index = return_end
                    continue
                expression_end = self._try_if_expression(condition)
                if expression_end is not None:
                    index = expression_end
                    continue
                index = self._if_statement(condition, loop)
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
                and index + 1 < end
                and loop is not None
                and not self.stack
                and self.tokens[index + 1].kind == "JUMP_FORWARD"
                and instruction_target(self.tokens[index + 1])
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
        start = (
            2
            if self.tokens and self.tokens[0].kind == "RETURN_GENERATOR"
            else 0
        )
        self.body = self._capture_region(start, len(self.tokens), None)
        if getattr(self.code, "co_name", "<module>") != "<module>" and not self.is_class_body:
            self._inject_function_docstring()
        self._prepend_scope_declarations()
        return self.body

    def decompile_expression(self) -> ast.expr:
        from decompyle3.parsers.p311.expressions import recover_expression311

        return recover_expression311(
            self.code,
            self.tokens,
            terminal_kinds=frozenset({"RETURN_VALUE"}),
        )
