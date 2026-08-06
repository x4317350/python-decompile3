"""Semantic regressions for CPython 3.11 exception-region boundaries."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 exception tables",
)


LOGIN_WITH_SDK_SOURCE = """
def login_with_sdk(is_login, do_login, check_channel, not_login):
    if is_login():
        do_login()
        try:
            check_channel()
        except:
            pass
    else:
        not_login()
"""


ON_LOGIN_RESULT_SOURCE = """
def on_login_result(
    get_server,
    is_special,
    is_open,
    download,
    reset,
    check_gray,
    success,
):
    try:
        server = get_server()
        if is_special(server):
            if not is_open():
                reset()
                return
            ret = download()
            if ret != 0:
                reset()
                return
    except:
        pass

    if check_gray():
        return
    success()
"""


def recover(source: str) -> tuple[str, ast.Module]:
    output = io.StringIO()
    code_deparse(
        compile(source, "<exception-boundary311-regression>", "exec"),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered = output.getvalue()
    tree = ast.parse(recovered)
    compile(tree, "<exception-boundary311-recovered>", "exec")
    return recovered, tree


def execute(source: str) -> dict[str, object]:
    namespace = {"__name__": "exception_boundary311_regression"}
    exec(
        compile(source, "<exception-boundary311-execution>", "exec"),
        namespace,
    )
    return namespace


def function_node(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def run_login(function, logged_in, channel_raises):
    events = []

    def is_login():
        events.append("is_login")
        return logged_in

    def do_login():
        events.append("do_login")

    def check_channel():
        events.append("check_channel")
        if channel_raises:
            raise LookupError("channel")

    def not_login():
        events.append("not_login")

    result = function(is_login, do_login, check_channel, not_login)
    return result, events


def test_terminal_if_else_owns_closed_try_except_true_branch():
    recovered, tree = recover(LOGIN_WITH_SDK_SOURCE)
    original_function = execute(LOGIN_WITH_SDK_SOURCE)["login_with_sdk"]
    recovered_function = execute(recovered)["login_with_sdk"]

    function = function_node(tree, "login_with_sdk")
    statement = function.body[0]
    assert isinstance(statement, ast.If)
    assert statement.orelse
    assert len(function.body) == 1
    assert any(isinstance(node, ast.Try) for node in statement.body)

    cases = (
        (False, False, ["is_login", "not_login"]),
        (
            True,
            False,
            ["is_login", "do_login", "check_channel"],
        ),
        (
            True,
            True,
            ["is_login", "do_login", "check_channel"],
        ),
    )
    for logged_in, channel_raises, expected_events in cases:
        original_result = run_login(
            original_function,
            logged_in,
            channel_raises,
        )
        recovered_result = run_login(
            recovered_function,
            logged_in,
            channel_raises,
        )
        assert original_result == recovered_result == (
            None,
            expected_events,
        )


def run_login_result(function, case):
    events = []

    def get_server():
        events.append("get_server")
        if case == "get_error":
            raise LookupError("server")
        return object()

    def is_special(server):
        assert server is not None
        events.append("is_special")
        return case not in ("ordinary", "get_error")

    def is_open():
        events.append("is_open")
        return case != "closed"

    def download():
        events.append("download")
        return 1 if case == "download_error" else 0

    def reset():
        events.append("reset")

    def check_gray():
        events.append("check_gray")
        return case == "gray"

    def success():
        events.append("success")

    result = function(
        get_server,
        is_special,
        is_open,
        download,
        reset,
        check_gray,
        success,
    )
    return result, events


def test_try_normal_completion_does_not_gain_return_or_try_else():
    recovered, tree = recover(ON_LOGIN_RESULT_SOURCE)
    original_function = execute(ON_LOGIN_RESULT_SOURCE)["on_login_result"]
    recovered_function = execute(recovered)["on_login_result"]

    function = function_node(tree, "on_login_result")
    statement = function.body[0]
    assert isinstance(statement, ast.Try)
    assert statement.orelse == []
    assert sum(isinstance(node, ast.Return) for node in ast.walk(function)) == 3

    expected = {
        "ordinary": [
            "get_server",
            "is_special",
            "check_gray",
            "success",
        ],
        "closed": ["get_server", "is_special", "is_open", "reset"],
        "download_error": [
            "get_server",
            "is_special",
            "is_open",
            "download",
            "reset",
        ],
        "happy": [
            "get_server",
            "is_special",
            "is_open",
            "download",
            "check_gray",
            "success",
        ],
        "gray": [
            "get_server",
            "is_special",
            "is_open",
            "download",
            "check_gray",
        ],
        "get_error": ["get_server", "check_gray", "success"],
    }
    for case, expected_events in expected.items():
        original_result = run_login_result(original_function, case)
        recovered_result = run_login_result(recovered_function, case)
        assert original_result == recovered_result == (
            None,
            expected_events,
        )
