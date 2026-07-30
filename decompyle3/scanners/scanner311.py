# Copyright (c) 2026
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Raw CPython 3.11 bytecode scanner.

This scanner preserves the physical instruction stream, including CACHE and
EXTENDED_ARG entries, and exposes a separate normalized parser-facing stream.
"""

from __future__ import annotations

from array import array
from typing import Iterable, Iterator, Tuple

from xdis import Bytecode, iscode
from xdis.instruction import Instruction

from decompyle3.scanner import (
    BytecodeScanError,
    MalformedBytecodeError,
    Scanner,
    UnknownOpcodeError,
)
from decompyle3.errors import add_error_context
from decompyle3.scanners.normalize311 import Normalizer311


class Scanner311(Scanner):
    """Load and tokenize an unnormalized CPython 3.11 instruction stream."""

    _METADATA_FIELDS = (
        "co_name",
        "co_qualname",
        "co_filename",
        "co_firstlineno",
        "co_flags",
        "co_argcount",
        "co_posonlyargcount",
        "co_kwonlyargcount",
        "co_nlocals",
        "co_stacksize",
        "co_varnames",
        "co_names",
        "co_freevars",
        "co_cellvars",
    )

    def __init__(self, show_asm=None):
        # Scanner37Base assumes opcodes which CPython 3.11 removed. Keep raw
        # ingestion independent by initializing the common Scanner directly.
        super(Scanner311, self).__init__((3, 11), show_asm, is_pypy=False)
        self.insts = []
        self.offset2inst_index = {}
        self.code_objects = ()
        self.nested_code_objects = ()
        self.exception_table = b""
        self.exception_entries = ()
        self.positions = ()
        self.positions_by_offset = {}
        self.line_ranges = ()
        self.code_metadata = {}
        self.raw_tokens = []
        self.normalized_instructions = ()
        self.physical_to_logical = {}
        self.logical_to_physical = {}
        self.cache_owner = {}
        self.stack_depths = {}
        self.max_stack_depth = 0

    def _validate_bytecode(self, bytecode: bytes, code_name: str = "<unknown>") -> None:
        """Validate the fixed-width 3.11 physical instruction layout."""
        if not isinstance(bytecode, (bytes, bytearray, memoryview)):
            raise MalformedBytecodeError(
                "CPython 3.11 code object has a non-bytes co_code",
                version=(3, 11),
                code_name=code_name,
            )

        if len(bytecode) % 2:
            raise MalformedBytecodeError(
                f"CPython 3.11 code object has an odd co_code length "
                f"({len(bytecode)} bytes)",
                version=(3, 11),
                code_name=code_name,
            )

        for offset in range(0, len(bytecode), 2):
            opcode = bytecode[offset]
            try:
                opname = self.opname[opcode]
            except IndexError as error:
                raise UnknownOpcodeError(
                    f"Unknown CPython 3.11 opcode {opcode} at offset {offset}",
                    version=(3, 11),
                    code_name=code_name,
                    offset=offset,
                ) from error
            if opname.startswith("<") and opname.endswith(">"):
                raise UnknownOpcodeError(
                    f"Unknown CPython 3.11 opcode {opcode} at offset {offset}",
                    version=(3, 11),
                    code_name=code_name,
                    offset=offset,
                )

    @staticmethod
    def iter_code_objects(co) -> Iterator[object]:
        """Yield a code object and every code object nested in its constants."""
        if not iscode(co):
            raise MalformedBytecodeError(
                f"Scanner311 expected a code object, got {type(co).__name__}",
                version=(3, 11),
                code_name="<unknown>",
            )

        pending = [co]
        seen = set()
        while pending:
            current = pending.pop()
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            yield current
            children = [
                constant
                for constant in getattr(current, "co_consts", ())
                if iscode(constant)
            ]
            pending.extend(reversed(children))

    @staticmethod
    def _read_positions(co) -> Tuple[tuple, ...]:
        positions = getattr(co, "co_positions", None)
        if not callable(positions):
            return ()
        return tuple(positions())

    @staticmethod
    def _read_line_ranges(co) -> Tuple[tuple, ...]:
        lines = getattr(co, "co_lines", None)
        if not callable(lines):
            return ()
        return tuple(lines())

    def _attach_positions(
        self, instructions: Iterable[Instruction]
    ) -> Tuple[Instruction, ...]:
        attached = []
        for instruction in instructions:
            position = self.positions_by_offset.get(instruction.offset)
            attached.append(
                instruction._replace(positions=position, inst_size=2)
            )
        return tuple(attached)

    def ingest_raw(self, co, classname=None, code_objects=None, show_asm=None):
        """Return physical 3.11 Tokens, including CACHE and EXTENDED_ARG."""
        if not iscode(co):
            raise MalformedBytecodeError(
                f"Scanner311 expected a code object, got {type(co).__name__}",
                version=(3, 11),
                code_name="<unknown>",
            )

        code_name = getattr(co, "co_name", "<unknown>")
        try:
            raw_code = bytes(co.co_code)
        except (AttributeError, TypeError, ValueError) as error:
            raise MalformedBytecodeError(
                "CPython 3.11 code object has invalid co_code",
                version=(3, 11),
                code_name=code_name,
            ) from error
        self._validate_bytecode(raw_code, code_name)

        self.code_object = co
        self.classname = classname
        self.loaded_code_objects = code_objects
        self.code = array("B", raw_code)
        self.raw_opargs = {
            offset: raw_code[offset + 1] for offset in range(0, len(raw_code), 2)
        }

        self.positions = self._read_positions(co)
        self.positions_by_offset = {
            index * 2: position for index, position in enumerate(self.positions)
        }
        self.line_ranges = self._read_line_ranges(co)
        self.code_metadata = {
            field: getattr(co, field)
            for field in self._METADATA_FIELDS
            if hasattr(co, field)
        }
        self.code_metadata["code_length"] = len(raw_code)

        self.code_objects = tuple(self.iter_code_objects(co))
        self.nested_code_objects = self.code_objects[1:]
        self.exception_table = bytes(getattr(co, "co_exceptiontable", b""))

        try:
            bytecode = Bytecode(co, self.opc)
            self.insts = list(self._attach_positions(bytecode))
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise MalformedBytecodeError(
                f"Unable to decode CPython 3.11 code object: {error}",
                version=(3, 11),
                code_name=code_name,
            ) from error

        expected_offsets = list(range(0, len(raw_code), 2))
        actual_offsets = [instruction.offset for instruction in self.insts]
        if actual_offsets != expected_offsets:
            raise MalformedBytecodeError(
                "CPython 3.11 code object did not decode into one instruction "
                "per two-byte code unit",
                version=(3, 11),
                code_name=code_name,
            )

        for instruction in self.insts:
            if instruction.opcode != raw_code[instruction.offset]:
                raise MalformedBytecodeError(
                    f"Decoded opcode mismatch at offset {instruction.offset}",
                    version=(3, 11),
                    code_name=code_name,
                    offset=instruction.offset,
                )

        self.offset2inst_index = {
            instruction.offset: index
            for index, instruction in enumerate(self.insts)
        }
        self.build_prev_op()
        self.lines = self.build_lines_data(co)
        self.exception_entries = tuple(bytecode.exception_entries or ())

        tokens = [
            self.Token(
                opname=instruction.opname,
                attr=instruction.argval,
                pattr=instruction.argrepr,
                offset=instruction.offset,
                linestart=instruction.starts_line,
                op=instruction.opcode,
                has_arg=instruction.has_arg,
                opc=self.opc,
                # EXTENDED_ARG is retained as its own physical token, so the
                # following token keeps its real integer offset.
                has_extended_arg=False,
                tos_str=instruction.tos_str,
                start_offset=instruction.start_offset,
                optype=instruction.optype,
            )
            for instruction in self.insts
        ]

        if show_asm is None:
            show_asm = self.show_asm
        if show_asm is True or show_asm in ("both", "before", "after"):
            print("\n# ---- raw CPython 3.11 tokenization:")
            for token in tokens:
                print(token.format(line_prefix=""))
            print()

        self.raw_tokens = tokens
        return tokens, {}

    def _normalized_tokens(self):
        tokens = []
        for instruction in self.normalized_instructions:
            if instruction.call is not None:
                attr = instruction.call
                pattr = repr(instruction.call)
            elif instruction.function is not None:
                attr = instruction.function
                pattr = repr(instruction.function)
            else:
                attr = instruction.argval
                pattr = instruction.argrepr
            tokens.append(
                self.Token(
                    opname=instruction.kind,
                    attr=attr,
                    pattr=pattr,
                    offset=instruction.offset,
                    linestart=self.insts[
                        self.offset2inst_index[instruction.offset]
                    ].starts_line,
                    op=instruction.original_opcode,
                    has_arg=instruction.arg is not None,
                    opc=self.opc,
                    has_extended_arg=False,
                    optype=self.insts[
                        self.offset2inst_index[instruction.offset]
                    ].optype,
                )
            )
        return tokens

    def _normalize(self, co, source_kind, instructions):
        normalizer = Normalizer311(self.opc)
        try:
            self.normalized_instructions = normalizer.normalize(
                instructions,
                co,
                exception_entries=self.exception_entries,
                source_kind=source_kind,
            )
        except BytecodeScanError as error:
            add_error_context(
                error,
                version=(3, 11),
                code_name=getattr(co, "co_name", "<unknown>"),
            )
            raise
        self.physical_to_logical = normalizer.physical_to_logical
        self.logical_to_physical = normalizer.logical_to_physical
        self.cache_owner = normalizer.cache_owner
        self.stack_depths = dict(normalizer.stack_analysis.depths)
        self.max_stack_depth = normalizer.stack_analysis.max_depth
        return self._normalized_tokens()

    def ingest(self, co, classname=None, code_objects=None, show_asm=None):
        """Return normalized parser-facing 3.11 Tokens without CACHE entries."""
        if show_asm is None:
            show_asm = self.show_asm
        raw_show = show_asm if show_asm is True or show_asm in ("both", "before") else None
        self.ingest_raw(
            co,
            classname=classname,
            code_objects=code_objects,
            show_asm=raw_show,
        )
        tokens = self._normalize(co, "pyc", self.insts)

        if show_asm is True or show_asm in ("both", "after"):
            print("\n# ---- normalized CPython 3.11 tokenization:")
            for token in tokens:
                print(token.format(line_prefix=""))
            print()
        return tokens, {}

    def ingest_runtime(self, co, classname=None, show_asm=None):
        """Normalize a live CPython 3.11 adaptive instruction stream."""
        self.ingest_raw(co, classname=classname, show_asm=None)
        normalizer = Normalizer311(self.opc)
        runtime_instructions = normalizer.runtime_instructions(co)
        try:
            self.normalized_instructions = normalizer.normalize(
                runtime_instructions,
                co,
                exception_entries=self.exception_entries,
                source_kind="runtime",
            )
        except BytecodeScanError as error:
            add_error_context(
                error,
                version=(3, 11),
                code_name=getattr(co, "co_name", "<unknown>"),
            )
            raise
        self.physical_to_logical = normalizer.physical_to_logical
        self.logical_to_physical = normalizer.logical_to_physical
        self.cache_owner = normalizer.cache_owner
        self.stack_depths = dict(normalizer.stack_analysis.depths)
        self.max_stack_depth = normalizer.stack_analysis.max_depth
        tokens = self._normalized_tokens()

        if show_asm is True or show_asm in ("both", "after"):
            print("\n# ---- normalized adaptive CPython 3.11 tokenization:")
            for token in tokens:
                print(token.format(line_prefix=""))
            print()
        return tokens, {}
