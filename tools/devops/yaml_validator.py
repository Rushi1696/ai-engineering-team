"""
tools/devops/yaml_validator.py

YAML/JSON/TOML validator protocol, validation report dataclass, and FakeConfigValidator test double.

Source of truth:
  - design.md Section 1.9 (DevOpsAgent Tools Required: YAML/JSON/TOML validator)
  - design.md Section 4.9 (DevOpsAgent Tools table)
  - design.md Section 6.1 (tools/devops/ owned by DevOps team)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union

from state.models import DeploymentArtifact


@dataclass
class ValidationReport:
    """Structured report returned from configuration syntax validation."""

    __test__ = False
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class ConfigValidator(Protocol):
    """Protocol for validating deployment configuration files (YAML, JSON, Dockerfiles, etc.)."""

    def validate_configs(
        self,
        deployment_artifacts: Dict[str, DeploymentArtifact],
    ) -> ValidationReport:
        """Validate syntax and integrity of deployment artifacts."""
        ...


class FakeConfigValidator:
    """Test double implementing ConfigValidator returning scripted results in order."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[ValidationReport, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[ValidationReport, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[ValidationReport, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted validation results."""
        self.results = list(results)

    def validate_configs(
        self,
        deployment_artifacts: Dict[str, DeploymentArtifact],
    ) -> ValidationReport:
        """Record invocation and return next scripted validation result."""
        self.calls.append({"deployment_artifacts": deployment_artifacts})
        if not self.results:
            return ValidationReport(
                valid=True,
                errors=[],
                warnings=[],
                details={},
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return ValidationReport(**next_res)
        return next_res
