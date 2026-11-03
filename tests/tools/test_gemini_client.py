"""
tests/tools/test_gemini_client.py

Unit tests for GeminiLLMClient using mocked google-genai SDK calls.
Ensures zero live network calls and no requirement for real API keys during testing.
"""

import json
from unittest.mock import MagicMock, patch
import pytest
from pydantic import BaseModel

from tools.shared.gemini_client import GeminiLLMClient


class SampleSchema(BaseModel):
    summary: str
    score: int


def test_gemini_client_missing_key_raises(monkeypatch):
    """GeminiLLMClient raises ValueError when GEMINI_API_KEY is not configured."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with patch("tools.shared.gemini_client.load_dotenv"):
        with pytest.raises(ValueError, match="GEMINI_API_KEY is not set"):
            GeminiLLMClient(api_key=None)


def test_gemini_client_default_model_is_gemini_3_8_flash():
    """GeminiLLMClient default_model is gemini-3.8-flash when unspecified."""
    with patch("google.genai.Client"):
        client = GeminiLLMClient(api_key="mock-key")
        assert client.default_model == "gemini-3.8-flash"


def test_gemini_client_generate_structured_success():
    """GeminiLLMClient passes prompt and config to SDK and parses response into Pydantic model."""
    mock_genai = MagicMock()
    mock_client = MagicMock()
    mock_genai.Client.return_value = mock_client

    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = json.dumps({"summary": "Gemini structured output", "score": 98})
    mock_client.models.generate_content.return_value = mock_response

    with patch.dict("sys.modules", {"google.genai": mock_genai}):
        with patch("google.genai.Client", return_value=mock_client):
            client = GeminiLLMClient(api_key="mock-key-123", default_model="gemini-3.8-flash")
            client._client = mock_client

            result = client.generate_structured(
                prompt="Generate summary",
                response_model=SampleSchema,
                system_prompt="Be concise",
                model_name="gemini-2.5-pro",
                temperature=0.2,
            )

            assert isinstance(result, SampleSchema)
            assert result.summary == "Gemini structured output"
            assert result.score == 98

            mock_client.models.generate_content.assert_called_once()
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            assert call_kwargs["model"] == "gemini-2.5-pro"
            assert call_kwargs["contents"] == "Generate summary"


def test_gemini_client_uses_parsed_response_when_available():
    """GeminiLLMClient returns response.parsed directly when populated by SDK."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = SampleSchema(summary="Direct parsed", score=100)
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client"):
        client = GeminiLLMClient(api_key="mock-key")
        client._client = mock_client

        result = client.generate_structured(prompt="test", response_model=SampleSchema)
        assert isinstance(result, SampleSchema)
        assert result.summary == "Direct parsed"
        assert result.score == 100


def test_gemini_client_cleans_markdown_fences():
    """GeminiLLMClient handles responses wrapped in markdown json fences."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = "```json\n" + json.dumps({"summary": "Fenced output", "score": 85}) + "\n```"
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client"):
        client = GeminiLLMClient(api_key="mock-key", default_model="gemini-3.8-flash")
        client._client = mock_client

        result = client.generate_structured(prompt="test", response_model=SampleSchema)
        assert result.summary == "Fenced output"
        assert result.score == 85


def test_gemini_client_api_error_handling_and_secret_masking():
    """GeminiLLMClient catches API errors, raises RuntimeError, and redacts the API key."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = RuntimeError("Rate limit hit with mock-secret-key-999")

    with patch("google.genai.Client"):
        client = GeminiLLMClient(api_key="mock-secret-key-999")
        client._client = mock_client

        with pytest.raises(RuntimeError) as exc_info:
            client.generate_structured(prompt="test", response_model=SampleSchema)

        assert "mock-secret-key-999" not in str(exc_info.value)
        assert "[REDACTED]" in str(exc_info.value)


def test_gemini_client_empty_response_raises():
    """GeminiLLMClient raises ValueError if the API returns an empty text payload."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = ""
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client"):
        client = GeminiLLMClient(api_key="mock-key")
        client._client = mock_client

        with pytest.raises(ValueError, match="empty response"):
            client.generate_structured(prompt="test", response_model=SampleSchema)


def test_gemini_client_malformed_json_raises():
    """GeminiLLMClient raises ValueError when response is not valid JSON for response_model."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = None
    mock_response.text = "not a valid json object"
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client"):
        client = GeminiLLMClient(api_key="mock-key")
        client._client = mock_client

        with pytest.raises(ValueError, match="Failed to validate Gemini response"):
            client.generate_structured(prompt="test", response_model=SampleSchema)
