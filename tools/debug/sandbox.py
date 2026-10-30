"""
tools/debug/sandbox.py

Code execution sandbox protocol, result dataclass, and FakeExecutionSandbox test double.

Source of truth:
  - design.md Section 1.7 (DebugAgent Tools Required: Code execution sandbox)
  - design.md Section 4.7 (DebugAgent Tools table)
  - design.md Section 6.1 (tools/debug/ owned by Debug team)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Union

from state.models import CodeArtifact, TestArtifact


@dataclass
class SandboxExecutionResult:
    """Structured report returned from sandbox code execution."""

    __test__ = False
    passed: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    duration_seconds: float = 0.0


class CodeExecutionSandbox(Protocol):
    """Protocol for safely executing code and tests in an isolated sandbox."""

    def execute_in_sandbox(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Optional[Dict[str, TestArtifact]] = None,
        command: Optional[str] = None,
    ) -> SandboxExecutionResult:
        """Run code and test artifacts inside an isolated sandbox and return result."""
        ...


class FakeExecutionSandbox:
    """Test double implementing CodeExecutionSandbox returning scripted results in order."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[SandboxExecutionResult, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[SandboxExecutionResult, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[SandboxExecutionResult, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted execution results."""
        self.results = list(results)

    def execute_in_sandbox(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Optional[Dict[str, TestArtifact]] = None,
        command: Optional[str] = None,
    ) -> SandboxExecutionResult:
        """Record invocation and return next scripted result in order."""
        self.calls.append(
            {
                "code_artifacts": code_artifacts,
                "test_artifacts": test_artifacts or {},
                "command": command,
            }
        )
        if not self.results:
            return SandboxExecutionResult(
                passed=True,
                exit_code=0,
                stdout="Sandbox test execution passed",
                stderr="",
                output="Sandbox test execution passed",
                duration_seconds=0.01,
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return SandboxExecutionResult(**next_res)
        return next_res

