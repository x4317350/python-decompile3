"""Recover CPython 3.11 structural pattern matching as standard AST nodes."""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from decompyle3.controlflow.cfg import instruction_target
from decompyle3.parsers.p311.base import Python311ParseError


@dataclass
class _PatternSlot:
    kind: Optional[str] = None
    value: Optional[ast.expr] = None
    children: List["_PatternSlot"] = field(default_factory=list)
    keys: List[ast.expr] = field(default_factory=list)
    class_expr: Optional[ast.expr] = None
    positional_count: int = 0
    keyword_names: List[str] = field(default_factory=list)
    alternatives: List["_PatternSlot"] = field(default_factory=list)
    capture: Optional[str] = None
    star: bool = False
    discarded: bool = False
    rest: Optional[str] = None


@dataclass
class _Values:
    slots: List[_PatternSlot]


@dataclass(frozen=True)
class _Marker:
    kind: str


class MatchStructureDecompiler311:
    """Decode the stack protocol emitted by CPython's pattern compiler."""

    def __init__(self, owner):
        self.owner = owner
        self.tokens = owner.tokens
        self.offset_to_index = owner.offset_to_index

    def _error(self, message, index=None):
        if index is not None:
            self.owner.current_token = self.tokens[index]
        token = self.owner.current_token
        offset = token.offset if token is not None else "?"
        raise Python311ParseError(
            f"{message} ({self.owner.code.co_name!r}, offset {offset})"
        )

    @staticmethod
    def _name(token) -> str:
        return token.attr if isinstance(token.attr, str) else token.pattr

    @staticmethod
    def _expr_name(name: str) -> ast.expr:
        return ast.Name(id=name, ctx=ast.Load())

    def _looks_like_case_start(self, index: int) -> bool:
        kind = self.tokens[index].kind
        if kind in ("MATCH_MAPPING", "MATCH_SEQUENCE"):
            return True
        if kind == "NOP":
            # CPython uses NOP as the pattern marker for an irrefutable
            # wildcard case, but NOP also appears between a decorator and its
            # MAKE_FUNCTION sequence.  A wildcard marker starts a basic block
            # reached through a failed POP_JUMP_* pattern decision; that block
            # may contain only POP_TOP cleanup before the NOP.  Deriving the
            # boundary from the CFG avoids both decorator padding and an
            # arbitrary backward instruction window.
            block = self.owner.cfg.block_at(self.tokens[index].offset)
            prefix = tuple(
                instruction
                for instruction in block.instructions
                if instruction.offset < self.tokens[index].offset
            )
            if any(instruction.kind != "POP_TOP" for instruction in prefix):
                return False
            return any(
                edge.kind != "exception"
                and self.owner.cfg.block(edge.source).last.kind.startswith(
                    "POP_JUMP_"
                )
                for edge in self.owner.cfg.incoming(block.index)
            )
        if kind.startswith("POP_JUMP_") or kind.startswith("STORE_"):
            return True
        lookahead = []
        for token in self.tokens[index : index + 12]:
            lookahead.append(token)
            if (
                token is not self.tokens[index]
                and token.kind
                in (
                    "BUILD_CONST_KEY_MAP",
                    "BUILD_LIST",
                    "BUILD_MAP",
                    "BUILD_SET",
                    "BUILD_TUPLE",
                    "CALL",
                    "MAKE_FUNCTION",
                    "POP_TOP",
                    "PRECALL",
                    "RAISE_VARARGS",
                    "RETURN_VALUE",
                )
                or token.kind.startswith("STORE_")
            ):
                break

        def has_pattern_comparison():
            """A value pattern compares and then branches on the result."""
            comparison_seen = False
            for token in lookahead[1:]:
                if token.kind.startswith("MATCH_"):
                    return True
                if token.kind.startswith("COMPARE_") or token.kind == "IS":
                    comparison_seen = True
                    continue
                if token.kind.startswith("POP_JUMP_"):
                    return comparison_seen or (
                        kind == "COPY_STACK"
                        and (
                            "IF_NONE" in token.kind
                            or "IF_NOT_NONE" in token.kind
                        )
                    )
            return False

        if kind == "COPY_STACK":
            return any(
                token.kind.startswith("MATCH_")
                for token in lookahead[1:]
            ) or has_pattern_comparison()
        if kind == "LOAD_CONST":
            return has_pattern_comparison()
        if kind in (
            "LOAD_CLASSDEREF",
            "LOAD_DEREF",
            "LOAD_FAST",
            "LOAD_GLOBAL",
            "LOAD_NAME",
        ):
            return any(
                token.kind == "MATCH_CLASS"
                for token in lookahead[1:]
            ) or has_pattern_comparison()
        return False

    def _first_case(self, start: int, end: int) -> Optional[int]:
        line = self.tokens[start].linestart
        if line is None:
            return None
        for index in range(start + 1, end):
            token_line = self.tokens[index].linestart
            if token_line is not None and token_line > line:
                return index if self._looks_like_case_start(index) else None
        return None

    @staticmethod
    def _set_value(slot: _PatternSlot, expression: ast.expr):
        slot.kind = "value"
        slot.value = expression

    @staticmethod
    def _slot_pattern(slot: _PatternSlot) -> ast.pattern:
        if slot.kind == "value":
            pattern = ast.MatchValue(value=slot.value)
        elif slot.kind == "singleton":
            pattern = ast.MatchSingleton(value=slot.value.value)
        elif slot.kind == "sequence":
            pattern = ast.MatchSequence(
                patterns=[
                    MatchStructureDecompiler311._slot_pattern(child)
                    for child in slot.children
                ]
            )
        elif slot.kind == "mapping":
            pattern = ast.MatchMapping(
                keys=slot.keys,
                patterns=[
                    MatchStructureDecompiler311._slot_pattern(child)
                    for child in slot.children
                ],
                rest=slot.rest,
            )
        elif slot.kind == "class":
            positional = slot.children[: slot.positional_count]
            keywords = slot.children[slot.positional_count :]
            pattern = ast.MatchClass(
                cls=slot.class_expr,
                patterns=[
                    MatchStructureDecompiler311._slot_pattern(child)
                    for child in positional
                ],
                kwd_attrs=slot.keyword_names,
                kwd_patterns=[
                    MatchStructureDecompiler311._slot_pattern(child)
                    for child in keywords
                ],
            )
        elif slot.kind == "or":
            pattern = ast.MatchOr(
                patterns=[
                    MatchStructureDecompiler311._slot_pattern(alternative)
                    for alternative in slot.alternatives
                ]
            )
        elif slot.star:
            return ast.MatchStar(name=slot.capture)
        else:
            pattern = None

        if slot.capture is not None:
            return ast.MatchAs(pattern=pattern, name=slot.capture)
        if pattern is not None:
            return pattern
        return ast.MatchAs(pattern=None, name=None)

    @staticmethod
    def _unbound_slots(slot: _PatternSlot) -> List[_PatternSlot]:
        if slot.kind == "or":
            result = []
            for alternative in slot.alternatives:
                result.extend(
                    MatchStructureDecompiler311._unbound_slots(alternative)
                )
            return result
        result = []
        if (
            slot.kind is None
            and slot.capture is None
            and not slot.discarded
        ):
            result.append(slot)
        for child in slot.children:
            result.extend(
                MatchStructureDecompiler311._unbound_slots(child)
            )
        return result

    @staticmethod
    def _pop_slot(stack) -> _PatternSlot:
        while stack:
            value = stack.pop()
            if isinstance(value, _PatternSlot):
                return value
        raise Python311ParseError("Pattern stack has no subject value")

    def _run_pattern_vm(
        self,
        start: int,
        end: int,
        root: _PatternSlot,
        ignored_stores=frozenset(),
        ignored_operations=frozenset(),
    ):
        stack = [root]
        mapping_slot = None
        index = start
        while index < end:
            token = self.tokens[index]
            kind = token.kind
            if index in ignored_operations:
                index += 1
                continue

            if kind == "COPY_STACK":
                depth = int(token.attr)
                if depth <= len(stack):
                    stack.append(stack[-depth])
            elif kind == "SWAP_STACK":
                depth = int(token.attr)
                if depth <= len(stack):
                    stack[-1], stack[-depth] = stack[-depth], stack[-1]
            elif kind in ("LOAD_FAST", "LOAD_GLOBAL", "LOAD_NAME"):
                stack.append(self._expr_name(self._name(token)))
            elif kind == "LOAD_ATTR":
                expression = stack.pop()
                if isinstance(expression, ast.expr):
                    stack.append(
                        ast.Attribute(
                            value=expression,
                            attr=self._name(token),
                            ctx=ast.Load(),
                        )
                    )
            elif kind == "LOAD_CONST":
                stack.append(ast.Constant(value=token.attr))
            elif kind in ("COMPARE_EQ", "IS"):
                right = stack.pop()
                left = stack.pop()
                if isinstance(left, _PatternSlot) and isinstance(
                    right,
                    ast.expr,
                ):
                    if kind == "IS" and isinstance(right, ast.Constant):
                        left.kind = "singleton"
                        left.value = right
                    else:
                        self._set_value(left, right)
                elif isinstance(right, _PatternSlot) and isinstance(
                    left,
                    ast.expr,
                ):
                    self._set_value(right, left)
                stack.append(_Marker("condition"))
            elif kind.startswith("COMPARE_"):
                if len(stack) >= 2:
                    stack.pop()
                    stack.pop()
                stack.append(_Marker("condition"))
            elif kind == "MATCH_SEQUENCE":
                subject = self._pop_slot(stack)
                subject.kind = "sequence"
                stack.append(subject)
                stack.append(_Marker("condition"))
            elif kind == "MATCH_MAPPING":
                subject = self._pop_slot(stack)
                subject.kind = "mapping"
                mapping_slot = subject
                stack.append(subject)
                stack.append(_Marker("condition"))
            elif kind == "MATCH_KEYS":
                keys_value = stack.pop()
                if not (
                    isinstance(keys_value, ast.Constant)
                    and isinstance(keys_value.value, tuple)
                    and mapping_slot is not None
                ):
                    self._error("MATCH_KEYS has no constant key tuple", index)
                mapping_slot.keys = [
                    ast.Constant(value=key) for key in keys_value.value
                ]
                mapping_slot.children = [
                    _PatternSlot() for _ in keys_value.value
                ]
                stack.append(_Values(mapping_slot.children))
            elif kind == "MATCH_CLASS":
                names_value = stack.pop()
                class_expression = stack.pop()
                subject = self._pop_slot(stack)
                if not (
                    isinstance(names_value, ast.Constant)
                    and isinstance(names_value.value, tuple)
                    and isinstance(class_expression, ast.expr)
                ):
                    self._error("MATCH_CLASS operands are malformed", index)
                positional = int(token.attr)
                children = [
                    _PatternSlot()
                    for _ in range(positional + len(names_value.value))
                ]
                subject.kind = "class"
                subject.class_expr = class_expression
                subject.positional_count = positional
                subject.keyword_names = list(names_value.value)
                subject.children = children
                stack.append(_Values(children))
            elif kind in ("UNPACK_SEQUENCE", "UNPACK_EX"):
                unpacked = stack.pop()
                if isinstance(unpacked, _Values):
                    children = unpacked.slots
                else:
                    subject = (
                        unpacked
                        if isinstance(unpacked, _PatternSlot)
                        else self._pop_slot(stack)
                    )
                    if kind == "UNPACK_SEQUENCE":
                        children = [
                            _PatternSlot() for _ in range(int(token.attr))
                        ]
                    else:
                        argument = int(token.attr)
                        before = argument & 0xFF
                        after = argument >> 8
                        children = [
                            _PatternSlot()
                            for _ in range(before + after + 1)
                        ]
                        children[before].star = True
                    subject.children = children
                stack.extend(reversed(children))
            elif kind.startswith("POP_JUMP_"):
                if stack:
                    condition = stack.pop()
                    if (
                        isinstance(condition, _PatternSlot)
                        and condition.kind is None
                        and "NOT_NONE" in kind
                    ):
                        condition.kind = "singleton"
                        condition.value = ast.Constant(value=None)
            elif kind.startswith("STORE_"):
                if index not in ignored_stores:
                    subject = self._pop_slot(stack)
                    subject.capture = self._name(token)
            elif kind == "POP_TOP":
                if stack:
                    value = stack.pop()
                    if (
                        isinstance(value, _PatternSlot)
                        and value.kind is None
                        and value.capture is None
                    ):
                        value.discarded = True
            elif kind == "GET_LEN":
                stack.append(_Marker("length"))
            elif kind in {
                "BUILD_MAP",
                "DELETE_SUBSCR",
                "DICT_UPDATE",
                "JUMP_FORWARD",
                "NOP",
            }:
                pass
            index += 1

    def _or_pattern(
        self,
        start: int,
        pattern_end: int,
    ) -> Optional[_PatternSlot]:
        forward_targets = [
            instruction_target(token)
            for token in self.tokens[start:pattern_end]
            if token.kind == "JUMP_FORWARD"
        ]
        repeated = [
            target
            for target, count in Counter(forward_targets).items()
            if target is not None and count > 1
        ]
        if not repeated:
            return None
        success_offset = min(repeated)
        success_index = self.offset_to_index[success_offset]
        jumps = [
            index
            for index in range(start, success_index)
            if self.tokens[index].kind == "JUMP_FORWARD"
            and instruction_target(self.tokens[index]) == success_offset
        ]
        if len(jumps) < 2:
            return None

        alternatives = []
        segment_start = (
            start + 1
            if self.tokens[start].kind == "COPY_STACK"
            else start
        )
        for jump_index in jumps:
            while (
                segment_start < jump_index
                and self.tokens[segment_start].kind == "POP_TOP"
            ):
                segment_start += 1
            alternative = _PatternSlot()
            self._run_pattern_vm(
                segment_start,
                jump_index,
                alternative,
            )
            alternatives.append(alternative)
            segment_start = jump_index + 1

        names = [
            self._name(self.tokens[index])
            for index in range(success_index, pattern_end)
            if self.tokens[index].kind.startswith("STORE_")
        ]
        for alternative in alternatives:
            unbound = self._unbound_slots(alternative)
            if len(unbound) != len(names):
                self._error(
                    "OR-pattern alternatives bind inconsistent names",
                    start,
                )
            for slot, name in zip(unbound, names):
                slot.capture = name
        return _PatternSlot(kind="or", alternatives=alternatives)

    def _pattern_and_guard(
        self,
        start: int,
        body_start: int,
    ) -> Tuple[ast.pattern, Optional[ast.expr]]:
        store_indices = [
            index
            for index in range(start, body_start)
            if self.tokens[index].kind.startswith("STORE_")
        ]
        last_store = store_indices[-1] if store_indices else start - 1
        guard_jumps = [
            index
            for index in range(last_store + 1, body_start)
            if self.tokens[index].kind.startswith("POP_JUMP_")
        ]
        guard = None
        pattern_end = body_start
        if guard_jumps and store_indices:
            guard_jump = guard_jumps[-1]
            guard_start = last_store + 1
            while (
                guard_start < guard_jump
                and self.tokens[guard_start].kind == "POP_TOP"
            ):
                guard_start += 1
            try:
                guard = self.owner._expression_slice(
                    guard_start,
                    guard_jump,
                )
            except Python311ParseError:
                self._error("Case guard is not one expression", guard_start)
            pattern_end = last_store + 1

        root = self._or_pattern(start, pattern_end)
        if root is None:
            root = _PatternSlot()
            ignored_stores = set()
            ignored_operations = set()
            delete_index = next(
                (
                    index
                    for index in range(start, pattern_end)
                    if self.tokens[index].kind == "DELETE_SUBSCR"
                ),
                None,
            )
            if delete_index is not None:
                rest_store = next(
                    (
                        index
                        for index in range(delete_index + 1, pattern_end)
                        if self.tokens[index].kind.startswith("STORE_")
                    ),
                    None,
                )
                if rest_store is None:
                    self._error(
                        "Mapping rest pattern has no binding",
                        delete_index,
                    )
                root.rest = self._name(self.tokens[rest_store])
                ignored_stores.add(rest_store)
                match_keys = next(
                    index
                    for index in range(start, delete_index)
                    if self.tokens[index].kind == "MATCH_KEYS"
                )
                value_unpack = next(
                    index
                    for index in range(match_keys + 1, delete_index)
                    if self.tokens[index].kind
                    in ("UNPACK_SEQUENCE", "UNPACK_EX")
                )
                rest_start = next(
                    (
                        index
                        for index in range(value_unpack + 1, delete_index)
                        if self.tokens[index].kind
                        in ("BUILD_MAP", "SWAP_STACK")
                    ),
                    delete_index,
                )
                ignored_operations.update(
                    range(rest_start, delete_index + 1)
                )
            self._run_pattern_vm(
                start,
                pattern_end,
                root,
                frozenset(ignored_stores),
                frozenset(ignored_operations),
            )

        return self._slot_pattern(root), guard

    def _body_start(self, case_start: int, end: int) -> Optional[int]:
        case_line = self.tokens[case_start].linestart
        for index in range(case_start + 1, end):
            line = self.tokens[index].linestart
            if line is not None:
                if line > case_line:
                    next_line = next(
                        (
                            candidate
                            for candidate in range(index + 1, end)
                            if self.tokens[candidate].linestart is not None
                            and self.tokens[candidate].linestart > line
                        ),
                        end,
                    )
                    if any(
                        token.kind.startswith("MATCH_")
                        for token in self.tokens[index:next_line]
                    ):
                        continue
                    candidate_line = line
                    pattern_failures = []
                    for token in self.tokens[case_start:index]:
                        if not token.kind.startswith("POP_JUMP_"):
                            continue
                        target = instruction_target(token)
                        target_index = self.offset_to_index.get(target)
                        if (
                            target_index is not None
                            and target_index > index
                        ):
                            pattern_failures.append(target_index)
                    for guard_jump in range(index, end):
                        guard_line = self.tokens[guard_jump].linestart
                        if (
                            guard_jump > index
                            and guard_line is not None
                            and guard_line > candidate_line
                        ):
                            break
                        if (
                            self.tokens[guard_jump].kind
                            .startswith("POP_JUMP_")
                            and guard_jump + 1 < end
                            and (
                                self.tokens[guard_jump + 1].kind
                                == "POP_TOP"
                                or (
                                    pattern_failures
                                    and self.offset_to_index[
                                        instruction_target(
                                            self.tokens[guard_jump]
                                        )
                                    ]
                                    >= min(pattern_failures)
                                )
                            )
                        ):
                            body_start = guard_jump + 1
                            if self.tokens[body_start].kind == "POP_TOP":
                                body_start += 1
                            return body_start
                    return index
                if line < case_line:
                    return None
        return None

    def _case_failure_index(
        self,
        case_start: int,
        body_start: int,
        end: int,
    ) -> Optional[int]:
        """Return the first physical block used by this case's failed match."""
        body_offset = self.tokens[body_start].offset
        candidates = []
        for index in range(case_start, body_start):
            token = self.tokens[index]
            if (
                not token.kind.startswith("POP_JUMP_")
                and token.kind != "JUMP_FORWARD"
            ):
                continue
            target = instruction_target(token)
            target_index = self.offset_to_index.get(target)
            if (
                target_index is not None
                and target > body_offset
                and target_index < end
            ):
                candidates.append(target_index)
        return min(candidates) if candidates else None

    def _validated_exit_join(
        self,
        body_start: int,
        physical_end: int,
    ) -> Tuple[Optional[int], Optional[int]]:
        """Find one CFG-proven case exit and its trailing jump instruction."""
        graph = self.owner.cfg
        analysis = self.owner.control_flow
        body_block = graph.block_at(self.tokens[body_start].offset).index
        candidates = []
        for index in range(body_start, physical_end):
            token = self.tokens[index]
            if token.kind != "JUMP_FORWARD":
                continue
            target = instruction_target(token)
            target_index = self.offset_to_index.get(target)
            if target_index is None or target_index < physical_end:
                continue
            source_block = graph.block_at(token.offset).index
            target_block = graph.block_at(target).index
            if body_block not in analysis.dominators.get(source_block, ()):
                self._error(
                    "Match case exit is not dominated by its body entry",
                    index,
                )
            if target_block not in analysis.post_dominators.get(
                source_block,
                (),
            ):
                self._error(
                    "Match case exit target does not post-dominate its jump",
                    index,
                )
            candidates.append((index, target))

        targets = {target for _, target in candidates}
        if len(targets) > 1:
            self._error(
                "Match case body has ambiguous exit targets",
                candidates[-1][0],
            )
        if not candidates:
            return None, None
        return candidates[-1][0], candidates[-1][1]

    def _fallthrough_join(
        self,
        body_start: int,
        physical_end: int,
    ) -> Optional[int]:
        """Validate a direct case-body fallthrough into a shared CFG join."""
        if physical_end >= len(self.tokens):
            return None
        join_token = self.tokens[physical_end]
        if join_token.kind in ("POP_TOP", "SWAP_STACK"):
            return None

        graph = self.owner.cfg
        analysis = self.owner.control_flow
        body_block = graph.block_at(self.tokens[body_start].offset).index
        join_block = graph.block_at(join_token.offset).index
        if join_block not in analysis.post_dominators.get(body_block, ()):
            return None

        previous_block = graph.block_at(
            self.tokens[physical_end - 1].offset
        ).index
        if not any(
            edge.target == join_block and edge.kind != "exception"
            for edge in graph.outgoing(previous_block)
        ):
            return None
        return join_token.offset

    def _body_end(
        self,
        body_start: int,
        end: int,
        failure_index: Optional[int] = None,
        known_join_index: Optional[int] = None,
    ) -> Tuple[int, Optional[int]]:
        physical_end = (
            failure_index
            if failure_index is not None
            else known_join_index
            if known_join_index is not None
            else end
        )
        if physical_end <= body_start:
            self._error("Match case body has an invalid boundary", body_start)

        exit_index, join = self._validated_exit_join(
            body_start,
            physical_end,
        )
        if exit_index is not None:
            trailing_index = physical_end - 1
            body_end = (
                exit_index if exit_index == trailing_index else physical_end
            )
            return body_end, join

        last = self.tokens[physical_end - 1]
        if last.kind in ("RAISE_VARARGS", "RERAISE", "RETURN_VALUE"):
            return physical_end, None

        expected_join = (
            self.tokens[known_join_index].offset
            if known_join_index is not None
            else None
        )
        fallthrough_join = self._fallthrough_join(
            body_start,
            physical_end,
        )
        if (
            fallthrough_join is not None
            and (
                expected_join is None
                or fallthrough_join == expected_join
            )
        ):
            return physical_end, fallthrough_join
        self._error("Match case body has no structural terminator", body_start)

    def _next_case(
        self,
        body_end: int,
        case_line: int,
        end: int,
    ) -> Optional[int]:
        for index in range(body_end, end):
            line = self.tokens[index].linestart
            if (
                line is not None
                and line > case_line
                and self._looks_like_case_start(index)
            ):
                return index
        return None

    def match_statement(self, start: int, end: int, loop):
        subject_start = start
        while (
            subject_start < end
            and self.tokens[subject_start].kind
            in ("INTERNAL_EXTENDED_ARG", "INTERNAL_RESUME")
        ):
            subject_start += 1
        if subject_start >= end:
            return None
        first_case = self._first_case(subject_start, end)
        if first_case is None:
            return None
        if any(
            token.offset
            in self.owner._suppressed_exception_protocol_offsets
            for token in self.tokens[subject_start + 1 : first_case + 1]
        ):
            return None
        try:
            subject = self.owner._expression_slice(
                subject_start,
                first_case,
            )
        except Python311ParseError:
            return None

        cases = []
        cursor = first_case
        join_offsets = []
        while cursor is not None and cursor < end:
            case_line = self.tokens[cursor].linestart
            body_start = self._body_start(cursor, end)
            if body_start is None:
                return None
            if any(
                token.offset
                in self.owner._suppressed_exception_protocol_offsets
                for token in self.tokens[cursor:body_start]
            ):
                return None
            if (
                self.tokens[cursor].kind != "NOP"
                and not self.tokens[cursor].kind.startswith("STORE_")
                and not any(
                    token.kind.startswith(("MATCH_", "POP_JUMP_"))
                    for token in self.tokens[cursor:body_start]
                )
            ):
                # A multiline call can otherwise look like ``match`` when a
                # later argument contains a comparison.  A real refutable
                # case completes at least one pattern-test branch on the case
                # source line before its body starts.
                return None
            pattern, guard = self._pattern_and_guard(cursor, body_start)
            failure_index = self._case_failure_index(
                cursor,
                body_start,
                end,
            )
            known_join_index = (
                self.offset_to_index[min(join_offsets)]
                if join_offsets
                else None
            )
            body_end, join = self._body_end(
                body_start,
                end,
                failure_index=failure_index,
                known_join_index=known_join_index,
            )
            irrefutable = (
                isinstance(pattern, ast.MatchAs)
                and pattern.pattern is None
            )
            capture_start = body_start
            if (
                capture_start < body_end
                and self.tokens[capture_start].kind == "POP_TOP"
            ):
                capture_start += 1
            body = self.owner._capture_region(
                capture_start,
                body_end,
                loop,
            )
            exits_loop = (
                irrefutable
                and loop is not None
                and join == loop.break_target
                and body_end < end
                and self.tokens[body_end].kind == "JUMP_FORWARD"
            )
            if exits_loop:
                body.append(ast.Break())
            if (
                not body
                and body_end - body_start >= 2
                and self.tokens[body_end - 1].kind == "RETURN_VALUE"
                and self.tokens[body_end - 2].kind == "LOAD_CONST"
                and self.tokens[body_end - 2].attr is None
            ):
                body = [ast.Return(value=ast.Constant(value=None))]
            cases.append(
                ast.match_case(
                    pattern=pattern,
                    guard=guard,
                    body=body or [ast.Pass()],
                )
            )
            if join is not None:
                if (
                    not exits_loop
                    and join_offsets
                    and join not in join_offsets
                ):
                    self._error(
                        "Match cases have ambiguous join targets",
                        body_start,
                    )
                if not exits_loop:
                    join_offsets.append(join)
            if irrefutable:
                cursor = None
                final_index = (
                    self.offset_to_index[join]
                    if exits_loop
                    else self.offset_to_index[max(join_offsets)]
                    if join_offsets
                    else body_end
                )
                break
            next_search = (
                failure_index
                if failure_index is not None
                else body_end
            )
            case_search_end = (
                min(
                    end,
                    self.offset_to_index[min(join_offsets)],
                )
                if join_offsets
                else end
            )
            cursor = self._next_case(
                next_search,
                case_line,
                case_search_end,
            )
            final_index = body_end
            if cursor is None:
                if join_offsets:
                    final_index = self.offset_to_index[max(join_offsets)]
                break

        if not cases:
            return None
        self.owner.body.append(ast.Match(subject=subject, cases=cases))
        # A refutable final case leaves the shared match subject on the
        # physical operand stack.  CPython discards it on the no-match path
        # before continuing with the following source statement (or the
        # implicit ``return None``).  The subject has already been consumed
        # into ``ast.Match`` above, so this POP_TOP is protocol rather than a
        # source expression statement.
        if (
            final_index < end
            and self.tokens[final_index].kind == "POP_TOP"
        ):
            final_index += 1
        return final_index


def recover_match_statement311(owner, index: int, end: int, loop):
    return MatchStructureDecompiler311(owner).match_statement(
        index,
        end,
        loop,
    )
