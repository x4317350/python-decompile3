"""Build stable basic-block control-flow graphs from normalized instructions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from decompyle3.controlflow.basicblock import BasicBlock
from decompyle3.errors import ControlFlowError


UNCONDITIONAL_JUMPS = {
    "JUMP_FORWARD",
    "JUMP_BACKWARD",
    "JUMP_BACKWARD_NO_INTERRUPT",
}

TERMINATORS = {
    "RAISE_VARARGS",
    "RERAISE",
    "RETURN_VALUE",
}


def is_jump_kind(kind: str) -> bool:
    return (
        kind in UNCONDITIONAL_JUMPS
        or kind.startswith("POP_JUMP_")
        or kind.startswith("JUMP_IF_")
        or kind in ("FOR_ITER", "SEND")
    )


def instruction_target(instruction) -> Optional[int]:
    target = getattr(instruction, "target", None)
    if target is not None:
        return target
    if is_jump_kind(instruction.kind) and isinstance(instruction.attr, int):
        return instruction.attr
    return None


@dataclass(frozen=True, order=True)
class Edge:
    """One directed CFG edge with a stable semantic label."""

    source: int
    target: int
    kind: str


@dataclass
class ControlFlowGraph:
    """Basic blocks, directed edges, and physical-offset lookup tables."""

    blocks: Tuple[BasicBlock, ...]
    edges: Tuple[Edge, ...]
    entry: int
    offset_to_block: Dict[int, int]

    def block(self, index: int) -> BasicBlock:
        return self.blocks[index]

    def block_at(self, offset: int) -> BasicBlock:
        return self.blocks[self.offset_to_block[offset]]

    def successors(self, index: int) -> Tuple[int, ...]:
        return tuple(edge.target for edge in self.edges if edge.source == index)

    def predecessors(self, index: int) -> Tuple[int, ...]:
        return tuple(edge.source for edge in self.edges if edge.target == index)

    def outgoing(self, index: int) -> Tuple[Edge, ...]:
        return tuple(edge for edge in self.edges if edge.source == index)

    def incoming(self, index: int) -> Tuple[Edge, ...]:
        return tuple(edge for edge in self.edges if edge.target == index)

    @property
    def reachable_blocks(self) -> Tuple[int, ...]:
        return tuple(block.index for block in self.blocks if block.reachable)

    def format(self) -> str:
        lines = []
        for block in self.blocks:
            state = "reachable" if block.reachable else "unreachable"
            predecessors = ", ".join(
                f"B{index}" for index in sorted(self.predecessors(block.index))
            )
            outgoing = ", ".join(
                f"B{edge.target}:{edge.kind}"
                for edge in sorted(self.outgoing(block.index))
            )
            lines.append(
                f"{block.label} [{block.start},{block.end}) {state} "
                f"pred=[{predecessors}] succ=[{outgoing}]"
            )
            for instruction in block.instructions:
                target = instruction_target(instruction)
                suffix = "" if target is None else f" -> {target}"
                lines.append(
                    f"  {instruction.offset:04d} {instruction.kind}{suffix}"
                )
        return "\n".join(lines)


def _edge_kinds(kind: str) -> Tuple[str, str]:
    if "IF_FALSE" in kind:
        return "false", "true"
    if "IF_TRUE" in kind:
        return "true", "false"
    if "IF_NOT_NONE" in kind:
        return "not_none", "none"
    if "IF_NONE" in kind:
        return "none", "not_none"
    if kind == "FOR_ITER":
        return "exhausted", "iterate"
    if kind == "SEND":
        return "returned", "yielded"
    return "jump", "fallthrough"


def _mark_reachable(blocks: Sequence[BasicBlock], edges: Sequence[Edge], entry: int):
    successors: Dict[int, List[int]] = {block.index: [] for block in blocks}
    for edge in edges:
        successors[edge.source].append(edge.target)
    pending = deque([entry])
    seen = set()
    while pending:
        index = pending.popleft()
        if index in seen:
            continue
        seen.add(index)
        blocks[index].reachable = True
        pending.extend(successors[index])


def build_cfg(
    instructions: Iterable[Any],
    exception_regions: Iterable[Any] = (),
) -> ControlFlowGraph:
    """Split normalized instructions and add fall-through/jump edges."""
    instructions = tuple(instructions)
    exception_regions = tuple(exception_regions)
    if not instructions:
        return ControlFlowGraph((), (), -1, {})

    offsets = [instruction.offset for instruction in instructions]
    offset_set = set(offsets)
    leaders = {offsets[0]}
    for region in exception_regions:
        for offset in (region.start, region.target):
            if offset not in offset_set:
                raise ControlFlowError(
                    "Exception region references a missing offset",
                    version=(3, 11),
                    offset=offset,
                )
            leaders.add(offset)
        if region.end in offset_set:
            leaders.add(region.end)
    for index, instruction in enumerate(instructions):
        target = instruction_target(instruction)
        if target is not None:
            if target not in offset_set:
                raise ControlFlowError(
                    f"{instruction.kind} targets missing offset {target}",
                    version=(3, 11),
                    offset=instruction.offset,
                )
            leaders.add(target)
        if (
            target is not None or instruction.kind in TERMINATORS
        ) and index + 1 < len(instructions):
            leaders.add(instructions[index + 1].offset)

    leader_indices = sorted(offsets.index(offset) for offset in leaders)
    blocks = []
    offset_to_block = {}
    for block_index, start_index in enumerate(leader_indices):
        stop_index = (
            leader_indices[block_index + 1]
            if block_index + 1 < len(leader_indices)
            else len(instructions)
        )
        block_instructions = instructions[start_index:stop_index]
        end = (
            instructions[stop_index].offset
            if stop_index < len(instructions)
            else block_instructions[-1].offset + 2
        )
        block = BasicBlock(
            index=block_index,
            start=block_instructions[0].offset,
            end=end,
            instructions=block_instructions,
        )
        blocks.append(block)
        for instruction in block_instructions:
            offset_to_block[instruction.offset] = block_index

    edges = []
    for block in blocks:
        last = block.last
        target = instruction_target(last)
        next_block = (
            block.index + 1 if block.index + 1 < len(blocks) else None
        )
        if last.kind in TERMINATORS:
            continue
        if last.kind in UNCONDITIONAL_JUMPS:
            edges.append(
                Edge(block.index, offset_to_block[target], "jump")
            )
            continue
        if target is not None:
            jump_kind, fallthrough_kind = _edge_kinds(last.kind)
            edges.append(
                Edge(block.index, offset_to_block[target], jump_kind)
            )
            if next_block is not None:
                edges.append(
                    Edge(block.index, next_block, fallthrough_kind)
                )
            continue
        if next_block is not None:
            edges.append(Edge(block.index, next_block, "fallthrough"))

    for region in exception_regions:
        handler = offset_to_block[region.target]
        for block in blocks:
            if block.start >= region.start and block.start < region.end:
                edges.append(Edge(block.index, handler, "exception"))

    edges = sorted(set(edges))
    _mark_reachable(blocks, edges, 0)
    return ControlFlowGraph(
        blocks=tuple(blocks),
        edges=tuple(edges),
        entry=0,
        offset_to_block=offset_to_block,
    )
