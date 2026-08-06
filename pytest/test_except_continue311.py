"""Semantic regressions for CPython 3.11 except loop transfers."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 exception tables",
)


SOURCE = """
def parse_keys(items, accepted):
    for key in items:
        try:
            value = int(key)
        except:
            continue
        accepted(value)
    return "done"


def parse_nested(groups, accepted):
    for group in groups:
        for key in group:
            try:
                value = int(key)
            except:
                continue
            accepted(value)
    return "nested"


def break_on_invalid(items, accepted):
    for key in items:
        try:
            value = int(key)
        except:
            break
        accepted(value)
    return "stopped"


def typed_continue(items, accepted):
    for key in items:
        try:
            value = int(key)
        except ValueError:
            continue
        accepted(value)
    return "typed"


def finally_continue(items, accepted):
    for key in items:
        try:
            accepted(("try", key))
        finally:
            continue
    return "finally"
"""


class InvalidInteger:
    def __init__(self, message="conversion failed"):
        self.message = message

    def __int__(self):
        raise RuntimeError(self.message)


def recover():
    original = compile(SOURCE, "<except-continue311-original>", "exec")
    output = io.StringIO()
    code_deparse(
        original,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered = output.getvalue()
    tree = ast.parse(recovered)
    rebuilt = compile(tree, "<except-continue311-recovered>", "exec")
    return original, recovered, tree, rebuilt


def execute(code, name):
    namespace = {"__name__": name}
    exec(code, namespace)
    return namespace


def function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def first_try(function: ast.FunctionDef) -> ast.Try:
    return next(node for node in ast.walk(function) if isinstance(node, ast.Try))


def assert_except_transfer(
    tree: ast.Module,
    function_name: str,
    transfer_type,
    exception_name=None,
):
    statement = first_try(function_node(tree, function_name))
    assert len(statement.handlers) == 1
    assert statement.finalbody == []
    handler = statement.handlers[0]
    if exception_name is None:
        assert handler.type is None
    else:
        assert isinstance(handler.type, ast.Name)
        assert handler.type.id == exception_name
    assert len(handler.body) == 1
    assert isinstance(handler.body[0], transfer_type)


def code_metadata(root, name):
    code = next(
        item
        for item in Scanner311.iter_code_objects(root)
        if item.co_name == name
    )
    return (
        code.co_argcount,
        code.co_posonlyargcount,
        code.co_kwonlyargcount,
        code.co_freevars,
        code.co_cellvars,
    )


def outcome(function, items, fail_on=None):
    events = []

    def accepted(value):
        events.append(value)
        if value == fail_on:
            raise LookupError(f"callback failed: {value!r}")

    try:
        result = function(items, accepted)
    except Exception as error:
        return "error", type(error), str(error), events
    return "return", result, type(result), events


def assert_same_outcome(original, rebuilt, items, fail_on=None):
    original_result = outcome(original, items, fail_on)
    rebuilt_result = outcome(rebuilt, items, fail_on)
    assert rebuilt_result == original_result
    return original_result


def test_bare_except_continue_recovers_handler_and_dynamic_semantics():
    original_root, recovered_source, tree, rebuilt_root = recover()
    original = execute(original_root, "except_continue311_original")
    rebuilt = execute(rebuilt_root, "except_continue311_rebuilt")

    assert recovered_source
    assert_except_transfer(tree, "parse_keys", ast.Continue)
    assert code_metadata(original_root, "parse_keys") == code_metadata(
        rebuilt_root,
        "parse_keys",
    )

    cases = (
        (["1", "invalid", "2"], [1, 2]),
        (["invalid", "3"], [3]),
        (["invalid-1", "invalid-2"], []),
        ([InvalidInteger(), "4"], [4]),
    )
    for items, expected_events in cases:
        result = assert_same_outcome(
            original["parse_keys"],
            rebuilt["parse_keys"],
            items,
        )
        assert result == (
            "return",
            "done",
            str,
            expected_events,
        )

    callback_error = assert_same_outcome(
        original["parse_keys"],
        rebuilt["parse_keys"],
        ["1", "2"],
        fail_on=2,
    )
    assert callback_error == (
        "error",
        LookupError,
        "callback failed: 2",
        [1, 2],
    )


def test_handler_transfer_classification_preserves_related_structures():
    original_root, _, tree, rebuilt_root = recover()
    original = execute(original_root, "except_transfer311_original")
    rebuilt = execute(rebuilt_root, "except_transfer311_rebuilt")

    assert_except_transfer(tree, "parse_nested", ast.Continue)
    assert_except_transfer(tree, "break_on_invalid", ast.Break)
    assert_except_transfer(
        tree,
        "typed_continue",
        ast.Continue,
        exception_name="ValueError",
    )

    final_try = first_try(function_node(tree, "finally_continue"))
    assert final_try.handlers == []
    assert any(
        isinstance(node, ast.Continue)
        for statement in final_try.finalbody
        for node in ast.walk(statement)
    )

    for name in (
        "parse_nested",
        "break_on_invalid",
        "typed_continue",
        "finally_continue",
    ):
        assert code_metadata(original_root, name) == code_metadata(
            rebuilt_root,
            name,
        )

    nested = assert_same_outcome(
        original["parse_nested"],
        rebuilt["parse_nested"],
        [["1", "bad", "2"], [InvalidInteger(), "3"]],
    )
    assert nested == ("return", "nested", str, [1, 2, 3])

    breaking = assert_same_outcome(
        original["break_on_invalid"],
        rebuilt["break_on_invalid"],
        ["1", "bad", "2"],
    )
    assert breaking == ("return", "stopped", str, [1])

    typed_value_error = assert_same_outcome(
        original["typed_continue"],
        rebuilt["typed_continue"],
        ["1", "bad", "2"],
    )
    assert typed_value_error == ("return", "typed", str, [1, 2])
    typed_runtime_error = assert_same_outcome(
        original["typed_continue"],
        rebuilt["typed_continue"],
        [InvalidInteger("typed runtime"), "2"],
    )
    assert typed_runtime_error == (
        "error",
        RuntimeError,
        "typed runtime",
        [],
    )

    finally_result = assert_same_outcome(
        original["finally_continue"],
        rebuilt["finally_continue"],
        ["first", "second"],
        fail_on=("try", "first"),
    )
    assert finally_result == (
        "return",
        "finally",
        str,
        [("try", "first"), ("try", "second")],
    )
