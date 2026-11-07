"""
tools/shared/gemini_client.py

Google Gemini API adapter implementing the shared LLMClient protocol.
Uses the official google-genai SDK for structured JSON generation conforming
to Pydantic models with quota protection and budget tracking.

Source of truth:
  - design.md Section 5 (LLM Strategy)
  - tools/shared/llm_client.py (LLMClient protocol)
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional, Type, TypeVar
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.shared.budget import RunBudget

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


def _is_retryable_status(exc: Exception) -> bool:
    """Check if exception corresponds to HTTP 429 (rate limit) or 503 (unavailable)."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in (429, 503):
        return True
    if code in (400, 401, 403, 404):
        return False

    msg = str(exc).upper()
    if any(k in msg for k in ["429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "RATE_LIMIT", "RATE LIMIT"]):
        return True
    return False


class GeminiLLMClient:
    """LLMClient implementation backed by Google's Gemini API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "gemini-3.8-flash",
        budget: Optional[RunBudget] = None,
        max_output_tokens: Optional[int] = 8192,
        request_timeout: Optional[float] = 60.0,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        """Initialize Gemini client.

        Args:
            api_key: Explicit API key. If None, reads from GEMINI_API_KEY env var
                (after loading .env).
            default_model: Fallback model identifier if not specified at call time.
            budget: Optional shared RunBudget instance for quota tracking.
            max_output_tokens: Maximum tokens in generated completion.
            request_timeout: Request timeout in seconds.
            sleep_fn: Injected sleep function for backoff (defaults to time.sleep).

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
        self.budget: Optional[RunBudget] = budget
        self.max_output_tokens: Optional[int] = max_output_tokens
        self.request_timeout: Optional[float] = request_timeout
        self.sleep_fn: Callable[[float], None] = sleep_fn if sleep_fn is not None else time.sleep

        # Initialize Google GenAI client lazily
        from google import genai

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.request_timeout is not None:
            from google.genai import types

            client_kwargs["http_options"] = types.HttpOptions(timeout=int(self.request_timeout * 1000))
        self._client = genai.Client(**client_kwargs)

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
            BudgetExceededError: If the run budget is exceeded.
            RuntimeError: If truncated by max_output_tokens, or on non-retryable API errors.
            ValueError: If the response cannot be parsed or validated against response_model.
        """
        effective_model = model_name or self.default_model
        prompt_len = len(prompt) + len(system_prompt or "")

        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
            "response_schema": response_model,
        }
        if self.max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = self.max_output_tokens

        config = types.GenerateContentConfig(**config_kwargs)

        max_adapter_retries = 3
        response = None
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_adapter_retries + 1):
            if self.budget is not None:
                self.budget.check_and_consume("gemini", prompt_len)

            try:
                response = self._client.models.generate_content(
                    model=effective_model,
                    contents=prompt,
                    config=config,
                )
                break
            except Exception as exc:
                last_exc = exc
                if _is_retryable_status(exc) and attempt < max_adapter_retries:
                    self.sleep_fn(1.0 * attempt)
                    continue

                err_msg = str(exc)
                if self.api_key and self.api_key in err_msg:
                    err_msg = err_msg.replace(self.api_key, "[REDACTED]")
                raise RuntimeError(f"Gemini API generation error ({effective_model}): {err_msg}") from exc

        # Check for token truncation
        if response and getattr(response, "candidates", None):
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, "finish_reason", None)
            finish_str = str(finish_reason).upper() if finish_reason else ""
            if "MAX_TOKENS" in finish_str or "LENGTH" in finish_str:
                raise RuntimeError(
                    f"Gemini response was truncated due to max_output_tokens limit ({self.max_output_tokens}). "
                    "Output is incomplete."
                )

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
