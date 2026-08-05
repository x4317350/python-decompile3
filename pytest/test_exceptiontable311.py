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
from decompyle3.controlflow.exception_structures import (
    ExceptionState311,
    ExceptionStructureDecompiler311,
)
from decompyle3.controlflow.cfg import instruction_target
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
HANDLER_RETURN_SOURCE = (
    ROOT
    / "test"
    / "fixtures311"
    / "except_handler_return.py"
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


def handler_return_root():
    return compile(
        HANDLER_RETURN_SOURCE.read_text(encoding="utf-8"),
        str(HANDLER_RETURN_SOURCE),
        "exec",
    )


def handler_return_code(name):
    return next(
        code
        for code in Scanner311.iter_code_objects(handler_return_root())
        if code.co_name == name
    )


def recover_handler_return_source():
    output = io.StringIO()
    code_deparse(
        handler_return_root(),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def handler_return_candidate(name):
    code = handler_return_code(name)
    scanner = Scanner311()
    tokens, _ = scanner.ingest(code)
    owner = StructuredDecompiler311(
        code,
        [copy(token) for token in tokens],
    )
    structure = ExceptionStructureDecompiler311(owner)
    handler_index = next(
        index
        for index, token in enumerate(owner.tokens)
        if token.kind == "PUSH_EXC_INFO"
        and structure._handler_has_match(index)
    )
    check_index = next(
        index
        for index in range(handler_index + 1, len(owner.tokens))
        if owner.tokens[index].kind == "CHECK_EXC_MATCH"
    )
    jump_index = owner._next_semantic_index(
        check_index + 1,
        len(owner.tokens),
    )
    false_index = owner.offset_to_index[
        instruction_target(owner.tokens[jump_index])
    ]
    binding_index = owner._next_semantic_index(
        jump_index + 1,
        len(owner.tokens),
    )
    binding = owner.tokens[binding_index]
    name_binding = (
        binding.attr
        if isinstance(binding.attr, str)
        else binding.pattr
    ) if binding.kind.startswith("STORE_") else None
    body_start = binding_index + 1
    body_end = structure._clause_structural_end(
        body_start,
        false_index,
        name_binding,
    )
    normal_jump = owner.tokens[handler_index - 1]
    assert normal_jump.kind == "JUMP_FORWARD"
    continuation = instruction_target(normal_jump)
    return (
        owner,
        structure,
        body_start,
        body_end,
        name_binding,
        continuation,
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


def _first_handler(tree, name):
    function = function_node(tree, name)
    return next(
        handler
        for node in ast.walk(function)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if isinstance(handler.type, ast.Name)
        and handler.type.id == "StopIteration"
    )


def test_except_handler_return_protocol_and_cfg_are_unambiguous():
    bare_code = handler_return_code("bare_return")
    scanner = Scanner311()
    bare_tokens, _ = scanner.ingest(bare_code)
    bare_entries = tuple(dis.Bytecode(bare_code).exception_entries)
    assert tuple(scanner.exception_entries) == bare_entries

    return_index = next(
        index
        for index in range(2, len(bare_tokens))
        if bare_tokens[index - 2].kind == "POP_EXCEPT"
        and bare_tokens[index - 1].kind == "LOAD_CONST"
        and bare_tokens[index - 1].attr is None
        and bare_tokens[index].kind == "RETURN_VALUE"
    )
    graph = build_cfg(
        scanner.normalized_instructions,
        decode_exception_table(bare_code),
    )
    return_block = graph.offset_to_block[
        bare_tokens[return_index].offset
    ]
    assert graph.outgoing(return_block) == ()
    assert any(
        token.kind == "JUMP_FORWARD"
        and token.attr > bare_tokens[return_index].offset
        for token in bare_tokens[:return_index]
    )

    pass_scanner = Scanner311()
    pass_tokens, _ = pass_scanner.ingest(
        handler_return_code("empty_pass")
    )
    assert any(
        pass_tokens[index].kind == "POP_EXCEPT"
        and pass_tokens[index + 1].kind == "JUMP_FORWARD"
        for index in range(len(pass_tokens) - 1)
    )


@pytest.mark.parametrize(
    "name",
    [
        "bare_return",
        "explicit_none_return",
        "named_return",
        "nested_return",
        "return_with_else",
        "return_inside_terminal_if",
        "return_in_loop",
        "return_after_nested_handler",
    ],
)
def test_except_handler_return_plan_requires_complete_owned_path(name):
    owner, structure, start, end, binding, continuation = (
        handler_return_candidate(name)
    )
    plan = structure._handler_none_return_plan(
        start,
        end,
        binding,
        continuation,
    )
    assert plan is not None
    assert plan.kind == "return_none"
    assert plan.body_start == start
    assert plan.return_index < owner.offset_to_index[continuation]
    return_block = owner.cfg.offset_to_block[
        owner.tokens[plan.return_index].offset
    ]
    assert owner.cfg.outgoing(return_block) == ()


def test_except_handler_return_plan_accepts_owned_loop_and_with_cleanup():
    owner, structure, start, end, binding, continuation = (
        handler_return_candidate("return_in_for_loop")
    )
    loop_plan = structure._handler_none_return_plan(
        start,
        end,
        binding,
        continuation,
        loop=object(),
    )
    assert loop_plan is not None
    assert any(
        token.kind == "POP_TOP"
        for token in owner.tokens[
            loop_plan.cleanup_start : loop_plan.return_load_index
        ]
    )

    owner, structure, start, end, binding, continuation = (
        handler_return_candidate("return_inside_with")
    )
    assert structure._handler_none_return_plan(
        start,
        end,
        binding,
        continuation,
    ) is None
    semantic = [
        index
        for index in range(start, end)
        if owner.tokens[index].kind != "INTERNAL_EXTENDED_ARG"
    ]
    cleanup_position = max(
        position
        for position, index in enumerate(semantic[:-2])
        if owner.tokens[index].kind == "POP_EXCEPT"
    )
    supplemental = semantic[cleanup_position + 4 : -2]
    owner._suppressed_exception_protocol_offsets.update(
        owner.tokens[index].offset for index in supplemental
    )
    with_plan = structure._handler_none_return_plan(
        start,
        end,
        binding,
        continuation,
    )
    assert with_plan is not None
    assert supplemental


@pytest.mark.parametrize(
    "corruption, expect_refusal",
    [
        ("missing_pop_except", True),
        ("non_none_value", False),
        ("missing_return", False),
        ("normal_successor", True),
        ("foreign_predecessor", True),
        ("return_exception_edge", True),
        ("missing_continuation", False),
        ("unknown_continuation", True),
        ("backward_continuation", True),
        ("named_store_mismatch", True),
        ("named_delete_mismatch", True),
        ("work_limit", True),
    ],
)
def test_except_handler_return_plan_fails_closed(
    corruption,
    expect_refusal,
):
    function_name = (
        "named_return"
        if corruption.startswith("named_") or corruption == "work_limit"
        else "bare_return"
    )
    owner, structure, start, end, binding, continuation = (
        handler_return_candidate(function_name)
    )
    original = structure._handler_none_return_plan(
        start,
        end,
        binding,
        continuation,
    )
    assert original is not None

    candidate_continuation = continuation
    if corruption == "missing_pop_except":
        owner.tokens[original.cleanup_start].kind = "NOP"
    elif corruption == "non_none_value":
        owner.tokens[original.return_load_index].attr = "not-none"
    elif corruption == "missing_return":
        owner.tokens[original.return_index].kind = "NOP"
    elif corruption == "normal_successor":
        return_block = owner.cfg.offset_to_block[
            owner.tokens[original.return_index].offset
        ]
        continuation_block = owner.cfg.offset_to_block[continuation]
        owner.cfg.edges += (
            Edge(return_block, continuation_block, "jump"),
        )
    elif corruption == "foreign_predecessor":
        return_block = owner.cfg.offset_to_block[
            owner.tokens[original.return_index].offset
        ]
        owner.cfg.edges += (
            Edge(owner.cfg.entry, return_block, "jump"),
        )
    elif corruption == "return_exception_edge":
        return_block = owner.cfg.offset_to_block[
            owner.tokens[original.return_index].offset
        ]
        continuation_block = owner.cfg.offset_to_block[continuation]
        owner.cfg.edges += (
            Edge(return_block, continuation_block, "exception"),
        )
    elif corruption == "missing_continuation":
        candidate_continuation = None
    elif corruption == "unknown_continuation":
        candidate_continuation = max(owner.offset_to_index) + 2
    elif corruption == "backward_continuation":
        candidate_continuation = owner.tokens[start].offset
    elif corruption == "named_store_mismatch":
        store = next(
            token
            for token in owner.tokens[
                original.cleanup_start : original.return_load_index
            ]
            if token.kind.startswith("STORE_")
        )
        store.attr = "other"
    elif corruption == "named_delete_mismatch":
        delete = next(
            token
            for token in owner.tokens[
                original.cleanup_start : original.return_load_index
            ]
            if token.kind.startswith("DELETE_")
        )
        delete.attr = "other"
    elif corruption == "work_limit":
        start_block = owner.cfg.offset_to_block[owner.tokens[start].offset]
        return_block = owner.cfg.offset_to_block[
            owner.tokens[original.return_index].offset
        ]
        owner.cfg.edges += tuple(
            Edge(start_block, return_block, "jump") for _ in range(256)
        )
    else:  # pragma: no cover - protects the corruption table itself
        raise AssertionError(corruption)

    assert structure._handler_none_return_plan(
        start,
        end,
        binding,
        candidate_continuation,
    ) is None
    if expect_refusal:
        with pytest.raises(
            Python311ParseError,
            match="Cannot prove except handler None-return ownership",
        ):
            structure._clause_body(
                start,
                end,
                binding,
                None,
                candidate_continuation,
            )


def test_non_none_and_terminal_pass_handlers_do_not_enter_return_plan():
    owner, structure, start, end, binding, continuation = (
        handler_return_candidate("return_value")
    )
    assert structure._handler_none_return_plan(
        start,
        end,
        binding,
        continuation,
    ) is None

    recovered = ast.parse(recover_handler_return_source())
    terminal_handler = _first_handler(recovered, "terminal_pass")
    assert len(terminal_handler.body) == 1
    assert isinstance(terminal_handler.body[0], ast.Pass)


def test_except_handler_none_returns_stay_inside_handlers():
    recovered = recover_handler_return_source()
    tree = ast.parse(recovered)
    compile(tree, "<except-handler-return-recovered>", "exec")

    for name in (
        "bare_return",
        "explicit_none_return",
        "named_return",
        "nested_return",
        "return_with_else",
        "return_inside_terminal_if",
        "return_in_loop",
        "return_in_for_loop",
        "return_inside_with",
        "return_after_nested_handler",
    ):
        handler = _first_handler(tree, name)
        assert isinstance(handler.body[-1], ast.Return)
        assert not any(isinstance(node, ast.Pass) for node in handler.body)

    pass_handler = _first_handler(tree, "empty_pass")
    assert len(pass_handler.body) == 1
    assert isinstance(pass_handler.body[0], ast.Pass)

    terminal_pass = _first_handler(tree, "terminal_pass")
    assert len(terminal_pass.body) == 1
    assert isinstance(terminal_pass.body[0], ast.Pass)

    value_handler = _first_handler(tree, "return_value")
    assert isinstance(value_handler.body[-1], ast.Return)
    assert isinstance(value_handler.body[-1].value, ast.Constant)
    assert value_handler.body[-1].value.value == "stopped"


def _call_with_events(namespace, name, values, *args):
    events = []
    result = namespace[name](*args, iter(values), events)
    return result, events


def test_except_handler_returns_preserve_continuation_behavior():
    original = execute(
        HANDLER_RETURN_SOURCE.read_text(encoding="utf-8"),
        "handler_return_original",
    )
    recovered = execute(
        recover_handler_return_source(),
        "handler_return_recovered",
    )

    for name in (
        "bare_return",
        "explicit_none_return",
        "named_return",
        "real_pass",
        "empty_pass",
        "return_value",
        "nested_return",
        "return_with_else",
        "return_in_loop",
        "return_in_for_loop",
        "return_inside_with",
        "return_after_nested_handler",
    ):
        for values in ((), (7,)):
            expected = _call_with_events(original, name, values)
            actual = _call_with_events(recovered, name, values)
            assert actual == expected

    class CountingStopIterator:
        def __init__(self):
            self.calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.calls += 1
            raise StopIteration

    class WrongExceptionIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("not StopIteration")

    for namespace in (original, recovered):
        iterator = CountingStopIterator()
        events = []
        assert namespace["bare_return"](iterator, events) is None
        assert iterator.calls == 1
        assert events == []

        with pytest.raises(RuntimeError, match="not StopIteration"):
            namespace["bare_return"](WrongExceptionIterator(), events)
        assert events == []

    for mode in ("stop", "value", "key", "other"):
        expected_events = []
        actual_events = []
        expected = original["multiple_handlers"](mode, expected_events)
        actual = recovered["multiple_handlers"](mode, actual_events)
        assert (actual, actual_events) == (expected, expected_events)

    for enabled in (False, True):
        for values in ((), (9,)):
            expected = _call_with_events(
                original,
                "return_inside_terminal_if",
                values,
                enabled,
            )
            actual = _call_with_events(
                recovered,
                "return_inside_terminal_if",
                values,
                enabled,
            )
            assert actual == expected
