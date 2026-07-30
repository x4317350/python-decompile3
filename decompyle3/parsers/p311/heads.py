"""Compile-mode entry points for the CPython 3.11 parser."""

from decompyle3.parsers.p311.full import Python311Parser
from decompyle3.parsers.p311.lambda_expr import Python311LambdaParser


class Python311ParserExec(Python311Parser):
    def __init__(self, debug_parser=None):
        Python311Parser.__init__(
            self,
            start_symbol="stmts",
            debug_parser=debug_parser,
            compile_mode="exec",
        )


class Python311ParserSingle(Python311Parser):
    def __init__(self, debug_parser=None):
        Python311Parser.__init__(
            self,
            start_symbol="single_start",
            debug_parser=debug_parser,
            compile_mode="single",
        )


class Python311ParserEval(Python311LambdaParser):
    def __init__(self, debug_parser=None):
        Python311LambdaParser.__init__(
            self,
            start_symbol="expr_start",
            debug_parser=debug_parser,
            compile_mode="eval",
        )


class Python311ParserExpr(Python311LambdaParser):
    def __init__(self, debug_parser=None):
        Python311LambdaParser.__init__(
            self,
            start_symbol="expr_start",
            debug_parser=debug_parser,
            compile_mode="expr",
        )


class Python311ParserLambda(Python311LambdaParser):
    def __init__(self, debug_parser=None):
        Python311LambdaParser.__init__(
            self,
            start_symbol="lambda_start",
            debug_parser=debug_parser,
            compile_mode="lambda",
        )
