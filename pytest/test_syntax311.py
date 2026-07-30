"""Phase 7 acceptance tests for CPython 3.11-only syntax."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


MATCH_SOURCE = ROOT / "test" / "simple_source" / "311" / "06_match.py"
GROUP_SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "07_exception_group.py"
)
EXCEPTION_SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "05_exceptions_with.py"
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Phase 7 syntax tests require CPython 3.11",
)


def recover_source(source, tmp_path):
    bytecode = tmp_path / f"{source.stem}.pyc"
    version, _, _, code, implementation, *_ = compile_source(
        source,
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


def function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def exception_shape(error):
    if isinstance(error, BaseExceptionGroup):
        return (
            type(error).__name__,
            error.message,
            tuple(exception_shape(child) for child in error.exceptions),
        )
    return type(error).__name__, str(error)


def leaf_exception_names(shape):
    if len(shape) == 2:
        return [shape[0]]
    names = []
    for child in shape[2]:
        names.extend(leaf_exception_names(child))
    return names


def split_outcome(namespace, group):
    try:
        return "returned", namespace["split_group"](group)
    except BaseException as error:
        return "raised", exception_shape(error)


def test_phase7_protocol_opcodes_are_present():
    kinds = set()
    for source in (MATCH_SOURCE, GROUP_SOURCE):
        root = compile(
            source.read_text(encoding="utf-8"),
            str(source),
            "exec",
        )
        for code in Scanner311.iter_code_objects(root):
            scanner = Scanner311()
            scanner.ingest(code)
            kinds.update(
                instruction.kind
                for instruction in scanner.normalized_instructions
            )

    assert {
        "CHECK_EG_MATCH",
        "MATCH_CLASS",
        "MATCH_KEYS",
        "MATCH_MAPPING",
        "MATCH_SEQUENCE",
        "PREP_RERAISE_STAR",
    } <= kinds


@pytest.mark.parametrize("source", [MATCH_SOURCE, GROUP_SOURCE])
def test_phase7_pyc_deparses_reparses_and_recompiles(source, tmp_path):
    recovered = recover_source(source, tmp_path)
    tree = ast.parse(recovered)
    compile(tree, f"<recovered-{source.stem}>", "exec")


def test_recovered_match_has_every_planned_pattern_form(tmp_path):
    tree = ast.parse(recover_source(MATCH_SOURCE, tmp_path))
    nodes = list(ast.walk(tree))

    assert any(isinstance(node, ast.Match) for node in nodes)
    assert any(isinstance(node, ast.MatchValue) for node in nodes)
    assert any(isinstance(node, ast.MatchSingleton) for node in nodes)
    assert any(isinstance(node, ast.MatchSequence) for node in nodes)
    assert any(isinstance(node, ast.MatchMapping) for node in nodes)
    assert any(isinstance(node, ast.MatchClass) for node in nodes)
    assert any(isinstance(node, ast.MatchOr) for node in nodes)
    assert any(isinstance(node, ast.MatchStar) for node in nodes)
    assert any(
        isinstance(node, ast.MatchAs) and node.name == "captured"
        for node in nodes
    )
    assert any(
        isinstance(node, ast.MatchAs)
        and node.name is None
        and node.pattern is None
        for node in nodes
    )
    assert any(
        isinstance(node, ast.match_case) and node.guard is not None
        for node in nodes
    )

    nested = function_node(tree, "nested_describe")
    nested_case = next(
        node for node in ast.walk(nested) if isinstance(node, ast.match_case)
    )
    assert isinstance(nested_case.pattern, ast.MatchMapping)
    assert any(
        isinstance(node, ast.MatchSequence)
        for node in ast.walk(nested_case.pattern)
    )
    assert any(
        isinstance(node, ast.MatchClass)
        for node in ast.walk(nested_case.pattern)
    )


def test_recovered_except_star_uses_trystar_and_keeps_normal_try_distinct(
    tmp_path,
):
    group_tree = ast.parse(recover_source(GROUP_SOURCE, tmp_path))
    group_nodes = list(ast.walk(group_tree))
    stars = [node for node in group_nodes if isinstance(node, ast.TryStar)]

    assert stars
    assert any(len(node.handlers) == 2 for node in stars)
    assert any(len(node.handlers) == 1 for node in stars)
    assert any(handler.name == "errors" for node in stars for handler in node.handlers)
    assert any(handler.name is None for node in stars for handler in node.handlers)
    assert "except*" in recover_source(GROUP_SOURCE, tmp_path)

    normal_tree = ast.parse(recover_source(EXCEPTION_SOURCE, tmp_path))
    assert any(isinstance(node, ast.Try) for node in ast.walk(normal_tree))
    assert not any(
        isinstance(node, ast.TryStar) for node in ast.walk(normal_tree)
    )


def match_behavior(namespace):
    point = namespace["Point"]
    describe_values = [
        None,
        0,
        1,
        [3, 4],
        [3, 4, 5],
        {"kind": "point", "x": 7, "y": 8},
        complex(2, 3),
        "other",
    ]
    described = [
        namespace["describe"](value) for value in describe_values
    ]
    nested = [
        namespace["nested_describe"](
            {"payload": [5, point(0, 9)]}
        ),
        namespace["nested_describe"](
            {"payload": [5, "not-a-point"]}
        ),
        namespace["nested_describe"](
            {"kind": "event", "extra": 12}
        ),
        namespace["nested_describe"]("captured"),
    ]
    collected = [
        namespace["collect_description"](1),
        namespace["collect_description"](2),
    ]
    return described, nested, collected


def test_recovered_match_preserves_bindings_guards_and_behavior(tmp_path):
    original = execute(
        MATCH_SOURCE.read_text(encoding="utf-8"),
        "phase7_match_original",
    )
    recovered = execute(
        recover_source(MATCH_SOURCE, tmp_path),
        "phase7_match_recovered",
    )

    assert match_behavior(recovered) == match_behavior(original)


def test_recovered_except_star_preserves_splitting_and_reraise(tmp_path):
    original = execute(
        GROUP_SOURCE.read_text(encoding="utf-8"),
        "phase7_group_original",
    )
    recovered = execute(
        recover_source(GROUP_SOURCE, tmp_path),
        "phase7_group_recovered",
    )

    assert recovered["handle_group"]() == original["handle_group"]()
    all_values_original = ExceptionGroup(
        "values",
        [ValueError("one"), ValueError("two")],
    )
    all_values_recovered = ExceptionGroup(
        "values",
        [ValueError("one"), ValueError("two")],
    )
    assert recovered["mark_values"](all_values_recovered) == original[
        "mark_values"
    ](all_values_original)
    assert split_outcome(
        recovered,
        ExceptionGroup(
            "values",
            [ValueError("one"), ValueError("two")],
        ),
    ) == split_outcome(
        original,
        ExceptionGroup(
            "values",
            [ValueError("one"), ValueError("two")],
        ),
    )
    assert recovered["split_group"](None) == original["split_group"](None)

    recovered_outcome = split_outcome(
        recovered,
        ExceptionGroup(
            "root",
            [
                ValueError("outer value"),
                ExceptionGroup(
                    "nested",
                    [TypeError("type"), ValueError("inner value")],
                ),
            ],
        ),
    )
    original_outcome = split_outcome(
        original,
        ExceptionGroup(
            "root",
            [
                ValueError("outer value"),
                ExceptionGroup(
                    "nested",
                    [TypeError("type"), ValueError("inner value")],
                ),
            ],
        ),
    )
    assert recovered_outcome == original_outcome
    assert recovered_outcome[0] == "raised"
    assert leaf_exception_names(recovered_outcome[1]) == ["TypeError"]
