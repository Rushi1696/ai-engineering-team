"""
agents/task_planner_agent.py

TaskPlannerAgent: Concrete pipeline agent that translates ProductRequirements
and SystemArchitecture into an ordered, dependency-resolved TaskPlan composed
of atomic ImplementationTask entries for Backend, Frontend, and QA agents.

Source of truth:
  - design.md Section 1.3 (TaskPlannerAgent contract)
  - design.md Section 2 (State Ownership Matrix)
  - design.md Section 6.1 (Folder ownership: agents/task_planner_agent.py)
  - requirements.md Requirement 1 (Run Metadata), Requirement 5 (TaskPlan model),
    Requirement 12 (Error Tracking), Requirement 13 (Retry and Escalation Tracking),
    Requirement 14 (Metadata), Requirement 21 criterion 6 (forward_stage idempotency),
    Requirement 21 criterion 7 (total_invocations).
  - workflow-design-revised.md Section 3.0, 3.3, 3.9, 3.10.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional, Set, Union
import uuid

from agents.base_agent import DEFAULT_MAX_RETRIES, BaseAgent
from agents.product_manager_agent import strip_metadata_header
from state.engineering_state import EngineeringState
from state.models import (
    AgentExecutionMetadata,
    ErrorRecord,
    ImplementationTask,
    ProductRequirements,
    SystemArchitecture,
    TaskPlan,
)
from tools.shared.llm_client import LLMClient
from workflows.constants import STATUS_PLANNING_COMPLETE

_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "task_planner"
_VALID_ASSIGNED_AGENTS: Set[str] = {"BackendAgent", "FrontendAgent", "QAAgent"}


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class TaskPlannerAgent(BaseAgent):
    """TaskPlannerAgent decomposes SystemArchitecture and ProductRequirements into a TaskPlan.

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
            prompts_dir: Directory containing prompt templates (defaults to prompts/task_planner).
        """
        if not model_name or not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("TaskPlannerAgent: model_name must be a non-empty string")
        if llm_client is None:
            raise ValueError("TaskPlannerAgent: llm_client must not be None")

        super().__init__(agent_name="TaskPlannerAgent", max_retries=max_retries)
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
        """Format ProductRequirements into text for prompt interpolation."""
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
        return "\n\n".join(sections)

    def _format_architecture(self, arch: SystemArchitecture) -> str:
        """Format SystemArchitecture into text for prompt interpolation."""
        sections = [f"Architecture Overview: {arch.overview}"]
        if arch.technology_stack:
            tech_lines = [f"- {comp}: {tech}" for comp, tech in arch.technology_stack.items()]
            sections.append("Technology Stack:\n" + "\n".join(tech_lines))
        if arch.api_specifications:
            api_lines = [
                f"- {spec.get('method', 'GET')} {spec.get('endpoint', '/')}: {spec.get('description', '')}"
                for spec in arch.api_specifications
            ]
            sections.append("API Specifications:\n" + "\n".join(api_lines))
        if arch.database_design:
            sections.append(f"Database Design Entities: {', '.join(arch.database_design.keys())}")
        if arch.scalability_strategy:
            sections.append(f"Scalability Strategy: {arch.scalability_strategy}")
        if arch.deployment_model:
            sections.append(f"Deployment Model: {arch.deployment_model}")
        return "\n\n".join(sections)

    def _validate_task_plan(self, plan: TaskPlan) -> None:
        """Validate all required fields of TaskPlan and its tasks (Req 5, design.md §1.3)."""
        if not plan.tasks or not isinstance(plan.tasks, list) or len(plan.tasks) == 0:
            raise ValueError("TaskPlan.tasks must contain at least one task.")

        seen_ids = set()
        for idx, task in enumerate(plan.tasks):
            if not isinstance(task, ImplementationTask):
                raise ValueError(f"TaskPlan.tasks[{idx}] must be an ImplementationTask instance.")

            if not task.task_id or not isinstance(task.task_id, str) or not task.task_id.strip():
                raise ValueError(f"TaskPlan.tasks[{idx}] missing non-empty 'task_id'.")
            if task.task_id in seen_ids:
                raise ValueError(f"TaskPlan.tasks contains duplicate task_id '{task.task_id}'.")
            seen_ids.add(task.task_id)

            if not task.title or not isinstance(task.title, str) or not task.title.strip():
                raise ValueError(f"Task '{task.task_id}' missing non-empty 'title'.")

            if not task.description or not isinstance(task.description, str) or not task.description.strip():
                raise ValueError(f"Task '{task.task_id}' missing non-empty 'description'.")

            if task.assigned_agent not in _VALID_ASSIGNED_AGENTS:
                raise ValueError(
                    f"Task '{task.task_id}' assigned_agent '{task.assigned_agent}' is invalid. "
                    f"Must be one of {sorted(_VALID_ASSIGNED_AGENTS)}."
                )

            if not (1 <= task.priority <= 100):
                raise ValueError(f"Task '{task.task_id}' priority {task.priority} must be between 1 and 100.")

            if not task.acceptance_criteria or not isinstance(task.acceptance_criteria, list):
                raise ValueError(f"Task '{task.task_id}' acceptance_criteria must be a non-empty list.")
            if not any(isinstance(c, str) and c.strip() for c in task.acceptance_criteria):
                raise ValueError(f"Task '{task.task_id}' acceptance_criteria must contain at least one non-empty string.")

        # Dependencies and execution_order validation
        for task in plan.tasks:
            for dep in task.dependencies:
                if dep not in seen_ids:
                    raise ValueError(f"Task '{task.task_id}' references unknown dependency '{dep}'.")
                if dep == task.task_id:
                    raise ValueError(f"Task '{task.task_id}' cannot depend on itself.")

        if set(plan.execution_order) != seen_ids:
            raise ValueError(
                "TaskPlan.execution_order must contain exactly the same task_id values as TaskPlan.tasks."
            )

        pos_map = {tid: idx for idx, tid in enumerate(plan.execution_order)}
        for task in plan.tasks:
            for dep in task.dependencies:
                if pos_map[dep] >= pos_map[task.task_id]:
                    raise ValueError(
                        f"Task '{task.task_id}' is ordered before its dependency '{dep}' in execution_order."
                    )

    def execute(self, state: EngineeringState) -> Dict[str, Any]:
        """Execute task plan generation with internal retry loop.

        Per design.md Section 1.3 & workflow-design-revised.md Section 3.10:
          - Reads `requirements` and `architecture` from state. If either is missing or invalid,
            immediately returns an exhaustion update with human_approval_required=True without LLM calls.
          - Runs attempts up to effective_max with prompt refinement on validation failure and
            exponential backoff (2s, 4s, 8s, ...) on unhandled exceptions.
          - On success, returns TaskPlan, increments forward_stage to 3 idempotently
            (Req 21 crit 6), resets retry_counts to 0 (Req 13 crit 7), and sets
            workflow_status='planning_complete', next_agent='BackendAgent'.
          - On exhaustion, returns an update with human_approval_required=True, an ErrorRecord
            with error_type='retry_exhausted', next_agent='TaskPlannerAgent', retry_counts at
            effective_max, custom execution_metadata with status='failed', and workflow_status
            unchanged.
        """
        started_at = _now_iso()
        requirements_raw = state.get("requirements")
        architecture_raw = state.get("architecture")
        user_context = state.get("user_context") or {}
        project_name = state.get("project_name", "")

        existing_max_retries = state.get("max_retries") or {}
        effective_max = existing_max_retries.get(self.agent_name, self.max_retries)

        existing_retry_counts = state.get("retry_counts") or {}
        start_retry_count = existing_retry_counts.get(self.agent_name, 0)

        # 1. Fast fail on missing or empty inputs
        if requirements_raw is None:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason="ProductRequirements are missing from state",
                effective_max=effective_max,
                retry_count=max(start_retry_count, effective_max),
            )

        if architecture_raw is None:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason="SystemArchitecture is missing from state",
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
                reason=f"ProductRequirements has unexpected type: {type(requirements_raw).__name__}",
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

        architecture: Optional[SystemArchitecture] = None
        if isinstance(architecture_raw, SystemArchitecture):
            architecture = architecture_raw
        elif isinstance(architecture_raw, dict):
            try:
                architecture = SystemArchitecture(**architecture_raw)
            except Exception as exc:
                return self._build_exhaustion_return(
                    state=state,
                    started_at=started_at,
                    reason=f"SystemArchitecture in state failed validation: {exc}",
                    effective_max=effective_max,
                    retry_count=max(start_retry_count, effective_max),
                )
        else:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason=f"SystemArchitecture has unexpected type: {type(architecture_raw).__name__}",
                effective_max=effective_max,
                retry_count=max(start_retry_count, effective_max),
            )

        if not architecture.overview or not architecture.overview.strip() or not architecture.technology_stack:
            return self._build_exhaustion_return(
                state=state,
                started_at=started_at,
                reason="SystemArchitecture is incomplete (missing overview or technology_stack)",
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

        req_text = self._format_requirements(requirements)
        arch_text = self._format_architecture(architecture)

        # 3. Internal retry loop bounded by remaining attempts
        for attempt in range(1, max_attempts + 1):
            current_retry_count += 1
            if attempt == 1:
                prompt = user_template.format(
                    project_name=project_name,
                    requirements=req_text,
                    architecture=arch_text,
                    user_context=user_context,
                )
            else:
                prompt = revision_template.format(
                    project_name=project_name,
                    requirements=req_text,
                    architecture=arch_text,
                    user_context=user_context,
                    error_message=f"{last_error_type}: {last_error}",
                )

            try:
                plan_obj = self.llm_client.generate_structured(
                    prompt=prompt,
                    response_model=TaskPlan,
                    system_prompt=system_prompt,
                    model_name=self.model_name,
                )
                self._validate_task_plan(plan_obj)
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
                    task_plan=plan_obj,
                )

        # 5. Exhaustion return after max_attempts failed attempts
        return self._build_exhaustion_return(
            state=state,
            started_at=started_at,
            reason=(
                f"Exhausted {max_attempts} attempts without valid task plan. "
                f"Last error ({last_error_type}): {last_error}"
            ),
            effective_max=effective_max,
            retry_count=max(current_retry_count, effective_max),
        )

    def _build_success_return(
        self,
        state: EngineeringState,
        started_at: str,
        task_plan: TaskPlan,
    ) -> Dict[str, Any]:
        """Assemble partial state update on successful task plan generation."""
        completed_at = _now_iso()

        # Versioning: increment version if task_plan already exists in state (Req 5 crit 4)
        existing_plan = state.get("task_plan")
        if existing_plan is not None:
            existing_version = getattr(existing_plan, "version", None)
            if existing_version is None and isinstance(existing_plan, dict):
                existing_version = existing_plan.get("version", 1)
            task_plan.version = (existing_version or 1) + 1
        else:
            task_plan.version = 1

        task_plan.produced_by = self.agent_name
        task_plan.produced_at = completed_at

        # Forward stage: advance to stage 3 on first-pass forward pipeline progress (Req 21 crit 6)
        current_forward_stage = state.get("forward_stage", 0)
        new_forward_stage = max(current_forward_stage, 3)

        project_name = state.get("project_name", "")
        task_count = len(task_plan.tasks)
        order_preview = " -> ".join(task_plan.execution_order[:5])
        if len(task_plan.execution_order) > 5:
            order_preview += f" ... ({len(task_plan.execution_order)} total)"

        scratchpad_entry = (
            f"TaskPlan v{task_plan.version} generated successfully for '{project_name}' "
            f"with {task_count} tasks. Execution order: {order_preview}"
        )

        updated_retry_counts = dict(state.get("retry_counts") or {})
        updated_retry_counts[self.agent_name] = 0

        return {
            "task_plan": task_plan,
            "workflow_status": STATUS_PLANNING_COMPLETE,
            "next_agent": "BackendAgent",
            "current_agent": self.agent_name,
            "forward_stage": new_forward_stage,
            "retry_counts": updated_retry_counts,  # Reset to 0 on success (Req 13 crit 7)
            "checkpoint_stage": "planning_complete",
            "checkpoints": {"planning_complete": completed_at},
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


__all__ = ["TaskPlannerAgent"]
