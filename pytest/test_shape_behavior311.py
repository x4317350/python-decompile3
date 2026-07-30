"""Phase 6 differential behavior coverage for CPython 3.11 shapes."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

import pytest

from behavior_cases311 import (
    FAIL_CLOSED_SHAPE_SOURCES,
    FIXTURE_PROBES,
    INLINE_SHAPE_PROBES,
    INLINE_SHAPE_SOURCES,
)
from decompyle3.controlflow import (
    BasicBlock,
    ControlFlowGraph,
    Edge,
    IrreducibleControlFlowError,
    analyze_control_flow,
)
from decompyle3.parsers.p311.base import (
    Python311ParseError,
    UnsupportedPython311ControlFlow,
)
from support311 import (
    ROOT,
    compare_behavior311,
    compile_behavior_pyc,
    recover_behavior_source,
)


SHAPE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "shape_matrix.json"
)
SHAPE_TEST_NODE = (
    "pytest/test_shape_behavior311.py::"
    "test_each_shape_has_differential_behavior_contract"
)
SHAPE_MATRIX = json.loads(
    SHAPE_MATRIX_PATH.read_text(encoding="utf-8")
)
SHAPE_ITEMS = tuple(SHAPE_MATRIX["shapes"])

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 shape behavior tests require CPython 3.11",
)


@dataclass(frozen=True)
class FakeInstruction:
    offset: int
    kind: str = "NOP"


def assert_irreducible_shape_fails_closed():
    instructions = tuple(
        FakeInstruction(offset) for offset in (0, 2, 4)
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
        IrreducibleControlFlowError,
        match="multiple entries: B1, B2",
    ):
        analyze_control_flow(graph)


def assert_source_shape_fails_closed(shape_name, tmp_path):
    source = tmp_path / f"{shape_name}.py"
    source.write_text(
        FAIL_CLOSED_SHAPE_SOURCES[shape_name],
        encoding="utf-8",
    )
    code, compile_mode = compile_behavior_pyc(
        source,
        tmp_path / f"{shape_name}.pyc",
    )
    expected_errors = {
        "except_star_with_else": UnsupportedPython311ControlFlow,
        "except_star_with_finally": UnsupportedPython311ControlFlow,
        "compound_assert_condition": Python311ParseError,
    }
    with pytest.raises(expected_errors[shape_name]) as raised:
        recover_behavior_source(code, compile_mode)
    assert raised.value.version == (3, 11)
    assert isinstance(raised.value.code_name, str)
    assert isinstance(raised.value.offset, int)


@pytest.mark.parametrize(
    "shape_name",
    [item["name"] for item in SHAPE_ITEMS],
)
def test_each_shape_has_differential_behavior_contract(
    shape_name,
    tmp_path,
):
    item = next(
        item for item in SHAPE_ITEMS if item["name"] == shape_name
    )

    assert len(SHAPE_ITEMS) == 31
    assert item["status"] in ("pass", "unsupported_fail_closed")
    assert SHAPE_TEST_NODE in item["tests"]

    if item["status"] == "unsupported_fail_closed":
        if shape_name == "irreducible_control_flow":
            assert_irreducible_shape_fails_closed()
        else:
            assert_source_shape_fails_closed(shape_name, tmp_path)
        return

    if item["fixture"] is not None:
        source = ROOT / item["fixture"]
        probe = FIXTURE_PROBES[item["fixture"]]
    else:
        source = tmp_path / f"{shape_name}.py"
        source.write_text(
            INLINE_SHAPE_SOURCES[shape_name],
            encoding="utf-8",
        )
        probe = INLINE_SHAPE_PROBES[shape_name]

    comparison = compare_behavior311(
        source,
        probe,
        tmp_path / f"{shape_name}-artifacts",
        shape_name=shape_name,
    )
    assert comparison.original.exitcode == 0
    assert comparison.recovered.exitcode == 0
