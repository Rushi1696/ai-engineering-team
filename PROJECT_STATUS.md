# Project Status — AI Engineering Team

> This file reflects the state of the repository as verified directly
> against `github.com/Rushi1696/ai-engineering-team` (main branch).
> `PROJECT_HANDOFF.md` in this repo is currently empty (0 bytes) and
> should not be relied on until repopulated. This file is intended to
> replace it as the up-to-date status reference.

**Last updated:** 2026-06-29

---

## Goal

Build an autonomous multi-agent software engineering organization that
converts natural language requirements into a production-ready software
project, orchestrated by LangGraph with `EngineeringState` as the single
source of truth between agents.

## Pipeline (11 agents)

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

## Core Principles

- Agents never communicate directly.
- `EngineeringState` is the single source of truth.
- LangGraph orchestrates the workflow.
- State transitions are checkpointed.
- Human approval is required for requirement/architecture rejection.
- Debug loops have a global limit (`max_debug_cycles`, default 10).

---

## Status Summary

| Phase | Status |
|---|---|
| Architecture (requirements, design, workflow design, reviews, alignment, sync) | ✅ Complete — Approved |
| EngineeringState Foundation (`state/`) | ✅ Complete and tested |
| BaseAgent (`agents/base_agent.py`) | ✅ Complete and tested |
| Graph Skeleton (`workflows/`) | ✅ Complete and tested |
| ProductManagerAgent | ⬜ Not started |
| ArchitectAgent | ⬜ Not started |
| TaskPlannerAgent | ⬜ Not started |
| BackendAgent | ⬜ Not started |
| FrontendAgent | ⬜ Not started |
| QAAgent | ⬜ Not started |
| DebugAgent | ⬜ Not started |
| ReviewerAgent | ⬜ Not started |
| DevOpsAgent | ⬜ Not started |
| DocumentationAgent | ⬜ Not started |
| FinalProjectAgent | ⬜ Not started |

---

## Completed Work

### Architecture (`.kiro/specs/engineering-state-design/`)

- `requirements.md` — 21 requirements covering all state fields, agent
  contracts, reducers, error/retry tracking, and workflow control.
- `design.md` — Agent contracts (Section 1) for all 11 agents, state
  ownership matrix, LangGraph topology, tooling/model strategy, folder
  ownership strategy (Section 6).
- `workflow-design-revised.md` — Revised graph topology, routing logic,
  global debug-cycle cap, timeout strategy, checkpoint strategy,
  `HumanApprovalNode` specification.
- `architecture-review.md`, `architecture-alignment-report.md`,
  `final-sync-validation-report.md` — Review and sync artifacts
  resolving issues ISSUE-08 through ISSUE-17.

**Architecture status: Approved.**

### EngineeringState Foundation (`state/`)

- `state/models.py` — 14 Pydantic v2 models (`ProductRequirements`,
  `SystemArchitecture`, `ImplementationTask`, `TaskPlan`, `CodeArtifact`,
  `TestArtifact`, `TestPlan`, `ReviewReport`, `DeploymentArtifact`,
  `DocumentationArtifact`, `ErrorRecord`, `AgentExecutionMetadata`,
  `MemoryReferences`, `FinalProjectOutput`).
- `state/reducers.py` — `replace_reducer`, `dict_merge_reducer`,
  `append_reducer`, plus the field-specific `memory_references_reducer`
  and the `resolve_error_record` helper.
- `state/validators.py` — Write-once guards, `workflow_status` /
  `checkpoint_stage` / `rejection_scope` enum validators, DebugAgent
  `next_agent` allow-list (7 permitted targets).
- `state/engineering_state.py` — The `EngineeringState` TypedDict (40
  fields — see field-count note below) and `create_initial_state()`,
  the sole sanctioned initial-state factory.

**Status: Complete and tested** (test suite in
`state/test_engineering_state.py`).

> **Field-count note:** upstream prose in some architecture documents
> states "41 fields." An exhaustive enumeration performed during
> implementation — cross-checked against the documents' own group and
> field tables — confirms exactly **40** unique fields. This is flagged
> as an upstream documentation discrepancy, not resolved by adding a
> fabricated field.

> **Known behavior note (rejection_scope lifecycle):** Under `replace_reducer`
> (Requirement 18 criterion 7), writing `None` is a no-op that preserves the existing
> value. Consequently, once populated by `ReviewerAgent`, `rejection_scope` is never
> cleared back to `None` in `EngineeringState`. Downstream consumers and routers must
> gate on `workflow_status == "escalation_required"` AND `review_report.overall_status == "rejected"`
> rather than inspecting `rejection_scope` alone.

### BaseAgent (`agents/base_agent.py`)

Shared abstract base class inherited by all 11 concrete agents, derived
from the seven-part contract shape repeated identically across all 11
per-agent specs in `design.md` Section 1, plus Requirements 12
(Error Tracking), 13 (Retry and Escalation Tracking), 14 (Per-Agent
Execution Metadata), and 21 criterion 7 (`total_invocations`).

- Abstract `execute(state) -> dict` for subclasses to implement their
  agent-specific logic.
- Template method `invoke(state) -> dict` — the LangGraph node
  callable — handling:
  - `execution_metadata` lifecycle (`running` → `completed`/`failed`,
    with `duration_seconds` computed from timestamps).
  - `ErrorRecord` construction on any exception from `execute()`
    (`ValueError` → `validation_failure`, all other exceptions →
    `unhandled_exception`).
  - Retry counting and escalation to `human_approval_required` when an
    agent's retry count reaches its effective `max_retries` (state
    override if present, else the agent's constructor default, else 3).
  - Unconditional `total_invocations` increment on every invocation,
    success or failure.
  - `invoke()` never raises — failures are converted into a normal
    partial state update so the router can act on `workflow_status` /
    `human_approval_required` rather than the graph crashing.

**Status: Complete and tested** — 48 tests in
`tests/agents/test_base_agent.py`, covering construction/validation,
the success path, the failure path, error-type classification,
retry/escalation edge cases, and `total_invocations` behavior on both
paths. Full suite (BaseAgent + pre-existing `state/` tests): **185
passed, 1 skipped** (LangGraph-compile smoke test skips when
`langgraph` is not installed in the environment).

### Graph Skeleton (`workflows/`)

LangGraph graph topology, routing, interrupt handling, and execution orchestration per `design.md` Section 6.1 and `workflow-design-revised.md` Section 3.

- `workflows/constants.py` — Node names (`NODE_*`), workflow status strings (`STATUS_*`), human approval decisions (`APPROVAL_DECISION_*`), and routing mappings (`NEXT_AGENT_TO_NODE`, `REJECTION_SCOPE_TO_AGENT`, `AGENT_NAME_TO_NODE`).
- `workflows/routers.py` — Pure conditional edge routers for all agent and synthetic nodes enforcing the strict 4-level precedence hierarchy (P1: deadline, P2: global debug cap, P3: human approval hold, P4: normal stage routing).
- `workflows/interrupts.py` — `HumanApprovalNode` utilizing native LangGraph `interrupt()` and `Command(resume=...)` for human-in-the-loop review escalations and retry exhaustion holds.
- `workflows/graph.py` — `build_graph()` constructing the StateGraph topology with 11 agent nodes, 3 synthetic nodes (`human_approval`, `deadline_enforcer`, `global_cap_handler`), and conditional routing edges.
- `workflows/runner.py` — Pipeline runner invoking the compiled graph, initializing default configurations, and capturing final results.

**Status: Complete and tested** — 139 workflow tests across `tests/workflows/` (routers, interrupts, graph structural & execution, runner). Full test suite: **325 tests passed** (0 failures, 2 warnings).

> **Known notes:**
> - (a) Deadline is enforced in router P1 (pipeline-level, checked after each node); per-node timeouts remain a TODO (§3.9).
> - (b) `runner` has no resume-after-restart and uses `MemorySaver`.
> - (c) `parse_iso_utc` currently lives in `workflows/routers.py`; to relocate to a shared utility module.

---

## Next Phase

**ProductManagerAgent** (`agents/product_manager/`) — The first concrete pipeline agent, converting natural language user requirements into structured `ProductRequirements` artifact, per `design.md` Section 1.1 and Section 6.1.

---

## Development Rules

- Follow the approved architecture exactly.
- Never redesign unless a real contradiction is found — and flag it
  rather than silently resolving it.
- Generate one component at a time.
- Explain before coding.
- Test every component.
- Run tests before continuing.
- Stop after completing the requested phase.
- Treat the repository, not any handoff summary, as the source of
  truth once both are available.