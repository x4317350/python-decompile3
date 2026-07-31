"""Phase 4 acceptance tests for CPython 3.11 control-flow recovery."""

from __future__ import annotations

import ast
import io
import sys
from dataclasses import dataclass
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
from decompyle3.parsers.main import python_parser
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


SOURCE = ROOT / "test" / "simple_source" / "311" / "02_control_flow.py"

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
