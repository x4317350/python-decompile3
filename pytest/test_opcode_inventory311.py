"""Phase 1 matrix validation and report freshness tests for CPython 3.11."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = (
    ROOT / "test" / "bytecode_3.11" / "generate_opcode_matrix.py"
)
OPCODE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "opcode_matrix.json"
)
SHAPE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "shape_matrix.json"
)

SPEC = importlib.util.spec_from_file_location(
    "generate_opcode_matrix311",
    GENERATOR_PATH,
)
assert SPEC is not None and SPEC.loader is not None
matrix_generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix_generator)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 opcode matrix tests require CPython 3.11",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def matrix_arguments(opcode_report: Path, shape_report: Path):
    return [
        "--opcode-matrix",
        str(OPCODE_MATRIX_PATH),
        "--shape-matrix",
        str(SHAPE_MATRIX_PATH),
        "--opcode-report",
        str(opcode_report),
        "--shape-report",
        str(shape_report),
    ]


def test_opcode_and_shape_matrices_are_valid():
    opcode_matrix = load_json(OPCODE_MATRIX_PATH)
    shape_matrix = load_json(SHAPE_MATRIX_PATH)

    matrix_generator.validate_opcode_matrix(opcode_matrix)
    matrix_generator.validate_shape_matrix(shape_matrix)

    assert len(opcode_matrix["opcodes"]) == 110
    assert len(shape_matrix["shapes"]) == 45


def test_checked_in_reports_are_current():
    assert matrix_generator.main(["--check"]) == 0


@pytest.mark.parametrize("invalid_case", ("unknown_status", "missing_field"))
def test_invalid_opcode_matrix_returns_nonzero(
    tmp_path,
    invalid_case,
    capsys,
):
    opcode_matrix = copy.deepcopy(load_json(OPCODE_MATRIX_PATH))
    first_opcode = opcode_matrix["opcodes"][0]
    if invalid_case == "unknown_status":
        first_opcode["layers"]["scanner"] = "unknown"
    else:
        first_opcode.pop("layers")

    invalid_matrix = tmp_path / "opcode_matrix.json"
    invalid_matrix.write_text(
        json.dumps(opcode_matrix, ensure_ascii=False),
        encoding="utf-8",
    )
    opcode_report = tmp_path / "opcode_report.md"
    shape_report = tmp_path / "shape_report.md"
    arguments = matrix_arguments(opcode_report, shape_report)
    arguments[1] = str(invalid_matrix)

    assert matrix_generator.main(arguments) == 2
    assert "矩阵校验失败" in capsys.readouterr().err
    assert not opcode_report.exists()
    assert not shape_report.exists()


def test_fail_closed_shape_requires_expected_error():
    shape_matrix = copy.deepcopy(load_json(SHAPE_MATRIX_PATH))
    fail_closed = next(
        shape
        for shape in shape_matrix["shapes"]
        if shape["status"] == "unsupported_fail_closed"
    )
    fail_closed["expected_error"] = None

    with pytest.raises(
        matrix_generator.MatrixValidationError,
        match="expected_error",
    ):
        matrix_generator.validate_shape_matrix(shape_matrix)


def test_check_mode_detects_stale_reports(tmp_path, capsys):
    opcode_report = tmp_path / "opcode_report.md"
    shape_report = tmp_path / "shape_report.md"
    arguments = matrix_arguments(opcode_report, shape_report)

    assert matrix_generator.main(arguments) == 0
    assert matrix_generator.main(["--check", *arguments]) == 0

    shape_report.write_text(
        shape_report.read_text(encoding="utf-8") + "stale\n",
        encoding="utf-8",
    )
    assert matrix_generator.main(["--check", *arguments]) == 1
    assert "报告已过期" in capsys.readouterr().err
