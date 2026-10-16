"""
tests/workflows/test_routers.py

Unit and behavioral tests for workflow router functions in workflows/routers.py.
"""

from typing import Any, Dict
import pytest
from langgraph.graph import END

from state.models import ReviewReport
from workflows.constants import (
    AGENT_NAME_TO_NODE,
    AGENT_NODE_ORDER,
    ALL_NODES,
    APPROVAL_DECISION_APPROVED,
    APPROVAL_DECISION_REJECTED,
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
    STATUS_DEVOPS_COMPLETE,
    STATUS_DOCUMENTATION_COMPLETE,
    STATUS_FRONTEND_COMPLETE,
    STATUS_PLANNING_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
    STATUS_DEBUG_REQUIRED,
    STATUS_ESCALATION_REQUIRED,
)
from workflows.routers import (
    router_after_architect,
    router_after_backend,
    router_after_debug,
    router_after_devops,
    router_after_documentation,
    router_after_frontend,
    router_after_human_approval,
    router_after_product_manager,
    router_after_qa,
    router_after_reviewer,
    router_after_task_planner,
)

ALL_ROUTER_FUNCTIONS = [
    router_after_product_manager,
    router_after_architect,
    router_after_task_planner,
    router_after_backend,
    router_after_frontend,
    router_after_qa,
    router_after_reviewer,
    router_after_devops,
    router_after_documentation,
    router_after_debug,
    router_after_human_approval,
]


def test_agent_node_order_and_all_nodes():
    """Verify AGENT_NODE_ORDER and ALL_NODES contents and counts."""
    assert len(AGENT_NODE_ORDER) == 11
    assert len(ALL_NODES) == 14
    assert set(AGENT_NODE_ORDER).issubset(set(ALL_NODES))
    assert NODE_HUMAN_APPROVAL in ALL_NODES
    assert NODE_DEADLINE_ENFORCER in ALL_NODES
    assert NODE_GLOBAL_CAP_HANDLER in ALL_NODES


def test_approval_decision_constants():
    """Verify approval decision constants."""
    assert APPROVAL_DECISION_APPROVED == "approved"
    assert APPROVAL_DECISION_REJECTED == "rejected"


def test_agent_name_to_node_covers_all_eleven_agents():
    """Verify AGENT_NAME_TO_NODE has exactly 11 mappings."""
    assert len(AGENT_NAME_TO_NODE) == 11
    expected_agents = {
        "ProductManagerAgent",
        "ArchitectAgent",
        "TaskPlannerAgent",
        "BackendAgent",
        "FrontendAgent",
        "QAAgent",
        "DebugAgent",
        "ReviewerAgent",
        "DevOpsAgent",
        "DocumentationAgent",
        "FinalProjectAgent",
    }
    assert set(AGENT_NAME_TO_NODE.keys()) == expected_agents


def test_next_agent_to_node_covers_seven_permitted_debug_targets():
    """Verify NEXT_AGENT_TO_NODE has exactly 7 mappings for DebugAgent permitted targets."""
    assert len(NEXT_AGENT_TO_NODE) == 7
    expected_debug_targets = {
        "ProductManagerAgent",
        "ArchitectAgent",
        "BackendAgent",
        "FrontendAgent",
        "QAAgent",
        "ReviewerAgent",
        "DevOpsAgent",
    }
    assert set(NEXT_AGENT_TO_NODE.keys()) == expected_debug_targets


@pytest.mark.parametrize(
    "router_fn, normal_status, expected_node",
    [
        (router_after_product_manager, STATUS_REQUIREMENTS_COMPLETE, NODE_ARCHITECT),
        (router_after_architect, STATUS_ARCHITECTURE_COMPLETE, NODE_TASK_PLANNER),
        (router_after_task_planner, STATUS_PLANNING_COMPLETE, NODE_BACKEND),
        (router_after_backend, STATUS_BACKEND_COMPLETE, NODE_FRONTEND),
        (router_after_frontend, STATUS_FRONTEND_COMPLETE, NODE_QA),
        (router_after_qa, STATUS_QA_COMPLETE, NODE_REVIEWER),
        (router_after_devops, STATUS_DEVOPS_COMPLETE, NODE_DOCUMENTATION),
        (router_after_documentation, STATUS_DOCUMENTATION_COMPLETE, NODE_FINAL_PROJECT),
    ],
)
def test_normal_forward_routing(router_fn, normal_status: str, expected_node: str):
    state = {"workflow_status": normal_status}
    assert router_fn(state) == expected_node


def test_router_after_reviewer_valid_combinations():
    """Verify router_after_reviewer checks BOTH workflow_status AND review_report.overall_status."""
    # 1. Approved -> devops
    report_approved = ReviewReport(
        overall_status="approved",
        code_quality_score=9.0,
        architecture_compliance=True,
        security_findings=[],
        performance_findings=[],
        maintainability_findings=[],
        testing_coverage_assessment="Good",
        engineering_standards_compliance=True,
        recommendations=[],
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    state_approved = {
        "workflow_status": STATUS_REVIEW_COMPLETE,
        "review_report": report_approved,
    }
    assert router_after_reviewer(state_approved) == NODE_DEVOPS

    # 2. Requires changes -> debug
    report_changes = ReviewReport(
        overall_status="requires_changes",
        code_quality_score=5.0,
        architecture_compliance=True,
        security_findings=[],
        performance_findings=[],
        maintainability_findings=[],
        testing_coverage_assessment="Moderate",
        engineering_standards_compliance=True,
        recommendations=["Fix formatting"],
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    state_changes = {
        "workflow_status": STATUS_DEBUG_REQUIRED,
        "review_report": report_changes,
    }
    assert router_after_reviewer(state_changes) == NODE_DEBUG

    # 3. Rejected -> human_approval
    report_rejected = ReviewReport(
        overall_status="rejected",
        code_quality_score=2.0,
        architecture_compliance=False,
        security_findings=["Major vulnerability"],
        performance_findings=[],
        maintainability_findings=[],
        testing_coverage_assessment="Poor",
        engineering_standards_compliance=False,
        recommendations=["Redesign system"],
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    state_rejected = {
        "workflow_status": STATUS_ESCALATION_REQUIRED,
        "review_report": report_rejected,
    }
    assert router_after_reviewer(state_rejected) == NODE_HUMAN_APPROVAL


def test_router_after_reviewer_mismatched_status_raises_error():
    """Verify router_after_reviewer FAILS if workflow_status == review_complete but overall_status != approved.

    Under old logic (workflow_status alone), status == 'review_complete' would incorrectly route to devops.
    Now it must raise RuntimeError.
    """
    report_rejected = ReviewReport(
        overall_status="rejected",
        code_quality_score=2.0,
        architecture_compliance=False,
        security_findings=[],
        performance_findings=[],
        maintainability_findings=[],
        testing_coverage_assessment="Poor",
        engineering_standards_compliance=False,
        recommendations=[],
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    # Mis-matched state: status claims review_complete, but report says rejected
    mismatched_state = {
        "workflow_status": STATUS_REVIEW_COMPLETE,
        "review_report": report_rejected,
    }
    with pytest.raises(RuntimeError, match="Unhandled status/review_report combination"):
        router_after_reviewer(mismatched_state)

    # Missing review_report entirely
    missing_report_state = {
        "workflow_status": STATUS_REVIEW_COMPLETE,
        "review_report": None,
    }
    with pytest.raises(RuntimeError, match="Unhandled status/review_report combination"):
        router_after_reviewer(missing_report_state)


@pytest.mark.parametrize("router_fn", ALL_ROUTER_FUNCTIONS)
def test_precedence_p1_deadline_exceeded(router_fn):
    """P1: deadline_exceeded takes top priority over all other fields."""
    state = {
        "workflow_status": "deadline_exceeded",
        "total_debug_cycles": 99,
        "max_debug_cycles": 10,
        "human_approval_required": True,
        "next_agent": "BackendAgent",
    }
    assert router_fn(state) == NODE_DEADLINE_ENFORCER


@pytest.mark.parametrize("router_fn", ALL_ROUTER_FUNCTIONS)
@pytest.mark.parametrize(
    "expired_deadline",
    [
        "2020-01-01T00:00:00Z",
        "2020-01-01T00:00:00+00:00",
        "2020-01-01T00:00:00",  # timezone-naive, treated as UTC
    ],
)
def test_precedence_p1_expired_deadline_all_routers(router_fn, expired_deadline):
    """P1: expired pipeline_deadline_at routes to deadline_enforcer for all 11 routers."""
    state = {
        "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
        "pipeline_deadline_at": expired_deadline,
        "next_agent": "ProductManagerAgent",
    }
    assert router_fn(state) == NODE_DEADLINE_ENFORCER


@pytest.mark.parametrize(
    "future_or_none_deadline",
    [
        "2099-01-01T00:00:00Z",
        "2099-01-01T00:00:00+00:00",
        "2099-01-01T00:00:00",
        None,
    ],
)
def test_deadline_future_and_none_normal_routing(future_or_none_deadline):
    """Future deadline and None allow normal forward routing to proceed."""
    state_pm = {
        "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
        "pipeline_deadline_at": future_or_none_deadline,
    }
    assert router_after_product_manager(state_pm) == NODE_ARCHITECT

    state_arch = {
        "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
        "pipeline_deadline_at": future_or_none_deadline,
    }
    assert router_after_architect(state_arch) == NODE_TASK_PLANNER


@pytest.mark.parametrize("router_fn", ALL_ROUTER_FUNCTIONS)
def test_deadline_malformed_raises_value_error(router_fn):
    """Malformed pipeline_deadline_at raises ValueError."""
    state = {
        "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
        "pipeline_deadline_at": "not-an-iso-8601-timestamp",
        "next_agent": "ProductManagerAgent",
    }
    with pytest.raises(ValueError, match="Malformed ISO 8601 timestamp"):
        router_fn(state)


@pytest.mark.parametrize("router_fn", ALL_ROUTER_FUNCTIONS)
def test_precedence_p1_expired_deadline_outranks_p2_and_p3(router_fn):
    """P1 (expired deadline) outranks P2 (global debug cap) and P3 (human approval)."""
    state = {
        "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
        "pipeline_deadline_at": "2020-01-01T00:00:00Z",
        "total_debug_cycles": 99,
        "max_debug_cycles": 10,
        "human_approval_required": True,
        "next_agent": "ProductManagerAgent",
    }
    assert router_fn(state) == NODE_DEADLINE_ENFORCER


@pytest.mark.parametrize("router_fn", ALL_ROUTER_FUNCTIONS)
def test_precedence_p2_global_debug_cap(router_fn):
    """P2: global debug cap reached routes to NODE_GLOBAL_CAP_HANDLER regardless of human_approval_required."""
    state = {
        "workflow_status": "debug_required",
        "total_debug_cycles": 10,
        "max_debug_cycles": 10,
        "human_approval_required": True,
        "next_agent": "BackendAgent",
    }
    assert router_fn(state) == NODE_GLOBAL_CAP_HANDLER


@pytest.mark.parametrize("router_fn", ALL_ROUTER_FUNCTIONS)
def test_precedence_p3_human_approval_required(router_fn):
    """P3: human_approval_required holds execution at human_approval node."""
    state = {
        "workflow_status": "requirements_complete",
        "total_debug_cycles": 2,
        "max_debug_cycles": 10,
        "human_approval_required": True,
    }
    assert router_fn(state) == NODE_HUMAN_APPROVAL


def test_router_after_human_approval_all_eleven_agents():
    """router_after_human_approval correctly maps all 11 agent class names to nodes when P1-P3 pass."""
    for agent_name, expected_node in AGENT_NAME_TO_NODE.items():
        state = {
            "workflow_status": "in_progress",
            "next_agent": agent_name,
            "total_debug_cycles": 0,
            "max_debug_cycles": 10,
            "human_approval_required": False,
        }
        assert router_after_human_approval(state) == expected_node


def test_router_after_human_approval_failed():
    """If workflow_status is failed, router_after_human_approval routes to END."""
    state = {
        "workflow_status": "failed",
        "next_agent": "BackendAgent",
        "human_approval_required": False,
    }
    assert router_after_human_approval(state) == END


def test_router_after_debug_permitted_targets():
    """router_after_debug maps all 7 permitted targets to nodes."""
    for agent_name, expected_node in NEXT_AGENT_TO_NODE.items():
        state = {
            "workflow_status": "debug_complete",
            "next_agent": agent_name,
            "human_approval_required": False,
        }
        assert router_after_debug(state) == expected_node


def test_router_after_debug_unmapped_agent_raises():
    """router_after_debug raises RuntimeError for unmapped agent targets (e.g. DebugAgent, TaskPlannerAgent)."""
    state = {
        "workflow_status": "debug_complete",
        "next_agent": "DebugAgent",
        "human_approval_required": False,
    }
    with pytest.raises(RuntimeError, match="Invalid or unmapped next_agent"):
        router_after_debug(state)


def test_state_immutability():
    """Routers must not mutate the incoming state dictionary."""
    state: Dict[str, Any] = {
        "workflow_status": "requirements_complete",
        "total_debug_cycles": 0,
        "human_approval_required": False,
    }
    original_copy = dict(state)
    router_after_product_manager(state)
    assert state == original_copy
