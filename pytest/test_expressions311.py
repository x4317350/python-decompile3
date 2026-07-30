"""CPython 3.11 expression-CFG and comprehension-filter regressions."""

from __future__ import annotations

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
