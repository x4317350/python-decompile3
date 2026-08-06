"""Semantic regressions for CPython 3.11 conditional value recovery."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 bytecode shapes",
)


COMPLEX_SHORT_CIRCUIT_SOURCE = """
def yuyue_chat_add_black_list(
    bind_master_info,
    avatar_id,
    mark,
    call_server,
):
    if bind_master_info and (
        bind_master_info.get("baseinfo") or {}
    ).get("user_id") == avatar_id:
        mark()
        return

    call_server()
"""


BOOLEAN_NORMALIZATION_SOURCE = """
def normalize(res):
    return True if res else False
"""


def recover(source: str) -> tuple[str, ast.Module]:
    output = io.StringIO()
    code_deparse(
        compile(source, "<controlflow311-regression>", "exec"),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered = output.getvalue()
    tree = ast.parse(recovered)
    compile(tree, "<controlflow311-recovered>", "exec")
    return recovered, tree


def execute(source: str) -> dict[str, object]:
    namespace = {"__name__": "controlflow311_regression"}
    exec(
        compile(source, "<controlflow311-execution>", "exec"),
        namespace,
    )
    return namespace


class TracedMapping:
    def __init__(self, values, events, label):
        self.values = values
        self.events = events
        self.label = label

    def __bool__(self):
        self.events.append((self.label, "bool"))
        return True

    def get(self, key):
        self.events.append((self.label, "get", key))
        return self.values.get(key)


def run_black_list(function, case, avatar_id):
    events = []
    if case == "none":
        bind_master_info = None
    elif case == "missing":
        bind_master_info = TracedMapping(
            {"unrelated": object()}, events, "outer"
        )
    else:
        baseinfo = TracedMapping(
            {"user_id": 7 if case == "match" else 8},
            events,
            "baseinfo",
        )
        bind_master_info = TracedMapping(
            {"baseinfo": baseinfo}, events, "outer"
        )

    result = function(
        bind_master_info,
        avatar_id,
        lambda: events.append("mark"),
        lambda: events.append("call_server"),
    )
    return result, events


def test_complex_and_or_receiver_and_join_preserve_short_circuit_semantics():
    recovered, tree = recover(COMPLEX_SHORT_CIRCUIT_SOURCE)
    original_function = execute(COMPLEX_SHORT_CIRCUIT_SOURCE)[
        "yuyue_chat_add_black_list"
    ]
    recovered_function = execute(recovered)["yuyue_chat_add_black_list"]

    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "yuyue_chat_add_black_list"
    )
    assert sum(isinstance(node, ast.Return) for node in ast.walk(function)) == 1
    assert any(
        isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And)
        for node in ast.walk(function)
    )
    assert any(
        isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
        for node in ast.walk(function)
    )

    expected_events = {
        "none": ["call_server"],
        "missing": [
            ("outer", "bool"),
            ("outer", "get", "baseinfo"),
            "call_server",
        ],
        "mismatch": [
            ("outer", "bool"),
            ("outer", "get", "baseinfo"),
            ("baseinfo", "bool"),
            ("baseinfo", "get", "user_id"),
            "call_server",
        ],
        "match": [
            ("outer", "bool"),
            ("outer", "get", "baseinfo"),
            ("baseinfo", "bool"),
            ("baseinfo", "get", "user_id"),
            "mark",
        ],
    }
    for case, expected in expected_events.items():
        original_result = run_black_list(original_function, case, 7)
        recovered_result = run_black_list(recovered_function, case, 7)
        assert original_result == recovered_result == (None, expected)


class TruthProbe:
    def __init__(self, truth, events):
        self.truth = truth
        self.events = events

    def __bool__(self):
        self.events.append("bool")
        return self.truth


def test_true_false_conditional_returns_bool_not_original_truthy_value():
    recovered, tree = recover(BOOLEAN_NORMALIZATION_SOURCE)
    original_function = execute(BOOLEAN_NORMALIZATION_SOURCE)["normalize"]
    recovered_function = execute(recovered)["normalize"]

    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "normalize"
    )
    returned = next(
        node for node in ast.walk(function) if isinstance(node, ast.Return)
    )
    assert isinstance(returned.value, ast.IfExp)

    for value, expected in ((0, False), (2, True), ([], False), ([1], True)):
        original_result = original_function(value)
        recovered_result = recovered_function(value)
        assert original_result is expected
        assert recovered_result is expected
        assert type(recovered_result) is bool

    for truth in (False, True):
        original_events = []
        recovered_events = []
        original_probe = TruthProbe(truth, original_events)
        recovered_probe = TruthProbe(truth, recovered_events)
        original_result = original_function(original_probe)
        recovered_result = recovered_function(recovered_probe)
        assert original_result is recovered_result is truth
        assert type(recovered_result) is bool
        assert original_events == recovered_events == ["bool"]
