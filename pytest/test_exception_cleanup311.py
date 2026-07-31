"""CPython 3.11 exception-cleanup control-transfer regressions."""

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
    reason="Parser311 exception-cleanup tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "12_exception_cleanup.py"
)


def deparse_exec(source: str) -> str:
    output = io.StringIO()
    deparsed = code_deparse(
        compile(source, str(SOURCE), "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert deparsed.text == output.getvalue()
    return deparsed.text


def execute_exec(source: str, name: str):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def fixture_namespaces():
    source = SOURCE.read_text(encoding="utf-8")
    recovered = deparse_exec(source)
    tree = ast.parse(recovered, filename="<recovered-exception-cleanup-311>")
    compile(tree, "<recovered-exception-cleanup-311>", "exec")
    return (
        tree,
        execute_exec(source, "original_exception_cleanup_311"),
        execute_exec(recovered, "recovered_exception_cleanup_311"),
    )


def capture_call(function, *arguments):
    try:
        value = function(*arguments)
    except BaseException as error:
        cause = error.__cause__
        return (
            "raise",
            type(error).__name__,
            error.args,
            None
            if cause is None
            else (type(cause).__name__, cause.args),
        )
    return "return", type(value).__name__, value


def drive_generator(function, fail):
    events = []
    generator = function(events, fail)
    try:
        value = next(generator)
    except BaseException as error:
        outcome = ("raise", type(error).__name__, error.args)
    else:
        outcome = ("yield", value)
        generator.close()
    return outcome, events


def test_exception_cleanup_protocol_is_structured_before_expression_stack():
    root = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
    )
    code_objects = {
        code.co_name: code for code in Scanner311.iter_code_objects(root)
    }
    scanner = Scanner311()
    scanner.ingest(code_objects["return_from_handler"])
    kinds = {
        instruction.kind for instruction in scanner.normalized_instructions
    }

    assert {
        "COPY_STACK",
        "POP_EXCEPT",
        "PUSH_EXC_INFO",
        "RERAISE",
        "SWAP_STACK",
    } <= kinds

    tree, _, _ = fixture_namespaces()
    assert sum(isinstance(node, ast.Try) for node in ast.walk(tree)) >= 8


def test_handler_return_raise_from_and_bare_reraise_preserve_behavior():
    _, original, recovered = fixture_namespaces()

    for value in (None, False, 0, "", "payload", (1, 2)):
        assert capture_call(
            recovered["return_from_handler"],
            value,
        ) == capture_call(original["return_from_handler"], value)

    for value in ("7", "invalid", None):
        original_events = []
        recovered_events = []
        assert capture_call(
            recovered["translate_error"],
            value,
            recovered_events,
        ) == capture_call(
            original["translate_error"],
            value,
            original_events,
        )
        assert recovered_events == original_events

    original_events = []
    recovered_events = []
    assert capture_call(
        recovered["reraised_error"],
        recovered_events,
    ) == capture_call(
        original["reraised_error"],
        original_events,
    )
    assert recovered_events == original_events == ["handler"]


def test_nested_handlers_and_finally_side_effect_order_are_preserved():
    _, original, recovered = fixture_namespaces()

    for value in (None, 0, "value", [1, 2]):
        assert capture_call(
            recovered["nested_handler_return"],
            value,
        ) == capture_call(original["nested_handler_return"], value)

    for fail in (False, True):
        original_events = []
        recovered_events = []
        assert capture_call(
            recovered["nested_finally"],
            recovered_events,
            fail,
        ) == capture_call(
            original["nested_finally"],
            original_events,
            fail,
        )
        assert recovered_events == original_events == [
            "body",
            "inner",
            "outer",
        ]


def test_generator_cleanup_control_transfers_preserve_behavior():
    _, original, recovered = fixture_namespaces()

    for fail in (False, True):
        assert drive_generator(
            recovered["cleanup_generator"],
            fail,
        ) == drive_generator(original["cleanup_generator"], fail)


def test_handler_break_from_while_true_preserves_behavior():
    _, original, recovered = fixture_namespaces()

    for values in ([], [1], [1, 2], [1, None, 3]):
        assert capture_call(
            recovered["handler_break"],
            values,
        ) == capture_call(original["handler_break"], values)
