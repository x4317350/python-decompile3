#!/usr/bin/env python3
"""Run and archive the CPython 3.11 real-world decompilation regression."""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import importlib.metadata
import io
import json
import re
import sys
import sysconfig
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from xdis.version_info import PythonImplementation

from decompyle3.errors import Decompyle3Error
from decompyle3.semantics.pysource import code_deparse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = (
    ROOT / "test" / "bytecode_3.11" / "realworld_regression311.json"
)
DEFAULT_REPORT = ROOT / "PYTHON_311_REALWORLD_REGRESSION.md"
STDLIB_PACKAGES = (
    "asyncio",
    "collections",
    "concurrent",
    "email",
    "http",
    "importlib",
    "json",
    "logging",
    "multiprocessing",
    "unittest",
    "urllib",
    "xml",
)
THIRD_PARTY_PACKAGES = (
    "attr",
    "attrs",
    "click",
    "packaging",
    "platformdirs",
    "pluggy",
    "_pytest",
)
THIRD_PARTY_DISTRIBUTIONS = (
    "attrs",
    "click",
    "packaging",
    "platformdirs",
    "pluggy",
    "pytest",
)
OPCODE_RE = re.compile(r"\bopcode ([A-Z][A-Z0-9_]*)\b")

REALWORLD_SHAPES = frozenset(
    {
        "realworld_call_and_expression_stack",
        "realworld_comprehension_and_iterator_protocol",
        "realworld_exception_cleanup_control_transfer",
        "realworld_function_object_flow",
        "realworld_import_protocol",
        "realworld_match_boundary",
        "realworld_recursive_structure",
        "realworld_unpack_assignment",
        "realworld_with_control_transfer",
    }
)


@dataclass(frozen=True)
class RegressionInput:
    group: str
    name: str
    path: Path


@dataclass(frozen=True)
class BehaviorCase:
    name: str
    group: str
    path: Path
    probe: str


def _is_test_path(path: Path, base: Path) -> bool:
    relative = path.relative_to(base)
    return any(part.lower() in ("test", "tests") for part in relative.parts)


def collect_inputs(
    root: Path = ROOT,
    stdlib: Path | None = None,
    purelib: Path | None = None,
) -> tuple[RegressionInput, ...]:
    """Return the deterministic breadth-audit input inventory."""
    stdlib = stdlib or Path(sysconfig.get_path("stdlib"))
    purelib = purelib or Path(sysconfig.get_path("purelib"))
    result = []
    seen = set()

    def add(group: str, name: str, path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        result.append(RegressionInput(group, name, resolved))

    for path in sorted(stdlib.glob("*.py")):
        add("stdlib", path.relative_to(stdlib).as_posix(), path)
    for package in STDLIB_PACKAGES:
        package_root = stdlib / package
        for path in sorted(package_root.rglob("*.py")):
            if "__pycache__" in path.parts or _is_test_path(path, stdlib):
                continue
            add("stdlib", path.relative_to(stdlib).as_posix(), path)
    for path in sorted((root / "decompyle3").rglob("*.py")):
        add("project", path.relative_to(root).as_posix(), path)
    for package in THIRD_PARTY_PACKAGES:
        package_root = purelib / package
        if not package_root.is_dir():
            continue
        for path in sorted(package_root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            add(
                "third_party",
                path.relative_to(purelib).as_posix(),
                path,
            )
    return tuple(sorted(result, key=lambda item: (item.group, item.name)))


def classify_failure(error: BaseException) -> str:
    """Map one fail-closed error to a shape-matrix category."""
    message = str(error)
    if "recursion limit reached" in message:
        return "realworld_recursive_structure"
    if "Match case" in message or "match pattern" in message.lower():
        return "realworld_match_boundary"
    if "Returning with-body" in message or "With cleanup" in message:
        return "realworld_with_control_transfer"
    if "IMPORT_FROM" in message or "IMPORT_NAME" in message:
        return "realworld_import_protocol"
    if (
        "_FunctionValue" in message
        or "function definition is stored to a non-name" in message
    ):
        return "realworld_function_object_flow"
    if (
        "_UnpackItem" in message
        or "UNPACK_SEQUENCE" in message
        or "UNPACK_EX" in message
    ):
        return "realworld_unpack_assignment"
    if any(
        marker in message
        for marker in (
            "MAP_ADD",
            "SET_ADD",
            "FOR_ITER",
            "RETURN_GENERATOR",
            "YIELD_VALUE",
        )
    ):
        return "realworld_comprehension_and_iterator_protocol"
    if any(
        marker in message
        for marker in (
            "Finally suite",
            "POP_EXCEPT",
            "PUSH_EXC_INFO",
            "RERAISE",
            "except",
            "Exception handler",
        )
    ):
        return "realworld_exception_cleanup_control_transfer"
    return "realworld_call_and_expression_stack"


def _recover(path: Path) -> str:
    source = path.read_bytes()
    code = compile(source, str(path), "exec", dont_inherit=True)
    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def _input_digest(inputs: tuple[RegressionInput, ...]) -> str:
    digest = hashlib.sha256()
    for item in inputs:
        digest.update(item.group.encode())
        digest.update(b"\0")
        digest.update(item.name.encode())
        digest.update(b"\0")
        digest.update(item.path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def behavior_cases(
    root: Path = ROOT,
    stdlib: Path | None = None,
    purelib: Path | None = None,
) -> tuple[BehaviorCase, ...]:
    stdlib = stdlib or Path(sysconfig.get_path("stdlib"))
    purelib = purelib or Path(sysconfig.get_path("purelib"))
    return (
        BehaviorCase(
            "stdlib_keyword",
            "stdlib",
            stdlib / "keyword.py",
            """
_record(
    "keyword",
    lambda: [
        (value, iskeyword(value), issoftkeyword(value))
        for value in ("def", "match", "ordinary")
    ],
)
""",
        ),
        BehaviorCase(
            "stdlib_colorsys",
            "stdlib",
            stdlib / "colorsys.py",
            """
_record(
    "colorsys",
    lambda: [
        rgb_to_hsv(*rgb)
        for rgb in (
            (0.0, 0.0, 0.0),
            (0.2, 0.4, 0.8),
            (1.0, 0.5, 0.0),
        )
    ],
)
""",
        ),
        BehaviorCase(
            "stdlib_hmac",
            "stdlib",
            stdlib / "hmac.py",
            """
_record(
    "hmac",
    lambda: digest(b"key", b"payload", "sha256").hex(),
)
""",
        ),
        BehaviorCase(
            "project_util",
            "project",
            root / "decompyle3" / "util.py",
            """
_record(
    "project_util",
    lambda: [
        better_repr(-0.0),
        better_repr(float("inf")),
        better_repr(complex(1.5, -2.0)),
        better_repr((1,)),
        better_repr([1, 2]),
    ],
)
""",
        ),
        BehaviorCase(
            "packaging_structures",
            "third_party",
            purelib / "packaging" / "_structures.py",
            """
_record(
    "packaging_structures",
    lambda: (
        repr(Infinity),
        repr(NegativeInfinity),
        type(Infinity).__name__,
        type(NegativeInfinity).__name__,
    ),
)
""",
        ),
        BehaviorCase(
            "click_utils",
            "third_party",
            purelib / "click" / "_utils.py",
            """
_record(
    "click_utils",
    lambda: (
        repr(UNSET),
        repr(FLAG_NEEDS_VALUE),
        UNSET is Sentinel.UNSET,
    ),
)
""",
        ),
    )


def _run_behavior_cases(
    cases: tuple[BehaviorCase, ...],
    artifact_root: Path,
) -> list[dict]:
    pytest_support = ROOT / "pytest"
    inserted = str(pytest_support) not in sys.path
    if inserted:
        sys.path.insert(0, str(pytest_support))
    try:
        from support311 import compare_behavior311

        results = []
        for case in cases:
            if not case.path.is_file():
                results.append(
                    {
                        "name": case.name,
                        "group": case.group,
                        "status": "missing_input",
                    }
                )
                continue
            try:
                compare_behavior311(
                    case.path,
                    case.probe,
                    artifact_root / case.name,
                    shape_name=case.name,
                )
            except BaseException as error:
                results.append(
                    {
                        "name": case.name,
                        "group": case.group,
                        "status": "mismatch",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )
            else:
                results.append(
                    {
                        "name": case.name,
                        "group": case.group,
                        "status": "consistent",
                    }
                )
        return results
    finally:
        if inserted:
            sys.path.remove(str(pytest_support))


def _package_versions() -> dict[str, str]:
    versions = {}
    for distribution in THIRD_PARTY_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions


def run_regression(
    *,
    root: Path = ROOT,
    stdlib: Path | None = None,
    purelib: Path | None = None,
    artifact_root: Path | None = None,
) -> dict:
    """Run breadth plus differential behavior regression and return JSON data."""
    stdlib = stdlib or Path(sysconfig.get_path("stdlib"))
    purelib = purelib or Path(sysconfig.get_path("purelib"))
    inputs = collect_inputs(root, stdlib, purelib)
    failures = []
    group_counts = {
        group: Counter()
        for group in ("stdlib", "project", "third_party")
    }

    for item in inputs:
        counts = group_counts[item.group]
        counts["input_files"] += 1
        try:
            recovered = _recover(item.path)
        except (SyntaxError, UnicodeError) as error:
            counts["malformed_or_unsupported_input"] += 1
            failures.append(
                {
                    "group": item.group,
                    "name": item.name,
                    "stage": "input",
                    "classification": "malformed_or_unsupported_input",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "opcode": None,
                }
            )
            continue
        except Decompyle3Error as error:
            classification = classify_failure(error)
            counts["fail_closed"] += 1
            failures.append(
                {
                    "group": item.group,
                    "name": item.name,
                    "stage": "decompile",
                    "classification": classification,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "opcode": (
                        OPCODE_RE.search(str(error)).group(1)
                        if OPCODE_RE.search(str(error))
                        else None
                    ),
                }
            )
            continue
        except BaseException as error:
            counts["unexpected_crash"] += 1
            failures.append(
                {
                    "group": item.group,
                    "name": item.name,
                    "stage": "decompile",
                    "classification": "unexpected_crash",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "opcode": None,
                }
            )
            continue

        counts["decompile_success"] += 1
        try:
            tree = ast.parse(recovered, filename=f"<recovered-{item.name}>")
            compile(
                tree,
                f"<recovered-{item.name}>",
                "exec",
                dont_inherit=True,
            )
        except (SyntaxError, SyntaxWarning) as error:
            counts["syntax_failure"] += 1
            failures.append(
                {
                    "group": item.group,
                    "name": item.name,
                    "stage": "syntax",
                    "classification": "generated_source_syntax_failure",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "opcode": None,
                }
            )
        else:
            counts["syntax_success"] += 1

    if artifact_root is None:
        temporary = tempfile.TemporaryDirectory(
            prefix="decompyle3-realworld311-"
        )
        artifact_root = Path(temporary.name)
    else:
        temporary = None
        artifact_root.mkdir(parents=True, exist_ok=True)
    try:
        behavior = _run_behavior_cases(
            behavior_cases(root, stdlib, purelib),
            artifact_root,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()

    classification_counts = Counter(
        failure["classification"] for failure in failures
    )
    samples = {}
    for classification in sorted(classification_counts):
        samples[classification] = [
            {
                "group": failure["group"],
                "name": failure["name"],
                "error_type": failure["error_type"],
                "opcode": failure["opcode"],
                "error": failure["error"],
            }
            for failure in failures
            if failure["classification"] == classification
        ][:3]

    groups = {}
    total = Counter()
    for group, counts in group_counts.items():
        group_result = {
            key: counts[key]
            for key in (
                "input_files",
                "decompile_success",
                "syntax_success",
                "syntax_failure",
                "fail_closed",
                "malformed_or_unsupported_input",
                "unexpected_crash",
            )
        }
        groups[group] = group_result
        total.update(group_result)

    behavior_counts = Counter(item["status"] for item in behavior)
    first_failure = failures[0] if failures else None
    return {
        "schema_version": 1,
        "phase": 8,
        "target": {
            "implementation": "CPython",
            "version": "3.11",
        },
        "environment": {
            "runtime": ".".join(map(str, sys.version_info[:3])),
            "platform": sys.platform,
            "package_versions": _package_versions(),
        },
        "selection": {
            "stdlib": {
                "top_level_modules": True,
                "packages": list(STDLIB_PACKAGES),
                "exclude_test_trees": True,
            },
            "project": "decompyle3/**/*.py",
            "third_party_packages": list(THIRD_PARTY_PACKAGES),
        },
        "input_digest": _input_digest(inputs),
        "totals": dict(total),
        "groups": groups,
        "behavior": {
            "input_cases": len(behavior),
            "consistent": behavior_counts["consistent"],
            "mismatch": behavior_counts["mismatch"],
            "missing_input": behavior_counts["missing_input"],
            "cases": behavior,
        },
        "first_failure": (
            {
                "group": first_failure["group"],
                "name": first_failure["name"],
                "opcode": first_failure["opcode"],
                "shape": first_failure["classification"],
                "error_type": first_failure["error_type"],
            }
            if first_failure is not None
            else None
        ),
        "failure_classifications": dict(
            sorted(classification_counts.items())
        ),
        "failure_samples": samples,
    }


def render_report(result: dict) -> str:
    totals = result["totals"]
    behavior = result["behavior"]
    first = result["first_failure"]
    lines = [
        "# CPython 3.11 标准库与真实项目回归报告",
        "",
        "> 本文件由 "
        "`test/bytecode_3.11/run_realworld_regression.py` 自动生成，",
        "> 记录阶段 8 的固定环境宽度审计，不等同于全量支持声明。",
        "",
        "## 环境",
        "",
        f"- Runtime：{result['environment']['runtime']}",
        f"- Platform：{result['environment']['platform']}",
        f"- 输入摘要：`{result['input_digest']}`",
        "",
        "第三方版本：",
        "",
    ]
    for name, version in result["environment"]["package_versions"].items():
        lines.append(f"- `{name}`：{version}")
    lines.extend(
        [
            "",
            "## 汇总",
            "",
            f"- 输入文件数：{totals['input_files']}",
            f"- 成功反编译数：{totals['decompile_success']}",
            f"- 语法验证成功数：{totals['syntax_success']}",
            f"- 语法失败数：{totals['syntax_failure']}",
            f"- fail-closed 数：{totals['fail_closed']}",
            "- malformed/unsupported input 数："
            f"{totals['malformed_or_unsupported_input']}",
            f"- 未包装崩溃数：{totals['unexpected_crash']}",
            f"- 行为一致数：{behavior['consistent']}",
            f"- 行为不一致数：{behavior['mismatch']}",
            f"- 行为输入缺失数：{behavior['missing_input']}",
            "- 首次失败 opcode："
            f"`{first['opcode'] if first and first['opcode'] else '—'}`",
            "- 首次失败 shape："
            f"`{first['shape'] if first else '—'}`",
            "",
            "## 分组",
            "",
            "| 分组 | 输入 | 反编译成功 | 语法成功 | 语法失败 | "
            "fail-closed | 输入不支持 | 未包装崩溃 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for group in ("stdlib", "project", "third_party"):
        counts = result["groups"][group]
        lines.append(
            f"| {group} | {counts['input_files']} | "
            f"{counts['decompile_success']} | "
            f"{counts['syntax_success']} | "
            f"{counts['syntax_failure']} | "
            f"{counts['fail_closed']} | "
            f"{counts['malformed_or_unsupported_input']} | "
            f"{counts['unexpected_crash']} |"
        )
    lines.extend(
        [
            "",
            "## Fail-closed 分类",
            "",
            "| Shape | 数量 |",
            "| --- | ---: |",
        ]
    )
    for shape, count in result["failure_classifications"].items():
        lines.append(f"| `{shape}` | {count} |")
    lines.extend(
        [
            "",
            "## 行为探针",
            "",
            "| 探针 | 分组 | 状态 |",
            "| --- | --- | --- |",
        ]
    )
    for case in behavior["cases"]:
        lines.append(
            f"| `{case['name']}` | {case['group']} | {case['status']} |"
        )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "- 本轮没有把失败 traceback 当作成功；所有反编译失败均需映射"
            "到 shape 矩阵或输入分类。",
            "- `unexpected_crash` 必须为 0；递归耗尽等内部异常必须转换为"
            "带版本和 code object 上下文的 fail-closed 错误。",
            "- 本报告反映固定运行时和固定第三方版本的结果；通过率不能解释为"
            "对整个标准库或任意第三方包的完整支持。",
            "",
        ]
    )
    return "\n".join(lines)


def _format_json(result: dict) -> str:
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def _check_file(path: Path, expected: str) -> bool:
    actual = path.read_text(encoding="utf-8") if path.is_file() else ""
    if actual == expected:
        return True
    print(f"结果已过期：{path}", file=sys.stderr)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)

    result = run_regression(artifact_root=arguments.artifact_root)
    json_text = _format_json(result)
    report_text = render_report(result)
    if arguments.check:
        okay = _check_file(arguments.output_json, json_text)
        okay &= _check_file(arguments.output_report, report_text)
        return 0 if okay else 1

    arguments.output_json.parent.mkdir(parents=True, exist_ok=True)
    arguments.output_report.parent.mkdir(parents=True, exist_ok=True)
    arguments.output_json.write_text(json_text, encoding="utf-8")
    arguments.output_report.write_text(report_text, encoding="utf-8")
    print(f"写入真实项目结果：{arguments.output_json}")
    print(f"写入真实项目报告：{arguments.output_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
