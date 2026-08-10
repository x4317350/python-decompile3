"""Dynamic regressions for terminal for-else and while latch ownership."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 terminal loop shapes",
)


SOURCE = r"""
def terminal_for_else(items, value, events):
    for item in items:
        if value > item:
            continue
        events.append(("hit", item))
        break
    else:
        events.append(
            ("fallback", items[-1] if items else None)
        )


def branched_terminal_for_else(items, choose_left, events):
    for item in items:
        if item < 0:
            continue
        if choose_left:
            events.append(("left", item))
        else:
            events.append(("right", item))
        break
    else:
        events.append(("fallback", None))


def explicit_return_for_else(items, events):
    for item in items:
        if item:
            events.append(("return", item))
            return None
    else:
        events.append(("fallback", None))
    events.append(("after", None))


def terminal_for_break(items, events):
    for item in items:
        events.append(("hit", item))
        break


def drain_list(enabled, items, callback):
    if enabled:
        while len(items) > 0:
            callback(items.pop(0))


def drain_two(first, second, callback):
    while first:
        item = first.pop()
        if item:
            callback(("first", item))
    while second:
        item = second.pop()
        if item:
            callback(("second", item))
"""


def execute(code, name):
    namespace = {"__name__": name}
    exec(code, namespace)
    return namespace


@pytest.fixture(scope="module")
def programs():
    original_root = compile(SOURCE, "<terminal-loops311-original>", "exec")
    output = io.StringIO()
    code_deparse(
        original_root,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered_source = output.getvalue()
    recovered_tree = ast.parse(recovered_source)
    rebuilt_root = compile(
        recovered_tree,
        "<terminal-loops311-recovered>",
        "exec",
    )
    return (
        recovered_tree,
        recovered_source,
        execute(original_root, "terminal_loops311_original"),
        execute(rebuilt_root, "terminal_loops311_recovered"),
    )


def function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def call_outcome(function, *args):
    try:
        value = function(*args)
    except BaseException as error:
        return "error", type(error), str(error)
    return "return", value, type(value)


def terminal_for_else_outcome(function, items, value):
    events = []
    result = call_outcome(function, list(items), value, events)
    return result, tuple(events)


def test_terminal_for_else_keeps_hit_and_exhaustion_paths_disjoint(programs):
    tree, _, original, recovered = programs
    function = function_node(tree, "terminal_for_else")
    loop = next(node for node in function.body if isinstance(node, ast.For))
    assert loop.orelse
    assert any(isinstance(node, ast.Break) for node in ast.walk(loop))

    for items, value in (([3, 4], 2), ([1, 3, 4], 2), ([1, 2], 3), ([], 3)):
        assert terminal_for_else_outcome(
            recovered["terminal_for_else"], items, value
        ) == terminal_for_else_outcome(original["terminal_for_else"], items, value)


def test_branched_terminal_for_else_owns_jump_and_fallthrough_cleanup(programs):
    tree, _, original, recovered = programs
    function = function_node(tree, "branched_terminal_for_else")
    loop = next(node for node in function.body if isinstance(node, ast.For))
    assert loop.orelse
    assert any(isinstance(node, ast.Break) for node in ast.walk(loop))

    for items, choose_left in (
        ([1], True),
        ([1], False),
        ([-1, 2], True),
        ([-1], False),
        ([], True),
    ):
        original_events = []
        recovered_events = []
        assert call_outcome(
            recovered["branched_terminal_for_else"],
            list(items),
            choose_left,
            recovered_events,
        ) == call_outcome(
            original["branched_terminal_for_else"],
            list(items),
            choose_left,
            original_events,
        )
        assert recovered_events == original_events


def test_explicit_return_and_plain_break_remain_distinct(programs):
    tree, _, original, recovered = programs
    explicit = function_node(tree, "explicit_return_for_else")
    explicit_loop = next(node for node in explicit.body if isinstance(node, ast.For))
    assert any(isinstance(node, ast.Return) for node in ast.walk(explicit_loop))
    assert not any(isinstance(node, ast.Break) for node in ast.walk(explicit_loop))

    plain = function_node(tree, "terminal_for_break")
    plain_loop = next(node for node in plain.body if isinstance(node, ast.For))
    assert not plain_loop.orelse
    assert any(isinstance(node, ast.Break) for node in ast.walk(plain_loop))

    for name, cases in (
        ("explicit_return_for_else", (([1],), ([0],), ([],))),
        ("terminal_for_break", (([1, 2],), ([],))),
    ):
        for arguments in cases:
            original_events = []
            recovered_events = []
            assert call_outcome(
                recovered[name], *arguments, recovered_events
            ) == call_outcome(original[name], *arguments, original_events)
            assert recovered_events == original_events


def drain_list_outcome(function, enabled, initial):
    items = list(initial)
    events = []

    def callback(item):
        events.append(item)
        if item == "first":
            items.append("extra")

    result = call_outcome(function, enabled, items, callback)
    return result, tuple(events), tuple(items)


def test_terminal_while_latch_is_owned_once(programs):
    tree, _, original, recovered = programs
    function = function_node(tree, "drain_list")
    assert sum(isinstance(node, ast.While) for node in ast.walk(function)) == 1
    assert not any(isinstance(node, ast.Break) for node in ast.walk(function))

    for enabled, items in ((True, ["first"]), (True, []), (False, ["first"])):
        assert drain_list_outcome(
            recovered["drain_list"], enabled, items
        ) == drain_list_outcome(original["drain_list"], enabled, items)


def drain_two_outcome(function, first, second):
    left = list(first)
    right = list(second)
    events = []
    result = call_outcome(function, left, right, events.append)
    return result, tuple(events), tuple(left), tuple(right)


def test_sequential_terminal_whiles_are_not_duplicated(programs):
    tree, _, original, recovered = programs
    function = function_node(tree, "drain_two")
    assert sum(isinstance(node, ast.While) for node in ast.walk(function)) == 2
    assert not any(isinstance(node, ast.Break) for node in ast.walk(function))

    for first, second in (([0, 1, 2], [3, 0, 4]), ([], [1]), ([], [])):
        assert drain_two_outcome(
            recovered["drain_two"], first, second
        ) == drain_two_outcome(original["drain_two"], first, second)
