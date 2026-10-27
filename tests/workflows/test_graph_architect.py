"""
tests/workflows/test_graph_architect.py

Integration tests verifying real ArchitectAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.architect_agent import ArchitectAgent
from agents.product_manager_agent import ProductManagerAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import ProductRequirements, ReviewReport, SystemArchitecture
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
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
)
from workflows.graph import build_graph


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["User can authenticate", "User can submit orders"],
        non_functional_requirements=["Response time under 200ms", "99.9% uptime"],
        user_stories=["As a user I want to sign up so that I can buy items"],
        acceptance_criteria=["Given valid credentials, return 200 and auth token"],
        constraints=["Python 3.11+", "PostgreSQL database"],
        business_goals=["Increase customer conversion rate by 15%"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture_dict() -> Dict[str, Any]:
    return {
        "overview": "Microservices-based event-driven architecture with API Gateway.",
        "technology_stack": {
            "backend": "Python 3.11 / FastAPI",
            "database": "PostgreSQL 16",
            "cache": "Redis 7",
        },
        "api_specifications": [
            {
                "endpoint": "/api/v1/auth/login",
                "method": "POST",
                "description": "Authenticate user and issue JWT",
            },
        ],
        "database_design": {"users": {"id": "UUID PK"}},
        "security_architecture": {"auth": "JWT RS256"},
        "scalability_strategy": "Horizontal autoscaling.",
        "deployment_model": "Kubernetes cluster.",
        "produced_by": "ArchitectAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def _make_dummy_agent(node_name: str, update: dict):
    def stub(state: EngineeringState):
        res = dict(update)
        res["current_agent"] = node_name
        return res
    return stub


def _build_pipeline_stubs():
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
        NODE_PRODUCT_MANAGER: _make_dummy_agent(
            NODE_PRODUCT_MANAGER,
            {
                "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
                "requirements": _sample_valid_requirements(),
                "forward_stage": 1,
            },
        ),
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


def test_graph_architect_happy_path_completes():
    """PM succeeds -> ArchitectAgent produces valid architecture -> pipeline completes normally."""
    client = FakeLLMClient(responses=[_sample_valid_architecture_dict()])
    arch_agent = ArchitectAgent(
        model_name="test-model",
        llm_client=client,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_ARCHITECT] = arch_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ArchGraphApp", user_input="Build architecture app")
    config = {"configurable": {"thread_id": "thread_arch_happy"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["architecture"] is not None
    assert isinstance(final_state["architecture"], SystemArchitecture)
    assert final_state["architecture"].produced_by == "ArchitectAgent"
    assert final_state["forward_stage"] >= 2
    assert final_state["retry_counts"]["ArchitectAgent"] == 0


def test_graph_architect_exhaustion_pauses_approves_and_completes():
    """All internal attempts fail -> pauses at human_approval -> approve -> Architect re-runs and completes."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
            _sample_valid_architecture_dict(),
        ]
    )

    arch_agent = ArchitectAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_ARCHITECT] = arch_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ArchGraphApp", user_input="Build architecture app")
    config = {"configurable": {"thread_id": "thread_arch_approve"}}

    # Stream initial execution; Architect exhausts and routes to human_approval
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
    assert snapshot.values["next_agent"] == "ArchitectAgent"
    assert snapshot.values["current_agent"] == "ArchitectAgent"
    assert snapshot.values["workflow_status"] == STATUS_REQUIREMENTS_COMPLETE
    assert snapshot.values["retry_counts"]["ArchitectAgent"] == 3

    # Operator approves continuation
    resume_cmd = Command(resume={"decision": "approved", "notes": "Approved retry"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["architecture"] is not None
    assert isinstance(final_state["architecture"], SystemArchitecture)
    assert final_state["architecture"].produced_by == "ArchitectAgent"
    assert final_state["retry_counts"]["ArchitectAgent"] == 0
    assert final_state["forward_stage"] >= 2


def test_graph_architect_exhaustion_pauses_rejects_and_fails():
    """All internal attempts fail -> pauses at human_approval -> reject -> workflow_status == failed."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
        ]
    )

    arch_agent = ArchitectAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_ARCHITECT] = arch_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ArchGraphRejectApp", user_input="Build reject app")
    config = {"configurable": {"thread_id": "thread_arch_reject"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["next_agent"] == "ArchitectAgent"

    # Operator rejects continuation
    resume_cmd = Command(resume={"decision": "rejected", "notes": "Rejected by operator"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["human_approval_required"] is False


def test_graph_architect_transient_failure_recovers_without_escalation():
    """First internal attempt fails, second succeeds -> no escalation, pipeline completes normally."""
    client = FakeLLMClient(
        responses=[
            ValueError("Malformed architecture output"),
            _sample_valid_architecture_dict(),
        ]
    )

    arch_agent = ArchitectAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_ARCHITECT] = arch_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ArchGraphTransientApp", user_input="Build transient app")
    config = {"configurable": {"thread_id": "thread_arch_transient"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    # Never escalated to human_approval
    assert final_state["human_approval_required"] is False
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["architecture"] is not None
    assert final_state["architecture"].version == 1
    assert final_state["retry_counts"]["ArchitectAgent"] == 0


def test_graph_chained_pm_to_architect_to_task_planner():
    """Verify complete sequential routing ProductManagerAgent -> ArchitectAgent -> TaskPlannerAgent."""
    pm_reqs_dict = {
        "functional_requirements": ["User can login", "User can submit orders"],
        "non_functional_requirements": ["Response time < 200ms"],
        "user_stories": ["As a shopper I want to order items"],
        "acceptance_criteria": ["Given valid payment, return order receipt"],
        "constraints": ["Python 3.11+", "PostgreSQL"],
        "business_goals": ["Launch e-commerce checkout"],
        "produced_by": "ProductManagerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }

    pm_client = FakeLLMClient(responses=[pm_reqs_dict])
    arch_client = FakeLLMClient(responses=[_sample_valid_architecture_dict()])

    pm_agent = ProductManagerAgent(model_name="pm-model", llm_client=pm_client)
    arch_agent = ArchitectAgent(model_name="arch-model", llm_client=arch_client)

    agents = _build_pipeline_stubs()
    agents[NODE_PRODUCT_MANAGER] = pm_agent.invoke
    agents[NODE_ARCHITECT] = arch_agent.invoke

    execution_order = []
    original_task_planner = agents[NODE_TASK_PLANNER]

    def recording_task_planner(state: EngineeringState):
        execution_order.append(NODE_TASK_PLANNER)
        assert state["workflow_status"] == STATUS_ARCHITECTURE_COMPLETE
        assert state["next_agent"] == "TaskPlannerAgent"
        assert state["current_agent"] == "ArchitectAgent"
        assert state["architecture"] is not None
        assert state["requirements"] is not None
        assert state["forward_stage"] == 2
        return original_task_planner(state)

    agents[NODE_TASK_PLANNER] = recording_task_planner

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ChainedApp", user_input="Build full e-commerce app")
    config = {"configurable": {"thread_id": "thread_chained_pm_arch"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert NODE_TASK_PLANNER in execution_order
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["requirements"] is not None
    assert final_state["architecture"] is not None
    assert final_state["requirements"].produced_by == "ProductManagerAgent"
    assert final_state["architecture"].produced_by == "ArchitectAgent"
    assert final_state["forward_stage"] >= 2
    assert len(pm_client.calls) == 1
    assert len(arch_client.calls) == 1

