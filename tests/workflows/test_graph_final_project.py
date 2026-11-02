"""
tests/workflows/test_graph_final_project.py

Integration tests for FinalProjectAgent within the compiled StateGraph topology,
including the comprehensive full 11-agent end-to-end pipeline test.
"""

from typing import Any, Dict
from unittest.mock import patch
from langgraph.checkpoint.memory import MemorySaver
import pytest

from agents.architect_agent import ArchitectAgent
from agents.backend_agent import BackendAgent
from agents.debug_agent import DebugAgent
from agents.devops_agent import DevOpsAgent
from agents.documentation_agent import DocumentationAgent
from agents.final_project_agent import FinalProjectAgent
from agents.frontend_agent import FrontendAgent
from agents.product_manager_agent import ProductManagerAgent
from agents.qa_agent import QAAgent
from agents.reviewer_agent import ReviewerAgent
from agents.task_planner_agent import TaskPlannerAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import (
    CodeArtifact,
    DeploymentArtifact,
    DocumentationArtifact,
    ErrorRecord,
    FinalProjectOutput,
    ImplementationTask,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TaskPlan,
    TestArtifact,
    TestPlan,
)

TestArtifact.__test__ = False
TestPlan.__test__ = False
from tools.debug.log_parser import RegexLogParser
from tools.debug.sandbox import FakeExecutionSandbox
from tools.devops.yaml_validator import FakeConfigValidator, ValidationReport
from tools.documentation.markdown_linter import FakeMarkdownLinter, LintReport
from tools.final_project.packager import FakeProjectPackager, PackageReport
from tools.qa.test_runner import FakeTestRunner, TestRunResult
from tools.reviewer.coverage_reader import CoverageReport, FakeCoverageReader
from tools.reviewer.static_analyzer import FakeStaticAnalyzer, StaticAnalysisReport
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
from workflows.interrupts import human_approval_node


# ===========================================================================
# Sample Artifact Fixtures for Full 11-Agent Pipeline
# ===========================================================================


def _pm_llm_dict() -> Dict[str, Any]:
    return {
        "functional_requirements": ["User authentication", "Dashboard interface"],
        "non_functional_requirements": ["Sub-second latency", "99.9% uptime"],
        "user_stories": ["As a developer I want full automation"],
        "acceptance_criteria": ["All tests pass"],
        "constraints": ["Python 3.11+"],
        "business_goals": ["Autonomous multi-agent software development"],
        "produced_by": "ProductManagerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def _architect_llm_dict() -> Dict[str, Any]:
    return {
        "overview": "Microservice container architecture",
        "technology_stack": {"backend": "FastAPI", "frontend": "React", "db": "PostgreSQL"},
        "api_specifications": [{"endpoint": "/health", "method": "GET", "description": "Check"}],
        "database_design": {"users": {"id": "UUID PK"}},
        "security_architecture": {"auth": "JWT Bearer"},
        "scalability_strategy": "Horizontal scaling",
        "deployment_model": "Docker containerized",
        "produced_by": "ArchitectAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def _task_planner_llm_dict() -> Dict[str, Any]:
    return {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Implement core API",
                "description": "FastAPI health and core endpoints",
                "assigned_agent": "BackendAgent",
                "priority": 10,
                "dependencies": [],
                "acceptance_criteria": ["Health check returns ok"],
                "status": "pending",
            },
            {
                "task_id": "TASK-002",
                "title": "Implement Web Dashboard",
                "description": "React UI views",
                "assigned_agent": "FrontendAgent",
                "priority": 20,
                "dependencies": ["TASK-001"],
                "acceptance_criteria": ["Dashboard renders"],
                "status": "pending",
            },
            {
                "task_id": "TASK-003",
                "title": "QA automated tests",
                "description": "Write automated test suite",
                "assigned_agent": "QAAgent",
                "priority": 30,
                "dependencies": ["TASK-001", "TASK-002"],
                "acceptance_criteria": ["All tests pass"],
                "status": "pending",
            },
        ],
        "execution_order": ["TASK-001", "TASK-002", "TASK-003"],
        "produced_by": "TaskPlannerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def _backend_llm_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "src/app.py",
                "language": "python",
                "content": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'status': 'ok'}\n",
                "artifact_type": "backend",
                "produced_by": "BackendAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "task_statuses": {"TASK-001": "completed"},
        "summary": "Implemented FastAPI backend",
    }


def _frontend_llm_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "frontend/App.tsx",
                "language": "typescript",
                "content": "import React from 'react';\nexport const App = () => <div>App</div>;\n",
                "artifact_type": "frontend",
                "produced_by": "FrontendAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "task_statuses": {"TASK-002": "completed"},
        "summary": "Implemented React frontend",
    }


def _qa_llm_dict() -> Dict[str, Any]:
    return {
        "test_plan": {
            "strategy": "Unit and integration tests for backend and frontend",
            "coverage_goals": {"src/app.py": "90%"},
            "test_suites": ["unit"],
            "quality_metrics": {"target_pass_rate": 1.0},
            "produced_by": "QAAgent",
            "produced_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
        "test_artifacts": [
            {
                "file_path": "tests/test_app.py",
                "content": "def test_health(): assert True\n",
                "test_type": "unit",
                "coverage_target": "src/app.py",
                "produced_by": "QAAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "ai_findings": ["All core endpoints covered by unit tests"],
    }


def _reviewer_llm_dict() -> Dict[str, Any]:
    return {
        "overall_status": "approved",
        "rejection_scope": None,
        "code_quality_score": 9.5,
        "architecture_compliance": True,
        "engineering_standards_compliance": True,
        "security_findings": [],
        "performance_findings": [],
        "maintainability_findings": [],
        "recommendations": ["Ready for production deployment"],
        "testing_coverage_assessment": "Comprehensive test plan and 100% passing tests",
    }


def _devops_llm_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "Dockerfile",
                "content": "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"src/app.py\"]\n",
                "artifact_type": "dockerfile",
            },
            {
                "file_path": ".github/workflows/ci.yml",
                "content": "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v3\n",
                "artifact_type": "ci_cd_pipeline",
            },
        ],
        "summary": "Generated Dockerfile and GitHub Actions workflow",
    }


def _docs_llm_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {"file_path": "README.md", "doc_type": "readme", "content": "# Autonomous App\nOverview"},
            {"file_path": "docs/architecture.md", "doc_type": "architecture", "content": "# Architecture\nDesign"},
            {"file_path": "docs/api_reference.md", "doc_type": "api_reference", "content": "# API\nEndpoints"},
            {"file_path": "docs/deployment.md", "doc_type": "deployment_guide", "content": "# Deploy\nSteps"},
        ],
        "summary": "Generated full project documentation suite",
    }


def _final_project_llm_dict() -> Dict[str, Any]:
    return {
        "summary": (
            "Complete delivery of autonomous engineering project. Features a high-performance "
            "FastAPI backend, React frontend, 100% verified test suite, Dockerized infrastructure, "
            "and exhaustive documentation."
        )
    }


# ===========================================================================
# 1. Full 11-Agent End-to-End Pipeline Happy Path Test
# ===========================================================================


def test_full_eleven_agent_end_to_end_pipeline():
    """End-to-end integration test executing all 11 real agents with test doubles across StateGraph."""
    pm_agent = ProductManagerAgent(
        model_name="pm-model",
        llm_client=FakeLLMClient(responses=[_pm_llm_dict()]),
    )
    architect_agent = ArchitectAgent(
        model_name="arch-model",
        llm_client=FakeLLMClient(responses=[_architect_llm_dict()]),
    )
    task_planner_agent = TaskPlannerAgent(
        model_name="planner-model",
        llm_client=FakeLLMClient(responses=[_task_planner_llm_dict()]),
    )
    backend_agent = BackendAgent(
        model_name="backend-model",
        llm_client=FakeLLMClient(responses=[_backend_llm_dict()]),
    )
    frontend_agent = FrontendAgent(
        model_name="frontend-model",
        llm_client=FakeLLMClient(responses=[_frontend_llm_dict()]),
    )
    qa_agent = QAAgent(
        model_name="qa-model",
        llm_client=FakeLLMClient(responses=[_qa_llm_dict()]),
        test_runner=FakeTestRunner(
            results=[
                TestRunResult(
                    passed=True,
                    total_tests=1,
                    passed_tests=1,
                    failed_tests=0,
                    duration_seconds=0.1,
                    output="1 passed",
                )
            ]
        ),
    )
    debug_agent = DebugAgent(
        model_name="debug-model",
        llm_client=FakeLLMClient(responses=[]),
        sandbox=FakeExecutionSandbox(),
        log_parser=RegexLogParser(),
    )
    reviewer_agent = ReviewerAgent(
        model_name="reviewer-model",
        llm_client=FakeLLMClient(responses=[_reviewer_llm_dict()]),
        static_analyzer=FakeStaticAnalyzer(results=[StaticAnalysisReport(passed=True)]),
        coverage_reader=FakeCoverageReader(results=[CoverageReport(coverage_percentage=95.0)]),
    )
    devops_agent = DevOpsAgent(
        model_name="devops-model",
        llm_client=FakeLLMClient(responses=[_devops_llm_dict()]),
        validator=FakeConfigValidator(results=[ValidationReport(valid=True)]),
    )
    doc_agent = DocumentationAgent(
        model_name="doc-model",
        llm_client=FakeLLMClient(responses=[_docs_llm_dict()]),
        linter=FakeMarkdownLinter(results=[LintReport(passed=True)]),
    )
    final_project_agent = FinalProjectAgent(
        model_name="final-model",
        llm_client=FakeLLMClient(responses=[_final_project_llm_dict()]),
        packager=FakeProjectPackager(results=[PackageReport(success=True)]),
    )

    agents = {
        NODE_PRODUCT_MANAGER: pm_agent.invoke,
        NODE_ARCHITECT: architect_agent.invoke,
        NODE_TASK_PLANNER: task_planner_agent.invoke,
        NODE_BACKEND: backend_agent.invoke,
        NODE_FRONTEND: frontend_agent.invoke,
        NODE_QA: qa_agent.invoke,
        NODE_DEBUG: debug_agent.invoke,
        NODE_REVIEWER: reviewer_agent.invoke,
        NODE_DEVOPS: devops_agent.invoke,
        NODE_DOCUMENTATION: doc_agent.invoke,
        NODE_FINAL_PROJECT: final_project_agent.invoke,
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(
        project_name="AutonomousEngineeringOrg",
        user_input="Build complete end-to-end autonomous software system",
    )
    config = {"configurable": {"thread_id": "full-e2e-pipeline-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    # 1. Pipeline reached END with STATUS_COMPLETED
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["forward_stage"] == 10
    assert final_state["current_agent"] == "FinalProjectAgent"
    # FinalProjectAgent writes next_agent=None per design.md §1.11; under replace_reducer None is a no-op
    assert final_state["next_agent"] in (None, "FinalProjectAgent")

    # 2. FinalProjectOutput populated and conforms to all invariants
    final_output: FinalProjectOutput = final_state["final_output"]
    assert isinstance(final_output, FinalProjectOutput)
    assert final_output.project_name == "AutonomousEngineeringOrg"
    assert "Complete delivery of autonomous engineering project" in final_output.summary
    assert final_output.overall_review_status == "approved"
    assert final_output.produced_by == "FinalProjectAgent"

    # 3. File count invariant strictly verified:
    # 1 backend + 1 frontend + 1 test + 2 devops + 4 docs = 9 total files
    expected_files = (
        len(final_state["code_artifacts"])
        + len(final_state["test_artifacts"])
        + len(final_state["deployment_artifacts"])
        + len(final_state["documentation_artifacts"])
    )
    assert final_output.total_files_generated == expected_files
    assert expected_files == 9
    assert len(final_output.code_paths) == 2
    assert len(final_output.test_paths) == 1
    assert len(final_output.deployment_paths) == 2
    assert len(final_output.documentation_paths) == 4

    # 4. Checkpoints contain completed timestamp
    assert "completed" in final_state["checkpoints"]


# ===========================================================================
# 2. Transient Failure Then Success
# ===========================================================================


def test_graph_final_project_transient_failure_then_success():
    """FinalProjectAgent recovers from empty summary via revision prompt and completes graph."""
    client = FakeLLMClient(
        responses=[
            {"summary": ""},  # Attempt 1: empty
            _final_project_llm_dict(),  # Attempt 2: valid
        ]
    )
    final_agent = FinalProjectAgent(
        model_name="final-model",
        llm_client=client,
        packager=FakeProjectPackager(),
    )

    def _dummy_doc(state):
        return {
            "workflow_status": STATUS_DOCUMENTATION_COMPLETE,
            "current_agent": NODE_DOCUMENTATION,
            "code_artifacts": {"src/app.py": {"content": "ok"}},
            "test_artifacts": {"tests/test.py": {"content": "ok"}},
            "deployment_artifacts": {"Dockerfile": {"content": "ok"}},
            "documentation_artifacts": {"README.md": {"content": "ok"}},
        }

    from tests.workflows.test_graph_documentation import _build_pipeline_stubs

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = _dummy_doc
    agents[NODE_FINAL_PROJECT] = final_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="FinalTransientApp", user_input="Build app")
    config = {"configurable": {"thread_id": "final-transient-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert len(client.calls) == 2


# ===========================================================================
# 3. Exhaustion: Unconditional final_project -> END & Human Approval Resumption
# ===========================================================================


def test_graph_final_project_exhaustion_approval_and_rejection():
    """Exhaustion in FinalProjectAgent terminates graph at END (unconditional edge); approval retries and succeeds; rejection fails."""
    # Graph execution reaches END with human_approval_required=True
    client = FakeLLMClient(
        responses=[
            RuntimeError("Summary gen fail 1"),
            RuntimeError("Summary gen fail 2"),
            RuntimeError("Summary gen fail 3"),
        ]
    )
    final_agent = FinalProjectAgent(
        model_name="final-model",
        llm_client=client,
        packager=FakeProjectPackager(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    def _dummy_doc(state):
        return {
            "workflow_status": STATUS_DOCUMENTATION_COMPLETE,
            "current_agent": NODE_DOCUMENTATION,
            "code_artifacts": {"src/app.py": {"content": "ok"}},
        }

    from tests.workflows.test_graph_documentation import _build_pipeline_stubs

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = _dummy_doc
    agents[NODE_FINAL_PROJECT] = final_agent.invoke

    checkpointer = MemorySaver()
    graph = build_graph(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "final-exhaust-thread"}}
    state = create_initial_state(project_name="FinalExhaustApp", user_input="Build app")

    final_state = graph.invoke(state, config=config)

    # Because final_project -> END is unconditional, graph halts at END with exhaustion metadata
    assert final_state["human_approval_required"] is True
    assert final_state["next_agent"] == "FinalProjectAgent"
    assert final_state["retry_counts"]["FinalProjectAgent"] == 3
    assert final_state.get("final_output") is None

    # Subcase A: Human approves retry
    resumed_client = FakeLLMClient(responses=[_final_project_llm_dict()])
    resumed_agent = FinalProjectAgent(
        model_name="final-model",
        llm_client=resumed_client,
        packager=FakeProjectPackager(),
    )
    with patch("workflows.interrupts.interrupt", return_value={"decision": "approved"}):
        approval_update = human_approval_node(final_state)

    assert approval_update["human_approval_required"] is False
    assert approval_update["retry_counts"]["FinalProjectAgent"] == 0

    merged_state = dict(final_state, **approval_update)
    resumed_result = resumed_agent.execute(merged_state)
    assert resumed_result["workflow_status"] == STATUS_COMPLETED
    assert resumed_result["final_output"] is not None

    # Subcase B: Human rejects retry
    with patch("workflows.interrupts.interrupt", return_value={"decision": "rejected"}):
        rejection_update = human_approval_node(final_state)

    assert rejection_update["workflow_status"] == STATUS_FAILED


# ===========================================================================
# 4. Model Name Passthrough
# ===========================================================================


def test_graph_final_project_model_name_passthrough():
    """Verify that FinalProjectAgent passes model_name configured in constructor to LLMClient."""
    client = FakeLLMClient(responses=[_final_project_llm_dict()])
    final_agent = FinalProjectAgent(
        model_name="custom-final-gpt4o-mini",
        llm_client=client,
        packager=FakeProjectPackager(),
    )

    def _dummy_doc(state):
        return {
            "workflow_status": STATUS_DOCUMENTATION_COMPLETE,
            "current_agent": NODE_DOCUMENTATION,
            "code_artifacts": {"src/app.py": {"content": "ok"}},
        }

    from tests.workflows.test_graph_documentation import _build_pipeline_stubs

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = _dummy_doc
    agents[NODE_FINAL_PROJECT] = final_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="FinalModelApp", user_input="Build app")
    config = {"configurable": {"thread_id": "final-model-thread"}}

    _ = graph.invoke(initial_state, config=config)

    assert len(client.calls) == 1
    assert client.calls[0]["model_name"] == "custom-final-gpt4o-mini"
