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
| ProductManagerAgent (`agents/product_manager_agent.py`) | ✅ Complete and tested |
| ArchitectAgent (`agents/architect_agent.py`) | ✅ Complete and tested |
| TaskPlannerAgent (`agents/task_planner_agent.py`) | ✅ Complete and tested |
| BackendAgent (`agents/backend_agent.py`) | ✅ Complete and tested |
| FrontendAgent (`agents/frontend_agent.py`) | ✅ Complete and tested |
| QAAgent (`agents/qa_agent.py`) | ✅ Complete and tested |
| DebugAgent (`agents/debug_agent.py`) | ✅ Complete and tested |
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
- Shared prompt helper `strip_metadata_header(text: str) -> str` stripping
  YAML frontmatter and HTML comment blocks from prompt templates across
  concrete agents without inter-agent imports (enforcing design.md §6 rule 3).

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

### ProductManagerAgent (`agents/product_manager_agent.py`)

First concrete pipeline agent, converting natural language user requirements into structured `ProductRequirements` artifact, per `design.md` Section 1.1 and Section 6.1.

- `agents/product_manager_agent.py` — Inherits from `BaseAgent`; features internal 3-attempt loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged; idempotent `forward_stage` increments (Req 21 crit 6); `requirements.version` incrementing (Req 3 crit 3); reset of `retry_counts` to 0 on success (Req 13 crit 7).
- `tools/shared/llm_client.py` — Protocol `LLMClient` and configurable test double `FakeLLMClient`.
- `prompts/product_manager/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.

**Status: Complete and tested** — Full test suite: **353 tests passed** (0 failures, 2 warnings).

### ArchitectAgent (`agents/architect_agent.py`)

Second concrete pipeline agent, translating `ProductRequirements` into structured `SystemArchitecture` artifact, per `design.md` Section 1.2 and Section 6.1.

- `agents/architect_agent.py` — Inherits from `BaseAgent`; features internal retry loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged; fast-fails on missing or empty requirements; idempotent `forward_stage` advance to 2 (Req 21 crit 6); `architecture.version` incrementing (Req 4 crit 3); reset of `retry_counts` to 0 on success (Req 13 crit 7); preservation of other agents' retry counts.
- `prompts/architect/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.
- `tests/agents/test_architect_agent.py` & `tests/workflows/test_graph_architect.py` — 18 tests covering valid generation, invalid output, missing input, internal retry recovery, retry exhaustion, and graph integration.

**Status: Complete and tested** — Full test suite: **371 tests passed** (0 failures, 2 warnings).

### TaskPlannerAgent (`agents/task_planner_agent.py`)

Third concrete pipeline agent, decomposing `SystemArchitecture` and `ProductRequirements` into an ordered, dependency-resolved `TaskPlan` of atomic `ImplementationTask` entries, per `design.md` Section 1.3 and Section 6.1.

- `agents/task_planner_agent.py` — Inherits from `BaseAgent`; features internal retry loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged; fast-fails on missing or empty requirements/architecture; idempotent `forward_stage` advance to 3 (Req 21 crit 6); `task_plan.version` incrementing (Req 5 crit 4); reset of `retry_counts` to 0 on success (Req 13 crit 7); topological validation of `execution_order` and dependencies; preservation of other agents' retry counts.
- `prompts/task_planner/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.
- `tests/agents/test_task_planner_agent.py` & `tests/workflows/test_graph_task_planner.py` — 26 tests covering constructor validation, valid plan generation, versioning, forward stage idempotency, missing/invalid input fast-fails, retry recovery, retry exhaustion, custom retry ceilings, and graph execution routing.

**Status: Complete and tested** — Full test suite: **398 tests passed** (0 failures, 2 warnings).

### BackendAgent (`agents/backend_agent.py`)

Fourth concrete pipeline agent, implementing backend `ImplementationTask` entries from `task_plan` by generating server-side code artifacts and writing them into `code_artifacts`, per `design.md` Section 1.4 and Section 6.1.

- `agents/backend_agent.py` — Inherits from `BaseAgent`; features internal retry loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged; fast-fails on missing or empty requirements/architecture/task_plan; filters assigned backend tasks respecting `task_plan.execution_order`; validates task dependencies; generates structured `CodeArtifact` entries with per-file versioning (Req 6 crit 3); updates backend tasks to `"completed"` in `task_plan`; idempotent `forward_stage` advance to 4 (Req 21 crit 6); reset of `retry_counts` to 0 on success (Req 13 crit 7); preservation of other agents' retry counts.
- `prompts/backend/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.
- `tests/agents/test_backend_agent.py` & `tests/workflows/test_graph_backend.py` — 23 tests covering constructor validation, valid artifact generation, versioning, task filtering, no-op skipping, dependency validation, fast-fails on invalid/missing inputs, retry recovery, retry exhaustion, custom retry ceilings, and graph execution routing.

**Status: Complete and tested** — Full test suite: **421 tests passed** (0 failures, 2 warnings).

### FrontendAgent (`agents/frontend_agent.py`)

Fifth concrete pipeline agent, implementing frontend `ImplementationTask` entries from `task_plan` by generating client-side components and styles as `CodeArtifact` entries (`artifact_type == "frontend"`), per `design.md` Section 1.5 and Section 6.1.

- `agents/frontend_agent.py` — Inherits from `BaseAgent`; features internal retry loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged; fast-fails on missing or empty requirements/architecture/task_plan; filters assigned frontend tasks respecting `task_plan.execution_order`; validates task dependencies; generates structured `CodeArtifact` entries with per-file versioning (Req 6 crit 4); updates frontend tasks to `"completed"` in `task_plan`; advances `forward_stage` to 5 idempotently (Req 21 crit 6); reset of `retry_counts` to 0 on success (Req 13 crit 7); preserves existing backend artifacts and other agents' retry counts.
- `prompts/frontend/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.
- `tests/agents/test_frontend_agent.py` & `tests/workflows/test_graph_frontend.py` — 23 tests covering constructor validation, valid artifact generation, versioning, task filtering, no-op skipping, dependency validation, fast-fails on invalid/missing inputs, retry recovery, retry exhaustion, custom retry ceilings, and graph execution routing.

**Status: Complete and tested** — Full test suite: **444 tests passed** (0 failures, 2 warnings).

### QAAgent (`agents/qa_agent.py`)

Sixth concrete pipeline agent, generating test plans and test artifacts based on requirements, architecture, task plan, and backend/frontend code artifacts, per `design.md` Section 1.6 and Section 6.1.

- `agents/qa_agent.py` — Inherits from `BaseAgent`; features internal retry loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged; fast-fails on missing or empty requirements/architecture/task_plan/code_artifacts; executes tests via injected `TestRunner`; conditional routing: passes route to `ReviewerAgent` with `workflow_status=STATUS_QA_COMPLETE`, `forward_stage` advanced to 6, and `retry_counts["QAAgent"]=0`; test failures route to `DebugAgent` with `workflow_status=STATUS_DEBUG_REQUIRED`, `ErrorRecord(error_type="other", message="test_failure: ...")` truncated to 2000 chars, resetting `retry_counts["QAAgent"]=0` without advancing `forward_stage` or marking QA tasks completed; stamps and increments versions for `test_plan` and `test_artifacts` (Req 7 crit 3-4); preserves other agents' retry counts.
- `tools/qa/test_runner.py` — `TestRunner` Protocol, `TestRunResult` dataclass, and `FakeTestRunner` returning scripted test results in order.
- `prompts/qa/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.
- `tests/agents/test_qa_agent.py` & `tests/workflows/test_graph_qa.py` — 17 tests covering constructor validation, valid QA generation, test execution failure routing to DebugAgent, runner output truncation to 2000 chars, versioning, missing/invalid input fast-fails, retry recovery, retry exhaustion, forward stage idempotency, and real-graph execution routing (pass, fail then debug stub then re-run pass, runner always fails to global cap, exhaustion then approve, exhaustion then reject).

**Status: Complete and tested** — Full test suite: **464 tests passed** (0 failures, 0 warnings).
- **Known note**: QA regenerates the test suite each debug loop; decide at the real-LLM phase.

### DebugAgent (`agents/debug_agent.py`)

Seventh concrete pipeline agent, diagnosing failures, producing corrected code and test artifacts, and routing back to appropriate agents or human approval, per `design.md` Section 1.7 and Section 6.1.

- `agents/debug_agent.py` — Inherits from `BaseAgent`; features internal retry loop for validation recovery and exponential backoff; exhaustion return sets `human_approval_required=True` while keeping `workflow_status` unchanged and without incrementing `total_debug_cycles`; fast-fails on missing or unresolvable error inputs; uses `ErrorLogParser` to extract structured error details; validates candidate fixes in isolation via `CodeExecutionSandbox`; generates structured `DebugResolution` with per-file versioning increments for corrected `code_artifacts` and `test_artifacts`; resolves target errors by appending a resolved copy (`same error_id, resolved=True, resolution_agent, resolution_notes`); clears `current_debug_target` to `""`; increments `total_debug_cycles` by 1 only on completed fix attempts; sets `next_agent` to one of the 7 permitted targets (`validate_debug_agent_next_agent`) while leaving `workflow_status` unchanged; resets `retry_counts["DebugAgent"]=0` on success.
- `tools/debug/sandbox.py` — `CodeExecutionSandbox` Protocol, `SandboxExecutionResult` dataclass, and `FakeExecutionSandbox` test double.
- `tools/debug/log_parser.py` — `ErrorLogParser` Protocol, `ParsedErrorLog` dataclass, and `FakeLogParser` rule-based/scripted test double.
- `prompts/debug/` — Markdown prompt templates (`system_prompt.md`, `user_prompt_template.md`, `revision_prompt.md`) with automated metadata header stripping.
- `state/reducers.py` — Added `unresolved_errors(errors)` pure helper to keep the last record per `error_id` and return unresolved ones.
- **Architectural & Design Notes**:
  - **Req 12 crit 5 deviation**: Because `EngineeringState.errors` uses `append_reducer`, resolving an error in-place would conflict with append-only semantics. DebugAgent appends a resolved copy with the same `error_id` and `resolved=True`, while `unresolved_errors(errors)` filters out resolved errors.
  - **Global debug cap off-by-one**: Documented that `max_debug_cycles=N means N-1 verified fixes` because `router_after_debug` evaluates P2 (`total_debug_cycles >= max_debug_cycles`) immediately upon cycle increment before downstream re-testing.
- `tests/agents/test_debug_agent.py` & `tests/workflows/test_graph_debug.py` — 13 tests covering constructor validation, QA failure resolution, Reviewer requires_changes handling, sandbox verification recovery, exhaustion without cycle increment, falsy `current_debug_target` handling, fast-fails on empty errors, invoke lifecycle, and real-graph execution (QA failure loop, Reviewer requires_changes loop, global cap termination, exhaustion approve/reject, and error deduplication).

**Status: Complete and tested** — Full test suite: **483 tests passed** (0 failures, 0 warnings).

---

## Next Phase

**ReviewerAgent** (`agents/reviewer_agent.py`) — The eighth concrete pipeline agent, performing holistic code review across code artifacts, test artifacts, and architecture specifications, per `design.md` Section 1.8 and Section 6.1.

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