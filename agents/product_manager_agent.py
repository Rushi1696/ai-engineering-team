"""
agents/product_manager_agent.py

ProductManagerAgent: Concrete pipeline agent that translates raw user input and
context into a structured ProductRequirements specification.

Source of truth:
  - design.md Section 1.1 (ProductManagerAgent contract)
  - design.md Section 2 (State Ownership Matrix)
  - design.md Section 6.1 (Folder ownership: agents/product_manager_agent.py)
  - requirements.md Requirement 1 (Run Metadata), Requirement 2 (User Input),
    Requirement 3 (ProductRequirements model), Requirement 12 (Error Tracking),
    Requirement 13 (Retry and Escalation Tracking), Requirement 14 (Metadata),
    Requirement 21 criterion 6 (forward_stage idempotency), Requirement 21
    criterion 7 (total_invocations).
  - workflow-design-revised.md Section 3.0, 3.4, 3.9, 3.10.2.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any, Callable, Dict, Optional, Union
import uuid

from agents.base_agent import DEFAULT_MAX_RETRIES, BaseAgent
from state.engineering_state import EngineeringState
from state.models import AgentExecutionMetadata, ErrorRecord, ProductRequirements
from tools.shared.llm_client import LLMClient
from workflows.constants import STATUS_REQUIREMENTS_COMPLETE

_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "product_manager"


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def strip_metadata_header(text: str) -> str:
    """Strip YAML frontmatter (--- ... ---) or HTML comment (<!-- ... -->) header block.

    Ensures prompt version and author metadata never reach the LLM inference call.
    """
    cleaned = text.lstrip()
    if cleaned.startswith("---"):
        match = re.match(r"^---\s*\n.*?\n---\s*\n?", cleaned, flags=re.DOTALL)
        if match:
            return cleaned[match.end():].lstrip()
    elif cleaned.startswith("<!--"):
        match = re.match(r"^<!--\s*\n.*?\n-->\s*\n?", cleaned, flags=re.DOTALL)
        if match:
            return cleaned[match.end():].lstrip()
    return cleaned


class ProductManagerAgent(BaseAgent):
    """ProductManagerAgent translates user_input into a structured ProductRequirements artifact.

    Inherits from BaseAgent and implements execute() with an internal 3-attempt retry loop
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
            prompts_dir: Directory containing prompt templates (defaults to prompts/product_manager).
        """
        super().__init__(agent_name="ProductManagerAgent", max_retries=max_retries)
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

    def _validate_requirements(self, req: ProductRequirements) -> None:
        """Validate all six requirement list fields contain at least one non-empty string entry (Req 3 crit 6)."""
        list_fields = [
            "functional_requirements",
            "non_functional_requirements",
            "user_stories",
            "acceptance_criteria",
            "constraints",
            "business_goals",
        ]
        for field in list_fields:
            items = getattr(req, field, None)
            if not items or not isinstance(items, list):
                raise ValueError(f"ProductRequirements.{field} must be a non-empty list.")
            if not any(isinstance(item, str) and item.strip() for item in items):
                raise ValueError(
                    f"ProductRequirements.{field} must contain at least one non-empty string entry."
                )

    def execute(self, state: EngineeringState) -> Dict[str, Any]:
        """Execute requirements generation with internal 3-attempt retry loop.

        Per design.md Section 1.1 & workflow-design-revised.md Section 3.10:
          - Validates user_input: if empty or whitespace-only, immediately returns an exhaustion
            update with human_approval_required=True without LLM calls or backoff.
          - Runs up to 3 attempts with prompt refinement on validation failure and exponential
            backoff (2s, 4s, 8s) on unhandled exceptions.
          - On success, returns ProductRequirements, increments forward_stage idempotently
            (Req 21 crit 6), resets retry_counts to 0 (Req 13 crit 7), and sets
            workflow_status='requirements_complete', next_agent='ArchitectAgent'.
          - On exhaustion, returns an update with human_approval_required=True, an ErrorRecord
            with error_type='retry_exhausted', next_agent='ProductManagerAgent', retry_counts at
            effective_max, custom execution_metadata with status='failed', and workflow_status
            unchanged.
        """
        started_at = _now_iso()
        user_input = state.get("user_input")
        user_context = state.get("user_context") or {}
        project_name = state.get("project_name", "")

        existing_max_retries = state.get("max_retries") or {}
        effective_max = existing_max_retries.get(self.agent_name, self.max_retries)

        # 1. Fast fail on empty or whitespace user_input (no LLM call, no backoff)
        if not user_input or not isinstance(user_input, str) or not user_input.strip():
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason="user_input is empty or whitespace-only",
                effective_max=effective_max,
                retry_count=effective_max,
            )

        # Load prompt templates
        system_prompt = self._load_prompt("system_prompt.md")
        user_template = self._load_prompt("user_prompt_template.md")
        revision_template = self._load_prompt("revision_prompt.md")

        backoff_delays = [2.0, 4.0, 8.0]
        last_error: Optional[Exception] = None
        last_error_type: str = "unhandled_exception"

        existing_retry_counts = state.get("retry_counts") or {}
        current_retry_count = existing_retry_counts.get(self.agent_name, 0)

        # 2. Internal 3-attempt loop
        for attempt in range(1, 4):
            current_retry_count += 1
            if attempt == 1:
                prompt = user_template.format(
                    project_name=project_name,
                    user_input=user_input,
                    user_context=user_context,
                )
            else:
                prompt = revision_template.format(
                    project_name=project_name,
                    user_input=user_input,
                    user_context=user_context,
                    error_message=f"{last_error_type}: {last_error}",
                )

            try:
                requirements_obj = self.llm_client.generate_structured(
                    prompt=prompt,
                    response_model=ProductRequirements,
                    system_prompt=system_prompt,
                    model_name=self.model_name,
                )
                self._validate_requirements(requirements_obj)
            except Exception as exc:
                last_error = exc
                is_validation = isinstance(exc, ValueError)
                last_error_type = (
                    "validation_failure" if is_validation else "unhandled_exception"
                )

                if attempt < 3:
                    if not is_validation:
                        delay = backoff_delays[attempt - 1]
                        self.sleep_fn(delay)
                    continue
                break
            else:
                # 3. Success
                return self._build_success_return(
                    state=state,
                    started_at=started_at,
                    requirements=requirements_obj,
                )

        # 4. Exhaustion return after 3 failed attempts
        return self._build_exhaustion_return(
            state=state,
            started_at=started_at,
            reason=f"Exhausted 3 attempts without valid requirements. Last error ({last_error_type}): {last_error}",
            effective_max=effective_max,
            retry_count=max(current_retry_count, effective_max),
        )

    def _build_success_return(
        self,
        state: EngineeringState,
        started_at: str,
        requirements: ProductRequirements,
    ) -> Dict[str, Any]:
        """Assemble partial state update on successful requirements generation."""
        completed_at = _now_iso()

        # Versioning: increment version if requirements already exist (Req 3 crit 3)
        existing_req = state.get("requirements")
        if existing_req is not None:
            existing_version = getattr(existing_req, "version", None)
            if existing_version is None and isinstance(existing_req, dict):
                existing_version = existing_req.get("version", 1)
            requirements.version = (existing_version or 1) + 1
        else:
            requirements.version = 1

        requirements.produced_by = self.agent_name
        requirements.produced_at = completed_at

        # Forward stage: increment by 1 only on first-pass forward pipeline progress (Req 21 crit 6)
        current_forward_stage = state.get("forward_stage", 0)
        new_forward_stage = max(current_forward_stage, 1)

        project_name = state.get("project_name", "")
        scratchpad_entry = (
            f"ProductRequirements v{requirements.version} generated successfully "
            f"for project '{project_name}'."
        )

        return {
            "requirements": requirements,
            "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
            "next_agent": "ArchitectAgent",
            "current_agent": self.agent_name,
            "forward_stage": new_forward_stage,
            "retry_counts": {self.agent_name: 0},  # Reset to 0 on success (Req 13 crit 7)
            "checkpoint_stage": "requirements_complete",
            "checkpoints": {"requirements_complete": completed_at},
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

        return {
            "current_agent": self.agent_name,
            "next_agent": self.agent_name,
            "errors": [error_record],
            "retry_counts": {self.agent_name: retry_count},
            "human_approval_required": True,
            "human_approval_notes": approval_notes,
            "execution_metadata": {self.agent_name: failed_metadata},
            "updated_at": completed_at,
        }


__all__ = ["ProductManagerAgent", "strip_metadata_header"]
