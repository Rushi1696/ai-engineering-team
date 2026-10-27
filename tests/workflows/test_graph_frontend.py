"""
tests/workflows/test_graph_frontend.py

Integration tests verifying real FrontendAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.backend_agent import BackendAgent
from agents.frontend_agent import FrontendAgent
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
        constraints=["React 18+", "Tailwind CSS"],
        business_goals=["Increase customer conversion rate by 15%"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture_dict() -> Dict[str, Any]:
    return {
        "overview": "React frontend communicating with FastAPI backend.",
        "technology_stack": {
            "frontend": "React 18 / Vite",
            "backend": "Python 3.11 / FastAPI",
        },
        "api_specifications": [
            {
                "endpoint": "/api/v1/auth/login",
                "method": "POST",
                "description": "Authenticate user and issue JWT",
            },
        ],
        "database_design": {"users": {"id": "UUID PK"}},
        "security_architecture": {"auth": "JWT Bearer"},
        "scalability_strategy": "CDN caching.",
        "deployment_model": "Vercel + AWS.",
        "produced_by": "ArchitectAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def _sample_valid_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="TASK-001",
                title="Backend Auth",
                description="Backend API",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["API returns 200"],
                status="completed",
            ),
            ImplementationTask(
                task_id="TASK-002",
                title="Frontend Auth UI",
                description="Login component",
                assigned_agent="FrontendAgent",
                priority=20,
                dependencies=["TASK-001"],
                acceptance_criteria=["UI connects to API"],
                status="pending",
            ),
        ],
        execution_order=["TASK-001", "TASK-002"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_frontend_artifacts_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "src/components/Login.tsx",
                "language": "typescript",
                "content": "export const Login = () => <form>Login</form>;\n",
                "artifact_type": "frontend",
                "produced_by": "FrontendAgent",
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
    backend_artifact = CodeArtifact(
        file_path="app/main.py",
        language="python",
        content="app = FastAPI()",
        artifact_type="backend",
        produced_by="BackendAgent",
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
        NODE_BACKEND: _make_dummy_agent(
            NODE_BACKEND,
            {
                "workflow_status": STATUS_BACKEND_COMPLETE,
                "code_artifacts": {"app/main.py": backend_artifact},
                "forward_stage": 4,
            },
        ),
        NODE_QA: _make_dummy_agent(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: _make_dummy_agent(NODE_REVIEWER, {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review}),
        NODE_DEVOPS: _make_dummy_agent(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: _make_dummy_agent(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: _make_dummy_agent(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


def test_graph_frontend_happy_path_completes():
    """Backend succeeds -> FrontendAgent produces code artifacts -> pipeline completes normally."""
    client = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    frontend_agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_FRONTEND] = frontend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="FrontendGraphApp", user_input="Build frontend app")
    config = {"configurable": {"thread_id": "thread_frontend_happy"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["code_artifacts"] is not None
    # Both backend and frontend artifacts preserved
    assert "app/main.py" in final_state["code_artifacts"]
    assert "src/components/Login.tsx" in final_state["code_artifacts"]
    assert final_state["code_artifacts"]["src/components/Login.tsx"].produced_by == "FrontendAgent"
    assert final_state["code_artifacts"]["src/components/Login.tsx"].artifact_type == "frontend"
    assert final_state["forward_stage"] >= 5
    assert final_state["retry_counts"]["FrontendAgent"] == 0


def test_graph_frontend_exhaustion_pauses_approves_and_completes():
    """All internal attempts fail -> pauses at human_approval -> approve -> Frontend re-runs and completes."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
            _sample_valid_frontend_artifacts_dict(),
        ]
    )

    frontend_agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_FRONTEND] = frontend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="FrontendGraphApp", user_input="Build frontend app")
    config = {"configurable": {"thread_id": "thread_frontend_approve"}}

    # Stream initial execution; FrontendAgent exhausts and routes to human_approval
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
    assert snapshot.values["next_agent"] == "FrontendAgent"
    assert snapshot.values["current_agent"] == "FrontendAgent"
    assert snapshot.values["workflow_status"] == STATUS_BACKEND_COMPLETE
    assert snapshot.values["retry_counts"]["FrontendAgent"] == 3

    # Operator approves continuation
    resume_cmd = Command(resume={"decision": "approved", "notes": "Approved retry"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert "src/components/Login.tsx" in final_state["code_artifacts"]
    assert final_state["retry_counts"]["FrontendAgent"] == 0
    assert final_state["forward_stage"] >= 5


def test_graph_frontend_exhaustion_pauses_rejects_and_fails():
    """All internal attempts fail -> pauses at human_approval -> reject -> workflow_status == failed."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
        ]
    )

    frontend_agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_FRONTEND] = frontend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="FrontendGraphRejectApp", user_input="Build reject app")
    config = {"configurable": {"thread_id": "thread_frontend_reject"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["next_agent"] == "FrontendAgent"

    # Operator rejects continuation
    resume_cmd = Command(resume={"decision": "rejected", "notes": "Rejected by operator"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["human_approval_required"] is False


def test_graph_frontend_transient_failure_recovers_without_escalation():
    """First internal attempt fails, second succeeds -> no escalation, pipeline completes normally."""
    invalid_art = _sample_valid_frontend_artifacts_dict()
    invalid_art["artifacts"][0]["content"] = ""

    client = FakeLLMClient(
        responses=[
            invalid_art,
            _sample_valid_frontend_artifacts_dict(),
        ]
    )

    frontend_agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_FRONTEND] = frontend_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="FrontendGraphTransientApp", user_input="Build transient app")
    config = {"configurable": {"thread_id": "thread_frontend_transient"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    # Never escalated to human_approval
    assert final_state["human_approval_required"] is False
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert "src/components/Login.tsx" in final_state["code_artifacts"]
    assert final_state["retry_counts"]["FrontendAgent"] == 0


def test_graph_chained_backend_to_frontend_to_qa():
    """Verify complete sequential routing Backend -> Frontend -> QA."""
    backend_artifacts = {
        "artifacts": [
            {
                "file_path": "app/main.py",
                "language": "python",
                "content": "app = FastAPI()",
                "artifact_type": "backend",
                "produced_by": "BackendAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ]
    }
    frontend_artifacts = _sample_valid_frontend_artifacts_dict()

    be_client = FakeLLMClient(responses=[backend_artifacts])
    fe_client = FakeLLMClient(responses=[frontend_artifacts])

    be_agent = BackendAgent(model_name="be-model", llm_client=be_client)
    fe_agent = FrontendAgent(model_name="fe-model", llm_client=fe_client)

    agents = _build_pipeline_stubs()
    agents[NODE_BACKEND] = be_agent.invoke
    agents[NODE_FRONTEND] = fe_agent.invoke

    execution_order = []
    original_qa = agents[NODE_QA]

    def recording_qa(state: EngineeringState):
        execution_order.append(NODE_QA)
        assert state["workflow_status"] == STATUS_FRONTEND_COMPLETE
        assert state["next_agent"] == "QAAgent"
        assert state["current_agent"] == "FrontendAgent"
        assert state["requirements"] is not None
        assert state["architecture"] is not None
        assert state["task_plan"] is not None
        assert state["code_artifacts"] is not None
        assert "app/main.py" in state["code_artifacts"]
        assert "src/components/Login.tsx" in state["code_artifacts"]
        assert state["forward_stage"] == 5
        return original_qa(state)

    agents[NODE_QA] = recording_qa

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ChainedApp", user_input="Build full e-commerce app")
    config = {"configurable": {"thread_id": "thread_chained_be_fe_qa"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert NODE_QA in execution_order
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert "app/main.py" in final_state["code_artifacts"]
    assert "src/components/Login.tsx" in final_state["code_artifacts"]
    assert final_state["code_artifacts"]["src/components/Login.tsx"].produced_by == "FrontendAgent"
    assert final_state["code_artifacts"]["app/main.py"].produced_by == "BackendAgent"
    assert final_state["forward_stage"] >= 5
    assert len(be_client.calls) == 1
    assert len(fe_client.calls) == 1
