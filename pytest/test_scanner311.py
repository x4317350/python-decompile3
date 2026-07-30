"""Phase 1 acceptance tests for raw CPython 3.11 bytecode scanning."""

import dis
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.parsers.main import get_python_parser
from decompyle3.scanner import (
    MalformedBytecodeError,
    UnknownOpcodeError,
    get_scanner,
)
from decompyle3.scanners.scanner311 import Scanner311
from support311 import SOURCE_DIR, compile_source, corpus_sources


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Scanner311 acceptance tests require CPython 3.11",
)


def load_corpus_code(source, tmp_path):
    bytecode = tmp_path / f"{source.stem}.pyc"
    version, _, _, code, implementation, *_ = compile_source(source, bytecode)
    assert version == (3, 11)
    assert str(implementation) == "CPython"
    return code


def test_scanner311_and_parser311_are_registered():
    scanner = get_scanner((3, 11, 9), PythonImplementation.CPython)
    assert isinstance(scanner, Scanner311)

    parser = get_python_parser((3, 11))
    assert parser.__class__.__name__ == "Python311ParserExec"

    with pytest.raises(RuntimeError, match="supports CPython bytecode only"):
        get_scanner((3, 11), PythonImplementation.PyPy)


def test_raw_instructions_match_dis_for_the_entire_corpus(tmp_path):
    saw_cache = False
    saw_jump = False
    saw_const = False
    saw_name = False

    for source in corpus_sources():
        root = load_corpus_code(source, tmp_path)
        for code in Scanner311.iter_code_objects(root):
            scanner = Scanner311()
            tokens, customize = scanner.ingest_raw(code)
            expected = list(
                dis.get_instructions(code, show_caches=True, adaptive=False)
            )

            assert customize == {}
            assert len(scanner.insts) == len(expected)
            assert len(tokens) == len(expected)
            assert [instruction.offset for instruction in scanner.insts] == list(
                range(0, len(code.co_code), 2)
            )
            assert [token.offset for token in tokens] == [
                instruction.offset for instruction in expected
            ]
            assert [token.kind for token in tokens] == [
                instruction.opname for instruction in expected
            ]

            for index, (actual, standard) in enumerate(
                zip(scanner.insts, expected)
            ):
                assert scanner.offset2inst_index[actual.offset] == index
                assert actual.offset == standard.offset
                assert actual.opcode == standard.opcode
                assert actual.opname == standard.opname
                assert actual.inst_size == 2
                assert scanner.raw_opargs[actual.offset] == code.co_code[
                    actual.offset + 1
                ]
                assert tuple(actual.positions) == tuple(standard.positions)

                if actual.opname == "CACHE":
                    saw_cache = True
                    assert scanner.raw_opargs[actual.offset] == standard.arg
                    continue

                assert actual.arg == standard.arg
                if actual.opcode in (
                    scanner.opc.JREL_OPS | scanner.opc.JABS_OPS
                ):
                    saw_jump = True
                    assert actual.argval == standard.argval
                elif (
                    actual.opcode in scanner.opc.CONST_OPS
                    and actual.opname != "KW_NAMES"
                ):
                    saw_const = True
                    assert actual.argval == standard.argval
                elif actual.opcode in (
                    scanner.opc.NAME_OPS
                    | scanner.opc.LOCAL_OPS
                    | scanner.opc.FREE_OPS
                ):
                    saw_name = True
                    assert actual.argval == standard.argval

    assert saw_cache
    assert saw_jump
    assert saw_const
    assert saw_name


def test_nested_code_objects_include_functions_lambda_comprehensions_and_classes(
    tmp_path,
):
    function_source = SOURCE_DIR / "01_functions_classes.py"
    comprehension_source = SOURCE_DIR / "03_comprehensions.py"

    names = set()
    for source in (function_source, comprehension_source):
        root = load_corpus_code(source, tmp_path)
        scanner = Scanner311()
        scanner.ingest_raw(root)
        names.update(code.co_name for code in scanner.code_objects)

    assert {
        "<module>",
        "wrapper",
        "increment",
        "<lambda>",
        "Accumulator",
        "NamedAccumulator",
        "<listcomp>",
        "<setcomp>",
        "<dictcomp>",
        "<genexpr>",
    } <= names


def test_scanner311_preserves_metadata_positions_and_exception_table(tmp_path):
    source = SOURCE_DIR / "05_exceptions_with.py"
    root = load_corpus_code(source, tmp_path)
    exception_code = next(
        code
        for code in Scanner311.iter_code_objects(root)
        if code.co_name == "guarded_division"
    )

    scanner = Scanner311()
    scanner.ingest_raw(exception_code)

    assert scanner.code_metadata["co_name"] == "guarded_division"
    assert scanner.code_metadata["code_length"] == len(exception_code.co_code)
    assert scanner.linestarts
    assert scanner.line_ranges
    assert len(scanner.positions) == len(exception_code.co_code) // 2
    assert scanner.exception_table == exception_code.co_exceptiontable
    assert scanner.exception_table
    assert scanner.exception_entries


def test_scanner311_reports_unknown_and_malformed_bytecode():
    scanner = Scanner311()

    with pytest.raises(MalformedBytecodeError, match="odd co_code length"):
        scanner._validate_bytecode(b"\x97", "odd")

    with pytest.raises(UnknownOpcodeError, match="opcode 255 at offset 0"):
        scanner._validate_bytecode(b"\xff\x00", "unknown")

    with pytest.raises(MalformedBytecodeError, match="expected a code object"):
        scanner.ingest(object())
