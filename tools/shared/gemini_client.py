"""
tools/shared/gemini_client.py

Google Gemini API adapter implementing the shared LLMClient protocol.
Uses the official google-genai SDK for structured JSON generation conforming
to Pydantic models.

Source of truth:
  - design.md Section 5 (LLM Strategy)
  - tools/shared/llm_client.py (LLMClient protocol)
"""

from __future__ import annotations

import os
from typing import Any, Optional, Type, TypeVar
from dotenv import load_dotenv
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def _clean_json_text(text: str) -> str:
    """Strip markdown code block fences if returned by the model."""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


class GeminiLLMClient:
    """LLMClient implementation backed by Google's Gemini API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "gemini-3.8-flash",
    ) -> None:
        """Initialize Gemini client.

        Args:
            api_key: Explicit API key. If None, reads from GEMINI_API_KEY env var
                (after loading .env).
            default_model: Fallback model identifier if not specified at call time.

        Raises:
            ValueError: If no API key is configured.
        """
        load_dotenv()
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not resolved_key or not resolved_key.strip():
            raise ValueError(
                "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable "
                "or configure it in your local .env file."
            )

        self.api_key: str = resolved_key.strip()
        self.default_model: str = default_model

        # Initialize Google GenAI client lazily
        from google import genai

        self._client = genai.Client(api_key=self.api_key)

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
    ) -> T:
        """Generate structured Pydantic output using Gemini native JSON schema enforcement.

        Args:
            prompt: User requirement or instructions prompt.
            response_model: Pydantic model class defining the desired schema.
            system_prompt: Optional persona or behavioral instruction.
            model_name: Optional model override. Defaults to self.default_model.
            temperature: Sampling temperature (0.0 for deterministic output).

        Returns:
            An instance of response_model populated from model response.

        Raises:
            RuntimeError: If Gemini API encounters rate limits, timeouts, or API errors.
            ValueError: If the response cannot be parsed or validated against response_model.
        """
        effective_model = model_name or self.default_model

        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=response_model,
        )

        try:
            response = self._client.models.generate_content(
                model=effective_model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            # Mask any potential credential exposure in exception messages
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED]")
            raise RuntimeError(f"Gemini API generation error ({effective_model}): {err_msg}") from exc

        if getattr(response, "parsed", None) is not None and isinstance(response.parsed, response_model):
            return response.parsed

        if not response or not getattr(response, "text", None):
            raise ValueError(f"Gemini returned an empty response for model {effective_model}")

        raw_text = response.text
        cleaned = _clean_json_text(raw_text)

        try:
            return response_model.model_validate_json(cleaned)
        except Exception as exc:
            preview = cleaned[:200] + "..." if len(cleaned) > 200 else cleaned
            raise ValueError(
                f"Failed to validate Gemini response against {response_model.__name__}: {exc}. "
                f"Response preview: {preview!r}"
            ) from exc


__all__ = ["GeminiLLMClient"]
