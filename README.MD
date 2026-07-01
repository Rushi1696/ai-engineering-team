# AI Engineering Team

An autonomous multi-agent software engineering organization, built on
[LangGraph](https://github.com/langchain-ai/langgraph), that converts a
natural-language requirement into a production-ready software project.

Eleven specialized agents — Product Manager, Architect, Task Planner,
Backend, Frontend, QA, Debug, Reviewer, DevOps, Documentation, and Final
Project — execute in a directed graph. Agents never communicate
directly with one another; all coordination happens through reads and
writes to a single shared state object, `EngineeringState`.

> **Project status:** in active development. See
> [`PROJECT_STATUS.md`](./PROJECT_STATUS.md) for the current
> phase-by-phase build status. `PROJECT_HANDOFF.md` predates that file
> and should not be relied on for current status.

---

## Pipeline

```
User
  → ProductManagerAgent
  → ArchitectAgent
  → TaskPlannerAgent
  → BackendAgent
  → FrontendAgent
  → QAAgent
  → DebugAgent
  → ReviewerAgent
  → DevOpsAgent
  → DocumentationAgent
  → FinalProjectAgent
```

`DebugAgent` is not purely linear — it can route back to
`BackendAgent`, `FrontendAgent`, `QAAgent`, `ReviewerAgent`,
`DevOpsAgent`, `ArchitectAgent`, or `ProductManagerAgent` depending on
where a failure originated, subject to a global debug-cycle cap.

## Core Principles

- **Agents never communicate directly.** All coordination is through
  shared state.
- **`EngineeringState` is the single source of truth.** Every agent
  reads the full state and returns a partial update; nothing is held
  in memory between nodes.
- **LangGraph orchestrates the workflow.** Nodes, conditional edges,
  and state merging are all LangGraph primitives.
- **State transitions are checkpointed**, so a run can be inspected or
  resumed.
- **Human approval is required** for requirement-level or
  design-level rejections.
- **Debug loops have a global limit** (`max_debug_cycles`, default
  10) that cannot be bypassed by human approval — it is the one
  unconditional termination path.

## Architecture Documents

The full approved architecture lives under
[`.kiro/specs/engineering-state-design/`](./.kiro/specs/engineering-state-design/):

| Document | Contents |
|---|---|
| `requirements.md` | 21 requirements covering every state field, agent contract, reducer, and control field |
| `design.md` | Per-agent contracts (inputs, outputs, tools, failure modes) for all 11 agents; state ownership matrix; folder ownership strategy |
| `workflow-design-revised.md` | LangGraph graph topology, routing logic, debug-cycle cap, timeout strategy, checkpoint strategy |
| `architecture-review.md`, `architecture-alignment-report.md`, `final-sync-validation-report.md` | Review and sync artifacts from the architecture approval process |

These documents are the source of truth for implementation. Code is
not expected to redesign anything they specify — discrepancies found
during implementation are flagged in code comments rather than
silently resolved.

## Project Layout

```
.kiro/specs/engineering-state-design/   Approved architecture documents
state/
  models.py            14 Pydantic v2 models used across EngineeringState
  reducers.py           replace / dict_merge / append reducers, plus
                         memory_references_reducer
  validators.py         Write-once guards, enum validators, DebugAgent
                         routing allow-list
  engineering_state.py   The EngineeringState TypedDict (40 fields) and
                         create_initial_state()
agents/
  base_agent.py          Shared base class inherited by all 11 agents
tests/
  state/                 Tests for the EngineeringState foundation
  agents/                Tests for BaseAgent
app.py                   Entry point
requirements.txt          Python dependencies
```

Planned but not yet present: `workflows/` (graph definition and
routers), the 11 concrete agent modules, `memory/`, `tools/`,
`prompts/`, `configs/`, and `docs/generated/` — see `design.md` Section
6 for the full intended layout and ownership model.

## State Model

`EngineeringState` is a `TypedDict` (not a Pydantic model — required
for LangGraph's state-merge protocol) with 40 fields, each wrapped in
`Annotated[T, reducer_fn]`. Fields fall into a few groups:

- **Identity & run metadata** — `run_id`, `session_id`, `project_name`, timestamps
- **Agent document outputs** — `requirements`, `architecture`, `task_plan`, `test_plan`, `review_report`
- **Agent artifact collections** — `code_artifacts`, `test_artifacts`, `deployment_artifacts`, `documentation_artifacts`
- **Workflow status & routing** — `workflow_status`, `current_agent`, `next_agent`
- **Error tracking** — `errors` (append-only `ErrorRecord` list)
- **Retry & escalation** — `retry_counts`, `max_retries`, `human_approval_required`
- **Execution & memory infrastructure** — `execution_metadata`, `agent_scratchpad`, `memory_references`
- **Checkpoint & versioning** — `checkpoint_stage`, `state_version`, `checkpoints`
- **Workflow control** — `total_debug_cycles`, `forward_stage`, `total_invocations`, `max_debug_cycles`, and related fields

Construct an initial state only through `create_initial_state()` in
`state/engineering_state.py` — it is the sole sanctioned way to build a
correctly-initialized state.

## BaseAgent

`agents/base_agent.py` is the shared abstract base class every concrete
agent inherits from. It owns the bookkeeping that is identical across
all 11 agents, so each concrete agent only needs to implement its own
business logic:

- Per-agent execution metadata lifecycle (`running` → `completed` /
  `failed`, with timing)
- Error recording (`ErrorRecord` construction on any exception)
- Retry counting and escalation to human approval at the configured
  retry limit
- The global `total_invocations` counter

A concrete agent implements `execute(state) -> dict`; `BaseAgent`'s
`invoke(state) -> dict` — the actual LangGraph node callable — wraps
it and never raises, converting any failure into a normal partial
state update instead.

## Setup

```bash
pip install -r requirements.txt
```

Key dependencies: `langgraph` (+ `langgraph-checkpoint`,
`langgraph-prebuilt`, `langgraph-sdk`), `langchain` /
`langchain-openai`, `openai`, `pydantic`, `pyyaml`, `python-dotenv`.

```bash
python app.py
```

## Running Tests

```bash
pip install pytest
python -m pytest tests/ -v
```

## Development Workflow

This project is built file-by-file against the approved architecture:

1. Inspect the current repository state before writing anything.
2. Identify the next unfinished component per the roadmap in
   `PROJECT_STATUS.md`.
3. Explain the implementation plan before coding.
4. Implement one component at a time.
5. Write and run tests for that component.
6. Stop after the requested phase — no scope creep into later phases.

No component is redesigned unless a real contradiction in the approved
architecture is found, and any such finding is flagged explicitly
rather than silently patched over.