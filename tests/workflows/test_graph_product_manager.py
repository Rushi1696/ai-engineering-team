"""
tests/workflows/test_graph_product_manager.py

Integration tests verifying real ProductManagerAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.product_manager_agent import ProductManagerAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import ProductRequirements, ReviewReport
from tools.shared.llm_client import FakeLLMClient
from workflows.constants import (
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
    STATUS_REVIEW_COMPLETE,
)
from workflows.graph import build_graph


def _sample_valid_requirements_dict() -> Dict[str, Any]:
    return {
        "functional_requirements": ["User can login", "User can view items"],
        "non_functional_requirements": ["Sub-second latency"],
        "user_stories": ["As a shopper I want to browse products"],
        "acceptance_criteria": ["Given catalog page, render item list"],
        "constraints": ["Mobile responsive"],
        "business_goals": ["Launch e-commerce MVP"],
        "produced_by": "ProductManagerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def _make_dummy_agent(node_name: str, update: dict):
    def stub(state: EngineeringState):
        res = dict(update)
        res["current_agent"] = node_name
        return res
    return stub


def _build_downstream_stubs():
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


def test_graph_pm_exhaustion_pauses_approves_and_completes():
    """All internal attempts fail -> pauses at human_approval -> approve -> PM re-runs and pipeline completes."""
    # 3 failures initially, followed by 1 success on re-run after approval
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
            _sample_valid_requirements_dict(),
        ]
    )

    pm_agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_downstream_stubs()
    agents[NODE_PRODUCT_MANAGER] = pm_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="PMGraphApp", user_input="Build e-commerce app")
    config = {"configurable": {"thread_id": "thread_pm_approve"}}

    # Stream initial execution; PM exhausts and routes to human_approval
    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    interrupts = []
    for task in snapshot.tasks:
        if getattr(task, "interrupts", None):
            interrupts.extend(task.interrupts)

    # Graph is paused at human_approval
    assert len(interrupts) == 1
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["next_agent"] == "ProductManagerAgent"
    # workflow_status was preserved (not 'failed')
    assert snapshot.values["workflow_status"] == "initiated"

    # Operator approves continuation
    resume_cmd = Command(resume={"decision": "approved", "notes": "Approved retry"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["requirements"] is not None
    assert isinstance(final_state["requirements"], ProductRequirements)
    assert final_state["requirements"].produced_by == "ProductManagerAgent"
    assert final_state["forward_stage"] >= 1


def test_graph_pm_exhaustion_pauses_rejects_and_fails():
    """All internal attempts fail -> pauses at human_approval -> reject -> workflow_status == failed."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
        ]
    )

    pm_agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_downstream_stubs()
    agents[NODE_PRODUCT_MANAGER] = pm_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="PMGraphRejectApp", user_input="Build reject app")
    config = {"configurable": {"thread_id": "thread_pm_reject"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True

    # Operator rejects continuation
    resume_cmd = Command(resume={"decision": "rejected", "notes": "Rejected by operator"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["human_approval_required"] is False


def test_graph_pm_transient_failure_recovers_without_escalation():
    """First internal attempt fails, second succeeds -> no escalation, pipeline completes normally."""
    client = FakeLLMClient(
        responses=[
            ValueError("Malformed requirements output"),
            _sample_valid_requirements_dict(),
        ]
    )

    pm_agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_downstream_stubs()
    agents[NODE_PRODUCT_MANAGER] = pm_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="PMGraphTransientApp", user_input="Build transient app")
    config = {"configurable": {"thread_id": "thread_pm_transient"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    # Never escalated to human_approval
    assert final_state["human_approval_required"] is False
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["requirements"] is not None
    assert final_state["requirements"].version == 1
