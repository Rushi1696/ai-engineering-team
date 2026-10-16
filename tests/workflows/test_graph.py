"""
tests/workflows/test_graph.py

Integration and graph structure tests for workflows/graph.py.
Covers requirements (a) through (f) from user specification.
"""

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from state.engineering_state import EngineeringState, create_initial_state
from state.models import ReviewReport
from workflows.constants import (
    AGENT_NODE_ORDER,
    APPROVAL_DECISION_APPROVED,
    APPROVAL_DECISION_REJECTED,
    NODE_ARCHITECT,
    NODE_BACKEND,
    NODE_DEBUG,
    NODE_DEVOPS,
    NODE_DOCUMENTATION,
    NODE_FINAL_PROJECT,
    NODE_FRONTEND,
    NODE_PRODUCT_MANAGER,
    NODE_QA,
    NODE_REVIEWER,
    NODE_TASK_PLANNER,
    STATUS_ARCHITECTURE_COMPLETE,
    STATUS_BACKEND_COMPLETE,
    STATUS_COMPLETED,
    STATUS_DEADLINE_EXCEEDED,
    STATUS_DEBUG_REQUIRED,
    STATUS_DEVOPS_COMPLETE,
    STATUS_DOCUMENTATION_COMPLETE,
    STATUS_ESCALATION_REQUIRED,
    STATUS_FAILED,
    STATUS_FRONTEND_COMPLETE,
    STATUS_PLANNING_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
)
from workflows.graph import build_graph


def _make_dummy_agent(node_name: str, return_update: dict):
    """Create a stub agent callable returning a predefined update dictionary."""
    def agent_fn(state: EngineeringState):
        return return_update
    return agent_fn


def test_build_graph_rejects_missing_or_extra_agents():
    """Requirement (a): build_graph rejects missing or extra agent keys."""
    # 1. Missing node (e.g. final_project missing)
    incomplete_agents = {node: _make_dummy_agent(node, {}) for node in AGENT_NODE_ORDER if node != NODE_FINAL_PROJECT}
    with pytest.raises(ValueError, match="missing required node"):
        build_graph(incomplete_agents)

    # 2. Extra node provided
    extra_agents = {node: _make_dummy_agent(node, {}) for node in AGENT_NODE_ORDER}
    extra_agents["extra_agent_node"] = _make_dummy_agent("extra_agent_node", {})
    with pytest.raises(ValueError, match="unexpected extra node"):
        build_graph(extra_agents)

    # 3. Empty dictionary
    with pytest.raises(ValueError, match="missing required node"):
        build_graph({})


def test_graph_happy_path():
    """Requirement (b): Happy path traverses product_manager -> ... -> final_project -> END."""
    execution_order = []

    def make_logging_stub(node_name: str, status: str, extra: dict = None):
        def stub(state: EngineeringState):
            execution_order.append(node_name)
            res = {"workflow_status": status, "current_agent": node_name}
            if extra:
                res.update(extra)
            return res
        return stub

    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.5,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    agents = {
        NODE_PRODUCT_MANAGER: make_logging_stub(NODE_PRODUCT_MANAGER, STATUS_REQUIREMENTS_COMPLETE),
        NODE_ARCHITECT: make_logging_stub(NODE_ARCHITECT, STATUS_ARCHITECTURE_COMPLETE),
        NODE_TASK_PLANNER: make_logging_stub(NODE_TASK_PLANNER, STATUS_PLANNING_COMPLETE),
        NODE_BACKEND: make_logging_stub(NODE_BACKEND, STATUS_BACKEND_COMPLETE),
        NODE_FRONTEND: make_logging_stub(NODE_FRONTEND, STATUS_FRONTEND_COMPLETE),
        NODE_QA: make_logging_stub(NODE_QA, STATUS_QA_COMPLETE),
        NODE_REVIEWER: make_logging_stub(NODE_REVIEWER, STATUS_REVIEW_COMPLETE, {"review_report": approved_review}),
        NODE_DEVOPS: make_logging_stub(NODE_DEVOPS, STATUS_DEVOPS_COMPLETE),
        NODE_DOCUMENTATION: make_logging_stub(NODE_DOCUMENTATION, STATUS_DOCUMENTATION_COMPLETE),
        NODE_FINAL_PROJECT: make_logging_stub(NODE_FINAL_PROJECT, STATUS_COMPLETED),
        NODE_DEBUG: make_logging_stub(NODE_DEBUG, STATUS_DEBUG_REQUIRED),  # Not invoked in happy path
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="HappyApp", user_input="Build happy app")
    config = {"configurable": {"thread_id": "thread_happy_path"}}

    final_state = graph.invoke(initial_state, config=config)

    expected_sequence = [
        NODE_PRODUCT_MANAGER,
        NODE_ARCHITECT,
        NODE_TASK_PLANNER,
        NODE_BACKEND,
        NODE_FRONTEND,
        NODE_QA,
        NODE_REVIEWER,
        NODE_DEVOPS,
        NODE_DOCUMENTATION,
        NODE_FINAL_PROJECT,
    ]
    assert execution_order == expected_sequence
    assert final_state["workflow_status"] == STATUS_COMPLETED


def test_graph_debug_loop_iteration():
    """Requirement (c): One debug-loop iteration: qa sets debug_required, debug runs, then loops back."""
    execution_order = []
    qa_invocation_count = 0

    def make_tracking_stub(node_name: str, update_dict: dict):
        def stub(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return stub

    def qa_stub(state: EngineeringState):
        nonlocal qa_invocation_count
        qa_invocation_count += 1
        execution_order.append(NODE_QA)
        if qa_invocation_count == 1:
            # First pass: test failure, request debug
            return {"workflow_status": STATUS_DEBUG_REQUIRED, "current_agent": "QAAgent"}
        # Second pass: tests pass
        return {"workflow_status": STATUS_QA_COMPLETE, "current_agent": "QAAgent"}

    def debug_stub(state: EngineeringState):
        execution_order.append(NODE_DEBUG)
        return {
            "workflow_status": STATUS_BACKEND_COMPLETE,
            "next_agent": "BackendAgent",
            "total_debug_cycles": 1,
            "current_agent": "DebugAgent",
        }

    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.0,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    agents = {
        NODE_PRODUCT_MANAGER: make_tracking_stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: make_tracking_stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: make_tracking_stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: make_tracking_stub(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE}),
        NODE_FRONTEND: make_tracking_stub(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: qa_stub,
        NODE_DEBUG: debug_stub,
        NODE_REVIEWER: make_tracking_stub(NODE_REVIEWER, {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review}),
        NODE_DEVOPS: make_tracking_stub(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: make_tracking_stub(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: make_tracking_stub(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DebugLoopApp", user_input="Build loop app")
    config = {"configurable": {"thread_id": "thread_debug_loop"}}

    final_state = graph.invoke(initial_state, config=config)

    assert NODE_DEBUG in execution_order
    # Verify the sequence up to debug and back through backend -> frontend -> qa
    assert execution_order == [
        NODE_PRODUCT_MANAGER,
        NODE_ARCHITECT,
        NODE_TASK_PLANNER,
        NODE_BACKEND,
        NODE_FRONTEND,
        NODE_QA,       # pass 1: requests debug
        NODE_DEBUG,    # resolves, directs to backend
        NODE_BACKEND,  # re-implements
        NODE_FRONTEND, # passes through
        NODE_QA,       # pass 2: passes
        NODE_REVIEWER,
        NODE_DEVOPS,
        NODE_DOCUMENTATION,
        NODE_FINAL_PROJECT,
    ]
    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["total_debug_cycles"] == 1


def test_graph_global_cap_routes_to_global_cap_handler():
    """Requirement (d): Global cap routes to global_cap_handler, sets workflow_status to failed and ends."""
    execution_order = []

    def stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return fn

    # backend sets total_debug_cycles to 10 where max_debug_cycles is 10
    agents = {
        NODE_PRODUCT_MANAGER: stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: stub(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE, "total_debug_cycles": 10, "current_agent": "BackendAgent"}),
        NODE_FRONTEND: stub(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: stub(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_DEBUG: stub(NODE_DEBUG, {}),
        NODE_REVIEWER: stub(NODE_REVIEWER, {}),
        NODE_DEVOPS: stub(NODE_DEVOPS, {}),
        NODE_DOCUMENTATION: stub(NODE_DOCUMENTATION, {}),
        NODE_FINAL_PROJECT: stub(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="CapApp", user_input="Build cap app")
    initial_state["max_debug_cycles"] = 10
    config = {"configurable": {"thread_id": "thread_cap"}}

    final_state = graph.invoke(initial_state, config=config)

    # After backend, router_after_backend sees total_debug_cycles >= max_debug_cycles (P2)
    # Routes to global_cap_handler -> END
    assert NODE_FRONTEND not in execution_order
    assert NODE_FINAL_PROJECT not in execution_order
    assert final_state["workflow_status"] == STATUS_FAILED
    assert any(err.error_type == "retry_exhausted" for err in final_state["errors"])


def test_graph_deadline_exceeded_routes_to_deadline_enforcer():
    """Requirement (e): deadline_exceeded routes to deadline_enforcer, sets failed and ends."""
    execution_order = []

    def stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return fn

    # architect times out and sets deadline_exceeded
    agents = {
        NODE_PRODUCT_MANAGER: stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: stub(NODE_ARCHITECT, {"workflow_status": STATUS_DEADLINE_EXCEEDED, "current_agent": "ArchitectAgent"}),
        NODE_TASK_PLANNER: stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: stub(NODE_BACKEND, {}),
        NODE_FRONTEND: stub(NODE_FRONTEND, {}),
        NODE_QA: stub(NODE_QA, {}),
        NODE_DEBUG: stub(NODE_DEBUG, {}),
        NODE_REVIEWER: stub(NODE_REVIEWER, {}),
        NODE_DEVOPS: stub(NODE_DEVOPS, {}),
        NODE_DOCUMENTATION: stub(NODE_DOCUMENTATION, {}),
        NODE_FINAL_PROJECT: stub(NODE_FINAL_PROJECT, {}),
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DeadlineApp", user_input="Build deadline app")
    config = {"configurable": {"thread_id": "thread_deadline"}}

    final_state = graph.invoke(initial_state, config=config)

    assert execution_order == [NODE_PRODUCT_MANAGER, NODE_ARCHITECT]
    assert final_state["workflow_status"] == STATUS_FAILED
    assert any(err.error_type == "timeout" for err in final_state["errors"])


def test_graph_expired_pipeline_deadline_diverts_after_first_node_to_deadline_enforcer():
    """A run with an expired pipeline_deadline_at diverts after the first node to deadline_enforcer,
    ends failed, with an ErrorRecord of error_type 'timeout'.
    Built directly with create_initial_state + build_graph.
    """
    execution_order = []

    def stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return fn

    agents = {
        NODE_PRODUCT_MANAGER: stub(
            NODE_PRODUCT_MANAGER,
            {"workflow_status": STATUS_REQUIREMENTS_COMPLETE, "current_agent": "ProductManagerAgent"},
        ),
        NODE_ARCHITECT: stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: stub(NODE_BACKEND, {}),
        NODE_FRONTEND: stub(NODE_FRONTEND, {}),
        NODE_QA: stub(NODE_QA, {}),
        NODE_DEBUG: stub(NODE_DEBUG, {}),
        NODE_REVIEWER: stub(NODE_REVIEWER, {}),
        NODE_DEVOPS: stub(NODE_DEVOPS, {}),
        NODE_DOCUMENTATION: stub(NODE_DOCUMENTATION, {}),
        NODE_FINAL_PROJECT: stub(NODE_FINAL_PROJECT, {}),
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(
        project_name="ExpiredDeadlineApp",
        user_input="Build expired deadline app",
        pipeline_deadline_at="2020-01-01T00:00:00Z",
    )
    config = {"configurable": {"thread_id": "thread_expired_deadline"}}

    final_state = graph.invoke(initial_state, config=config)

    # First node executes, but router after ProductManagerAgent diverts to deadline_enforcer -> END
    assert execution_order == [NODE_PRODUCT_MANAGER]
    assert final_state["workflow_status"] == STATUS_FAILED
    timeout_errors = [err for err in final_state["errors"] if err.error_type == "timeout"]
    assert len(timeout_errors) == 1
    assert timeout_errors[0].agent == "ProductManagerAgent"



def test_graph_human_approval_interrupt_and_resume_approved_and_rejected():
    """Requirement (f): Real human_approval interrupt, resumed with Command(resume=...)
    for both 'approved' (resumes at the right node) and 'rejected' (ends failed).
    """
    def stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            return update_dict
        return fn

    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.0,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    # Reviewer triggers human approval hold
    agents = {
        NODE_PRODUCT_MANAGER: stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: stub(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE}),
        NODE_FRONTEND: stub(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: stub(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: stub(
            NODE_REVIEWER,
            {
                "human_approval_required": True,
                "current_agent": "ReviewerAgent",
                "next_agent": "DevOpsAgent",
                "workflow_status": STATUS_REVIEW_COMPLETE,
                "review_report": approved_review,
            },
        ),
        NODE_DEVOPS: stub(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: stub(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: stub(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: stub(NODE_DEBUG, {}),
    }

    # -------------------------------------------------------------------------
    # Case 1: Approved -> resumes at DevOpsAgent and reaches completed
    # -------------------------------------------------------------------------
    graph_approved = build_graph(agents, checkpointer=MemorySaver())
    initial_state_1 = create_initial_state(project_name="InterruptApp1", user_input="Build test app")
    config_1 = {"configurable": {"thread_id": "thread_interrupt_approved"}}

    # First execution pauses at human_approval node
    list(graph_approved.stream(initial_state_1, config=config_1))
    snapshot_1 = graph_approved.get_state(config_1)
    assert len(snapshot_1.tasks) > 0
    assert snapshot_1.tasks[0].interrupts[0].value["current_agent"] == "ReviewerAgent"

    # Resume with Command(resume="approved")
    resume_cmd_approved = Command(resume={"decision": APPROVAL_DECISION_APPROVED, "notes": "Approved by human lead"})
    list(graph_approved.stream(resume_cmd_approved, config=config_1))

    final_snapshot_1 = graph_approved.get_state(config_1)
    assert final_snapshot_1.values["workflow_status"] == STATUS_COMPLETED
    assert final_snapshot_1.values["human_approval_required"] is False
    assert final_snapshot_1.values["human_approval_notes"] == "Approved by human lead"

    # -------------------------------------------------------------------------
    # Case 2: Rejected -> terminates with status failed
    # -------------------------------------------------------------------------
    graph_rejected = build_graph(agents, checkpointer=MemorySaver())
    initial_state_2 = create_initial_state(project_name="InterruptApp2", user_input="Build test app")
    config_2 = {"configurable": {"thread_id": "thread_interrupt_rejected"}}

    # First execution pauses at human_approval node
    list(graph_rejected.stream(initial_state_2, config=config_2))
    snapshot_2 = graph_rejected.get_state(config_2)
    assert len(snapshot_2.tasks) > 0

    # Resume with Command(resume="rejected")
    resume_cmd_rejected = Command(resume={"decision": APPROVAL_DECISION_REJECTED, "notes": "Rejected by human lead"})
    list(graph_rejected.stream(resume_cmd_rejected, config=config_2))

    final_snapshot_2 = graph_rejected.get_state(config_2)
    assert final_snapshot_2.values["workflow_status"] == STATUS_FAILED
    assert final_snapshot_2.values["human_approval_required"] is False
    assert final_snapshot_2.values["human_approval_notes"] == "Rejected by human lead"


def test_graph_rejected_review_escalation_design_level():
    """Follow-up (1): Rejected review with rejection_scope='design_level'.
    Resuming with approved re-enters architect, clears rejection_scope, and completes.
    Resuming with rejected sets workflow_status to failed.
    """
    execution_order = []
    reviewer_invocations = 0

    def make_tracking_stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return fn

    report_rejected = ReviewReport(
        overall_status="rejected",
        code_quality_score=3.0,
        architecture_compliance=False,
        engineering_standards_compliance=False,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    report_approved = ReviewReport(
        overall_status="approved",
        code_quality_score=9.0,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=2,
    )

    def reviewer_stub(state: EngineeringState):
        nonlocal reviewer_invocations
        reviewer_invocations += 1
        execution_order.append(NODE_REVIEWER)
        if reviewer_invocations == 1:
            return {
                "workflow_status": STATUS_ESCALATION_REQUIRED,
                "review_report": report_rejected,
                "rejection_scope": "design_level",
                "human_approval_required": True,
                "current_agent": "ReviewerAgent",
            }
        return {
            "workflow_status": STATUS_REVIEW_COMPLETE,
            "review_report": report_approved,
            "human_approval_required": False,
            "current_agent": "ReviewerAgent",
        }

    agents = {
        NODE_PRODUCT_MANAGER: make_tracking_stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: make_tracking_stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: make_tracking_stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: make_tracking_stub(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE}),
        NODE_FRONTEND: make_tracking_stub(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: make_tracking_stub(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: reviewer_stub,
        NODE_DEVOPS: make_tracking_stub(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: make_tracking_stub(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: make_tracking_stub(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: make_tracking_stub(NODE_DEBUG, {}),
    }

    # Case 1: Approved -> re-enters architect, clears rejection_scope, completes
    graph_approved = build_graph(agents, checkpointer=MemorySaver())
    initial_state_1 = create_initial_state(project_name="DesignReviewApp1", user_input="Build app")
    config_1 = {"configurable": {"thread_id": "thread_design_review_approved"}}

    list(graph_approved.stream(initial_state_1, config=config_1))
    snapshot_1 = graph_approved.get_state(config_1)
    assert len(snapshot_1.tasks) > 0
    assert snapshot_1.tasks[0].interrupts[0].value["rejection_scope"] == "design_level"

    resume_cmd = Command(resume={"decision": APPROVAL_DECISION_APPROVED, "notes": "Approved redesign"})
    list(graph_approved.stream(resume_cmd, config=config_1))

    final_snapshot_1 = graph_approved.get_state(config_1)
    assert final_snapshot_1.values["workflow_status"] == STATUS_COMPLETED
    # Under replace_reducer (Requirement 18 criterion 7), rejection_scope preserves its written value
    assert final_snapshot_1.values["rejection_scope"] == "design_level"
    # Verify execution order re-entered architect after first reviewer call
    assert execution_order == [
        NODE_PRODUCT_MANAGER,
        NODE_ARCHITECT,
        NODE_TASK_PLANNER,
        NODE_BACKEND,
        NODE_FRONTEND,
        NODE_QA,
        NODE_REVIEWER,  # 1st pass: rejected
        NODE_ARCHITECT, # re-entered!
        NODE_TASK_PLANNER,
        NODE_BACKEND,
        NODE_FRONTEND,
        NODE_QA,
        NODE_REVIEWER,  # 2nd pass: approved
        NODE_DEVOPS,
        NODE_DOCUMENTATION,
        NODE_FINAL_PROJECT,
    ]

    # Case 2: Rejected -> terminates failed
    execution_order.clear()
    reviewer_invocations = 0
    graph_rejected = build_graph(agents, checkpointer=MemorySaver())
    initial_state_2 = create_initial_state(project_name="DesignReviewApp2", user_input="Build app")
    config_2 = {"configurable": {"thread_id": "thread_design_review_rejected"}}

    list(graph_rejected.stream(initial_state_2, config=config_2))
    resume_cmd_reject = Command(resume={"decision": APPROVAL_DECISION_REJECTED, "notes": "Abandon redesign"})
    list(graph_rejected.stream(resume_cmd_reject, config=config_2))

    final_snapshot_2 = graph_rejected.get_state(config_2)
    assert final_snapshot_2.values["workflow_status"] == STATUS_FAILED


def test_graph_rejected_review_escalation_code_level():
    """Follow-up (1): Rejected review with rejection_scope='code_level' re-enters at debug."""
    execution_order = []
    reviewer_invocations = 0

    def make_tracking_stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return fn

    report_rejected = ReviewReport(
        overall_status="rejected",
        code_quality_score=3.0,
        architecture_compliance=True,
        engineering_standards_compliance=False,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    report_approved = ReviewReport(
        overall_status="approved",
        code_quality_score=9.0,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=2,
    )

    def reviewer_stub(state: EngineeringState):
        nonlocal reviewer_invocations
        reviewer_invocations += 1
        execution_order.append(NODE_REVIEWER)
        if reviewer_invocations == 1:
            return {
                "workflow_status": STATUS_ESCALATION_REQUIRED,
                "review_report": report_rejected,
                "rejection_scope": "code_level",
                "human_approval_required": True,
                "current_agent": "ReviewerAgent",
            }
        return {
            "workflow_status": STATUS_REVIEW_COMPLETE,
            "review_report": report_approved,
            "human_approval_required": False,
            "current_agent": "ReviewerAgent",
        }

    def debug_stub(state: EngineeringState):
        execution_order.append(NODE_DEBUG)
        return {
            "workflow_status": STATUS_BACKEND_COMPLETE,
            "next_agent": "BackendAgent",
            "current_agent": "DebugAgent",
        }

    agents = {
        NODE_PRODUCT_MANAGER: make_tracking_stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: make_tracking_stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: make_tracking_stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: make_tracking_stub(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE}),
        NODE_FRONTEND: make_tracking_stub(NODE_FRONTEND, {"workflow_status": STATUS_FRONTEND_COMPLETE}),
        NODE_QA: make_tracking_stub(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: reviewer_stub,
        NODE_DEBUG: debug_stub,
        NODE_DEVOPS: make_tracking_stub(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: make_tracking_stub(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: make_tracking_stub(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="CodeReviewApp", user_input="Build app")
    config = {"configurable": {"thread_id": "thread_code_review_approved"}}

    list(graph.stream(initial_state, config=config))
    snapshot = graph.get_state(config)
    assert snapshot.tasks[0].interrupts[0].value["rejection_scope"] == "code_level"

    resume_cmd = Command(resume={"decision": APPROVAL_DECISION_APPROVED, "notes": "Approved code fix"})
    list(graph.stream(resume_cmd, config=config))

    final_snapshot = graph.get_state(config)
    assert final_snapshot.values["workflow_status"] == STATUS_COMPLETED
    # Under replace_reducer (Requirement 18 criterion 7), rejection_scope preserves its written value
    assert final_snapshot.values["rejection_scope"] == "code_level"
    # Verify execution order re-entered debug after first reviewer call
    assert execution_order == [
        NODE_PRODUCT_MANAGER,
        NODE_ARCHITECT,
        NODE_TASK_PLANNER,
        NODE_BACKEND,
        NODE_FRONTEND,
        NODE_QA,
        NODE_REVIEWER,  # 1st pass: rejected with code_level
        NODE_DEBUG,     # re-entered at debug!
        NODE_BACKEND,
        NODE_FRONTEND,
        NODE_QA,
        NODE_REVIEWER,  # 2nd pass: approved
        NODE_DEVOPS,
        NODE_DOCUMENTATION,
        NODE_FINAL_PROJECT,
    ]


def test_graph_stale_rejection_scope_hazard_on_retry_exhaustion():
    """End-to-end test of the stale-scope hazard:
    An earlier review left rejection_scope='code_level' in state.
    A later retry-exhaustion escalation occurs where workflow_status is NOT escalation_required
    (e.g., workflow_status='frontend_complete', human_approval_required=True,
     current_agent='FrontendAgent', next_agent='QAAgent', retry_counts={'FrontendAgent': 3}).
    When human approval approves, the graph resumes at next_agent ('QAAgent' / qa)
    and does NOT divert to debug.
    """
    execution_order = []

    def stub(node_name: str, update_dict: dict):
        def fn(state: EngineeringState):
            execution_order.append(node_name)
            return update_dict
        return fn

    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.0,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    agents = {
        NODE_PRODUCT_MANAGER: stub(NODE_PRODUCT_MANAGER, {"workflow_status": STATUS_REQUIREMENTS_COMPLETE}),
        NODE_ARCHITECT: stub(NODE_ARCHITECT, {"workflow_status": STATUS_ARCHITECTURE_COMPLETE}),
        NODE_TASK_PLANNER: stub(NODE_TASK_PLANNER, {"workflow_status": STATUS_PLANNING_COMPLETE}),
        NODE_BACKEND: stub(NODE_BACKEND, {"workflow_status": STATUS_BACKEND_COMPLETE}),
        NODE_FRONTEND: stub(
            NODE_FRONTEND,
            {
                "workflow_status": STATUS_FRONTEND_COMPLETE,
                "human_approval_required": True,
                "current_agent": "FrontendAgent",
                "next_agent": "QAAgent",
                "retry_counts": {"FrontendAgent": 3},
            },
        ),
        NODE_QA: stub(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_DEBUG: stub(NODE_DEBUG, {}),
        NODE_REVIEWER: stub(NODE_REVIEWER, {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review}),
        NODE_DEVOPS: stub(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: stub(NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}),
        NODE_FINAL_PROJECT: stub(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
    }

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="StaleScopeApp", user_input="Build app")
    # Simulate stale rejection_scope left in state from an earlier review pass
    initial_state["rejection_scope"] = "code_level"
    config = {"configurable": {"thread_id": "thread_stale_scope"}}

    list(graph.stream(initial_state, config=config))
    snapshot = graph.get_state(config)
    assert len(snapshot.tasks) > 0

    # Resume with Command(resume=approved)
    resume_cmd = Command(resume={"decision": APPROVAL_DECISION_APPROVED, "notes": "Approved retry"})
    list(graph.stream(resume_cmd, config=config))

    final_snapshot = graph.get_state(config)
    assert final_snapshot.values["workflow_status"] == STATUS_COMPLETED
    assert final_snapshot.values["retry_counts"]["FrontendAgent"] == 0
    # Crucial assertion: debug was NEVER executed!
    assert NODE_DEBUG not in execution_order
    # Resumed at qa!
    assert execution_order == [
        NODE_PRODUCT_MANAGER,
        NODE_ARCHITECT,
        NODE_TASK_PLANNER,
        NODE_BACKEND,
        NODE_FRONTEND,  # pauses at human approval
        NODE_QA,        # resumes at QAAgent, NOT debug!
        NODE_REVIEWER,
        NODE_DEVOPS,
        NODE_DOCUMENTATION,
        NODE_FINAL_PROJECT,
    ]


def test_graph_conditional_edges_structural_completeness():
    """Follow-up (2): For every conditional-edge node, every destination its router
    can return is present in the compiled graph's edges.
    """
    dummy_agents = {node: _make_dummy_agent(node, {}) for node in AGENT_NODE_ORDER}
    graph = build_graph(dummy_agents, checkpointer=MemorySaver())
    edges = graph.get_graph().edges
    edge_pairs = {(e.source, e.target) for e in edges}

    # Common destinations for every conditional router
    common = {"deadline_enforcer", "global_cap_handler", "human_approval", "__end__"}

    expected_conditional_destinations = {
        NODE_PRODUCT_MANAGER: {NODE_ARCHITECT} | common,
        NODE_ARCHITECT: {NODE_TASK_PLANNER} | common,
        NODE_TASK_PLANNER: {NODE_BACKEND} | common,
        NODE_BACKEND: {NODE_FRONTEND, NODE_DEBUG} | common,
        NODE_FRONTEND: {NODE_QA, NODE_DEBUG} | common,
        NODE_QA: {NODE_REVIEWER, NODE_DEBUG} | common,
        NODE_REVIEWER: {NODE_DEVOPS, NODE_DEBUG} | common,
        NODE_DEVOPS: {NODE_DOCUMENTATION, NODE_DEBUG} | common,
        NODE_DOCUMENTATION: {NODE_FINAL_PROJECT} | common,
        NODE_DEBUG: {
            NODE_PRODUCT_MANAGER,
            NODE_ARCHITECT,
            NODE_BACKEND,
            NODE_FRONTEND,
            NODE_QA,
            NODE_REVIEWER,
            NODE_DEVOPS,
        } | common,
        "human_approval": set(AGENT_NODE_ORDER) | common,
    }

    for source_node, expected_targets in expected_conditional_destinations.items():
        actual_targets = {target for src, target in edge_pairs if src == source_node}
        for expected in expected_targets:
            assert expected in actual_targets, (
                f"Missing edge destination in compiled graph: {source_node} -> {expected}. "
                f"Actual destinations for {source_node}: {sorted(actual_targets)}"
            )
