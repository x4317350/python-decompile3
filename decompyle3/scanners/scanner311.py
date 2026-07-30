# Copyright (c) 2026
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Raw CPython 3.11 bytecode scanner.

This scanner intentionally preserves the physical instruction stream,
including CACHE and EXTENDED_ARG entries. Normalization for the parser belongs
to the next implementation phase.
"""

from __future__ import annotations

from array import array
from typing import Iterable, Iterator, Tuple

from xdis import Bytecode, iscode
from xdis.instruction import Instruction

from decompyle3.scanner import (
    MalformedBytecodeError,
    Scanner,
    UnknownOpcodeError,
)


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

    def _validate_bytecode(self, bytecode: bytes, code_name: str = "<unknown>") -> None:
        """Validate the fixed-width 3.11 physical instruction layout."""
        if not isinstance(bytecode, (bytes, bytearray, memoryview)):
            raise MalformedBytecodeError(
                f"CPython 3.11 code object {code_name!r} has a non-bytes co_code"
            )

        if len(bytecode) % 2:
            raise MalformedBytecodeError(
                f"CPython 3.11 code object {code_name!r} has an odd co_code "
                f"length ({len(bytecode)} bytes)"
            )

        for offset in range(0, len(bytecode), 2):
            opcode = bytecode[offset]
            try:
                opname = self.opname[opcode]
            except IndexError as error:
                raise UnknownOpcodeError(
                    f"Unknown CPython 3.11 opcode {opcode} at offset {offset} "
                    f"in {code_name!r}"
                ) from error
            if opname.startswith("<") and opname.endswith(">"):
                raise UnknownOpcodeError(
                    f"Unknown CPython 3.11 opcode {opcode} at offset {offset} "
                    f"in {code_name!r}"
                )

    @staticmethod
    def iter_code_objects(co) -> Iterator[object]:
        """Yield a code object and every code object nested in its constants."""
        if not iscode(co):
            raise MalformedBytecodeError(
                f"Scanner311 expected a code object, got {type(co).__name__}"
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

    def ingest(self, co, classname=None, code_objects=None, show_asm=None):
        """Return raw 3.11 Tokens and an empty parser-customization mapping."""
        if not iscode(co):
            raise MalformedBytecodeError(
                f"Scanner311 expected a code object, got {type(co).__name__}"
            )

        code_name = getattr(co, "co_name", "<unknown>")
        try:
            raw_code = bytes(co.co_code)
        except (AttributeError, TypeError, ValueError) as error:
            raise MalformedBytecodeError(
                f"CPython 3.11 code object {code_name!r} has invalid co_code"
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
                f"Unable to decode CPython 3.11 code object {code_name!r}: {error}"
            ) from error

        expected_offsets = list(range(0, len(raw_code), 2))
        actual_offsets = [instruction.offset for instruction in self.insts]
        if actual_offsets != expected_offsets:
            raise MalformedBytecodeError(
                f"CPython 3.11 code object {code_name!r} did not decode into "
                "one instruction per two-byte code unit"
            )

        for instruction in self.insts:
            if instruction.opcode != raw_code[instruction.offset]:
                raise MalformedBytecodeError(
                    f"Decoded opcode mismatch at offset {instruction.offset} "
                    f"in {code_name!r}"
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

        return tokens, {}
