"""
tools/shared/budget.py

Run budget enforcement, limit tracking, and BudgetedLLMClient wrapper.
Enforces hard limits on LLM usage (total calls, prompt chars per call, total prompt chars)
to protect free-tier API quotas.

Source of truth:
  - design.md Section 5 (LLM Strategy)
  - tools/shared/llm_client.py (LLMClient protocol)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type, TypeVar
from pydantic import BaseModel

from tools.shared.llm_client import LLMClient

T = TypeVar("T", bound=BaseModel)


class BudgetExceededError(BaseException):
    """Raised when an LLM call violates run budget constraints.

    Inherits from BaseException so agent retry loops (which catch Exception)
    do not swallow or retry it, ensuring immediate run termination.
    """

    pass


class RunBudget:
    """Shared singleton budget tracking usage across all LLM providers in a run."""

    def __init__(
        self,
        max_total_calls: Optional[int] = None,
        max_prompt_chars_per_call: Optional[int] = None,
        max_total_prompt_chars: Optional[int] = None,
    ) -> None:
        self.max_total_calls = max_total_calls
        self.max_prompt_chars_per_call = max_prompt_chars_per_call
        self.max_total_prompt_chars = max_total_prompt_chars

        self.total_calls: int = 0
        self.total_prompt_chars: int = 0
        self.usage_by_provider: Dict[str, Dict[str, int]] = {}

    def check_and_consume(self, provider: str, prompt_len: int) -> None:
        """Check limits and record usage for an upcoming LLM call.

        Checks use 'is not None' so that 0 is treated as a valid limit.

        Raises:
            BudgetExceededError: If any configured limit is violated.
        """
        if self.max_prompt_chars_per_call is not None and prompt_len > self.max_prompt_chars_per_call:
            raise BudgetExceededError(
                f"Prompt size {prompt_len} exceeds max_prompt_chars_per_call limit "
                f"({self.max_prompt_chars_per_call})."
            )

        if self.max_total_calls is not None and (self.total_calls + 1) > self.max_total_calls:
            raise BudgetExceededError(
                f"Run budget exceeded: total calls {self.total_calls + 1} exceeds "
                f"max_total_calls limit ({self.max_total_calls})."
            )

        if (
            self.max_total_prompt_chars is not None
            and (self.total_prompt_chars + prompt_len) > self.max_total_prompt_chars
        ):
            raise BudgetExceededError(
                f"Run budget exceeded: total prompt chars {self.total_prompt_chars + prompt_len} "
                f"exceeds max_total_prompt_chars limit ({self.max_total_prompt_chars})."
            )

        self.total_calls += 1
        self.total_prompt_chars += prompt_len

        stats = self.usage_by_provider.setdefault(
            provider, {"calls": 0, "prompt_chars": 0, "largest_prompt_chars": 0}
        )
        stats["calls"] += 1
        stats["prompt_chars"] += prompt_len
        if prompt_len > stats["largest_prompt_chars"]:
            stats["largest_prompt_chars"] = prompt_len

    def generate_report(self) -> str:
        """Generate formatted summary report of LLM usage and limits."""
        lines = [
            "=" * 70,
            " LLM BUDGET & USAGE REPORT:",
            f" Total Calls:         {self.total_calls}"
            + (f" / {self.max_total_calls}" if self.max_total_calls is not None else " (unlimited)"),
            f" Total Prompt Chars:  {self.total_prompt_chars:,}"
            + (
                f" / {self.max_total_prompt_chars:,}"
                if self.max_total_prompt_chars is not None
                else " (unlimited)"
            ),
        ]
        if self.usage_by_provider:
            lines.append(" Usage by Provider:")
            for prov, stats in sorted(self.usage_by_provider.items()):
                lines.append(
                    f"   - {prov}: {stats['calls']} call(s), "
                    f"{stats['prompt_chars']:,} prompt chars "
                    f"(largest single prompt: {stats.get('largest_prompt_chars', 0):,} chars)"
                )
        else:
            lines.append(" Usage by Provider: none")
        lines.append("=" * 70)
        return "\n".join(lines)


class BudgetedLLMClient:
    """Wrapper around any LLMClient enforcing RunBudget limits."""

    def __init__(
        self,
        inner_client: LLMClient,
        budget: RunBudget,
        provider_name: str = "llm",
    ) -> None:
        self.inner_client = inner_client
        self.budget = budget
        self.provider_name = provider_name

    def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
    ) -> T:
        """Check budget before delegating to inner client."""
        prompt_len = len(prompt) + len(system_prompt or "")
        self.budget.check_and_consume(self.provider_name, prompt_len)
        return self.inner_client.generate_structured(
            prompt=prompt,
            response_model=response_model,
            system_prompt=system_prompt,
            model_name=model_name,
            temperature=temperature,
        )


__all__ = [
    "BudgetExceededError",
    "RunBudget",
    "BudgetedLLMClient",
]

