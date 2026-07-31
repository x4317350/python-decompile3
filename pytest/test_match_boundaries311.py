"""Stage 9 acceptance tests for CPython 3.11 match case boundaries."""

from __future__ import annotations

import ast
import io
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow.cfg import build_cfg
from decompyle3.controlflow.dominators import analyze_control_flow
from decompyle3.controlflow.match_structures import (
    MatchStructureDecompiler311,
)
from decompyle3.parsers.p311.base import Python311ParseError
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "18_match_boundaries.py"
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Stage 9 match tests require CPython 3.11",
)


@dataclass(frozen=True)
class FakeInstruction:
    offset: int
    kind: str
    target: int | None = None
    attr: int | None = None
    linestart: int | None = None


def recover_source(tmp_path):
    bytecode = tmp_path / "18_match_boundaries.pyc"
    version, _, _, code, implementation, *_ = compile_source(
        SOURCE,
        bytecode,
    )
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


def outcome(namespace, function, *args):
    events = []
    try:
        value = namespace[function](*args, events)
    except Exception as error:
        return "raise", type(error).__name__, str(error), tuple(events)
    return "return", value, tuple(events)


def behavior(namespace):
    return (
        tuple(
            outcome(namespace, "boundary", value)
            for value in (0, 2, None, "stop", "other")
        ),
        tuple(
            outcome(namespace, "nested_boundary", value)
            for value in (("outer", 1), ("outer", 2), "other")
        ),
        tuple(
            outcome(namespace, "refutable_fallthrough", value)
            for value in ("hit", "miss")
        ),
        tuple(
            outcome(namespace, "conditional_exit", value, flag)
            for value, flag in (
                ("mixed", True),
                ("mixed", False),
                ("raise", True),
                ("raise", False),
                ("other", True),
            )
        ),
    )


def test_case_boundaries_reparse_recompile_and_preserve_nested_ast(tmp_path):
    recovered = recover_source(tmp_path)
    tree = ast.parse(recovered)
    compile(tree, "<stage9-match-recovered>", "exec")

    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]
    assert len(matches) == 5
    assert any(
        isinstance(statement, ast.If)
        for match in matches
        for case in match.cases
        for statement in case.body
    )
    assert any(
        isinstance(statement, ast.Match)
        for match in matches
        for case in match.cases
        for statement in case.body
    )

    top_level_matches = [
        node for node in tree.body if isinstance(node, ast.Match)
    ]
    assert not top_level_matches


def test_guard_fallthrough_return_raise_and_nesting_preserve_behavior(
    tmp_path,
):
    original = execute(
        SOURCE.read_text(encoding="utf-8"),
        "stage9_match_original",
    )
    recovered = execute(
        recover_source(tmp_path),
        "stage9_match_recovered",
    )

    assert behavior(recovered) == behavior(original)
    assert recovered["PAX_NUMBER_FIELDS"] == original["PAX_NUMBER_FIELDS"]
    assert recovered["ENCODING"] == original["ENCODING"]


def test_ambiguous_case_exit_targets_fail_closed():
    tokens = (
        FakeInstruction(
            0,
            "POP_JUMP_FORWARD_IF_FALSE",
            target=6,
            attr=6,
        ),
        FakeInstruction(2, "JUMP_FORWARD", target=10, attr=10),
        FakeInstruction(4, "NOP"),
        FakeInstruction(6, "JUMP_FORWARD", target=12, attr=12),
        FakeInstruction(8, "POP_TOP"),
        FakeInstruction(10, "RETURN_VALUE"),
        FakeInstruction(12, "RETURN_VALUE"),
    )
    graph = build_cfg(tokens)
    owner = SimpleNamespace(
        tokens=tokens,
        offset_to_index={
            token.offset: index for index, token in enumerate(tokens)
        },
        cfg=graph,
        control_flow=analyze_control_flow(graph),
        current_token=tokens[0],
        code=SimpleNamespace(co_name="ambiguous_match"),
    )
    matcher = MatchStructureDecompiler311(owner)

    with pytest.raises(
        Python311ParseError,
        match="ambiguous exit targets",
    ):
        matcher._body_end(0, len(tokens), failure_index=4)
