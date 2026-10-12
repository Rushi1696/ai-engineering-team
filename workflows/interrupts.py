"""
workflows/interrupts.py

HumanApprovalNode implementation for LangGraph human-in-the-loop pause and resume.

Source of truth:
  - requirements.md: Requirement 13 (Retry and Escalation Tracking),
    Requirement 21 criterion 5 (rejection_scope post-approval routing).
  - design.md / workflow-design-revised.md: Section 3.6.2 & Section 3.6.3 (Reviewer rejection paths),
    Section 3.8 (Human Approval Checkpoint), Section 3.10.4 (HumanApprovalNode Resolution Actions).
"""

from datetime import datetime, timezone
from typing import Any, Dict

from langgraph.types import interrupt

from state.engineering_state import EngineeringState
from workflows.constants import (
    APPROVAL_DECISION_APPROVED,
    APPROVAL_DECISION_REJECTED,
    REJECTION_SCOPE_TO_AGENT,
    STATUS_ARCHITECTURE_COMPLETE,
    STATUS_DEBUG_REQUIRED,
    STATUS_ESCALATION_REQUIRED,
    STATUS_FAILED,
    STATUS_INITIATED,
)


def human_approval_node(state: EngineeringState) -> Dict[str, Any]:
    """Execute the human_approval infrastructure node.

    Pauses graph execution via LangGraph native `interrupt()` surfacing state context.
    Upon receiving a resume command (e.g., via Command(resume=...)):
      - On APPROVAL_DECISION_APPROVED ("approved"): Resets human_approval_required to False,
        resets retry count for target agent (if rejected-review) or current_agent (if retry-exhaustion),
        and sets next_agent & workflow_status based on rejection_scope ONLY
        if workflow_status == STATUS_ESCALATION_REQUIRED and review_report.overall_status == "rejected".
        Otherwise, preserves the pre-interrupt next_agent and status.
      - On APPROVAL_DECISION_REJECTED ("rejected"): Sets workflow_status to STATUS_FAILED
        and human_approval_required to False.
      - Any other decision raises ValueError.

    Returns partial state dictionary update without mutating incoming state in place.
    """
    # JSON-serialize review_report if present as a Pydantic model
    review_report = state.get("review_report")
    serialized_review_report = None
    if review_report is not None:
        if hasattr(review_report, "model_dump"):
            serialized_review_report = review_report.model_dump()
        elif isinstance(review_report, dict):
            serialized_review_report = review_report

    # JSON-serialize error records if present
    errors = state.get("errors", [])
    serialized_errors = []
    for err in errors:
        if hasattr(err, "model_dump"):
            serialized_errors.append(err.model_dump())
        elif isinstance(err, dict):
            serialized_errors.append(err)

    interrupt_payload = {
        "run_id": state.get("run_id"),
        "human_approval_notes": state.get("human_approval_notes"),
        "rejection_scope": state.get("rejection_scope"),
        "workflow_status": state.get("workflow_status"),
        "current_agent": state.get("current_agent"),
        "next_agent": state.get("next_agent"),
        "total_debug_cycles": state.get("total_debug_cycles"),
        "errors": serialized_errors,
        "review_report": serialized_review_report,
        "retry_counts": state.get("retry_counts", {}),
    }

    resume_input = interrupt(interrupt_payload)

    decision = None
    notes = None
    if isinstance(resume_input, dict):
        decision = resume_input.get("decision") or resume_input.get("approval_decision")
        notes = resume_input.get("notes") or resume_input.get("human_approval_notes")
    elif isinstance(resume_input, str):
        decision = resume_input

    if decision not in (APPROVAL_DECISION_APPROVED, APPROVAL_DECISION_REJECTED):
        raise ValueError(
            f"Invalid human approval decision: {decision!r}. "
            f"Must be {APPROVAL_DECISION_APPROVED!r} or {APPROVAL_DECISION_REJECTED!r}."
        )

    now_iso = datetime.now(timezone.utc).isoformat()

    if decision == APPROVAL_DECISION_REJECTED:
        return {
            "workflow_status": STATUS_FAILED,
            "human_approval_required": False,
            "human_approval_notes": notes or state.get("human_approval_notes"),
            "updated_at": now_iso,
        }

    # Extract review_report overall_status to determine if this is a rejected-review escalation
    review_overall_status = None
    if review_report is not None:
        if isinstance(review_report, dict):
            review_overall_status = review_report.get("overall_status")
        else:
            review_overall_status = getattr(review_report, "overall_status", None)

    workflow_status = state.get("workflow_status")
    is_rejected_review_escalation = (
        workflow_status == STATUS_ESCALATION_REQUIRED
        and review_overall_status == "rejected"
    )

    rejection_scope = state.get("rejection_scope")
    target_next_agent = state.get("next_agent")
    target_status = state.get("workflow_status")

    retry_counts = dict(state.get("retry_counts", {}))

    if is_rejected_review_escalation:
        if rejection_scope not in REJECTION_SCOPE_TO_AGENT:
            raise ValueError(
                f"Rejected review escalation requires valid rejection_scope in "
                f"{sorted(REJECTION_SCOPE_TO_AGENT.keys())}, got {rejection_scope!r}"
            )

        target_next_agent = REJECTION_SCOPE_TO_AGENT[rejection_scope]
        if rejection_scope == "code_level":
            # Note: rejected + code_level -> debug_required is an implementation choice
            # for routing code-level review rejections to DebugAgent.
            target_status = STATUS_DEBUG_REQUIRED
        elif rejection_scope == "design_level":
            # workflow-design-revised.md §3.6.2: workflow_status = "architecture_complete"
            target_status = STATUS_ARCHITECTURE_COMPLETE
        elif rejection_scope == "requirements_level":
            # workflow-design-revised.md §3.6.3: workflow_status = "initiated"
            target_status = STATUS_INITIATED

        # §3.6.2 / §3.10.4: For rejected-review escalations, reset retry_counts for the TARGET agent
        if target_next_agent and target_next_agent in retry_counts:
            retry_counts[target_next_agent] = 0
    else:
        # §3.10.4 & Requirement 13: For retry-exhaustion escalations, reset retry_counts for current_agent
        escalating_agent = state.get("current_agent")
        if escalating_agent and escalating_agent in retry_counts:
            retry_counts[escalating_agent] = 0

    # Note: rejection_scope is never cleared in state because None is a no-op under
    # replace_reducer (Requirement 18 criterion 7). The combination gate:
    # (workflow_status == STATUS_ESCALATION_REQUIRED and review_overall_status == "rejected")
    # is the sole mechanism that prevents stale rejection_scope values from misrouting
    # subsequent retry-exhaustion escalations.
    return {
        "human_approval_required": False,
        "human_approval_notes": notes or state.get("human_approval_notes"),
        "next_agent": target_next_agent,
        "workflow_status": target_status,
        "retry_counts": retry_counts,
        "updated_at": now_iso,
    }
