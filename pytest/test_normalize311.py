"""Phase 2 acceptance tests for CPython 3.11 instruction normalization."""

import dis
import sys
from pathlib import Path

import pytest

from decompyle3.scanner import (
    BytecodeNormalizationError,
    InvalidJumpTargetError,
    UnsupportedSpecializedOpcodeError,
)
from decompyle3.scanners.normalize311 import (
    BINARY_OPERATIONS,
    SPECIALIZED_TO_BASE,
    Normalizer311,
)
from decompyle3.scanners.scanner311 import Scanner311
from support311 import SOURCE_DIR, compile_source, corpus_sources


TOKEN_GOLDEN_DIR = (
    Path(__file__).resolve().parents[1]
    / "test"
    / "bytecode_3.11"
    / "golden_tokens"
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Scanner311 normalization tests require CPython 3.11",
)


def load_corpus_code(source, tmp_path):
    bytecode = tmp_path / f"{source.stem}.pyc"
    version, _, _, code, implementation, *_ = compile_source(source, bytecode)
    assert version == (3, 11)
    assert str(implementation) == "CPython"
    return code


def scan_named_code(source_name, qualname, tmp_path):
    root = load_corpus_code(SOURCE_DIR / source_name, tmp_path)
    code = next(
        nested
        for nested in Scanner311.iter_code_objects(root)
        if nested.co_qualname == qualname
    )
    scanner = Scanner311()
    scanner.ingest(code)
    return scanner


def test_normalized_streams_preserve_offsets_without_cache(tmp_path):
    saw_cache = False
    for source in corpus_sources():
        root = load_corpus_code(source, tmp_path)
        for code in Scanner311.iter_code_objects(root):
            scanner = Scanner311()
            tokens, customize = scanner.ingest(code)
            normalized = scanner.normalized_instructions

            assert customize == {}
            assert tokens
            assert len(tokens) == len(normalized)
            assert all(token.kind != "CACHE" for token in tokens)
            assert all(item.original_opname != "CACHE" for item in normalized)
            assert [item.logical_index for item in normalized] == list(
                range(len(normalized))
            )
            assert [token.offset for token in tokens] == [
                item.physical_offset for item in normalized
            ]

            offsets = {item.physical_offset for item in normalized}
            for item in normalized:
                assert scanner.logical_to_physical[item.logical_index] == item.offset
                assert scanner.physical_to_logical[item.offset] == item.logical_index
                if item.target is not None:
                    assert item.target in offsets
                    assert item.target not in scanner.cache_owner
                    assert normalized[item.target_index].offset == item.target

            for raw in scanner.insts:
                if raw.opname == "CACHE":
                    saw_cache = True
                    owner = scanner.cache_owner[raw.offset]
                    assert scanner.physical_to_logical[raw.offset] == owner
                    assert raw.offset in normalized[owner].cache_offsets

            assert scanner.stack_depths
            assert min(scanner.stack_depths.values()) >= 0
            assert scanner.max_stack_depth <= code.co_stacksize

    assert saw_cache


def test_all_operations_have_stable_normalized_kinds(tmp_path):
    binary = scan_named_code(
        "00_expressions.py", "all_binary_operations", tmp_path
    )
    inplace = scan_named_code(
        "00_expressions.py", "all_inplace_operations", tmp_path
    )
    comparisons = scan_named_code(
        "00_expressions.py", "all_comparisons", tmp_path
    )

    expected_binary = {
        kind for kind, _, is_inplace in BINARY_OPERATIONS if not is_inplace
    }
    expected_inplace = {
        kind for kind, _, is_inplace in BINARY_OPERATIONS if is_inplace
    }
    assert expected_binary <= {
        item.kind for item in binary.normalized_instructions
    }
    assert expected_inplace <= {
        item.kind for item in inplace.normalized_instructions
    }
    assert {
        "COMPARE_LT",
        "COMPARE_LE",
        "COMPARE_EQ",
        "COMPARE_NE",
        "COMPARE_GT",
        "COMPARE_GE",
        "CONTAINS",
        "NOT_CONTAINS",
        "IS",
        "IS_NOT",
        "COPY_STACK",
        "SWAP_STACK",
    } <= {item.kind for item in comparisons.normalized_instructions}

    for scanner in (binary, inplace):
        for item in scanner.normalized_instructions:
            if item.kind in expected_binary | expected_inplace:
                metadata = item.metadata_dict()
                assert metadata["operator"]
                assert metadata["inplace"] == item.kind.startswith("INPLACE_")
                assert item.stack_effect == -1


def test_call_protocol_tracks_keywords_null_self_and_unpacking(tmp_path):
    direct = scan_named_code(
        "00_expressions.py", "call_examples", tmp_path
    )
    direct_call = next(
        item for item in direct.normalized_instructions if item.call is not None
    )
    assert direct_call.call.argc == 4
    assert direct_call.call.positional_count == 2
    assert direct_call.call.keyword_names == ("scale", "extra")
    assert direct_call.call.has_null
    assert not direct_call.call.is_method
    assert direct_call.call.precall_offset is not None
    assert direct_call.call.kw_names_offset is not None

    method = scan_named_code(
        "01_functions_classes.py", "Accumulator.from_values", tmp_path
    )
    method_call = next(
        item
        for item in method.normalized_instructions
        if item.call is not None and item.call.is_method
    )
    assert method_call.call.has_self
    assert not method_call.call.has_null
    assert method_call.call.receiver_mode == "self_or_null"

    unpacked = scan_named_code(
        "01_functions_classes.py", "marker.<locals>.wrapper", tmp_path
    )
    unpacked_call = next(
        item for item in unpacked.normalized_instructions if item.call is not None
    )
    assert unpacked_call.original_opname == "CALL_FUNCTION_EX"
    assert unpacked_call.call.uses_ex
    assert unpacked_call.call.has_starargs
    assert unpacked_call.call.has_kwargs

    assert any(
        item.original_opname == "PUSH_NULL" and item.is_internal
        for item in direct.normalized_instructions
    )
    assert any(
        item.original_opname == "KW_NAMES" and item.is_internal
        for item in direct.normalized_instructions
    )
    precall = next(
        item
        for item in direct.normalized_instructions
        if item.original_opname == "PRECALL"
    )
    assert precall.metadata_dict()["keyword_names"] == ("scale", "extra")
    assert precall.metadata_dict()["call_offset"] == direct_call.offset


def test_all_jump_families_have_absolute_valid_targets(tmp_path):
    found = {}
    for source in corpus_sources():
        root = load_corpus_code(source, tmp_path)
        for code in Scanner311.iter_code_objects(root):
            scanner = Scanner311()
            scanner.ingest(code)
            for item in scanner.normalized_instructions:
                if item.target is not None:
                    found.setdefault(item.original_opname, item)
                    assert item.jump_direction in ("forward", "backward")
                    assert item.target in scanner.physical_to_logical
                    assert item.target not in scanner.cache_owner

    expected = {
        "JUMP_FORWARD",
        "JUMP_BACKWARD",
        "JUMP_BACKWARD_NO_INTERRUPT",
        "JUMP_IF_FALSE_OR_POP",
        "JUMP_IF_TRUE_OR_POP",
        "POP_JUMP_FORWARD_IF_FALSE",
        "POP_JUMP_FORWARD_IF_TRUE",
        "POP_JUMP_FORWARD_IF_NONE",
        "POP_JUMP_FORWARD_IF_NOT_NONE",
        "POP_JUMP_BACKWARD_IF_FALSE",
        "POP_JUMP_BACKWARD_IF_TRUE",
        "POP_JUMP_BACKWARD_IF_NONE",
        "POP_JUMP_BACKWARD_IF_NOT_NONE",
    }
    assert expected <= set(found)

    for name, item in found.items():
        if name.startswith("POP_JUMP_"):
            assert item.jump_pops is True
        elif name in ("JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP"):
            assert item.jump_pops is False
            assert item.jump_stack_effect == 0


def test_make_function_and_localsplus_metadata(tmp_path):
    module = scan_named_code(
        "01_functions_classes.py", "<module>", tmp_path
    )
    functions = {
        item.function.code_name: item.function
        for item in module.normalized_instructions
        if item.function is not None
    }
    combine = functions["combine"]
    assert combine.flags == 0x07
    assert combine.default_count == 1
    assert combine.kwdefault_names == ("scale",)
    assert combine.annotation_names == (
        "left",
        "right",
        "values",
        "scale",
        "options",
        "return",
    )

    counter = scan_named_code(
        "01_functions_classes.py", "make_counter", tmp_path
    )
    increment = next(
        item.function
        for item in counter.normalized_instructions
        if item.function is not None
    )
    assert increment.flags == 0x09
    assert increment.default_count == 1
    assert increment.closure_names == ("current",)

    increment_code = scan_named_code(
        "01_functions_classes.py",
        "make_counter.<locals>.increment",
        tmp_path,
    )
    copy_free = next(
        item
        for item in increment_code.normalized_instructions
        if item.original_opname == "COPY_FREE_VARS"
    )
    assert copy_free.metadata_dict()["freevars"] == ("current",)
    for item in increment_code.normalized_instructions:
        if item.original_opname in {
            "MAKE_CELL",
            "LOAD_CLOSURE",
            "LOAD_DEREF",
            "STORE_DEREF",
            "DELETE_DEREF",
            "LOAD_CLASSDEREF",
        }:
            metadata = item.metadata_dict()
            assert metadata["localsplus_name"] == item.argval

    root = load_corpus_code(
        SOURCE_DIR / "01_functions_classes.py", tmp_path
    )
    closure_opnames = set()
    for code in Scanner311.iter_code_objects(root):
        scanner = Scanner311()
        scanner.ingest(code)
        closure_opnames.update(
            item.original_opname
            for item in scanner.normalized_instructions
            if item.original_opname
            in {
                "MAKE_CELL",
                "COPY_FREE_VARS",
                "LOAD_CLOSURE",
                "LOAD_DEREF",
                "STORE_DEREF",
            }
        )
    assert closure_opnames == {
        "MAKE_CELL",
        "COPY_FREE_VARS",
        "LOAD_CLOSURE",
        "LOAD_DEREF",
        "STORE_DEREF",
    }


def test_specialized_runtime_opcodes_are_deoptimized_or_rejected():
    scanner = Scanner311()
    normalizer = Normalizer311(scanner.opc)
    assert (
        normalizer.despecialize_opname(
            "BINARY_OP_ADD_INT", source_kind="runtime"
        )
        == "BINARY_OP"
    )
    with pytest.raises(UnsupportedSpecializedOpcodeError, match="standard .pyc"):
        normalizer.despecialize_opname(
            "BINARY_OP_ADD_INT", source_kind="pyc"
        )
    with pytest.raises(
        UnsupportedSpecializedOpcodeError, match="Cannot de-specialize"
    ):
        normalizer.despecialize_opname(
            "UNKNOWN_ADAPTIVE_OPCODE", source_kind="runtime"
        )

    def hot(values):
        total = 0
        for value in values:
            total += value
        return len(values) + total

    for _ in range(20_000):
        hot([1, 2, 3])
    adaptive_names = {
        instruction.opname
        for instruction in dis.get_instructions(hot, adaptive=True)
    }
    if not adaptive_names & set(SPECIALIZED_TO_BASE):
        pytest.skip("this CPython build did not specialize the test function")

    scanner.ingest_runtime(hot.__code__)
    assert any(
        item.original_opname in SPECIALIZED_TO_BASE
        for item in scanner.normalized_instructions
    )
    assert all(
        item.kind != "CACHE" for item in scanner.normalized_instructions
    )


def test_normalizer_rejects_cache_jump_and_malformed_call_protocol():
    def loop(values):
        for value in values:
            if value + 1:
                break

    scanner = Scanner311()
    scanner.ingest_raw(loop.__code__)
    cache_offset = next(
        instruction.offset
        for instruction in scanner.insts
        if instruction.opname == "CACHE"
    )
    jump_index = next(
        index
        for index, instruction in enumerate(scanner.insts)
        if instruction.opcode
        in (scanner.opc.JREL_OPS | scanner.opc.JABS_OPS)
    )
    bad_jump = list(scanner.insts)
    bad_jump[jump_index] = bad_jump[jump_index]._replace(argval=cache_offset)
    with pytest.raises(InvalidJumpTargetError, match="CACHE slot"):
        Normalizer311(scanner.opc).normalize(bad_jump, loop.__code__)

    def called(function, value):
        return function(value)

    scanner.ingest_raw(called.__code__)
    precall_index = next(
        index
        for index, instruction in enumerate(scanner.insts)
        if instruction.opname == "PRECALL"
    )
    bad_call = list(scanner.insts)
    bad_call[precall_index] = bad_call[precall_index]._replace(arg=2, argval=2)
    with pytest.raises(BytecodeNormalizationError, match="argument mismatch"):
        Normalizer311(scanner.opc).normalize(bad_call, called.__code__)

    unknown = list(scanner.insts)
    unknown[0] = unknown[0]._replace(opcode=3, opname="<3>")
    with pytest.raises(BytecodeNormalizationError, match="Unknown CPython 3.11"):
        Normalizer311(scanner.opc).normalize(unknown, called.__code__)


def test_normalized_token_goldens_cover_every_corpus_source():
    assert {
        path.name for path in TOKEN_GOLDEN_DIR.glob("*.tokens")
    } == {f"{source.stem}.tokens" for source in corpus_sources()}
