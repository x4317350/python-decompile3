#!/usr/bin/env python3
"""Validate CPython 3.11 coverage matrices and generate Markdown reports."""

from __future__ import annotations

import argparse
import difflib
import dis
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
BYTECODE_DIR = Path(__file__).resolve().parent
DEFAULT_OPCODE_MATRIX = BYTECODE_DIR / "opcode_matrix.json"
DEFAULT_SHAPE_MATRIX = BYTECODE_DIR / "shape_matrix.json"
DEFAULT_OPCODE_REPORT = ROOT / "PYTHON_311_OPCODE_COVERAGE.md"
DEFAULT_SHAPE_REPORT = ROOT / "PYTHON_311_SHAPE_COVERAGE.md"

ALLOWED_STATUSES = (
    "pass",
    "internal_consumed",
    "unsupported_fail_closed",
    "not_applicable",
    "missing",
)
LAYERS = ("scanner", "normalizer", "parser", "behavior")


class MatrixValidationError(ValueError):
    """A coverage matrix is incomplete, inconsistent, or stale."""


def _require_fields(
    value: Mapping[str, Any],
    required: Iterable[str],
    context: str,
) -> None:
    missing = sorted(set(required) - set(value))
    if missing:
        raise MatrixValidationError(
            f"{context} 缺少字段：{', '.join(missing)}"
        )


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise MatrixValidationError(f"矩阵文件不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise MatrixValidationError(
            f"矩阵 JSON 无效：{path}:{error.lineno}:{error.colno}: "
            f"{error.msg}"
        ) from error
    if not isinstance(value, dict):
        raise MatrixValidationError(f"矩阵根节点必须是 object：{path}")
    return value


def _validate_status_values(data: Mapping[str, Any], context: str) -> None:
    values = data.get("status_values")
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise MatrixValidationError(f"{context}.status_values 必须是字符串数组")
    if len(values) != len(set(values)):
        raise MatrixValidationError(f"{context}.status_values 存在重复值")
    if set(values) != set(ALLOWED_STATUSES):
        raise MatrixValidationError(
            f"{context}.status_values 必须精确包含："
            f"{', '.join(ALLOWED_STATUSES)}"
        )


def _validate_test_nodes(
    tests: Any,
    context: str,
    status: str,
) -> None:
    if not isinstance(tests, list) or not all(
        isinstance(test, str) and test for test in tests
    ):
        raise MatrixValidationError(f"{context}.tests 必须是字符串数组")
    if status in (
        "pass",
        "internal_consumed",
        "unsupported_fail_closed",
    ) and not tests:
        raise MatrixValidationError(
            f"{context} 状态为 {status} 时必须关联测试"
        )

    for test in tests:
        parts = test.split("::")
        if len(parts) < 2:
            raise MatrixValidationError(
                f"{context} 测试节点必须包含文件和测试名：{test}"
            )
        test_path = ROOT / parts[0]
        if not test_path.is_file():
            raise MatrixValidationError(
                f"{context} 引用的测试文件不存在：{parts[0]}"
            )
        test_name = parts[1].split("[", 1)[0]
        source = test_path.read_text(encoding="utf-8")
        if f"def {test_name}(" not in source:
            raise MatrixValidationError(
                f"{context} 引用的测试函数不存在：{test}"
            )


def _validate_runtime_opcode_table(opcodes: Sequence[Mapping[str, Any]]) -> None:
    if sys.version_info[:2] != (3, 11):
        raise MatrixValidationError(
            "opcode 矩阵一致性检查必须使用 CPython 3.11"
        )

    from decompyle3.scanners.scanner311 import Scanner311

    matrix_opmap = {item["name"]: item["opcode"] for item in opcodes}
    scanner_opmap = Scanner311().opc.opmap
    if matrix_opmap != dis.opmap:
        raise MatrixValidationError(
            "opcode_matrix.json 与 CPython 3.11 dis.opmap 不一致"
        )
    if matrix_opmap != scanner_opmap:
        raise MatrixValidationError(
            "opcode_matrix.json 与 Scanner311 的 xdis opcode 表不一致"
        )


def validate_opcode_matrix(
    data: Mapping[str, Any],
    *,
    runtime_check: bool = True,
) -> None:
    """Validate the machine-readable 110-opcode inventory."""
    _require_fields(
        data,
        (
            "schema_version",
            "phase",
            "target",
            "opcode_source",
            "status_values",
            "baseline",
            "opcodes",
        ),
        "opcode matrix",
    )
    _validate_status_values(data, "opcode matrix")

    if not isinstance(data["schema_version"], int) or data["schema_version"] < 1:
        raise MatrixValidationError("opcode matrix.schema_version 必须是正整数")
    if not isinstance(data["phase"], int) or data["phase"] < 0:
        raise MatrixValidationError("opcode matrix.phase 必须是非负整数")

    target = data["target"]
    if not isinstance(target, dict):
        raise MatrixValidationError("opcode matrix.target 必须是 object")
    _require_fields(
        target,
        ("implementation", "version", "cache_tag", "magic_int", "magic_hex"),
        "opcode matrix.target",
    )
    if target["implementation"] != "CPython":
        raise MatrixValidationError("opcode matrix 只接受 CPython 目标")

    source = data["opcode_source"]
    if not isinstance(source, dict):
        raise MatrixValidationError("opcode matrix.opcode_source 必须是 object")
    _require_fields(
        source,
        (
            "authoritative",
            "cross_check",
            "authoritative_count",
            "cross_check_count",
            "tables_equal",
        ),
        "opcode matrix.opcode_source",
    )
    if source["authoritative_count"] != 110:
        raise MatrixValidationError("CPython 3.11 opcode 数量必须为 110")
    if source["cross_check_count"] != 110 or source["tables_equal"] is not True:
        raise MatrixValidationError("CPython 与 xdis opcode 表基线不一致")

    baseline = data["baseline"]
    if not isinstance(baseline, dict):
        raise MatrixValidationError("opcode matrix.baseline 必须是 object")
    _require_fields(
        baseline,
        (
            "corpus_glob",
            "corpus_source_count",
            "code_object_count",
            "raw_seen_count",
            "normalized_seen_count",
            "raw_missing",
            "normalized_missing",
            "python",
            "decompyle3",
            "xdis",
            "pytest",
        ),
        "opcode matrix.baseline",
    )

    opcodes = data["opcodes"]
    if not isinstance(opcodes, list):
        raise MatrixValidationError("opcode matrix.opcodes 必须是数组")
    if len(opcodes) != 110:
        raise MatrixValidationError(
            f"opcode matrix 必须包含 110 项，实际为 {len(opcodes)}"
        )

    opcode_numbers = set()
    opcode_names = set()
    raw_seen = set()
    normalized_seen = set()
    for index, item in enumerate(opcodes):
        context = f"opcode matrix.opcodes[{index}]"
        if not isinstance(item, dict):
            raise MatrixValidationError(f"{context} 必须是 object")
        _require_fields(
            item,
            (
                "opcode",
                "name",
                "category",
                "source_fixture",
                "observed_in",
                "corpus",
                "layers",
                "tests",
                "notes",
            ),
            context,
        )
        opcode = item["opcode"]
        name = item["name"]
        if not isinstance(opcode, int) or not 0 <= opcode <= 255:
            raise MatrixValidationError(f"{context}.opcode 必须位于 0..255")
        if not isinstance(name, str) or not name:
            raise MatrixValidationError(f"{context}.name 必须是非空字符串")
        if opcode in opcode_numbers:
            raise MatrixValidationError(f"opcode 编号重复：{opcode}")
        if name in opcode_names:
            raise MatrixValidationError(f"opcode 名称重复：{name}")
        opcode_numbers.add(opcode)
        opcode_names.add(name)

        if not isinstance(item["category"], str) or not item["category"]:
            raise MatrixValidationError(f"{context}.category 必须是非空字符串")
        if item["source_fixture"] is not None:
            fixture = ROOT / item["source_fixture"]
            if not fixture.is_file():
                raise MatrixValidationError(
                    f"{context}.source_fixture 不存在："
                    f"{item['source_fixture']}"
                )
        observed_in = item["observed_in"]
        if not isinstance(observed_in, list) or not all(
            isinstance(filename, str) and filename for filename in observed_in
        ):
            raise MatrixValidationError(
                f"{context}.observed_in 必须是字符串数组"
            )
        for filename in observed_in:
            observed_path = (
                ROOT / filename
                if "/" in filename
                else ROOT / "test" / "simple_source" / "311" / filename
            )
            if not observed_path.is_file():
                raise MatrixValidationError(
                    f"{context}.observed_in 文件不存在：{filename}"
                )

        corpus = item["corpus"]
        if not isinstance(corpus, dict):
            raise MatrixValidationError(f"{context}.corpus 必须是 object")
        _require_fields(corpus, ("raw_seen", "normalized_seen"), f"{context}.corpus")
        if not isinstance(corpus["raw_seen"], bool) or not isinstance(
            corpus["normalized_seen"], bool
        ):
            raise MatrixValidationError(
                f"{context}.corpus 状态必须是 boolean"
            )
        if corpus["raw_seen"] != bool(observed_in):
            raise MatrixValidationError(
                f"{context}.raw_seen 与 observed_in 不一致"
            )
        if corpus["raw_seen"]:
            raw_seen.add(name)
        if corpus["normalized_seen"]:
            normalized_seen.add(name)

        layers = item["layers"]
        if not isinstance(layers, dict) or set(layers) != set(LAYERS):
            raise MatrixValidationError(
                f"{context}.layers 必须精确包含：{', '.join(LAYERS)}"
            )
        for layer, status in layers.items():
            if status not in ALLOWED_STATUSES:
                raise MatrixValidationError(
                    f"{context}.layers.{layer} 包含未知状态：{status}"
                )
        strongest_status = next(
            (
                layers[layer]
                for layer in LAYERS
                if layers[layer]
                in (
                    "pass",
                    "internal_consumed",
                    "unsupported_fail_closed",
                )
            ),
            "missing",
        )
        _validate_test_nodes(item["tests"], context, strongest_status)
        if not isinstance(item["notes"], str):
            raise MatrixValidationError(f"{context}.notes 必须是字符串")

    raw_missing = sorted(opcode_names - raw_seen)
    normalized_missing = sorted(opcode_names - normalized_seen)
    if baseline["raw_seen_count"] != len(raw_seen):
        raise MatrixValidationError("baseline.raw_seen_count 与 opcode 项不一致")
    if baseline["normalized_seen_count"] != len(normalized_seen):
        raise MatrixValidationError(
            "baseline.normalized_seen_count 与 opcode 项不一致"
        )
    if baseline["raw_missing"] != raw_missing:
        raise MatrixValidationError("baseline.raw_missing 与 opcode 项不一致")
    if baseline["normalized_missing"] != normalized_missing:
        raise MatrixValidationError(
            "baseline.normalized_missing 与 opcode 项不一致"
        )

    if runtime_check:
        _validate_runtime_opcode_table(opcodes)


def validate_shape_matrix(data: Mapping[str, Any]) -> None:
    """Validate the control-flow and multi-opcode shape inventory."""
    _require_fields(
        data,
        ("schema_version", "phase", "target", "status_values", "shapes"),
        "shape matrix",
    )
    _validate_status_values(data, "shape matrix")
    if not isinstance(data["schema_version"], int) or data["schema_version"] < 1:
        raise MatrixValidationError("shape matrix.schema_version 必须是正整数")
    if not isinstance(data["phase"], int) or data["phase"] < 0:
        raise MatrixValidationError("shape matrix.phase 必须是非负整数")

    target = data["target"]
    if not isinstance(target, dict):
        raise MatrixValidationError("shape matrix.target 必须是 object")
    _require_fields(
        target,
        ("implementation", "version"),
        "shape matrix.target",
    )
    if target["implementation"] != "CPython" or target["version"] != "3.11":
        raise MatrixValidationError("shape matrix 目标必须是 CPython 3.11")

    shapes = data["shapes"]
    if not isinstance(shapes, list):
        raise MatrixValidationError("shape matrix.shapes 必须是数组")
    names = set()
    for index, item in enumerate(shapes):
        context = f"shape matrix.shapes[{index}]"
        if not isinstance(item, dict):
            raise MatrixValidationError(f"{context} 必须是 object")
        _require_fields(
            item,
            (
                "name",
                "category",
                "status",
                "fixture",
                "expected_error",
                "tests",
                "notes",
            ),
            context,
        )
        name = item["name"]
        if not isinstance(name, str) or not name:
            raise MatrixValidationError(f"{context}.name 必须是非空字符串")
        if name in names:
            raise MatrixValidationError(f"shape 名称重复：{name}")
        names.add(name)
        if not isinstance(item["category"], str) or not item["category"]:
            raise MatrixValidationError(f"{context}.category 必须是非空字符串")
        status = item["status"]
        if status not in ALLOWED_STATUSES:
            raise MatrixValidationError(
                f"{context}.status 包含未知状态：{status}"
            )
        if item["fixture"] is not None:
            fixture = ROOT / item["fixture"]
            if not fixture.is_file():
                raise MatrixValidationError(
                    f"{context}.fixture 不存在：{item['fixture']}"
                )
        expected_error = item["expected_error"]
        if expected_error is not None and not isinstance(expected_error, str):
            raise MatrixValidationError(
                f"{context}.expected_error 必须是字符串或 null"
            )
        if status == "unsupported_fail_closed" and not expected_error:
            raise MatrixValidationError(
                f"{context} fail-closed 状态必须填写 expected_error"
            )
        if status != "unsupported_fail_closed" and expected_error is not None:
            raise MatrixValidationError(
                f"{context} 只有 fail-closed 状态可以填写 expected_error"
            )
        _validate_test_nodes(item["tests"], context, status)
        if not isinstance(item["notes"], str) or not item["notes"]:
            raise MatrixValidationError(f"{context}.notes 必须是非空字符串")


def _status_counts(items: Sequence[Mapping[str, Any]], layer: str) -> Counter:
    return Counter(item["layers"][layer] for item in items)


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def render_opcode_report(data: Mapping[str, Any]) -> str:
    """Render the deterministic opcode coverage report."""
    opcodes = data["opcodes"]
    target = data["target"]
    baseline = data["baseline"]
    lines = [
        "# CPython 3.11 Opcode 四层覆盖报告",
        "",
        "> 本文件由 `test/bytecode_3.11/generate_opcode_matrix.py` 自动生成，",
        "> 请勿手工修改。",
        "",
        "## 目标与来源",
        "",
        f"- Implementation：{target['implementation']}",
        f"- Python：{target['version']}",
        f"- Cache tag：`{target['cache_tag']}`",
        f"- Magic：`{target['magic_hex']}` / `{target['magic_int']}`",
        f"- Opcode inventory：{len(opcodes)}/110",
        f"- CPython/xdis 表一致：{_yes_no(data['opcode_source']['tables_equal'])}",
        "",
        "## Corpus 基线",
        "",
        f"- 源文件：{baseline['corpus_source_count']}",
        f"- Code object：{baseline['code_object_count']}",
        f"- Raw opcode：{baseline['raw_seen_count']}/110",
        f"- Normalized original opcode：{baseline['normalized_seen_count']}/110",
        "",
        "## 四层状态汇总",
        "",
        "| 层级 | pass | internal | fail-closed | N/A | missing |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for layer in LAYERS:
        counts = _status_counts(opcodes, layer)
        lines.append(
            f"| {layer} | {counts['pass']} | "
            f"{counts['internal_consumed']} | "
            f"{counts['unsupported_fail_closed']} | "
            f"{counts['not_applicable']} | {counts['missing']} |"
        )

    lines.extend(
        [
            "",
            f"截至阶段 {data['phase']}，逐 opcode 四层正式状态仍从 `missing`",
            "开始归因。Corpus 中观察到指令不等同于完成 Scanner、Normalizer、",
            "Parser 和行为验证。",
            "",
            "## Opcode 明细",
            "",
            "| 编号 | Opcode | 类别 | Raw | Normalized | Scanner | Normalizer | Parser | Behavior | Fixture |",
            "| ---: | --- | --- | :---: | :---: | --- | --- | --- | --- | --- |",
        ]
    )
    for item in opcodes:
        fixture = item["source_fixture"] or "—"
        lines.append(
            f"| {item['opcode']} | `{item['name']}` | {item['category']} | "
            f"{_yes_no(item['corpus']['raw_seen'])} | "
            f"{_yes_no(item['corpus']['normalized_seen'])} | "
            f"{item['layers']['scanner']} | "
            f"{item['layers']['normalizer']} | "
            f"{item['layers']['parser']} | "
            f"{item['layers']['behavior']} | `{fixture}` |"
        )

    lines.extend(
        [
            "",
            "## 尚未触达",
            "",
            "### Raw corpus",
            "",
            "```text",
            *baseline["raw_missing"],
            "```",
            "",
            "### Normalized corpus",
            "",
            "```text",
            *baseline["normalized_missing"],
            "```",
            "",
            "## 状态定义",
            "",
            "- `pass`：已实现并通过该层验证；",
            "- `internal_consumed`：由内部协议消费，不直接生成 AST；",
            "- `unsupported_fail_closed`：明确不支持并有稳定错误测试；",
            "- `not_applicable`：该层不适用；",
            "- `missing`：尚未完成实现或逐项测试归因。",
            "",
        ]
    )
    return "\n".join(lines)


def render_shape_report(data: Mapping[str, Any]) -> str:
    """Render the deterministic multi-opcode shape coverage report."""
    shapes = data["shapes"]
    counts = Counter(item["status"] for item in shapes)
    lines = [
        "# CPython 3.11 指令组合覆盖报告",
        "",
        "> 本文件由 `test/bytecode_3.11/generate_opcode_matrix.py` 自动生成，",
        "> 请勿手工修改。",
        "",
        "## 汇总",
        "",
        f"- Shape inventory：{len(shapes)}",
        f"- pass：{counts['pass']}",
        f"- internal_consumed：{counts['internal_consumed']}",
        f"- unsupported_fail_closed：{counts['unsupported_fail_closed']}",
        f"- not_applicable：{counts['not_applicable']}",
        f"- missing：{counts['missing']}",
        "",
        "## Shape 明细",
        "",
        "| Shape | 类别 | 状态 | Fixture | 预期错误 | 测试数 |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for item in shapes:
        fixture = item["fixture"] or "—"
        expected_error = item["expected_error"] or "—"
        lines.append(
            f"| `{item['name']}` | {item['category']} | {item['status']} | "
            f"`{fixture}` | `{expected_error}` | {len(item['tests'])} |"
        )

    missing = [item["name"] for item in shapes if item["status"] == "missing"]
    unsupported = [
        item
        for item in shapes
        if item["status"] == "unsupported_fail_closed"
    ]
    lines.extend(["", "## Missing", "", "```text", *missing, "```"])
    lines.extend(["", "## Fail-closed", ""])
    if unsupported:
        for item in unsupported:
            lines.append(
                f"- `{item['name']}`：`{item['expected_error']}`；"
                f"{item['notes']}"
            )
    else:
        lines.append("- 无")
    lines.append("")
    return "\n".join(lines)


def _write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _check_report(path: Path, expected: str) -> bool:
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"报告不存在：{path}", file=sys.stderr)
        return False
    if actual == expected:
        return True
    print(f"报告已过期：{path}", file=sys.stderr)
    diff = difflib.unified_diff(
        actual.splitlines(),
        expected.splitlines(),
        fromfile=str(path),
        tofile=f"{path}（期望）",
        lineterm="",
    )
    for line in list(diff)[:80]:
        print(line, file=sys.stderr)
    return False


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查报告是否最新")
    parser.add_argument(
        "--opcode-matrix",
        type=Path,
        default=DEFAULT_OPCODE_MATRIX,
    )
    parser.add_argument(
        "--shape-matrix",
        type=Path,
        default=DEFAULT_SHAPE_MATRIX,
    )
    parser.add_argument(
        "--opcode-report",
        type=Path,
        default=DEFAULT_OPCODE_REPORT,
    )
    parser.add_argument(
        "--shape-report",
        type=Path,
        default=DEFAULT_SHAPE_REPORT,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        opcode_matrix = _load_json(args.opcode_matrix)
        shape_matrix = _load_json(args.shape_matrix)
        validate_opcode_matrix(opcode_matrix)
        validate_shape_matrix(shape_matrix)
    except MatrixValidationError as error:
        print(f"矩阵校验失败：{error}", file=sys.stderr)
        return 2

    opcode_report = render_opcode_report(opcode_matrix)
    shape_report = render_shape_report(shape_matrix)
    if args.check:
        valid = _check_report(args.opcode_report, opcode_report)
        valid = _check_report(args.shape_report, shape_report) and valid
        if not valid:
            return 1
        print("opcode 和 shape 覆盖报告均为最新")
        return 0

    _write_report(args.opcode_report, opcode_report)
    _write_report(args.shape_report, shape_report)
    print(f"生成 opcode 报告：{args.opcode_report}")
    print(f"生成 shape 报告：{args.shape_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
