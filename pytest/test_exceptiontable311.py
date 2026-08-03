"""Phase 6 acceptance tests for CPython 3.11 exception-table recovery."""

from __future__ import annotations

import ast
import asyncio
import dis
import io
import sys
from copy import copy
from dataclasses import replace

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
from decompyle3.parsers.p311.base import Python311ParseError
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from decompyle3.controlflow.structures import StructuredDecompiler311
from support311 import ROOT, compile_source


SOURCE = ROOT / "test" / "simple_source" / "311" / "05_exceptions_with.py"
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


def empty_star_root():
    return compile(
        EMPTY_STAR_SOURCE.read_text(encoding="utf-8"),
        str(EMPTY_STAR_SOURCE),
        "exec",
    )


def empty_star_code(name):
    return next(
        code
        for code in Scanner311.iter_code_objects(empty_star_root())
        if code.co_name == name
    )


def terminal_star_root():
    return compile(
        TERMINAL_STAR_SOURCE.read_text(encoding="utf-8"),
        str(TERMINAL_STAR_SOURCE),
        "exec",
    )


def terminal_star_code(name):
    return next(
        code
        for code in Scanner311.iter_code_objects(terminal_star_root())
        if code.co_name == name
    )


def normalized_protocol(name):
    scanner = Scanner311()
    scanner.ingest(empty_star_code(name))
    instructions = scanner.normalized_instructions
    start = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.kind == "CHECK_EG_MATCH"
    )
    end = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.kind == "PREP_RERAISE_STAR"
    )
    return tuple(
        (
            instruction.offset,
            instruction.kind,
            instruction.argval,
            instruction.target,
        )
        for instruction in instructions[start : end + 1]
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


def test_except_star_empty_body_normalized_protocol_is_stable():
    assert normalized_protocol("empty_handler") == (
        (28, "CHECK_EG_MATCH", None, None),
        (30, "COPY_STACK", 1, None),
        (32, "POP_JUMP_FORWARD_IF_NONE", 46, 46),
        (34, "POP_TOP", None, None),
        (36, "JUMP_FORWARD", 44, 44),
        (38, "LIST_APPEND", 3, None),
        (40, "POP_TOP", None, None),
        (42, "JUMP_FORWARD", 48, 48),
        (44, "JUMP_FORWARD", 48, 48),
        (46, "POP_TOP", None, None),
        (48, "LIST_APPEND", 1, None),
        (50, "PREP_RERAISE_STAR", None, None),
    )
    assert normalized_protocol("empty_named_handler") == (
        (28, "CHECK_EG_MATCH", None, None),
        (30, "COPY_STACK", 1, None),
        (32, "POP_JUMP_FORWARD_IF_NONE", 58, 58),
        (34, "STORE_FAST", "error", None),
        (36, "LOAD_CONST", None, None),
        (38, "STORE_FAST", "error", None),
        (40, "DELETE_FAST", "error", None),
        (42, "JUMP_FORWARD", 56, 56),
        (44, "LOAD_CONST", None, None),
        (46, "STORE_FAST", "error", None),
        (48, "DELETE_FAST", "error", None),
        (50, "LIST_APPEND", 3, None),
        (52, "POP_TOP", None, None),
        (54, "JUMP_FORWARD", 60, 60),
        (56, "JUMP_FORWARD", 60, 60),
        (58, "POP_TOP", None, None),
        (60, "LIST_APPEND", 1, None),
        (62, "PREP_RERAISE_STAR", None, None),
    )


def test_except_star_empty_body_has_no_depth_four_region():
    expected = {
        "empty_handler": (
            (4, 8, 8, 0, False),
            (8, 58, 68, 1, True),
        ),
        "empty_named_handler": (
            (4, 8, 8, 0, False),
            (8, 70, 80, 1, True),
        ),
    }
    for name, entries in expected.items():
        actual = tuple(
            (entry.start, entry.end, entry.target, entry.depth, entry.lasti)
            for entry in dis.Bytecode(empty_star_code(name)).exception_entries
        )
        assert actual == entries
        assert not any(entry[3] >= 4 for entry in actual)

    nonempty_entries = tuple(
        dis.Bytecode(empty_star_code("nonempty_handler")).exception_entries
    )
    assert any(
        entry.start == 36 and entry.end == 78 and entry.depth >= 4
        for entry in nonempty_entries
    )


def test_except_star_empty_body_recovers_only_after_protocol_match():
    expected_names = {
        "empty_handler": None,
        "empty_named_handler": "error",
    }
    for name, expected_name in expected_names.items():
        output = io.StringIO()
        code_deparse(
            empty_star_code(name),
            out=output,
            version=(3, 11),
            python_implementation=PythonImplementation.CPython,
        )
        recovered = ast.parse(output.getvalue())
        statement = next(
            node
            for node in ast.walk(recovered)
            if isinstance(node, ast.TryStar)
        )
        assert statement.handlers[0].name == expected_name
        assert len(statement.handlers[0].body) == 1
        assert isinstance(statement.handlers[0].body[0], ast.Pass)

    output = io.StringIO()
    code_deparse(
        empty_star_code("nonempty_handler"),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    recovered = ast.parse(output.getvalue())
    statement = next(
        node for node in ast.walk(recovered) if isinstance(node, ast.TryStar)
    )
    assert statement.handlers[0].body


def _corrupted_empty_star_decompiler(name, mutation):
    code = empty_star_code(name)
    scanner = Scanner311()
    tokens, _ = scanner.ingest(code)
    tokens = [copy(token) for token in tokens]
    by_offset = {token.offset: token for token in tokens}
    mutation(by_offset)
    return StructuredDecompiler311(code, tokens)


@pytest.mark.parametrize(
    "name, mutation",
    [
        (
            "empty_handler",
            lambda tokens: setattr(tokens[36], "attr", 48),
        ),
        (
            "empty_handler",
            lambda tokens: setattr(tokens[40], "kind", "NOP"),
        ),
        (
            "empty_handler",
            lambda tokens: setattr(tokens[38], "attr", 2),
        ),
        (
            "empty_handler",
            lambda tokens: setattr(tokens[42], "attr", 44),
        ),
        (
            "empty_named_handler",
            lambda tokens: setattr(tokens[48], "attr", "other"),
        ),
        (
            "empty_handler",
            lambda tokens: setattr(tokens[42], "attr", 52),
        ),
        (
            "empty_handler",
            lambda tokens: (
                setattr(tokens[40], "kind", "LOAD_CONST"),
                setattr(tokens[40], "attr", None),
            ),
        ),
    ],
    ids=(
        "normal-jump-target",
        "missing-pop-top",
        "list-append-depth",
        "exception-continuation",
        "cleanup-name",
        "jump-past-prep-reraise-star",
        "unknown-source-token",
    ),
)
def test_except_star_empty_body_protocol_corruption_fails_closed(
    name,
    mutation,
):
    decompiler = _corrupted_empty_star_decompiler(name, mutation)
    with pytest.raises(
        Python311ParseError,
        match=(
            r"except\* clause body has neither a protected region "
            r"nor a valid empty-body protocol"
        ),
    ) as raised:
        decompiler.decompile_body()

    assert raised.value.offset == 36
    assert raised.value.code_name == name
    assert raised.value.version == (3, 11)


def test_terminal_except_star_cleanup_protocol_is_stable():
    code = terminal_star_code("terminal_raise")
    scanner = Scanner311()
    scanner.ingest(code)
    instructions = scanner.normalized_instructions
    prep_index = next(
        index
        for index, instruction in enumerate(instructions)
        if instruction.kind == "PREP_RERAISE_STAR"
    )
    cleanup = instructions[prep_index:]

    assert tuple((item.kind, item.argval) for item in cleanup) == (
        ("PREP_RERAISE_STAR", None),
        ("COPY_STACK", 1),
        ("POP_JUMP_FORWARD_IF_NOT_NONE", 62),
        ("POP_TOP", None),
        ("POP_EXCEPT", None),
        ("LOAD_CONST", None),
        ("RETURN_VALUE", None),
        ("SWAP_STACK", 2),
        ("POP_EXCEPT", None),
        ("RERAISE", 0),
        ("COPY_STACK", 3),
        ("POP_EXCEPT", None),
        ("RERAISE", 1),
    )
    entries = tuple(dis.Bytecode(code).exception_entries)
    assert any(
        entry.depth == 1
        and entry.lasti
        and entry.end == 56
        and entry.target == 68
        for entry in entries
    )


def _corrupted_terminal_star_decompiler(mutation):
    code = terminal_star_code("terminal_raise")
    scanner = Scanner311()
    tokens, _ = scanner.ingest(code)
    owner = StructuredDecompiler311(
        code,
        [copy(token) for token in tokens],
    )
    prep_index = next(
        index
        for index, token in enumerate(owner.tokens)
        if token.kind == "PREP_RERAISE_STAR"
    )
    mutation(owner, prep_index)
    return owner, owner.tokens[prep_index].offset


def _change_terminal_cleanup_region_target(owner, prep_index):
    handler_offset = next(
        token.offset for token in owner.tokens if token.kind == "PUSH_EXC_INFO"
    )
    wrong_target = owner.tokens[prep_index + 7].offset
    owner.exception_regions = tuple(
        replace(region, target=wrong_target)
        if region.depth == 1
        and region.lasti
        and region.start >= handler_offset
        else region
        for region in owner.exception_regions
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda owner, prep: setattr(owner.tokens[prep + 1], "attr", 2),
        lambda owner, prep: setattr(
            owner.tokens[prep + 2],
            "attr",
            owner.tokens[prep + 10].offset,
        ),
        lambda owner, prep: setattr(owner.tokens[prep + 3], "kind", "NOP"),
        lambda owner, prep: setattr(owner.tokens[prep + 5], "attr", 1),
        lambda owner, prep: setattr(owner.tokens[prep + 6], "kind", "NOP"),
        lambda owner, prep: setattr(owner.tokens[prep + 7], "attr", 3),
        lambda owner, prep: setattr(owner.tokens[prep + 9], "attr", 1),
        lambda owner, prep: setattr(owner.tokens[prep + 10], "attr", 2),
        lambda owner, prep: setattr(owner.tokens[prep + 12], "attr", 0),
        lambda owner, prep: owner.tokens.append(copy(owner.tokens[-1])),
        _change_terminal_cleanup_region_target,
    ],
    ids=(
        "copy-depth",
        "conditional-target",
        "missing-pop-top",
        "non-none-return",
        "missing-return",
        "swap-depth",
        "reraise-zero-argument",
        "outer-copy-depth",
        "reraise-one-argument",
        "trailing-token",
        "exception-table-target",
    ),
)
def test_terminal_except_star_cleanup_corruption_fails_closed(mutation):
    decompiler, prep_offset = _corrupted_terminal_star_decompiler(mutation)
    with pytest.raises(
        Python311ParseError,
        match=(
            r"except\* cleanup has neither a normal continuation "
            r"nor a valid terminal protocol"
        ),
    ) as raised:
        decompiler.decompile_body()

    assert raised.value.offset == prep_offset
    assert raised.value.code_name == "terminal_raise"
    assert raised.value.version == (3, 11)


def test_terminal_except_star_else_remains_a_separate_fail_closed_shape():
    source = """
def terminal_else(group, events):
    try:
        if group is not None:
            raise group
    except* ValueError:
        pass
    else:
        events.append("else")
"""
    code = next(
        nested
        for nested in Scanner311.iter_code_objects(
            compile(source, "<terminal-except-star-else>", "exec")
        )
        if nested.co_name == "terminal_else"
    )
    output = io.StringIO()
    with pytest.raises(
        Python311ParseError,
        match=r"except\* cleanup has neither a normal continuation",
    ) as raised:
        code_deparse(
            code,
            out=output,
            version=(3, 11),
            python_implementation=PythonImplementation.CPython,
        )
    assert raised.value.offset == 56


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
