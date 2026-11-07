"""
tests/workflows/test_factory_providers.py

Unit and integration tests for multi-provider agent factory creation
and smoke test CLI utility with mocked credentials.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from tools.shared.gemini_client import GeminiLLMClient
from tools.shared.groq_client import GroqLLMClient
from tools.shared.llm_client import FakeLLMClient, create_provider_client
from tools.shared.smoke_test import run_smoke_test
from workflows.constants import AGENT_NODE_ORDER
from workflows.factory import create_agents


def test_create_provider_client_factory():
    """create_provider_client returns matching client instance or raises ValueError for unknown provider."""
    # Fake
    fake = create_provider_client("fake")
    assert isinstance(fake, FakeLLMClient)

    # Gemini with mock key
    with patch("google.genai.Client"):
        gemini = create_provider_client("gemini", api_key="test-key")
        assert isinstance(gemini, GeminiLLMClient)
        assert gemini.default_model == "gemini-3.8-flash"

    # Groq with mock key
    with patch("groq.Groq"):
        groq = create_provider_client("groq", api_key="test-key")
        assert isinstance(groq, GroqLLMClient)
        assert groq.default_model == "openai/gpt-oss-120b"

    # Unsupported
    with pytest.raises(ValueError, match="Unsupported LLM provider 'unsupported'"):
        create_provider_client("unsupported")


def test_create_agents_with_mocked_gemini_provider(monkeypatch):
    """create_agents(provider='gemini') configures Gemini models and injects GeminiLLMClient."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    with patch("google.genai.Client"):
        agents = create_agents(provider="gemini")

        assert tuple(agents.keys()) == AGENT_NODE_ORDER
        for node_name, agent in agents.items():
            assert isinstance(agent.llm_client, GeminiLLMClient)
            assert "gemini" in agent.model_name.lower()

        # Product manager uses the new default Gemini model
        assert agents["product_manager"].model_name == "gemini-3.8-flash"


def test_create_agents_with_mocked_groq_provider(monkeypatch):
    """create_agents(provider='groq') configures Groq models and injects GroqLLMClient."""
    monkeypatch.setenv("GROQ_API_KEY", "mock-groq-key")
    with patch("groq.Groq"):
        agents = create_agents(provider="groq")

        assert tuple(agents.keys()) == AGENT_NODE_ORDER
        for node_name, agent in agents.items():
            assert isinstance(agent.llm_client, GroqLLMClient)
            assert any(m in agent.model_name.lower() for m in ["gpt-oss", "qwen", "llama", "mixtral", "gemma"])

        # Product manager uses the new default Groq model
        assert agents["product_manager"].model_name == "openai/gpt-oss-120b"


def test_create_agents_per_agent_provider_config(tmp_path: Path, monkeypatch):
    """create_agents supports configuring different providers for different agents."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "mock-groq-key")

    custom_yaml = tmp_path / "mixed_agents.yaml"
    custom_yaml.write_text(
        """
workflow:
  max_debug_cycles: 10
  default_provider: "fake"

agents:
  product_manager:
    provider: "gemini"
    model: "gemini-3.8-flash"
    max_retries: 3
    timeout_seconds: 120
  architect:
    provider: "groq"
    model: "openai/gpt-oss-120b"
    max_retries: 3
    timeout_seconds: 180
  task_planner:
    provider: "fake"
    model: "test-fake-planner"
    max_retries: 3
    timeout_seconds: 120
  backend:
    model: "test-fake-be"
  frontend:
    model: "test-fake-fe"
  qa:
    model: "test-fake-qa"
  debug:
    model: "test-fake-dbg"
  reviewer:
    model: "test-fake-rev"
  devops:
    model: "test-fake-ops"
  documentation:
    model: "test-fake-doc"
  final_project:
    model: "test-fake-fp"
""",
        encoding="utf-8",
    )

    with patch("google.genai.Client"), patch("groq.Groq"):
        agents = create_agents(config_path=custom_yaml)

        assert isinstance(agents["product_manager"].llm_client, GeminiLLMClient)
        assert agents["product_manager"].model_name == "gemini-3.8-flash"

        assert isinstance(agents["architect"].llm_client, GroqLLMClient)
        assert agents["architect"].model_name == "openai/gpt-oss-120b"

        assert isinstance(agents["task_planner"].llm_client, FakeLLMClient)
        assert agents["task_planner"].model_name == "test-fake-planner"


def test_smoke_test_missing_credentials_exits_with_code_1(monkeypatch):
    """run_smoke_test returns 1 when required credentials are missing."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch("tools.shared.smoke_test.load_dotenv"):
        exit_code = run_smoke_test(provider="gemini")
        assert exit_code == 1


def test_smoke_test_with_mocked_credentials_succeeds(monkeypatch):
    """run_smoke_test returns 0 and validates schema when API responds."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    mock_genai_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"status": "ok", "provider": "gemini", "message": "Connection active"}'
    mock_genai_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_genai_client):
        exit_code = run_smoke_test(provider="gemini")
        assert exit_code == 0


def test_smoke_test_groq_missing_credentials_exits_with_code_1(monkeypatch):
    """run_smoke_test returns 1 when GROQ_API_KEY is missing."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with patch("tools.shared.smoke_test.load_dotenv"):
        exit_code = run_smoke_test(provider="groq")
        assert exit_code == 1


def test_smoke_test_groq_with_mocked_credentials_succeeds(monkeypatch):
    """run_smoke_test returns 0 and validates schema when Groq API responds."""
    monkeypatch.setenv("GROQ_API_KEY", "mock-groq-key")
    mock_groq_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"status": "ok", "provider": "groq", "message": "Connection active"}'
    mock_groq_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    with patch("groq.Groq", return_value=mock_groq_client):
        exit_code = run_smoke_test(provider="groq")
        assert exit_code == 0


def test_smoke_test_unsupported_provider():
    """run_smoke_test returns 1 for unsupported provider."""
    exit_code = run_smoke_test(provider="unsupported")
    assert exit_code == 1


def test_create_agents_different_overrides_get_different_clients_with_shared_budget(tmp_path: Path, monkeypatch):
    """Two agents with different max_output_tokens on same provider get different clients with shared budget."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-key")
    custom_yaml = tmp_path / "custom_agents.yaml"
    custom_yaml.write_text(
        """
workflow:
  max_debug_cycles: 10
  default_provider: "gemini"

agents:
  product_manager:
    model: "gemini-3.8-flash"
    max_output_tokens: 4096
    timeout_seconds: 60
  architect:
    model: "gemini-3.8-flash"
    max_output_tokens: 8192
    timeout_seconds: 60
  task_planner:
    model: "gemini-3.8-flash"
    max_output_tokens: 4096
    timeout_seconds: 60
  backend:
    model: "gemini-3.8-flash"
  frontend:
    model: "gemini-3.8-flash"
  qa:
    model: "gemini-3.8-flash"
  debug:
    model: "gemini-3.8-flash"
  reviewer:
    model: "gemini-3.8-flash"
  devops:
    model: "gemini-3.8-flash"
  documentation:
    model: "gemini-3.8-flash"
  final_project:
    model: "gemini-3.8-flash"
""",
        encoding="utf-8",
    )

    with patch("google.genai.Client"):
        agents = create_agents(config_path=custom_yaml)
        pm_client = agents["product_manager"].llm_client
        arch_client = agents["architect"].llm_client
        planner_client = agents["task_planner"].llm_client

        # Different max_output_tokens yield distinct client instances
        assert pm_client is not arch_client
        assert pm_client.max_output_tokens == 4096
        assert arch_client.max_output_tokens == 8192

        # Same max_output_tokens & timeout reuses cached client instance
        assert pm_client is planner_client

        # Budget object is shared across both different clients
        assert pm_client.budget is not None
        assert pm_client.budget is arch_client.budget

