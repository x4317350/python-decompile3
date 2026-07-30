# Copyright (c) 2026
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""Small, version-neutral instruction records used between scanner and parser."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class CallInfo:
    """Normalized description of a CPython call protocol."""

    argc: Optional[int]
    positional_count: Optional[int]
    keyword_names: Tuple[str, ...] = ()
    is_method: bool = False
    has_null: bool = False
    has_self: bool = False
    receiver_mode: str = "unknown"
    uses_ex: bool = False
    has_starargs: bool = False
    has_kwargs: bool = False
    precall_offset: Optional[int] = None
    kw_names_offset: Optional[int] = None


@dataclass(frozen=True)
class FunctionInfo:
    """Normalized MAKE_FUNCTION flags and discoverable operands."""

    flags: int
    code_name: Optional[str]
    has_defaults: bool
    has_kwdefaults: bool
    has_annotations: bool
    has_closure: bool
    default_count: Optional[int] = None
    kwdefault_names: Tuple[str, ...] = ()
    annotation_names: Tuple[str, ...] = ()
    closure_names: Tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedInstruction:
    """A parser-facing instruction tied to its original physical opcode."""

    logical_index: int
    physical_offset: int
    original_opcode: int
    original_opname: str
    kind: str
    arg: Optional[int]
    argval: Any
    argrepr: str
    target: Optional[int]
    target_index: Optional[int]
    stack_pop: int
    stack_push: int
    stack_effect: int
    jump_stack_effect: Optional[int] = None
    required_depth: int = 0
    is_internal: bool = False
    jump_direction: Optional[str] = None
    jump_condition: Optional[str] = None
    jump_pops: Optional[bool] = None
    cache_offsets: Tuple[int, ...] = ()
    metadata: Tuple[Tuple[str, Any], ...] = ()
    call: Optional[CallInfo] = None
    function: Optional[FunctionInfo] = None

    @property
    def offset(self) -> int:
        """Compatibility alias for code which expects Token-like offsets."""
        return self.physical_offset

    @property
    def is_jump(self) -> bool:
        return self.target is not None

    def metadata_dict(self) -> Mapping[str, Any]:
        return dict(self.metadata)


@dataclass(frozen=True)
class StackAnalysis:
    """Reachable stack depths for one code object."""

    depths: Mapping[int, int]
    max_depth: int
