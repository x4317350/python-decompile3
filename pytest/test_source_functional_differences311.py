"""Semantic regressions found by the Python 3.11/Python 2.7 source audit."""

from __future__ import annotations

import ast
import io
import itertools
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 control-flow shapes",
)


SOURCE = r'''
def terminal_try(size, should_fail, event):
    try:
        event("try")
        if should_fail:
            raise RuntimeError("failed")
        if size >= 10:
            event("large")
            return True
        event("small")
        return False
    except RuntimeError:
        event("except")
        return False


def missing_npk(
    res_exists,
    res_size,
    script_exists,
    script_size,
    clean,
    tail,
    error,
):
    try:
        if res_exists() == False and res_size > 0 or \
                script_exists() == False and script_size > 0:
            clean()
            return
    except Exception:
        error()
    tail()


def active_window(begin, now, cycle_end, total_end, mark):
    if not (
        mark("begin", begin)
        <= mark("now", now)
        < mark("cycle", cycle_end)
        and mark("cycle_total", cycle_end)
        <= mark("total", total_end)
    ):
        return None
    return (begin, cycle_end)


def try_else_boundary(work, callback, recover):
    try:
        work()
    except LookupError:
        recover("work")
        return "caught"
    else:
        return callback()


def condition_then_nested(spec, spec_set, original, mark):
    if spec is not None or spec_set is not None:
        if original:
            mark("inner")
    mark("tail")
'''


def execute(code, name):
    namespace = {"__name__": name}
    exec(code, namespace)
    return namespace


@pytest.fixture(scope="module")
def programs():
    original_root = compile(SOURCE, "<functional-differences311-original>", "exec")
    output = io.StringIO()
    code_deparse(
        original_root,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered_source = output.getvalue()
    tree = ast.parse(recovered_source)
    rebuilt_root = compile(
        tree,
        "<functional-differences311-recovered>",
        "exec",
    )
    return (
        tree,
        recovered_source,
        execute(original_root, "functional_differences311_original"),
        execute(rebuilt_root, "functional_differences311_recovered"),
    )


def function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def capture(operation, events):
    try:
        value = operation()
    except BaseException as error:
        return "error", type(error), str(error), events
    return "return", value, type(value), events


def terminal_outcome(function, size, should_fail):
    events = []
    result = capture(
        lambda: function(size, should_fail, events.append),
        events,
    )
    return result


def test_try_terminal_boolean_frontier_keeps_both_returns(programs):
    tree, _, original, rebuilt = programs
    terminal = function_node(tree, "terminal_try")
    statement = next(node for node in terminal.body if isinstance(node, ast.Try))
    assert not statement.orelse

    cases = (
        (0, False, ("return", False, bool, ["try", "small"])),
        (10, False, ("return", True, bool, ["try", "large"])),
        (
            10,
            True,
            ("return", False, bool, ["try", "except"]),
        ),
    )
    for size, should_fail, expected in cases:
        assert terminal_outcome(
            original["terminal_try"],
            size,
            should_fail,
        ) == terminal_outcome(
            rebuilt["terminal_try"],
            size,
            should_fail,
        ) == expected


def missing_outcome(function, values, sizes, failure=None):
    events = []

    def exists(name):
        def check():
            events.append(name)
            if failure == name:
                raise LookupError(name)
            return values[name]

        return check

    def clean():
        events.append("clean")
        if failure == "clean":
            raise RuntimeError("clean")

    def tail():
        events.append("tail")

    def error():
        events.append("error")

    return capture(
        lambda: function(
            exists("res"),
            sizes[0],
            exists("script"),
            sizes[1],
            clean,
            tail,
            error,
        ),
        events,
    )


def test_multiline_or_inside_try_keeps_error_block_conditional(programs):
    _, _, original, rebuilt = programs
    for booleans in itertools.product((False, True), repeat=2):
        values = dict(zip(("res", "script"), booleans))
        for sizes in itertools.product((0, 1), repeat=2):
            assert missing_outcome(
                original["missing_npk"],
                values,
                sizes,
            ) == missing_outcome(
                rebuilt["missing_npk"],
                values,
                sizes,
            )

    for failure in ("res", "script", "clean"):
        values = {"res": False, "script": False}
        assert missing_outcome(
            original["missing_npk"],
            values,
            (1, 1),
            failure,
        ) == missing_outcome(
            rebuilt["missing_npk"],
            values,
            (1, 1),
            failure,
        )

    both_present = {"res": True, "script": True}
    assert missing_outcome(
        rebuilt["missing_npk"],
        both_present,
        (1, 1),
    )[-1] == ["res", "script", "tail"]


def active_outcome(function, values):
    events = []

    def mark(name, value):
        events.append(name)
        return value

    return capture(lambda: function(*values, mark), events)


def test_chained_comparison_keeps_final_conjunct_and_tuple_return(programs):
    tree, _, original, rebuilt = programs
    function = function_node(tree, "active_window")
    assert isinstance(function.body[-1], ast.Return)
    assert isinstance(function.body[-1].value, ast.Tuple)

    cases = (
        ((0, 1, 2, 3), (0, 2)),
        ((0, -1, 2, 3), None),
        ((0, 3, 2, 4), None),
        ((0, 1, 4, 3), None),
    )
    for values, expected in cases:
        original_outcome = active_outcome(original["active_window"], values)
        rebuilt_outcome = active_outcome(rebuilt["active_window"], values)
        assert original_outcome == rebuilt_outcome
        assert rebuilt_outcome[1] == expected


def boundary_outcome(function, work_failure=None, callback_failure=None):
    events = []

    def work():
        events.append("work")
        if work_failure is not None:
            raise work_failure("work")

    def callback():
        events.append("callback")
        if callback_failure is not None:
            raise callback_failure("callback")
        return "callback-result"

    def recover(label):
        events.append(("recover", label))

    return capture(lambda: function(work, callback, recover), events)


def test_real_try_else_keeps_exception_boundary(programs):
    tree, _, original, rebuilt = programs
    function = function_node(tree, "try_else_boundary")
    statement = next(node for node in function.body if isinstance(node, ast.Try))
    assert statement.orelse

    cases = (
        (None, None),
        (LookupError, None),
        (None, RuntimeError),
    )
    for work_failure, callback_failure in cases:
        assert boundary_outcome(
            original["try_else_boundary"],
            work_failure,
            callback_failure,
        ) == boundary_outcome(
            rebuilt["try_else_boundary"],
            work_failure,
            callback_failure,
        )


def test_condition_extension_does_not_absorb_nested_suite(programs):
    tree, _, original, rebuilt = programs
    function = function_node(tree, "condition_then_nested")
    outer = next(node for node in function.body if isinstance(node, ast.If))
    assert any(isinstance(node, ast.If) for node in outer.body)

    for spec, spec_set, original_value in itertools.product(
        (None, object()),
        (None, object()),
        (False, True),
    ):
        original_events = []
        rebuilt_events = []
        original["condition_then_nested"](
            spec,
            spec_set,
            original_value,
            original_events.append,
        )
        rebuilt["condition_then_nested"](
            spec,
            spec_set,
            original_value,
            rebuilt_events.append,
        )
        assert original_events == rebuilt_events
