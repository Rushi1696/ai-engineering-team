"""
tests/tools/test_groq_client.py

Unit tests for GroqLLMClient using mocked groq SDK calls.
Ensures zero live network calls and no requirement for real API keys during testing.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from pydantic import BaseModel

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


def test_groq_client_generate_structured_success():
    """GroqLLMClient sends messages with schema instructions and parses JSON response."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"summary": "Groq structured output", "score": 92})
    mock_completion = MagicMock(choices=[mock_choice])
    mock_client.chat.completions.create.return_value = mock_completion

    with patch("groq.Groq", return_value=mock_client):
        client = GroqLLMClient(api_key="mock-groq-key", default_model="llama-3.3-70b-versatile")
        client._client = mock_client

        result = client.generate_structured(
            prompt="Plan tasks",
            response_model=SampleSchema,
            system_prompt="Be an architect",
            model_name="llama-3.1-8b-instant",
            temperature=0.1,
        )

        assert isinstance(result, SampleSchema)
        assert result.summary == "Groq structured output"
        assert result.score == 92

        mock_client.chat.completions.create.assert_called_once()
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "llama-3.1-8b-instant"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert any("SampleSchema" in m["content"] for m in kwargs["messages"])


def test_groq_client_cleans_markdown_fences():
    """GroqLLMClient handles responses wrapped in markdown code fences."""
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "```json\n" + json.dumps({"summary": "Fenced output", "score": 88}) + "\n```"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    client = GroqLLMClient(api_key="mock-key")
    client._client = mock_client

    result = client.generate_structured(prompt="test", response_model=SampleSchema)
    assert result.summary == "Fenced output"
    assert result.score == 88


def test_groq_client_api_error_handling_and_secret_masking():
    """GroqLLMClient catches API errors, raises RuntimeError, and redacts the API key."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Rate limit hit with gsk_secret_key_123")

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

    client = GroqLLMClient(api_key="mock-key")
    client._client = mock_client

    with pytest.raises(ValueError, match="Failed to validate Groq response"):
        client.generate_structured(prompt="test", response_model=SampleSchema)
