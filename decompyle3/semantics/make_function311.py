"""Build Python AST argument lists from CPython 3.11 MAKE_FUNCTION operands."""

from __future__ import annotations

import ast
import __future__
from typing import Dict, List, Optional, Tuple


CO_VARARGS = 0x04
CO_VARKEYWORDS = 0x08


def _constant_nodes(value) -> List[ast.expr]:
    if isinstance(value, ast.Tuple):
        return list(value.elts)
    if isinstance(value, ast.List):
        return list(value.elts)
    if isinstance(value, ast.Constant) and isinstance(value.value, tuple):
        return [ast.Constant(value=item) for item in value.value]
    return []


def _keyword_defaults(value) -> Dict[str, ast.expr]:
    if not isinstance(value, ast.Dict):
        return {}
    result = {}
    for key, item in zip(value.keys, value.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            result[key.value] = item
    return result


def _annotation_expression(node, future_annotations):
    if (
        future_annotations
        and isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    ):
        try:
            return ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return node
    return node


def _annotations(
    value,
    future_annotations=False,
) -> Tuple[Dict[str, ast.expr], Optional[ast.expr]]:
    elements = _constant_nodes(value)
    result = {}
    returns = None
    for index in range(0, len(elements) - 1, 2):
        key = elements[index]
        annotation = _annotation_expression(
            elements[index + 1],
            future_annotations,
        )
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if key.value == "return":
            returns = annotation
        else:
            result[key.value] = annotation
    return result, returns


def build_arguments311(code, defaults=None, kwdefaults=None, annotations=None):
    """Return ``(ast.arguments, return_annotation)`` for a 3.11 code object."""
    names = list(getattr(code, "co_varnames", ()))
    positional_count = int(getattr(code, "co_argcount", 0))
    posonly_count = int(getattr(code, "co_posonlyargcount", 0))
    kwonly_count = int(getattr(code, "co_kwonlyargcount", 0))
    flags = int(getattr(code, "co_flags", 0))

    annotation_map, returns = _annotations(
        annotations,
        bool(flags & __future__.annotations.compiler_flag),
    )

    positional = [
        ast.arg(arg=name, annotation=annotation_map.get(name))
        for name in names[:positional_count]
    ]
    posonlyargs = positional[:posonly_count]
    args = positional[posonly_count:]

    cursor = positional_count
    kwonly_names = names[cursor : cursor + kwonly_count]
    cursor += kwonly_count
    kwonlyargs = [
        ast.arg(arg=name, annotation=annotation_map.get(name))
        for name in kwonly_names
    ]

    vararg = None
    if flags & CO_VARARGS:
        name = names[cursor]
        cursor += 1
        vararg = ast.arg(arg=name, annotation=annotation_map.get(name))

    kwarg = None
    if flags & CO_VARKEYWORDS:
        name = names[cursor]
        kwarg = ast.arg(arg=name, annotation=annotation_map.get(name))

    positional_defaults = _constant_nodes(defaults)
    keyword_defaults = _keyword_defaults(kwdefaults)
    kw_defaults = [keyword_defaults.get(name) for name in kwonly_names]

    arguments = ast.arguments(
        posonlyargs=posonlyargs,
        args=args,
        vararg=vararg,
        kwonlyargs=kwonlyargs,
        kw_defaults=kw_defaults,
        kwarg=kwarg,
        defaults=positional_defaults,
    )
    return arguments, returns
