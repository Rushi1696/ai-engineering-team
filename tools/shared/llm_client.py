"""
tools/shared/llm_client.py

Protocol for structured LLM inference and FakeLLMClient test double.

Source of truth:
  - design.md Section 6.1 (tools/shared/llm_client.py -- LLM inference wrapper)
  - design.md Section 6.2 (Tools injected via constructors, not instantiated directly)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Type, TypeVar, Union
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


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


__all__ = ["LLMClient", "FakeLLMClient"]
