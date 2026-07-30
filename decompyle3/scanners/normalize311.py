# Copyright (c) 2026
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Normalize CPython 3.11 instructions without applying parser grammar."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from xdis import iscode
from xdis.bytecode import get_optype
from xdis.cross_dis import xstack_effect
from xdis.instruction import Instruction

from decompyle3.ir import (
    CallInfo,
    FunctionInfo,
    NormalizedInstruction,
    StackAnalysis,
)
from decompyle3.scanner import (
    BytecodeNormalizationError,
    InvalidJumpTargetError,
    StackDepthError,
    UnsupportedSpecializedOpcodeError,
)


BINARY_OPERATIONS = (
    ("BINARY_ADD", "+", False),
    ("BINARY_AND", "&", False),
    ("BINARY_FLOOR_DIVIDE", "//", False),
    ("BINARY_LSHIFT", "<<", False),
    ("BINARY_MATRIX_MULTIPLY", "@", False),
    ("BINARY_MULTIPLY", "*", False),
    ("BINARY_MODULO", "%", False),
    ("BINARY_OR", "|", False),
    ("BINARY_POWER", "**", False),
    ("BINARY_RSHIFT", ">>", False),
    ("BINARY_SUBTRACT", "-", False),
    ("BINARY_TRUE_DIVIDE", "/", False),
    ("BINARY_XOR", "^", False),
    ("INPLACE_ADD", "+=", True),
    ("INPLACE_AND", "&=", True),
    ("INPLACE_FLOOR_DIVIDE", "//=", True),
    ("INPLACE_LSHIFT", "<<=", True),
    ("INPLACE_MATRIX_MULTIPLY", "@=", True),
    ("INPLACE_MULTIPLY", "*=", True),
    ("INPLACE_MODULO", "%=", True),
    ("INPLACE_OR", "|=", True),
    ("INPLACE_POWER", "**=", True),
    ("INPLACE_RSHIFT", ">>=", True),
    ("INPLACE_SUBTRACT", "-=", True),
    ("INPLACE_TRUE_DIVIDE", "/=", True),
    ("INPLACE_XOR", "^=", True),
)

COMPARE_KINDS = {
    "<": "COMPARE_LT",
    "<=": "COMPARE_LE",
    "==": "COMPARE_EQ",
    "!=": "COMPARE_NE",
    ">": "COMPARE_GT",
    ">=": "COMPARE_GE",
}

SPECIALIZED_TO_BASE = {
    "BINARY_OP_ADAPTIVE": "BINARY_OP",
    "BINARY_OP_ADD_FLOAT": "BINARY_OP",
    "BINARY_OP_ADD_INT": "BINARY_OP",
    "BINARY_OP_ADD_UNICODE": "BINARY_OP",
    "BINARY_OP_INPLACE_ADD_UNICODE": "BINARY_OP",
    "BINARY_OP_MULTIPLY_FLOAT": "BINARY_OP",
    "BINARY_OP_MULTIPLY_INT": "BINARY_OP",
    "BINARY_OP_SUBTRACT_FLOAT": "BINARY_OP",
    "BINARY_OP_SUBTRACT_INT": "BINARY_OP",
    "BINARY_SUBSCR_ADAPTIVE": "BINARY_SUBSCR",
    "BINARY_SUBSCR_DICT": "BINARY_SUBSCR",
    "BINARY_SUBSCR_GETITEM": "BINARY_SUBSCR",
    "BINARY_SUBSCR_LIST_INT": "BINARY_SUBSCR",
    "BINARY_SUBSCR_TUPLE_INT": "BINARY_SUBSCR",
    "CALL_ADAPTIVE": "CALL",
    "CALL_PY_EXACT_ARGS": "CALL",
    "CALL_PY_WITH_DEFAULTS": "CALL",
    "COMPARE_OP_ADAPTIVE": "COMPARE_OP",
    "COMPARE_OP_FLOAT_JUMP": "COMPARE_OP",
    "COMPARE_OP_INT_JUMP": "COMPARE_OP",
    "COMPARE_OP_STR_JUMP": "COMPARE_OP",
    "EXTENDED_ARG_QUICK": "EXTENDED_ARG",
    "JUMP_BACKWARD_QUICK": "JUMP_BACKWARD",
    "LOAD_ATTR_ADAPTIVE": "LOAD_ATTR",
    "LOAD_ATTR_INSTANCE_VALUE": "LOAD_ATTR",
    "LOAD_ATTR_MODULE": "LOAD_ATTR",
    "LOAD_ATTR_SLOT": "LOAD_ATTR",
    "LOAD_ATTR_WITH_HINT": "LOAD_ATTR",
    "LOAD_CONST__LOAD_FAST": "LOAD_CONST",
    "LOAD_FAST__LOAD_CONST": "LOAD_FAST",
    "LOAD_FAST__LOAD_FAST": "LOAD_FAST",
    "LOAD_GLOBAL_ADAPTIVE": "LOAD_GLOBAL",
    "LOAD_GLOBAL_BUILTIN": "LOAD_GLOBAL",
    "LOAD_GLOBAL_MODULE": "LOAD_GLOBAL",
    "LOAD_METHOD_ADAPTIVE": "LOAD_METHOD",
    "LOAD_METHOD_CLASS": "LOAD_METHOD",
    "LOAD_METHOD_MODULE": "LOAD_METHOD",
    "LOAD_METHOD_NO_DICT": "LOAD_METHOD",
    "LOAD_METHOD_WITH_DICT": "LOAD_METHOD",
    "LOAD_METHOD_WITH_VALUES": "LOAD_METHOD",
    "PRECALL_ADAPTIVE": "PRECALL",
    "PRECALL_BOUND_METHOD": "PRECALL",
    "PRECALL_BUILTIN_CLASS": "PRECALL",
    "PRECALL_BUILTIN_FAST_WITH_KEYWORDS": "PRECALL",
    "PRECALL_METHOD_DESCRIPTOR_FAST_WITH_KEYWORDS": "PRECALL",
    "PRECALL_NO_KW_BUILTIN_FAST": "PRECALL",
    "PRECALL_NO_KW_BUILTIN_O": "PRECALL",
    "PRECALL_NO_KW_ISINSTANCE": "PRECALL",
    "PRECALL_NO_KW_LEN": "PRECALL",
    "PRECALL_NO_KW_LIST_APPEND": "PRECALL",
    "PRECALL_NO_KW_METHOD_DESCRIPTOR_FAST": "PRECALL",
    "PRECALL_NO_KW_METHOD_DESCRIPTOR_NOARGS": "PRECALL",
    "PRECALL_NO_KW_METHOD_DESCRIPTOR_O": "PRECALL",
    "PRECALL_NO_KW_STR_1": "PRECALL",
    "PRECALL_NO_KW_TUPLE_1": "PRECALL",
    "PRECALL_NO_KW_TYPE_1": "PRECALL",
    "PRECALL_PYFUNC": "PRECALL",
    "RESUME_QUICK": "RESUME",
    "STORE_ATTR_ADAPTIVE": "STORE_ATTR",
    "STORE_ATTR_INSTANCE_VALUE": "STORE_ATTR",
    "STORE_ATTR_SLOT": "STORE_ATTR",
    "STORE_ATTR_WITH_HINT": "STORE_ATTR",
    "STORE_FAST__LOAD_FAST": "STORE_FAST",
    "STORE_FAST__STORE_FAST": "STORE_FAST",
    "STORE_SUBSCR_ADAPTIVE": "STORE_SUBSCR",
    "STORE_SUBSCR_DICT": "STORE_SUBSCR",
    "STORE_SUBSCR_LIST_INT": "STORE_SUBSCR",
    "UNPACK_SEQUENCE_ADAPTIVE": "UNPACK_SEQUENCE",
    "UNPACK_SEQUENCE_LIST": "UNPACK_SEQUENCE",
    "UNPACK_SEQUENCE_TUPLE": "UNPACK_SEQUENCE",
    "UNPACK_SEQUENCE_TWO_TUPLE": "UNPACK_SEQUENCE",
}

UNCONDITIONAL_JUMPS = {
    "JUMP_FORWARD",
    "JUMP_BACKWARD",
    "JUMP_BACKWARD_NO_INTERRUPT",
}

TERMINATORS = {
    "RETURN_VALUE",
    "RAISE_VARARGS",
    "RERAISE",
    "RETURN_GENERATOR",
}


@dataclass
class _StackValue:
    kind: str
    value: Any = None
    origin: Optional[int] = None


class Normalizer311:
    """Convert a raw 3.11 instruction stream into parser-facing records."""

    def __init__(self, opc):
        self.opc = opc
        self.physical_to_logical: Dict[int, int] = {}
        self.logical_to_physical: Dict[int, int] = {}
        self.cache_owner: Dict[int, int] = {}
        self.stack_analysis = StackAnalysis({}, 0)
        self.call_contexts: Dict[int, Tuple[Tuple[str, ...], int, Optional[int]]] = {}

    @staticmethod
    def _base_name(instruction: NormalizedInstruction) -> str:
        return SPECIALIZED_TO_BASE.get(
            instruction.original_opname, instruction.original_opname
        )

    def despecialize_opname(self, opname: str, source_kind: str) -> str:
        """Return the base opcode for adaptive runtime instructions."""
        if opname not in SPECIALIZED_TO_BASE:
            if opname in self.opc.opmap:
                return opname
            if source_kind == "runtime":
                raise UnsupportedSpecializedOpcodeError(
                    f"Cannot de-specialize CPython 3.11 runtime opcode {opname}"
                )
            return opname
        if source_kind != "runtime":
            raise UnsupportedSpecializedOpcodeError(
                f"Specialized opcode {opname} is not valid in a standard .pyc"
            )
        return SPECIALIZED_TO_BASE[opname]

    def runtime_instructions(self, co) -> Tuple[Instruction, ...]:
        """Read adaptive instructions from a live CPython 3.11 code object."""
        try:
            import dis
            import sys
        except ImportError as error:
            raise UnsupportedSpecializedOpcodeError(
                "Runtime specialization inspection requires CPython 3.11"
            ) from error

        if sys.version_info[:2] != (3, 11) or not hasattr(co, "_co_code_adaptive"):
            raise UnsupportedSpecializedOpcodeError(
                "Runtime specialization inspection requires a live CPython 3.11 "
                "code object"
            )

        instructions = []
        for native in dis.get_instructions(
            co, adaptive=True, show_caches=True
        ):
            base_name = SPECIALIZED_TO_BASE.get(native.opname, native.opname)
            if base_name not in self.opc.opmap:
                raise UnsupportedSpecializedOpcodeError(
                    f"Cannot de-specialize CPython 3.11 runtime opcode "
                    f"{native.opname} at offset {native.offset}"
                )
            base_opcode = self.opc.opmap[base_name]
            instructions.append(
                Instruction(
                    opcode=native.opcode,
                    opname=native.opname,
                    arg=native.arg,
                    argval=native.argval,
                    argrepr=native.argrepr,
                    offset=native.offset,
                    starts_line=native.starts_line,
                    is_jump_target=native.is_jump_target,
                    positions=native.positions,
                    optype=get_optype(base_opcode, self.opc),
                    has_arg=native.arg is not None,
                    inst_size=2,
                    has_extended_arg=False,
                    fallthrough=None,
                    tos_str=None,
                    start_offset=None,
                )
            )
        return tuple(instructions)

    def normalize(
        self,
        instructions: Iterable[Instruction],
        co,
        exception_entries: Sequence[object] = (),
        source_kind: str = "pyc",
    ) -> Tuple[NormalizedInstruction, ...]:
        if source_kind not in ("pyc", "runtime"):
            raise BytecodeNormalizationError(
                f"Unknown CPython 3.11 bytecode source kind {source_kind!r}"
            )

        raw = tuple(instructions)
        cache_offsets, cache_owner_offsets = self._cache_layout(raw)
        self.cache_owner = {}
        self.call_contexts = {}
        semantic = []
        for instruction in raw:
            effective_name = self.despecialize_opname(
                instruction.opname, source_kind
            )
            if effective_name == "CACHE":
                continue
            if effective_name not in self.opc.opmap:
                raise BytecodeNormalizationError(
                    f"Unknown CPython 3.11 instruction {instruction.opname} "
                    f"at offset {instruction.offset}"
                )
            semantic.append((instruction, effective_name))

        self.physical_to_logical = {}
        self.logical_to_physical = {}
        offset_to_index = {}
        for logical_index, (instruction, _) in enumerate(semantic):
            offset_to_index[instruction.offset] = logical_index
            self.physical_to_logical[instruction.offset] = logical_index
            self.logical_to_physical[logical_index] = instruction.offset
        for cache_offset, owner_offset in cache_owner_offsets.items():
            owner_index = offset_to_index[owner_offset]
            self.physical_to_logical[cache_offset] = owner_index
            self.cache_owner[cache_offset] = owner_index

        physical_offsets = {instruction.offset for instruction in raw}
        normalized = []
        for logical_index, (instruction, effective_name) in enumerate(semantic):
            effective_opcode = self.opc.opmap[effective_name]
            target = (
                instruction.argval
                if effective_opcode
                in (self.opc.JREL_OPS | self.opc.JABS_OPS)
                else None
            )
            if target is not None:
                if target not in physical_offsets:
                    raise InvalidJumpTargetError(
                        f"{instruction.opname} at offset {instruction.offset} "
                        f"targets invalid offset {target}"
                    )
                if target in cache_owner_offsets:
                    raise InvalidJumpTargetError(
                        f"{instruction.opname} at offset {instruction.offset} "
                        f"targets CACHE slot {target}"
                    )

            kind, internal, metadata = self._normalized_kind(
                instruction, effective_name, co
            )
            pop, push, effect, jump_effect, required = self._stack_shape(
                instruction, effective_name, effective_opcode
            )
            direction, condition, jump_pops = self._jump_metadata(
                effective_name, instruction.offset, target
            )
            normalized.append(
                NormalizedInstruction(
                    logical_index=logical_index,
                    physical_offset=instruction.offset,
                    original_opcode=instruction.opcode,
                    original_opname=instruction.opname,
                    kind=kind,
                    arg=instruction.arg,
                    argval=instruction.argval,
                    argrepr=instruction.argrepr or "",
                    target=target,
                    target_index=(
                        offset_to_index[target] if target is not None else None
                    ),
                    stack_pop=pop,
                    stack_push=push,
                    stack_effect=effect,
                    jump_stack_effect=jump_effect,
                    required_depth=required,
                    is_internal=internal,
                    jump_direction=direction,
                    jump_condition=condition,
                    jump_pops=jump_pops,
                    cache_offsets=tuple(
                        cache_offsets.get(instruction.offset, ())
                    ),
                    metadata=tuple(metadata),
                )
            )

        normalized = self._validate_calls(tuple(normalized))
        self.stack_analysis = self.analyze_stack(
            normalized, exception_entries
        )
        return self._attach_symbolic_metadata(normalized, co)

    @staticmethod
    def _cache_layout(
        instructions: Sequence[Instruction],
    ) -> Tuple[Dict[int, List[int]], Dict[int, int]]:
        caches: Dict[int, List[int]] = {}
        owners: Dict[int, int] = {}
        owner_offset = None
        for instruction in instructions:
            if instruction.opname == "CACHE":
                if owner_offset is None:
                    raise BytecodeNormalizationError(
                        f"CACHE at offset {instruction.offset} has no owner"
                    )
                caches.setdefault(owner_offset, []).append(instruction.offset)
                owners[instruction.offset] = owner_offset
            else:
                owner_offset = instruction.offset
                caches.setdefault(owner_offset, [])
        return caches, owners

    def _normalized_kind(
        self, instruction: Instruction, opname: str, co
    ) -> Tuple[str, bool, List[Tuple[str, Any]]]:
        metadata: List[Tuple[str, Any]] = []
        internal = False
        kind = opname

        if opname == "RESUME":
            kind = "INTERNAL_RESUME"
            internal = True
            metadata.append(("resume_where", instruction.arg))
        elif opname == "EXTENDED_ARG":
            kind = "INTERNAL_EXTENDED_ARG"
            internal = True
        elif opname == "BINARY_OP":
            if instruction.arg is None or not (
                0 <= instruction.arg < len(BINARY_OPERATIONS)
            ):
                raise BytecodeNormalizationError(
                    f"Invalid BINARY_OP argument {instruction.arg!r} at offset "
                    f"{instruction.offset}"
                )
            kind, symbol, inplace = BINARY_OPERATIONS[instruction.arg]
            metadata.extend(
                (("operator", symbol), ("inplace", inplace))
            )
        elif opname == "COMPARE_OP":
            kind = COMPARE_KINDS.get(instruction.argval)
            if kind is None:
                raise BytecodeNormalizationError(
                    f"Invalid COMPARE_OP argument {instruction.argval!r} at "
                    f"offset {instruction.offset}"
                )
            metadata.append(("operator", instruction.argval))
        elif opname == "CONTAINS_OP":
            if instruction.arg not in (0, 1):
                raise BytecodeNormalizationError(
                    f"Invalid CONTAINS_OP argument {instruction.arg!r} at "
                    f"offset {instruction.offset}"
                )
            kind = "NOT_CONTAINS" if instruction.arg else "CONTAINS"
            metadata.append(("negated", bool(instruction.arg)))
        elif opname == "IS_OP":
            if instruction.arg not in (0, 1):
                raise BytecodeNormalizationError(
                    f"Invalid IS_OP argument {instruction.arg!r} at offset "
                    f"{instruction.offset}"
                )
            kind = "IS_NOT" if instruction.arg else "IS"
            metadata.append(("negated", bool(instruction.arg)))
        elif opname == "COPY":
            kind = "COPY_STACK"
            metadata.append(("depth", instruction.arg))
        elif opname == "SWAP":
            kind = "SWAP_STACK"
            metadata.append(("depth", instruction.arg))
        elif opname == "PUSH_NULL":
            internal = True
            metadata.append(("call_protocol", "null"))
        elif opname == "KW_NAMES":
            internal = True
        elif opname == "PRECALL":
            internal = True
        elif opname == "CALL_FUNCTION_EX":
            kind = "CALL"
            metadata.extend(
                (
                    ("uses_ex", True),
                    ("has_kwargs", bool((instruction.arg or 0) & 1)),
                )
            )
        elif opname == "MAKE_FUNCTION":
            flags = instruction.arg or 0
            if flags & ~0x0F:
                raise BytecodeNormalizationError(
                    f"Unknown MAKE_FUNCTION flags 0x{flags:x} at offset "
                    f"{instruction.offset}"
                )
        elif opname in (
            "MAKE_CELL",
            "LOAD_CLOSURE",
            "LOAD_DEREF",
            "STORE_DEREF",
            "DELETE_DEREF",
            "LOAD_CLASSDEREF",
        ):
            metadata.extend(
                (
                    ("localsplus_index", instruction.arg),
                    ("localsplus_name", instruction.argval),
                )
            )
        elif opname == "COPY_FREE_VARS":
            count = instruction.arg or 0
            metadata.append(
                ("freevars", tuple(getattr(co, "co_freevars", ())[:count]))
            )

        return kind, internal, metadata

    def _stack_shape(
        self, instruction: Instruction, opname: str, opcode: int
    ) -> Tuple[int, int, int, Optional[int], int]:
        arg = instruction.arg or 0
        jump_effect = None

        if opname == "PRECALL":
            return arg, 0, -arg, None, arg + 2
        if opname == "CALL":
            return 2, 1, -1, None, 2
        if opname == "CALL_FUNCTION_EX":
            pop = 3 + int(bool(arg & 1))
            return pop, 1, 1 - pop, None, pop
        if opname == "MAKE_FUNCTION":
            pop = 1 + bin(arg & 0x0F).count("1")
            return pop, 1, 1 - pop, None, pop
        if opname == "LOAD_GLOBAL":
            push = 2 if arg & 1 else 1
            return 0, push, push, None, 0
        if opname == "LOAD_METHOD":
            return 1, 2, 1, None, 1
        if opname in ("BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_STRING"):
            return arg, 1, 1 - arg, None, arg
        if opname == "BUILD_MAP":
            return arg * 2, 1, 1 - (arg * 2), None, arg * 2
        if opname == "BUILD_CONST_KEY_MAP":
            return arg + 1, 1, -arg, None, arg + 1
        if opname == "UNPACK_SEQUENCE":
            return 1, arg, arg - 1, None, 1
        if opname == "UNPACK_EX":
            push = (arg & 0xFF) + (arg >> 8) + 1
            return 1, push, push - 1, None, 1
        if opname == "BUILD_SLICE":
            pop = 3 if arg == 3 else 2
            return pop, 1, 1 - pop, None, pop
        if opname == "FORMAT_VALUE":
            pop = 2 if arg & 0x04 else 1
            return pop, 1, 1 - pop, None, pop
        if opname == "RAISE_VARARGS":
            return arg, 0, -arg, None, arg
        if opname in ("LIST_APPEND", "SET_ADD", "LIST_EXTEND", "SET_UPDATE"):
            return 1, 0, -1, None, max(arg, 1)
        if opname in ("DICT_MERGE", "DICT_UPDATE"):
            return 1, 0, -1, None, max(arg, 1)
        if opname == "MAP_ADD":
            return 2, 0, -2, None, max(arg + 1, 2)
        if opname == "COPY":
            return 0, 1, 1, None, max(arg, 1)
        if opname == "SWAP":
            return 0, 0, 0, None, max(arg, 1)
        if opname in ("JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP"):
            return 1, 0, -1, 0, 1
        if opname == "FOR_ITER":
            return 0, 1, 1, -1, 1
        if opname == "SEND":
            return 0, 0, 0, -1, 2
        if opname.startswith("POP_JUMP_"):
            return 1, 0, -1, -1, 1
        if opname in UNCONDITIONAL_JUMPS:
            return 0, 0, 0, 0, 0
        if opname in ("BEFORE_WITH", "BEFORE_ASYNC_WITH"):
            return 1, 2, 1, None, 1

        effect = xstack_effect(opcode, self.opc, arg)
        if effect is None or effect == -100:
            raise BytecodeNormalizationError(
                f"Unknown stack effect for {opname} at offset "
                f"{instruction.offset}"
            )
        pop = self.opc.oppop[opcode]
        push = self.opc.oppush[opcode]
        if pop < 0 or push < 0 or push - pop != effect:
            pop = max(-effect, 0)
            push = max(effect, 0)
        return pop, push, effect, jump_effect, pop

    @staticmethod
    def _jump_metadata(
        opname: str, offset: int, target: Optional[int]
    ) -> Tuple[Optional[str], Optional[str], Optional[bool]]:
        if target is None:
            return None, None, None
        direction = "backward" if target < offset else "forward"
        condition = None
        jump_pops = None

        if "IF_FALSE" in opname:
            condition = "false"
        elif "IF_TRUE" in opname:
            condition = "true"
        elif "IF_NOT_NONE" in opname:
            condition = "not_none"
        elif "IF_NONE" in opname:
            condition = "none"
        elif opname == "FOR_ITER":
            condition = "iterator_exhausted"
        elif opname == "SEND":
            condition = "iterator_returned"

        if opname.startswith("POP_JUMP_"):
            jump_pops = True
        elif opname in ("JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP"):
            jump_pops = False
        elif opname in ("FOR_ITER", "SEND"):
            jump_pops = True

        return direction, condition, jump_pops

    def _validate_calls(
        self, instructions: Tuple[NormalizedInstruction, ...]
    ) -> Tuple[NormalizedInstruction, ...]:
        updated = list(instructions)
        pending_names: Tuple[str, ...] = ()
        pending_offset = None

        for index, instruction in enumerate(instructions):
            opname = self._base_name(instruction)
            if opname == "KW_NAMES":
                value = instruction.argval
                if not isinstance(value, tuple) or not all(
                    isinstance(name, str) for name in value
                ):
                    raise BytecodeNormalizationError(
                        f"KW_NAMES at offset {instruction.offset} does not "
                        "reference a tuple of strings"
                    )
                pending_names = value
                pending_offset = instruction.offset
                if index + 1 >= len(instructions) or (
                    self._base_name(instructions[index + 1]) != "PRECALL"
                ):
                    raise BytecodeNormalizationError(
                        f"KW_NAMES at offset {instruction.offset} is not "
                        "followed by PRECALL"
                    )
                continue

            if opname != "PRECALL":
                continue

            if index + 1 >= len(instructions):
                raise BytecodeNormalizationError(
                    f"PRECALL at offset {instruction.offset} has no CALL"
                )
            call = instructions[index + 1]
            if self._base_name(call) != "CALL":
                raise BytecodeNormalizationError(
                    f"PRECALL at offset {instruction.offset} is followed by "
                    f"{call.original_opname}, not CALL"
                )
            if instruction.arg != call.arg:
                raise BytecodeNormalizationError(
                    f"PRECALL/CALL argument mismatch at offsets "
                    f"{instruction.offset}/{call.offset}"
                )
            argc = instruction.arg or 0
            if len(pending_names) > argc:
                raise BytecodeNormalizationError(
                    f"CALL at offset {call.offset} has {argc} arguments but "
                    f"{len(pending_names)} keyword names"
                )

            metadata = list(instruction.metadata)
            metadata.extend(
                (
                    ("call_offset", call.offset),
                    ("argc", argc),
                    ("keyword_names", pending_names),
                )
            )
            updated[index] = replace(instruction, metadata=tuple(metadata))
            self.call_contexts[call.offset] = (
                pending_names,
                instruction.offset,
                pending_offset,
            )
            pending_names = ()
            pending_offset = None

        return tuple(updated)

    def analyze_stack(
        self,
        instructions: Sequence[NormalizedInstruction],
        exception_entries: Sequence[object] = (),
    ) -> StackAnalysis:
        if not instructions:
            return StackAnalysis({}, 0)

        by_offset = {instruction.offset: instruction for instruction in instructions}
        next_offset = {
            instruction.offset: (
                instructions[index + 1].offset
                if index + 1 < len(instructions)
                else None
            )
            for index, instruction in enumerate(instructions)
        }
        depths: Dict[int, int] = {}
        pending = deque([(instructions[0].offset, 0)])
        maximum = 0

        if self._base_name(instructions[0]) == "RETURN_GENERATOR":
            resumed = next_offset[instructions[0].offset]
            if resumed is not None:
                pending.append((resumed, 1))

        while pending:
            offset, depth = pending.popleft()
            previous = depths.get(offset)
            if previous is not None:
                if previous != depth:
                    raise StackDepthError(
                        f"Inconsistent stack depth at offset {offset}: "
                        f"{previous} versus {depth}"
                    )
                continue

            instruction = by_offset[offset]
            if depth < instruction.required_depth:
                raise StackDepthError(
                    f"{instruction.kind} at offset {offset} requires stack "
                    f"depth {instruction.required_depth}, found {depth}"
                )
            depths[offset] = depth
            maximum = max(maximum, depth)

            fall_depth = depth + instruction.stack_effect
            if fall_depth < 0:
                raise StackDepthError(
                    f"{instruction.kind} at offset {offset} makes stack depth "
                    f"negative ({fall_depth})"
                )

            following = next_offset[offset]
            if instruction.target is not None:
                jump_effect = (
                    instruction.jump_stack_effect
                    if instruction.jump_stack_effect is not None
                    else instruction.stack_effect
                )
                jump_depth = depth + jump_effect
                if jump_depth < 0:
                    raise StackDepthError(
                        f"{instruction.kind} at offset {offset} makes jump "
                        f"stack depth negative ({jump_depth})"
                    )
                pending.append((instruction.target, jump_depth))
                if self._base_name(instruction) not in UNCONDITIONAL_JUMPS:
                    if following is not None:
                        pending.append((following, fall_depth))
            elif self._base_name(instruction) not in TERMINATORS:
                if following is not None:
                    pending.append((following, fall_depth))

            for entry in exception_entries:
                if entry.start <= offset < entry.end:
                    handler_depth = entry.depth + 1 + int(entry.lasti)
                    pending.append((entry.target, handler_depth))

        return StackAnalysis(depths, maximum)

    def _attach_symbolic_metadata(
        self, instructions: Tuple[NormalizedInstruction, ...], co
    ) -> Tuple[NormalizedInstruction, ...]:
        stack: List[_StackValue] = []
        enriched = []

        for instruction in instructions:
            expected_depth = self.stack_analysis.depths.get(instruction.offset)
            if expected_depth is None:
                stack = []
            elif len(stack) != expected_depth:
                stack = [
                    _StackValue("unknown", origin=instruction.offset)
                    for _ in range(expected_depth)
                ]

            call = None
            function = None
            opname = self._base_name(instruction)
            if opname == "CALL":
                names, precall_offset, kw_offset = self.call_contexts[
                    instruction.offset
                ]
                call = self._call_info(
                    stack,
                    instruction.arg or 0,
                    names,
                    precall_offset,
                    kw_offset,
                    uses_ex=False,
                    has_kwargs=bool(names),
                )
            elif opname == "CALL_FUNCTION_EX":
                call = self._call_info(
                    stack,
                    None,
                    (),
                    None,
                    None,
                    uses_ex=True,
                    has_kwargs=bool((instruction.arg or 0) & 1),
                )
            elif opname == "MAKE_FUNCTION":
                function = self._function_info(stack, instruction.arg or 0)

            self._apply_symbolic_instruction(stack, instruction)
            enriched.append(
                replace(instruction, call=call, function=function)
            )

        return tuple(enriched)

    @staticmethod
    def _pop_values(stack: List[_StackValue], count: int) -> List[_StackValue]:
        if count <= 0:
            return []
        if len(stack) < count:
            stack[:0] = [
                _StackValue("unknown") for _ in range(count - len(stack))
            ]
        values = stack[-count:]
        del stack[-count:]
        return values

    def _apply_symbolic_instruction(
        self, stack: List[_StackValue], instruction: NormalizedInstruction
    ) -> None:
        opname = self._base_name(instruction)
        offset = instruction.offset

        if opname == "PUSH_NULL":
            stack.append(_StackValue("null", origin=offset))
            return
        if opname == "LOAD_CONST":
            stack.append(_StackValue("const", instruction.argval, offset))
            return
        if opname == "LOAD_CLOSURE":
            stack.append(_StackValue("closure", instruction.argval, offset))
            return
        if opname == "LOAD_GLOBAL":
            if (instruction.arg or 0) & 1:
                stack.append(_StackValue("null", origin=offset))
            stack.append(_StackValue("value", instruction.argval, offset))
            return
        if opname == "LOAD_METHOD":
            self._pop_values(stack, 1)
            stack.append(_StackValue("method", instruction.argval, offset))
            stack.append(_StackValue("self_or_null", origin=offset))
            return
        if opname == "BUILD_TUPLE":
            values = self._pop_values(stack, instruction.arg or 0)
            stack.append(
                _StackValue(
                    "tuple",
                    tuple(value.value for value in values),
                    offset,
                )
            )
            return
        if opname == "BUILD_CONST_KEY_MAP":
            keys = self._pop_values(stack, 1)
            self._pop_values(stack, instruction.arg or 0)
            key_values = keys[0].value if keys else ()
            stack.append(_StackValue("dict", tuple(key_values or ()), offset))
            return
        if opname == "BUILD_MAP":
            values = self._pop_values(stack, (instruction.arg or 0) * 2)
            keys = tuple(
                value.value for value in values[0::2] if value.kind == "const"
            )
            stack.append(_StackValue("dict", keys, offset))
            return
        if opname == "COPY":
            depth = instruction.arg or 0
            if depth and len(stack) >= depth:
                stack.append(stack[-depth])
            else:
                stack.append(_StackValue("unknown", origin=offset))
            return
        if opname == "SWAP":
            depth = instruction.arg or 0
            if depth and len(stack) >= depth:
                stack[-1], stack[-depth] = stack[-depth], stack[-1]
            return

        self._pop_values(stack, instruction.stack_pop)
        for _ in range(instruction.stack_push):
            stack.append(_StackValue("value", origin=offset))

    @staticmethod
    def _protocol_info(
        first: _StackValue, second: _StackValue
    ) -> Tuple[bool, bool, bool, str]:
        if first.kind == "null":
            return False, True, False, "null"
        if first.kind == "method" or second.kind == "self_or_null":
            return True, False, True, "self_or_null"
        return False, False, False, "unknown"

    def _call_info(
        self,
        stack: Sequence[_StackValue],
        argc: Optional[int],
        keyword_names: Tuple[str, ...],
        precall_offset: Optional[int],
        kw_names_offset: Optional[int],
        uses_ex: bool,
        has_kwargs: bool,
    ) -> CallInfo:
        extra_values = 1 + int(has_kwargs) if uses_ex else 0
        protocol_end = len(stack) - extra_values
        if protocol_end >= 2:
            first = stack[protocol_end - 2]
            second = stack[protocol_end - 1]
        else:
            first = _StackValue("unknown")
            second = _StackValue("unknown")
        is_method, has_null, has_self, mode = self._protocol_info(
            first, second
        )
        positional = (
            None if argc is None else argc - len(keyword_names)
        )
        return CallInfo(
            argc=argc,
            positional_count=positional,
            keyword_names=keyword_names,
            is_method=is_method,
            has_null=has_null,
            has_self=has_self,
            receiver_mode=mode,
            uses_ex=uses_ex,
            has_starargs=uses_ex,
            has_kwargs=has_kwargs,
            precall_offset=precall_offset,
            kw_names_offset=kw_names_offset,
        )

    @staticmethod
    def _function_info(
        stack: Sequence[_StackValue], flags: int
    ) -> FunctionInfo:
        code_value = stack[-1].value if stack else None
        code_name = getattr(code_value, "co_name", None) if iscode(code_value) else None
        cursor = len(stack) - 2
        closure = annotations = kwdefaults = defaults = None

        if flags & 0x08 and cursor >= 0:
            closure = stack[cursor]
            cursor -= 1
        if flags & 0x04 and cursor >= 0:
            annotations = stack[cursor]
            cursor -= 1
        if flags & 0x02 and cursor >= 0:
            kwdefaults = stack[cursor]
            cursor -= 1
        if flags & 0x01 and cursor >= 0:
            defaults = stack[cursor]

        default_count = (
            len(defaults.value)
            if defaults is not None and isinstance(defaults.value, tuple)
            else None
        )
        kwdefault_names = (
            tuple(str(name) for name in kwdefaults.value)
            if kwdefaults is not None
            and isinstance(kwdefaults.value, tuple)
            else ()
        )
        annotation_names = (
            tuple(str(name) for name in annotations.value[0::2])
            if annotations is not None
            and isinstance(annotations.value, tuple)
            else ()
        )
        closure_names = (
            tuple(str(name) for name in closure.value)
            if closure is not None and isinstance(closure.value, tuple)
            else ()
        )
        return FunctionInfo(
            flags=flags,
            code_name=code_name,
            has_defaults=bool(flags & 0x01),
            has_kwdefaults=bool(flags & 0x02),
            has_annotations=bool(flags & 0x04),
            has_closure=bool(flags & 0x08),
            default_count=default_count,
            kwdefault_names=kwdefault_names,
            annotation_names=annotation_names,
            closure_names=closure_names,
        )
