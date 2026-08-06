"""Semantic regressions for CPython 3.11 terminal cleanup ownership."""

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
    reason="These regressions exercise CPython 3.11 terminal CFG exits",
)


SOURCE = """
def upload_error(kind, is_video, notify):
    try:
        raise RuntimeError("failed")
    except Exception:
        if kind == "screenshot":
            notify("share")
            return
        if not is_video:
            notify("image")
        else:
            notify("video")


def remove_first(items, target):
    index = 0
    for item in items:
        if item == target:
            del items[index]
            break
        index += 1


def visit_limited(groups, visit):
    count = 0
    for group in groups:
        for item in group:
            count += 1
            if count > 3:
                return
            visit(item)


def start_timer(items, is_active, has_timer, schedule):
    for item in items:
        if is_active(item):
            if has_timer():
                return
            schedule(item)
            return


def compact_guard(stop, action):
    if stop: return
    action()
"""


@pytest.fixture(scope="module")
def programs():
    original_root = compile(
        SOURCE,
        "<terminal-cleanup311-original>",
        "exec",
    )
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
        "<terminal-cleanup311-recovered>",
        "exec",
    )
    original = execute(original_root, "terminal_cleanup311_original")
    rebuilt = execute(rebuilt_root, "terminal_cleanup311_rebuilt")
    return original_root, rebuilt_root, tree, original, rebuilt


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


def capture(call, events):
    try:
        result = call()
    except Exception as error:
        return "error", type(error), str(error), events
    return "return", result, type(result), events


def upload_outcome(function, kind, is_video, failure=None):
    events = []

    def notify(message):
        events.append(message)
        if message == failure:
            raise LookupError(f"notify failed: {message}")

    return capture(lambda: function(kind, is_video, notify), events)


def remove_outcome(function, values, target):
    items = list(values)
    events = []

    def run():
        result = function(items, target)
        events.append(tuple(items))
        return result

    return capture(run, events)


def visit_outcome(function, groups, failure=None):
    events = []

    def visit(item):
        events.append(item)
        if item == failure:
            raise LookupError(f"visit failed: {item}")

    return capture(lambda: function(groups, visit), events)


def timer_outcome(
    function,
    items,
    active_items,
    timer_exists,
    schedule_failure=None,
):
    events = []

    def is_active(item):
        events.append(("active", item))
        return item in active_items

    def has_timer():
        events.append(("has_timer",))
        return timer_exists

    def schedule(item):
        events.append(("schedule", item))
        if item == schedule_failure:
            raise RuntimeError(f"schedule failed: {item}")

    return capture(
        lambda: function(items, is_active, has_timer, schedule),
        events,
    )


def compact_outcome(function, stop):
    events = []
    return capture(lambda: function(stop, lambda: events.append("action")), events)


def test_handler_early_return_preserves_notifications(programs):
    original_root, rebuilt_root, tree, original, rebuilt = programs
    handler = next(
        node
        for node in ast.walk(function_node(tree, "upload_error"))
        if isinstance(node, ast.ExceptHandler)
    )
    screenshot = next(node for node in handler.body if isinstance(node, ast.If))
    assert any(isinstance(node, ast.Return) for node in ast.walk(screenshot))
    assert not any(isinstance(node, ast.Pass) for node in ast.walk(screenshot))
    assert code_metadata(original_root, "upload_error") == code_metadata(
        rebuilt_root,
        "upload_error",
    )

    cases = (
        ("screenshot", False, ("return", None, type(None), ["share"])),
        ("normal", False, ("return", None, type(None), ["image"])),
        ("normal", True, ("return", None, type(None), ["video"])),
    )
    for kind, is_video, expected in cases:
        original_result = upload_outcome(original["upload_error"], kind, is_video)
        rebuilt_result = upload_outcome(rebuilt["upload_error"], kind, is_video)
        assert original_result == rebuilt_result == expected

    original_error = upload_outcome(
        original["upload_error"],
        "screenshot",
        False,
        failure="share",
    )
    rebuilt_error = upload_outcome(
        rebuilt["upload_error"],
        "screenshot",
        False,
        failure="share",
    )
    assert original_error == rebuilt_error == (
        "error",
        LookupError,
        "notify failed: share",
        ["share"],
    )


def test_terminal_loop_break_keeps_single_deletion(programs):
    original_root, rebuilt_root, tree, original, rebuilt = programs
    function = function_node(tree, "remove_first")
    match = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
    )
    assert isinstance(match.body[-1], (ast.Break, ast.Return))
    assert code_metadata(original_root, "remove_first") == code_metadata(
        rebuilt_root,
        "remove_first",
    )

    for values, target, expected_items in (
        ([1, 2, 1], 1, (2, 1)),
        ([1, 2, 3], 9, (1, 2, 3)),
        ([1, 1, 1], 1, (1, 1)),
    ):
        original_result = remove_outcome(original["remove_first"], values, target)
        rebuilt_result = remove_outcome(rebuilt["remove_first"], values, target)
        expected = ("return", None, type(None), [expected_items])
        assert original_result == rebuilt_result == expected


def test_nested_loop_guard_return_enforces_visit_limit(programs):
    original_root, rebuilt_root, tree, original, rebuilt = programs
    function = function_node(tree, "visit_limited")
    threshold = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
    )
    assert isinstance(threshold.body[-1], ast.Return)
    assert not any(isinstance(node, ast.Pass) for node in ast.walk(threshold))
    assert code_metadata(original_root, "visit_limited") == code_metadata(
        rebuilt_root,
        "visit_limited",
    )

    original_result = visit_outcome(
        original["visit_limited"],
        [[1, 2], [3, 4, 5]],
    )
    rebuilt_result = visit_outcome(
        rebuilt["visit_limited"],
        [[1, 2], [3, 4, 5]],
    )
    assert original_result == rebuilt_result == (
        "return",
        None,
        type(None),
        [1, 2, 3],
    )

    original_error = visit_outcome(
        original["visit_limited"],
        [[1, 2, 3]],
        failure=2,
    )
    rebuilt_error = visit_outcome(
        rebuilt["visit_limited"],
        [[1, 2, 3]],
        failure=2,
    )
    assert original_error == rebuilt_error == (
        "error",
        LookupError,
        "visit failed: 2",
        [1, 2],
    )


def test_timer_guards_and_compact_return_keep_control_boundaries(programs):
    original_root, rebuilt_root, tree, original, rebuilt = programs
    timer = function_node(tree, "start_timer")
    assert sum(isinstance(node, ast.Return) for node in ast.walk(timer)) == 2
    assert not any(isinstance(node, ast.Pass) for node in ast.walk(timer))
    compact = function_node(tree, "compact_guard")
    assert any(isinstance(node, ast.Return) for node in ast.walk(compact))

    for name in ("start_timer", "compact_guard"):
        assert code_metadata(original_root, name) == code_metadata(
            rebuilt_root,
            name,
        )

    cases = (
        ([1, 2], {1, 2}, True, None),
        ([1, 2], {1, 2}, False, None),
        ([1, 2, 3], {2, 3}, False, None),
        ([1, 2], set(), False, None),
        ([1, 2], {1}, False, 1),
    )
    for items, active, exists, failure in cases:
        original_result = timer_outcome(
            original["start_timer"],
            items,
            active,
            exists,
            failure,
        )
        rebuilt_result = timer_outcome(
            rebuilt["start_timer"],
            items,
            active,
            exists,
            failure,
        )
        assert original_result == rebuilt_result

    assert timer_outcome(
        rebuilt["start_timer"],
        [1, 2],
        {1, 2},
        True,
    )[-1] == [("active", 1), ("has_timer",)]
    assert timer_outcome(
        rebuilt["start_timer"],
        [1, 2],
        {1, 2},
        False,
    )[-1] == [("active", 1), ("has_timer",), ("schedule", 1)]

    for stop, expected_events in ((True, []), (False, ["action"])):
        original_result = compact_outcome(original["compact_guard"], stop)
        rebuilt_result = compact_outcome(rebuilt["compact_guard"], stop)
        assert original_result == rebuilt_result == (
            "return",
            None,
            type(None),
            expected_events,
        )
