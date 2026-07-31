"""CPython 3.11 nested assignment and loop-target unpacking regressions."""

from __future__ import annotations

import ast
import io
import sys
import sysconfig
from pathlib import Path

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 unpacking tests require CPython 3.11",
)

SOURCE = ROOT / "test" / "simple_source" / "311" / "10_nested_unpacking.py"


def deparse_exec(source: str) -> str:
    output = io.StringIO()
    deparsed = code_deparse(
        compile(source + "\n", "<unpack-exec-311>", "exec"),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert deparsed.text == output.getvalue()
    return deparsed.text


def execute_exec(source: str, name: str):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def capture_call(function, value):
    try:
        result = function(value)
    except Exception as error:
        return "raise", type(error).__name__, error.args
    return "return", type(result).__name__, result


def assert_same_calls(original, recovered, values):
    for value in values:
        assert capture_call(recovered, value) == capture_call(original, value)


def recover_fixture():
    return deparse_exec(SOURCE.read_text(encoding="utf-8"))


def fixture_namespaces():
    source = SOURCE.read_text(encoding="utf-8")
    recovered = recover_fixture()
    tree = ast.parse(recovered)
    compile(tree, "<recovered-nested-unpacking-311>", "exec")
    return (
        tree,
        execute_exec(source, "original_nested_unpacking_311"),
        execute_exec(recovered, "recovered_nested_unpacking_311"),
    )


def test_nested_unpack_normalized_protocol_preserves_target_order():
    root = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
    )
    code_objects = {
        code.co_name: code for code in Scanner311.iter_code_objects(root)
    }

    def target_protocol(name):
        scanner = Scanner311()
        scanner.ingest(code_objects[name])
        return [
            instruction.kind
            for instruction in scanner.normalized_instructions
            if instruction.kind in {
                "INTERNAL_EXTENDED_ARG",
                "STORE_FAST",
                "UNPACK_EX",
                "UNPACK_SEQUENCE",
            }
        ]

    assert target_protocol("unpack_sequence")[:6] == [
        "UNPACK_SEQUENCE",
        "STORE_FAST",
        "UNPACK_SEQUENCE",
        "STORE_FAST",
        "STORE_FAST",
        "STORE_FAST",
    ]
    assert target_protocol("unpack_extended")[:6] == [
        "UNPACK_SEQUENCE",
        "STORE_FAST",
        "INTERNAL_EXTENDED_ARG",
        "UNPACK_EX",
        "STORE_FAST",
        "STORE_FAST",
    ]
    assert target_protocol("sequence_loop")[:4] == [
        "STORE_FAST",
        "UNPACK_SEQUENCE",
        "UNPACK_SEQUENCE",
        "STORE_FAST",
    ]
    assert target_protocol("extended_loop")[:5] == [
        "STORE_FAST",
        "UNPACK_SEQUENCE",
        "INTERNAL_EXTENDED_ARG",
        "UNPACK_EX",
        "STORE_FAST",
    ]


def test_nested_assignment_unpack_sequence_and_ex_preserve_behavior():
    tree, original_namespace, recovered_namespace = fixture_namespaces()

    assert_same_calls(
        original_namespace["unpack_sequence"],
        recovered_namespace["unpack_sequence"],
        (
            ("message", ("file.py", 3, 7, "line")),
            ("message", ("file.py", 3)),
            ("message", None),
            ("message", ("file.py", 3, 7, "line"), "extra"),
            ("message",),
        ),
    )
    assert_same_calls(
        original_namespace["unpack_extended"],
        recovered_namespace["unpack_extended"],
        (
            ("head", (1, 2), "tail"),
            ("head", (1, 2, 3, 4), "tail"),
            ("head", (1,), "tail"),
            ("head", None, "tail"),
            ("head", (1, 2)),
        ),
    )

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Tuple)
    ]
    assert len(assignments) == 2
    assert all(
        any(isinstance(element, ast.Tuple) for element in node.targets[0].elts)
        for node in assignments
    )
    assert any(
        isinstance(node, ast.Starred)
        for assignment in assignments
        for node in ast.walk(assignment.targets[0])
    )


def test_nested_for_targets_preserve_iteration_and_unpack_exceptions():
    tree, original_namespace, recovered_namespace = fixture_namespaces()

    assert_same_calls(
        original_namespace["sequence_loop"],
        recovered_namespace["sequence_loop"],
        (
            [],
            [((1, 2), 3)],
            [((1, 2), 3), ((4, 5), 6)],
            [((1,), 3)],
            [(None, 3)],
            [((1, 2),)],
        ),
    )
    assert_same_calls(
        original_namespace["extended_loop"],
        recovered_namespace["extended_loop"],
        (
            [],
            [((1, 2), 3)],
            [((1, 2, 3, 4), 5)],
            [((1,), 2)],
            [(None, 2)],
            [((1, 2),)],
        ),
    )

    loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)]
    assert len(loops) == 2
    assert all(
        isinstance(node.target, ast.Tuple)
        and isinstance(node.target.elts[0], ast.Tuple)
        for node in loops
    )
    assert any(
        isinstance(node, ast.Starred)
        for loop in loops
        for node in ast.walk(loop.target)
    )


def test_nested_comprehension_target_uses_the_same_recursive_shape():
    tree, original_namespace, recovered_namespace = fixture_namespaces()
    original = original_namespace["collect"]
    rebuilt = recovered_namespace["collect"]

    assert_same_calls(
        original,
        rebuilt,
        (
            [],
            [((1, 2), 3)],
            [((1, 2), 3), ((4, 5), 6)],
            [((1,), 3)],
            [(None, 3)],
        ),
    )

    comprehension = next(
        node for node in ast.walk(tree) if isinstance(node, ast.ListComp)
    )
    target = comprehension.generators[0].target
    assert isinstance(target, ast.Tuple)
    assert isinstance(target.elts[0], ast.Tuple)


@pytest.mark.parametrize(
    ("source", "code_name", "statement_type"),
    (
        (
            Path(sysconfig.get_path("stdlib")) / "code.py",
            "showsyntaxerror",
            ast.Assign,
        ),
        (
            Path(sysconfig.get_path("stdlib"))
            / "multiprocessing"
            / "util.py",
            "_run_after_forkers",
            ast.For,
        ),
    ),
)
def test_stage1_realworld_code_objects_recover_and_recompile(
    source,
    code_name,
    statement_type,
):
    root = compile(
        source.read_bytes(),
        str(source),
        "exec",
        dont_inherit=True,
    )
    code = next(
        code
        for code in Scanner311.iter_code_objects(root)
        if code.co_name == code_name
    )
    output = io.StringIO()
    deparsed = code_deparse(
        code,
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert deparsed.text == output.getvalue()
    tree = ast.parse(deparsed.text, filename=f"<recovered-{code_name}>")
    compile(tree, f"<recovered-{code_name}>", "exec", dont_inherit=True)

    targets = [
        (
            statement.targets[0]
            if isinstance(statement, ast.Assign)
            else statement.target
        )
        for statement in ast.walk(tree)
        if isinstance(statement, statement_type)
    ]
    assert any(
        isinstance(target, ast.Tuple)
        and any(isinstance(element, ast.Tuple) for element in target.elts)
        for target in targets
    )
