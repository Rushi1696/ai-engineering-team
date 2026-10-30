"""
state/reducers.py

Reducer functions for EngineeringState fields, registered via
Annotated[T, reducer_fn] in state/engineering_state.py.

Source of truth: requirements.md Requirement 18 (State Reducers),
design.md Section 2 (State Ownership Matrix, "Reducer Type" column).

Design notes (see Phase 1 Technical Design Document, Section 3):
  - Three base reducer types per Requirement 18: replace_reducer,
    dict_merge_reducer, append_reducer.
  - A fourth, field-specific reducer (memory_references_reducer) is
    introduced solely to satisfy the dedup-append requirement on
    MemoryReferences (Requirement 16 criteria 3-4). It is scoped to the
    memory_references field only.
  - ALL reducers in this module are pure functions: no I/O, no mutation of
    input arguments, no reliance on external state such as timestamps or
    randomness (Requirement 18 criterion 6).
  - Write-once enforcement is intentionally NOT implemented inside any
    reducer in this file. It is implemented in state/validators.py and
    runs as a precondition check at the state-update dispatch boundary,
    BEFORE the reducer is invoked. This keeps every reducer here a pure
    (old, new) -> merged function, per Requirement 18 criterion 6 and
    Technical Design Document Section 3.2.
  - The version-increment-on-collision behavior described in
    requirements.md (e.g. Requirement 6 criterion 3) is the responsibility
    of the PRODUCING AGENT, not the reducer. dict_merge_reducer here is a
    generic structural merge; it has no field-specific knowledge of what
    "version" means. See Technical Design Document Section 3.4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypeVar

from .models import ErrorRecord, MemoryReferences

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Requirement 18 criterion 2: replace_reducer
# ---------------------------------------------------------------------------


def replace_reducer(existing: Optional[T], incoming: Optional[T]) -> Optional[T]:
    """Scalar / whole-value replacement reducer.

    The incoming value entirely replaces the existing value. If incoming is
    None, the existing value is returned unchanged (Requirement 18
    criterion 7).

    Applies to: workflow_status, current_agent, next_agent, pipeline_stage,
    forward_stage, total_invocations, state_version, checkpoint_stage,
    requirements, architecture, task_plan, test_plan, review_report,
    final_output, total_debug_cycles, current_debug_target,
    rejection_scope, human_approval_required, human_approval_notes,
    pipeline_deadline_at, updated_at, run_id, session_id, project_name,
    created_at, user_input, pipeline_started_at, max_debug_cycles.

    Write-once enforcement for the subset of these fields that require it
    (run_id, session_id, created_at, user_input, pipeline_started_at,
    max_debug_cycles, project_name) is NOT performed here -- see
    state/validators.py and Technical Design Document Section 3.2.
    """
    if incoming is None:
        return existing
    return incoming


# ---------------------------------------------------------------------------
# Requirement 18 criterion 3: dict_merge_reducer
# ---------------------------------------------------------------------------


def dict_merge_reducer(
    existing: Optional[Dict[str, Any]], incoming: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Dict-merge reducer.

    Incoming dict's keys are merged into the existing dict. Matching keys
    overwrite; non-matching keys are preserved.

    Two distinct null-handling rules apply (Technical Design Document
    Section 3.3):
      1. If the INCOMING DICT ITSELF is None, the existing dict is
         returned entirely unchanged (Requirement 18 criterion 3).
      2. If the incoming dict is provided but a SPECIFIC VALUE within it is
         None, that key is treated as a no-op -- the existing entry for
         that key (if any) is left unchanged rather than being overwritten
         with None (Requirement 6 criterion 6, applied identically to
         test_artifacts, deployment_artifacts, documentation_artifacts per
         Requirement 7 criterion 5, Requirement 9 criterion 4, Requirement
         10 criterion 4).

    Applies to: user_context, code_artifacts, test_artifacts,
    deployment_artifacts, documentation_artifacts, retry_counts,
    max_retries, execution_metadata, agent_scratchpad, checkpoints.
    """
    if existing is None:
        existing = {}
    if incoming is None:
        return dict(existing)

    result = dict(existing)
    for key, value in incoming.items():
        if value is None:
            continue
        result[key] = value
    return result


# ---------------------------------------------------------------------------
# Requirement 18 criterion 4: append_reducer
# ---------------------------------------------------------------------------


def append_reducer(
    existing: Optional[List[Any]], incoming: Optional[List[Any]]
) -> List[Any]:
    """Append-only list reducer.

    New entries are accumulated without replacement of existing entries.
    If incoming is None, the existing list is returned unchanged
    (Requirement 18 criterion 7).

    Applies to: errors.

    NOTE on errors specifically: this reducer handles the "append a new
    ErrorRecord" write path (Requirement 12 criterion 4) only. Resolving
    an existing ErrorRecord (Requirement 12 criterion 5) is a logically
    distinct "replace in place" operation and is NOT performed via this
    reducer. See resolve_error_record() below and Technical Design
    Document Section 9.4 for the rationale.
    """
    if existing is None:
        existing = []
    if incoming is None:
        return list(existing)
    return list(existing) + list(incoming)


# ---------------------------------------------------------------------------
# errors field: resolution helper (distinct from append_reducer)
# ---------------------------------------------------------------------------


def resolve_error_record(
    existing: Optional[List[ErrorRecord]], resolved_record: ErrorRecord
) -> List[ErrorRecord]:
    """Apply a resolution update to a single existing ErrorRecord.

    Requirement 12 criterion 5 requires that resolving an error be applied
    as a REPLACEMENT of the matching record in the list (matched by
    error_id), not as an additional appended entry. This is a distinct
    operation from append_reducer's add-only semantics, and is exposed as
    its own explicit function rather than overloading append_reducer or
    silently special-casing the errors reducer (see Technical Design
    Document Section 9.4 -- this is the implementation's chosen resolution
    of the documented append-only-reducer vs. replace-in-place tension).

    This function is pure: it does not mutate `existing` and returns a new
    list. It is the agent's (DebugAgent's) responsibility to call this
    helper -- rather than the generic errors reducer -- when constructing
    a resolution write.

    If no record in `existing` matches resolved_record.error_id, the
    resolved_record is appended (defensive fallback; in correct operation
    this should not occur, since resolution always targets a previously
    appended record).
    """
    if existing is None:
        existing = []

    result: List[ErrorRecord] = []
    matched = False
    for record in existing:
        if record.error_id == resolved_record.error_id:
            result.append(resolved_record)
            matched = True
        else:
            result.append(record)

    if not matched:
        result.append(resolved_record)

    return result


def unresolved_errors(errors: Optional[List[ErrorRecord]]) -> List[ErrorRecord]:
    """Return all currently unresolved ErrorRecord entries from errors.

    Because the errors field uses append_reducer, resolving an error appends
    a new record with the same error_id and resolved=True. This helper keeps
    the LAST record seen for each error_id and returns only those where
    resolved is False.
    """
    if not errors:
        return []
    latest_by_id: dict[str, ErrorRecord] = {}
    for err in errors:
        latest_by_id[err.error_id] = err
    return [err for err in latest_by_id.values() if not err.resolved]


# ---------------------------------------------------------------------------
# Requirement 16 criteria 3-4: memory_references_reducer (composite,
# field-specific reducer for the memory_references field only)
# ---------------------------------------------------------------------------


def _dedup_append(existing_list: List[str], incoming_list: List[str]) -> List[str]:
    """Append entries from incoming_list to existing_list, skipping any
    entry already present in existing_list. Pure function; does not
    mutate either input list.
    """
    result = list(existing_list)
    for key in incoming_list:
        if key not in result:
            result.append(key)
    return result


def memory_references_reducer(
    existing: Optional[MemoryReferences], incoming: Optional[MemoryReferences]
) -> MemoryReferences:
    """Composite reducer for the memory_references field.

    Unlike the generic append_reducer, each of the five inner lists
    (long_term, vector, semantic, episodic, organizational) is merged with
    deduplication: a new reference key is appended only if it is not
    already present in the existing list (Requirement 16 criterion 4).

    If incoming is None, existing is returned unchanged (Requirement 18
    criterion 7, applied to this field-specific reducer for consistency).
    If existing is None, an empty MemoryReferences is used as the base.
    """
    if existing is None:
        existing = MemoryReferences()
    if incoming is None:
        return existing

    return MemoryReferences(
        long_term=_dedup_append(existing.long_term, incoming.long_term),
        vector=_dedup_append(existing.vector, incoming.vector),
        semantic=_dedup_append(existing.semantic, incoming.semantic),
        episodic=_dedup_append(existing.episodic, incoming.episodic),
        organizational=_dedup_append(existing.organizational, incoming.organizational),
    )


__all__ = [
    "replace_reducer",
    "dict_merge_reducer",
    "append_reducer",
    "resolve_error_record",
    "unresolved_errors",
    "memory_references_reducer",
]