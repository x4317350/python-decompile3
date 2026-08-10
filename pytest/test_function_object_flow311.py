"""CPython 3.11 delayed function-object flow regression coverage."""

from __future__ import annotations

import ast
import inspect
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 function-object tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "14_function_object_flow.py"
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
    tree = ast.parse(recovered, filename="<function-object-flow-311>")
    compile(tree, "<function-object-flow-311>", "exec")
    original = {"__name__": "original_function_object_flow_311"}
    rebuilt = {"__name__": "rebuilt_function_object_flow_311"}
    exec(
        compile(source, str(SOURCE), "exec", dont_inherit=True),
        original,
    )
    exec(
        compile(
            recovered,
            "<recovered-function-object-flow-311>",
            "exec",
            dont_inherit=True,
        ),
        rebuilt,
    )
    return tree, original, rebuilt


def test_scanner_and_ast_cover_delayed_function_consumers():
    root = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
        dont_inherit=True,
    )
    kinds = set()
    for code in Scanner311.iter_code_objects(root):
        scanner = Scanner311()
        scanner.ingest(code)
        kinds.update(
            instruction.kind
            for instruction in scanner.normalized_instructions
        )

    assert {
        "BUILD_CONST_KEY_MAP",
        "BUILD_LIST",
        "BUILD_MAP",
        "BUILD_TUPLE",
        "CALL",
        "MAKE_FUNCTION",
        "STORE_ATTR",
        "STORE_SUBSCR",
    } <= kinds

    tree, _, _ = namespaces()
    assert sum(isinstance(node, ast.Lambda) for node in ast.walk(tree)) >= 7
    decorated = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "decorated"
    )
    assert len(decorated.decorator_list) == 2
    assert any(
        isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and isinstance(node.value, ast.Lambda)
        for node in ast.walk(tree)
    )
    assert any(
        isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(node.value, ast.Lambda)
        for node in ast.walk(tree)
    )


def test_decorator_order_signature_and_descriptors_preserve_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        function = namespace["decorated"]
        assert function(4, scale=5) == 20
        assert str(inspect.signature(function.original.original)) == (
            "(value: int = 2, *, scale: int = 3) -> int"
        )
        assert namespace["EVENTS"] == [
            ("decorate", "inner"),
            ("decorate", "outer"),
            ("call", "outer"),
            ("call", "inner"),
        ]

        descriptor = namespace["DescriptorDemo"](6)
        assert descriptor.add(2, 4) == 6
        assert descriptor.owner_name() == "DescriptorDemo"
        assert descriptor.doubled == 12


def test_lambda_targets_defaults_collections_and_closures_preserve_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        assert namespace["default_callback"]() == 3
        holder, mapping, sequence, assigned = namespace["build_lambdas"](7)
        assert holder.transform() == 8
        assert holder.transform(5) == 12
        assert mapping["scale"](3) == 21
        assert mapping["identity"]("value") == "value"
        assert sequence[0]() == 7
        assert sequence[1][0](2) == 5
        assert assigned["offset"](4) == 11


def test_lambda_used_by_short_circuit_store_deref_preserves_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        then, events = namespace["make_lazy_resolver"](10)
        assert events == []
        assert then(1) == 11
        assert then(2) == 12
        assert events == [1, 2]
