"""
tests/workflows/test_graph_debug.py

Integration tests verifying real DebugAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.debug_agent import DebugAgent
from state.engineering_state import EngineeringState, create_initial_state
from state.models import (
    CodeArtifact,
    ErrorRecord,
    ImplementationTask,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TaskPlan,
    TestArtifact,
)

TestArtifact.__test__ = False
from state.reducers import unresolved_errors
from tools.debug.log_parser import FakeLogParser, ParsedErrorLog
from tools.debug.sandbox import FakeExecutionSandbox, SandboxExecutionResult
from tools.shared.llm_client import FakeLLMClient
from workflows.constants import (
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
    STATUS_DEBUG_REQUIRED,
    STATUS_DEVOPS_COMPLETE,
    STATUS_DOCUMENTATION_COMPLETE,
    STATUS_FAILED,
    STATUS_FRONTEND_COMPLETE,
    STATUS_PLANNING_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
)
from workflows.graph import build_graph


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["User can authenticate", "User can submit orders"],
        non_functional_requirements=["Response time under 200ms", "99.9% uptime"],
        user_stories=["As a user I want to sign up so that I can buy items"],
        acceptance_criteria=["Given valid credentials, return 200 and auth token"],
        constraints=["Python 3.11+", "FastAPI"],
        business_goals=["Increase customer conversion rate by 15%"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture() -> SystemArchitecture:
    return SystemArchitecture(
        overview="React frontend communicating with FastAPI backend.",
        technology_stack={
            "backend": "Python 3.11 / FastAPI",
            "frontend": "React 18 / Vite",
        },
        api_specifications=[
            {
                "endpoint": "/api/v1/auth/login",
                "method": "POST",
                "description": "Authenticate user and issue JWT",
            },
        ],
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="TASK-001",
                title="Backend Auth",
                description="Backend API",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["API returns 200"],
                status="completed",
            ),
        ],
        execution_order=["TASK-001"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "app/auth.py": CodeArtifact(
            file_path="app/auth.py",
            language="python",
            content="def login(): return 500  # Bug\n",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
    }


def _sample_test_artifacts() -> Dict[str, TestArtifact]:
    return {
        "tests/test_auth.py": TestArtifact(
            file_path="tests/test_auth.py",
            content="def test_login(): assert login() == 200\n",
            test_type="unit",
            coverage_target="app/auth.py",
            produced_by="QAAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_debug_resolution_dict(target_agent: str = "BackendAgent") -> Dict[str, Any]:
    return {
        "root_cause_analysis": "login() returns 500 instead of 200.",
        "hypothesis": "Changing status return to 200 fixes the test.",
        "corrected_code_artifacts": [
            {
                "file_path": "app/auth.py",
                "language": "python",
                "content": "def login(): return 200  # Fixed\n",
                "artifact_type": "backend",
                "produced_by": "DebugAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "corrected_test_artifacts": [],
        "target_agent": target_agent,
        "resolution_notes": "Patched login() to return 200.",
    }


def _make_dummy_agent(node_name: str, update: dict):
    def stub(state: EngineeringState):
        res = dict(update)
        res["current_agent"] = node_name
        return res
    return stub


def _build_pipeline_stubs():
    approved_review = ReviewReport(
        overall_status="approved",
        code_quality_score=9.5,
        architecture_compliance=True,
        engineering_standards_compliance=True,
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    code_arts = _sample_code_artifacts()

    def dynamic_backend(state: EngineeringState):
        arts = state.get("code_artifacts") or code_arts
        return {
            "current_agent": NODE_BACKEND,
            "workflow_status": STATUS_BACKEND_COMPLETE,
            "code_artifacts": arts,
            "forward_stage": 4,
        }

    def dynamic_frontend(state: EngineeringState):
        arts = state.get("code_artifacts") or code_arts
        return {
            "current_agent": NODE_FRONTEND,
            "workflow_status": STATUS_FRONTEND_COMPLETE,
            "code_artifacts": arts,
            "forward_stage": 5,
        }

    return {
        NODE_PRODUCT_MANAGER: _make_dummy_agent(
            NODE_PRODUCT_MANAGER,
            {
                "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
                "requirements": _sample_valid_requirements(),
                "forward_stage": 1,
            },
        ),
        NODE_ARCHITECT: _make_dummy_agent(
            NODE_ARCHITECT,
            {
                "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
                "architecture": _sample_valid_architecture(),
                "forward_stage": 2,
            },
        ),
        NODE_TASK_PLANNER: _make_dummy_agent(
            NODE_TASK_PLANNER,
            {
                "workflow_status": STATUS_PLANNING_COMPLETE,
                "task_plan": _sample_valid_task_plan(),
                "forward_stage": 3,
            },
        ),
        NODE_BACKEND: dynamic_backend,
        NODE_FRONTEND: dynamic_frontend,
        NODE_QA: _make_dummy_agent(NODE_QA, {"workflow_status": STATUS_QA_COMPLETE}),
        NODE_REVIEWER: _make_dummy_agent(
            NODE_REVIEWER,
            {"workflow_status": STATUS_REVIEW_COMPLETE, "review_report": approved_review},
        ),
        NODE_DEVOPS: _make_dummy_agent(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: _make_dummy_agent(
            NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}
        ),
        NODE_FINAL_PROJECT: _make_dummy_agent(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


# ===========================================================================
# 1. QA Failure Loop: QA fail -> Debug -> Backend -> Frontend -> QA pass -> Reviewer
# ===========================================================================


def test_graph_debug_qa_failure_loop():
    """QA failure triggers DebugAgent, which fixes code and routes to Backend -> Frontend -> QA pass."""
    client = FakeLLMClient(responses=[_sample_debug_resolution_dict("BackendAgent")])
    sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(passed=True, exit_code=0, output="1 passed")
        ]
    )
    debug_agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=sandbox,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_DEBUG] = debug_agent.invoke

    qa_invocations = 0

    def dynamic_qa(state: EngineeringState):
        nonlocal qa_invocations
        qa_invocations += 1
        if qa_invocations == 1:
            # First pass: QA fails, records error, triggers debug_required
            err = ErrorRecord(
                error_id="err-qa-fail",
                agent="QAAgent",
                stage=STATUS_DEBUG_REQUIRED,
                error_type="other",
                message="test_failure: AssertionError in test_auth.py",
                occurred_at="2026-01-01T00:00:00Z",
                resolved=False,
            )
            return {
                "workflow_status": STATUS_DEBUG_REQUIRED,
                "next_agent": "DebugAgent",
                "current_agent": "QAAgent",
                "errors": [err],
                "current_debug_target": "err-qa-fail",
            }
        else:
            # Second pass after fix: QA passes
            return {
                "workflow_status": STATUS_QA_COMPLETE,
                "next_agent": "ReviewerAgent",
                "current_agent": "QAAgent",
            }

    agents[NODE_QA] = dynamic_qa

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DebugQAFailApp", user_input="Build app")
    config = {"configurable": {"thread_id": "debug-thread-1"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["total_debug_cycles"] == 1
    assert final_state["code_artifacts"]["app/auth.py"].version == 2
    assert final_state["code_artifacts"]["app/auth.py"].produced_by == "DebugAgent"

    # All errors must be resolved (Decision 1 & unresolved_errors helper)
    assert unresolved_errors(final_state["errors"]) == []


# ===========================================================================
# 2. Reviewer requires_changes Loop: Reviewer requires_changes -> Debug -> Backend -> Reviewer pass
# ===========================================================================


def test_graph_debug_reviewer_requires_changes_loop():
    """Reviewer requires_changes routes to DebugAgent, which resolves and routes to Backend -> Reviewer approved."""
    client = FakeLLMClient(responses=[_sample_debug_resolution_dict("BackendAgent")])
    debug_agent = DebugAgent(model_name="test-model", llm_client=client)

    agents = _build_pipeline_stubs()
    agents[NODE_DEBUG] = debug_agent.invoke

    reviewer_invocations = 0

    def dynamic_reviewer(state: EngineeringState):
        nonlocal reviewer_invocations
        reviewer_invocations += 1
        if reviewer_invocations == 1:
            review_report = ReviewReport(
                overall_status="requires_changes",
                code_quality_score=6.0,
                architecture_compliance=True,
                engineering_standards_compliance=False,
                security_findings=["Missing auth check"],
                performance_findings=[],
                maintainability_findings=[],
                recommendations=["Fix auth check in app/auth.py"],
                testing_coverage_assessment="Good",
                produced_by="ReviewerAgent",
                produced_at="2026-01-01T00:00:00Z",
                version=1,
            )
            err = ErrorRecord(
                error_id="err-rev-1",
                agent="ReviewerAgent",
                stage=STATUS_DEBUG_REQUIRED,
                error_type="other",
                message="reviewer_requires_changes: Fix auth check",
                occurred_at="2026-01-01T00:00:00Z",
                resolved=False,
            )
            return {
                "workflow_status": STATUS_DEBUG_REQUIRED,
                "next_agent": "DebugAgent",
                "current_agent": "ReviewerAgent",
                "review_report": review_report,
                "errors": [err],
                "current_debug_target": "err-rev-1",
            }
        else:
            review_report = ReviewReport(
                overall_status="approved",
                code_quality_score=9.5,
                architecture_compliance=True,
                engineering_standards_compliance=True,
                produced_by="ReviewerAgent",
                produced_at="2026-01-01T00:00:00Z",
                version=2,
            )
            return {
                "workflow_status": STATUS_REVIEW_COMPLETE,
                "next_agent": "DevOpsAgent",
                "current_agent": "ReviewerAgent",
                "review_report": review_report,
            }

    agents[NODE_REVIEWER] = dynamic_reviewer

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DebugReviewerApp", user_input="Build app")
    config = {"configurable": {"thread_id": "debug-thread-2"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["total_debug_cycles"] == 1
    assert final_state["code_artifacts"]["app/auth.py"].version == 2
    assert unresolved_errors(final_state["errors"]) == []


# ===========================================================================
# 3. Global Cap Loop Termination
# ===========================================================================


def test_graph_debug_cap_termination():
    """When total_debug_cycles reaches max_debug_cycles, global_cap_handler terminates with failed status."""
    # DebugAgent resolves each error and increments cycles
    # LLM responses for multiple cycles
    client = FakeLLMClient(
        responses=[
            _sample_debug_resolution_dict("BackendAgent"),
            _sample_debug_resolution_dict("BackendAgent"),
            _sample_debug_resolution_dict("BackendAgent"),
        ]
    )
    debug_agent = DebugAgent(model_name="test-model", llm_client=client)

    agents = _build_pipeline_stubs()
    agents[NODE_DEBUG] = debug_agent.invoke

    # QA continually fails tests, forcing re-entry to Debug
    def failing_qa(state: EngineeringState):
        err = ErrorRecord(
            error_id=f"err-qa-{state.get('total_debug_cycles', 0)}",
            agent="QAAgent",
            stage=STATUS_DEBUG_REQUIRED,
            error_type="other",
            message="test_failure: Continuous failure",
            occurred_at="2026-01-01T00:00:00Z",
            resolved=False,
        )
        return {
            "workflow_status": STATUS_DEBUG_REQUIRED,
            "next_agent": "DebugAgent",
            "current_agent": "QAAgent",
            "errors": [err],
        }

    agents[NODE_QA] = failing_qa

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="DebugCapApp", user_input="Build app")
    # Set cap to 2 for fast test execution
    initial_state["max_debug_cycles"] = 2
    config = {"configurable": {"thread_id": "debug-thread-cap"}}

    final_state = graph.invoke(initial_state, config=config)

    # Terminated by global_cap_handler with failed status
    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["total_debug_cycles"] >= 2
    # Verify ErrorRecord added by global_cap_handler
    cap_errors = [e for e in final_state["errors"] if e.agent == "Orchestrator"]
    assert len(cap_errors) == 1
    assert "Global debug cycle cap reached" in cap_errors[0].message


# ===========================================================================
# 4. Exhaustion -> Human Approval -> Resume (Approve vs Reject)
# ===========================================================================


def test_graph_debug_exhaustion_approval_and_rejection():
    """DebugAgent exhaustion triggers HumanApprovalNode pause; resume with approve completes, reject fails."""
    # DebugAgent encounters 3 unrecoverable failures initially, then succeeds on resumed attempt
    client_a = FakeLLMClient(
        responses=[
            RuntimeError("LLM fail 1"),
            RuntimeError("LLM fail 2"),
            RuntimeError("LLM fail 3"),
            # Resumed attempt after approval: succeeds
            _sample_debug_resolution_dict("BackendAgent"),
        ]
    )
    debug_agent_a = DebugAgent(
        model_name="test-model",
        llm_client=client_a,
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_DEBUG] = debug_agent_a.invoke

    qa_invocations_a = 0

    def qa_fails_once(state: EngineeringState):
        nonlocal qa_invocations_a
        qa_invocations_a += 1
        if qa_invocations_a == 1:
            err = ErrorRecord(
                error_id="err-qa-exhaust",
                agent="QAAgent",
                stage=STATUS_DEBUG_REQUIRED,
                error_type="other",
                message="test_failure",
                occurred_at="2026-01-01T00:00:00Z",
                resolved=False,
            )
            return {
                "workflow_status": STATUS_DEBUG_REQUIRED,
                "next_agent": "DebugAgent",
                "current_agent": "QAAgent",
                "errors": [err],
                "current_debug_target": "err-qa-exhaust",
            }
        else:
            return {
                "workflow_status": STATUS_QA_COMPLETE,
                "next_agent": "ReviewerAgent",
                "current_agent": "QAAgent",
            }

    agents_a[NODE_QA] = qa_fails_once

    # --- Subcase A: Human approves resume ---
    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "debug-exhaust-approve"}}
    state_a = create_initial_state(project_name="DebugExhaustApp", user_input="Build app")

    # Run until pause
    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snapshot_a = graph_a.get_state(config_a)
    assert snapshot_a.values["human_approval_required"] is True
    assert snapshot_a.values["next_agent"] == "DebugAgent"

    # Resume with approval
    approval_command = Command(
        resume={
            "decision": "approved",
            "notes": "Approved retry after debugging failure",
        }
    )
    for _ in graph_a.stream(approval_command, config=config_a):
        pass

    final_snapshot_a = graph_a.get_state(config_a)
    assert final_snapshot_a.values["workflow_status"] == STATUS_COMPLETED

    # --- Subcase B: Human rejects ---
    client_b = FakeLLMClient(
        responses=[
            RuntimeError("LLM fail 1"),
            RuntimeError("LLM fail 2"),
            RuntimeError("LLM fail 3"),
        ]
    )
    debug_agent_b = DebugAgent(
        model_name="test-model",
        llm_client=client_b,
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    qa_invocations_b = 0

    def qa_fails_b(state: EngineeringState):
        nonlocal qa_invocations_b
        qa_invocations_b += 1
        if qa_invocations_b == 1:
            err = ErrorRecord(
                error_id="err-qa-exhaust-b",
                agent="QAAgent",
                stage=STATUS_DEBUG_REQUIRED,
                error_type="other",
                message="test_failure",
                occurred_at="2026-01-01T00:00:00Z",
                resolved=False,
            )
            return {
                "workflow_status": STATUS_DEBUG_REQUIRED,
                "next_agent": "DebugAgent",
                "current_agent": "QAAgent",
                "errors": [err],
                "current_debug_target": "err-qa-exhaust-b",
            }
        else:
            return {
                "workflow_status": STATUS_QA_COMPLETE,
                "next_agent": "ReviewerAgent",
                "current_agent": "QAAgent",
            }

    agents_b = _build_pipeline_stubs()
    agents_b[NODE_DEBUG] = debug_agent_b.invoke
    agents_b[NODE_QA] = qa_fails_b

    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "debug-exhaust-reject"}}
    state_b = create_initial_state(project_name="DebugExhaustRejectApp", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    snapshot_b = graph_b.get_state(config_b)
    assert snapshot_b.values["human_approval_required"] is True

    rejection_command = Command(
        resume={
            "decision": "rejected",
            "notes": "Rejected by human operator",
            "feedback": "Abort pipeline",
        }
    )
    for _ in graph_b.stream(rejection_command, config=config_b):
        pass

    final_snapshot_b = graph_b.get_state(config_b)
    assert final_snapshot_b.values["workflow_status"] == STATUS_FAILED


# ===========================================================================
# 5. No Duplicate Errors After Resolution (Decision 1)
# ===========================================================================


def test_graph_debug_no_duplicate_errors_after_resolution():
    """Verifies that after DebugAgent resolves an error by appending, unresolved_errors contains no duplicates."""
    client = FakeLLMClient(responses=[_sample_debug_resolution_dict("BackendAgent")])
    debug_agent = DebugAgent(model_name="test-model", llm_client=client)

    err1 = ErrorRecord(
        error_id="err-101",
        agent="QAAgent",
        stage=STATUS_DEBUG_REQUIRED,
        error_type="other",
        message="test_failure: 101",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )
    err2 = ErrorRecord(
        error_id="err-102",
        agent="BackendAgent",
        stage="backend",
        error_type="unhandled_exception",
        message="exception: 102",
        occurred_at="2026-01-01T00:01:00Z",
        resolved=False,
    )

    state = create_initial_state(project_name="DebugDupsApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_code_artifacts()
    state["errors"] = [err1, err2]
    state["current_debug_target"] = "err-101"

    update = debug_agent.invoke(state)

    # When merged via append_reducer:
    merged_errors = state["errors"] + update["errors"]
    # Total records in list is 3 (err1 open, err2 open, err1 resolved)
    assert len(merged_errors) == 3

    # But unresolved_errors filters to only the unresolved ones (err2 only!)
    unresolved = unresolved_errors(merged_errors)
    assert len(unresolved) == 1
    assert unresolved[0].error_id == "err-102"
    assert unresolved[0].resolved is False
