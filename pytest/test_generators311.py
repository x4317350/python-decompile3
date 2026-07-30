"""Phase 5 acceptance tests for CPython 3.11 comprehensions and suspension."""

from __future__ import annotations

import ast
import asyncio
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT, compile_source


COMPREHENSION_SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "03_comprehensions.py"
)
GENERATOR_SOURCE = (
    ROOT / "test" / "simple_source" / "311" / "04_generators_async.py"
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 suspension tests require CPython 3.11",
)


def recover_source(source, tmp_path):
    bytecode = tmp_path / f"{source.stem}.pyc"
    version, _, _, code, implementation, *_ = compile_source(source, bytecode)
    assert version == (3, 11)
    assert implementation is PythonImplementation.CPython

    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def execute(source, name):
    namespace = {"__name__": name}
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


class AsyncValues:
    def __init__(self, values):
        self.values = iter(values)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.values)
        except StopIteration:
            raise StopAsyncIteration


def drive_echo(function):
    generator = function([1, 2])
    result = [next(generator), generator.send(10), next(generator)]
    with pytest.raises(StopIteration) as stopped:
        generator.send(None)
    result.append(stopped.value.value)
    return result


async def async_behavior(namespace):
    async def ready(value):
        return value

    async def collect(iterator):
        return [value async for value in iterator]

    return (
        await namespace["consume"](AsyncValues([0, 1, 2, 3])),
        await namespace["await_value"](ready(42)),
        await namespace["async_filtered"](AsyncValues([-1, 1, 2, 3])),
        await collect(namespace["async_numbers"](4)),
        await collect(
            namespace["async_transform"]([ready(5), ready(8)])
        ),
    )


def test_comprehension_and_suspension_protocol_opcodes_are_present():
    root = compile(
        GENERATOR_SOURCE.read_text(encoding="utf-8"),
        str(GENERATOR_SOURCE),
        "exec",
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
        "ASYNC_GEN_WRAP",
        "END_ASYNC_FOR",
        "GET_AITER",
        "GET_ANEXT",
        "GET_AWAITABLE",
        "GET_YIELD_FROM_ITER",
        "RETURN_GENERATOR",
        "SEND",
        "YIELD_VALUE",
    } <= kinds


@pytest.mark.parametrize(
    "source",
    [COMPREHENSION_SOURCE, GENERATOR_SOURCE],
)
def test_phase5_pyc_deparses_reparses_and_recompiles(source, tmp_path):
    recovered = recover_source(source, tmp_path)
    tree = ast.parse(recovered)
    compile(tree, f"<recovered-{source.stem}>", "exec")


def test_recovered_comprehensions_have_all_ast_forms(tmp_path):
    recovered = recover_source(COMPREHENSION_SOURCE, tmp_path)
    tree = ast.parse(recovered)
    nodes = list(ast.walk(tree))

    assert any(isinstance(node, ast.ListComp) for node in nodes)
    assert any(isinstance(node, ast.SetComp) for node in nodes)
    assert any(isinstance(node, ast.DictComp) for node in nodes)
    assert any(isinstance(node, ast.GeneratorExp) for node in nodes)
    assert any(isinstance(node, ast.Lambda) for node in nodes)
    assert any(
        isinstance(node, ast.ListComp) and len(node.generators) > 1
        for node in nodes
    )
    assert any(
        isinstance(node, ast.comprehension) and len(node.ifs) > 1
        for node in nodes
    )


def test_recovered_generators_and_coroutines_have_all_ast_forms(tmp_path):
    recovered = recover_source(GENERATOR_SOURCE, tmp_path)
    tree = ast.parse(recovered)
    nodes = list(ast.walk(tree))

    assert any(isinstance(node, ast.Yield) for node in nodes)
    assert any(isinstance(node, ast.YieldFrom) for node in nodes)
    assert any(isinstance(node, ast.Await) for node in nodes)
    assert any(isinstance(node, ast.AsyncFunctionDef) for node in nodes)
    assert any(
        isinstance(node, ast.comprehension) and node.is_async
        for node in nodes
    )


def test_recovered_comprehensions_preserve_behavior_and_scope(tmp_path):
    original = execute(
        COMPREHENSION_SOURCE.read_text(encoding="utf-8"),
        "phase5_comprehension_original",
    )
    recovered = execute(
        recover_source(COMPREHENSION_SOURCE, tmp_path),
        "phase5_comprehension_recovered",
    )
    rows = [[], [1, 2, 3], [-1, 4, 5]]
    values = [-2, -1, 0, 1, 2, 3, 5]

    assert recovered["comprehensions"](rows) == original["comprehensions"](
        rows
    )
    assert recovered["nested_comprehension"](6) == original[
        "nested_comprehension"
    ](6)
    assert recovered["filtered_lambda"](values) == original[
        "filtered_lambda"
    ](values)
    assert recovered["comprehension_scope"](values) == original[
        "comprehension_scope"
    ](values)
    assert recovered["comprehension_scope"](values)[0] == "outer"
    assert "value" not in recovered


def test_recovered_generators_and_coroutines_preserve_behavior(tmp_path):
    original = execute(
        GENERATOR_SOURCE.read_text(encoding="utf-8"),
        "phase5_generator_original",
    )
    recovered = execute(
        recover_source(GENERATOR_SOURCE, tmp_path),
        "phase5_generator_recovered",
    )

    assert list(recovered["numbers"](5)) == list(original["numbers"](5))
    assert list(recovered["delegating"]([2, 4, 6])) == list(
        original["delegating"]([2, 4, 6])
    )
    assert drive_echo(recovered["echo"]) == drive_echo(original["echo"])
    assert asyncio.run(async_behavior(recovered)) == asyncio.run(
        async_behavior(original)
    )
