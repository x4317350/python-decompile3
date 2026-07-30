"""Queries over decoded CPython 3.11 exception regions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from decompyle3.controlflow.exceptiontable311 import ExceptionRegion


@dataclass(frozen=True)
class ExceptionRegionMap:
    """Stable indexes for protected intervals and shared handler targets."""

    entries: Tuple[ExceptionRegion, ...]
    by_target: Dict[int, Tuple[ExceptionRegion, ...]]

    def covering(self, offset: int) -> Tuple[ExceptionRegion, ...]:
        return tuple(entry for entry in self.entries if entry.contains(offset))

    def starting_at(self, offset: int) -> Tuple[ExceptionRegion, ...]:
        return tuple(entry for entry in self.entries if entry.start == offset)

    @property
    def handler_targets(self) -> Tuple[int, ...]:
        return tuple(sorted(self.by_target))


def build_exception_region_map(
    entries: Iterable[ExceptionRegion],
) -> ExceptionRegionMap:
    entries = tuple(sorted(entries))
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry.target].append(entry)
    return ExceptionRegionMap(
        entries=entries,
        by_target={
            target: tuple(regions)
            for target, regions in sorted(grouped.items())
        },
    )
