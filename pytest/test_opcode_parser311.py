"""Phase 5 Parser coverage for all 110 CPython 3.11 opcodes."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import sys
from functools import lru_cache
from pathlib import Path

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.parsers.main import get_python_parser
from decompyle3.parsers.p311.base import (
    PARSER_INTERNAL_CONSUMERS,
    PARSER_INTERNAL_OPNAMES,
    Python311ParseError,
)
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse


ROOT = Path(__file__).resolve().parents[1]
CORPUS_GENERATOR_PATH = (
    ROOT / "test" / "bytecode_3.11" / "generate.py"
)
OPCODE_MATRIX_PATH = (
    ROOT / "test" / "bytecode_3.11" / "opcode_matrix.json"
)
PARSER_TEST_NODE = (
    "pytest/test_opcode_parser311.py::"
    "test_each_opcode_has_stable_parser_contract"
)

SPEC = importlib.util.spec_from_file_location(
    "generate_parser_corpus311",
    CORPUS_GENERATOR_PATH,
)
assert SPEC is not None and SPEC.loader is not None
corpus_generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(corpus_generator)

OPCODE_MATRIX = json.loads(
    OPCODE_MATRIX_PATH.read_text(encoding="utf-8")
)
OPCODE_ITEMS = tuple(
    sorted(OPCODE_MATRIX["opcodes"], key=lambda item: item["opcode"])
)

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="CPython 3.11 Parser matrix tests require CPython 3.11",
)


@lru_cache(maxsize=None)
def compile_fixture(relative_path):
    return corpus_generator.compile_source(ROOT / relative_path)


@lru_cache(maxsize=None)
def recover_fixture(relative_path):
    source = ROOT / relative_path
    output = io.StringIO()
    code_deparse(
        compile_fixture(relative_path),
        out=output,
        version=(3, 11),
        compile_mode=corpus_generator.source_compile_mode(source),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def recover_code(code, compile_mode="exec"):
    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        compile_mode=compile_mode,
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def parse_recovered(relative_path):
    source = ROOT / relative_path
    mode = corpus_generator.source_compile_mode(source)
    recovered = recover_fixture(relative_path)
    tree = ast.parse(
        recovered,
        filename=f"<recovered-{source.stem}>",
        mode=mode,
    )
    compile(tree, f"<recovered-{source.stem}>", mode)
    return tree


def execute(source, name):
    namespace = {
        "__file__": f"<{name}>",
        "__name__": name,
        "__package__": None,
    }
    exec(
        compile(
            source,
            f"<{name}>",
            "exec",
            dont_inherit=True,
        ),
        namespace,
    )
    return namespace


def original_and_recovered(relative_path):
    source = ROOT / relative_path
    return (
        execute(source.read_text(encoding="utf-8"), "stage5_original"),
        execute(recover_fixture(relative_path), "stage5_recovered"),
    )


@pytest.mark.parametrize(
    "opcode_name",
    [item["name"] for item in OPCODE_ITEMS],
    ids=[
        f"{item['opcode']:03d}-{item['name']}"
        for item in OPCODE_ITEMS
    ],
)
def test_each_opcode_has_stable_parser_contract(opcode_name):
    item = next(
        item for item in OPCODE_ITEMS if item["name"] == opcode_name
    )
    expected_status = (
        "internal_consumed"
        if opcode_name in PARSER_INTERNAL_OPNAMES
        else "pass"
    )
    root_code = compile_fixture(item["source_fixture"])
    observed = set()
    for code in Scanner311.iter_code_objects(root_code):
        scanner = Scanner311()
        scanner.ingest_raw(code)
        observed.update(raw.opname for raw in scanner.insts)

    assert len(OPCODE_ITEMS) == 110
    assert opcode_name in observed
    assert item["layers"]["parser"] == expected_status
    assert PARSER_TEST_NODE in item["tests"]

    tree = parse_recovered(item["source_fixture"])
    assert isinstance(tree, (ast.Module, ast.Expression, ast.Interactive))


def test_internal_protocol_consumers_are_explicit():
    assert PARSER_INTERNAL_OPNAMES == {
        "CACHE",
        "RESUME",
        "EXTENDED_ARG",
        "PUSH_NULL",
        "PRECALL",
        "KW_NAMES",
        "MAKE_CELL",
        "COPY_FREE_VARS",
    }
    assert set(PARSER_INTERNAL_CONSUMERS) == PARSER_INTERNAL_OPNAMES
    assert all(PARSER_INTERNAL_CONSUMERS.values())


@pytest.mark.parametrize(
    "family, relative_paths, expected_nodes",
    [
        (
            "scope",
            (
                "test/bytecode_3.11/opcode_fixtures/"
                "scope/setup_annotations.py",
                "test/bytecode_3.11/opcode_fixtures/"
                "scope/load_classderef.py",
            ),
            (ast.AnnAssign, ast.ClassDef),
        ),
        (
            "expression",
            ("test/simple_source/311/00_expressions.py",),
            (ast.UnaryOp, ast.BinOp, ast.BoolOp, ast.Compare),
        ),
        (
            "collection",
            ("test/simple_source/311/08_imports_unpacking.py",),
            (ast.Dict, ast.Starred, ast.Tuple),
        ),
        (
            "call_function_class",
            ("test/simple_source/311/01_functions_classes.py",),
            (ast.Call, ast.FunctionDef, ast.ClassDef, ast.Lambda),
        ),
        (
            "import_annotation_assert",
            (
                "test/bytecode_3.11/opcode_fixtures/"
                "imports/import_star.py",
                "test/bytecode_3.11/opcode_fixtures/"
                "scope/setup_annotations.py",
                "test/bytecode_3.11/opcode_fixtures/"
                "statements/load_assertion_error.py",
            ),
            (ast.ImportFrom, ast.AnnAssign, ast.Assert),
        ),
        (
            "control_flow",
            ("test/simple_source/311/02_control_flow.py",),
            (ast.If, ast.For, ast.While),
        ),
        (
            "comprehension_generator_async",
            (
                "test/simple_source/311/03_comprehensions.py",
                "test/simple_source/311/04_generators_async.py",
            ),
            (ast.ListComp, ast.GeneratorExp, ast.Yield, ast.Await),
        ),
        (
            "exception_with_except_star",
            (
                "test/simple_source/311/05_exceptions_with.py",
                "test/simple_source/311/07_exception_group.py",
            ),
            (ast.Try, ast.With, ast.AsyncWith, ast.TryStar),
        ),
        (
            "match_case",
            ("test/simple_source/311/06_match.py",),
            (ast.Match,),
        ),
        (
            "internal_protocol",
            (
                "test/bytecode_3.11/opcode_fixtures/"
                "internal/print_expr.py",
            ),
            (ast.Expr,),
        ),
    ],
)
def test_semantic_families_build_expected_ast(
    family,
    relative_paths,
    expected_nodes,
):
    del family
    observed_types = set()
    for relative_path in relative_paths:
        observed_types.update(
            type(node) for node in ast.walk(parse_recovered(relative_path))
        )
    assert set(expected_nodes) <= observed_types


def test_assert_annotation_and_import_star_preserve_behavior():
    annotation_path = (
        "test/bytecode_3.11/opcode_fixtures/"
        "scope/setup_annotations.py"
    )
    original, recovered = original_and_recovered(annotation_path)
    assert recovered["__annotations__"] == original["__annotations__"]
    assert recovered["Annotated"].__annotations__ == (
        original["Annotated"].__annotations__
    )

    assert_path = (
        "test/bytecode_3.11/opcode_fixtures/"
        "statements/load_assertion_error.py"
    )
    original, recovered = original_and_recovered(assert_path)
    assert original["require_value"]("present") is None
    assert recovered["require_value"]("present") is None
    for namespace in (original, recovered):
        with pytest.raises(AssertionError) as raised:
            namespace["require_value"]("")
        assert raised.value.args == ("value is required",)

    import_path = (
        "test/bytecode_3.11/opcode_fixtures/imports/import_star.py"
    )
    original, recovered = original_and_recovered(import_path)
    assert recovered["sqrt"](81) == original["sqrt"](81) == 9
    assert recovered["pi"] == original["pi"]


def test_annotation_assignments_and_future_expressions_preserve_values():
    source = """
from __future__ import annotations

module_value: list[int] = []

class Annotated:
    member: dict[str, int] = {}

def convert(value: tuple[int, ...]) -> set[int]:
    return set(value)
"""
    original = execute(source, "stage5_future_annotation_original")
    recovered_source = recover_code(
        compile(
            source,
            "<stage5-future-annotations>",
            "exec",
            dont_inherit=True,
        )
    )
    recovered = execute(
        recovered_source,
        "stage5_future_annotation_recovered",
    )

    tree = ast.parse(recovered_source)
    assert sum(
        isinstance(node, ast.AnnAssign) for node in ast.walk(tree)
    ) == 2
    assert recovered["__annotations__"] == original["__annotations__"]
    assert recovered["Annotated"].__annotations__ == (
        original["Annotated"].__annotations__
    )
    assert recovered["convert"].__annotations__ == (
        original["convert"].__annotations__
    )
    assert recovered["convert"]((1, 2, 2)) == {1, 2}


@pytest.mark.parametrize(
    "assertion, accepted, rejected",
    [
        ("value", True, False),
        ("not value", False, True),
        ("value is None", None, 1),
        ("value is not None", 1, None),
    ],
)
def test_assert_predicate_variants_preserve_behavior(
    assertion,
    accepted,
    rejected,
):
    source = (
        "def require(value):\n"
        f"    assert {assertion}, f'bad {{value!r}}'\n"
    )
    original = execute(source, "stage5_assert_original")
    recovered_source = recover_code(
        compile(
            source,
            "<stage5-assert>",
            "exec",
            dont_inherit=True,
        )
    )
    recovered = execute(recovered_source, "stage5_assert_recovered")

    assert any(
        isinstance(node, ast.Assert)
        for node in ast.walk(ast.parse(recovered_source))
    )
    assert recovered["require"](accepted) is None
    for namespace in (original, recovered):
        with pytest.raises(AssertionError) as raised:
            namespace["require"](rejected)
        assert raised.value.args == (f"bad {rejected!r}",)


@pytest.mark.parametrize(
    "assertion, accepted, rejected",
    [
        (
            "left and right",
            ((1, 2),),
            ((0, 2), (1, 0)),
        ),
        (
            "left or right",
            ((1, 0), (0, 2)),
            ((0, 0),),
        ),
    ],
)
def test_compound_assertions_preserve_behavior(
    assertion,
    accepted,
    rejected,
):
    source = (
        "def require(left, right):\n"
        f"    assert {assertion}, 'both values are required'\n"
    )
    original = execute(source, "stage7_compound_assert_original")
    recovered_source = recover_code(
        compile(
            source,
            "<stage7-compound-assert>",
            "exec",
            dont_inherit=True,
        )
    )
    recovered = execute(
        recovered_source,
        "stage7_compound_assert_recovered",
    )
    tree = ast.parse(recovered_source)
    assertion_node = next(
        node for node in ast.walk(tree) if isinstance(node, ast.Assert)
    )
    assert isinstance(assertion_node.test, ast.BoolOp)

    for arguments in accepted:
        assert original["require"](*arguments) is None
        assert recovered["require"](*arguments) is None
    for arguments in rejected:
        for namespace in (original, recovered):
            with pytest.raises(AssertionError) as raised:
                namespace["require"](*arguments)
            assert raised.value.args == ("both values are required",)


def test_scope_deletion_starred_collection_and_mapping_preserve_behavior():
    closure_path = (
        "test/bytecode_3.11/opcode_fixtures/"
        "scope/load_classderef.py"
    )
    original, recovered = original_and_recovered(closure_path)
    assert recovered["class_from_closure"]("value").captured == (
        original["class_from_closure"]("value").captured
    )

    global_store_path = (
        "test/bytecode_3.11/opcode_fixtures/scope/store_global.py"
    )
    original, recovered = original_and_recovered(global_store_path)
    original["store_value"]("updated")
    recovered["store_value"]("updated")
    assert recovered["value"] == original["value"] == "updated"

    global_delete_path = (
        "test/bytecode_3.11/opcode_fixtures/scope/delete_global.py"
    )
    original, recovered = original_and_recovered(global_delete_path)
    original["delete_value"]()
    recovered["delete_value"]()
    assert "value" not in original
    assert "value" not in recovered

    deref_delete_path = (
        "test/bytecode_3.11/opcode_fixtures/scope/delete_deref.py"
    )
    original, recovered = original_and_recovered(deref_delete_path)
    for namespace in (original, recovered):
        delete_value = namespace["make_deleter"]()
        assert delete_value() is None
        with pytest.raises(NameError):
            delete_value()

    tuple_path = (
        "test/bytecode_3.11/opcode_fixtures/"
        "collections/list_to_tuple.py"
    )
    original, recovered = original_and_recovered(tuple_path)
    assert recovered["starred_tuple"]([1, 2, 3]) == (
        original["starred_tuple"]([1, 2, 3])
    )

    set_path = (
        "test/bytecode_3.11/opcode_fixtures/collections/set_update.py"
    )
    original, recovered = original_and_recovered(set_path)
    assert recovered["starred_set"]([1, 2, 3]) == (
        original["starred_set"]([1, 2, 3])
    )

    mapping_path = "test/simple_source/311/08_imports_unpacking.py"
    original, recovered = original_and_recovered(mapping_path)
    arguments = ((1, 2), (3, 4))
    keywords = {"label": "kept"}
    assert recovered["unpacking"](*arguments, **keywords) == (
        original["unpacking"](*arguments, **keywords)
    )


def test_unknown_parser_vocabulary_fails_closed():
    code = compile("answer = 42", "<unknown-parser-opcode>", "exec")
    scanner = Scanner311()
    tokens, customize = scanner.ingest(code)
    unknown = next(token for token in tokens if token.kind == "LOAD_CONST")
    unknown.kind = "UNKNOWN_PARSER_OPCODE"

    parser = get_python_parser(
        (3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    parser.code_object = code
    parser.customize_grammar_rules(tokens, customize)
    with pytest.raises(
        Python311ParseError,
        match="Unsupported phase-3 opcode UNKNOWN_PARSER_OPCODE",
    ):
        parser.parse(tokens)
