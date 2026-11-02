"""
tests/workflows/test_graph_documentation.py

Integration tests verifying real DocumentationAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.devops_agent import DevOpsAgent
from agents.documentation_agent import DocumentationAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import (
    CodeArtifact,
    DeploymentArtifact,
    DocumentationArtifact,
    ErrorRecord,
    FinalProjectOutput,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TestArtifact,
    TestPlan,
)

TestArtifact.__test__ = False
TestPlan.__test__ = False
from tools.devops.yaml_validator import FakeConfigValidator
from tools.documentation.markdown_linter import FakeMarkdownLinter, LintReport
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


# ===========================================================================
# Test Fixtures & Pipeline Stubs
# ===========================================================================


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["Render documentation"],
        non_functional_requirements=["High readability"],
        user_stories=["As a dev I want docs"],
        acceptance_criteria=["README available"],
        constraints=["Markdown format"],
        business_goals=["Clear developer onboarding"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture() -> SystemArchitecture:
    return SystemArchitecture(
        overview="Containerized microservice architecture",
        technology_stack={"backend": "FastAPI", "database": "PostgreSQL"},
        api_specifications=[
            {"endpoint": "/health", "method": "GET", "description": "Health check"}
        ],
        components=[{"name": "API", "description": "Main API"}],
        data_models=[{"name": "Item", "fields": {"id": "str"}}],
        security_considerations=["Container non-root user"],
        scalability_considerations=["Autoscaling"],
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "src/app.py": CodeArtifact(
            file_path="src/app.py",
            language="python",
            content="print('Hello World')",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_documentation_output_dict(
    omit_doc_type: str = "",
) -> Dict[str, Any]:
    doc_types = [
        ("README.md", "readme", "# DocApp\nOverview and quickstart."),
        ("docs/architecture.md", "architecture", "# Architecture\nSystem design details."),
        ("docs/api_reference.md", "api_reference", "# API Reference\nEndpoints and schemas."),
        ("docs/deployment.md", "deployment_guide", "# Deployment Guide\nSetup instructions."),
    ]
    artifacts = []
    for path, dtype, content in doc_types:
        if omit_doc_type and dtype == omit_doc_type:
            continue
        artifacts.append({"file_path": path, "doc_type": dtype, "content": content})

    return {
        "artifacts": artifacts,
        "summary": "Generated README, architecture, API reference, and deployment guide.",
    }


def _make_dummy_agent(name: str, output: Dict[str, Any]):
    def _agent(state: EngineeringState):
        ret = dict(output)
        ret["current_agent"] = name
        return ret

    return _agent


def _build_pipeline_stubs() -> Dict[str, Any]:
    return {
        NODE_PRODUCT_MANAGER: _make_dummy_agent(
            NODE_PRODUCT_MANAGER,
            {
                "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
                "requirements": _sample_valid_requirements(),
            },
        ),
        NODE_ARCHITECT: _make_dummy_agent(
            NODE_ARCHITECT,
            {
                "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
                "architecture": _sample_valid_architecture(),
            },
        ),
        NODE_TASK_PLANNER: _make_dummy_agent(
            NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}
        ),
        NODE_BACKEND: _make_dummy_agent(
            NODE_BACKEND,
            {
                "workflow_status": STATUS_BACKEND_COMPLETE,
                "code_artifacts": _sample_valid_code_artifacts(),
            },
        ),
        NODE_FRONTEND: _make_dummy_agent(
            NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}
        ),
        NODE_QA: _make_dummy_agent(
            NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}
        ),
        NODE_REVIEWER: _make_dummy_agent(
            NODE_REVIEWER,
            {
                "workflow_status": STATUS_REVIEW_COMPLETE,
                "review_report": ReviewReport(
                    overall_status="approved",
                    code_quality_score=9.5,
                    architecture_compliance=True,
                    engineering_standards_compliance=True,
                    produced_by="ReviewerAgent",
                    produced_at="2026-01-01T00:00:00Z",
                    version=1,
                ),
            },
        ),
        NODE_DEVOPS: _make_dummy_agent(
            NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}
        ),
        NODE_FINAL_PROJECT: _make_dummy_agent(
            NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}
        ),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


# ===========================================================================
# 1. Happy Path
# ===========================================================================


def test_graph_documentation_happy_path():
    """DocumentationAgent generates artifacts; routes to FinalProject -> Completed."""
    client = FakeLLMClient(responses=[_sample_documentation_output_dict()])
    linter = FakeMarkdownLinter()
    doc_agent = DocumentationAgent(
        model_name="test-doc-model",
        llm_client=client,
        linter=linter,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = doc_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DocApp", user_input="Build app")
    config = {"configurable": {"thread_id": "doc-happy-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["forward_stage"] >= 9
    assert "documentation_complete" in final_state["checkpoints"]
    assert "README.md" in final_state["documentation_artifacts"]
    assert "docs/architecture.md" in final_state["documentation_artifacts"]
    assert "docs/api_reference.md" in final_state["documentation_artifacts"]
    assert "docs/deployment.md" in final_state["documentation_artifacts"]


# ===========================================================================
# 2. Transient Failure Then Success
# ===========================================================================


def test_graph_documentation_transient_failure_then_success():
    """DocumentationAgent recovers from transient validation failure internally and succeeds."""
    client = FakeLLMClient(
        responses=[
            # Attempt 1: missing required deployment_guide
            _sample_documentation_output_dict(omit_doc_type="deployment_guide"),
            # Attempt 2: valid output with all 4 doc types
            _sample_documentation_output_dict(),
        ]
    )
    linter = FakeMarkdownLinter()
    doc_agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = doc_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DocTransientApp", user_input="Build app")
    config = {"configurable": {"thread_id": "doc-transient-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert len(client.calls) == 2


# ===========================================================================
# 3. Exhaustion Then Approve and Reject
# ===========================================================================


def test_graph_documentation_exhaustion_approval_and_rejection():
    """DocumentationAgent exhaustion triggers HumanApprovalNode pause; approve retries and passes; reject fails."""
    # Subcase A: Human approves retry
    client_a = FakeLLMClient(
        responses=[
            RuntimeError("Doc fail 1"),
            RuntimeError("Doc fail 2"),
            RuntimeError("Doc fail 3"),
            # Resumed attempt after approval: succeeds
            _sample_documentation_output_dict(),
        ]
    )
    doc_a = DocumentationAgent(
        model_name="test-model",
        llm_client=client_a,
        linter=FakeMarkdownLinter(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_DOCUMENTATION] = doc_a.invoke

    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "doc-exhaust-approve"}}
    state_a = create_initial_state(project_name="DocExhaustApp", user_input="Build app")

    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snapshot_a = graph_a.get_state(config_a)
    assert snapshot_a.values["human_approval_required"] is True
    assert snapshot_a.values["next_agent"] == "DocumentationAgent"
    assert snapshot_a.values["retry_counts"]["DocumentationAgent"] == 3

    # Human approves
    for _ in graph_a.stream(Command(resume={"decision": "approved"}), config=config_a):
        pass

    final_a = graph_a.get_state(config_a).values
    assert final_a["workflow_status"] == STATUS_COMPLETED
    assert final_a["retry_counts"]["DocumentationAgent"] == 0

    # Subcase B: Human rejects
    client_b = FakeLLMClient(
        responses=[
            RuntimeError("Doc fail 1"),
            RuntimeError("Doc fail 2"),
            RuntimeError("Doc fail 3"),
        ]
    )
    doc_b = DocumentationAgent(
        model_name="test-model",
        llm_client=client_b,
        linter=FakeMarkdownLinter(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_b = _build_pipeline_stubs()
    agents_b[NODE_DOCUMENTATION] = doc_b.invoke

    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "doc-exhaust-reject"}}
    state_b = create_initial_state(project_name="DocExhaustApp2", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    for _ in graph_b.stream(Command(resume={"decision": "rejected"}), config=config_b):
        pass

    final_b = graph_b.get_state(config_b).values
    assert final_b["workflow_status"] == STATUS_FAILED


# ===========================================================================
# 4. Linter Failure Exhausts to Human Approval (No Debug Route)
# ===========================================================================


def test_graph_documentation_linter_failure_exhausts_to_human_approval():
    """Markdown linter failure exhausts directly to HumanApprovalNode (no debug route); approved retry passes."""
    client = FakeLLMClient(
        responses=[
            _sample_documentation_output_dict(),
            _sample_documentation_output_dict(),
        ]
    )
    # Pass 1: linter fails; Pass 2: linter passes
    linter = FakeMarkdownLinter(
        results=[
            LintReport(passed=False, errors=["Heading level hierarchy broken in README.md"]),
            LintReport(passed=True),
        ]
    )
    doc_agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = doc_agent.invoke

    checkpointer = MemorySaver()
    graph = build_graph(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "doc-linter-exhaust-thread"}}
    state = create_initial_state(project_name="DocLinterApp", user_input="Build app")

    for _ in graph.stream(state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["next_agent"] == "DocumentationAgent"
    assert "Markdown linting failed" in snapshot.values["errors"][0].message

    # Human approves retry
    for _ in graph.stream(Command(resume={"decision": "approved"}), config=config):
        pass

    final_state = graph.get_state(config).values
    assert final_state["workflow_status"] == STATUS_COMPLETED


# ===========================================================================
# 5. Model Name Passthrough
# ===========================================================================


def test_graph_documentation_model_name_passthrough():
    """Verify that DocumentationAgent passes model_name configured in constructor to LLMClient."""
    client = FakeLLMClient(responses=[_sample_documentation_output_dict()])
    doc_agent = DocumentationAgent(
        model_name="custom-doc-v1",
        llm_client=client,
        linter=FakeMarkdownLinter(),
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DOCUMENTATION] = doc_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DocModelApp", user_input="Build app")
    config = {"configurable": {"thread_id": "doc-model-thread"}}

    _ = graph.invoke(initial_state, config=config)

    assert len(client.calls) == 1
    assert client.calls[0]["model_name"] == "custom-doc-v1"
