"""
tests/tools/test_budget.py

Unit tests for LLM budget limits, quota protection, adapter retries,
and compiled graph termination upon budget exhaustion.
All tests are strictly mocked with zero live API calls or network access.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from pydantic import BaseModel

from agents.debug_agent import DebugAgent
from agents.product_manager_agent import ProductManagerAgent
from state.engineering_state import create_initial_state
from state.models import ErrorRecord, ProductRequirements
from tools.shared.budget import BudgetExceededError, BudgetedLLMClient, RunBudget
from tools.shared.gemini_client import GeminiLLMClient
from tools.shared.groq_client import GroqLLMClient
from tools.shared.llm_client import FakeLLMClient, create_provider_client
from workflows.factory import create_agents
from workflows.graph import build_graph


class DummySchema(BaseModel):
    summary: str


def test_run_budget_tracks_calls_and_prompt_chars():
    """RunBudget tracks calls, total prompt characters, and per-provider usage."""
    budget = RunBudget(max_total_calls=10, max_prompt_chars_per_call=1000, max_total_prompt_chars=5000)
    budget.check_and_consume("gemini", 120)
    budget.check_and_consume("groq", 250)
    budget.check_and_consume("gemini", 300)

    assert budget.total_calls == 3
    assert budget.total_prompt_chars == 670
    assert budget.usage_by_provider["gemini"]["calls"] == 2
    assert budget.usage_by_provider["gemini"]["prompt_chars"] == 420
    assert budget.usage_by_provider["gemini"]["largest_prompt_chars"] == 300
    assert budget.usage_by_provider["groq"]["calls"] == 1
    assert budget.usage_by_provider["groq"]["prompt_chars"] == 250
    assert budget.usage_by_provider["groq"]["largest_prompt_chars"] == 250


def test_run_budget_raises_on_call_limit_breach():
    """RunBudget raises BudgetExceededError when total calls reach limit."""
    budget = RunBudget(max_total_calls=2)
    budget.check_and_consume("gemini", 50)
    budget.check_and_consume("gemini", 50)

    with pytest.raises(BudgetExceededError, match="max_total_calls limit"):
        budget.check_and_consume("gemini", 50)


def test_run_budget_raises_on_per_call_prompt_char_limit():
    """RunBudget raises BudgetExceededError when a single prompt exceeds per-call limit."""
    budget = RunBudget(max_prompt_chars_per_call=100)
    with pytest.raises(BudgetExceededError, match="max_prompt_chars_per_call limit"):
        budget.check_and_consume("groq", 101)


def test_run_budget_raises_on_total_prompt_char_limit():
    """RunBudget raises BudgetExceededError when cumulative prompt characters exceed total limit."""
    budget = RunBudget(max_total_prompt_chars=200)
    budget.check_and_consume("groq", 150)
    with pytest.raises(BudgetExceededError, match="max_total_prompt_chars limit"):
        budget.check_and_consume("groq", 60)


def test_zero_limit_blocks_first_call():
    """RunBudget treats 0 as a valid limit and blocks calls immediately using 'is not None'."""
    # 0 call limit
    budget_zero_calls = RunBudget(max_total_calls=0)
    with pytest.raises(BudgetExceededError, match="max_total_calls limit"):
        budget_zero_calls.check_and_consume("gemini", 10)

    # 0 per-call prompt limit
    budget_zero_prompt = RunBudget(max_prompt_chars_per_call=0)
    with pytest.raises(BudgetExceededError, match="max_prompt_chars_per_call limit"):
        budget_zero_prompt.check_and_consume("gemini", 1)

    # 0 total prompt limit
    budget_zero_total_prompt = RunBudget(max_total_prompt_chars=0)
    with pytest.raises(BudgetExceededError, match="max_total_prompt_chars limit"):
        budget_zero_total_prompt.check_and_consume("gemini", 1)


def test_budgeted_llm_client_delegates_on_success():
    """BudgetedLLMClient checks budget and delegates to inner client on success."""
    fake_inner = FakeLLMClient(responses=[DummySchema(summary="delegated ok")])
    budget = RunBudget(max_total_calls=5, max_prompt_chars_per_call=500)
    client = BudgetedLLMClient(inner_client=fake_inner, budget=budget, provider_name="fake_provider")

    result = client.generate_structured(
        prompt="hello world",
        response_model=DummySchema,
        system_prompt="system test",
    )

    assert result.summary == "delegated ok"
    assert budget.total_calls == 1
    assert budget.total_prompt_chars == len("hello world") + len("system test")
    assert budget.usage_by_provider["fake_provider"]["calls"] == 1


def test_budget_exceeded_bypasses_agent_retry_loop_and_fails_run():
    """BudgetExceededError is not caught by agent's retry loop and causes BaseAgent to fail cleanly."""
    budget = RunBudget(max_total_calls=0)
    fake_inner = FakeLLMClient(responses=[DummySchema(summary="never reached")])
    budgeted_client = BudgetedLLMClient(inner_client=fake_inner, budget=budget)

    mock_sleep = MagicMock()
    pm_agent = ProductManagerAgent(
        model_name="test-model",
        llm_client=budgeted_client,
        max_retries=3,
        sleep_fn=mock_sleep,
    )

    state = create_initial_state(project_name="Test Budget", user_input="Build something")
    update = pm_agent.invoke(state)

    # Assert agent immediately returned failure without retries or sleeps
    assert update["workflow_status"] == "failed"
    assert mock_sleep.call_count == 0
    assert "retry_counts" not in update
    assert "human_approval_required" not in update
    assert len(update["errors"]) == 1
    assert update["errors"][0].error_type == "other"
    assert "Run budget exceeded" in update["errors"][0].message
    assert update["total_invocations"] == 1


def test_gemini_client_retries_429_and_503_only():
    """GeminiLLMClient retries 429 and 503 errors and succeeds on attempt 2."""
    mock_client = MagicMock()
    mock_sleep = MagicMock()

    # First call raises 429, second succeeds
    err_429 = RuntimeError("Error 429: Resource exhausted")
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = json.dumps({"summary": "recovered after 429"})
    mock_client.models.generate_content.side_effect = [err_429, mock_response]

    budget = RunBudget(max_total_calls=10)
    with patch("google.genai.Client", return_value=mock_client):
        client = GeminiLLMClient(
            api_key="mock-key",
            budget=budget,
            sleep_fn=mock_sleep,
        )
        client._client = mock_client

        res = client.generate_structured(prompt="test", response_model=DummySchema)
        assert res.summary == "recovered after 429"
        assert mock_sleep.call_count == 1
        # Both attempts counted toward budget
        assert budget.total_calls == 2
        assert budget.usage_by_provider["gemini"]["calls"] == 2


def test_gemini_client_fails_fast_on_400_and_404():
    """GeminiLLMClient fails immediately without retry on client error 400 or 404."""
    mock_client = MagicMock()
    mock_sleep = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("Error 404: Model not found")

    with patch("google.genai.Client", return_value=mock_client):
        client = GeminiLLMClient(
            api_key="mock-key",
            sleep_fn=mock_sleep,
        )
        client._client = mock_client

        with pytest.raises(RuntimeError, match="Model not found"):
            client.generate_structured(prompt="test", response_model=DummySchema)

        assert mock_sleep.call_count == 0


def test_groq_client_retries_429_and_503_only():
    """GroqLLMClient retries on rate limit (429) and succeeds on retry."""
    mock_client = MagicMock()
    mock_sleep = MagicMock()

    err_503 = RuntimeError("Error 503: Service Unavailable")
    mock_choice = MagicMock()
    mock_choice.finish_reason = "stop"
    mock_choice.message.content = json.dumps({"summary": "recovered after 503"})
    mock_completion = MagicMock(choices=[mock_choice])
    mock_client.chat.completions.create.side_effect = [err_503, mock_completion]

    budget = RunBudget(max_total_calls=10)
    with patch("groq.Groq", return_value=mock_client):
        client = GroqLLMClient(
            api_key="mock-key",
            budget=budget,
            sleep_fn=mock_sleep,
        )
        client._client = mock_client

        res = client.generate_structured(prompt="test", response_model=DummySchema)
        assert res.summary == "recovered after 503"
        assert mock_sleep.call_count == 1
        assert budget.total_calls == 2


def test_groq_client_fails_fast_on_401_and_403():
    """GroqLLMClient fails immediately without retry on auth error 401."""
    mock_client = MagicMock()
    mock_sleep = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Error 401: Invalid API Key")

    with patch("groq.Groq", return_value=mock_client):
        client = GroqLLMClient(
            api_key="mock-key",
            sleep_fn=mock_sleep,
        )
        client._client = mock_client

        with pytest.raises(RuntimeError, match="Invalid API Key"):
            client.generate_structured(prompt="test", response_model=DummySchema)

        assert mock_sleep.call_count == 0


def test_adapter_retry_attempts_count_toward_budget():
    """Adapter retries count every HTTP attempt toward budget and stop if budget is exceeded."""
    mock_client = MagicMock()
    mock_sleep = MagicMock()

    # 429 on first attempt, but budget only allows 1 total call
    err_429 = RuntimeError("Error 429: Resource exhausted")
    mock_client.models.generate_content.side_effect = err_429

    budget = RunBudget(max_total_calls=1)
    with patch("google.genai.Client", return_value=mock_client):
        client = GeminiLLMClient(
            api_key="mock-key",
            budget=budget,
            sleep_fn=mock_sleep,
        )
        client._client = mock_client

        # First attempt consumes 1 call, then retry attempt is blocked by budget
        with pytest.raises(BudgetExceededError):
            client.generate_structured(prompt="test", response_model=DummySchema)

        assert budget.total_calls == 1


def test_truncated_response_raises_clear_error():
    """Gemini and Groq raise RuntimeError explicitly naming max_output_tokens when finish_reason is length."""
    # Gemini
    mock_genai_client = MagicMock()
    candidate = MagicMock()
    candidate.finish_reason = "MAX_TOKENS"
    mock_response = MagicMock(candidates=[candidate], text="")
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_genai_client):
        gemini = GeminiLLMClient(api_key="mock-key", max_output_tokens=1024)
        gemini._client = mock_genai_client

        with pytest.raises(RuntimeError, match="max_output_tokens limit \\(1024\\)"):
            gemini.generate_structured(prompt="test", response_model=DummySchema)

    # Groq
    mock_groq_client = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "length"
    choice.message.content = '{"partial":'
    mock_groq_client.chat.completions.create.return_value = MagicMock(choices=[choice])

    with patch("groq.Groq", return_value=mock_groq_client):
        groq = GroqLLMClient(api_key="mock-key", max_output_tokens=2048)
        groq._client = mock_groq_client

        with pytest.raises(RuntimeError, match="max_output_tokens limit \\(2048\\)"):
            groq.generate_structured(prompt="test", response_model=DummySchema)


def test_shared_budget_across_providers():
    """Gemini and Groq clients created via factory share the single RunBudget instance."""
    shared_budget = RunBudget(max_total_calls=10, max_total_prompt_chars=50000)

    with patch("google.genai.Client"), patch("groq.Groq"):
        gemini = create_provider_client("gemini", api_key="k1", budget=shared_budget)
        groq = create_provider_client("groq", api_key="k2", budget=shared_budget)

        assert gemini.budget is shared_budget
        assert groq.budget is shared_budget

        shared_budget.check_and_consume("gemini", 100)
        shared_budget.check_and_consume("groq", 200)

        assert shared_budget.total_calls == 2
        assert shared_budget.total_prompt_chars == 300
        assert shared_budget.usage_by_provider["gemini"]["calls"] == 1
        assert shared_budget.usage_by_provider["groq"]["calls"] == 1


def test_fake_mode_budget_defaults_to_unlimited():
    """In fake mode, create_agents defaults to unlimited budget (None)."""
    agents = create_agents(provider="fake")
    for agent in agents.values():
        # Fake clients have no budget restrictions by default
        assert not isinstance(agent.llm_client, BudgetedLLMClient)


def test_usage_report_includes_largest_prompt():
    """generate_report formats total calls, total prompt chars, and largest single prompt per provider."""
    budget = RunBudget(max_total_calls=20, max_total_prompt_chars=80000)
    budget.check_and_consume("gemini", 500)
    budget.check_and_consume("gemini", 1200)
    budget.check_and_consume("groq", 800)

    report = budget.generate_report()
    assert "Total Calls:         3 / 20" in report
    assert "Total Prompt Chars:  2,500 / 80,000" in report
    assert "gemini: 2 call(s), 1,700 prompt chars (largest single prompt: 1,200 chars)" in report
    assert "groq: 1 call(s), 800 prompt chars (largest single prompt: 800 chars)" in report


def test_budget_stop_through_compiled_graph():
    """Budget exhaustion stops execution through compiled LangGraph to END without sleep or retry."""
    budget = RunBudget(max_total_calls=0)
    mock_sleep = MagicMock()

    # Pass budgeted fake client and mock sleep into create_agents
    fake_client = BudgetedLLMClient(inner_client=FakeLLMClient(), budget=budget, provider_name="fake")
    agents = create_agents(llm_client=fake_client, sleep_fn=mock_sleep)
    graph = build_graph(agents=agents)

    initial_state = create_initial_state(
        project_name="Budget Graph Test",
        user_input="Build web application",
    )

    config = {"configurable": {"thread_id": "budget-test-thread"}}
    for _ in graph.stream(initial_state, config=config):
        pass

    snapshot = graph.get_state(config)
    final_state = snapshot.values

    assert final_state is not None
    assert final_state["workflow_status"] == "failed"
    assert mock_sleep.call_count == 0
    assert final_state["retry_counts"] == initial_state["retry_counts"]
    assert final_state.get("human_approval_required") is not True
    assert final_state["total_invocations"] == 1
    assert len(final_state["errors"]) == 1
    assert final_state["errors"][0].error_type == "other"
    assert "Run budget exceeded" in final_state["errors"][0].message


def test_budget_exhaustion_stops_debug_agent_without_entering_retry_loop():
    """BudgetExceededError stops DebugAgent immediately with workflow_status='failed' without entering retry loop."""
    budget = RunBudget(max_total_prompt_chars=0)
    mock_sleep = MagicMock()
    mock_sandbox = MagicMock()

    budgeted_client = BudgetedLLMClient(
        inner_client=FakeLLMClient(),
        budget=budget,
        provider_name="fake",
    )

    agent = DebugAgent(
        model_name="test-model",
        llm_client=budgeted_client,
        sandbox=mock_sandbox,
        sleep_fn=mock_sleep,
    )

    state = create_initial_state(project_name="DebugBudgetTest", user_input="Fix issue")
    error_rec = ErrorRecord(
        error_id="err-001",
        agent="QAAgent",
        stage="qa_in_progress",
        error_type="unhandled_exception",
        message="assert 'created_at' in data failed",
        traceback="Traceback...",
        occurred_at="2026-01-01T00:00:00Z",
    )
    state["errors"] = [error_rec]
    state["current_debug_target"] = "err-001"

    result = agent.invoke(state)

    assert result["workflow_status"] == "failed"
    assert mock_sleep.call_count == 0
    assert result.get("human_approval_required") is not True
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_type == "other"
    assert "Run budget exceeded" in result["errors"][0].message
    assert result["execution_metadata"]["DebugAgent"].status == "failed"

