"""Phase 3 acceptance tests for CPython 3.11 source recovery."""

from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.parsers.main import get_python_parser, python_parser
from decompyle3.semantics.pysource import code_deparse, deparse_code2str
from support311 import ROOT, compile_source


SOURCE = ROOT / "test" / "simple_source" / "311" / "09_straight_line.py"

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 acceptance tests require CPython 3.11",
)


def deparse311(code, compile_mode="exec") -> str:
    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        compile_mode=compile_mode,
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def load_stage3_code(tmp_path: Path):
    bytecode = tmp_path / "09_straight_line.pyc"
    version, _, _, code, implementation, *_ = compile_source(SOURCE, bytecode)
    assert version == (3, 11)
    assert implementation is PythonImplementation.CPython
    return code


def execute(source: str):
    namespace = {"__name__": "stage3_recovered"}
    exec(compile(source, "<stage3>", "exec"), namespace)
    return namespace


def test_parser311_registers_all_compile_modes():
    expected = {
        "exec": "Python311ParserExec",
        "single": "Python311ParserSingle",
        "eval": "Python311ParserEval",
        "expr": "Python311ParserExpr",
        "lambda": "Python311ParserLambda",
    }
    for mode, class_name in expected.items():
        parser = get_python_parser(
            (3, 11, 9),
            compile_mode=mode,
            python_implementation=PythonImplementation.CPython,
        )
        assert parser.__class__.__name__ == class_name


def test_python311_source_walker_preserves_text_contract():
    code = compile("answer = 42", "<text-contract-311>", "exec")
    output = io.StringIO()
    deparsed = code_deparse(
        code,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )

    assert deparsed.text == "answer = 42"
    assert output.getvalue() == deparsed.text
    assert (
        deparse_code2str(
            code,
            out=io.StringIO(),
            version=(3, 11),
            python_implementation=PythonImplementation.CPython,
        )
        == deparsed.text
    )


def test_straight_line_pyc_deparses_and_recompiles(tmp_path):
    recovered = deparse311(load_stage3_code(tmp_path))
    tree = ast.parse(recovered)
    compile(tree, "<recovered-311>", "exec")

    assert any(isinstance(node, ast.ClassDef) for node in tree.body)
    calculate = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate"
    )
    assert [argument.arg for argument in calculate.args.posonlyargs] == [
        "left",
        "right",
    ]
    assert calculate.args.vararg.arg == "values"
    assert calculate.args.kwarg.arg == "options"
    assert calculate.decorator_list
    assert calculate.returns is not None


def test_stage3_recovered_source_has_equivalent_behavior(tmp_path):
    original_source = SOURCE.read_text(encoding="utf-8")
    recovered_source = deparse311(load_stage3_code(tmp_path))
    original = execute(original_source)
    recovered = execute(recovered_source)

    assert recovered["CONSTANT"] == original["CONSTANT"]
    assert recovered["CHAIN_LEFT"] == original["CHAIN_LEFT"]
    assert recovered["CHAIN_RIGHT"] == original["CHAIN_RIGHT"]
    assert "temporary" not in recovered

    arguments = (6, 3, 9, 12)
    keywords = {"scale": 2, "extra": "kept"}
    assert recovered["calculate"](*arguments, **keywords) == original[
        "calculate"
    ](*arguments, **keywords)
    assert recovered["calculate"].stage3 is True

    assert recovered["Accumulator"].from_value(4).doubled == 8
    child = recovered["Child"](3)
    assert child.add(5) == 13
    assert recovered["Child"].stage3 is True
    assert recovered["make_adder"](10)(5) == 15
    counter = recovered["make_counter"](5)
    assert [counter(), counter(3)] == [6, 9]
    assert recovered["make_lambda"](4)(6) == 24
    assert recovered["unpack"]([1, 2, 3, 4]) == (1, [2, 3], 4)

    with pytest.raises(ValueError, match="stage 3"):
        recovered["fail"]("stage 3")


def test_eval_expr_single_and_lambda_modes():
    eval_code = compile("value + 2", "<eval-311>", "eval")
    assert deparse311(eval_code, "eval").strip() == "value + 2"
    assert deparse311(eval_code, "expr").strip() == "value + 2"

    single_code = compile("value\n", "<single-311>", "single")
    assert ast.parse(deparse311(single_code, "single"))

    module = compile("result = lambda value: value * 3", "<lambda-311>", "exec")
    lambda_code = next(
        constant
        for constant in module.co_consts
        if hasattr(constant, "co_name") and constant.co_name == "<lambda>"
    )
    assert deparse311(lambda_code, "lambda").strip() == "lambda value: value * 3"


def test_python_parser_returns_standard_ast_result():
    code = compile("answer = 40 + 2", "<parser-311>", "exec")
    result = python_parser(
        code,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    assert result.kind == "stmts"
    assert isinstance(result.tree, ast.Module)
    assert compile(result.tree, "<parser-result>", "exec")
