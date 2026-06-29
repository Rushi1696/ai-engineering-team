"""
state/validators.py

Layer 2 (state-transition) validation for EngineeringState fields.

Source of truth: requirements.md Requirement 1 (write-once identity
fields), Requirement 2 (user_input write-once), Requirement 11
(workflow_status enum), Requirement 17 (checkpoint_stage enum,
checkpoints forensic-key exemption), Requirement 21 (pipeline_started_at
and max_debug_cycles write-once), design.md Section 3.4 (DebugAgent
next_agent allow-list).

Design notes (see Phase 1 Technical Design Document, Section 4):
  - This module implements ONLY state-transition validity: is this write
    permitted given the CURRENT STATE. It does not duplicate Pydantic
    field-level validation (Layer 1), which lives entirely in
    state/models.py and runs automatically on model construction /
    assignment.
  - Validation here runs BEFORE the corresponding reducer is invoked
    (Technical Design Document Section 3.2, Section 9.3). This module does
    not import from state/reducers.py and has no knowledge of merge
    semantics -- it only decides whether a proposed write is permitted.
  - The write-once guard is intentionally implemented as a precondition
    check here, separate from replace_reducer in reducers.py, to keep
    every reducer a pure (old, new) -> merged function with no need to
    "know" about prior-write history (Requirement 18 criterion 6).
  - The `checkpoints` dict (Requirement 17 criterion 9) receives NO key-
    format validation whatsoever. Forensic keys of the form
    "debug_{n}_start" / "debug_{n}_complete" are explicitly permitted and
    are not validated against, or confused with, the checkpoint_stage
    enum. See validate_checkpoints_write() below.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

# ---------------------------------------------------------------------------
# Requirement 11 criterion 1: workflow_status valid values (15 values)
# ---------------------------------------------------------------------------

WORKFLOW_STATUS_VALUES: FrozenSet[str] = frozenset(
    {
        "initiated",
        "requirements_complete",
        "architecture_complete",
        "planning_complete",
        "backend_complete",
        "frontend_complete",
        "qa_complete",
        "review_complete",
        "debug_required",
        "escalation_required",
        "devops_complete",
        "documentation_complete",
        "deadline_exceeded",
        "completed",
        "failed",
    }
)

# ---------------------------------------------------------------------------
# Requirement 17 criterion 1: checkpoint_stage valid values.
#
# Derived from WORKFLOW_STATUS_VALUES minus the non-forward-progress
# values, rather than hand-maintained as a second literal list. This
# guarantees the two enums cannot silently drift apart if
# WORKFLOW_STATUS_VALUES is ever revised under a future approved change
# (Technical Design Document Section 4.4).
# ---------------------------------------------------------------------------

_NON_FORWARD_PROGRESS_STATUSES: FrozenSet[str] = frozenset(
    {"debug_required", "escalation_required", "deadline_exceeded", "failed"}
)

CHECKPOINT_STAGE_VALUES: FrozenSet[str] = (
    WORKFLOW_STATUS_VALUES - _NON_FORWARD_PROGRESS_STATUSES
)

# ---------------------------------------------------------------------------
# Requirement 21 criterion 5: rejection_scope valid values
# ---------------------------------------------------------------------------

REJECTION_SCOPE_VALUES: FrozenSet[str] = frozenset(
    {"code_level", "design_level", "requirements_level"}
)

# ---------------------------------------------------------------------------
# design.md Section 3.4 / workflow-design-revised.md Section 3.4:
# DebugAgent next_agent allow-list (7 permitted values)
# ---------------------------------------------------------------------------

DEBUG_AGENT_NEXT_AGENT_VALUES: FrozenSet[str] = frozenset(
    {
        "BackendAgent",
        "FrontendAgent",
        "QAAgent",
        "ReviewerAgent",
        "DevOpsAgent",
        "ArchitectAgent",
        "ProductManagerAgent",
    }
)

# ---------------------------------------------------------------------------
# Write-once fields (Requirement 1 criterion 7, Requirement 2 criterion 3,
# Requirement 21 criteria 2 and 8). project_name is included for
# consistency with its write-once annotation in design.md's State
# Ownership Matrix, though Requirement 1 does not pair it with an explicit
# ValueError clause (Technical Design Document Section 4.3).
# ---------------------------------------------------------------------------

WRITE_ONCE_FIELDS: FrozenSet[str] = frozenset(
    {
        "run_id",
        "session_id",
        "created_at",
        "user_input",
        "pipeline_started_at",
        "max_debug_cycles",
        "project_name",
    }
)


class WriteOnceViolationError(ValueError):
    """Raised when a write-once field is written to after its initial
    assignment. Subclasses ValueError so existing `except ValueError`
    handling in calling code still works, while allowing callers that want
    to distinguish this specific failure mode to catch it explicitly.
    """

    def __init__(self, field_name: str, existing_value: Any) -> None:
        self.field_name = field_name
        self.existing_value = existing_value
        super().__init__(
            f"Write-once violation: field '{field_name}' has already been "
            f"assigned (current value: {existing_value!r}) and cannot be "
            f"overwritten."
        )


class InvalidEnumValueError(ValueError):
    """Raised when a field is written with a value outside its permitted
    enum set.
    """

    def __init__(self, field_name: str, value: Any, valid_values: FrozenSet[str]) -> None:
        self.field_name = field_name
        self.value = value
        self.valid_values = valid_values
        super().__init__(
            f"Invalid value for '{field_name}': {value!r}. "
            f"Must be one of: {sorted(valid_values)}"
        )


class InvalidRoutingTargetError(ValueError):
    """Raised when DebugAgent attempts to set next_agent to a value
    outside the seven permitted targets.
    """

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__(
            f"Invalid DebugAgent next_agent value: {value!r}. "
            f"Must be one of: {sorted(DEBUG_AGENT_NEXT_AGENT_VALUES)}"
        )


# ---------------------------------------------------------------------------
# Write-once guard
# ---------------------------------------------------------------------------


def check_write_once(
    field_name: str, existing_value: Any, incoming_value: Any
) -> None:
    """Raise WriteOnceViolationError if `field_name` is a write-once field
    that already holds a non-None value and a new write is being
    attempted.

    This function does not perform the write itself and does not invoke
    any reducer -- it is a precondition check only, intended to be called
    by the state-update dispatch layer (Technical Design Document Section
    3.2) before the corresponding reducer runs. If the field is not in
    WRITE_ONCE_FIELDS, this function is a no-op regardless of arguments.

    A write of the SAME value as the existing value is still rejected --
    "write-once" means exactly one write is permitted, not "writes are
    permitted as long as the value doesn't change." This matches
    Requirement 1 criterion 7's "reject the write without modifying
    state" language, which does not carve out an idempotent-write
    exception.
    """
    if field_name not in WRITE_ONCE_FIELDS:
        return
    if existing_value is not None:
        raise WriteOnceViolationError(field_name, existing_value)
    # incoming_value is intentionally unused beyond triggering the check;
    # the guard's job is solely to decide permission, not to inspect the
    # new value's shape (that is Layer 1's job).
    del incoming_value


# ---------------------------------------------------------------------------
# Enum validators
# ---------------------------------------------------------------------------


def validate_workflow_status(value: Optional[str]) -> None:
    """Raise InvalidEnumValueError if `value` is not a valid
    workflow_status (Requirement 11 criterion 1). A None value is
    permitted to pass through here -- absence-of-write is handled by
    replace_reducer's None-is-no-op behavior, not by this validator.
    """
    if value is None:
        return
    if value not in WORKFLOW_STATUS_VALUES:
        raise InvalidEnumValueError("workflow_status", value, WORKFLOW_STATUS_VALUES)


def validate_checkpoint_stage(value: Optional[str]) -> None:
    """Raise InvalidEnumValueError if `value` is not a valid
    checkpoint_stage (Requirement 17 criterion 1 -- the forward-progress
    subset of workflow_status values, excluding debug_required,
    escalation_required, deadline_exceeded, and failed).
    """
    if value is None:
        return
    if value not in CHECKPOINT_STAGE_VALUES:
        raise InvalidEnumValueError(
            "checkpoint_stage", value, CHECKPOINT_STAGE_VALUES
        )


def validate_rejection_scope(value: Optional[str]) -> None:
    """Raise InvalidEnumValueError if `value` is not a valid
    rejection_scope (Requirement 21 criterion 5). None is permitted (it is
    the field's default / non-rejection state).
    """
    if value is None:
        return
    if value not in REJECTION_SCOPE_VALUES:
        raise InvalidEnumValueError("rejection_scope", value, REJECTION_SCOPE_VALUES)


def validate_debug_agent_next_agent(
    current_agent: Optional[str], next_agent_value: Optional[str]
) -> None:
    """Raise InvalidRoutingTargetError if the agent currently writing
    state is DebugAgent and it is attempting to set next_agent to a value
    outside the seven permitted targets (design.md Section 3.4,
    workflow-design-revised.md Section 3.4).

    This check is scoped specifically to writes where current_agent ==
    "DebugAgent" -- it does not constrain next_agent values written by any
    other agent, since each of the other ten agents has its own fixed (or
    binary-branching) set of valid successors defined by the approved
    router tables, which is a Phase 10 (routing) concern, not a Phase 1
    (state) concern. Only DebugAgent's allow-list is enforced at the state
    layer, per the explicit "any other value is a validation error"
    language in workflow-design-revised.md Section 3.4.

    next_agent_value of None is rejected here as invalid for DebugAgent
    specifically, since DebugAgent must always route somewhere (it is
    never the terminal node) -- the seven permitted values do not include
    None.
    """
    if current_agent != "DebugAgent":
        return
    if next_agent_value not in DEBUG_AGENT_NEXT_AGENT_VALUES:
        raise InvalidRoutingTargetError(next_agent_value)


# ---------------------------------------------------------------------------
# Requirement 17 criterion 9: checkpoints forensic-key exemption
# ---------------------------------------------------------------------------


def validate_checkpoints_write(incoming: Optional[Dict[str, str]]) -> None:
    """Intentional no-op validator for the `checkpoints` field.

    Requirement 17 criterion 9 permits `checkpoints` to contain keys such
    as "debug_{n}_start" / "debug_{n}_complete" that are NOT members of
    the workflow_status / checkpoint_stage value sets, written by
    DebugAgent for forensic history. These entries must not be used for
    recovery resumption and must not update the checkpoint_stage scalar
    field -- but they must also not be REJECTED by any key-format
    validator, since they are explicitly permitted.

    This function exists (rather than simply omitting validation
    entirely) to make that design decision explicit and discoverable: the
    `checkpoints` field intentionally receives no key-format validation
    at the state layer. The constraint that only DebugAgent writes
    "debug_{n}_*" keys and only the ten forward-progress-completing agents
    write checkpoint_stage-aligned keys is an AGENT-LEVEL contract, not a
    state-layer schema constraint (Technical Design Document Section 4.5).

    Always returns without raising, for any input.
    """
    del incoming  # intentionally unvalidated; see docstring
    return


__all__ = [
    "WORKFLOW_STATUS_VALUES",
    "CHECKPOINT_STAGE_VALUES",
    "REJECTION_SCOPE_VALUES",
    "DEBUG_AGENT_NEXT_AGENT_VALUES",
    "WRITE_ONCE_FIELDS",
    "WriteOnceViolationError",
    "InvalidEnumValueError",
    "InvalidRoutingTargetError",
    "check_write_once",
    "validate_workflow_status",
    "validate_checkpoint_stage",
    "validate_rejection_scope",
    "validate_debug_agent_next_agent",
    "validate_checkpoints_write",
]