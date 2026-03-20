"""Classify mxCell elements into BPMN types based on style attributes.

Parses the Draw.io style strings and XML structure to produce typed
:class:`BPMNElement` dataclass instances that downstream code
(:mod:`process_model`) can use to build a traversable process graph.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from xml.etree.ElementTree import Element


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BPMNType(str, Enum):
    """High-level BPMN element category."""

    EVENT = "event"
    TASK = "task"
    GATEWAY = "gateway"
    POOL = "pool"
    LANE = "lane"
    SEQUENCE_FLOW = "sequence_flow"
    ANNOTATION = "annotation"
    TEXT_LABEL = "text_label"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    START = "start"
    END = "end"
    INTERMEDIATE_CATCHING = "intermediate_catching"
    INTERMEDIATE_THROWING = "intermediate_throwing"
    BOUNDARY_INTERRUPTING = "boundary_interrupting"
    BOUNDARY_NON_INTERRUPTING = "boundary_non_interrupting"


class TaskMarker(str, Enum):
    ABSTRACT = "abstract"
    USER = "user"
    SERVICE = "service"
    SEND = "send"
    RECEIVE = "receive"
    MANUAL = "manual"
    BUSINESS_RULE = "business_rule"
    SCRIPT = "script"


class GatewayType(str, Enum):
    EXCLUSIVE = "exclusive"
    PARALLEL = "parallel"
    INCLUSIVE = "inclusive"
    EVENT_BASED = "event"
    COMPLEX = "complex"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BPMNElement:
    """A classified BPMN element extracted from Draw.io XML."""

    id: str
    bpmn_type: BPMNType
    label: str = ""
    parent_id: str = ""
    style: dict[str, str] = field(default_factory=dict)
    custom_properties: dict[str, str] = field(default_factory=dict)
    geometry: dict[str, float] = field(default_factory=dict)

    # Event-specific
    event_type: Optional[EventType] = None
    event_symbol: str = ""

    # Task-specific
    task_marker: Optional[TaskMarker] = None

    # Gateway-specific
    gateway_type: Optional[GatewayType] = None

    # Edge-specific (sequence flows)
    source_id: str = ""
    target_id: str = ""


# ---------------------------------------------------------------------------
# Style parser
# ---------------------------------------------------------------------------


def parse_style(style_str: str) -> dict[str, str]:
    """Parse a Draw.io semicolon-delimited style string into a dict.

    >>> parse_style("shape=mxgraph.bpmn.task;taskMarker=user;fontStyle=1;")
    {'shape': 'mxgraph.bpmn.task', 'taskMarker': 'user', 'fontStyle': '1'}
    """
    result: dict[str, str] = {}
    for part in style_str.rstrip(";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
            result[key.strip()] = value.strip()
        else:
            result[part] = "true"
    return result


# ---------------------------------------------------------------------------
# Element classification
# ---------------------------------------------------------------------------


_OUTLINE_TO_EVENT_TYPE = {
    "standard": EventType.START,
    "end": EventType.END,
    "catching": EventType.INTERMEDIATE_CATCHING,
    "eventInt": EventType.INTERMEDIATE_CATCHING,
    "throwing": EventType.INTERMEDIATE_THROWING,
    "eventNonint": EventType.INTERMEDIATE_THROWING,
    "boundInt": EventType.BOUNDARY_INTERRUPTING,
    "boundNonint": EventType.BOUNDARY_NON_INTERRUPTING,
}

_TASK_MARKER_MAP = {
    "abstract": TaskMarker.ABSTRACT,
    "user": TaskMarker.USER,
    "service": TaskMarker.SERVICE,
    "send": TaskMarker.SEND,
    "receive": TaskMarker.RECEIVE,
    "manual": TaskMarker.MANUAL,
    "business": TaskMarker.BUSINESS_RULE,
    "businessRule": TaskMarker.BUSINESS_RULE,
    "script": TaskMarker.SCRIPT,
}

_GATEWAY_TYPE_MAP = {
    "exclusive": GatewayType.EXCLUSIVE,
    "parallel": GatewayType.PARALLEL,
    "inclusive": GatewayType.INCLUSIVE,
    "event": GatewayType.EVENT_BASED,
    "complex": GatewayType.COMPLEX,
}


def _classify_shape(style: dict[str, str]) -> BPMNType:
    """Determine the BPMN type from the parsed style dict."""
    shape = style.get("shape", "")

    if "bpmn.event" in shape:
        return BPMNType.EVENT
    if "bpmn.task" in shape:
        return BPMNType.TASK
    if "bpmn.pool" in shape:
        return BPMNType.POOL
    if "bpmn.lane" in shape:
        return BPMNType.LANE
    # Gateways in our diagram use shape=mxgraph.bpmn.shape with gwType
    if "gwType" in style:
        return BPMNType.GATEWAY
    if shape in ("note2", "mxgraph.bpmn.annotation"):
        return BPMNType.ANNOTATION
    if "text" in style:
        return BPMNType.TEXT_LABEL

    return BPMNType.UNKNOWN


def _extract_label(xml_element: Element) -> str:
    """Extract the display label from an mxCell or object wrapper.

    Returns cleaned text with XML entities decoded and ``&#xa;``
    newlines replaced with actual newlines.
    """
    # object wrappers use 'label', mxCell uses 'value'
    raw = xml_element.get("label") or xml_element.get("value") or ""
    # Decode common XML entities / HTML entities
    text = html.unescape(raw)
    # Draw.io uses &#xa; for newlines in attribute values (already decoded
    # by the XML parser to \n in most cases, but handle explicit ones too)
    text = text.replace("&#xa;", "\n")
    return text.strip()


def _extract_geometry(cell: Element) -> dict[str, float]:
    """Pull x, y, width, height from the nested <mxGeometry>."""
    geom = cell.find("mxGeometry")
    if geom is None:
        # For object-wrapped cells, look inside the inner mxCell
        inner = cell.find("mxCell")
        if inner is not None:
            geom = inner.find("mxGeometry")
    if geom is None:
        return {}
    result: dict[str, float] = {}
    for attr in ("x", "y", "width", "height"):
        val = geom.get(attr)
        if val is not None:
            result[attr] = float(val)
    return result


def _extract_custom_properties(element: Element) -> dict[str, str]:
    """Extract custom properties from an <object> wrapper.

    Custom properties are all attributes on the <object> tag except
    ``id`` and ``label`` (which are structural).
    """
    _STRUCTURAL = {"id", "label"}
    return {
        k: v
        for k, v in element.attrib.items()
        if k not in _STRUCTURAL
    }


def _get_cell_and_style(element: Element) -> tuple[Element, dict[str, str]]:
    """Return (mxCell_element, parsed_style) for a top-level graph element.

    Handles both plain ``<mxCell>`` and ``<object>``-wrapped cells.
    """
    if element.tag == "mxCell":
        return element, parse_style(element.get("style", ""))

    # Object wrapper – the mxCell is a child
    inner = element.find("mxCell")
    if inner is not None:
        return inner, parse_style(inner.get("style", ""))

    return element, {}


def classify_element(element: Element) -> Optional[BPMNElement]:
    """Classify a single XML element from the Draw.io ``<root>``.

    Parameters
    ----------
    element:
        An ``<mxCell>`` or ``<object>`` (or custom-tagged) element from
        the graph model's ``<root>``.

    Returns
    -------
    BPMNElement or None
        ``None`` for base structural cells (id 0 / 1) and elements that
        have no style (pure grouping cells).
    """
    cell, style = _get_cell_and_style(element)
    if not style:
        return None

    elem_id = element.get("id", "")
    parent_id = cell.get("parent", "")
    label = _extract_label(element)
    geometry = _extract_geometry(element)
    custom_props = (
        _extract_custom_properties(element)
        if element.tag != "mxCell"
        else {}
    )

    # Check if this is an edge (sequence flow)
    if cell.get("edge") == "1":
        return BPMNElement(
            id=elem_id,
            bpmn_type=BPMNType.SEQUENCE_FLOW,
            label=label,
            parent_id=parent_id,
            style=style,
            custom_properties=custom_props,
            geometry=geometry,
            source_id=cell.get("source", ""),
            target_id=cell.get("target", ""),
        )

    bpmn_type = _classify_shape(style)

    result = BPMNElement(
        id=elem_id,
        bpmn_type=bpmn_type,
        label=label,
        parent_id=parent_id,
        style=style,
        custom_properties=custom_props,
        geometry=geometry,
    )

    # Enrich based on type
    if bpmn_type == BPMNType.EVENT:
        outline = style.get("outline", "")
        result.event_type = _OUTLINE_TO_EVENT_TYPE.get(outline)
        result.event_symbol = style.get("symbol", "general")

    elif bpmn_type == BPMNType.TASK:
        marker_raw = style.get("taskMarker", "abstract")
        result.task_marker = _TASK_MARKER_MAP.get(marker_raw, TaskMarker.ABSTRACT)

    elif bpmn_type == BPMNType.GATEWAY:
        gw_raw = style.get("gwType", "")
        result.gateway_type = _GATEWAY_TYPE_MAP.get(gw_raw)

    return result


def classify_all(graph_model: Element) -> list[BPMNElement]:
    """Classify every element in an ``<mxGraphModel>`` root.

    Parameters
    ----------
    graph_model:
        The ``<mxGraphModel>`` element (as returned by
        :func:`drawio_loader.load_drawio`).

    Returns
    -------
    list[BPMNElement]
        All classified elements (structural cells ``0``/``1`` are
        excluded).
    """
    root = graph_model.find("root")
    if root is None:
        return []

    elements: list[BPMNElement] = []
    for child in root:
        classified = classify_element(child)
        if classified is not None:
            elements.append(classified)
    return elements
