"""Core CPython 3.11 stack-to-AST parser.

The older decompyle3 parsers encode control flow in a large Spark grammar.
CPython 3.11 removed many of the protocol opcodes used by that grammar. This
module instead consumes normalized tokens and constructs Python's standard
``ast`` nodes. CFG, comprehension, suspension, and exception-table structure
recovery extend this core in separate modules.
"""

from __future__ import annotations

import ast
import __future__
import warnings
from dataclasses import dataclass, field, replace
from typing import Any, List, Optional, Tuple

from xdis import iscode
from xdis.version_info import PythonImplementation

from decompyle3.errors import (
    ControlFlowError,
    ParserError,
    SemanticGenerationError,
    add_error_context,
)
from decompyle3.ir import CallInfo, FunctionInfo
from decompyle3.scanner import get_scanner
from decompyle3.semantics.make_function311 import build_arguments311


CO_GENERATOR = 0x20
CO_COROUTINE = 0x80
CO_ASYNC_GENERATOR = 0x200


class Python311ParseError(ParserError):
    """Base error for a 3.11 token stream that cannot be recovered safely."""


class UnsupportedPython311ControlFlow(Python311ParseError, ControlFlowError):
    """Raised instead of emitting source for a not-yet-supported structure."""


@dataclass
class Python311ParseResult:
    """Parser result consumed by the 3.11 SourceWalker adapter."""

    kind: str
    tree: ast.AST
    source: str
    code: Any
    is_python311_result: bool = field(default=True, init=False)

    def __eq__(self, other):
        if isinstance(other, str):
            return self.kind == other
        return super(Python311ParseResult, self).__eq__(other)

    def __len__(self):
        if isinstance(self.tree, ast.Module):
            return len(self.tree.body)
        return 1


@dataclass
class _CodeValue:
    code: Any


@dataclass
class _FunctionValue:
    code: Any
    defaults: Optional[ast.expr] = None
    kwdefaults: Optional[ast.expr] = None
    annotations: Optional[ast.expr] = None
    closure: Optional[ast.expr] = None
    decorators: List[ast.expr] = field(default_factory=list)


@dataclass
class _ClassValue:
    name: str
    body_function: _FunctionValue
    bases: List[ast.expr]
    keywords: List[ast.keyword]
    decorators: List[ast.expr] = field(default_factory=list)


@dataclass(frozen=True)
class _BuildClassValue:
    pass


@dataclass(frozen=True)
class _NullValue:
    pass


@dataclass
class _ImportValue:
    module: str
    level: int
    fromlist: Tuple[str, ...]


@dataclass
class _ImportedName:
    owner: _ImportValue
    name: str


@dataclass
class _AugmentedValue:
    target: ast.expr
    operator: ast.operator
    value: ast.expr


@dataclass
class _UnpackGroup:
    value: ast.expr
    targets: List[Optional[ast.expr]]
    remaining: int


@dataclass
class _UnpackItem:
    group: _UnpackGroup
    index: int
    starred: bool = False


@dataclass
class _PendingBoolean:
    target: int
    operator: ast.boolop
    left: ast.expr


_BINARY_OPERATORS = {
    "BINARY_ADD": ast.Add,
    "BINARY_AND": ast.BitAnd,
    "BINARY_FLOOR_DIVIDE": ast.FloorDiv,
    "BINARY_LSHIFT": ast.LShift,
    "BINARY_MATRIX_MULTIPLY": ast.MatMult,
    "BINARY_MULTIPLY": ast.Mult,
    "BINARY_MODULO": ast.Mod,
    "BINARY_OR": ast.BitOr,
    "BINARY_POWER": ast.Pow,
    "BINARY_RSHIFT": ast.RShift,
    "BINARY_SUBTRACT": ast.Sub,
    "BINARY_TRUE_DIVIDE": ast.Div,
    "BINARY_XOR": ast.BitXor,
}

_INPLACE_OPERATORS = {
    name.replace("BINARY_", "INPLACE_"): operator
    for name, operator in _BINARY_OPERATORS.items()
}

_UNARY_OPERATORS = {
    "UNARY_POSITIVE": ast.UAdd,
    "UNARY_NEGATIVE": ast.USub,
    "UNARY_NOT": ast.Not,
    "UNARY_INVERT": ast.Invert,
}

_COMPARE_OPERATORS = {
    "COMPARE_LT": ast.Lt,
    "COMPARE_LE": ast.LtE,
    "COMPARE_EQ": ast.Eq,
    "COMPARE_NE": ast.NotEq,
    "COMPARE_GT": ast.Gt,
    "COMPARE_GE": ast.GtE,
    "CONTAINS": ast.In,
    "NOT_CONTAINS": ast.NotIn,
    "IS": ast.Is,
    "IS_NOT": ast.IsNot,
}

PARSER_INTERNAL_CONSUMERS = {
    "CACHE": "Scanner311 cache owner mapping",
    "RESUME": "_IGNORED_INTERNAL scope protocol",
    "EXTENDED_ARG": "_IGNORED_INTERNAL combined argument",
    "PUSH_NULL": "_NullValue consumed by CALL",
    "PRECALL": "CallInfo consumed by CALL",
    "KW_NAMES": "pending keyword names consumed by CALL",
    "MAKE_CELL": "_IGNORED_INTERNAL scope protocol",
    "COPY_FREE_VARS": "_IGNORED_INTERNAL scope protocol",
}
PARSER_INTERNAL_OPNAMES = frozenset(PARSER_INTERNAL_CONSUMERS)

_IGNORED_INTERNAL = {
    "INTERNAL_RESUME",
    "INTERNAL_EXTENDED_ARG",
    "MAKE_CELL",
    "COPY_FREE_VARS",
    "NOP",
}

_UNSUPPORTED_PREFIXES = (
    "MATCH_",
    "POP_JUMP_",
    "JUMP_BACKWARD",
)

_UNSUPPORTED_OPS = {
    "ASYNC_GEN_WRAP",
    "BEFORE_ASYNC_WITH",
    "BEFORE_WITH",
    "CHECK_EG_MATCH",
    "CHECK_EXC_MATCH",
    "END_ASYNC_FOR",
    "FOR_ITER",
    "GET_AITER",
    "GET_ANEXT",
    "GET_AWAITABLE",
    "GET_ITER",
    "JUMP_FORWARD",
    "LIST_APPEND",
    "MAP_ADD",
    "PUSH_EXC_INFO",
    "RERAISE",
    "RETURN_GENERATOR",
    "SEND",
    "SET_ADD",
    "WITH_EXCEPT_START",
    "YIELD_VALUE",
}


def _as_load(node: ast.expr) -> ast.expr:
    if isinstance(node, ast.Name):
        return ast.Name(id=node.id, ctx=ast.Load())
    if isinstance(node, ast.Attribute):
        return ast.Attribute(value=node.value, attr=node.attr, ctx=ast.Load())
    if isinstance(node, ast.Subscript):
        return ast.Subscript(value=node.value, slice=node.slice, ctx=ast.Load())
    return node


def _as_target(node: ast.expr, ctx=None) -> ast.expr:
    if ctx is None:
        ctx = ast.Store()
    if isinstance(node, ast.Name):
        return ast.Name(id=node.id, ctx=ctx)
    if isinstance(node, ast.Attribute):
        return ast.Attribute(value=node.value, attr=node.attr, ctx=ctx)
    if isinstance(node, ast.Subscript):
        return ast.Subscript(value=node.value, slice=node.slice, ctx=ctx)
    if isinstance(node, ast.Tuple):
        return ast.Tuple(
            elts=[_as_target(item, ctx) for item in node.elts],
            ctx=ctx,
        )
    if isinstance(node, ast.List):
        return ast.List(
            elts=[_as_target(item, ctx) for item in node.elts],
            ctx=ctx,
        )
    if isinstance(node, ast.Starred):
        return ast.Starred(value=_as_target(node.value, ctx), ctx=ctx)
    raise Python311ParseError(
        "CPython 3.11 bytecode produced an invalid assignment target "
        f"{type(node).__name__}"
    )


def _constant_value(node, default=None):
    if isinstance(node, ast.Constant):
        return node.value
    return default


def _new_decompiler311(code, tokens, compile_mode="exec", is_class_body=False):
    from decompyle3.controlflow.structures import StructuredDecompiler311

    return StructuredDecompiler311(
        code,
        tokens,
        compile_mode=compile_mode,
        is_class_body=is_class_body,
    )


class _StraightLineDecompiler:
    """Convert one normalized 3.11 token stream to Python AST statements."""

    def __init__(self, code, tokens, compile_mode="exec", is_class_body=False):
        self.code = code
        self.tokens = list(tokens)
        self.compile_mode = compile_mode
        self.is_class_body = is_class_body
        self.stack: List[Any] = []
        self.body: List[ast.stmt] = []
        self.pending_booleans: List[_PendingBoolean] = []
        self.pending_keywords: Tuple[str, ...] = ()
        self.pending_assignment_value: Optional[ast.expr] = None
        self.pending_assignment_targets: List[ast.expr] = []
        self.global_names = set()
        self.nonlocal_names = set()
        self.current_token = None

    def _error(self, message, error_type=Python311ParseError):
        token = self.current_token
        if token is not None:
            message = f"{message} (opcode {token.kind})"
        raise error_type(
            message,
            version=(3, 11),
            code_name=getattr(self.code, "co_name", "<unknown>"),
            offset=token.offset if token is not None else None,
        )

    def _validate_scope(self):
        if bytes(getattr(self.code, "co_exceptiontable", b"") or b""):
            self._error(
                "The code object has a CPython 3.11 exception table; "
                "exception and with-statement recovery belongs to phase 6",
                UnsupportedPython311ControlFlow,
            )

        for token in self.tokens:
            kind = token.kind
            if kind in _UNSUPPORTED_OPS or kind.startswith(_UNSUPPORTED_PREFIXES):
                self.current_token = token
                self._error(
                    "This control-flow opcode is outside the straight-line "
                    "phase-3 parser",
                    UnsupportedPython311ControlFlow,
                )

    def _pop(self):
        if not self.stack:
            self._error("Operand stack underflow")
        return self.stack.pop()

    def _pop_expr(self) -> ast.expr:
        value = self._pop()
        if not isinstance(value, ast.expr):
            self._error(
                "Expected an expression on the operand stack, found "
                f"{type(value).__name__}"
            )
        return value

    def _pop_many(self, count: int) -> List[Any]:
        if count < 0 or len(self.stack) < count:
            self._error(
                f"Operand stack needs {count} values, found {len(self.stack)}"
            )
        if count == 0:
            return []
        values = self.stack[-count:]
        del self.stack[-count:]
        return values

    def _pop_exprs(self, count: int) -> List[ast.expr]:
        values = self._pop_many(count)
        if not all(isinstance(value, ast.expr) for value in values):
            self._error("Expected only expressions in a variable-length operand")
        return values

    def _flush_assignment(self):
        if self.pending_assignment_value is None:
            return
        self.body.append(
            ast.Assign(
                targets=list(self.pending_assignment_targets),
                value=self.pending_assignment_value,
            )
        )
        self.pending_assignment_value = None
        self.pending_assignment_targets = []

    def _resolve_booleans(self, offset: int):
        while self.pending_booleans and self.pending_booleans[-1].target == offset:
            pending = self.pending_booleans.pop()
            right = self._pop_expr()
            if (
                isinstance(right, ast.BoolOp)
                and isinstance(right.op, type(pending.operator))
            ):
                values = [pending.left] + right.values
            else:
                values = [pending.left, right]
            self.stack.append(ast.BoolOp(op=pending.operator, values=values))

    def _name(self, token) -> ast.Name:
        name = token.attr if isinstance(token.attr, str) else token.pattr
        if not isinstance(name, str):
            self._error("Name opcode has no string operand")
        return ast.Name(id=name, ctx=ast.Load())

    def _load_const(self, token):
        value = token.attr
        if iscode(value):
            self.stack.append(_CodeValue(value))
        else:
            self.stack.append(ast.Constant(value=value))

    def _collection(self, kind: str, count: int):
        values = self._pop_exprs(count)
        if kind == "BUILD_TUPLE":
            node = ast.Tuple(elts=values, ctx=ast.Load())
        elif kind == "BUILD_LIST":
            node = ast.List(elts=values, ctx=ast.Load())
        elif kind == "BUILD_SET":
            node = ast.Set(elts=values)
        else:
            self._error(f"Unknown collection builder {kind}")
        self.stack.append(node)

    def _build_map(self, count: int):
        values = self._pop_exprs(count * 2)
        self.stack.append(
            ast.Dict(keys=values[0::2], values=values[1::2])
        )

    def _build_const_key_map(self, count: int):
        keys_node = self._pop_expr()
        values = self._pop_exprs(count)
        keys = _constant_value(keys_node)
        if not isinstance(keys, tuple) or len(keys) != count:
            self._error("BUILD_CONST_KEY_MAP does not reference matching keys")
        self.stack.append(
            ast.Dict(
                keys=[ast.Constant(value=key) for key in keys],
                values=values,
            )
        )

    def _sequence_extend(self, kind: str, depth: int):
        incoming = self._pop_expr()
        index = -max(depth, 1)
        if len(self.stack) < abs(index):
            self._error(f"{kind} references a missing container")
        container = self.stack[index]

        if kind == "LIST_EXTEND" and isinstance(container, ast.List):
            constant = _constant_value(incoming)
            if isinstance(constant, (tuple, list)):
                container.elts.extend(
                    ast.Constant(value=item) for item in constant
                )
            else:
                container.elts.append(
                    ast.Starred(value=incoming, ctx=ast.Load())
                )
            return

        if kind == "SET_UPDATE" and isinstance(container, ast.Set):
            constant = _constant_value(incoming)
            if isinstance(constant, (tuple, list, set, frozenset)):
                container.elts.extend(
                    ast.Constant(value=item) for item in constant
                )
            else:
                container.elts.append(
                    ast.Starred(value=incoming, ctx=ast.Load())
                )
            return

        self._error(f"{kind} does not target a compatible literal")

    def _dict_update(self, depth: int):
        incoming = self._pop_expr()
        index = -max(depth, 1)
        if len(self.stack) < abs(index):
            self._error("DICT_UPDATE/DICT_MERGE references a missing mapping")
        target = self.stack[index]
        if not isinstance(target, ast.Dict):
            target = ast.Dict(keys=[None], values=[target])
            self.stack[index] = target
        if isinstance(incoming, ast.Dict):
            target.keys.extend(incoming.keys)
            target.values.extend(incoming.values)
        else:
            target.keys.append(None)
            target.values.append(incoming)

    def _format_value(self, argument: int):
        format_spec = self._pop_expr() if argument & 0x04 else None
        value = self._pop_expr()
        conversion = {0: -1, 1: ord("s"), 2: ord("r"), 3: ord("a")}[
            argument & 0x03
        ]
        self.stack.append(
            ast.FormattedValue(
                value=value,
                conversion=conversion,
                format_spec=format_spec,
            )
        )

    def _build_string(self, count: int):
        values = self._pop_exprs(count)
        joined = []
        for value in values:
            if isinstance(value, ast.FormattedValue):
                joined.append(value)
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                joined.append(value)
            else:
                self._error("BUILD_STRING contains a non-string component")
        self.stack.append(ast.JoinedStr(values=joined))

    def _binary(self, kind: str):
        right = self._pop_expr()
        left = self._pop_expr()
        self.stack.append(
            ast.BinOp(left=left, op=_BINARY_OPERATORS[kind](), right=right)
        )

    def _inplace(self, kind: str):
        right = self._pop_expr()
        left = self._pop_expr()
        self.stack.append(
            _AugmentedValue(
                target=left,
                operator=_INPLACE_OPERATORS[kind](),
                value=right,
            )
        )

    def _compare(self, kind: str):
        right = self._pop_expr()
        left = self._pop_expr()
        self.stack.append(
            ast.Compare(
                left=left,
                ops=[_COMPARE_OPERATORS[kind]()],
                comparators=[right],
            )
        )

    def _slice(self, count: int):
        values = self._pop_exprs(count)
        lower = values[0]
        upper = values[1]
        step = values[2] if count == 3 else None
        self.stack.append(
            ast.Slice(
                lower=None if _constant_value(lower, ...) is None else lower,
                upper=None if _constant_value(upper, ...) is None else upper,
                step=(
                    None
                    if step is None or _constant_value(step, ...) is None
                    else step
                ),
            )
        )

    def _unpack(self, before: int, after: int):
        value = self._pop_expr()
        total = before if after < 0 else before + after + 1
        starred_index = before if after >= 0 else None
        group = _UnpackGroup(
            value=value,
            targets=[None] * total,
            remaining=total,
        )
        items = [
            _UnpackItem(
                group=group,
                index=index,
                starred=index == starred_index,
            )
            for index in range(total)
        ]
        self.stack.extend(reversed(items))

    def _store_unpack(self, item: _UnpackItem, target: ast.expr):
        if item.starred:
            target = ast.Starred(value=target, ctx=ast.Store())
        item.group.targets[item.index] = target
        item.group.remaining -= 1
        if item.group.remaining:
            return
        if any(target is None for target in item.group.targets):
            self._error("Incomplete unpacking assignment")
        tuple_target = ast.Tuple(
            elts=list(item.group.targets),
            ctx=ast.Store(),
        )
        self.body.append(ast.Assign(targets=[tuple_target], value=item.group.value))

    def _store_import(self, value, target: ast.expr):
        if not isinstance(target, ast.Name):
            self._error("An import target is not a name")
        if isinstance(value, _ImportValue):
            asname = target.id
            default_name = value.module.split(".")[0]
            if asname == default_name or asname == value.module:
                asname = None
            self.body.append(
                ast.Import(names=[ast.alias(name=value.module, asname=asname)])
            )
            return
        if isinstance(value, _ImportedName):
            asname = None if target.id == value.name else target.id
            self.body.append(
                ast.ImportFrom(
                    module=value.owner.module or None,
                    names=[ast.alias(name=value.name, asname=asname)],
                    level=value.owner.level,
                )
            )
            return
        self._error("Unknown import value")

    def _store_annotation(
        self,
        owner: ast.expr,
        key: ast.expr,
        annotation: ast.expr,
    ) -> bool:
        if (
            not isinstance(owner, ast.Name)
            or owner.id != "__annotations__"
            or not isinstance(key, ast.Constant)
            or not isinstance(key.value, str)
        ):
            return False

        if (
            int(getattr(self.code, "co_flags", 0))
            & __future__.annotations.compiler_flag
            and isinstance(annotation, ast.Constant)
            and isinstance(annotation.value, str)
        ):
            try:
                annotation = ast.parse(
                    annotation.value,
                    mode="eval",
                ).body
            except SyntaxError:
                self._error(
                    "Future annotation is not a valid expression"
                )

        assigned_value = None
        if self.body and isinstance(self.body[-1], ast.Assign):
            assignment = self.body[-1]
            if (
                len(assignment.targets) == 1
                and isinstance(assignment.targets[0], ast.Name)
                and assignment.targets[0].id == key.value
            ):
                assigned_value = assignment.value
                self.body.pop()

        self.body.append(
            ast.AnnAssign(
                target=ast.Name(id=key.value, ctx=ast.Store()),
                annotation=annotation,
                value=assigned_value,
                simple=1,
            )
        )
        return True

    def _import_star(self):
        value = self._pop()
        if not isinstance(value, _ImportValue):
            self._error("IMPORT_STAR has no owning IMPORT_NAME")
        if value.fromlist != ("*",):
            self._error("IMPORT_STAR owner does not request the '*' name")
        self.body.append(
            ast.ImportFrom(
                module=value.module or None,
                names=[ast.alias(name="*", asname=None)],
                level=value.level,
            )
        )

    def _make_function(self, token):
        info = token.attr
        if not isinstance(info, FunctionInfo):
            self._error("MAKE_FUNCTION has no normalized FunctionInfo")
        code_value = self._pop()
        if not isinstance(code_value, _CodeValue):
            self._error("MAKE_FUNCTION code operand is not a code object")

        closure = self._pop_expr() if info.has_closure else None
        annotations = self._pop_expr() if info.has_annotations else None
        kwdefaults = self._pop_expr() if info.has_kwdefaults else None
        defaults = self._pop_expr() if info.has_defaults else None
        self.stack.append(
            _FunctionValue(
                code=code_value.code,
                defaults=defaults,
                kwdefaults=kwdefaults,
                annotations=annotations,
                closure=closure,
            )
        )

    def _callable(self):
        callable_value = self._pop()
        if self.stack and isinstance(self.stack[-1], _NullValue):
            self.stack.pop()
        return callable_value

    @staticmethod
    def _call_ex_arguments(value: ast.expr) -> List[ast.expr]:
        if isinstance(value, (ast.Tuple, ast.List)):
            return list(value.elts)
        return [ast.Starred(value=value, ctx=ast.Load())]

    @staticmethod
    def _call_ex_keywords(value: ast.expr) -> List[ast.keyword]:
        if not isinstance(value, ast.Dict):
            return [ast.keyword(arg=None, value=value)]
        result = []
        pending_keys = []
        pending_values = []

        def flush_pending():
            if pending_keys:
                result.append(
                    ast.keyword(
                        arg=None,
                        value=ast.Dict(
                            keys=list(pending_keys),
                            values=list(pending_values),
                        ),
                    )
                )
                pending_keys[:] = []
                pending_values[:] = []

        for key, item in zip(value.keys, value.values):
            if key is None:
                flush_pending()
                result.append(ast.keyword(arg=None, value=item))
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                flush_pending()
                result.append(ast.keyword(arg=key.value, value=item))
            else:
                pending_keys.append(key)
                pending_values.append(item)
        flush_pending()
        return result

    def _decorate(self, callable_value, args, keywords):
        if keywords or len(args) != 1:
            return None
        decorated = args[0]
        if not isinstance(callable_value, ast.expr):
            return None
        if isinstance(decorated, _FunctionValue):
            return replace(
                decorated,
                decorators=[callable_value] + decorated.decorators,
            )
        if isinstance(decorated, _ClassValue):
            return replace(
                decorated,
                decorators=[callable_value] + decorated.decorators,
            )
        return None

    def _build_class(self, args, keywords):
        if len(args) < 2 or not isinstance(args[0], _FunctionValue):
            self._error("LOAD_BUILD_CLASS call has no class body function")
        name = _constant_value(args[1])
        if not isinstance(name, str):
            self._error("LOAD_BUILD_CLASS call has no class name")
        bases = args[2:]
        if not all(isinstance(base, ast.expr) for base in bases):
            self._error("Class bases contain a non-expression")
        return _ClassValue(
            name=name,
            body_function=args[0],
            bases=list(bases),
            keywords=keywords,
        )

    def _call(self, token):
        info = token.attr
        if not isinstance(info, CallInfo):
            self._error("CALL has no normalized CallInfo")

        if info.uses_ex:
            kwargs_value = self._pop_expr() if info.has_kwargs else None
            args_value = self._pop_expr()
            callable_value = self._callable()
            args = self._call_ex_arguments(args_value)
            keywords = (
                self._call_ex_keywords(kwargs_value)
                if kwargs_value is not None
                else []
            )
        else:
            argc = info.argc
            if argc is None:
                self._error("CALL has no argument count")
            values = self._pop_many(argc)
            hidden_argument = None
            if (
                not info.has_null
                and not info.is_method
                and len(self.stack) >= 2
            ):
                if (
                    isinstance(self.stack[-1], (_FunctionValue, _ClassValue))
                    and isinstance(self.stack[-2], ast.expr)
                ):
                    hidden_argument = self._pop()
                elif (
                    isinstance(self.stack[-1], ast.expr)
                    and isinstance(self.stack[-2], _FunctionValue)
                ):
                    hidden_argument = self._pop()
            callable_value = self._callable()
            keyword_count = len(info.keyword_names)
            positional_count = argc - keyword_count
            args = values[:positional_count]
            if hidden_argument is not None:
                args.insert(0, hidden_argument)
            keyword_values = values[positional_count:]
            keywords = [
                ast.keyword(arg=name, value=value)
                for name, value in zip(info.keyword_names, keyword_values)
            ]

        if isinstance(callable_value, _BuildClassValue):
            self.stack.append(self._build_class(args, keywords))
            return

        if isinstance(callable_value, _FunctionValue):
            from decompyle3.parsers.p311.comprehensions import (
                build_comprehension311,
                is_comprehension_code,
            )

            if is_comprehension_code(callable_value.code):
                if keywords or len(args) != 1:
                    self._error(
                        "Comprehension call does not have one hidden iterator"
                    )
                iterable = self._expression_value(args[0])
                self.stack.append(
                    build_comprehension311(self, callable_value, iterable)
                )
                return

        decorated = self._decorate(callable_value, args, keywords)
        if decorated is not None:
            self.stack.append(decorated)
            return

        args = [self._expression_value(value) for value in args]
        keywords = [
            ast.keyword(arg=keyword.arg, value=self._expression_value(keyword.value))
            for keyword in keywords
        ]
        if isinstance(callable_value, _FunctionValue):
            callable_value = self._expression_value(callable_value)
        if not isinstance(callable_value, ast.expr):
            self._error(
                "CALL target is not an expression: "
                f"{type(callable_value).__name__}"
            )
        self.stack.append(
            ast.Call(func=callable_value, args=list(args), keywords=keywords)
        )

    def _nested_tokens(self, code):
        scanner = get_scanner((3, 11), PythonImplementation.CPython)
        tokens, _ = scanner.ingest(code)
        return tokens

    def _function_node(self, value: _FunctionValue, name: str):
        code = value.code
        flags = int(getattr(code, "co_flags", 0))
        arguments, returns = build_arguments311(
            code,
            defaults=value.defaults,
            kwdefaults=value.kwdefaults,
            annotations=value.annotations,
        )
        nested = _new_decompiler311(
            code,
            self._nested_tokens(code),
            compile_mode="exec",
            is_class_body=False,
        )
        body = nested.decompile_body()
        if not body:
            body = [ast.Pass()]
        function_type = (
            ast.AsyncFunctionDef
            if flags & (CO_COROUTINE | CO_ASYNC_GENERATOR)
            else ast.FunctionDef
        )
        return function_type(
            name=name,
            args=arguments,
            body=body,
            decorator_list=list(value.decorators),
            returns=returns,
            type_comment=None,
        )

    def _lambda_node(self, value: _FunctionValue):
        arguments, _ = build_arguments311(
            value.code,
            defaults=value.defaults,
            kwdefaults=value.kwdefaults,
            annotations=value.annotations,
        )
        nested = _new_decompiler311(
            value.code,
            self._nested_tokens(value.code),
            compile_mode="lambda",
            is_class_body=False,
        )
        expression = nested.decompile_expression()
        return ast.Lambda(args=arguments, body=expression)

    def _expression_value(self, value):
        if isinstance(value, ast.expr):
            return value
        if (
            isinstance(value, _FunctionValue)
            and getattr(value.code, "co_name", None) == "<lambda>"
        ):
            return self._lambda_node(value)
        self._error(
            "Expected an expression, found parser-only value "
            f"{type(value).__name__}"
        )

    def _class_node(self, value: _ClassValue, name: str):
        class_name = name or value.name
        nested = _new_decompiler311(
            value.body_function.code,
            self._nested_tokens(value.body_function.code),
            compile_mode="exec",
            is_class_body=True,
        )
        body = nested.decompile_body()
        if not body:
            body = [ast.Pass()]
        return ast.ClassDef(
            name=class_name,
            bases=value.bases,
            keywords=value.keywords,
            body=body,
            decorator_list=list(value.decorators),
        )

    def _store_value(self, target: ast.expr, value):
        if isinstance(value, _UnpackItem):
            self._store_unpack(value, target)
            return
        if isinstance(value, (_ImportValue, _ImportedName)):
            self._store_import(value, target)
            return
        if isinstance(value, _FunctionValue):
            if not isinstance(target, ast.Name):
                self._error("A function definition is stored to a non-name target")
            if getattr(value.code, "co_name", None) == "<lambda>":
                expression = self._lambda_node(value)
                self._queue_assignment(target, expression)
            else:
                self.body.append(self._function_node(value, target.id))
            return
        if isinstance(value, _ClassValue):
            if not isinstance(target, ast.Name):
                self._error("A class definition is stored to a non-name target")
            self.body.append(self._class_node(value, target.id))
            return
        if isinstance(value, _AugmentedValue):
            self.body.append(
                ast.AugAssign(
                    target=target,
                    op=value.operator,
                    value=value.value,
                )
            )
            return
        if not isinstance(value, ast.expr):
            self._error(
                f"Cannot store parser-only value {type(value).__name__}"
            )
        self._queue_assignment(target, value)

    def _queue_assignment(self, target: ast.expr, value: ast.expr):
        if (
            self.pending_assignment_value is not None
            and self.pending_assignment_value is not value
        ):
            self._flush_assignment()
        if self.pending_assignment_value is None:
            self.pending_assignment_value = value
        self.pending_assignment_targets.append(target)

    def _store_name(self, token):
        value = self._pop()
        name = token.attr if isinstance(token.attr, str) else token.pattr
        if not isinstance(name, str):
            self._error("STORE name is not a string")

        if self.is_class_body and name in (
            "__module__",
            "__qualname__",
            "__classcell__",
        ):
            return
        if name == "__doc__" and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                self.body.append(ast.Expr(value=value))
                return

        if token.kind == "STORE_GLOBAL" and getattr(
            self.code, "co_name", "<module>"
        ) != "<module>":
            self.global_names.add(name)
        if token.kind == "STORE_DEREF" and name in tuple(
            getattr(self.code, "co_freevars", ())
        ):
            self.nonlocal_names.add(name)
        self._store_value(ast.Name(id=name, ctx=ast.Store()), value)

    def _delete_name(self, token):
        name = token.attr if isinstance(token.attr, str) else token.pattr
        if not isinstance(name, str):
            self._error("DELETE name is not a string")
        if token.kind == "DELETE_GLOBAL" and getattr(
            self.code,
            "co_name",
            "<module>",
        ) != "<module>":
            self.global_names.add(name)
        if token.kind == "DELETE_DEREF" and name in tuple(
            getattr(self.code, "co_freevars", ())
        ):
            self.nonlocal_names.add(name)
        self.body.append(
            ast.Delete(targets=[ast.Name(id=name, ctx=ast.Del())])
        )

    def _return(self):
        value = self._expression_value(self._pop())
        if self.compile_mode in ("eval", "expr", "lambda"):
            self.body.append(ast.Return(value=value))
            return
        if self.is_class_body:
            return
        if isinstance(value, ast.Constant) and value.value is None:
            return
        self.body.append(ast.Return(value=value))

    def _raise(self, count: int):
        if count == 0:
            self.body.append(ast.Raise(exc=None, cause=None))
        elif count == 1:
            self.body.append(ast.Raise(exc=self._pop_expr(), cause=None))
        elif count == 2:
            cause = self._pop_expr()
            exc = self._pop_expr()
            self.body.append(ast.Raise(exc=exc, cause=cause))
        else:
            self._error(f"Invalid RAISE_VARARGS count {count}")

    def _import_name(self, token):
        fromlist_node = self._pop_expr()
        level_node = self._pop_expr()
        fromlist = _constant_value(fromlist_node, ())
        if fromlist is None:
            fromlist = ()
        if not isinstance(fromlist, tuple):
            self._error("IMPORT_NAME fromlist is not a tuple")
        level = _constant_value(level_node)
        if not isinstance(level, int):
            self._error("IMPORT_NAME level is not an integer")
        module = token.attr if isinstance(token.attr, str) else token.pattr
        self.stack.append(
            _ImportValue(module=module, level=level, fromlist=fromlist)
        )

    def _dispatch(self, token):
        kind = token.kind
        argument = token.attr if isinstance(token.attr, int) else 0

        if kind in _IGNORED_INTERNAL:
            return
        if kind == "PUSH_NULL":
            self.stack.append(_NullValue())
        elif kind == "LOAD_CONST":
            self._load_const(token)
        elif kind in (
            "LOAD_NAME",
            "LOAD_FAST",
            "LOAD_GLOBAL",
            "LOAD_DEREF",
            "LOAD_CLASSDEREF",
            "LOAD_CLOSURE",
        ):
            self.stack.append(self._name(token))
        elif kind in ("STORE_NAME", "STORE_FAST", "STORE_GLOBAL", "STORE_DEREF"):
            self._store_name(token)
        elif kind in ("DELETE_NAME", "DELETE_FAST", "DELETE_GLOBAL", "DELETE_DEREF"):
            self._delete_name(token)
        elif kind in ("LOAD_ATTR", "LOAD_METHOD"):
            owner = self._pop_expr()
            name = token.attr if isinstance(token.attr, str) else token.pattr
            self.stack.append(
                ast.Attribute(value=owner, attr=name, ctx=ast.Load())
            )
        elif kind == "STORE_ATTR":
            owner = self._pop_expr()
            value = self._pop()
            name = token.attr if isinstance(token.attr, str) else token.pattr
            target = ast.Attribute(value=owner, attr=name, ctx=ast.Store())
            self._store_value(target, value)
        elif kind == "DELETE_ATTR":
            owner = self._pop_expr()
            name = token.attr if isinstance(token.attr, str) else token.pattr
            self.body.append(
                ast.Delete(
                    targets=[
                        ast.Attribute(value=owner, attr=name, ctx=ast.Del())
                    ]
                )
            )
        elif kind == "BINARY_SUBSCR":
            index = self._pop_expr()
            owner = self._pop_expr()
            self.stack.append(
                ast.Subscript(value=owner, slice=index, ctx=ast.Load())
            )
        elif kind == "STORE_SUBSCR":
            index = self._pop_expr()
            owner = self._pop_expr()
            value = self._pop()
            if not (
                isinstance(value, ast.expr)
                and self._store_annotation(owner, index, value)
            ):
                target = ast.Subscript(
                    value=owner,
                    slice=index,
                    ctx=ast.Store(),
                )
                self._store_value(target, value)
        elif kind == "DELETE_SUBSCR":
            index = self._pop_expr()
            owner = self._pop_expr()
            self.body.append(
                ast.Delete(
                    targets=[
                        ast.Subscript(value=owner, slice=index, ctx=ast.Del())
                    ]
                )
            )
        elif kind in ("BUILD_TUPLE", "BUILD_LIST", "BUILD_SET"):
            self._collection(kind, argument)
        elif kind == "BUILD_MAP":
            self._build_map(argument)
        elif kind == "BUILD_CONST_KEY_MAP":
            self._build_const_key_map(argument)
        elif kind in ("LIST_EXTEND", "SET_UPDATE"):
            self._sequence_extend(kind, argument)
        elif kind in ("DICT_UPDATE", "DICT_MERGE"):
            self._dict_update(argument)
        elif kind == "LIST_TO_TUPLE":
            value = self._pop_expr()
            if not isinstance(value, ast.List):
                self._error("LIST_TO_TUPLE operand is not a list builder")
            self.stack.append(ast.Tuple(elts=value.elts, ctx=ast.Load()))
        elif kind == "BUILD_SLICE":
            self._slice(argument)
        elif kind == "FORMAT_VALUE":
            self._format_value(argument)
        elif kind == "BUILD_STRING":
            self._build_string(argument)
        elif kind in _UNARY_OPERATORS:
            self.stack.append(
                ast.UnaryOp(op=_UNARY_OPERATORS[kind](), operand=self._pop_expr())
            )
        elif kind in _BINARY_OPERATORS:
            self._binary(kind)
        elif kind in _INPLACE_OPERATORS:
            self._inplace(kind)
        elif kind in _COMPARE_OPERATORS:
            self._compare(kind)
        elif kind == "COPY_STACK":
            depth = argument
            if depth <= 0 or len(self.stack) < depth:
                self._error(f"COPY_STACK depth {depth} is invalid")
            self.stack.append(self.stack[-depth])
        elif kind == "SWAP_STACK":
            depth = argument
            if depth <= 0 or len(self.stack) < depth:
                self._error(f"SWAP_STACK depth {depth} is invalid")
            self.stack[-1], self.stack[-depth] = (
                self.stack[-depth],
                self.stack[-1],
            )
        elif kind == "UNPACK_SEQUENCE":
            self._unpack(argument, -1)
        elif kind == "UNPACK_EX":
            self._unpack(argument & 0xFF, argument >> 8)
        elif kind == "KW_NAMES":
            names = token.attr
            if not isinstance(names, tuple):
                self._error("KW_NAMES is not a tuple")
            self.pending_keywords = names
        elif kind in ("GET_ITER", "GET_AITER"):
            return
        elif kind == "PRECALL":
            return
        elif kind == "CALL":
            self._call(token)
            self.pending_keywords = ()
        elif kind == "LOAD_BUILD_CLASS":
            self.stack.append(_BuildClassValue())
        elif kind == "MAKE_FUNCTION":
            self._make_function(token)
        elif kind == "IMPORT_NAME":
            self._import_name(token)
        elif kind == "IMPORT_FROM":
            if not self.stack or not isinstance(self.stack[-1], _ImportValue):
                self._error("IMPORT_FROM has no owning IMPORT_NAME")
            name = token.attr if isinstance(token.attr, str) else token.pattr
            self.stack.append(_ImportedName(self.stack[-1], name))
        elif kind == "IMPORT_STAR":
            self._import_star()
        elif kind == "SETUP_ANNOTATIONS":
            return
        elif kind == "POP_TOP":
            value = self._pop()
            if isinstance(value, _ImportValue):
                return
            if not isinstance(value, ast.expr):
                self._error("POP_TOP operand is not an expression")
            self.body.append(ast.Expr(value=value))
        elif kind == "PRINT_EXPR":
            self.body.append(ast.Expr(value=self._pop_expr()))
        elif kind == "RETURN_VALUE":
            self._return()
        elif kind == "RAISE_VARARGS":
            self._raise(argument)
        elif kind in ("JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP"):
            target = token.attr
            if not isinstance(target, int):
                self._error("Short-circuit jump has no absolute target")
            operator = (
                ast.And()
                if kind == "JUMP_IF_FALSE_OR_POP"
                else ast.Or()
            )
            self.pending_booleans.append(
                _PendingBoolean(
                    target=target,
                    operator=operator,
                    left=self._pop_expr(),
                )
            )
        else:
            self._error(f"Unsupported phase-3 opcode {kind}")

    def _prepend_scope_declarations(self):
        declarations = []
        if self.global_names:
            declarations.append(ast.Global(names=sorted(self.global_names)))
        if self.nonlocal_names:
            declarations.append(ast.Nonlocal(names=sorted(self.nonlocal_names)))
        if declarations:
            docstring_count = int(
                bool(
                    self.body
                    and isinstance(self.body[0], ast.Expr)
                    and isinstance(self.body[0].value, ast.Constant)
                    and isinstance(self.body[0].value.value, str)
                )
            )
            self.body[docstring_count:docstring_count] = declarations

    def _inject_function_docstring(self):
        constants = tuple(getattr(self.code, "co_consts", ()))
        if not constants or not isinstance(constants[0], str):
            return
        if (
            self.body
            and isinstance(self.body[0], ast.Expr)
            and isinstance(self.body[0].value, ast.Constant)
            and self.body[0].value.value == constants[0]
        ):
            return
        self.body.insert(0, ast.Expr(value=ast.Constant(value=constants[0])))

    def decompile_body(self) -> List[ast.stmt]:
        self._validate_scope()
        store_kinds = {
            "STORE_NAME",
            "STORE_FAST",
            "STORE_GLOBAL",
            "STORE_DEREF",
            "STORE_ATTR",
            "STORE_SUBSCR",
        }
        for token in self.tokens:
            self.current_token = token
            self._resolve_booleans(token.offset)
            if token.kind not in store_kinds:
                self._flush_assignment()
            self._dispatch(token)

        self._flush_assignment()
        if self.pending_booleans:
            pending = self.pending_booleans[-1]
            self._error(
                f"Unresolved short-circuit expression targeting {pending.target}"
            )
        if self.stack and not all(
            isinstance(value, _NullValue) for value in self.stack
        ):
            self._error(
                "Straight-line parse ended with values on the operand stack"
            )

        if (
            getattr(self.code, "co_name", "<module>") != "<module>"
            and not self.is_class_body
        ):
            self._inject_function_docstring()
        self._prepend_scope_declarations()
        return self.body

    def decompile_expression(self) -> ast.expr:
        body = self.decompile_body()
        returns = [statement for statement in body if isinstance(statement, ast.Return)]
        other = [
            statement
            for statement in body
            if not isinstance(statement, (ast.Return, ast.Global, ast.Nonlocal))
            and not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ]
        if len(returns) != 1 or other:
            self._error(
                "Expression/lambda mode did not reduce to one returned expression"
            )
        if returns[0].value is None:
            self._error("Expression/lambda mode returned no value")
        return returns[0].value


class Python311BaseParser:
    """Small parser facade matching the public decompyle3 parser contract."""

    def __init__(
        self,
        start_symbol="stmts",
        debug_parser=None,
        compile_mode="exec",
    ):
        self.start_symbol = start_symbol
        self.compile_mode = compile_mode
        self.debug = debug_parser or {}
        self.is_lambda = compile_mode == "lambda"
        self.version = (3, 11)
        self.insts = []
        self.offset2inst_index = {}
        self.opc = None
        self.code_object = None
        self.seen_ops = frozenset()

    def customize_grammar_rules(self, tokens, customize):
        """Record the exact 3.11 vocabulary; no legacy grammar is inherited."""
        self.seen_ops = frozenset(token.kind for token in tokens)

    def parse(self, tokens):
        if self.code_object is None:
            raise Python311ParseError(
                "Parser311 requires the active code object from SourceWalker",
                version=(3, 11),
                code_name="<unknown>",
            )
        try:
            decompiler = _new_decompiler311(
                self.code_object,
                tokens,
                compile_mode=self.compile_mode,
            )

            if self.compile_mode in ("eval", "expr"):
                expression = decompiler.decompile_expression()
                tree = ast.Expression(body=expression)
                kind = "expr_start"
            elif self.compile_mode == "lambda":
                value = _FunctionValue(code=self.code_object)
                expression = decompiler._lambda_node(value)
                tree = ast.Expression(body=expression)
                kind = "lambda_start"
            else:
                body = decompiler.decompile_body()
                tree = ast.Module(body=body, type_ignores=[])
                kind = (
                    "single_start"
                    if self.compile_mode == "single"
                    else "stmts"
                )

            tree = ast.fix_missing_locations(tree)
            try:
                unparse = ast.unparse
            except AttributeError as error:
                raise SemanticGenerationError(
                    "Parser311 source generation requires ast.unparse",
                    version=(3, 11),
                    code_name=self.code_object.co_name,
                ) from error
            source = unparse(tree)
            validation_mode = (
                "eval"
                if isinstance(tree, ast.Expression)
                else (
                    "single"
                    if self.compile_mode == "single"
                    else "exec"
                )
            )
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", SyntaxWarning)
                    if (
                        validation_mode == "exec"
                        and self.code_object.co_name != "<module>"
                    ):
                        # A function code object is represented as a module
                        # containing its body so SourceWalker can render the
                        # statements directly. Validate those statements in
                        # their original function scope: compiling the raw
                        # module would reject legitimate return/yield/await
                        # statements as being outside a function.
                        parsed_source = ast.parse(
                            source,
                            "<decompyle3-3.11-validation>",
                            mode="exec",
                        )
                        function_type = (
                            ast.AsyncFunctionDef
                            if self.code_object.co_flags
                            & (CO_COROUTINE | CO_ASYNC_GENERATOR)
                            else ast.FunctionDef
                        )
                        inner_function = function_type(
                            name="__decompyle3_validated_code__",
                            args=ast.arguments(
                                posonlyargs=[],
                                args=[],
                                vararg=None,
                                kwonlyargs=[],
                                kw_defaults=[],
                                kwarg=None,
                                defaults=[],
                            ),
                            body=parsed_source.body or [ast.Pass()],
                            decorator_list=[],
                            returns=None,
                            type_comment=None,
                        )
                        closure_names = set(
                            self.code_object.co_freevars
                        )
                        for node in ast.walk(parsed_source):
                            if isinstance(node, ast.Nonlocal):
                                closure_names.update(node.names)
                        closure_setup = [
                            ast.Assign(
                                targets=[
                                    ast.Name(id=name, ctx=ast.Store())
                                ],
                                value=ast.Constant(value=None),
                            )
                            for name in sorted(closure_names)
                        ]
                        validation_tree = ast.Module(
                            body=[
                                ast.FunctionDef(
                                    name="__decompyle3_validation_scope__",
                                    args=ast.arguments(
                                        posonlyargs=[],
                                        args=[],
                                        vararg=None,
                                        kwonlyargs=[],
                                        kw_defaults=[],
                                        kwarg=None,
                                        defaults=[],
                                    ),
                                    body=closure_setup + [inner_function],
                                    decorator_list=[],
                                    returns=None,
                                    type_comment=None,
                                )
                            ],
                            type_ignores=[],
                        )
                        compile(
                            ast.fix_missing_locations(validation_tree),
                            "<decompyle3-3.11-validation>",
                            "exec",
                            dont_inherit=True,
                        )
                    else:
                        compile(
                            source,
                            "<decompyle3-3.11-validation>",
                            validation_mode,
                            dont_inherit=True,
                        )
            except (SyntaxError, SyntaxWarning) as error:
                raise SemanticGenerationError(
                    "Parser311 generated source that does not recompile "
                    "cleanly",
                    version=(3, 11),
                    code_name=self.code_object.co_name,
                ) from error
            self.cfg = decompiler.cfg
            self.control_flow = decompiler.control_flow
            if self.debug.get("cfg", False):
                print(self.cfg.format())
            return Python311ParseResult(
                kind=kind,
                tree=tree,
                source=source,
                code=self.code_object,
            )
        except (ParserError, ControlFlowError, SemanticGenerationError) as error:
            add_error_context(
                error,
                version=(3, 11),
                code_name=self.code_object.co_name,
            )
            raise
        except RecursionError as error:
            raise Python311ParseError(
                "Parser311 recursion limit reached while structuring "
                "control flow",
                version=(3, 11),
                code_name=self.code_object.co_name,
            ) from error
