"""Phase 6 acceptance tests for CPython 3.11 exception-table recovery."""

from __future__ import annotations

import ast
import asyncio
import dis
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow import (
    Edge,
    ExceptionRegion,
    ExceptionTableDecodeError,
    build_cfg,
    build_exception_region_map,
    decode_exception_table,
)
from decompyle3.controlflow.exception_structures import ExceptionState311
from decompyle3.controlflow.exceptiontable311 import (
    decode_exception_table_bytes,
    validate_exception_regions,
)
from decompyle3.parsers.p311.base import UnsupportedPython311ControlFlow
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


SOURCE = ROOT / "test" / "simple_source" / "311" / "05_exceptions_with.py"

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Exception-table tests require CPython 3.11",
)


def native_root():
    return compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
    )


def native_code(name):
    return next(
        code
        for code in Scanner311.iter_code_objects(native_root())
        if code.co_qualname == name
    )


def recover_source(tmp_path):
    bytecode = tmp_path / "05_exceptions_with.pyc"
    version, _, _, code, implementation, *_ = compile_source(
        SOURCE,
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


def execute(source, name):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


class ExplodingDivision:
    def __rtruediv__(self, other):
        raise KeyError("division failed")


class SyncResource:
    def __init__(self, value, events, name="resource", suppress=False):
        self.value = value
        self.events = events
        self.name = name
        self.suppress = suppress

    def __enter__(self):
        self.events.append(f"{self.name}:enter")
        return self

    def __exit__(self, exc_type, exc, traceback):
        exception_name = exc_type.__name__ if exc_type is not None else "none"
        self.events.append(f"{self.name}:exit:{exception_name}")
        return self.suppress

    def fail(self):
        raise ValueError("context failed")


class AsyncResource:
    def __init__(self, value, events):
        self.value = value
        self.events = events

    async def __aenter__(self):
        self.events.append("async:enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        exception_name = exc_type.__name__ if exc_type is not None else "none"
        self.events.append(f"async:exit:{exception_name}")
        return False


class AsyncValues:
    def __init__(self, values):
        self.values = iter(values)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.values)
        except StopIteration:
            raise StopAsyncIteration


def test_exception_table_decoder_matches_dis_exactly():
    for code in Scanner311.iter_code_objects(native_root()):
        expected = tuple(
            ExceptionRegion(
                start=entry.start,
                end=entry.end,
                target=entry.target,
                depth=entry.depth,
                lasti=entry.lasti,
            )
            for entry in dis.Bytecode(code).exception_entries
        )
        assert decode_exception_table(code) == expected


def test_exception_table_decoder_rejects_truncation_and_invalid_ranges():
    with pytest.raises(ExceptionTableDecodeError, match="Truncated"):
        decode_exception_table_bytes(bytes([0x40]))

    with pytest.raises(ExceptionTableDecodeError, match="end offset"):
        validate_exception_regions(
            [ExceptionRegion(2, 2, 4, 0, False)],
            code_length=8,
        )
    with pytest.raises(ExceptionTableDecodeError, match="handler target"):
        validate_exception_regions(
            [ExceptionRegion(0, 2, 8, 0, False)],
            code_length=8,
        )


def test_exception_region_map_indexes_ranges_and_shared_handlers():
    entries = decode_exception_table(native_code("guarded_division"))
    regions = build_exception_region_map(entries)

    assert regions.entries == tuple(sorted(entries))
    assert regions.handler_targets == tuple(
        sorted({entry.target for entry in entries})
    )
    for entry in entries:
        assert entry in regions.starting_at(entry.start)
        assert entry in regions.covering(entry.start)
        assert entry in regions.by_target[entry.target]
        assert not entry.contains(entry.end)


def test_cfg_contains_every_exception_edge_and_normal_path():
    code = native_code("guarded_division")
    scanner = Scanner311()
    scanner.ingest(code)
    entries = decode_exception_table(code)
    graph = build_cfg(scanner.normalized_instructions, entries)

    exception_edges = {
        edge for edge in graph.edges if edge.kind == "exception"
    }
    assert exception_edges
    assert any(edge.kind != "exception" for edge in graph.edges)
    assert all(graph.block_at(entry.target).start == entry.target for entry in entries)

    for entry in entries:
        handler = graph.offset_to_block[entry.target]
        protected = [
            block
            for block in graph.blocks
            if entry.start <= block.start < entry.end
        ]
        assert protected
        assert all(
            Edge(block.index, handler, "exception") in exception_edges
            for block in protected
        )

    assert ":exception" in graph.format()
    assert graph.reachable_blocks


def test_exception_protocol_opcodes_and_state_are_represented():
    kinds = set()
    for code in Scanner311.iter_code_objects(native_root()):
        scanner = Scanner311()
        scanner.ingest(code)
        kinds.update(
            instruction.kind
            for instruction in scanner.normalized_instructions
        )

    assert {
        "BEFORE_ASYNC_WITH",
        "BEFORE_WITH",
        "CHECK_EXC_MATCH",
        "POP_EXCEPT",
        "PUSH_EXC_INFO",
        "RERAISE",
        "WITH_EXCEPT_START",
    } <= kinds
    entry = decode_exception_table(native_code("guarded_division"))[0]
    assert ExceptionState311(entry.target, entry.depth, entry.lasti) == (
        ExceptionState311(
            handler_offset=entry.target,
            depth=entry.depth,
            lasti=entry.lasti,
        )
    )


def test_phase6_pyc_deparses_reparses_and_recovers_all_structures(tmp_path):
    recovered = recover_source(tmp_path)
    tree = ast.parse(recovered)
    compile(tree, "<recovered-phase6>", "exec")

    guarded = function_node(tree, "guarded_division")
    guarded_try = next(node for node in guarded.body if isinstance(node, ast.Try))
    assert len(guarded_try.handlers) == 1
    assert guarded_try.handlers[0].name == "error"
    assert guarded_try.orelse
    assert guarded_try.finalbody

    nested = function_node(tree, "nested_exception")
    outer_try = next(node for node in nested.body if isinstance(node, ast.Try))
    assert isinstance(outer_try.body[0], ast.Try)

    multiple = function_node(tree, "multiple_handlers")
    multiple_try = next(node for node in multiple.body if isinstance(node, ast.Try))
    assert len(multiple_try.handlers) == 3
    assert multiple_try.handlers[-1].type is None

    cleanup = function_node(tree, "cleanup_only")
    cleanup_try = next(node for node in cleanup.body if isinstance(node, ast.Try))
    assert not cleanup_try.handlers
    assert cleanup_try.finalbody

    returning = function_node(tree, "finally_return")
    returning_try = next(
        node for node in returning.body if isinstance(node, ast.Try)
    )
    assert any(
        isinstance(node, ast.Return)
        for statement in returning_try.finalbody
        for node in ast.walk(statement)
    )

    breaking = function_node(tree, "finally_break")
    continuing = function_node(tree, "finally_continue")
    assert any(
        isinstance(node, ast.Break)
        for statement in ast.walk(breaking)
        if isinstance(statement, ast.Try)
        for final in statement.finalbody
        for node in ast.walk(final)
    )
    assert any(
        isinstance(node, ast.Continue)
        for statement in ast.walk(continuing)
        if isinstance(statement, ast.Try)
        for final in statement.finalbody
        for node in ast.walk(final)
    )

    multiple_with = function_node(tree, "use_two_contexts")
    multiple_with_node = next(
        node for node in multiple_with.body if isinstance(node, ast.With)
    )
    assert len(multiple_with_node.items) == 2

    targetless = function_node(tree, "use_context_without_target")
    targetless_with = next(
        node for node in targetless.body if isinstance(node, ast.With)
    )
    assert targetless_with.items[0].optional_vars is None

    nested_with = function_node(tree, "use_nested_context")
    outer_with = next(
        node for node in nested_with.body if isinstance(node, ast.With)
    )
    assert isinstance(outer_with.body[0], ast.With)

    assert any(
        isinstance(node, ast.AsyncWith)
        for node in ast.walk(function_node(tree, "use_async_context"))
    )
    assert any(
        isinstance(node, ast.AsyncFor)
        for node in ast.walk(function_node(tree, "consume_async"))
    )
    async_for_else = next(
        node
        for node in ast.walk(function_node(tree, "consume_until_negative"))
        if isinstance(node, ast.AsyncFor)
    )
    assert async_for_else.orelse


def test_recovered_exception_structures_preserve_behavior(tmp_path):
    original = execute(
        SOURCE.read_text(encoding="utf-8"),
        "phase6_original",
    )
    recovered = execute(recover_source(tmp_path), "phase6_recovered")

    for arguments in ((8, 2), (8, 0)):
        assert recovered["guarded_division"](*arguments) == original[
            "guarded_division"
        ](*arguments)
    for value in ("12", None, "invalid"):
        assert recovered["nested_exception"](value) == original[
            "nested_exception"
        ](value)
    for value in (2, 0, None, ExplodingDivision()):
        assert recovered["multiple_handlers"](value) == original[
            "multiple_handlers"
        ](value)

    assert recovered["cleanup_only"]([]) == original["cleanup_only"]([])
    assert recovered["finally_return"]("value") == original[
        "finally_return"
    ]("value")
    assert recovered["finally_break"]([1, 2, 3]) == original[
        "finally_break"
    ]([1, 2, 3])
    assert recovered["finally_continue"]([1, 2, 3]) == original[
        "finally_continue"
    ]([1, 2, 3])


def sync_context_behavior(namespace):
    events = []
    value = namespace["use_context"](SyncResource(7, events))

    recorded = []
    namespace["record_context"](SyncResource(9, recorded), recorded)

    targetless = []
    namespace["use_context_without_target"](
        SyncResource(0, targetless, "targetless"),
        targetless,
    )

    multiple_events = []
    multiple = namespace["use_two_contexts"](
        SyncResource(2, multiple_events, "left"),
        SyncResource(5, multiple_events, "right"),
    )

    nested_events = []
    nested = namespace["use_nested_context"](
        SyncResource(3, nested_events, "outer"),
        SyncResource(4, nested_events, "inner"),
    )

    failure_events = []
    with pytest.raises(ValueError, match="context failed"):
        namespace["context_failure"](
            SyncResource(0, failure_events, "failure"),
        )

    suppressed_events = []
    suppressed = namespace["context_failure"](
        SyncResource(
            0,
            suppressed_events,
            "suppressed",
            suppress=True,
        )
    )
    return (
        value,
        events,
        recorded,
        targetless,
        multiple,
        multiple_events,
        nested,
        nested_events,
        failure_events,
        suppressed,
        suppressed_events,
    )


def test_recovered_with_structures_preserve_exit_behavior(tmp_path):
    original = execute(
        SOURCE.read_text(encoding="utf-8"),
        "phase6_with_original",
    )
    recovered = execute(recover_source(tmp_path), "phase6_with_recovered")

    assert sync_context_behavior(recovered) == sync_context_behavior(original)


async def async_behavior(namespace):
    events = []
    value = await namespace["use_async_context"](
        AsyncResource(11, events)
    )
    recorded = []
    result = await namespace["async_record_context"](
        AsyncResource(13, recorded),
        recorded,
    )
    consumed = await namespace["consume_async"](
        AsyncValues([1, 2, 3])
    )
    exhausted = await namespace["consume_until_negative"](
        AsyncValues([1, 2, 3])
    )
    broken = await namespace["consume_until_negative"](
        AsyncValues([1, -1, 3])
    )
    return value, events, result, recorded, consumed, exhausted, broken


def test_recovered_async_with_and_async_for_preserve_behavior(tmp_path):
    original = execute(
        SOURCE.read_text(encoding="utf-8"),
        "phase6_async_original",
    )
    recovered = execute(recover_source(tmp_path), "phase6_async_recovered")

    assert asyncio.run(async_behavior(recovered)) == asyncio.run(
        async_behavior(original)
    )


def test_uncertain_exception_group_structure_fails_closed():
    source = (
        "def guarded():\n"
        "    try:\n"
        "        raise ExceptionGroup('group', [ValueError()])\n"
        "    except* ValueError:\n"
        "        handled = True\n"
    )
    output = io.StringIO()
    with pytest.raises(
        UnsupportedPython311ControlFlow,
        match=r"CHECK_EG_MATCH.*offset",
    ):
        code_deparse(
            compile(source, "<unsupported-except-star>", "exec"),
            out=output,
            version=(3, 11),
            python_implementation=PythonImplementation.CPython,
        )
