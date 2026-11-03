"""
tools/shared/groq_client.py

Groq API adapter implementing the shared LLMClient protocol.
Uses the official groq SDK for fast inference and JSON-mode structured output
conforming to Pydantic models.

Source of truth:
  - design.md Section 5 (LLM Strategy)
  - tools/shared/llm_client.py (LLMClient protocol)
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Optional, Type, TypeVar
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


class GroqLLMClient:
    """LLMClient implementation backed by Groq Cloud API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        default_model: str = "llama-3.3-70b-versatile",
    ) -> None:
        """Initialize Groq client.

        Args:
            api_key: Explicit API key. If None, reads from GROQ_API_KEY env var
                (after loading .env).
            default_model: Fallback model identifier if not specified at call time.

        Raises:
            ValueError: If no API key is configured.
        """
        load_dotenv()
        resolved_key = api_key or os.environ.get("GROQ_API_KEY")
        if not resolved_key or not resolved_key.strip():
            raise ValueError(
                "GROQ_API_KEY is not set. Please set the GROQ_API_KEY environment variable "
                "or configure it in your local .env file."
            )

        self.api_key: str = resolved_key.strip()
        self.default_model: str = default_model

        # Initialize Groq client lazily
        from groq import Groq

        self._client = Groq(api_key=self.api_key)

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
    ) -> T:
        """Generate structured Pydantic output using Groq JSON mode and schema prompting.

        Args:
            prompt: User requirement or instructions prompt.
            response_model: Pydantic model class defining the desired schema.
            system_prompt: Optional persona or behavioral instruction.
            model_name: Optional model override. Defaults to self.default_model.
            temperature: Sampling temperature (0.0 for deterministic output).

        Returns:
            An instance of response_model populated from model response.

        Raises:
            RuntimeError: If Groq API encounters rate limits, timeouts, or API errors.
            ValueError: If the response cannot be parsed or validated against response_model.
        """
        effective_model = model_name or self.default_model

        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        schema_instruction = (
            "You MUST output valid, syntactically correct JSON strictly adhering "
            f"to the following JSON schema with no extra keys or text:\n{schema_json}"
        )

        messages: List[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": f"{system_prompt}\n\n{schema_instruction}"})
        else:
            messages.append({"role": "system", "content": schema_instruction})

        messages.append({"role": "user", "content": prompt})

        try:
            completion = self._client.chat.completions.create(
                model=effective_model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            err_msg = str(exc)
            if self.api_key and self.api_key in err_msg:
                err_msg = err_msg.replace(self.api_key, "[REDACTED]")
            raise RuntimeError(f"Groq API generation error ({effective_model}): {err_msg}") from exc

        if not completion.choices or not completion.choices[0].message.content:
            raise ValueError(f"Groq returned an empty response for model {effective_model}")

        content = completion.choices[0].message.content
        cleaned = _clean_json_text(content)

        try:
            return response_model.model_validate_json(cleaned)
        except Exception as exc:
            preview = cleaned[:200] + "..." if len(cleaned) > 200 else cleaned
            raise ValueError(
                f"Failed to validate Groq response against {response_model.__name__}: {exc}. "
                f"Response preview: {preview!r}"
            ) from exc


__all__ = ["GroqLLMClient"]
