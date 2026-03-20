"""Tests for the BPMN element classifier."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from mcp_sh_bpmn.bpmn_classifier import (
    BPMNElement,
    BPMNType,
    EventType,
    GatewayType,
    TaskMarker,
    classify_all,
    classify_element,
    parse_style,
)


# ---------------------------------------------------------------------------
# parse_style
# ---------------------------------------------------------------------------


class TestParseStyle:
    def test_basic_key_value_pairs(self):
        result = parse_style("shape=mxgraph.bpmn.task;taskMarker=user;fontStyle=1;")
        assert result == {
            "shape": "mxgraph.bpmn.task",
            "taskMarker": "user",
            "fontStyle": "1",
        }

    def test_bare_style_names(self):
        result = parse_style("text;html=0;align=center;")
        assert result["text"] == "true"
        assert result["html"] == "0"

    def test_empty_string(self):
        assert parse_style("") == {}

    def test_trailing_semicolons(self):
        result = parse_style("rounded=1;;;")
        assert result == {"rounded": "1"}

    def test_no_trailing_semicolon(self):
        result = parse_style("shape=rect;fillColor=#fff")
        assert result == {"shape": "rect", "fillColor": "#fff"}


# ---------------------------------------------------------------------------
# classify_element - Events
# ---------------------------------------------------------------------------


class TestClassifyEvents:
    def test_start_event(self):
        xml = '<mxCell id="s1" style="shape=mxgraph.bpmn.event;outline=standard;symbol=general;" vertex="1" parent="lane1"><mxGeometry x="60" y="90" width="40" height="40" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.EVENT
        assert elem.event_type == EventType.START
        assert elem.event_symbol == "general"
        assert elem.id == "s1"

    def test_end_event_terminate(self):
        xml = '<mxCell id="e1" style="shape=mxgraph.bpmn.event;outline=end;symbol=terminate;" vertex="1" parent="lane1"><mxGeometry x="100" y="100" width="40" height="40" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.EVENT
        assert elem.event_type == EventType.END
        assert elem.event_symbol == "terminate"

    def test_intermediate_catching_event(self):
        xml = '<mxCell id="ic1" style="shape=mxgraph.bpmn.event;outline=catching;symbol=message;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.event_type == EventType.INTERMEDIATE_CATCHING

    def test_boundary_interrupting(self):
        xml = '<mxCell id="bi1" style="shape=mxgraph.bpmn.event;outline=boundInt;symbol=timer;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.event_type == EventType.BOUNDARY_INTERRUPTING


# ---------------------------------------------------------------------------
# classify_element - Tasks
# ---------------------------------------------------------------------------


class TestClassifyTasks:
    def test_user_task(self):
        xml = '<mxCell id="t1" style="shape=mxgraph.bpmn.task;taskMarker=user;bpmnShapeType=task;" vertex="1" parent="lane1"><mxGeometry x="160" y="80" width="180" height="60" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.TASK
        assert elem.task_marker == TaskMarker.USER

    def test_service_task(self):
        xml = '<mxCell id="t2" style="shape=mxgraph.bpmn.task;taskMarker=service;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.task_marker == TaskMarker.SERVICE

    def test_send_task(self):
        xml = '<mxCell id="t3" style="shape=mxgraph.bpmn.task;taskMarker=send;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.task_marker == TaskMarker.SEND

    def test_manual_task(self):
        xml = '<mxCell id="t4" style="shape=mxgraph.bpmn.task;taskMarker=manual;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.task_marker == TaskMarker.MANUAL

    def test_business_rule_task(self):
        xml = '<mxCell id="t5" style="shape=mxgraph.bpmn.task;taskMarker=business;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.task_marker == TaskMarker.BUSINESS_RULE

    def test_abstract_task(self):
        xml = '<mxCell id="t6" style="shape=mxgraph.bpmn.task;taskMarker=abstract;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.task_marker == TaskMarker.ABSTRACT

    def test_task_with_object_wrapper(self):
        xml = """
        <object label="Create Epics" agent="agent_plan_software-architect" phase="1" phase_name="Planning" mandatory="true" id="task_create_epics">
          <mxCell style="shape=mxgraph.bpmn.task;taskMarker=user;bpmnShapeType=task;" vertex="1" parent="lane1">
            <mxGeometry x="160" y="80" width="180" height="60" as="geometry"/>
          </mxCell>
        </object>
        """
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.TASK
        assert elem.task_marker == TaskMarker.USER
        assert elem.label == "Create Epics"
        assert elem.id == "task_create_epics"
        assert elem.custom_properties["agent"] == "agent_plan_software-architect"
        assert elem.custom_properties["phase"] == "1"
        assert elem.custom_properties["phase_name"] == "Planning"
        assert elem.custom_properties["mandatory"] == "true"

    def test_task2_variant(self):
        xml = '<mxCell id="t7" style="shape=mxgraph.bpmn.task2;taskMarker=script;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.TASK
        assert elem.task_marker == TaskMarker.SCRIPT


# ---------------------------------------------------------------------------
# classify_element - Gateways
# ---------------------------------------------------------------------------


class TestClassifyGateways:
    def test_exclusive_gateway(self):
        xml = '<mxCell id="gw1" value="Arch Valid?" style="shape=mxgraph.bpmn.shape;symbol=exclusiveGw;gwType=exclusive;" vertex="1" parent="lane1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.GATEWAY
        assert elem.gateway_type == GatewayType.EXCLUSIVE
        assert elem.label == "Arch Valid?"

    def test_parallel_gateway(self):
        xml = '<mxCell id="gw2" value="+" style="shape=mxgraph.bpmn.shape;symbol=parallelGw;gwType=parallel;" vertex="1" parent="lane1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.GATEWAY
        assert elem.gateway_type == GatewayType.PARALLEL

    def test_inclusive_gateway(self):
        xml = '<mxCell id="gw3" style="shape=mxgraph.bpmn.gateway2;gwType=inclusive;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.GATEWAY
        assert elem.gateway_type == GatewayType.INCLUSIVE

    def test_merge_gateway_no_label(self):
        xml = '<mxCell id="gw_merge" value="" style="shape=mxgraph.bpmn.shape;symbol=exclusiveGw;gwType=exclusive;" vertex="1" parent="lane1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.GATEWAY
        assert elem.gateway_type == GatewayType.EXCLUSIVE
        assert elem.label == ""


# ---------------------------------------------------------------------------
# classify_element - Pools & Lanes
# ---------------------------------------------------------------------------


class TestClassifyPoolsLanes:
    def test_pool(self):
        xml = '<mxCell id="pool1" value="My Process" style="shape=mxgraph.bpmn.pool;startSize=30;" vertex="1" parent="1"><mxGeometry x="20" y="20" width="4900" height="3100" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.POOL
        assert elem.label == "My Process"
        assert elem.geometry["width"] == 4900.0

    def test_lane(self):
        xml = '<mxCell id="lane1" value="Planning" style="shape=mxgraph.bpmn.lane;startSize=40;" vertex="1" parent="pool1"><mxGeometry y="30" width="4900" height="440" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.LANE
        assert elem.label == "Planning"
        assert elem.parent_id == "pool1"


# ---------------------------------------------------------------------------
# classify_element - Sequence Flows (Edges)
# ---------------------------------------------------------------------------


class TestClassifySequenceFlows:
    def test_basic_flow(self):
        xml = '<mxCell id="f1" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=1;strokeWidth=2;" edge="1" source="task1" target="gw1" parent="lane1"><mxGeometry relative="1" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.SEQUENCE_FLOW
        assert elem.source_id == "task1"
        assert elem.target_id == "gw1"

    def test_labeled_flow(self):
        xml = '<mxCell id="f2" value="Yes" style="edgeStyle=orthogonalEdgeStyle;rounded=1;strokeWidth=2;fontStyle=1;" edge="1" source="gw1" target="task2" parent="lane1"><mxGeometry relative="1" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.SEQUENCE_FLOW
        assert elem.label == "Yes"
        assert elem.source_id == "gw1"

    def test_dashed_loopback_flow(self):
        xml = '<mxCell id="f3" value="No" style="edgeStyle=orthogonalEdgeStyle;rounded=1;dashed=1;dashPattern=8 4;strokeWidth=2;" edge="1" source="gw1" target="task1" parent="lane1"><mxGeometry relative="1" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.SEQUENCE_FLOW
        assert elem.label == "No"
        assert elem.style.get("dashed") == "1"


# ---------------------------------------------------------------------------
# classify_element - Annotations & Text Labels
# ---------------------------------------------------------------------------


class TestClassifyAnnotations:
    def test_note_annotation(self):
        xml = '<mxCell id="n1" value="RULE: Only Security Scanner" style="shape=note2;boundedLbl=1;whiteSpace=wrap;" vertex="1" parent="lane1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.ANNOTATION

    def test_text_label(self):
        xml = '<mxCell id="tl1" value="PHASE 1: Planning" style="text;html=0;align=left;fontSize=12;" vertex="1" parent="lane1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.TEXT_LABEL


# ---------------------------------------------------------------------------
# classify_element - Edge cases
# ---------------------------------------------------------------------------


class TestClassifyEdgeCases:
    def test_base_cell_returns_none(self):
        xml = '<mxCell id="0"/>'
        assert classify_element(ET.fromstring(xml)) is None

    def test_no_style_returns_none(self):
        xml = '<mxCell id="1" parent="0"/>'
        assert classify_element(ET.fromstring(xml)) is None

    def test_unknown_shape(self):
        xml = '<mxCell id="u1" style="shape=someCustomShape;rounded=1;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.bpmn_type == BPMNType.UNKNOWN

    def test_geometry_extraction(self):
        xml = '<mxCell id="g1" style="shape=mxgraph.bpmn.task;taskMarker=user;" vertex="1" parent="1"><mxGeometry x="100" y="200" width="180" height="60" as="geometry"/></mxCell>'
        elem = classify_element(ET.fromstring(xml))
        assert elem.geometry == {"x": 100.0, "y": 200.0, "width": 180.0, "height": 60.0}

    def test_object_wrapper_custom_properties(self):
        xml = """
        <object id="t1" label="My Task" agent="my_agent" rule="must do X" status_to="done">
          <mxCell style="shape=mxgraph.bpmn.task;taskMarker=user;" vertex="1" parent="1"/>
        </object>
        """
        elem = classify_element(ET.fromstring(xml))
        assert elem.custom_properties == {
            "agent": "my_agent",
            "rule": "must do X",
            "status_to": "done",
        }
        # id and label should NOT be in custom_properties
        assert "id" not in elem.custom_properties
        assert "label" not in elem.custom_properties

    def test_html_entity_in_label(self):
        xml = '<mxCell id="h1" value="Create Test Cases&#xa;&amp; Suites" style="shape=mxgraph.bpmn.task;taskMarker=abstract;" vertex="1" parent="1"/>'
        elem = classify_element(ET.fromstring(xml))
        assert "& Suites" in elem.label


# ---------------------------------------------------------------------------
# classify_all - Full graph model
# ---------------------------------------------------------------------------


class TestClassifyAll:
    def _make_graph_model(self, *cells: str) -> ET.Element:
        xml = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
        xml += "".join(cells)
        xml += "</root></mxGraphModel>"
        return ET.fromstring(xml)

    def test_classifies_mixed_elements(self):
        model = self._make_graph_model(
            '<mxCell id="pool1" value="Pool" style="shape=mxgraph.bpmn.pool;" vertex="1" parent="1"/>',
            '<mxCell id="lane1" value="Lane" style="shape=mxgraph.bpmn.lane;" vertex="1" parent="pool1"/>',
            '<mxCell id="s1" style="shape=mxgraph.bpmn.event;outline=standard;symbol=general;" vertex="1" parent="lane1"/>',
            '<mxCell id="t1" style="shape=mxgraph.bpmn.task;taskMarker=user;" vertex="1" parent="lane1"/>',
            '<mxCell id="gw1" style="shape=mxgraph.bpmn.shape;gwType=exclusive;" vertex="1" parent="lane1"/>',
            '<mxCell id="f1" style="edgeStyle=orthogonalEdgeStyle;" edge="1" source="s1" target="t1" parent="lane1"/>',
        )
        elements = classify_all(model)
        types = {e.id: e.bpmn_type for e in elements}
        assert types["pool1"] == BPMNType.POOL
        assert types["lane1"] == BPMNType.LANE
        assert types["s1"] == BPMNType.EVENT
        assert types["t1"] == BPMNType.TASK
        assert types["gw1"] == BPMNType.GATEWAY
        assert types["f1"] == BPMNType.SEQUENCE_FLOW

    def test_excludes_base_cells(self):
        model = self._make_graph_model(
            '<mxCell id="t1" style="shape=mxgraph.bpmn.task;taskMarker=user;" vertex="1" parent="1"/>',
        )
        elements = classify_all(model)
        ids = {e.id for e in elements}
        assert "0" not in ids
        assert "1" not in ids
        assert "t1" in ids

    def test_handles_object_wrappers(self):
        model = self._make_graph_model(
            '<object id="t1" label="My Task" agent="my_agent"><mxCell style="shape=mxgraph.bpmn.task;taskMarker=user;" vertex="1" parent="1"/></object>',
        )
        elements = classify_all(model)
        assert len(elements) == 1
        assert elements[0].label == "My Task"
        assert elements[0].custom_properties["agent"] == "my_agent"

    def test_no_root_returns_empty(self):
        model = ET.fromstring("<mxGraphModel/>")
        assert classify_all(model) == []

    def test_with_real_diagram(self):
        """Test classification against the actual dev-workflow.drawio."""
        from mcp_sh_bpmn.drawio_loader import load_drawio

        model = load_drawio("diagrams/dev-workflow.drawio")
        elements = classify_all(model)

        by_type: dict[BPMNType, list[BPMNElement]] = {}
        for e in elements:
            by_type.setdefault(e.bpmn_type, []).append(e)

        # Verify expected counts from the diagram
        assert len(by_type[BPMNType.POOL]) == 1
        assert len(by_type[BPMNType.LANE]) == 7
        assert len(by_type[BPMNType.TASK]) == 23
        assert len(by_type[BPMNType.EVENT]) == 2
        assert len(by_type[BPMNType.GATEWAY]) >= 7
        assert len(by_type[BPMNType.SEQUENCE_FLOW]) >= 40

        # Verify specific task properties
        tasks_by_id = {e.id: e for e in by_type[BPMNType.TASK]}

        create_epics = tasks_by_id["task_create_epics"]
        assert create_epics.custom_properties["agent"] == "agent_plan_software-architect"
        assert create_epics.task_marker == TaskMarker.USER

        security_scan = tasks_by_id["task_security_scan"]
        assert security_scan.custom_properties["rule"] == "Only agent that can mark DONE"
        assert security_scan.custom_properties["status_to"] == "done"

        # Verify start and end events
        events = by_type[BPMNType.EVENT]
        start_events = [e for e in events if e.event_type == EventType.START]
        end_events = [e for e in events if e.event_type == EventType.END]
        assert len(start_events) == 1
        assert len(end_events) == 1
        assert end_events[0].event_symbol == "terminate"

        # Verify gateways
        gateways = by_type[BPMNType.GATEWAY]
        exclusive_gws = [g for g in gateways if g.gateway_type == GatewayType.EXCLUSIVE]
        parallel_gws = [g for g in gateways if g.gateway_type == GatewayType.PARALLEL]
        assert len(exclusive_gws) >= 4  # arch, reqs, frontend, issues, more_stories
        assert len(parallel_gws) == 2  # open + close
