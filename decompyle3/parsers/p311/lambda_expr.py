"""Expression and lambda parser variants for CPython 3.11."""

from decompyle3.parsers.p311.base import Python311BaseParser


class Python311LambdaParser(Python311BaseParser):
    def __init__(
        self,
        start_symbol="lambda_start",
        debug_parser=None,
        compile_mode="lambda",
    ):
        Python311BaseParser.__init__(
            self,
            start_symbol=start_symbol,
            debug_parser=debug_parser,
            compile_mode=compile_mode,
        )
