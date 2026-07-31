"""Dominator, post-dominator, loop, and reducibility analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from decompyle3.controlflow.cfg import ControlFlowGraph
from decompyle3.errors import ControlFlowError


class IrreducibleControlFlowError(ControlFlowError):
    """Raised for an SCC with more than one external entry."""

    def __init__(
        self,
        message,
        *,
        component_blocks=(),
        entry_blocks=(),
        entry_edges=(),
    ):
        super().__init__(message)
        self.component_blocks = tuple(component_blocks)
        self.entry_blocks = tuple(entry_blocks)
        self.entry_edges = tuple(entry_edges)


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


def _bounded_diagnostic(items, formatter, limit=16):
    items = tuple(items)
    rendered = ", ".join(formatter(item) for item in items[:limit])
    if len(items) > limit:
        rendered += f", ... (+{len(items) - limit} more)"
    return rendered


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
    reachable = set(graph.reachable_blocks)
    members = {header, latch}
    pending = [] if latch == header else [latch]
    while pending:
        node = pending.pop()
        for predecessor in graph.predecessors(node):
            if predecessor in reachable and predecessor not in members:
                members.add(predecessor)
                pending.append(predecessor)
    return frozenset(members)


def _strong_components(graph) -> List[Set[int]]:
    """Return reachable SCCs without depending on Python recursion depth."""
    reachable = set(graph.reachable_blocks)
    successors = {node: set() for node in reachable}
    predecessors = {node: set() for node in reachable}
    for edge in graph.edges:
        if edge.source in reachable and edge.target in reachable:
            successors[edge.source].add(edge.target)
            predecessors[edge.target].add(edge.source)

    visited = set()
    finish_order = []
    for root in sorted(reachable):
        if root in visited:
            continue
        visited.add(root)
        pending = [(root, iter(sorted(successors[root])))]
        while pending:
            node, children = pending[-1]
            try:
                successor = next(children)
            except StopIteration:
                pending.pop()
                finish_order.append(node)
                continue
            if successor not in visited:
                visited.add(successor)
                pending.append(
                    (successor, iter(sorted(successors[successor])))
                )

    components = []
    assigned = set()
    for root in reversed(finish_order):
        if root in assigned:
            continue
        component = set()
        pending = [root]
        assigned.add(root)
        while pending:
            node = pending.pop()
            component.add(node)
            for predecessor in sorted(
                predecessors[node],
                reverse=True,
            ):
                if predecessor not in assigned:
                    assigned.add(predecessor)
                    pending.append(predecessor)
        components.append(component)
    return components


def _ensure_reducible(graph):
    reachable = set(graph.reachable_blocks)
    for component in _strong_components(graph):
        first = min(component)
        cyclic = len(component) > 1 or first in graph.successors(first)
        if not cyclic:
            continue
        entry_edges = tuple(
            sorted(
                (
                    edge.source,
                    edge.target,
                    edge.kind,
                )
                for edge in graph.edges
                if edge.source in reachable
                and edge.target in component
                and edge.source not in component
            )
        )
        entries = {target for _, target, _ in entry_edges}
        if len(entries) > 1:
            sorted_entries = tuple(sorted(entries))
            sorted_component = tuple(sorted(component))
            formatted = _bounded_diagnostic(
                sorted_entries,
                lambda entry: f"B{entry}",
            )
            component_text = _bounded_diagnostic(
                sorted_component,
                lambda block: f"B{block}",
            )
            edge_text = _bounded_diagnostic(
                entry_edges,
                lambda edge: f"B{edge[0]}->B{edge[1]}:{edge[2]}",
            )
            raise IrreducibleControlFlowError(
                "Irreducible control flow has multiple entries: "
                f"{formatted}; component: {component_text}; "
                f"entry edges: {edge_text}",
                component_blocks=sorted_component,
                entry_blocks=sorted_entries,
                entry_edges=entry_edges,
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
