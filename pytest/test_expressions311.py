"""CPython 3.11 expression-CFG and comprehension-filter regressions."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 expression tests require CPython 3.11",
)


def deparse_expression(source: str, mode="eval") -> str:
    output = io.StringIO()
    deparsed = code_deparse(
        compile(source + "\n", "<expression-311>", mode),
        out=output,
        version=(3, 11),
        compile_mode=mode,
        python_implementation=PythonImplementation.CPython,
    )
    assert deparsed.text == output.getvalue()
    return deparsed.text


def deparse_exec(source: str) -> str:
    output = io.StringIO()
    deparsed = code_deparse(
        compile(source + "\n", "<expression-exec-311>", "exec"),
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


def capture_call(function, value):
    try:
        result = function(value)
    except Exception as error:
        return "raise", type(error).__name__, str(error)
    return "return", type(result).__name__, result


@pytest.mark.parametrize(
    "source",
    (
        "a and b",
        "a or b",
        "(a and b) + 1",
        "a and (b or c)",
        "a and b or c",
        "(a or b) and c",
        "a == 1 or a == 2",
        "a < b < c",
        "a < b < c or b",
        "a < b < c and b",
        "b if a else c",
    ),
)
def test_eval_short_circuit_expressions_preserve_values(source):
    recovered = deparse_expression(source)
    compile(recovered, "<recovered-expression-311>", "eval")

    for a in (0, 1, 2):
        for b in (0, 3):
            for c in (0, 5):
                namespace = {"a": a, "b": b, "c": c}
                assert eval(recovered, namespace) == eval(source, namespace)


def test_exec_return_mixed_short_circuit_preserves_false_values():
    source = """
def make_apply(base):
    def apply(value):
        return (value and value + base) or base
    return apply
"""
    recovered = deparse_exec(source)
    compile(recovered, "<recovered-expression-exec-311>", "exec")
    original_namespace = execute_exec(source, "original_expression_exec_311")
    recovered_namespace = execute_exec(
        recovered,
        "recovered_expression_exec_311",
    )
    original = original_namespace["make_apply"](10)
    rebuilt = recovered_namespace["make_apply"](10)

    for value in (0, 5, -10, None, False, 0.0):
        assert capture_call(rebuilt, value) == capture_call(original, value)

    recovered_ast = ast.parse(recovered)
    assert not any(
        isinstance(node, ast.If)
        and len(node.body) == 1
        and isinstance(node.body[0], ast.Pass)
        for node in ast.walk(recovered_ast)
    )

    class FalseValue:
        def __init__(self):
            self.add_calls = 0

        def __bool__(self):
            return False

        def __add__(self, other):
            self.add_calls += 1
            raise AssertionError("false values must short-circuit addition")

    original_value = FalseValue()
    recovered_value = FalseValue()
    assert original(original_value) == 10
    assert rebuilt(recovered_value) == 10
    assert original_value.add_calls == 0
    assert recovered_value.add_calls == 0


def test_exec_explicit_if_return_keeps_statement_control_flow():
    source = """
def make_apply(base):
    def apply(value):
        if not value:
            return base
        result = value + base
        if not result:
            return base
        return result
    return apply
"""
    recovered = deparse_exec(source)
    original_namespace = execute_exec(source, "original_explicit_if_311")
    recovered_namespace = execute_exec(recovered, "recovered_explicit_if_311")
    original = original_namespace["make_apply"](10)
    rebuilt = recovered_namespace["make_apply"](10)

    for value in (0, 5, -10, None, False, 0.0):
        assert capture_call(rebuilt, value) == capture_call(original, value)

    module = ast.parse(recovered)
    apply_function = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "apply"
    )
    assert sum(isinstance(node, ast.If) for node in apply_function.body) == 2


def test_short_circuit_expressions_preserve_evaluation_order():
    source = "mark('a', a) and mark('b', b) or mark('c', c)"
    recovered = deparse_expression(source)

    for values in ((0, 1, 2), (1, 0, 2), (1, 3, 2)):
        original_events = []
        recovered_events = []

        def original_mark(name, value):
            original_events.append(name)
            return value

        def recovered_mark(name, value):
            recovered_events.append(name)
            return value

        a, b, c = values
        original = eval(
            source,
            {"mark": original_mark, "a": a, "b": b, "c": c},
        )
        rebuilt = eval(
            recovered,
            {"mark": recovered_mark, "a": a, "b": b, "c": c},
        )
        assert rebuilt == original
        assert recovered_events == original_events


def test_single_mode_uses_expression_cfg_instead_of_statement_if():
    source = "i and j or k"
    recovered = deparse_expression(source, mode="single")
    assert "if i:" not in recovered

    for i in (0, 1):
        for j in (0, 2):
            for k in (0, 3):
                namespace = {"i": i, "j": j, "k": k}
                assert eval(recovered, namespace) == eval(source, namespace)


@pytest.mark.parametrize(
    "source",
    (
        "(i for i in values if 0 < i < 4)",
        "[i for i in values if 0 < i < 4]",
        "{i for i in values if 0 < i < 4}",
        "{i: i * 2 for i in values if 0 < i < 4}",
    ),
)
def test_chained_comparison_comprehension_filters(source):
    recovered = deparse_expression(source, mode="single")
    namespace = {"values": [-2, 0, 1, 3, 4, 8]}
    original = eval(source, namespace)
    rebuilt = eval(recovered, namespace)

    if source.startswith("("):
        original = list(original)
        rebuilt = list(rebuilt)
    assert rebuilt == original
