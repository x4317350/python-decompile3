"""Shared helpers for CPython 3.11 corpus and behavior tests."""

from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from xdis import load_module


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "test" / "simple_source" / "311"


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
