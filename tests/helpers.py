"""Shared test factory functions for BPMN element creation."""

from __future__ import annotations

from mcp_sh_bpmn.bpmn_classifier import (
    BPMNElement,
    BPMNType,
    EventType,
    GatewayType,
    TaskMarker,
)


def make_event(id: str, event_type: EventType, parent: str = "lane1") -> BPMNElement:
    return BPMNElement(
        id=id, bpmn_type=BPMNType.EVENT, event_type=event_type, parent_id=parent
    )


def make_task(
    id: str,
    label: str = "",
    parent: str = "lane1",
    marker: TaskMarker = TaskMarker.USER,
    **custom_props: str,
) -> BPMNElement:
    return BPMNElement(
        id=id,
        bpmn_type=BPMNType.TASK,
        label=label,
        parent_id=parent,
        task_marker=marker,
        custom_properties=custom_props,
    )


def make_gateway(
    id: str,
    gw_type: GatewayType = GatewayType.EXCLUSIVE,
    label: str = "",
    parent: str = "lane1",
) -> BPMNElement:
    return BPMNElement(
        id=id,
        bpmn_type=BPMNType.GATEWAY,
        label=label,
        gateway_type=gw_type,
        parent_id=parent,
    )


def make_flow(
    id: str, source: str, target: str, label: str = ""
) -> BPMNElement:
    return BPMNElement(
        id=id,
        bpmn_type=BPMNType.SEQUENCE_FLOW,
        label=label,
        source_id=source,
        target_id=target,
    )


def make_lane(id: str, label: str = "", parent: str = "pool1") -> BPMNElement:
    return BPMNElement(
        id=id, bpmn_type=BPMNType.LANE, label=label, parent_id=parent
    )


def make_pool(id: str, label: str = "") -> BPMNElement:
    return BPMNElement(id=id, bpmn_type=BPMNType.POOL, label=label, parent_id="1")
