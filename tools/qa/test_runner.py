"""
tools/qa/test_runner.py

Test runner protocol, result dataclass, and FakeTestRunner test double.

Source of truth:
  - design.md Section 1.6 (QAAgent Tools Required: Test runner)
  - design.md Section 4.6 (QAAgent Tools table)
  - design.md Section 6.1 (tools/qa/test_runner.py owned by QA team)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union

from state.models import CodeArtifact, TestArtifact


@dataclass
class TestRunResult:
    """Structured report returned from test runner execution."""

    __test__ = False
    passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_seconds: float
    output: str
    failing_tests: List[str] = field(default_factory=list)
    coverage_percentage: Optional[float] = None


class TestRunner(Protocol):
    """Protocol for executing tests against code artifacts."""

    def run_tests(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Dict[str, TestArtifact],
    ) -> TestRunResult:
        """Run generated test artifacts against code artifacts and return structured result."""
        ...


class FakeTestRunner:
    """Test double implementing TestRunner returning scripted results in sequential order."""

    def __init__(
        self,
        results: Optional[List[Union[TestRunResult, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[TestRunResult, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[TestRunResult, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted test run results."""
        self.results = list(results)

    def run_tests(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Dict[str, TestArtifact],
    ) -> TestRunResult:
        """Record invocation and return next scripted result in order."""
        self.calls.append(
            {
                "code_artifacts": code_artifacts,
                "test_artifacts": test_artifacts,
            }
        )
        if not self.results:
            return TestRunResult(
                passed=True,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                duration_seconds=0.01,
                output="1 passed in 0.01s",
                failing_tests=[],
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return TestRunResult(**next_res)
        return next_res
