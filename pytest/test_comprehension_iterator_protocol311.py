"""CPython 3.11 stage 6 comprehension and iterator regressions."""

from __future__ import annotations

import ast
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 comprehension tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "15_comprehension_iterator_protocol.py"
)


def deparse(source: str) -> str:
    output = io.StringIO()
    result = code_deparse(
        compile(source, str(SOURCE), "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert result.text == output.getvalue()
    return result.text


def namespaces():
    source = SOURCE.read_text(encoding="utf-8")
    recovered = deparse(source)
    tree = ast.parse(recovered, filename="<comprehension-iterator-311>")
    compile(tree, "<comprehension-iterator-311>", "exec")
    original = {"__name__": "original_comprehension_iterator_311"}
    rebuilt = {"__name__": "rebuilt_comprehension_iterator_311"}
    exec(compile(source, str(SOURCE), "exec", dont_inherit=True), original)
    exec(
        compile(
            recovered,
            "<recovered-comprehension-iterator-311>",
            "exec",
            dont_inherit=True,
        ),
        rebuilt,
    )
    return tree, original, rebuilt


def generator_lambda_snapshot(namespace):
    generator = namespace["generator_lambda"](7)
    first = next(generator)
    with pytest.raises(StopIteration) as stopped:
        generator.send("finished")
    return first, stopped.value.value


def test_scanner_and_ast_cover_stage6_protocol_shapes():
    root = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
        dont_inherit=True,
    )
    kinds = set()
    tokens = []
    for code in Scanner311.iter_code_objects(root):
        scanner = Scanner311()
        code_tokens, _ = scanner.ingest(code)
        tokens.extend(code_tokens)
        kinds.update(token.kind for token in code_tokens)

    assert {
        "FOR_ITER",
        "INTERNAL_EXTENDED_ARG",
        "JUMP_BACKWARD",
        "LIST_APPEND",
        "MAP_ADD",
        "RETURN_GENERATOR",
        "SET_ADD",
        "YIELD_VALUE",
    } <= kinds
    assert any(
        token.kind in ("LIST_APPEND", "MAP_ADD", "SET_ADD")
        and token.attr == 1
        for token in tokens
    )

    tree, _, _ = namespaces()
    assert any(isinstance(node, ast.IfExp) for node in ast.walk(tree))
    assert any(
        isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Lambda)
        and isinstance(node.body, ast.Yield)
        for node in ast.walk(tree)
    )


def test_incremental_literals_extended_loop_and_terminal_loop_preserve_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        sequence, members, mapping = namespace["incremental_literals"](
            [1, 2],
            {"seed": 0},
            3,
        )
        assert sequence == [1, 2, 3]
        assert members == {1, 2, 3}
        assert mapping == {"seed": 0, "tail": 3}
        assert namespace["extended_for"](
            ["k0", "k69", "missing"]
        ) == [0, 69, "missing"]
        assert namespace["first_or_error"]([5, 6]) == 5
        with pytest.raises(LookupError, match="empty"):
            namespace["first_or_error"]([])


def test_iterator_cleanup_break_preserves_single_iteration_and_outer_loop():
    tree, original, rebuilt = namespaces()
    assert sum(isinstance(node, ast.Break) for node in ast.walk(tree)) >= 2

    for namespace in (original, rebuilt):
        assert namespace["first_or_default"]([], "missing") == "missing"
        assert namespace["first_or_default"]([1, 2, 3], "missing") == 1
        events = []
        namespace["nested_first"]([[1, 2], [], [3, 4]], events)
        assert events == [1, "group", "group", 3, "group"]


def test_conditional_outputs_filters_and_generators_preserve_behavior():
    _, original, rebuilt = namespaces()
    values = [-2, -1, 0, 1, 2, 3]
    records = [
        {"name": "compare", "hash": None, "compare": True},
        {"name": "skip", "hash": None, "compare": False},
        {"name": "hash", "hash": True, "compare": False},
    ]

    for namespace in (original, rebuilt):
        result = namespace["comprehension_shapes"](
            values,
            records,
            [b"a", b"bc"],
        )
        assert result[0] == {
            -2: "even",
            -1: "odd",
            0: "even",
            1: "odd",
            2: "even",
            3: "odd",
        }
        assert result[1] == [-2, -1, 0, 2]
        assert [item["name"] for item in result[2]] == [
            "compare",
            "hash",
        ]
        assert result[3] is True
        assert namespace["comprehension_shapes"](
            values,
            records,
            [b"", b"abc"],
        )[3] is False
        assert list(namespace["make_prefixed"](1, [2, 3])()) == [
            1,
            2,
            3,
        ]
        assert generator_lambda_snapshot(namespace) == (7, "finished")


def test_extended_arg_comprehension_latch_preserves_behavior():
    root = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
        dont_inherit=True,
    )
    comprehension = max(
        (
            code
            for code in Scanner311.iter_code_objects(root)
            if code.co_name == "<listcomp>"
        ),
        key=lambda code: len(code.co_code),
    )
    scanner = Scanner311()
    tokens, _ = scanner.ingest(comprehension)
    assert any(token.kind == "INTERNAL_EXTENDED_ARG" for token in tokens)

    _, original, rebuilt = namespaces()
    values = [-1, 0, 37, 179, 180]
    assert original["extended_comprehension_filter"](values) == [0, 37, 179]
    assert rebuilt["extended_comprehension_filter"](values) == [0, 37, 179]
