"""Regressions for factoring common suffixes in Python 3.11 conditions."""

from __future__ import annotations

import ast
import io
import itertools
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow.structures import (
    _factor_ifexp_common_and_suffix,
)
from decompyle3.semantics.pysource import code_deparse

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 condition decisions",
)


SOURCE = r"""
def choose(model, force_use_high_peishi, replace, show):
    if (show(model) or force_use_high_peishi) and len(replace) == 2:
        return replace[1]
    return replace[0]
"""


def recover(source: str) -> str:
    output = io.StringIO()
    code_deparse(
        compile(source, "<conditional-factoring311-original>", "exec"),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def execute(source: str, name: str):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def programs():
    recovered_source = recover(SOURCE)
    recovered_tree = ast.parse(recovered_source)
    compile(recovered_tree, "<conditional-factoring311-recompiled>", "exec")
    return (
        recovered_source,
        recovered_tree,
        execute(SOURCE, "conditional_factoring311_original"),
        execute(recovered_source, "conditional_factoring311_recovered"),
    )


def test_common_and_suffix_is_factored_in_statement_condition(programs):
    recovered_source, tree, _, _ = programs
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "choose"
    )
    condition = next(
        node.test for node in ast.walk(function) if isinstance(node, ast.If)
    )

    assert isinstance(condition, ast.BoolOp)
    assert isinstance(condition.op, ast.And)
    assert len(condition.values) == 2
    guard, suffix = condition.values
    assert isinstance(guard, ast.BoolOp)
    assert isinstance(guard.op, ast.Or)
    assert [ast.unparse(value) for value in guard.values] == [
        "show(model)",
        "force_use_high_peishi",
    ]
    assert ast.unparse(suffix) == "len(replace) == 2"
    assert not any(isinstance(node, ast.IfExp) for node in ast.walk(condition))
    assert "(show(model) or force_use_high_peishi) and len(replace) == 2" in (
        recovered_source
    )


class TruthValue:
    def __init__(self, label, truth, events, failure=None):
        self.label = label
        self.truth = truth
        self.events = events
        self.failure = failure

    def __bool__(self):
        self.events.append(f"truth:{self.label}")
        if self.failure == f"truth:{self.label}":
            raise RuntimeError(f"truth:{self.label}")
        return self.truth


class ReplaceValue:
    def __init__(self, length, events, failure=None):
        self.length = length
        self.events = events
        self.failure = failure
        self.items = [object(), object()]

    def __len__(self):
        self.events.append("len")
        if self.failure == "len":
            raise RuntimeError("len")
        return self.length

    def __getitem__(self, index):
        self.events.append(f"getitem:{index}")
        return self.items[index]


def choose_outcome(function, a, b, c, failure=None):
    events = []
    replace = ReplaceValue(2 if c else 1, events, failure=failure)
    force = TruthValue("b", b, events, failure=failure)

    def show(model):
        events.append("show")
        if failure == "show":
            raise RuntimeError("show")
        return TruthValue("a", a, events, failure=failure)

    try:
        result = function(object(), force, replace, show)
    except BaseException as error:
        return "error", type(error), str(error), tuple(events)
    index = next(index for index, item in enumerate(replace.items) if result is item)
    return "return", index, type(result), tuple(events)


def test_factored_condition_preserves_truth_table_and_event_order(programs):
    _, _, original, recovered = programs
    for a, b, c in itertools.product((False, True), repeat=3):
        original_outcome = choose_outcome(original["choose"], a, b, c)
        recovered_outcome = choose_outcome(recovered["choose"], a, b, c)
        assert recovered_outcome == original_outcome

        expected_index = 1 if (a or b) and c else 0
        assert original_outcome[0:2] == ("return", expected_index)
        if a:
            expected_events = ("show", "truth:a", "len")
        elif b:
            expected_events = ("show", "truth:a", "truth:b", "len")
        else:
            expected_events = ("show", "truth:a", "truth:b")
        assert original_outcome[-1] == expected_events + (f"getitem:{expected_index}",)


@pytest.mark.parametrize(
    ("a", "b", "failure"),
    (
        (True, True, "show"),
        (True, True, "truth:a"),
        (False, True, "truth:b"),
        (True, False, "len"),
        (False, True, "len"),
    ),
)
def test_factored_condition_preserves_exception_frontier(
    programs,
    a,
    b,
    failure,
):
    _, _, original, recovered = programs
    original_outcome = choose_outcome(
        original["choose"],
        a,
        b,
        True,
        failure=failure,
    )
    recovered_outcome = choose_outcome(
        recovered["choose"],
        a,
        b,
        True,
        failure=failure,
    )
    assert recovered_outcome == original_outcome
    assert original_outcome[0:3] == ("error", RuntimeError, failure)


def expression(source: str) -> ast.expr:
    return ast.parse(source, mode="eval").body


def compile_expression_function(value: ast.expr, name: str):
    function = ast.FunctionDef(
        name=name,
        args=ast.arguments(
            posonlyargs=[],
            args=[
                ast.arg(arg="a"),
                ast.arg(arg="b"),
                ast.arg(arg="e"),
                ast.arg(arg="events"),
            ],
            vararg=None,
            kwonlyargs=[],
            kw_defaults=[],
            kwarg=None,
            defaults=[],
        ),
        body=[ast.Return(value=value)],
        decorator_list=[],
    )
    tree = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))

    def probe(events, label, result):
        events.append(label)
        return result

    namespace = {"probe": probe}
    exec(compile(tree, f"<{name}>", "exec"), namespace)
    return namespace[name]


def value_expressions():
    original = expression(
        "probe(events, 'e', e) if probe(events, 'a', a) "
        "else probe(events, 'b', b) and probe(events, 'e', e)"
    )
    assert isinstance(original, ast.IfExp)
    factored = _factor_ifexp_common_and_suffix(
        original.test,
        original.body,
        original.orelse,
    )
    assert factored is not None
    return original, factored


def test_factoring_preserves_non_boolean_result_identity():
    original_expression, factored_expression = value_expressions()
    original = compile_expression_function(original_expression, "original")
    factored = compile_expression_function(factored_expression, "factored")

    class CustomTruth:
        def __init__(self, truth):
            self.truth = truth

        def __bool__(self):
            return self.truth

    cases = (
        (CustomTruth(True), object(), object(), "e", ("a", "e")),
        (
            CustomTruth(False),
            CustomTruth(False),
            object(),
            "b",
            ("a", "b"),
        ),
        (
            CustomTruth(False),
            CustomTruth(True),
            object(),
            "e",
            ("a", "b", "e"),
        ),
        (CustomTruth(True), object(), False, "e", ("a", "e")),
        (
            CustomTruth(False),
            CustomTruth(True),
            False,
            "e",
            ("a", "b", "e"),
        ),
    )
    for a, b, e, expected_name, expected_events in cases:
        original_events = []
        factored_events = []
        original_result = original(a, b, e, original_events)
        factored_result = factored(a, b, e, factored_events)
        expected = {"a": a, "b": b, "e": e}[expected_name]

        assert original_result is expected
        assert factored_result is expected
        assert tuple(original_events) == tuple(factored_events) == expected_events


@pytest.mark.parametrize(
    "source",
    (
        "x if a else b and y",
        "x if a else b or x",
        "x if a else x",
        "x if a else x and b",
        "call(1) if a else b and call(2)",
    ),
)
def test_non_matching_ifexp_is_not_factored(source):
    candidate = expression(source)
    assert isinstance(candidate, ast.IfExp)
    assert (
        _factor_ifexp_common_and_suffix(
            candidate.test,
            candidate.body,
            candidate.orelse,
        )
        is None
    )


def test_multiple_alternate_guards_keep_order():
    candidate = expression("e if a else b and c and e")
    assert isinstance(candidate, ast.IfExp)
    factored = _factor_ifexp_common_and_suffix(
        candidate.test,
        candidate.body,
        candidate.orelse,
    )

    assert factored is not None
    assert ast.unparse(factored) == "(a or (b and c)) and e"
