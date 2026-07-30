"""Phase 4 Normalizer coverage for all 110 CPython 3.11 opcodes."""

from __future__ import annotations

import dis
import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest

from decompyle3.scanner import (
    BytecodeNormalizationError,
    InvalidJumpTargetError,
    StackDepthError,
    UnsupportedSpecializedOpcodeError,
)
from decompyle3.scanners.normalize311 import (
    INTERNAL_OPNAMES,
    SPECIALIZED_TO_BASE,
    Normalizer311,
)
from decompyle3.scanners.scanner311 import Scanner311


ROOT = Path(__file__).resolve().parents[1]
CORPUS_GENERATOR_PATH = (
    ROOT / "test" / "bytecode_3.11" / "generate.py"
)
OPCODE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "opcode_matrix.json"
)
NORMALIZER_TEST_NODE = (
    "pytest/test_opcode_normalizer311.py::"
    "test_each_opcode_has_stable_normalized_contract"
)

SPEC = importlib.util.spec_from_file_location(
    "generate_normalizer_corpus311",
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
    reason="CPython 3.11 Normalizer matrix tests require CPython 3.11",
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
        raw_scanner = Scanner311()
        raw_scanner.ingest_raw(code)
        for raw in raw_scanner.insts:
            if raw.opname != opcode_name:
                continue
            scanner = Scanner311()
            tokens, _ = scanner.ingest(code)
            return item, code, scanner, tokens, raw
    raise AssertionError(
        f"{opcode_name} is absent from {item['source_fixture']}"
    )


def native_stack_effect(opcode, argument, *, jump=None):
    keywords = {} if jump is None else {"jump": jump}
    if opcode < dis.HAVE_ARGUMENT:
        return dis.stack_effect(opcode, **keywords)
    return dis.stack_effect(opcode, argument, **keywords)


def scan_named_code(relative_path, qualname):
    root_code = compile_fixture(relative_path)
    code = next(
        nested
        for nested in Scanner311.iter_code_objects(root_code)
        if nested.co_qualname == qualname
    )
    scanner = Scanner311()
    scanner.ingest(code)
    return scanner


@pytest.mark.parametrize(
    "opcode_name",
    [item["name"] for item in OPCODE_ITEMS],
    ids=[
        f"{item['opcode']:03d}-{item['name']}"
        for item in OPCODE_ITEMS
    ],
)
def test_each_opcode_has_stable_normalized_contract(opcode_name):
    item, _, scanner, tokens, raw = locate_opcode(opcode_name)
    expected_status = (
        "internal_consumed"
        if opcode_name in INTERNAL_OPNAMES
        else "pass"
    )

    assert len(OPCODE_ITEMS) == 110
    assert item["layers"]["scanner"] == "pass"
    assert item["layers"]["normalizer"] == expected_status
    assert NORMALIZER_TEST_NODE in item["tests"]

    if opcode_name == "CACHE":
        owner_index = scanner.cache_owner[raw.offset]
        owner = scanner.normalized_instructions[owner_index]
        assert scanner.physical_to_logical[raw.offset] == owner_index
        assert raw.offset in owner.cache_offsets
        assert all(
            instruction.original_opname != "CACHE"
            for instruction in scanner.normalized_instructions
        )
        return

    logical_index = scanner.physical_to_logical[raw.offset]
    normalized = scanner.normalized_instructions[logical_index]
    token = tokens[logical_index]

    assert scanner.logical_to_physical[logical_index] == raw.offset
    assert normalized.logical_index == logical_index
    assert normalized.physical_offset == raw.offset
    assert normalized.original_opcode == item["opcode"]
    assert normalized.original_opname == opcode_name
    assert normalized.kind
    assert normalized.kind != "CACHE"
    assert not normalized.kind.startswith("<")
    assert token.kind == normalized.kind
    assert token.offset == normalized.physical_offset
    assert normalized.is_internal == (opcode_name in INTERNAL_OPNAMES)

    assert isinstance(normalized.stack_pop, int)
    assert isinstance(normalized.stack_push, int)
    assert isinstance(normalized.stack_effect, int)
    assert normalized.stack_pop >= 0
    assert normalized.stack_push >= 0
    assert normalized.required_depth >= normalized.stack_pop
    assert (
        normalized.stack_push - normalized.stack_pop
        == normalized.stack_effect
    )

    if normalized.target is None:
        expected_effect = native_stack_effect(
            normalized.original_opcode,
            normalized.arg,
        )
        assert normalized.stack_effect == expected_effect
        assert normalized.jump_stack_effect is None
    else:
        expected_fall = native_stack_effect(
            normalized.original_opcode,
            normalized.arg,
            jump=False,
        )
        expected_jump = native_stack_effect(
            normalized.original_opcode,
            normalized.arg,
            jump=True,
        )
        actual_jump = (
            normalized.jump_stack_effect
            if normalized.jump_stack_effect is not None
            else normalized.stack_effect
        )
        assert normalized.stack_effect == expected_fall
        assert actual_jump == expected_jump
        assert normalized.target in scanner.physical_to_logical
        assert normalized.target not in scanner.cache_owner
        assert (
            scanner.normalized_instructions[
                normalized.target_index
            ].offset
            == normalized.target
        )


def test_internal_protocol_set_and_kinds_are_explicit():
    assert INTERNAL_OPNAMES == {
        "CACHE",
        "RESUME",
        "EXTENDED_ARG",
        "PUSH_NULL",
        "PRECALL",
        "KW_NAMES",
        "MAKE_CELL",
        "COPY_FREE_VARS",
    }

    observed_internal = {"CACHE"}
    original_opnames = set()
    for source in corpus_generator.corpus_sources():
        root_code = corpus_generator.compile_source(source)
        for code in Scanner311.iter_code_objects(root_code):
            scanner = Scanner311()
            scanner.ingest(code)
            for instruction in scanner.normalized_instructions:
                original_opnames.add(instruction.original_opname)
                if instruction.is_internal:
                    observed_internal.add(instruction.original_opname)

    assert observed_internal == INTERNAL_OPNAMES
    assert original_opnames == set(dis.opmap) - {"CACHE"}

    _, _, resume_scanner, _, resume_raw = locate_opcode("RESUME")
    resume = resume_scanner.normalized_instructions[
        resume_scanner.physical_to_logical[resume_raw.offset]
    ]
    assert resume.kind == "INTERNAL_RESUME"
    assert "resume_where" in resume.metadata_dict()

    _, _, extended_scanner, _, extended_raw = locate_opcode("EXTENDED_ARG")
    extended = extended_scanner.normalized_instructions[
        extended_scanner.physical_to_logical[extended_raw.offset]
    ]
    assert extended.kind == "INTERNAL_EXTENDED_ARG"


def test_physical_logical_offsets_and_stack_depths_cover_full_corpus():
    saw_cache = False
    for source in corpus_generator.corpus_sources():
        root_code = corpus_generator.compile_source(source)
        for code in Scanner311.iter_code_objects(root_code):
            scanner = Scanner311()
            scanner.ingest(code)
            normalized = scanner.normalized_instructions
            assert len(normalized) == sum(
                raw.opname != "CACHE" for raw in scanner.insts
            )
            assert [item.logical_index for item in normalized] == list(
                range(len(normalized))
            )
            for raw in scanner.insts:
                logical_index = scanner.physical_to_logical[raw.offset]
                if raw.opname == "CACHE":
                    saw_cache = True
                    assert scanner.cache_owner[raw.offset] == logical_index
                    assert (
                        raw.offset
                        in normalized[logical_index].cache_offsets
                    )
                else:
                    assert (
                        scanner.logical_to_physical[logical_index]
                        == raw.offset
                    )
                    assert normalized[logical_index].offset == raw.offset

            assert scanner.stack_depths
            assert min(scanner.stack_depths.values()) >= 0
            assert scanner.max_stack_depth <= code.co_stacksize

    assert saw_cache


def test_call_function_and_scope_metadata_are_complete():
    direct = scan_named_code(
        "test/simple_source/311/00_expressions.py",
        "call_examples",
    )
    call = next(
        item
        for item in direct.normalized_instructions
        if item.call is not None
    )
    assert call.call.argc == 4
    assert call.call.positional_count == 2
    assert call.call.keyword_names == ("scale", "extra")
    assert call.call.has_null
    assert call.call.precall_offset is not None
    assert call.call.kw_names_offset is not None

    precall = next(
        item
        for item in direct.normalized_instructions
        if item.original_opname == "PRECALL"
    )
    assert precall.is_internal
    assert precall.metadata_dict()["call_offset"] == call.offset
    assert precall.metadata_dict()["argc"] == call.call.argc

    module = scan_named_code(
        "test/simple_source/311/01_functions_classes.py",
        "<module>",
    )
    function = next(
        item.function
        for item in module.normalized_instructions
        if item.function is not None
        and item.function.code_name == "combine"
    )
    assert function.flags == 0x07
    assert function.default_count == 1
    assert function.kwdefault_names == ("scale",)
    assert function.has_annotations
    assert function.annotation_names[-1] == "return"

    scope_cases = (
        (
            "test/bytecode_3.11/opcode_fixtures/scope/"
            "delete_deref.py",
            "make_deleter.<locals>.delete_value",
            "DELETE_DEREF",
            "value",
        ),
        (
            "test/bytecode_3.11/opcode_fixtures/scope/"
            "load_classderef.py",
            "class_from_closure.<locals>.Captures",
            "LOAD_CLASSDEREF",
            "value",
        ),
    )
    for relative_path, qualname, opname, expected_name in scope_cases:
        scanner = scan_named_code(relative_path, qualname)
        instruction = next(
            item
            for item in scanner.normalized_instructions
            if item.original_opname == opname
        )
        metadata = instruction.metadata_dict()
        assert metadata["localsplus_index"] == instruction.arg
        assert metadata["localsplus_name"] == expected_name

    counter = scan_named_code(
        "test/simple_source/311/01_functions_classes.py",
        "make_counter.<locals>.increment",
    )
    copy_free = next(
        item
        for item in counter.normalized_instructions
        if item.original_opname == "COPY_FREE_VARS"
    )
    assert copy_free.is_internal
    assert copy_free.metadata_dict()["freevars"] == ("current",)


@pytest.mark.parametrize(
    "specialized,base",
    sorted(SPECIALIZED_TO_BASE.items()),
)
def test_every_specialized_opcode_maps_to_declared_base(
    specialized,
    base,
):
    normalizer = Normalizer311(Scanner311().opc)
    assert (
        normalizer.despecialize_opname(specialized, "runtime")
        == base
    )
    with pytest.raises(
        UnsupportedSpecializedOpcodeError,
        match="standard .pyc",
    ):
        normalizer.despecialize_opname(specialized, "pyc")


def test_malformed_internal_protocols_fail_closed():
    def called(function, value):
        return function(value)

    scanner = Scanner311()
    scanner.ingest_raw(called.__code__)
    normalizer = Normalizer311(scanner.opc)

    cache = next(
        instruction
        for instruction in scanner.insts
        if instruction.opname == "CACHE"
    )
    with pytest.raises(
        BytecodeNormalizationError,
        match="CACHE.*no owner",
    ):
        normalizer.normalize([cache], called.__code__)

    precall_index = next(
        index
        for index, instruction in enumerate(scanner.insts)
        if instruction.opname == "PRECALL"
    )
    bad_call = list(scanner.insts)
    bad_call[precall_index] = bad_call[precall_index]._replace(
        arg=2,
        argval=2,
    )
    with pytest.raises(
        BytecodeNormalizationError,
        match="argument mismatch",
    ):
        Normalizer311(scanner.opc).normalize(
            bad_call,
            called.__code__,
        )

    raw_scanner = Scanner311()
    code = next(
        nested
        for nested in Scanner311.iter_code_objects(
            compile_fixture(
                "test/simple_source/311/02_control_flow.py"
            )
        )
        if nested.co_qualname == "loops"
    )
    raw_scanner.ingest_raw(code)
    cache_offset = next(
        instruction.offset
        for instruction in raw_scanner.insts
        if instruction.opname == "CACHE"
    )
    jump_index = next(
        index
        for index, instruction in enumerate(raw_scanner.insts)
        if instruction.opcode
        in (raw_scanner.opc.JREL_OPS | raw_scanner.opc.JABS_OPS)
    )
    bad_jump = list(raw_scanner.insts)
    bad_jump[jump_index] = bad_jump[jump_index]._replace(
        argval=cache_offset
    )
    with pytest.raises(InvalidJumpTargetError, match="CACHE slot"):
        Normalizer311(raw_scanner.opc).normalize(bad_jump, code)

    binary_index = next(
        index
        for index, instruction in enumerate(raw_scanner.insts)
        if instruction.opname == "BINARY_OP"
    )
    bad_binary = list(raw_scanner.insts)
    bad_binary[binary_index] = bad_binary[binary_index]._replace(
        arg=255,
        argval=255,
    )
    with pytest.raises(
        BytecodeNormalizationError,
        match="Invalid BINARY_OP",
    ):
        Normalizer311(raw_scanner.opc).normalize(bad_binary, code)

    return_instruction = next(
        instruction
        for instruction in scanner.insts
        if instruction.opname == "RETURN_VALUE"
    )
    with pytest.raises(StackDepthError, match="requires stack depth"):
        Normalizer311(scanner.opc).normalize(
            [return_instruction],
            called.__code__,
        )

    with pytest.raises(
        BytecodeNormalizationError,
        match="source kind",
    ):
        Normalizer311(scanner.opc).normalize(
            scanner.insts,
            called.__code__,
            source_kind="unknown",
        )
