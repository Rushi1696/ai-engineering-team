"""
tools/shared/llm_client.py

Protocol for structured LLM inference and FakeLLMClient test double.

Source of truth:
  - design.md Section 6.1 (tools/shared/llm_client.py -- LLM inference wrapper)
  - design.md Section 6.2 (Tools injected via constructors, not instantiated directly)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol, Type, TypeVar, Union, runtime_checkable
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for structured LLM inference across agents."""

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
    ) -> T:
        """Generate structured output conforming to a Pydantic response_model."""
        ...


class FakeLLMClient:
    """Test double implementing LLMClient with deterministic responses and exception raising."""

    def __init__(
        self,
        responses: Optional[List[Union[BaseModel, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.responses: List[Union[BaseModel, Dict[str, Any], Exception]] = (
            list(responses) if responses is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_responses(
        self, responses: List[Union[BaseModel, Dict[str, Any], Exception]]
    ) -> None:
        """Replace remaining configured responses."""
        self.responses = list(responses)

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
    ) -> T:
        self.calls.append(
            {
                "prompt": prompt,
                "response_model": response_model,
                "system_prompt": system_prompt,
                "model_name": model_name,
                "temperature": temperature,
            }
        )

        if not self.responses:
            raise RuntimeError("FakeLLMClient: No configured response available.")

        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, response_model):
            return resp
        if isinstance(resp, dict):
            return response_model(**resp)
        raise TypeError(
            f"FakeLLMClient: Unsupported response type '{type(resp).__name__}' for {response_model.__name__}"
        )


def create_provider_client(
    provider: str,
    default_model: Optional[str] = None,
    api_key: Optional[str] = None,
    budget: Optional[Any] = None,
    max_output_tokens: Optional[int] = None,
    timeout_seconds: Optional[float] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> LLMClient:
    """Factory creating an LLMClient instance for the specified provider ('fake', 'gemini', 'groq').

    Args:
        provider: Provider name ('fake', 'gemini', 'groq').
        default_model: Optional default model name.
        api_key: Optional explicit API key override.
        budget: Optional shared RunBudget instance.
        max_output_tokens: Optional token cap per call.
        timeout_seconds: Optional request timeout in seconds.
        sleep_fn: Optional sleep function for backoff.

    Returns:
        An instantiated LLMClient implementation.

    Raises:
        ValueError: If provider name is unsupported or required credentials are missing.
    """
    normalized = provider.lower().strip()
    if normalized == "fake":
        return FakeLLMClient()

    if normalized == "gemini":
        from tools.shared.gemini_client import GeminiLLMClient

        kwargs: Dict[str, Any] = {}
        if default_model:
            kwargs["default_model"] = default_model
        if api_key:
            kwargs["api_key"] = api_key
        if budget is not None:
            kwargs["budget"] = budget
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if timeout_seconds is not None:
            kwargs["request_timeout"] = timeout_seconds
        if sleep_fn is not None:
            kwargs["sleep_fn"] = sleep_fn
        return GeminiLLMClient(**kwargs)

    if normalized == "groq":
        from tools.shared.groq_client import GroqLLMClient

        kwargs = {}
        if default_model:
            kwargs["default_model"] = default_model
        if api_key:
            kwargs["api_key"] = api_key
        if budget is not None:
            kwargs["budget"] = budget
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if timeout_seconds is not None:
            kwargs["request_timeout"] = timeout_seconds
        if sleep_fn is not None:
            kwargs["sleep_fn"] = sleep_fn
        return GroqLLMClient(**kwargs)

    raise ValueError(
        f"Unsupported LLM provider '{provider}'. Supported options: 'fake', 'gemini', 'groq'"
    )


__all__ = ["LLMClient", "FakeLLMClient", "create_provider_client"]
