"""
tests/agents/test_final_project_agent.py

Comprehensive unit and behavioral tests for FinalProjectAgent in agents/final_project_agent.py.
"""

import copy
from typing import Any, Dict, List, Optional
import pytest

from agents.final_project_agent import (
    STATUS_COMPLETED,
    FinalProjectAgent,
    FinalProjectSummary,
)
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    DeploymentArtifact,
    DocumentationArtifact,
    FinalProjectOutput,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TestArtifact,
    TestPlan,
)

TestArtifact.__test__ = False
TestPlan.__test__ = False
from tools.final_project.packager import (
    FakeProjectPackager,
    PackageReport,
    validate_safe_path,
)
from tools.shared.llm_client import FakeLLMClient


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["Render complete project"],
        non_functional_requirements=["High reliability"],
        user_stories=["Deliver software"],
        acceptance_criteria=["All artifacts present"],
        constraints=["Python 3.11+"],
        business_goals=["Production delivery"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture() -> SystemArchitecture:
    return SystemArchitecture(
        overview="Full-stack containerized architecture",
        technology_stack={"backend": "FastAPI", "frontend": "React", "db": "PostgreSQL"},
        api_specifications=[{"endpoint": "/api/v1", "method": "GET", "description": "Index"}],
        components=[{"name": "API", "description": "Main service"}],
        data_models=[{"name": "Item", "fields": {"id": "str"}}],
        security_considerations=["Authentication required"],
        scalability_considerations=["Horizontal scaling"],
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "src/app.py": CodeArtifact(
            file_path="src/app.py",
            language="python",
            content="print('app')",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
        "src/ui.tsx": CodeArtifact(
            file_path="src/ui.tsx",
            language="typescript",
            content="export const App = () => <div>App</div>;",
            artifact_type="frontend",
            produced_by="FrontendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
    }


def _sample_test_artifacts() -> Dict[str, TestArtifact]:
    return {
        "tests/test_app.py": TestArtifact(
            file_path="tests/test_app.py",
            language="python",
            content="def test_ok(): pass",
            test_type="unit",
            target_artifact="src/app.py",
            produced_by="QAAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_deployment_artifacts() -> Dict[str, DeploymentArtifact]:
    return {
        "Dockerfile": DeploymentArtifact(
            file_path="Dockerfile",
            content="FROM python:3.11",
            artifact_type="dockerfile",
            produced_by="DevOpsAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_documentation_artifacts() -> Dict[str, DocumentationArtifact]:
    return {
        "README.md": DocumentationArtifact(
            file_path="README.md",
            content="# Project README",
            doc_type="readme",
            produced_by="DocumentationAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_review_report(overall_status: str = "approved") -> ReviewReport:
    return ReviewReport(
        overall_status=overall_status,
        code_quality_score=9.0,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _build_test_state(
    retry_counts: Optional[Dict[str, int]] = None,
    overall_review_status: str = "approved",
) -> Dict[str, Any]:
    state = create_initial_state(project_name="DeliverApp", user_input="Deliver full application")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["code_artifacts"] = _sample_code_artifacts()
    state["test_artifacts"] = _sample_test_artifacts()
    state["deployment_artifacts"] = _sample_deployment_artifacts()
    state["documentation_artifacts"] = _sample_documentation_artifacts()
    state["review_report"] = _sample_review_report(overall_status=overall_review_status)
    state["workflow_status"] = "documentation_complete"
    state["forward_stage"] = 9
    state["pipeline_started_at"] = "2026-01-01T00:00:00+00:00"
    if retry_counts is not None:
        state["retry_counts"] = retry_counts
    return state


# ===========================================================================
# 1. Constructor Validation
# ===========================================================================


def test_constructor_validates_required_arguments():
    """FinalProjectAgent constructor rejects empty model_name, None llm_client, or missing packager."""
    client = FakeLLMClient(responses=[])
    packager = FakeProjectPackager()

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        FinalProjectAgent(
            model_name="",
            llm_client=client,
            packager=packager,
        )

    with pytest.raises(ValueError, match="llm_client must not be None"):
        FinalProjectAgent(
            model_name="test-model",
            llm_client=None,  # type: ignore
            packager=packager,
        )

    with pytest.raises(ValueError, match="packager must not be None"):
        FinalProjectAgent(
            model_name="test-model",
            llm_client=client,
            packager=None,  # type: ignore
        )


# ===========================================================================
# 2. Fast-Fail Guards
# ===========================================================================


def test_execute_fast_fails_when_retry_limit_reached():
    """Fast-fails with retry_exhausted if retry count already equals or exceeds ceiling."""
    client = FakeLLMClient(responses=[])
    packager = FakeProjectPackager()
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
        max_retries=3,
    )
    state = _build_test_state(retry_counts={"FinalProjectAgent": 3})

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "FinalProjectAgent"
    assert result["execution_metadata"]["FinalProjectAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result  # Preserved unchanged


# ===========================================================================
# 3. Path Safety Guard
# ===========================================================================


def test_packager_path_safety_guard_rejects_absolute_paths_and_dotdot():
    """Path safety validator strictly rejects absolute paths, drive specifiers, and '..' traversal."""
    # Safe relative paths must pass
    validate_safe_path("src/app.py")
    validate_safe_path("README.md")
    validate_safe_path("dist/package.zip")

    # Insecure paths must raise ValueError
    with pytest.raises(ValueError, match="Insecure path detected.*traversal"):
        validate_safe_path("../etc/passwd")

    with pytest.raises(ValueError, match="Insecure path detected.*traversal"):
        validate_safe_path("src/../../secret.txt")

    with pytest.raises(ValueError, match="Insecure path detected.*absolute"):
        validate_safe_path("/root/secret")

    with pytest.raises(ValueError, match="Insecure path detected.*drive"):
        validate_safe_path("C:\\windows\\system32")

    with pytest.raises(ValueError, match="Invalid path"):
        validate_safe_path("")


def test_packager_detects_unsafe_artifact_paths_and_exhausts():
    """Unsafe artifact paths passed to execute route to tool exception exhaustion."""
    client = FakeLLMClient(responses=[{"summary": "Executive summary"}])
    packager = FakeProjectPackager()
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
    )
    state = _build_test_state()
    # Inject unsafe artifact path with '..' segment
    state["code_artifacts"]["../escape.py"] = CodeArtifact(
        file_path="../escape.py",
        language="python",
        content="malicious",
        artifact_type="backend",
        produced_by="BackendAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "FinalProjectAgent"
    assert "Project packager tool exception" in result["errors"][0].message
    assert "Insecure path detected" in result["errors"][0].message
    assert len(client.calls) == 0  # No LLM tokens spent on unsafe paths


# ===========================================================================
# 4. Tool Exception & Report Failure Handling
# ===========================================================================


def test_packager_tool_exception_routes_to_exhaustion_without_llm_call():
    """A packager tool exception goes straight to exhaustion without LLM regeneration."""
    client = FakeLLMClient(responses=[{"summary": "Executive summary"}])
    packager = FakeProjectPackager(results=[RuntimeError("Disk full during zip creation")])
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "FinalProjectAgent"
    assert len(client.calls) == 0
    assert "Project packager tool exception: Disk full" in result["errors"][0].message


def test_packager_report_failure_exhausts():
    """Packager returning failed report exhausts to human approval."""
    client = FakeLLMClient(responses=[{"summary": "Executive summary"}])
    packager = FakeProjectPackager(
        results=[PackageReport(success=False, errors=["Archive permissions denied"])]
    )
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert "Project packaging failed: Archive permissions denied" in result["errors"][0].message


# ===========================================================================
# 5. LLM Validation & Retry
# ===========================================================================


def test_llm_validation_retries_without_sleep_and_recovers():
    """Empty summary triggers validation retry without backoff sleep, recovering on 2nd attempt."""
    sleep_calls: List[float] = []
    client = FakeLLMClient(
        responses=[
            {"summary": ""},  # Attempt 1: empty summary triggers validation retry
            {"summary": "Comprehensive final executive summary."},  # Attempt 2: valid
        ]
    )
    packager = FakeProjectPackager()
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
        sleep_fn=sleep_calls.append,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_COMPLETED
    assert result["next_agent"] is None
    assert len(client.calls) == 2
    assert len(sleep_calls) == 0


def test_unhandled_llm_exception_triggers_backoff_sleep():
    """Unhandled LLM exceptions trigger exponential backoff between retries."""
    sleep_calls: List[float] = []
    client = FakeLLMClient(
        responses=[
            ConnectionResetError("Connection lost"),
            {"summary": "Comprehensive final executive summary."},
        ]
    )
    packager = FakeProjectPackager()
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
        sleep_fn=sleep_calls.append,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_COMPLETED
    assert len(client.calls) == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0


# ===========================================================================
# 6. Success Path, FinalProjectOutput & Invariants
# ===========================================================================


def test_execute_success_produces_valid_final_project_output():
    """Successful execution writes final_output, completes workflow, advances forward_stage, and satisfies file invariant."""
    client = FakeLLMClient(
        responses=[{"summary": "Delivered high-performance containerized system."}]
    )
    packager = FakeProjectPackager()
    agent = FinalProjectAgent(
        model_name="final-model-v1",
        llm_client=client,
        packager=packager,
    )
    state = _build_test_state(retry_counts={"FinalProjectAgent": 1, "ReviewerAgent": 0})

    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_COMPLETED
    assert result["next_agent"] is None
    assert result["forward_stage"] == 10
    assert result["checkpoint_stage"] == "completed"
    assert "completed" in result["checkpoints"]
    assert result["retry_counts"]["FinalProjectAgent"] == 0

    final_output: FinalProjectOutput = result["final_output"]
    assert isinstance(final_output, FinalProjectOutput)
    assert final_output.project_name == "DeliverApp"
    assert final_output.summary == "Delivered high-performance containerized system."
    assert final_output.overall_review_status == "approved"
    assert final_output.produced_by == "FinalProjectAgent"
    assert final_output.produced_at != ""

    # Verify file count invariant (Requirement 20)
    expected_total = 2 + 1 + 1 + 1  # 2 code, 1 test, 1 deploy, 1 doc
    assert final_output.total_files_generated == expected_total
    assert len(final_output.code_paths) == 2
    assert len(final_output.test_paths) == 1
    assert len(final_output.deployment_paths) == 1
    assert len(final_output.documentation_paths) == 1


# ===========================================================================
# 7. Input State Unchanged
# ===========================================================================


def test_input_state_unchanged_after_execute():
    """FinalProjectAgent.execute does not mutate any fields of incoming state."""
    client = FakeLLMClient(
        responses=[{"summary": "Delivered high-performance containerized system."}]
    )
    packager = FakeProjectPackager()
    agent = FinalProjectAgent(
        model_name="test-model",
        llm_client=client,
        packager=packager,
    )
    state = _build_test_state(retry_counts={"FinalProjectAgent": 1})
    state_before = copy.deepcopy(state)

    _ = agent.execute(state)

    assert state == state_before
