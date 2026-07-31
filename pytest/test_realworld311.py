"""Phase 8 standard-library and real-world regression contracts."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import sysconfig
from pathlib import Path

import pytest

from support311 import ROOT, compare_behavior311


RUNNER_PATH = (
    ROOT / "test" / "bytecode_3.11" / "run_realworld_regression.py"
)
ARCHIVE_PATH = (
    ROOT / "test" / "bytecode_3.11" / "realworld_regression311.json"
)
REPORT_PATH = ROOT / "PYTHON_311_REALWORLD_REGRESSION.md"
SHAPE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "shape_matrix.json"
)
RELEASE_POLICY_PATH = (
    ROOT / "test" / "bytecode_3.11" / "release_policy311.json"
)

SPEC = importlib.util.spec_from_file_location(
    "run_realworld_regression311",
    RUNNER_PATH,
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

ARCHIVE = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
SHAPE_MATRIX = json.loads(SHAPE_MATRIX_PATH.read_text(encoding="utf-8"))
RELEASE_POLICY = json.loads(
    RELEASE_POLICY_PATH.read_text(encoding="utf-8")
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 real-world regression requires CPython 3.11",
)


def test_archived_realworld_report_has_required_metrics():
    totals = ARCHIVE["totals"]
    behavior = ARCHIVE["behavior"]

    assert ARCHIVE["phase"] == 8
    assert ARCHIVE["target"] == {
        "implementation": "CPython",
        "version": "3.11",
    }
    assert totals["input_files"] == sum(
        group["input_files"] for group in ARCHIVE["groups"].values()
    )
    assert totals["input_files"] == (
        totals["decompile_success"]
        + totals["fail_closed"]
        + totals["malformed_or_unsupported_input"]
        + totals["unexpected_crash"]
    )
    assert totals["syntax_success"] + totals["syntax_failure"] == (
        totals["decompile_success"]
    )
    assert totals["syntax_failure"] == 0
    assert totals["unexpected_crash"] == 0
    assert behavior["input_cases"] == 6
    assert behavior["consistent"] == 6
    assert behavior["mismatch"] == 0
    assert behavior["missing_input"] == 0
    assert all(
        ARCHIVE["groups"][group]["input_files"] > 0
        for group in ("stdlib", "project", "third_party")
    )


def test_archived_failures_are_fully_classified():
    classifications = ARCHIVE["failure_classifications"]
    matrix_items = {
        item["name"]: item for item in SHAPE_MATRIX["shapes"]
    }
    expected = set(
        RELEASE_POLICY["realworld"]["approved_failure_classifications"]
    )

    assert set(classifications) == expected
    assert sum(classifications.values()) == ARCHIVE["totals"]["fail_closed"]
    for shape_name, count in classifications.items():
        assert count > 0
        item = matrix_items[shape_name]
        if item["status"] == "unsupported_fail_closed":
            assert item["expected_error"] is not None
            assert (
                "pytest/test_realworld311.py::"
                "test_archived_failures_are_fully_classified"
                in item["tests"]
            )
        else:
            assert item["status"] == "pass"
            assert (
                "pytest/test_shape_behavior311.py::"
                "test_each_shape_has_differential_behavior_contract"
                in item["tests"]
            )
        samples = ARCHIVE["failure_samples"][shape_name]
        assert samples
        assert all(sample["error_type"] for sample in samples)
    assert ARCHIVE["first_failure"]["shape"] in classifications


def test_realworld_inventory_and_report_match_archive_environment():
    assert runner.render_report(ARCHIVE) == REPORT_PATH.read_text(
        encoding="utf-8"
    )

    inputs = runner.collect_inputs()
    current_environment = {
        "runtime": ".".join(map(str, sys.version_info[:3])),
        "platform": sys.platform,
        "package_versions": runner._package_versions(),
    }
    if current_environment == ARCHIVE["environment"]:
        assert len(inputs) == ARCHIVE["totals"]["input_files"]
        assert runner._input_digest(inputs) == ARCHIVE["input_digest"]
        return

    # The byte-for-byte breadth archive is tied to its recorded OS, patch
    # release, standard library, and dependency versions. Other CI platforms
    # still verify that all three input groups are present; portable smoke and
    # differential behavior tests below exercise the live environment.
    groups = {item.group for item in inputs}
    assert groups == {"stdlib", "project", "third_party"}
    assert all(
        any(item.group == group for item in inputs)
        for group in groups
    )


def smoke_sources():
    stdlib = Path(sysconfig.get_path("stdlib"))
    purelib = Path(sysconfig.get_path("purelib"))
    return (
        stdlib / "abc.py",
        stdlib / "colorsys.py",
        stdlib / "copy.py",
        stdlib / "hmac.py",
        stdlib / "keyword.py",
        ROOT / "decompyle3" / "controlflow" / "basicblock.py",
        ROOT / "decompyle3" / "util.py",
        ROOT / "decompyle3" / "version.py",
        purelib / "attrs" / "exceptions.py",
        purelib / "click" / "_utils.py",
        purelib / "packaging" / "_structures.py",
    )


@pytest.mark.parametrize(
    "source",
    smoke_sources(),
    ids=lambda path: path.name,
)
def test_realworld_smoke_sources_recover_and_recompile(source):
    recovered = runner._recover(source)
    tree = ast.parse(recovered, filename=f"<smoke-{source.name}>")
    compile(tree, f"<smoke-{source.name}>", "exec", dont_inherit=True)


@pytest.mark.parametrize(
    "case",
    runner.behavior_cases(),
    ids=lambda case: case.name,
)
def test_realworld_differential_behavior(case, tmp_path):
    comparison = compare_behavior311(
        case.path,
        case.probe,
        tmp_path / case.name,
        shape_name=case.name,
    )
    assert comparison.original.exitcode == 0
    assert comparison.recovered.exitcode == 0


def test_previous_recursion_sample_recovers_and_recompiles():
    source = Path(sysconfig.get_path("stdlib")) / "fnmatch.py"
    recovered = runner._recover(source)
    tree = ast.parse(recovered, filename="<recovered-fnmatch>")
    compile(tree, "<recovered-fnmatch>", "exec", dont_inherit=True)


def test_previous_with_cleanup_sample_recovers_and_recompiles():
    source = (
        Path(sysconfig.get_path("stdlib"))
        / "multiprocessing"
        / "spawn.py"
    )
    recovered = runner._recover(source)
    tree = ast.parse(recovered, filename="<recovered-spawn>")
    compile(tree, "<recovered-spawn>", "exec", dont_inherit=True)
