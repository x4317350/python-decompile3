"""Dominator, post-dominator, loop, and reducibility analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from decompyle3.controlflow.cfg import ControlFlowGraph


class IrreducibleControlFlowError(Exception):
    """Raised for an SCC with more than one external entry."""


@dataclass(frozen=True)
class NaturalLoop:
    header: int
    latch: int
    blocks: FrozenSet[int]


@dataclass(frozen=True)
class ControlFlowAnalysis:
    dominators: Dict[int, FrozenSet[int]]
    immediate_dominators: Dict[int, Optional[int]]
    post_dominators: Dict[int, FrozenSet[int]]
    immediate_post_dominators: Dict[int, Optional[int]]
    back_edges: Tuple[Tuple[int, int], ...]
    loops: Tuple[NaturalLoop, ...]


def _fixed_point_sets(graph, reverse=False):
    nodes = set(graph.reachable_blocks)
    if not nodes:
        return {}

    if reverse:
        roots = {
            node for node in nodes if not set(graph.successors(node)) & nodes
        }
        predecessor = graph.successors
    else:
        roots = {graph.entry}
        predecessor = graph.predecessors

    result = {
        node: frozenset({node}) if node in roots else frozenset(nodes)
        for node in nodes
    }
    changed = True
    while changed:
        changed = False
        for node in sorted(nodes):
            if node in roots:
                continue
            incoming = [
                result[parent]
                for parent in predecessor(node)
                if parent in nodes
            ]
            shared = set.intersection(*(set(item) for item in incoming)) if incoming else set()
            updated = frozenset({node} | shared)
            if updated != result[node]:
                result[node] = updated
                changed = True
    return result


def _immediate(sets):
    result = {}
    for node, members in sets.items():
        strict = set(members) - {node}
        immediate = None
        for candidate in strict:
            if all(
                other == candidate or other in sets[candidate]
                for other in strict
            ):
                immediate = candidate
                break
        result[node] = immediate
    return result


def _natural_loop(graph, latch: int, header: int) -> FrozenSet[int]:
    members = {header, latch}
    pending = [] if latch == header else [latch]
    while pending:
        node = pending.pop()
        for predecessor in graph.predecessors(node):
            if predecessor not in members:
                members.add(predecessor)
                pending.append(predecessor)
    return frozenset(members)


def _strong_components(graph) -> List[Set[int]]:
    reachable = set(graph.reachable_blocks)
    index = 0
    indices = {}
    lowlinks = {}
    stack = []
    on_stack = set()
    components = []

    def visit(node):
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for successor in graph.successors(node):
            if successor not in reachable:
                continue
            if successor not in indices:
                visit(successor)
                lowlinks[node] = min(lowlinks[node], lowlinks[successor])
            elif successor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[successor])

        if lowlinks[node] == indices[node]:
            component = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in sorted(reachable):
        if node not in indices:
            visit(node)
    return components


def _ensure_reducible(graph):
    for component in _strong_components(graph):
        cyclic = len(component) > 1 or any(
            successor == next(iter(component))
            for successor in graph.successors(next(iter(component)))
        )
        if not cyclic:
            continue
        entries = {
            edge.target
            for edge in graph.edges
            if edge.target in component and edge.source not in component
        }
        if len(entries) > 1:
            formatted = ", ".join(f"B{entry}" for entry in sorted(entries))
            raise IrreducibleControlFlowError(
                f"Irreducible control flow has multiple entries: {formatted}"
            )


def analyze_control_flow(graph: ControlFlowGraph) -> ControlFlowAnalysis:
    """Return all stage-4 graph analyses and reject irreducible graphs."""
    _ensure_reducible(graph)
    dominators = _fixed_point_sets(graph)
    post_dominators = _fixed_point_sets(graph, reverse=True)
    back_edges = tuple(
        sorted(
            (edge.source, edge.target)
            for edge in graph.edges
            if edge.target in dominators.get(edge.source, ())
        )
    )
    loops = tuple(
        NaturalLoop(
            header=header,
            latch=latch,
            blocks=_natural_loop(graph, latch, header),
        )
        for latch, header in back_edges
    )
    return ControlFlowAnalysis(
        dominators=dominators,
        immediate_dominators=_immediate(dominators),
        post_dominators=post_dominators,
        immediate_post_dominators=_immediate(post_dominators),
        back_edges=back_edges,
        loops=loops,
    )
