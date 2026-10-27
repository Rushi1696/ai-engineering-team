"""
agents/base_agent.py

BaseAgent: the shared abstract base class inherited by all eleven
concrete pipeline agents (ProductManagerAgent, ArchitectAgent,
TaskPlannerAgent, BackendAgent, FrontendAgent, QAAgent, DebugAgent,
ReviewerAgent, DevOpsAgent, DocumentationAgent, FinalProjectAgent).

Source of truth: design.md Section 1 (Agent Contracts -- every one of the
eleven per-agent contracts shares the same seven-part shape: execution
metadata lifecycle, error/retry handling, scratchpad write, current/next
agent + workflow_status + checkpoint_stage + state_version + updated_at
bookkeeping), design.md Section 6.1 ("agents/base_agent.py -- shared base
class, owned by platform team. All agents read base_agent.py (inherits
interface contract)."), design.md Section 6.2 cross-cutting rule 1
("Agents... receive configuration through constructor parameters" --
not by reading configs/ directly), requirements.md Requirement 12 (Error
Tracking), Requirement 13 (Retry and Escalation Tracking), Requirement 14
(Per-Agent Execution Metadata), Requirement 21 criterion 7
(total_invocations).

Design notes:
  - No "BaseAgent" section exists verbatim in the approved architecture
    documents. This implementation is derived from the seven-part
    structure repeated identically across all eleven per-agent contracts
    in design.md Section 1, plus the explicit cross-cutting requirements
    (12, 13, 14, 21-crit-7) that those contracts all reference. No field,
    method, or behavior has been added beyond what those sources specify
    for ALL eleven agents in common.
  - BaseAgent.invoke() is a template method matching the LangGraph node
    callable convention: it accepts the full EngineeringState and returns
    a PARTIAL state update dict, to be merged by the registered reducers
    (design.md Section 1, opening paragraph: "Agents receive the full
    EngineeringState as input... emit a partial state update").
  - BaseAgent never raises out of invoke(). A subclass's execute() may
    raise; invoke() catches it, converts it into an ErrorRecord + failure
    AgentExecutionMetadata + retry/escalation bookkeeping, and returns
    that as a normal partial-update dict. This is deliberate: a raised
    exception would crash the LangGraph node entirely, bypassing the
    router's ability to act on workflow_status="failed" or
    human_approval_required (Technical Design intent consistent with
    Requirement 12 criterion 3 and Requirement 13 criteria 5-6, which
    describe these as STATE WRITES, not exceptions propagating upward).
  - BaseAgent does NOT decide which document/artifact field a concrete
    agent writes (requirements, architecture, code_artifacts, etc.), does
    NOT decide next_agent routing beyond merging whatever the subclass's
    execute() already returned, and does NOT touch forward_stage or
    total_debug_cycles -- those have conditional, agent-specific
    semantics (Requirement 21 criteria 1 and 6) that are out of scope for
    a generic base class shared by all eleven agents identically.
  - BaseAgent does not call any function in state/reducers.py directly.
    Per design.md Section 6.2 rule: "No agent imports reducers.py
    directly (reducers are registered in Annotated field declarations)."
    BaseAgent only ever returns a partial dict; LangGraph + the
    Annotated[T, reducer_fn] wiring in state/engineering_state.py is
    responsible for the actual merge.
  - BaseAgent does not read configuration files. max_retries is supplied
    via the constructor (design.md Section 6.1, configs/ ownership note:
    "Agents do not read config files directly -- they receive
    configuration through constructor parameters").
"""

from __future__ import annotations

import re
import traceback as traceback_module
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from state.engineering_state import EngineeringState
from state.models import AgentExecutionMetadata, ErrorRecord

# ---------------------------------------------------------------------------
# Requirement 13 criterion 2: effective max_retries default when no
# explicit value has been configured for an agent.
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES: int = 3


def strip_metadata_header(text: str) -> str:
    """Strip YAML frontmatter (--- ... ---) or HTML comment (<!-- ... -->) header block.

    Ensures prompt version and author metadata never reach the LLM inference call.
    """
    cleaned = re.sub(r"^<!--[\s\S]*?-->\s*", "", text)
    cleaned = re.sub(r"^---[\s\S]*?---\s*", "", cleaned)
    return cleaned.strip()


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string.

    A thin named wrapper (rather than inlining datetime.now(timezone.utc)
    at every call site) so timestamp generation has exactly one
    implementation to keep consistent with state/engineering_state.py's
    own now_iso pattern, and so tests can monkeypatch a single seam if
    ever needed.
    """
    return datetime.now(timezone.utc).isoformat()


class BaseAgent(ABC):
    """Shared base class for all eleven concrete pipeline agents.

    Subclasses implement `execute()` with their agent-specific business
    logic (LLM calls, tool use, document/artifact construction) and
    return a partial EngineeringState update dict containing whatever
    fields THEY own (e.g. ProductManagerAgent returns `requirements`,
    `workflow_status`, `next_agent`, etc., per design.md Section 1.1).

    BaseAgent.invoke() wraps execute() and is responsible ONLY for the
    bookkeeping that is identical across all eleven agents:
      - execution_metadata lifecycle (Requirement 14)
      - error recording on failure (Requirement 12)
      - retry counting and human-approval escalation (Requirement 13)
      - total_invocations increment (Requirement 21 criterion 7)

    invoke() is the method LangGraph should register as the node
    callable. It never raises -- failures are converted into a partial
    state update describing the failure, per the design note above.
    """

    def __init__(self, agent_name: str, max_retries: int = DEFAULT_MAX_RETRIES) -> None:
        """
        Args:
            agent_name: This agent's canonical name as it appears in
                current_agent, next_agent, execution_metadata keys,
                agent_scratchpad keys, retry_counts keys, and
                ErrorRecord.agent (e.g. "ProductManagerAgent"). Must be
                non-empty.
            max_retries: This agent's configured retry ceiling
                (Requirement 13 criterion 2). Supplied by the caller via
                constructor injection -- BaseAgent does not read
                configs/agents.yaml itself. Defaults to
                DEFAULT_MAX_RETRIES (3), matching the documented
                "effective limit" used when no configuration entry is
                present for an agent.

        Raises:
            ValueError: if agent_name is empty, or if max_retries is not
                a positive integer.
        """
        if not agent_name:
            raise ValueError("BaseAgent: agent_name must be non-empty")
        if max_retries <= 0:
            raise ValueError("BaseAgent: max_retries must be a positive integer")

        self.agent_name = agent_name
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Abstract contract every concrete agent must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def execute(self, state: EngineeringState) -> Dict[str, Any]:
        """Agent-specific business logic.

        Subclasses read whatever fields of `state` they need (per their
        own "Inputs -- State Fields Read" table in design.md Section 1)
        and return a partial EngineeringState update dict containing
        whatever fields they own as outputs (per their own "Outputs --
        State Fields Written" table).

        Subclasses should NOT set execution_metadata, total_invocations,
        retry_counts, max_retries, human_approval_required, or
        human_approval_notes themselves -- invoke() owns those. A
        subclass MAY still set current_agent, next_agent,
        workflow_status, checkpoint_stage, state_version, updated_at,
        and agent_scratchpad[agent_name], since those values are
        agent-specific decisions invoke() cannot make on the subclass's
        behalf.

        Subclasses may raise any exception to signal failure; invoke()
        will catch it and convert it into the appropriate failure-path
        state update. Subclasses should NOT catch-and-swallow their own
        exceptions internally if they want invoke()'s failure handling
        (error recording, retry counting, escalation) to apply.

        Returns:
            A partial EngineeringState update dict (never the full
            state).
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Template method: the LangGraph node callable
    # ------------------------------------------------------------------

    def invoke(self, state: EngineeringState) -> Dict[str, Any]:
        """Run this agent against `state` and return a partial state
        update.

        This is the method that should be registered as the LangGraph
        node callable for this agent. It never raises: any exception
        from execute() is caught and converted into a failure-path
        partial update (error record + failed execution metadata +
        retry/escalation bookkeeping) instead of propagating.

        Lifecycle:
            1. Build the "running" AgentExecutionMetadata entry
               (Requirement 14 criterion 3).
            2. Call self.execute(state).
            3a. On success: finalize execution_metadata to "completed"
                (Requirement 14 criterion 4), increment
                total_invocations (Requirement 21 criterion 7), and
                merge these with whatever the subclass already
                returned -- the subclass's own keys take precedence for
                any key BaseAgent does not own.
            3b. On exception: build an ErrorRecord (Requirement 12
                criterion 3), finalize execution_metadata to "failed"
                (Requirement 14 criterion 5), apply retry/escalation
                bookkeeping (Requirement 13 criteria 5-6), increment
                total_invocations, and return that as the partial
                update.

        Args:
            state: The full current EngineeringState.

        Returns:
            A partial EngineeringState update dict. Never raises.
        """
        started_at = _now_iso()
        running_metadata = self._start_execution_metadata(started_at)

        try:
            subclass_update = self.execute(state)
        except Exception as exc:  # noqa: BLE001 -- intentionally broad; see docstring
            return self._build_failure_update(state, started_at, exc)

        return self._build_success_update(state, started_at, subclass_update)

    # ------------------------------------------------------------------
    # Execution metadata helpers (Requirement 14)
    # ------------------------------------------------------------------

    def _start_execution_metadata(self, started_at: str) -> AgentExecutionMetadata:
        """Build the 'running' AgentExecutionMetadata entry for this
        invocation (Requirement 14 criterion 3).

        This is constructed for documentation/testing purposes and to
        keep the "running" shape independently inspectable; invoke()
        does not return this intermediate value on its own -- only the
        final completed/failed metadata is included in the returned
        partial update, since LangGraph nodes run synchronously to
        completion within a single invoke() call and there is no
        separate "announce running, then announce completion" write in
        this architecture.
        """
        return AgentExecutionMetadata(
            agent_name=self.agent_name,
            started_at=started_at,
            completed_at=None,
            duration_seconds=None,
            tokens_used=0,
            model_calls=0,
            status="running",
            notes=None,
        )

    @staticmethod
    def _duration_seconds(started_at: str, completed_at: str) -> float:
        """Compute completed_at - started_at in seconds (Requirement 14
        criterion 4)."""
        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(completed_at)
        return (end_dt - start_dt).total_seconds()

    def _complete_execution_metadata(
        self, started_at: str, completed_at: str
    ) -> AgentExecutionMetadata:
        """Build the 'completed' AgentExecutionMetadata entry
        (Requirement 14 criterion 4).

        tokens_used and model_calls are recorded as 0 here: BaseAgent has
        no visibility into LLM call counts or token usage performed
        inside a subclass's execute() (no such values are part of the
        execute() return-type contract). A subclass that wants accurate
        tokens_used/model_calls accounting should include its own
        execution_metadata[agent_name] override in its returned dict --
        per invoke()'s merge order, a subclass-provided
        execution_metadata entry takes precedence over this default.
        """
        return AgentExecutionMetadata(
            agent_name=self.agent_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=self._duration_seconds(started_at, completed_at),
            tokens_used=0,
            model_calls=0,
            status="completed",
            notes=None,
        )

    def _fail_execution_metadata(
        self, started_at: str, completed_at: str, error: Exception
    ) -> AgentExecutionMetadata:
        """Build the 'failed' AgentExecutionMetadata entry (Requirement
        14 criterion 5)."""
        return AgentExecutionMetadata(
            agent_name=self.agent_name,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=self._duration_seconds(started_at, completed_at),
            tokens_used=0,
            model_calls=0,
            status="failed",
            notes=str(error),
        )

    # ------------------------------------------------------------------
    # Error tracking helper (Requirement 12)
    # ------------------------------------------------------------------

    def _build_error_record(
        self, state: EngineeringState, occurred_at: str, error: Exception
    ) -> ErrorRecord:
        """Build a new ErrorRecord for an exception raised by execute()
        (Requirement 12 criteria 2-3).

        error_type is classified as "validation_failure" for a
        ValueError (the conventional exception type raised by Pydantic
        validation and by state/validators.py's own guard functions) and
        "unhandled_exception" for anything else, matching the two
        failure-mode categories that appear identically across every one
        of the eleven per-agent contracts in design.md Section 1
        (e.g. Section 1.1's Failure Modes table: "validation_failure" /
        "unhandled_exception").

        `stage` is set to the current workflow_status at the time of
        failure (Requirement 12 criterion 2: "stage... one of the valid
        workflow_status values") -- this reflects the last successfully
        completed stage, since the agent that just failed has not yet
        completed its own stage transition.
        """
        error_type = "validation_failure" if isinstance(error, ValueError) else "unhandled_exception"

        return ErrorRecord(
            error_id=str(uuid.uuid4()),
            agent=self.agent_name,
            stage=state.get("workflow_status", ""),
            error_type=error_type,
            message=str(error),
            traceback=traceback_module.format_exc(),
            occurred_at=occurred_at,
            resolved=False,
            resolution_agent=None,
            resolution_notes=None,
        )

    # ------------------------------------------------------------------
    # Retry / escalation helper (Requirement 13)
    # ------------------------------------------------------------------

    def _apply_retry_escalation(
        self, state: EngineeringState
    ) -> tuple[Dict[str, int], bool, Optional[str]]:
        """Compute the retry_counts update and human-approval escalation
        decision for this agent's failure (Requirement 13 criteria 2,
        5, 6).

        Returns:
            A 3-tuple of:
              - retry_counts_update: a {agent_name: new_count} dict
                suitable for a dict_merge_reducer-backed partial update
                (only this agent's own key is included; criterion 5).
              - human_approval_required: True if the new retry count has
                reached or exceeded the effective max_retries limit
                (criterion 6), else False.
              - human_approval_notes: a populated message when escalating,
                else None.

        Per criterion 2, "WHEN a max_retries entry is not present for an
        agent, THE system SHALL treat the effective limit as 3" -- this
        reads state["max_retries"] for an existing entry and falls back
        to self.max_retries (this agent's own constructor-configured
        default, itself defaulting to 3) only if state has no entry yet.
        """
        existing_retry_counts = state.get("retry_counts") or {}
        existing_count = existing_retry_counts.get(self.agent_name, 0)
        new_count = existing_count + 1

        existing_max_retries = state.get("max_retries") or {}
        effective_max = existing_max_retries.get(self.agent_name, self.max_retries)

        human_approval_required = new_count >= effective_max
        human_approval_notes: Optional[str] = None
        if human_approval_required:
            human_approval_notes = (
                f"{self.agent_name} has failed {new_count} time(s), "
                f"reaching its configured max_retries limit of {effective_max}. "
                "Human approval is required to continue."
            )

        return {self.agent_name: new_count}, human_approval_required, human_approval_notes

    # ------------------------------------------------------------------
    # invoke() result assembly
    # ------------------------------------------------------------------

    def _build_success_update(
        self,
        state: EngineeringState,
        started_at: str,
        subclass_update: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge BaseAgent's own bookkeeping fields with whatever the
        subclass's execute() returned, for the success path.

        Merge precedence: subclass-provided keys win over BaseAgent's
        defaults for any key both happen to set (e.g. a subclass that
        wants to report real tokens_used/model_calls via its own
        execution_metadata entry, or that wants to set current_agent
        itself). BaseAgent only fills in keys the subclass did not
        already provide, except for total_invocations, which BaseAgent
        always increments unconditionally per Requirement 21 criterion 7
        ("incremented by 1 on every agent node invocation... never
        capped or reset") -- a subclass has no legitimate reason to
        override this generic counter.
        """
        completed_at = _now_iso()

        base_update: Dict[str, Any] = {
            "current_agent": self.agent_name,
            "execution_metadata": {
                self.agent_name: self._complete_execution_metadata(
                    started_at, completed_at
                )
            },
            "updated_at": completed_at,
        }

        merged: Dict[str, Any] = dict(base_update)
        merged.update(subclass_update)

        # total_invocations is BaseAgent's alone to own; always increment,
        # regardless of anything the subclass returned.
        merged["total_invocations"] = state.get("total_invocations", 0) + 1

        return merged

    def _build_failure_update(
        self, state: EngineeringState, started_at: str, error: Exception
    ) -> Dict[str, Any]:
        """Build the complete failure-path partial update: error record,
        failed execution metadata, retry/escalation bookkeeping, and
        total_invocations increment.
        """
        completed_at = _now_iso()
        error_record = self._build_error_record(state, completed_at, error)
        retry_counts_update, human_approval_required, human_approval_notes = (
            self._apply_retry_escalation(state)
        )

        update: Dict[str, Any] = {
            "current_agent": self.agent_name,
            "execution_metadata": {
                self.agent_name: self._fail_execution_metadata(
                    started_at, completed_at, error
                )
            },
            "errors": [error_record],
            "retry_counts": retry_counts_update,
            "updated_at": completed_at,
            "total_invocations": state.get("total_invocations", 0) + 1,
        }

        if human_approval_required:
            update["human_approval_required"] = True
            update["human_approval_notes"] = human_approval_notes

        return update


__all__ = ["BaseAgent", "DEFAULT_MAX_RETRIES"]