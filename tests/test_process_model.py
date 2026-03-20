"""Tests for the ProcessModel in-memory graph."""

from __future__ import annotations

import pytest

from mcp_sh_bpmn.bpmn_classifier import (
    BPMNElement,
    BPMNType,
    EventType,
    GatewayType,
    TaskMarker,
)
from mcp_sh_bpmn.process_model import FlowEdge, ProcessModel

from helpers import make_event, make_flow, make_gateway, make_lane, make_pool, make_task


def _simple_model() -> ProcessModel:
    """Create: start -> task1 -> gw -> (Yes) task2 -> end
                                    -> (No)  task1 (loop)
    """
    return ProcessModel([
        make_pool("pool1", "Test Process"),
        make_lane("lane1", "Dev Lane"),
        make_event("start", EventType.START),
        make_task("task1", "Do Work", agent="auto:agent_dev", phase="1", phase_name="Dev"),
        make_gateway("gw1", label="OK?"),
        make_task("task2", "Review", agent="agent_qa_code-reviewer", phase="2", phase_name="Review", status_from="in_progress", status_to="done"),
        make_event("end", EventType.END),
        make_flow("f1", "start", "task1"),
        make_flow("f2", "task1", "gw1"),
        make_flow("f3", "gw1", "task2", "Yes"),
        make_flow("f4", "gw1", "task1", "No"),
        make_flow("f5", "task2", "end"),
    ])


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_nodes_and_edges_separated(self):
        model = _simple_model()
        assert model.get_node("task1") is not None
        assert model.get_node("f1") is None  # flows are edges, not nodes

    def test_all_tasks(self):
        model = _simple_model()
        tasks = model.get_all_tasks()
        assert len(tasks) == 2
        assert {t.id for t in tasks} == {"task1", "task2"}

    def test_all_gateways(self):
        model = _simple_model()
        gateways = model.get_all_gateways()
        assert len(gateways) == 1
        assert gateways[0].id == "gw1"


# ---------------------------------------------------------------------------
# Event queries
# ---------------------------------------------------------------------------


class TestEventQueries:
    def test_start_events(self):
        model = _simple_model()
        starts = model.get_start_events()
        assert len(starts) == 1
        assert starts[0].id == "start"

    def test_end_events(self):
        model = _simple_model()
        ends = model.get_end_events()
        assert len(ends) == 1
        assert ends[0].id == "end"


# ---------------------------------------------------------------------------
# Custom properties
# ---------------------------------------------------------------------------


class TestCustomProperties:
    def test_get_custom_properties(self):
        model = _simple_model()
        props = model.get_custom_properties("task1")
        assert props["agent"] == "auto:agent_dev"
        assert props["phase"] == "1"

    def test_get_agent(self):
        model = _simple_model()
        assert model.get_agent("task1") == "auto:agent_dev"
        assert model.get_agent("task2") == "agent_qa_code-reviewer"
        assert model.get_agent("nonexistent") == ""

    def test_missing_node_returns_empty_dict(self):
        model = _simple_model()
        assert model.get_custom_properties("nonexistent") == {}


# ---------------------------------------------------------------------------
# Lane / Pool membership
# ---------------------------------------------------------------------------


class TestLanePoolMembership:
    def test_get_lane(self):
        model = _simple_model()
        lane = model.get_lane("task1")
        assert lane is not None
        assert lane.id == "lane1"

    def test_get_pool(self):
        model = _simple_model()
        pool = model.get_pool("task1")
        assert pool is not None
        assert pool.id == "pool1"

    def test_get_tasks_in_lane(self):
        model = _simple_model()
        tasks = model.get_tasks_in_lane("lane1")
        assert len(tasks) == 2

    def test_get_lane_for_nonexistent(self):
        model = _simple_model()
        assert model.get_lane("nonexistent") is None

    def test_get_pool_for_nonexistent(self):
        model = _simple_model()
        assert model.get_pool("nonexistent") is None

    def test_get_lanes(self):
        model = _simple_model()
        lanes = model.get_lanes()
        assert len(lanes) == 1
        assert lanes[0].label == "Dev Lane"

    def test_get_pools(self):
        model = _simple_model()
        pools = model.get_pools()
        assert len(pools) == 1
        assert pools[0].label == "Test Process"


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------


class TestGraphTraversal:
    def test_get_outgoing(self):
        model = _simple_model()
        edges = model.get_outgoing("task1")
        assert len(edges) == 1
        assert edges[0].target_id == "gw1"

    def test_get_incoming(self):
        model = _simple_model()
        edges = model.get_incoming("task1")
        # start->task1 and gw1->task1 (No loop)
        assert len(edges) == 2
        sources = {e.source_id for e in edges}
        assert sources == {"start", "gw1"}

    def test_get_next_all(self):
        model = _simple_model()
        nexts = model.get_next("gw1")
        assert len(nexts) == 2
        assert {n.id for n in nexts} == {"task1", "task2"}

    def test_get_next_with_condition_yes(self):
        model = _simple_model()
        nexts = model.get_next("gw1", condition="yes")
        assert len(nexts) == 1
        assert nexts[0].id == "task2"

    def test_get_next_with_condition_no(self):
        model = _simple_model()
        nexts = model.get_next("gw1", condition="no")
        assert len(nexts) == 1
        assert nexts[0].id == "task1"

    def test_get_next_condition_no_match(self):
        model = _simple_model()
        nexts = model.get_next("gw1", condition="maybe")
        assert nexts == []

    def test_get_predecessors(self):
        model = _simple_model()
        preds = model.get_predecessors("gw1")
        assert len(preds) == 1
        assert preds[0].id == "task1"

    def test_get_next_from_nonexistent(self):
        model = _simple_model()
        assert model.get_next("nonexistent") == []

    def test_get_outgoing_returns_copies(self):
        model = _simple_model()
        edges1 = model.get_outgoing("gw1")
        edges2 = model.get_outgoing("gw1")
        assert edges1 is not edges2


# ---------------------------------------------------------------------------
# Path finding
# ---------------------------------------------------------------------------


class TestPathFinding:
    def test_direct_path(self):
        model = _simple_model()
        path = model.get_path("start", "task1")
        assert len(path) == 2
        assert path[0].id == "start"
        assert path[1].id == "task1"

    def test_multi_hop_path(self):
        model = _simple_model()
        path = model.get_path("start", "end")
        # start -> task1 -> gw1 -> task2 -> end
        assert len(path) == 5
        assert path[0].id == "start"
        assert path[-1].id == "end"

    def test_same_node_path(self):
        model = _simple_model()
        path = model.get_path("task1", "task1")
        assert len(path) == 1
        assert path[0].id == "task1"

    def test_no_path(self):
        model = _simple_model()
        # end has no outgoing edges, so can't reach start
        path = model.get_path("end", "start")
        assert path == []

    def test_nonexistent_node(self):
        model = _simple_model()
        assert model.get_path("nonexistent", "task1") == []


# ---------------------------------------------------------------------------
# Phase queries
# ---------------------------------------------------------------------------


class TestPhaseQueries:
    def test_get_tasks_in_phase(self):
        model = _simple_model()
        tasks = model.get_tasks_in_phase(1)
        assert len(tasks) == 1
        assert tasks[0].id == "task1"

    def test_get_tasks_in_phase_string(self):
        model = _simple_model()
        tasks = model.get_tasks_in_phase("2")
        assert len(tasks) == 1
        assert tasks[0].id == "task2"

    def test_get_phases(self):
        model = _simple_model()
        phases = model.get_phases()
        assert len(phases) == 2
        assert phases[0] == {"phase": "1", "phase_name": "Dev"}
        assert phases[1] == {"phase": "2", "phase_name": "Review"}


# ---------------------------------------------------------------------------
# Transition rules
# ---------------------------------------------------------------------------


class TestTransitionRules:
    def test_get_transition_rules(self):
        model = _simple_model()
        rules = model.get_transition_rules("task2")
        assert rules["status_from"] == "in_progress"
        assert rules["status_to"] == "done"

    def test_no_transitions(self):
        model = _simple_model()
        rules = model.get_transition_rules("task1")
        assert rules["status_from"] == ""
        assert rules["status_to"] == ""
        assert rules["rule"] == ""


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary(self):
        model = _simple_model()
        s = model.summary()
        assert s["pools"] == 1
        assert s["lanes"] == 1
        assert s["tasks"] == 2
        assert s["gateways"] == 1
        assert s["events"] == 2
        assert s["sequence_flows"] == 5
        assert len(s["phases"]) == 2


# ---------------------------------------------------------------------------
# Integration with real diagram
# ---------------------------------------------------------------------------


class TestRealDiagram:
    def test_from_drawio(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        s = model.summary()
        assert s["pools"] == 1
        assert s["lanes"] == 7
        assert s["tasks"] == 23
        assert s["gateways"] >= 7
        assert s["events"] == 2

    def test_phase_navigation(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        phases = model.get_phases()
        phase_nums = [p["phase"] for p in phases]
        assert "1" in phase_nums
        assert "9" in phase_nums

    def test_agent_lookup(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        assert model.get_agent("task_create_epics") == "agent_plan_software-architect"
        assert model.get_agent("task_security_scan") == "agent_qa_security-scanner"
        assert model.get_agent("task_retrospective") == "agent_qa_retrospective"

    def test_gateway_branching(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        # Issues Found? gateway should have Yes and No paths
        nexts = model.get_next("gw_issues")
        assert len(nexts) == 2

        yes_path = model.get_next("gw_issues", condition="yes")
        assert len(yes_path) == 1
        assert yes_path[0].id == "task_implement_tdd"

        no_path = model.get_next("gw_issues", condition="no")
        assert len(no_path) == 1
        assert no_path[0].id == "task_doc_story"

    def test_parallel_gateway_fan_out(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        fan_out = model.get_next("gw_parallel_open")
        assert len(fan_out) == 8  # 8 quality gate tasks

    def test_parallel_gateway_fan_in(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        incoming = model.get_incoming("gw_parallel_close")
        assert len(incoming) == 8

    def test_lane_membership(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        lane = model.get_lane("task_code_review")
        assert lane is not None
        assert "Review" in lane.label

    def test_security_scan_rules(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        rules = model.get_transition_rules("task_security_scan")
        assert rules["status_to"] == "done"
        assert "DONE" in rules["rule"]

    def test_path_start_to_end(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        path = model.get_path("start1", "end1")
        assert len(path) > 5  # should traverse multiple phases
        assert path[0].id == "start1"
        assert path[-1].id == "end1"

    def test_tasks_in_quality_gate(self):
        model = ProcessModel.from_drawio("diagrams/dev-workflow.drawio")
        qg_tasks = model.get_tasks_in_phase(7)
        assert len(qg_tasks) == 8
        agents = {t.custom_properties.get("agent", "") for t in qg_tasks}
        assert "agent_qa_dependency-analyzer" in agents
        assert "agent_qa_regression-tester" in agents
