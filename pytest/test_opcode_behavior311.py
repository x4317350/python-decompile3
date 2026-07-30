"""Phase 6 differential behavior coverage for all CPython 3.11 opcodes."""

from __future__ import annotations

import json
import sys
import pytest

from behavior_cases311 import FIXTURE_PROBES
from decompyle3.parsers.p311.base import PARSER_INTERNAL_OPNAMES
from support311 import (
    BEHAVIOR_MARKER,
    BehaviorMismatchError,
    ROOT,
    compare_behavior311,
    normalize_behavior_text,
)


OPCODE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "opcode_matrix.json"
)
BEHAVIOR_TEST_NODE = (
    "pytest/test_opcode_behavior311.py::"
    "test_each_opcode_has_differential_behavior_contract"
)
OPCODE_MATRIX = json.loads(
    OPCODE_MATRIX_PATH.read_text(encoding="utf-8")
)
OPCODE_ITEMS = tuple(
    sorted(OPCODE_MATRIX["opcodes"], key=lambda item: item["opcode"])
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 behavior matrix tests require CPython 3.11",
)


@pytest.mark.parametrize(
    "opcode_name",
    [item["name"] for item in OPCODE_ITEMS],
    ids=[
        f"{item['opcode']:03d}-{item['name']}"
        for item in OPCODE_ITEMS
    ],
)
def test_each_opcode_has_differential_behavior_contract(
    opcode_name,
    tmp_path,
):
    item = next(
        item for item in OPCODE_ITEMS if item["name"] == opcode_name
    )
    expected_status = (
        "internal_consumed"
        if opcode_name in PARSER_INTERNAL_OPNAMES
        else "pass"
    )
    relative_path = item["source_fixture"]

    assert len(OPCODE_ITEMS) == 110
    assert item["layers"]["behavior"] == expected_status
    assert BEHAVIOR_TEST_NODE in item["tests"]
    assert relative_path in FIXTURE_PROBES

    comparison = compare_behavior311(
        ROOT / relative_path,
        FIXTURE_PROBES[relative_path],
        tmp_path / f"{item['opcode']:03d}-{opcode_name}",
        opcode_name=opcode_name,
    )
    assert comparison.original.exitcode == 0
    assert comparison.recovered.exitcode == 0
    assert not comparison.original.timed_out
    assert not comparison.recovered.timed_out


def test_behavior_framework_records_rich_observables(tmp_path):
    source = tmp_path / "rich_behavior.py"
    source.write_text(
        """
state = []

class Resource:
    def __init__(self):
        self.value = 7

    def __enter__(self):
        state.append("enter")
        return self

    def __exit__(self, kind, value, traceback):
        state.append("exit")

def returning():
    return {"value": 42}

def failing():
    raise ValueError("broken", 3)

def values():
    yield 1
    yield 2

async def ready():
    return "async"

def context_value():
    with Resource() as resource:
        return resource.value
""".lstrip(),
        encoding="utf-8",
    )
    probe = """
_record("return", returning)
_record("exception", failing)
_record("generator", lambda: list(values()))
_record_async("coroutine", ready)
_record("context", context_value)
_record("global_state", lambda: state)
"""
    comparison = compare_behavior311(
        source,
        probe,
        tmp_path / "rich-artifacts",
    )

    records = [
        json.loads(line.removeprefix(BEHAVIOR_MARKER))
        for line in comparison.original.stdout.splitlines()
        if line.startswith(BEHAVIOR_MARKER)
    ]
    assert {record["label"] for record in records} == {
        "return",
        "exception",
        "generator",
        "coroutine",
        "context",
        "global_state",
    }
    exception = next(
        record for record in records if record["label"] == "exception"
    )
    assert exception["outcome"] == "exception"
    assert exception["type"] == "ValueError"
    assert exception["args"] == {"$tuple": ["broken", 3]}


def test_behavior_normalization_scrubs_nondeterministic_values(tmp_path):
    text = (
        f"path={tmp_path}/fixture.py "
        "address=0x7ffeeabc "
        "at=2026-07-30T18:00:01.123+08:00 "
        "epoch=1785405601.125 duration=0.123456s"
    )
    normalized = normalize_behavior_text(text, tmp_path)

    assert str(tmp_path) not in normalized
    assert "0x7ffeeabc" not in normalized
    assert "2026-07-30" not in normalized
    assert "1785405601.125" not in normalized
    assert "0.123456s" not in normalized
    assert "<PATH>" in normalized
    assert "0x<ADDR>" in normalized
    assert normalized.count("<TIME>") == 3


def assert_failure_artifacts(directory):
    required = {
        "fixture.py",
        "fixture.pyc",
        "fixture.dis",
        "fixture.tokens",
        "fixture.cfg",
        "recovered.py",
        "original.stdout",
        "original.stderr",
        "original.exitcode",
        "recovered.stdout",
        "recovered.stderr",
        "recovered.exitcode",
        "failure.json",
    }
    assert required <= {path.name for path in directory.iterdir()}


def test_behavior_mismatch_retains_complete_failure(tmp_path):
    source = tmp_path / "mismatch.py"
    source.write_text('print("original")\n', encoding="utf-8")
    artifacts = tmp_path / "mismatch-artifacts"

    with pytest.raises(BehaviorMismatchError, match="BehaviorMismatch"):
        compare_behavior311(
            source,
            "",
            artifacts,
            opcode_name="CALL",
            shape_name="behavior_artifact_contract",
            recovered_override='print("recovered")\n',
        )

    assert_failure_artifacts(artifacts)
    failure = json.loads(
        (artifacts / "failure.json").read_text(encoding="utf-8")
    )
    assert failure["opcode"] == "CALL"
    assert failure["shape"] == "behavior_artifact_contract"
    assert failure["exception"] == "BehaviorMismatch"
    assert failure["runtime"].startswith("3.11.")
    assert failure["target"] == "3.11"


def test_behavior_timeout_is_failure_and_retains_artifacts(tmp_path):
    source = tmp_path / "timeout.py"
    source.write_text(
        "import time\ntime.sleep(1)\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "timeout-artifacts"

    with pytest.raises(BehaviorMismatchError, match="BehaviorTimeout"):
        compare_behavior311(
            source,
            "",
            artifacts,
            timeout=0.01,
        )

    assert_failure_artifacts(artifacts)
    failure = json.loads(
        (artifacts / "failure.json").read_text(encoding="utf-8")
    )
    assert failure["exception"] == "BehaviorTimeout"
