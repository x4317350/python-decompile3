#!/usr/bin/env python3
"""Validate and run the CPython 3.11 release gate."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
BYTECODE_DIR = Path(__file__).resolve().parent
POLICY_PATH = BYTECODE_DIR / "release_policy311.json"
OPCODE_MATRIX_PATH = BYTECODE_DIR / "opcode_matrix.json"
SHAPE_MATRIX_PATH = BYTECODE_DIR / "shape_matrix.json"
REALWORLD_ARCHIVE_PATH = BYTECODE_DIR / "realworld_regression311.json"
OPCODE_REPORT_PATH = ROOT / "PYTHON_311_OPCODE_COVERAGE.md"
SHAPE_REPORT_PATH = ROOT / "PYTHON_311_SHAPE_COVERAGE.md"
REALWORLD_REPORT_PATH = ROOT / "PYTHON_311_REALWORLD_REGRESSION.md"
RELEASE_REPORT_PATH = ROOT / "PYTHON_311_RELEASE_GATE.md"
SUPPORT_PATH = ROOT / "PYTHON_311_SUPPORT.md"
SUPPORT_START = "<!-- BEGIN PYTHON311 RELEASE STATUS -->"
SUPPORT_END = "<!-- END PYTHON311 RELEASE STATUS -->"
LAYERS = ("scanner", "normalizer", "parser", "behavior")
STATUSES = (
    "pass",
    "internal_consumed",
    "unsupported_fail_closed",
    "not_applicable",
    "missing",
)


class ReleaseGateError(ValueError):
    """The checked-in Python 3.11 release contract was violated."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ReleaseGateError(f"文件不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise ReleaseGateError(
            f"JSON 无效：{path}:{error.lineno}:{error.colno}"
        ) from error
    if not isinstance(value, dict):
        raise ReleaseGateError(f"JSON 根节点必须是 object：{path}")
    return value


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ReleaseGateError(f"无法加载检查脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require_fields(
    value: Mapping[str, Any],
    required: Sequence[str],
    context: str,
) -> None:
    missing = sorted(set(required) - set(value))
    if missing:
        raise ReleaseGateError(
            f"{context} 缺少字段：{', '.join(missing)}"
        )


def _names_with_status(
    opcodes: Sequence[Mapping[str, Any]],
    layer: str,
    status: str,
) -> list[str]:
    return sorted(
        item["name"]
        for item in opcodes
        if item["layers"][layer] == status
    )


def _shape_names_with_status(
    shapes: Sequence[Mapping[str, Any]],
    status: str,
) -> list[str]:
    return sorted(
        item["name"] for item in shapes if item["status"] == status
    )


def _validate_skip_nodes(expected_skips: Any) -> None:
    if not isinstance(expected_skips, list):
        raise ReleaseGateError("pytest.expected_skips 必须是数组")
    seen = set()
    for item in expected_skips:
        if not isinstance(item, dict):
            raise ReleaseGateError("skip 白名单项必须是 object")
        _require_fields(item, ("nodeid", "reason"), "skip 白名单项")
        nodeid = item["nodeid"]
        reason = item["reason"]
        if (
            not isinstance(nodeid, str)
            or "::" not in nodeid
            or not isinstance(reason, str)
            or not reason
        ):
            raise ReleaseGateError("skip 白名单 nodeid/reason 无效")
        if nodeid in seen:
            raise ReleaseGateError(f"skip 白名单重复：{nodeid}")
        seen.add(nodeid)
        filename, test_name = nodeid.split("::", 1)
        path = ROOT / filename
        if not path.is_file():
            raise ReleaseGateError(f"skip 测试文件不存在：{filename}")
        function_name = test_name.split("[", 1)[0]
        source = path.read_text(encoding="utf-8")
        if f"def {function_name}(" not in source:
            raise ReleaseGateError(f"skip 测试节点不存在：{nodeid}")


def validate_release_policy(
    policy: Mapping[str, Any],
    opcode_matrix: Mapping[str, Any],
    shape_matrix: Mapping[str, Any],
    realworld: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate exact approved states and return report metrics."""
    _require_fields(
        policy,
        (
            "schema_version",
            "phase",
            "target",
            "dependencies",
            "opcode",
            "shape",
            "realworld",
            "pytest",
        ),
        "release policy",
    )
    if policy["schema_version"] != 1 or policy["phase"] != 9:
        raise ReleaseGateError("release policy 必须使用 schema 1、阶段 9")
    if sys.version_info[:2] != (3, 11):
        raise ReleaseGateError("发布门禁必须使用 CPython 3.11")
    if opcode_matrix.get("phase") != 9 or shape_matrix.get("phase") != 9:
        raise ReleaseGateError("opcode/shape 矩阵必须标记为阶段 9")

    target = policy["target"]
    if target != {"implementation": "CPython", "version": "3.11.9"}:
        raise ReleaseGateError("发布策略目标必须精确为 CPython 3.11.9")
    if sys.version_info[:3] != (3, 11, 9):
        raise ReleaseGateError(
            "发布门禁运行时必须精确为 CPython 3.11.9"
        )
    dependencies = policy["dependencies"]
    if not isinstance(dependencies, dict) or not dependencies:
        raise ReleaseGateError("发布策略 dependencies 必须是非空 object")
    installed_dependencies = {}
    for name, expected_version in dependencies.items():
        try:
            actual_version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise ReleaseGateError(f"发布门禁依赖未安装：{name}") from error
        installed_dependencies[name] = actual_version
        if actual_version != expected_version:
            raise ReleaseGateError(
                f"发布门禁依赖版本不一致：{name} "
                f"{actual_version} != {expected_version}"
            )

    opcode_policy = policy["opcode"]
    _require_fields(opcode_policy, ("inventory", "layers"), "opcode policy")
    opcodes = opcode_matrix["opcodes"]
    if len(opcodes) != opcode_policy["inventory"]:
        raise ReleaseGateError(
            "opcode inventory 与发布策略不一致："
            f"{len(opcodes)} != {opcode_policy['inventory']}"
        )
    if set(opcode_policy["layers"]) != set(LAYERS):
        raise ReleaseGateError("opcode policy.layers 不完整")

    layer_counts = {}
    for layer in LAYERS:
        approved = opcode_policy["layers"][layer]
        _require_fields(
            approved,
            (
                "internal_consumed",
                "unsupported_fail_closed",
                "missing",
            ),
            f"opcode policy.{layer}",
        )
        for status in (
            "internal_consumed",
            "unsupported_fail_closed",
            "missing",
        ):
            actual = _names_with_status(opcodes, layer, status)
            expected = sorted(approved[status])
            if actual != expected:
                raise ReleaseGateError(
                    f"{layer}.{status} 未经审批："
                    f"实际 {actual}，策略 {expected}"
                )
        not_applicable = _names_with_status(
            opcodes,
            layer,
            "not_applicable",
        )
        if not_applicable:
            raise ReleaseGateError(
                f"{layer}.not_applicable 未经审批：{not_applicable}"
            )
        counts = Counter(item["layers"][layer] for item in opcodes)
        if sum(counts.values()) != len(opcodes):
            raise ReleaseGateError(f"{layer} 状态计数不完整")
        layer_counts[layer] = {
            status: counts[status] for status in STATUSES
        }

    shape_policy = policy["shape"]
    _require_fields(
        shape_policy,
        ("inventory", "unsupported_fail_closed", "missing"),
        "shape policy",
    )
    shapes = shape_matrix["shapes"]
    if len(shapes) != shape_policy["inventory"]:
        raise ReleaseGateError("shape inventory 与发布策略不一致")
    for status in ("unsupported_fail_closed", "missing"):
        actual = _shape_names_with_status(shapes, status)
        expected = sorted(shape_policy[status])
        if actual != expected:
            raise ReleaseGateError(
                f"shape.{status} 未经审批："
                f"实际 {actual}，策略 {expected}"
            )
    for status in ("internal_consumed", "not_applicable"):
        unexpected = _shape_names_with_status(shapes, status)
        if unexpected:
            raise ReleaseGateError(
                f"shape.{status} 未经审批：{unexpected}"
            )
    shape_counts = Counter(item["status"] for item in shapes)

    realworld_policy = policy["realworld"]
    _require_fields(
        realworld_policy,
        (
            "archived_input_files",
            "maximum_syntax_failures",
            "maximum_unexpected_crashes",
            "required_behavior_consistent",
            "maximum_behavior_mismatch",
            "approved_failure_classifications",
        ),
        "realworld policy",
    )
    totals = realworld["totals"]
    behavior = realworld["behavior"]
    if totals["input_files"] != realworld_policy["archived_input_files"]:
        raise ReleaseGateError("真实语料输入数与发布策略不一致")
    if totals["syntax_failure"] > realworld_policy["maximum_syntax_failures"]:
        raise ReleaseGateError("真实语料出现语法失败")
    if (
        totals["unexpected_crash"]
        > realworld_policy["maximum_unexpected_crashes"]
    ):
        raise ReleaseGateError("真实语料出现未包装崩溃")
    if (
        behavior["consistent"]
        != realworld_policy["required_behavior_consistent"]
    ):
        raise ReleaseGateError("真实语料行为一致数量不符合策略")
    if behavior["mismatch"] > realworld_policy["maximum_behavior_mismatch"]:
        raise ReleaseGateError("真实语料出现行为不一致")
    classifications = sorted(realworld["failure_classifications"])
    approved_classifications = sorted(
        realworld_policy["approved_failure_classifications"]
    )
    if classifications != approved_classifications:
        raise ReleaseGateError("真实语料失败分类未经审批")

    pytest_policy = policy["pytest"]
    _require_fields(pytest_policy, ("expected_skips",), "pytest policy")
    _validate_skip_nodes(pytest_policy["expected_skips"])

    return {
        "opcode_inventory": len(opcodes),
        "layer_counts": layer_counts,
        "shape_inventory": len(shapes),
        "shape_counts": {
            status: shape_counts[status] for status in STATUSES
        },
        "realworld": {
            "input_files": totals["input_files"],
            "decompile_success": totals["decompile_success"],
            "fail_closed": totals["fail_closed"],
            "syntax_failure": totals["syntax_failure"],
            "unexpected_crash": totals["unexpected_crash"],
            "behavior_consistent": behavior["consistent"],
            "behavior_mismatch": behavior["mismatch"],
        },
        "expected_skips": len(pytest_policy["expected_skips"]),
        "dependencies": installed_dependencies,
    }


def summary_lines(metrics: Mapping[str, Any]) -> list[str]:
    layers = metrics["layer_counts"]
    normalizer = layers["normalizer"]
    parser = layers["parser"]
    behavior = layers["behavior"]
    shapes = metrics["shape_counts"]
    normalizer_verified = (
        metrics["opcode_inventory"]
        - normalizer["missing"]
        - normalizer["not_applicable"]
    )
    behavior_verified = (
        metrics["opcode_inventory"]
        - behavior["missing"]
        - behavior["not_applicable"]
    )
    return [
        f"Opcode inventory: {metrics['opcode_inventory']}/110",
        f"Scanner: {layers['scanner']['pass']}/110",
        "Normalizer: "
        f"{normalizer_verified}/110 "
        f"({normalizer['pass']} pass, "
        f"{normalizer['internal_consumed']} internal_consumed)",
        f"Parser pass: {parser['pass']}/110",
        "Parser internal_consumed: "
        f"{parser['internal_consumed']}/110",
        "Parser unsupported_fail_closed: "
        f"{parser['unsupported_fail_closed']}/110",
        f"Parser missing: {parser['missing']}/110",
        f"Behavior verified: {behavior_verified}/110",
        f"Shape pass: {shapes['pass']}",
        "Shape fail-closed: "
        f"{shapes['unsupported_fail_closed']}",
        f"Shape missing: {shapes['missing']}",
    ]


def render_support_status(metrics: Mapping[str, Any]) -> str:
    lines = [
        SUPPORT_START,
        "",
        "当前发布门禁基线：",
        "",
    ]
    lines.extend(f"- `{line}`" for line in summary_lines(metrics))
    realworld = metrics["realworld"]
    lines.extend(
        [
            "- 真实语料："
            f"{realworld['decompile_success']}/"
            f"{realworld['input_files']} 成功反编译，"
            f"{realworld['fail_closed']} 项明确 fail-closed；",
            "- 差分行为探针："
            f"{realworld['behavior_consistent']} 项一致，"
            f"{realworld['behavior_mismatch']} 项不一致；",
            f"- 全量测试允许的已解释 legacy skip："
            f"{metrics['expected_skips']} 项。",
            "",
            SUPPORT_END,
        ]
    )
    return "\n".join(lines)


def render_release_report(
    policy: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> str:
    realworld = metrics["realworld"]
    fail_closed_shapes = policy["shape"]["unsupported_fail_closed"]
    skips = policy["pytest"]["expected_skips"]
    lines = [
        "# CPython 3.11 发布门禁",
        "",
        "> 本文件由 `test/bytecode_3.11/run_release_gate.py` 生成，",
        "> 与阶段 9 发布策略、覆盖矩阵和真实语料归档同步。",
        "",
        "## 四层覆盖",
        "",
    ]
    lines.extend(f"- `{line}`" for line in summary_lines(metrics))
    lines.extend(
        [
            "",
            "## 固定环境",
            "",
            "- Runtime：CPython 3.11.9",
            *[
                f"- `{name}`：{version}"
                for name, version in metrics["dependencies"].items()
            ],
            "",
            "## 真实语料归档",
            "",
            f"- 输入：{realworld['input_files']}",
            f"- 成功反编译：{realworld['decompile_success']}",
            f"- fail-closed：{realworld['fail_closed']}",
            f"- 语法失败：{realworld['syntax_failure']}",
            f"- 未包装崩溃：{realworld['unexpected_crash']}",
            f"- 行为一致：{realworld['behavior_consistent']}",
            f"- 行为不一致：{realworld['behavior_mismatch']}",
            "",
            "## 已审批的 fail-closed shape",
            "",
        ]
    )
    lines.extend(f"- `{name}`" for name in fail_closed_shapes)
    lines.extend(["", "## 已解释的全量测试 skip", ""])
    lines.extend(
        f"- `{item['nodeid']}`：{item['reason']}" for item in skips
    )
    lines.extend(
        [
            "",
            "## CI 命令",
            "",
            "```console",
            "python test/bytecode_3.11/run_release_gate.py --check",
            "python test/bytecode_3.11/generate.py --check",
            "python test/bytecode_3.11/run_release_gate.py --pytest",
            "```",
            "",
            "任何 `missing`、未经审批的状态变化、行为不一致、报告过期、",
            "新增 skip 或全量测试失败都会使门禁返回非零状态。",
            "",
        ]
    )
    return "\n".join(lines)


def _extract_support_status(source: str) -> str:
    start = source.find(SUPPORT_START)
    end = source.find(SUPPORT_END)
    if start < 0 or end < start:
        raise ReleaseGateError("支持文档缺少发布状态标记")
    return source[start : end + len(SUPPORT_END)]


def _check_equal(path: Path, expected: str, description: str) -> None:
    actual = path.read_text(encoding="utf-8") if path.is_file() else ""
    if actual != expected:
        raise ReleaseGateError(f"{description}已过期：{path}")


def load_and_validate() -> tuple[dict[str, Any], dict[str, Any]]:
    """Load all release inputs, validate policy, and return policy/metrics."""
    policy = _load_json(POLICY_PATH)
    opcode_matrix = _load_json(OPCODE_MATRIX_PATH)
    shape_matrix = _load_json(SHAPE_MATRIX_PATH)
    realworld = _load_json(REALWORLD_ARCHIVE_PATH)

    matrix_generator = _load_script(
        "generate_opcode_matrix_release_gate311",
        BYTECODE_DIR / "generate_opcode_matrix.py",
    )
    try:
        matrix_generator.validate_opcode_matrix(opcode_matrix)
        matrix_generator.validate_shape_matrix(shape_matrix)
    except matrix_generator.MatrixValidationError as error:
        raise ReleaseGateError(str(error)) from error

    metrics = validate_release_policy(
        policy,
        opcode_matrix,
        shape_matrix,
        realworld,
    )
    _check_equal(
        OPCODE_REPORT_PATH,
        matrix_generator.render_opcode_report(opcode_matrix),
        "opcode 覆盖报告",
    )
    _check_equal(
        SHAPE_REPORT_PATH,
        matrix_generator.render_shape_report(shape_matrix),
        "shape 覆盖报告",
    )

    realworld_runner = _load_script(
        "run_realworld_regression_release_gate311",
        BYTECODE_DIR / "run_realworld_regression.py",
    )
    _check_equal(
        REALWORLD_REPORT_PATH,
        realworld_runner.render_report(realworld),
        "真实语料报告",
    )
    _check_equal(
        RELEASE_REPORT_PATH,
        render_release_report(policy, metrics),
        "发布门禁报告",
    )
    support_source = SUPPORT_PATH.read_text(encoding="utf-8")
    if _extract_support_status(support_source) != render_support_status(metrics):
        raise ReleaseGateError("PYTHON_311_SUPPORT.md 发布状态已过期")
    return policy, metrics


def _normalize_skip_reason(report) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) >= 3:
        reason = str(longrepr[2])
    else:
        crash = getattr(longrepr, "reprcrash", None)
        reason = str(getattr(crash, "message", longrepr))
    for prefix in ("Skipped: ", "SKIPPED: "):
        if reason.startswith(prefix):
            reason = reason[len(prefix) :]
    return reason


class _SkipRecorder:
    def __init__(self):
        self.skips: dict[str, str] = {}

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skips[report.nodeid] = _normalize_skip_reason(report)


def validate_observed_skips(
    expected: Sequence[Mapping[str, str]],
    observed: Mapping[str, str],
) -> None:
    expected_map = {item["nodeid"]: item["reason"] for item in expected}
    observed_map = dict(observed)
    if observed_map != expected_map:
        raise ReleaseGateError(
            "全量 pytest skip 与白名单不一致："
            f"实际 {json.dumps(observed_map, ensure_ascii=False, sort_keys=True)}；"
            f"策略 {json.dumps(expected_map, ensure_ascii=False, sort_keys=True)}"
        )


def run_full_pytest(policy: Mapping[str, Any]) -> None:
    import pytest

    recorder = _SkipRecorder()
    exit_code = pytest.main(
        ["-q", "-rs", str(ROOT / "pytest")],
        plugins=[recorder],
    )
    if int(exit_code) != 0:
        raise ReleaseGateError(f"全量 pytest 失败，退出码 {int(exit_code)}")
    validate_observed_skips(
        policy["pytest"]["expected_skips"],
        recorder.skips,
    )
    print(f"全量 pytest skip 白名单通过：{len(recorder.skips)} 项")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查策略、矩阵和所有报告（默认行为）",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="重新生成发布门禁报告",
    )
    parser.add_argument(
        "--pytest",
        action="store_true",
        help="在静态门禁通过后执行全量 pytest 和 skip 白名单检查",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        if args.write_report:
            policy = _load_json(POLICY_PATH)
            opcode_matrix = _load_json(OPCODE_MATRIX_PATH)
            shape_matrix = _load_json(SHAPE_MATRIX_PATH)
            realworld = _load_json(REALWORLD_ARCHIVE_PATH)
            metrics = validate_release_policy(
                policy,
                opcode_matrix,
                shape_matrix,
                realworld,
            )
            RELEASE_REPORT_PATH.write_text(
                render_release_report(policy, metrics),
                encoding="utf-8",
            )
            print(f"写入发布门禁报告：{RELEASE_REPORT_PATH}")
        policy, metrics = load_and_validate()
        for line in summary_lines(metrics):
            print(line)
        print("文档与归档时效检查：通过")
        if args.pytest:
            run_full_pytest(policy)
    except (OSError, ReleaseGateError) as error:
        print(f"Python 3.11 发布门禁失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
