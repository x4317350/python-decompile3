"""Stage 0 checks for the CPython 3.11 source and bytecode corpus."""

import subprocess
import sys
from pathlib import Path

import pytest

from support311 import compile_source, corpus_sources


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "test" / "simple_source" / "311"
GENERATOR = ROOT / "test" / "bytecode_3.11" / "generate.py"
EXPECTED_SOURCES = {
    "00_expressions.py",
    "01_functions_classes.py",
    "02_control_flow.py",
    "03_comprehensions.py",
    "04_generators_async.py",
    "05_exceptions_with.py",
    "06_match.py",
    "07_exception_group.py",
    "08_imports_unpacking.py",
    "09_straight_line.py",
    "10_nested_unpacking.py",
}

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="the CPython 3.11 corpus must be generated with Python 3.11",
)


def test_corpus_has_expected_sources():
    assert {path.name for path in SOURCE_DIR.glob("*.py")} == EXPECTED_SOURCES


def test_xdis_loads_generated_311_bytecode(tmp_path):
    for source in corpus_sources():
        bytecode = tmp_path / f"{source.stem}.pyc"
        version, _, _, code, implementation, *_ = compile_source(source, bytecode)
        assert version == (3, 11)
        assert str(implementation) == "CPython"
        assert code.co_name == "<module>"


def test_disassembly_goldens_are_current():
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
