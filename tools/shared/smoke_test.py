"""
tools/shared/smoke_test.py

Lightweight smoke test utility sending a single structured request to Gemini or Groq
to verify API credentials, network reachability, and structured JSON parsing.

Only executes against a real API when explicitly invoked with credentials configured.

Usage:
  python -m tools.shared.smoke_test --provider gemini [--model gemini-3.8-flash]
  python -m tools.shared.smoke_test --provider groq   [--model llama-3.3-70b-versatile]
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel

from tools.shared.llm_client import create_provider_client


class SmokeTestPayload(BaseModel):
    """Schema for smoke test structured response verification."""

    status: str
    provider: str
    message: str


def run_smoke_test(provider: str = "gemini", model: Optional[str] = None) -> int:
    """Run a live smoke test against the designated provider.

    Args:
        provider: 'gemini' or 'groq'.
        model: Optional model identifier override.

    Returns:
        0 on success, 1 on failure.
    """
    load_dotenv()
    normalized_provider = provider.lower().strip()

    if normalized_provider not in ("gemini", "groq"):
        print(
            f"[Smoke Test Error] Unsupported provider '{provider}'. Must be 'gemini' or 'groq'.",
            file=sys.stderr,
        )
        return 1

    env_var = "GEMINI_API_KEY" if normalized_provider == "gemini" else "GROQ_API_KEY"
    api_key = os.environ.get(env_var)

    if not api_key or not api_key.strip():
        print("=" * 60, file=sys.stderr)
        print(f"[Smoke Test] Credentials Missing: {env_var} is not set.", file=sys.stderr)
        print(f"Please configure {env_var} in your local .env file or environment.", file=sys.stderr)
        print("Reference template: .env.example", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        return 1

    print("=" * 60)
    print(f" SMOKE TEST: {normalized_provider.upper()} API CONNECTION")
    print("=" * 60)
    print(f" Provider:       {normalized_provider}")
    print(f" Target Model:   {model or 'default'}")
    print(f" Credential:     {env_var} is present (length: {len(api_key.strip())} chars)")
    print("-" * 60)
    print(" Sending structured inference request...")

    try:
        client = create_provider_client(provider=normalized_provider, default_model=model)
        result: SmokeTestPayload = client.generate_structured(
            prompt=(
                f"Verify API connectivity for {normalized_provider}. "
                "Respond with status='ok', provider set to your provider name, and a short message confirming connection."
            ),
            response_model=SmokeTestPayload,
            model_name=model,
            temperature=0.0,
        )
    except Exception as exc:
        print(f"\n[Smoke Test Error] Request failed: {exc}", file=sys.stderr)
        return 1

    print(" Response received and validated against Pydantic schema:")
    print(f"   * status:   {result.status}")
    print(f"   * provider: {result.provider}")
    print(f"   * message:  {result.message}")
    print("=" * 60)
    print(" [SUCCESS] API credentials and structured output validated successfully!")
    print("=" * 60)
    return 0


def main() -> int:
    """CLI entry point for smoke test."""
    parser = argparse.ArgumentParser(
        description="Smoke test for Gemini or Groq API provider connection"
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "groq"],
        default="gemini",
        help="Provider to test ('gemini' or 'groq')",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model identifier override (e.g. gemini-3.8-flash or llama-3.3-70b-versatile)",
    )

    args = parser.parse_args()
    return run_smoke_test(provider=args.provider, model=args.model)


if __name__ == "__main__":
    sys.exit(main())
