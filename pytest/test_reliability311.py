"""Phase 8 reliability and release-readiness tests for CPython 3.11."""

from __future__ import annotations

import ast
import io
import sys
import sysconfig
from pathlib import Path

import pytest
from click.testing import CliRunner
from xdis.version_info import PythonImplementation

from decompyle3.bin.decompile import main_bin
from decompyle3.errors import (
    ControlFlowError,
    Decompyle3Error,
    ExceptionTableError,
    MalformedBytecodeError,
    ParserError,
    SemanticGenerationError,
    UnsupportedOpcodeError,
    UnsupportedVersionError,
    VerificationError,
)
from decompyle3.main import verify_source
from decompyle3.parsers.p311.base import UnsupportedPython311ControlFlow
from decompyle3.scanner import get_scanner
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import (
    ROOT,
    assert_same_behavior,
    compile_source,
    corpus_sources,
)


STDLIB = Path(sysconfig.get_path("stdlib"))
STDLIB_SUBSET = ("abc.py", "colorsys.py", "copy.py", "hmac.py", "keyword.py")

pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Phase 8 release tests require CPython 3.11",
)


def recover_code(code) -> str:
    output = io.StringIO()
    code_deparse(
        code,
        out=output,
        version=(3, 11),
        python_implementation=PythonImplementation.CPython,
    )
    return output.getvalue()


def execute(source: str, name: str):
    namespace = {
        "__file__": f"<{name}>",
        "__name__": name,
        "__package__": None,
    }
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def test_public_error_hierarchy_has_machine_readable_context():
    expected = (
        UnsupportedVersionError,
        UnsupportedOpcodeError,
        MalformedBytecodeError,
        ControlFlowError,
        ExceptionTableError,
        ParserError,
        SemanticGenerationError,
        VerificationError,
    )
    assert all(issubclass(error_type, Decompyle3Error) for error_type in expected)

    scanner = Scanner311()
    with pytest.raises(UnsupportedOpcodeError) as raised:
        scanner._validate_bytecode(b"\xff\x00", "broken")
    error = raised.value
    assert error.version == (3, 11)
    assert error.code_name == "broken"
    assert error.offset == 0
    assert "version=3.11" in str(error)
    assert "code='broken'" in str(error)
    assert "offset=0" in str(error)

    with pytest.raises(UnsupportedVersionError) as raised:
        get_scanner((3, 10), PythonImplementation.CPython)
    assert raised.value.version == (3, 10)

    with pytest.raises(VerificationError) as raised:
        verify_source(
            "if:",
            bytecode_version=(3, 11),
            code_name="<module>",
        )
    assert raised.value.version == (3, 11)
    assert raised.value.code_name == "<module>"
    assert "offset=?" in str(raised.value)


@pytest.mark.parametrize("source", list(corpus_sources()), ids=lambda path: path.stem)
def test_complete_corpus_reparses_recompiles_and_runs(source, tmp_path):
    bytecode = tmp_path / f"{source.stem}.pyc"
    version, _, _, code, implementation, *_ = compile_source(source, bytecode)
    assert version == (3, 11)
    assert implementation is PythonImplementation.CPython

    recovered_source = recover_code(code)
    tree = ast.parse(recovered_source, filename=f"<{source.stem}-recovered>")
    compile(tree, f"<{source.stem}-recovered>", "exec")

    recovered = tmp_path / f"{source.stem}_recovered.py"
    recovered.write_text(recovered_source, encoding="utf-8")
    assert_same_behavior(source, recovered)


@pytest.mark.parametrize("module_name", STDLIB_SUBSET)
def test_python311_standard_library_subset(module_name):
    source_path = STDLIB / module_name
    source = source_path.read_text(encoding="utf-8")
    recovered = recover_code(compile(source, str(source_path), "exec"))
    tree = ast.parse(recovered, filename=f"<stdlib-{module_name}>")
    compile(tree, f"<stdlib-{module_name}>", "exec")


def test_standard_library_subset_preserves_selected_behavior():
    keyword_path = STDLIB / "keyword.py"
    keyword_original = execute(
        keyword_path.read_text(encoding="utf-8"),
        "stdlib_keyword_original",
    )
    keyword_recovered = execute(
        recover_code(
            compile(
                keyword_path.read_text(encoding="utf-8"),
                str(keyword_path),
                "exec",
            )
        ),
        "stdlib_keyword_recovered",
    )
    for value in ("def", "match", "ordinary"):
        assert keyword_recovered["iskeyword"](value) == keyword_original[
            "iskeyword"
        ](value)
        assert keyword_recovered["issoftkeyword"](value) == keyword_original[
            "issoftkeyword"
        ](value)

    colorsys_path = STDLIB / "colorsys.py"
    colorsys_original = execute(
        colorsys_path.read_text(encoding="utf-8"),
        "stdlib_colorsys_original",
    )
    colorsys_recovered = execute(
        recover_code(
            compile(
                colorsys_path.read_text(encoding="utf-8"),
                str(colorsys_path),
                "exec",
            )
        ),
        "stdlib_colorsys_recovered",
    )
    for rgb in ((0.0, 0.0, 0.0), (0.2, 0.4, 0.8), (1.0, 0.5, 0.0)):
        assert colorsys_recovered["rgb_to_hsv"](*rgb) == pytest.approx(
            colorsys_original["rgb_to_hsv"](*rgb)
        )

    hmac_path = STDLIB / "hmac.py"
    hmac_original = execute(
        hmac_path.read_text(encoding="utf-8"),
        "stdlib_hmac_original",
    )
    hmac_recovered = execute(
        recover_code(
            compile(
                hmac_path.read_text(encoding="utf-8"),
                str(hmac_path),
                "exec",
            )
        ),
        "stdlib_hmac_recovered",
    )
    assert hmac_recovered["digest"](b"key", b"payload", "sha256") == (
        hmac_original["digest"](b"key", b"payload", "sha256")
    )


def stress_source() -> str:
    large = ["def large(value):"]
    large.extend(
        f"    item_{index} = value + {index}" for index in range(600)
    )
    large.append("    return item_0 + item_599")

    nested = "    return value\n"
    for index in reversed(range(30)):
        indented = "".join(
            f"    {line}\n" for line in nested.splitlines()
        )
        nested = (
            f"    def level_{index}(value):\n"
            f"{indented}"
            f"    return level_{index}(value)\n"
        )

    collection = ", ".join(str(index) for index in range(1500))
    return (
        "\n".join(large)
        + "\n\n"
        + "def deep(value):\n"
        + nested
        + "\n"
        + f"VALUES = [{collection}]\n\n"
        + "def collection_ends():\n"
        + "    return VALUES[0], VALUES[-1], len(VALUES)\n"
    )


def test_large_function_deep_nesting_and_long_collection_stress():
    source = stress_source()
    code = compile(source, "<phase8-stress>", "exec")
    saw_extended_arg = False
    for nested_code in Scanner311.iter_code_objects(code):
        scanner = Scanner311()
        scanner.ingest_raw(nested_code)
        saw_extended_arg |= any(
            token.kind == "EXTENDED_ARG" for token in scanner.raw_tokens
        )
    assert saw_extended_arg

    recovered = recover_code(code)
    tree = ast.parse(recovered, filename="<phase8-stress-recovered>")
    compile(tree, "<phase8-stress-recovered>", "exec")
    original_namespace = execute(source, "phase8_stress_original")
    recovered_namespace = execute(recovered, "phase8_stress_recovered")

    assert recovered_namespace["large"](7) == original_namespace["large"](7)
    assert recovered_namespace["deep"](11) == 11
    assert recovered_namespace["collection_ends"]() == (0, 1499, 1500)


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            """
def unsafe(group, events):
    try:
        if group is not None:
            raise group
    except* ValueError:
        events.append("handled")
    else:
        events.append("else")
    return events
""",
            "else suite",
        ),
        (
            """
def unsafe(group, events):
    try:
        raise group
    except* ValueError:
        events.append("handled")
    finally:
        events.append("finally")
    return events
""",
            "combined with finally",
        ),
    ],
)
def test_unsafe_except_star_combinations_fail_closed_with_context(
    source,
    expected,
):
    with pytest.raises(
        UnsupportedPython311ControlFlow,
        match=expected,
    ) as raised:
        recover_code(compile(source, "<unsafe-except-star>", "exec"))
    error = raised.value
    assert isinstance(error, ControlFlowError)
    assert error.version == (3, 11)
    assert error.code_name == "unsafe"
    assert isinstance(error.offset, int)


def compile_cli_input(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    compile_source(source, destination)
    return destination


def test_cli_accepts_a_new_output_file(tmp_path):
    bytecode = compile_cli_input(
        ROOT / "test" / "simple_source" / "311" / "09_straight_line.py",
        tmp_path / "straight.pyc",
    )
    output = tmp_path / "new-output.py"
    result = CliRunner().invoke(
        main_bin,
        ["--output", str(output), str(bytecode)],
    )

    assert result.exit_code == 0, result.output
    assert output.is_file()
    ast.parse(output.read_text(encoding="utf-8"))


def test_cli_batch_success_and_syntax_verification(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    bytecodes = [
        compile_cli_input(
            ROOT / "test" / "simple_source" / "311" / source_name,
            input_dir / f"{Path(source_name).stem}.pyc",
        )
        for source_name in ("00_expressions.py", "02_control_flow.py")
    ]

    result = CliRunner().invoke(
        main_bin,
        [
            "--verify",
            "syntax",
            "--output",
            str(output_dir),
            *(str(path) for path in bytecodes),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "2 okay, 0 failed, 0 failed verification" in result.output
    for bytecode in bytecodes:
        recovered = output_dir / bytecode.with_suffix(".py").name
        assert recovered.is_file()
        ast.parse(recovered.read_text(encoding="utf-8"))


def test_cli_batch_continues_after_failure_and_marks_partial_output(tmp_path):
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    broken = input_dir / "broken.pyc"
    broken.write_bytes(b"not a valid pyc")
    valid = compile_cli_input(
        ROOT / "test" / "simple_source" / "311" / "09_straight_line.py",
        input_dir / "valid.pyc",
    )

    result = CliRunner().invoke(
        main_bin,
        [
            "--output",
            str(output_dir),
            str(broken),
            str(valid),
        ],
    )

    assert result.exit_code == 1
    assert "2 files: 1 okay, 1 failed" in result.output
    assert "Traceback" not in result.output
    assert not (output_dir / "broken.py").exists()
    assert (output_dir / "broken.py_failed").is_file()
    recovered = output_dir / "valid.py"
    assert recovered.is_file()
    ast.parse(recovered.read_text(encoding="utf-8"))
