"""
workflows/constants.py

Node names, status string constants, routing maps, approval constants, node ordering,
and drift-guard assertions for LangGraph workflows.

Source of truth: design.md Section 3.2, workflow-design-revised.md Section 3.1 & 3.4,
requirements.md Requirement 11, Requirement 13, & Requirement 21.
"""

from typing import Dict, Final, Tuple

from state.validators import (
    DEBUG_AGENT_NEXT_AGENT_VALUES,
    REJECTION_SCOPE_VALUES,
    WORKFLOW_STATUS_VALUES,
)

# ---------------------------------------------------------------------------
# Node Names (14 nodes total: 11 agent nodes + 3 infrastructure nodes)
# ---------------------------------------------------------------------------
NODE_PRODUCT_MANAGER: Final[str] = "product_manager"
NODE_ARCHITECT: Final[str] = "architect"
NODE_TASK_PLANNER: Final[str] = "task_planner"
NODE_BACKEND: Final[str] = "backend"
NODE_FRONTEND: Final[str] = "frontend"
NODE_QA: Final[str] = "qa"
NODE_DEBUG: Final[str] = "debug"
NODE_REVIEWER: Final[str] = "reviewer"
NODE_DEVOPS: Final[str] = "devops"
NODE_DOCUMENTATION: Final[str] = "documentation"
NODE_FINAL_PROJECT: Final[str] = "final_project"

NODE_HUMAN_APPROVAL: Final[str] = "human_approval"
NODE_DEADLINE_ENFORCER: Final[str] = "deadline_enforcer"
NODE_GLOBAL_CAP_HANDLER: Final[str] = "global_cap_handler"

# ---------------------------------------------------------------------------
# Canonical Node Sequence & Enums
# ---------------------------------------------------------------------------
AGENT_NODE_ORDER: Final[Tuple[str, ...]] = (
    NODE_PRODUCT_MANAGER,
    NODE_ARCHITECT,
    NODE_TASK_PLANNER,
    NODE_BACKEND,
    NODE_FRONTEND,
    NODE_QA,
    NODE_DEBUG,
    NODE_REVIEWER,
    NODE_DEVOPS,
    NODE_DOCUMENTATION,
    NODE_FINAL_PROJECT,
)

ALL_NODES: Final[Tuple[str, ...]] = (
    *AGENT_NODE_ORDER,
    NODE_HUMAN_APPROVAL,
    NODE_DEADLINE_ENFORCER,
    NODE_GLOBAL_CAP_HANDLER,
)

# ---------------------------------------------------------------------------
# Human Approval Decision Constants (Requirement 13)
# ---------------------------------------------------------------------------
APPROVAL_DECISION_APPROVED: Final[str] = "approved"
APPROVAL_DECISION_REJECTED: Final[str] = "rejected"

# ---------------------------------------------------------------------------
# Workflow Status Constants (Requirement 11 criterion 1)
# ---------------------------------------------------------------------------
STATUS_INITIATED: Final[str] = "initiated"
STATUS_REQUIREMENTS_COMPLETE: Final[str] = "requirements_complete"
STATUS_ARCHITECTURE_COMPLETE: Final[str] = "architecture_complete"
STATUS_PLANNING_COMPLETE: Final[str] = "planning_complete"
STATUS_BACKEND_COMPLETE: Final[str] = "backend_complete"
STATUS_FRONTEND_COMPLETE: Final[str] = "frontend_complete"
STATUS_QA_COMPLETE: Final[str] = "qa_complete"
STATUS_REVIEW_COMPLETE: Final[str] = "review_complete"
STATUS_DEBUG_REQUIRED: Final[str] = "debug_required"
STATUS_ESCALATION_REQUIRED: Final[str] = "escalation_required"
STATUS_DEVOPS_COMPLETE: Final[str] = "devops_complete"
STATUS_DOCUMENTATION_COMPLETE: Final[str] = "documentation_complete"
STATUS_DEADLINE_EXCEEDED: Final[str] = "deadline_exceeded"
STATUS_COMPLETED: Final[str] = "completed"
STATUS_FAILED: Final[str] = "failed"

# ---------------------------------------------------------------------------
# Rejection Scope to Agent Mapping (Requirement 21 criterion 5)
# ---------------------------------------------------------------------------
REJECTION_SCOPE_TO_AGENT: Final[Dict[str, str]] = {
    "code_level": "DebugAgent",
    "design_level": "ArchitectAgent",
    "requirements_level": "ProductManagerAgent",
}

# ---------------------------------------------------------------------------
# Drift-guard Assertions against state/validators.py
# Uses explicit ImportError checks so Python -O optimization cannot strip them
# ---------------------------------------------------------------------------
_ALL_LOCAL_STATUSES = {
    STATUS_INITIATED,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_ARCHITECTURE_COMPLETE,
    STATUS_PLANNING_COMPLETE,
    STATUS_BACKEND_COMPLETE,
    STATUS_FRONTEND_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REVIEW_COMPLETE,
    STATUS_DEBUG_REQUIRED,
    STATUS_ESCALATION_REQUIRED,
    STATUS_DEVOPS_COMPLETE,
    STATUS_DOCUMENTATION_COMPLETE,
    STATUS_DEADLINE_EXCEEDED,
    STATUS_COMPLETED,
    STATUS_FAILED,
}

if _ALL_LOCAL_STATUSES != WORKFLOW_STATUS_VALUES:
    raise ImportError(
        f"Status constants in workflows/constants.py drift from state/validators.py: "
        f"{_ALL_LOCAL_STATUSES ^ WORKFLOW_STATUS_VALUES}"
    )

_EXPECTED_REJECTION_SCOPES = {"code_level", "design_level", "requirements_level"}
if REJECTION_SCOPE_VALUES != _EXPECTED_REJECTION_SCOPES:
    raise ImportError(
        f"REJECTION_SCOPE_VALUES in state/validators.py drifts from expected set: "
        f"{REJECTION_SCOPE_VALUES ^ _EXPECTED_REJECTION_SCOPES}"
    )

if set(REJECTION_SCOPE_TO_AGENT.keys()) != REJECTION_SCOPE_VALUES:
    raise ImportError(
        f"REJECTION_SCOPE_TO_AGENT keys drift from state/validators.py::REJECTION_SCOPE_VALUES: "
        f"{set(REJECTION_SCOPE_TO_AGENT.keys()) ^ REJECTION_SCOPE_VALUES}"
    )

# ---------------------------------------------------------------------------
# Mapping of all 11 Agent Class Names -> Graph Node Names
# Used by HumanApprovalNode post-approval resume routing (Requirement 21 criterion 5)
# ---------------------------------------------------------------------------
AGENT_NAME_TO_NODE: Final[Dict[str, str]] = {
    "ProductManagerAgent": NODE_PRODUCT_MANAGER,
    "ArchitectAgent": NODE_ARCHITECT,
    "TaskPlannerAgent": NODE_TASK_PLANNER,
    "BackendAgent": NODE_BACKEND,
    "FrontendAgent": NODE_FRONTEND,
    "QAAgent": NODE_QA,
    "DebugAgent": NODE_DEBUG,
    "ReviewerAgent": NODE_REVIEWER,
    "DevOpsAgent": NODE_DEVOPS,
    "DocumentationAgent": NODE_DOCUMENTATION,
    "FinalProjectAgent": NODE_FINAL_PROJECT,
}

# ---------------------------------------------------------------------------
# Mapping of DebugAgent's 7 Permitted next_agent Values -> Graph Node Names
# (state/validators.py::DEBUG_AGENT_NEXT_AGENT_VALUES)
# ---------------------------------------------------------------------------
NEXT_AGENT_TO_NODE: Final[Dict[str, str]] = {
    "ProductManagerAgent": NODE_PRODUCT_MANAGER,
    "ArchitectAgent": NODE_ARCHITECT,
    "BackendAgent": NODE_BACKEND,
    "FrontendAgent": NODE_FRONTEND,
    "QAAgent": NODE_QA,
    "ReviewerAgent": NODE_REVIEWER,
    "DevOpsAgent": NODE_DEVOPS,
}

if set(NEXT_AGENT_TO_NODE.keys()) != DEBUG_AGENT_NEXT_AGENT_VALUES:
    raise ImportError(
        f"NEXT_AGENT_TO_NODE keys drift from state/validators.py::DEBUG_AGENT_NEXT_AGENT_VALUES: "
        f"{set(NEXT_AGENT_TO_NODE.keys()) ^ DEBUG_AGENT_NEXT_AGENT_VALUES}"
    )
