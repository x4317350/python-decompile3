"""Fail-closed formatting for recovered CPython 3.11 module source."""

from __future__ import annotations

import ast
import io
import tokenize
from typing import Dict, List, Tuple

from decompyle3.errors import SemanticGenerationError

MIN_LINE_LENGTH = 60
MAX_LINE_LENGTH = 240
DEFAULT_LINE_LENGTH = 100


def _semantic_ast(source: str) -> str:
    """Return a location-independent representation of parsed source."""
    return ast.dump(
        ast.parse(source, mode="exec"),
        annotate_fields=True,
        include_attributes=False,
    )


def _ast_absolute_offset(
    lines: List[str],
    line_starts: List[int],
    line_number: int,
    byte_column: int,
) -> int:
    """Convert an AST UTF-8 byte column into a source character offset."""
    prefix = lines[line_number - 1].encode("utf-8")[:byte_column]
    return line_starts[line_number - 1] + len(prefix.decode("utf-8"))


def _token_absolute_offset(
    line_starts: List[int],
    position: Tuple[int, int],
) -> int:
    """Convert a tokenize character position into a source offset."""
    line_number, column = position
    return line_starts[line_number - 1] + column


def _line_layout(source: str) -> Tuple[List[str], List[int]]:
    lines = source.splitlines(keepends=True)
    starts = []
    position = 0
    for line in lines:
        starts.append(position)
        position += len(line)
    return lines, starts


def _protected_docstrings(source: str) -> Tuple[str, Dict[str, str]]:
    """Replace docstrings so Black cannot normalize their runtime values."""
    tree = ast.parse(source, mode="exec")
    lines, line_starts = _line_layout(source)
    replacements = []
    values = {}
    containers = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    for node in ast.walk(tree):
        if not isinstance(node, containers) or not node.body:
            continue
        statement = node.body[0]
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        literal = statement.value
        index = len(values)
        sentinel = f"__DECOMPYLE3_PRESERVED_DOCSTRING_{index}__"
        while sentinel in source or sentinel in values:
            sentinel = "_" + sentinel
        values[sentinel] = literal.value
        start = _ast_absolute_offset(
            lines,
            line_starts,
            literal.lineno,
            literal.col_offset,
        )
        end = _ast_absolute_offset(
            lines,
            line_starts,
            literal.end_lineno,
            literal.end_col_offset,
        )
        replacements.append((start, end, repr(sentinel)))

    protected = source
    for start, end, replacement in sorted(replacements, reverse=True):
        protected = protected[:start] + replacement + protected[end:]
    return protected, values


def _restore_docstrings(source: str, values: Dict[str, str]) -> str:
    """Restore shielded docstrings after formatting, preserving exact values."""
    if not values:
        return source
    _, line_starts = _line_layout(source)
    replacements = []
    restored = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.STRING:
            continue
        try:
            value = ast.literal_eval(token.string)
        except (SyntaxError, ValueError):
            continue
        if value not in values:
            continue
        start = _token_absolute_offset(line_starts, token.start)
        end = _token_absolute_offset(line_starts, token.end)
        replacements.append((start, end, repr(values[value])))
        restored.add(value)

    if restored != set(values):
        raise ValueError("Black did not preserve all protected docstrings")

    result = source
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def format_python311_source(
    source: str,
    *,
    line_length: int = DEFAULT_LINE_LENGTH,
    code_name: str = "<module>",
) -> str:
    """Format recovered Python 3.11 module source without changing its AST.

    Formatting is deliberately fail-closed: the unformatted source is never
    returned as a silent fallback when Black is unavailable, rejects the
    source, or produces a different Python AST.
    """
    if (
        isinstance(line_length, bool)
        or not isinstance(line_length, int)
        or not MIN_LINE_LENGTH <= line_length <= MAX_LINE_LENGTH
    ):
        raise SemanticGenerationError(
            "Python 3.11 source line length must be between "
            f"{MIN_LINE_LENGTH} and {MAX_LINE_LENGTH}",
            version=(3, 11),
            code_name=code_name,
        )

    try:
        import black
    except ImportError as error:
        raise SemanticGenerationError(
            "Python 3.11 source formatting requires the Black package",
            version=(3, 11),
            code_name=code_name,
        ) from error

    try:
        before = _semantic_ast(source)
        protected_source, docstrings = _protected_docstrings(source)
        formatted = black.format_str(
            protected_source,
            mode=black.FileMode(
                target_versions={black.TargetVersion.PY311},
                line_length=line_length,
            ),
        ).rstrip()
        formatted = _restore_docstrings(formatted, docstrings)
        after = _semantic_ast(formatted)
    except Exception as error:
        raise SemanticGenerationError(
            "Python 3.11 source formatting failed",
            version=(3, 11),
            code_name=code_name,
        ) from error

    if before != after:
        raise SemanticGenerationError(
            "Source formatter changed the recovered AST",
            version=(3, 11),
            code_name=code_name,
        )

    return formatted


__all__ = [
    "DEFAULT_LINE_LENGTH",
    "MAX_LINE_LENGTH",
    "MIN_LINE_LENGTH",
    "format_python311_source",
]
