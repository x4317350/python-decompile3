"""Dynamic regressions for the Python 3.11 logic-audit fixes."""

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
def guarded_for(items, first, second, payload):
    for item in items:
        if first(item):
            continue
        if second(item):
            continue
        payload(item)
        break
    return "done"


def guarded_while(items, guard, payload):
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if guard(item):
            continue
        payload(item)
        break
    return index


def guarded_while_true(items, guard, payload):
    iterator = iter(items)
    while True:
        item = next(iterator)
        if guard(item):
            continue
        payload(item)
        break
    return item


def guarded_generator(items, guard, payload):
    for item in items:
        if guard(item):
            yield ("guard", item)
            continue
        payload(item)
        yield ("payload", item)
        break


def handler_continue(values, guard, cleanup):
    iterator = iter(values)
    while True:
        try:
            value = next(iterator)
            if guard(value):
                continue
            return value
        finally:
            cleanup(value)


def bounded_week(start, end, tail):
    if not 0 <= start <= 6 or not 0 <= end <= 6:
        return "invalid"
    tail()
    return "valid"


def connect_shape(err, pending, invalid, platform, connected):
    if err in pending or \
       err == invalid and platform():
        return "pending"
    if err == 0:
        connected()
        return "connected"
    raise LookupError(err)


def segment_shape(point, side):
    if (
        point(0) and side(0, 0) and side(0, 1)
        or point(1) and side(1, 0) and side(1, 1)
        or point(2) and side(2, 0) and side(2, 1)
        or point(3) and side(3, 0) and side(3, 1)
    ):
        return True
    return False


def hero_shape(model, result, is_pad, no_model, hit, tail):
    if (model and model == result) or is_pad or no_model:
        hit()
        return "hit"
    tail()
    return "tail"


class Secret:
    def __value(self):
        return 3

    reveal = __value

    def _Secret__manual(self):
        return 4


class _Hidden:
    def __value(self):
        return 5

    reveal = __value


class Outer:
    class Inner:
        def __value(self):
            return 6

        reveal = __value
'''


def execute(code, name):
    namespace = {"__name__": name}
    exec(code, namespace)
    return namespace


@pytest.fixture(scope="module")
def programs():
    original_root = compile(SOURCE, "<logic-regression311-original>", "exec")
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
        "<logic-regression311-recovered>",
        "exec",
    )
    return (
        recovered_tree,
        recovered_source,
        execute(original_root, "logic_regression311_original"),
        execute(rebuilt_root, "logic_regression311_recovered"),
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
        return "error", type(error), str(error), tuple(events)
    return "return", value, type(value), tuple(events)


def guarded_for_outcome(function, items, first_values, second_values):
    events = []

    def first(item):
        events.append(("first", item))
        return item in first_values

    def second(item):
        events.append(("second", item))
        return item in second_values

    def payload(item):
        events.append(("payload", item))

    return capture(lambda: function(items, first, second, payload), events)


def guarded_single_outcome(function, items, guarded, *, generator=False):
    events = []

    def guard(item):
        events.append(("guard", item))
        return item in guarded

    def payload(item):
        events.append(("payload", item))

    if generator:
        def operation():
            return list(function(items, guard, payload))
    else:
        def operation():
            return function(items, guard, payload)

    return capture(operation, events)


def test_guard_continue_payloads_remain_inside_loops(programs):
    tree, recovered_source, original, recovered = programs
    guarded_for = function_node(tree, "guarded_for")
    loop = next(node for node in guarded_for.body if isinstance(node, ast.For))
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "payload"
        for node in ast.walk(loop)
    )
    assert any(isinstance(node, ast.Break) for node in ast.walk(loop))
    assert "payload(item)" in recovered_source

    for items, first, second in (
        ([1, 2, 3], {1}, {2}),
        ([1, 2, 3], set(), set()),
        ([1, 2, 3], {1, 2, 3}, set()),
    ):
        assert guarded_for_outcome(
            recovered["guarded_for"], items, first, second
        ) == guarded_for_outcome(
            original["guarded_for"], items, first, second
        )


def test_guarded_while_and_generator_preserve_events(programs):
    tree, _, original, recovered = programs
    for name in ("guarded_while", "guarded_while_true"):
        function = function_node(tree, name)
        assert sum(isinstance(node, ast.While) for node in ast.walk(function)) == 1

    for name, items, guarded in (
        ("guarded_while", [1, 2, 3], {1, 2}),
        ("guarded_while_true", [1, 2, 3], {1, 2}),
    ):
        assert guarded_single_outcome(
            recovered[name], items, guarded
        ) == guarded_single_outcome(original[name], items, guarded)

    assert guarded_single_outcome(
        recovered["guarded_generator"],
        [1, 2, 3],
        {1, 2},
        generator=True,
    ) == guarded_single_outcome(
        original["guarded_generator"],
        [1, 2, 3],
        {1, 2},
        generator=True,
    )


def handler_continue_outcome(function, values, guarded):
    events = []

    def guard(value):
        events.append(("guard", value))
        return value in guarded

    def cleanup(value):
        events.append(("cleanup", value))

    return capture(lambda: function(values, guard, cleanup), events)


def test_handler_nop_is_not_treated_as_a_tail_break(programs):
    tree, _, original, recovered = programs
    function = function_node(tree, "handler_continue")
    assert sum(isinstance(node, ast.While) for node in ast.walk(function)) == 1
    assert sum(isinstance(node, ast.Try) for node in ast.walk(function)) == 1

    for values, guarded in (([1, 2, 3], {1, 2}), ([1], set())):
        assert handler_continue_outcome(
            recovered["handler_continue"], values, guarded
        ) == handler_continue_outcome(
            original["handler_continue"], values, guarded
        )


def bounded_outcome(function, start, end):
    events = []
    return capture(lambda: function(start, end, lambda: events.append("tail")), events)


def connect_outcome(function, err):
    events = []

    def platform():
        events.append("platform")
        return True

    def connected():
        events.append("connected")

    return capture(
        lambda: function(err, {1}, 2, platform, connected),
        events,
    )


def test_multiline_conditions_keep_join_and_terminal_boundaries(programs):
    _, _, original, recovered = programs
    for start, end in itertools.product((-1, 0, 3, 6, 7), repeat=2):
        assert bounded_outcome(
            recovered["bounded_week"], start, end
        ) == bounded_outcome(original["bounded_week"], start, end)

    for err in (0, 1, 2, 3):
        assert connect_outcome(
            recovered["connect_shape"], err
        ) == connect_outcome(original["connect_shape"], err)


def segment_outcome(function, values):
    events = []

    def point(index):
        events.append(("point", index))
        return values[(index, "point")]

    def side(index, edge):
        events.append(("side", index, edge))
        return values[(index, edge)]

    return capture(lambda: function(point, side), events)


def test_or_group_condition_preserves_short_circuit_order(programs):
    _, _, original, recovered = programs
    cases = []
    for winner in (None, 0, 1, 2, 3):
        values = {}
        for index in range(4):
            values[(index, "point")] = winner == index
            values[(index, 0)] = True
            values[(index, 1)] = True
        cases.append(values)
    partial = dict(cases[-1])
    partial[(0, "point")] = True
    partial[(0, 0)] = False
    cases.append(partial)

    for values in cases:
        assert segment_outcome(
            recovered["segment_shape"], values
        ) == segment_outcome(original["segment_shape"], values)


class TruthProbe:
    def __init__(self, label, truth, events, equal=False):
        self.label = label
        self.truth = truth
        self.events = events
        self.equal = equal

    def __bool__(self):
        self.events.append(("truth", self.label))
        return self.truth

    def __eq__(self, other):
        self.events.append(("equal", self.label, other))
        return self.equal


def hero_outcome(function, model_truth, model_equal, pad, missing):
    events = []
    model = TruthProbe("model", model_truth, events, equal=model_equal)
    pad_value = TruthProbe("pad", pad, events)
    missing_value = TruthProbe("missing", missing, events)
    return capture(
        lambda: function(
            model,
            "result",
            pad_value,
            missing_value,
            lambda: events.append("hit"),
            lambda: events.append("tail"),
        ),
        events,
    )


def test_and_or_condition_preserves_truth_and_equality_calls(programs):
    _, _, original, recovered = programs
    for values in itertools.product((False, True), repeat=4):
        assert hero_outcome(recovered["hero_shape"], *values) == hero_outcome(
            original["hero_shape"], *values
        )


def test_private_method_names_restore_source_and_reflection(programs):
    tree, recovered_source, original, recovered = programs
    secret = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Secret"
    )
    names = [
        node.name for node in secret.body if isinstance(node, ast.FunctionDef)
    ]
    assert names == ["__value", "_Secret__manual"]
    assert "def _Secret__value" not in recovered_source

    for namespace in (original, recovered):
        secret_type = namespace["Secret"]
        assert secret_type.reveal(secret_type()) == 3
        assert secret_type.reveal.__name__ == "__value"
        assert secret_type.reveal.__qualname__ == "Secret.__value"
        assert secret_type._Secret__manual(secret_type()) == 4
        assert secret_type._Secret__manual.__name__ == "_Secret__manual"

        hidden_type = namespace["_Hidden"]
        assert hidden_type.reveal(hidden_type()) == 5
        assert hidden_type.reveal.__name__ == "__value"
        assert hidden_type.reveal.__qualname__ == "_Hidden.__value"

        inner_type = namespace["Outer"].Inner
        assert inner_type.reveal(inner_type()) == 6
        assert inner_type.reveal.__name__ == "__value"
        assert inner_type.reveal.__qualname__ == "Outer.Inner.__value"
