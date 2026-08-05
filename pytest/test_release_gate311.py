"""Stage 11 CI and release-gate contracts for CPython 3.11."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "test" / "bytecode_3.11" / "run_release_gate.py"
SPEC = importlib.util.spec_from_file_location(
    "run_release_gate_test311",
    GATE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 release gate requires CPython 3.11",
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def release_inputs():
    return (
        load(gate.POLICY_PATH),
        load(gate.OPCODE_MATRIX_PATH),
        load(gate.SHAPE_MATRIX_PATH),
        load(gate.REALWORLD_ARCHIVE_PATH),
    )


def test_current_release_policy_is_satisfied():
    policy, opcode_matrix, shape_matrix, realworld = release_inputs()
    metrics = gate.validate_release_policy(
        policy,
        opcode_matrix,
        shape_matrix,
        realworld,
    )

    assert metrics["opcode_inventory"] == 110
    assert metrics["layer_counts"]["scanner"]["pass"] == 110
    assert metrics["layer_counts"]["parser"]["missing"] == 0
    assert metrics["shape_counts"]["pass"] == 44
    assert metrics["shape_counts"]["unsupported_fail_closed"] == 1
    assert metrics["realworld"]["decompile_success"] == 604
    assert metrics["realworld"]["fail_closed"] == 0


@pytest.mark.parametrize(
    ("status", "message"),
    (
        ("missing", "parser.missing 未经审批"),
        (
            "unsupported_fail_closed",
            "parser.unsupported_fail_closed 未经审批",
        ),
    ),
)
def test_release_policy_rejects_parser_regression(status, message):
    policy, opcode_matrix, shape_matrix, realworld = release_inputs()
    changed = copy.deepcopy(opcode_matrix)
    passing = next(
        opcode
        for opcode in changed["opcodes"]
        if opcode["layers"]["parser"] == "pass"
    )
    passing["layers"]["parser"] = status

    with pytest.raises(gate.ReleaseGateError, match=message):
        gate.validate_release_policy(
            policy,
            changed,
            shape_matrix,
            realworld,
        )


def test_release_policy_rejects_unapproved_shape_regression():
    policy, opcode_matrix, shape_matrix, realworld = release_inputs()
    changed = copy.deepcopy(shape_matrix)
    passing = next(
        shape for shape in changed["shapes"] if shape["status"] == "pass"
    )
    passing["status"] = "unsupported_fail_closed"

    with pytest.raises(
        gate.ReleaseGateError,
        match="shape.unsupported_fail_closed 未经审批",
    ):
        gate.validate_release_policy(
            policy,
            opcode_matrix,
            changed,
            realworld,
        )


def test_release_policy_rejects_behavior_mismatch():
    policy, opcode_matrix, shape_matrix, realworld = release_inputs()
    changed = copy.deepcopy(realworld)
    changed["behavior"]["consistent"] -= 1
    changed["behavior"]["mismatch"] += 1

    with pytest.raises(
        gate.ReleaseGateError,
        match="行为一致数量不符合策略",
    ):
        gate.validate_release_policy(
            policy,
            opcode_matrix,
            shape_matrix,
            changed,
        )


def test_release_reports_and_support_status_are_current():
    policy, metrics = gate.load_and_validate()

    assert gate.render_release_report(
        policy,
        metrics,
    ) == gate.RELEASE_REPORT_PATH.read_text(encoding="utf-8")
    support = gate.SUPPORT_PATH.read_text(encoding="utf-8")
    assert gate._extract_support_status(
        support
    ) == gate.render_support_status(metrics)


def test_release_policy_rejects_unexpected_skip():
    policy, _, _, _ = release_inputs()
    observed = {
        item["nodeid"]: item["reason"]
        for item in policy["pytest"]["expected_skips"]
    }
    observed["pytest/test_new.py::test_unexpected"] = "new skip"

    with pytest.raises(
        gate.ReleaseGateError,
        match="skip 与白名单不一致",
    ):
        gate.validate_observed_skips(
            policy["pytest"]["expected_skips"],
            observed,
        )
