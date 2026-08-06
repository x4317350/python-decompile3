"""Semantic regressions for copied CPython 3.11 None-return epilogues."""

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
    reason="These regressions exercise CPython 3.11 return epilogues",
)


BATTLE_SOURCE = """
def battle_like(
    mode,
    enabled,
    is_team_scene,
    begin_fight,
    clear_combat,
):
    if mode == 1:
        if enabled:
            begin_fight()
    elif mode == 2:
        if enabled:
            begin_fight()
            if is_team_scene():
                clear_combat()
    elif mode == 3:
        if enabled:
            begin_fight()
"""


REALNAME_SOURCE = """
def realname_like(realname_msg, shown, action):
    if realname_msg:
        def is_young():
            if realname_msg.get("verify_status", 2) == 3:
                return True
            if realname_msg.get("verify_status", 2) == 1:
                return False
            if realname_msg.get("age_range", 4) in (1, 2, 3, 9):
                return True
            return False

        if shown():
            return

        if is_young():
            action()
"""


def recover(source: str) -> tuple[str, ast.Module, object]:
    original = compile(source, "<return-none311-regression>", "exec")
    output = io.StringIO()
    code_deparse(
        original,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered = output.getvalue()
    tree = ast.parse(recovered)
    rebuilt = compile(tree, "<return-none311-recovered>", "exec")
    return recovered, tree, rebuilt


def execute(source: str) -> dict[str, object]:
    namespace = {"__name__": "return_none311_regression"}
    exec(
        compile(source, "<return-none311-execution>", "exec"),
        namespace,
    )
    return namespace


def function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def unreachable_suite_edges(tree: ast.AST):
    errors = []
    terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)

    def check(statements):
        for index, statement in enumerate(statements[:-1]):
            if isinstance(statement, terminators):
                errors.append(
                    (type(statement).__name__, type(statements[index + 1]).__name__)
                )
        for statement in statements:
            for field in ("body", "orelse", "finalbody"):
                child = getattr(statement, field, None)
                if isinstance(child, list):
                    check(child)
            for handler in getattr(statement, "handlers", ()):
                check(handler.body)

    check(tree.body)
    return errors


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


def battle_outcome(function, mode, enabled, team_scene, failure=None):
    events = []

    def is_team_scene():
        events.append("is_team_scene")
        if failure == "team":
            raise LookupError("team")
        return team_scene

    def begin_fight():
        events.append("begin_fight")
        if failure == "begin":
            raise RuntimeError("begin")

    def clear_combat():
        events.append("clear_combat")
        if failure == "clear":
            raise ValueError("clear")

    try:
        result = function(
            mode,
            enabled,
            is_team_scene,
            begin_fight,
            clear_combat,
        )
    except Exception as error:
        return "error", type(error), str(error), events
    return "return", result, type(result), events


def test_copied_battle_epilogues_are_not_recovered_as_returns():
    recovered, tree, rebuilt = recover(BATTLE_SOURCE)
    original_root = compile(BATTLE_SOURCE, "<battle-original>", "exec")
    original = execute(BATTLE_SOURCE)["battle_like"]
    rebuilt_function = execute(recovered)["battle_like"]

    function = function_node(tree, "battle_like")
    assert not any(isinstance(node, ast.Return) for node in ast.walk(function))
    assert unreachable_suite_edges(tree) == []
    assert code_metadata(original_root, "battle_like") == code_metadata(
        rebuilt,
        "battle_like",
    )

    cases = (
        (1, False, False, []),
        (1, True, False, ["begin_fight"]),
        (2, False, True, []),
        (2, True, False, ["begin_fight", "is_team_scene"]),
        (
            2,
            True,
            True,
            ["begin_fight", "is_team_scene", "clear_combat"],
        ),
        (3, True, False, ["begin_fight"]),
    )
    for mode, enabled, team_scene, expected_events in cases:
        original_result = battle_outcome(
            original,
            mode,
            enabled,
            team_scene,
        )
        rebuilt_result = battle_outcome(
            rebuilt_function,
            mode,
            enabled,
            team_scene,
        )
        assert original_result == rebuilt_result == (
            "return",
            None,
            type(None),
            expected_events,
        )

    for failure in ("begin", "team", "clear"):
        original_result = battle_outcome(
            original,
            2,
            True,
            True,
            failure,
        )
        rebuilt_result = battle_outcome(
            rebuilt_function,
            2,
            True,
            True,
            failure,
        )
        assert original_result == rebuilt_result
        assert original_result[0] == "error"


def realname_outcome(function, realname_msg, shown_value, failure=None):
    events = []

    def shown():
        events.append("shown")
        if failure == "shown":
            raise LookupError("shown")
        return shown_value

    def action():
        events.append("action")
        if failure == "action":
            raise RuntimeError("action")

    try:
        result = function(realname_msg, shown, action)
    except Exception as error:
        return "error", type(error), str(error), events
    return "return", result, type(result), events


def test_realname_tail_cleanup_keeps_the_control_critical_early_return():
    recovered, tree, rebuilt = recover(REALNAME_SOURCE)
    original_root = compile(REALNAME_SOURCE, "<realname-original>", "exec")
    original = execute(REALNAME_SOURCE)["realname_like"]
    rebuilt_function = execute(recovered)["realname_like"]

    function = function_node(tree, "realname_like")
    outer_if = next(node for node in function.body if isinstance(node, ast.If))
    stop_if = next(
        node
        for node in outer_if.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "shown"
    )
    young_if = next(
        node
        for node in outer_if.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "is_young"
    )
    assert len(stop_if.body) == 1 and isinstance(stop_if.body[0], ast.Return)
    assert not any(isinstance(node, ast.Return) for node in ast.walk(young_if))
    assert not isinstance(function.body[-1], ast.Return)
    assert unreachable_suite_edges(tree) == []
    assert code_metadata(original_root, "realname_like") == code_metadata(
        rebuilt,
        "realname_like",
    )

    cases = (
        ({}, False, []),
        ({"verify_status": 3}, True, ["shown"]),
        ({"verify_status": 3}, False, ["shown", "action"]),
        ({"verify_status": 1}, False, ["shown"]),
        ({"age_range": 2}, False, ["shown", "action"]),
    )
    for message, shown_value, expected_events in cases:
        original_result = realname_outcome(original, message, shown_value)
        rebuilt_result = realname_outcome(
            rebuilt_function,
            message,
            shown_value,
        )
        assert original_result == rebuilt_result == (
            "return",
            None,
            type(None),
            expected_events,
        )

    for failure in ("shown", "action"):
        original_result = realname_outcome(
            original,
            {"verify_status": 3},
            False,
            failure,
        )
        rebuilt_result = realname_outcome(
            rebuilt_function,
            {"verify_status": 3},
            False,
            failure,
        )
        assert original_result == rebuilt_result
        assert original_result[0] == "error"
