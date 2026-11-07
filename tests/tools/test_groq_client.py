"""
tests/tools/test_groq_client.py

Unit tests for GroqLLMClient using mocked groq SDK calls.
Ensures zero live network calls and no requirement for real API keys during testing.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from pydantic import BaseModel

from tools.shared.budget import RunBudget
from tools.shared.groq_client import GroqLLMClient


class SampleSchema(BaseModel):
    summary: str
    score: int


def test_groq_client_missing_key_raises(monkeypatch):
    """GroqLLMClient raises ValueError when GROQ_API_KEY is not configured."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with patch("tools.shared.groq_client.load_dotenv"):
        with pytest.raises(ValueError, match="GROQ_API_KEY is not set"):
            GroqLLMClient(api_key=None)


def test_groq_client_default_model_is_gpt_oss_120b():
    """GroqLLMClient default_model is openai/gpt-oss-120b when unspecified."""
    with patch("groq.Groq"):
        client = GroqLLMClient(api_key="mock-key")
        assert client.default_model == "openai/gpt-oss-120b"


def test_groq_client_generate_structured_success():
    """GroqLLMClient sends messages with schema instructions and parses JSON response."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"summary": "Groq structured output", "score": 92})
    mock_completion = MagicMock(choices=[mock_choice])
    mock_client.chat.completions.create.return_value = mock_completion

    with patch("groq.Groq", return_value=mock_client):
        client = GroqLLMClient(api_key="mock-groq-key")
        client._client = mock_client

        # Call with explicit model override
        result = client.generate_structured(
            prompt="Plan tasks",
            response_model=SampleSchema,
            system_prompt="Be an architect",
            model_name="qwen/qwen3.8-27b",
            temperature=0.1,
        )

        assert isinstance(result, SampleSchema)
        assert result.summary == "Groq structured output"
        assert result.score == 92

        mock_client.chat.completions.create.assert_called_once()
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "qwen/qwen3.8-27b"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert any("SampleSchema" in m["content"] for m in kwargs["messages"])

        # Call with fallback to default_model (openai/gpt-oss-120b)
        mock_client.chat.completions.create.reset_mock()
        default_result = client.generate_structured(
            prompt="Default model check",
            response_model=SampleSchema,
        )
        assert isinstance(default_result, SampleSchema)
        default_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert default_kwargs["model"] == "openai/gpt-oss-120b"


def test_groq_client_cleans_markdown_fences():
    """GroqLLMClient handles responses wrapped in markdown code fences."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "```json\n" + json.dumps({"summary": "Fenced output", "score": 88}) + "\n```"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    with patch("groq.Groq"):
        client = GroqLLMClient(api_key="mock-key", default_model="openai/gpt-oss-120b")
        client._client = mock_client

        result = client.generate_structured(prompt="test", response_model=SampleSchema)
        assert result.summary == "Fenced output"
        assert result.score == 88


def test_groq_client_api_error_handling_and_secret_masking():
    """GroqLLMClient catches API errors, raises RuntimeError, and redacts the API key."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Rate limit hit with gsk_secret_key_123")

    with patch("groq.Groq"):
        client = GroqLLMClient(api_key="gsk_secret_key_123")
        client._client = mock_client

        with pytest.raises(RuntimeError) as exc_info:
            client.generate_structured(prompt="test", response_model=SampleSchema)

        assert "gsk_secret_key_123" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


def test_groq_client_empty_response_raises():
    """GroqLLMClient raises ValueError if choices or content are empty."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[])

    with patch("groq.Groq"):
        client = GroqLLMClient(api_key="mock-key")
        client._client = mock_client

        with pytest.raises(ValueError, match="empty response"):
            client.generate_structured(prompt="test", response_model=SampleSchema)


def test_groq_client_malformed_json_raises():
    """GroqLLMClient raises ValueError when content cannot be parsed as JSON."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "invalid { json"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    with patch("groq.Groq"):
        client = GroqLLMClient(api_key="mock-key")
        client._client = mock_client

        with pytest.raises(ValueError, match="Failed to validate Groq response"):
            client.generate_structured(prompt="test", response_model=SampleSchema)


def test_groq_client_prompt_len_includes_schema_instruction():
    """GroqLLMClient accounts for full system content (system_prompt + schema_instruction) in prompt length."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"summary": "ok", "score": 10})
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    budget = RunBudget(max_total_calls=5)
    with patch("groq.Groq", return_value=mock_client):
        client = GroqLLMClient(api_key="mock-key", budget=budget)
        client._client = mock_client

        user_prompt = "Plan architecture"
        sys_prompt = "You are a software architect"
        client.generate_structured(
            prompt=user_prompt,
            response_model=SampleSchema,
            system_prompt=sys_prompt,
        )

        sent_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
        system_msg_content = sent_messages[0]["content"]
        assert sys_prompt in system_msg_content
        assert "JSON schema" in system_msg_content

        expected_prompt_len = len(user_prompt) + len(system_msg_content)
        assert budget.total_prompt_chars == expected_prompt_len
        assert budget.usage_by_provider["groq"]["prompt_chars"] == expected_prompt_len

        # Test without system_prompt (only schema instruction)
        client.generate_structured(
            prompt=user_prompt,
            response_model=SampleSchema,
            system_prompt=None,
        )
        sent_messages_2 = mock_client.chat.completions.create.call_args.kwargs["messages"]
        system_msg_content_2 = sent_messages_2[0]["content"]
        expected_len_2 = len(user_prompt) + len(system_msg_content_2)
        assert budget.total_prompt_chars == expected_prompt_len + expected_len_2

