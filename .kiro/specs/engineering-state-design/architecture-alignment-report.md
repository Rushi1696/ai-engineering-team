# Final Architecture Alignment Report
**Scope:** Contradictions between `requirements.md`, `design.md`, and `workflow-design-revised.md`
**Date:** 2026-06-16
**Status:** Pre-implementation alignment check

---

## Executive Summary

Nine contradictions remain across the three documents. They fall into four categories:

1. **ISSUE-08 residual** — requirements.md and design.md Section 1 still carry the self-defeating field-level write guard on `final_output` that the workflow revision correctly eliminated.
2. **`workflow_status` enum gap** — two new status values used throughout the revised workflow (`deadline_exceeded`, `escalation_required`) are not present in requirements.md Requirement 11 or in design.md Section 2's State Ownership Matrix.
3. **New state fields not registered** — seven new fields introduced in `workflow-design-revised.md` §3.0 are absent from requirements.md and from the State Ownership Matrix in design.md Section 2.
4. **Stale agent contract language** — the FinalProjectAgent contract in design.md Section 1.11 still describes the old flawed guard behavior that Section 3 has since corrected.

`workflow-design-revised.md` is the **source of truth** for all workflow-related decisions. Everywhere it contradicts the other documents, the other documents must be updated to align.

---

## Contradiction 1: `final_output` Write Guard — requirements.md vs. workflow-design-revised.md

**Severity:** Critical — will cause implementation to build a self-defeating field-level guard

### Source Document
`requirements.md` — Requirement 20, criterion 5

### Conflicting Text
> IF any agent or process attempts to write to `final_output` while `workflow_status` is not `"completed"`, THE `EngineeringState` SHALL raise a `ValueError` and reject the write.

### Why It Contradicts
`workflow-design-revised.md` §3.3 explicitly resolves ISSUE-08 by replacing this field-level guard with topology enforcement: *"The `documentation → final_project` edge is the sole path into the `final_project` node… the graph guarantees it is invoked only at the correct time."* The field-level guard is self-defeating because FinalProjectAgent itself sets `workflow_status = "completed"` and writes `final_output` in the same atomic state update. At the time the guard would evaluate, the status is still `"documentation_complete"`, so the guard always rejects the write from the only agent authorized to make it.

### Recommended Source of Truth
`workflow-design-revised.md` §3.3

### Exact Change Required
**In `requirements.md`, Requirement 20, criterion 5 — replace with:**
> THE `final_output` field SHALL only be written by `FinalProjectAgent`. Access control is enforced structurally by the LangGraph graph topology: the `final_project` node is only reachable via the `documentation → final_project` edge, which fires exclusively when `workflow_status == "documentation_complete"`. No field-level `ValueError` guard is required or permitted.

---

## Contradiction 2: `final_output` Write Guard — design.md Section 1.11 vs. workflow-design-revised.md

**Severity:** Critical — creates direct contradiction between agent contract and workflow specification

### Source Document
`design.md` — Section 1.11, FinalProjectAgent contract

### Conflicting Text (two locations)

**Location A — Responsibilities bullet, line ~712:**
> Write `final_output` only when `workflow_status == "completed"`.

**Location B — Outputs table, line ~734:**
> `final_output` | replace_reducer | `FinalProjectOutput` **(guarded by status check)**

**Location C — State Ownership Matrix, line ~809:**
> `final_output` | FinalProjectAgent | End user interface, monitoring | Once; only when `workflow_status == "completed"` | replace_reducer **(guarded write)**

### Why It Contradicts
All three locations repeat the field-level status-check guard that workflow-design-revised.md §3.3 has replaced with topology enforcement. The phrase "guarded by status check" will mislead an implementer into adding the `ValueError` guard that ISSUE-08 proved is self-defeating.

### Recommended Source of Truth
`workflow-design-revised.md` §3.3

### Exact Changes Required

**Location A — replace the bullet:**
> Write `final_output` unconditionally. Graph topology guarantees this agent is only invoked when `workflow_status == "documentation_complete"`. No runtime status check is required.

**Location B — replace the Notes cell:**
> `FinalProjectOutput` — topology-guarded (node only reachable from `documentation` node)

**Location C — replace the Update Frequency cell:**
> Once; written unconditionally by FinalProjectAgent. Access enforced by graph topology, not by field-level validation.
> Reducer: `replace_reducer` (remove the `(guarded write)` annotation)

---

## Contradiction 3: `workflow_status` Valid Values Enum Is Incomplete

**Severity:** High — two new status values used in all routing tables and loop diagrams have no formal definition

### Source Document
`requirements.md` — Requirement 11, criterion 1

### Conflicting Text
> THE `EngineeringState` SHALL contain a `workflow_status` field of type `str` with the following valid values: `initiated`, `requirements_complete`, `architecture_complete`, `planning_complete`, `backend_complete`, `frontend_complete`, `qa_complete`, `review_complete`, `debug_required`, `devops_complete`, `documentation_complete`, `completed`, `failed`.

### Why It Contradicts
`workflow-design-revised.md` introduces two new status values used in every router table and loop specification:
- `"deadline_exceeded"` — written when any agent's per-node timeout fires (§3.8.2, §3.4 P1 check in every router)
- `"escalation_required"` — written by ReviewerAgent when `overall_status == "rejected"` (§3.4 ReviewerAgent router, §3.6.2, §3.6.3)

Neither value appears in the valid values list. Any implementation that validates `workflow_status` against the Requirement 11 enum will reject both writes as invalid, silently breaking timeout handling and review rejection routing.

Also: `requirements.md` Requirement 12, criterion 2 specifies that `ErrorRecord.stage` must be *"one of the valid `workflow_status` values"*. Since `deadline_exceeded` and `escalation_required` are not in that list, an `ErrorRecord` created during a timeout event will fail its own validation.

Also: `requirements.md` Requirement 17, criterion 1 specifies that `checkpoint_stage` valid values are *"the same set as `workflow_status`."* The debug cycle checkpoint keys (`"debug_{n}_start"`, `"debug_{n}_complete"`) introduced in `workflow-design-revised.md` §3.9.3 are also not in the enum.

### Recommended Source of Truth
`workflow-design-revised.md` §3.1, §3.4, §3.8

### Exact Changes Required

**In `requirements.md`, Requirement 11, criterion 1 — replace the valid values list with:**
> `initiated`, `requirements_complete`, `architecture_complete`, `planning_complete`, `backend_complete`, `frontend_complete`, `qa_complete`, `review_complete`, `debug_required`, `escalation_required`, `devops_complete`, `documentation_complete`, `deadline_exceeded`, `completed`, `failed`

**In `requirements.md`, Requirement 17, criterion 1 — update the cross-reference note:**
> Valid values SHALL be the same set as `workflow_status` (Requirement 11, criterion 1) plus forensic debug keys of the form `"debug_{n}_start"` and `"debug_{n}_complete"` for `checkpoints` entries written by DebugAgent invocations.

---

## Contradiction 4: Seven New State Fields Are Not in requirements.md

**Severity:** High — fields used throughout the workflow specification have no requirements backing

### Source Document
`requirements.md` — no entry for any of these fields

### Conflicting Text (absence)
`workflow-design-revised.md` §3.0 introduces seven new `EngineeringState` fields:

| New Field | Used In |
|-----------|---------|
| `total_debug_cycles` | Every router (P2), §3.5, §3.6, §3.7 |
| `pipeline_started_at` | §3.8.3, DeadlineEnforcerNode |
| `pipeline_deadline_at` | §3.8.3, every router (P1) |
| `current_debug_target` | §3.5, §3.6.1, DebugAgent scoping |
| `rejection_scope` | §3.4 ReviewerAgent router, §3.6.2, §3.6.3 |
| `forward_stage` | Deprecates `pipeline_stage` |
| `total_invocations` | §3.5, monitoring |

### Why It Contradicts
requirements.md makes no mention of these fields. An implementer working from requirements.md has no specification for their types, defaults, reducers, immutability rules, or validation constraints. The fields are effectively unspecified.

### Recommended Source of Truth
`workflow-design-revised.md` §3.0 for all seven fields

### Exact Changes Required
**Add a new Requirement 21 to `requirements.md`:**

> **Requirement 21: Workflow Control Fields (Introduced by Workflow Revision)**
>
> **User Story:** As the LangGraph orchestrator, I want structured fields for global cycle limiting, timeout enforcement, and scoped debug targeting, so that the pipeline has guaranteed termination and deterministic failure modes.
>
> **Acceptance Criteria:**
> 1. THE `EngineeringState` SHALL contain a `total_debug_cycles` field of type `int`, initialized to `0`, incremented by `1` on every DebugAgent invocation, and never reset by any agent or human approval action.
> 2. THE `EngineeringState` SHALL contain a `pipeline_started_at` field of type `str` (ISO 8601 datetime), set once at pipeline initialization and treated as write-once immutable.
> 3. THE `EngineeringState` SHALL contain a `pipeline_deadline_at` field of type `Optional[str]` (ISO 8601 datetime), initialized to `None`. When set, the orchestrator SHALL route to the deadline enforcer node when `utcnow() > pipeline_deadline_at`.
> 4. THE `EngineeringState` SHALL contain a `current_debug_target` field of type `Optional[str]` (UUID v4 referencing an `error_id`), initialized to `None`. WHEN a failure routes to DebugAgent, THE triggering agent SHALL write the `error_id` of the specific failing error to this field.
> 5. THE `EngineeringState` SHALL contain a `rejection_scope` field of type `Optional[str]` with valid values `code_level`, `design_level`, `requirements_level`, initialized to `None`. WHEN ReviewerAgent writes `overall_status = "rejected"`, it SHALL set `rejection_scope` to indicate the scope of the rejection.
> 6. THE `EngineeringState` SHALL contain a `forward_stage` field of type `int`, initialized to `0`, incremented by `1` only on first-pass forward pipeline progress (not on debug re-entries), capped at the number of agent nodes.
> 7. THE `EngineeringState` SHALL contain a `total_invocations` field of type `int`, initialized to `0`, incremented by `1` on every agent node invocation including re-entries.
> 8. THE `pipeline_stage` field defined in Requirement 11, criterion 4 is deprecated. Implementations SHALL use `forward_stage` and `total_invocations` instead. `pipeline_stage` remains in state for backward compatibility but SHALL NOT be incremented by agents.

---

## Contradiction 5: Seven New State Fields Are Not in design.md State Ownership Matrix

**Severity:** High — Section 2 matrix is the canonical field-level reference and is now stale

### Source Document
`design.md` — Section 2, State Ownership Matrix

### Conflicting Text (absence)
The State Ownership Matrix has 33 rows (lines ~770–810). None of the seven fields from `workflow-design-revised.md` §3.0 appear in it. A developer using Section 2 as the canonical field reference will not know the producer, consumers, update frequency, or reducer for any of these fields.

### Recommended Source of Truth
`workflow-design-revised.md` §3.0

### Exact Changes Required
**Add 7 rows to the State Ownership Matrix in `design.md` Section 2:**

| State Field | Producer | Consumers | Update Frequency | Reducer Type |
|---|---|---|---|---|
| `total_debug_cycles` | DebugAgent (increments); never reset | All routers (P2 cap check), monitoring | Every DebugAgent invocation | replace_reducer |
| `pipeline_started_at` | System (init) | DeadlineEnforcerNode, all routers (P1 deadline check) | Once at init; immutable | replace_reducer (write-once guard) |
| `pipeline_deadline_at` | System (init from config) | All routers (P1 deadline check), DeadlineEnforcerNode | Set at init; may be updated by operator | replace_reducer |
| `current_debug_target` | Any agent triggering `debug_required`; cleared by DebugAgent on resolution | DebugAgent | On each debug cycle entry | replace_reducer |
| `rejection_scope` | ReviewerAgent (on `rejected`) | HumanApprovalNode, DebugAgent router | On each `rejected` review outcome | replace_reducer |
| `forward_stage` | All agent nodes (on first-pass forward progress only) | Monitoring, FinalProjectAgent | Once per forward stage (capped at 11) | replace_reducer |
| `total_invocations` | Every agent node invocation | Monitoring, forensics | Every agent invocation including re-entries | replace_reducer |

---

## Contradiction 6: `pipeline_stage` Is Deprecated in workflow-design-revised.md But Still Active in requirements.md

**Severity:** Medium — creates ambiguity about which field drives forward progress tracking

### Source Document
`requirements.md` — Requirement 11, criterion 4; Requirement 18, criterion 2

### Conflicting Text

**Requirement 11, criterion 4:**
> THE `EngineeringState` SHALL contain a `pipeline_stage` field of type `int` (0-indexed) initialized to `0` and incremented by `1` each time `workflow_status` transitions to a new non-failure, non-debug stage.

**Requirement 11, criterion 5:**
> … increment `pipeline_stage` by `1`.

**Requirement 18, criterion 2:**
> THE `EngineeringState` SHALL define a `replace_reducer` for scalar fields … (e.g., `workflow_status`, `current_agent`, `next_agent`, **`pipeline_stage`**, `state_version`).

### Why It Contradicts
`workflow-design-revised.md` §3.0 states: *"`pipeline_stage` is deprecated in favour of `forward_stage` + `total_invocations`. It remains in state for backward compatibility but is no longer updated by agents."* requirements.md still mandates that agents increment `pipeline_stage`, and Requirement 18 still lists it as an actively-used scalar field in the reducer specification.

### Recommended Source of Truth
`workflow-design-revised.md` §3.0

### Exact Changes Required

**In `requirements.md`, Requirement 11, criterion 4 — replace with:**
> THE `EngineeringState` SHALL contain a `pipeline_stage` field of type `int`. This field is **deprecated** and SHALL NOT be incremented by agents. It is retained in state for backward compatibility only. Use `forward_stage` (Requirement 21, criterion 6) for first-pass progress tracking and `total_invocations` (Requirement 21, criterion 7) for total invocation counting.

**In `requirements.md`, Requirement 11, criterion 5 — remove the `pipeline_stage` increment clause:**
> WHEN an agent completes successfully, THE `EngineeringState` SHALL update `workflow_status` to the corresponding completion value, set `current_agent` to the completing agent's name, and set `next_agent` to the name of the next agent in the pipeline.

**In `requirements.md`, Requirement 18, criterion 2 — remove `pipeline_stage` from the example list:**
> THE `EngineeringState` SHALL define a `replace_reducer` for scalar fields that replaces the previous value entirely on update (e.g., `workflow_status`, `current_agent`, `next_agent`, `forward_stage`, `total_invocations`, `state_version`).

---

## Contradiction 7: `ErrorRecord.stage` Valid Values Are Inconsistent with New `workflow_status` Values

**Severity:** Medium — error records created during timeout or escalation events fail their own field validation

### Source Document
`requirements.md` — Requirement 12, criterion 2

### Conflicting Text
> `stage` (str, one of the valid `workflow_status` values)

### Why It Contradicts
`workflow-design-revised.md` §3.2 specifies that `DeadlineEnforcerNode` appends an `ErrorRecord` with `error_type = "timeout"`. The `stage` field of that record would be `"deadline_exceeded"` — a value that is not in the current `workflow_status` valid values list (Contradiction 3). Similarly, an `ErrorRecord` appended during `escalation_required` would have `stage = "escalation_required"`, which is also not in the list.

This contradiction is downstream of Contradiction 3 but has an independent material impact on `ErrorRecord` validation at the model level.

### Recommended Source of Truth
The updated `workflow_status` enum from Contradiction 3's fix

### Exact Change Required
This contradiction resolves automatically once Contradiction 3's change is applied — adding `deadline_exceeded` and `escalation_required` to the `workflow_status` valid values list also makes them valid `ErrorRecord.stage` values. No independent change required beyond Contradiction 3.

---

## Contradiction 8: `max_debug_cycles` Is a Config Value but Is Referenced Inline in State Operations

**Severity:** Medium — router logic references `max_debug_cycles` as if it were a state field, but it is not in state

### Source Document
`design.md` — Section 3.0 New State Fields table

### Conflicting Text
The §3.0 table defines `total_debug_cycles` as a state field but does NOT define `max_debug_cycles` as a state field. The text in §3.7.1 says: *"`max_debug_cycles: int` (configurable, default 10) to configs."*

However, every router table in Section 3.4 contains the evaluation:
> `P2: total_debug_cycles >= max_debug_cycles → END (failed)`

And the topology diagram in §3.1 shows:
> Any node → [HumanApprovalNode] when ... `total_debug_cycles >= max_debug_cycles`

### Why It Contradicts
The router is an in-process function that reads from `EngineeringState`. If `max_debug_cycles` lives only in `configs/agents.yaml` and is not in state, the router must read it from config at evaluation time. This is not inherently wrong, but:

1. The router evaluation statement `total_debug_cycles >= max_debug_cycles` implies both values are accessible to the router in the same way — one from state and one from config is an asymmetric access pattern that is not documented.
2. If `max_debug_cycles` is changed between a checkpoint save and a recovery run, the effective cap changes without any trace in state. This creates a class of bugs where a recovered pipeline behaves differently from the original run.
3. The State Ownership Matrix has no row for `max_debug_cycles`, so its producer, consumer, and lifecycle are entirely undocumented.

### Recommended Source of Truth
`workflow-design-revised.md` §3.7 (design intent) + alignment with existing `max_retries` pattern in `requirements.md` Requirement 13, criterion 2

### Exact Changes Required
**Option A (recommended — consistent with `max_retries` pattern):** Add `max_debug_cycles` to `EngineeringState` as a state field sourced from config at init, following the same pattern as `max_retries`. Add a row to the State Ownership Matrix. This makes the cap value checkpointed alongside `total_debug_cycles`.

**In `requirements.md`, Requirement 21 (new, from Contradiction 4), add criterion 9:**
> THE `EngineeringState` SHALL contain a `max_debug_cycles` field of type `int`, initialized from `configs/workflow.yaml` at pipeline startup. The default value SHALL be `10`. This field SHALL be treated as write-once after initialization.

**In `design.md` State Ownership Matrix, add row:**
> `max_debug_cycles` | System (init from config) | All routers (P2 global cap check) | Set at init; write-once | replace_reducer (write-once guard)

---

## Contradiction 9: `checkpoint_stage` Valid Values Cross-Reference Is Broken

**Severity:** Low — implementation ambiguity about valid `checkpoint_stage` values

### Source Document
`requirements.md` — Requirement 17, criterion 1

### Conflicting Text
> Valid values SHALL be the same set as `workflow_status` (Requirement 11, criterion 1).

### Why It Contradicts
`workflow-design-revised.md` §3.9.3 introduces debug cycle checkpoint keys of the form `"debug_{n}_start"` and `"debug_{n}_complete"` written into the `checkpoints` dict. However, the `checkpoint_stage` field (distinct from the `checkpoints` dict) is specifically described as being updated only on forward-progress stages. The debug keys go into `checkpoints` (the dict) but `checkpoint_stage` (the scalar) still only takes valid `workflow_status` values.

The contradiction is therefore narrower than it appears: it is the `checkpoints` dict keys that can now include non-`workflow_status` values (the forensic debug keys), not the `checkpoint_stage` scalar. The cross-reference in Requirement 17 criterion 1 only governs `checkpoint_stage`, not the `checkpoints` dict keys.

### Recommended Source of Truth
`workflow-design-revised.md` §3.9.2 and §3.9.3 combined

### Exact Changes Required
**In `requirements.md`, add a new criterion 9 to Requirement 17:**
> THE `checkpoints` dict MAY contain entries with keys that are not in the `workflow_status` valid values set. Specifically, entries of the form `"debug_{n}_start"` and `"debug_{n}_complete"` SHALL be written by DebugAgent on each invocation for forensic history. These entries SHALL NOT be used for recovery resumption and SHALL NOT update the `checkpoint_stage` scalar field.

---

## Summary of Required Changes

| # | Contradiction | Document(s) to Update | Priority |
|---|---------------|----------------------|----------|
| 1 | `final_output` field-level guard in requirements.md | requirements.md — Req 20 criterion 5 | Critical |
| 2 | `final_output` field-level guard in design.md Section 1.11 | design.md — §1.11 (3 locations) | Critical |
| 3 | `workflow_status` enum missing `deadline_exceeded`, `escalation_required` | requirements.md — Req 11 criterion 1; Req 17 criterion 1 | High |
| 4 | Seven new state fields absent from requirements.md | requirements.md — add Requirement 21 | High |
| 5 | Seven new state fields absent from design.md State Ownership Matrix | design.md — Section 2 table | High |
| 6 | `pipeline_stage` deprecated in workflow but still active in requirements | requirements.md — Req 11 crit 4-5; Req 18 crit 2 | Medium |
| 7 | `ErrorRecord.stage` valid values broken by new workflow_status values | Resolves with Contradiction 3 fix | Downstream of #3 |
| 8 | `max_debug_cycles` not in state — asymmetric router access pattern | requirements.md — add to Req 21; design.md — add to matrix | Medium |
| 9 | `checkpoints` dict keys vs `checkpoint_stage` cross-reference ambiguity | requirements.md — Req 17 add criterion 9 | Low |

---

## Source of Truth Hierarchy

When any two documents conflict on workflow-related decisions, resolve in this order:

1. `workflow-design-revised.md` — workflow topology, routing logic, loop termination, node contracts
2. `design.md` Section 3 — references workflow-design-revised.md and is its summary counterpart
3. `design.md` Section 1 (agent contracts) — must be updated to align with Section 3
4. `design.md` Section 2 (state matrix) — must include all fields used in Section 3
5. `requirements.md` — must be updated last to reflect all architectural decisions above

All five critical and high contradictions in this report represent cases where requirements.md and/or design.md Section 1/2 have not yet been updated to reflect the workflow revision. The workflow revision is correct. The other documents need to catch up.
