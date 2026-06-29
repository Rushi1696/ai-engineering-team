# Architecture Review: EngineeringState Multi-Agent System
**Review Type:** Principal Engineer / Staff+ Production Readiness Review  
**Documents Reviewed:** `requirements.md`, `design.md`  
**Date:** 2026-06-15  
**Reviewer Role:** Principal Software Architect  

---

## Executive Summary

The design is ambitious, well-structured, and conceptually sound for a research or demonstration system. However, it contains **22 distinct issues** ranging from critical architectural flaws to maintenance risks before it could be considered production-ready. The most serious problems cluster around three themes:

1. **State bloat** — storing full file content inside a shared in-memory object that must be serialized on every checkpoint is a fundamental scalability mistake.
2. **Reducer contract conflicts** — the `errors` field has mutually incompatible reducer semantics (append-only vs. in-place update) that cannot be satisfied simultaneously by a single LangGraph reducer.
3. **Unbounded debug loops** — the retry/escalation logic contains a race condition that allows infinite cycling without guaranteed termination.

Issues are ranked by severity: **Critical**, **High**, **Medium**, **Low**.

---

## 1. EngineeringState Design

---

### ISSUE-01 — State Contains Full File Content In-Memory
**Severity:** Critical  
**Area:** EngineeringState Design / Scalability

**Explanation:**  
`code_artifacts`, `test_artifacts`, `deployment_artifacts`, and `documentation_artifacts` store the full `content` string of every generated file directly inside `EngineeringState`. For a real project — even a modest one — this means the state object will accumulate tens to hundreds of kilobytes of code content per file. With 50 source files averaging 5KB each, the state object reaches 250KB before test files, deployment configs, or documentation are added. On every agent invocation, LangGraph serializes the entire state to the checkpoint store. At ReviewerAgent, the full state is passed as context to the LLM — potentially exceeding 150k tokens even on moderate projects. This breaks the 128k context window of GPT-4o (the proposed model for backend/frontend) and strains Claude 3.5 Sonnet's 200k limit.

**Recommendation:**  
Replace `content: str` inside artifact models with a `content_ref: str` pointing to a content-addressed storage path (e.g., a hash-keyed file in a local or object store). Store content externally. Keep only `file_path`, `content_ref`, `language`, `artifact_type`, `produced_by`, `produced_at`, and `version` in state. Agents that need to read content fetch it from the store via the ref. ReviewerAgent receives a manifest, not the full codebase. This is a fundamentally different approach to state design and must be resolved before any implementation begins.

---

### ISSUE-02 — `errors` Reducer Contract Is Unsatisfiable
**Severity:** Critical  
**Area:** EngineeringState Design / State Reducers

**Explanation:**  
Requirement 18 defines `append_reducer` for `errors` — new records are appended without replacing existing ones. Requirement 12, criterion 5 then requires that when DebugAgent resolves an error, the system must "locate the `ErrorRecord` matching the given `error_id`, set its `resolved` field to `True`" applied "as a replacement of the matching record in the list." These two requirements are mutually exclusive. An `append_reducer` cannot perform an in-place update of an existing list entry — it can only add new entries. LangGraph's state merge protocol applies reducers to incoming partial updates; it has no mechanism for identifying and replacing an element within an existing list based on a field value.

**Recommendation:**  
Two viable approaches:  
**(a)** Change `errors` from `list[ErrorRecord]` to `Dict[str, ErrorRecord]` keyed by `error_id`. Use `dict_merge_reducer`. DebugAgent emits a partial dict `{error_id: updated_record}` and the reducer naturally overwrites the existing entry. This is the cleanest solution.  
**(b)** Keep the list, but model resolution as appending a new `ErrorResolution` event record rather than mutating the original `ErrorRecord`. Downstream consumers determine the effective state of each error by finding the latest record with a given `error_id`. This is event-sourcing and adds query complexity.

Option (a) is strongly recommended.

---

### ISSUE-03 — `task_plan` Uses `replace_reducer` But Multiple Agents Write Task Statuses
**Severity:** High  
**Area:** EngineeringState Design / Reducer Conflicts

**Explanation:**  
`task_plan` is assigned `replace_reducer` in both requirements and the state ownership matrix. However, BackendAgent, FrontendAgent, and QAAgent all write task status updates (`pending` → `in_progress` → `completed`/`failed`) back to `task_plan`. With `replace_reducer`, whichever agent writes last wins — any concurrent or near-concurrent writes will silently overwrite each other's status updates. In LangGraph's sequential (non-parallel) execution this is less immediately dangerous, but it creates a fragile contract: if two agents ever execute in parallel branches (a natural evolution of this system), task statuses will be lost.

**Recommendation:**  
Extract task status out of `TaskPlan`. Add a separate `task_statuses: Dict[str, str]` field in `EngineeringState` using `dict_merge_reducer`, where keys are `task_id` values and values are status strings. `TaskPlan` itself becomes immutable after TaskPlannerAgent writes it. Each implementation agent writes only to `task_statuses[task_id]` for the tasks it owns. This cleanly separates the static plan from dynamic execution state.

---

### ISSUE-04 — `agent_scratchpad` Grows Without Bound and Is Never Cleared
**Severity:** High  
**Area:** EngineeringState Design / Memory

**Explanation:**  
`agent_scratchpad` accumulates intermediate reasoning from every agent invocation. Across a multi-cycle debug run (QA → Debug → Backend → QA → Debug → Backend...), each pass appends new reasoning to the same key. There is no eviction policy, size limit, or cleanup mechanism defined. In a long-running pipeline with many debug cycles, `agent_scratchpad` will grow to megabytes of accumulated intermediate reasoning, all serialized into every checkpoint. Additionally, if the scratchpad is passed into LLM context (the design says each agent writes to and reads from their own key), this growing blob will eat into the effective context budget.

**Recommendation:**  
Add a `max_scratchpad_size_bytes` config per agent. On each invocation, the agent replaces (not appends to) its own scratchpad key with only the reasoning from the current invocation. Historical reasoning should be written to long-term memory if it needs to be preserved, not accumulated in state. Change the reducer for each agent's scratchpad key from merge to replace for the agent's own key.

---

### ISSUE-05 — No Idempotency Key or Deduplication for State Writes
**Severity:** High  
**Area:** EngineeringState Design

**Explanation:**  
There is no mechanism to detect or prevent duplicate writes. If a LangGraph checkpoint is restored after a partial agent execution (e.g., the agent completed but the checkpoint write failed), the agent will re-execute and attempt to write the same artifacts again. With `dict_merge_reducer`, this means artifact versions will be incremented again for the same logical change, creating a false version history. With `replace_reducer`, the output is overwritten with an identical value — benign but untraceable.

**Recommendation:**  
Add an `operation_id: str` field to each artifact model (UUID v4 generated at the start of each agent invocation). Before writing, agents check whether a matching `operation_id` already exists in state. If it does, the write is skipped. This gives idempotent write semantics and is essential for reliable checkpoint recovery.

---

### ISSUE-06 — `memory_references` Is Too Coarse — All Agents Share One Namespace
**Severity:** Medium  
**Area:** EngineeringState Design

**Explanation:**  
`memory_references` stores a flat list of keys per memory type (long_term, vector, semantic, etc.) accumulated across all agents across all runs. By the time a pipeline is mid-way through execution, an agent reading `memory_references.long_term` cannot tell which keys are relevant to its domain — it receives a merged list of requirement patterns, architectural patterns, code templates, test patterns, and debug solutions all interleaved. This defeats the purpose of having typed memory references.

**Recommendation:**  
Namespace memory references by agent or by domain. Change from:
```
memory_references.long_term: list[str]
```
to:
```
memory_references.long_term: Dict[str, list[str]]
```
where the key is the agent name (e.g., `"ArchitectAgent"`) or a domain tag (e.g., `"architecture_patterns"`). Each agent appends only to its own namespace and reads only from its own namespace. The deduplication rule from Requirement 16 still applies per-namespace.

---

## 2. Agent Contracts

---

### ISSUE-07 — DebugAgent Has No Defined Input Contract for Which Errors to Fix
**Severity:** High  
**Area:** Agent Contracts

**Explanation:**  
DebugAgent's contract says it "reads all unresolved `ErrorRecord` entries from `errors`." This is ambiguous when multiple agents have failed across multiple cycles. If BackendAgent failed twice and QAAgent also failed, DebugAgent receives four unresolved errors. There is no priority ordering, no scope boundary (DebugAgent should only fix the most recent failure, not all historical failures), and no mechanism for DebugAgent to know which failure triggered the current invocation. The `next_agent` routing logic based on `produced_by` of the failing artifact does not generalize when multiple artifacts from multiple agents have failed simultaneously.

**Recommendation:**  
Add a `current_debug_target: Optional[str]` field to `EngineeringState` (type: `error_id` UUID). When a failure occurs and `workflow_status` is set to `"debug_required"`, the triggering agent also writes `current_debug_target = error_id` of the specific error. DebugAgent reads this field to scope its work to the specific failure at hand. Other unresolved errors are queued for subsequent debug cycles.

---

### ISSUE-08 — FinalProjectAgent Has a Race Condition in Its Write Guard
**Severity:** High  
**Area:** Agent Contracts

**Explanation:**  
FinalProjectAgent is designed to set `workflow_status = "completed"` and then write `final_output`. The guard on `final_output` checks that `workflow_status == "completed"` before allowing the write. But both the status transition and the guarded write happen within the same agent invocation — the agent sets the status and then immediately writes the output in the same state update. Since reducers are applied atomically after the agent returns (LangGraph applies the full partial update at once), the guard check happens against the pre-invocation state where `workflow_status == "documentation_complete"`, not `"completed"`. This means the guard will reject the write from FinalProjectAgent itself.

**Recommendation:**  
Remove the requirement that `final_output` be guarded by checking `workflow_status` at write time. Instead, the guard should be enforced by the LangGraph router: `final_output` can only be written by `FinalProjectAgent`, and the graph only routes to `FinalProjectAgent` when `workflow_status == "documentation_complete"`. The access control is structural (graph topology), not field-level. This is a cleaner and more reliable pattern.

---

### ISSUE-09 — ReviewerAgent's `rejected` Status Has No Defined Recovery Path
**Severity:** High  
**Area:** Agent Contracts / Workflow Design

**Explanation:**  
The design correctly routes `requires_changes` to the Debug cycle. However, when `overall_status == "rejected"`, the design immediately sets `human_approval_required = True` and routes to the human approval interrupt. There is no definition of what the human can do other than approve or reject continuation. If the human approves after a rejection, the system re-routes to DebugAgent — but DebugAgent's contract is to fix bugs and validation failures, not to address fundamental architecture or requirements rejections. A reviewer rejection typically means the implementation doesn't meet acceptance criteria, not that there's a bug. Routing rejected builds back through DebugAgent is the wrong recovery path.

**Recommendation:**  
Define a distinct recovery path for `rejected`: route back to TaskPlannerAgent (or ArchitectAgent if the rejection is architecture-level) rather than DebugAgent. Add a `rejection_scope` field to `ReviewReport` with values `code_level`, `design_level`, `requirements_level` to inform the routing decision. DebugAgent should only handle `requires_changes` (fixable bugs); `rejected` should restart the pipeline from the appropriate upstream agent.

---

### ISSUE-10 — BackendAgent and FrontendAgent Both Use `replace_reducer` on `task_plan`
**Severity:** Medium  
**Area:** Agent Contracts

**Explanation:**  
Both BackendAgent and FrontendAgent write `task_plan` with `replace_reducer` to update task statuses. This is already identified as a concern in ISSUE-03. The specific agent contract risk is that if BackendAgent's update is applied and then FrontendAgent replaces the entire `task_plan`, BackendAgent's completed task statuses will be silently overwritten. In the current strictly sequential design this doesn't manifest, but it is a latent bug that will surface if execution order ever changes.

**Recommendation:**  
See ISSUE-03. Separate `task_statuses` from `task_plan` as an independent `dict_merge_reducer` field.

---

### ISSUE-11 — No Agent Contract Specifies LLM Model as a Configurable Parameter
**Severity:** Medium  
**Area:** Agent Contracts

**Explanation:**  
The LLM strategy section recommends specific models per agent (GPT-4o for BackendAgent, Claude 3.5 Sonnet for DebugAgent, etc.). However, no agent contract lists the LLM model as an input or configurable parameter — it's implicit and hardcoded in the design narrative. In production, LLM model availability, pricing, and capability change frequently. Hardcoding model choice into an agent's implementation means a model deprecation requires code changes to every affected agent.

**Recommendation:**  
Add an `llm_config` parameter to each agent's contract. The contract should specify: `primary_model`, `fallback_model`, `temperature`, `max_tokens`, and `structured_output_schema`. Source these from `configs/agents.yaml`. This makes model selection a configuration concern, not an implementation concern.

---

## 3. State Ownership Matrix

---

### ISSUE-12 — `user_context` Is Listed as `replace_reducer` But Is a Dict
**Severity:** Medium  
**Area:** State Ownership Matrix

**Explanation:**  
The state ownership matrix assigns `replace_reducer` to `user_context`. However, `user_context` is typed as `Dict[str, Any]`. Using `replace_reducer` on a dict means that any agent writing to `user_context` replaces the entire dict, destroying any keys written by other agents or the initialization process. This is inconsistent with the pattern used for all other dict fields (`code_artifacts`, `execution_metadata`, etc.) which correctly use `dict_merge_reducer`. Furthermore, if `user_context` is intended to be write-once (set at init), it should have the same write-once guard as `user_input`, not just a `replace_reducer`.

**Recommendation:**  
If `user_context` is write-once, add the same immutability guard as `user_input` (Requirement 1, criterion 7 pattern). If it can be enriched by agents during the pipeline, change to `dict_merge_reducer` and document which agents are permitted to add keys.

---

### ISSUE-13 — `pipeline_stage` Has No Maximum Value or Reset Strategy
**Severity:** Low  
**Area:** State Ownership Matrix

**Explanation:**  
`pipeline_stage` is an integer that increments on each forward stage transition. In a pipeline with multiple debug cycles, the stage counter can increment well beyond 11 (the number of agents). A run with 5 debug cycles across 3 agents could reach `pipeline_stage = 26`. There is no definition of what `pipeline_stage` means in a re-entrant loop, making it useless for monitoring (a stage of 26 tells you nothing meaningful) and potentially confusing for recovery logic.

**Recommendation:**  
Replace `pipeline_stage` with two separate fields: `forward_stage` (increments only on first-pass forward progress, capped at the number of agents) and `total_invocations` (counts all agent invocations including re-entries). This separates "how far along the pipeline are we" from "how many times have agents run." Alternatively, encode stage as a string matching `workflow_status` rather than an integer, making it self-documenting.

---

## 4. LangGraph Workflow Design

---

### ISSUE-14 — Debug Loop Has No Global Iteration Cap
**Severity:** Critical  
**Area:** LangGraph Workflow / Loops

**Explanation:**  
The QA → Debug → Developer loop and the Reviewer → Debug → Developer loop both terminate when `retry_counts[agent] >= max_retries[agent]`. However, `retry_counts` is keyed by agent name and reset to `0` on human approval. Consider this scenario: the human approves after BackendAgent exhausts retries. `retry_counts["BackendAgent"]` resets to 0. BackendAgent fails again. Another 3 retries. Another human approval. This cycle can repeat indefinitely with no overall limit. There is no global loop counter, no maximum total invocation count, and no escalation path that permanently stops the cycle.

**Recommendation:**  
Add a `total_debug_cycles: int` counter to `EngineeringState` that increments on every DebugAgent invocation and is never reset. Add `max_debug_cycles: int` (configurable, default 10) to configs. When `total_debug_cycles >= max_debug_cycles`, set `workflow_status = "failed"` permanently regardless of human approval status. This provides a hard backstop against infinite cycling.

---

### ISSUE-15 — The Graph Has No Timeout or Deadline Mechanism
**Severity:** High  
**Area:** LangGraph Workflow

**Explanation:**  
There is no `timeout_at` field in state, no `max_pipeline_duration_seconds` config, and no design for what happens if a single agent hangs (e.g., LLM API rate limit, infinite LLM streaming, unresponsive sandbox). In production, a hung agent will hold the LangGraph thread indefinitely. The only failure modes defined are validation failures and API errors — not timeouts. LangGraph's built-in timeout support (per-node `timeout` parameter) is not referenced in the design.

**Recommendation:**  
Add `pipeline_started_at: str` (ISO 8601) and `pipeline_deadline_at: Optional[str]` to `EngineeringState`. Add per-agent timeout configuration to `configs/agents.yaml`. The orchestrator checks `pipeline_deadline_at` at each routing decision. If exceeded, set `workflow_status = "failed"` with `human_approval_notes` explaining the timeout. Each agent node should be wrapped with LangGraph's per-node timeout.

---

### ISSUE-16 — `human_approval_interrupt` Is Not a Defined LangGraph Node
**Severity:** High  
**Area:** LangGraph Workflow Design

**Explanation:**  
The conditional edge tables route to `human_approval_interrupt` as a destination, but Section 3.2 (the Nodes table) lists only 11 agent nodes. `human_approval_interrupt` is mentioned in Section 3.8 as "the graph pauses at a `human_approval_interrupt` node" but is never formally defined as a node, never included in the node table, and has no agent contract. In LangGraph, human-in-the-loop is typically implemented using `interrupt()` within a node (LangGraph's native interrupt mechanism) or a special interrupt node. The design leaves this entirely unspecified, which means the interrupt mechanism cannot be implemented consistently.

**Recommendation:**  
Add `human_approval_interrupt` as a formally defined node in Section 3.2. Define its contract: it reads `human_approval_required` and `human_approval_notes`, surfaces them to a human interface (CLI prompt, Slack message, API endpoint, or LangGraph Studio), waits for a resume signal, and based on the signal either resets `retry_counts` and sets `human_approval_required = False` (approve) or sets `workflow_status = "failed"` (reject). Specify whether this uses LangGraph's `interrupt()` function or an external webhook pattern. This is a load-bearing design gap.

---

### ISSUE-17 — DebugAgent Can Route Back to ProductManagerAgent or ArchitectAgent — But No Edge Exists
**Severity:** High  
**Area:** LangGraph Workflow Design

**Explanation:**  
DebugAgent's router (Section 3.4) shows it can route to BackendAgent, FrontendAgent, QAAgent, ReviewerAgent, and DevOpsAgent via `next_agent`. However, the design also says DebugAgent routes to "the `produced_by` value of the failing artifact." `CodeArtifact.produced_by` can be `"BackendAgent"` or `"FrontendAgent"` — that part is covered. But `requirements.produced_by = "ProductManagerAgent"` and `architecture.produced_by = "ArchitectAgent"`. If a critical validation failure occurs on these fields (e.g., ReviewerAgent rejects because the architecture is fundamentally flawed), the `produced_by` routing logic would try to route to `"ArchitectAgent"` or `"ProductManagerAgent"` — but no conditional edge from DebugAgent to those nodes is defined in Section 3.4, and no edge exists in the graph topology at all for this path.

**Recommendation:**  
Explicitly define the full set of nodes DebugAgent can route to as an enumerated list in the design. Add edges from `debug` → `architect` and `debug` → `product_manager` if that path is intended. If it's not intended (DebugAgent only fixes code, not architecture), then the design must explicitly prohibit routing to upstream agents and define what happens when the root cause is architectural.

---

## 5. Tooling Strategy

---

### ISSUE-18 — Code Execution Sandbox Is Listed as Required for DebugAgent But Has No Security Specification
**Severity:** High  
**Area:** Tooling Strategy / Security

**Explanation:**  
DebugAgent lists "Code execution sandbox" as a **required** tool. This means the system will execute LLM-generated code in a sandbox environment as a core, non-optional capability. Executing untrusted LLM-generated code is one of the highest-risk operations in any AI system. The design provides no specification for:
- What the sandbox technology is (Docker, Firecracker, gVisor, subprocess)
- What filesystem access the sandbox has
- What network access the sandbox has
- What resource limits (CPU, memory, time) apply
- What happens if the code attempts privilege escalation
- Whether the sandbox can write to the host filesystem
- How sandbox output is sanitized before being written back into `EngineeringState`

**Recommendation:**  
Add a dedicated **Sandbox Security Specification** section to the design. At minimum, it must define: execution environment isolation mechanism, network policy (default deny), filesystem policy (read-only host, ephemeral write), resource limits, execution timeout, output size limit, and sanitization of stdout/stderr before writing to state. Until this is specified, the sandbox should be demoted to "Optional" in the DebugAgent tool contract.

---

### ISSUE-19 — Secret Scanning Is Listed as Optional for DevOpsAgent
**Severity:** High  
**Area:** Tooling Strategy / Security

**Explanation:**  
Secret scanning is listed as **Optional** for DevOpsAgent. This means a production deployment of this system could generate and persist Dockerfiles, CI/CD pipeline configs, and infrastructure templates that contain hardcoded secrets, API keys, or credentials — with no guarantee of detection. LLMs are known to hallucinate credentials in generated IaC, particularly when given API key examples in context. Allowing a system that auto-generates and commits infrastructure code to run without mandatory secret scanning is a critical security gap.

**Recommendation:**  
Promote secret scanning from Optional to **Required** for DevOpsAgent. Also add secret scanning as a Required tool for BackendAgent and FrontendAgent — LLM-generated application code can also contain hardcoded credentials. Add a `secrets_scan_status: str` field to `DeploymentArtifact` and `CodeArtifact` models with values `not_scanned`, `clean`, `flagged`. The pipeline should not proceed to DocumentationAgent or FinalProjectAgent while any artifact has `secrets_scan_status == "flagged"`.

---

## 6. LLM Strategy

---

### ISSUE-20 — No Fallback Strategy for LLM API Outages Across Different Providers
**Severity:** Medium  
**Area:** LLM Strategy

**Explanation:**  
The LLM strategy assigns Claude 3.5 Sonnet (Anthropic) as primary for 4 agents and GPT-4o (OpenAI) as primary for 3 agents. Fallbacks swap between the two providers. This is a reasonable design — but there is no specification for what happens when both primary and fallback are unavailable simultaneously (e.g., a broad cloud provider outage, or rate limiting exhaustion on both providers). In this scenario, the pipeline silently routes to DebugAgent via `unhandled_exception`, which itself uses Claude 3.5 Sonnet. If Claude is down, DebugAgent also fails, triggering its own retry and eventually escalating to human approval — with no information about the root cause being an LLM provider outage rather than a code problem.

**Recommendation:**  
Add an `llm_provider_status` check to the orchestrator that runs before each agent invocation. Add a third fallback model (Gemini Flash or a locally-hosted model via Ollama) for critical agents. Add a new `error_type` value `llm_provider_unavailable` to `ErrorRecord` to distinguish provider outages from code errors. The DebugAgent contract should explicitly exclude `llm_provider_unavailable` errors from its debug scope.

---

### ISSUE-21 — Context Window Overflow Is Unhandled for ReviewerAgent
**Severity:** Medium  
**Area:** LLM Strategy

**Explanation:**  
The design acknowledges (in Section 5.2) that "by the time ReviewerAgent runs, state includes requirements + architecture + task_plan + all code_artifacts + test artifacts, potentially 50k–150k tokens." Combined with ISSUE-01 (full file content in state), real projects will routinely exceed this estimate. For a 100-file project with moderate-sized files, total code content alone could be 500k–1M tokens. The design proposes Gemini 1.5 Pro as a "future consideration" but provides no concrete strategy for context window overflow as a design-time concern — it's deferred entirely.

**Recommendation:**  
Define a concrete chunking and summarisation strategy for ReviewerAgent. Options:
- **Hierarchical review**: ReviewerAgent first reviews architecture compliance, then samples high-risk files identified by static analysis, then reviews test coverage. Full file-by-file review is deferred to a separate review pass.
- **File-level review agents**: Replace the single ReviewerAgent with a `ReviewOrchestrator` that spawns per-file review subtasks and aggregates findings.
- **Content-addressed state** (also resolves ISSUE-01): ReviewerAgent receives a manifest of file paths and fetches only the files it needs from external storage.

This must be resolved in the design, not deferred. The 200k context assumption fails on any project above minimal complexity.

---

## 7. Folder Ownership Strategy

---

### ISSUE-22 — `docs/generated/` Is Written at Runtime by DocumentationAgent to the Project's Own Source Tree
**Severity:** Medium  
**Area:** Folder Ownership Strategy / Security / Maintenance

**Explanation:**  
The folder ownership design specifies that DocumentationAgent writes generated documentation to `docs/generated/` within the project's source tree via the file write tool. This means the AI system has write access to the project's own git-tracked source directory. Several risks follow:
- Generated content overwrites or conflicts with human-authored content if filenames collide.
- If the pipeline is re-run (checkpoint recovery, partial re-execution), generated files may be overwritten mid-review.
- Any injection vulnerability in DocumentationAgent's output (e.g., malicious content in `architecture.api_specifications` injected via an untrusted user input) could write arbitrary content to tracked source files.
- `docs/generated/` being in the source tree means accidental `git add .` by a developer commits AI-generated content.

**Recommendation:**  
Write generated documentation to an isolated output directory outside the source tree (e.g., `output/{run_id}/docs/`) rather than inside `docs/`. The `run_id` namespace prevents cross-run collisions. If documentation should eventually be promoted to `docs/`, require an explicit human-initiated promotion step (a separate tool or CLI command), not an automated write by the agent. Add a `.gitignore` rule for `output/`.

---

## Summary Table

| ID | Severity | Area | Issue |
|----|----------|------|-------|
| ISSUE-01 | **Critical** | State Design | Full file content stored in-memory state — causes LLM context overflow and checkpoint bloat |
| ISSUE-02 | **Critical** | State Reducers | `errors` reducer contract is unsatisfiable — append-only vs. in-place update conflict |
| ISSUE-14 | **Critical** | Workflow Loops | Debug loop has no global iteration cap — can cycle indefinitely |
| ISSUE-03 | **High** | State Reducers | `task_plan` uses `replace_reducer` but multiple agents write to it — silent overwrite risk |
| ISSUE-04 | **High** | State Design | `agent_scratchpad` grows unboundedly — no eviction policy |
| ISSUE-05 | **High** | State Design | No idempotency keys — duplicate writes on checkpoint recovery inflate artifact versions |
| ISSUE-07 | **High** | Agent Contracts | DebugAgent has no scoped input contract — receives all unresolved errors indiscriminately |
| ISSUE-08 | **High** | Agent Contracts | FinalProjectAgent write guard is self-defeating — checks pre-invocation status |
| ISSUE-09 | **High** | Agent Contracts | `rejected` review status has no correct recovery path — routes to wrong agent type |
| ISSUE-15 | **High** | Workflow Design | No timeout or pipeline deadline mechanism |
| ISSUE-16 | **High** | Workflow Design | `human_approval_interrupt` is not a defined node — unimplementable as specified |
| ISSUE-17 | **High** | Workflow Design | DebugAgent `produced_by` routing can reference nodes with no outgoing edge defined |
| ISSUE-18 | **High** | Tooling / Security | Code execution sandbox is required but has no security specification |
| ISSUE-19 | **High** | Tooling / Security | Secret scanning is optional — permits credential leakage in generated IaC |
| ISSUE-06 | **Medium** | State Design | `memory_references` namespace is too coarse — all agents share flat lists |
| ISSUE-10 | **Medium** | Agent Contracts | `task_plan` replace-reducer conflict — same root as ISSUE-03, agent contract level |
| ISSUE-11 | **Medium** | Agent Contracts | LLM model not configurable per agent — hardcoded model choice |
| ISSUE-12 | **Medium** | State Matrix | `user_context` uses `replace_reducer` but is a dict — inconsistent with all other dicts |
| ISSUE-20 | **Medium** | LLM Strategy | No fallback for simultaneous multi-provider LLM outage |
| ISSUE-21 | **Medium** | LLM Strategy | Context window overflow for ReviewerAgent is deferred — not addressed as a design concern |
| ISSUE-22 | **Medium** | Folder Ownership | Agent writes generated files into the project's own source tree |
| ISSUE-13 | **Low** | State Matrix | `pipeline_stage` integer loses meaning in re-entrant loops |

---

## Minimum Viable Resolutions Before Implementation

The following issues **must** be resolved in the design before any implementation begins. They represent architectural decisions that cannot be patched after the fact without restructuring core state and workflow components.

1. **ISSUE-01** — Decide: content-addressed storage vs. in-state content. This decision shapes every artifact model and every LLM prompt strategy.
2. **ISSUE-02** — Decide: `errors` as `Dict[str, ErrorRecord]` or event-sourced append. This shapes the reducer architecture.
3. **ISSUE-14** — Add `total_debug_cycles` and `max_debug_cycles` to state and workflow. Without this, the system has no guaranteed termination.
4. **ISSUE-16** — Formally specify the `human_approval_interrupt` node. Without this, the human-in-the-loop mechanism cannot be built.
5. **ISSUE-08** — Fix the `final_output` write guard. The current specification is logically self-contradictory.
6. **ISSUE-18** — Specify sandbox security before implementing DebugAgent. Unspecified sandbox security in a system that executes LLM-generated code is a production blocker.

---

## Commendations

The following aspects of the design are well-executed and should be preserved:

- The state-as-communication-channel architecture is sound and correctly prevents agent-to-agent coupling.
- The agent contract format (inputs/outputs/failure modes/retry/success criteria) is thorough and production-grade.
- The write-once immutability guards on `run_id`, `session_id`, `created_at`, and `user_input` are correctly specified.
- The checkpoint strategy (per-stage, with correct backend selection by environment) is well thought out.
- The folder ownership cross-cutting access rules correctly prohibit agent-to-agent imports and prevent circular dependencies at the module level.
- The `FinalProjectOutput` guard (write only when `workflow_status == "completed"`) is a good pattern, even though the implementation approach needs correction (ISSUE-08).
- The LLM model selection rationale is well-reasoned and appropriately differentiates reasoning tiers from cost tiers.

---

*Review completed. 22 issues identified: 3 Critical, 9 High, 7 Medium, 1 Low. Recommend addressing all Critical and High issues in a revised design.md before proceeding to implementation.*
