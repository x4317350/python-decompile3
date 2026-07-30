"""Decode and validate CPython 3.11 zero-cost exception tables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Tuple

from decompyle3.errors import ExceptionTableError


class ExceptionTableDecodeError(ExceptionTableError):
    """Raised when a CPython 3.11 exception table is truncated or invalid."""


@dataclass(frozen=True, order=True)
class ExceptionRegion:
    """One protected bytecode interval and its exception-handler target."""

    start: int
    end: int
    target: int
    depth: int
    lasti: bool

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


def _parse_varint(iterator) -> int:
    try:
        byte = next(iterator)
    except StopIteration as error:
        raise ExceptionTableDecodeError(
            "Truncated CPython 3.11 exception-table varint"
        ) from error
    value = byte & 0x3F
    while byte & 0x40:
        try:
            byte = next(iterator)
        except StopIteration as error:
            raise ExceptionTableDecodeError(
                "Truncated CPython 3.11 exception-table varint"
            ) from error
        value = (value << 6) | (byte & 0x3F)
    return value


def decode_exception_table_bytes(data: bytes) -> Tuple[ExceptionRegion, ...]:
    """Decode raw ``co_exceptiontable`` bytes without using private ``dis`` APIs."""
    iterator = iter(data)
    entries = []
    while True:
        try:
            first = next(iterator)
        except StopIteration:
            break

        def prefixed():
            yield first
            yield from iterator

        shared = iter(prefixed())
        start = _parse_varint(shared) * 2
        length = _parse_varint(shared) * 2
        target = _parse_varint(shared) * 2
        depth_and_lasti = _parse_varint(shared)
        iterator = shared
        entries.append(
            ExceptionRegion(
                start=start,
                end=start + length,
                target=target,
                depth=depth_and_lasti >> 1,
                lasti=bool(depth_and_lasti & 1),
            )
        )
    return tuple(entries)


def validate_exception_regions(
    regions: Iterable[ExceptionRegion],
    code_length: int,
) -> Tuple[ExceptionRegion, ...]:
    regions = tuple(regions)
    for region in regions:
        if region.start < 0 or region.start % 2:
            raise ExceptionTableDecodeError(
                f"Invalid exception-region start offset {region.start}"
            )
        if region.end <= region.start or region.end > code_length or region.end % 2:
            raise ExceptionTableDecodeError(
                f"Invalid exception-region end offset {region.end}"
            )
        if region.target < 0 or region.target >= code_length or region.target % 2:
            raise ExceptionTableDecodeError(
                f"Invalid exception handler target {region.target}"
            )
        if region.depth < 0:
            raise ExceptionTableDecodeError(
                f"Invalid exception-handler stack depth {region.depth}"
            )
    return regions


def decode_exception_table(code) -> Tuple[ExceptionRegion, ...]:
    """Decode and validate the exception table attached to one code object."""
    try:
        data = bytes(code.co_exceptiontable)
        code_length = len(code.co_code)
    except (AttributeError, TypeError, ValueError) as error:
        raise ExceptionTableDecodeError(
            "Object has no valid CPython 3.11 exception table",
            version=(3, 11),
            code_name=getattr(code, "co_name", "<unknown>"),
        ) from error
    try:
        return validate_exception_regions(
            decode_exception_table_bytes(data),
            code_length,
        )
    except ExceptionTableDecodeError as error:
        error.add_context(
            version=(3, 11),
            code_name=getattr(code, "co_name", "<unknown>"),
        )
        raise
