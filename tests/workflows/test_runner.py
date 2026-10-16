"""
tests/workflows/test_runner.py

Unit and integration tests for workflows/runner.py.
"""

from unittest.mock import patch
import pytest

from state.engineering_state import EngineeringState
from state.models import ReviewReport
from workflows.constants import (
    APPROVAL_DECISION_APPROVED,
    APPROVAL_DECISION_REJECTED,
    NODE_ARCHITECT,
    NODE_BACKEND,
    NODE_DEBUG,
    NODE_DEVOPS,
    NODE_DOCUMENTATION,
    NODE_FINAL_PROJECT,
    NODE_FRONTEND,
    NODE_PRODUCT_MANAGER,
    NODE_QA,
    NODE_REVIEWER,
    NODE_TASK_PLANNER,
    STATUS_ARCHITECTURE_COMPLETE,
    STATUS_BACKEND_COMPLETE,
    STATUS_COMPLETED,
    STATUS_DEVOPS_COMPLETE,
    STATUS_DOCUMENTATION_COMPLETE,
    STATUS_FAILED,
    STATUS_FRONTEND_COMPLETE,
    STATUS_PLANNING_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
)
from workflows.runner import cli_prompt_approval, run_pipeline


def _make_dummy_agent(node_name: str, update: dict):
    def stub(state: EngineeringState):
        res = dict(update)
        res["current_agent"] = node_name
        return res
    return stub


def _build_happy_agents():
    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.5,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    return {
        NODE_PRODUCT_MANAGER: _make_dummy_agent(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: _make_dummy_agent(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: _make_dummy_agent(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: _make_dummy_agent(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE}),
        NODE_FRONTEND: _make_dummy_agent(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: _make_dummy_agent(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: _make_dummy_agent(NODE_REVIEWER, {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review}),
        NODE_DEVOPS: _make_dummy_agent(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: _make_dummy_agent(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: _make_dummy_agent(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


def test_run_pipeline_happy_path():
    """run_pipeline builds initial state, compiles graph, and runs through to completed."""
    agents = _build_happy_agents()

    final_state = run_pipeline(
        project_name="RunnerHappyApp",
        user_input="Build runner happy app",
        agents=agents,
    )

    assert final_state["project_name"] == "RunnerHappyApp"
    assert final_state["user_input"] == "Build runner happy app"
    assert final_state["workflow_status"] == STATUS_COMPLETED


def test_run_pipeline_interrupt_and_resume_approved():
    """run_pipeline surfaces interrupt to approval_callback and resumes upon approval."""
    agents = _build_happy_agents()

    # Configure ReviewerAgent to trigger human approval hold
    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.5,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    agents[NODE_REVIEWER] = _make_dummy_agent(
        NODE_REVIEWER,
        {
            "workflow_status": STATUS_REVIEW_COMPLETE,
            "review_report": approved_review,
            "human_approval_required": True,
            "next_agent": "DevOpsAgent",
            "human_approval_notes": "Needs human verification before deployment",
        },
    )

    callback_called = False
    surfaced_payload = None

    def mock_approval_callback(payload: dict) -> dict:
        nonlocal callback_called, surfaced_payload
        callback_called = True
        surfaced_payload = payload
        return {"decision": APPROVAL_DECISION_APPROVED, "notes": "Approved by mock callback"}

    final_state = run_pipeline(
        project_name="RunnerApproveApp",
        user_input="Build runner approve app",
        agents=agents,
        approval_callback=mock_approval_callback,
    )

    assert callback_called is True
    assert surfaced_payload is not None
    assert surfaced_payload["current_agent"] == NODE_REVIEWER
    assert surfaced_payload["human_approval_notes"] == "Needs human verification before deployment"

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["human_approval_notes"] == "Approved by mock callback"


def test_run_pipeline_interrupt_and_resume_rejected():
    """run_pipeline surfaces interrupt to approval_callback and halts with failed upon rejection."""
    agents = _build_happy_agents()

    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.5,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    agents[NODE_REVIEWER] = _make_dummy_agent(
        NODE_REVIEWER,
        {
            "workflow_status": STATUS_REVIEW_COMPLETE,
            "review_report": approved_review,
            "human_approval_required": True,
            "next_agent": "DevOpsAgent",
            "human_approval_notes": "Verification requested",
        },
    )

    def mock_reject_callback(payload: dict) -> dict:
        return {"decision": APPROVAL_DECISION_REJECTED, "notes": "Rejected by operator"}

    final_state = run_pipeline(
        project_name="RunnerRejectApp",
        user_input="Build runner reject app",
        agents=agents,
        approval_callback=mock_reject_callback,
    )

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["human_approval_required"] is False
    assert final_state["human_approval_notes"] == "Rejected by operator"


def test_cli_prompt_approval(monkeypatch):
    """cli_prompt_approval displays context and reads operator input from stdin."""
    payload = {
        "run_id": "test-run-123",
        "current_agent": "ReviewerAgent",
        "workflow_status": "review_complete",
        "next_agent": "DevOpsAgent",
        "rejection_scope": None,
        "total_debug_cycles": 2,
        "human_approval_notes": "Reviewer flagged high risk",
    }

    # Simulate entering invalid input first, then 'approved', then notes
    inputs = iter(["maybe", "approved", "LGTM"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    res = cli_prompt_approval(payload)
    assert res == {"decision": "approved", "notes": "LGTM"}


def test_run_pipeline_rejects_malformed_deadline():
    """run_pipeline raises ValueError if pipeline_deadline_at is malformed."""
    agents = _build_happy_agents()
    with pytest.raises(ValueError, match="Malformed ISO 8601 timestamp"):
        run_pipeline(
            project_name="MalformedDeadlineApp",
            user_input="Build app",
            agents=agents,
            pipeline_deadline_at="not-a-valid-timestamp",
        )


def test_run_pipeline_rejects_already_expired_deadline():
    """run_pipeline fails fast with ValueError if pipeline_deadline_at is already in the past."""
    agents = _build_happy_agents()
    with pytest.raises(ValueError, match="already in the past"):
        run_pipeline(
            project_name="ExpiredDeadlineApp",
            user_input="Build app",
            agents=agents,
            pipeline_deadline_at="2020-01-01T00:00:00Z",
        )


def test_run_pipeline_accepts_valid_future_deadline():
    """run_pipeline proceeds normally when pipeline_deadline_at is a valid future timestamp."""
    agents = _build_happy_agents()
    final_state = run_pipeline(
        project_name="FutureDeadlineApp",
        user_input="Build app",
        agents=agents,
        pipeline_deadline_at="2099-01-01T00:00:00Z",
    )
    assert final_state["workflow_status"] == STATUS_COMPLETED

