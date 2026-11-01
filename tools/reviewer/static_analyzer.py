"""
tools/reviewer/static_analyzer.py

Static analysis tool protocol, result dataclass, and FakeStaticAnalyzer test double.

Source of truth:
  - design.md Section 1.8 (ReviewerAgent Tools Required: Static analysis tool)
  - design.md Section 4.8 (ReviewerAgent Tools table)
  - design.md Section 6.1 (tools/reviewer/ owned by Reviewer team)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union

from state.models import CodeArtifact


@dataclass
class StaticAnalysisReport:
    """Structured report returned from static code analysis."""

    __test__ = False
    passed: bool = True
    complexity_score: float = 1.0
    security_warnings: List[str] = field(default_factory=list)
    maintainability_warnings: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)


class StaticAnalysisTool(Protocol):
    """Protocol for analyzing code artifacts for security and maintainability."""

    def analyze_code(
        self,
        code_artifacts: Dict[str, CodeArtifact],
    ) -> StaticAnalysisReport:
        """Run static analysis against code artifacts and return report."""
        ...


class FakeStaticAnalyzer:
    """Test double implementing StaticAnalysisTool returning scripted results in order."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[StaticAnalysisReport, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[StaticAnalysisReport, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[StaticAnalysisReport, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted analysis results."""
        self.results = list(results)

    def analyze_code(
        self,
        code_artifacts: Dict[str, CodeArtifact],
    ) -> StaticAnalysisReport:
        """Record invocation and return next scripted result in order."""
        self.calls.append({"code_artifacts": code_artifacts})
        if not self.results:
            return StaticAnalysisReport(
                passed=True,
                complexity_score=1.5,
                security_warnings=[],
                maintainability_warnings=[],
                issues=[],
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return StaticAnalysisReport(**next_res)
        return next_res
