"""Stage 11 convergence regressions for CPython 3.11 real-world shapes."""

from __future__ import annotations

import ast
import io
import sys
import sysconfig
from pathlib import Path

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Stage 11 convergence tests require CPython 3.11",
)

SOURCE = """
def conditional_guard(depth, root, driver, left, right):
    if (root or driver) if depth == 0 else left != right:
        return "selected"
    return "rejected"


def conditional_lambda(enabled, value):
    transform = str if enabled else (lambda item: item + 1)
    return transform(value)


def return_through_loop_finally(values, events):
    try:
        return values[0]
    finally:
        for value in values:
            events.append(("cleanup", value))


def protected_loop(values, events):
    try:
        index = 0
        while True:
            if index >= len(values):
                break
            events.append(values[index])
            index += 1
        return tuple(events)
    finally:
        events.append("done")
"""


def recover():
    output = io.StringIO()
    result = code_deparse(
        compile(SOURCE, "<stage11-convergence-311>", "exec"),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert result.text == output.getvalue()
    tree = ast.parse(result.text, filename="<recovered-stage11-311>")
    compile(tree, "<recovered-stage11-311>", "exec", dont_inherit=True)
    return result.text, tree


def namespaces():
    recovered, tree = recover()
    original = {"__name__": "stage11_original"}
    rebuilt = {"__name__": "stage11_rebuilt"}
    exec(compile(SOURCE, "<stage11-original>", "exec"), original)
    exec(compile(recovered, "<stage11-rebuilt>", "exec"), rebuilt)
    return tree, original, rebuilt


def outcome(function, *arguments):
    try:
        value = function(*arguments)
    except BaseException as error:
        return "raise", type(error).__name__, error.args
    return "return", value


def test_stage11_shapes_reparse_recompile_and_keep_structured_ast():
    tree, _, _ = namespaces()

    assert any(isinstance(node, ast.IfExp) for node in ast.walk(tree))
    assert any(isinstance(node, ast.Lambda) for node in ast.walk(tree))
    assert any(isinstance(node, ast.Break) for node in ast.walk(tree))
    assert sum(isinstance(node, ast.Try) for node in ast.walk(tree)) >= 2


def test_conditional_expression_and_lambda_values_preserve_behavior():
    _, original, rebuilt = namespaces()

    guard_arguments = (
        (0, None, "driver", 1, 1),
        (0, None, None, 1, 2),
        (1, "root", None, 1, 1),
        (1, None, None, 1, 2),
    )
    for arguments in guard_arguments:
        assert rebuilt["conditional_guard"](*arguments) == original[
            "conditional_guard"
        ](*arguments)

    for enabled, value in ((True, 3), (False, 3), (True, "x")):
        assert outcome(
            rebuilt["conditional_lambda"],
            enabled,
            value,
        ) == outcome(original["conditional_lambda"], enabled, value)


def test_return_values_survive_loop_finally_cleanup():
    _, original, rebuilt = namespaces()

    for values in ((0, 2, 3), (), (False, "")):
        original_events = []
        rebuilt_events = []
        assert outcome(
            rebuilt["return_through_loop_finally"],
            values,
            rebuilt_events,
        ) == outcome(
            original["return_through_loop_finally"],
            values,
            original_events,
        )
        assert rebuilt_events == original_events


def test_protected_loop_control_flow_preserves_behavior():
    _, original, rebuilt = namespaces()

    for values in ((), (1, 2, 3)):
        original_events = []
        rebuilt_events = []
        assert rebuilt["protected_loop"](
            values,
            rebuilt_events,
        ) == original["protected_loop"](values, original_events)
        assert rebuilt_events == original_events


def stage11_realworld_samples():
    stdlib = Path(sysconfig.get_path("stdlib"))
    purelib = Path(sysconfig.get_path("purelib"))
    return (
        stdlib / "pathlib.py",
        stdlib / "runpy.py",
        stdlib / "xml" / "etree" / "ElementTree.py",
        purelib / "_pytest" / "assertion" / "rewrite.py",
        purelib / "_pytest" / "config" / "__init__.py",
        ROOT / "decompyle3" / "controlflow" / "structures.py",
    )


@pytest.mark.parametrize(
    "source_path",
    stage11_realworld_samples(),
    ids=lambda path: path.name,
)
def test_previous_stage11_realworld_failures_recover(source_path):
    source = source_path.read_text(encoding="utf-8")
    output = io.StringIO()
    result = code_deparse(
        compile(source, str(source_path), "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert result.text == output.getvalue()
    tree = ast.parse(
        result.text,
        filename=f"<recovered-{source_path.name}>",
    )
    compile(
        tree,
        f"<recovered-{source_path.name}>",
        "exec",
        dont_inherit=True,
    )
