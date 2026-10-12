"""
workflows/graph.py

LangGraph StateGraph construction, node registration, and edge wiring for
the 11-agent engineering system plus infrastructure nodes.

Source of truth:
  - workflow-design-revised.md:
      - Section 3.1: Graph Topology (DAG with 13 nodes, entry point product_manager, final_project -> END)
      - Section 3.2: Nodes & Infrastructure Nodes (deadline_enforcer, human_approval specifications)
      - Section 3.3: Normal Sequential Edges
      - Section 3.4: Router Specifications (conditional edges)
      - Section 3.7: Global Debug Cycle Cap (global_cap_handler node behavior)
      - Section 3.8: Timeout Strategy (deadline_enforcer node behavior)
      - Section 3.9: Checkpoint Strategy (MemorySaver default checkpointer)
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
import uuid

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from state.engineering_state import EngineeringState
from state.models import ErrorRecord
from workflows.constants import (
    AGENT_NODE_ORDER,
    NODE_ARCHITECT,
    NODE_BACKEND,
    NODE_DEADLINE_ENFORCER,
    NODE_DEVOPS,
    NODE_DOCUMENTATION,
    NODE_FINAL_PROJECT,
    NODE_FRONTEND,
    NODE_GLOBAL_CAP_HANDLER,
    NODE_HUMAN_APPROVAL,
    NODE_PRODUCT_MANAGER,
    NODE_QA,
    NODE_REVIEWER,
    NODE_TASK_PLANNER,
    NODE_DEBUG,
    STATUS_DEADLINE_EXCEEDED,
    STATUS_FAILED,
)
from workflows.interrupts import human_approval_node
from workflows.routers import (
    router_after_architect,
    router_after_backend,
    router_after_debug,
    router_after_devops,
    router_after_documentation,
    router_after_frontend,
    router_after_human_approval,
    router_after_product_manager,
    router_after_qa,
    router_after_reviewer,
    router_after_task_planner,
)


def deadline_enforcer_node(state: EngineeringState) -> Dict[str, Any]:
    """Infrastructure node executing when pipeline timeout or node deadline is exceeded.

    Per workflow-design-revised.md §3.2 and §3.8.4:
      - Identifies the agent that timed out from current_agent
      - Sets workflow_status = STATUS_FAILED
      - Appends an ErrorRecord with error_type = "timeout"
      - Writes diagnostic summary to human_approval_notes
      - Unconditionally routes to END (no human approval bypass)
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    current_agent = state.get("current_agent") or "unknown"

    notes = f"Pipeline deadline exceeded or timeout in agent '{current_agent}'."
    if state.get("pipeline_deadline_at"):
        notes += f" Deadline was {state.get('pipeline_deadline_at')}."

    error = ErrorRecord(
        error_id=str(uuid.uuid4()),
        agent=current_agent,
        stage=str(state.get("workflow_status") or STATUS_DEADLINE_EXCEEDED),
        error_type="timeout",
        message=f"Execution deadline exceeded in agent '{current_agent}'",
        occurred_at=now_iso,
    )

    return {
        "workflow_status": STATUS_FAILED,
        "errors": [error],
        "human_approval_notes": notes,
        "updated_at": now_iso,
    }


def global_cap_handler_node(state: EngineeringState) -> Dict[str, Any]:
    """Infrastructure node executing when total_debug_cycles reaches or exceeds max_debug_cycles.

    Per workflow-design-revised.md §3.7.3:
      - Sets workflow_status = STATUS_FAILED
      - Appends an ErrorRecord with error_type = "retry_exhausted", agent = "Orchestrator"
      - Populates human_approval_notes with cycle count and diagnostic context
      - Unconditionally routes to END (hard stop, non-bypassable)
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    total_cycles = state.get("total_debug_cycles", 0)
    max_cycles = state.get("max_debug_cycles", 10)
    current_agent = state.get("current_agent") or "unknown"

    errors = state.get("errors", [])
    last_unresolved_msg = ""
    for err in reversed(errors):
        # Handle both Pydantic ErrorRecord instances and dict objects defensively
        is_resolved = getattr(err, "resolved", False) if not isinstance(err, dict) else err.get("resolved", False)
        if not is_resolved:
            last_unresolved_msg = getattr(err, "message", "") if not isinstance(err, dict) else err.get("message", "")
            break

    notes = f"Global debug cycle cap reached ({total_cycles}/{max_cycles}). Last agent: {current_agent}."
    if last_unresolved_msg:
        notes += f" Last unresolved error: {last_unresolved_msg}"

    error = ErrorRecord(
        error_id=str(uuid.uuid4()),
        agent="Orchestrator",
        stage=str(state.get("workflow_status") or "global_debug_cap"),
        error_type="retry_exhausted",
        message="Global debug cycle cap reached",
        occurred_at=now_iso,
    )

    return {
        "workflow_status": STATUS_FAILED,
        "errors": [error],
        "human_approval_notes": notes,
        "updated_at": now_iso,
    }


def build_graph(
    agents: Dict[str, Callable[[EngineeringState], Dict[str, Any]]],
    checkpointer: Optional[Any] = None,
) -> CompiledStateGraph:
    """Construct and compile the 14-node LangGraph workflow.

    Args:
        agents: Dictionary mapping each of the 11 agent node names in AGENT_NODE_ORDER
            to a node callable. Must contain exactly the 11 node names.
        checkpointer: LangGraph checkpointer instance. Defaults to MemorySaver() per §3.9.

    Returns:
        CompiledStateGraph ready for execution.

    Raises:
        ValueError: If agents dictionary has missing or extra keys compared to AGENT_NODE_ORDER.
    """
    expected_nodes = set(AGENT_NODE_ORDER)
    provided_nodes = set(agents.keys())
    if provided_nodes != expected_nodes:
        missing = expected_nodes - provided_nodes
        extra = provided_nodes - expected_nodes
        details = []
        if missing:
            details.append(f"missing required node(s): {sorted(missing)}")
        if extra:
            details.append(f"unexpected extra node(s): {sorted(extra)}")
        raise ValueError(
            f"agents dict must contain exactly the 11 node names in AGENT_NODE_ORDER. "
            f"Found discrepancies: {'; '.join(details)}"
        )

    builder = StateGraph(EngineeringState)

    # -------------------------------------------------------------------------
    # 1. Register 11 Agent Nodes
    # TODO: (§3.9 / §3.8.2) Per-node timeout is not supported for synchronous
    # node callables in LangGraph 1.2.5. When async agent execution is introduced
    # or LangGraph adds sync timeout support, configure per-node timeouts sourced
    # from configs/agents.yaml here.
    # -------------------------------------------------------------------------
    for node_name in AGENT_NODE_ORDER:
        builder.add_node(node_name, agents[node_name])

    # -------------------------------------------------------------------------
    # 2. Register 3 Infrastructure Nodes (§3.2, §3.7.3, §3.8.4)
    # -------------------------------------------------------------------------
    builder.add_node(NODE_DEADLINE_ENFORCER, deadline_enforcer_node)
    builder.add_node(NODE_GLOBAL_CAP_HANDLER, global_cap_handler_node)
    builder.add_node(NODE_HUMAN_APPROVAL, human_approval_node)

    # -------------------------------------------------------------------------
    # 3. Entry Point (§3.1)
    # -------------------------------------------------------------------------
    builder.set_entry_point(NODE_PRODUCT_MANAGER)

    # -------------------------------------------------------------------------
    # 4. Unconditional Edges (§3.1, §3.2, §3.7.3, §3.8.4)
    # -------------------------------------------------------------------------
    builder.add_edge(NODE_FINAL_PROJECT, END)
    builder.add_edge(NODE_DEADLINE_ENFORCER, END)
    builder.add_edge(NODE_GLOBAL_CAP_HANDLER, END)

    # -------------------------------------------------------------------------
    # 5. Conditional Edges with Explicit path_map for EVERY Possible Destination (§3.3, §3.4)
    # -------------------------------------------------------------------------
    # Common destinations reachable via P1 (deadline), P2 (global cap), P3 (human approval), or P4 fail
    common_p1_p2_p3_destinations = {
        NODE_DEADLINE_ENFORCER: NODE_DEADLINE_ENFORCER,
        NODE_GLOBAL_CAP_HANDLER: NODE_GLOBAL_CAP_HANDLER,
        NODE_HUMAN_APPROVAL: NODE_HUMAN_APPROVAL,
        END: END,
    }

    # product_manager -> architect, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_PRODUCT_MANAGER,
        router_after_product_manager,
        {NODE_ARCHITECT: NODE_ARCHITECT, **common_p1_p2_p3_destinations},
    )

    # architect -> task_planner, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_ARCHITECT,
        router_after_architect,
        {NODE_TASK_PLANNER: NODE_TASK_PLANNER, **common_p1_p2_p3_destinations},
    )

    # task_planner -> backend, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_TASK_PLANNER,
        router_after_task_planner,
        {NODE_BACKEND: NODE_BACKEND, **common_p1_p2_p3_destinations},
    )

    # backend -> frontend, debug, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_BACKEND,
        router_after_backend,
        {NODE_FRONTEND: NODE_FRONTEND, NODE_DEBUG: NODE_DEBUG, **common_p1_p2_p3_destinations},
    )

    # frontend -> qa, debug, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_FRONTEND,
        router_after_frontend,
        {NODE_QA: NODE_QA, NODE_DEBUG: NODE_DEBUG, **common_p1_p2_p3_destinations},
    )

    # qa -> reviewer, debug, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_QA,
        router_after_qa,
        {NODE_REVIEWER: NODE_REVIEWER, NODE_DEBUG: NODE_DEBUG, **common_p1_p2_p3_destinations},
    )

    # reviewer -> devops, debug, human_approval, deadline_enforcer, global_cap_handler, END
    builder.add_conditional_edges(
        NODE_REVIEWER,
        router_after_reviewer,
        {NODE_DEVOPS: NODE_DEVOPS, NODE_DEBUG: NODE_DEBUG, **common_p1_p2_p3_destinations},
    )

    # devops -> documentation, debug, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_DEVOPS,
        router_after_devops,
        {NODE_DOCUMENTATION: NODE_DOCUMENTATION, NODE_DEBUG: NODE_DEBUG, **common_p1_p2_p3_destinations},
    )

    # documentation -> final_project, deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_DOCUMENTATION,
        router_after_documentation,
        {NODE_FINAL_PROJECT: NODE_FINAL_PROJECT, **common_p1_p2_p3_destinations},
    )

    # debug -> product_manager, architect, backend, frontend, qa, reviewer, devops,
    #          deadline_enforcer, global_cap_handler, human_approval, END
    builder.add_conditional_edges(
        NODE_DEBUG,
        router_after_debug,
        {
            NODE_PRODUCT_MANAGER: NODE_PRODUCT_MANAGER,
            NODE_ARCHITECT: NODE_ARCHITECT,
            NODE_BACKEND: NODE_BACKEND,
            NODE_FRONTEND: NODE_FRONTEND,
            NODE_QA: NODE_QA,
            NODE_REVIEWER: NODE_REVIEWER,
            NODE_DEVOPS: NODE_DEVOPS,
            **common_p1_p2_p3_destinations,
        },
    )

    # human_approval -> all 11 agent nodes, deadline_enforcer, global_cap_handler, human_approval, END
    human_approval_path_map = {n: n for n in AGENT_NODE_ORDER}
    human_approval_path_map.update(common_p1_p2_p3_destinations)
    builder.add_conditional_edges(
        NODE_HUMAN_APPROVAL,
        router_after_human_approval,
        human_approval_path_map,
    )

    # -------------------------------------------------------------------------
    # 6. Checkpointer & Compilation (§3.9)
    # Default to MemorySaver() if none is provided.
    # -------------------------------------------------------------------------
    if checkpointer is None:
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)


__all__ = [
    "deadline_enforcer_node",
    "global_cap_handler_node",
    "build_graph",
]
