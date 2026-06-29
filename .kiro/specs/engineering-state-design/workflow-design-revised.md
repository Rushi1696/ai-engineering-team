# Section 3: LangGraph Workflow Design (Revised)

**Revision status:** Addresses ISSUE-08, ISSUE-14, ISSUE-15, ISSUE-16, ISSUE-17 from architecture-review.md.

---

## 3.0 New State Fields Required by This Revision

The following fields must be added to `EngineeringState` to support the corrected workflow design.
They do not appear in the original requirements or design and are introduced here as part of the architectural correction.

| Field | Type | Default | Reducer | Purpose |
|-------|------|---------|---------|---------|
| `total_debug_cycles` | `int` | `0` | `replace_reducer` | Global DebugAgent invocation counter. Never reset. Fixes ISSUE-14. |
| `pipeline_started_at` | `str` | set at init (ISO 8601) | `replace_reducer` (write-once) | Pipeline wall-clock start time. Fixes ISSUE-15. |
| `pipeline_deadline_at` | `Optional[str]` | `None` | `replace_reducer` | Absolute ISO 8601 deadline. If `None`, no deadline is enforced. Fixes ISSUE-15. |
| `current_debug_target` | `Optional[str]` | `None` | `replace_reducer` | `error_id` UUID of the specific `ErrorRecord` DebugAgent must address in the current cycle. Fixes ISSUE-07 (referenced in ISSUE-17). |
| `rejection_scope` | `Optional[str]` | `None` | `replace_reducer` | Set by ReviewerAgent on `rejected` status. Values: `code_level`, `design_level`, `requirements_level`. Drives upstream re-routing. Fixes ISSUE-09 (referenced in ISSUE-17). |
| `forward_stage` | `int` | `0` | `replace_reducer` | First-pass forward progress counter, capped at 11. Replaces the ambiguous `pipeline_stage`. Fixes ISSUE-13. |
| `total_invocations` | `int` | `0` | `replace_reducer` | Total agent invocation count including all re-entries. Replaces the second role of `pipeline_stage`. Fixes ISSUE-13. |

> `pipeline_stage` is deprecated in favour of `forward_stage` + `total_invocations`. It remains in state for backward compatibility but is no longer updated by agents.

---

## 3.1 Graph Topology (Revised)

The pipeline is a directed acyclic graph of 13 nodes: 11 agent nodes, 1 human approval node, and 1 timeout/deadline node. The primary sequential path is linear. Conditional edges implement debug routing, rejection-scope re-routing, human approval holds, and deadline enforcement.

**Fixes applied:**
- Added `human_approval` as a formal node (ISSUE-16).
- Added `deadline_enforcer` as a formal node (ISSUE-15).
- Added edges from `debug` → `architect` and `debug` → `product_manager` for upstream re-routing (ISSUE-17).
- `final_project` node no longer uses a field-level write guard on `final_output`; access is enforced by graph topology (ISSUE-08).

```
START
  │  (pipeline_started_at set, pipeline_deadline_at set from config)
  ▼
[ProductManagerAgent]
  │  workflow_status → "requirements_complete"
  ▼
[ArchitectAgent]
  │  workflow_status → "architecture_complete"
  ▼
[TaskPlannerAgent]
  │  workflow_status → "planning_complete"
  ▼
[BackendAgent]
  │  workflow_status → "backend_complete"
  │                  | "debug_required"
  │                  | "deadline_exceeded"
  ▼
[FrontendAgent]
  │  workflow_status → "frontend_complete"
  │                  | "debug_required"
  │                  | "deadline_exceeded"
  ▼
[QAAgent]
  │  workflow_status → "qa_complete"
  │                  | "debug_required"
  │                  | "deadline_exceeded"
  ▼
[ReviewerAgent]
  │  workflow_status → "review_complete"
  │                  | "debug_required"      (requires_changes)
  │                  | "escalation_required" (rejected)
  │                  | "deadline_exceeded"
  ▼
[DevOpsAgent]
  │  workflow_status → "devops_complete"
  │                  | "debug_required"
  │                  | "deadline_exceeded"
  ▼
[DocumentationAgent]
  │  workflow_status → "documentation_complete"
  ▼
[FinalProjectAgent]         ← only reachable when status == "documentation_complete"
  │  workflow_status → "completed"
  │  final_output written here (no field guard needed — topology enforces access)
  ▼
END

Conditional branches:
  Any node → [DeadlineEnforcerNode]  when workflow_status == "deadline_exceeded"
  Any node → [HumanApprovalNode]     when human_approval_required == True
             or workflow_status == "escalation_required"
             or total_debug_cycles >= max_debug_cycles
  [DebugAgent] → one of:
             backend | frontend | qa | reviewer | devops (code-level)
             architect                                    (design_level rejection)
             product_manager                             (requirements_level rejection)
             [HumanApprovalNode]                         when human_approval_required == True
```

---

## 3.2 Nodes (Revised)

Thirteen nodes in total. Two new infrastructure nodes added. The `human_approval_interrupt` pseudo-destination from the original design is replaced by the formally specified `human_approval` node.

| Node Name | Class / Type | Pipeline Role | Order |
|-----------|-------------|--------------|-------|
| `product_manager` | ProductManagerAgent | Requirements generation | 1 |
| `architect` | ArchitectAgent | System architecture design | 2 |
| `task_planner` | TaskPlannerAgent | Task decomposition and ordering | 3 |
| `backend` | BackendAgent | Server-side code generation | 4 |
| `frontend` | FrontendAgent | Client-side code generation | 5 |
| `qa` | QAAgent | Test generation and execution | 6 |
| `debug` | DebugAgent | Root-cause analysis and fix generation | 7 (conditional) |
| `reviewer` | ReviewerAgent | Code quality and compliance review | 8 |
| `devops` | DevOpsAgent | Infrastructure and deployment artifact generation | 9 |
| `documentation` | DocumentationAgent | Documentation synthesis | 10 |
| `final_project` | FinalProjectAgent | Output consolidation and summary | 11 |
| `human_approval` | HumanApprovalNode | Human-in-the-loop interrupt and resume | Infrastructure |
| `deadline_enforcer` | DeadlineEnforcerNode | Pipeline timeout enforcement | Infrastructure |

### Node: `human_approval` (New — fixes ISSUE-16)

**Purpose:** Provide a formally specified, LangGraph-native human-in-the-loop pause point. Replaces all references to the undefined `human_approval_interrupt` pseudo-destination in the original design.

**Mechanism:** Uses LangGraph's native `interrupt()` function. When the node is reached, execution pauses and the graph state is persisted to the checkpoint store. The node surfaces `human_approval_notes` and a snapshot of relevant state fields to the configured human interface (LangGraph Studio, CLI, or webhook). Execution does not resume until the orchestrator receives an explicit resume command carrying an `approval_decision` value of `"approved"` or `"rejected"`.

**State Fields Read:**
- `human_approval_required` — confirmed `True` on entry
- `human_approval_notes` — surfaced to human reviewer
- `errors` — recent unresolved error records displayed as context
- `review_report` — if populated, displayed as context for rejection decisions
- `total_debug_cycles` — displayed to show escalation history
- `retry_counts` — displayed per-agent retry history

**State Fields Written (on approved):**
| Field | Value Written | Reducer |
|-------|--------------|---------|
| `human_approval_required` | `False` | replace_reducer |
| `human_approval_notes` | Resolution notes from human | replace_reducer |
| `retry_counts[agent]` | `0` (reset for the approved agent) | dict_merge_reducer |
| `workflow_status` | Restored to the pre-escalation stage's in-progress value | replace_reducer |
| `updated_at` | Current ISO 8601 timestamp | replace_reducer |

**State Fields Written (on rejected):**
| Field | Value Written | Reducer |
|-------|--------------|---------|
| `workflow_status` | `"failed"` | replace_reducer |
| `human_approval_required` | `False` | replace_reducer |
| `updated_at` | Current ISO 8601 timestamp | replace_reducer |

**Outgoing Edges:**
- On `"approved"` → resumes at the node that triggered escalation (determined by `next_agent`)
- On `"rejected"` → `END`

**Human Interface Options (in priority order):**
1. LangGraph Studio interrupt UI (default for local/staging)
2. Configured webhook endpoint (production — receives `run_id`, `human_approval_notes`, state snapshot)
3. CLI prompt (fallback for headless environments)

---

### Node: `deadline_enforcer` (New — fixes ISSUE-15)

**Purpose:** Handle pipeline timeout by writing a terminal failure state and routing to `END`. Avoids hung pipelines from unresponsive LLM APIs or infinite sandbox execution.

**Trigger condition:** `workflow_status == "deadline_exceeded"`. This value is written by any agent node whose per-node LangGraph timeout fires before the agent returns.

**State Fields Read:**
- `pipeline_started_at` — for elapsed time computation
- `pipeline_deadline_at` — for absolute deadline check
- `current_agent` — identifies which agent timed out

**State Fields Written:**
| Field | Value Written | Reducer |
|-------|--------------|---------|
| `workflow_status` | `"failed"` | replace_reducer |
| `errors` | New `ErrorRecord` with `error_type = "timeout"`, `agent = current_agent` | append_reducer |
| `human_approval_notes` | Timeout summary (agent name, elapsed seconds, deadline) | replace_reducer |
| `updated_at` | Current ISO 8601 timestamp | replace_reducer |

**Outgoing Edges:** `deadline_enforcer` → `END` (unconditional)

---

## 3.3 Normal Sequential Edges (Revised)

The happy-path sequential edges are unchanged from the original design. All edges are conditional on `workflow_status` reaching the expected completion value and the deadline not being exceeded.

```
product_manager  →  architect        when workflow_status == "requirements_complete"
architect        →  task_planner     when workflow_status == "architecture_complete"
task_planner     →  backend          when workflow_status == "planning_complete"
backend          →  frontend         when workflow_status == "backend_complete"
frontend         →  qa               when workflow_status == "frontend_complete"
qa               →  reviewer         when workflow_status == "qa_complete"
reviewer         →  devops           when workflow_status == "review_complete"
devops           →  documentation    when workflow_status == "devops_complete"
documentation    →  final_project    when workflow_status == "documentation_complete"
final_project    →  END              when workflow_status == "completed"
```

The edge `documentation → final_project` is the **sole** path into the `final_project` node. Because `final_project` is only reachable when `workflow_status == "documentation_complete"`, the field-level write guard on `final_output` (original Requirement 20, criterion 5) is **replaced** by this structural topology constraint. FinalProjectAgent does not need to check `workflow_status` before writing `final_output` — the graph guarantees it is invoked only at the correct time. This resolves **ISSUE-08**.

---

## 3.4 Conditional Edges and Routing Logic (Revised)

Every router evaluates conditions in this fixed precedence order:

1. **Deadline check first** — if `workflow_status == "deadline_exceeded"`, always route to `deadline_enforcer`.
2. **Global debug cycle cap** — if `total_debug_cycles >= max_debug_cycles`, set `workflow_status = "failed"` and route to `END`. This is evaluated by the router, not by the agent. Resolves **ISSUE-14**.
3. **Human approval hold** — if `human_approval_required == True`, route to `human_approval`.
4. **Normal routing** — evaluate `workflow_status` and `next_agent` for standard flow.

This precedence order ensures deadline and global cap are hard stops that cannot be bypassed by agent-level logic.

---

### Router: After ProductManagerAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer   [DEADLINE — P1]
total_debug_cycles >= max_debug_cycles              →  END (failed)        [GLOBAL CAP — P2]
human_approval_required == True                     →  human_approval      [APPROVAL — P3]
workflow_status == "requirements_complete"          →  architect           [NORMAL — P4]
workflow_status == "failed"                         →  END
```

### Router: After ArchitectAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "architecture_complete"          →  task_planner
workflow_status == "failed"                         →  END
```

### Router: After TaskPlannerAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "planning_complete"              →  backend
workflow_status == "failed"                         →  END
```

### Router: After BackendAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "backend_complete"               →  frontend
workflow_status == "debug_required"                 →  debug
workflow_status == "failed"                         →  END
```

### Router: After FrontendAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "frontend_complete"              →  qa
workflow_status == "debug_required"                 →  debug
workflow_status == "failed"                         →  END
```

### Router: After QAAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "qa_complete"                    →  reviewer
workflow_status == "debug_required"                 →  debug
workflow_status == "failed"                         →  END
```

### Router: After ReviewerAgent (Revised — fixes ISSUE-09, ISSUE-17)

The original design incorrectly routed all `rejected` outcomes to `human_approval` without providing a re-entry path. The revised design uses `rejection_scope` to determine the correct upstream re-entry point after human approval, rather than routing to `DebugAgent` for architectural rejections.

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)

workflow_status == "review_complete"
  AND review_report.overall_status == "approved"   →  devops

workflow_status == "debug_required"
  AND review_report.overall_status == "requires_changes"
                                                   →  debug        (code-level fix)

workflow_status == "escalation_required"
  AND review_report.overall_status == "rejected"   →  human_approval
    (human_approval_required is set True by ReviewerAgent,
     rejection_scope is written to state for post-approval routing)

workflow_status == "failed"                         →  END
```

**Post-approval routing from `human_approval` when triggered by `rejected`:**
```
rejection_scope == "code_level"          →  debug       (then backend/frontend)
rejection_scope == "design_level"        →  architect
rejection_scope == "requirements_level"  →  product_manager
human rejects                            →  END (failed)
```

### Router: After DevOpsAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "devops_complete"                →  documentation
workflow_status == "debug_required"                 →  debug
workflow_status == "failed"                         →  END
```

### Router: After DocumentationAgent

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer
total_debug_cycles >= max_debug_cycles              →  END (failed)
human_approval_required == True                     →  human_approval
workflow_status == "documentation_complete"         →  final_project
workflow_status == "failed"                         →  END
```

### Router: After DebugAgent (Revised — fixes ISSUE-17)

The original design's router only listed edges to `backend`, `frontend`, `qa`, `reviewer`, and `devops`. This was incomplete: DebugAgent can be invoked after a `rejected` review with `rejection_scope == "design_level"` or `"requirements_level"`, which requires routing to `architect` or `product_manager`. All reachable targets are now explicitly enumerated.

**Permitted routing targets from `debug` node:**

| `next_agent` Value | Target Node | When Used |
|--------------------|------------|-----------|
| `"BackendAgent"` | `backend` | Code-level fix for backend task failure |
| `"FrontendAgent"` | `frontend` | Code-level fix for frontend task failure |
| `"QAAgent"` | `qa` | Test fix that requires QA re-execution only |
| `"ReviewerAgent"` | `reviewer` | Fix applied, need re-review without reimplementing |
| `"DevOpsAgent"` | `devops` | Deployment artifact fix |
| `"ArchitectAgent"` | `architect` | Design-level rejection requiring architecture revision |
| `"ProductManagerAgent"` | `product_manager` | Requirements-level rejection requiring requirements revision |

**DebugAgent router table:**

```
workflow_status == "deadline_exceeded"              →  deadline_enforcer   [P1]
total_debug_cycles >= max_debug_cycles              →  END (failed)        [P2]
human_approval_required == True                     →  human_approval      [P3]

next_agent == "BackendAgent"                        →  backend             [P4]
next_agent == "FrontendAgent"                       →  frontend
next_agent == "QAAgent"                             →  qa
next_agent == "ReviewerAgent"                       →  reviewer
next_agent == "DevOpsAgent"                         →  devops
next_agent == "ArchitectAgent"                      →  architect
next_agent == "ProductManagerAgent"                 →  product_manager

workflow_status == "failed"                         →  END
```

**Constraint:** DebugAgent must only set `next_agent` to one of the seven enumerated values above. Any other value is a validation error that sets `workflow_status = "failed"`.

---

## 3.5 QA → Debug → Developer Loop (Revised)

The loop structure is largely unchanged. Three corrections are applied: the global debug cycle cap is checked on every iteration, `current_debug_target` scopes DebugAgent to the specific failing error, and per-agent `retry_counts` exhaustion no longer represents the only termination path.

```
[QAAgent]
    │ test failure detected
    │ workflow_status        = "debug_required"
    │ current_debug_target   = error_id of the specific failing error
    │ next_agent             = "DebugAgent"
    │ total_invocations      incremented
    ▼
[Router: after QAAgent]
    │ CHECK: total_debug_cycles >= max_debug_cycles?
    │   YES → workflow_status = "failed" → END  ← hard stop (ISSUE-14 fix)
    │   NO  → route to [DebugAgent]
    ▼
[DebugAgent]
    │ reads current_debug_target → fetches that specific ErrorRecord
    │ reads failing code_artifacts for that error
    │ performs root-cause analysis
    │ total_debug_cycles     incremented by 1  ← never reset
    │ produces corrected code_artifacts (version++)
    │ resolves ErrorRecord (resolved=True, resolution_notes populated)
    │ sets next_agent        = "BackendAgent" or "FrontendAgent"
    │ (deadline check applies here too via per-node LangGraph timeout)
    ▼
[BackendAgent or FrontendAgent]
    │ reads DebugAgent's resolution_notes from the resolved ErrorRecord
    │ re-implements the failing task
    │ produces new code_artifacts (version++)
    │ total_invocations      incremented
    ▼
[QAAgent]  ← loop back
    │ re-runs tests against corrected code
    │ if all pass:  workflow_status = "qa_complete" → exit loop → [reviewer]
    │ if still failing:
    │   CHECK retry_counts[agent] >= max_retries[agent]?
    │     YES → human_approval_required = True → [human_approval]
    │     NO  → loop continues (new current_debug_target set)
```

**Termination conditions (in priority order):**

| Condition | Action |
|-----------|--------|
| `total_debug_cycles >= max_debug_cycles` | `workflow_status = "failed"` → `END`. Hard stop, no bypass. |
| `pipeline_deadline_at` exceeded | `workflow_status = "deadline_exceeded"` → `deadline_enforcer` → `END` |
| All tests pass | `workflow_status = "qa_complete"` → `reviewer` |
| `retry_counts[agent] >= max_retries[agent]` | `human_approval_required = True` → `human_approval`. Human may approve (resets `retry_counts`, loop continues) or reject (→ `END`). Note: `total_debug_cycles` is NOT reset on human approval. |

---

## 3.6 Reviewer → Debug → Developer Loop (Revised)

The reviewer loop now handles three distinct outcomes from `rejected` — each with a different upstream re-entry point determined by `rejection_scope`. DebugAgent is only used for `code_level` rejections.

### 3.6.1 Path A: `requires_changes` (Code-Level Fix)

Unchanged in structure. DebugAgent handles the fix, routes back to the implementing agent.

```
[ReviewerAgent]
    │ overall_status = "requires_changes"
    │ workflow_status        = "debug_required"
    │ rejection_scope        = "code_level"
    │ current_debug_target   = most critical error_id from review_report
    │ next_agent             = "DebugAgent"
    ▼
[Router: after ReviewerAgent]
    │ CHECK: total_debug_cycles >= max_debug_cycles? → END if yes
    │ route to [DebugAgent]
    ▼
[DebugAgent]
    │ total_debug_cycles     incremented
    │ reads review_report.recommendations as fix context
    │ produces corrected code_artifacts (version++)
    │ next_agent             = "BackendAgent" or "FrontendAgent"
    ▼
[BackendAgent or FrontendAgent]
    │ applies corrections
    ▼
[QAAgent]
    │ re-validates corrected code
    ▼
[ReviewerAgent]  ← loop back
    │ if approved:          workflow_status = "review_complete" → [devops]
    │ if requires_changes:  loop continues (total_debug_cycles check on each pass)
    │ if rejected:          see Path B or C below
```

### 3.6.2 Path B: `rejected` with `rejection_scope = "design_level"`

```
[ReviewerAgent]
    │ overall_status         = "rejected"
    │ workflow_status        = "escalation_required"
    │ rejection_scope        = "design_level"
    │ human_approval_required= True
    │ human_approval_notes   = rejection reasons + quality score
    ▼
[HumanApprovalNode]
    │ surfaces rejection_scope, rejection reasons, review_report to human
    │ if human APPROVES:
    │   next_agent           = "ArchitectAgent"
    │   workflow_status      = "architecture_complete" (reset to re-enter architect)
    │   retry_counts reset for ArchitectAgent
    │   → [architect] → [task_planner] → [backend] → ... → [reviewer]
    │ if human REJECTS:
    │   workflow_status      = "failed" → END
```

### 3.6.3 Path C: `rejected` with `rejection_scope = "requirements_level"`

```
[ReviewerAgent]
    │ overall_status         = "rejected"
    │ workflow_status        = "escalation_required"
    │ rejection_scope        = "requirements_level"
    │ human_approval_required= True
    ▼
[HumanApprovalNode]
    │ if human APPROVES:
    │   next_agent           = "ProductManagerAgent"
    │   workflow_status      = "initiated" (full pipeline restart)
    │   → [product_manager] → ... → [reviewer]
    │ if human REJECTS:
    │   workflow_status      = "failed" → END
```

**Reviewer loop termination conditions:**

| Condition | Action |
|-----------|--------|
| `total_debug_cycles >= max_debug_cycles` | `workflow_status = "failed"` → `END` |
| `pipeline_deadline_at` exceeded | → `deadline_enforcer` → `END` |
| `review_report.overall_status == "approved"` | → `devops` |
| `retry_counts["ReviewerAgent"] >= max_retries["ReviewerAgent"]` | `human_approval_required = True` → `human_approval` |
| `rejected` + human rejects | `workflow_status = "failed"` → `END` |

---

## 3.7 Global Debug Cycle Cap (New — fixes ISSUE-14)

### 3.7.1 Design

The global debug cycle cap is the **only** termination guarantee that cannot be bypassed by human approval. It exists to prevent infinite cycling in cases where a human repeatedly approves continuation of a pipeline that is fundamentally unable to produce correct output.

**State fields involved:**

| Field | Type | Default | Behavior |
|-------|------|---------|----------|
| `total_debug_cycles` | `int` | `0` | Incremented by `1` on every DebugAgent invocation. **Never reset** — not by human approval, not by checkpoint recovery. |
| `max_debug_cycles` | `int` | `10` | Sourced from `configs/agents.yaml`. Configurable per project. Evaluated by routers, not by agents. |

### 3.7.2 Evaluation Point

The cap is checked by **every router** as priority-2 (after deadline, before human approval). It is not checked inside DebugAgent itself — an agent must never be responsible for its own termination condition. The router is the correct enforcement point because it executes between every node and has read access to the full state.

```
Router evaluation order (all nodes):
  P1: workflow_status == "deadline_exceeded"       → deadline_enforcer
  P2: total_debug_cycles >= max_debug_cycles       → workflow_status = "failed" → END
  P3: human_approval_required == True              → human_approval
  P4: normal workflow_status / next_agent routing
```

### 3.7.3 Behavior on Cap Reached

When `total_debug_cycles >= max_debug_cycles`:
- The router (not any agent) sets `workflow_status = "failed"`.
- A new `ErrorRecord` is appended to `errors` with `error_type = "retry_exhausted"`, `message = "Global debug cycle cap reached"`, `agent = "Orchestrator"`.
- `human_approval_notes` is populated with the total cycle count, the last failing agent, and the last unresolved `ErrorRecord.message`.
- The graph routes to `END`.
- `final_output` is **not** written.
- The checkpoint at the last successful stage remains intact for offline analysis.

### 3.7.4 Configuration

`max_debug_cycles` is defined in `configs/agents.yaml` at the workflow level (not per-agent). Recommended values:

| Environment | Recommended `max_debug_cycles` |
|-------------|-------------------------------|
| Development | `5` — fail fast, expose issues quickly |
| Staging | `10` — default, matches production |
| Production | `10` — balances resilience with cost control |
| Testing | `2` — force deterministic failure in integration tests |

---

## 3.8 Timeout Strategy (New — fixes ISSUE-15)

### 3.8.1 Design

Two timeout mechanisms work together: a per-node execution timeout (LangGraph-native) and a pipeline-level deadline (state-field-enforced).

### 3.8.2 Per-Node Timeout

Each agent node is registered in the LangGraph graph with a `timeout` parameter sourced from `configs/agents.yaml`. If the node's callable does not return within the configured timeout, LangGraph raises a `TimeoutError`. The node's exception handler catches this, writes `workflow_status = "deadline_exceeded"` and a `timeout` `ErrorRecord` to state, and returns the partial state update. The router then sees `workflow_status == "deadline_exceeded"` and routes to `deadline_enforcer`.

**Default per-node timeouts (configurable):**

| Agent Node | Default Timeout | Rationale |
|-----------|----------------|-----------|
| `product_manager` | 120s | LLM structured output, moderate context |
| `architect` | 180s | Complex multi-constraint reasoning |
| `task_planner` | 120s | Dependency graph construction |
| `backend` | 300s | Multi-file code generation, linting |
| `frontend` | 300s | Multi-file code generation, linting |
| `qa` | 300s | Test generation + test runner execution |
| `debug` | 240s | RCA + fix generation + sandbox execution |
| `reviewer` | 360s | Full codebase review, static analysis |
| `devops` | 180s | IaC generation + YAML validation |
| `documentation` | 240s | Multi-document synthesis |
| `final_project` | 60s | Enumeration and summarisation only |
| `human_approval` | 0 (no timeout) | Human-driven; no programmatic timeout |
| `deadline_enforcer` | 10s | State write only; must complete fast |

### 3.8.3 Pipeline-Level Deadline

`pipeline_deadline_at` is an optional absolute ISO 8601 timestamp set at pipeline initialization from `configs/workflow.yaml`. If set, every router evaluates `utcnow() > pipeline_deadline_at` as its P1 check. If the deadline is exceeded, `workflow_status` is set to `"deadline_exceeded"` and the router directs to `deadline_enforcer`.

The deadline is independent of per-node timeouts — a pipeline can be within its per-node timeout but still exceed its wall-clock deadline if many agents run sequentially with accumulated time.

**Recommended pipeline deadlines (configurable):**

| Project Scale | Recommended Deadline |
|--------------|---------------------|
| Small (< 20 tasks) | 30 minutes |
| Medium (20–100 tasks) | 2 hours |
| Large (100+ tasks) | 6 hours |
| Unlimited | `None` (no deadline enforced) |

### 3.8.4 `DeadlineEnforcerNode` Behavior

When `deadline_enforcer` is reached:
1. Reads `pipeline_started_at` and computes elapsed seconds.
2. Reads `current_agent` to identify which agent timed out.
3. Appends an `ErrorRecord` with `error_type = "timeout"`.
4. Sets `workflow_status = "failed"`.
5. Writes `human_approval_notes` with: agent name, elapsed time, deadline, per-node timeout config.
6. Routes unconditionally to `END`.

No human approval is offered for deadline exceeded — this is an operational limit, not a quality gate. If the operator wants to resume, they must re-run from the last checkpoint with an extended deadline.

---

## 3.9 Checkpoint Strategy (Revised)

### 3.9.1 What Changes From the Original

The checkpoint strategy is largely preserved. Three corrections are applied:

1. DebugAgent invocations now write a **debug cycle checkpoint** (separate from forward-progress checkpoints) for forensic recovery. This was not in the original design.
2. `HumanApprovalNode` writes a **pre-interrupt checkpoint** before pausing, so that the state at the moment of escalation is always recoverable.
3. `total_debug_cycles` is included in every checkpoint and is restored exactly as saved — it is never reset on recovery.

### 3.9.2 Forward-Progress Checkpoints

These are unchanged from the original design. They represent confirmed forward pipeline progress and are used for recovery resumption.

| Trigger | `checkpoint_stage` Written | `checkpoints[stage]` Timestamp |
|---------|---------------------------|-------------------------------|
| ProductManagerAgent completes successfully | `"requirements_complete"` | ISO 8601 |
| ArchitectAgent completes successfully | `"architecture_complete"` | ISO 8601 |
| TaskPlannerAgent completes successfully | `"planning_complete"` | ISO 8601 |
| BackendAgent completes successfully | `"backend_complete"` | ISO 8601 |
| FrontendAgent completes successfully | `"frontend_complete"` | ISO 8601 |
| QAAgent completes successfully | `"qa_complete"` | ISO 8601 |
| ReviewerAgent issues `approved` | `"review_complete"` | ISO 8601 |
| DevOpsAgent completes successfully | `"devops_complete"` | ISO 8601 |
| DocumentationAgent completes successfully | `"documentation_complete"` | ISO 8601 |
| FinalProjectAgent completes successfully | `"completed"` | ISO 8601 |

Forward-progress checkpoints advance `forward_stage` by 1. Debug cycles do not advance `forward_stage`.

### 3.9.3 Debug Cycle Checkpoints (New)

DebugAgent writes a labeled checkpoint entry on every invocation. These are stored in `checkpoints` under the key `"debug_{total_debug_cycles}"` (e.g., `"debug_1"`, `"debug_2"`). They do **not** update `checkpoint_stage` or `forward_stage`.

| Trigger | `checkpoints` Entry | Purpose |
|---------|-------------------|---------|
| DebugAgent begins | `checkpoints["debug_{n}_start"] = iso_timestamp` | Forensic start marker |
| DebugAgent completes | `checkpoints["debug_{n}_complete"] = iso_timestamp` | Forensic completion marker |

These entries are used for forensic analysis of debug cycle history. They are not used for recovery resumption.

### 3.9.4 Human Approval Checkpoint (New)

When `HumanApprovalNode` is reached, the LangGraph `interrupt()` call automatically persists the current state to the checkpoint store. This is LangGraph-native behavior and requires no additional agent-side writes. The checkpoint is labeled internally by LangGraph with the `run_id` and thread context.

This ensures that if the human approval wait is interrupted (e.g., process restart), the pipeline can resume from the pre-interrupt state rather than re-executing the agent that triggered escalation.

### 3.9.5 Checkpoint Recovery Protocol (Revised)

On recovery:

1. The orchestrator reads `checkpoint_stage` from the most recent forward-progress checkpoint.
2. It reads `total_debug_cycles` from the checkpoint — this value is restored exactly. It is not zeroed.
3. If `human_approval_required == True` in the checkpoint, the orchestrator resumes at `HumanApprovalNode` (not at the agent that triggered it).
4. If `workflow_status == "deadline_exceeded"` or `"failed"` in the checkpoint, the orchestrator does not resume automatically — it surfaces the error state to the operator.
5. If the checkpoint cannot be deserialized, a `RuntimeError` is raised identifying the `checkpoint_stage` and the field that failed deserialization.

### 3.9.6 Checkpoint Backend Selection

Unchanged from original design.

| Environment | Backend | Rationale |
|-------------|---------|-----------|
| Local development | `MemorySaver` | In-memory, zero setup, ephemeral |
| Testing | `MemorySaver` | Deterministic, isolated per test |
| Staging / Production | `SqliteSaver` | Persistent, single-process workloads |
| Distributed Production | `PostgresSaver` | Multi-process, high availability, concurrent runs |

---

## 3.10 HumanApprovalNode — Full Specification (New — fixes ISSUE-16)

This section provides the complete design contract for the `human_approval` node, replacing all informal references to `human_approval_interrupt` in the original design.

### 3.10.1 Node Contract

**Node name:** `human_approval`  
**Mechanism:** LangGraph native `interrupt()` — execution pauses at this node, state is checkpointed, and the graph waits for an explicit external resume command.  
**Implementation interface:** The node calls `interrupt(value)` where `value` is a structured dict surfacing `human_approval_notes`, `run_id`, `total_debug_cycles`, and the relevant error/review context. The caller receives this value via LangGraph's streaming API.

### 3.10.2 Trigger Conditions

| Condition | Triggered By | `human_approval_notes` Content |
|-----------|-------------|-------------------------------|
| `retry_counts[agent] >= max_retries[agent]` | Any agent or orchestrator | Agent name, retry count, last `ErrorRecord.message` and traceback summary |
| `review_report.overall_status == "rejected"` | ReviewerAgent | `rejection_scope`, review rejection reasons, `code_quality_score` |
| DebugAgent sets `human_approval_required = True` after max debug attempts | DebugAgent | `error_id`, RCA summary, all attempted fix descriptions |
| Unresolvable `ValidationError` on `requirements` or `architecture` | Orchestrator | Field name, validation error message, raw LLM output excerpt |

### 3.10.3 Information Surfaced to Human Reviewer

The interrupt value presented to the human interface contains:

```
run_id                   — identifies the pipeline run
project_name             — project context
human_approval_notes     — primary escalation reason (plain language)
rejection_scope          — present only when triggered by reviewer rejection
total_debug_cycles       — how many debug cycles have run
max_debug_cycles         — the global cap (for context)
retry_counts             — per-agent retry history
last_error               — the most recent ErrorRecord (message + traceback if available)
workflow_status          — current pipeline status
checkpoint_stage         — last successful forward stage
```

### 3.10.4 Resolution Actions

**Action: Approve continuation**

The human submits `approval_decision = "approved"` via the resume command. The node writes:

| Field | Value |
|-------|-------|
| `human_approval_required` | `False` |
| `human_approval_notes` | Human's resolution note (or cleared) |
| `retry_counts[agent]` | `0` — reset for the specific agent |
| `workflow_status` | Restored to the appropriate in-progress value |
| `next_agent` | Set based on `rejection_scope` (for rejected paths) or the pre-interrupt `next_agent` |
| `updated_at` | Current timestamp |

Then routes to the node identified by `next_agent`.

**Action: Reject continuation**

The human submits `approval_decision = "rejected"`. The node writes:

| Field | Value |
|-------|-------|
| `workflow_status` | `"failed"` |
| `human_approval_required` | `False` |
| `human_approval_notes` | Human's rejection note |
| `updated_at` | Current timestamp |

Then routes to `END`. `final_output` is not written. The last forward-progress checkpoint remains accessible for forensic analysis.

### 3.10.5 Human Interface Options

In priority order based on environment:

| Environment | Interface | Mechanism |
|-------------|-----------|-----------|
| Local development | LangGraph Studio | Native interrupt visualization via Studio UI |
| CI/automated testing | Mocked approval | Test fixtures inject pre-configured `approval_decision` |
| Staging | CLI prompt | `runner.py` polls LangGraph streaming API and prompts operator |
| Production | Webhook endpoint | Orchestrator posts interrupt value to configured `APPROVAL_WEBHOOK_URL`; waits for signed HTTP callback |

The webhook approach (production) must:
- Authenticate the callback with a shared secret or signed JWT
- Accept the `run_id` as part of the callback payload for correlation
- Reject callbacks for unknown or completed `run_id` values

---

## 3.11 Issue Resolution Cross-Reference

| Issue | Status | Resolution Applied In |
|-------|--------|----------------------|
| ISSUE-08 (FinalProjectAgent write guard race condition) | Resolved | §3.3 — topology enforcement replaces field-level guard |
| ISSUE-14 (unbounded debug loop) | Resolved | §3.4 (router P2 check), §3.7 (global cap specification) |
| ISSUE-15 (no timeout mechanism) | Resolved | §3.8 (per-node timeout + pipeline deadline + DeadlineEnforcerNode) |
| ISSUE-16 (human_approval not a defined node) | Resolved | §3.2 (node table), §3.10 (full specification) |
| ISSUE-17 (DebugAgent missing edges to architect/product_manager) | Resolved | §3.4 (DebugAgent router table with all 7 targets), §3.6.2, §3.6.3 |
| ISSUE-09 (rejected status has no correct recovery path) | Resolved | §3.6.2 and §3.6.3 (rejection_scope-based routing) |
| ISSUE-13 (pipeline_stage ambiguous in re-entrant loops) | Resolved | §3.0 (forward_stage + total_invocations replacement fields) |
