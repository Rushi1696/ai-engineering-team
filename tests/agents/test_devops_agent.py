"""
tests/agents/test_devops_agent.py

Comprehensive unit and behavioral tests for DevOpsAgent in agents/devops_agent.py.
"""

import copy
from typing import Any, Dict, List, Optional
import pytest

from agents.devops_agent import (
    STATUS_DEBUG_REQUIRED,
    STATUS_DEVOPS_COMPLETE,
    DevOpsAgent,
    DevOpsOutput,
    DraftDeploymentArtifact,
)
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    DeploymentArtifact,
    ErrorRecord,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
)
from tools.devops.yaml_validator import FakeConfigValidator, ValidationReport
from tools.shared.llm_client import FakeLLMClient


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


def _sample_devops_output_dict(
    include_dockerfile: bool = True,
    include_cicd: bool = True,
) -> Dict[str, Any]:
    artifacts = []
    if include_dockerfile:
        artifacts.append(
            {
                "file_path": "Dockerfile",
                "content": "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"src/app.py\"]\n",
                "artifact_type": "dockerfile",
            }
        )
    if include_cicd:
        artifacts.append(
            {
                "file_path": ".github/workflows/ci.yml",
                "content": "name: CI\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v3\n",
                "artifact_type": "ci_cd_pipeline",
            }
        )
    return {
        "artifacts": artifacts,
        "summary": "Generated Dockerfile and GitHub Actions workflow",
    }


def _build_test_state(
    retry_counts: Optional[Dict[str, int]] = None,
    deployment_artifacts: Optional[Dict[str, DeploymentArtifact]] = None,
) -> Dict[str, Any]:
    state = create_initial_state(project_name="DevOpsApp", user_input="Build devops app")
    state["architecture"] = _sample_valid_architecture()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["workflow_status"] = "review_complete"
    state["forward_stage"] = 7
    if retry_counts is not None:
        state["retry_counts"] = retry_counts
    if deployment_artifacts is not None:
        state["deployment_artifacts"] = deployment_artifacts
    return state


# ===========================================================================
# 1. Constructor Validation
# ===========================================================================


def test_constructor_validates_required_arguments():
    """DevOpsAgent constructor rejects empty model_name, None llm_client, or missing validator."""
    client = FakeLLMClient(responses=[])
    validator = FakeConfigValidator()

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        DevOpsAgent(
            model_name="",
            llm_client=client,
            validator=validator,
        )

    with pytest.raises(ValueError, match="llm_client must not be None"):
        DevOpsAgent(
            model_name="test-model",
            llm_client=None,  # type: ignore
            validator=validator,
        )

    with pytest.raises(ValueError, match="validator must not be None"):
        DevOpsAgent(
            model_name="test-model",
            llm_client=client,
            validator=None,  # type: ignore
        )


# ===========================================================================
# 2. Fast-Fail Guards
# ===========================================================================


def test_execute_fast_fails_when_retry_limit_reached():
    """Fast-fails with retry_exhausted if retry count already equals or exceeds ceiling."""
    client = FakeLLMClient(responses=[])
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
        max_retries=3,
    )

    state = _build_test_state(retry_counts={"DevOpsAgent": 3})
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert result["retry_counts"]["DevOpsAgent"] == 3
    assert result["current_agent"] == "DevOpsAgent"
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "Retry limit already reached" in result["errors"][0].message
    assert len(validator.calls) == 0


def test_execute_fast_fails_when_mandatory_inputs_missing():
    """Fast-fails with validation_failure if architecture or code_artifacts are missing."""
    client = FakeLLMClient(responses=[])
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    state = _build_test_state()
    state["architecture"] = None
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert len(result["errors"]) == 1
    assert "Missing or empty architecture" in result["errors"][0].message


# ===========================================================================
# 3. Tool Exception Outside LLM Loop
# ===========================================================================


def test_validator_exception_routes_to_exhaustion():
    """A validator exception outside the loop routes directly to exhaustion without LLM regeneration."""
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    validator = FakeConfigValidator(results=[RuntimeError("Validator process crashed")])
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert len(result["errors"]) == 1
    assert "Config validation tool exception: Validator process crashed" in result["errors"][0].message
    assert len(client.calls) == 1


# ===========================================================================
# 4. LLM Validation & Retries Inside Loop
# ===========================================================================


def test_missing_required_artifacts_retries_and_recovers():
    """Missing dockerfile or ci_cd_pipeline retries without sleep and recovers on second attempt."""
    sleep_calls = []

    def mock_sleep(d: float) -> None:
        sleep_calls.append(d)

    client = FakeLLMClient(
        responses=[
            # Attempt 1: only dockerfile, missing ci_cd_pipeline -> triggers validation retry
            _sample_devops_output_dict(include_dockerfile=True, include_cicd=False),
            # Attempt 2: valid output with both
            _sample_devops_output_dict(include_dockerfile=True, include_cicd=True),
        ]
    )
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
        sleep_fn=mock_sleep,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DEVOPS_COMPLETE
    assert len(client.calls) == 2
    assert len(sleep_calls) == 0  # No backoff on validation errors


def test_unhandled_exception_triggers_backoff_sleep():
    """Unhandled exception during generation triggers exponential backoff."""
    sleep_calls = []

    def mock_sleep(d: float) -> None:
        sleep_calls.append(d)

    client = FakeLLMClient(
        responses=[
            RuntimeError("Network timeout connecting to LLM"),
            _sample_devops_output_dict(),
        ]
    )
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
        sleep_fn=mock_sleep,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DEVOPS_COMPLETE
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0


# ===========================================================================
# 5. Success Path & Versioning
# ===========================================================================


def test_successful_devops_generation():
    """Successful generation stamps version, sets devops_complete, forward_stage 8, and checkpoints."""
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DEVOPS_COMPLETE
    assert result["current_agent"] == "DevOpsAgent"
    assert result["next_agent"] == "DocumentationAgent"
    assert result["forward_stage"] == 8
    assert result["checkpoint_stage"] == "devops_complete"
    assert "devops_complete" in result["checkpoints"]
    assert result["retry_counts"]["DevOpsAgent"] == 0

    artifacts = result["deployment_artifacts"]
    assert "Dockerfile" in artifacts
    assert ".github/workflows/ci.yml" in artifacts
    assert artifacts["Dockerfile"].version == 1
    assert artifacts["Dockerfile"].produced_by == "DevOpsAgent"


def test_devops_re_run_bumps_version():
    """Re-running DevOpsAgent increments artifact version on modified files."""
    prior_artifact = DeploymentArtifact(
        file_path="Dockerfile",
        content="FROM python:3.10\n",
        artifact_type="dockerfile",
        produced_by="DevOpsAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=2,
    )
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    state = _build_test_state(deployment_artifacts={"Dockerfile": prior_artifact})
    result = agent.execute(state)

    artifacts = result["deployment_artifacts"]
    assert artifacts["Dockerfile"].version == 3


# ===========================================================================
# 6. Syntax Validation Failure Routes to DebugAgent
# ===========================================================================


def test_validation_failure_routes_to_debug_agent():
    """When config validation fails, DevOpsAgent routes to DebugAgent with ErrorRecord and debug_target."""
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    validator = FakeConfigValidator(
        results=[
            ValidationReport(
                valid=False,
                errors=["YAML syntax error at line 5: unexpected indent in ci.yml"],
            )
        ]
    )
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DEBUG_REQUIRED
    assert result["next_agent"] == "DebugAgent"
    assert result["current_agent"] == "DevOpsAgent"
    assert result["retry_counts"]["DevOpsAgent"] == 0

    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err.error_type == "other"
    assert err.agent == "DevOpsAgent"
    assert err.stage == STATUS_DEBUG_REQUIRED
    assert "devops_config_failure:" in err.message
    assert "unexpected indent in ci.yml" in err.message
    assert result["current_debug_target"] == err.error_id


# ===========================================================================
# 7. Input State Unchanged
# ===========================================================================


def test_input_state_unchanged_after_execute():
    """Verify that DevOpsAgent.execute does not mutate any fields of incoming state."""
    client = FakeLLMClient(responses=[_sample_devops_output_dict()])
    validator = FakeConfigValidator()
    agent = DevOpsAgent(
        model_name="test-model",
        llm_client=client,
        validator=validator,
    )

    state = _build_test_state()
    state_before = copy.deepcopy(state)

    _ = agent.execute(state)

    assert state == state_before
