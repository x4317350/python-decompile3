"""Stage 10 audit tests for the irreducible-CFG safety boundary."""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest

from decompyle3.controlflow import (
    BasicBlock,
    ControlFlowGraph,
    Edge,
    IrreducibleControlFlowError,
    analyze_control_flow,
    build_cfg,
    decode_exception_table,
)
from decompyle3.scanners.scanner311 import Scanner311
from support311 import corpus_sources


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Stage 10 CFG audit requires CPython 3.11",
)


@dataclass(frozen=True)
class FakeInstruction:
    offset: int
    kind: str = "NOP"


def manual_graph(block_count, edges, reachable=None):
    reachable = (
        set(range(block_count)) if reachable is None else set(reachable)
    )
    instructions = tuple(
        FakeInstruction(index * 2) for index in range(block_count)
    )
    blocks = tuple(
        BasicBlock(
            index,
            instruction.offset,
            instruction.offset + 2,
            (instruction,),
            index in reachable,
        )
        for index, instruction in enumerate(instructions)
    )
    return ControlFlowGraph(
        blocks=blocks,
        edges=tuple(edges),
        entry=0,
        offset_to_block={
            instruction.offset: index
            for index, instruction in enumerate(instructions)
        },
    )


def irreducible_graph(edges=None):
    return manual_graph(
        3,
        edges
        or (
            Edge(0, 1, "true"),
            Edge(0, 2, "false"),
            Edge(1, 2, "jump"),
            Edge(2, 1, "jump"),
        ),
    )


def test_irreducible_error_has_stable_component_and_entry_context():
    with pytest.raises(IrreducibleControlFlowError) as caught:
        analyze_control_flow(irreducible_graph())

    error = caught.value
    assert error.component_blocks == (1, 2)
    assert error.entry_blocks == (1, 2)
    assert error.entry_edges == (
        (0, 1, "true"),
        (0, 2, "false"),
    )
    assert str(error) == (
        "Irreducible control flow has multiple entries: B1, B2; "
        "component: B1, B2; "
        "entry edges: B0->B1:true, B0->B2:false"
    )


def test_irreducible_diagnostic_is_independent_of_edge_order():
    edges = irreducible_graph().edges
    messages = []
    for ordered in (edges, tuple(reversed(edges))):
        with pytest.raises(IrreducibleControlFlowError) as caught:
            analyze_control_flow(irreducible_graph(ordered))
        messages.append(str(caught.value))

    assert messages[0] == messages[1]


def test_dead_predecessor_does_not_create_a_false_second_entry():
    graph = manual_graph(
        4,
        (
            Edge(0, 1, "fallthrough"),
            Edge(1, 2, "jump"),
            Edge(2, 1, "jump"),
            Edge(3, 2, "jump"),
        ),
        reachable={0, 1, 2},
    )

    analysis = analyze_control_flow(graph)

    assert analysis.back_edges == ((2, 1),)
    assert analysis.loops[0].blocks == frozenset({1, 2})


def test_unreachable_irreducible_component_is_ignored():
    graph = manual_graph(
        5,
        (
            Edge(1, 3, "true"),
            Edge(2, 4, "false"),
            Edge(3, 4, "jump"),
            Edge(4, 3, "jump"),
        ),
        reachable={0},
    )

    analysis = analyze_control_flow(graph)

    assert analysis.dominators == {0: frozenset({0})}
    assert not analysis.loops


def test_reducible_loop_and_acyclic_merge_remain_accepted():
    graph = manual_graph(
        5,
        (
            Edge(0, 1, "true"),
            Edge(0, 3, "false"),
            Edge(1, 2, "fallthrough"),
            Edge(2, 1, "jump"),
            Edge(1, 3, "false"),
            Edge(2, 3, "false"),
            Edge(3, 4, "fallthrough"),
        ),
    )

    analysis = analyze_control_flow(graph)

    assert analysis.back_edges == ((2, 1),)
    assert analysis.loops[0].blocks == frozenset({1, 2})
    assert analysis.immediate_dominators[4] == 3


def test_large_irreducible_scc_fails_closed_without_python_recursion():
    component_size = 2500
    first = 2
    last = first + component_size - 1
    midpoint = first + component_size // 2
    edges = [
        Edge(0, 1, "false"),
        Edge(0, first, "true"),
        Edge(1, midpoint, "jump"),
    ]
    edges.extend(
        Edge(node, node + 1, "jump")
        for node in range(first, last)
    )
    edges.append(Edge(last, first, "jump"))
    graph = manual_graph(last + 1, edges)

    with pytest.raises(IrreducibleControlFlowError) as caught:
        analyze_control_flow(graph)

    assert caught.value.entry_blocks == (first, midpoint)
    assert len(caught.value.component_blocks) == component_size
    assert f"... (+{component_size - 16} more)" in str(caught.value)
    assert len(str(caught.value)) < 1000


def test_cpython311_corpus_contains_only_reducible_cfgs():
    analyzed = 0
    for source in corpus_sources():
        root = compile(
            source.read_text(encoding="utf-8"),
            str(source),
            "exec",
            dont_inherit=True,
        )
        for code in Scanner311.iter_code_objects(root):
            scanner = Scanner311()
            scanner.ingest(code)
            graph = build_cfg(
                scanner.normalized_instructions,
                decode_exception_table(code),
            )
            analyze_control_flow(graph)
            analyzed += 1

    assert analyzed >= 100
