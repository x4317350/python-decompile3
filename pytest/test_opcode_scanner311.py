"""Phase 3 raw Scanner coverage for all 110 CPython 3.11 opcodes."""

from __future__ import annotations

import dis
import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from decompyle3.scanner import MalformedBytecodeError, UnknownOpcodeError
from decompyle3.scanners.scanner311 import (
    INLINE_CACHE_ENTRIES_311,
    Scanner311,
)


ROOT = Path(__file__).resolve().parents[1]
CORPUS_GENERATOR_PATH = (
    ROOT / "test" / "bytecode_3.11" / "generate.py"
)
OPCODE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "opcode_matrix.json"
)
SOURCE_DIR = ROOT / "test" / "simple_source" / "311"
SCANNER_TEST_NODE = (
    "pytest/test_opcode_scanner311.py::"
    "test_each_opcode_matches_cpython_dis"
)

SPEC = importlib.util.spec_from_file_location(
    "generate_scanner_corpus311",
    CORPUS_GENERATOR_PATH,
)
assert SPEC is not None and SPEC.loader is not None
corpus_generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(corpus_generator)

OPCODE_MATRIX = json.loads(
    OPCODE_MATRIX_PATH.read_text(encoding="utf-8")
)
OPCODE_ITEMS = tuple(
    sorted(OPCODE_MATRIX["opcodes"], key=lambda item: item["opcode"])
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 Scanner matrix tests require CPython 3.11",
)


@lru_cache(maxsize=None)
def compile_fixture(relative_path):
    return corpus_generator.compile_source(ROOT / relative_path)


@lru_cache(maxsize=None)
def locate_opcode(opcode_name):
    item = next(
        item for item in OPCODE_ITEMS if item["name"] == opcode_name
    )
    root_code = compile_fixture(item["source_fixture"])
    for code in Scanner311.iter_code_objects(root_code):
        scanner = Scanner311()
        scanner.ingest_raw(code)
        native = tuple(
            dis.get_instructions(
                code,
                show_caches=True,
                adaptive=False,
            )
        )
        for index, instruction in enumerate(scanner.insts):
            if instruction.opname == opcode_name:
                return item, code, scanner, index, native[index]
    raise AssertionError(
        f"{opcode_name} is absent from {item['source_fixture']}"
    )


@pytest.mark.parametrize(
    "opcode_name",
    [item["name"] for item in OPCODE_ITEMS],
    ids=[
        f"{item['opcode']:03d}-{item['name']}"
        for item in OPCODE_ITEMS
    ],
)
def test_each_opcode_matches_cpython_dis(opcode_name):
    item, code, scanner, index, standard = locate_opcode(opcode_name)
    actual = scanner.insts[index]
    token = scanner.raw_tokens[index]

    assert len(OPCODE_ITEMS) == 110
    assert item["opcode"] == dis.opmap[opcode_name]
    assert scanner.opc.opmap[opcode_name] == item["opcode"]
    assert actual.opcode == standard.opcode == item["opcode"]
    assert actual.opname == standard.opname == opcode_name
    assert actual.offset == standard.offset == token.offset
    assert actual.offset % 2 == 0
    assert actual.inst_size == 2
    assert code.co_code[actual.offset] == item["opcode"]
    assert scanner.raw_opargs[actual.offset] == code.co_code[
        actual.offset + 1
    ]
    assert token.kind == opcode_name
    assert token.op == item["opcode"]
    assert tuple(actual.positions) == tuple(standard.positions)
    assert actual.is_jump_target == standard.is_jump_target

    if opcode_name == "CACHE":
        assert actual.arg is None
        assert standard.arg == scanner.raw_opargs[actual.offset] == 0
    else:
        assert actual.arg == standard.arg

    if actual.opcode in (scanner.opc.JREL_OPS | scanner.opc.JABS_OPS):
        assert actual.argval == standard.argval
    elif actual.opcode in scanner.opc.CONST_OPS and opcode_name != "KW_NAMES":
        assert actual.argval == standard.argval
    elif actual.opcode in (
        scanner.opc.NAME_OPS
        | scanner.opc.LOCAL_OPS
        | scanner.opc.FREE_OPS
    ):
        assert actual.argval == standard.argval

    assert item["layers"]["scanner"] == "pass"
    assert SCANNER_TEST_NODE in item["tests"]


def test_inline_cache_layout_and_owner_mapping_match_cpython():
    native_layout = {
        name: dis._inline_cache_entries[opcode]
        for name, opcode in dis.opmap.items()
        if dis._inline_cache_entries[opcode]
    }
    assert INLINE_CACHE_ENTRIES_311 == native_layout

    root_code = compile_fixture(
        "test/simple_source/311/00_expressions.py"
    )
    saw_cache = False
    for code in Scanner311.iter_code_objects(root_code):
        scanner = Scanner311()
        scanner.ingest(code)
        normalized = scanner.normalized_instructions
        for raw in scanner.insts:
            if raw.opname != "CACHE":
                continue
            saw_cache = True
            owner_index = scanner.cache_owner[raw.offset]
            owner = normalized[owner_index]
            assert raw.offset in owner.cache_offsets
            assert (
                len(owner.cache_offsets)
                == INLINE_CACHE_ENTRIES_311[owner.original_opname]
            )
            assert scanner.physical_to_logical[raw.offset] == owner_index

    assert saw_cache


def test_extended_arg_preserves_physical_units_and_combined_argument():
    _, _, scanner, index, standard = locate_opcode("EXTENDED_ARG")
    extended = scanner.insts[index]
    following = scanner.insts[index + 1]

    assert extended.offset == standard.offset
    assert following.offset == extended.offset + 2
    assert scanner.raw_tokens[index].offset == extended.offset
    assert scanner.raw_tokens[index + 1].offset == following.offset
    assert following.has_extended_arg
    assert following.arg == (
        scanner.raw_opargs[extended.offset] << 8
        | scanner.raw_opargs[following.offset]
    )


def test_all_jump_targets_match_dis_and_target_real_instructions():
    saw_forward = False
    saw_backward = False
    for source in corpus_generator.corpus_sources():
        root_code = corpus_generator.compile_source(source)
        for code in Scanner311.iter_code_objects(root_code):
            scanner = Scanner311()
            scanner.ingest_raw(code)
            native = tuple(
                dis.get_instructions(
                    code,
                    show_caches=True,
                    adaptive=False,
                )
            )
            by_offset = {
                instruction.offset: instruction
                for instruction in scanner.insts
            }
            for actual, standard in zip(scanner.insts, native):
                assert actual.is_jump_target == standard.is_jump_target
                if actual.opcode not in (
                    scanner.opc.JREL_OPS | scanner.opc.JABS_OPS
                ):
                    continue
                assert actual.argval == standard.argval
                assert actual.argval in by_offset
                assert by_offset[actual.argval].opname != "CACHE"
                assert by_offset[actual.argval].is_jump_target
                saw_forward |= actual.argval > actual.offset
                saw_backward |= actual.argval < actual.offset

    assert saw_forward
    assert saw_backward


def test_positions_lines_and_nested_code_objects_are_preserved():
    sources = (
        SOURCE_DIR / "01_functions_classes.py",
        SOURCE_DIR / "03_comprehensions.py",
        ROOT
        / "test"
        / "bytecode_3.11"
        / "opcode_fixtures"
        / "scope"
        / "load_classderef.py",
    )
    nested_names = set()
    for source in sources:
        root_code = corpus_generator.compile_source(source)
        scanner = Scanner311()
        scanner.ingest_raw(root_code)

        expected = []

        def walk(code):
            expected.append(code)
            for constant in code.co_consts:
                if isinstance(constant, type(code)):
                    walk(constant)

        walk(root_code)
        assert scanner.code_objects == tuple(expected)
        assert scanner.nested_code_objects == tuple(expected[1:])
        nested_names.update(
            getattr(code, "co_qualname", code.co_name)
            for code in scanner.nested_code_objects
        )

        for code in scanner.code_objects:
            nested_scanner = Scanner311()
            nested_scanner.ingest_raw(code)
            assert nested_scanner.positions == tuple(code.co_positions())
            assert nested_scanner.line_ranges == tuple(code.co_lines())
            assert len(nested_scanner.positions) == len(code.co_code) // 2
            assert nested_scanner.code_metadata["co_name"] == code.co_name
            assert (
                nested_scanner.code_metadata["code_length"]
                == len(code.co_code)
            )

    assert any("<lambda>" in name for name in nested_names)
    assert any("<listcomp>" in name for name in nested_names)
    assert any("Accumulator" in name for name in nested_names)
    assert any("Captures" in name for name in nested_names)


def test_exception_table_matches_cpython_dis():
    source = SOURCE_DIR / "05_exceptions_with.py"
    root_code = corpus_generator.compile_source(source)
    saw_entries = False
    for code in Scanner311.iter_code_objects(root_code):
        scanner = Scanner311()
        scanner.ingest_raw(code)
        expected = tuple(dis.Bytecode(code).exception_entries)

        assert scanner.exception_table == code.co_exceptiontable
        assert scanner.exception_entries == expected
        saw_entries |= bool(expected)

    assert saw_entries


def test_scanner_rejects_unknown_odd_and_illegal_cache_bytecode():
    scanner = Scanner311()

    with pytest.raises(UnknownOpcodeError, match="opcode 255 at offset 0"):
        scanner._validate_bytecode(b"\xff\x00", "unknown")
    with pytest.raises(MalformedBytecodeError, match="odd co_code length"):
        scanner._validate_bytecode(bytes([dis.opmap["RESUME"]]), "odd")
    with pytest.raises(MalformedBytecodeError, match="CACHE.*no owner"):
        scanner._validate_bytecode(
            bytes([dis.opmap["CACHE"], 0, dis.opmap["RESUME"], 0]),
            "orphan-cache",
        )
    with pytest.raises(MalformedBytecodeError, match="expected CACHE"):
        scanner._validate_bytecode(
            bytes(
                [
                    dis.opmap["LOAD_GLOBAL"],
                    0,
                    dis.opmap["RETURN_VALUE"],
                    0,
                ]
            ),
            "missing-cache",
        )
