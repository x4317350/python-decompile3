"""CPython 3.11 stage 8 recursive structure-recovery regressions."""

from __future__ import annotations

import ast
import io
import sys
import types

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.controlflow.structures import StructuredDecompiler311
from decompyle3.parsers.p311.base import UnsupportedPython311ControlFlow
from decompyle3.scanner import get_scanner
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 recursive-structure tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "17_recursive_structure.py"
)


def recover(source: str):
    output = io.StringIO()
    result = code_deparse(
        compile(source, "<recursive-structure-311>", "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert result.text == output.getvalue()
    tree = ast.parse(result.text, filename="<recovered-recursive-structure-311>")
    compile(
        tree,
        "<recovered-recursive-structure-311>",
        "exec",
        dont_inherit=True,
    )
    return result.text, tree


def namespaces():
    source = SOURCE.read_text(encoding="utf-8")
    recovered, tree = recover(source)
    original = {"__name__": "original_recursive_structure_311"}
    rebuilt = {"__name__": "rebuilt_recursive_structure_311"}
    exec(compile(source, str(SOURCE), "exec", dont_inherit=True), original)
    exec(
        compile(
            recovered,
            "<recovered-recursive-structure-311>",
            "exec",
            dont_inherit=True,
        ),
        rebuilt,
    )
    return tree, original, rebuilt


def test_compound_while_latch_excludes_the_repeated_condition():
    tree, _, _ = namespaces()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    scan_loops = [
        node
        for node in ast.walk(functions["scan_until"])
        if isinstance(node, ast.While)
    ]
    assert len(scan_loops) == 1
    assert isinstance(scan_loops[0].test, ast.BoolOp)
    assert any(
        isinstance(node, (ast.Assign, ast.AugAssign))
        for node in ast.walk(scan_loops[0])
    )


def test_nested_and_chained_compound_loops_preserve_behavior():
    _, original, rebuilt = namespaces()
    calls = (
        ("scan_until", ("abc]tail", 0, "]")),
        ("scan_until", ("plain", 1, "]")),
        ("collect_prefix", ([2, None, 4, "stop", 6], "stop")),
        ("nested_compound", (["ab]", "xyz", "]"], "]")),
        ("chained_guard", ([2, 4, 6, 3, 8],)),
        ("chained_guard", ([2, -2, 4],)),
    )
    for name, arguments in calls:
        assert rebuilt[name](*arguments) == original[name](*arguments)


def test_deep_linear_condition_uses_bounded_structure_work():
    names = [f"value_{index}" for index in range(600)]
    source = (
        "def deep_guard("
        + ", ".join(names)
        + "):\n"
        + "    if "
        + " and ".join(names)
        + ":\n"
        + "        return 'all'\n"
        + "    return 'short'\n"
    )
    recovered, _ = recover(source)
    namespace = {}
    exec(
        compile(
            recovered,
            "<recovered-deep-recursive-structure-311>",
            "exec",
            dont_inherit=True,
        ),
        namespace,
    )
    assert namespace["deep_guard"](*([True] * 600)) == "all"
    assert namespace["deep_guard"](*([True] * 599 + [False])) == "short"


def test_repeated_structure_region_fails_closed_before_python_recursion():
    code = compile("value = 1\n", "<cyclic-region-311>", "exec")
    scanner = get_scanner((3, 11), PythonImplementation.CPython)
    tokens, _ = scanner.ingest(code)
    decompiler = StructuredDecompiler311(code, tokens)

    def repeat(owner, start, end, loop):
        owner._capture_region(start, end, loop)

    decompiler._parse_region = types.MethodType(repeat, decompiler)
    with pytest.raises(
        UnsupportedPython311ControlFlow,
        match="Structured region cycle detected",
    ):
        decompiler._capture_region(0, len(tokens), None)
