"""CPython 3.11 call and expression-stack regression coverage."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 call/expression tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "13_call_expression_stack.py"
)


def deparse(source: str) -> str:
    output = io.StringIO()
    result = code_deparse(
        compile(source, str(SOURCE), "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert result.text == output.getvalue()
    return result.text


def namespaces():
    source = SOURCE.read_text(encoding="utf-8")
    recovered = deparse(source)
    tree = ast.parse(recovered, filename="<call-expression-stack-311>")
    compile(tree, "<call-expression-stack-311>", "exec")
    original = {"__name__": "original_call_expression_stack_311"}
    rebuilt = {"__name__": "rebuilt_call_expression_stack_311"}
    exec(compile(source, str(SOURCE), "exec"), original)
    exec(compile(recovered, "<recovered-call-expression-311>", "exec"), rebuilt)
    return tree, original, rebuilt


def outcome(function, *args):
    try:
        value = function(*args)
    except BaseException as error:
        return "raise", type(error).__name__, error.args
    return "return", value


def test_scanner_and_ast_cover_stage4_protocols():
    root = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
        dont_inherit=True,
    )
    kinds = set()
    for code in Scanner311.iter_code_objects(root):
        scanner = Scanner311()
        scanner.ingest(code)
        kinds.update(
            instruction.kind
            for instruction in scanner.normalized_instructions
        )

    assert {
        "CALL",
        "COPY_STACK",
        "FORMAT_VALUE",
        "KW_NAMES",
        "POP_JUMP_FORWARD_IF_FALSE",
        "SWAP_STACK",
    } <= kinds

    tree, _, _ = namespaces()
    assert any(isinstance(node, ast.IfExp) for node in ast.walk(tree))
    assert any(isinstance(node, ast.JoinedStr) for node in ast.walk(tree))
    assert any(isinstance(node, ast.Lambda) for node in ast.walk(tree))


def test_calls_conditionals_and_comparison_chains_preserve_behavior():
    _, original, rebuilt = namespaces()

    for value in (0.0, 1.25, -4000.0):
        assert outcome(rebuilt["formatted"], value) == outcome(
            original["formatted"],
            value,
        )
    for left, right in ((1, 1), (2, 1), (1, 2)):
        assert outcome(rebuilt["nested_choice"], left, right) == outcome(
            original["nested_choice"],
            left,
            right,
        )
    for implementation, version in (
        ("cpython", (3, 11, 4)),
        ("cpython", (3, 11, 5)),
        ("pypy", (3, 11, 12)),
        ("other", (3, 10)),
        ("other", (3, 12)),
    ):
        arguments = (implementation, version, "old", "current")
        assert outcome(rebuilt["selected_pattern"], *arguments) == outcome(
            original["selected_pattern"],
            *arguments,
        )


def test_callback_and_argument_evaluation_order_are_preserved():
    _, original, rebuilt = namespaces()

    def register(callback):
        return callback()

    class Receiver:
        def method(self, positional, *, keyword):
            return positional, keyword

    for namespace in (original, rebuilt):
        events = []

        def mark(name, value):
            events.append(name)
            return value

        assert namespace["callback_argument"](register, 7) == 7
        assert namespace["ordered_call"](Receiver(), mark) == (1, 2)
        assert events == ["positional", "keyword"]


def test_loop_return_and_nested_cleanup_preserve_behavior():
    _, original, rebuilt = namespaces()

    for values in ([1, 2, 0, 4], [1, 2, 3], []):
        original_events = []
        rebuilt_events = []
        assert rebuilt["chain_loop"](
            values,
            rebuilt_events,
        ) == original["chain_loop"](values, original_events)
        assert rebuilt_events == original_events

    for fail in (False, True):
        def make_function():
            if fail:
                raise ValueError("failed")
            return "value"

        original_cleanup = []
        rebuilt_cleanup = []
        assert outcome(
            rebuilt["nested_finally_except"],
            make_function,
            lambda: rebuilt_cleanup.append("cleanup"),
        ) == outcome(
            original["nested_finally_except"],
            make_function,
            lambda: original_cleanup.append("cleanup"),
        )
        assert rebuilt_cleanup == original_cleanup == ["cleanup"]

    functions = [lambda: None, lambda: "found", lambda: "late"]
    original_cleanup = []
    rebuilt_cleanup = []
    assert rebuilt["loop_return_finally"](
        functions,
        lambda: rebuilt_cleanup.append("cleanup"),
    ) == original["loop_return_finally"](
        functions,
        lambda: original_cleanup.append("cleanup"),
    )
    assert rebuilt_cleanup == original_cleanup == ["cleanup"]
