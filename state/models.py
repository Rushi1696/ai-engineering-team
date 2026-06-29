"""
state/models.py

The 14 Pydantic v2 models referenced by EngineeringState, per:
  - requirements.md Req 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 20
  - design.md Section 1, Section 2 (State Ownership Matrix)
  - Technical Design Document, Phase 1: EngineeringState, Section 5

Design constraints carried over from the approved technical design:
  - All 14 models are independent siblings. No shared base class
    (Section 5.1 — explicitly left out of scope for Phase 1).
  - model_config = ConfigDict(frozen=False, validate_assignment=True)
    is repeated on every model individually (Section 5.2).
  - Every field type is JSON-serializable: str, int, float, bool,
    list, dict, Optional[...] thereof. No datetime, no set, no
    non-string dict keys anywhere (Section 6.2 / Req 19 crit 8).
  - Timestamps are ISO 8601 strings, never datetime objects.
  - Optional fields default to None (Req 19 crit 3).
  - List fields use default_factory=list (Req 19 crit 4).
  - Dict fields use default_factory=dict (Req 19 crit 5).
  - Two whole-model invariants (TaskPlan, FinalProjectOutput) are
    enforced via @model_validator(mode="after") per Section 5.5.

No architectural deviation. No new fields beyond what the approved
requirements/design/technical-design documents specify.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Req 3 / design.md Section 1.1 (ProductManagerAgent output)
# ---------------------------------------------------------------------------


class ProductRequirements(BaseModel):
    """Structured requirements output produced by ProductManagerAgent."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    user_stories: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    business_goals: list[str] = Field(default_factory=list)

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 4 / design.md Section 1.2 (ArchitectAgent output)
# ---------------------------------------------------------------------------


class SystemArchitecture(BaseModel):
    """Structured architecture output produced by ArchitectAgent."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    overview: str
    technology_stack: dict[str, str] = Field(default_factory=dict)
    api_specifications: list[dict[str, Any]] = Field(default_factory=list)
    database_design: dict[str, Any] = Field(default_factory=dict)
    security_architecture: dict[str, Any] = Field(default_factory=dict)
    scalability_strategy: str = ""
    deployment_model: str = ""

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 5 / design.md Section 1.3 (TaskPlannerAgent output)
# ---------------------------------------------------------------------------


class ImplementationTask(BaseModel):
    """A single task within a TaskPlan."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    task_id: str
    title: str
    description: str
    assigned_agent: str
    priority: int = Field(ge=1, le=100)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"


class TaskPlan(BaseModel):
    """Structured task plan output produced by TaskPlannerAgent."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    tasks: list[ImplementationTask] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)

    produced_by: str
    produced_at: str
    version: int = 1

    @model_validator(mode="after")
    def _check_execution_order_consistency(self) -> "TaskPlan":
        """
        Req 5 crit 7 (Technical Design Section 5.5, item 1):
        every task_id in `tasks` must appear exactly once in
        `execution_order`, and no task may precede any task in its
        own `dependencies` list.
        """
        task_ids = [t.task_id for t in self.tasks]
        task_id_set = set(task_ids)

        if len(task_ids) != len(task_id_set):
            raise ValueError("TaskPlan.tasks contains duplicate task_id values")

        if set(self.execution_order) != task_id_set:
            raise ValueError(
                "TaskPlan.execution_order must contain exactly the same "
                "task_id values as TaskPlan.tasks, each exactly once"
            )

        if len(self.execution_order) != len(task_id_set):
            raise ValueError(
                "TaskPlan.execution_order must not contain duplicate task_id values"
            )

        position = {tid: idx for idx, tid in enumerate(self.execution_order)}
        tasks_by_id = {t.task_id: t for t in self.tasks}

        for tid in self.execution_order:
            task = tasks_by_id[tid]
            for dep in task.dependencies:
                if dep not in position:
                    raise ValueError(
                        f"Task '{tid}' depends on unknown task_id '{dep}'"
                    )
                if position[dep] >= position[tid]:
                    raise ValueError(
                        f"Task '{tid}' is ordered before its dependency '{dep}' "
                        "in execution_order"
                    )

        return self


# ---------------------------------------------------------------------------
# Req 6 / design.md Section 1.4 (BackendAgent / FrontendAgent output)
# ---------------------------------------------------------------------------


class CodeArtifact(BaseModel):
    """A single code file artifact."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    file_path: str
    language: str
    content: str
    artifact_type: Literal[
        "backend", "frontend", "test", "config", "schema", "migration", "other"
    ]

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 7 / design.md Section 1.5 (QAAgent output)
# ---------------------------------------------------------------------------


class TestArtifact(BaseModel):
    """A single test file artifact."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    file_path: str
    content: str
    test_type: Literal["unit", "integration", "regression", "acceptance", "e2e"]
    coverage_target: str = ""

    produced_by: str
    produced_at: str
    version: int = 1


class TestPlan(BaseModel):
    """Structured test plan/strategy output produced by QAAgent."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    strategy: str
    coverage_goals: dict[str, str] = Field(default_factory=dict)
    test_suites: list[str] = Field(default_factory=list)
    quality_metrics: dict[str, Any] = Field(default_factory=dict)

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 8 / design.md Section 1.6 (ReviewerAgent output)
# ---------------------------------------------------------------------------


class ReviewReport(BaseModel):
    """Structured review output produced by ReviewerAgent."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    overall_status: Literal["approved", "requires_changes", "rejected"]
    code_quality_score: float = Field(ge=0.0, le=10.0)
    architecture_compliance: bool
    engineering_standards_compliance: bool
    security_findings: list[str] = Field(default_factory=list)
    performance_findings: list[str] = Field(default_factory=list)
    maintainability_findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    testing_coverage_assessment: str = ""

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 9 / design.md Section 1.8 (DevOpsAgent output)
# ---------------------------------------------------------------------------


class DeploymentArtifact(BaseModel):
    """A single deployment-related file artifact."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    file_path: str
    content: str
    artifact_type: Literal[
        "dockerfile",
        "docker_compose",
        "ci_cd_pipeline",
        "infrastructure_template",
        "deployment_script",
        "monitoring_config",
        "other",
    ]

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 10 / design.md Section 1.9 (DocumentationAgent output)
# ---------------------------------------------------------------------------


class DocumentationArtifact(BaseModel):
    """A single documentation file artifact."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    file_path: str
    content: str
    doc_type: Literal[
        "readme",
        "onboarding",
        "architecture",
        "api_reference",
        "deployment_guide",
        "maintenance",
        "other",
    ]

    produced_by: str
    produced_at: str
    version: int = 1


# ---------------------------------------------------------------------------
# Req 12 / design.md Section 2 (error tracking)
# ---------------------------------------------------------------------------


class ErrorRecord(BaseModel):
    """A single recorded pipeline error."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    error_id: str
    agent: str
    stage: str
    error_type: Literal[
        "unhandled_exception", "validation_failure", "timeout", "retry_exhausted", "other"
    ]
    message: str
    traceback: Optional[str] = None
    occurred_at: str
    resolved: bool = False
    resolution_agent: Optional[str] = None
    resolution_notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Req 14 / design.md Section 2 (per-agent execution metadata)
# ---------------------------------------------------------------------------


class AgentExecutionMetadata(BaseModel):
    """Execution metadata for a single agent invocation."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    agent_name: str
    started_at: str
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    tokens_used: int = 0
    model_calls: int = 0
    status: Literal["running", "completed", "failed", "skipped"]
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Req 16 / design.md Section 2 (memory subsystem references)
# ---------------------------------------------------------------------------


class MemoryReferences(BaseModel):
    """Deduplicated cross-references into the five memory stores."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    long_term: list[str] = Field(default_factory=list)
    vector: list[str] = Field(default_factory=list)
    semantic: list[str] = Field(default_factory=list)
    episodic: list[str] = Field(default_factory=list)
    organizational: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Req 20 / design.md Section 1.11 (FinalProjectAgent output)
# ---------------------------------------------------------------------------


class FinalProjectOutput(BaseModel):
    """Final pipeline output produced by FinalProjectAgent."""

    model_config = ConfigDict(frozen=False, validate_assignment=True)

    project_name: str
    summary: str
    total_files_generated: int
    code_paths: list[str] = Field(default_factory=list)
    test_paths: list[str] = Field(default_factory=list)
    deployment_paths: list[str] = Field(default_factory=list)
    documentation_paths: list[str] = Field(default_factory=list)
    overall_review_status: Literal[
        "approved", "requires_changes", "rejected", "not_reviewed"
    ]
    pipeline_duration_seconds: float

    produced_by: str
    produced_at: str

    @model_validator(mode="after")
    def _check_total_files_generated(self) -> "FinalProjectOutput":
        """
        Req 20 crit 2 (Technical Design Section 5.5, item 2):
        total_files_generated must equal the sum of the four
        *_paths list lengths.
        """
        expected = (
            len(self.code_paths)
            + len(self.test_paths)
            + len(self.deployment_paths)
            + len(self.documentation_paths)
        )
        if self.total_files_generated != expected:
            raise ValueError(
                "total_files_generated "
                f"({self.total_files_generated}) does not match the sum of "
                f"code_paths + test_paths + deployment_paths + "
                f"documentation_paths ({expected})"
            )
        return self


__all__ = [
    "ProductRequirements",
    "SystemArchitecture",
    "ImplementationTask",
    "TaskPlan",
    "CodeArtifact",
    "TestArtifact",
    "TestPlan",
    "ReviewReport",
    "DeploymentArtifact",
    "DocumentationArtifact",
    "ErrorRecord",
    "AgentExecutionMetadata",
    "MemoryReferences",
    "FinalProjectOutput",
]