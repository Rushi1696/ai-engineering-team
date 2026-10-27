"""
agents/architect_agent.py

ArchitectAgent: Concrete pipeline agent that translates ProductRequirements
into a structured, versioned SystemArchitecture specification.

Source of truth:
  - design.md Section 1.2 (ArchitectAgent contract)
  - design.md Section 2 (State Ownership Matrix)
  - design.md Section 6.1 (Folder ownership: agents/architect_agent.py)
  - requirements.md Requirement 1 (Run Metadata), Requirement 4 (SystemArchitecture model),
    Requirement 12 (Error Tracking), Requirement 13 (Retry and Escalation Tracking),
    Requirement 14 (Metadata), Requirement 21 criterion 6 (forward_stage idempotency),
    Requirement 21 criterion 7 (total_invocations).
  - workflow-design-revised.md Section 3.0, 3.3, 3.9, 3.10.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional, Union
import uuid

from agents.base_agent import DEFAULT_MAX_RETRIES, BaseAgent
from agents.product_manager_agent import strip_metadata_header
from state.engineering_state import EngineeringState
from state.models import AgentExecutionMetadata, ErrorRecord, ProductRequirements, SystemArchitecture
from tools.shared.llm_client import LLMClient
from workflows.constants import STATUS_ARCHITECTURE_COMPLETE

_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "architect"


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class ArchitectAgent(BaseAgent):
    """ArchitectAgent translates ProductRequirements into a structured SystemArchitecture artifact.

    Inherits from BaseAgent and implements execute() with an internal retry loop
    for validation errors and unhandled exceptions.
    """

    def __init__(
        self,
        model_name: str,
        llm_client: LLMClient,
        max_retries: int = DEFAULT_MAX_RETRIES,
        sleep_fn: Callable[[float], None] = time.sleep,
        prompts_dir: Optional[Union[Path, str]] = None,
    ) -> None:
        """
        Args:
            model_name: Model identifier (e.g. from configs/agents.yaml).
            llm_client: Structured LLM client implementation conforming to LLMClient Protocol.
            max_retries: Effective retry ceiling passed directly to BaseAgent constructor.
            sleep_fn: Injectable sleep function for backoff timing (defaults to time.sleep).
            prompts_dir: Directory containing prompt templates (defaults to prompts/architect).
        """
        if not model_name or not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("ArchitectAgent: model_name must be a non-empty string")
        if llm_client is None:
            raise ValueError("ArchitectAgent: llm_client must not be None")

        super().__init__(agent_name="ArchitectAgent", max_retries=max_retries)
        self.model_name = model_name
        self.llm_client = llm_client
        self.sleep_fn = sleep_fn
        self.prompts_dir = Path(prompts_dir) if prompts_dir else _DEFAULT_PROMPTS_DIR

    def _load_prompt(self, filename: str) -> str:
        """Read a prompt file and strip its metadata header block."""
        filepath = self.prompts_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(f"Prompt template file not found: {filepath}")
        raw_text = filepath.read_text(encoding="utf-8")
        return strip_metadata_header(raw_text)

    def _format_requirements(self, req: ProductRequirements) -> str:
        """Format ProductRequirements into human-readable text for prompt interpolation."""
        sections = []
        if req.functional_requirements:
            sections.append("Functional Requirements:\n" + "\n".join(f"- {r}" for r in req.functional_requirements))
        if req.non_functional_requirements:
            sections.append("Non-Functional Requirements:\n" + "\n".join(f"- {r}" for r in req.non_functional_requirements))
        if req.user_stories:
            sections.append("User Stories:\n" + "\n".join(f"- {r}" for r in req.user_stories))
        if req.acceptance_criteria:
            sections.append("Acceptance Criteria:\n" + "\n".join(f"- {r}" for r in req.acceptance_criteria))
        if req.constraints:
            sections.append("Constraints:\n" + "\n".join(f"- {r}" for r in req.constraints))
        if req.business_goals:
            sections.append("Business Goals:\n" + "\n".join(f"- {r}" for r in req.business_goals))
        return "\n\n".join(sections)

    def _validate_architecture(self, arch: SystemArchitecture) -> None:
        """Validate all required fields of SystemArchitecture (Req 4 criteria 2 & 6, design.md §1.2)."""
        if not arch.overview or not isinstance(arch.overview, str) or not arch.overview.strip():
            raise ValueError("SystemArchitecture.overview must be a non-empty string.")

        if not arch.scalability_strategy or not isinstance(arch.scalability_strategy, str) or not arch.scalability_strategy.strip():
            raise ValueError("SystemArchitecture.scalability_strategy must be a non-empty string.")

        if not arch.deployment_model or not isinstance(arch.deployment_model, str) or not arch.deployment_model.strip():
            raise ValueError("SystemArchitecture.deployment_model must be a non-empty string.")

        if not arch.technology_stack or not isinstance(arch.technology_stack, dict) or len(arch.technology_stack) == 0:
            raise ValueError("SystemArchitecture.technology_stack must be a non-empty dict.")

        for comp, tech in arch.technology_stack.items():
            if not isinstance(comp, str) or not comp.strip() or not isinstance(tech, str) or not tech.strip():
                raise ValueError("SystemArchitecture.technology_stack entries must have non-empty string keys and values.")

        if not isinstance(arch.api_specifications, list):
            raise ValueError("SystemArchitecture.api_specifications must be a list.")

        for idx, spec in enumerate(arch.api_specifications):
            if not isinstance(spec, dict):
                raise ValueError(f"SystemArchitecture.api_specifications[{idx}] must be a dict.")
            if "endpoint" not in spec or not isinstance(spec["endpoint"], str) or not spec["endpoint"].strip():
                raise ValueError(f"SystemArchitecture.api_specifications[{idx}] missing non-empty 'endpoint' string.")
            if "method" not in spec or not isinstance(spec["method"], str) or not spec["method"].strip():
                raise ValueError(f"SystemArchitecture.api_specifications[{idx}] missing non-empty 'method' string.")

        if not isinstance(arch.database_design, dict):
            raise ValueError("SystemArchitecture.database_design must be a dict.")

        if not isinstance(arch.security_architecture, dict):
            raise ValueError("SystemArchitecture.security_architecture must be a dict.")

    def execute(self, state: EngineeringState) -> Dict[str, Any]:
        """Execute architecture generation with internal retry loop.

        Per design.md Section 1.2 & workflow-design-revised.md Section 3.10:
          - Reads `requirements` from state. If missing or invalid, immediately returns an exhaustion
            update with human_approval_required=True without LLM calls or backoff.
          - Runs attempts up to effective_max with prompt refinement on validation failure and
            exponential backoff (2s, 4s, 8s, ...) on unhandled exceptions.
          - On success, returns SystemArchitecture, increments forward_stage to 2 idempotently
            (Req 21 crit 6), resets retry_counts to 0 (Req 13 crit 7), and sets
            workflow_status='architecture_complete', next_agent='TaskPlannerAgent'.
          - On exhaustion, returns an update with human_approval_required=True, an ErrorRecord
            with error_type='retry_exhausted', next_agent='ArchitectAgent', retry_counts at
            effective_max, custom execution_metadata with status='failed', and workflow_status
            unchanged.
        """
        started_at = _now_iso()
        requirements_raw = state.get("requirements")
        user_context = state.get("user_context") or {}
        project_name = state.get("project_name", "")

        existing_max_retries = state.get("max_retries") or {}
        effective_max = existing_max_retries.get(self.agent_name, self.max_retries)

        existing_retry_counts = state.get("retry_counts") or {}
        start_retry_count = existing_retry_counts.get(self.agent_name, 0)

        # 1. Fast fail on missing or empty input
        if requirements_raw is None:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason="ProductRequirements are missing from state",
                effective_max=effective_max,
                retry_count=max(start_retry_count, effective_max),
            )

        requirements: Optional[ProductRequirements] = None
        if isinstance(requirements_raw, ProductRequirements):
            requirements = requirements_raw
        elif isinstance(requirements_raw, dict):
            try:
                requirements = ProductRequirements(**requirements_raw)
            except Exception as exc:
                return self._build_exhaustion_return(
                    state=state,
                    started_at=started_at,
                    reason=f"ProductRequirements in state failed validation: {exc}",
                    effective_max=effective_max,
                    retry_count=max(start_retry_count, effective_max),
                )
        else:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason=f"ProductRequirements in state has unexpected type: {type(requirements_raw).__name__}",
                effective_max=effective_max,
                retry_count=max(start_retry_count, effective_max),
            )

        if not requirements.functional_requirements or not any(
            isinstance(item, str) and item.strip() for item in requirements.functional_requirements
        ):
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason="ProductRequirements contain no valid functional requirements",
                effective_max=effective_max,
                retry_count=max(start_retry_count, effective_max),
            )

        # 2. Fast fail if retry limit already reached prior to execution
        if start_retry_count >= effective_max:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason=(
                    f"Retry limit already reached ({start_retry_count}/{effective_max}) "
                    f"for {self.agent_name} before execution."
                ),
                effective_max=effective_max,
                retry_count=start_retry_count,
            )

        max_attempts = effective_max - start_retry_count

        # Load prompt templates
        system_prompt = self._load_prompt("system_prompt.md")
        user_template = self._load_prompt("user_prompt_template.md")
        revision_template = self._load_prompt("revision_prompt.md")

        last_error: Optional[Exception] = None
        last_error_type: str = "unhandled_exception"
        current_retry_count = start_retry_count
        requirements_formatted = self._format_requirements(requirements)

        # 3. Internal retry loop bounded by remaining attempts
        for attempt in range(1, max_attempts + 1):
            current_retry_count += 1
            if attempt == 1:
                prompt = user_template.format(
                    project_name=project_name,
                    requirements=requirements_formatted,
                    user_context=user_context,
                )
            else:
                prompt = revision_template.format(
                    project_name=project_name,
                    requirements=requirements_formatted,
                    user_context=user_context,
                    error_message=f"{last_error_type}: {last_error}",
                )

            try:
                arch_obj = self.llm_client.generate_structured(
                    prompt=prompt,
                    response_model=SystemArchitecture,
                    system_prompt=system_prompt,
                    model_name=self.model_name,
                )
                self._validate_architecture(arch_obj)
            except Exception as exc:
                last_error = exc
                is_validation = isinstance(exc, ValueError)
                last_error_type = (
                    "validation_failure" if is_validation else "unhandled_exception"
                )

                if attempt < max_attempts:
                    if not is_validation:
                        delay = 2.0 * (2 ** (attempt - 1))
                        self.sleep_fn(delay)
                    continue
                break
            else:
                # 4. Success
                return self._build_success_return(
                    state=state,
                    started_at=started_at,
                    architecture=arch_obj,
                )

        # 5. Exhaustion return after max_attempts failed attempts
        return self._build_exhaustion_return(
            state=state,
            started_at=started_at,
            reason=(
                f"Exhausted {max_attempts} attempts without valid architecture. "
                f"Last error ({last_error_type}): {last_error}"
            ),
            effective_max=effective_max,
            retry_count=max(current_retry_count, effective_max),
        )

    def _build_success_return(
        self,
        state: EngineeringState,
        started_at: str,
        architecture: SystemArchitecture,
    ) -> Dict[str, Any]:
        """Assemble partial state update on successful architecture generation."""
        completed_at = _now_iso()

        # Versioning: increment version if architecture already exists in state (Req 4 crit 3)
        existing_arch = state.get("architecture")
        if existing_arch is not None:
            existing_version = getattr(existing_arch, "version", None)
            if existing_version is None and isinstance(existing_arch, dict):
                existing_version = existing_arch.get("version", 1)
            architecture.version = (existing_version or 1) + 1
        else:
            architecture.version = 1

        architecture.produced_by = self.agent_name
        architecture.produced_at = completed_at

        # Forward stage: advance to stage 2 on first-pass forward pipeline progress (Req 21 crit 6)
        current_forward_stage = state.get("forward_stage", 0)
        new_forward_stage = max(current_forward_stage, 2)

        project_name = state.get("project_name", "")
        scratchpad_entry = (
            f"SystemArchitecture v{architecture.version} generated successfully "
            f"for project '{project_name}'. Overview: {architecture.overview[:80]}..."
        )

        updated_retry_counts = dict(state.get("retry_counts") or {})
        updated_retry_counts[self.agent_name] = 0

        return {
            "architecture": architecture,
            "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
            "next_agent": "TaskPlannerAgent",
            "current_agent": self.agent_name,
            "forward_stage": new_forward_stage,
            "retry_counts": updated_retry_counts,  # Reset to 0 on success (Req 13 crit 7)
            "checkpoint_stage": "architecture_complete",
            "checkpoints": {"architecture_complete": completed_at},
            "agent_scratchpad": {self.agent_name: scratchpad_entry},
            "state_version": state.get("state_version", 0) + 1,
            "updated_at": completed_at,
        }

    def _build_exhaustion_return(
        self,
        state: EngineeringState,
        started_at: str,
        reason: str,
        effective_max: int,
        retry_count: int,
    ) -> Dict[str, Any]:
        """Assemble partial update on retry exhaustion requiring human approval.

        Preserves workflow_status (does not set 'failed' so human resume can continue),
        sets human_approval_required=True, and overrides execution_metadata with status='failed'.
        """
        completed_at = _now_iso()
        error_record = ErrorRecord(
            error_id=str(uuid.uuid4()),
            agent=self.agent_name,
            stage=state.get("workflow_status", ""),
            error_type="retry_exhausted",
            message=reason,
            traceback="",
            occurred_at=completed_at,
            resolved=False,
            resolution_agent=None,
            resolution_notes=None,
        )

        duration = (
            datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)
        ).total_seconds()
        failed_metadata = AgentExecutionMetadata(
            agent_name=self.agent_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration,
            tokens_used=0,
            model_calls=0,
            status="failed",
            notes=reason,
        )

        approval_notes = (
            f"{self.agent_name} failed after reaching max retries ({effective_max}). "
            f"Reason: {reason}. Human approval is required to continue."
        )

        updated_retry_counts = dict(state.get("retry_counts") or {})
        updated_retry_counts[self.agent_name] = retry_count

        return {
            "current_agent": self.agent_name,
            "next_agent": self.agent_name,
            "errors": [error_record],
            "retry_counts": updated_retry_counts,
            "human_approval_required": True,
            "human_approval_notes": approval_notes,
            "execution_metadata": {self.agent_name: failed_metadata},
            "updated_at": completed_at,
        }


__all__ = ["ArchitectAgent"]
