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
    "SETUP_ANNOTATIONS",
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
        self._validate_exception_group_shapes()
        self.cfg = build_cfg(flow_tokens, self.exception_regions)
        self.control_flow = analyze_control_flow(self.cfg)

    def _validate_exception_group_shapes(self):
        """Reject except* layouts whose else/finally semantics are ambiguous."""
        if not any(token.kind == "CHECK_EG_MATCH" for token in self.tokens):
            return

        for entry in self.exception_regions:
            handler_index = self.offset_to_index[entry.target]
            if (
                entry.lasti
                or self.tokens[handler_index].kind != "PUSH_EXC_INFO"
            ):
                continue
            prep_index = next(
                (
                    index
                    for index in range(handler_index, len(self.tokens))
                    if self.tokens[index].kind == "PREP_RERAISE_STAR"
                ),
                None,
            )
            check_index = next(
                (
                    index
                    for index in range(handler_index, prep_index or handler_index)
                    if self.tokens[index].kind == "CHECK_EG_MATCH"
                ),
                None,
            )
            if prep_index is None or check_index is None:
                continue

            continuation_jump = next(
                (
                    token
                    for token in self.tokens[prep_index + 1 :]
                    if token.kind == "JUMP_FORWARD"
                ),
                None,
            )
            continuation = (
                instruction_target(continuation_jump)
                if continuation_jump is not None
                else None
            )
            if entry.end < entry.target:
                gap = self.tokens[
                    self.offset_to_index[entry.end] : handler_index
                ]
                if any(
                    token.kind
                    not in ("INTERNAL_EXTENDED_ARG", "JUMP_FORWARD", "NOP")
                    for token in gap
                ) or any(
                    token.kind == "JUMP_FORWARD"
                    and instruction_target(token) != continuation
                    for token in gap
                ):
                    raise UnsupportedPython311ControlFlow(
                        "except* with an else suite is not yet supported safely",
                        version=(3, 11),
                        code_name=self.code.co_name,
                        offset=entry.end,
                    )

            if continuation_jump is None:
                continue
            outer_finally = next(
                (
                    candidate
                    for candidate in self.exception_regions
                    if candidate is not entry
                    and not candidate.lasti
                    and self.tokens[prep_index].offset
                    <= candidate.start
                    < candidate.end
                    <= continuation
                    and self.tokens[
                        self.offset_to_index[candidate.target]
                    ].kind
                    == "PUSH_EXC_INFO"
                ),
                None,
            )
            if outer_finally is not None:
                raise UnsupportedPython311ControlFlow(
                    "except* combined with finally is not yet supported safely",
                    version=(3, 11),
                    code_name=self.code.co_name,
                    offset=outer_finally.start,
                )

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
        parser = _StraightLineDecompiler(
            self.code,
            self.tokens[start:end],
            compile_mode="expr",
            is_class_body=self.is_class_body,
        )
        for token in parser.tokens:
            parser.current_token = token
            parser._resolve_booleans(token.offset)
            parser._dispatch(token)
        parser._flush_assignment()
        if parser.body or parser.pending_booleans or len(parser.stack) != 1:
            raise Python311ParseError(
                f"Instruction range {start}:{end} is not one expression"
            )
        return parser._expression_value(parser.stack[0])

    def _condition_jump(self, start: int) -> Optional[int]:
        for index in range(start, len(self.tokens)):
            kind = self.tokens[index].kind
            if kind in _CONDITIONAL_JUMPS:
                return index
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

    def _condition_plan(self, start: int) -> Optional[_ConditionPlan]:
        jump_index = self._condition_jump(start)
        if jump_index is None:
            return None
        first_line = next(
            (
                token.linestart
                for token in self.tokens[start:jump_index]
                if token.linestart is not None
            ),
            None,
        )
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
            predicate = self._predicate(node_start, node_jump)
            true_offset, false_offset = self._jump_outcomes(node_jump)
            nodes[offset] = _DecisionNode(
                start_index=node_start,
                jump_index=node_jump,
                predicate=predicate,
                true_offset=true_offset,
                false_offset=false_offset,
            )
            for successor in (true_offset, false_offset):
                successor_index = self.offset_to_index[successor]
                successor_token = self.tokens[successor_index]
                block = self.cfg.block_at(successor)
                same_condition_line = successor_token.linestart in (
                    None,
                    first_line,
                )
                has_single_predecessor = len(self.cfg.predecessors(block.index)) == 1
                if (
                    same_condition_line
                    and has_single_predecessor
                    and self._condition_jump(successor_index) is not None
                ):
                    pending.append(successor_index)
                else:
                    endpoints.add(successor)

        if len(endpoints) != 2:
            return None
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
        return _ConditionPlan(
            entry_offset=entry_offset,
            nodes=nodes,
            endpoints=tuple(sorted(endpoints)),
            true_endpoint=true_endpoint,
            false_endpoint=false_endpoint,
            expression=build(entry_offset),
        )

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
        try:
            body = self._expression_slice(true_index, jump_index)
            orelse = self._expression_slice(false_index, join_index)
        except Python311ParseError:
            return None
        self.stack.append(
            ast.IfExp(test=plan.expression, body=body, orelse=orelse)
        )
        return join_index

    def _if_statement(
        self,
        plan: _ConditionPlan,
        loop: Optional[_LoopContext],
    ) -> int:
        true_index = self.offset_to_index[plan.true_endpoint]
        false_index = self.offset_to_index[plan.false_endpoint]
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

    def _for_target(
        self,
        start: int,
    ) -> Tuple[Optional[ast.expr], int]:
        token = self.tokens[start]
        if token.kind == "POP_TOP":
            return None, start + 1
        if token.kind.startswith("STORE_"):
            name = token.attr if isinstance(token.attr, str) else token.pattr
            return ast.Name(id=name, ctx=ast.Store()), start + 1
        if token.kind not in ("UNPACK_SEQUENCE", "UNPACK_EX"):
            self.current_token = token
            self._error("FOR_ITER is not followed by a store target")

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
            store = self.tokens[cursor]
            if not store.kind.startswith("STORE_"):
                self.current_token = store
                self._error("Unpacking loop target contains a non-store opcode")
            name = store.attr if isinstance(store.attr, str) else store.pattr
            target = ast.Name(id=name, ctx=ast.Store())
            if after >= 0 and target_index == before:
                target = ast.Starred(value=target, ctx=ast.Store())
            targets.append(target)
            cursor += 1
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

            condition = self._condition_plan(index)
            if condition is not None:
                loop_end = self._while_loop(condition, loop)
                if loop_end is not None:
                    index = loop_end
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

            if (
                token.kind == "POP_TOP"
                and index + 1 < end
                and loop is not None
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
