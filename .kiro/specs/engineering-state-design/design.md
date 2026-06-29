# Design Document: EngineeringState — Multi-Agent System Architecture

## Overview

The `EngineeringState` design defines the architectural backbone of an autonomous multi-agent software engineering pipeline built on LangGraph. Eleven specialized agents — Product Manager, Architect, Task Planner, Backend, Frontend, QA, Debug, Reviewer, DevOps, Documentation, and Final Project — execute in a directed graph where `EngineeringState` is the exclusive communication channel. No agent ever holds a reference to another agent; all coordination, routing, error handling, and output accumulation happens through structured reads and writes to the shared state object.

This document covers six design concerns: the behavioral contract for each agent (inputs, outputs, tools, failure modes), the state ownership matrix (which agent owns each field), the LangGraph workflow topology (nodes, edges, conditional routing, loops), the tooling strategy per agent, the LLM model selection rationale, and the folder ownership strategy for the project codebase.

---

## Section 1: Agent Contracts

Each agent is a LangGraph node. Agents receive the full `EngineeringState` as input, perform bounded work, and emit a partial state update. The partial update is merged into the canonical state by the registered reducers before the next node executes.

---

### 1.1 ProductManagerAgent

**Purpose**
Translate the raw user request into a structured, versioned `ProductRequirements` document that downstream agents can consume without re-reading `user_input`.

**Responsibilities**
- Parse `user_input` and `user_context` to extract intent, constraints, and domain context.
- Produce `functional_requirements`, `non_functional_requirements`, `user_stories`, `acceptance_criteria`, `constraints`, and `business_goals`.
- Write `requirements` to state with `produced_by = "ProductManagerAgent"` and `version = 1` (or incremented on revision).
- Transition `workflow_status` to `"requirements_complete"`.
- Set `next_agent` to `"ArchitectAgent"`.
- Write `AgentExecutionMetadata` (started_at, completed_at, tokens_used, model_calls, status).
- Write reasoning chain to `agent_scratchpad["ProductManagerAgent"]`.
- Persist any reusable requirement patterns to long-term memory and update `memory_references.long_term`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `user_input` | Primary source of feature intent |
| `user_context` | Constraints, stack preferences, domain hints |
| `memory_references.long_term` | Prior requirement patterns from past runs |
| `memory_references.episodic` | Past project summaries for context |
| `run_id`, `session_id`, `project_name` | Metadata stamped on produced artifact |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `requirements` | replace_reducer | Full `ProductRequirements` instance |
| `workflow_status` | replace_reducer | → `"requirements_complete"` |
| `current_agent` | replace_reducer | `"ProductManagerAgent"` |
| `next_agent` | replace_reducer | `"ArchitectAgent"` |
| `pipeline_stage` | replace_reducer | Incremented by 1 |
| `execution_metadata["ProductManagerAgent"]` | dict_merge_reducer | Full `AgentExecutionMetadata` |
| `agent_scratchpad["ProductManagerAgent"]` | dict_merge_reducer | Reasoning chain, intermediate drafts |
| `memory_references.long_term` | append_reducer | New memory keys written to store |
| `checkpoint_stage` | replace_reducer | `"requirements_complete"` |
| `state_version` | replace_reducer | Incremented by 1 |
| `updated_at` | replace_reducer | Current ISO 8601 timestamp |

**Tools Required**
- LLM inference (structured output to `ProductRequirements` schema)
- Long-term memory read (retrieve prior requirement templates)
- Long-term memory write (persist new requirement patterns)

**Tools Optional**
- Web search (domain research for unfamiliar industries or terminology)
- Vector store query (semantic similarity to past requirements)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | LLM output fails `ProductRequirements` schema validation |
| `unhandled_exception` | LLM API timeout or network error |
| `validation_failure` | `user_input` is empty or whitespace |
| `retry_exhausted` | Max retries exceeded without valid output |

**Retry Strategy**
- On `validation_failure`: Re-invoke LLM with stricter structured-output prompt and the validation error message as additional context. Max 3 attempts.
- On `unhandled_exception`: Exponential backoff (2s, 4s, 8s). Max 3 attempts.
- On `retry_exhausted`: Set `human_approval_required = True`, populate `human_approval_notes`, set `workflow_status = "failed"`.

**Success Criteria**
- `requirements` is a valid `ProductRequirements` instance with all six list fields containing at least one non-empty string.
- `produced_by == "ProductManagerAgent"`, `version >= 1`, `produced_at` is valid ISO 8601.
- `workflow_status == "requirements_complete"`.
- `execution_metadata["ProductManagerAgent"].status == "completed"`.

---

### 1.2 ArchitectAgent

**Purpose**
Translate `ProductRequirements` into a complete `SystemArchitecture` that defines the technology stack, API surface, database design, security posture, scalability strategy, and deployment model.

**Responsibilities**
- Read `requirements` and derive a coherent, implementable system design.
- Populate all `SystemArchitecture` fields.
- Validate that the technology choices are consistent with constraints in `user_context`.
- Transition `workflow_status` to `"architecture_complete"`.
- Set `next_agent` to `"TaskPlannerAgent"`.
- Write `AgentExecutionMetadata`.
- Update `memory_references` with any architectural patterns persisted.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `requirements` | Source of functional/non-functional requirements |
| `user_context` | Stack preferences, deployment constraints |
| `memory_references.long_term` | Architectural patterns from prior runs |
| `memory_references.semantic` | Known architecture facts |
| `run_id`, `project_name` | Metadata for artifact stamping |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `architecture` | replace_reducer | Full `SystemArchitecture` instance |
| `workflow_status` | replace_reducer | → `"architecture_complete"` |
| `current_agent` | replace_reducer | `"ArchitectAgent"` |
| `next_agent` | replace_reducer | `"TaskPlannerAgent"` |
| `pipeline_stage` | replace_reducer | Incremented by 1 |
| `execution_metadata["ArchitectAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["ArchitectAgent"]` | dict_merge_reducer | Design rationale, alternatives considered |
| `memory_references.long_term` | append_reducer | Architecture patterns stored |
| `memory_references.semantic` | append_reducer | Structured architecture facts |
| `checkpoint_stage` | replace_reducer | `"architecture_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (structured output to `SystemArchitecture` schema)
- Long-term memory read/write (architectural pattern retrieval and persistence)

**Tools Optional**
- Web search (research on technology choices, framework comparisons)
- Diagram generation (Mermaid or PlantUML for architecture visualization)
- Vector store query (similar past architectures)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Missing required fields (`overview`, `scalability_strategy`, `deployment_model` empty) |
| `validation_failure` | `technology_stack` empty |
| `unhandled_exception` | LLM API failure |
| `retry_exhausted` | Cannot produce valid architecture after max retries |

**Retry Strategy**
- On `validation_failure`: Re-prompt with specific field validation errors. Max 3 attempts.
- On `unhandled_exception`: Exponential backoff. Max 3 attempts.
- On `retry_exhausted`: Escalate to human approval.

**Success Criteria**
- `architecture` is a valid `SystemArchitecture` with all required non-empty fields.
- `technology_stack` has at least one entry.
- `produced_by == "ArchitectAgent"`, `version >= 1`.
- `workflow_status == "architecture_complete"`.

---

### 1.3 TaskPlannerAgent

**Purpose**
Decompose `SystemArchitecture` and `ProductRequirements` into an ordered, dependency-resolved `TaskPlan` of atomic `ImplementationTask` entries assignable to Backend, Frontend, and QA agents.

**Responsibilities**
- Derive all implementation tasks from the architecture and requirements.
- Assign each task to `"BackendAgent"`, `"FrontendAgent"`, or `"QAAgent"`.
- Build a valid `execution_order` satisfying dependency constraints.
- Validate that all `dependencies` references resolve to existing `task_id` values.
- Transition `workflow_status` to `"planning_complete"`.
- Set `next_agent` to `"BackendAgent"`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `requirements` | Acceptance criteria and user stories to decompose |
| `architecture` | Components and services to implement |
| `user_context` | Scope and priority hints |
| `memory_references.episodic` | Task patterns from prior similar projects |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `task_plan` | replace_reducer | Full `TaskPlan` instance |
| `workflow_status` | replace_reducer | → `"planning_complete"` |
| `current_agent` | replace_reducer | `"TaskPlannerAgent"` |
| `next_agent` | replace_reducer | `"BackendAgent"` |
| `pipeline_stage` | replace_reducer | Incremented by 1 |
| `execution_metadata["TaskPlannerAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["TaskPlannerAgent"]` | dict_merge_reducer | Dependency graph scratch work |
| `checkpoint_stage` | replace_reducer | `"planning_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (structured output to `TaskPlan`/`ImplementationTask` schemas)
- Dependency graph validator (topological sort verification)

**Tools Optional**
- Memory retrieval (similar task plans from prior runs)
- Task estimation tool (complexity scoring)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | `dependencies` reference a `task_id` not in `tasks` |
| `validation_failure` | `execution_order` violates dependency ordering |
| `validation_failure` | `priority` out of range 1–100 |
| `unhandled_exception` | LLM API failure |

**Retry Strategy**
- On `validation_failure`: Re-prompt with the specific validation error (invalid reference, ordering violation). Max 3 attempts.
- On `retry_exhausted`: Escalate to human.

**Success Criteria**
- `task_plan` has valid `TaskPlan` with all tasks having unique `task_id` values.
- `execution_order` is a valid topological ordering.
- All `dependencies` resolve.
- `workflow_status == "planning_complete"`.

---

### 1.4 BackendAgent

**Purpose**
Implement all backend `ImplementationTask` entries from `task_plan` by generating server-side code artifacts and writing them into `code_artifacts`.

**Responsibilities**
- Iterate over tasks in `task_plan.execution_order` where `assigned_agent == "BackendAgent"`.
- Generate code for each task respecting the `architecture.technology_stack` choices.
- Write each produced file as a `CodeArtifact` with `artifact_type` in `{backend, config, schema, migration}`.
- Update `task_plan` task statuses to `"completed"` or `"failed"`.
- Transition `workflow_status` to `"backend_complete"`.
- Set `next_agent` to `"FrontendAgent"`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `task_plan` | Tasks assigned to BackendAgent |
| `architecture` | Technology stack, API specs, database design |
| `requirements` | Acceptance criteria to meet |
| `code_artifacts` | Existing files to avoid collision or to extend |
| `memory_references.long_term` | Code templates from prior runs |
| `memory_references.vector` | Semantically similar code snippets |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `code_artifacts` | dict_merge_reducer | One `CodeArtifact` per file path |
| `task_plan` | replace_reducer | Task statuses updated |
| `workflow_status` | replace_reducer | → `"backend_complete"` |
| `current_agent` | replace_reducer | `"BackendAgent"` |
| `next_agent` | replace_reducer | `"FrontendAgent"` |
| `pipeline_stage` | replace_reducer | Incremented |
| `execution_metadata["BackendAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["BackendAgent"]` | dict_merge_reducer | Implementation notes, design decisions |
| `checkpoint_stage` | replace_reducer | `"backend_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (code generation)
- File write tool (persisting generated files to disk and into state)
- Code linter/formatter (language-specific syntax validation)

**Tools Optional**
- Long-term memory read/write (code template retrieval and storage)
- Vector store query (retrieve similar implementations)
- Static analysis tool (security and quality pre-checks)
- Dependency resolver (package.json, requirements.txt, pom.xml management)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Generated code fails schema validation or linting |
| `unhandled_exception` | LLM API failure, file system error |
| `retry_exhausted` | Unable to produce valid code after max retries |
| `timeout` | Code generation exceeds time budget for a task |

**Retry Strategy**
- On `validation_failure`: Re-prompt with linting errors as context. Max 3 per task.
- On `timeout`: Reduce task scope and retry. If persists, log error and continue with partial output.
- On `retry_exhausted`: Set task status to `"failed"`, set `workflow_status = "debug_required"`.

**Success Criteria**
- All assigned tasks have `status == "completed"` in `task_plan`.
- `code_artifacts` contains valid `CodeArtifact` entries for all expected file paths.
- `produced_by == "BackendAgent"` on all new entries.
- `workflow_status == "backend_complete"`.

---

### 1.5 FrontendAgent

**Purpose**
Implement all frontend `ImplementationTask` entries from `task_plan`, generating UI components, styles, and client-side logic as `CodeArtifact` entries with `artifact_type == "frontend"`.

**Responsibilities**
- Iterate over tasks in `task_plan.execution_order` where `assigned_agent == "FrontendAgent"`.
- Generate frontend code consistent with `architecture.technology_stack` (framework, styling, state management).
- Ensure API calls align with `architecture.api_specifications`.
- Update task statuses in `task_plan`.
- Transition `workflow_status` to `"frontend_complete"`.
- Set `next_agent` to `"QAAgent"`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `task_plan` | Frontend-assigned tasks |
| `architecture` | Technology stack, API specs |
| `requirements` | UI/UX acceptance criteria |
| `code_artifacts` | Backend code to align with (API clients, shared types) |
| `memory_references.long_term` | UI component templates |
| `memory_references.vector` | Similar UI patterns |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `code_artifacts` | dict_merge_reducer | Frontend `CodeArtifact` entries |
| `task_plan` | replace_reducer | Frontend task statuses updated |
| `workflow_status` | replace_reducer | → `"frontend_complete"` |
| `current_agent` | replace_reducer | `"FrontendAgent"` |
| `next_agent` | replace_reducer | `"QAAgent"` |
| `pipeline_stage` | replace_reducer | Incremented |
| `execution_metadata["FrontendAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["FrontendAgent"]` | dict_merge_reducer | Component decisions, layout notes |
| `checkpoint_stage` | replace_reducer | `"frontend_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (UI/component code generation)
- File write tool
- Frontend linter/formatter (ESLint, Prettier, or equivalent)

**Tools Optional**
- Accessibility validator (WCAG compliance checking)
- Design token extractor (if design system context is available)
- Component library lookup (if using a known component library)
- Vector store query (similar UI patterns)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Linting errors, missing required UI elements |
| `unhandled_exception` | LLM API failure |
| `retry_exhausted` | Max retries on a frontend task |
| `validation_failure` | API client references non-existent backend endpoint |

**Retry Strategy**
- On `validation_failure`: Re-prompt with linting output and the specific API spec. Max 3 attempts.
- On `retry_exhausted`: Set task to `"failed"`, set `workflow_status = "debug_required"`.

**Success Criteria**
- All frontend tasks have `status == "completed"`.
- `code_artifacts` has valid frontend entries with consistent API alignment.
- `workflow_status == "frontend_complete"`.

---

### 1.6 QAAgent

**Purpose**
Generate a comprehensive `TestPlan` and produce `TestArtifact` entries covering unit, integration, regression, acceptance, and e2e tests for all code in `code_artifacts`.

**Responsibilities**
- Analyse `code_artifacts`, `requirements.acceptance_criteria`, and `task_plan` to derive test cases.
- Produce a `TestPlan` specifying strategy, coverage goals, and quality metrics.
- Generate test files as `TestArtifact` entries.
- Assign QA tasks from `task_plan` to status `"completed"`.
- Transition `workflow_status` to `"qa_complete"`.
- Set `next_agent` to `"ReviewerAgent"` (if no failures) or `"DebugAgent"` (if test failures detected).

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `code_artifacts` | Source code to test |
| `requirements` | Acceptance criteria to derive tests from |
| `task_plan` | QA tasks, coverage targets |
| `architecture` | Integration points, external dependencies |
| `memory_references.long_term` | Prior test templates |
| `memory_references.vector` | Similar test patterns |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `test_artifacts` | dict_merge_reducer | `TestArtifact` per test file |
| `test_plan` | replace_reducer | Full `TestPlan` instance |
| `task_plan` | replace_reducer | QA task statuses updated |
| `workflow_status` | replace_reducer | → `"qa_complete"` or `"debug_required"` |
| `current_agent` | replace_reducer | `"QAAgent"` |
| `next_agent` | replace_reducer | `"ReviewerAgent"` or `"DebugAgent"` |
| `pipeline_stage` | replace_reducer | Incremented (only on non-debug path) |
| `execution_metadata["QAAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["QAAgent"]` | dict_merge_reducer | Coverage analysis, gap notes |
| `checkpoint_stage` | replace_reducer | `"qa_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (test generation)
- Test runner (execute generated tests against code_artifacts)
- Code coverage analyzer

**Tools Optional**
- Mutation testing tool
- Contract testing validator (API contract verification)
- Performance test generator
- Security scanning tool (SAST)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Generated tests fail to parse or compile |
| `unhandled_exception` | Test runner crash, LLM API failure |
| `retry_exhausted` | Cannot generate passing tests after max retries |
| `validation_failure` | Coverage below required threshold |

**Retry Strategy**
- On test compilation failure: Re-generate test file with error context. Max 3 per file.
- On coverage gap: Add additional test cases in a follow-up pass. Max 2 additional passes.
- On `retry_exhausted`: Route to DebugAgent.

**Success Criteria**
- `test_plan` is fully populated with strategy and coverage goals.
- `test_artifacts` contains test files for all coverage targets.
- Coverage meets thresholds defined in `test_plan.quality_metrics`.
- `workflow_status == "qa_complete"`.

---

### 1.7 DebugAgent

**Purpose**
Perform root-cause analysis on errors recorded in `errors`, diagnose failing code or tests in `code_artifacts` / `test_artifacts`, and produce corrected artifacts with fix recommendations routed back to the originating agent.

**Responsibilities**
- Read all unresolved `ErrorRecord` entries from `errors`.
- Read the failing artifacts (code or tests) identified in each error.
- Analyse root cause using LLM reasoning.
- Produce corrected `CodeArtifact` entries (versioned) for code failures.
- Produce corrected `TestArtifact` entries for test failures.
- Resolve `ErrorRecord` entries: set `resolved = True`, populate `resolution_agent` and `resolution_notes`.
- Set `next_agent` to the `produced_by` value of the failing artifact (route back to originator).
- Transition `workflow_status` back to the appropriate in-progress status.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `errors` | Unresolved `ErrorRecord` entries with traceback |
| `code_artifacts` | Failing code files |
| `test_artifacts` | Failing test files |
| `review_report` | Reviewer findings that triggered debug cycle |
| `architecture` | Design constraints to respect in fixes |
| `task_plan` | Task context for the failing work |
| `retry_counts` | Current retry state per agent |
| `max_retries` | Per-agent limits |
| `memory_references` | Prior debug solutions |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `code_artifacts` | dict_merge_reducer | Corrected files, version incremented |
| `test_artifacts` | dict_merge_reducer | Corrected test files, version incremented |
| `errors` | append_reducer | Resolution updates (resolved=True records) |
| `workflow_status` | replace_reducer | Routes to appropriate in-progress status |
| `current_agent` | replace_reducer | `"DebugAgent"` |
| `next_agent` | replace_reducer | Originating agent of the failure |
| `retry_counts` | dict_merge_reducer | Updated counts |
| `human_approval_required` | replace_reducer | True if retry limit reached |
| `human_approval_notes` | replace_reducer | Context for human reviewer |
| `execution_metadata["DebugAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["DebugAgent"]` | dict_merge_reducer | RCA reasoning, hypothesis chain |
| `memory_references.long_term` | append_reducer | Debug patterns stored for future use |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (root-cause analysis, fix generation)
- Code execution sandbox (reproduce errors safely)
- Error log parser

**Tools Optional**
- Static analysis tool (identify bug patterns)
- Memory retrieval (prior solutions to similar bugs)
- Diff tool (compare original and fixed versions)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Proposed fix introduces new validation errors |
| `unhandled_exception` | LLM API failure during RCA |
| `retry_exhausted` | Cannot resolve error after max debug attempts |

**Retry Strategy**
- On fix failure: Try alternative approach with different reasoning strategy. Max 3 debug attempts per error.
- On `retry_exhausted`: Set `human_approval_required = True`.

**Success Criteria**
- All targeted `ErrorRecord` entries have `resolved == True`.
- Corrected artifacts have incremented `version` values.
- `next_agent` is set to the originating agent.
- If retry limit reached, `human_approval_required == True`.

---

### 1.8 ReviewerAgent

**Purpose**
Perform a holistic code review across all `code_artifacts`, `test_artifacts`, and `architecture` to produce a `ReviewReport` assessing quality, security, compliance, and readiness for deployment.

**Responsibilities**
- Review all code artifacts for quality, security, and maintainability.
- Validate architecture compliance against `architecture`.
- Assess test coverage adequacy from `test_plan` and `test_artifacts`.
- Score code quality (0.0–10.0).
- Produce a `ReviewReport` with `overall_status` in `{approved, requires_changes, rejected}`.
- If `overall_status == "requires_changes"` or `"rejected"`: set `workflow_status = "debug_required"`, `next_agent = "DebugAgent"`.
- If `overall_status == "approved"`: set `workflow_status = "review_complete"`, `next_agent = "DevOpsAgent"`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `code_artifacts` | All generated code for review |
| `test_artifacts` | Test coverage to assess |
| `test_plan` | Coverage goals to validate against |
| `architecture` | Design compliance reference |
| `requirements` | Acceptance criteria compliance |
| `errors` | Prior error history for context |
| `memory_references.organizational` | Engineering standards and conventions |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `review_report` | replace_reducer | Full `ReviewReport` instance |
| `workflow_status` | replace_reducer | → `"review_complete"` or `"debug_required"` |
| `current_agent` | replace_reducer | `"ReviewerAgent"` |
| `next_agent` | replace_reducer | `"DevOpsAgent"` or `"DebugAgent"` |
| `pipeline_stage` | replace_reducer | Incremented (only on approved path) |
| `execution_metadata["ReviewerAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["ReviewerAgent"]` | dict_merge_reducer | Detailed review notes per file |
| `memory_references.organizational` | append_reducer | New standards patterns identified |
| `checkpoint_stage` | replace_reducer | `"review_complete"` (only on approved) |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (code review and scoring)
- Static analysis tool (security scanning, complexity metrics)
- Test coverage report reader

**Tools Optional**
- Dependency vulnerability scanner
- License compliance checker
- Architecture diagram generator (for compliance visualization)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | `code_quality_score` outside 0.0–10.0 |
| `validation_failure` | `overall_status` not one of the three valid values |
| `unhandled_exception` | LLM API failure |

**Retry Strategy**
- On `validation_failure`: Re-run review with corrected prompt. Max 3 attempts.
- On `unhandled_exception`: Exponential backoff. Max 3 attempts.

**Success Criteria**
- `review_report` is a fully populated `ReviewReport` instance.
- `overall_status` is one of `{approved, requires_changes, rejected}`.
- `code_quality_score` is in 0.0–10.0.
- Routing decision is correctly encoded in `workflow_status` and `next_agent`.

---

### 1.9 DevOpsAgent

**Purpose**
Generate all deployment infrastructure artifacts (Dockerfiles, CI/CD pipelines, infrastructure templates, monitoring configs) and write them as `DeploymentArtifact` entries into `deployment_artifacts`.

**Responsibilities**
- Read `architecture.deployment_model` and `architecture.technology_stack` to determine container, orchestration, and CI/CD strategy.
- Generate `DeploymentArtifact` entries for all required deployment file types.
- Validate that deployment artifacts reference the correct file paths from `code_artifacts`.
- Transition `workflow_status` to `"devops_complete"`.
- Set `next_agent` to `"DocumentationAgent"`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `architecture` | Deployment model, technology stack, security architecture |
| `code_artifacts` | Source file paths to reference in Dockerfiles/builds |
| `review_report` | Security findings that must be addressed in deployment |
| `requirements` | Non-functional requirements (SLA, scale targets) |
| `memory_references.long_term` | Deployment templates from prior runs |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `deployment_artifacts` | dict_merge_reducer | All `DeploymentArtifact` entries |
| `workflow_status` | replace_reducer | → `"devops_complete"` |
| `current_agent` | replace_reducer | `"DevOpsAgent"` |
| `next_agent` | replace_reducer | `"DocumentationAgent"` |
| `pipeline_stage` | replace_reducer | Incremented |
| `execution_metadata["DevOpsAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["DevOpsAgent"]` | dict_merge_reducer | Infrastructure decisions, config notes |
| `checkpoint_stage` | replace_reducer | `"devops_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (IaC and pipeline generation)
- File write tool
- YAML/JSON/TOML validator (config file syntax validation)

**Tools Optional**
- Container image vulnerability scanner
- Infrastructure cost estimator
- Secret scanning tool (ensure no credentials in IaC)
- Cloud provider CLI validation (dry-run commands)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Invalid YAML/JSON syntax in generated configs |
| `validation_failure` | Deployment artifact references non-existent code path |
| `unhandled_exception` | LLM API failure |
| `retry_exhausted` | Cannot produce valid deployment config |

**Retry Strategy**
- On `validation_failure`: Re-prompt with syntax error output. Max 3 attempts.
- On `retry_exhausted`: Set `workflow_status = "debug_required"`, route to DebugAgent.

**Success Criteria**
- `deployment_artifacts` contains at minimum one `dockerfile` and one `ci_cd_pipeline` artifact.
- All artifact YAML/JSON syntax is valid.
- `workflow_status == "devops_complete"`.

---

### 1.10 DocumentationAgent

**Purpose**
Generate all project documentation — README, onboarding guide, architecture docs, API reference, deployment guide, maintenance guide — as `DocumentationArtifact` entries in `documentation_artifacts`.

**Responsibilities**
- Synthesise content from `requirements`, `architecture`, `code_artifacts`, `test_plan`, `review_report`, and `deployment_artifacts`.
- Produce one `DocumentationArtifact` per document type required.
- Ensure documentation is internally consistent (no references to non-existent endpoints, files, or services).
- Transition `workflow_status` to `"documentation_complete"`.
- Set `next_agent` to `"FinalProjectAgent"`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `requirements` | Project goals and user stories for README |
| `architecture` | System design for architecture docs |
| `code_artifacts` | Public interfaces for API reference |
| `test_plan` | Testing strategy for maintenance docs |
| `review_report` | Review status for project summary |
| `deployment_artifacts` | Deployment instructions source |
| `project_name`, `run_id` | Identity stamping |
| `memory_references.organizational` | Documentation standards |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `documentation_artifacts` | dict_merge_reducer | All `DocumentationArtifact` entries |
| `workflow_status` | replace_reducer | → `"documentation_complete"` |
| `current_agent` | replace_reducer | `"DocumentationAgent"` |
| `next_agent` | replace_reducer | `"FinalProjectAgent"` |
| `pipeline_stage` | replace_reducer | Incremented |
| `execution_metadata["DocumentationAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["DocumentationAgent"]` | dict_merge_reducer | Doc outline drafts |
| `checkpoint_stage` | replace_reducer | `"documentation_complete"` |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (documentation generation)
- File write tool
- Markdown linter/validator

**Tools Optional**
- OpenAPI/Swagger generator (from `architecture.api_specifications`)
- Diagram renderer (Mermaid to image for embedded docs)
- Spell checker / grammar tool

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | Required `doc_type` entries missing |
| `unhandled_exception` | LLM API failure |
| `retry_exhausted` | Cannot produce valid documentation |

**Retry Strategy**
- On `validation_failure`: Re-prompt targeting missing document type. Max 3 attempts.
- On `retry_exhausted`: Log error, continue with partial documentation set.

**Success Criteria**
- `documentation_artifacts` contains at minimum: `readme`, `architecture`, `api_reference`, `deployment_guide`.
- All entries have valid `produced_by`, `produced_at`, `version`.
- `workflow_status == "documentation_complete"`.

---

### 1.11 FinalProjectAgent

**Purpose**
Validate the completeness of all pipeline outputs and consolidate them into a single `FinalProjectOutput` instance, providing the end user with a unified summary of the delivered project.

**Responsibilities**
- Verify that all required artifact categories are populated in state.
- Count total files generated across `code_artifacts`, `test_artifacts`, `deployment_artifacts`, `documentation_artifacts`.
- Determine `overall_review_status` from `review_report.overall_status` (or `"not_reviewed"` if absent).
- Compute `pipeline_duration_seconds` from `execution_metadata` timestamps.
- Write `final_output` unconditionally. Graph topology guarantees this agent is only invoked when `workflow_status == "documentation_complete"` — the `documentation → final_project` edge is the sole entry path. No runtime status check is required or permitted.
- Transition `workflow_status` to `"completed"`.
- Set `next_agent` to `None`.

**Inputs — State Fields Read**
| Field | Purpose |
|-------|---------|
| `code_artifacts` | File path enumeration |
| `test_artifacts` | File path enumeration |
| `deployment_artifacts` | File path enumeration |
| `documentation_artifacts` | File path enumeration |
| `review_report` | Overall review status |
| `requirements` | Project name validation |
| `execution_metadata` | Timing data for duration computation |
| `workflow_status` | Guard: must be `"documentation_complete"` before writing |
| `project_name` | Carried into `FinalProjectOutput` |
| `errors` | Error summary for final notes |

**Outputs — State Fields Written**
| Field | Reducer | Notes |
|-------|---------|-------|
| `workflow_status` | replace_reducer | → `"completed"` |
| `final_output` | replace_reducer | `FinalProjectOutput` — topology-guarded (node only reachable from `documentation` node; no field-level status check required or permitted) |
| `current_agent` | replace_reducer | `"FinalProjectAgent"` |
| `next_agent` | replace_reducer | `None` |
| `execution_metadata["FinalProjectAgent"]` | dict_merge_reducer | Full metadata |
| `agent_scratchpad["FinalProjectAgent"]` | dict_merge_reducer | Validation check log |
| `checkpoint_stage` | replace_reducer | `"completed"` |
| `checkpoints["completed"]` | dict_merge_reducer | ISO 8601 completion timestamp |
| `state_version` | replace_reducer | Incremented |
| `updated_at` | replace_reducer | Current timestamp |

**Tools Required**
- LLM inference (summary generation for `FinalProjectOutput.summary`)
- File path enumeration utility

**Tools Optional**
- Report generator (PDF or HTML summary export)
- Notification tool (send completion notification to user)
- Long-term memory write (store completed project metadata in episodic memory)

**Failure Modes**
| Failure | Trigger |
|---------|---------|
| `validation_failure` | `total_files_generated` mismatch with actual artifact counts |
| `unhandled_exception` | LLM API failure during summary generation |

**Retry Strategy**
- On `validation_failure`: Recount artifacts and regenerate. Max 3 attempts.
- On `unhandled_exception`: Write `final_output` with empty summary rather than failing entire workflow.

**Success Criteria**
- `final_output` is a valid `FinalProjectOutput` with non-empty `project_name` and `summary`.
- `total_files_generated` matches the sum of all four path list lengths.
- `workflow_status == "completed"`.
- `next_agent == None`.

---

## Section 2: State Ownership Matrix

The table below covers all 33 fields of `EngineeringState`. "Update Frequency" describes how often the field changes during a typical run. "Reducer Type" matches the reducer defined in Requirement 18.

| State Field | Producer | Consumers | Update Frequency | Reducer Type |
|---|---|---|---|---|
| `run_id` | System (init) | All agents (read-only) | Once at init; immutable thereafter | replace_reducer (write-once guard) |
| `session_id` | System (init) | ProductManagerAgent, FinalProjectAgent | Once at init; immutable thereafter | replace_reducer (write-once guard) |
| `project_name` | System (init) | All agents (read), FinalProjectAgent (write to output) | Once at init | replace_reducer (write-once guard) |
| `created_at` | System (init) | FinalProjectAgent, monitoring | Once at init; immutable thereafter | replace_reducer (write-once guard) |
| `updated_at` | Every agent (on write) | Monitoring, FinalProjectAgent | Every state write cycle | replace_reducer |
| `user_input` | System (init) | ProductManagerAgent | Once at init; immutable thereafter | replace_reducer (write-once guard) |
| `user_context` | System (init) | ProductManagerAgent, ArchitectAgent, TaskPlannerAgent | Once at init | replace_reducer |
| `requirements` | ProductManagerAgent | ArchitectAgent, TaskPlannerAgent, QAAgent, ReviewerAgent, DocumentationAgent, FinalProjectAgent | Once per run; possible revision on debug cycle | replace_reducer |
| `architecture` | ArchitectAgent | TaskPlannerAgent, BackendAgent, FrontendAgent, QAAgent, ReviewerAgent, DevOpsAgent, DocumentationAgent | Once per run; possible revision on debug cycle | replace_reducer |
| `task_plan` | TaskPlannerAgent | BackendAgent, FrontendAgent, QAAgent, ReviewerAgent | Once; task statuses updated by BackendAgent, FrontendAgent, QAAgent | replace_reducer |
| `code_artifacts` | BackendAgent, FrontendAgent, DebugAgent | QAAgent, ReviewerAgent, DevOpsAgent, DocumentationAgent, FinalProjectAgent | Multiple writes (one per file); updated on debug cycles | dict_merge_reducer |
| `test_artifacts` | QAAgent, DebugAgent | ReviewerAgent, FinalProjectAgent | Multiple writes (one per test file); updated on debug cycles | dict_merge_reducer |
| `test_plan` | QAAgent | ReviewerAgent, DocumentationAgent, FinalProjectAgent | Once per run | replace_reducer |
| `review_report` | ReviewerAgent | DebugAgent, DevOpsAgent, DocumentationAgent, FinalProjectAgent | Once per review cycle; replaced on re-review | replace_reducer |
| `deployment_artifacts` | DevOpsAgent | DocumentationAgent, FinalProjectAgent | Multiple writes (one per deployment file) | dict_merge_reducer |
| `documentation_artifacts` | DocumentationAgent | FinalProjectAgent | Multiple writes (one per doc) | dict_merge_reducer |
| `workflow_status` | Every agent (final write) | LangGraph router (conditional edges), all agents (read for guard checks) | Once per agent completion | replace_reducer |
| `current_agent` | Every agent | Logging, monitoring, DebugAgent | Once per agent invocation | replace_reducer |
| `next_agent` | Every agent | LangGraph router | Once per agent completion | replace_reducer |
| `pipeline_stage` | Every agent (non-debug path) | Monitoring, FinalProjectAgent | Incremented on each successful stage | replace_reducer |
| `errors` | Any agent (on failure), DebugAgent (resolution updates) | DebugAgent, ReviewerAgent, FinalProjectAgent | Appended on failure; records updated on resolution | append_reducer |
| `retry_counts` | DebugAgent, orchestrator | DebugAgent, orchestrator | Incremented on each retry; reset on human approval | dict_merge_reducer |
| `max_retries` | System (init) or config | DebugAgent, orchestrator | Set at init; rarely modified | replace_reducer |
| `human_approval_required` | Orchestrator, DebugAgent | LangGraph router (human approval conditional edge) | Set on retry exhaustion; reset on approval | replace_reducer |
| `human_approval_notes` | Orchestrator, DebugAgent | Human reviewer interface | Set with `human_approval_required = True` | replace_reducer |
| `execution_metadata` | Every agent | FinalProjectAgent, monitoring | One entry per agent, updated at start and completion | dict_merge_reducer |
| `agent_scratchpad` | Every agent (own key only) | Each agent reads only its own key | Each agent invocation | dict_merge_reducer |
| `memory_references` | Any agent writing to external store | Any agent reading from external store | Appended when new keys are written to external stores | append_reducer (per list field) |
| `checkpoint_stage` | Every agent (on success) | Orchestrator, recovery logic | Once per successful pipeline stage | replace_reducer |
| `state_version` | Every agent (on any write) | Monitoring, recovery validation | Every state update cycle | replace_reducer |
| `checkpoints` | Every agent (on success) | Recovery logic, FinalProjectAgent | One entry per successful stage; forensic debug keys also written by DebugAgent | dict_merge_reducer |
| `final_output` | FinalProjectAgent | End user interface, monitoring | Once; written unconditionally by FinalProjectAgent — access enforced by graph topology, not field-level validation | replace_reducer |
| `total_debug_cycles` | DebugAgent (increments on every invocation); never reset | All routers (P2 global cap check), monitoring | Every DebugAgent invocation | replace_reducer |
| `pipeline_started_at` | System (init) | DeadlineEnforcerNode, all routers (P1 deadline check) | Once at init; immutable thereafter | replace_reducer (write-once guard) |
| `pipeline_deadline_at` | System (init from `configs/workflow.yaml`) | All routers (P1 deadline check), DeadlineEnforcerNode | Set at init; may be updated by operator before run | replace_reducer |
| `current_debug_target` | Any agent triggering `debug_required` (writes `error_id`); DebugAgent (clears to `None` on resolution) | DebugAgent | On each debug cycle entry and exit | replace_reducer |
| `rejection_scope` | ReviewerAgent (on `rejected` outcome) | HumanApprovalNode (drives resume routing), DebugAgent router | On each `rejected` review | replace_reducer |
| `forward_stage` | All agent nodes (first-pass forward progress only; NOT on re-entry) | Monitoring, FinalProjectAgent | Once per forward stage; capped at 11 | replace_reducer |
| `total_invocations` | Every agent node invocation including re-entries | Monitoring, forensics | Every agent invocation | replace_reducer |
| `max_debug_cycles` | System (init from `configs/workflow.yaml`; default `10`) | All routers (P2 global cap check) | Set at init; write-once | replace_reducer (write-once guard) |

---

## Section 3: LangGraph Workflow Design

> **Revision note:** This section has been fully revised to address ISSUE-08, ISSUE-09, ISSUE-13, ISSUE-14, ISSUE-15, ISSUE-16, and ISSUE-17 from the architecture review. See `workflow-design-revised.md` for the full annotated revision document. Key changes: `HumanApprovalNode` and `DeadlineEnforcerNode` are now formally specified nodes; `final_output` write guard is enforced by graph topology rather than field-level check; a global debug cycle cap is introduced; per-node and pipeline-level timeouts are specified; DebugAgent routing now covers all seven valid targets including `architect` and `product_manager`; `rejection_scope` drives post-rejection recovery routing.

### 3.0 New State Fields Required by This Revision

The following fields are added to `EngineeringState` by this revision.

| Field | Type | Default | Reducer | Purpose |
|-------|------|---------|---------|---------|
| `total_debug_cycles` | `int` | `0` | `replace_reducer` | Global DebugAgent invocation counter. Never reset. |
| `pipeline_started_at` | `str` | init timestamp | `replace_reducer` (write-once) | Pipeline wall-clock start time. |
| `pipeline_deadline_at` | `Optional[str]` | `None` | `replace_reducer` | Absolute ISO 8601 deadline. `None` = no deadline. |
| `current_debug_target` | `Optional[str]` | `None` | `replace_reducer` | `error_id` of the specific error DebugAgent must address. |
| `rejection_scope` | `Optional[str]` | `None` | `replace_reducer` | Set by ReviewerAgent on `rejected`. Values: `code_level`, `design_level`, `requirements_level`. |
| `forward_stage` | `int` | `0` | `replace_reducer` | First-pass forward progress counter (capped at 11). Replaces `pipeline_stage`. |
| `total_invocations` | `int` | `0` | `replace_reducer` | Total agent invocations including re-entries. |

> `pipeline_stage` is deprecated. It remains in state for backward compatibility but is no longer updated by agents.

---

### 3.1 Graph Topology (Revised)

The pipeline is a directed graph with **13 nodes**: 11 agent nodes, 1 human approval node, and 1 deadline enforcer node. The primary sequential path is linear. Conditional edges implement debug routing, rejection-scope re-routing, human approval holds, and deadline enforcement.

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
  │  workflow_status → "backend_complete" | "debug_required" | "deadline_exceeded"
  ▼
[FrontendAgent]
  │  workflow_status → "frontend_complete" | "debug_required" | "deadline_exceeded"
  ▼
[QAAgent]
  │  workflow_status → "qa_complete" | "debug_required" | "deadline_exceeded"
  ▼
[ReviewerAgent]
  │  workflow_status → "review_complete" | "debug_required" | "escalation_required" | "deadline_exceeded"
  ▼
[DevOpsAgent]
  │  workflow_status → "devops_complete" | "debug_required" | "deadline_exceeded"
  ▼
[DocumentationAgent]
  │  workflow_status → "documentation_complete"
  ▼
[FinalProjectAgent]         ← only reachable when status == "documentation_complete"
  │  workflow_status → "completed"
  │  final_output written here — no field guard needed (topology enforces access)
  ▼
END

Conditional branches:
  Any node → [DeadlineEnforcerNode]  when workflow_status == "deadline_exceeded"
  Any node → [HumanApprovalNode]     when human_approval_required == True
                                     or workflow_status == "escalation_required"
                                     or total_debug_cycles >= max_debug_cycles
  [DebugAgent] → one of:
             backend | frontend | qa | reviewer | devops  (code-level)
             architect                                     (design_level rejection)
             product_manager                              (requirements_level rejection)
             [HumanApprovalNode]                          when human_approval_required == True
```

---

### 3.2 Nodes (Revised)

Thirteen nodes in total. Two infrastructure nodes added: `human_approval` and `deadline_enforcer`.

| Node Name | Class / Type | Pipeline Role | Order |
|-----------|-------------|--------------|-------|
| `product_manager` | ProductManagerAgent | Requirements generation | 1 |
| `architect` | ArchitectAgent | System architecture design | 2 |
| `task_planner` | TaskPlannerAgent | Task decomposition | 3 |
| `backend` | BackendAgent | Server-side code generation | 4 |
| `frontend` | FrontendAgent | Client-side code generation | 5 |
| `qa` | QAAgent | Test generation and execution | 6 |
| `debug` | DebugAgent | Root-cause analysis and fix generation | 7 (conditional) |
| `reviewer` | ReviewerAgent | Code quality and compliance review | 8 |
| `devops` | DevOpsAgent | Infrastructure artifact generation | 9 |
| `documentation` | DocumentationAgent | Documentation synthesis | 10 |
| `final_project` | FinalProjectAgent | Output consolidation | 11 |
| `human_approval` | HumanApprovalNode | Human-in-the-loop interrupt/resume | Infrastructure |
| `deadline_enforcer` | DeadlineEnforcerNode | Pipeline timeout enforcement | Infrastructure |

**HumanApprovalNode** uses LangGraph's native `interrupt()` function. Execution pauses, state is checkpointed, and the node waits for an external resume command carrying `approval_decision = "approved"` or `"rejected"`. On `"approved"` it resets `retry_counts`, clears `human_approval_required`, and routes to `next_agent`. On `"rejected"` it sets `workflow_status = "failed"` and routes to `END`.

**DeadlineEnforcerNode** receives control when `workflow_status == "deadline_exceeded"`. It appends a `timeout` `ErrorRecord`, sets `workflow_status = "failed"`, writes `human_approval_notes` with elapsed time and agent name, and routes unconditionally to `END`. No human approval is offered — deadline exceeded is an operational stop, not a quality gate.

---

### 3.3 Normal Sequential Edges (Revised)

Happy-path edges are unchanged. All edges are conditional on `workflow_status` reaching the expected completion value. The `documentation → final_project` edge is the **sole** entry path to `final_project` — this structural constraint replaces the original field-level write guard on `final_output` (ISSUE-08 fix).

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

---

### 3.4 Conditional Edges and Routing Logic (Revised)

Every router evaluates conditions in this fixed **precedence order**:

1. **P1 — Deadline:** `workflow_status == "deadline_exceeded"` → `deadline_enforcer`
2. **P2 — Global cap:** `total_debug_cycles >= max_debug_cycles` → `workflow_status = "failed"` → `END`
3. **P3 — Human approval:** `human_approval_required == True` → `human_approval`
4. **P4 — Normal routing:** evaluate `workflow_status` and `next_agent`

This precedence ensures P1 and P2 are hard stops that no agent-level logic can bypass.

#### Router: After BackendAgent

```
P1: workflow_status == "deadline_exceeded"         →  deadline_enforcer
P2: total_debug_cycles >= max_debug_cycles         →  END (failed)
P3: human_approval_required == True                →  human_approval
P4: workflow_status == "backend_complete"          →  frontend
    workflow_status == "debug_required"            →  debug
    workflow_status == "failed"                    →  END
```

#### Router: After FrontendAgent

```
P1: workflow_status == "deadline_exceeded"         →  deadline_enforcer
P2: total_debug_cycles >= max_debug_cycles         →  END (failed)
P3: human_approval_required == True                →  human_approval
P4: workflow_status == "frontend_complete"         →  qa
    workflow_status == "debug_required"            →  debug
    workflow_status == "failed"                    →  END
```

#### Router: After QAAgent

```
P1: workflow_status == "deadline_exceeded"         →  deadline_enforcer
P2: total_debug_cycles >= max_debug_cycles         →  END (failed)
P3: human_approval_required == True                →  human_approval
P4: workflow_status == "qa_complete"               →  reviewer
    workflow_status == "debug_required"            →  debug
    workflow_status == "failed"                    →  END
```

#### Router: After ReviewerAgent (Revised — fixes ISSUE-09, ISSUE-17)

`requires_changes` routes to `debug` (code-level fix). `rejected` routes to `human_approval` with `rejection_scope` set, which drives the post-approval re-entry point — not DebugAgent.

```
P1: workflow_status == "deadline_exceeded"         →  deadline_enforcer
P2: total_debug_cycles >= max_debug_cycles         →  END (failed)
P3: workflow_status == "escalation_required"
    (review_report.overall_status == "rejected")  →  human_approval
P4: workflow_status == "review_complete"
    (overall_status == "approved")                →  devops
    workflow_status == "debug_required"
    (overall_status == "requires_changes")        →  debug
    workflow_status == "failed"                   →  END
```

**Post-approval routing from `human_approval` when triggered by `rejected`:**

| `rejection_scope` | Resume Node |
|------------------|------------|
| `"code_level"` | `debug` → then `backend` or `frontend` |
| `"design_level"` | `architect` |
| `"requirements_level"` | `product_manager` |

#### Router: After DevOpsAgent

```
P1: workflow_status == "deadline_exceeded"         →  deadline_enforcer
P2: total_debug_cycles >= max_debug_cycles         →  END (failed)
P3: human_approval_required == True                →  human_approval
P4: workflow_status == "devops_complete"           →  documentation
    workflow_status == "debug_required"            →  debug
    workflow_status == "failed"                    →  END
```

#### Router: After DebugAgent (Revised — fixes ISSUE-17)

All seven valid routing targets are now explicitly enumerated. `next_agent` must be one of these values; any other value is a validation error.

```
P1: workflow_status == "deadline_exceeded"         →  deadline_enforcer
P2: total_debug_cycles >= max_debug_cycles         →  END (failed)
P3: human_approval_required == True                →  human_approval
P4: next_agent == "BackendAgent"                   →  backend
    next_agent == "FrontendAgent"                  →  frontend
    next_agent == "QAAgent"                        →  qa
    next_agent == "ReviewerAgent"                  →  reviewer
    next_agent == "DevOpsAgent"                    →  devops
    next_agent == "ArchitectAgent"                 →  architect
    next_agent == "ProductManagerAgent"            →  product_manager
    workflow_status == "failed"                    →  END
```

**Permitted `next_agent` values from DebugAgent:**

| Value | Target Node | When Used |
|-------|------------|-----------|
| `"BackendAgent"` | `backend` | Code-level fix for backend task |
| `"FrontendAgent"` | `frontend` | Code-level fix for frontend task |
| `"QAAgent"` | `qa` | Test fix requiring QA re-execution only |
| `"ReviewerAgent"` | `reviewer` | Fix applied; re-review without reimplementing |
| `"DevOpsAgent"` | `devops` | Deployment artifact fix |
| `"ArchitectAgent"` | `architect` | Design-level rejection (ISSUE-17 fix) |
| `"ProductManagerAgent"` | `product_manager` | Requirements-level rejection (ISSUE-17 fix) |

---

### 3.5 QA → Debug → Developer Loop (Revised — fixes ISSUE-14)

The global debug cycle cap is checked by the router on every iteration. `current_debug_target` scopes DebugAgent to the specific failing error.

```
[QAAgent]
    │ test failure detected
    │ workflow_status        = "debug_required"
    │ current_debug_target   = error_id of the specific failing error
    │ next_agent             = "DebugAgent"
    │ total_invocations      incremented
    ▼
[Router: after QAAgent]
    │ P2 CHECK: total_debug_cycles >= max_debug_cycles?
    │   YES → workflow_status = "failed" → END   ← hard stop, no bypass
    │   NO  → route to [DebugAgent]
    ▼
[DebugAgent]
    │ reads current_debug_target → scopes to that specific ErrorRecord
    │ total_debug_cycles     incremented by 1 (never reset)
    │ performs root-cause analysis on the scoped error
    │ produces corrected code_artifacts (version++)
    │ resolves ErrorRecord (resolved=True)
    │ sets next_agent        = "BackendAgent" or "FrontendAgent"
    ▼
[BackendAgent or FrontendAgent]
    │ reads resolution_notes from the resolved ErrorRecord
    │ re-implements the failing task
    │ produces new code_artifacts (version++)
    ▼
[QAAgent]  ← loop back
    │ re-runs tests against corrected code
    │ if all pass:  workflow_status = "qa_complete" → [reviewer]
    │ if still failing:
    │   retry_counts[agent] >= max_retries[agent]?
    │     YES → human_approval_required = True → [human_approval]
    │     NO  → set new current_debug_target, loop continues
```

**Termination conditions (priority order):**

| Priority | Condition | Action |
|----------|-----------|--------|
| 1 | `total_debug_cycles >= max_debug_cycles` | `workflow_status = "failed"` → `END`. Hard stop. |
| 2 | `pipeline_deadline_at` exceeded | `workflow_status = "deadline_exceeded"` → `deadline_enforcer` → `END` |
| 3 | All tests pass | `workflow_status = "qa_complete"` → `reviewer` |
| 4 | `retry_counts[agent] >= max_retries[agent]` | `human_approval_required = True` → `human_approval`. Human may approve (resets `retry_counts`, loop continues — but `total_debug_cycles` is NOT reset) or reject → `END`. |

---

### 3.6 Reviewer → Debug → Developer Loop (Revised — fixes ISSUE-09, ISSUE-17)

Three distinct paths based on `review_report.overall_status`. DebugAgent handles only `requires_changes`. `rejected` uses `rejection_scope` to route to the correct upstream agent after human approval.

#### Path A: `requires_changes` (code-level fix)

```
[ReviewerAgent]
    │ overall_status         = "requires_changes"
    │ workflow_status        = "debug_required"
    │ rejection_scope        = "code_level"
    │ current_debug_target   = most critical error_id from review findings
    ▼
[Router] → P2 check: total_debug_cycles >= max_debug_cycles? → END if yes
    ▼
[DebugAgent]
    │ total_debug_cycles     incremented
    │ reads review_report.recommendations as fix context
    │ produces corrected code_artifacts (version++)
    │ sets next_agent        = "BackendAgent" or "FrontendAgent"
    ▼
[BackendAgent or FrontendAgent] → [QAAgent] → [ReviewerAgent]  ← loop back
    │ if approved:          workflow_status = "review_complete" → [devops]
    │ if requires_changes:  loop (total_debug_cycles checked each pass)
    │ if rejected:          see Path B or C
```

#### Path B: `rejected` with `rejection_scope = "design_level"`

```
[ReviewerAgent]
    │ workflow_status        = "escalation_required"
    │ rejection_scope        = "design_level"
    │ human_approval_required= True
    ▼
[HumanApprovalNode]
    │ if APPROVED:  next_agent = "ArchitectAgent"
    │               workflow_status reset → [architect] → ... → [reviewer]
    │ if REJECTED:  workflow_status = "failed" → END
```

#### Path C: `rejected` with `rejection_scope = "requirements_level"`

```
[ReviewerAgent]
    │ workflow_status        = "escalation_required"
    │ rejection_scope        = "requirements_level"
    │ human_approval_required= True
    ▼
[HumanApprovalNode]
    │ if APPROVED:  next_agent = "ProductManagerAgent"
    │               workflow_status = "initiated" → full pipeline restart
    │ if REJECTED:  workflow_status = "failed" → END
```

**Loop termination conditions:**

| Priority | Condition | Action |
|----------|-----------|--------|
| 1 | `total_debug_cycles >= max_debug_cycles` | `workflow_status = "failed"` → `END` |
| 2 | `pipeline_deadline_at` exceeded | → `deadline_enforcer` → `END` |
| 3 | `review_report.overall_status == "approved"` | → `devops` |
| 4 | `retry_counts["ReviewerAgent"] >= max_retries["ReviewerAgent"]` | `human_approval_required = True` → `human_approval` |
| 5 | `rejected` + human rejects | `workflow_status = "failed"` → `END` |

---

### 3.7 Checkpoint Strategy (Revised)

**Forward-progress checkpoints** (update `checkpoint_stage` and `forward_stage`):

| Trigger | `checkpoint_stage` | `forward_stage` |
|---------|-------------------|----------------|
| ProductManagerAgent completes | `"requirements_complete"` | 1 |
| ArchitectAgent completes | `"architecture_complete"` | 2 |
| TaskPlannerAgent completes | `"planning_complete"` | 3 |
| BackendAgent completes | `"backend_complete"` | 4 |
| FrontendAgent completes | `"frontend_complete"` | 5 |
| QAAgent completes | `"qa_complete"` | 6 |
| ReviewerAgent approves | `"review_complete"` | 7 |
| DevOpsAgent completes | `"devops_complete"` | 8 |
| DocumentationAgent completes | `"documentation_complete"` | 9 |
| FinalProjectAgent completes | `"completed"` | 10 |

DebugAgent invocations write `checkpoints["debug_{n}_start"]` and `checkpoints["debug_{n}_complete"]` for forensic history. They do NOT update `checkpoint_stage` or `forward_stage`. `HumanApprovalNode` checkpoints automatically via LangGraph's `interrupt()` before pausing.

**Recovery protocol:**
1. Restore `checkpoint_stage` from latest forward-progress checkpoint.
2. Restore `total_debug_cycles` exactly — never zero on recovery.
3. If `human_approval_required == True` in checkpoint, resume at `human_approval` node.
4. If `workflow_status == "failed"` or `"deadline_exceeded"`, surface to operator; do not auto-resume.
5. If deserialization fails, raise `RuntimeError` identifying `checkpoint_stage` and the failing field.

**Backend selection (unchanged):**

| Environment | Backend |
|-------------|---------|
| Local development | `MemorySaver` |
| Testing | `MemorySaver` |
| Staging / Production | `SqliteSaver` |
| Distributed Production | `PostgresSaver` |

---

### 3.8 Human Approval Points (Revised — fixes ISSUE-16)

`HumanApprovalNode` is a formally defined LangGraph node using `interrupt()`. It replaces all references to `human_approval_interrupt` from the original design.

**Trigger conditions:**

| Trigger | Set By | `human_approval_notes` Content |
|---------|--------|-------------------------------|
| `retry_counts[agent] >= max_retries[agent]` | Any agent or orchestrator | Agent name, retry count, last error message |
| `review_report.overall_status == "rejected"` | ReviewerAgent | `rejection_scope`, rejection reasons, `code_quality_score` |
| DebugAgent exhausts its own retry attempts | DebugAgent | `error_id`, RCA summary, fixes attempted |
| Unresolvable `ValidationError` on `requirements` or `architecture` | Orchestrator | Field name, validation error message |

**On `"approved"`:** resets `retry_counts[agent]` to `0`, sets `human_approval_required = False`, routes to `next_agent` (determined by `rejection_scope` for rejected paths).

**On `"rejected"`:** sets `workflow_status = "failed"`, routes to `END`. `final_output` not written.

**Human interface options:** LangGraph Studio (local), mocked fixtures (CI), CLI prompt (staging), signed webhook callback (production).


---

## Section 4: Tooling Strategy

Tools are registered per-agent. Each agent has access to a bounded toolset — agents do not share tool instances to prevent cross-contamination of tool state.

### 4.1 ProductManagerAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM structured output | Generate `ProductRequirements` from `user_input` |
| Required | Long-term memory read | Retrieve prior requirement patterns by project type |
| Required | Long-term memory write | Persist new requirement patterns |
| Optional | Web search | Domain research for unfamiliar industries |
| Optional | Vector store query | Semantic similarity to past requirements |
| Future | Stakeholder interview simulator | Generate clarifying Q&A from user input |
| Future | Requirements conflict detector | Identify contradictions in functional/non-functional requirements |

### 4.2 ArchitectAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM structured output | Generate `SystemArchitecture` |
| Required | Long-term memory read/write | Retrieve and store architectural patterns |
| Optional | Web search | Research technology comparisons, framework docs |
| Optional | Mermaid diagram generator | Produce architecture diagrams for documentation |
| Optional | Vector store query | Semantic similarity to prior architectures |
| Future | Architecture evaluation tool | Score architecture against quality attributes (scalability, security, cost) |
| Future | Technology compatibility checker | Validate technology stack compatibility |
| Future | Cloud pricing estimator | Estimate infrastructure cost from architecture spec |

### 4.3 TaskPlannerAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM structured output | Generate `TaskPlan` and `ImplementationTask` list |
| Required | Topological sort validator | Verify `execution_order` satisfies dependency constraints |
| Optional | Memory retrieval | Similar task plans from prior projects |
| Optional | Task complexity estimator | Score task complexity (informs priority assignment) |
| Future | Critical path analyzer | Identify the longest dependency chain |
| Future | Agent workload balancer | Distribute tasks evenly across BackendAgent/FrontendAgent |

### 4.4 BackendAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM code generation | Generate backend source files |
| Required | File write tool | Write `CodeArtifact` content to disk and state |
| Required | Language linter/formatter | Syntax and style validation (pylint, ESLint, etc.) |
| Optional | Long-term memory read/write | Code template retrieval and storage |
| Optional | Vector store query | Retrieve similar implementations |
| Optional | Static analysis tool | Security and complexity pre-checks |
| Optional | Dependency resolver | Manage package manifests (requirements.txt, package.json) |
| Future | Unit test auto-runner | Execute generated code against basic sanity tests during generation |
| Future | Database migration validator | Verify migration files against schema definitions |
| Future | API contract validator | Ensure generated routes match `architecture.api_specifications` |

### 4.5 FrontendAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM code generation | Generate frontend components and logic |
| Required | File write tool | Write `CodeArtifact` entries |
| Required | Frontend linter/formatter | ESLint, Prettier, or equivalent |
| Optional | Accessibility validator | WCAG compliance checker |
| Optional | Design token extractor | Extract styles from design system context |
| Optional | Component library lookup | Reference known component library APIs |
| Optional | Vector store query | Semantic similarity to UI patterns |
| Future | Visual regression snapshot tool | Capture component screenshots for baseline comparison |
| Future | Storybook generator | Auto-generate component stories |
| Future | Bundle size analyzer | Estimate frontend bundle impact |

### 4.6 QAAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM test generation | Generate `TestArtifact` files |
| Required | Test runner | Execute tests against `code_artifacts` |
| Required | Code coverage analyzer | Measure and report coverage percentages |
| Optional | Mutation testing tool | Validate test suite quality |
| Optional | Contract testing validator | API contract verification |
| Optional | Performance test generator | Generate load/stress test scripts |
| Optional | Security scanner (SAST) | Static application security testing |
| Future | Fuzz testing tool | Property-based / fuzz input generation |
| Future | Test impact analyzer | Identify which tests are affected by recent code changes |

### 4.7 DebugAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM root-cause analysis | Analyse errors and generate fix hypotheses |
| Required | Code execution sandbox | Safely reproduce errors in isolation |
| Required | Error log parser | Structure raw tracebacks into analysable form |
| Optional | Static analysis tool | Identify bug patterns in failing code |
| Optional | Memory retrieval | Prior solutions to similar error patterns |
| Optional | Diff tool | Compare original and fixed code versions |
| Future | Symbolic execution engine | Explore code paths that trigger failures |
| Future | Automated patch applicator | Apply minimal diffs to fix code without full regeneration |

### 4.8 ReviewerAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM code review | Generate `ReviewReport` with findings and scores |
| Required | Static analysis tool | Security scanning, complexity metrics (Bandit, Semgrep, SonarQube) |
| Required | Coverage report reader | Parse test coverage reports |
| Optional | Dependency vulnerability scanner | Check for known CVEs in dependencies |
| Optional | License compliance checker | Validate open source license compatibility |
| Optional | Architecture compliance validator | Check code structure matches `architecture` spec |
| Future | Code churn analyzer | Identify frequently changed, high-risk files |
| Future | Technical debt quantifier | Estimate debt as engineering effort |

### 4.9 DevOpsAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM IaC generation | Generate Dockerfiles, CI/CD pipelines, infra templates |
| Required | File write tool | Write `DeploymentArtifact` content |
| Required | YAML/JSON/TOML validator | Syntax validation of config files |
| Optional | Container image vulnerability scanner | Check generated Dockerfile for known CVEs |
| Optional | Infrastructure cost estimator | Estimate cloud resource costs |
| Optional | Secret scanning tool | Detect accidentally included credentials |
| Optional | Cloud provider CLI (dry-run) | Validate IaC templates against cloud provider APIs |
| Future | Drift detection tool | Compare desired vs actual infrastructure state |
| Future | Deployment simulation | Dry-run deployment in staging environment |

### 4.10 DocumentationAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM documentation generation | Generate `DocumentationArtifact` content |
| Required | File write tool | Write documentation files |
| Required | Markdown linter | Validate markdown syntax and structure |
| Optional | OpenAPI/Swagger generator | Generate API reference from `architecture.api_specifications` |
| Optional | Diagram renderer | Convert Mermaid syntax to images |
| Optional | Grammar/spell checker | Improve documentation quality |
| Future | Interactive documentation generator | Generate Docusaurus or MkDocs sites |
| Future | Video script generator | Auto-generate tutorial video scripts |

### 4.11 FinalProjectAgent Tools

| Category | Tool | Purpose |
|----------|------|---------|
| Required | LLM summary generation | Write `FinalProjectOutput.summary` |
| Required | File path enumerator | Count all artifacts accurately |
| Optional | Report generator | Export final summary as PDF or HTML |
| Optional | Notification tool | Send completion notification to user |
| Optional | Long-term memory write | Store completed project metadata in episodic memory |
| Future | Project health dashboard generator | Auto-generate a quality scorecard |
| Future | Deployment readiness checker | Validate all artifacts are ready for release |

---

## Section 5: LLM Strategy

LLM selection is driven by four factors: reasoning complexity (how much multi-step thinking the task requires), cost (tokens per call × price per token × call frequency), context length (total tokens in state at the time of invocation), and reliability (structured output success rate for the required schema).

### 5.1 Model Recommendations Per Agent

| Agent | Recommended Model | Fallback Model | Reasoning |
|-------|------------------|---------------|-----------|
| ProductManagerAgent | Claude 3.5 Sonnet | GPT-4o | High reasoning: must synthesise ambiguous natural language into structured requirements. Sonnet excels at instruction-following and structured JSON output. Context is moderate (user_input + memory). |
| ArchitectAgent | Claude 3.5 Sonnet | GPT-4o | High reasoning: must design coherent multi-component systems from requirements. Requires deep technical knowledge and multi-constraint reasoning. Sonnet's 200k context handles large requirement docs. |
| TaskPlannerAgent | GPT-4o | Claude 3.5 Sonnet | High reasoning: dependency graph construction and topological ordering require structured logical reasoning. GPT-4o's function calling and structured output mode is reliable for complex nested schemas like `TaskPlan`. |
| BackendAgent | GPT-4o | Claude 3.5 Sonnet | High reasoning + high context: must generate syntactically correct code that aligns with architecture. GPT-4o has strong code generation across most languages. Context length is large (architecture + task_plan + existing code_artifacts). |
| FrontendAgent | GPT-4o | Claude 3.5 Sonnet | High reasoning: UI code generation requires framework-specific idioms, accessibility awareness, and API alignment. GPT-4o has strong React/Vue/Angular training coverage. |
| QAAgent | GPT-4o-mini | GPT-4o | Moderate reasoning: test generation is formulaic given code artifacts. GPT-4o-mini is cost-effective for high-volume test file generation. Escalate to GPT-4o for complex coverage strategy. |
| DebugAgent | Claude 3.5 Sonnet | GPT-4o | Highest reasoning: root-cause analysis requires multi-step hypothesis generation, cross-referencing tracebacks with code, and proposing minimal targeted fixes. Sonnet's extended thinking capability is valuable here. |
| ReviewerAgent | Claude 3.5 Sonnet | GPT-4o | High reasoning + very high context: must review all code_artifacts, test_artifacts, and architecture simultaneously. Sonnet's 200k context window accommodates large codebases. Security and quality assessment requires nuanced judgment. |
| DevOpsAgent | GPT-4o | Claude 3.5 Sonnet | Moderate-high reasoning: IaC generation is template-driven but requires understanding deployment_model and security_architecture. GPT-4o handles structured YAML/JSON generation reliably. |
| DocumentationAgent | Claude 3 Haiku | GPT-4o-mini | Low reasoning, high volume: documentation is primarily synthesis and formatting. Haiku is fast and cost-effective for large-volume text generation. Complex doc sections (architecture, API reference) can escalate to Sonnet. |
| FinalProjectAgent | GPT-4o-mini | Claude 3 Haiku | Low reasoning: primarily enumeration and summarisation. GPT-4o-mini is reliable for structured output with simple schemas. |

---

### 5.2 Detailed Justifications

#### Claude 3.5 Sonnet — Primary for High-Reasoning Agents

**Justification**: Claude 3.5 Sonnet leads on instruction following, structured output reliability, and complex multi-step reasoning tasks. Its 200k token context window accommodates the cumulative state growth as the pipeline progresses (by the time ReviewerAgent runs, state includes requirements + architecture + task_plan + all code_artifacts + test artifacts, potentially 50k–150k tokens). The structured JSON output mode reliably produces valid Pydantic-compatible schemas for `ProductRequirements`, `SystemArchitecture`, and `ReviewReport`. DebugAgent specifically benefits from extended reasoning for root-cause analysis chains.

**Cost consideration**: More expensive than GPT-4o-mini and Haiku. Reserved for agents where reasoning quality directly determines downstream pipeline correctness (a bad architecture cascades into all subsequent agents). Cost is justified by reduced retry loops.

#### GPT-4o — Primary for Code Generation Agents

**Justification**: GPT-4o has strong performance on code generation benchmarks across Python, TypeScript, Java, and other common backend/frontend languages. Its function calling and structured output mode is highly reliable for nested schemas. For BackendAgent and FrontendAgent, where the output must be syntactically correct and executable, GPT-4o's reliability reduces validation failures and retry counts. Context window (128k) is sufficient for most code generation tasks.

**Cost consideration**: Mid-tier cost. Justified for BackendAgent/FrontendAgent where output volume (many files) and quality both matter.

#### GPT-4o-mini — Primary for High-Volume / Low-Complexity Agents

**Justification**: QAAgent generates many test files with formulaic patterns (given a function signature, produce unit tests). GPT-4o-mini is significantly cheaper than GPT-4o or Sonnet and performs well on structured generation tasks with clear templates. FinalProjectAgent's summarisation task is straightforward. DocumentationAgent (in conjunction with Haiku) uses mini for boilerplate-heavy sections.

**Cost consideration**: Lowest cost per token among capable models. Appropriate when reasoning complexity is low and volume is high.

#### Claude 3 Haiku — Primary for DocumentationAgent

**Justification**: Haiku is Anthropic's fastest and most cost-effective model. DocumentationAgent's primary work is synthesising existing structured data (architecture, code, API specs) into readable prose — a task that requires good language quality but not deep reasoning. Haiku produces fluent markdown and is significantly cheaper than Sonnet for the high token volume documentation requires.

**Cost consideration**: Lowest-cost Anthropic model. Ideal for the documentation synthesis workload.

#### Gemini 1.5 Pro / Gemini Flash — Future Consideration

Both models are strong alternatives, particularly Gemini 1.5 Pro for its 1 million token context window. As `EngineeringState` grows across large projects, ReviewerAgent's context requirement could exceed 200k tokens. Gemini 1.5 Pro is the recommended upgrade path for ReviewerAgent on large codebases. Gemini Flash is a cost-effective alternative to GPT-4o-mini for high-volume generation tasks.

---

### 5.3 Model Selection Summary

```
Highest Reasoning:  DebugAgent, ReviewerAgent, ArchitectAgent, ProductManagerAgent
  → Claude 3.5 Sonnet (200k context, structured output, extended reasoning)

High Reasoning + Code:  BackendAgent, FrontendAgent, TaskPlannerAgent
  → GPT-4o (strong code generation, reliable structured output, 128k context)

High Volume / Low Complexity:  QAAgent, FinalProjectAgent
  → GPT-4o-mini (cost-effective, reliable for structured generation)

High Volume / Synthesis:  DocumentationAgent
  → Claude 3 Haiku (fluent prose, cost-effective, fast)

Large Codebase Overflow:  ReviewerAgent on very large projects
  → Gemini 1.5 Pro (1M token context window)
```

---

## Section 6: Folder Ownership Strategy

Each folder in the project has a designated owner (the agent or system component primarily responsible for its content) and a set of readers and writers.

### 6.1 Ownership Map

#### `agents/`

**Owner**: Each agent owns its own file within this folder.

**Structure**:
```
agents/
  product_manager_agent.py      ← owned by ProductManagerAgent team
  architect_agent.py            ← owned by ArchitectAgent team
  task_planner_agent.py         ← owned by TaskPlannerAgent team
  backend_agent.py              ← owned by BackendAgent team
  frontend_agent.py             ← owned by FrontendAgent team
  qa_agent.py                   ← owned by QAAgent team
  debug_agent.py                ← owned by DebugAgent team
  reviewer_agent.py             ← owned by ReviewerAgent team
  devops_agent.py               ← owned by DevOpsAgent team
  documentation_agent.py        ← owned by DocumentationAgent team
  final_project_agent.py        ← owned by FinalProjectAgent team
  base_agent.py                 ← shared base class, owned by platform team
```

**Reads**: All agents read `base_agent.py` (inherits interface contract).  
**Writes**: Each agent writes only its own file.  
**Access Pattern**: Agents are instantiated by the workflow graph. No agent imports another agent's file directly.

---

#### `workflows/`

**Owner**: Platform/orchestration team.

**Structure**:
```
workflows/
  graph.py                  ← LangGraph graph definition (nodes, edges)
  routers.py                ← Conditional edge router functions
  interrupts.py             ← Human approval interrupt nodes
  runner.py                 ← Entry point: initialise state, compile graph, run
```

**Reads**: All agents read this folder's output at runtime (graph compilation).  
**Writes**: Platform team. Agents do not write to `workflows/` — routing logic is centralised here.  
**Access Pattern**: `runner.py` imports from `graph.py`; `graph.py` imports from `agents/`; `routers.py` reads `EngineeringState` fields only.

---

#### `state/`

**Owner**: Platform/state team.

**Structure**:
```
state/
  engineering_state.py      ← EngineeringState TypedDict + all Pydantic models
  reducers.py               ← replace_reducer, dict_merge_reducer, append_reducer
  validators.py             ← write-once guards, field validators
```

**Reads**: All agents, all routers, all tools.  
**Writes**: Platform team only. State structure changes require cross-team coordination.  
**Access Pattern**: Every agent imports `EngineeringState` and the Pydantic models from this folder. No agent imports `reducers.py` directly (reducers are registered in `Annotated` field declarations).

---

#### `memory/`

**Owner**: Memory/infrastructure team.

**Structure**:
```
memory/
  long_term.py              ← Long-term memory store interface
  vector_store.py           ← Vector/embedding store interface
  semantic_store.py         ← Structured fact store interface
  episodic_store.py         ← Past run record store interface
  organizational_store.py   ← Conventions and standards store interface
  memory_manager.py         ← Unified interface for all memory types
```

**Reads**: ProductManagerAgent, ArchitectAgent, BackendAgent, FrontendAgent, QAAgent, DebugAgent, ReviewerAgent, DevOpsAgent, DocumentationAgent.  
**Writes**: Any agent that persists new knowledge (ProductManagerAgent, ArchitectAgent, BackendAgent, DebugAgent, ReviewerAgent, DocumentationAgent, FinalProjectAgent).  
**Access Pattern**: Agents call `memory_manager.py` methods. Memory stores are injected as dependencies; agents do not construct store instances directly.

---

#### `prompts/`

**Owner**: Each agent owns its own prompt files; prompt engineering team owns templates.

**Structure**:
```
prompts/
  product_manager/
    system_prompt.md
    user_prompt_template.md
    revision_prompt.md
  architect/
    system_prompt.md
    user_prompt_template.md
  task_planner/
    ...
  backend/
    ...
  frontend/
    ...
  qa/
    ...
  debug/
    ...
  reviewer/
    ...
  devops/
    ...
  documentation/
    ...
  final_project/
    ...
```

**Reads**: Each agent reads only its own prompt directory.  
**Writes**: Prompt engineering team. Individual agents may update their own prompts without affecting others.  
**Access Pattern**: Prompts are loaded at agent initialisation. No cross-agent prompt sharing.

---

#### `tools/`

**Owner**: Tools team; individual agents own tool files specific to their use case.

**Structure**:
```
tools/
  shared/
    llm_client.py           ← LLM inference wrapper (all agents)
    file_writer.py          ← File write utility (Backend, Frontend, DevOps, Documentation)
    memory_tools.py         ← Memory read/write helpers (multiple agents)
    web_search.py           ← Web search tool (ProductManager, Architect)
  backend/
    linter.py               ← Language-specific linters
    dependency_resolver.py
  frontend/
    frontend_linter.py
    accessibility_validator.py
  qa/
    test_runner.py
    coverage_analyzer.py
  debug/
    sandbox.py              ← Code execution sandbox
    log_parser.py
  reviewer/
    static_analyzer.py
    vulnerability_scanner.py
  devops/
    yaml_validator.py
    secret_scanner.py
  documentation/
    markdown_linter.py
    openapi_generator.py
```

**Reads**: Agents read only `shared/` and their own subdirectory.  
**Writes**: Tools team owns `shared/`; each agent team owns its subdirectory.  
**Access Pattern**: Tools are injected into agent constructors. Agents do not instantiate tool classes directly.

---

#### `tests/`

**Owner**: QA team (test infrastructure); individual agent teams own unit tests for their agents.

**Structure**:
```
tests/
  unit/
    test_product_manager.py
    test_architect.py
    test_task_planner.py
    test_backend.py
    test_frontend.py
    test_qa.py
    test_debug.py
    test_reviewer.py
    test_devops.py
    test_documentation.py
    test_final_project.py
    test_state_reducers.py
    test_state_validators.py
  integration/
    test_pipeline_happy_path.py
    test_debug_loop.py
    test_review_loop.py
    test_checkpoint_recovery.py
    test_human_approval.py
  fixtures/
    sample_states.py        ← Pre-built EngineeringState fixtures
    mock_agents.py          ← Mock agent implementations
```

**Reads**: CI/CD pipeline, developers.  
**Writes**: Agent teams write unit tests for their own agents; QA team writes integration tests.  
**Access Pattern**: Tests import from `agents/`, `state/`, `workflows/`, and `tools/`. Tests do not import from each other.

---

#### `configs/`

**Owner**: Platform/infrastructure team.

**Structure**:
```
configs/
  agents.yaml               ← Agent configurations (max_retries, LLM model selection)
  memory.yaml               ← Memory backend configuration
  workflow.yaml             ← Graph execution settings
  logging.yaml              ← Logging configuration
  .env.example              ← Environment variable template
```

**Reads**: `runner.py`, agents (model config), memory manager (backend config).  
**Writes**: Platform team only. Configuration changes are reviewed before deployment.  
**Access Pattern**: Configs are loaded at application startup and injected via a config object. Agents do not read config files directly — they receive configuration through constructor parameters.

---

#### `docs/`

**Owner**: Documentation team (human-authored); DocumentationAgent writes generated artifacts here.

**Structure**:
```
docs/
  architecture/
    system_overview.md      ← Human-authored system architecture
    agent_contracts.md      ← This document's agent contract section
    state_ownership.md      ← State ownership matrix reference
  developer/
    setup.md
    contributing.md
    agent_development_guide.md
  generated/                ← DocumentationAgent writes here at runtime
    README.md
    API_REFERENCE.md
    DEPLOYMENT_GUIDE.md
    ARCHITECTURE.md
    ONBOARDING.md
    MAINTENANCE.md
  adr/                      ← Architecture Decision Records
    001-langgraph-state.md
    002-pydantic-v2.md
    003-llm-selection.md
```

**Reads**: All developers, end users, operators.  
**Writes**: `docs/architecture/` and `docs/developer/` are written by the human team. `docs/generated/` is written exclusively by DocumentationAgent at runtime.  
**Access Pattern**: `docs/generated/` is populated by DocumentationAgent via the file write tool into `documentation_artifacts`, which are then flushed to disk. Human-authored docs are not accessible to agents at runtime.

---

### 6.2 Cross-Cutting Access Rules

1. Agents only import from: `state/`, `tools/shared/`, `tools/{own-subdirectory}/`, `memory/`, `prompts/{own-subdirectory}/`, `configs/` (via injected config object).
2. No agent imports from `agents/` (no agent-to-agent dependencies).
3. No agent imports from `workflows/` (workflow topology is external to agents).
4. `state/` has no imports from any agent, tool, or workflow module (zero upward dependencies).
5. `workflows/` imports from `agents/` and `state/` only.
6. `tests/` imports from anywhere but is never imported by production code.
