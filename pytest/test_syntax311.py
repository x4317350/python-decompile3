"""Phase 7 acceptance tests for CPython 3.11-only syntax."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


MATCH_SOURCE = ROOT / "test" / "simple_source" / "311" / "06_match.py"
GROUP_SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "07_exception_group.py"
)
EXCEPTION_SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "05_exceptions_with.py"
)
EMPTY_STAR_SOURCE = (
    ROOT
    / "test"
    / "fixtures311"
    / "except_star_empty_body.py"
)
TERMINAL_STAR_SOURCE = (
    ROOT
    / "test"
    / "fixtures311"
    / "except_star_terminal_cleanup.py"
)

EMPTY_STAR_MATRIX_SOURCE = """
def ellipsis_body(group):
    try:
        raise group
    except* ValueError:
        ...
    return group


def constant_assert_body(group):
    try:
        raise group
    except* ValueError:
        assert True
    return group


def dead_branch_body(group):
    try:
        raise group
    except* ValueError:
        if False:
            action()
    return group


def empty_then_nonempty(group, events):
    try:
        raise group
    except* ValueError:
        pass
    except* TypeError:
        events.append("type")
    return events


def nonempty_then_empty(group, events):
    try:
        raise group
    except* ValueError:
        events.append("value")
    except* TypeError:
        pass
    return events


def consecutive_empty(group):
    try:
        raise group
    except* ValueError:
        pass
    except* TypeError:
        pass
    return group


def empty_with_else(group, events):
    try:
        if group is not None:
            raise group
    except* ValueError:
        pass
    else:
        events.append("else")
    return events


def empty_with_finally(group, events):
    try:
        raise group
    except* ValueError:
        pass
    finally:
        events.append("finally")
    return events


def empty_with_else_finally(group, events):
    try:
        if group is not None:
            raise group
    except* ValueError:
        pass
    else:
        events.append("else")
    finally:
        events.append("finally")
    return events


def empty_inside_normal_try(group, events):
    try:
        try:
            raise group
        except* ValueError:
            pass
    finally:
        events.append("outer-finally")
    return events
"""

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Phase 7 syntax tests require CPython 3.11",
)


def recover_source(source, tmp_path):
    bytecode = tmp_path / f"{source.stem}.pyc"
    version, _, _, code, implementation, *_ = compile_source(
        source,
        bytecode,
    )
    assert version == (3, 11)
    assert implementation is PythonImplementation.CPython

    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def recover_inline(source):
    output = io.StringIO()
    code_deparse(
        compile(source, "<except-star-empty-matrix>", "exec"),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def execute(source, name):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def exception_shape(error):
    if isinstance(error, BaseExceptionGroup):
        return (
            type(error).__name__,
            error.message,
            tuple(exception_shape(child) for child in error.exceptions),
        )
    return type(error).__name__, str(error)


def leaf_exception_names(shape):
    if len(shape) == 2:
        return [shape[0]]
    names = []
    for child in shape[2]:
        names.extend(leaf_exception_names(child))
    return names


def split_outcome(namespace, group):
    try:
        return "returned", namespace["split_group"](group)
    except BaseException as error:
        return "raised", exception_shape(error)


def test_phase7_protocol_opcodes_are_present():
    kinds = set()
    for source in (MATCH_SOURCE, GROUP_SOURCE):
        root = compile(
            source.read_text(encoding="utf-8"),
            str(source),
            "exec",
        )
        for code in Scanner311.iter_code_objects(root):
            scanner = Scanner311()
            scanner.ingest(code)
            kinds.update(
                instruction.kind
                for instruction in scanner.normalized_instructions
            )

    assert {
        "CHECK_EG_MATCH",
        "MATCH_CLASS",
        "MATCH_KEYS",
        "MATCH_MAPPING",
        "MATCH_SEQUENCE",
        "PREP_RERAISE_STAR",
    } <= kinds


@pytest.mark.parametrize("source", [MATCH_SOURCE, GROUP_SOURCE])
def test_phase7_pyc_deparses_reparses_and_recompiles(source, tmp_path):
    recovered = recover_source(source, tmp_path)
    tree = ast.parse(recovered)
    compile(tree, f"<recovered-{source.stem}>", "exec")


def test_recovered_match_has_every_planned_pattern_form(tmp_path):
    tree = ast.parse(recover_source(MATCH_SOURCE, tmp_path))
    nodes = list(ast.walk(tree))

    assert any(isinstance(node, ast.Match) for node in nodes)
    assert any(isinstance(node, ast.MatchValue) for node in nodes)
    assert any(isinstance(node, ast.MatchSingleton) for node in nodes)
    assert any(isinstance(node, ast.MatchSequence) for node in nodes)
    assert any(isinstance(node, ast.MatchMapping) for node in nodes)
    assert any(isinstance(node, ast.MatchClass) for node in nodes)
    assert any(isinstance(node, ast.MatchOr) for node in nodes)
    assert any(isinstance(node, ast.MatchStar) for node in nodes)
    assert any(
        isinstance(node, ast.MatchAs) and node.name == "captured"
        for node in nodes
    )
    assert any(
        isinstance(node, ast.MatchAs)
        and node.name is None
        and node.pattern is None
        for node in nodes
    )
    assert any(
        isinstance(node, ast.match_case) and node.guard is not None
        for node in nodes
    )

    nested = function_node(tree, "nested_describe")
    nested_case = next(
        node for node in ast.walk(nested) if isinstance(node, ast.match_case)
    )
    assert isinstance(nested_case.pattern, ast.MatchMapping)
    assert any(
        isinstance(node, ast.MatchSequence)
        for node in ast.walk(nested_case.pattern)
    )
    assert any(
        isinstance(node, ast.MatchClass)
        for node in ast.walk(nested_case.pattern)
    )


def test_recovered_except_star_uses_trystar_and_keeps_normal_try_distinct(
    tmp_path,
):
    group_tree = ast.parse(recover_source(GROUP_SOURCE, tmp_path))
    group_nodes = list(ast.walk(group_tree))
    stars = [node for node in group_nodes if isinstance(node, ast.TryStar)]

    assert stars
    assert any(len(node.handlers) == 2 for node in stars)
    assert any(len(node.handlers) == 1 for node in stars)
    assert any(handler.name == "errors" for node in stars for handler in node.handlers)
    assert any(handler.name is None for node in stars for handler in node.handlers)
    assert "except*" in recover_source(GROUP_SOURCE, tmp_path)

    normal_tree = ast.parse(recover_source(EXCEPTION_SOURCE, tmp_path))
    assert any(isinstance(node, ast.Try) for node in ast.walk(normal_tree))
    assert not any(
        isinstance(node, ast.TryStar) for node in ast.walk(normal_tree)
    )


def test_recovered_empty_except_star_uses_pass_and_keeps_binding(tmp_path):
    recovered = recover_source(EMPTY_STAR_SOURCE, tmp_path)
    tree = ast.parse(recovered)
    compile(tree, "<recovered-empty-except-star>", "exec")

    empty = function_node(tree, "empty_handler")
    named = function_node(tree, "empty_named_handler")
    nonempty = function_node(tree, "nonempty_handler")
    empty_try = next(node for node in ast.walk(empty) if isinstance(node, ast.TryStar))
    named_try = next(node for node in ast.walk(named) if isinstance(node, ast.TryStar))
    nonempty_try = next(
        node for node in ast.walk(nonempty) if isinstance(node, ast.TryStar)
    )

    assert empty_try.handlers[0].name is None
    assert named_try.handlers[0].name == "error"
    assert all(
        len(statement.handlers[0].body) == 1
        and isinstance(statement.handlers[0].body[0], ast.Pass)
        for statement in (empty_try, named_try)
    )
    assert not isinstance(nonempty_try.handlers[0].body[0], ast.Pass)


def test_empty_except_star_clause_matrix_reparses_and_keeps_boundaries():
    recovered = recover_inline(EMPTY_STAR_MATRIX_SOURCE)
    tree = ast.parse(recovered)
    compile(tree, "<recompiled-empty-except-star-matrix>", "exec")

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    statements = {
        name: next(
            node for node in ast.walk(function) if isinstance(node, ast.TryStar)
        )
        for name, function in functions.items()
    }
    for name in (
        "ellipsis_body",
        "constant_assert_body",
        "dead_branch_body",
    ):
        assert isinstance(statements[name].handlers[0].body[0], ast.Pass)

    assert len(statements["empty_then_nonempty"].handlers) == 2
    assert isinstance(
        statements["empty_then_nonempty"].handlers[0].body[0],
        ast.Pass,
    )
    assert not isinstance(
        statements["empty_then_nonempty"].handlers[1].body[0],
        ast.Pass,
    )
    assert not isinstance(
        statements["nonempty_then_empty"].handlers[0].body[0],
        ast.Pass,
    )
    assert isinstance(
        statements["nonempty_then_empty"].handlers[1].body[0],
        ast.Pass,
    )
    assert len(statements["consecutive_empty"].handlers) == 2
    assert all(
        isinstance(handler.body[0], ast.Pass)
        for handler in statements["consecutive_empty"].handlers
    )

    assert statements["empty_with_else"].orelse
    assert not statements["empty_with_else"].finalbody
    assert not statements["empty_with_finally"].orelse
    assert statements["empty_with_finally"].finalbody
    assert statements["empty_with_else_finally"].orelse
    assert statements["empty_with_else_finally"].finalbody

    nested = statements["empty_inside_normal_try"]
    assert isinstance(nested.handlers[0].body[0], ast.Pass)
    assert nested.finalbody


def test_terminal_except_star_cleanup_reparses_and_keeps_handlers(tmp_path):
    recovered = recover_source(TERMINAL_STAR_SOURCE, tmp_path)
    tree = ast.parse(recovered)
    compile(tree, "<recompiled-terminal-except-star>", "exec")

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    statements = {
        name: next(
            node for node in ast.walk(function) if isinstance(node, ast.TryStar)
        )
        for name, function in functions.items()
    }
    assert isinstance(statements["terminal_empty"].handlers[0].body[0], ast.Pass)
    assert statements["terminal_named"].handlers[0].name == "error"
    assert isinstance(statements["terminal_named"].handlers[0].body[0], ast.Pass)
    assert not isinstance(
        statements["terminal_nonempty"].handlers[0].body[0],
        ast.Pass,
    )
    assert isinstance(
        statements["terminal_raise"].handlers[0].body[0],
        ast.Raise,
    )
    assert len(statements["terminal_multiple"].handlers) == 2
    assert isinstance(functions["terminal_async"], ast.AsyncFunctionDef)
    assert any(
        isinstance(node, ast.Yield)
        for node in ast.walk(functions["terminal_generator"])
    )
    assert all(
        not isinstance(function.body[-1], ast.Return)
        for function in functions.values()
    )


def test_module_terminal_except_star_cleanup_reparses():
    recovered = recover_inline(
        """
try:
    raise ExceptionGroup("value", [ValueError("bad")])
except* ValueError:
    pass
"""
    )
    tree = ast.parse(recovered)
    compile(tree, "<recompiled-module-terminal-except-star>", "exec")
    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.TryStar)
    assert isinstance(tree.body[0].handlers[0].body[0], ast.Pass)


def match_behavior(namespace):
    point = namespace["Point"]
    describe_values = [
        None,
        0,
        1,
        [3, 4],
        [3, 4, 5],
        {"kind": "point", "x": 7, "y": 8},
        complex(2, 3),
        "other",
    ]
    described = [
        namespace["describe"](value) for value in describe_values
    ]
    nested = [
        namespace["nested_describe"](
            {"payload": [5, point(0, 9)]}
        ),
        namespace["nested_describe"](
            {"payload": [5, "not-a-point"]}
        ),
        namespace["nested_describe"](
            {"kind": "event", "extra": 12}
        ),
        namespace["nested_describe"]("captured"),
    ]
    collected = [
        namespace["collect_description"](1),
        namespace["collect_description"](2),
    ]
    return described, nested, collected


def test_recovered_match_preserves_bindings_guards_and_behavior(tmp_path):
    original = execute(
        MATCH_SOURCE.read_text(encoding="utf-8"),
        "phase7_match_original",
    )
    recovered = execute(
        recover_source(MATCH_SOURCE, tmp_path),
        "phase7_match_recovered",
    )

    assert match_behavior(recovered) == match_behavior(original)


def test_recovered_except_star_preserves_splitting_and_reraise(tmp_path):
    original = execute(
        GROUP_SOURCE.read_text(encoding="utf-8"),
        "phase7_group_original",
    )
    recovered = execute(
        recover_source(GROUP_SOURCE, tmp_path),
        "phase7_group_recovered",
    )

    assert recovered["handle_group"]() == original["handle_group"]()
    all_values_original = ExceptionGroup(
        "values",
        [ValueError("one"), ValueError("two")],
    )
    all_values_recovered = ExceptionGroup(
        "values",
        [ValueError("one"), ValueError("two")],
    )
    assert recovered["mark_values"](all_values_recovered) == original[
        "mark_values"
    ](all_values_original)
    assert split_outcome(
        recovered,
        ExceptionGroup(
            "values",
            [ValueError("one"), ValueError("two")],
        ),
    ) == split_outcome(
        original,
        ExceptionGroup(
            "values",
            [ValueError("one"), ValueError("two")],
        ),
    )
    assert recovered["split_group"](None) == original["split_group"](None)

    recovered_outcome = split_outcome(
        recovered,
        ExceptionGroup(
            "root",
            [
                ValueError("outer value"),
                ExceptionGroup(
                    "nested",
                    [TypeError("type"), ValueError("inner value")],
                ),
            ],
        ),
    )
    original_outcome = split_outcome(
        original,
        ExceptionGroup(
            "root",
            [
                ValueError("outer value"),
                ExceptionGroup(
                    "nested",
                    [TypeError("type"), ValueError("inner value")],
                ),
            ],
        ),
    )
    assert recovered_outcome == original_outcome
    assert recovered_outcome[0] == "raised"
    assert leaf_exception_names(recovered_outcome[1]) == ["TypeError"]
