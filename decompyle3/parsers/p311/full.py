"""Full straight-line CPython 3.11 parser."""

from decompyle3.parsers.p311.base import Python311BaseParser
from decompyle3.parsers.p311.full_custom import Python311FullCustom


class Python311Parser(Python311FullCustom, Python311BaseParser):
    """Parser for phase-3 ``exec``/``single`` code objects."""

    def __init__(self, start_symbol="stmts", debug_parser=None, compile_mode="exec"):
        Python311BaseParser.__init__(
            self,
            start_symbol=start_symbol,
            debug_parser=debug_parser,
            compile_mode=compile_mode,
        )
