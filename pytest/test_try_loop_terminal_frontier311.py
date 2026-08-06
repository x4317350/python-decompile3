"""Semantic regressions for try/except loop terminal frontiers."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow.cfg import Edge, instruction_target
from decompyle3.controlflow.exception_structures import (
    ExceptionStructureDecompiler311,
)
from decompyle3.controlflow.structures import (
    StructuredDecompiler311,
    _LoopContext,
)
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 exception tables",
)


SOURCE = r'''
def match_any(filename, patterns, compile_pattern, report_error):
    cache = {}
    for pattern in patterns:
        try:
            if pattern not in cache:
                cache[pattern] = compile_pattern(pattern)
            if cache[pattern].search(filename):
                return True
        except Exception as error:
            report_error(error)
            continue
    return False


def conditional_return(items, predicate, report):
    for item in items:
        try:
            if predicate(item):
                return True
        except Exception as error:
            report(error)
            continue
    return False


def conditional_break(items, predicate, report):
    visited = []
    for item in items:
        visited.append(item)
        try:
            if predicate(item):
                break
        except Exception as error:
            report(error)
            continue
    return visited


def normal_loop_tail(items, work, report):
    for item in items:
        try:
            work(item)
        except Exception as error:
            report(error)
            continue
    return "done"


def real_try_else(items, work, callback, recover):
    for item in items:
        try:
            work(item)
        except LookupError:
            recover(item)
        else:
            callback(item)
    return "done"
'''


def execute(code, name):
    namespace = {"__name__": name}
    exec(code, namespace)
    return namespace


@pytest.fixture(scope="module")
def programs():
    original_root = compile(SOURCE, "<try-loop-frontier311-original>", "exec")
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
        "<try-loop-frontier311-recovered>",
        "exec",
    )
    return (
        tree,
        recovered_source,
        execute(original_root, "try_loop_frontier311_original"),
        execute(rebuilt_root, "try_loop_frontier311_recovered"),
    )


def function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def first_try(tree, name):
    function = function_node(tree, name)
    return next(node for node in ast.walk(function) if isinstance(node, ast.Try))


def native_code(name):
    root = compile(SOURCE, "<try-loop-frontier311-cfg>", "exec")
    return next(
        code
        for code in Scanner311.iter_code_objects(root)
        if code.co_name == name
    )


def frontier_candidate(name):
    code = native_code(name)
    scanner = Scanner311()
    tokens, _ = scanner.ingest(code)
    owner = StructuredDecompiler311(code, tokens)
    structure = ExceptionStructureDecompiler311(owner)

    entry = next(
        candidate
        for candidate in owner.exception_regions
        if not candidate.lasti
        and owner.tokens[owner.offset_to_index[candidate.target]].kind
        == "PUSH_EXC_INFO"
    )
    start = owner.offset_to_index[entry.start]
    handler_index = owner.offset_to_index[entry.target]
    fragments = [
        candidate
        for candidate in owner.exception_regions
        if candidate.target == entry.target
        and candidate.depth == entry.depth
        and candidate.lasti == entry.lasti
        and start
        <= owner.offset_to_index[candidate.start]
        < handler_index
    ]
    body_end = max(
        owner.offset_to_index[candidate.end]
        for candidate in fragments
    )

    for_iter_index = next(
        index
        for index, token in enumerate(owner.tokens)
        if token.kind == "FOR_ITER"
    )
    else_offset = instruction_target(owner.tokens[for_iter_index])
    else_index = owner.offset_to_index[else_offset]
    latch_candidates = [
        index
        for index in range(for_iter_index + 1, else_index)
        if owner.tokens[index].kind.startswith("JUMP_BACKWARD")
        and instruction_target(owner.tokens[index])
        == owner.tokens[for_iter_index].offset
    ]
    latch = latch_candidates[-1]
    break_targets = [
        instruction_target(owner.tokens[index])
        for index in range(for_iter_index + 1, latch)
        if owner.tokens[index].kind == "JUMP_FORWARD"
        and instruction_target(owner.tokens[index]) >= else_offset
    ]
    loop = _LoopContext(
        break_target=max(break_targets) if break_targets else else_offset,
        continue_targets=frozenset(
            {
                owner.tokens[for_iter_index].offset,
                owner.tokens[latch].offset,
            }
        ),
    )
    return owner, structure, fragments, body_end, handler_index, loop


def capture(operation, events):
    try:
        value = operation()
    except BaseException as error:
        return "error", type(error), str(error), events
    return "return", value, type(value), events


class Pattern:
    def __init__(self, label, behavior, events):
        self.label = label
        self.behavior = behavior
        self.events = events

    def search(self, filename):
        self.events.append(("search", self.label, filename))
        result = self.behavior[self.label]
        if isinstance(result, BaseException):
            raise result
        return result


def match_outcome(
    function,
    labels,
    behavior,
    compile_failure=None,
    report_failure=False,
):
    events = []

    def compile_pattern(label):
        events.append(("compile", label))
        if label == compile_failure:
            raise ValueError(f"compile failed: {label}")
        return Pattern(label, behavior, events)

    def report_error(error):
        events.append(("report", type(error).__name__, str(error)))
        if report_failure:
            raise RuntimeError("report failed")

    return capture(
        lambda: function(
            "sample.py",
            labels,
            compile_pattern,
            report_error,
        ),
        events,
    )


def test_match_any_has_no_false_try_else_and_preserves_dynamic_semantics(
    programs,
):
    tree, _, original, rebuilt = programs
    statement = first_try(tree, "match_any")
    assert statement.orelse == []
    assert any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and node.value.value is True
        for suite_statement in statement.body
        for node in ast.walk(suite_statement)
    )

    cases = (
        ((), {}, None, False),
        (("hit",), {"hit": True}, None, False),
        (("miss", "hit"), {"miss": False, "hit": True}, None, False),
        (
            ("miss1", "miss2"),
            {"miss1": False, "miss2": False},
            None,
            False,
        ),
        (("broken", "hit"), {"hit": True}, "broken", False),
        (
            ("broken", "miss"),
            {
                "broken": LookupError("search failed: broken"),
                "miss": False,
            },
            None,
            False,
        ),
        (
            ("broken",),
            {"broken": LookupError("search failed: broken")},
            None,
            True,
        ),
    )
    for labels, behavior, compile_failure, report_failure in cases:
        assert match_outcome(
            original["match_any"],
            labels,
            behavior,
            compile_failure,
            report_failure,
        ) == match_outcome(
            rebuilt["match_any"],
            labels,
            behavior,
            compile_failure,
            report_failure,
        )


def predicate_outcome(function, values, matches, failure=None):
    events = []

    def predicate(value):
        events.append(("predicate", value))
        if value == failure:
            raise ArithmeticError(f"predicate failed: {value}")
        return value in matches

    def report(error):
        events.append(("report", type(error).__name__, str(error)))

    return capture(lambda: function(values, predicate, report), events)


def test_conditional_return_and_break_keep_condition_boundaries(programs):
    tree, _, original, rebuilt = programs
    for name in ("conditional_return", "conditional_break"):
        assert first_try(tree, name).orelse == []

    cases = (
        ((1, 2, 3), frozenset(), None),
        ((1, 2, 3), frozenset({2}), None),
        ((1, 2, 3), frozenset({3}), 2),
    )
    for name in ("conditional_return", "conditional_break"):
        for values, matches, failure in cases:
            assert predicate_outcome(
                original[name],
                values,
                matches,
                failure,
            ) == predicate_outcome(
                rebuilt[name],
                values,
                matches,
                failure,
            )


def work_outcome(function, values, failures=()):
    events = []

    def work(value):
        events.append(("work", value))
        if value in failures:
            raise OSError(f"work failed: {value}")

    def report(error):
        events.append(("report", type(error).__name__, str(error)))

    return capture(lambda: function(values, work, report), events)


def test_natural_loop_tail_continue_stays_in_try_body(programs):
    tree, _, original, rebuilt = programs
    assert first_try(tree, "normal_loop_tail").orelse == []
    for values, failures in (
        ((), frozenset()),
        ((1, 2, 3), frozenset()),
        ((1, 2, 3), frozenset({2})),
    ):
        assert work_outcome(
            original["normal_loop_tail"],
            values,
            failures,
        ) == work_outcome(
            rebuilt["normal_loop_tail"],
            values,
            failures,
        )


def try_else_outcome(
    function,
    values,
    work_failure=None,
    callback_failure=None,
):
    events = []

    def work(value):
        events.append(("work", value))
        if value == work_failure:
            raise LookupError(f"work failed: {value}")

    def callback(value):
        events.append(("callback", value))
        if value == callback_failure:
            raise RuntimeError(f"callback failed: {value}")

    def recover(value):
        events.append(("recover", value))

    return capture(
        lambda: function(values, work, callback, recover),
        events,
    )


def test_real_try_else_keeps_business_callback_and_exception_boundary(
    programs,
):
    tree, _, original, rebuilt = programs
    statement = first_try(tree, "real_try_else")
    assert statement.orelse
    assert any(
        isinstance(node, ast.Call)
        for suite_statement in statement.orelse
        for node in ast.walk(suite_statement)
    )

    for values, work_failure, callback_failure in (
        ((1, 2), None, None),
        ((1, 2), 1, None),
        ((1, 2), None, 2),
    ):
        assert try_else_outcome(
            original["real_try_else"],
            values,
            work_failure,
            callback_failure,
        ) == try_else_outcome(
            rebuilt["real_try_else"],
            values,
            work_failure,
            callback_failure,
        )


@pytest.mark.parametrize(
    "name, expected",
    (
        ("match_any", (1, 1, 0, 1)),
        ("conditional_return", (1, 1, 0, 1)),
        ("conditional_break", (0, 1, 1, 1)),
        ("normal_loop_tail", (0, 1, 0, 0)),
    ),
)
def test_loop_terminal_frontier_classifies_complete_owned_gap(name, expected):
    owner, structure, fragments, body_end, handler_index, loop = (
        frontier_candidate(name)
    )
    frontier = structure._protected_loop_terminal_frontier(
        fragments,
        body_end,
        handler_index,
        loop,
    )
    assert frontier is not None
    assert frontier.end_index == handler_index
    assert frontier.owned_blocks == (
        frontier.return_blocks
        | frontier.continue_blocks
        | frontier.break_blocks
    )
    assert (
        len(frontier.return_blocks),
        len(frontier.continue_blocks),
        len(frontier.break_blocks),
        len(frontier.cleanup_offsets),
    ) == expected


def test_loop_terminal_frontier_rejects_business_try_else_gap():
    _, structure, fragments, body_end, handler_index, loop = (
        frontier_candidate("real_try_else")
    )
    assert structure._protected_loop_terminal_frontier(
        fragments,
        body_end,
        handler_index,
        loop,
    ) is None


@pytest.mark.parametrize(
    "corruption",
    (
        "foreign_predecessor",
        "exception_edge",
        "wrong_continue_target",
        "wrong_break_target",
        "business_instruction",
        "return_successor",
        "partial_gap",
    ),
)
def test_loop_terminal_frontier_rejects_unsafe_cfg(corruption):
    name = "conditional_break" if corruption == "wrong_break_target" else (
        "conditional_return"
    )
    owner, structure, fragments, body_end, handler_index, loop = (
        frontier_candidate(name)
    )
    lower = owner.tokens[body_end].offset
    upper = owner.tokens[handler_index].offset
    frontier_blocks = {
        block.index
        for block in owner.cfg.blocks
        if lower <= block.start < upper
    }
    first_frontier = min(
        frontier_blocks,
        key=lambda index: owner.cfg.block(index).start,
    )
    handler_block = owner.cfg.offset_to_block[upper]

    if corruption == "foreign_predecessor":
        owner.cfg.edges += (
            Edge(handler_block, first_frontier, "jump"),
        )
    elif corruption == "exception_edge":
        protected_block = next(
            block.index
            for block in owner.cfg.blocks
            if any(
                fragment.start <= block.start < fragment.end
                for fragment in fragments
            )
        )
        owner.cfg.edges += (
            Edge(protected_block, first_frontier, "exception"),
        )
    elif corruption == "wrong_continue_target":
        jump = next(
            token
            for token in owner.tokens[body_end:handler_index]
            if token.kind.startswith("JUMP_BACKWARD")
        )
        jump.attr = loop.break_target
    elif corruption == "wrong_break_target":
        jump = next(
            token
            for token in owner.tokens[body_end:handler_index]
            if token.kind == "JUMP_FORWARD"
        )
        jump.attr = min(loop.continue_targets)
    elif corruption == "business_instruction":
        owner.tokens[body_end].kind = "LOAD_FAST"
    elif corruption == "return_successor":
        return_block = next(
            block_index
            for block_index in frontier_blocks
            if owner.cfg.block(block_index).terminator == "RETURN_VALUE"
        )
        owner.cfg.edges += (
            Edge(return_block, handler_block, "fallthrough"),
        )
    elif corruption == "partial_gap":
        jump = next(
            token
            for token in owner.tokens[body_end:handler_index]
            if token.kind.startswith("JUMP_BACKWARD")
        )
        jump.kind = "NOP"

    assert structure._protected_loop_terminal_frontier(
        fragments,
        body_end,
        handler_index,
        loop,
    ) is None
