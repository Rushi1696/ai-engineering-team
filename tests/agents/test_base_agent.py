"""
tests/agents/test_base_agent.py

Comprehensive test suite for agents/base_agent.py, per design.md Section
1 (the seven-part contract shape shared by all eleven concrete agents),
Requirement 12 (Error Tracking), Requirement 13 (Retry and Escalation
Tracking), Requirement 14 (Per-Agent Execution Metadata), and
Requirement 21 criterion 7 (total_invocations).

Covers:
  - BaseAgent cannot be instantiated directly (ABC contract)
  - constructor validation (agent_name, max_retries)
  - success path: execution_metadata lifecycle, current_agent,
    updated_at, total_invocations, merge precedence with subclass output
  - failure path: ErrorRecord construction, failed execution_metadata,
    retry_counts increment, human_approval_required escalation,
    total_invocations increment
  - error_type classification (ValueError -> validation_failure,
    other Exception -> unhandled_exception)
  - retry/escalation edge cases: no existing retry_counts entry, no
    existing max_retries entry (falls back to constructor default),
    explicit per-agent max_retries override in state
  - invoke() never raises, regardless of what execute() raises
"""

import sys
from typing import Any, Dict

sys.path.insert(0, "/home/claude/work")

import pytest

from agents.base_agent import DEFAULT_MAX_RETRIES, BaseAgent
from state.engineering_state import create_initial_state
from state.models import AgentExecutionMetadata, ErrorRecord


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _SucceedingAgent(BaseAgent):
    """A concrete agent whose execute() always returns a fixed partial
    update, for exercising the success path."""

    def __init__(self, agent_name: str = "ProductManagerAgent", max_retries: int = DEFAULT_MAX_RETRIES, update: Dict[str, Any] | None = None):
        super().__init__(agent_name, max_retries)
        self._update = update if update is not None else {
            "workflow_status": "requirements_complete",
            "next_agent": "ArchitectAgent",
        }

    def execute(self, state):
        return dict(self._update)


class _FailingAgent(BaseAgent):
    """A concrete agent whose execute() always raises a configurable
    exception, for exercising the failure path."""

    def __init__(self, agent_name: str = "ProductManagerAgent", max_retries: int = DEFAULT_MAX_RETRIES, exc: Exception | None = None):
        super().__init__(agent_name, max_retries)
        self._exc = exc if exc is not None else ValueError("synthetic failure")

    def execute(self, state):
        raise self._exc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def initial_state():
    return create_initial_state(project_name="TestProject", user_input="build a thing")


# ---------------------------------------------------------------------------
# ABC / construction
# ---------------------------------------------------------------------------


def test_base_agent_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseAgent("ProductManagerAgent")  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated():
    agent = _SucceedingAgent()
    assert agent.agent_name == "ProductManagerAgent"
    assert agent.max_retries == DEFAULT_MAX_RETRIES


def test_constructor_rejects_empty_agent_name():
    with pytest.raises(ValueError):
        _SucceedingAgent(agent_name="")


@pytest.mark.parametrize("bad_value", [0, -1, -100])
def test_constructor_rejects_non_positive_max_retries(bad_value):
    with pytest.raises(ValueError):
        _SucceedingAgent(max_retries=bad_value)


def test_constructor_accepts_custom_max_retries():
    agent = _SucceedingAgent(max_retries=5)
    assert agent.max_retries == 5


def test_default_max_retries_constant_is_three():
    assert DEFAULT_MAX_RETRIES == 3


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_success_sets_current_agent(initial_state):
    agent = _SucceedingAgent(agent_name="ArchitectAgent")
    result = agent.invoke(initial_state)
    assert result["current_agent"] == "ArchitectAgent"


def test_success_merges_subclass_output(initial_state):
    agent = _SucceedingAgent(update={"workflow_status": "architecture_complete", "next_agent": "TaskPlannerAgent"})
    result = agent.invoke(initial_state)
    assert result["workflow_status"] == "architecture_complete"
    assert result["next_agent"] == "TaskPlannerAgent"


def test_success_sets_execution_metadata_completed(initial_state):
    agent = _SucceedingAgent(agent_name="QAAgent")
    result = agent.invoke(initial_state)
    metadata = result["execution_metadata"]["QAAgent"]
    assert isinstance(metadata, AgentExecutionMetadata)
    assert metadata.status == "completed"
    assert metadata.agent_name == "QAAgent"
    assert metadata.started_at is not None
    assert metadata.completed_at is not None
    assert metadata.duration_seconds is not None
    assert metadata.duration_seconds >= 0.0


def test_success_execution_metadata_tokens_and_model_calls_default_zero(initial_state):
    agent = _SucceedingAgent()
    result = agent.invoke(initial_state)
    metadata = result["execution_metadata"][agent.agent_name]
    assert metadata.tokens_used == 0
    assert metadata.model_calls == 0


def test_success_sets_updated_at(initial_state):
    agent = _SucceedingAgent()
    result = agent.invoke(initial_state)
    assert "updated_at" in result
    assert isinstance(result["updated_at"], str)


def test_success_increments_total_invocations_from_zero(initial_state):
    agent = _SucceedingAgent()
    result = agent.invoke(initial_state)
    assert result["total_invocations"] == 1


def test_success_increments_total_invocations_from_existing_value(initial_state):
    state = dict(initial_state)
    state["total_invocations"] = 7
    agent = _SucceedingAgent()
    result = agent.invoke(state)
    assert result["total_invocations"] == 8


def test_success_subclass_can_override_current_agent(initial_state):
    """Subclass-provided keys take precedence over BaseAgent defaults,
    EXCEPT total_invocations which BaseAgent always owns."""
    agent = _SucceedingAgent(
        agent_name="ProductManagerAgent",
        update={"current_agent": "SomethingElse", "total_invocations": 999},
    )
    result = agent.invoke(initial_state)
    assert result["current_agent"] == "SomethingElse"
    # total_invocations is NOT overridable by the subclass
    assert result["total_invocations"] == 1


def test_success_does_not_set_errors_key(initial_state):
    agent = _SucceedingAgent()
    result = agent.invoke(initial_state)
    assert "errors" not in result


def test_success_does_not_set_human_approval_keys(initial_state):
    agent = _SucceedingAgent()
    result = agent.invoke(initial_state)
    assert "human_approval_required" not in result
    assert "human_approval_notes" not in result


# ---------------------------------------------------------------------------
# Failure path -- invoke() never raises
# ---------------------------------------------------------------------------


def test_invoke_never_raises_on_value_error(initial_state):
    agent = _FailingAgent(exc=ValueError("bad"))
    result = agent.invoke(initial_state)  # should not raise
    assert isinstance(result, dict)


def test_invoke_never_raises_on_arbitrary_exception(initial_state):
    agent = _FailingAgent(exc=RuntimeError("boom"))
    result = agent.invoke(initial_state)  # should not raise
    assert isinstance(result, dict)


def test_invoke_never_raises_on_key_error(initial_state):
    agent = _FailingAgent(exc=KeyError("missing"))
    result = agent.invoke(initial_state)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Failure path -- ErrorRecord construction (Requirement 12)
# ---------------------------------------------------------------------------


def test_failure_appends_one_error_record(initial_state):
    agent = _FailingAgent(agent_name="BackendAgent")
    result = agent.invoke(initial_state)
    assert "errors" in result
    assert len(result["errors"]) == 1
    assert isinstance(result["errors"][0], ErrorRecord)


def test_failure_error_record_has_correct_agent_and_message(initial_state):
    agent = _FailingAgent(agent_name="BackendAgent", exc=ValueError("schema invalid"))
    result = agent.invoke(initial_state)
    record = result["errors"][0]
    assert record.agent == "BackendAgent"
    assert record.message == "schema invalid"
    assert record.resolved is False
    assert record.resolution_agent is None
    assert record.resolution_notes is None


def test_failure_error_record_has_unique_error_id(initial_state):
    agent = _FailingAgent()
    result1 = agent.invoke(initial_state)
    result2 = agent.invoke(initial_state)
    assert result1["errors"][0].error_id != result2["errors"][0].error_id


def test_failure_error_record_stage_is_current_workflow_status(initial_state):
    state = dict(initial_state)
    state["workflow_status"] = "architecture_complete"
    agent = _FailingAgent()
    result = agent.invoke(state)
    assert result["errors"][0].stage == "architecture_complete"


def test_failure_error_record_has_traceback(initial_state):
    agent = _FailingAgent()
    result = agent.invoke(initial_state)
    assert result["errors"][0].traceback is not None
    assert "ValueError" in result["errors"][0].traceback


def test_failure_error_record_occurred_at_is_iso8601(initial_state):
    agent = _FailingAgent()
    result = agent.invoke(initial_state)
    occurred_at = result["errors"][0].occurred_at
    # Round-trip through fromisoformat to assert validity rather than
    # asserting an exact string shape.
    from datetime import datetime

    datetime.fromisoformat(occurred_at)


@pytest.mark.parametrize(
    "exc, expected_type",
    [
        (ValueError("bad value"), "validation_failure"),
        (RuntimeError("boom"), "unhandled_exception"),
        (TypeError("wrong type"), "unhandled_exception"),
        (KeyError("missing"), "unhandled_exception"),
        (ConnectionError("network down"), "unhandled_exception"),
    ],
)
def test_failure_error_type_classification(initial_state, exc, expected_type):
    agent = _FailingAgent(exc=exc)
    result = agent.invoke(initial_state)
    assert result["errors"][0].error_type == expected_type


# ---------------------------------------------------------------------------
# Failure path -- execution metadata (Requirement 14 criterion 5)
# ---------------------------------------------------------------------------


def test_failure_sets_execution_metadata_failed(initial_state):
    agent = _FailingAgent(agent_name="DevOpsAgent")
    result = agent.invoke(initial_state)
    metadata = result["execution_metadata"]["DevOpsAgent"]
    assert metadata.status == "failed"
    assert metadata.completed_at is not None
    assert metadata.duration_seconds is not None
    assert metadata.duration_seconds >= 0.0


def test_failure_execution_metadata_notes_contains_error_message(initial_state):
    agent = _FailingAgent(exc=ValueError("very specific problem"))
    result = agent.invoke(initial_state)
    metadata = result["execution_metadata"][agent.agent_name]
    assert "very specific problem" in metadata.notes


# ---------------------------------------------------------------------------
# Failure path -- retry counting (Requirement 13 criteria 2, 5)
# ---------------------------------------------------------------------------


def test_failure_initializes_retry_count_to_one_when_absent(initial_state):
    agent = _FailingAgent(agent_name="QAAgent")
    result = agent.invoke(initial_state)
    assert result["retry_counts"] == {"QAAgent": 1}


def test_failure_increments_existing_retry_count(initial_state):
    state = dict(initial_state)
    state["retry_counts"] = {"QAAgent": 1}
    agent = _FailingAgent(agent_name="QAAgent")
    result = agent.invoke(state)
    assert result["retry_counts"] == {"QAAgent": 2}


def test_failure_retry_count_update_only_contains_this_agent(initial_state):
    state = dict(initial_state)
    state["retry_counts"] = {"OtherAgent": 5}
    agent = _FailingAgent(agent_name="QAAgent")
    result = agent.invoke(state)
    # dict_merge_reducer semantics: only this agent's key needs to be in
    # the partial update; OtherAgent's entry is preserved by the
    # reducer, not by this dict itself.
    assert result["retry_counts"] == {"QAAgent": 1}
    assert "OtherAgent" not in result["retry_counts"]


# ---------------------------------------------------------------------------
# Failure path -- human approval escalation (Requirement 13 criterion 6)
# ---------------------------------------------------------------------------


def test_failure_does_not_escalate_below_max_retries(initial_state):
    state = dict(initial_state)
    state["retry_counts"] = {"QAAgent": 0}
    agent = _FailingAgent(agent_name="QAAgent", max_retries=3)
    result = agent.invoke(state)
    assert result["retry_counts"]["QAAgent"] == 1
    assert "human_approval_required" not in result
    assert "human_approval_notes" not in result


def test_failure_escalates_when_reaching_max_retries(initial_state):
    state = dict(initial_state)
    state["retry_counts"] = {"QAAgent": 2}
    agent = _FailingAgent(agent_name="QAAgent", max_retries=3)
    result = agent.invoke(state)
    assert result["retry_counts"]["QAAgent"] == 3
    assert result["human_approval_required"] is True
    assert "QAAgent" in result["human_approval_notes"]
    assert "3" in result["human_approval_notes"]


def test_failure_escalates_when_exceeding_max_retries(initial_state):
    """Defensive case: count already at-or-past the limit before this
    failure (should still escalate, not silently skip)."""
    state = dict(initial_state)
    state["retry_counts"] = {"QAAgent": 5}
    agent = _FailingAgent(agent_name="QAAgent", max_retries=3)
    result = agent.invoke(state)
    assert result["retry_counts"]["QAAgent"] == 6
    assert result["human_approval_required"] is True


def test_failure_uses_constructor_default_max_retries_when_state_has_no_entry(initial_state):
    """Requirement 13 criterion 2: effective limit is 3 when no
    max_retries entry exists for the agent -- exercised here via the
    agent's own constructor default (which itself defaults to 3)."""
    state = dict(initial_state)
    state["retry_counts"] = {"QAAgent": 2}
    # state["max_retries"] is {} (from create_initial_state) -- no entry
    # for QAAgent, so the agent's own constructor default applies.
    agent = _FailingAgent(agent_name="QAAgent", max_retries=DEFAULT_MAX_RETRIES)
    result = agent.invoke(state)
    assert result["human_approval_required"] is True


def test_failure_uses_state_max_retries_entry_when_present(initial_state):
    """When state["max_retries"] DOES contain an entry for this agent,
    that value -- not the agent's constructor default -- governs
    escalation (Requirement 13 criterion 2 default only applies when
    "not present")."""
    state = dict(initial_state)
    state["retry_counts"] = {"QAAgent": 1}
    state["max_retries"] = {"QAAgent": 2}
    # Constructor default is 3, but state explicitly configures 2.
    agent = _FailingAgent(agent_name="QAAgent", max_retries=3)
    result = agent.invoke(state)
    assert result["retry_counts"]["QAAgent"] == 2
    assert result["human_approval_required"] is True


def test_failure_human_approval_notes_is_none_when_not_escalating():
    """_apply_retry_escalation returns None notes when not escalating
    (verified at the helper level for precision)."""
    state = create_initial_state(project_name="P", user_input="x")
    agent = _FailingAgent(agent_name="QAAgent", max_retries=5)
    _, escalated, notes = agent._apply_retry_escalation(state)
    assert escalated is False
    assert notes is None


# ---------------------------------------------------------------------------
# Failure path -- total_invocations still increments on failure
# ---------------------------------------------------------------------------


def test_failure_still_increments_total_invocations(initial_state):
    state = dict(initial_state)
    state["total_invocations"] = 4
    agent = _FailingAgent()
    result = agent.invoke(state)
    assert result["total_invocations"] == 5


def test_failure_sets_current_agent(initial_state):
    agent = _FailingAgent(agent_name="ReviewerAgent")
    result = agent.invoke(initial_state)
    assert result["current_agent"] == "ReviewerAgent"


def test_failure_sets_updated_at(initial_state):
    agent = _FailingAgent()
    result = agent.invoke(initial_state)
    assert "updated_at" in result


# ---------------------------------------------------------------------------
# Integration: success then failure then success across calls
# (verifies BaseAgent instances are stateless / reusable across invoke()
# calls, with all state living in the EngineeringState dict passed in)
# ---------------------------------------------------------------------------


def test_agent_instance_is_reusable_across_multiple_invocations(initial_state):
    agent = _SucceedingAgent(agent_name="FrontendAgent")

    result1 = agent.invoke(initial_state)
    assert result1["total_invocations"] == 1

    state_after_1 = dict(initial_state)
    state_after_1["total_invocations"] = result1["total_invocations"]

    result2 = agent.invoke(state_after_1)
    assert result2["total_invocations"] == 2


def test_two_different_agent_instances_track_retries_independently_via_state(initial_state):
    """retry_counts is keyed by agent_name in state, so two different
    agents failing independently should not interfere with each
    other's counts within a single partial update."""
    state = dict(initial_state)
    state["retry_counts"] = {"BackendAgent": 2}

    backend_agent = _FailingAgent(agent_name="BackendAgent", max_retries=3)
    frontend_agent = _FailingAgent(agent_name="FrontendAgent", max_retries=3)

    backend_result = backend_agent.invoke(state)
    frontend_result = frontend_agent.invoke(state)

    assert backend_result["retry_counts"] == {"BackendAgent": 3}
    assert backend_result["human_approval_required"] is True

    assert frontend_result["retry_counts"] == {"FrontendAgent": 1}
    assert "human_approval_required" not in frontend_result
    