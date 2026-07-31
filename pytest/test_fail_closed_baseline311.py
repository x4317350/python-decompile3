"""Phase 0 baseline contracts for fail-closed shape remediation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = (
    ROOT / "test" / "bytecode_3.11" / "build_fail_closed_baseline.py"
)
BASELINE_PATH = (
    ROOT / "test" / "bytecode_3.11" / "fail_closed_baseline311.json"
)
SHAPE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "shape_matrix.json"
)
REPORT_PATH = ROOT / "PYTHON_311_FAIL_CLOSED_BASELINE.md"
PLAN_PATH = ROOT / "PYTHON_311_FAIL_CLOSED_REMEDIATION_PLAN.md"

SPEC = importlib.util.spec_from_file_location(
    "build_fail_closed_baseline_test311",
    BUILDER_PATH,
)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)

BASELINE = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
SHAPE_MATRIX = json.loads(SHAPE_MATRIX_PATH.read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 fail-closed baseline requires CPython 3.11",
)


def test_baseline_totals_preserve_the_frozen_phase_zero_snapshot():
    assert BASELINE["schema_version"] == 1
    assert BASELINE["phase"] == 0
    assert BASELINE["source_commit"] == "9f5bb1e4"
    assert BASELINE["input_digest"] == (
        "8b69da10c639757a77c33fe575a95f5c9"
        "cd7d4ebd84e3be835e181a024b9ac62"
    )
    assert BASELINE["input_files"] == 604
    assert BASELINE["decompile_success"] == 203
    assert BASELINE["fail_closed"] == 401
    assert BASELINE["shape_inventory"] == 10
    assert BASELINE["realworld_shape_count"] == 9
    assert BASELINE["safety_boundary_count"] == 1


def test_each_realworld_family_preserves_count_and_signature_algebra():
    baseline_by_name = {
        item["name"]: item for item in BASELINE["shapes"]
    }
    frozen_counts = {
        name: item["archived_fail_closed"]
        for name, item in baseline_by_name.items()
        if name.startswith("realworld_")
    }

    assert set(frozen_counts) == {
        name
        for name in baseline_by_name
        if name.startswith("realworld_")
    }
    assert sum(frozen_counts.values()) == BASELINE["fail_closed"]
    for name, archived_count in frozen_counts.items():
        item = baseline_by_name[name]
        assert item["archived_fail_closed"] == archived_count
        assert sum(item["error_types"].values()) == archived_count
        assert sum(item["opcodes"].values()) == archived_count
        assert sum(
            signature["count"] for signature in item["signatures"]
        ) == archived_count
        assert item["representatives"]


def test_remediation_order_and_safety_boundary_are_explicit():
    assert [item["name"] for item in BASELINE["shapes"]] == list(
        builder.ORDER
    )
    assert [item["stage"] for item in BASELINE["shapes"]] == list(
        range(1, 11)
    )
    matrix_by_name = {
        item["name"]: item for item in SHAPE_MATRIX["shapes"]
    }
    for item in BASELINE["shapes"]:
        assert item["status"] == "unsupported_fail_closed"
        assert item["name"] in matrix_by_name
        if item["name"] == "irreducible_control_flow":
            assert item["archived_fail_closed"] == 0
            assert item["disposition"] == "retain_safety_boundary"
            assert item["risk"] == "security_boundary"
        else:
            assert item["disposition"] == "recover_or_split_until_zero"


def test_generated_baseline_report_is_current():
    assert builder.render_report(BASELINE) == REPORT_PATH.read_text(
        encoding="utf-8"
    )


def test_remediation_plan_mentions_every_shape_and_freezes_phase_zero():
    plan = PLAN_PATH.read_text(encoding="utf-8")
    for item in BASELINE["shapes"]:
        assert f"`{item['name']}`" in plan
    assert "阶段 0：冻结基线" in plan
    assert plan.count("- [x]") >= 10
    assert "604/604" in plan


def test_failure_signature_removes_input_specific_context():
    error = ValueError(
        "Instruction range 12:37 does not contain 1 expression value(s) "
        "('sample', offset 44) "
        "[version=3.11, code='demo', offset=44]"
    )

    assert builder.normalize_failure_message(error) == (
        "Instruction range #:# does not contain # expression value(s) "
        "('<value>', offset #)"
    )
