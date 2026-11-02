"""
tests/workflows/test_graph_devops.py

Integration tests verifying real DevOpsAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.debug_agent import DebugAgent
from agents.devops_agent import DevOpsAgent
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
    TaskPlan,
    TestArtifact,
    TestPlan,
)

TestArtifact.__test__ = False
TestPlan.__test__ = False
from state.reducers import unresolved_errors
from tools.debug.sandbox import FakeExecutionSandbox
from tools.devops.yaml_validator import FakeConfigValidator, ValidationReport
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
    STATUS_DEBUG_REQUIRED,
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


def _sample_devops_output_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "Dockerfile",
                "content": "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"src/app.py\"]\n",
                "artifact_type": "dockerfile",
            },
            {
                "file_path": ".github/workflows/ci.yml",
                "content": "name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v3\n",
                "artifact_type": "ci_cd_pipeline",
            },
        ],
        "summary": "Generated Dockerfile and GitHub Actions workflow",
    }


def _sample_debug_resolution_dict(target_agent: str = "DevOpsAgent") -> Dict[str, Any]:
    return {
        "root_cause_analysis": "YAML syntax error resolved in ci.yml",
        "hypothesis": "Correcting indentation fixes syntax validation.",
        "corrected_code_artifacts": [
            {
                "file_path": "src/app.py",
                "language": "python",
                "content": "print('Hello World fixed')",
                "artifact_type": "backend",
                "produced_by": "DebugAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "corrected_test_artifacts": [],
        "target_agent": target_agent,
        "resolution_notes": "Patched configuration syntax.",
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
            NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}
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
        NODE_DOCUMENTATION: _make_dummy_agent(
            NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}
        ),
        NODE_FINAL_PROJECT: _make_dummy_agent(
            NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}
        ),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


# ===========================================================================
# 1. Happy Path
# ===========================================================================


def test_graph_devops_happy_path():
    """DevOpsAgent generates artifacts; routes to Documentation -> FinalProject -> Completed."""
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    validator = FakeConfigValidator()
    devops = DevOpsAgent(
        model_name="test-devops-model",
        llm_client=client,
        validator=validator,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DEVOPS] = devops.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DevOpsApp", user_input="Build app")
    config = {"configurable": {"thread_id": "devops-happy-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["forward_stage"] >= 8
    assert "devops_complete" in final_state["checkpoints"]
    assert "Dockerfile" in final_state["deployment_artifacts"]
    assert ".github/workflows/ci.yml" in final_state["deployment_artifacts"]


# ===========================================================================
# 2. Transient Failure Then Success
# ===========================================================================


def test_graph_devops_transient_failure_then_success():
    """DevOpsAgent recovers from transient validation failure internally and succeeds."""
    client = FakeLLMClient(
        responses=[
            # Attempt 1: missing required ci_cd_pipeline
            {
                "artifacts": [
                    {
                        "file_path": "Dockerfile",
                        "content": "FROM python:3.11",
                        "artifact_type": "dockerfile",
                    }
                ],
                "summary": "Incomplete",
            },
            # Attempt 2: valid output
            _sample_devops_output_dict(),
        ]
    )
    validator = FakeConfigValidator()
    devops = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DEVOPS] = devops.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DevOpsTransientApp", user_input="Build app")
    config = {"configurable": {"thread_id": "devops-transient-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert len(client.calls) == 2


# ===========================================================================
# 3. Validation Failure -> Real DebugAgent Loop -> Success
# ===========================================================================


def test_graph_devops_failure_routes_to_real_debug_agent_loop():
    """DevOps syntax failure routes to DebugAgent; DebugAgent resolves and routes back to DevOps."""
    devops_client = FakeLLMClient(
        responses=[
            _sample_devops_output_dict(),
            _sample_devops_output_dict(),
        ]
    )
    # Validator fails on pass 1, passes on pass 2
    validator = FakeConfigValidator(
        results=[
            ValidationReport(valid=False, errors=["YAML syntax error in ci.yml"]),
            ValidationReport(valid=True),
        ]
    )
    devops_agent = DevOpsAgent(
        model_name="test-model",
        llm_client=devops_client,
        validator=validator,
    )

    debug_client = FakeLLMClient(responses=[_sample_debug_resolution_dict("DevOpsAgent")])
    debug_agent = DebugAgent(
        model_name="test-model",
        llm_client=debug_client,
        sandbox=FakeExecutionSandbox(),
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DEVOPS] = devops_agent.invoke
    agents[NODE_DEBUG] = debug_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DevOpsDebugLoopApp", user_input="Build app")
    config = {"configurable": {"thread_id": "devops-debug-loop-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["total_debug_cycles"] == 1
    assert final_state["current_debug_target"] == ""
    assert unresolved_errors(final_state["errors"]) == []


# ===========================================================================
# 4. Exhaustion Then Approve and Reject
# ===========================================================================


def test_graph_devops_exhaustion_approval_and_rejection():
    """DevOpsAgent exhaustion triggers HumanApprovalNode pause; approve retries and passes; reject fails."""
    # Subcase A: Human approves retry
    client_a = FakeLLMClient(
        responses=[
            RuntimeError("DevOps fail 1"),
            RuntimeError("DevOps fail 2"),
            RuntimeError("DevOps fail 3"),
            # Resumed attempt after approval: succeeds
            _sample_devops_output_dict(),
        ]
    )
    devops_a = DevOpsAgent(
        model_name="test-model",
        llm_client=client_a,
        validator=FakeConfigValidator(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_DEVOPS] = devops_a.invoke

    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "devops-exhaust-approve"}}
    state_a = create_initial_state(project_name="DevOpsExhaustApp", user_input="Build app")

    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snapshot_a = graph_a.get_state(config_a)
    assert snapshot_a.values["human_approval_required"] is True
    assert snapshot_a.values["next_agent"] == "DevOpsAgent"
    assert snapshot_a.values["retry_counts"]["DevOpsAgent"] == 3

    # Human approves
    for _ in graph_a.stream(Command(resume={"decision": "approved"}), config=config_a):
        pass

    final_a = graph_a.get_state(config_a).values
    assert final_a["workflow_status"] == STATUS_COMPLETED
    assert final_a["retry_counts"]["DevOpsAgent"] == 0

    # Subcase B: Human rejects
    client_b = FakeLLMClient(
        responses=[
            RuntimeError("DevOps fail 1"),
            RuntimeError("DevOps fail 2"),
            RuntimeError("DevOps fail 3"),
        ]
    )
    devops_b = DevOpsAgent(
        model_name="test-model",
        llm_client=client_b,
        validator=FakeConfigValidator(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_b = _build_pipeline_stubs()
    agents_b[NODE_DEVOPS] = devops_b.invoke

    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "devops-exhaust-reject"}}
    state_b = create_initial_state(project_name="DevOpsExhaustApp2", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    for _ in graph_b.stream(Command(resume={"decision": "rejected"}), config=config_b):
        pass

    final_b = graph_b.get_state(config_b).values
    assert final_b["workflow_status"] == STATUS_FAILED


# ===========================================================================
# 5. Model Name Passthrough
# ===========================================================================


def test_graph_devops_model_name_passthrough():
    """Verify that DevOpsAgent passes model_name configured in constructor to LLMClient."""
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    devops = DevOpsAgent(
        model_name="custom-devops-v1",
        llm_client=client,
        validator=FakeConfigValidator(),
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DEVOPS] = devops.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DevOpsModelApp", user_input="Build app")
    config = {"configurable": {"thread_id": "devops-model-thread"}}

    _ = graph.invoke(initial_state, config=config)

    assert len(client.calls) == 1
    assert client.calls[0]["model_name"] == "custom-devops-v1"
