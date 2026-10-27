"""
tests/workflows/test_graph_backend.py

Integration tests verifying real BackendAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.architect_agent import ArchitectAgent
from agents.backend_agent import BackendAgent
from agents.product_manager_agent import ProductManagerAgent
from agents.task_planner_agent import TaskPlannerAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import (
    CodeArtifact,
    ImplementationTask,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TaskPlan,
)
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


def _sample_valid_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="TASK-001",
                title="Setup FastAPI and User model",
                description="Initialize backend structure and implement DB auth models.",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["JWT auth model is created"],
                status="pending",
            ),
            ImplementationTask(
                task_id="TASK-002",
                title="Build frontend login UI",
                description="Create login page.",
                assigned_agent="FrontendAgent",
                priority=20,
                dependencies=["TASK-001"],
                acceptance_criteria=["User can login through form"],
                status="pending",
            ),
        ],
        execution_order=["TASK-001", "TASK-002"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_backend_artifacts_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "app/main.py",
                "language": "python",
                "content": "from fastapi import FastAPI\napp = FastAPI()\n",
                "artifact_type": "backend",
                "produced_by": "BackendAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ]
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
        NODE_ARCHITECT: _make_dummy_agent(
            NODE_ARCHITECT,
            {
                "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
                "architecture": SystemArchitecture(**_sample_valid_architecture_dict()),
                "forward_stage": 2,
            },
        ),
        NODE_TASK_PLANNER: _make_dummy_agent(
            NODE_TASK_PLANNER,
            {
                "workflow_status": STATUS_PLANNING_COMPLETE,
                "task_plan": _sample_valid_task_plan(),
                "forward_stage": 3,
            },
        ),
        NODE_FRONTEND: _make_dummy_agent(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: _make_dummy_agent(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: _make_dummy_agent(NODE_REVIEWER, {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review}),
        NODE_DEVOPS: _make_dummy_agent(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: _make_dummy_agent(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: _make_dummy_agent(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


def test_graph_backend_happy_path_completes():
    """TaskPlanner succeeds -> BackendAgent produces code artifacts -> pipeline completes normally."""
    client = FakeLLMClient(responses=[_sample_valid_backend_artifacts_dict()])
    backend_agent = BackendAgent(
        model_name="test-model",
        llm_client=client,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_BACKEND] = backend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="BackendGraphApp", user_input="Build backend app")
    config = {"configurable": {"thread_id": "thread_backend_happy"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["code_artifacts"] is not None
    assert "app/main.py" in final_state["code_artifacts"]
    assert final_state["code_artifacts"]["app/main.py"].produced_by == "BackendAgent"
    assert final_state["forward_stage"] >= 4
    assert final_state["retry_counts"]["BackendAgent"] == 0


def test_graph_backend_exhaustion_pauses_approves_and_completes():
    """All internal attempts fail -> pauses at human_approval -> approve -> Backend re-runs and completes."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
            _sample_valid_backend_artifacts_dict(),
        ]
    )

    backend_agent = BackendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_BACKEND] = backend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="BackendGraphApp", user_input="Build backend app")
    config = {"configurable": {"thread_id": "thread_backend_approve"}}

    # Stream initial execution; BackendAgent exhausts and routes to human_approval
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
    assert snapshot.values["next_agent"] == "BackendAgent"
    assert snapshot.values["current_agent"] == "BackendAgent"
    assert snapshot.values["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert snapshot.values["retry_counts"]["BackendAgent"] == 3

    # Operator approves continuation
    resume_cmd = Command(resume={"decision": "approved", "notes": "Approved retry"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert "app/main.py" in final_state["code_artifacts"]
    assert final_state["retry_counts"]["BackendAgent"] == 0
    assert final_state["forward_stage"] >= 4


def test_graph_backend_exhaustion_pauses_rejects_and_fails():
    """All internal attempts fail -> pauses at human_approval -> reject -> workflow_status == failed."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
        ]
    )

    backend_agent = BackendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_BACKEND] = backend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="BackendGraphRejectApp", user_input="Build reject app")
    config = {"configurable": {"thread_id": "thread_backend_reject"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["next_agent"] == "BackendAgent"

    # Operator rejects continuation
    resume_cmd = Command(resume={"decision": "rejected", "notes": "Rejected by operator"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["human_approval_required"] is False


def test_graph_backend_transient_failure_recovers_without_escalation():
    """First internal attempt fails, second succeeds -> no escalation, pipeline completes normally."""
    invalid_art = _sample_valid_backend_artifacts_dict()
    invalid_art["artifacts"][0]["content"] = ""

    client = FakeLLMClient(
        responses=[
            invalid_art,
            _sample_valid_backend_artifacts_dict(),
        ]
    )

    backend_agent = BackendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_BACKEND] = backend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="BackendGraphTransientApp", user_input="Build transient app")
    config = {"configurable": {"thread_id": "thread_backend_transient"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    # Never escalated to human_approval
    assert final_state["human_approval_required"] is False
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert "app/main.py" in final_state["code_artifacts"]
    assert final_state["retry_counts"]["BackendAgent"] == 0


def test_graph_chained_pm_to_architect_to_task_planner_to_backend_to_frontend():
    """Verify complete sequential routing PM -> Architect -> TaskPlanner -> Backend -> Frontend."""
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

    tp_plan_dict = {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Backend API",
                "description": "FastAPI backend",
                "assigned_agent": "BackendAgent",
                "priority": 10,
                "dependencies": [],
                "acceptance_criteria": ["API returns 200"],
                "status": "pending",
            },
            {
                "task_id": "TASK-002",
                "title": "Frontend UI",
                "description": "React UI",
                "assigned_agent": "FrontendAgent",
                "priority": 20,
                "dependencies": ["TASK-001"],
                "acceptance_criteria": ["UI works"],
                "status": "pending",
            },
        ],
        "execution_order": ["TASK-001", "TASK-002"],
        "produced_by": "TaskPlannerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }

    pm_client = FakeLLMClient(responses=[pm_reqs_dict])
    arch_client = FakeLLMClient(responses=[_sample_valid_architecture_dict()])
    tp_client = FakeLLMClient(responses=[tp_plan_dict])
    be_client = FakeLLMClient(responses=[_sample_valid_backend_artifacts_dict()])

    pm_agent = ProductManagerAgent(model_name="pm-model", llm_client=pm_client)
    arch_agent = ArchitectAgent(model_name="arch-model", llm_client=arch_client)
    tp_agent = TaskPlannerAgent(model_name="tp-model", llm_client=tp_client)
    be_agent = BackendAgent(model_name="be-model", llm_client=be_client)

    agents = _build_pipeline_stubs()
    agents[NODE_PRODUCT_MANAGER] = pm_agent.invoke
    agents[NODE_ARCHITECT] = arch_agent.invoke
    agents[NODE_TASK_PLANNER] = tp_agent.invoke
    agents[NODE_BACKEND] = be_agent.invoke

    execution_order = []
    original_frontend = agents[NODE_FRONTEND]

    def recording_frontend(state: EngineeringState):
        execution_order.append(NODE_FRONTEND)
        assert state["workflow_status"] == STATUS_BACKEND_COMPLETE
        assert state["next_agent"] == "FrontendAgent"
        assert state["current_agent"] == "BackendAgent"
        assert state["requirements"] is not None
        assert state["architecture"] is not None
        assert state["task_plan"] is not None
        assert state["code_artifacts"] is not None
        assert "app/main.py" in state["code_artifacts"]
        assert state["forward_stage"] == 4
        return original_frontend(state)

    agents[NODE_FRONTEND] = recording_frontend

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ChainedApp", user_input="Build full e-commerce app")
    config = {"configurable": {"thread_id": "thread_chained_pm_arch_tp_be"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert NODE_FRONTEND in execution_order
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["requirements"] is not None
    assert final_state["architecture"] is not None
    assert final_state["task_plan"] is not None
    assert "app/main.py" in final_state["code_artifacts"]
    assert final_state["code_artifacts"]["app/main.py"].produced_by == "BackendAgent"
    assert final_state["forward_stage"] >= 4
    assert len(pm_client.calls) == 1
    assert len(arch_client.calls) == 1
    assert len(tp_client.calls) == 1
    assert len(be_client.calls) == 1
