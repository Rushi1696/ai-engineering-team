"""
tests/workflows/test_graph_qa.py

Integration tests verifying real QAAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.qa_agent import QAAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import (
    CodeArtifact,
    ImplementationTask,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TaskPlan,
)
from tools.qa.test_runner import FakeTestRunner, TestRunResult
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
                title="QA Tests",
                description="QA test suite",
                assigned_agent="QAAgent",
                priority=20,
                dependencies=["TASK-001"],
                acceptance_criteria=["Tests pass"],
                status="pending",
            ),
        ],
        execution_order=["TASK-001", "TASK-002"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "app/main.py": CodeArtifact(
            file_path="app/main.py",
            language="python",
            content="from fastapi import FastAPI\napp = FastAPI()\n",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
    }


def _sample_valid_qa_dict() -> Dict[str, Any]:
    return {
        "test_plan": {
            "strategy": "Comprehensive unit, integration, and E2E test strategy.",
            "coverage_goals": {"unit": "85%", "integration": "80%"},
            "test_suites": ["tests/test_auth.py"],
            "quality_metrics": {"target_pass_rate": 1.0},
            "produced_by": "QAAgent",
            "produced_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
        "test_artifacts": [
            {
                "file_path": "tests/test_auth.py",
                "content": "def test_login(): assert True\n",
                "test_type": "integration",
                "coverage_target": "85%",
                "produced_by": "QAAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "ai_findings": ["All endpoints covered by tests."],
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
    code_arts = _sample_code_artifacts()
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
                "code_artifacts": {"app/main.py": code_arts["app/main.py"]},
                "forward_stage": 4,
            },
        ),
        NODE_FRONTEND: _make_dummy_agent(
            NODE_FRONTEND,
            {
                "workflow_status": STATUS_FRONTEND_COMPLETE,
                "code_artifacts": code_arts,
                "forward_stage": 5,
            },
        ),
        NODE_QA: _make_dummy_agent(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: _make_dummy_agent(
            NODE_REVIEWER,
            {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review},
        ),
        NODE_DEVOPS: _make_dummy_agent(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: _make_dummy_agent(
            NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}
        ),
        NODE_FINAL_PROJECT: _make_dummy_agent(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


def test_graph_qa_pass_path_completes():
    """Passing test runner -> routes to ReviewerAgent -> pipeline completes normally."""
    client = FakeLLMClient(responses=[_sample_valid_qa_dict()])
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=True,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                duration_seconds=0.1,
                output="1 passed",
                failing_tests=[],
            )
        ]
    )
    qa_agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_QA] = qa_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="QAGraphPassApp", user_input="Build verified app")
    config = {"configurable": {"thread_id": "thread_qa_pass"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert final_state["test_plan"] is not None
    assert "tests/test_auth.py" in final_state["test_artifacts"]
    assert final_state["forward_stage"] >= 6
    assert final_state["retry_counts"]["QAAgent"] == 0


def test_graph_qa_fail_then_debug_stub_then_rerun_passes():
    """Runner fails -> routes to Debug stub -> Debug routes to QA -> QA re-run passes and completes."""
    client = FakeLLMClient(
        responses=[
            _sample_valid_qa_dict(),
            _sample_valid_qa_dict(),
        ]
    )
    # Runner first fails, second execution passes
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=False,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                duration_seconds=0.1,
                output="1 failed",
                failing_tests=["tests/test_auth.py::test_login"],
            ),
            TestRunResult(
                passed=True,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                duration_seconds=0.1,
                output="1 passed",
                failing_tests=[],
            ),
        ]
    )
    qa_agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_QA] = qa_agent.invoke

    debug_calls = []

    def debug_stub(state: EngineeringState):
        debug_calls.append(len(debug_calls) + 1)
        # Debug agent diagnoses failure and routes back to QAAgent
        return {
            "current_agent": "DebugAgent",
            "next_agent": "QAAgent",
            "total_debug_cycles": state.get("total_debug_cycles", 0) + 1,
        }

    agents[NODE_DEBUG] = debug_stub

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="QADebugApp", user_input="Build verified app")
    config = {"configurable": {"thread_id": "thread_qa_fail_debug_pass"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert len(debug_calls) == 1
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["total_debug_cycles"] == 1
    assert final_state["retry_counts"]["QAAgent"] == 0
    assert final_state["forward_stage"] >= 6


def test_graph_qa_runner_always_fails_ending_failed_via_global_cap():
    """Runner always fails -> debug stub increments cycles -> ends failed via global_cap_handler."""
    client = FakeLLMClient(
        responses=[
            _sample_valid_qa_dict(),
            _sample_valid_qa_dict(),
            _sample_valid_qa_dict(),
            _sample_valid_qa_dict(),
            _sample_valid_qa_dict(),
        ]
    )
    # Runner always returns failure
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=False,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                duration_seconds=0.05,
                output="AssertionError",
                failing_tests=["test_fail"],
            )
            for _ in range(10)
        ]
    )
    qa_agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_QA] = qa_agent.invoke

    def debug_stub(state: EngineeringState):
        return {
            "current_agent": "DebugAgent",
            "next_agent": "QAAgent",
            "total_debug_cycles": state.get("total_debug_cycles", 0) + 1,
        }

    agents[NODE_DEBUG] = debug_stub

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="QACapApp", user_input="Build failing app")
    initial_state["max_debug_cycles"] = 2  # Set cap to 2 for fast test execution
    config = {"configurable": {"thread_id": "thread_qa_global_cap"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    # Ended via global_cap_handler
    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["total_debug_cycles"] >= 2
    errors = final_state.get("errors", [])
    assert any(e.error_type == "retry_exhausted" and e.agent == "Orchestrator" for e in errors)


def test_graph_qa_exhaustion_then_approve():
    """All internal attempts fail -> pauses at human_approval -> approve -> QA re-runs and completes."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("LLM error 1"),
            RuntimeError("LLM error 2"),
            RuntimeError("LLM error 3"),
            _sample_valid_qa_dict(),
        ]
    )
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=True,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                duration_seconds=0.05,
                output="1 passed",
                failing_tests=[],
            )
        ]
    )
    qa_agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_QA] = qa_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="QAApproveApp", user_input="Build app")
    config = {"configurable": {"thread_id": "thread_qa_approve"}}

    # Stream initial execution; QAAgent exhausts and pauses at human_approval
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
    assert snapshot.values["next_agent"] == "QAAgent"
    assert snapshot.values["current_agent"] == "QAAgent"
    assert snapshot.values["workflow_status"] == STATUS_FRONTEND_COMPLETE
    assert snapshot.values["retry_counts"]["QAAgent"] == 3

    # Operator approves continuation
    resume_cmd = Command(resume={"decision": "approved", "notes": "Approved retry after QA error"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["human_approval_required"] is False
    assert "tests/test_auth.py" in final_state["test_artifacts"]
    assert final_state["retry_counts"]["QAAgent"] == 0
    assert final_state["forward_stage"] >= 6


def test_graph_qa_exhaustion_then_reject():
    """All internal attempts fail -> pauses at human_approval -> reject -> workflow_status == failed."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("LLM error 1"),
            RuntimeError("LLM error 2"),
            RuntimeError("LLM error 3"),
        ]
    )
    runner = FakeTestRunner()
    qa_agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_QA] = qa_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="QARejectApp", user_input="Build reject app")
    config = {"configurable": {"thread_id": "thread_qa_reject"}}

    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["next_agent"] == "QAAgent"

    # Operator rejects continuation
    resume_cmd = Command(resume={"decision": "rejected", "notes": "Operator rejected further attempts"})
    for _ in graph.stream(resume_cmd, config=config):
        pass

    final_snapshot = graph.get_state(config)
    final_state = final_snapshot.values

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["human_approval_required"] is False
