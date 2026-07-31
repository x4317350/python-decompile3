#!/usr/bin/env python3
"""Build the phase-0 baseline for Python 3.11 fail-closed remediation."""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from decompyle3.errors import Decompyle3Error


ROOT = Path(__file__).resolve().parents[2]
BYTECODE_DIR = Path(__file__).resolve().parent
RUNNER_PATH = BYTECODE_DIR / "run_realworld_regression.py"
ARCHIVE_PATH = BYTECODE_DIR / "realworld_regression311.json"
SHAPE_MATRIX_PATH = BYTECODE_DIR / "shape_matrix.json"
DEFAULT_JSON = BYTECODE_DIR / "fail_closed_baseline311.json"
DEFAULT_REPORT = ROOT / "PYTHON_311_FAIL_CLOSED_BASELINE.md"

ORDER = (
    "realworld_unpack_assignment",
    "realworld_import_protocol",
    "realworld_exception_cleanup_control_transfer",
    "realworld_call_and_expression_stack",
    "realworld_function_object_flow",
    "realworld_comprehension_and_iterator_protocol",
    "realworld_with_control_transfer",
    "realworld_recursive_structure",
    "realworld_match_boundary",
    "irreducible_control_flow",
)

METADATA = {
    "realworld_unpack_assignment": {
        "risk": "medium",
        "dependencies": [],
        "objective": (
            "Recover assignment and loop-target routing without allowing "
            "parser-only unpack markers to escape."
        ),
    },
    "realworld_import_protocol": {
        "risk": "medium",
        "dependencies": [],
        "objective": (
            "Bind IMPORT_FROM to its owning import transaction across "
            "intermediate stack operations."
        ),
    },
    "realworld_function_object_flow": {
        "risk": "medium",
        "dependencies": ["realworld_call_and_expression_stack"],
        "objective": (
            "Represent function values as expressions until their final "
            "name, attribute, subscript, or call consumer is known."
        ),
    },
    "realworld_recursive_structure": {
        "risk": "high",
        "dependencies": [
            "realworld_call_and_expression_stack",
            "realworld_comprehension_and_iterator_protocol",
        ],
        "objective": (
            "Replace unbounded recursive structure probing with bounded or "
            "iterative traversal while preserving cycle detection."
        ),
    },
    "realworld_with_control_transfer": {
        "risk": "high",
        "dependencies": [
            "realworld_call_and_expression_stack",
            "realworld_exception_cleanup_control_transfer",
        ],
        "objective": (
            "Recover return, yield, break, continue, and cleanup transfers "
            "inside with/async-with bodies."
        ),
    },
    "realworld_exception_cleanup_control_transfer": {
        "risk": "very_high",
        "dependencies": [],
        "objective": (
            "Structure exception-table cleanup and RERAISE/POP_EXCEPT "
            "transfers before expression fallback."
        ),
    },
    "realworld_comprehension_and_iterator_protocol": {
        "risk": "very_high",
        "dependencies": ["realworld_call_and_expression_stack"],
        "objective": (
            "Recover nested iterator, filter, append/add, generator, and "
            "suspension protocols in arbitrary enclosing code objects."
        ),
    },
    "realworld_call_and_expression_stack": {
        "risk": "very_high",
        "dependencies": [],
        "objective": (
            "Split the umbrella into precise call, stack, jump-expression, "
            "and generated-source shapes, then eliminate each residual."
        ),
    },
    "realworld_match_boundary": {
        "risk": "high",
        "dependencies": [
            "realworld_call_and_expression_stack",
            "realworld_exception_cleanup_control_transfer",
        ],
        "objective": (
            "Recover canonical case fallthrough and body terminators using "
            "CFG dominance rather than source-order guessing."
        ),
    },
    "irreducible_control_flow": {
        "risk": "security_boundary",
        "dependencies": [],
        "objective": (
            "Keep artificial irreducible graphs fail-closed unless a "
            "semantics-preserving structured representation is proven."
        ),
    },
}

CONTEXT_RE = re.compile(r"\s+\[version=3\.11,.*\]$")
INSTRUCTION_RANGE_RE = re.compile(r"Instruction range \d+:\d+")
OFFSET_RE = re.compile(r"\boffset[ =]\??\d+\b")
QUOTED_RE = re.compile(r"'[^']*'")
NUMBER_RE = re.compile(r"\b\d+\b")


class BaselineError(ValueError):
    """The remediation baseline cannot be reproduced safely."""


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BaselineError(f"JSON 根节点必须是 object：{path}")
    return value


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_realworld_regression_fail_closed_baseline311",
        RUNNER_PATH,
    )
    if spec is None or spec.loader is None:
        raise BaselineError(f"无法加载真实语料运行器：{RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalize_failure_message(error: BaseException) -> str:
    """Collapse input-specific names and offsets into a stable signature."""
    message = CONTEXT_RE.sub("", str(error))
    message = INSTRUCTION_RANGE_RE.sub("Instruction range #:#", message)
    message = OFFSET_RE.sub("offset #", message)
    message = QUOTED_RE.sub("'<value>'", message)
    message = NUMBER_RE.sub("#", message)
    return " ".join(message.split())


def _environment(runner) -> dict[str, Any]:
    return {
        "runtime": ".".join(map(str, sys.version_info[:3])),
        "platform": sys.platform,
        "package_versions": runner._package_versions(),
    }


def _representative(
    item,
    error: BaseException,
    opcode: str | None,
    signature: str,
) -> dict[str, Any]:
    return {
        "group": item.group,
        "name": item.name,
        "error_type": type(error).__name__,
        "opcode": opcode,
        "code_name": getattr(error, "code_name", None),
        "offset": getattr(error, "offset", None),
        "signature": signature,
        "error": str(error),
    }


def run_baseline() -> dict[str, Any]:
    """Re-run the archived input set and aggregate all fail-closed errors."""
    runner = _load_runner()
    archive = _load_json(ARCHIVE_PATH)
    shape_matrix = _load_json(SHAPE_MATRIX_PATH)
    if _environment(runner) != archive["environment"]:
        raise BaselineError(
            "阶段 0 必须在真实语料归档记录的固定环境中运行"
        )
    inputs = runner.collect_inputs()
    if len(inputs) != archive["totals"]["input_files"]:
        raise BaselineError("当前真实语料输入数量与阶段 8 归档不一致")
    if runner._input_digest(inputs) != archive["input_digest"]:
        raise BaselineError("当前真实语料输入摘要与阶段 8 归档不一致")

    aggregates = {
        name: {
            "count": 0,
            "error_types": Counter(),
            "opcodes": Counter(),
            "signatures": Counter(),
            "representatives": [],
        }
        for name in ORDER
    }
    unexpected = []
    for item in inputs:
        try:
            runner._recover(item.path)
        except Decompyle3Error as error:
            shape = runner.classify_failure(error)
            if shape not in aggregates:
                unexpected.append(
                    {
                        "group": item.group,
                        "name": item.name,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
                continue
            matched = runner.OPCODE_RE.search(str(error))
            opcode = matched.group(1) if matched else None
            signature = normalize_failure_message(error)
            aggregate = aggregates[shape]
            aggregate["count"] += 1
            aggregate["error_types"][type(error).__name__] += 1
            aggregate["opcodes"][opcode or "<none>"] += 1
            aggregate["signatures"][signature] += 1
            if len(aggregate["representatives"]) < 5:
                aggregate["representatives"].append(
                    _representative(
                        item,
                        error,
                        opcode,
                        signature,
                    )
                )
        except (SyntaxError, UnicodeError):
            continue
        except BaseException as error:
            unexpected.append(
                {
                    "group": item.group,
                    "name": item.name,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )

    archived_counts = archive["failure_classifications"]
    actual_counts = {
        name: aggregates[name]["count"]
        for name in runner.REALWORLD_SHAPES
    }
    if actual_counts != archived_counts:
        raise BaselineError(
            "阶段 0 失败分类数量与阶段 8 归档不一致："
            f"{actual_counts} != {archived_counts}"
        )
    if unexpected:
        raise BaselineError(f"阶段 0 出现未归类失败：{unexpected[:3]}")

    matrix_by_name = {
        item["name"]: item for item in shape_matrix["shapes"]
    }
    shapes = []
    for stage, name in enumerate(ORDER, 1):
        aggregate = aggregates[name]
        matrix_item = matrix_by_name[name]
        metadata = METADATA[name]
        disposition = (
            "retain_safety_boundary"
            if name == "irreducible_control_flow"
            else "recover_or_split_until_zero"
        )
        shapes.append(
            {
                "stage": stage,
                "name": name,
                "category": matrix_item["category"],
                "status": matrix_item["status"],
                "disposition": disposition,
                "risk": metadata["risk"],
                "dependencies": metadata["dependencies"],
                "objective": metadata["objective"],
                "archived_fail_closed": aggregate["count"],
                "error_types": dict(
                    sorted(aggregate["error_types"].items())
                ),
                "opcodes": dict(
                    sorted(
                        aggregate["opcodes"].items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
                "signatures": [
                    {"signature": signature, "count": count}
                    for signature, count in aggregate[
                        "signatures"
                    ].most_common()
                ],
                "representatives": aggregate["representatives"],
            }
        )

    return {
        "schema_version": 1,
        "phase": 0,
        "target": {
            "implementation": "CPython",
            "version": "3.11.9",
        },
        "source_commit": "9f5bb1e4",
        "environment": archive["environment"],
        "input_digest": archive["input_digest"],
        "input_files": archive["totals"]["input_files"],
        "decompile_success": archive["totals"]["decompile_success"],
        "fail_closed": archive["totals"]["fail_closed"],
        "shape_inventory": 10,
        "realworld_shape_count": 9,
        "safety_boundary_count": 1,
        "shapes": shapes,
    }


def _top_counts(values: Mapping[str, int], limit: int = 4) -> str:
    if not values:
        return "—"
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return "、".join(f"`{name}`×{count}" for name, count in ordered[:limit])


def render_report(result: Mapping[str, Any]) -> str:
    lines = [
        "# CPython 3.11 Fail-closed Shape 阶段 0 基线",
        "",
        "> 本文件由 "
        "`test/bytecode_3.11/build_fail_closed_baseline.py` 生成。",
        "> 计数来自固定环境中的 604 文件真实语料重放；人工不可约 CFG",
        "> 是独立的安全边界，不包含在 401 个真实语料失败中。",
        "",
        "## 汇总",
        "",
        f"- 输入文件：{result['input_files']}",
        f"- 成功反编译：{result['decompile_success']}",
        f"- fail-closed：{result['fail_closed']}",
        f"- 真实语料失败家族：{result['realworld_shape_count']}",
        f"- 人工安全边界：{result['safety_boundary_count']}",
        f"- 输入摘要：`{result['input_digest']}`",
        "",
        "## 修复顺序",
        "",
        "| 阶段 | Shape | 基线失败 | 风险 | 主要 opcode | 处置 |",
        "| ---: | --- | ---: | --- | --- | --- |",
    ]
    for item in result["shapes"]:
        lines.append(
            f"| {item['stage']} | `{item['name']}` | "
            f"{item['archived_fail_closed']} | {item['risk']} | "
            f"{_top_counts(item['opcodes'])} | "
            f"`{item['disposition']}` |"
        )
    lines.extend(
        [
            "",
            "## 分项基线",
            "",
        ]
    )
    for item in result["shapes"]:
        dependencies = "、".join(
            f"`{name}`" for name in item["dependencies"]
        ) or "无"
        lines.extend(
            [
                f"### {item['stage']}. `{item['name']}`",
                "",
                f"- 基线失败：{item['archived_fail_closed']}",
                f"- 风险：`{item['risk']}`",
                f"- 依赖：{dependencies}",
                f"- 目标：{item['objective']}",
                f"- 错误类型：{_top_counts(item['error_types'])}",
                f"- Opcode：{_top_counts(item['opcodes'], limit=8)}",
                "- 主要错误签名：",
                "",
            ]
        )
        signatures = item["signatures"][:8]
        if signatures:
            lines.extend(
                f"  - `{signature['signature']}`："
                f"{signature['count']}"
                for signature in signatures
            )
        else:
            lines.append("  - 无真实语料；由人工 CFG 契约覆盖。")
        lines.extend(["", "代表输入：", ""])
        representatives = item["representatives"]
        if representatives:
            lines.extend(
                f"- `{sample['group']}:{sample['name']}`，"
                f"code=`{sample['code_name'] or '—'}`，"
                f"opcode=`{sample['opcode'] or '—'}`"
                for sample in representatives
            )
        else:
            lines.append("- 无；参见人工不可约 CFG 单元测试。")
        lines.append("")
    lines.extend(
        [
            "## 阶段 0 约束",
            "",
            "- 任一粗粒度家族只有在归档计数降为 0 后才可直接转为 `pass`；",
            "- 部分修复必须把剩余失败拆成更精确、可复现的 shape；",
            "- 每个新增子 shape 必须有最小 fixture、AST/语法验证和差分行为测试；",
            "- 不得用放宽异常捕获、删除语料或修改分类顺序制造计数下降；",
            "- 人工不可约 CFG 不计入 604 文件成功率，继续作为安全边界审查；",
            "- 每阶段必须更新真实语料归档、shape 矩阵和发布策略。",
            "",
        ]
    )
    return "\n".join(lines)


def _formatted_json(result: Mapping[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def _check_file(path: Path, expected: str) -> bool:
    actual = path.read_text(encoding="utf-8") if path.is_file() else ""
    if actual == expected:
        return True
    print(f"阶段 0 产物已过期：{path}", file=sys.stderr)
    print(
        "".join(
            difflib.unified_diff(
                actual.splitlines(True),
                expected.splitlines(True),
                fromfile=str(path),
                tofile=f"{path}（期望）",
            )
        ),
        file=sys.stderr,
    )
    return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_baseline()
    except (OSError, BaselineError) as error:
        print(f"阶段 0 基线失败：{error}", file=sys.stderr)
        return 1
    json_text = _formatted_json(result)
    report_text = render_report(result)
    if args.check:
        okay = _check_file(args.output_json, json_text)
        okay &= _check_file(args.output_report, report_text)
        return 0 if okay else 1
    args.output_json.write_text(json_text, encoding="utf-8")
    args.output_report.write_text(report_text, encoding="utf-8")
    print(f"写入阶段 0 JSON：{args.output_json}")
    print(f"写入阶段 0 报告：{args.output_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
