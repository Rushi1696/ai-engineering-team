"""
agents/final_project_agent.py

FinalProjectAgent: The eleventh and final pipeline agent, consolidating all deliverables,
packaging project artifacts, verifying file enumeration, and producing FinalProjectOutput.

Source of truth:
  - design.md Section 1.11 (FinalProjectAgent Contract)
  - design.md Section 4.11 (FinalProjectAgent Tools table)
  - design.md Section 6.1 (Folder ownership)
  - workflows/graph.py (final_project -> END edge)
  - requirements.md Requirement 11 (FinalProjectOutput), Requirement 13 (Retry & Escalation),
    Requirement 20 (File Count Invariant), Requirement 21 (Workflow Control)
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from agents.base_agent import BaseAgent, DEFAULT_MAX_RETRIES, strip_metadata_header
from state.engineering_state import EngineeringState
from state.models import (
    AgentExecutionMetadata,
    ErrorRecord,
    FinalProjectOutput,
)
from state.validators import WORKFLOW_STATUS_VALUES
from tools.final_project.packager import PackageReport, ProjectPackager, validate_safe_path
from tools.shared.llm_client import LLMClient

# Local status constants (guarded to ensure membership in WORKFLOW_STATUS_VALUES)
STATUS_COMPLETED: str = "completed"
if STATUS_COMPLETED not in WORKFLOW_STATUS_VALUES:
    raise ImportError("Local status constant is not a valid workflow status")

_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "final_project"


def _now_iso() -> str:
    """Return the current UTC timestamp formatted as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class FinalProjectSummary(BaseModel):
    """Draft executive summary produced by LLM prior to agent metadata stamping."""

    summary: str = Field(description="Comprehensive executive summary of the completed engineering project")


class FinalProjectAgent(BaseAgent):
    """Concrete agent that packages project deliverables and consolidates FinalProjectOutput."""

    def __init__(
        self,
        model_name: str,
        llm_client: LLMClient,
        packager: ProjectPackager,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep_fn: Callable[[float], None] = time.sleep,
        prompts_dir: Optional[Union[Path, str]] = None,
    ) -> None:
        """
        Args:
            model_name: Model identifier (e.g. from configs/agents.yaml).
            llm_client: Structured LLM client conforming to LLMClient Protocol.
            packager: Required packaging and export tool (Protocol).
            max_retries: Default retry ceiling passed to BaseAgent constructor.
            sleep_fn: Injectable sleep function for backoff timing.
            prompts_dir: Directory containing prompt templates (defaults to prompts/final_project).
        """
        if not model_name or not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("FinalProjectAgent: model_name must be a non-empty string")
        if llm_client is None:
            raise ValueError("FinalProjectAgent: llm_client must not be None")
        if packager is None:
            raise ValueError("FinalProjectAgent: packager must not be None")

        super().__init__(agent_name="FinalProjectAgent", max_retries=max_retries)
        self.model_name = model_name
        self.llm_client = llm_client
        self.packager: ProjectPackager = packager
        self.sleep_fn = sleep_fn
        self.prompts_dir = Path(prompts_dir) if prompts_dir else _DEFAULT_PROMPTS_DIR

    def _load_prompt(self, filename: str) -> str:
        """Read a prompt file and strip its metadata header block."""
        filepath = self.prompts_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Prompt template file not found: {filepath}")
        raw_text = filepath.read_text(encoding="utf-8")
        return strip_metadata_header(raw_text)

    def _get_effective_max_retries(self, state: EngineeringState) -> int:
        """Resolve effective max retries ceiling for FinalProjectAgent."""
        max_retries_map = state.get("max_retries") or {}
        limit = max_retries_map.get(self.agent_name, self.max_retries)
        try:
            val = int(limit)
            return val if val > 0 else DEFAULT_MAX_RETRIES
        except (TypeError, ValueError):
            return DEFAULT_MAX_RETRIES

    def execute(self, state: EngineeringState) -> Dict[str, Any]:
        """Package deliverables, generate executive summary, and write final_output."""
        started_at = _now_iso()
        effective_max = self._get_effective_max_retries(state)
        current_retries = (state.get("retry_counts") or {}).get(self.agent_name, 0)

        # 1. Fast-fail if retry limit already reached prior to execution
        if current_retries >= effective_max:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                error_type="retry_exhausted",
                error_message=(
                    f"Retry limit already reached ({current_retries}/{effective_max}) "
                    f"for {self.agent_name} before execution."
                ),
                traceback_str="",
                attempt_count=0,
            )

        # 2. Extract artifact collections and enumerate file paths
        project_name = state.get("project_name") or "Autonomous Engineering Project"
        code_artifacts = state.get("code_artifacts") or {}
        test_artifacts = state.get("test_artifacts") or {}
        deployment_artifacts = state.get("deployment_artifacts") or {}
        documentation_artifacts = state.get("documentation_artifacts") or {}

        code_paths = sorted(list(code_artifacts.keys()))
        test_paths = sorted(list(test_artifacts.keys()))
        deployment_paths = sorted(list(deployment_artifacts.keys()))
        documentation_paths = sorted(list(documentation_artifacts.keys()))
        total_files = len(code_paths) + len(test_paths) + len(deployment_paths) + len(documentation_paths)

        # 3. Path safety check & cross-category duplicate detection OUTSIDE LLM loop
        # Validate path safety on every artifact path before packaging
        category_map = [
            ("code_artifacts", code_artifacts),
            ("test_artifacts", test_artifacts),
            ("deployment_artifacts", deployment_artifacts),
            ("documentation_artifacts", documentation_artifacts),
        ]
        for cat_name, artifact_dict in category_map:
            for path_key in artifact_dict.keys():
                try:
                    validate_safe_path(path_key)
                except Exception as exc:
                    return self._build_exhaustion_return(
                        state=state,
                        started_at=started_at,
                        error_type="validation_failure",
                        error_message=f"Insecure or invalid artifact path '{path_key}' in {cat_name}: {exc}",
                        traceback_str="",
                        attempt_count=0,
                    )

        # Detect paths duplicated across categories
        seen_paths: Dict[str, str] = {}
        duplicates: List[str] = []
        for cat_name, artifact_dict in category_map:
            for path_key in sorted(artifact_dict.keys()):
                if path_key in seen_paths:
                    duplicates.append(f"'{path_key}' found in both {seen_paths[path_key]} and {cat_name}")
                else:
                    seen_paths[path_key] = cat_name

        if duplicates:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                error_type="validation_failure",
                error_message=f"Duplicate artifact paths detected across categories: {'; '.join(duplicates)}",
                traceback_str="",
                attempt_count=0,
            )

        # Project packaging tool execution OUTSIDE LLM loop
        # A tool exception goes straight to exhaustion without LLM regeneration
        all_artifacts: Dict[str, Any] = {}
        all_artifacts.update(code_artifacts)
        all_artifacts.update(test_artifacts)
        all_artifacts.update(deployment_artifacts)
        all_artifacts.update(documentation_artifacts)

        try:
            package_report: PackageReport = self.packager.package_project(
                project_name=project_name,
                artifacts=all_artifacts,
                output_dir="dist",
            )
        except Exception as tool_exc:
            import traceback

            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                error_type="unhandled_exception",
                error_message=f"Project packager tool exception: {tool_exc}",
                traceback_str=traceback.format_exc(),
                attempt_count=0,
            )

        if not package_report.success:
            err_details = "; ".join(package_report.errors or ["Project packaging failed"])
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                error_type="validation_failure",
                error_message=f"Project packaging failed: {err_details}",
                traceback_str="",
                attempt_count=0,
            )

        # 4. Determine overall review status
        review_report = state.get("review_report")
        overall_review_status = "not_reviewed"
        if review_report is not None:
            if isinstance(review_report, dict):
                r_status = review_report.get("overall_status")
            else:
                r_status = getattr(review_report, "overall_status", None)
            if r_status in {"approved", "requires_changes", "rejected", "not_reviewed"}:
                overall_review_status = r_status

        # 5. Build prompt context
        requirements = state.get("requirements")
        reqs_str = (
            str(requirements.model_dump())
            if hasattr(requirements, "model_dump")
            else str(requirements)
            if requirements
            else "None"
        )
        architecture = state.get("architecture")
        arch_str = (
            str(architecture.model_dump())
            if hasattr(architecture, "model_dump")
            else str(architecture)
            if architecture
            else "None"
        )

        code_summary = "\n".join(f"- {p}" for p in code_paths) or "None"
        test_summary = "\n".join(f"- {p}" for p in test_paths) or "None"
        deploy_summary = "\n".join(f"- {p}" for p in deployment_paths) or "None"
        doc_summary = "\n".join(f"- {p}" for p in documentation_paths) or "None"

        system_prompt = self._load_prompt("system_prompt.md")
        user_prompt_template = self._load_prompt("user_prompt_template.md")
        revision_prompt_template = self._load_prompt("revision_prompt.md")

        initial_user_prompt = user_prompt_template.format(
            project_name=project_name,
            requirements=reqs_str,
            architecture=arch_str,
            total_code_files=len(code_paths),
            code_summary=code_summary,
            total_test_files=len(test_paths),
            test_summary=test_summary,
            total_deploy_files=len(deployment_paths),
            deployment_summary=deploy_summary,
            total_doc_files=len(documentation_paths),
            documentation_summary=doc_summary,
            overall_review_status=overall_review_status,
            total_files_generated=total_files,
        )

        remaining_attempts = max(1, effective_max - current_retries)
        last_error_message = ""
        last_error_type = "validation_failure"
        last_traceback = ""
        attempt = 0
        summary_output: Optional[FinalProjectSummary] = None

        # 6. LLM retry loop for summary generation
        while attempt < remaining_attempts:
            if attempt == 0:
                current_prompt = initial_user_prompt
            else:
                current_prompt = revision_prompt_template.format(
                    error_message=last_error_message,
                    project_name=project_name,
                    architecture=arch_str,
                    total_files_generated=total_files,
                )

            try:
                candidate: FinalProjectSummary = self.llm_client.generate_structured(
                    prompt=current_prompt,
                    system_prompt=system_prompt,
                    response_model=FinalProjectSummary,
                    model_name=self.model_name,
                )

                if not candidate.summary or not candidate.summary.strip():
                    raise ValueError("FinalProjectAgent: Generated summary cannot be empty")

                summary_output = candidate
                break

            except Exception as exc:
                attempt += 1
                last_error_message = str(exc)
                is_validation = isinstance(exc, (ValueError, TypeError))
                last_error_type = (
                    "validation_failure" if is_validation else "unhandled_exception"
                )
                import traceback

                last_traceback = traceback.format_exc()

                if attempt < remaining_attempts and not is_validation:
                    backoff_delay = 2.0 ** attempt
                    self.sleep_fn(backoff_delay)

        # 7. Handle exhaustion if summary generation fails
        if summary_output is None:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                error_type=last_error_type,
                error_message=(
                    f"Exhausted {remaining_attempts} attempts without valid project summary. "
                    f"Last error ({last_error_type}): {last_error_message}"
                ),
                traceback_str=last_traceback,
                attempt_count=attempt,
            )

        completed_at = _now_iso()
        duration = (
            datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)
        ).total_seconds()

        # Compute pipeline total duration
        pipeline_started_at = state.get("pipeline_started_at") or state.get("created_at")
        if pipeline_started_at:
            try:
                start_dt = datetime.fromisoformat(pipeline_started_at.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                pipeline_duration = max(0.0, (end_dt - start_dt).total_seconds())
            except Exception:
                pipeline_duration = duration
        else:
            pipeline_duration = duration

        # 8. Construct FinalProjectOutput (enforcing total_files_generated invariant)
        final_output = FinalProjectOutput(
            project_name=project_name,
            summary=summary_output.summary,
            total_files_generated=total_files,
            code_paths=code_paths,
            test_paths=test_paths,
            deployment_paths=deployment_paths,
            documentation_paths=documentation_paths,
            overall_review_status=overall_review_status,
            pipeline_duration_seconds=pipeline_duration,
            produced_by=self.agent_name,
            produced_at=completed_at,
        )

        current_forward_stage = state.get("forward_stage", 0)
        new_forward_stage = max(current_forward_stage, 10)
        updated_retry_counts = dict(state.get("retry_counts") or {})
        updated_retry_counts[self.agent_name] = 0

        metadata = AgentExecutionMetadata(
            agent_name=self.agent_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            tokens_used=0,
            model_calls=attempt + 1,
            status="completed",
            notes="Final project deliverables consolidated and packaged",
        )

        return {
            "final_output": final_output,
            "workflow_status": STATUS_COMPLETED,
            "current_agent": self.agent_name,
            "next_agent": None,
            "forward_stage": new_forward_stage,
            "checkpoint_stage": "completed",
            "checkpoints": {"completed": completed_at},
            "retry_counts": updated_retry_counts,
            "execution_metadata": {self.agent_name: metadata},
            "agent_scratchpad": {self.agent_name: summary_output.summary},
            "state_version": state.get("state_version", 0) + 1,
            "updated_at": completed_at,
        }

    def _build_exhaustion_return(
        self,
        state: EngineeringState,
        started_at: str,
        error_type: str,
        error_message: str,
        traceback_str: str,
        attempt_count: int,
    ) -> Dict[str, Any]:
        """Build exhaustion state update triggering HumanApprovalNode pause."""
        completed_at = _now_iso()
        effective_max = self._get_effective_max_retries(state)

        updated_retry_counts = dict(state.get("retry_counts") or {})
        updated_retry_counts[self.agent_name] = effective_max

        human_notes = (
            f"FinalProjectAgent failed after reaching max retries ({effective_max}). "
            f"Reason: {error_message}. Human approval is required to continue."
        )

        error_record = ErrorRecord(
            error_id=str(uuid.uuid4()),
            agent=self.agent_name,
            stage=str(state.get("workflow_status") or "final_project_exhaustion"),
            error_type=error_type,
            message=error_message,
            traceback=traceback_str,
            occurred_at=completed_at,
            resolved=False,
        )

        duration = (
            datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)
        ).total_seconds()

        metadata = AgentExecutionMetadata(
            agent_name=self.agent_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            tokens_used=0,
            model_calls=attempt_count,
            status="failed",
            notes=error_message,
        )

        # Invariant: workflow_status is preserved unchanged
        return {
            "current_agent": self.agent_name,
            "next_agent": self.agent_name,
            "human_approval_required": True,
            "human_approval_notes": human_notes,
            "errors": [error_record],
            "retry_counts": updated_retry_counts,
            "execution_metadata": {self.agent_name: metadata},
            "agent_scratchpad": {self.agent_name: f"Exhaustion: {error_message}"},
            "state_version": state.get("state_version", 0) + 1,
            "updated_at": completed_at,
        }

