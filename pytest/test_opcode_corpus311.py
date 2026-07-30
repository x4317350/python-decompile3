"""Phase 2 focused corpus tests for the 13 previously unseen opcodes."""

from __future__ import annotations

import dis
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from xdis import load_module

from decompyle3.errors import Decompyle3Error
from decompyle3.scanners.scanner311 import Scanner311


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "test" / "bytecode_3.11" / "generate.py"
OPCODE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "opcode_matrix.json"
)
PHASE2_OPCODES = (
    "DELETE_ATTR",
    "DELETE_DEREF",
    "DELETE_GLOBAL",
    "IMPORT_STAR",
    "LIST_TO_TUPLE",
    "LOAD_ASSERTION_ERROR",
    "LOAD_CLASSDEREF",
    "PRINT_EXPR",
    "SETUP_ANNOTATIONS",
    "SET_UPDATE",
    "STORE_GLOBAL",
    "UNARY_NOT",
    "UNARY_POSITIVE",
)

SPEC = importlib.util.spec_from_file_location(
    "generate_opcode_corpus311",
    GENERATOR_PATH,
)
assert SPEC is not None and SPEC.loader is not None
corpus_generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(corpus_generator)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 opcode fixtures require CPython 3.11",
)


def opcode_items():
    matrix = json.loads(OPCODE_MATRIX_PATH.read_text(encoding="utf-8"))
    return {item["name"]: item for item in matrix["opcodes"]}


def raw_opnames(root_code):
    return {
        instruction.opname
        for code in Scanner311.iter_code_objects(root_code)
        for instruction in dis.get_instructions(code, show_caches=True)
    }


def normalized_opnames(root_code):
    names = set()
    errors = []
    for code in Scanner311.iter_code_objects(root_code):
        scanner = Scanner311()
        try:
            scanner.ingest(code)
        except Decompyle3Error as error:
            errors.append(error)
            continue
        names.update(
            instruction.original_opname
            for instruction in scanner.normalized_instructions
        )
    return names, errors


@pytest.mark.parametrize("opcode_name", PHASE2_OPCODES)
def test_each_gap_fixture_contains_declared_opcode(opcode_name):
    item = opcode_items()[opcode_name]
    source = ROOT / item["source_fixture"]
    root_code = corpus_generator.compile_source(source)

    assert opcode_name in raw_opnames(root_code)
    assert item["source_fixture"] in item["observed_in"]
    assert (
        "pytest/test_opcode_corpus311.py::"
        "test_each_gap_fixture_contains_declared_opcode"
    ) in item["tests"]

    normalized, errors = normalized_opnames(root_code)
    assert opcode_name in normalized
    assert not errors


def test_phase2_corpus_reaches_full_raw_inventory():
    sources = corpus_generator.corpus_sources()
    raw = set()
    normalized = set()
    failures = []
    code_object_count = 0
    for source in sources:
        root_code = corpus_generator.compile_source(source)
        codes = list(Scanner311.iter_code_objects(root_code))
        code_object_count += len(codes)
        raw.update(raw_opnames(root_code))
        names, errors = normalized_opnames(root_code)
        normalized.update(names)
        failures.extend(
            (source.relative_to(ROOT).as_posix(), error)
            for error in errors
        )

    assert len(sources) == 23
    assert code_object_count == 131
    assert raw == set(dis.opmap)
    assert set(dis.opmap) - normalized == {"CACHE"}
    assert not failures


@pytest.mark.parametrize("opcode_name", PHASE2_OPCODES)
def test_each_gap_fixture_generates_loadable_pyc(
    tmp_path,
    opcode_name,
):
    item = opcode_items()[opcode_name]
    source = ROOT / item["source_fixture"]
    bytecode = tmp_path / f"{opcode_name.lower()}.pyc"

    corpus_generator.write_bytecode(source, bytecode)
    version, _, _, code, implementation, *_ = load_module(str(bytecode))

    assert version == (3, 11)
    assert str(implementation) == "CPython"
    assert code.co_name == "<module>"


@pytest.mark.parametrize("opcode_name", PHASE2_OPCODES)
def test_each_gap_fixture_has_dis_token_and_cfg_golden(opcode_name):
    item = opcode_items()[opcode_name]
    source = ROOT / item["source_fixture"]
    _, dis_path, token_path, cfg_path = corpus_generator.artifact_paths(source)

    for path in (dis_path, token_path, cfg_path):
        assert path.is_file(), path.relative_to(ROOT)
        assert (
            f"# Source: {item['source_fixture']}"
            in path.read_text(encoding="utf-8")
        )

    assert opcode_name in dis_path.read_text(encoding="utf-8")
    token_golden = token_path.read_text(encoding="utf-8")
    assert opcode_name in token_golden
