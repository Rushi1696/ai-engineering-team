"""
workflows/routers.py

Pure router functions for LangGraph workflow conditional edges.

Precedence order enforced across all routers (workflow-design-revised.md §3.4):
  P1: Deadline check (workflow_status == "deadline_exceeded" -> deadline_enforcer)
  P2: Global debug cycle cap (total_debug_cycles >= max_debug_cycles -> global_cap_handler)
  P3: Human approval hold (human_approval_required is True -> human_approval)
  P4: Normal stage completion & next_agent routing

Source of truth: workflow-design-revised.md Section 3.4 & Section 3.7.
"""

from datetime import datetime, timezone

from langgraph.graph import END

from state.engineering_state import EngineeringState
from workflows.constants import (
    AGENT_NAME_TO_NODE,
    NEXT_AGENT_TO_NODE,
    NODE_ARCHITECT,
    NODE_BACKEND,
    NODE_DEADLINE_ENFORCER,
    NODE_DEVOPS,
    NODE_DOCUMENTATION,
    NODE_FINAL_PROJECT,
    NODE_FRONTEND,
    NODE_GLOBAL_CAP_HANDLER,
    NODE_HUMAN_APPROVAL,
    NODE_PRODUCT_MANAGER,
    NODE_QA,
    NODE_REVIEWER,
    NODE_TASK_PLANNER,
    NODE_DEBUG,
    STATUS_ARCHITECTURE_COMPLETE,
    STATUS_BACKEND_COMPLETE,
    STATUS_DEADLINE_EXCEEDED,
    STATUS_DEVOPS_COMPLETE,
    STATUS_DOCUMENTATION_COMPLETE,
    STATUS_DEBUG_REQUIRED,
    STATUS_ESCALATION_REQUIRED,
    STATUS_FAILED,
    STATUS_FRONTEND_COMPLETE,
    STATUS_PLANNING_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
)


def parse_iso_utc(value: str) -> datetime:
    """Parse an ISO 8601 datetime string, treating trailing 'Z' and timezone-naive values as UTC.

    Raises:
        ValueError: if value is not a valid ISO 8601 datetime string.
    """
    if not isinstance(value, str):
        raise ValueError(f"Invalid timestamp type: expected str, got {type(value).__name__}")

    cleaned = value.strip()
    if cleaned.endswith("Z") or cleaned.endswith("z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(cleaned)
    except Exception as exc:
        raise ValueError(f"Malformed ISO 8601 timestamp '{value}': {exc}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _check_common_precedence(state: EngineeringState) -> str | None:
    """Evaluate Priority 1, 2, and 3 routing precedence common to all agent routers.

    P1: Deadline check (workflow_status == "deadline_exceeded" OR now_utc > pipeline_deadline_at -> deadline_enforcer)
    P2: Global debug cycle cap (total_debug_cycles >= max_debug_cycles -> global_cap_handler)
    P3: Human approval hold (human_approval_required is True -> human_approval)

    Returns target node name if a high-precedence condition matches, else None to signal P4 fallback.
    """
    deadline = state.get("pipeline_deadline_at")
    deadline_exceeded = False
    if deadline is not None:
        deadline_dt = parse_iso_utc(deadline)
        if datetime.now(timezone.utc) > deadline_dt:
            deadline_exceeded = True

    if state.get("workflow_status") == STATUS_DEADLINE_EXCEEDED or deadline_exceeded:
        return NODE_DEADLINE_ENFORCER

    total_debug_cycles = state.get("total_debug_cycles", 0)
    max_debug_cycles = state.get("max_debug_cycles", 10)
    if total_debug_cycles >= max_debug_cycles:
        return NODE_GLOBAL_CAP_HANDLER

    if state.get("human_approval_required") is True:
        return NODE_HUMAN_APPROVAL

    return None


def router_after_product_manager(state: EngineeringState) -> str:
    """Route execution after ProductManagerAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_REQUIREMENTS_COMPLETE:
        return NODE_ARCHITECT
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after ProductManagerAgent: {status!r}")


def router_after_architect(state: EngineeringState) -> str:
    """Route execution after ArchitectAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_ARCHITECTURE_COMPLETE:
        return NODE_TASK_PLANNER
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after ArchitectAgent: {status!r}")


def router_after_task_planner(state: EngineeringState) -> str:
    """Route execution after TaskPlannerAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_PLANNING_COMPLETE:
        return NODE_BACKEND
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after TaskPlannerAgent: {status!r}")


def router_after_backend(state: EngineeringState) -> str:
    """Route execution after BackendAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_BACKEND_COMPLETE:
        return NODE_FRONTEND
    if status == STATUS_DEBUG_REQUIRED:
        return NODE_DEBUG
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after BackendAgent: {status!r}")


def router_after_frontend(state: EngineeringState) -> str:
    """Route execution after FrontendAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_FRONTEND_COMPLETE:
        return NODE_QA
    if status == STATUS_DEBUG_REQUIRED:
        return NODE_DEBUG
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after FrontendAgent: {status!r}")


def router_after_qa(state: EngineeringState) -> str:
    """Route execution after QAAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_QA_COMPLETE:
        return NODE_REVIEWER
    if status == STATUS_DEBUG_REQUIRED:
        return NODE_DEBUG
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after QAAgent: {status!r}")


def router_after_reviewer(state: EngineeringState) -> str:
    """Route execution after ReviewerAgent node.

    Per workflow-design-revised.md §3.4:
    - workflow_status == "review_complete" AND review_report.overall_status == "approved" -> devops
    - workflow_status == "debug_required" AND review_report.overall_status == "requires_changes" -> debug
    - workflow_status == "escalation_required" AND review_report.overall_status == "rejected" -> human_approval
    - workflow_status == "failed" -> END
    """
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_FAILED:
        return END

    review_report = state.get("review_report")
    overall_status: str | None = None
    if review_report is not None:
        if isinstance(review_report, dict):
            overall_status = review_report.get("overall_status")
        else:
            overall_status = getattr(review_report, "overall_status", None)

    if status == STATUS_REVIEW_COMPLETE and overall_status == "approved":
        return NODE_DEVOPS
    if status == STATUS_DEBUG_REQUIRED and overall_status == "requires_changes":
        return NODE_DEBUG
    if status == STATUS_ESCALATION_REQUIRED and overall_status == "rejected":
        return NODE_HUMAN_APPROVAL

    raise RuntimeError(
        f"Unhandled status/review_report combination after ReviewerAgent: "
        f"status={status!r}, overall_status={overall_status!r}"
    )


def router_after_devops(state: EngineeringState) -> str:
    """Route execution after DevOpsAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_DEVOPS_COMPLETE:
        return NODE_DOCUMENTATION
    if status == STATUS_DEBUG_REQUIRED:
        return NODE_DEBUG
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after DevOpsAgent: {status!r}")


def router_after_documentation(state: EngineeringState) -> str:
    """Route execution after DocumentationAgent node."""
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_DOCUMENTATION_COMPLETE:
        return NODE_FINAL_PROJECT
    if status == STATUS_FAILED:
        return END

    raise RuntimeError(f"Unhandled workflow_status after DocumentationAgent: {status!r}")


def router_after_debug(state: EngineeringState) -> str:
    """Route execution after DebugAgent node.

    Uses NEXT_AGENT_TO_NODE to map DebugAgent's next_agent write
    to the 7 permitted target nodes.
    """
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_FAILED:
        return END

    next_agent = state.get("next_agent")
    if next_agent in NEXT_AGENT_TO_NODE:
        return NEXT_AGENT_TO_NODE[next_agent]

    raise RuntimeError(f"Invalid or unmapped next_agent after DebugAgent: {next_agent!r}")


def router_after_human_approval(state: EngineeringState) -> str:
    """Route execution after HumanApprovalNode completes.

    Enforces P1 -> P2 -> P3 -> P4 precedence.
    P4 uses AGENT_NAME_TO_NODE to resolve post-approval resume target node
    from state['next_agent'], covering all 11 agent names (Requirement 21 criterion 5).
    """
    common = _check_common_precedence(state)
    if common is not None:
        return common

    status = state.get("workflow_status")
    if status == STATUS_FAILED:
        return END

    next_agent = state.get("next_agent")
    if next_agent in AGENT_NAME_TO_NODE:
        return AGENT_NAME_TO_NODE[next_agent]

    raise RuntimeError(
        f"Invalid or unmapped next_agent after HumanApprovalNode: {next_agent!r}"
    )


__all__ = [
    "parse_iso_utc",
    "router_after_product_manager",
    "router_after_architect",
    "router_after_task_planner",
    "router_after_backend",
    "router_after_frontend",
    "router_after_qa",
    "router_after_reviewer",
    "router_after_devops",
    "router_after_documentation",
    "router_after_debug",
    "router_after_human_approval",
]
