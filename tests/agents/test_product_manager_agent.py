"""
tests/agents/test_product_manager_agent.py

Comprehensive unit and behavioral tests for ProductManagerAgent in agents/product_manager_agent.py.
"""

from typing import Any, Dict
import pytest

from agents.product_manager_agent import ProductManagerAgent, strip_metadata_header
from state.engineering_state import create_initial_state
from state.models import ProductRequirements
from tools.shared.llm_client import FakeLLMClient
from workflows.constants import STATUS_REQUIREMENTS_COMPLETE


def _sample_valid_requirements_dict() -> Dict[str, Any]:
    return {
        "functional_requirements": ["User can authenticate", "User can submit orders"],
        "non_functional_requirements": ["Response time under 200ms", "99.9% uptime"],
        "user_stories": ["As a user I want to sign up so that I can buy items"],
        "acceptance_criteria": ["Given valid credentials, return 200 and auth token"],
        "constraints": ["Python 3.11+", "PostgreSQL database"],
        "business_goals": ["Increase customer conversion rate by 15%"],
        "produced_by": "ProductManagerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def test_strip_metadata_header():
    """strip_metadata_header removes YAML frontmatter and HTML comment blocks."""
    yaml_header = "---\nversion: 1.0.0\nauthor: Team\n---\nPrompt body here."
    assert strip_metadata_header(yaml_header) == "Prompt body here."

    html_header = "<!--\nversion: 1.0.0\n-->\nPrompt body here."
    assert strip_metadata_header(html_header) == "Prompt body here."

    no_header = "Direct prompt content without header."
    assert strip_metadata_header(no_header) == "Direct prompt content without header."


def test_pm_agent_constructor_validation():
    """Constructor validates agent arguments."""
    client = FakeLLMClient()
    agent = ProductManagerAgent(model_name="test-model", llm_client=client, max_retries=3)
    assert agent.agent_name == "ProductManagerAgent"
    assert agent.max_retries == 3
    assert agent.model_name == "test-model"

    with pytest.raises(ValueError, match="max_retries must be a positive integer"):
        ProductManagerAgent(model_name="test-model", llm_client=client, max_retries=0)


def test_pm_success_path_valid_requirements():
    """ProductManagerAgent.execute succeeds with valid requirements and stamps all required fields."""
    client = FakeLLMClient(responses=[_sample_valid_requirements_dict()])
    agent = ProductManagerAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build an order app")
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_REQUIREMENTS_COMPLETE
    assert result["next_agent"] == "ArchitectAgent"
    assert result["current_agent"] == "ProductManagerAgent"
    assert result["forward_stage"] == 1
    assert result["retry_counts"] == {"ProductManagerAgent": 0}
    assert result["checkpoint_stage"] == "requirements_complete"
    assert "requirements_complete" in result["checkpoints"]

    req: ProductRequirements = result["requirements"]
    assert isinstance(req, ProductRequirements)
    assert req.produced_by == "ProductManagerAgent"
    assert req.version == 1
    assert len(req.functional_requirements) == 2


def test_pm_version_increment_when_requirements_already_exist():
    """ProductRequirements version increments when requirements already exist in state (Req 3 crit 3)."""
    client = FakeLLMClient(responses=[_sample_valid_requirements_dict()])
    agent = ProductManagerAgent(model_name="test-model", llm_client=client)

    existing_req = ProductRequirements(**_sample_valid_requirements_dict())
    existing_req.version = 3

    state = create_initial_state(project_name="OrderApp", user_input="Revise requirements")
    state["requirements"] = existing_req

    result = agent.execute(state)
    assert result["requirements"].version == 4


def test_pm_forward_stage_idempotency_on_reruns():
    """forward_stage is idempotent and does not double-count on re-runs (Req 21 crit 6)."""
    client = FakeLLMClient(
        responses=[
            _sample_valid_requirements_dict(),
            _sample_valid_requirements_dict(),
            _sample_valid_requirements_dict(),
        ]
    )
    agent = ProductManagerAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["forward_stage"] = 0
    res1 = agent.execute(state)
    assert res1["forward_stage"] == 1

    # Re-run when forward_stage is already at or beyond stage 1
    state["forward_stage"] = 1
    res2 = agent.execute(state)
    assert res2["forward_stage"] == 1

    state["forward_stage"] = 4
    res3 = agent.execute(state)
    assert res3["forward_stage"] == 4


@pytest.mark.parametrize("empty_input", ["", "   ", "\t\n  ", None])
def test_pm_empty_or_whitespace_user_input_fast_fails_to_exhaustion(empty_input):
    """Empty or whitespace user_input bypasses LLM and backoff, going directly to exhaustion return."""
    client = FakeLLMClient(responses=[])
    sleep_calls = []

    agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="EmptyApp", user_input="valid initial input")
    state["user_input"] = empty_input

    result = agent.execute(state)

    # No LLM calls made and no sleep delay
    assert len(client.calls) == 0
    assert len(sleep_calls) == 0

    assert result["human_approval_required"] is True
    assert result["next_agent"] == "ProductManagerAgent"
    assert result["retry_counts"] == {"ProductManagerAgent": 3}
    assert result["execution_metadata"]["ProductManagerAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    # workflow_status is UNCHANGED (not in result)
    assert "workflow_status" not in result


def test_pm_internal_retry_recovers_on_second_attempt_after_validation_error():
    """Validation failure on attempt 1 triggers revision prompt on attempt 2 without backoff sleep."""
    invalid_req = _sample_valid_requirements_dict()
    invalid_req["functional_requirements"] = []  # violates non-empty requirement

    valid_req = _sample_valid_requirements_dict()

    client = FakeLLMClient(responses=[invalid_req, valid_req])
    sleep_calls = []

    agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    result = agent.execute(state)

    assert len(client.calls) == 2
    assert len(sleep_calls) == 0  # no backoff for validation failures
    assert "failed validation" in client.calls[1]["prompt"]  # revision prompt used
    assert result["workflow_status"] == STATUS_REQUIREMENTS_COMPLETE
    assert result["requirements"].version == 1
    assert result["retry_counts"] == {"ProductManagerAgent": 0}


def test_pm_internal_retry_applies_backoff_on_unhandled_exception():
    """Unhandled exception triggers exponential backoff (2.0s on attempt 1)."""
    valid_req = _sample_valid_requirements_dict()
    client = FakeLLMClient(responses=[RuntimeError("API timeout"), valid_req])
    sleep_calls = []

    agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    result = agent.execute(state)

    assert len(client.calls) == 2
    assert sleep_calls == [2.0]
    assert result["workflow_status"] == STATUS_REQUIREMENTS_COMPLETE
    assert result["retry_counts"] == {"ProductManagerAgent": 0}


def test_pm_exhaustion_after_three_failed_attempts():
    """All 3 attempts failing returns exhaustion update with failed metadata and human approval hold."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("Network error 1"),
            RuntimeError("Network error 2"),
            RuntimeError("Network error 3"),
        ]
    )
    sleep_calls = []

    agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    result = agent.execute(state)

    assert len(client.calls) == 3
    assert sleep_calls == [2.0, 4.0]  # backoff before attempts 2 and 3

    assert result["human_approval_required"] is True
    assert result["next_agent"] == "ProductManagerAgent"
    assert result["retry_counts"] == {"ProductManagerAgent": 3}
    assert result["execution_metadata"]["ProductManagerAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result


def test_pm_invoke_lifecycle_success():
    """BaseAgent.invoke merges metadata and increments total_invocations on success."""
    client = FakeLLMClient(responses=[_sample_valid_requirements_dict()])
    agent = ProductManagerAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    update = agent.invoke(state)

    assert update["workflow_status"] == STATUS_REQUIREMENTS_COMPLETE
    assert update["execution_metadata"]["ProductManagerAgent"].status == "completed"
    assert update["total_invocations"] == 1


def test_pm_invoke_lifecycle_exhaustion():
    """BaseAgent.invoke respects subclass's failed status on exhaustion return."""
    client = FakeLLMClient(
        responses=[
            ValueError("Bad JSON 1"),
            ValueError("Bad JSON 2"),
            ValueError("Bad JSON 3"),
        ]
    )
    agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: None,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    update = agent.invoke(state)

    assert update["human_approval_required"] is True
    assert update["execution_metadata"]["ProductManagerAgent"].status == "failed"
    assert update["total_invocations"] == 1
    assert "workflow_status" not in update
