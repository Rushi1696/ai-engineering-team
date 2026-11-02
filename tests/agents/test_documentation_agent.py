"""
tests/agents/test_documentation_agent.py

Comprehensive unit and behavioral tests for DocumentationAgent in agents/documentation_agent.py.
"""

import copy
from typing import Any, Dict, List, Optional
import pytest

from agents.documentation_agent import (
    STATUS_DOCUMENTATION_COMPLETE,
    DocumentationAgent,
    DocumentationOutput,
    DraftDocumentationArtifact,
)
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    DeploymentArtifact,
    DocumentationArtifact,
    ErrorRecord,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
)
from tools.documentation.markdown_linter import FakeMarkdownLinter, LintReport
from tools.shared.llm_client import FakeLLMClient


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
        scalability_considerations=["Horizontal pod autoscaling"],
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
    include_all_required: bool = True,
    omit_doc_type: Optional[str] = None,
) -> Dict[str, Any]:
    doc_types = [
        ("README.md", "readme", "# DocTestApp\nProject overview and quickstart."),
        ("docs/architecture.md", "architecture", "# Architecture\nSystem design details."),
        ("docs/api_reference.md", "api_reference", "# API Reference\nEndpoints and schemas."),
        ("docs/deployment.md", "deployment_guide", "# Deployment Guide\nSetup instructions."),
    ]
    artifacts = []
    for path, dtype, content in doc_types:
        if omit_doc_type and dtype == omit_doc_type:
            continue
        if not include_all_required and dtype == "deployment_guide":
            continue
        artifacts.append({"file_path": path, "doc_type": dtype, "content": content})

    return {
        "artifacts": artifacts,
        "summary": "Generated README, architecture, API reference, and deployment guide.",
    }


def _build_test_state(
    retry_counts: Optional[Dict[str, int]] = None,
    documentation_artifacts: Optional[Dict[str, DocumentationArtifact]] = None,
) -> Dict[str, Any]:
    state = create_initial_state(project_name="DocTestApp", user_input="Build doc test app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["workflow_status"] = "devops_complete"
    state["forward_stage"] = 8
    if retry_counts is not None:
        state["retry_counts"] = retry_counts
    if documentation_artifacts is not None:
        state["documentation_artifacts"] = documentation_artifacts
    return state


# ===========================================================================
# 1. Constructor Validation
# ===========================================================================


def test_constructor_validates_required_arguments():
    """DocumentationAgent constructor rejects empty model_name, None llm_client, or missing linter."""
    client = FakeLLMClient(responses=[])
    linter = FakeMarkdownLinter()

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        DocumentationAgent(
            model_name="",
            llm_client=client,
            linter=linter,
        )

    with pytest.raises(ValueError, match="llm_client must not be None"):
        DocumentationAgent(
            model_name="test-model",
            llm_client=None,  # type: ignore
            linter=linter,
        )

    with pytest.raises(ValueError, match="linter must not be None"):
        DocumentationAgent(
            model_name="test-model",
            llm_client=client,
            linter=None,  # type: ignore
        )


# ===========================================================================
# 2. Fast-Fail Guards
# ===========================================================================


def test_execute_fast_fails_when_retry_limit_reached():
    """Fast-fails with retry_exhausted if retry count already equals or exceeds ceiling."""
    client = FakeLLMClient(responses=[])
    linter = FakeMarkdownLinter()
    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
        max_retries=3,
    )
    state = _build_test_state(retry_counts={"DocumentationAgent": 3})

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "DocumentationAgent"
    assert result["execution_metadata"]["DocumentationAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result  # Preserved unchanged


def test_execute_fast_fails_when_mandatory_inputs_missing():
    """Fast-fails if requirements or architecture are missing from state."""
    client = FakeLLMClient(responses=[])
    linter = FakeMarkdownLinter()
    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
    )

    state = _build_test_state()
    state["requirements"] = None

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "DocumentationAgent"
    assert "Missing or empty requirements or architecture" in result["errors"][0].message


# ===========================================================================
# 3. Tool Exception Handling (Linter)
# ===========================================================================


def test_linter_tool_exception_routes_directly_to_exhaustion():
    """A linter tool exception routes directly to exhaustion without LLM regeneration."""
    client = FakeLLMClient(responses=[_sample_documentation_output_dict()])
    linter = FakeMarkdownLinter()

    def _broken_lint(documentation_artifacts):
        raise RuntimeError("Disk I/O error during markdown linting")

    linter.lint_documentation = _broken_lint  # type: ignore

    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "DocumentationAgent"
    assert len(client.calls) == 1  # No LLM retry loop triggered for tool failure
    assert "Markdown linter tool exception" in result["errors"][0].message
    assert result["errors"][0].error_type == "retry_exhausted"


# ===========================================================================
# 4. Linter Report Failure Feeds LLM Retry Loop
# ===========================================================================


def test_linter_report_failure_then_success_on_retry():
    """A failed lint report feeds the retry loop; LLM receives errors in revision prompt and succeeds."""
    sleep_calls: List[float] = []
    client = FakeLLMClient(
        responses=[
            _sample_documentation_output_dict(),
            _sample_documentation_output_dict(),
        ]
    )
    failing_report = LintReport(
        passed=False,
        errors=["Broken relative link in docs/architecture.md"],
        warnings=["Heading level 3 skipped in README.md"],
    )
    passing_report = LintReport(passed=True)
    linter = FakeMarkdownLinter(results=[failing_report, passing_report])

    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
        sleep_fn=sleep_calls.append,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_DOCUMENTATION_COMPLETE
    assert result["next_agent"] == "FinalProjectAgent"
    assert len(client.calls) == 2
    assert len(sleep_calls) == 0  # No backoff sleep on lint validation error

    # Verify attempt 2 received the lint error and previous artifacts
    attempt_2_prompt = client.calls[1]["prompt"]
    assert "Broken relative link in docs/architecture.md" in attempt_2_prompt
    assert "README.md" in attempt_2_prompt


def test_linter_failure_exhausts_to_human_approval():
    """Repeated lint failures exhaust to human approval without changing workflow_status."""
    client = FakeLLMClient(
        responses=[
            _sample_documentation_output_dict(),
            _sample_documentation_output_dict(),
            _sample_documentation_output_dict(),
        ]
    )
    failing_report = LintReport(
        passed=False,
        errors=["Broken relative link in docs/architecture.md"],
    )
    linter = FakeMarkdownLinter(
        results=[failing_report, failing_report, failing_report]
    )

    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
        max_retries=3,
        sleep_fn=lambda s: None,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "DocumentationAgent"
    assert "workflow_status" not in result  # Preserved unchanged
    assert "Markdown linting failed" in result["errors"][0].message
    assert len(client.calls) == 3


# ===========================================================================
# 5. LLM Validation & Retry Without Sleep
# ===========================================================================


def test_llm_validation_retries_without_sleep_and_recovers():
    """Missing required doc types triggers validation retry without backoff sleep, recovering on 2nd attempt."""
    sleep_calls: List[float] = []

    # First response omits deployment_guide; second response includes all 4
    invalid_output = _sample_documentation_output_dict(omit_doc_type="deployment_guide")
    valid_output = _sample_documentation_output_dict()

    client = FakeLLMClient(responses=[invalid_output, valid_output])
    linter = FakeMarkdownLinter()

    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
        sleep_fn=sleep_calls.append,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_DOCUMENTATION_COMPLETE
    assert result["next_agent"] == "FinalProjectAgent"
    assert len(client.calls) == 2
    assert len(sleep_calls) == 0  # No sleep on validation failure


# ===========================================================================
# 6. Unhandled LLM Exception Triggers Backoff Sleep
# ===========================================================================


def test_unhandled_llm_exception_triggers_backoff_sleep():
    """Unhandled LLM exceptions (e.g. ConnectionError) trigger backoff sleep between retries."""
    sleep_calls: List[float] = []

    client = FakeLLMClient(
        responses=[
            ConnectionError("LLM API endpoint unavailable"),
            _sample_documentation_output_dict(),
        ]
    )
    linter = FakeMarkdownLinter()

    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
        sleep_fn=sleep_calls.append,
    )
    state = _build_test_state()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_DOCUMENTATION_COMPLETE
    assert len(client.calls) == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0  # 2.0 ** 1


# ===========================================================================
# 7. Success Path & Stamping
# ===========================================================================


def test_execute_success_stamps_metadata_and_promotes_stage():
    """Successful generation stamps produced_by, produced_at, version=1, resets retry counts, and advances forward stage."""
    client = FakeLLMClient(responses=[_sample_documentation_output_dict()])
    linter = FakeMarkdownLinter()

    agent = DocumentationAgent(
        model_name="doc-model-v1",
        llm_client=client,
        linter=linter,
    )
    state = _build_test_state(retry_counts={"DocumentationAgent": 2, "BackendAgent": 1})

    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DOCUMENTATION_COMPLETE
    assert result["next_agent"] == "FinalProjectAgent"
    assert result["forward_stage"] == 9
    assert result["checkpoint_stage"] == "documentation_complete"
    assert "documentation_complete" in result["checkpoints"]

    # Retries reset for self, preserved for others
    assert result["retry_counts"]["DocumentationAgent"] == 0
    assert result["retry_counts"]["BackendAgent"] == 1

    # Artifacts stamped
    artifacts = result["documentation_artifacts"]
    assert "README.md" in artifacts
    readme = artifacts["README.md"]
    assert readme.produced_by == "DocumentationAgent"
    assert readme.version == 1
    assert readme.doc_type == "readme"
    assert readme.produced_at != ""

    metadata = result["execution_metadata"]["DocumentationAgent"]
    assert metadata.status == "completed"


# ===========================================================================
# 8. Version Bumping on Re-runs
# ===========================================================================


def test_execute_bumps_artifact_version_on_rerun():
    """Re-running documentation generation bumps existing artifact versions by 1."""
    existing_docs = {
        "README.md": DocumentationArtifact(
            file_path="README.md",
            content="# Old README",
            doc_type="readme",
            produced_by="DocumentationAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }
    client = FakeLLMClient(responses=[_sample_documentation_output_dict()])
    linter = FakeMarkdownLinter()

    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
    )
    state = _build_test_state(documentation_artifacts=existing_docs)

    result = agent.execute(state)
    artifacts = result["documentation_artifacts"]
    assert artifacts["README.md"].version == 2
    assert artifacts["docs/architecture.md"].version == 1  # Brand new artifact starts at 1


# ===========================================================================
# 9. Input State Unchanged
# ===========================================================================


def test_input_state_unchanged_after_execute():
    """DocumentationAgent.execute() does not mutate any fields of the input state."""
    client = FakeLLMClient(responses=[_sample_documentation_output_dict()])
    linter = FakeMarkdownLinter()
    agent = DocumentationAgent(
        model_name="test-model",
        llm_client=client,
        linter=linter,
    )

    state = _build_test_state(retry_counts={"DocumentationAgent": 1})
    state_before = copy.deepcopy(state)

    agent.execute(state)

    assert state == state_before
