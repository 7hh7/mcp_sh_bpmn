"""Tests for the MCP server tool handlers in server.py.

Tests are organized into:
1. Helper function unit tests (_node_to_dict, _task_detail)
2. Handler unit tests via synthetic model (all 11 tools)
3. Error case tests (no diagram, invalid IDs, wrong node types)
4. Integration tests against the real dev-workflow.drawio diagram
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mcp.types import CallToolRequest, CallToolRequestParams

from mcp_sh_bpmn.bpmn_classifier import (
    BPMNElement,
    BPMNType,
    EventType,
    GatewayType,
    TaskMarker,
)
from mcp_sh_bpmn.process_model import ProcessModel
from mcp_sh_bpmn.server import _node_to_dict, _task_detail, create_server

from helpers import make_event, make_flow, make_gateway, make_lane, make_pool, make_task


def _simple_model() -> ProcessModel:
    """Create: start -> task1 -> gw -> (Yes) task2 -> end
                                    -> (No)  task1 (loop)
    Two lanes, two phases, agent assignments, transition rules.
    """
    return ProcessModel([
        make_pool("pool1", "Test Process"),
        make_lane("lane1", "Dev Lane"),
        make_lane("lane2", "QA Lane"),
        make_event("start", EventType.START),
        make_task(
            "task1", "Do Work",
            agent="auto:agent_dev", phase="1", phase_name="Dev",
        ),
        make_gateway("gw1", label="OK?"),
        make_task(
            "task2", "Review", parent="lane2",
            agent="agent_qa_code-reviewer", phase="2", phase_name="Review",
            status_from="in_progress", status_to="done", rule="Only QA can mark DONE",
        ),
        make_event("end", EventType.END),
        make_flow("f1", "start", "task1"),
        make_flow("f2", "task1", "gw1"),
        make_flow("f3", "gw1", "task2", "Yes"),
        make_flow("f4", "gw1", "task1", "No"),
        make_flow("f5", "task2", "end"),
    ])


# ---------------------------------------------------------------------------
# Fixture: call a tool on a server and return parsed JSON
# ---------------------------------------------------------------------------


async def _call_tool(server, name: str, arguments: dict | None = None) -> dict | list:
    """Invoke a tool on the given MCP server and return parsed JSON result."""
    handler = server.request_handlers[CallToolRequest]
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name=name, arguments=arguments or {}),
    )
    result = await handler(req)
    text = result.root.content[0].text
    return json.loads(text)


# ---------------------------------------------------------------------------
# _node_to_dict helper
# ---------------------------------------------------------------------------


class TestNodeToDict:
    """Unit tests for the _node_to_dict helper."""

    def test_basic_task(self):
        node = make_task("t1", "My Task")
        d = _node_to_dict(node)
        assert d["id"] == "t1"
        assert d["type"] == "task"
        assert d["label"] == "My Task"
        assert d["task_marker"] == "user"

    def test_task_with_custom_properties(self):
        node = make_task("t1", "Work", agent="agent_dev", phase="3")
        d = _node_to_dict(node)
        assert d["properties"] == {"agent": "agent_dev", "phase": "3"}

    def test_task_without_custom_properties(self):
        node = BPMNElement(
            id="t1", bpmn_type=BPMNType.TASK, label="Bare Task",
            task_marker=TaskMarker.ABSTRACT,
        )
        d = _node_to_dict(node)
        assert "properties" not in d

    def test_event_includes_event_type(self):
        node = make_event("e1", EventType.START)
        d = _node_to_dict(node)
        assert d["type"] == "event"
        assert d["event_type"] == "start"
        assert d["event_symbol"] == ""

    def test_gateway_includes_gateway_type(self):
        node = make_gateway("gw1", GatewayType.PARALLEL, "Fork")
        d = _node_to_dict(node)
        assert d["type"] == "gateway"
        assert d["gateway_type"] == "parallel"
        assert d["label"] == "Fork"

    def test_event_with_symbol(self):
        node = BPMNElement(
            id="e1", bpmn_type=BPMNType.EVENT,
            event_type=EventType.INTERMEDIATE_CATCHING,
            event_symbol="timer",
        )
        d = _node_to_dict(node)
        assert d["event_type"] == "intermediate_catching"
        assert d["event_symbol"] == "timer"

    def test_no_optional_fields_when_absent(self):
        node = BPMNElement(id="x", bpmn_type=BPMNType.TASK, label="plain")
        d = _node_to_dict(node)
        assert "properties" not in d
        assert "event_type" not in d
        assert "gateway_type" not in d
        assert "task_marker" not in d


# ---------------------------------------------------------------------------
# _task_detail helper
# ---------------------------------------------------------------------------


class TestTaskDetail:
    """Unit tests for the _task_detail helper."""

    def test_includes_lane_info(self):
        model = _simple_model()
        node = model.get_node("task1")
        d = _task_detail(model, node)
        assert "lane" in d
        assert d["lane"]["id"] == "lane1"
        assert d["lane"]["label"] == "Dev Lane"

    def test_includes_transition_rules(self):
        model = _simple_model()
        node = model.get_node("task2")
        d = _task_detail(model, node)
        assert "transitions" in d
        assert d["transitions"]["status_from"] == "in_progress"
        assert d["transitions"]["status_to"] == "done"
        assert d["transitions"]["rule"] == "Only QA can mark DONE"

    def test_no_transitions_when_empty(self):
        model = _simple_model()
        node = model.get_node("task1")
        d = _task_detail(model, node)
        # task1 has no status_from/status_to/rule so transitions should be absent
        assert "transitions" not in d

    def test_preserves_node_to_dict_fields(self):
        model = _simple_model()
        node = model.get_node("task1")
        d = _task_detail(model, node)
        assert d["id"] == "task1"
        assert d["type"] == "task"
        assert d["label"] == "Do Work"
        assert d["properties"]["agent"] == "auto:agent_dev"

    def test_event_node_no_lane(self):
        """Events may be in a lane but confirm lane lookup works."""
        model = _simple_model()
        node = model.get_node("start")
        d = _task_detail(model, node)
        # start event is in lane1
        assert "lane" in d
        assert d["lane"]["id"] == "lane1"


# ---------------------------------------------------------------------------
# Tool handler tests via create_server with synthetic model
# ---------------------------------------------------------------------------
# We use the real create_server with env vars pointing to the diagram
# and test each tool through the call_tool dispatch.
# ---------------------------------------------------------------------------


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def real_server():
    """Create a server loaded with the real dev-workflow.drawio diagram."""
    old_dir = os.getcwd()
    old_diagrams = os.environ.get("BPMN_DIAGRAMS_DIR")
    old_default = os.environ.get("BPMN_DEFAULT_DIAGRAM")
    try:
        os.chdir(str(PROJECT_ROOT))
        os.environ["BPMN_DIAGRAMS_DIR"] = "./diagrams"
        os.environ["BPMN_DEFAULT_DIAGRAM"] = "dev-workflow.drawio"
        server = create_server()
    finally:
        os.chdir(old_dir)
        if old_diagrams is None:
            os.environ.pop("BPMN_DIAGRAMS_DIR", None)
        else:
            os.environ["BPMN_DIAGRAMS_DIR"] = old_diagrams
        if old_default is None:
            os.environ.pop("BPMN_DEFAULT_DIAGRAM", None)
        else:
            os.environ["BPMN_DEFAULT_DIAGRAM"] = old_default
    return server


@pytest.fixture(scope="module")
def no_diagram_server():
    """Create a server where the diagram failed to load (model is None)."""
    old_diagrams = os.environ.get("BPMN_DIAGRAMS_DIR")
    old_default = os.environ.get("BPMN_DEFAULT_DIAGRAM")
    try:
        os.environ["BPMN_DIAGRAMS_DIR"] = "/tmp/nonexistent_bpmn_dir"
        os.environ["BPMN_DEFAULT_DIAGRAM"] = "missing.drawio"
        server = create_server()
    finally:
        if old_diagrams is None:
            os.environ.pop("BPMN_DIAGRAMS_DIR", None)
        else:
            os.environ["BPMN_DIAGRAMS_DIR"] = old_diagrams
        if old_default is None:
            os.environ.pop("BPMN_DEFAULT_DIAGRAM", None)
        else:
            os.environ["BPMN_DEFAULT_DIAGRAM"] = old_default
    return server


# ---------------------------------------------------------------------------
# bpmn_get_overview
# ---------------------------------------------------------------------------


class TestGetOverview:
    @pytest.mark.asyncio
    async def test_returns_summary(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_overview")
        assert result["pools"] == 1
        assert result["lanes"] == 7
        assert result["tasks"] == 23
        assert result["gateways"] >= 7
        assert result["events"] == 2
        assert result["sequence_flows"] >= 40
        assert isinstance(result["phases"], list)
        assert len(result["phases"]) == 9



# ---------------------------------------------------------------------------
# bpmn_get_phases
# ---------------------------------------------------------------------------


class TestGetPhases:
    @pytest.mark.asyncio
    async def test_returns_all_phases(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_phases")
        assert isinstance(result, list)
        assert len(result) == 9
        phase_nums = [p["phase"] for p in result]
        assert "1" in phase_nums
        assert "9" in phase_nums

    @pytest.mark.asyncio
    async def test_phase_structure(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_phases")
        first = result[0]
        assert "phase" in first
        assert "phase_name" in first
        assert first["phase"] == "1"
        assert first["phase_name"] == "Planning"



# ---------------------------------------------------------------------------
# bpmn_get_phase_tasks
# ---------------------------------------------------------------------------


class TestGetPhaseTasks:
    @pytest.mark.asyncio
    async def test_phase_1_tasks(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_phase_tasks", {"phase": "1"}
        )
        assert result["phase"] == "1"
        assert isinstance(result["tasks"], list)
        assert len(result["tasks"]) >= 1

    @pytest.mark.asyncio
    async def test_phase_7_quality_gate(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_phase_tasks", {"phase": "7"}
        )
        assert result["phase"] == "7"
        assert len(result["tasks"]) == 8

    @pytest.mark.asyncio
    async def test_task_detail_in_phase(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_phase_tasks", {"phase": "1"}
        )
        task = result["tasks"][0]
        assert "id" in task
        assert "type" in task
        assert "label" in task

    @pytest.mark.asyncio
    async def test_empty_phase(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_phase_tasks", {"phase": "99"}
        )
        assert result["phase"] == "99"
        assert result["tasks"] == []



# ---------------------------------------------------------------------------
# bpmn_get_task
# ---------------------------------------------------------------------------


class TestGetTask:
    @pytest.mark.asyncio
    async def test_valid_task(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_task", {"task_id": "task_create_epics"}
        )
        assert result["id"] == "task_create_epics"
        assert result["type"] == "task"
        assert "label" in result
        assert "properties" in result

    @pytest.mark.asyncio
    async def test_task_with_lane(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_task", {"task_id": "task_code_review"}
        )
        assert "lane" in result
        assert "id" in result["lane"]
        assert "label" in result["lane"]

    @pytest.mark.asyncio
    async def test_task_with_transitions(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_task", {"task_id": "task_security_scan"}
        )
        assert "transitions" in result
        assert result["transitions"]["status_to"] == "done"

    @pytest.mark.asyncio
    async def test_invalid_task_id(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_task", {"task_id": "nonexistent_task"}
        )
        assert "error" in result
        assert "not found" in result["error"].lower()



# ---------------------------------------------------------------------------
# bpmn_get_agent
# ---------------------------------------------------------------------------


class TestGetAgent:
    @pytest.mark.asyncio
    async def test_task_with_agent(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_agent", {"task_id": "task_create_epics"}
        )
        assert result["task_id"] == "task_create_epics"
        assert result["agent"] == "agent_plan_software-architect"

    @pytest.mark.asyncio
    async def test_security_scan_agent(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_agent", {"task_id": "task_security_scan"}
        )
        assert result["agent"] == "agent_qa_security-scanner"

    @pytest.mark.asyncio
    async def test_task_without_agent(self, real_server):
        # Use a nonexistent task; get_agent returns "" for unknown node
        result = await _call_tool(
            real_server, "bpmn_get_agent", {"task_id": "nonexistent_task"}
        )
        assert result["task_id"] == "nonexistent_task"
        assert result["agent"] is None
        assert "No agent assigned" in result["note"]



# ---------------------------------------------------------------------------
# bpmn_get_next
# ---------------------------------------------------------------------------


class TestGetNext:
    @pytest.mark.asyncio
    async def test_next_from_gateway(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_next", {"node_id": "gw_issues"}
        )
        assert result["from"] == "gw_issues"
        assert result["condition"] is None
        assert isinstance(result["branches"], list)
        assert len(result["branches"]) == 2

    @pytest.mark.asyncio
    async def test_next_with_yes_condition(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_next",
            {"node_id": "gw_issues", "condition": "yes"},
        )
        assert result["condition"] == "yes"
        assert len(result["branches"]) == 1
        target = result["branches"][0]["target"]
        assert target["id"] == "task_implement_tdd"

    @pytest.mark.asyncio
    async def test_next_with_no_condition(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_next",
            {"node_id": "gw_issues", "condition": "no"},
        )
        assert result["condition"] == "no"
        assert len(result["branches"]) == 1
        target = result["branches"][0]["target"]
        assert target["id"] == "task_doc_story"

    @pytest.mark.asyncio
    async def test_next_condition_no_match(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_next",
            {"node_id": "gw_issues", "condition": "maybe"},
        )
        assert result["branches"] == []

    @pytest.mark.asyncio
    async def test_next_from_task(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_next", {"node_id": "task_create_epics"}
        )
        assert result["from"] == "task_create_epics"
        assert len(result["branches"]) >= 1

    @pytest.mark.asyncio
    async def test_branch_structure(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_next", {"node_id": "gw_issues"}
        )
        branch = result["branches"][0]
        assert "edge_label" in branch
        assert "target" in branch
        assert "id" in branch["target"]



# ---------------------------------------------------------------------------
# bpmn_get_predecessors
# ---------------------------------------------------------------------------


class TestGetPredecessors:
    @pytest.mark.asyncio
    async def test_predecessors_of_gateway(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_predecessors", {"node_id": "gw_issues"}
        )
        assert result["node_id"] == "gw_issues"
        assert isinstance(result["predecessors"], list)
        assert len(result["predecessors"]) >= 1

    @pytest.mark.asyncio
    async def test_predecessor_structure(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_predecessors", {"node_id": "gw_issues"}
        )
        pred = result["predecessors"][0]
        assert "id" in pred
        assert "type" in pred
        assert "label" in pred

    @pytest.mark.asyncio
    async def test_predecessors_of_start_event(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_predecessors", {"node_id": "start1"}
        )
        assert result["predecessors"] == []

    @pytest.mark.asyncio
    async def test_parallel_close_has_many_predecessors(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_predecessors", {"node_id": "gw_parallel_close"}
        )
        assert len(result["predecessors"]) == 8



# ---------------------------------------------------------------------------
# bpmn_get_gateway
# ---------------------------------------------------------------------------


class TestGetGateway:
    @pytest.mark.asyncio
    async def test_exclusive_gateway(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_gateway", {"gateway_id": "gw_issues"}
        )
        assert result["id"] == "gw_issues"
        assert result["gateway_type"] == "exclusive"
        assert isinstance(result["branches"], list)
        assert len(result["branches"]) == 2

    @pytest.mark.asyncio
    async def test_parallel_gateway(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_gateway", {"gateway_id": "gw_parallel_open"}
        )
        assert result["gateway_type"] == "parallel"
        assert len(result["branches"]) == 8

    @pytest.mark.asyncio
    async def test_gateway_branch_structure(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_gateway", {"gateway_id": "gw_issues"}
        )
        branch = result["branches"][0]
        assert "edge_id" in branch
        assert "condition" in branch
        assert "target_id" in branch
        assert "target_label" in branch

    @pytest.mark.asyncio
    async def test_gateway_not_found(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_gateway", {"gateway_id": "nonexistent_gw"}
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_not_a_gateway(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_gateway", {"gateway_id": "task_code_review"}
        )
        assert "error" in result
        assert "not a gateway" in result["error"].lower()



# ---------------------------------------------------------------------------
# bpmn_get_lanes
# ---------------------------------------------------------------------------


class TestGetLanes:
    @pytest.mark.asyncio
    async def test_returns_all_lanes(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_lanes")
        assert "lanes" in result
        assert len(result["lanes"]) == 7

    @pytest.mark.asyncio
    async def test_lane_structure(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_lanes")
        lane = result["lanes"][0]
        assert "id" in lane
        assert "label" in lane
        assert "task_count" in lane
        assert "tasks" in lane
        assert isinstance(lane["tasks"], list)

    @pytest.mark.asyncio
    async def test_lane_task_structure(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_lanes")
        # Find a lane that has tasks
        lanes_with_tasks = [lane for lane in result["lanes"] if lane["task_count"] > 0]
        assert len(lanes_with_tasks) > 0
        task = lanes_with_tasks[0]["tasks"][0]
        assert "id" in task
        assert "label" in task

    @pytest.mark.asyncio
    async def test_task_count_matches_tasks_list(self, real_server):
        result = await _call_tool(real_server, "bpmn_get_lanes")
        for lane in result["lanes"]:
            assert lane["task_count"] == len(lane["tasks"])



# ---------------------------------------------------------------------------
# bpmn_get_transition_rules
# ---------------------------------------------------------------------------


class TestGetTransitionRules:
    @pytest.mark.asyncio
    async def test_task_with_transitions(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_transition_rules",
            {"task_id": "task_security_scan"},
        )
        assert result["task_id"] == "task_security_scan"
        assert result["status_to"] == "done"
        assert "DONE" in result["rule"]

    @pytest.mark.asyncio
    async def test_task_without_transitions(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_transition_rules",
            {"task_id": "task_create_epics"},
        )
        assert result["task_id"] == "task_create_epics"
        # Should have status_from, status_to, rule keys even if empty
        assert "status_from" in result
        assert "status_to" in result
        assert "rule" in result

    @pytest.mark.asyncio
    async def test_invalid_task_id(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_transition_rules",
            {"task_id": "nonexistent"},
        )
        assert "error" in result
        assert "not found" in result["error"].lower()



# ---------------------------------------------------------------------------
# bpmn_get_path
# ---------------------------------------------------------------------------


class TestGetPath:
    @pytest.mark.asyncio
    async def test_path_start_to_end(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_path",
            {"from_id": "start1", "to_id": "end1"},
        )
        assert result["from"] == "start1"
        assert result["to"] == "end1"
        assert result["steps"] > 5
        assert isinstance(result["path"], list)
        assert result["path"][0]["id"] == "start1"
        assert result["path"][-1]["id"] == "end1"

    @pytest.mark.asyncio
    async def test_path_node_structure(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_path",
            {"from_id": "start1", "to_id": "end1"},
        )
        node = result["path"][0]
        assert "id" in node
        assert "type" in node
        assert "label" in node

    @pytest.mark.asyncio
    async def test_no_path_reverse(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_path",
            {"from_id": "end1", "to_id": "start1"},
        )
        assert result["path"] == []
        assert "No path found" in result["note"]

    @pytest.mark.asyncio
    async def test_same_node(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_path",
            {"from_id": "start1", "to_id": "start1"},
        )
        assert result["steps"] == 1
        assert len(result["path"]) == 1
        assert result["path"][0]["id"] == "start1"

    @pytest.mark.asyncio
    async def test_nonexistent_from(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_path",
            {"from_id": "nonexistent", "to_id": "end1"},
        )
        assert result["path"] == []
        assert "No path found" in result["note"]

    @pytest.mark.asyncio
    async def test_nonexistent_to(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_get_path",
            {"from_id": "start1", "to_id": "nonexistent"},
        )
        assert result["path"] == []
        assert "No path found" in result["note"]



# ---------------------------------------------------------------------------
# Parametrized no-diagram error tests (covers all 11 tools)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,args", [
    ("bpmn_get_overview", {}),
    ("bpmn_get_phases", {}),
    ("bpmn_get_phase_tasks", {"phase": "1"}),
    ("bpmn_get_task", {"task_id": "x"}),
    ("bpmn_get_agent", {"task_id": "x"}),
    ("bpmn_get_next", {"node_id": "x"}),
    ("bpmn_get_predecessors", {"node_id": "x"}),
    ("bpmn_get_gateway", {"gateway_id": "x"}),
    ("bpmn_get_lanes", {}),
    ("bpmn_get_transition_rules", {"task_id": "x"}),
    ("bpmn_get_path", {"from_id": "x", "to_id": "y"}),
    ("bpmn_load_diagram", {"filename": "dev-workflow.drawio"}),
    ("bpmn_reload", {}),
])
async def test_no_diagram_returns_error(no_diagram_server, tool_name, args):
    result = await _call_tool(no_diagram_server, tool_name, args)
    if isinstance(result, list):
        assert "error" in result[0]
    else:
        assert "error" in result


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_name(self, real_server):
        """Unknown tool returns plain text (not JSON) with error message."""
        handler = real_server.request_handlers[CallToolRequest]
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="bpmn_nonexistent_tool", arguments={}
            ),
        )
        result = await handler(req)
        text = result.root.content[0].text
        assert "Unknown tool" in text
        assert "bpmn_nonexistent_tool" in text


# ---------------------------------------------------------------------------
# Integration: synthetic model tested through create_server helpers
# ---------------------------------------------------------------------------


class TestSyntheticModelHelpers:
    """Test handler logic using the synthetic model and helper functions.

    Since handlers are thin wrappers around ProcessModel + _node_to_dict/_task_detail,
    we verify the complete pipeline here.
    """

    def test_overview_logic(self):
        model = _simple_model()
        result = model.summary()
        assert result["pools"] == 1
        assert result["lanes"] == 2
        assert result["tasks"] == 2
        assert result["gateways"] == 1
        assert result["events"] == 2
        assert result["sequence_flows"] == 5

    def test_phases_logic(self):
        model = _simple_model()
        phases = model.get_phases()
        assert len(phases) == 2
        assert phases[0] == {"phase": "1", "phase_name": "Dev"}
        assert phases[1] == {"phase": "2", "phase_name": "Review"}

    def test_phase_tasks_logic(self):
        model = _simple_model()
        tasks = model.get_tasks_in_phase("1")
        result = {
            "phase": "1",
            "tasks": [_task_detail(model, t) for t in tasks],
        }
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["id"] == "task1"
        assert result["tasks"][0]["properties"]["agent"] == "auto:agent_dev"

    def test_get_task_logic(self):
        model = _simple_model()
        node = model.get_node("task2")
        result = _task_detail(model, node)
        assert result["id"] == "task2"
        assert result["lane"]["id"] == "lane2"
        assert result["transitions"]["status_from"] == "in_progress"

    def test_get_agent_logic(self):
        model = _simple_model()
        agent = model.get_agent("task1")
        assert agent == "auto:agent_dev"
        assert model.get_agent("nonexistent") == ""

    def test_get_next_logic(self):
        model = _simple_model()
        outgoing = model.get_outgoing("gw1")
        result = {
            "from": "gw1",
            "condition": None,
            "branches": [
                {
                    "edge_label": e.label,
                    "target": _node_to_dict(model.get_node(e.target_id)),
                }
                for e in outgoing
            ],
        }
        assert len(result["branches"]) == 2
        labels = {b["edge_label"] for b in result["branches"]}
        assert labels == {"Yes", "No"}

    def test_get_next_with_condition_logic(self):
        model = _simple_model()
        outgoing = model.get_outgoing("gw1")
        condition = "yes"
        branches = [
            {
                "edge_label": e.label,
                "target": _node_to_dict(model.get_node(e.target_id)),
            }
            for e in outgoing
            if e.label.lower().startswith(condition.lower())
        ]
        assert len(branches) == 1
        assert branches[0]["target"]["id"] == "task2"

    def test_predecessors_logic(self):
        model = _simple_model()
        preds = model.get_predecessors("gw1")
        result = {
            "node_id": "gw1",
            "predecessors": [_node_to_dict(p) for p in preds],
        }
        assert len(result["predecessors"]) == 1
        assert result["predecessors"][0]["id"] == "task1"

    def test_gateway_logic(self):
        model = _simple_model()
        node = model.get_node("gw1")
        outgoing = model.get_outgoing("gw1")
        result = {
            "id": node.id,
            "label": node.label,
            "gateway_type": node.gateway_type.value,
            "branches": [
                {
                    "edge_id": e.id,
                    "condition": e.label,
                    "target_id": e.target_id,
                    "target_label": model.get_node(e.target_id).label,
                }
                for e in outgoing
            ],
        }
        assert result["gateway_type"] == "exclusive"
        assert len(result["branches"]) == 2
        conditions = {b["condition"] for b in result["branches"]}
        assert conditions == {"Yes", "No"}

    def test_lanes_logic(self):
        model = _simple_model()
        lanes = model.get_lanes()
        result = []
        for lane in lanes:
            tasks = model.get_tasks_in_lane(lane.id)
            result.append({
                "id": lane.id,
                "label": lane.label,
                "task_count": len(tasks),
                "tasks": [{"id": t.id, "label": t.label} for t in tasks],
            })
        assert len(result) == 2
        # lane1 has task1, lane2 has task2
        lane1 = next(lane for lane in result if lane["id"] == "lane1")
        assert lane1["task_count"] == 1
        assert lane1["tasks"][0]["id"] == "task1"

    def test_transition_rules_logic(self):
        model = _simple_model()
        rules = model.get_transition_rules("task2")
        rules["task_id"] = "task2"
        assert rules["status_from"] == "in_progress"
        assert rules["status_to"] == "done"
        assert rules["rule"] == "Only QA can mark DONE"
        assert rules["task_id"] == "task2"

    def test_path_logic(self):
        model = _simple_model()
        path = model.get_path("start", "end")
        result = {
            "from": "start",
            "to": "end",
            "steps": len(path),
            "path": [_node_to_dict(n) for n in path],
        }
        assert result["steps"] == 5
        assert result["path"][0]["id"] == "start"
        assert result["path"][-1]["id"] == "end"

    def test_path_no_route(self):
        model = _simple_model()
        path = model.get_path("end", "start")
        assert path == []


# ---------------------------------------------------------------------------
# bpmn_list_diagrams
# ---------------------------------------------------------------------------


class TestListDiagrams:
    @pytest.mark.asyncio
    async def test_returns_diagrams_list(self, real_server):
        result = await _call_tool(real_server, "bpmn_list_diagrams")
        assert "diagrams_dir" in result
        assert "diagrams" in result
        assert isinstance(result["diagrams"], list)
        # There is at least dev-workflow.drawio
        filenames = [d["filename"] for d in result["diagrams"]]
        assert "dev-workflow.drawio" in filenames

    @pytest.mark.asyncio
    async def test_diagram_entry_has_size(self, real_server):
        result = await _call_tool(real_server, "bpmn_list_diagrams")
        entry = result["diagrams"][0]
        assert "filename" in entry
        assert "size_bytes" in entry
        assert isinstance(entry["size_bytes"], int)
        assert entry["size_bytes"] > 0

    @pytest.mark.asyncio
    async def test_list_diagrams_no_diagram_server_still_works(self, no_diagram_server):
        """bpmn_list_diagrams should still list files even if no model is loaded,
        but returns an error if the diagrams_dir does not exist."""
        result = await _call_tool(no_diagram_server, "bpmn_list_diagrams")
        # The no_diagram_server has a nonexistent dir, so should error
        assert "error" in result


# ---------------------------------------------------------------------------
# bpmn_load_diagram
# ---------------------------------------------------------------------------


class TestLoadDiagram:
    @pytest.mark.asyncio
    async def test_load_valid_diagram(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "dev-workflow.drawio"}
        )
        assert "loaded" in result
        assert result["loaded"] == "dev-workflow.drawio"
        assert "summary" in result
        assert result["summary"]["pools"] == 1
        assert result["summary"]["tasks"] == 23

    @pytest.mark.asyncio
    async def test_load_nonexistent_file(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "nonexistent.drawio"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_load_path_traversal_dotdot(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "../../etc/passwd"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_load_path_traversal_slash(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "/etc/passwd"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_load_path_traversal_backslash(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "..\\..\\etc\\passwd"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_load_non_drawio_file(self, real_server):
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "readme.txt"}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_load_preserves_old_model_on_failure(self, real_server):
        """After a failed load, existing query tools should still work."""
        # First ensure a valid model is loaded
        await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "dev-workflow.drawio"}
        )
        # Now try a bad load
        result = await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "nonexistent.drawio"}
        )
        assert "error" in result
        # Existing tools should still work
        overview = await _call_tool(real_server, "bpmn_get_overview")
        assert "error" not in overview
        assert overview["tasks"] == 23


# ---------------------------------------------------------------------------
# bpmn_reload
# ---------------------------------------------------------------------------


class TestReload:
    @pytest.mark.asyncio
    async def test_reload_current_diagram(self, real_server):
        """After loading a diagram, reload should refresh it."""
        # First load a diagram to set current_file
        await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "dev-workflow.drawio"}
        )
        result = await _call_tool(real_server, "bpmn_reload")
        assert "reloaded" in result
        assert result["reloaded"] == "dev-workflow.drawio"
        assert "summary" in result
        assert result["summary"]["tasks"] == 23

    @pytest.mark.asyncio
    async def test_reload_no_diagram_loaded(self, no_diagram_server):
        """Reload should error when no diagram has been loaded."""
        result = await _call_tool(no_diagram_server, "bpmn_reload")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_existing_tools_work_after_reload(self, real_server):
        """Verify query tools work correctly after a reload."""
        # Ensure a diagram is loaded
        await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "dev-workflow.drawio"}
        )
        # Reload
        await _call_tool(real_server, "bpmn_reload")
        # Verify existing query tools
        overview = await _call_tool(real_server, "bpmn_get_overview")
        assert "error" not in overview
        assert overview["pools"] == 1
        phases = await _call_tool(real_server, "bpmn_get_phases")
        assert isinstance(phases, list)
        assert len(phases) == 9


# ---------------------------------------------------------------------------
# Integration: all-tools smoke test with real diagram
# ---------------------------------------------------------------------------


class TestAllToolsSmokeTest:
    """Verify every tool returns valid JSON without errors on the real diagram."""

    @pytest.mark.asyncio
    async def test_all_tools_no_error(self, real_server):
        """Call every tool and verify none return an error response."""
        calls = [
            ("bpmn_get_overview", {}),
            ("bpmn_get_phases", {}),
            ("bpmn_get_phase_tasks", {"phase": "1"}),
            ("bpmn_get_task", {"task_id": "task_create_epics"}),
            ("bpmn_get_agent", {"task_id": "task_create_epics"}),
            ("bpmn_get_next", {"node_id": "task_create_epics"}),
            ("bpmn_get_predecessors", {"node_id": "task_create_epics"}),
            ("bpmn_get_gateway", {"gateway_id": "gw_issues"}),
            ("bpmn_get_lanes", {}),
            ("bpmn_get_transition_rules", {"task_id": "task_security_scan"}),
            ("bpmn_get_path", {"from_id": "start1", "to_id": "end1"}),
            ("bpmn_list_diagrams", {}),
            ("bpmn_reload", {}),
        ]
        # Ensure a diagram is loaded so reload works
        await _call_tool(
            real_server, "bpmn_load_diagram", {"filename": "dev-workflow.drawio"}
        )
        for tool_name, args in calls:
            result = await _call_tool(real_server, tool_name, args)
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        assert "error" not in item, (
                            f"Tool {tool_name} returned error: {item}"
                        )
            else:
                assert "error" not in result, (
                    f"Tool {tool_name} returned error: {result}"
                )


# ---------------------------------------------------------------------------
# Edge cases: list_tools
# ---------------------------------------------------------------------------


class TestListTools:
    @pytest.mark.asyncio
    async def test_list_tools_returns_14_tools(self, real_server):
        from mcp.types import ListToolsRequest

        handler = real_server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list")
        result = await handler(req)
        tools = result.root.tools
        assert len(tools) == 14
        tool_names = {t.name for t in tools}
        expected = {
            "bpmn_get_overview",
            "bpmn_get_phases",
            "bpmn_get_phase_tasks",
            "bpmn_get_task",
            "bpmn_get_agent",
            "bpmn_get_next",
            "bpmn_get_predecessors",
            "bpmn_get_gateway",
            "bpmn_get_lanes",
            "bpmn_get_transition_rules",
            "bpmn_get_path",
            "bpmn_list_diagrams",
            "bpmn_load_diagram",
            "bpmn_reload",
        }
        assert tool_names == expected

    @pytest.mark.asyncio
    async def test_each_tool_has_input_schema(self, real_server):
        from mcp.types import ListToolsRequest

        handler = real_server.request_handlers[ListToolsRequest]
        req = ListToolsRequest(method="tools/list")
        result = await handler(req)
        for tool in result.root.tools:
            assert tool.inputSchema is not None, (
                f"Tool {tool.name} missing inputSchema"
            )
            assert tool.inputSchema["type"] == "object"
