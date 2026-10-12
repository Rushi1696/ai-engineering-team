"""
tests/workflows/test_interrupts.py

Unit and end-to-end integration tests for HumanApprovalNode in workflows/interrupts.py.
"""

from unittest.mock import patch
import pytest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.types import Command

from state.engineering_state import EngineeringState, create_initial_state
from state.models import ReviewReport
from workflows.constants import (
    APPROVAL_DECISION_APPROVED,
    APPROVAL_DECISION_REJECTED,
    STATUS_ARCHITECTURE_COMPLETE,
    STATUS_DEBUG_REQUIRED,
    STATUS_ESCALATION_REQUIRED,
    STATUS_FAILED,
    STATUS_INITIATED,
)
from workflows.interrupts import human_approval_node


def test_human_approval_node_invalid_decision_raises_value_error():
    """Requirement (a): Invalid or missing decision raises ValueError."""
    state = create_initial_state(project_name="TestApp", user_input="Build an app")
    state["human_approval_required"] = True

    # 1. Invalid string decision
    with patch("workflows.interrupts.interrupt", return_value="maybe"):
        with pytest.raises(ValueError, match="Invalid human approval decision: 'maybe'"):
            human_approval_node(state)

    # 2. Dict missing decision key
    with patch("workflows.interrupts.interrupt", return_value={"notes": "No decision"}):
        with pytest.raises(ValueError, match="Invalid human approval decision: None"):
            human_approval_node(state)

    # 3. None return value
    with patch("workflows.interrupts.interrupt", return_value=None):
        with pytest.raises(ValueError, match="Invalid human approval decision: None"):
            human_approval_node(state)


def test_human_approval_node_stale_rejection_scope_not_used_on_retry_exhaustion():
    """Requirement (b): Stale rejection_scope="code_level" on retry-exhaustion does NOT route to DebugAgent."""
    state = create_initial_state(project_name="TestApp", user_input="Build an app")
    state["human_approval_required"] = True
    state["rejection_scope"] = "code_level"  # Stale scope left from earlier review
    state["workflow_status"] = "backend_complete"  # NOT escalation_required
    state["current_agent"] = "FrontendAgent"
    state["next_agent"] = "QAAgent"
    state["retry_counts"] = {"FrontendAgent": 3}

    with patch("workflows.interrupts.interrupt", return_value={"decision": APPROVAL_DECISION_APPROVED}) as mock_interrupt:
        result = human_approval_node(state)

        mock_interrupt.assert_called_once()
        assert result["human_approval_required"] is False
        assert "rejection_scope" not in result
        # Does NOT route to DebugAgent! Keeps pre-interrupt next_agent
        assert result["next_agent"] == "QAAgent"
        assert result["workflow_status"] == "backend_complete"


def test_human_approval_node_resets_current_agent_retry_counts():
    """Requirement (c): Retry reset hits current_agent (the escalating agent) on retry exhaustion."""
    state = create_initial_state(project_name="TestApp", user_input="Build an app")
    state["human_approval_required"] = True
    state["current_agent"] = "BackendAgent"
    state["next_agent"] = "FrontendAgent"
    state["retry_counts"] = {"BackendAgent": 3, "FrontendAgent": 0}

    with patch("workflows.interrupts.interrupt", return_value={"decision": APPROVAL_DECISION_APPROVED}) as mock_interrupt:
        result = human_approval_node(state)

        mock_interrupt.assert_called_once()
        assert result["retry_counts"]["BackendAgent"] == 0


def test_human_approval_node_rejected_decision():
    """Rejection path sets workflow_status = failed and human_approval_required = False."""
    state = create_initial_state(project_name="TestApp", user_input="Build an app")
    state["human_approval_required"] = True

    with patch("workflows.interrupts.interrupt", return_value={"decision": APPROVAL_DECISION_REJECTED, "notes": "Project cancelled"}) as mock_interrupt:
        result = human_approval_node(state)

        mock_interrupt.assert_called_once()
        assert result["human_approval_required"] is False
        assert result["workflow_status"] == STATUS_FAILED
        assert result["human_approval_notes"] == "Project cancelled"


@pytest.mark.parametrize(
    "scope, expected_next_agent, expected_status",
    [
        ("code_level", "DebugAgent", STATUS_DEBUG_REQUIRED),
        ("design_level", "ArchitectAgent", STATUS_ARCHITECTURE_COMPLETE),
        ("requirements_level", "ProductManagerAgent", STATUS_INITIATED),
    ],
)
def test_human_approval_node_parametrized_rejection_scopes(scope, expected_next_agent, expected_status):
    """Parametrized test covering all three rejection scopes: asserts next_agent, status, and target retry reset."""
    report_rejected = ReviewReport(
        overall_status="rejected",
        code_quality_score=2.0,
        architecture_compliance=False,
        security_findings=["Sec vulnerability"],
        performance_findings=[],
        maintainability_findings=[],
        testing_coverage_assessment="Poor",
        engineering_standards_compliance=False,
        recommendations=["Action required"],
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    state = create_initial_state(project_name="TestApp", user_input="Build an app")
    state["human_approval_required"] = True
    state["workflow_status"] = STATUS_ESCALATION_REQUIRED
    state["review_report"] = report_rejected
    state["rejection_scope"] = scope
    state["current_agent"] = "ReviewerAgent"
    state["retry_counts"] = {expected_next_agent: 2, "ReviewerAgent": 1}

    with patch("workflows.interrupts.interrupt", return_value={"decision": APPROVAL_DECISION_APPROVED}):
        result = human_approval_node(state)

        assert result["next_agent"] == expected_next_agent
        assert result["workflow_status"] == expected_status
        assert result["retry_counts"][expected_next_agent] == 0
        assert "rejection_scope" not in result


def test_human_approval_node_missing_or_invalid_rejection_scope_raises_error():
    """Rejected review escalation requires valid rejection_scope; missing/invalid raises ValueError."""
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
    state = create_initial_state(project_name="TestApp", user_input="Build an app")
    state["human_approval_required"] = True
    state["workflow_status"] = STATUS_ESCALATION_REQUIRED
    state["review_report"] = report_rejected

    # 1. rejection_scope is None
    state["rejection_scope"] = None
    with patch("workflows.interrupts.interrupt", return_value={"decision": APPROVAL_DECISION_APPROVED}):
        with pytest.raises(ValueError, match="Rejected review escalation requires valid rejection_scope"):
            human_approval_node(state)

    # 2. rejection_scope is invalid string
    state["rejection_scope"] = "invalid_scope"
    with patch("workflows.interrupts.interrupt", return_value={"decision": APPROVAL_DECISION_APPROVED}):
        with pytest.raises(ValueError, match="Rejected review escalation requires valid rejection_scope"):
            human_approval_node(state)


def test_end_to_end_interrupt_and_resume_approved():
    """Requirement (d): Real end-to-end interrupt/resume through compiled StateGraph + MemorySaver on Approved."""
    builder = StateGraph(EngineeringState)

    def start_node(state: EngineeringState):
        return {"human_approval_required": True, "current_agent": "BackendAgent"}

    builder.add_node("start", start_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_edge("start", "human_approval")
    builder.add_edge("human_approval", END)
    builder.set_entry_point("start")

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    initial_state = create_initial_state(project_name="TestApp", user_input="Build an app")
    initial_state["retry_counts"] = {"BackendAgent": 3}
    config = {"configurable": {"thread_id": "test_thread_approved"}}

    # 1. First invocation: pauses at human_approval node
    list(graph.stream(initial_state, config=config))
    snapshot = graph.get_state(config)
    assert len(snapshot.tasks) > 0
    assert snapshot.tasks[0].interrupts[0].value["current_agent"] == "BackendAgent"

    # 2. Resume with Command(resume=...)
    resume_cmd = Command(resume={"decision": APPROVAL_DECISION_APPROVED, "notes": "Human approved retry"})
    list(graph.stream(resume_cmd, config=config))

    final_snapshot = graph.get_state(config)
    assert final_snapshot.values["human_approval_required"] is False
    assert final_snapshot.values["retry_counts"]["BackendAgent"] == 0
    assert final_snapshot.values["human_approval_notes"] == "Human approved retry"


def test_end_to_end_interrupt_and_resume_rejected():
    """Requirement (d): Real end-to-end interrupt/resume through compiled StateGraph + MemorySaver on Rejected."""
    builder = StateGraph(EngineeringState)

    def start_node(state: EngineeringState):
        return {"human_approval_required": True, "current_agent": "BackendAgent"}

    builder.add_node("start", start_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_edge("start", "human_approval")
    builder.add_edge("human_approval", END)
    builder.set_entry_point("start")

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    initial_state = create_initial_state(project_name="TestApp", user_input="Build an app")
    config = {"configurable": {"thread_id": "test_thread_rejected"}}

    # 1. First invocation: pauses at human_approval node
    list(graph.stream(initial_state, config=config))
    snapshot = graph.get_state(config)
    assert len(snapshot.tasks) > 0

    # 2. Resume with Command(resume=...) -> Rejected
    resume_cmd = Command(resume={"decision": APPROVAL_DECISION_REJECTED, "notes": "Human rejected continuation"})
    list(graph.stream(resume_cmd, config=config))

    final_snapshot = graph.get_state(config)
    assert final_snapshot.values["human_approval_required"] is False
    assert final_snapshot.values["workflow_status"] == STATUS_FAILED
    assert final_snapshot.values["human_approval_notes"] == "Human rejected continuation"
