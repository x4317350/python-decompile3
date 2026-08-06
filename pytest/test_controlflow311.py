"""Phase 4 acceptance tests for CPython 3.11 control-flow recovery."""

from __future__ import annotations

import ast
import io
import sys
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow.basicblock import BasicBlock
from decompyle3.controlflow.cfg import ControlFlowGraph, Edge, build_cfg
from decompyle3.controlflow.dominators import (
    IrreducibleControlFlowError,
    analyze_control_flow,
)
from decompyle3.controlflow.match_structures import (
    MatchStructureDecompiler311,
)
from decompyle3.controlflow.structures import StructuredDecompiler311
from decompyle3.parsers.main import python_parser
from decompyle3.parsers.p311.base import Python311ParseError
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


SOURCE = ROOT / "test" / "simple_source" / "311" / "02_control_flow.py"
TERMINAL_SOURCE = ROOT / "test" / "fixtures311" / "terminal_if_else.py"

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 control-flow tests require CPython 3.11",
)


@dataclass(frozen=True)
class FakeInstruction:
    offset: int
    kind: str
    target: int | None = None
    attr: int | None = None


def native_code(name):
    root = compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec")
    return next(
        code
        for code in Scanner311.iter_code_objects(root)
        if code.co_qualname == name
    )


def terminal_native_code(name):
    root = compile(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        str(TERMINAL_SOURCE),
        "exec",
    )
    return next(
        code
        for code in Scanner311.iter_code_objects(root)
        if code.co_name == name
    )


def terminal_decompiler(name="terminal_if_else"):
    decompiler, start = terminal_structured_decompiler(name)
    condition = decompiler._bounded_condition_plan(
        start,
        len(decompiler.tokens),
    )
    assert condition is not None
    return decompiler, condition


def terminal_structured_decompiler(name):
    code = terminal_native_code(name)
    scanner = Scanner311()
    tokens, _ = scanner.ingest(code)
    decompiler = StructuredDecompiler311(code, tokens)
    start = next(
        index
        for index, token in enumerate(tokens)
        if token.kind not in ("INTERNAL_RESUME",)
    )
    return decompiler, start


def graph_for(name):
    scanner = Scanner311()
    scanner.ingest(native_code(name))
    return build_cfg(scanner.normalized_instructions)


def recover_source(tmp_path):
    bytecode = tmp_path / "02_control_flow.pyc"
    version, _, _, code, implementation, *_ = compile_source(SOURCE, bytecode)
    assert version == (3, 11)
    assert implementation is PythonImplementation.CPython

    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def recover_terminal_source():
    output = io.StringIO()
    code_deparse(
        compile(
            TERMINAL_SOURCE.read_text(encoding="utf-8"),
            str(TERMINAL_SOURCE),
            "exec",
        ),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def execute(source, name):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def test_cfg_splits_blocks_and_labels_all_edge_kinds():
    graph = graph_for("loops")

    assert graph.entry == 0
    assert graph.reachable_blocks
    assert all(block.reachable for block in graph.blocks)
    assert {edge.kind for edge in graph.edges} >= {
        "exhausted",
        "false",
        "fallthrough",
        "iterate",
        "jump",
        "true",
    }
    assert any(block.terminator == "RETURN_VALUE" for block in graph.blocks)

    formatted = graph.format()
    assert formatted == graph.format()
    assert "B0 [" in formatted
    assert "pred=[" in formatted
    assert "succ=[" in formatted
    assert "0x" not in formatted


def test_dominators_post_dominators_and_natural_loops():
    graph = graph_for("loops")
    analysis = analyze_control_flow(graph)
    reachable = set(graph.reachable_blocks)

    assert all(graph.entry in analysis.dominators[node] for node in reachable)
    assert analysis.immediate_dominators[graph.entry] is None
    assert all(
        node in analysis.post_dominators[node] for node in reachable
    )
    assert analysis.back_edges
    assert len(analysis.loops) >= 2

    for loop in analysis.loops:
        assert loop.header in loop.blocks
        assert loop.latch in loop.blocks
        assert loop.header in analysis.dominators[loop.latch]

    exits = {
        block.index
        for block in graph.blocks
        if block.reachable and not graph.successors(block.index)
    }
    assert len(exits) == 1
    assert exits <= analysis.post_dominators[graph.entry]


def test_unreachable_block_is_split_and_not_merged():
    graph = build_cfg(
        [
            FakeInstruction(0, "NOP"),
            FakeInstruction(2, "JUMP_FORWARD", target=8),
            FakeInstruction(4, "LOAD_CONST"),
            FakeInstruction(6, "RAISE_VARARGS"),
            FakeInstruction(8, "RETURN_VALUE"),
        ]
    )

    assert len(graph.blocks) == 3
    assert graph.block_at(4).reachable is False
    assert graph.block_at(4).start == 4
    assert graph.block_at(4).end == 8
    assert graph.block_at(4).terminator == "RAISE_VARARGS"
    assert graph.block_at(8).terminator == "RETURN_VALUE"
    assert not analyze_control_flow(graph).loops
    assert "B1 [4,8) unreachable" in graph.format()


def test_irreducible_graph_is_rejected_explicitly():
    instructions = tuple(
        FakeInstruction(offset, "NOP") for offset in (0, 2, 4)
    )
    blocks = tuple(
        BasicBlock(
            index,
            instruction.offset,
            instruction.offset + 2,
            (instruction,),
            True,
        )
        for index, instruction in enumerate(instructions)
    )
    graph = ControlFlowGraph(
        blocks=blocks,
        edges=(
            Edge(0, 1, "true"),
            Edge(0, 2, "false"),
            Edge(1, 2, "jump"),
            Edge(2, 1, "jump"),
        ),
        entry=0,
        offset_to_block={0: 0, 2: 1, 4: 2},
    )

    with pytest.raises(
        IrreducibleControlFlowError, match="multiple entries: B1, B2"
    ):
        analyze_control_flow(graph)


def test_match_wildcard_nop_is_distinguished_from_decorator_padding():
    wildcard_tokens = (
        FakeInstruction(0, "MATCH_CLASS"),
        FakeInstruction(
            2,
            "POP_JUMP_FORWARD_IF_NONE",
            target=8,
            attr=8,
        ),
        FakeInstruction(4, "RETURN_VALUE"),
        FakeInstruction(8, "POP_TOP"),
        FakeInstruction(10, "NOP"),
        FakeInstruction(12, "RETURN_VALUE"),
    )
    wildcard_owner = SimpleNamespace(
        tokens=wildcard_tokens,
        offset_to_index={
            token.offset: index
            for index, token in enumerate(wildcard_tokens)
        },
        cfg=build_cfg(wildcard_tokens),
    )
    wildcard_matcher = MatchStructureDecompiler311(wildcard_owner)

    decorator_tokens = (
        FakeInstruction(
            0,
            "POP_JUMP_FORWARD_IF_FALSE",
            target=8,
            attr=8,
        ),
        FakeInstruction(2, "RETURN_VALUE"),
        FakeInstruction(8, "LOAD_NAME"),
        FakeInstruction(10, "NOP"),
        FakeInstruction(12, "MAKE_FUNCTION"),
        FakeInstruction(14, "RETURN_VALUE"),
    )
    decorator_owner = SimpleNamespace(
        tokens=decorator_tokens,
        offset_to_index={
            token.offset: index
            for index, token in enumerate(decorator_tokens)
        },
        cfg=build_cfg(decorator_tokens),
    )
    decorator_matcher = MatchStructureDecompiler311(decorator_owner)

    assert wildcard_matcher._looks_like_case_start(4)
    assert not decorator_matcher._looks_like_case_start(3)


def test_cfg_debug_option_prints_stable_graph(capsys):
    code = native_code("choose")
    result = python_parser(
        code,
        version=(3, 11),
        parser_debug={"cfg": True},
        python_implementation=PythonImplementation.CPython,
    )

    output = capsys.readouterr().out
    assert result.kind == "stmts"
    assert output.startswith("B0 [")
    assert "succ=[" in output


def test_control_flow_source_reparses_recompiles_and_has_all_structures(
    tmp_path,
):
    recovered = recover_source(tmp_path)
    tree = ast.parse(recovered)
    compile(tree, "<phase4-recovered>", "exec")

    assert "COME_FROM" not in recovered
    assert any(isinstance(node, ast.IfExp) for node in ast.walk(tree))
    assert any(isinstance(node, ast.Break) for node in ast.walk(tree))
    assert any(isinstance(node, ast.Continue) for node in ast.walk(tree))
    assert any(
        isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And)
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.If)
        and node.orelse
        and isinstance(node.orelse[0], ast.If)
        for node in ast.walk(tree)
    )

    loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.While))
    ]
    assert any(isinstance(node, ast.For) and node.orelse for node in loops)
    assert any(isinstance(node, ast.While) and node.orelse for node in loops)
    assert any(
        isinstance(child, ast.For)
        for node in loops
        for child in node.body
    )


def test_recovered_control_flow_has_equivalent_behavior(tmp_path):
    original = execute(SOURCE.read_text(encoding="utf-8"), "phase4_original")
    recovered = execute(recover_source(tmp_path), "phase4_recovered")

    for value in (-3, 0, 1, 2):
        assert recovered["classify"](value) == original["classify"](value)
    for values in ([], [1, 2, 3], [1, 0, 3], [2, -1, 4], [600, 600]):
        assert recovered["loops"](values) == original["loops"](values)
    for arguments in (
        (False, False, None),
        (False, "right", "fallback"),
        ("left", False, "fallback"),
        ("left", "right", "fallback"),
    ):
        assert recovered["nested_conditions"](*arguments) == original[
            "nested_conditions"
        ](*arguments)
    for left, right in ((0, 2), (1, 2), ("", "right"), ("left", "")):
        assert recovered["boolean_values"](left, right) == original[
            "boolean_values"
        ](left, right)
    for value in (None, 0, "value"):
        assert recovered["choose"](value) == original["choose"](value)
        assert recovered["not_none_loop"](value) == original[
            "not_none_loop"
        ](value)
    for value, items in ((None, []), (None, [1, None, 2]), (3, [1, 2])):
        assert recovered["none_control"](value, list(items)) == original[
            "none_control"
        ](value, list(items))
    for rows in ([], [[]], [[1, 2], [0, 3]], [[1, -1, 5], [2]]):
        assert recovered["nested_loops"](rows) == original["nested_loops"](
            rows
        )
    for limit in (0, 1, 2, 7):
        assert recovered["while_continue"](limit) == original[
            "while_continue"
        ](limit)


def test_terminal_if_else_and_elif_keep_mutually_exclusive_ast_regions():
    recovered = recover_terminal_source()
    tree = ast.parse(recovered)
    compile(tree, "<terminal-if-recovered>", "exec")
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    terminal_if = functions["terminal_if_else"].body[0]
    assert isinstance(terminal_if, ast.If)
    assert terminal_if.orelse
    assert len(functions["terminal_if_else"].body) == 1

    terminal_elif = functions["terminal_if_elif"].body[0]
    assert isinstance(terminal_elif, ast.If)
    assert len(terminal_elif.orelse) == 1
    assert isinstance(terminal_elif.orelse[0], ast.If)
    nested_elif = terminal_elif.orelse[0]
    assert nested_elif.orelse
    assert len(functions["terminal_if_elif"].body) == 1

    multi = functions["terminal_multi_elif"].body[0]
    assert isinstance(multi, ast.If)
    assert isinstance(multi.orelse[0], ast.If)
    assert isinstance(multi.orelse[0].orelse[0], ast.If)
    assert len(multi.orelse[0].orelse[0].orelse) == 2
    assert len(functions["terminal_multi_elif"].body) == 1

    nested = functions["terminal_nested_elif"]
    assert len(nested.body) == 2
    assert isinstance(nested.body[1], ast.If)
    positive = nested.body[1].orelse[0]
    assert isinstance(positive, ast.If)
    assert isinstance(positive.body[0], ast.If)
    assert positive.body[0].orelse

    for name in (
        "terminal_short_circuit",
        "terminal_membership",
        "terminal_reversed",
        "joined_if_else",
    ):
        statement = functions[name].body[0]
        assert isinstance(statement, ast.If)
        assert statement.orelse


def test_terminal_if_else_and_elif_preserve_branch_behavior():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_if_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_if_recovered",
    )

    for flag, expected in ((False, ["right"]), (True, ["left"])):
        original_events = []
        recovered_events = []
        original["terminal_if_else"](flag, original_events)
        recovered["terminal_if_else"](flag, recovered_events)
        assert original_events == expected
        assert recovered_events == original_events

    for value, expected in (
        (-2, ["not positive"]),
        (2, ["positive even"]),
        (3, ["positive odd"]),
    ):
        original_events = []
        recovered_events = []
        original["terminal_nested"](value, original_events)
        recovered["terminal_nested"](value, recovered_events)
        assert original_events == expected
        assert recovered_events == original_events

    for flag in (False, True):
        original_events = []
        recovered_events = []
        original["plain_terminal_if"](flag, original_events)
        recovered["plain_terminal_if"](flag, recovered_events)
        assert recovered_events == original_events

        original_events = []
        recovered_events = []
        original["early_return"](flag, original_events)
        recovered["early_return"](flag, recovered_events)
        assert recovered_events == original_events
        assert recovered["terminal_explicit_returns"](flag) == original[
            "terminal_explicit_returns"
        ](flag)

    for value, expected in (
        (1, ["one"]),
        (2, ["two"]),
        (3, ["other"]),
    ):
        original_events = []
        recovered_events = []
        original["terminal_if_elif"](value, original_events)
        recovered["terminal_if_elif"](value, recovered_events)
        assert original_events == expected
        assert recovered_events == original_events

    for value, expected in (
        (0, ["zero"]),
        (1, ["one"]),
        (2, ["two"]),
        (3, ["other", "done"]),
    ):
        original_events = []
        recovered_events = []
        original["terminal_multi_elif"](value, original_events)
        recovered["terminal_multi_elif"](value, recovered_events)
        assert original_events == expected
        assert recovered_events == original_events

    for value, expected in (
        (-1, ["start", "negative"]),
        (0, ["start", "zero"]),
        (2, ["start", "positive even"]),
        (3, ["start", "positive odd"]),
    ):
        original_events = []
        recovered_events = []
        original["terminal_nested_elif"](value, original_events)
        recovered["terminal_nested_elif"](value, recovered_events)
        assert original_events == expected
        assert recovered_events == original_events

    for flag in (False, True):
        original_events = []
        recovered_events = []
        original["terminal_reversed"](flag, original_events)
        recovered["terminal_reversed"](flag, recovered_events)
        assert recovered_events == original_events

        original_events = []
        recovered_events = []
        original_result = original["terminal_mixed_return"](
            flag,
            original_events,
        )
        recovered_result = recovered["terminal_mixed_return"](
            flag,
            recovered_events,
        )
        assert recovered_result == original_result
        assert recovered_events == original_events

        for function_name in ("terminal_reversed_layout", "joined_if_else"):
            original_events = []
            recovered_events = []
            original[function_name](flag, original_events)
            recovered[function_name](flag, recovered_events)
            assert recovered_events == original_events


def test_terminal_conditions_preserve_special_method_side_effects():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_special_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_special_recovered",
    )

    class BoolProbe:
        def __init__(self, value, calls, label):
            self.value = value
            self.calls = calls
            self.label = label

        def __bool__(self):
            self.calls.append(self.label)
            return self.value

    for left_value, right_value in ((False, True), (True, False), (True, True)):
        original_calls = []
        recovered_calls = []
        original_events = []
        recovered_events = []
        original["terminal_short_circuit"](
            BoolProbe(left_value, original_calls, "left"),
            BoolProbe(right_value, original_calls, "right"),
            original_events,
        )
        recovered["terminal_short_circuit"](
            BoolProbe(left_value, recovered_calls, "left"),
            BoolProbe(right_value, recovered_calls, "right"),
            recovered_events,
        )
        assert recovered_calls == original_calls
        assert recovered_events == original_events

    class EqualityProbe:
        def __init__(self, value, calls):
            self.value = value
            self.calls = calls

        def __eq__(self, other):
            self.calls.append(other)
            return self.value == other

    for value in (1, 2, 3):
        original_calls = []
        recovered_calls = []
        original_events = []
        recovered_events = []
        original["terminal_if_elif"](
            EqualityProbe(value, original_calls),
            original_events,
        )
        recovered["terminal_if_elif"](
            EqualityProbe(value, recovered_calls),
            recovered_events,
        )
        assert recovered_calls == original_calls
        assert recovered_events == original_events

    class HashProbe:
        def __init__(self, value, calls):
            self.value = value
            self.calls = calls

        def __hash__(self):
            self.calls.append(("hash", self.value))
            return hash(self.value)

        def __eq__(self, other):
            self.calls.append(("eq", other))
            return self.value == other

    for value in ("left", "missing"):
        original_calls = []
        recovered_calls = []
        original_events = []
        recovered_events = []
        original["terminal_membership"](
            HashProbe(value, original_calls),
            original_events,
        )
        recovered["terminal_membership"](
            HashProbe(value, recovered_calls),
            recovered_events,
        )
        assert recovered_calls == original_calls
        assert recovered_events == original_events

    def raise_condition():
        raise LookupError("condition")

    for namespace in (original, recovered):
        with pytest.raises(LookupError, match="condition"):
            namespace["terminal_condition"](raise_condition, [])


def test_terminal_condition_is_evaluated_once_and_plain_if_stays_plain():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_condition_original",
    )
    recovered_text = recover_terminal_source()
    recovered = execute(recovered_text, "terminal_condition_recovered")

    for result in (False, True):
        original_calls = []
        recovered_calls = []
        original_events = []
        recovered_events = []

        def original_predicate():
            original_calls.append("condition")
            return result

        def recovered_predicate():
            recovered_calls.append("condition")
            return result

        original["terminal_condition"](original_predicate, original_events)
        recovered["terminal_condition"](
            recovered_predicate,
            recovered_events,
        )
        assert recovered_calls == original_calls == ["condition"]
        assert recovered_events == original_events

    tree = ast.parse(recovered_text)
    plain = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "plain_terminal_if"
    )
    assert isinstance(plain.body[0], ast.If)
    assert plain.body[0].orelse == []

    early = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "early_return"
    )
    assert isinstance(early.body[0], ast.If)
    assert early.body[0].orelse == []
    assert isinstance(early.body[0].body[-1], ast.Return)


def test_terminal_no_else_implicit_epilogues_are_not_source_returns():
    recovered_text = recover_terminal_source()
    tree = ast.parse(recovered_text)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in (
        "terminal_and_no_else",
        "terminal_or_no_else",
        "terminal_nested_no_else",
        "terminal_nested_short_circuit_no_else",
        "terminal_many_and_no_else",
        "terminal_mixed_no_else",
        "terminal_not_no_else",
        "terminal_before_no_else",
    ):
        function = functions[name]
        assert isinstance(function.body[-1], ast.If)
        assert function.body[-1].orelse == []
        assert not any(
            isinstance(node, ast.Return)
            for node in ast.walk(function)
        )


def test_terminal_empty_if_restores_pass_without_source_returns():
    recovered_text = recover_terminal_source()
    tree = ast.parse(recovered_text)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }

    for name in (
        "terminal_empty_if",
        "terminal_empty_and",
        "terminal_empty_many_and",
        "terminal_empty_or",
        "terminal_empty_not",
        "terminal_empty_mixed",
        "terminal_empty_condition",
    ):
        function = functions[name]
        assert len(function.body) == 1
        statement = function.body[0]
        assert isinstance(statement, ast.If)
        assert statement.orelse == []
        assert len(statement.body) == 1
        assert isinstance(statement.body[0], ast.Pass)
        assert not any(isinstance(node, ast.Return) for node in ast.walk(function))


@pytest.mark.parametrize(
    ("name", "exit_count"),
    (
        ("terminal_empty_if", 2),
        ("terminal_empty_and", 3),
        ("terminal_empty_many_and", 4),
        ("terminal_empty_or", 2),
        ("terminal_empty_not", 2),
        ("terminal_empty_mixed", 2),
        ("terminal_empty_condition", 3),
    ),
)
def test_terminal_empty_if_plan_owns_all_physical_none_returns(
    name,
    exit_count,
):
    decompiler, condition = terminal_decompiler(name)
    plan = decompiler._terminal_empty_if_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )

    assert plan is not None
    assert len(plan.exit_blocks) == exit_count
    assert len(plan.owned_offsets) == exit_count * 2
    assert plan.condition_blocks
    assert condition.true_endpoint == min(condition.endpoints)


def test_terminal_empty_if_preserves_condition_order_and_exceptions():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_empty_if_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_empty_if_recovered",
    )

    for first_result, second_result in (
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    ):
        original_calls = []
        recovered_calls = []

        def original_first():
            original_calls.append("first")
            return first_result

        def original_second():
            original_calls.append("second")
            return second_result

        def recovered_first():
            recovered_calls.append("first")
            return first_result

        def recovered_second():
            recovered_calls.append("second")
            return second_result

        assert (
            original["terminal_empty_condition"](
                original_first,
                original_second,
            )
            is None
        )
        assert (
            recovered["terminal_empty_condition"](
                recovered_first,
                recovered_second,
            )
            is None
        )
        assert recovered_calls == original_calls

    def raise_condition():
        raise LookupError("terminal empty condition")

    for namespace in (original, recovered):
        with pytest.raises(LookupError, match="terminal empty condition"):
            namespace["terminal_empty_condition"](
                lambda: True,
                raise_condition,
            )


def test_terminal_bare_return_preserves_short_circuit_structure_and_behavior():
    recovered_text = recover_terminal_source()
    tree = ast.parse(recovered_text)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "terminal_return_condition"
    )
    assert len(function.body) == 1
    statement = function.body[0]
    assert isinstance(statement, ast.If)
    assert isinstance(statement.test, ast.BoolOp)
    assert isinstance(statement.test.op, ast.And)
    assert len(statement.body) == 1
    assert isinstance(statement.body[0], ast.Return)
    assert statement.body[0].value is None

    decompiler, condition = terminal_decompiler(
        "terminal_return_condition"
    )
    plan = decompiler._terminal_empty_if_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert plan is not None
    assert plan.suite_kind == "return"

    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_return_original",
    )
    recovered = execute(recovered_text, "terminal_return_recovered")
    for first_result, second_result in (
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    ):
        original_calls = []
        recovered_calls = []

        def original_first():
            original_calls.append("first")
            return first_result

        def original_second():
            original_calls.append("second")
            return second_result

        def recovered_first():
            recovered_calls.append("first")
            return first_result

        def recovered_second():
            recovered_calls.append("second")
            return second_result

        assert (
            original["terminal_return_condition"](
                original_first,
                original_second,
            )
            is None
        )
        assert (
            recovered["terminal_return_condition"](
                recovered_first,
                recovered_second,
            )
            is None
        )
        assert recovered_calls == original_calls


def test_terminal_empty_if_does_not_guess_explicit_return_none():
    suite = "return None"
    root = compile(
        f"def explicit_none(first, second):\n"
        f"    if first and second:\n"
        f"        {suite}\n",
        "<terminal-explicit-none>",
        "exec",
    )
    code = next(
        code
        for code in Scanner311.iter_code_objects(root)
        if code.co_name == "explicit_none"
    )
    scanner = Scanner311()
    tokens, _ = scanner.ingest(code)
    decompiler = StructuredDecompiler311(code, tokens)
    start = next(
        index
        for index, token in enumerate(tokens)
        if token.kind != "INTERNAL_RESUME"
    )

    assert decompiler._bounded_condition_plan(start, len(tokens)) is None
    with pytest.raises(
        Python311ParseError,
        match="Unsupported phase-3 opcode POP_JUMP_FORWARD_IF_FALSE",
    ):
        decompiler.decompile_body()


@pytest.mark.parametrize(
    ("name", "exit_count", "operator"),
    (
        ("terminal_short_circuit_statement_and", 2, ast.And),
        ("terminal_short_circuit_statement_or", 2, ast.Or),
        ("terminal_short_circuit_statement_many", 3, ast.And),
    ),
)
def test_terminal_short_circuit_statement_plan_owns_cleanup_and_ast(
    name,
    exit_count,
    operator,
):
    decompiler, start = terminal_structured_decompiler(name)
    plan = decompiler._terminal_short_circuit_statement_plan(
        start,
        loop=None,
        region_end=len(decompiler.tokens),
    )

    assert plan is not None
    assert isinstance(plan.expression, ast.BoolOp)
    assert isinstance(plan.expression.op, operator)
    assert len(plan.exit_blocks) == exit_count
    assert len(plan.cleanup_offsets) == exit_count
    assert plan.condition_blocks

    recovered_text = recover_terminal_source()
    tree = ast.parse(recovered_text)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    assert len(function.body) == 1
    statement = function.body[0]
    assert isinstance(statement, ast.Expr)
    assert isinstance(statement.value, ast.BoolOp)
    assert isinstance(statement.value.op, operator)
    assert not any(isinstance(node, ast.Return) for node in ast.walk(function))


def test_terminal_short_circuit_statement_preserves_calls_and_return_values():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_short_circuit_statement_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_short_circuit_statement_recovered",
    )

    class BindingProbe:
        def __init__(self, truth, events):
            self.truth = truth
            self.events = events

        def __bool__(self):
            self.events.append("bool")
            return self.truth

        def binding(self, value):
            self.events.append(("binding", value))
            return "bound"

    for name in (
        "terminal_short_circuit_statement_and",
        "terminal_short_circuit_statement_or",
    ):
        for truth in (False, True):
            original_events = []
            recovered_events = []
            original_probe = BindingProbe(truth, original_events)
            recovered_probe = BindingProbe(truth, recovered_events)

            assert original[name](original_probe) is None
            assert recovered[name](recovered_probe) is None
            assert recovered_events == original_events

    for values in (
        (False, False),
        (False, True),
        (True, False),
        (True, True),
    ):
        original_events = []
        recovered_events = []

        def callback(events, label, result):
            def invoke():
                events.append(label)
                return result

            return invoke

        original_result = original[
            "terminal_short_circuit_statement_many"
        ](
            callback(original_events, "first", values[0]),
            callback(original_events, "second", values[1]),
            callback(original_events, "final", True),
        )
        recovered_result = recovered[
            "terminal_short_circuit_statement_many"
        ](
            callback(recovered_events, "first", values[0]),
            callback(recovered_events, "second", values[1]),
            callback(recovered_events, "final", True),
        )
        assert recovered_result is original_result is None
        assert recovered_events == original_events

    class RaisingBindingProbe(BindingProbe):
        def binding(self, value):
            self.events.append(("binding", value))
            raise LookupError("binding failure")

    for namespace in (original, recovered):
        events = []
        with pytest.raises(LookupError, match="binding failure"):
            namespace["terminal_short_circuit_statement_and"](
                RaisingBindingProbe(True, events)
            )
        assert events == ["bool", ("binding", False)]


@pytest.mark.parametrize(
    "corruption",
    (
        "outside_region",
        "loop_context",
        "class_body",
        "module_body",
        "generator",
        "coroutine",
        "async_generator",
        "pending_stack",
        "missing_position",
        "wrong_position_line",
        "non_none_return",
        "missing_cleanup",
        "missing_return",
        "extra_pop",
        "wrong_condition_opcode",
        "wrong_jump_target",
        "normal_successor",
        "back_edge",
        "exception_edge",
        "foreign_predecessor",
        "unreachable_exit",
        "work_limit",
    ),
)
def test_terminal_short_circuit_statement_rejects_unsafe_shape(corruption):
    decompiler, start = terminal_structured_decompiler(
        "terminal_short_circuit_statement_and"
    )
    baseline = decompiler._terminal_short_circuit_statement_plan(
        start,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert baseline is not None

    blocks = decompiler.cfg.blocks
    edges = decompiler.cfg.edges
    loop = None
    region_end = len(decompiler.tokens)
    if corruption == "outside_region":
        region_end -= 1
    elif corruption == "loop_context":
        loop = SimpleNamespace()
    elif corruption == "class_body":
        decompiler.is_class_body = True
    elif corruption == "module_body":
        decompiler.code = decompiler.code.replace(co_name="<module>")
    elif corruption in ("generator", "coroutine", "async_generator"):
        flag = {
            "generator": 0x20,
            "coroutine": 0x80,
            "async_generator": 0x200,
        }[corruption]
        decompiler.code = decompiler.code.replace(
            co_flags=decompiler.code.co_flags | flag,
        )
    elif corruption == "pending_stack":
        decompiler.stack.append(ast.Constant(value="unexpected"))
    elif corruption in ("missing_position", "wrong_position_line"):
        cleanup = min(baseline.cleanup_offsets)
        if corruption == "missing_position":
            decompiler._positions_by_offset.pop(cleanup)
        else:
            line, end_line, column, end_column = (
                decompiler._positions_by_offset[cleanup]
            )
            decompiler._positions_by_offset[cleanup] = (
                line + 1,
                end_line + 1,
                column,
                end_column,
            )
    elif corruption in (
        "non_none_return",
        "missing_cleanup",
        "missing_return",
    ):
        cleanup = min(baseline.cleanup_offsets)
        cleanup_token = decompiler.tokens[
            decompiler.offset_to_index[cleanup]
        ]
        if corruption == "non_none_return":
            load = decompiler.tokens[
                decompiler.offset_to_index[cleanup] + 1
            ]
            assert load.kind == "LOAD_CONST"
            load.attr = 1
        elif corruption == "missing_cleanup":
            cleanup_token.kind = "NOP"
        else:
            returned = decompiler.tokens[
                decompiler.offset_to_index[cleanup] + 2
            ]
            assert returned.kind == "RETURN_VALUE"
            returned.kind = "NOP"
    elif corruption == "extra_pop":
        token = decompiler.tokens[start]
        assert token.kind == "LOAD_FAST"
        token.kind = "POP_TOP"
    elif corruption in ("wrong_condition_opcode", "wrong_jump_target"):
        condition_block = min(baseline.condition_blocks)
        block = blocks[condition_block]
        last = block.instructions[-1]
        replacement = FakeInstruction(
            offset=last.offset,
            kind=(
                "POP_JUMP_FORWARD_IF_FALSE"
                if corruption == "wrong_condition_opcode"
                else last.kind
            ),
            target=(
                min(baseline.cleanup_offsets)
                if corruption == "wrong_condition_opcode"
                else max(baseline.cleanup_offsets) + 100
            ),
        )
        blocks = tuple(
            replace(
                candidate,
                instructions=candidate.instructions[:-1] + (replacement,),
            )
            if candidate.index == condition_block
            else candidate
            for candidate in blocks
        )
    elif corruption in ("normal_successor", "foreign_predecessor"):
        extra_index = len(blocks)
        extra_start = max(block.end for block in blocks) + 2
        extra = BasicBlock(
            index=extra_index,
            start=extra_start,
            end=extra_start + 2,
            instructions=(FakeInstruction(extra_start, "NOP"),),
        )
        blocks = (*blocks, extra)
        if corruption == "normal_successor":
            edges = tuple(
                sorted(
                    (*edges, Edge(max(baseline.exit_blocks), extra_index, "jump"))
                )
            )
        else:
            edges = tuple(
                sorted(
                    (*edges, Edge(extra_index, min(baseline.exit_blocks), "jump"))
                )
            )
    elif corruption in ("back_edge", "exception_edge"):
        source = max(baseline.exit_blocks)
        kind = "jump" if corruption == "back_edge" else "exception"
        edges = tuple(
            sorted((*edges, Edge(source, decompiler.cfg.entry, kind)))
        )
    elif corruption == "unreachable_exit":
        orphan = max(baseline.exit_blocks)
        edges = tuple(edge for edge in edges if edge.target != orphan)
    elif corruption == "work_limit":
        target = min(baseline.exit_blocks)
        edges = tuple(
            sorted(
                (
                    *edges,
                    *(
                        Edge(decompiler.cfg.entry, target, "jump")
                        for _ in range(300)
                    ),
                )
            )
        )

    decompiler.cfg = ControlFlowGraph(
        blocks=blocks,
        edges=edges,
        entry=decompiler.cfg.entry,
        offset_to_block=decompiler.cfg.offset_to_block,
    )
    assert (
        decompiler._terminal_short_circuit_statement_plan(
            start,
            loop=loop,
            region_end=region_end,
        )
        is None
    )


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_entry_position",
        "missing_jump_position",
        "missing_pass_position",
        "wrong_span",
        "wrong_indent",
        "duplicate_pass_position",
    ),
)
def test_terminal_empty_if_plan_rejects_ambiguous_pass_position(corruption):
    decompiler, condition = terminal_decompiler("terminal_empty_and")
    baseline = decompiler._terminal_empty_if_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert baseline is not None

    pass_offset = condition.true_endpoint
    if corruption == "missing_entry_position":
        decompiler._positions_by_offset.pop(condition.entry_offset)
    elif corruption == "missing_jump_position":
        jump_offset = decompiler.tokens[
            next(iter(condition.nodes.values())).jump_index
        ].offset
        decompiler._positions_by_offset.pop(jump_offset)
    elif corruption == "missing_pass_position":
        decompiler._positions_by_offset.pop(pass_offset)
    elif corruption == "wrong_span":
        line, end_line, column, end_column = (
            decompiler._positions_by_offset[pass_offset]
        )
        decompiler._positions_by_offset[pass_offset] = (
            line,
            end_line,
            column,
            end_column + 1,
        )
    elif corruption == "wrong_indent":
        line, end_line, column, end_column = (
            decompiler._positions_by_offset[pass_offset]
        )
        decompiler._positions_by_offset[pass_offset] = (
            line,
            end_line,
            column + 8,
            end_column + 8,
        )
    else:
        decompiler._positions_by_offset[condition.false_endpoint] = (
            decompiler._positions_by_offset[pass_offset]
        )

    start = decompiler.offset_to_index[condition.entry_offset]
    assert (
        decompiler._bounded_condition_plan(start, len(decompiler.tokens))
        is None
    )
    assert (
        decompiler._terminal_empty_if_plan(
            condition,
            loop=None,
            region_end=len(decompiler.tokens),
        )
        is None
    )


@pytest.mark.parametrize(
    "corruption",
    (
        "outside_region",
        "loop_context",
        "class_body",
        "module_body",
        "generator",
        "coroutine",
        "async_generator",
        "missing_condition_block",
        "non_none_return",
        "extra_semantic_instruction",
        "missing_return",
        "overlapping_pairs",
        "normal_successor",
        "back_edge",
        "exception_edge",
        "foreign_predecessor",
        "unreachable_exit",
        "work_limit",
    ),
)
def test_terminal_empty_if_plan_rejects_unsafe_cfg_ownership(corruption):
    decompiler, condition = terminal_decompiler("terminal_empty_and")
    baseline = decompiler._terminal_empty_if_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert baseline is not None

    blocks = decompiler.cfg.blocks
    edges = decompiler.cfg.edges
    loop = None
    region_end = len(decompiler.tokens)
    if corruption == "outside_region":
        region_end -= 1
    elif corruption == "loop_context":
        loop = SimpleNamespace()
    elif corruption == "class_body":
        decompiler.is_class_body = True
    elif corruption == "module_body":
        decompiler.code = decompiler.code.replace(co_name="<module>")
    elif corruption in ("generator", "coroutine", "async_generator"):
        flag = {
            "generator": 0x20,
            "coroutine": 0x80,
            "async_generator": 0x200,
        }[corruption]
        decompiler.code = decompiler.code.replace(
            co_flags=decompiler.code.co_flags | flag,
        )
    elif corruption == "missing_condition_block":
        jump_offset = decompiler.tokens[
            next(iter(condition.nodes.values())).jump_index
        ].offset
        decompiler.cfg.offset_to_block = {
            offset: block
            for offset, block in decompiler.cfg.offset_to_block.items()
            if offset != jump_offset
        }
    elif corruption in (
        "non_none_return",
        "extra_semantic_instruction",
        "missing_return",
    ):
        offset = min(baseline.owned_offsets)
        token = decompiler.tokens[decompiler.offset_to_index[offset]]
        if corruption == "non_none_return":
            assert token.kind == "LOAD_CONST"
            token.attr = 1
        elif corruption == "extra_semantic_instruction":
            assert token.kind == "LOAD_CONST"
            token.kind = "LOAD_FAST"
            token.attr = "unexpected"
        else:
            return_offset = offset + 2
            returned = decompiler.tokens[
                decompiler.offset_to_index[return_offset]
            ]
            assert returned.kind == "RETURN_VALUE"
            returned.kind = "NOP"
    elif corruption == "overlapping_pairs":
        owned = sorted(baseline.owned_offsets)
        first_block = decompiler.cfg.offset_to_block[owned[0]]
        decompiler.cfg.offset_to_block = dict(decompiler.cfg.offset_to_block)
        decompiler.cfg.offset_to_block[owned[-2]] = first_block
        decompiler.cfg.offset_to_block[owned[-1]] = first_block
    elif corruption in ("normal_successor", "foreign_predecessor"):
        extra_index = len(blocks)
        extra_start = max(block.end for block in blocks) + 2
        extra = BasicBlock(
            index=extra_index,
            start=extra_start,
            end=extra_start + 2,
            instructions=(FakeInstruction(extra_start, "NOP"),),
        )
        blocks = (*blocks, extra)
        if corruption == "normal_successor":
            edges = tuple(
                sorted(
                    (*edges, Edge(max(baseline.exit_blocks), extra_index, "jump"))
                )
            )
        else:
            edges = tuple(
                sorted(
                    (*edges, Edge(extra_index, min(baseline.exit_blocks), "jump"))
                )
            )
    elif corruption in ("back_edge", "exception_edge"):
        source = max(baseline.exit_blocks)
        kind = "jump" if corruption == "back_edge" else "exception"
        edges = tuple(
            sorted((*edges, Edge(source, decompiler.cfg.entry, kind)))
        )
    elif corruption == "unreachable_exit":
        orphan = max(baseline.exit_blocks)
        edges = tuple(edge for edge in edges if edge.target != orphan)
    elif corruption == "work_limit":
        forward = min(baseline.exit_blocks)
        edges = tuple(
            sorted(
                (
                    *edges,
                    *(
                        Edge(decompiler.cfg.entry, forward, "jump")
                        for _ in range(300)
                    ),
                )
            )
        )

    decompiler.cfg = ControlFlowGraph(
        blocks=blocks,
        edges=edges,
        entry=decompiler.cfg.entry,
        offset_to_block=decompiler.cfg.offset_to_block,
    )
    assert (
        decompiler._terminal_empty_if_plan(
            condition,
            loop=loop,
            region_end=region_end,
        )
        is None
    )


def test_terminal_no_else_epilogues_preserve_behavior_and_short_circuiting():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_epilogue_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_epilogue_recovered",
    )

    cases = (
        ("terminal_and_no_else", (False, False)),
        ("terminal_and_no_else", (False, True)),
        ("terminal_and_no_else", (True, False)),
        ("terminal_and_no_else", (True, True)),
        ("terminal_or_no_else", (False, False)),
        ("terminal_or_no_else", (False, True)),
        ("terminal_or_no_else", (True, False)),
        ("terminal_or_no_else", (True, True)),
    )
    for name, arguments in cases:
        original_events = []
        recovered_events = []
        assert original[name](*arguments, original_events) is None
        assert recovered[name](*arguments, recovered_events) is None
        assert recovered_events == original_events

    for arguments in (
        (False, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ):
        for name in (
            "terminal_many_and_no_else",
            "terminal_mixed_no_else",
        ):
            original_events = []
            recovered_events = []
            original_result = original[name](*arguments, original_events)
            recovered_result = recovered[name](*arguments, recovered_events)
            assert recovered_result is original_result is None
            assert recovered_events == original_events

        original_events = []
        recovered_events = []
        original_result = original["terminal_nested_no_else"](
            *arguments,
            original_events,
        )
        recovered_result = recovered["terminal_nested_no_else"](
            *arguments,
            recovered_events,
        )
        assert recovered_result is original_result is None
        assert recovered_events == original_events

    for flag in (False, True):
        original_events = []
        recovered_events = []
        original["terminal_not_no_else"](flag, original_events)
        recovered["terminal_not_no_else"](flag, recovered_events)
        assert recovered_events == original_events

    for arguments in (
        (False, True, True, True),
        (True, False, True, True),
        (True, True, False, True),
        (True, True, True, False),
        (True, True, True, True),
    ):
        original_events = []
        recovered_events = []
        original["terminal_nested_short_circuit_no_else"](
            *arguments,
            original_events,
        )
        recovered["terminal_nested_short_circuit_no_else"](
            *arguments,
            recovered_events,
        )
        assert recovered_events == original_events

    for left in (False, True):
        for right in (False, True):
            original_events = []
            recovered_events = []
            original["terminal_before_no_else"](
                left,
                right,
                original_events,
            )
            recovered["terminal_before_no_else"](
                left,
                right,
                recovered_events,
            )
            assert recovered_events == original_events


def test_terminal_no_else_mixed_condition_preserves_bool_call_order():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_epilogue_calls_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_epilogue_calls_recovered",
    )

    class BoolProbe:
        def __init__(self, value, calls, label):
            self.value = value
            self.calls = calls
            self.label = label

        def __bool__(self):
            self.calls.append(self.label)
            return self.value

    for values in (
        (False, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ):
        original_calls = []
        recovered_calls = []
        original_events = []
        recovered_events = []
        original_arguments = tuple(
            BoolProbe(value, original_calls, label)
            for value, label in zip(values, ("first", "second", "third"))
        )
        recovered_arguments = tuple(
            BoolProbe(value, recovered_calls, label)
            for value, label in zip(values, ("first", "second", "third"))
        )
        original["terminal_mixed_no_else"](
            *original_arguments,
            original_events,
        )
        recovered["terminal_mixed_no_else"](
            *recovered_arguments,
            recovered_events,
        )
        assert recovered_calls == original_calls
        assert recovered_events == original_events


def test_terminal_no_else_epilogue_preserves_condition_and_suite_errors():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "terminal_epilogue_errors_original",
    )
    recovered = execute(
        recover_terminal_source(),
        "terminal_epilogue_errors_recovered",
    )

    class RaisingBool:
        def __bool__(self):
            raise LookupError("condition")

    class RaisingEvents:
        def append(self, value):
            raise RuntimeError("suite")

    for namespace in (original, recovered):
        with pytest.raises(LookupError, match="condition"):
            namespace["terminal_and_no_else"](
                RaisingBool(),
                True,
                [],
            )
        with pytest.raises(RuntimeError, match="suite"):
            namespace["terminal_and_no_else"](
                True,
                True,
                RaisingEvents(),
            )


def test_condition_extension_does_not_absorb_an_independent_terminal_if():
    original = execute(
        TERMINAL_SOURCE.read_text(encoding="utf-8"),
        "independent_terminal_if_original",
    )
    recovered_text = recover_terminal_source()
    recovered = execute(
        recovered_text,
        "independent_terminal_if_recovered",
    )
    function = next(
        node
        for node in ast.parse(recovered_text).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "independent_terminal_ifs"
    )

    assert len(function.body) == 2
    assert all(isinstance(statement, ast.If) for statement in function.body)
    for first in (False, True):
        for second in (False, True):
            original_events = []
            recovered_events = []
            original["independent_terminal_ifs"](
                first,
                second,
                original_events,
            )
            recovered["independent_terminal_ifs"](
                first,
                second,
                recovered_events,
            )
            assert recovered_events == original_events


def test_condition_extension_rejects_an_exception_predecessor():
    decompiler, condition = terminal_decompiler("terminal_mixed_no_else")
    extension_offset = max(condition.nodes)
    extension_block = decompiler.cfg.offset_to_block[extension_offset]
    source_block = min(
        decompiler.cfg.offset_to_block[
            decompiler.tokens[node.jump_index].offset
        ]
        for offset, node in condition.nodes.items()
        if offset != extension_offset
    )
    decompiler.cfg = ControlFlowGraph(
        blocks=decompiler.cfg.blocks,
        edges=tuple(
            sorted(
                (
                    *decompiler.cfg.edges,
                    Edge(source_block, extension_block, "exception"),
                )
            )
        ),
        entry=decompiler.cfg.entry,
        offset_to_block=decompiler.cfg.offset_to_block,
    )
    start = decompiler.offset_to_index[condition.entry_offset]
    replanned = decompiler._bounded_condition_plan(
        start,
        len(decompiler.tokens),
    )

    assert replanned is not None
    assert extension_offset not in replanned.nodes


@pytest.mark.parametrize(
    ("name", "exit_count"),
    (
        ("plain_terminal_if", 2),
        ("terminal_and_no_else", 3),
        ("terminal_or_no_else", 2),
        ("terminal_nested_no_else", 4),
        ("terminal_nested_short_circuit_no_else", 5),
        ("terminal_many_and_no_else", 4),
        ("terminal_mixed_no_else", 2),
        ("terminal_not_no_else", 2),
    ),
)
def test_implicit_return_epilogue_plan_owns_every_physical_exit(
    name,
    exit_count,
):
    decompiler, condition = terminal_decompiler(name)
    plan = decompiler._implicit_return_epilogue_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )

    assert plan is not None
    assert len(plan.exit_blocks) == exit_count
    assert len(plan.owned_offsets) == exit_count * 2
    assert plan.condition_blocks
    assert all(
        decompiler.tokens[decompiler.offset_to_index[offset]].kind
        in ("LOAD_CONST", "RETURN_VALUE")
        for offset in plan.owned_offsets
    )


@pytest.mark.parametrize(
    "name",
    ("early_return", "terminal_mixed_return", "terminal_if_else"),
)
def test_implicit_return_epilogue_plan_rejects_real_control_transfers(name):
    decompiler, condition = terminal_decompiler(name)
    assert (
        decompiler._implicit_return_epilogue_plan(
            condition,
            loop=None,
            region_end=len(decompiler.tokens),
        )
        is None
    )


@pytest.mark.parametrize(
    "corruption",
    (
        "outside_region",
        "missing_endpoint",
        "middle_endpoint",
        "missing_condition_block",
        "loop_context",
        "class_body",
        "module_body",
        "generator",
        "coroutine",
        "async_generator",
        "non_none_return",
        "extra_semantic_instruction",
        "missing_return",
        "overlapping_pairs",
        "normal_successor",
        "back_edge",
        "exception_edge",
        "foreign_predecessor",
        "unreachable_exit",
        "work_limit",
    ),
)
def test_implicit_return_epilogue_plan_rejects_unsafe_ownership(corruption):
    decompiler, condition = terminal_decompiler("terminal_and_no_else")
    baseline = decompiler._implicit_return_epilogue_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert baseline is not None

    blocks = decompiler.cfg.blocks
    edges = decompiler.cfg.edges
    loop = None
    region_end = len(decompiler.tokens)
    if corruption == "outside_region":
        region_end -= 1
    elif corruption == "missing_endpoint":
        condition = replace(
            condition,
            endpoints=(condition.endpoints[0], max(decompiler.offset_to_index) + 2),
        )
    elif corruption == "middle_endpoint":
        condition = replace(
            condition,
            endpoints=(condition.endpoints[0] + 2, condition.endpoints[1]),
        )
    elif corruption == "missing_condition_block":
        jump_offset = decompiler.tokens[
            next(iter(condition.nodes.values())).jump_index
        ].offset
        decompiler.cfg.offset_to_block = {
            offset: block
            for offset, block in decompiler.cfg.offset_to_block.items()
            if offset != jump_offset
        }
    elif corruption == "loop_context":
        loop = SimpleNamespace()
    elif corruption == "class_body":
        decompiler.is_class_body = True
    elif corruption == "module_body":
        decompiler.code = decompiler.code.replace(co_name="<module>")
    elif corruption in ("generator", "coroutine", "async_generator"):
        flag = {
            "generator": 0x20,
            "coroutine": 0x80,
            "async_generator": 0x200,
        }[corruption]
        decompiler.code = decompiler.code.replace(
            co_flags=decompiler.code.co_flags | flag,
        )
    elif corruption in (
        "non_none_return",
        "extra_semantic_instruction",
        "missing_return",
    ):
        owned = sorted(baseline.owned_offsets)
        offset = owned[-1] if corruption == "missing_return" else owned[0]
        token = decompiler.tokens[decompiler.offset_to_index[offset]]
        if corruption == "non_none_return":
            assert token.kind == "LOAD_CONST"
            token.attr = 1
        elif corruption == "extra_semantic_instruction":
            assert token.kind == "LOAD_CONST"
            token.kind = "LOAD_FAST"
            token.attr = "unexpected"
        else:
            assert token.kind == "RETURN_VALUE"
            token.kind = "NOP"
    elif corruption == "overlapping_pairs":
        owned = sorted(baseline.owned_offsets)
        first_block = decompiler.cfg.offset_to_block[owned[0]]
        decompiler.cfg.offset_to_block = dict(decompiler.cfg.offset_to_block)
        decompiler.cfg.offset_to_block[owned[-2]] = first_block
        decompiler.cfg.offset_to_block[owned[-1]] = first_block
    elif corruption == "normal_successor":
        extra_index = len(blocks)
        extra_start = max(block.end for block in blocks) + 2
        extra = BasicBlock(
            index=extra_index,
            start=extra_start,
            end=extra_start + 2,
            instructions=(FakeInstruction(extra_start, "NOP"),),
        )
        blocks = (*blocks, extra)
        edges = tuple(
            sorted(
                (*edges, Edge(max(baseline.exit_blocks), extra_index, "jump"))
            )
        )
    elif corruption in ("back_edge", "exception_edge"):
        exit_block = max(baseline.exit_blocks)
        kind = "jump" if corruption == "back_edge" else "exception"
        edges = tuple(
            sorted((*edges, Edge(exit_block, decompiler.cfg.entry, kind)))
        )
    elif corruption == "foreign_predecessor":
        extra_index = len(blocks)
        extra_start = max(block.end for block in blocks) + 2
        extra = BasicBlock(
            index=extra_index,
            start=extra_start,
            end=extra_start + 2,
            instructions=(FakeInstruction(extra_start, "NOP"),),
        )
        blocks = (*blocks, extra)
        edges = tuple(
            sorted(
                (*edges, Edge(extra_index, min(baseline.exit_blocks), "jump"))
            )
        )
    elif corruption == "unreachable_exit":
        orphan = max(baseline.exit_blocks)
        edges = tuple(edge for edge in edges if edge.target != orphan)
    elif corruption == "work_limit":
        forward = min(baseline.exit_blocks)
        edges = tuple(
            sorted(
                (
                    *edges,
                    *(
                        Edge(decompiler.cfg.entry, forward, "jump")
                        for _ in range(300)
                    ),
                )
            )
        )

    decompiler.cfg = ControlFlowGraph(
        blocks=blocks,
        edges=edges,
        entry=decompiler.cfg.entry,
        offset_to_block=decompiler.cfg.offset_to_block,
    )
    assert (
        decompiler._implicit_return_epilogue_plan(
            condition,
            loop=loop,
            region_end=region_end,
        )
        is None
    )


def test_terminal_if_plan_uses_two_disjoint_returning_cfg_regions():
    decompiler, condition = terminal_decompiler()
    plan = decompiler._terminal_if_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )

    assert plan is not None
    assert plan.body_exit_kinds == frozenset({"RETURN_VALUE"})
    assert plan.orelse_exit_kinds == frozenset({"RETURN_VALUE"})
    assert not plan.body_is_implicit_return_only
    assert not plan.orelse_is_implicit_return_only


def test_terminal_if_plan_preserves_endpoint_polarity_and_canonical_join():
    decompiler, condition = terminal_decompiler()
    reversed_condition = replace(
        condition,
        true_endpoint=condition.false_endpoint,
        false_endpoint=condition.true_endpoint,
    )
    reversed_plan = decompiler._terminal_if_plan(
        reversed_condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert reversed_plan is not None
    assert isinstance(reversed_plan.test, ast.UnaryOp)
    assert isinstance(reversed_plan.test.op, ast.Not)

    joined, joined_condition = terminal_decompiler("joined_if_else")
    assert (
        joined._terminal_if_plan(
            joined_condition,
            loop=None,
            region_end=len(joined.tokens),
        )
        is None
    )


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_endpoint",
        "outside_region",
        "missing_condition_block",
        "loop_context",
        "cross_branch",
        "foreign_predecessor",
        "back_edge",
        "exception_edge",
        "unterminated_body",
        "reachable_after_return",
        "work_limit",
    ),
)
def test_terminal_if_plan_rejects_unsafe_cfg_ownership(corruption):
    decompiler, condition = terminal_decompiler()
    true_block = decompiler.cfg.offset_to_block[condition.true_endpoint]
    false_block = decompiler.cfg.offset_to_block[condition.false_endpoint]
    blocks = decompiler.cfg.blocks
    edges = decompiler.cfg.edges
    loop = None
    region_end = len(decompiler.tokens)

    if corruption == "missing_endpoint":
        condition = replace(
            condition,
            true_endpoint=max(decompiler.offset_to_index) + 2,
        )
    elif corruption == "outside_region":
        region_end = max(
            decompiler.offset_to_index[condition.true_endpoint],
            decompiler.offset_to_index[condition.false_endpoint],
        )
    elif corruption == "missing_condition_block":
        jump_offset = decompiler.tokens[
            next(iter(condition.nodes.values())).jump_index
        ].offset
        decompiler.cfg.offset_to_block = {
            offset: block
            for offset, block in decompiler.cfg.offset_to_block.items()
            if offset != jump_offset
        }
    elif corruption == "loop_context":
        loop = SimpleNamespace()
    elif corruption == "cross_branch":
        edges = tuple(sorted((*edges, Edge(true_block, false_block, "jump"))))
    elif corruption == "foreign_predecessor":
        edges = tuple(sorted((*edges, Edge(false_block, true_block, "jump"))))
    elif corruption == "back_edge":
        edges = tuple(sorted((*edges, Edge(true_block, true_block, "jump"))))
    elif corruption == "exception_edge":
        edges = tuple(
            sorted((*edges, Edge(true_block, false_block, "exception")))
        )
    elif corruption == "unterminated_body":
        body = blocks[true_block]
        instructions = body.instructions[:-1] + (
            FakeInstruction(body.instructions[-1].offset, "NOP"),
        )
        blocks = tuple(
            replace(block, instructions=instructions)
            if block.index == true_block
            else block
            for block in blocks
        )
    elif corruption == "reachable_after_return":
        body = blocks[true_block]
        extra_index = len(blocks)
        extra = BasicBlock(
            index=extra_index,
            start=body.instructions[-1].offset,
            end=body.end,
            instructions=(body.instructions[-1],),
        )
        blocks = (*blocks, extra)
        edges = tuple(
            sorted((*edges, Edge(true_block, extra_index, "fallthrough")))
        )
    elif corruption == "work_limit":
        edges = tuple(
            sorted(
                (*edges, *(Edge(true_block, true_block, "jump") for _ in range(64)))
            )
        )

    decompiler.cfg = ControlFlowGraph(
        blocks=blocks,
        edges=edges,
        entry=decompiler.cfg.entry,
        offset_to_block=decompiler.cfg.offset_to_block,
    )
    assert (
        decompiler._terminal_if_plan(
            condition,
            loop=loop,
            region_end=region_end,
        )
        is None
    )


def test_terminal_if_corrupt_implicit_return_stack_fails_closed():
    decompiler, condition = terminal_decompiler("plain_terminal_if")
    plan = decompiler._terminal_if_plan(
        condition,
        loop=None,
        region_end=len(decompiler.tokens),
    )
    assert plan is not None
    assert plan.orelse_is_implicit_return_only

    corrupt_offset = decompiler.tokens[plan.orelse_start].offset
    decompiler.tokens[plan.orelse_start].kind = "POP_TOP"
    with pytest.raises(
        Python311ParseError,
        match=r"Operand stack underflow \(opcode POP_TOP\)",
    ) as raised:
        decompiler.decompile_body()

    assert raised.value.code_name == "plain_terminal_if"
    assert raised.value.offset == corrupt_offset
    assert raised.value.version == (3, 11)
