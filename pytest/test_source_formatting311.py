"""Fail-closed source-formatting regressions for CPython 3.11 modules."""

from __future__ import annotations

import ast
import builtins
import io
import py_compile
import sys
import types

import pytest
from click.testing import CliRunner
from xdis.version_info import PythonImplementation

from decompyle3.bin.decompile import main_bin
from decompyle3.errors import SemanticGenerationError
from decompyle3.semantics.pysource import code_deparse
from decompyle3.source_format import format_python311_source

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="These regressions exercise CPython 3.11 source generation",
)


SOURCE = r"""
events = []


def mark(value):
    "  leading whitespace is observable through mark.__doc__"
    events.append(value)
    return value


DEFAULT_MAP = {
    mark("model\\first_component_with_a_long_name.gim"),
    mark("model\\second.gimmodel\\third.gimmodel\\fourth.gim"),
    mark("model\\fifth_component_with_a_long_name.gim"),
}

SPECIAL_MODEL = {
    mark("first"): (mark("model/first"), mark("bone001")),
    mark("second"): (mark("model/second"), mark("bone002")),
    mark("third"): (mark("model/third"), mark("bone003")),
}


class Example:
    def __init__(self, entityid = None):
        self.entityid = entityid
"""


def recover(source: str, *, format_source=True, line_length=88) -> str:
    output = io.StringIO()
    code_deparse(
        compile(source, "<source-formatting311>", "exec"),
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
        format_source=format_source,
        line_length=line_length,
    )
    return output.getvalue()


def execute(source: str):
    namespace = {"__name__": "source_formatting311"}
    exec(compile(source, "<source-formatting311-exec>", "exec"), namespace)
    return namespace


def semantic_ast(source: str) -> str:
    return ast.dump(ast.parse(source), include_attributes=False)


def test_formatter_wraps_collections_and_preserves_ast():
    recovered = recover(SOURCE, format_source=False)
    formatted = format_python311_source(recovered, line_length=88)

    assert semantic_ast(formatted) == semantic_ast(recovered)
    assert "DEFAULT_MAP = {\n" in formatted
    assert "SPECIAL_MODEL = {\n" in formatted
    assert "entityid=None" in formatted
    ast.parse(formatted)
    compile(formatted, "<source-formatting311-recompiled>", "exec")


def test_integrated_formatter_preserves_dynamic_semantics_and_order():
    formatted = recover(SOURCE, line_length=88)
    original_namespace = execute(SOURCE)
    formatted_namespace = execute(formatted)

    assert formatted_namespace["events"] == original_namespace["events"]
    assert formatted_namespace["mark"].__doc__ == original_namespace["mark"].__doc__
    assert formatted_namespace["DEFAULT_MAP"] == original_namespace["DEFAULT_MAP"]
    assert len(formatted_namespace["DEFAULT_MAP"]) == 3
    assert list(formatted_namespace["SPECIAL_MODEL"]) == list(
        original_namespace["SPECIAL_MODEL"]
    )
    assert formatted_namespace["SPECIAL_MODEL"] == original_namespace["SPECIAL_MODEL"]
    assert formatted_namespace["Example"]().entityid is None


def test_no_format_source_keeps_ast_unparse_layout():
    recovered = recover(SOURCE, format_source=False, line_length=60)

    assert "DEFAULT_MAP = {mark(" in recovered
    assert "DEFAULT_MAP = {\n" not in recovered
    compile(recovered, "<source-formatting311-unformatted>", "exec")


def test_missing_black_dependency_fails_closed(monkeypatch):
    real_import = builtins.__import__

    def missing_black(name, *args, **kwargs):
        if name == "black":
            raise ImportError("black deliberately unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_black)
    with pytest.raises(
        SemanticGenerationError,
        match="requires the Black package",
    ):
        format_python311_source("value = 1", line_length=88)


def test_formatter_ast_change_fails_closed(monkeypatch):
    fake_black = types.SimpleNamespace(
        TargetVersion=types.SimpleNamespace(PY311="py311"),
        FileMode=lambda **kwargs: kwargs,
        format_str=lambda source, mode: "value = 2\n",
    )
    monkeypatch.setitem(sys.modules, "black", fake_black)

    with pytest.raises(
        SemanticGenerationError,
        match="changed the recovered AST",
    ):
        format_python311_source("value = 1", line_length=88)


def test_cli_format_switch_and_line_length(tmp_path):
    source = tmp_path / "format_input.py"
    bytecode = tmp_path / "format_input.pyc"
    formatted_output = tmp_path / "formatted.py"
    unformatted_output = tmp_path / "unformatted.py"
    source.write_text(SOURCE, encoding="utf-8")
    py_compile.compile(str(source), cfile=str(bytecode), doraise=True)

    formatted_result = CliRunner().invoke(
        main_bin,
        [
            "--format-source",
            "--line-length",
            "88",
            "--output",
            str(formatted_output),
            str(bytecode),
        ],
    )
    assert formatted_result.exit_code == 0, formatted_result.output
    formatted = formatted_output.read_text(encoding="utf-8")
    assert "DEFAULT_MAP = {\n" in formatted
    compile(formatted, str(formatted_output), "exec")

    unformatted_result = CliRunner().invoke(
        main_bin,
        [
            "--no-format-source",
            "--output",
            str(unformatted_output),
            str(bytecode),
        ],
    )
    assert unformatted_result.exit_code == 0, unformatted_result.output
    unformatted = unformatted_output.read_text(encoding="utf-8")
    assert "DEFAULT_MAP = {mark(" in unformatted
    compile(unformatted, str(unformatted_output), "exec")
