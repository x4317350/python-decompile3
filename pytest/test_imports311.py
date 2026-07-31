"""CPython 3.11 import-transaction recovery regressions."""

from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
from xdis.version_info import PythonImplementation

from decompyle3.parsers.p311.base import Python311ParseError
from decompyle3.scanners.scanner311 import Scanner311
from decompyle3.semantics.pysource import code_deparse
from support311 import ROOT


pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="Parser311 import tests require CPython 3.11",
)

SOURCE = (
    ROOT
    / "test"
    / "simple_source"
    / "311"
    / "11_import_transactions.py"
)
RELATIVE_SOURCE = """
from .relative_target import VALUE as relative_value
from . import relative_target as sibling
"""
MISSING_SOURCE = "from .missing_target import VALUE\n"
CIRCULAR_SOURCE = """
from .cycle_b import VALUE
READY = True
"""


def deparse_exec(source: str, filename="<import-exec-311>") -> str:
    output = io.StringIO()
    deparsed = code_deparse(
        compile(source, filename, "exec", dont_inherit=True),
        out=output,
        version=(3, 11),
        compile_mode="exec",
        python_implementation=PythonImplementation.CPython,
    )
    assert deparsed.text == output.getvalue()
    return deparsed.text


def execute_exec(source: str, name: str, package=None):
    namespace = {
        "__name__": name,
        "__package__": package,
    }
    exec(compile(source, f"<{name}>", "exec"), namespace)
    return namespace


def import_snapshot(namespace):
    return (
        namespace["json_decoder"].JSONDecoder().decode('{"value": 7}')[
            "value"
        ],
        namespace["xml"].etree.ElementTree.fromstring("<root />").tag,
        namespace["element_tree"].fromstring("<child />").tag,
        list(namespace["deque"]((1, 2, 3))),
        namespace["mapping_factory"](list, {"key": [4]})["key"],
        namespace["encode"]("a value"),
        namespace["unquote"]("a%20value"),
    )


def capture_exec(source: str, name: str, package: str):
    try:
        execute_exec(source, name, package)
    except BaseException as error:
        return (
            type(error).__name__,
            error.args,
            getattr(error, "name", None),
        )
    return "return", (), None


def test_normalized_dotted_alias_keeps_one_import_transaction():
    code = compile(
        SOURCE.read_text(encoding="utf-8"),
        str(SOURCE),
        "exec",
        dont_inherit=True,
    )
    scanner = Scanner311()
    scanner.ingest(code)
    kinds = [
        instruction.kind
        for instruction in scanner.normalized_instructions
        if instruction.kind
        in {
            "IMPORT_FROM",
            "IMPORT_NAME",
            "POP_TOP",
            "STORE_NAME",
            "SWAP_STACK",
        }
    ]

    dotted_alias = [
        "IMPORT_NAME",
        "IMPORT_FROM",
        "SWAP_STACK",
        "POP_TOP",
        "IMPORT_FROM",
        "STORE_NAME",
        "POP_TOP",
    ]
    assert any(
        kinds[index : index + len(dotted_alias)] == dotted_alias
        for index in range(len(kinds) - len(dotted_alias) + 1)
    )


def test_dotted_multi_name_and_alias_imports_preserve_behavior():
    source = SOURCE.read_text(encoding="utf-8")
    recovered = deparse_exec(source, str(SOURCE))
    tree = ast.parse(recovered, filename="<recovered-imports-311>")
    compile(tree, "<recovered-imports-311>", "exec", dont_inherit=True)

    original_namespace = execute_exec(source, "original_imports_311")
    recovered_namespace = execute_exec(recovered, "recovered_imports_311")
    assert import_snapshot(recovered_namespace) == import_snapshot(
        original_namespace
    )

    imports = [
        alias
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert ("xml.etree.ElementTree", None) in {
        (alias.name, alias.asname) for alias in imports
    }
    assert ("xml.etree.ElementTree", "element_tree") in {
        (alias.name, alias.asname) for alias in imports
    }
    assert ("json.decoder", "json_decoder") in {
        (alias.name, alias.asname) for alias in imports
    }
    imported_names = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("collections", 0, "defaultdict", "mapping_factory") in (
        imported_names
    )
    assert ("collections", 0, "deque", None) in imported_names


def test_relative_imports_preserve_level_binding_and_missing_error(
    tmp_path,
):
    package = tmp_path / "stage2pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "relative_target.py").write_text(
        "VALUE = 'relative-value'\n",
        encoding="utf-8",
    )
    recovered = deparse_exec(RELATIVE_SOURCE)
    tree = ast.parse(recovered, filename="<recovered-relative-imports-311>")
    compile(
        tree,
        "<recovered-relative-imports-311>",
        "exec",
        dont_inherit=True,
    )

    sys.path.insert(0, str(tmp_path))
    try:
        original_namespace = execute_exec(
            RELATIVE_SOURCE,
            "stage2pkg.original_imports",
            "stage2pkg",
        )
        recovered_namespace = execute_exec(
            recovered,
            "stage2pkg.recovered_imports",
            "stage2pkg",
        )
        original_values = (
            original_namespace["relative_value"],
            original_namespace["sibling"].VALUE,
        )
        recovered_values = (
            recovered_namespace["relative_value"],
            recovered_namespace["sibling"].VALUE,
        )
        assert recovered_values == original_values

        recovered_missing = deparse_exec(MISSING_SOURCE)
        assert capture_exec(
            recovered_missing,
            "stage2pkg.recovered_missing",
            "stage2pkg",
        ) == capture_exec(
            MISSING_SOURCE,
            "stage2pkg.original_missing",
            "stage2pkg",
        )
    finally:
        sys.path.remove(str(tmp_path))
        for module_name in tuple(sys.modules):
            if module_name == "stage2pkg" or module_name.startswith(
                "stage2pkg."
            ):
                sys.modules.pop(module_name)

    relative_imports = [
        node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    ]
    assert all(node.level == 1 for node in relative_imports)
    assert {node.module for node in relative_imports} == {
        None,
        "relative_target",
    }


def circular_result(root: Path):
    script = """
import json
try:
    import stage2cycle.cycle_a
except BaseException as error:
    print(json.dumps({
        "args": error.args,
        "name": getattr(error, "name", None),
        "type": type(error).__name__,
    }, sort_keys=True))
else:
    print(json.dumps({"type": "return"}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def test_circular_relative_import_preserves_import_error(tmp_path):
    package = tmp_path / "stage2cycle"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cycle_b.py").write_text(
        "from .cycle_a import READY\nVALUE = 'ready'\n",
        encoding="utf-8",
    )
    cycle_a = package / "cycle_a.py"
    cycle_a.write_text(CIRCULAR_SOURCE, encoding="utf-8")
    original = circular_result(tmp_path)

    recovered = deparse_exec(CIRCULAR_SOURCE)
    compile(
        ast.parse(recovered),
        "<recovered-circular-import-311>",
        "exec",
        dont_inherit=True,
    )
    cycle_a.write_text(recovered, encoding="utf-8")
    rebuilt = circular_result(tmp_path)

    assert rebuilt == original
    assert original["type"] == "ImportError"
    assert original["name"] == "stage2cycle.cycle_a"


@pytest.mark.parametrize(
    ("source", "next_failure"),
    (
        (ROOT / "decompyle3" / "scanners" / "pypy37.py", None),
        (ROOT / "decompyle3" / "scanners" / "pypy38.py", None),
        (ROOT / "decompyle3" / "semantics" / "customize311.py", None),
        (ROOT / "decompyle3" / "semantics" / "parser_error.py", None),
        (
            ROOT / "decompyle3" / "semantics" / "pysource.py",
            "Operand stack underflow",
        ),
        (
            Path(sysconfig.get_path("purelib")) / "_pytest" / "junitxml.py",
            "function definition is stored to a non-name target",
        ),
    ),
)
def test_stage2_archived_inputs_clear_the_import_protocol(
    source,
    next_failure,
):
    if next_failure is not None:
        with pytest.raises(
            Python311ParseError,
            match=next_failure,
        ) as raised:
            deparse_exec(source.read_text(encoding="utf-8"), str(source))
        assert "IMPORT_FROM has no owning IMPORT_NAME" not in str(
            raised.value
        )
        return

    recovered = deparse_exec(
        source.read_text(encoding="utf-8"),
        str(source),
    )
    compile(
        ast.parse(recovered, filename=f"<recovered-{source.name}>"),
        f"<recovered-{source.name}>",
        "exec",
        dont_inherit=True,
    )
