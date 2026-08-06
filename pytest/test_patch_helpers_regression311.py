"""Semantic regressions from the Patch and com.utils.helpers samples."""

from __future__ import annotations

import ast
import dis
import io
import itertools
import sys
import time

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow.structures import StructuredDecompiler311
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 control-flow shapes",
)


SOURCE = r'''
def normal_completion(get_sizes, prompt, continue_init):
    try:
        first, second = get_sizes()
        if first >= 10 or second >= 10:
            try:
                prompt("upgrade")
            except Exception:
                pass
            prompt("stop")
            return
    except Exception:
        pass
    continue_init()


def is_below_limit(import_primary, import_fallback, system_fallback, events):
    try:
        try:
            winreg = import_primary()
        except ImportError:
            winreg = import_fallback()
        key = winreg.OpenKey("root", "version")
        try:
            major = winreg.QueryValueEx(key, "major")[0]
        finally:
            winreg.CloseKey(key)
        return major < 10
    except Exception as error:
        events.append(("registry", type(error).__name__))
        try:
            return system_fallback() < 10
        except Exception as fallback_error:
            events.append(("fallback", type(fallback_error).__name__))
            return False


def terminal_if_else_try(flag, true_action, false_action, false_tail):
    if flag:
        try:
            true_action()
        except Exception:
            pass
    else:
        try:
            false_action()
        except Exception:
            pass
        false_tail()


def choose_scale(scale_x, scale_y, use):
    use(scale_x > scale_y and scale_x or scale_y)


def condition_then_nested(spec, spec_set, original, mark):
    if spec is not None or spec_set is not None:
        if original:
            mark("inner")
    mark("tail")


extra_total = 0
total = 0


def assign_totals(tag, first, second):
    global extra_total, total
    extra_total = total = second if tag == 12 else first


def nested_failure_branches(file_ok, data_ok, make, setup, notify):
    try:
        if file_ok and data_ok:
            panel = make()
            if panel is not None:
                setup(panel)
            else:
                notify("inner")
        else:
            notify("outer")
    except Exception:
        notify("error")


def three_missing_conditions(
    res_exists,
    res_loads,
    res_size,
    script_exists,
    script_loads,
    script_size,
    tex_exists,
    tex_loads,
    tex_size,
    clean,
    after,
):
    if (
        (not res_exists() or not res_loads()) and res_size > 0
        or (not script_exists() or not script_loads()) and script_size > 0
        or (not tex_exists() or not tex_loads()) and tex_size > 0
    ):
        clean()
        return
    after()
'''


def execute(code, name):
    namespace = {"__name__": name}
    exec(code, namespace)
    return namespace


@pytest.fixture(scope="module")
def programs():
    original_root = compile(SOURCE, "<patch-helpers311-original>", "exec")
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
        "<patch-helpers311-recovered>",
        "exec",
    )
    return (
        original_root,
        rebuilt_root,
        tree,
        execute(original_root, "patch_helpers311_original"),
        execute(rebuilt_root, "patch_helpers311_recovered"),
    )


def capture(operation, events):
    try:
        value = operation()
    except BaseException as error:
        return "error", type(error), str(error), events
    return "return", value, type(value), events


def normal_outcome(function, sizes, failure=None):
    events = []

    def get_sizes():
        events.append("sizes")
        if failure == "sizes":
            raise LookupError("sizes")
        return sizes

    def prompt(message):
        events.append(("prompt", message))
        if failure == message:
            raise RuntimeError(message)

    def continue_init():
        events.append("continue")

    return capture(
        lambda: function(get_sizes, prompt, continue_init),
        events,
    )


def test_protected_conditional_return_keeps_normal_completion(programs):
    _, _, _, original, rebuilt = programs
    cases = (
        ((1, 2), None),
        ((10, 2), None),
        ((1, 20), None),
        ((10, 2), "upgrade"),
        ((10, 2), "stop"),
        ((1, 2), "sizes"),
    )
    for sizes, failure in cases:
        assert normal_outcome(
            original["normal_completion"],
            sizes,
            failure,
        ) == normal_outcome(
            rebuilt["normal_completion"],
            sizes,
            failure,
        )


class Registry:
    def __init__(self, events, major=9, failure=None):
        self.events = events
        self.major = major
        self.failure = failure

    def OpenKey(self, root, name):
        self.events.append(("open", root, name))
        if self.failure == "open":
            raise OSError("open")
        return "key"

    def QueryValueEx(self, key, name):
        self.events.append(("query", key, name))
        if self.failure == "query":
            raise OSError("query")
        return (self.major, None)

    def CloseKey(self, key):
        self.events.append(("close", key))
        if self.failure == "close":
            raise OSError("close")


def registry_outcome(function, case):
    events = []
    registry = Registry(
        events,
        major=case.get("major", 9),
        failure=case.get("registry_failure"),
    )

    def primary():
        events.append("primary")
        failure = case.get("primary_failure")
        if failure == "import":
            raise ImportError("primary")
        if failure == "other":
            raise RuntimeError("primary")
        return registry

    def fallback_import():
        events.append("fallback_import")
        if case.get("fallback_import_failure"):
            raise ImportError("fallback import")
        return registry

    def system_fallback():
        events.append("system")
        if case.get("system_failure"):
            raise RuntimeError("system")
        return case.get("system_major", 9)

    return capture(
        lambda: function(
            primary,
            fallback_import,
            system_fallback,
            events,
        ),
        events,
    )


def test_nested_handler_and_finally_preserve_exception_semantics(programs):
    _, _, _, original, rebuilt = programs
    cases = (
        {"major": 9},
        {"major": 11},
        {"primary_failure": "import", "major": 9},
        {"primary_failure": "other", "system_major": 9},
        {"registry_failure": "open", "system_major": 11},
        {"registry_failure": "query", "system_major": 9},
        {"registry_failure": "close", "system_major": 11},
        {"registry_failure": "query", "system_failure": True},
        {"primary_failure": "import", "fallback_import_failure": True},
    )
    for case in cases:
        original_outcome = registry_outcome(
            original["is_below_limit"],
            case,
        )
        rebuilt_outcome = registry_outcome(
            rebuilt["is_below_limit"],
            case,
        )
        assert original_outcome == rebuilt_outcome
        assert sum(
            event[0] == "close"
            for event in rebuilt_outcome[-1]
            if isinstance(event, tuple)
        ) <= 1


def terminal_outcome(function, flag, failure=None):
    events = []

    def action(label):
        def run():
            events.append(label)
            if failure == label:
                raise RuntimeError(label)

        return run

    return capture(
        lambda: function(
            flag,
            action("true"),
            action("false"),
            action("tail"),
        ),
        events,
    )


def test_terminal_if_else_and_conditional_value_keep_branch_semantics(programs):
    _, _, tree, original, rebuilt = programs
    terminal = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "terminal_if_else_try"
    )
    assert isinstance(terminal.body[0], ast.If)
    assert terminal.body[0].orelse
    assert not any(isinstance(node, ast.Return) for node in ast.walk(terminal))

    for flag, failure in itertools.product(
        (False, True),
        (None, "true", "false", "tail"),
    ):
        assert terminal_outcome(
            original["terminal_if_else_try"],
            flag,
            failure,
        ) == terminal_outcome(
            rebuilt["terminal_if_else_try"],
            flag,
            failure,
        )

    for scale_x, scale_y in (
        (2, 1),
        (1, 2),
        (0, -1),
        (-1, 0),
        (0.0, 0.0),
    ):
        original_events = []
        rebuilt_events = []
        original["choose_scale"](
            scale_x,
            scale_y,
            original_events.append,
        )
        rebuilt["choose_scale"](
            scale_x,
            scale_y,
            rebuilt_events.append,
        )
        assert original_events == rebuilt_events


def test_converging_condition_does_not_absorb_nested_suite(programs):
    _, _, tree, original, rebuilt = programs
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "condition_then_nested"
    )
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


def test_consumed_assignment_stores_keep_global_scope(programs):
    original_root, rebuilt_root, _, original, rebuilt = programs
    for namespace in (original, rebuilt):
        namespace["assign_totals"](12, 7, 8)
        assert namespace["extra_total"] == namespace["total"] == 8
        namespace["assign_totals"](13, 7, 8)
        assert namespace["extra_total"] == namespace["total"] == 7

    def assignment_code(root):
        return next(
            code
            for code in Scanner311.iter_code_objects(root)
            if code.co_name == "assign_totals"
        )

    for code in (assignment_code(original_root), assignment_code(rebuilt_root)):
        stores = [
            instruction.argval
            for instruction in dis.get_instructions(code)
            if instruction.opname == "STORE_GLOBAL"
        ]
        assert stores == ["extra_total", "total"]
        assert "extra_total" not in code.co_varnames


def failure_outcome(function, file_ok, data_ok, panel, failure=None):
    events = []

    def make():
        events.append("make")
        if failure == "make":
            raise RuntimeError("make")
        return panel

    def setup(value):
        events.append(("setup", value))
        if failure == "setup":
            raise RuntimeError("setup")

    def notify(message):
        events.append(("notify", message))
        if failure == f"notify:{message}":
            raise RuntimeError(message)

    return capture(
        lambda: function(file_ok, data_ok, make, setup, notify),
        events,
    )


def test_nested_failure_branches_do_not_duplicate_notifications(programs):
    _, _, _, original, rebuilt = programs
    cases = (
        (False, False, None, None),
        (True, False, None, None),
        (True, True, None, None),
        (True, True, "panel", None),
        (True, True, "panel", "make"),
        (True, True, "panel", "setup"),
        (True, True, None, "notify:inner"),
    )
    for file_ok, data_ok, panel, failure in cases:
        original_outcome = failure_outcome(
            original["nested_failure_branches"],
            file_ok,
            data_ok,
            panel,
            failure,
        )
        rebuilt_outcome = failure_outcome(
            rebuilt["nested_failure_branches"],
            file_ok,
            data_ok,
            panel,
            failure,
        )
        assert original_outcome == rebuilt_outcome


def missing_outcome(function, values, sizes):
    events = []

    def probe(label):
        def run():
            events.append(label)
            return values[label]

        return run

    return capture(
        lambda: function(
            probe("res_exists"),
            probe("res_loads"),
            sizes[0],
            probe("script_exists"),
            probe("script_loads"),
            sizes[1],
            probe("tex_exists"),
            probe("tex_loads"),
            sizes[2],
            lambda: events.append("clean"),
            lambda: events.append("after"),
        ),
        events,
    )


def test_converging_or_groups_keep_short_circuit_order(programs):
    _, _, _, original, rebuilt = programs
    labels = (
        "res_exists",
        "res_loads",
        "script_exists",
        "script_loads",
        "tex_exists",
        "tex_loads",
    )
    for booleans in itertools.product((False, True), repeat=len(labels)):
        values = dict(zip(labels, booleans))
        for sizes in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)):
            assert missing_outcome(
                original["three_missing_conditions"],
                values,
                sizes,
            ) == missing_outcome(
                rebuilt["three_missing_conditions"],
                values,
                sizes,
            )


def test_module_return_expression_probe_is_constant_time():
    source = "flag = True\n" + "\n".join(
        f"value_{index} = {index} if flag else -{index}"
        for index in range(400)
    )
    code = compile(source, "<module-return-expression311>", "exec")
    tokens, _ = Scanner311().ingest(code)
    owner = StructuredDecompiler311(code, tokens)
    started = time.monotonic()
    for _ in range(1000):
        assert owner._try_return_expression(0, len(tokens)) is None
    assert time.monotonic() - started < 1.0
