"""
workflows/runner.py

Thin pipeline execution entry point for the 11-agent engineering system.

Source of truth:
  - design.md Section 6.1 (workflows/ folder ownership, runner.py entry point:
    initialise state, compile graph, run).
  - workflow-design-revised.md Section 3.10.5 (line 759: runner.py polls LangGraph
    streaming API and prompts operator; test fixtures inject pre-configured approval_decision).
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
import uuid

from langgraph.types import Command

from state.engineering_state import EngineeringState, create_initial_state
from workflows.constants import (
    APPROVAL_DECISION_APPROVED,
    APPROVAL_DECISION_REJECTED,
)
from workflows.graph import build_graph
from workflows.routers import parse_iso_utc


def cli_prompt_approval(interrupt_payload: Dict[str, Any]) -> Dict[str, str]:
    """Default interactive CLI prompt for operator human approval.

    Surfaces escalation context to the operator via stdout and reads decision from stdin.
    Separated from run_pipeline execution logic to allow headless/mock testing.
    """
    print("\n" + "=" * 60)
    print("HUMAN APPROVAL REQUIRED")
    print("=" * 60)
    print(f"Run ID:               {interrupt_payload.get('run_id')}")
    print(f"Current Agent:        {interrupt_payload.get('current_agent')}")
    print(f"Workflow Status:      {interrupt_payload.get('workflow_status')}")
    print(f"Next Agent:           {interrupt_payload.get('next_agent')}")
    if interrupt_payload.get("rejection_scope"):
        print(f"Rejection Scope:      {interrupt_payload.get('rejection_scope')}")
    if interrupt_payload.get("total_debug_cycles") is not None:
        print(f"Total Debug Cycles:   {interrupt_payload.get('total_debug_cycles')}")
    print(f"Escalation Notes:     {interrupt_payload.get('human_approval_notes')}")
    print("-" * 60)

    while True:
        decision = input(
            f"Enter decision [{APPROVAL_DECISION_APPROVED}/{APPROVAL_DECISION_REJECTED}]: "
        ).strip().lower()
        if decision in (APPROVAL_DECISION_APPROVED, APPROVAL_DECISION_REJECTED):
            break
        print(f"Invalid input. Please enter '{APPROVAL_DECISION_APPROVED}' or '{APPROVAL_DECISION_REJECTED}'.")

    notes = input("Enter operator notes (optional): ").strip()
    return {"decision": decision, "notes": notes}


def run_pipeline(
    project_name: str,
    user_input: str,
    agents: Dict[str, Callable[[EngineeringState], Dict[str, Any]]],
    checkpointer: Optional[Any] = None,
    user_context: Optional[Dict[str, Any]] = None,
    pipeline_deadline_at: Optional[str] = None,
    max_debug_cycles: int = 10,
    session_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    approval_callback: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> EngineeringState:
    """Execute the engineering pipeline from initial state through completion or terminal failure.

    Steps:
      (a) Build initial state via create_initial_state
      (b) Compile graph via build_graph with injected agents and checkpointer
      (c) Stream execution
      (d) Detect interrupts via snapshot.tasks / interrupts and surface interrupt payload
      (e) Resume with Command(resume={"decision": ..., "notes": ...})

    Args:
        project_name: Name of the project.
        user_input: Raw natural language requirements.
        agents: Dictionary mapping 11 agent node names to node callables.
        checkpointer: Optional LangGraph checkpointer (defaults to MemorySaver).
        user_context: Optional context dictionary.
        pipeline_deadline_at: Optional ISO 8601 deadline.
        max_debug_cycles: Maximum global debug cycles (default 10).
        session_id: Optional session identifier.
        thread_id: Optional LangGraph thread_id for checkpoint isolation.
        approval_callback: Callback invoked with interrupt_payload when an interrupt occurs.
            Must return dict with 'decision' and optional 'notes'. If None, defaults to cli_prompt_approval.

    Returns:
        The final EngineeringState dictionary.
    """
    # Validate deadline before state creation
    if pipeline_deadline_at is not None:
        deadline_dt = parse_iso_utc(pipeline_deadline_at)
        if deadline_dt <= datetime.now(timezone.utc):
            raise ValueError(
                f"pipeline_deadline_at '{pipeline_deadline_at}' is already in the past."
            )

    # (a) Build initial state
    initial_state = create_initial_state(
        project_name=project_name,
        user_input=user_input,
        user_context=user_context,
        pipeline_deadline_at=pipeline_deadline_at,
        max_debug_cycles=max_debug_cycles,
        session_id=session_id,
    )

    # (b) Compile graph
    graph = build_graph(agents, checkpointer=checkpointer)

    effective_thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": effective_thread_id}}

    # (c) Stream initial execution
    for _ in graph.stream(initial_state, config=config):
        pass

    # (d) Detect interrupts and loop until graph terminates
    while True:
        snapshot = graph.get_state(config)
        interrupts = []
        for task in snapshot.tasks:
            if getattr(task, "interrupts", None):
                interrupts.extend(task.interrupts)

        if not interrupts:
            break

        interrupt_payload = interrupts[0].value

        callback = approval_callback or cli_prompt_approval
        approval_response = callback(interrupt_payload)

        # (e) Resume with Command
        resume_cmd = Command(resume=approval_response)
        for _ in graph.stream(resume_cmd, config=config):
            pass

    final_snapshot = graph.get_state(config)
    return final_snapshot.values


__all__ = [
    "cli_prompt_approval",
    "run_pipeline",
]
