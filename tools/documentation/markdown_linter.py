"""
tools/documentation/markdown_linter.py

Markdown linter protocol, lint report dataclass, and FakeMarkdownLinter test double.

Source of truth:
  - design.md Section 1.10 (DocumentationAgent Tools Required: Markdown linter/validator)
  - design.md Section 4.10 (DocumentationAgent Tools table)
  - design.md Section 6.1 (tools/documentation/ owned by Documentation team)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union

from state.models import DocumentationArtifact


@dataclass
class LintReport:
    """Structured report returned from markdown linting and structure validation."""

    __test__ = False
    passed: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class MarkdownLinter(Protocol):
    """Protocol for validating markdown documentation structure and syntax."""

    def lint_documentation(
        self,
        documentation_artifacts: Dict[str, DocumentationArtifact],
    ) -> LintReport:
        """Lint documentation artifacts and return validation report."""
        ...


class FakeMarkdownLinter:
    """Test double implementing MarkdownLinter returning scripted results in order."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[LintReport, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[LintReport, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[LintReport, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted lint reports."""
        self.results = list(results)

    def lint_documentation(
        self,
        documentation_artifacts: Dict[str, DocumentationArtifact],
    ) -> LintReport:
        """Record invocation and return next scripted lint report."""
        self.calls.append({"documentation_artifacts": documentation_artifacts})
        if not self.results:
            return LintReport(
                passed=True,
                errors=[],
                warnings=[],
                details={},
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return LintReport(**next_res)
        return next_res
