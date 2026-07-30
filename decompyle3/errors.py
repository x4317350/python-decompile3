"""Public, contextual error hierarchy for decompilation failures."""

from __future__ import annotations

import re
from typing import Optional, Tuple

from xdis.version_info import version_tuple_to_str


_OFFSET_RE = re.compile(r"\boffset\s+(-?\d+)\b")


class Decompyle3Error(Exception):
    """Base class for failures that callers may safely report to users."""


class ContextualDecompilationError(Decompyle3Error):
    """A failure annotated with bytecode version and code location."""

    def __init__(
        self,
        message: str,
        *,
        version: Optional[Tuple[int, ...]] = None,
        code_name: Optional[str] = None,
        offset: Optional[int] = None,
    ):
        super().__init__(message)
        self.message = str(message)
        self.version = tuple(version) if version is not None else None
        self.code_name = code_name
        self.offset = offset

    def add_context(
        self,
        *,
        version: Optional[Tuple[int, ...]] = None,
        code_name: Optional[str] = None,
        offset: Optional[int] = None,
    ):
        """Fill context not already supplied and return this exception."""
        if self.version is None and version is not None:
            self.version = tuple(version)
        if self.code_name is None and code_name is not None:
            self.code_name = code_name
        if self.offset is None and offset is not None:
            self.offset = offset
        return self

    def __str__(self) -> str:
        if (
            self.version is None
            and self.code_name is None
            and self.offset is None
        ):
            return self.message
        version = (
            version_tuple_to_str(self.version, end=2)
            if self.version is not None
            else "?"
        )
        code_name = repr(self.code_name) if self.code_name is not None else "?"
        offset = self.offset if self.offset is not None else "?"
        return (
            f"{self.message} "
            f"[version={version}, code={code_name}, offset={offset}]"
        )


class UnsupportedVersionError(ContextualDecompilationError, RuntimeError):
    """The requested target bytecode or implementation is unsupported."""


class UnsupportedFeatureError(ContextualDecompilationError, RuntimeError):
    """A public option is not safely supported for the requested target."""


class BytecodeError(ContextualDecompilationError, ValueError):
    """Base class for ingestion and normalization failures."""


class BytecodeScanError(BytecodeError):
    """Bytecode cannot be scanned safely."""


class MalformedBytecodeError(BytecodeScanError):
    """The physical bytecode layout or metadata is malformed."""


class UnsupportedOpcodeError(BytecodeScanError):
    """An opcode cannot be interpreted for the target version."""


class UnknownOpcodeError(UnsupportedOpcodeError):
    """The target opcode table has no name for an opcode number."""


class BytecodeNormalizationError(BytecodeScanError):
    """Raw bytecode cannot be normalized without guessing."""


class UnsupportedSpecializedOpcodeError(BytecodeNormalizationError):
    """Adaptive runtime bytecode cannot be de-specialized safely."""


class InvalidJumpTargetError(BytecodeNormalizationError):
    """A jump targets a cache entry or invalid physical offset."""


class StackDepthError(BytecodeNormalizationError):
    """Reachable instructions have inconsistent operand-stack depths."""


class ControlFlowError(ContextualDecompilationError):
    """A control-flow graph cannot be structured safely."""


class ExceptionTableError(ContextualDecompilationError, ValueError):
    """A zero-cost exception table is invalid or unsupported."""


class ParserError(ContextualDecompilationError):
    """A token stream cannot be converted to a syntax tree safely."""


class SemanticGenerationError(ContextualDecompilationError):
    """A syntax tree cannot be rendered as reliable Python source."""


class VerificationError(ContextualDecompilationError):
    """Generated source failed syntax, compile, or behavior verification."""


def inferred_offset(error: BaseException) -> Optional[int]:
    """Extract a physical bytecode offset from a conventional error message."""
    match = _OFFSET_RE.search(str(error))
    return int(match.group(1)) if match is not None else None


def add_error_context(
    error: BaseException,
    *,
    version: Tuple[int, ...],
    code_name: str,
    offset: Optional[int] = None,
) -> BaseException:
    """Add context to one of this module's public error types."""
    if isinstance(error, ContextualDecompilationError):
        return error.add_context(
            version=version,
            code_name=code_name,
            offset=offset if offset is not None else inferred_offset(error),
        )
    return error


__all__ = [
    "BytecodeError",
    "BytecodeNormalizationError",
    "BytecodeScanError",
    "ContextualDecompilationError",
    "ControlFlowError",
    "Decompyle3Error",
    "ExceptionTableError",
    "InvalidJumpTargetError",
    "MalformedBytecodeError",
    "ParserError",
    "SemanticGenerationError",
    "StackDepthError",
    "UnknownOpcodeError",
    "UnsupportedOpcodeError",
    "UnsupportedFeatureError",
    "UnsupportedSpecializedOpcodeError",
    "UnsupportedVersionError",
    "VerificationError",
    "add_error_context",
    "inferred_offset",
]
