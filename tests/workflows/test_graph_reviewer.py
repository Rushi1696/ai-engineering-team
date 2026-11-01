"""
tests/workflows/test_graph_reviewer.py

Integration tests verifying real ReviewerAgent.invoke execution within
the compiled StateGraph topology from workflows/graph.py.
"""

from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.debug_agent import DebugAgent
from agents.reviewer_agent import ReviewerAgent
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
    TestPlan,
)

TestArtifact.__test__ = False
TestPlan.__test__ = False
from state.reducers import unresolved_errors
from tools.debug.sandbox import FakeExecutionSandbox, SandboxExecutionResult
from tools.reviewer.coverage_reader import FakeCoverageReader
from tools.reviewer.static_analyzer import FakeStaticAnalyzer
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
    STATUS_INITIATED,
    STATUS_PLANNING_COMPLETE,
    STATUS_QA_COMPLETE,
    STATUS_REQUIREMENTS_COMPLETE,
    STATUS_REVIEW_COMPLETE,
)
from workflows.graph import build_graph


# ===========================================================================
# Test Data Fixtures
# ===========================================================================


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["User can authenticate", "User can submit orders"],
        non_functional_requirements=["Response time under 200ms", "99.9% uptime"],
        user_stories=["As a user I want to sign up so that I can buy items"],
        acceptance_criteria=["Given valid credentials, return 200 and auth token"],
        constraints=["Python 3.11+", "PostgreSQL database"],
        business_goals=["Increase customer conversion rate by 15%"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture() -> SystemArchitecture:
    return SystemArchitecture(
        overview="Microservices-based event-driven architecture.",
        technology_stack={
            "backend": "Python 3.11 / FastAPI",
            "frontend": "React 18 / TypeScript",
            "database": "PostgreSQL 16",
        },
        api_specifications=[
            {
                "endpoint": "/api/v1/auth/login",
                "method": "POST",
                "description": "Authenticate user and issue JWT",
            }
        ],
        components=[{"name": "AuthService", "description": "Authentication"}],
        data_models=[{"name": "User", "fields": {"id": "str", "email": "str"}}],
        security_considerations=["JWT auth", "Bcrypt hashing"],
        scalability_considerations=["Stateless API instances"],
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


def _sample_valid_test_plan() -> TestPlan:
    return TestPlan(
        strategy="Unit and integration tests",
        coverage_goals={"app/auth.py": "95%"},
        test_suites=["AuthSuite"],
        quality_metrics={"pass_rate": 100.0},
        produced_by="QAAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "app/auth.py": CodeArtifact(
            file_path="app/auth.py",
            language="python",
            content="def login(): return 200\n",
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


def _sample_reviewer_output_dict(
    overall_status: str = "approved",
    rejection_scope: str | None = None,
    code_quality_score: float = 9.0,
) -> Dict[str, Any]:
    return {
        "overall_status": overall_status,
        "code_quality_score": code_quality_score,
        "architecture_compliance": True,
        "engineering_standards_compliance": True,
        "security_findings": [],
        "performance_findings": [],
        "maintainability_findings": [],
        "recommendations": ["Ensure thorough logging"],
        "testing_coverage_assessment": "95% coverage meets target",
        "rejection_scope": rejection_scope,
        "summary": f"Review finished with status {overall_status}",
    }


def _sample_debug_resolution_dict(target_agent: str = "BackendAgent") -> Dict[str, Any]:
    return {
        "root_cause_analysis": "Fixing code per reviewer findings.",
        "hypothesis": "Patching app/auth.py satisfies review recommendations.",
        "corrected_code_artifacts": [
            {
                "file_path": "app/auth.py",
                "language": "python",
                "content": "def login(): return 200  # Patched\n",
                "artifact_type": "backend",
                "produced_by": "DebugAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "corrected_test_artifacts": [],
        "target_agent": target_agent,
        "resolution_notes": "Applied requested review fixes.",
    }


def _make_dummy_agent(name: str, output: Dict[str, Any]):
    def _agent(state: EngineeringState):
        ret = dict(output)
        ret["current_agent"] = name
        return ret

    return _agent


def _build_pipeline_stubs() -> Dict[str, Any]:
    return {
        NODE_PRODUCT_MANAGER: _make_dummy_agent(
            NODE_PRODUCT_MANAGER,
            {
                "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
                "requirements": _sample_valid_requirements(),
            },
        ),
        NODE_ARCHITECT: _make_dummy_agent(
            NODE_ARCHITECT,
            {
                "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
                "architecture": _sample_valid_architecture(),
            },
        ),
        NODE_TASK_PLANNER: _make_dummy_agent(
            NODE_TASK_PLANNER,
            {
                "workflow_status": STATUS_PLANNING_COMPLETE,
                "task_plan": _sample_valid_task_plan(),
            },
        ),
        NODE_BACKEND: _make_dummy_agent(
            NODE_BACKEND,
            {
                "workflow_status": STATUS_BACKEND_COMPLETE,
                "code_artifacts": _sample_code_artifacts(),
            },
        ),
        NODE_FRONTEND: _make_dummy_agent(
            NODE_FRONTEND,
            {"workflow_status": STATUS_FRONTEND_COMPLETE},
        ),
        NODE_QA: _make_dummy_agent(
            NODE_QA,
            {
                "workflow_status": STATUS_QA_COMPLETE,
                "test_artifacts": _sample_test_artifacts(),
                "test_plan": _sample_valid_test_plan(),
                "next_agent": "ReviewerAgent",
            },
        ),
        NODE_DEVOPS: _make_dummy_agent(NODE_DEVOPS, {"workflow_status": STATUS_DEVOPS_COMPLETE}),
        NODE_DOCUMENTATION: _make_dummy_agent(
            NODE_DOCUMENTATION, {"workflow_status": STATUS_DOCUMENTATION_COMPLETE}
        ),
        NODE_FINAL_PROJECT: _make_dummy_agent(NODE_FINAL_PROJECT, {"workflow_status": STATUS_COMPLETED}),
        NODE_DEBUG: _make_dummy_agent(NODE_DEBUG, {}),
    }


# ===========================================================================
# 1. Real Graph: Reviewer Approved Path
# ===========================================================================


def test_graph_reviewer_approved():
    """Reviewer approves code; routes to DevOps -> Docs -> FinalProject -> Completed."""
    client = FakeLLMClient(responses=[_sample_reviewer_output_dict("approved", code_quality_score=9.5)])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    reviewer = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_REVIEWER] = reviewer.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ReviewApp", user_input="Build app")
    config = {"configurable": {"thread_id": "reviewer-approved-1"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["review_report"].overall_status == "approved"
    assert final_state["review_report"].code_quality_score == 9.5
    assert final_state["forward_stage"] >= 7
    assert "review_complete" in final_state["checkpoints"]


# ===========================================================================
# 2. Real Graph: Reviewer requires_changes Loop with Real DebugAgent
# ===========================================================================


def test_graph_reviewer_requires_changes_loop():
    """Reviewer requires_changes triggers DebugAgent, which patches code; second review approves."""
    reviewer_client = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("requires_changes", code_quality_score=6.0),
            _sample_reviewer_output_dict("approved", code_quality_score=9.5),
        ]
    )
    reviewer = ReviewerAgent(
        model_name="test-model",
        llm_client=reviewer_client,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )

    debug_client = FakeLLMClient(responses=[_sample_debug_resolution_dict("BackendAgent")])
    debug_agent = DebugAgent(
        model_name="test-model",
        llm_client=debug_client,
        sandbox=FakeExecutionSandbox(
            results=[SandboxExecutionResult(passed=True, exit_code=0, output="OK")]
        ),
    )

    agents = _build_pipeline_stubs()
    agents[NODE_REVIEWER] = reviewer.invoke
    agents[NODE_DEBUG] = debug_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="ReviewDebugLoopApp", user_input="Build app")
    config = {"configurable": {"thread_id": "reviewer-debug-loop-1"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_COMPLETED
    assert final_state["total_debug_cycles"] == 1
    assert final_state["current_debug_target"] == ""
    assert final_state["review_report"].overall_status == "approved"
    assert final_state["review_report"].version == 2
    assert unresolved_errors(final_state["errors"]) == []


# ===========================================================================
# 3. Real Graph: requires_changes Loop Ending at Debug Cycle Cap
# ===========================================================================


def test_graph_reviewer_requires_changes_loop_cap_termination():
    """Reviewer constantly requires_changes; loop terminates via global_cap_handler at max_debug_cycles."""
    # Reviewer always requires changes
    reviewer_client = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("requires_changes", code_quality_score=5.0),
            _sample_reviewer_output_dict("requires_changes", code_quality_score=5.0),
            _sample_reviewer_output_dict("requires_changes", code_quality_score=5.0),
        ]
    )
    reviewer = ReviewerAgent(
        model_name="test-model",
        llm_client=reviewer_client,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )

    debug_client = FakeLLMClient(
        responses=[
            _sample_debug_resolution_dict("BackendAgent"),
            _sample_debug_resolution_dict("BackendAgent"),
            _sample_debug_resolution_dict("BackendAgent"),
        ]
    )
    debug_agent = DebugAgent(
        model_name="test-model",
        llm_client=debug_client,
        sandbox=FakeExecutionSandbox(),
    )

    agents = _build_pipeline_stubs()
    agents[NODE_REVIEWER] = reviewer.invoke
    agents[NODE_DEBUG] = debug_agent.invoke

    graph = build_graph(agents, checkpointer=MemorySaver())
    initial_state = create_initial_state(project_name="CapLoopApp", user_input="Build app")
    initial_state["max_debug_cycles"] = 2  # Cap at 2 cycles
    config = {"configurable": {"thread_id": "reviewer-cap-thread"}}

    final_state = graph.invoke(initial_state, config=config)

    assert final_state["workflow_status"] == STATUS_FAILED
    assert final_state["total_debug_cycles"] == 2


# ===========================================================================
# 4. Real Graph: Reviewer Rejected at Each of the 3 Scopes (Approve and Reject)
# ===========================================================================


def test_graph_reviewer_rejected_code_level_approve_and_reject():
    """Reviewer rejected with code_level scope: approve routes to DebugAgent; reject fails to END."""
    # --- Subcase A: Human approves ---
    # Reviewer rejects on pass 1, approved on pass 2 (after Debug fix)
    rev_client_a = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="code_level", code_quality_score=4.0),
            _sample_reviewer_output_dict("approved", code_quality_score=9.0),
        ]
    )
    reviewer_a = ReviewerAgent(
        model_name="test-model",
        llm_client=rev_client_a,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )
    debug_client_a = FakeLLMClient(responses=[_sample_debug_resolution_dict("BackendAgent")])
    debug_a = DebugAgent(
        model_name="test-model",
        llm_client=debug_client_a,
        sandbox=FakeExecutionSandbox(),
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_REVIEWER] = reviewer_a.invoke
    agents_a[NODE_DEBUG] = debug_a.invoke

    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "rejected-code-approve"}}
    state_a = create_initial_state(project_name="CodeApp", user_input="Build app")

    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snap_a = graph_a.get_state(config_a)
    assert snap_a.values["human_approval_required"] is True
    assert snap_a.values["rejection_scope"] == "code_level"
    assert snap_a.values["next_agent"] == "DebugAgent"

    # Human approves escalation -> routes to DebugAgent -> completes
    for _ in graph_a.stream(Command(resume={"decision": "approved"}), config=config_a):
        pass

    final_a = graph_a.get_state(config_a).values
    assert final_a["workflow_status"] == STATUS_COMPLETED

    # --- Subcase B: Human rejects ---
    rev_client_b = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="code_level", code_quality_score=3.0)
        ]
    )
    reviewer_b = ReviewerAgent(
        model_name="test-model",
        llm_client=rev_client_b,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )
    agents_b = _build_pipeline_stubs()
    agents_b[NODE_REVIEWER] = reviewer_b.invoke
    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "rejected-code-reject"}}
    state_b = create_initial_state(project_name="CodeApp2", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    for _ in graph_b.stream(Command(resume={"decision": "rejected"}), config=config_b):
        pass

    final_b = graph_b.get_state(config_b).values
    assert final_b["workflow_status"] == STATUS_FAILED


def test_graph_reviewer_rejected_design_level_approve_and_reject():
    """Reviewer rejected with design_level scope: approve routes to ArchitectAgent; reject fails to END."""
    # Subcase A: Human approves -> routes to ArchitectAgent
    architect_invocations = 0

    def dynamic_architect(state: EngineeringState):
        nonlocal architect_invocations
        architect_invocations += 1
        return {
            "workflow_status": STATUS_ARCHITECTURE_COMPLETE,
            "architecture": _sample_valid_architecture(),
            "current_agent": "ArchitectAgent",
        }

    rev_client_a = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="design_level", code_quality_score=4.0),
            _sample_reviewer_output_dict("approved", code_quality_score=9.0),
        ]
    )
    reviewer_a = ReviewerAgent(
        model_name="test-model",
        llm_client=rev_client_a,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_REVIEWER] = reviewer_a.invoke
    agents_a[NODE_ARCHITECT] = dynamic_architect

    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "rejected-design-approve"}}
    state_a = create_initial_state(project_name="DesignApp", user_input="Build app")

    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snap_a = graph_a.get_state(config_a)
    assert snap_a.values["human_approval_required"] is True
    assert snap_a.values["rejection_scope"] == "design_level"
    assert snap_a.values["next_agent"] == "ArchitectAgent"

    for _ in graph_a.stream(Command(resume={"decision": "approved"}), config=config_a):
        pass

    final_a = graph_a.get_state(config_a).values
    assert architect_invocations == 2  # Once initially, once on redesign
    assert final_a["workflow_status"] == STATUS_COMPLETED

    # Subcase B: Human rejects -> fails to END
    rev_client_b = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="design_level", code_quality_score=3.0)
        ]
    )
    reviewer_b = ReviewerAgent(
        model_name="test-model",
        llm_client=rev_client_b,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )
    agents_b = _build_pipeline_stubs()
    agents_b[NODE_REVIEWER] = reviewer_b.invoke
    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "rejected-design-reject"}}
    state_b = create_initial_state(project_name="DesignApp2", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    for _ in graph_b.stream(Command(resume={"decision": "rejected"}), config=config_b):
        pass

    final_b = graph_b.get_state(config_b).values
    assert final_b["workflow_status"] == STATUS_FAILED


def test_graph_reviewer_rejected_requirements_level_approve_and_reject():
    """Reviewer rejected with requirements_level: approve routes to ProductManagerAgent; reject fails to END."""
    pm_invocations = 0

    def dynamic_pm(state: EngineeringState):
        nonlocal pm_invocations
        pm_invocations += 1
        return {
            "workflow_status": STATUS_REQUIREMENTS_COMPLETE,
            "requirements": _sample_valid_requirements(),
            "current_agent": "ProductManagerAgent",
        }

    rev_client_a = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="requirements_level", code_quality_score=3.0),
            _sample_reviewer_output_dict("approved", code_quality_score=9.0),
        ]
    )
    reviewer_a = ReviewerAgent(
        model_name="test-model",
        llm_client=rev_client_a,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_REVIEWER] = reviewer_a.invoke
    agents_a[NODE_PRODUCT_MANAGER] = dynamic_pm

    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "rejected-reqs-approve"}}
    state_a = create_initial_state(project_name="ReqsApp", user_input="Build app")

    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snap_a = graph_a.get_state(config_a)
    assert snap_a.values["human_approval_required"] is True
    assert snap_a.values["rejection_scope"] == "requirements_level"
    assert snap_a.values["next_agent"] == "ProductManagerAgent"

    for _ in graph_a.stream(Command(resume={"decision": "approved"}), config=config_a):
        pass

    final_a = graph_a.get_state(config_a).values
    assert pm_invocations == 2  # Once initially, once restarted
    assert final_a["workflow_status"] == STATUS_COMPLETED

    # Subcase B: Human rejects -> fails to END
    rev_client_b = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="requirements_level", code_quality_score=2.0)
        ]
    )
    reviewer_b = ReviewerAgent(
        model_name="test-model",
        llm_client=rev_client_b,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
    )
    agents_b = _build_pipeline_stubs()
    agents_b[NODE_REVIEWER] = reviewer_b.invoke
    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "rejected-reqs-reject"}}
    state_b = create_initial_state(project_name="ReqsApp2", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    for _ in graph_b.stream(Command(resume={"decision": "rejected"}), config=config_b):
        pass

    final_b = graph_b.get_state(config_b).values
    assert final_b["workflow_status"] == STATUS_FAILED


# ===========================================================================
# 5. Real Graph: Invalid Scope Retry
# ===========================================================================


def test_graph_reviewer_invalid_scope_retry():
    """Reviewer LLM returns invalid rejection scope, internally retries and succeeds with valid scope."""
    client = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="invalid_scope", code_quality_score=4.0),
            _sample_reviewer_output_dict("rejected", rejection_scope="code_level", code_quality_score=4.0),
        ]
    )
    reviewer = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
        sleep_fn=lambda s: None,
    )

    agents = _build_pipeline_stubs()
    agents[NODE_REVIEWER] = reviewer.invoke

    checkpointer = MemorySaver()
    graph = build_graph(agents, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "reviewer-scope-retry"}}
    state = create_initial_state(project_name="ScopeRetryApp", user_input="Build app")

    for _ in graph.stream(state, config=config):
        pass

    snapshot = graph.get_state(config)
    assert snapshot.values["human_approval_required"] is True
    assert snapshot.values["rejection_scope"] == "code_level"
    assert snapshot.values["next_agent"] == "DebugAgent"
    assert len(client.calls) == 2


# ===========================================================================
# 6. Real Graph: Exhaustion Then Approve and Reject
# ===========================================================================


def test_graph_reviewer_exhaustion_approval_and_rejection():
    """ReviewerAgent exhaustion triggers HumanApprovalNode; approve retries and passes; reject fails."""
    # Subcase A: Human approves retry
    client_a = FakeLLMClient(
        responses=[
            RuntimeError("Reviewer fail 1"),
            RuntimeError("Reviewer fail 2"),
            RuntimeError("Reviewer fail 3"),
            # Resumed run succeeds
            _sample_reviewer_output_dict("approved", code_quality_score=9.0),
        ]
    )
    reviewer_a = ReviewerAgent(
        model_name="test-model",
        llm_client=client_a,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_a = _build_pipeline_stubs()
    agents_a[NODE_REVIEWER] = reviewer_a.invoke

    checkpointer_a = MemorySaver()
    graph_a = build_graph(agents_a, checkpointer=checkpointer_a)
    config_a = {"configurable": {"thread_id": "reviewer-exhaust-approve"}}
    state_a = create_initial_state(project_name="ExhaustApp", user_input="Build app")

    for _ in graph_a.stream(state_a, config=config_a):
        pass

    snapshot_a = graph_a.get_state(config_a)
    assert snapshot_a.values["human_approval_required"] is True
    assert snapshot_a.values["next_agent"] == "ReviewerAgent"
    assert snapshot_a.values["retry_counts"]["ReviewerAgent"] == 3

    # Human approves retry
    for _ in graph_a.stream(Command(resume={"decision": "approved"}), config=config_a):
        pass

    final_a = graph_a.get_state(config_a).values
    assert final_a["workflow_status"] == STATUS_COMPLETED
    assert final_a["retry_counts"]["ReviewerAgent"] == 0

    # Subcase B: Human rejects
    client_b = FakeLLMClient(
        responses=[
            RuntimeError("Reviewer fail 1"),
            RuntimeError("Reviewer fail 2"),
            RuntimeError("Reviewer fail 3"),
        ]
    )
    reviewer_b = ReviewerAgent(
        model_name="test-model",
        llm_client=client_b,
        static_analyzer=FakeStaticAnalyzer(),
        coverage_reader=FakeCoverageReader(),
        max_retries=3,
        sleep_fn=lambda s: None,
    )

    agents_b = _build_pipeline_stubs()
    agents_b[NODE_REVIEWER] = reviewer_b.invoke

    checkpointer_b = MemorySaver()
    graph_b = build_graph(agents_b, checkpointer=checkpointer_b)
    config_b = {"configurable": {"thread_id": "reviewer-exhaust-reject"}}
    state_b = create_initial_state(project_name="ExhaustApp2", user_input="Build app")

    for _ in graph_b.stream(state_b, config=config_b):
        pass

    for _ in graph_b.stream(Command(resume={"decision": "rejected"}), config=config_b):
        pass

    final_b = graph_b.get_state(config_b).values
    assert final_b["workflow_status"] == STATUS_FAILED
