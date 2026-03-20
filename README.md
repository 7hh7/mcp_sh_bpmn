# MCP BPMN Server (`mcp-sh-bpmn`)

An MCP server that parses Draw.io (`.drawio`) BPMN diagrams and exposes process definitions as queryable tools. It enables Claude Code CLI to navigate workflow diagrams at runtime — following phases, tasks, gateways, and agents step-by-step without hardcoding process logic.

## Architecture

```
~/.claude/commands/              Draw.io Diagrams              MCP BPMN Server
 ┌──────────────────┐    ┌──────────────────────────┐    ┌──────────────────────┐
 │ /dev-orchestrator│───>│ dev-workflow.drawio      │<───│ bpmn_load_diagram    │
 │ /deep-research   │───>│ deep-research-workflow   │    │ bpmn_get_phases      │
 │ ...              │───>│ ...                      │    │ bpmn_get_phase_tasks │
 └──────────────────┘    └──────────────────────────┘    │ bpmn_get_task        │
                                                         │ bpmn_get_gateway     │
 ~/.claude/agents/                                       │ bpmn_get_next        │
 ┌──────────────────┐                                    │ ...14 tools total    │
 │ agent_dev_python │<── delegated by orchestrators      └──────────────────────┘
 │ agent_qa_*       │    based on BPMN task agent
 │ agent_dev_*      │    assignments
 └──────────────────┘
```

The server loads `.drawio` XML files from the `diagrams/` directory, parses BPMN elements (pools, lanes, tasks, gateways, events, sequence flows), and exposes them via 14 MCP tools that allow Claude to query and navigate the process model.

## Installation

```bash
cd /home/h7h7/tools/MCP/mcp_servers/mcp_sh_bpmn
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Register with Claude Code:

```bash
claude mcp add sh-bpmn ./venv/bin/mcp-sh-bpmn
```

## Configuration

Environment variables (optional):

| Variable | Default | Description |
|----------|---------|-------------|
| `BPMN_DIAGRAMS_DIR` | `./diagrams` | Directory containing `.drawio` files |
| `BPMN_DEFAULT_DIAGRAM` | `dev-workflow.drawio` | Diagram loaded on server startup |

## MCP Tools

### Diagram Management

| Tool | Description |
|------|-------------|
| `bpmn_list_diagrams` | List all `.drawio` files in the diagrams directory with sizes |
| `bpmn_load_diagram` | Load a specific diagram, replacing the current model |
| `bpmn_reload` | Reload the current diagram from disk (picks up external edits) |

### Process Overview

| Tool | Description |
|------|-------------|
| `bpmn_get_overview` | Summary of loaded model: pools, lanes, task/gateway/event counts, phases |
| `bpmn_get_phases` | List all workflow phases with numbers and names |
| `bpmn_get_lanes` | List all lanes (swimlanes) in the process |

### Task Queries

| Tool | Description |
|------|-------------|
| `bpmn_get_phase_tasks` | Get all tasks in a specific phase with agent assignments and status transitions |
| `bpmn_get_task` | Full details for a task: agent, phase, status transitions, rules, lane, mandatory flag |
| `bpmn_get_agent` | Quick lookup: which agent handles a specific task? |
| `bpmn_get_transition_rules` | Status transition rules for a task: `status_from`, `status_to`, and constraints |

### Flow Navigation

| Tool | Description |
|------|-------------|
| `bpmn_get_next` | Get next node(s) from a given node. At gateways, filter by condition label |
| `bpmn_get_predecessors` | Get nodes that feed into a given node |
| `bpmn_get_path` | Find the path between two nodes |
| `bpmn_get_gateway` | Gateway details with all outgoing branches and condition labels |

## BPMN Diagram Conventions

### Task Properties

Tasks are defined as `<object>` elements in Draw.io with custom attributes that the server parses:

```xml
<object label="Design Role&#xa;Persona"
        agent="agent_generator_prompt_architect"
        phase="3"
        phase_name="Prompt Construction"
        mandatory="true"
        status_from="deep_researched"
        status_to="persona_designed"
        rule="Create character name, background, experience narrative"
        id="task_design_persona">
  <mxCell style="shape=mxgraph.bpmn.task;taskMarker=user;..." vertex="1" parent="lane_prompt_architect">
    <mxGeometry x="1000" y="80" width="180" height="60" as="geometry" />
  </mxCell>
</object>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `label` | Yes | Display name (use `&#xa;` for line breaks) |
| `agent` | Yes | Agent responsible for this task (e.g., `agent_dev_python`) |
| `phase` | Yes | Phase number (e.g., `1`, `2`, `3C`) |
| `phase_name` | Yes | Human-readable phase name |
| `mandatory` | Yes | `true` or `false` — mandatory tasks must not be skipped |
| `status_from` | Yes | Required story status before executing this task |
| `status_to` | Yes | Status after task completion |
| `rule` | Yes | Execution rule or description for the agent |

### Gateway Conventions

Gateways use exclusive (XOR) or parallel (+) types. Outgoing edges carry condition labels:

```xml
<!-- Gateway -->
<mxCell id="gw_quality_ok" value="Quality&#xa;OK?"
        style="shape=mxgraph.bpmn.shape;symbol=exclusiveGw;gwType=exclusive;..." />

<!-- Outgoing edge with condition -->
<mxCell id="flow_yes" value="Yes" source="gw_quality_ok" target="task_next" />
<mxCell id="flow_no"  value="No (Rework)" source="gw_quality_ok" target="task_redo" />
```

Condition matching in `bpmn_get_next` is **case-insensitive prefix**: `"yes"` matches `"Yes (Rework)"`.

### Link Events (Sub-Process References)

Throwing link events reference other diagrams for sub-process execution:

```xml
<mxCell id="evt_link_deep_research" value=""
        style="shape=mxgraph.bpmn.event;symbol=link;outline=throwing;..." />
<mxCell id="lbl_link" value="-> deep-research-workflow.drawio" />
```

The orchestrator loads the referenced diagram, executes it, then reloads the parent diagram.

### Phase Labels

Phase labels are text cells with a naming convention:

```xml
<mxCell id="phase1_label" value="PHASE 1: Input &amp; Classification"
        style="text;fontStyle=5;fontColor=#9673a6;" />
```

The server extracts phase numbers from task `phase` attributes, not from labels.

## Diagrams

### Available Diagrams

| Diagram | Purpose | Phases |
|---------|---------|--------|
| `agent-command-generator-workflow.drawio` | Agent/command file generation for Claude Code CLI | 6 (Input → Research → Construct → Validate → Quality → Output) |
| `deep-research-workflow.drawio` | Deep internet research orchestration | Multi-phase research with source validation |


### Example: Agent & Command Generator Workflow

`agent-command-generator-workflow.drawio` demonstrates a complete workflow for generating Claude Code CLI agent (`.md`) and command (`.md`) files.

**Lanes**: User, Orchestrator, Prompt Architect, Token Validator

**Flow**:

```
Phase 1: Input & Classification
  User provides: type (agent/command), role name, domain, expertise, model
  Gateway: Agent or Command? (classification for Phase 2 routing)

Phase 2: Research & Analysis
  Analyze existing agents/commands for duplicates
  Research domain expertise
  Deep Research via /deep-research (linked sub-process)
  Gateway: Agent or Command? (routes to Phase 3 or 3C)

Phase 3: Agent Prompt Construction (Agent path)
  Design Role Persona → Define Expertise → Write Workflow → Compose Description
  Output: frontmatter (name, description, model) + persona-based markdown

Phase 3C: Command Construction (Command path)
  Design Orchestrator Role → Define Rules & Constraints → Write Navigation → Compose Skill Entry
  Output: orchestrator-style markdown (no frontmatter, uses $ARGUMENTS)

Phase 4: Token Validation & Assembly
  Count tokens → Under 15k? (if no → Trim & Optimize → recount loop)
  Assemble final .md file

Phase 5: Quality Check & Output
  Validate structure → Verify no duplicates → Quality OK? (if no → rework loop)
  Write to ~/.claude/agents/ or ~/.claude/commands/
  Register (optional)
```

**Agent vs Command output differences**:

| Aspect | Agent | Command |
|--------|-------|---------|
| Location | `~/.claude/agents/` | `~/.claude/commands/` |
| Frontmatter | `name`, `description`, `model` | None |
| Style | Role persona ("You are **Name**...") | Orchestrator ("You are the **Orchestrator** for...") |
| Invocation | Subagent type in Agent tool | `/command-name` slash command |
| User input | Via agent prompt parameter | `$ARGUMENTS` variable |

## Creating a New Diagram

1. Open [draw.io](https://app.diagrams.net/) or the desktop app
2. Create a BPMN diagram with:
   - A pool containing one or more lanes
   - Tasks as `<object>` elements with the required attributes (see conventions above)
   - Gateways with labeled outgoing edges
   - Start and end events
   - Phase labels as text cells
3. Save as `.drawio` (uncompressed XML) in the `diagrams/` directory
4. Load it at runtime: `bpmn_load_diagram(filename="my-workflow.drawio")`
5. Alternative to step 1 to 4 use the existing 'agent-command-generator-workflow.drawio' diagram to generate an agent with the capabilities to generate BPMN diagrams.
   You need for step 5 also the mcp_sh_browser server registered within claude code cli.

## Project Structure

```
mcp_sh_bpmn/
├── diagrams/                    # .drawio BPMN diagram files
│   ├── agent-command-generator-workflow.drawio
│   ├── deep-research-workflow.drawio
│   └── ...
├── src/mcp_sh_bpmn/
│   ├── server.py                # MCP server with 14 tools
│   ├── process_model.py         # BPMN process model and navigation logic
│   ├── drawio_loader.py         # Draw.io XML parser
│   ├── bpmn_classifier.py       # BPMN element type classification
│   └── __init__.py
├── tests/
├── pyproject.toml
└── README.md
```

## Development

```bash
source venv/bin/activate
pip install -e ".[dev]"
pytest
```
