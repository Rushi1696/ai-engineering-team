# Requirements Document

## Introduction

The `EngineeringState` is the central shared memory of an autonomous multi-agent software engineering system built with LangGraph and Python. It serves as the single source of truth across a pipeline of specialized agents: Product Manager, Architect, Task Planner, Backend, Frontend, QA, Debug, Reviewer, DevOps, Documentation, and Final Project agents.

Each agent reads from state, performs its work, and writes structured outputs back into state. Agents never communicate directly. Every state transition must be explicit, traceable, versioned, and recoverable. This document defines the structural and behavioral requirements for `EngineeringState` — covering field definitions, typing strategy (Pydantic + TypedDict), state reducers, versioning, and error/execution tracking — without specifying implementation code.

---

## Glossary

- **EngineeringState**: The central LangGraph state object that flows through the entire agent pipeline, carrying all inputs, intermediate outputs, and final artifacts.
- **Agent**: A specialized LangGraph node that reads from and writes to `EngineeringState`. Agents never communicate with each other directly.
- **Reducer**: A LangGraph-compatible function that merges an incoming partial state update with the existing state value for a given field. Used via `Annotated[T, reducer_fn]` in TypedDict.
- **Producer**: The agent responsible for writing a specific field's value.
- **Consumer**: An agent that reads a specific field's value.
- **Workflow Metadata**: Fields that track pipeline-level execution status, routing decisions, and run identifiers.
- **Execution Metadata**: Fields that track per-agent timing, token usage, model calls, and retry counts.
- **Short-Term Memory**: In-context scratchpad memory visible only within a single agent's invocation.
- **Long-Term Memory**: Persisted memory written to an external store and accessible across workflow runs.
- **Vector Memory**: Embeddings-based semantic memory for similarity retrieval.
- **Semantic Memory**: Structured facts and concepts derived from prior runs.
- **Episodic Memory**: Records of past workflow executions and their outcomes.
- **Organizational Knowledge**: Shared conventions, standards, and patterns accumulated across many runs.
- **Checkpoint**: A snapshot of `EngineeringState` at a specific pipeline stage, enabling recovery and replay.
- **Artifact**: A produced output (code file, test suite, Dockerfile, README, etc.) stored within or referenced from state.
- **Revision**: An incremented version of a state field resulting from an update cycle.
- **Debug Cycle**: A routing loop where failures are sent to the Debug Agent for root-cause analysis and fix recommendations before re-entering the pipeline.

---

## Requirements

---

### Requirement 1: Shared Identity and Run Metadata

**User Story:** As a system operator, I want every workflow run to carry a unique, traceable identity, so that I can correlate logs, checkpoints, and outputs across the entire pipeline.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `run_id` field of type `str` that uniquely identifies the current workflow execution.
2. THE `EngineeringState` SHALL contain a `session_id` field of type `str` that groups related runs belonging to the same user session.
3. THE `EngineeringState` SHALL contain a `project_name` field of type `str` that is non-empty and identifies the software project being built.
4. THE `EngineeringState` SHALL contain a `created_at` field of type `str` (ISO 8601 datetime) recording when the workflow run was initiated.
5. THE `EngineeringState` SHALL contain an `updated_at` field of type `str` (ISO 8601 datetime) initialized to the same value as `created_at` and updated on every subsequent state write.
6. WHEN a new workflow run is initiated, THE `EngineeringState` SHALL assign a `run_id` that is a UUID v4 string (format: `xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx`).
7. IF any agent or process attempts to overwrite `run_id`, `session_id`, or `created_at` after initial assignment, THE `EngineeringState` SHALL raise a `ValueError` and reject the write without modifying state.

---

### Requirement 2: User Input and Raw Intent

**User Story:** As the Product Manager agent, I want to read the original user-provided request from state, so that I can convert it into structured requirements without losing intent.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `user_input` field of type `str` storing the original natural-language feature request, bounded to between 1 and 100,000 characters inclusive.
2. THE `EngineeringState` SHALL contain a `user_context` field of type `Dict[str, Any]` storing supplementary metadata provided by the user (e.g., target language, preferred stack, constraints).
3. IF any agent downstream of the pipeline entry point attempts to overwrite `user_input`, THE `EngineeringState` SHALL raise a `ValueError` and reject the write, leaving `user_input` unchanged.
4. WHEN `user_context` is not provided by the user, THE `EngineeringState` SHALL initialize it to an empty dict.
5. WHEN `user_input` is provided at pipeline entry with a length of 0 or consists entirely of whitespace, THE `EngineeringState` SHALL raise a `ValueError` and reject pipeline initialization.

---

### Requirement 3: Product Requirements Output

**User Story:** As the Architect agent, I want to read a structured requirements document from state, so that I can design a system architecture that satisfies the stated business goals.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `requirements` field of type `Optional[ProductRequirements]`, initialized to `None` until the Product Manager agent writes to it.
2. THE `ProductRequirements` model SHALL contain: `functional_requirements` (list of str), `non_functional_requirements` (list of str), `user_stories` (list of str), `acceptance_criteria` (list of str), `constraints` (list of str), and `business_goals` (list of str).
3. WHEN the `requirements` field is assigned a new `ProductRequirements` instance for the first time, THE model SHALL set `version` to `1`. WHEN an existing `ProductRequirements` instance is revised, THE model SHALL increment `version` by `1`.
4. THE `ProductRequirements` model SHALL contain a `produced_by` field of type `str` set to `"ProductManagerAgent"`.
5. THE `ProductRequirements` model SHALL contain a `produced_at` field of type `str` (ISO 8601 datetime) set to the timestamp at the moment the Product Manager agent writes the instance.
6. WHEN the Product Manager agent completes its task, THE `EngineeringState` SHALL update the `requirements` field with a `ProductRequirements` instance where: all six list fields each contain at least one non-empty string entry, `version` is `≥ 1`, `produced_by` equals `"ProductManagerAgent"`, and `produced_at` is a valid ISO 8601 datetime string.
7. IF the `requirements` field has not yet been populated, THE `EngineeringState` SHALL initialize it to `None`.

---

### Requirement 4: System Architecture Output

**User Story:** As the Task Planner agent, I want to read a system architecture definition from state, so that I can decompose it into implementable atomic tasks.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain an `architecture` field of type `Optional[SystemArchitecture]`, initialized to `None` until the Architect agent writes to it.
2. THE `SystemArchitecture` model SHALL contain: `overview` (non-empty str), `technology_stack` (Dict[str, str] mapping component names to technology choices), `api_specifications` (list of Dict, where each Dict contains at minimum `endpoint` (str) and `method` (str) keys), `database_design` (Dict[str, Any]), `security_architecture` (Dict[str, Any]), `scalability_strategy` (non-empty str), and `deployment_model` (non-empty str).
3. WHEN the `architecture` field is assigned a new `SystemArchitecture` instance for the first time, THE model SHALL set `version` to `1`. WHEN an existing instance is revised, THE model SHALL increment `version` by `1`.
4. THE `SystemArchitecture` model SHALL contain a `produced_by` field of type `str` set to `"ArchitectAgent"`.
5. THE `SystemArchitecture` model SHALL contain a `produced_at` field of type `str` (ISO 8601 datetime) set at the moment the Architect agent writes the instance.
6. WHEN the Architect agent completes its task, THE `EngineeringState` SHALL update the `architecture` field with a `SystemArchitecture` instance where: `overview`, `scalability_strategy`, and `deployment_model` are non-empty strings; `technology_stack` contains at least one entry; `version` is `≥ 1`; `produced_by` equals `"ArchitectAgent"`; and `produced_at` is a valid ISO 8601 datetime string.
7. IF the `architecture` field has not yet been populated, THE `EngineeringState` SHALL initialize it to `None`.

---

### Requirement 5: Task Plan Output

**User Story:** As the Backend, Frontend, and QA agents, I want to read an ordered list of atomic implementation tasks from state, so that I can execute assigned work in the correct sequence.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `task_plan` field of type `Optional[TaskPlan]`, initialized to `None` until the Task Planner agent writes to it.
2. THE `TaskPlan` model SHALL contain a `tasks` field of type `list[ImplementationTask]`.
3. THE `ImplementationTask` model SHALL contain: `task_id` (str), `title` (str), `description` (str), `assigned_agent` (str), `priority` (int in range 1–100 where 1 is highest priority), `dependencies` (list of str referencing other `task_id` values in the same plan), `acceptance_criteria` (list of str), and `status` (str with values: `pending`, `in_progress`, `completed`, `failed`).
4. THE `TaskPlan` model SHALL contain a `version` field of type `int` initialized to `1` on first write and incremented by `1` on each revision.
5. THE `TaskPlan` model SHALL contain a `produced_by` field of type `str` set to `"TaskPlannerAgent"`.
6. THE `TaskPlan` model SHALL contain a `produced_at` field of type `str` (ISO 8601 datetime) set at the moment the Task Planner agent writes the instance.
7. THE `TaskPlan` model SHALL contain an `execution_order` field of type `list[str]` satisfying: (a) every entry in `execution_order` is a `task_id` present in `tasks`; (b) every `task_id` in `tasks` appears exactly once in `execution_order`; (c) no task appears in `execution_order` before any task listed in its `dependencies`.
8. IF a `dependencies` entry references a `task_id` not present in the same `TaskPlan.tasks` list, THE `EngineeringState` SHALL raise a `ValidationError` identifying the invalid reference.
9. WHEN the Task Planner agent completes its task, THE `EngineeringState` SHALL update the `task_plan` field with a fully populated `TaskPlan` instance.
10. IF the `task_plan` field has not yet been populated, THE `EngineeringState` SHALL initialize it to `None`.

---

### Requirement 6: Generated Code Artifacts

**User Story:** As the QA, Reviewer, and DevOps agents, I want to read generated code artifacts from state, so that I can validate, review, and deploy them.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `code_artifacts` field of type `Dict[str, CodeArtifact]` mapping a file path key to a `CodeArtifact` Pydantic model.
2. THE `CodeArtifact` model SHALL contain: `file_path` (str), `language` (str), `content` (str), `artifact_type` (str with values: `backend`, `frontend`, `test`, `config`, `schema`, `migration`, `other`), `produced_by` (str), `produced_at` (str ISO 8601), and `version` (int, initialized to `1` on first insertion).
3. WHEN the Backend agent produces a code file for a file path not yet in `code_artifacts`, THE `EngineeringState` SHALL insert a new `CodeArtifact` entry with `version` set to `1`. WHEN a file path already exists, THE `EngineeringState` SHALL overwrite the entry with `version` incremented by `1` and `produced_at` updated to the current timestamp.
4. WHEN the Frontend agent produces a code file for a file path not yet in `code_artifacts`, THE `EngineeringState` SHALL insert a new `CodeArtifact` entry with `version` set to `1`. WHEN a file path already exists, THE `EngineeringState` SHALL overwrite the entry with `version` incremented by `1` and `produced_at` updated to the current timestamp.
5. WHEN the Debug agent produces a corrected code file for a file path that already exists in `code_artifacts`, THE `EngineeringState` SHALL update the corresponding `CodeArtifact` entry, incrementing `version` by `1` and updating `produced_at` to the current timestamp.
6. THE reducer for `code_artifacts` SHALL merge incoming dict updates with the existing dict, overwriting entries with matching keys and preserving entries with non-matching keys. IF an incoming value for an existing key is `None`, THE reducer SHALL treat it as a no-op and leave the existing entry unchanged.
7. IF `code_artifacts` has not yet been populated, THE `EngineeringState` SHALL initialize it to an empty dict.

---

### Requirement 7: Test Artifacts

**User Story:** As the Reviewer and Debug agents, I want to read test plans and test files from state, so that I can assess coverage and identify failures.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `test_artifacts` field of type `Dict[str, TestArtifact]` mapping a file path key to a `TestArtifact` Pydantic model.
2. THE `TestArtifact` model SHALL contain: `file_path` (str), `test_type` (str with values: `unit`, `integration`, `regression`, `acceptance`, `e2e`), `content` (str), `coverage_target` (str), `produced_by` (str), `produced_at` (str ISO 8601), and `version` (int).
3. THE `EngineeringState` SHALL contain a `test_plan` field of type `Optional[TestPlan]` containing: `strategy` (str), `coverage_goals` (Dict[str, str]), `test_suites` (list of str), `quality_metrics` (Dict[str, Any]), `produced_by` (str), `produced_at` (str ISO 8601), and `version` (int).
4. WHEN the QA agent produces test files, THE `EngineeringState` SHALL insert or update entries in `test_artifacts` following the same insert/update/version rules as `code_artifacts` (Requirement 6, criteria 3–5).
5. THE reducer for `test_artifacts` SHALL follow the same merge-and-overwrite strategy as `code_artifacts`, including the `None`-value no-op rule.
6. IF `test_artifacts` has not yet been populated, THE `EngineeringState` SHALL initialize it to an empty dict.
7. IF `test_plan` has not yet been populated, THE `EngineeringState` SHALL initialize it to `None`.

---

### Requirement 8: Review Report

**User Story:** As the Debug and DevOps agents, I want to read review findings from state, so that I can act on identified issues before deployment.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `review_report` field of type `Optional[ReviewReport]`, initialized to `None` until the Reviewer agent writes to it.
2. THE `ReviewReport` model SHALL contain: `overall_status` (str with values: `approved`, `requires_changes`, `rejected`), `code_quality_score` (float, 0.0–10.0), `architecture_compliance` (bool), `security_findings` (list of str), `performance_findings` (list of str), `maintainability_findings` (list of str), `testing_coverage_assessment` (str), `engineering_standards_compliance` (bool), `recommendations` (list of str), `produced_by` (str), `produced_at` (str ISO 8601), and `version` (int).
3. WHEN the Reviewer agent completes its task, THE `EngineeringState` SHALL update the `review_report` field with a fully populated `ReviewReport` instance where `overall_status` is one of the three valid values and `code_quality_score` is in the range 0.0–10.0 inclusive.
4. IF `review_report` has not yet been populated, THE `EngineeringState` SHALL initialize it to `None`.

---

### Requirement 9: Deployment Artifacts

**User Story:** As the Documentation agent and system operators, I want to read deployment artifacts from state, so that I can generate operational documentation and execute deployments.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `deployment_artifacts` field of type `Dict[str, DeploymentArtifact]` mapping a file path key to a `DeploymentArtifact` Pydantic model.
2. THE `DeploymentArtifact` model SHALL contain: `file_path` (str), `artifact_type` (str with values: `dockerfile`, `docker_compose`, `ci_cd_pipeline`, `infrastructure_template`, `deployment_script`, `monitoring_config`, `other`), `content` (str), `produced_by` (str), `produced_at` (str ISO 8601), and `version` (int).
3. WHEN the DevOps agent produces a deployment artifact, THE `EngineeringState` SHALL insert or update the corresponding entry in `deployment_artifacts` following the same insert/update/version rules as `code_artifacts`.
4. THE reducer for `deployment_artifacts` SHALL follow the same merge-and-overwrite strategy as `code_artifacts`, including the `None`-value no-op rule.
5. IF `deployment_artifacts` has not yet been populated, THE `EngineeringState` SHALL initialize it to an empty dict.

---

### Requirement 10: Documentation Artifacts

**User Story:** As the Final Project agent and end users, I want to read all generated documentation from state, so that I can validate the completeness of the delivered project.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `documentation_artifacts` field of type `Dict[str, DocumentationArtifact]`.
2. THE `DocumentationArtifact` model SHALL contain: `file_path` (str), `doc_type` (str with values: `readme`, `onboarding`, `architecture`, `api_reference`, `deployment_guide`, `maintenance`, `other`), `content` (str), `produced_by` (str), `produced_at` (str ISO 8601), and `version` (int).
3. WHEN the Documentation agent produces a document, THE `EngineeringState` SHALL insert or update the corresponding entry in `documentation_artifacts` following the same insert/update/version rules as `code_artifacts`.
4. THE reducer for `documentation_artifacts` SHALL follow the same merge-and-overwrite strategy as `code_artifacts`, including the `None`-value no-op rule.
5. IF `documentation_artifacts` has not yet been populated, THE `EngineeringState` SHALL initialize it to an empty dict.

---

### Requirement 11: Workflow Status and Routing

**User Story:** As the LangGraph orchestrator, I want to read the current workflow status and routing decision from state, so that I can determine which agent to invoke next.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `workflow_status` field of type `str` with the following valid values: `initiated`, `requirements_complete`, `architecture_complete`, `planning_complete`, `backend_complete`, `frontend_complete`, `qa_complete`, `review_complete`, `debug_required`, `escalation_required`, `devops_complete`, `documentation_complete`, `deadline_exceeded`, `completed`, `failed`. The initial value SHALL be `"initiated"`. The values `escalation_required` and `deadline_exceeded` were added by the workflow revision to support review-rejection escalation routing and timeout enforcement respectively.
2. THE `EngineeringState` SHALL contain a `current_agent` field of type `str` identifying the agent currently holding execution (e.g., `"ProductManagerAgent"`).
3. THE `EngineeringState` SHALL contain a `next_agent` field of type `Optional[str]` identifying the agent that should execute next, or `None` if the workflow is complete or failed.
4. THE `EngineeringState` SHALL contain a `pipeline_stage` field of type `int`. This field is **deprecated** and SHALL NOT be incremented by agents. It is retained in state for backward compatibility only. Implementations SHALL use `forward_stage` (Requirement 21, criterion 6) for first-pass progress tracking and `total_invocations` (Requirement 21, criterion 7) for total invocation counting.
5. WHEN an agent completes successfully, THE `EngineeringState` SHALL update `workflow_status` to the corresponding completion value, set `current_agent` to the completing agent's name, and set `next_agent` to the name of the next agent in the pipeline.
6. WHEN the Debug agent determines a fix is complete, THE `EngineeringState` SHALL set `next_agent` to the value of `produced_by` on the failing artifact that triggered the debug cycle.
7. WHEN an agent encounters a failure that cannot be self-recovered, THE `EngineeringState` SHALL set `workflow_status` to `"debug_required"` and set `next_agent` to `"DebugAgent"`.
8. THE `workflow_status` field SHALL be updated by each agent as its final write operation before yielding control.

---

### Requirement 12: Error Tracking

**User Story:** As the Debug agent, I want to read structured error records from state, so that I can perform root-cause analysis and route corrective work to the appropriate agent.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain an `errors` field of type `list[ErrorRecord]`, initialized to an empty list.
2. THE `ErrorRecord` model SHALL contain: `error_id` (str UUID v4), `agent` (str), `stage` (str, one of the valid `workflow_status` values), `error_type` (str, one of: `unhandled_exception`, `validation_failure`, `timeout`, `retry_exhausted`, `other`), `message` (str), `traceback` (Optional[str]), `occurred_at` (str ISO 8601), `resolved` (bool, default `False`), `resolution_agent` (Optional[str]), and `resolution_notes` (Optional[str]).
3. WHEN any agent encounters an unhandled exception or validation failure, THE `EngineeringState` SHALL append a new `ErrorRecord` to `errors` with a unique `error_id`, `resolved` set to `False`, and `traceback` populated if a stack trace is available.
4. THE reducer for `errors` SHALL append incoming `ErrorRecord` entries to the existing list without replacing or removing existing entries.
5. WHEN the Debug agent resolves an error, THE `EngineeringState` SHALL locate the `ErrorRecord` matching the given `error_id`, set its `resolved` field to `True`, and populate `resolution_agent` and `resolution_notes`. This update SHALL be applied as a replacement of the matching record in the list.
6. IF `errors` has not yet been populated, THE `EngineeringState` SHALL initialize it to an empty list.

---

### Requirement 13: Retry and Escalation Tracking

**User Story:** As the orchestrator, I want to read retry counts and escalation status from state, so that I can enforce retry limits and trigger human approval when necessary.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `retry_counts` field of type `Dict[str, int]` mapping agent names to their current retry count, initialized to an empty dict.
2. THE `EngineeringState` SHALL contain a `max_retries` field of type `Dict[str, int]` mapping agent names to their configured maximum retry limit, initialized to an empty dict. WHEN a `max_retries` entry is not present for an agent, THE system SHALL treat the effective limit as `3`.
3. THE `EngineeringState` SHALL contain a `human_approval_required` field of type `bool`, initialized to `False`.
4. THE `EngineeringState` SHALL contain a `human_approval_notes` field of type `Optional[str]`, initialized to `None`.
5. WHEN an agent fails and is scheduled for retry, THE `EngineeringState` SHALL increment the corresponding `retry_counts` entry by `1`. WHEN no entry exists for the agent, THE `EngineeringState` SHALL initialize it to `1`.
6. WHEN an agent's `retry_counts` value is `>=` its corresponding `max_retries` value, THE `EngineeringState` SHALL set `human_approval_required` to `True` and populate `human_approval_notes` with the agent name and current retry count.
7. WHEN a human approves continuation, THE `EngineeringState` SHALL reset the relevant `retry_counts` entry to `0` and set `human_approval_required` to `False`.

---

### Requirement 14: Per-Agent Execution Metadata

**User Story:** As a system operator, I want to read per-agent execution timing, token usage, and model call metadata from state, so that I can monitor costs, performance, and throughput.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain an `execution_metadata` field of type `Dict[str, AgentExecutionMetadata]` mapping agent names to their execution metadata, initialized to an empty dict.
2. THE `AgentExecutionMetadata` model SHALL contain: `agent_name` (str), `started_at` (str ISO 8601), `completed_at` (Optional[str] ISO 8601, default `None`), `duration_seconds` (Optional[float], default `None`), `tokens_used` (int, default `0`), `model_calls` (int, default `0`), `status` (str with values: `running`, `completed`, `failed`, `skipped`), and `notes` (Optional[str], default `None`).
3. WHEN an agent begins execution, THE `EngineeringState` SHALL insert or update the corresponding entry in `execution_metadata` with `status` set to `"running"`, `started_at` set to the current ISO 8601 timestamp, `tokens_used` set to `0`, and `model_calls` set to `0`.
4. WHEN an agent completes execution successfully, THE `EngineeringState` SHALL update the corresponding `AgentExecutionMetadata` with `completed_at` set to the current timestamp, `duration_seconds` set to `completed_at - started_at` in seconds, `tokens_used` set to the total tokens consumed, `model_calls` set to the total number of model invocations, and `status` set to `"completed"`.
5. WHEN an agent fails, THE `EngineeringState` SHALL update the corresponding `AgentExecutionMetadata` with `completed_at` set to the current timestamp, `duration_seconds` computed as in criterion 4, and `status` set to `"failed"`.
6. THE reducer for `execution_metadata` SHALL merge incoming dict updates with the existing dict, following the same merge strategy as `code_artifacts`.

---

### Requirement 15: Agent Scratchpad and Short-Term Memory

**User Story:** As each agent, I want a private scratchpad field in state for in-context reasoning, so that intermediate thoughts and chain-of-thought outputs are preserved within a pipeline step without polluting shared fields.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain an `agent_scratchpad` field of type `Dict[str, Any]` storing transient, agent-specific intermediate reasoning data, initialized to an empty dict.
2. THE `agent_scratchpad` field SHALL be keyed by agent name (e.g., `"ProductManagerAgent"`).
3. THE reducer for `agent_scratchpad` SHALL merge incoming key-value pairs into the existing dict. A write by Agent A to key `"AgentA"` SHALL NOT overwrite or remove the entry for key `"AgentB"`.
4. THE `agent_scratchpad` field SHALL NOT be declared as a dependency or consumed as input in any agent's input schema other than the agent whose name matches the key.
5. WHEN `agent_scratchpad` has not yet been populated, THE `EngineeringState` SHALL initialize it to an empty dict.

---

### Requirement 16: Memory References

**User Story:** As each agent, I want to read references to long-term, vector, semantic, episodic, and organizational memory from state, so that I can retrieve relevant prior knowledge without querying external stores directly from business logic.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `memory_references` field of type `MemoryReferences` Pydantic model.
2. THE `MemoryReferences` model SHALL contain: `long_term` (list of str, referencing keys in the long-term memory store), `vector` (list of str, referencing document IDs in the vector store), `semantic` (list of str, referencing structured fact IDs), `episodic` (list of str, referencing past run IDs), and `organizational` (list of str, referencing knowledge base entry IDs). All lists SHALL default to empty.
3. WHEN any agent writes new knowledge to an external memory store, THE `EngineeringState` SHALL update the corresponding list in `memory_references` with the new reference key, provided the key is not already present in the list.
4. THE reducer for `memory_references` lists SHALL append a new reference key only if it is not already present in the existing list (deduplication enforced).
5. IF `memory_references` has not yet been populated, THE `EngineeringState` SHALL initialize all five lists to empty.

---

### Requirement 17: Checkpoint and Versioning Strategy

**User Story:** As the orchestrator, I want each field to carry a version number and the state to support checkpointing, so that I can recover and replay any pipeline stage without data loss.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `checkpoint_stage` field of type `str` identifying the last successfully completed forward pipeline stage. Valid values SHALL be the same set as the forward-progress `workflow_status` values from Requirement 11, criterion 1 (i.e., all values except `debug_required`, `escalation_required`, `deadline_exceeded`, and `failed`).
2. THE `EngineeringState` SHALL contain a `state_version` field of type `int` initialized to `0` and incremented by `1` on every state update cycle.
3. THE `EngineeringState` SHALL contain a `checkpoints` field of type `Dict[str, str]` mapping pipeline stage names to ISO 8601 timestamps of when those stages were checkpointed.
4. WHEN a pipeline stage completes successfully, THE `EngineeringState` SHALL update `checkpoint_stage` to the completed stage name and record the ISO 8601 completion timestamp in `checkpoints[stage_name]`.
5. WHEN a pipeline stage completes successfully, THE `EngineeringState` SHALL increment `state_version` by `1`.
6. ALL mutable Pydantic model fields within `EngineeringState` SHALL carry a `version` field of type `int` that is incremented each time the model is updated.
7. THE `EngineeringState` SHALL be compatible with LangGraph's `MemorySaver` and `SqliteSaver` checkpointing backends, requiring all field types to be JSON-serializable (no `datetime` objects, no sets, no non-string keys).
8. WHEN restoring from a checkpoint, THE `EngineeringState` SHALL produce an object graph where every field value, `version` number, and timestamp is identical to the saved snapshot. IF restoration fails, THE system SHALL raise a `RuntimeError` identifying the checkpoint stage and the field that could not be restored.
9. THE `checkpoints` dict MAY contain entries with keys that are not in the `workflow_status` valid values set. Specifically, entries of the form `"debug_{n}_start"` and `"debug_{n}_complete"` SHALL be written by DebugAgent on each invocation for forensic history. These entries SHALL NOT be used for recovery resumption and SHALL NOT update the `checkpoint_stage` scalar field.

---

### Requirement 18: State Reducers

**User Story:** As the LangGraph framework, I want each `EngineeringState` field to define a compatible reducer, so that partial state updates from agents are merged correctly without overwriting unrelated fields.

#### Acceptance Criteria

1. THE `EngineeringState` TypedDict SHALL declare each field using `Annotated[T, reducer_fn]` syntax compatible with LangGraph's state merge protocol.
2. THE `EngineeringState` SHALL define a `replace_reducer` for scalar fields that replaces the previous value entirely on update (e.g., `workflow_status`, `current_agent`, `next_agent`, `forward_stage`, `total_invocations`, `state_version`).
3. THE `EngineeringState` SHALL define a `dict_merge_reducer` for dict fields that merges incoming key-value pairs into the existing dict without removing existing keys. IF the incoming update is `None`, THE `dict_merge_reducer` SHALL return the existing dict unchanged.
4. THE `EngineeringState` SHALL define an `append_reducer` for list fields where new entries must be accumulated without replacement (e.g., `errors`).
5. THE `EngineeringState` SHALL define a `replace_reducer` for complex Pydantic model fields that represent a complete authoritative document and must be replaced as a whole (e.g., `requirements`, `architecture`, `task_plan`, `review_report`, `test_plan`).
6. ALL reducers SHALL be pure functions with no side effects.
7. IF any reducer receives `None` as its incoming update value, THE reducer SHALL return the current state value unchanged.

---

### Requirement 19: Pydantic Model Compliance

**User Story:** As a developer integrating with `EngineeringState`, I want all nested data structures to be defined as Pydantic v2 models, so that I get automatic validation, serialization, and IDE support.

#### Acceptance Criteria

1. ALL structured sub-objects within `EngineeringState` (`ProductRequirements`, `SystemArchitecture`, `TaskPlan`, `ImplementationTask`, `CodeArtifact`, `TestArtifact`, `TestPlan`, `ReviewReport`, `DeploymentArtifact`, `DocumentationArtifact`, `ErrorRecord`, `AgentExecutionMetadata`, `MemoryReferences`, `FinalProjectOutput`) SHALL be defined as Pydantic v2 `BaseModel` subclasses.
2. ALL Pydantic models SHALL use `model_config = ConfigDict(frozen=False, validate_assignment=True)` to allow updates while still enforcing type validation on assignment.
3. ALL optional fields in Pydantic models SHALL be declared as `Optional[T]` with a default of `None`.
4. ALL list fields in Pydantic models SHALL declare a default factory of `list`.
5. ALL dict fields in Pydantic models SHALL declare a default factory of `dict`.
6. THE `EngineeringState` top-level structure SHALL be defined as a `TypedDict` (not a Pydantic model) for compatibility with LangGraph's state protocol.
7. WHEN a Pydantic model field receives an invalid value, THE validation layer SHALL raise a `ValidationError` whose message identifies at minimum: the model name, the field name, and the received value type.
8. WHEN `model.model_dump()` is called on any Pydantic model in `EngineeringState`, THE result SHALL be a JSON-serializable dict (no non-serializable types). IF serialization fails, THE model SHALL raise a `ValueError` identifying the non-serializable field.

---

### Requirement 20: Final Project Output

**User Story:** As the end user, I want to read a consolidated final project summary from state after all agents have completed, so that I can access the complete delivered artifact set in one place.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `final_output` field of type `Optional[FinalProjectOutput]`, initialized to `None`.
2. THE `FinalProjectOutput` model SHALL contain: `project_name` (str), `summary` (str), `total_files_generated` (int, equal to `len(code_artifact_paths) + len(test_artifact_paths) + len(deployment_artifact_paths) + len(documentation_artifact_paths)`), `code_artifact_paths` (list of str), `test_artifact_paths` (list of str), `deployment_artifact_paths` (list of str), `documentation_artifact_paths` (list of str), `overall_review_status` (str with values: `approved`, `requires_changes`, `rejected`, `not_reviewed`), `pipeline_duration_seconds` (float), `produced_by` (str), `produced_at` (str ISO 8601).
3. WHEN the Final Project agent completes its task, THE `EngineeringState` SHALL update the `final_output` field with a `FinalProjectOutput` instance where: `project_name` is non-empty, `total_files_generated` equals the sum of all four path list lengths, `overall_review_status` is one of the four valid values, and `produced_at` is a valid ISO 8601 datetime.
4. IF `final_output` has not yet been populated, THE `EngineeringState` SHALL initialize it to `None`.
5. THE `final_output` field SHALL only be written by `FinalProjectAgent`. Access control is enforced structurally by the LangGraph graph topology: the `final_project` node is only reachable via the `documentation → final_project` edge, which fires exclusively when `workflow_status == "documentation_complete"`. No field-level `ValueError` guard is required or permitted.

---

### Requirement 21: Workflow Control Fields

**User Story:** As the LangGraph orchestrator, I want structured fields for global cycle limiting, timeout enforcement, scoped debug targeting, and accurate progress tracking, so that the pipeline has guaranteed termination and deterministic failure modes.

> **Note:** This requirement was introduced by the workflow design revision (see `workflow-design-revised.md` §3.0) to address ISSUE-14, ISSUE-15, ISSUE-17, and ISSUE-13 from the architecture review. All fields in this requirement are new additions to `EngineeringState` that do not replace or modify any field defined in Requirements 1–20.

#### Acceptance Criteria

1. THE `EngineeringState` SHALL contain a `total_debug_cycles` field of type `int`, initialized to `0`. This field SHALL be incremented by `1` on every DebugAgent invocation and SHALL NEVER be reset by any agent, human approval action, or checkpoint recovery operation.
2. THE `EngineeringState` SHALL contain a `pipeline_started_at` field of type `str` (ISO 8601 datetime), set once at pipeline initialization. This field SHALL be treated as write-once immutable after initialization. IF any process attempts to overwrite `pipeline_started_at` after initial assignment, THE `EngineeringState` SHALL raise a `ValueError` and reject the write.
3. THE `EngineeringState` SHALL contain a `pipeline_deadline_at` field of type `Optional[str]` (ISO 8601 datetime), initialized to `None`. WHEN `pipeline_deadline_at` is set and `utcnow() > pipeline_deadline_at`, THE orchestrator SHALL set `workflow_status = "deadline_exceeded"` and route to the `deadline_enforcer` node. IF `pipeline_deadline_at` is `None`, no deadline is enforced.
4. THE `EngineeringState` SHALL contain a `current_debug_target` field of type `Optional[str]` (UUID v4 referencing an `error_id` in the `errors` field), initialized to `None`. WHEN a failure routes to DebugAgent, THE triggering agent SHALL write the `error_id` of the specific failing error to `current_debug_target`. WHEN DebugAgent resolves the targeted error, it SHALL reset `current_debug_target` to `None`.
5. THE `EngineeringState` SHALL contain a `rejection_scope` field of type `Optional[str]` with valid values `code_level`, `design_level`, `requirements_level`, initialized to `None`. WHEN ReviewerAgent sets `review_report.overall_status = "rejected"`, it SHALL also set `rejection_scope` to the appropriate value. WHEN the pipeline restarts from a human-approved rejection, THE `HumanApprovalNode` SHALL use `rejection_scope` to determine the correct re-entry node: `code_level` → `DebugAgent`, `design_level` → `ArchitectAgent`, `requirements_level` → `ProductManagerAgent`.
6. THE `EngineeringState` SHALL contain a `forward_stage` field of type `int`, initialized to `0`. This field SHALL be incremented by `1` only on confirmed first-pass forward pipeline progress — i.e., when `workflow_status` transitions to a new completion value for the first time. It SHALL NOT be incremented on debug re-entries or re-reviews. Its maximum value SHALL be capped at the number of agent nodes in the pipeline (11).
7. THE `EngineeringState` SHALL contain a `total_invocations` field of type `int`, initialized to `0`. This field SHALL be incremented by `1` on every agent node invocation, including debug re-entries, re-reviews, and upstream restarts. It is never capped or reset.
8. THE `EngineeringState` SHALL contain a `max_debug_cycles` field of type `int`, initialized from `configs/workflow.yaml` at pipeline startup with a default value of `10`. This field SHALL be treated as write-once after initialization. WHEN `total_debug_cycles >= max_debug_cycles`, THE router SHALL set `workflow_status = "failed"` and route to `END` regardless of `human_approval_required` status. This is the only termination path that cannot be bypassed by human approval.
9. The `pipeline_stage` field defined in Requirement 11, criterion 4 is **deprecated**. Implementations SHALL use `forward_stage` (criterion 6 above) for first-pass progress tracking and `total_invocations` (criterion 7 above) for total invocation counting. `pipeline_stage` remains in state for backward compatibility and SHALL be initialized to `0` but SHALL NOT be incremented by agents.
