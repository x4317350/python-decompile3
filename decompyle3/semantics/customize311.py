"""SourceWalker adapter for the CPython 3.11 standard-AST parser."""

import ast

import decompyle3.parsers.main as python_parser
from decompyle3.semantics.pysource import SourceWalker


class Python311SourceWalker(SourceWalker):
    """Reuse SourceWalker's output/debug contract for standard-AST results."""

    def build_ast(
        self,
        tokens,
        customize,
        code,
        is_lambda=False,
        noneInNames=False,
        is_top_level_module=False,
    ):
        self.p.code_object = code
        result = python_parser.parse(
            self.p,
            tokens,
            customize,
            is_lambda=is_lambda,
        )
        if self.showast.get("before", False) or self.showast.get("after", False):
            self.println(ast.dump(result.tree, indent=2))
        return result

    def gen_source(
        self,
        tree,
        name,
        customize,
        is_lambda=False,
        returnNone=False,
        debug_opts=None,
    ):
        if not getattr(tree, "is_python311_result", False):
            return SourceWalker.gen_source(
                self,
                tree,
                name,
                customize,
                is_lambda=is_lambda,
                returnNone=returnNone,
                debug_opts=debug_opts,
            )
        self.name = name
        self.println(tree.source)
