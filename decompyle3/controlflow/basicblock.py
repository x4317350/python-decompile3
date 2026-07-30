"""Basic-block records for normalized bytecode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple


@dataclass
class BasicBlock:
    """A maximal straight-line instruction interval."""

    index: int
    start: int
    end: int
    instructions: Tuple[Any, ...]
    reachable: bool = False

    @property
    def label(self) -> str:
        return f"B{self.index}"

    @property
    def last(self):
        return self.instructions[-1]

    @property
    def terminator(self) -> str:
        return self.last.kind

    def contains(self, offset: int) -> bool:
        return any(instruction.offset == offset for instruction in self.instructions)
