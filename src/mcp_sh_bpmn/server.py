"""MCP server that exposes BPMN process definitions from Draw.io diagrams."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .bpmn_classifier import BPMNElement, BPMNType
from .process_model import ProcessModel

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mcp-sh-bpmn")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_dict(node: BPMNElement) -> dict:
    """Serialize a BPMNElement to a JSON-friendly dict."""
    d: dict[str, Any] = {
        "id": node.id,
        "type": node.bpmn_type.value,
        "label": node.label,
    }
    if node.custom_properties:
        d["properties"] = node.custom_properties
    if node.event_type:
        d["event_type"] = node.event_type.value
        d["event_symbol"] = node.event_symbol
    if node.task_marker:
        d["task_marker"] = node.task_marker.value
    if node.gateway_type:
        d["gateway_type"] = node.gateway_type.value
    return d


def _task_detail(model: ProcessModel, node: BPMNElement) -> dict:
    """Build a detailed task dict including lane and transition info."""
    d = _node_to_dict(node)
    lane = model.get_lane(node.id)
    if lane:
        d["lane"] = {"id": lane.id, "label": lane.label}
    rules = model.get_transition_rules(node.id)
    if rules["status_from"] or rules["status_to"] or rules["rule"]:
        d["transitions"] = rules
    return d


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def create_server() -> Server:
    """Create and configure the MCP BPMN server."""
    server = Server("mcp-sh-bpmn")

    # Load the process model from the configured diagram
    diagrams_dir = Path(os.getenv("BPMN_DIAGRAMS_DIR", "./diagrams")).resolve()
    default_diagram = os.getenv("BPMN_DEFAULT_DIAGRAM", "dev-workflow.drawio")
    diagram_path = (diagrams_dir / default_diagram).resolve()

    # Mutable state container so handlers can update the model at runtime
    state: dict[str, Any] = {
        "model": None,
        "current_file": None,
        "diagrams_dir": diagrams_dir,
    }

    if not diagram_path.is_relative_to(diagrams_dir):
        logger.error(f"Diagram path escapes diagrams directory: {diagram_path}")
    else:
        try:
            state["model"] = ProcessModel.from_drawio(str(diagram_path))
            state["current_file"] = default_diagram
            logger.info(f"Loaded BPMN diagram: {diagram_path}")
            logger.info(f"Model summary: {state['model'].summary()}")
        except Exception as e:
            logger.error(f"Failed to load diagram {diagram_path}: {e}")

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def handle_get_overview(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        return state["model"].summary()

    async def handle_get_phases(arguments: dict) -> list:
        if state["model"] is None:
            return [{"error": "No diagram loaded"}]
        return state["model"].get_phases()

    async def handle_get_phase_tasks(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        phase = arguments.get("phase", "")
        model = state["model"]
        tasks = model.get_tasks_in_phase(phase)
        return {
            "phase": str(phase),
            "tasks": [_task_detail(model, t) for t in tasks],
        }

    async def handle_get_task(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        task_id = arguments.get("task_id", "")
        model = state["model"]
        node = model.get_node(task_id)
        if node is None:
            return {"error": f"Task not found: {task_id}"}
        return _task_detail(model, node)

    async def handle_get_agent(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        task_id = arguments.get("task_id", "")
        agent = state["model"].get_agent(task_id)
        if not agent:
            return {"task_id": task_id, "agent": None, "note": "No agent assigned"}
        return {"task_id": task_id, "agent": agent}

    async def handle_get_next(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        model = state["model"]
        node_id = arguments.get("node_id", "")
        condition = arguments.get("condition")
        outgoing = model.get_outgoing(node_id)
        return {
            "from": node_id,
            "condition": condition,
            "branches": [
                {
                    "edge_label": e.label,
                    "target": _node_to_dict(model.get_node(e.target_id))
                    if model.get_node(e.target_id)
                    else {"id": e.target_id},
                }
                for e in outgoing
                if condition is None
                or e.label.lower().startswith(condition.lower())
            ],
        }

    async def handle_get_predecessors(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        node_id = arguments.get("node_id", "")
        preds = state["model"].get_predecessors(node_id)
        return {
            "node_id": node_id,
            "predecessors": [_node_to_dict(p) for p in preds],
        }

    async def handle_get_gateway(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        model = state["model"]
        gateway_id = arguments.get("gateway_id", "")
        node = model.get_node(gateway_id)
        if node is None:
            return {"error": f"Gateway not found: {gateway_id}"}
        if node.bpmn_type != BPMNType.GATEWAY:
            return {"error": f"Node {gateway_id} is not a gateway (type: {node.bpmn_type.value})"}
        outgoing = model.get_outgoing(gateway_id)
        return {
            "id": node.id,
            "label": node.label,
            "gateway_type": node.gateway_type.value if node.gateway_type else "unknown",
            "branches": [
                {
                    "edge_id": e.id,
                    "condition": e.label,
                    "target_id": e.target_id,
                    "target_label": (model.get_node(e.target_id).label if model.get_node(e.target_id) else ""),
                }
                for e in outgoing
            ],
        }

    async def handle_get_lanes(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        model = state["model"]
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
        return {"lanes": result}

    async def handle_get_transition_rules(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        task_id = arguments.get("task_id", "")
        node = state["model"].get_node(task_id)
        if node is None:
            return {"error": f"Task not found: {task_id}"}
        rules = state["model"].get_transition_rules(task_id)
        rules["task_id"] = task_id
        return rules

    async def handle_get_path(arguments: dict) -> dict:
        if state["model"] is None:
            return {"error": "No diagram loaded"}
        from_id = arguments.get("from_id", "")
        to_id = arguments.get("to_id", "")
        model = state["model"]
        path = model.get_path(from_id, to_id)
        if not path:
            return {"from": from_id, "to": to_id, "path": [], "note": "No path found"}
        return {
            "from": from_id,
            "to": to_id,
            "steps": len(path),
            "path": [_node_to_dict(n) for n in path],
        }

    # ------------------------------------------------------------------
    # New tools: multi-diagram support and hot-reload
    # ------------------------------------------------------------------

    def _validate_filename(filename: str) -> str | None:
        """Validate a diagram filename. Return error string or None if valid."""
        if not filename:
            return "Filename is required"
        if ".." in filename or "/" in filename or "\\" in filename:
            return f"Invalid filename (path traversal rejected): {filename}"
        if not filename.endswith(".drawio"):
            return f"Only .drawio files are allowed, got: {filename}"
        return None

    async def handle_list_diagrams(arguments: dict) -> dict:
        dd = state["diagrams_dir"]
        if not dd.is_dir():
            return {"error": f"Diagrams directory not found: {dd}"}
        diagrams = []
        for f in sorted(dd.iterdir()):
            if f.is_file() and f.suffix == ".drawio":
                diagrams.append({
                    "filename": f.name,
                    "size_bytes": f.stat().st_size,
                })
        return {
            "diagrams_dir": str(dd),
            "diagrams": diagrams,
        }

    async def handle_load_diagram(arguments: dict) -> dict:
        filename = arguments.get("filename", "")
        err = _validate_filename(filename)
        if err:
            return {"error": err}
        dd = state["diagrams_dir"]
        if not dd.is_dir():
            return {"error": f"Diagrams directory not found: {dd}"}
        full_path = (dd / filename).resolve()
        # Double-check resolved path stays inside diagrams_dir
        if not full_path.is_relative_to(dd):
            return {"error": f"Invalid filename (path traversal rejected): {filename}"}
        if not full_path.is_file():
            return {"error": f"Diagram file not found: {filename}"}
        try:
            new_model = ProcessModel.from_drawio(str(full_path))
        except Exception as e:
            logger.error(f"Failed to load diagram {full_path}: {e}")
            return {"error": f"Failed to parse diagram: {e}"}
        # Success: swap model atomically
        state["model"] = new_model
        state["current_file"] = filename
        logger.info(f"Loaded BPMN diagram: {full_path}")
        return {
            "loaded": filename,
            "summary": new_model.summary(),
        }

    async def handle_reload(arguments: dict) -> dict:
        current = state["current_file"]
        if not current:
            return {"error": "No diagram is currently loaded to reload"}
        dd = state["diagrams_dir"]
        full_path = (dd / current).resolve()
        if not full_path.is_relative_to(dd):
            return {"error": f"Diagram path escapes diagrams directory: {current}"}
        if not full_path.is_file():
            return {"error": f"Current diagram file no longer exists: {current}"}
        try:
            new_model = ProcessModel.from_drawio(str(full_path))
        except Exception as e:
            logger.error(f"Failed to reload diagram {full_path}: {e}")
            return {"error": f"Failed to reload diagram: {e}"}
        state["model"] = new_model
        logger.info(f"Reloaded BPMN diagram: {full_path}")
        return {
            "reloaded": current,
            "summary": new_model.summary(),
        }

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    TOOL_HANDLERS = {
        "bpmn_get_overview": handle_get_overview,
        "bpmn_get_phases": handle_get_phases,
        "bpmn_get_phase_tasks": handle_get_phase_tasks,
        "bpmn_get_task": handle_get_task,
        "bpmn_get_agent": handle_get_agent,
        "bpmn_get_next": handle_get_next,
        "bpmn_get_predecessors": handle_get_predecessors,
        "bpmn_get_gateway": handle_get_gateway,
        "bpmn_get_lanes": handle_get_lanes,
        "bpmn_get_transition_rules": handle_get_transition_rules,
        "bpmn_get_path": handle_get_path,
        "bpmn_list_diagrams": handle_list_diagrams,
        "bpmn_load_diagram": handle_load_diagram,
        "bpmn_reload": handle_reload,
    }

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="bpmn_get_overview",
                description="Get a summary of the loaded BPMN process: pools, lanes, phases, task/gateway/event counts.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="bpmn_get_phases",
                description="List all workflow phases with their numbers and names.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="bpmn_get_phase_tasks",
                description="Get all tasks in a specific phase with agent assignments, status transitions, and rules.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "phase": {
                            "type": "string",
                            "description": "Phase number (e.g. '1', '5', '7')",
                        },
                    },
                    "required": ["phase"],
                },
            ),
            Tool(
                name="bpmn_get_task",
                description="Get full details for a specific task: agent, phase, status transitions, rules, lane, mandatory flag.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task element ID (e.g. 'task_code_review', 'task_security_scan')",
                        },
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="bpmn_get_agent",
                description="Quick lookup: which agent handles a specific task?",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task element ID",
                        },
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="bpmn_get_next",
                description="Get the next node(s) reachable from a given node. At a gateway, optionally filter by condition label (e.g. 'yes', 'no').",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "Current node ID (task, gateway, or event)",
                        },
                        "condition": {
                            "type": "string",
                            "description": "Optional condition label to filter branches (case-insensitive prefix match, e.g. 'yes' matches 'Yes (Rework)')",
                        },
                    },
                    "required": ["node_id"],
                },
            ),
            Tool(
                name="bpmn_get_predecessors",
                description="Get all predecessor nodes (nodes with edges leading into the given node).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "node_id": {
                            "type": "string",
                            "description": "Node ID to find predecessors for",
                        },
                    },
                    "required": ["node_id"],
                },
            ),
            Tool(
                name="bpmn_get_gateway",
                description="Get gateway details with all outgoing branches and their condition labels. Use this to understand decision points.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "gateway_id": {
                            "type": "string",
                            "description": "Gateway element ID (e.g. 'gw_issues', 'gw_frontend')",
                        },
                    },
                    "required": ["gateway_id"],
                },
            ),
            Tool(
                name="bpmn_get_lanes",
                description="Get all swim lanes with their contained tasks. Shows the organizational structure of the workflow.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="bpmn_get_transition_rules",
                description="Get status transition rules for a task: status_from, status_to, and any special rules/constraints.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "Task element ID",
                        },
                    },
                    "required": ["task_id"],
                },
            ),
            Tool(
                name="bpmn_get_path",
                description="Find the sequence of nodes between two points in the process. Returns the shortest path via BFS.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "from_id": {
                            "type": "string",
                            "description": "Starting node ID",
                        },
                        "to_id": {
                            "type": "string",
                            "description": "Target node ID",
                        },
                    },
                    "required": ["from_id", "to_id"],
                },
            ),
            Tool(
                name="bpmn_list_diagrams",
                description="List all .drawio diagram files available in the diagrams directory with their sizes.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="bpmn_load_diagram",
                description="Load a specific .drawio diagram file at runtime, replacing the current model. Use bpmn_list_diagrams to see available files.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Name of the .drawio file to load (e.g. 'dev-workflow.drawio'). Must be in the diagrams directory.",
                        },
                    },
                    "required": ["filename"],
                },
            ),
            Tool(
                name="bpmn_reload",
                description="Reload the current diagram from disk. Use this after editing the diagram in Draw.io to refresh the model.",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    # ------------------------------------------------------------------
    # Call tool dispatcher
    # ------------------------------------------------------------------

    @server.call_tool()
    async def call_tool(name: str, arguments: Any) -> list[TextContent]:
        try:
            handler = TOOL_HANDLERS.get(name)
            if handler is None:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

            result = await handler(arguments or {})
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error calling tool {name}: {e}", exc_info=True)
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return server


def main():
    """Main entry point for the MCP server."""
    import asyncio

    async def run():
        server = create_server()
        async with stdio_server() as (read_stream, write_stream):
            logger.info("MCP SH BPMN server starting...")
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    asyncio.run(run())


if __name__ == "__main__":
    main()
