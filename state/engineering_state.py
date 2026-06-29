"""
state/engineering_state.py

The EngineeringState TypedDict: the central shared LangGraph state object
that flows through the entire agent pipeline.

Source of truth: requirements.md (Requirements 1-21, full field manifest),
design.md Section 2 (State Ownership Matrix), workflow-design-revised.md
Section 3.0 (new fields introduced by the workflow revision).

Design notes (see Phase 1 Technical Design Document, Sections 1-2, 6-8):
  - EngineeringState is declared as a TypedDict, NOT a Pydantic BaseModel,
    per Requirement 19 criterion 6 -- required for compatibility with
    LangGraph's state-merge protocol.
  - Every field is wrapped in Annotated[T, reducer_fn] per Requirement 18
    criterion 1. There are no bare (un-annotated) fields.
  - All 40 fields are present (see NOTE ON FIELD COUNT below the field
    manifest for why this is 40, not the "41" figure stated in upstream
    documents). pipeline_stage (field 20) is retained for
    backward compatibility ONLY -- it is initialized to 0 and this module
    contains no logic that increments it anywhere (Requirement 11
    criterion 4, Requirement 21 criterion 9).
  - final_output carries NO field-level guard tied to workflow_status.
    Access control is enforced by LangGraph graph topology (a Phase 10
    concern), per Requirement 20 criterion 5. Nothing in this file
    performs that check, and nothing should be added here later.
  - All field types are JSON-serializable (str, int, float, bool, list,
    dict, Optional/list/dict compositions, and the Pydantic models from
    state/models.py, which are themselves JSON-serializable by
    construction) -- required for LangGraph MemorySaver / SqliteSaver /
    PostgresSaver checkpoint compatibility (Requirement 17 criterion 7).
  - This module also provides create_initial_state(), the single factory
    function responsible for producing a correctly-initialized
    EngineeringState at pipeline start (Requirement 1, Requirement 2,
    Requirement 21 criteria 1-2, 6-8). No agent should hand-construct an
    initial state dict directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from .models import (
    CodeArtifact,
    DeploymentArtifact,
    DocumentationArtifact,
    ErrorRecord,
    FinalProjectOutput,
    MemoryReferences,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TaskPlan,
    TestArtifact,
    TestPlan,
    AgentExecutionMetadata,
)
from .reducers import (
    append_reducer,
    dict_merge_reducer,
    memory_references_reducer,
    replace_reducer,
)

# ---------------------------------------------------------------------------
# EngineeringState: all 40 fields (see NOTE ON FIELD COUNT further below),
# grouped per Technical Design Document
# Section 1.2. Grouping is for documentation/readability only -- TypedDict
# itself is flat and grouping has no runtime effect.
# ---------------------------------------------------------------------------


class EngineeringState(TypedDict):
    # -- Identity & Run Metadata (Requirement 1) -- 5 fields -------------
    run_id: Annotated[str, replace_reducer]
    session_id: Annotated[str, replace_reducer]
    project_name: Annotated[str, replace_reducer]
    created_at: Annotated[str, replace_reducer]
    updated_at: Annotated[str, replace_reducer]

    # -- User Input (Requirement 2) -- 2 fields ---------------------------
    user_input: Annotated[str, replace_reducer]
    user_context: Annotated[Dict[str, Any], dict_merge_reducer]

    # -- Agent Document Outputs (Requirements 3, 4, 5, 7, 8) -- 5 fields --
    requirements: Annotated[Optional[ProductRequirements], replace_reducer]
    architecture: Annotated[Optional[SystemArchitecture], replace_reducer]
    task_plan: Annotated[Optional[TaskPlan], replace_reducer]
    test_plan: Annotated[Optional[TestPlan], replace_reducer]
    review_report: Annotated[Optional[ReviewReport], replace_reducer]

    # -- Agent Artifact Collections (Requirements 6, 7, 9, 10) -- 4 fields
    code_artifacts: Annotated[Dict[str, CodeArtifact], dict_merge_reducer]
    test_artifacts: Annotated[Dict[str, TestArtifact], dict_merge_reducer]
    deployment_artifacts: Annotated[Dict[str, DeploymentArtifact], dict_merge_reducer]
    documentation_artifacts: Annotated[
        Dict[str, DocumentationArtifact], dict_merge_reducer
    ]

    # -- Workflow Status & Routing (Requirement 11) -- 4 fields ----------
    workflow_status: Annotated[str, replace_reducer]
    current_agent: Annotated[str, replace_reducer]
    next_agent: Annotated[Optional[str], replace_reducer]
    pipeline_stage: Annotated[int, replace_reducer]  # DEPRECATED -- never incremented

    # -- Error Tracking (Requirement 12) -- 1 field -----------------------
    errors: Annotated[List[ErrorRecord], append_reducer]

    # -- Retry & Escalation (Requirement 13) -- 4 fields ------------------
    retry_counts: Annotated[Dict[str, int], dict_merge_reducer]
    max_retries: Annotated[Dict[str, int], dict_merge_reducer]
    human_approval_required: Annotated[bool, replace_reducer]
    human_approval_notes: Annotated[Optional[str], replace_reducer]

    # -- Execution & Memory Infrastructure (Requirements 14, 15, 16) -----
    execution_metadata: Annotated[
        Dict[str, AgentExecutionMetadata], dict_merge_reducer
    ]
    agent_scratchpad: Annotated[Dict[str, Any], dict_merge_reducer]
    memory_references: Annotated[MemoryReferences, memory_references_reducer]

    # -- Checkpoint & Versioning (Requirement 17) -- 3 fields -------------
    checkpoint_stage: Annotated[str, replace_reducer]
    state_version: Annotated[int, replace_reducer]
    checkpoints: Annotated[Dict[str, str], dict_merge_reducer]

    # -- Final Output (Requirement 20) -- 1 field --------------------------
    final_output: Annotated[Optional[FinalProjectOutput], replace_reducer]

    # -- Workflow Control Fields (Requirement 21) -- 8 fields --------------
    total_debug_cycles: Annotated[int, replace_reducer]  # never reset
    pipeline_started_at: Annotated[str, replace_reducer]  # write-once
    pipeline_deadline_at: Annotated[Optional[str], replace_reducer]
    current_debug_target: Annotated[Optional[str], replace_reducer]
    rejection_scope: Annotated[Optional[str], replace_reducer]
    forward_stage: Annotated[int, replace_reducer]  # capped at 11
    total_invocations: Annotated[int, replace_reducer]  # uncapped
    max_debug_cycles: Annotated[int, replace_reducer]  # write-once


# ---------------------------------------------------------------------------
# Field manifest constant -- used by tests and by the initial-state factory
# to assert exactly 40 fields are present (see NOTE ON FIELD COUNT below).
# via TypedDict introspection only) so the count is independently visible
# and reviewable as a literal list matching the Technical Design Document
# Section 2 field table.
# ---------------------------------------------------------------------------

ENGINEERING_STATE_FIELDS: tuple = (
    "run_id",
    "session_id",
    "project_name",
    "created_at",
    "updated_at",
    "user_input",
    "user_context",
    "requirements",
    "architecture",
    "task_plan",
    "test_plan",
    "review_report",
    "code_artifacts",
    "test_artifacts",
    "deployment_artifacts",
    "documentation_artifacts",
    "workflow_status",
    "current_agent",
    "next_agent",
    "pipeline_stage",
    "errors",
    "retry_counts",
    "max_retries",
    "human_approval_required",
    "human_approval_notes",
    "execution_metadata",
    "agent_scratchpad",
    "memory_references",
    "checkpoint_stage",
    "state_version",
    "checkpoints",
    "final_output",
    "total_debug_cycles",
    "pipeline_started_at",
    "pipeline_deadline_at",
    "current_debug_target",
    "rejection_scope",
    "forward_stage",
    "total_invocations",
    "max_debug_cycles",
)

# NOTE ON FIELD COUNT: the approved documents (final-sync-validation-report.md,
# the Phase 1 Technical Design Document) state the field count as 41
# ("33 original + 8 new"). An exhaustive enumeration of every field named
# across requirements.md Req 1-21 and design.md's State Ownership Matrix
# -- performed independently here and cross-checked against the Technical
# Design Document's own Section 1.2 group table and Section 2 field table
# -- yields exactly 40 unique field names, not 41. Both the 40-row group
# breakdown and the 40-row field table in the Technical Design Document
# already reflect this; "41" in that document's prose was a count error,
# not a 41st field this implementation is omitting. No field has been
# invented or omitted here to force a particular count. This discrepancy
# is upstream of this file and should be confirmed/corrected in the
# architecture documents rather than resolved by adding a fabricated
# field to this module.
assert len(ENGINEERING_STATE_FIELDS) == 40, (
    f"ENGINEERING_STATE_FIELDS must contain exactly 40 entries, "
    f"found {len(ENGINEERING_STATE_FIELDS)}"
)
assert len(set(ENGINEERING_STATE_FIELDS)) == 40, (
    "ENGINEERING_STATE_FIELDS contains duplicate field names"
)
assert set(ENGINEERING_STATE_FIELDS) == set(EngineeringState.__annotations__.keys()), (
    "ENGINEERING_STATE_FIELDS does not match EngineeringState's declared "
    "TypedDict fields -- these must stay in sync"
)


# ---------------------------------------------------------------------------
# Default max_debug_cycles (Requirement 21 criterion 8). The approved
# default is 10; the actual operative value at pipeline start is sourced
# from configs/workflow.yaml (a Phase 0 concern) and passed into
# create_initial_state() by the caller -- this module does not read config
# files directly (state/ has zero upward dependencies per design.md
# Section 6.2).
# ---------------------------------------------------------------------------

DEFAULT_MAX_DEBUG_CYCLES: int = 10


def create_initial_state(
    *,
    project_name: str,
    user_input: str,
    session_id: Optional[str] = None,
    user_context: Optional[Dict[str, Any]] = None,
    pipeline_deadline_at: Optional[str] = None,
    max_debug_cycles: int = DEFAULT_MAX_DEBUG_CYCLES,
) -> EngineeringState:
    """Construct a correctly-initialized EngineeringState at pipeline
    start.

    This is the SOLE sanctioned way to construct an initial state. Agents
    and orchestration code should not hand-build an initial state dict
    directly, since doing so risks omitting one of the 40 fields or
    mis-initializing a write-once field (Technical Design Document
    Section 8.1-8.2).

    Parameters correspond directly to the requirements that govern
    pipeline initialization:
      - project_name: Requirement 1 criterion 3 (must be non-empty).
      - user_input: Requirement 2 criterion 1 (1-100,000 characters) and
        criterion 5 (must not be empty or whitespace-only).
      - session_id: Requirement 1 criterion 2. If not supplied, a new
        UUID v4 is generated -- the requirement specifies session_id
        groups related runs, but does not mandate the caller always have
        one in hand at first invocation.
      - user_context: Requirement 2 criterion 2 and criterion 4 (defaults
        to empty dict when not provided).
      - pipeline_deadline_at: Requirement 21 criterion 3 (None means no
        deadline is enforced).
      - max_debug_cycles: Requirement 21 criterion 8 (write-once after
        this point; sourced from configs/workflow.yaml by the caller,
        defaulting to 10 if the caller does not override it).

    Raises:
        ValueError: if project_name is empty, or if user_input is empty or
            consists entirely of whitespace (Requirement 2 criterion 5), or
            if user_input length is outside the 1-100,000 character bound
            (Requirement 2 criterion 1).
    """
    if not project_name:
        raise ValueError(
            "create_initial_state: project_name must be non-empty "
            "(Requirement 1 criterion 3)"
        )

    if len(user_input) == 0 or user_input.strip() == "":
        raise ValueError(
            "create_initial_state: user_input must not be empty or "
            "whitespace-only (Requirement 2 criterion 5)"
        )
    if not (1 <= len(user_input) <= 100_000):
        raise ValueError(
            "create_initial_state: user_input must be between 1 and "
            "100,000 characters inclusive (Requirement 2 criterion 1); "
            f"received length {len(user_input)}"
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid.uuid4())
    resolved_session_id = session_id if session_id is not None else str(uuid.uuid4())

    state: EngineeringState = {
        # Identity & Run Metadata
        "run_id": run_id,
        "session_id": resolved_session_id,
        "project_name": project_name,
        "created_at": now_iso,
        "updated_at": now_iso,
        # User Input
        "user_input": user_input,
        "user_context": dict(user_context) if user_context is not None else {},
        # Agent Document Outputs
        "requirements": None,
        "architecture": None,
        "task_plan": None,
        "test_plan": None,
        "review_report": None,
        # Agent Artifact Collections
        "code_artifacts": {},
        "test_artifacts": {},
        "deployment_artifacts": {},
        "documentation_artifacts": {},
        # Workflow Status & Routing
        "workflow_status": "initiated",
        "current_agent": "",
        "next_agent": None,
        "pipeline_stage": 0,  # deprecated; never incremented
        # Error Tracking
        "errors": [],
        # Retry & Escalation
        "retry_counts": {},
        "max_retries": {},
        "human_approval_required": False,
        "human_approval_notes": None,
        # Execution & Memory Infrastructure
        "execution_metadata": {},
        "agent_scratchpad": {},
        "memory_references": MemoryReferences(),
        # Checkpoint & Versioning
        "checkpoint_stage": "initiated",
        "state_version": 0,
        "checkpoints": {},
        # Final Output
        "final_output": None,
        # Workflow Control Fields (Requirement 21)
        "total_debug_cycles": 0,
        "pipeline_started_at": now_iso,
        "pipeline_deadline_at": pipeline_deadline_at,
        "current_debug_target": None,
        "rejection_scope": None,
        "forward_stage": 0,
        "total_invocations": 0,
        "max_debug_cycles": max_debug_cycles,
    }

    return state


__all__ = [
    "EngineeringState",
    "ENGINEERING_STATE_FIELDS",
    "DEFAULT_MAX_DEBUG_CYCLES",
    "create_initial_state",
]