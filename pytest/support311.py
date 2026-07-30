"""Shared helpers for CPython 3.11 corpus and behavior tests."""

from __future__ import annotations

import dis
import io
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from importlib._bootstrap_external import _code_to_hash_pyc
from importlib.util import source_hash
from pathlib import Path
from typing import Iterable

from xdis import load_module
from xdis.version_info import PythonImplementation

from decompyle3.controlflow import (
    analyze_control_flow,
    build_cfg,
    decode_exception_table,
)
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "test" / "simple_source" / "311"
COMPILE_MODE_RE = re.compile(r"# compile-mode: (exec|single|eval)")
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]+")
ISO_TIME_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-2]\d:[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
EPOCH_TIME_RE = re.compile(r"\b1[5-9]\d{8}(?:\.\d+)?\b")
DURATION_RE = re.compile(r"\b\d+\.\d{3,}(?=s\b)")
BEHAVIOR_MARKER = "__DECOMPYLE3_BEHAVIOR__"

BEHAVIOR_DRIVER = textwrap.dedent(
    f"""
    import asyncio
    import json
    import sys

    _MARKER = {BEHAVIOR_MARKER!r}

    def _type_name(value):
        value_type = type(value)
        if value_type.__module__ == "builtins":
            return value_type.__qualname__
        return value_type.__module__ + "." + value_type.__qualname__

    def _normalize(value):
        if value is None or isinstance(value, (bool, int, str)):
            return value
        if isinstance(value, float):
            if value != value:
                return {{"$float": "nan"}}
            if value == float("inf"):
                return {{"$float": "inf"}}
            if value == float("-inf"):
                return {{"$float": "-inf"}}
            return value
        if isinstance(value, bytes):
            return {{"$bytes": value.hex()}}
        if isinstance(value, tuple):
            return {{"$tuple": [_normalize(item) for item in value]}}
        if isinstance(value, list):
            return [_normalize(item) for item in value]
        if isinstance(value, (set, frozenset)):
            items = [_normalize(item) for item in value]
            items.sort(
                key=lambda item: json.dumps(
                    item,
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
            return {{"$set": items}}
        if isinstance(value, dict):
            items = [
                [_normalize(key), _normalize(item)]
                for key, item in value.items()
            ]
            items.sort(
                key=lambda item: json.dumps(
                    item[0],
                    sort_keys=True,
                    ensure_ascii=False,
                )
            )
            return {{"$dict": items}}
        state = getattr(value, "__dict__", None)
        if isinstance(state, dict):
            return {{
                "$object": _type_name(value),
                "state": _normalize(state),
            }}
        return {{"$repr": repr(value)}}

    def _record(label, operation):
        try:
            value = operation()
        except BaseException as error:
            payload = {{
                "label": label,
                "outcome": "exception",
                "type": _type_name(error),
                "args": _normalize(error.args),
            }}
        else:
            payload = {{
                "label": label,
                "outcome": "return",
                "type": _type_name(value),
                "value": _normalize(value),
            }}
        print(
            _MARKER
            + json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    def _record_async(label, operation):
        _record(label, lambda: asyncio.run(operation()))

    source_path, compile_mode, probe_path = sys.argv[1:4]
    namespace = {{
        "__file__": "fixture.py",
        "__name__": "__behavior_fixture__",
        "__package__": None,
        "_normalize": _normalize,
        "_record": _record,
        "_record_async": _record_async,
        "asyncio": asyncio,
    }}
    source = open(source_path, encoding="utf-8").read()
    code = compile(
        source,
        "fixture.py",
        compile_mode,
        dont_inherit=True,
    )
    if compile_mode == "eval":
        _record("eval", lambda: eval(code, namespace))
    else:
        exec(code, namespace)
    probe = open(probe_path, encoding="utf-8").read()
    if probe:
        exec(compile(probe, "probe.py", "exec"), namespace)
    """
).strip()


@dataclass(frozen=True)
class BehaviorExecution:
    stdout: str
    stderr: str
    exitcode: int
    timed_out: bool = False


@dataclass(frozen=True)
class BehaviorComparison:
    original: BehaviorExecution
    recovered: BehaviorExecution
    artifact_dir: Path


class BehaviorMismatchError(AssertionError):
    """Original and recovered CPython 3.11 behavior differ."""


def corpus_sources() -> Iterable[Path]:
    return sorted(SOURCE_DIR.glob("*.py"))


def compile_source(source: Path, destination: Path):
    """Compile one corpus source and return the tuple produced by xdis."""
    py_compile.compile(
        str(source),
        cfile=str(destination),
        dfile=source.relative_to(ROOT).as_posix(),
        doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
    )
    return load_module(str(destination))


def run_source(source: Path) -> subprocess.CompletedProcess:
    """Run one source in a subprocess for future behavior comparisons."""
    return subprocess.run(
        [sys.executable, str(source)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def assert_same_behavior(original: Path, recovered: Path) -> None:
    """Compare exit status and observable output for two source files."""
    original_result = run_source(original)
    recovered_result = run_source(recovered)
    assert recovered_result.returncode == original_result.returncode
    assert recovered_result.stdout == original_result.stdout
    assert recovered_result.stderr == original_result.stderr


def behavior_compile_mode(source: Path) -> str:
    """Return an optional fixture compile-mode declaration."""
    first_line = source.read_text(encoding="utf-8").splitlines()[0]
    matched = COMPILE_MODE_RE.fullmatch(first_line)
    return matched.group(1) if matched else "exec"


def compile_behavior_pyc(source: Path, destination: Path):
    """Compile one fixture and persist a checked-hash CPython 3.11 pyc."""
    source_bytes = source.read_bytes()
    mode = behavior_compile_mode(source)
    code = compile(
        source_bytes,
        "fixture.py",
        mode,
        dont_inherit=True,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        _code_to_hash_pyc(
            code,
            source_hash(source_bytes),
            checked=True,
        )
    )
    version, _, _, loaded, implementation, *_ = load_module(
        str(destination)
    )
    assert version == (3, 11)
    assert implementation is PythonImplementation.CPython
    return loaded, mode


def recover_behavior_source(code, compile_mode: str) -> str:
    """Recover source from a code object loaded through its pyc."""
    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        compile_mode=compile_mode,
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def normalize_behavior_text(text: str, *paths: Path) -> str:
    """Remove nondeterministic paths, addresses, and wall-clock values."""
    normalized = text.replace("\r\n", "\n")
    for path in (ROOT, *paths):
        normalized = normalized.replace(str(path), "<PATH>")
    normalized = ADDRESS_RE.sub("0x<ADDR>", normalized)
    normalized = ISO_TIME_RE.sub("<TIME>", normalized)
    normalized = EPOCH_TIME_RE.sub("<TIME>", normalized)
    normalized = DURATION_RE.sub("<TIME>", normalized)
    return normalized


def _run_behavior_source(
    source: Path,
    compile_mode: str,
    probe: Path,
    driver: Path,
    *,
    timeout: float,
) -> BehaviorExecution:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
        }
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(driver),
                str(source),
                compile_mode,
                str(probe),
            ],
            cwd=driver.parent,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return BehaviorExecution(
            stdout=stdout,
            stderr=stderr,
            exitcode=-9,
            timed_out=True,
        )
    return BehaviorExecution(
        stdout=completed.stdout,
        stderr=completed.stderr,
        exitcode=completed.returncode,
    )


def _locate_behavior_opcode(code, opcode_name):
    if opcode_name is None:
        return getattr(code, "co_name", "<module>"), None
    for nested in Scanner311.iter_code_objects(code):
        scanner = Scanner311()
        scanner.ingest_raw(nested)
        for instruction in scanner.insts:
            if instruction.opname == opcode_name:
                return nested.co_name, instruction.offset
    return getattr(code, "co_name", "<module>"), None


def _stable_disassembly(code) -> str:
    output = io.StringIO()
    dis.dis(
        code,
        file=output,
        depth=None,
        show_caches=True,
        adaptive=False,
    )
    return ADDRESS_RE.sub("0x<ADDR>", output.getvalue())


def _stable_tokens(code) -> str:
    lines = []
    for nested in Scanner311.iter_code_objects(code):
        scanner = Scanner311()
        scanner.ingest(nested)
        lines.append(f"## code: {nested.co_qualname}")
        for instruction in scanner.normalized_instructions:
            lines.append(
                f"{instruction.offset:04d} | "
                f"{instruction.logical_index:04d} | "
                f"{instruction.kind} | "
                f"target={instruction.target!r} | "
                f"value={instruction.argval!r}"
            )
        lines.append("")
    return "\n".join(lines)


def _stable_cfg(code) -> str:
    lines = []
    for nested in Scanner311.iter_code_objects(code):
        scanner = Scanner311()
        scanner.ingest(nested)
        graph = build_cfg(
            scanner.normalized_instructions,
            decode_exception_table(nested),
        )
        analysis = analyze_control_flow(graph)
        lines.extend(
            [
                f"## code: {nested.co_qualname}",
                graph.format(),
                f"back_edges={analysis.back_edges!r}",
                "",
            ]
        )
    return "\n".join(lines)


def _write_execution(
    directory: Path,
    prefix: str,
    execution: BehaviorExecution,
) -> None:
    (directory / f"{prefix}.stdout").write_text(
        execution.stdout,
        encoding="utf-8",
    )
    (directory / f"{prefix}.stderr").write_text(
        execution.stderr,
        encoding="utf-8",
    )
    (directory / f"{prefix}.exitcode").write_text(
        f"{execution.exitcode}\n",
        encoding="utf-8",
    )


def _retain_behavior_failure(
    directory: Path,
    code,
    source: Path,
    opcode_name,
    shape_name,
    exception_name,
    original: BehaviorExecution,
    recovered: BehaviorExecution,
) -> None:
    (directory / "fixture.dis").write_text(
        _stable_disassembly(code),
        encoding="utf-8",
    )
    (directory / "fixture.tokens").write_text(
        _stable_tokens(code),
        encoding="utf-8",
    )
    (directory / "fixture.cfg").write_text(
        _stable_cfg(code),
        encoding="utf-8",
    )
    _write_execution(directory, "original", original)
    _write_execution(directory, "recovered", recovered)
    code_name, offset = _locate_behavior_opcode(code, opcode_name)
    failure = {
        "opcode": opcode_name,
        "shape": shape_name,
        "code_name": code_name,
        "offset": offset,
        "exception": exception_name,
        "runtime": ".".join(map(str, sys.version_info[:3])),
        "target": "3.11",
        "fixture": source.relative_to(ROOT).as_posix()
        if source.is_relative_to(ROOT)
        else str(source),
    }
    (directory / "failure.json").write_text(
        json.dumps(failure, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def compare_behavior311(
    source: Path,
    probe_source: str,
    artifact_dir: Path,
    *,
    opcode_name=None,
    shape_name=None,
    timeout: float = 10,
    recovered_override=None,
) -> BehaviorComparison:
    """Compile, decompile, execute, and compare one CPython 3.11 fixture."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    fixture = artifact_dir / "fixture.py"
    bytecode = artifact_dir / "fixture.pyc"
    recovered = artifact_dir / "recovered.py"
    probe = artifact_dir / "probe.py"
    driver = artifact_dir / "driver.py"
    shutil.copyfile(source, fixture)
    code, compile_mode = compile_behavior_pyc(fixture, bytecode)
    recovered_source = recover_behavior_source(code, compile_mode)
    if recovered_override is not None:
        recovered_source = recovered_override
    recovered.write_text(recovered_source, encoding="utf-8")
    probe.write_text(probe_source, encoding="utf-8")
    driver.write_text(BEHAVIOR_DRIVER + "\n", encoding="utf-8")

    original_execution = _run_behavior_source(
        fixture,
        compile_mode,
        probe,
        driver,
        timeout=timeout,
    )
    recovered_execution = _run_behavior_source(
        recovered,
        compile_mode,
        probe,
        driver,
        timeout=timeout,
    )
    comparison = BehaviorComparison(
        original=original_execution,
        recovered=recovered_execution,
        artifact_dir=artifact_dir,
    )

    original_normalized = (
        original_execution.exitcode,
        normalize_behavior_text(
            original_execution.stdout,
            artifact_dir,
        ),
        normalize_behavior_text(
            original_execution.stderr,
            artifact_dir,
        ),
    )
    recovered_normalized = (
        recovered_execution.exitcode,
        normalize_behavior_text(
            recovered_execution.stdout,
            artifact_dir,
        ),
        normalize_behavior_text(
            recovered_execution.stderr,
            artifact_dir,
        ),
    )
    timed_out = (
        original_execution.timed_out or recovered_execution.timed_out
    )
    if timed_out or original_normalized != recovered_normalized:
        exception_name = (
            "BehaviorTimeout" if timed_out else "BehaviorMismatch"
        )
        _retain_behavior_failure(
            artifact_dir,
            code,
            source,
            opcode_name,
            shape_name,
            exception_name,
            original_execution,
            recovered_execution,
        )
        raise BehaviorMismatchError(
            f"{exception_name}: behavior artifacts retained in "
            f"{artifact_dir}"
        )
    return comparison
