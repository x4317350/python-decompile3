"""CPython 3.11 stage 7 with-statement control-transfer regressions."""

from __future__ import annotations

import ast
import asyncio
import io
import sys

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 with-control tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "16_with_control_transfer.py"
)


def namespaces():
    source = SOURCE.read_text(encoding="utf-8")
    output = io.StringIO()
    result = code_deparse(
        compile(source, str(SOURCE), "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert result.text == output.getvalue()
    tree = ast.parse(result.text, filename="<with-control-311>")
    compile(tree, "<with-control-311>", "exec", dont_inherit=True)
    original = {"__name__": "original_with_control_311"}
    rebuilt = {"__name__": "rebuilt_with_control_311"}
    exec(compile(source, str(SOURCE), "exec", dont_inherit=True), original)
    exec(
        compile(
            result.text,
            "<recovered-with-control-311>",
            "exec",
            dont_inherit=True,
        ),
        rebuilt,
    )
    return tree, original, rebuilt


def context(namespace, events, name, value=0, suppress=False):
    return namespace["TraceContext"](
        events,
        name,
        value=value,
        suppress=suppress,
    )


def test_stage7_ast_keeps_control_transfers_inside_with_suites():
    tree, _, _ = namespaces()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    returning = next(
        node
        for node in ast.walk(functions["multi_statement_return"])
        if isinstance(node, ast.With)
    )
    assert sum(
        isinstance(node, ast.Return) for node in ast.walk(returning)
    ) == 2

    loop_with = next(
        node
        for node in ast.walk(functions["loop_transfers"])
        if isinstance(node, ast.With)
    )
    assert any(isinstance(node, ast.Break) for node in ast.walk(loop_with))
    assert any(isinstance(node, ast.Continue) for node in ast.walk(loop_with))

    multiple = next(
        node
        for node in ast.walk(functions["multiple_contexts"])
        if isinstance(node, ast.With)
    )
    assert len(multiple.items) == 2

    nested = next(
        node
        for node in ast.walk(functions["nested_contexts"])
        if isinstance(node, ast.With)
    )
    assert any(isinstance(node, ast.With) for node in nested.body)
    assert any(
        isinstance(node, ast.AsyncWith)
        for node in ast.walk(functions["async_return"])
    )


def test_sync_with_control_transfers_preserve_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        events = []
        assert namespace["multi_statement_return"](
            context(namespace, events, "return", value=3),
            4,
        ) == 14
        assert events == [
            ("enter", "return"),
            ("mark", "return", "before"),
            ("mark", "return", "return"),
            ("exit", "return", None),
        ]

        loop_events = []

        def factory(value):
            return context(namespace, loop_events, str(value))

        assert namespace["loop_transfers"](
            factory,
            [1, -1, 2, 0, 3],
        ) == [1, 2]
        assert [event for event in loop_events if event[0] == "exit"] == [
            ("exit", "1", None),
            ("exit", "-1", None),
            ("exit", "2", None),
            ("exit", "0", None),
        ]


def test_multiple_nested_suppressed_and_try_with_preserve_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        events = []
        left = context(namespace, events, "left", value=2)
        right = context(namespace, events, "right", value=5)
        assert namespace["multiple_contexts"](left, right) == 7
        assert events[-2:] == [
            ("exit", "right", None),
            ("exit", "left", None),
        ]

        events = []
        outer = context(namespace, events, "outer", value=3)
        inner = context(namespace, events, "inner", value=4)
        assert namespace["nested_contexts"](outer, inner) == 12
        assert events[-2:] == [
            ("exit", "inner", None),
            ("exit", "outer", None),
        ]

        events = []
        manager = context(
            namespace,
            events,
            "suppress",
            suppress=True,
        )
        assert namespace["suppressed_exception"](manager) == "suppressed"
        assert ("exit", "suppress", "ValueError") in events

        events = []
        manager = context(namespace, events, "try", value=9)
        assert namespace["with_inside_try"](manager, False) == 9
        assert events[-1] == ("finally", "try")


def test_generator_and_async_with_transfers_preserve_behavior():
    _, original, rebuilt = namespaces()

    for namespace in (original, rebuilt):
        events = []
        generated = namespace["generator_transfer"](
            context(namespace, events, "generator", value=8)
        )
        assert next(generated) == 8
        assert generated.send("sent") == "sent"
        with pytest.raises(StopIteration) as stopped:
            next(generated)
        assert stopped.value.value == "done"
        assert events[-1] == ("exit", "generator", None)

        async def snapshot():
            async_events = []
            manager = namespace["AsyncTraceContext"](
                async_events,
                "async",
                value=6,
            )
            returned = await namespace["async_return"](manager, 4)

            def factory(value):
                return namespace["AsyncTraceContext"](
                    async_events,
                    str(value),
                )

            looped = await namespace["async_loop_transfers"](
                factory,
                [1, -1, 2, 0, 3],
            )
            return returned, looped, async_events

        returned, looped, events = asyncio.run(snapshot())
        assert returned == 10
        assert looped == [1, 2]
        assert ("aexit", "async", None) in events
