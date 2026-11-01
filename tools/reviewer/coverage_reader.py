"""
tools/reviewer/coverage_reader.py

Coverage report reader protocol, result dataclass, and FakeCoverageReader test double.

Source of truth:
  - design.md Section 1.8 (ReviewerAgent Tools Required: Coverage report reader)
  - design.md Section 4.8 (ReviewerAgent Tools table)
  - design.md Section 6.1 (tools/reviewer/ owned by Reviewer team)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union

from state.models import TestArtifact, TestPlan


@dataclass
class CoverageReport:
    """Structured report returned from test coverage evaluation."""

    __test__ = False
    coverage_percentage: float = 100.0
    uncovered_files: List[str] = field(default_factory=list)
    summary: str = "All lines covered"


class CoverageReportReader(Protocol):
    """Protocol for reading and evaluating test coverage against test artifacts and test plan."""

    def read_coverage(
        self,
        test_plan: Optional[TestPlan],
        test_artifacts: Dict[str, TestArtifact],
    ) -> CoverageReport:
        """Evaluate coverage from test plan and test artifacts."""
        ...


class FakeCoverageReader:
    """Test double implementing CoverageReportReader returning scripted results in order."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[CoverageReport, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[CoverageReport, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[CoverageReport, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted coverage reports."""
        self.results = list(results)

    def read_coverage(
        self,
        test_plan: Optional[TestPlan],
        test_artifacts: Dict[str, TestArtifact],
    ) -> CoverageReport:
        """Record invocation and return next scripted report in order."""
        self.calls.append({"test_plan": test_plan, "test_artifacts": test_artifacts})
        if not self.results:
            return CoverageReport(
                coverage_percentage=95.0,
                uncovered_files=[],
                summary="Adequate coverage across all test suites",
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return CoverageReport(**next_res)
        return next_res
