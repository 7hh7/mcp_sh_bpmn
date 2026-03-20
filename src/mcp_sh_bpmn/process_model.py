"""In-memory directed graph representing a BPMN process.

Constructed from a list of :class:`~bpmn_classifier.BPMNElement` instances,
the :class:`ProcessModel` provides query methods that an orchestrator can
call to navigate the workflow one step at a time.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .bpmn_classifier import (
    BPMNElement,
    BPMNType,
    EventType,
    GatewayType,
    classify_all,
)
from .drawio_loader import load_drawio


# ---------------------------------------------------------------------------
# Edge helper
# ---------------------------------------------------------------------------


@dataclass
class FlowEdge:
    """A directed edge in the process graph (sequence flow)."""

    id: str
    source_id: str
    target_id: str
    label: str = ""
    style: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ProcessModel
# ---------------------------------------------------------------------------


class ProcessModel:
    """Queryable in-memory graph of a BPMN process.

    Parameters
    ----------
    elements:
        Classified BPMN elements from :func:`bpmn_classifier.classify_all`.
    """

    def __init__(self, elements: list[BPMNElement]) -> None:
        self._nodes: dict[str, BPMNElement] = {}
        self._edges: dict[str, FlowEdge] = {}
        # adjacency: node_id -> list of outgoing FlowEdge
        self._outgoing: dict[str, list[FlowEdge]] = {}
        # reverse adjacency: node_id -> list of incoming FlowEdge
        self._incoming: dict[str, list[FlowEdge]] = {}

        for elem in elements:
            if elem.bpmn_type == BPMNType.SEQUENCE_FLOW:
                edge = FlowEdge(
                    id=elem.id,
                    source_id=elem.source_id,
                    target_id=elem.target_id,
                    label=elem.label,
                    style=elem.style,
                )
                self._edges[edge.id] = edge
                self._outgoing.setdefault(edge.source_id, []).append(edge)
                self._incoming.setdefault(edge.target_id, []).append(edge)
            else:
                self._nodes[elem.id] = elem

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_drawio(cls, file_path: str, page: str | int | None = None) -> ProcessModel:
        """Load a .drawio file and build a ProcessModel in one step."""
        graph_model = load_drawio(file_path, page=page)
        elements = classify_all(graph_model)
        return cls(elements)

    # ------------------------------------------------------------------
    # Node access
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[BPMNElement]:
        """Return a node by ID, or ``None`` if not found."""
        return self._nodes.get(node_id)

    def get_nodes_by_type(self, bpmn_type: BPMNType) -> list[BPMNElement]:
        """Return all nodes of a given BPMN type."""
        return [n for n in self._nodes.values() if n.bpmn_type == bpmn_type]

    def get_all_tasks(self) -> list[BPMNElement]:
        """Return all task nodes."""
        return self.get_nodes_by_type(BPMNType.TASK)

    def get_all_gateways(self) -> list[BPMNElement]:
        """Return all gateway nodes."""
        return self.get_nodes_by_type(BPMNType.GATEWAY)

    def get_start_events(self) -> list[BPMNElement]:
        """Return all start event nodes."""
        return [
            n for n in self._nodes.values()
            if n.bpmn_type == BPMNType.EVENT and n.event_type == EventType.START
        ]

    def get_end_events(self) -> list[BPMNElement]:
        """Return all end event nodes."""
        return [
            n for n in self._nodes.values()
            if n.bpmn_type == BPMNType.EVENT and n.event_type == EventType.END
        ]

    # ------------------------------------------------------------------
    # Custom properties
    # ------------------------------------------------------------------

    def get_custom_properties(self, node_id: str) -> dict[str, str]:
        """Return custom properties for a node (empty dict if not found)."""
        node = self._nodes.get(node_id)
        return node.custom_properties if node else {}

    def get_agent(self, node_id: str) -> str:
        """Return the ``agent`` custom property for a task, or ``""``."""
        return self.get_custom_properties(node_id).get("agent", "")

    # ------------------------------------------------------------------
    # Lane / Pool membership
    # ------------------------------------------------------------------

    def get_lane(self, node_id: str) -> Optional[BPMNElement]:
        """Return the lane that contains *node_id*, or ``None``."""
        node = self._nodes.get(node_id)
        if node is None:
            return None
        parent = self._nodes.get(node.parent_id)
        if parent is not None and parent.bpmn_type == BPMNType.LANE:
            return parent
        return None

    def get_pool(self, node_id: str) -> Optional[BPMNElement]:
        """Return the pool that ultimately contains *node_id*."""
        node = self._nodes.get(node_id)
        if node is None:
            return None
        # Walk parent chain
        current = node
        for _ in range(10):  # safety limit
            parent = self._nodes.get(current.parent_id)
            if parent is None:
                return None
            if parent.bpmn_type == BPMNType.POOL:
                return parent
            current = parent
        return None

    def get_tasks_in_lane(self, lane_id: str) -> list[BPMNElement]:
        """Return all tasks whose parent is *lane_id*."""
        return [
            n for n in self._nodes.values()
            if n.bpmn_type == BPMNType.TASK and n.parent_id == lane_id
        ]

    def get_lanes(self) -> list[BPMNElement]:
        """Return all lane nodes."""
        return self.get_nodes_by_type(BPMNType.LANE)

    def get_pools(self) -> list[BPMNElement]:
        """Return all pool nodes."""
        return self.get_nodes_by_type(BPMNType.POOL)

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def get_outgoing(self, node_id: str) -> list[FlowEdge]:
        """Return all outgoing edges from *node_id*."""
        return list(self._outgoing.get(node_id, []))

    def get_incoming(self, node_id: str) -> list[FlowEdge]:
        """Return all incoming edges to *node_id*."""
        return list(self._incoming.get(node_id, []))

    def get_next(
        self, node_id: str, condition: str | None = None
    ) -> list[BPMNElement]:
        """Return the next node(s) reachable from *node_id*.

        If *condition* is provided, only follow edges whose label matches
        (case-insensitive prefix match, e.g. ``"yes"`` matches ``"Yes (Rework)"``).
        If *condition* is ``None``, return all successors.
        """
        edges = self._outgoing.get(node_id, [])
        if condition is not None:
            cond_lower = condition.lower()
            edges = [
                e for e in edges
                if e.label.lower().startswith(cond_lower)
            ]
        result = []
        for edge in edges:
            target = self._nodes.get(edge.target_id)
            if target is not None:
                result.append(target)
        return result

    def get_predecessors(self, node_id: str) -> list[BPMNElement]:
        """Return all predecessor nodes (nodes with edges into *node_id*)."""
        edges = self._incoming.get(node_id, [])
        result = []
        for edge in edges:
            source = self._nodes.get(edge.source_id)
            if source is not None:
                result.append(source)
        return result

    def get_path(
        self, from_id: str, to_id: str, max_depth: int = 50
    ) -> list[BPMNElement]:
        """Return an ordered list of nodes from *from_id* to *to_id* (BFS).

        Returns an empty list if no path is found within *max_depth* hops.
        The returned path includes both endpoints.
        """
        if from_id == to_id:
            node = self._nodes.get(from_id)
            return [node] if node else []

        # BFS
        visited: set[str] = set()
        queue: deque[list[str]] = deque([[from_id]])

        while queue:
            path = queue.popleft()
            if len(path) > max_depth:
                continue
            current = path[-1]
            if current in visited:
                continue
            visited.add(current)

            for edge in self._outgoing.get(current, []):
                next_id = edge.target_id
                if next_id in visited:
                    continue
                new_path = path + [next_id]
                if next_id == to_id:
                    return [
                        self._nodes[nid]
                        for nid in new_path
                        if nid in self._nodes
                    ]
                queue.append(new_path)

        return []

    # ------------------------------------------------------------------
    # Phase queries (using custom properties)
    # ------------------------------------------------------------------

    def get_tasks_in_phase(self, phase: str | int) -> list[BPMNElement]:
        """Return all tasks with a ``phase`` custom property matching *phase*."""
        phase_str = str(phase)
        return [
            n for n in self._nodes.values()
            if n.bpmn_type == BPMNType.TASK
            and n.custom_properties.get("phase") == phase_str
        ]

    def get_phases(self) -> list[dict[str, str]]:
        """Return a summary of all phases found in task custom properties.

        Returns a list of ``{"phase": "1", "phase_name": "Planning"}``
        dicts, deduplicated and sorted by phase number.
        """
        seen: dict[str, str] = {}
        for node in self._nodes.values():
            if node.bpmn_type == BPMNType.TASK:
                phase = node.custom_properties.get("phase", "")
                name = node.custom_properties.get("phase_name", "")
                if phase and phase not in seen:
                    seen[phase] = name
        return [
            {"phase": p, "phase_name": seen[p]}
            for p in sorted(seen.keys(), key=lambda x: int(x) if x.isdigit() else 0)
        ]

    # ------------------------------------------------------------------
    # Status transition queries
    # ------------------------------------------------------------------

    def get_transition_rules(self, node_id: str) -> dict[str, str]:
        """Return status transition info for a node.

        Returns a dict with keys ``status_from``, ``status_to``, and
        ``rule`` (any of which may be empty strings).
        """
        props = self.get_custom_properties(node_id)
        return {
            "status_from": props.get("status_from", ""),
            "status_to": props.get("status_to", ""),
            "rule": props.get("rule", ""),
        }

    # ------------------------------------------------------------------
    # Summary / overview
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Return a compact overview of the process model."""
        return {
            "pools": len(self.get_pools()),
            "lanes": len(self.get_lanes()),
            "tasks": len(self.get_all_tasks()),
            "gateways": len(self.get_all_gateways()),
            "events": len(self.get_nodes_by_type(BPMNType.EVENT)),
            "sequence_flows": len(self._edges),
            "phases": self.get_phases(),
        }
