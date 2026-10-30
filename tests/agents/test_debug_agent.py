"""
tests/agents/test_debug_agent.py

Comprehensive unit and behavioral tests for DebugAgent in agents/debug_agent.py.
"""

from typing import Any, Dict, List, Optional
import pytest

from agents.debug_agent import DebugAgent, DebugResolution
from state.engineering_state import create_initial_state
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
    t1 = ImplementationTask(
        task_id="TASK-001",
        title="Implement Auth",
        description="Write auth endpoints",
        assigned_agent="BackendAgent",
        priority=1,
        status="completed",
        dependencies=[],
        acceptance_criteria=["Login returns 200"],
    )
    return TaskPlan(
        tasks=[t1],
        execution_order=["TASK-001"],
        milestones=[{"name": "M1", "task_ids": ["TASK-001"]}],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "src/auth.py": CodeArtifact(
            file_path="src/auth.py",
            language="python",
            content="def login(): return 500  # Bug here",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_valid_test_artifacts() -> Dict[str, TestArtifact]:
    return {
        "tests/test_auth.py": TestArtifact(
            file_path="tests/test_auth.py",
            content="def test_login(): assert login() == 200",
            test_type="unit",
            coverage_target="src/auth.py",
            produced_by="QAAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_valid_resolution_dict() -> Dict[str, Any]:
    return {
        "root_cause_analysis": "login() returns HTTP 500 status code instead of 200 on valid credentials.",
        "hypothesis": "Changing returned status code to 200 resolves the assertion failure.",
        "corrected_code_artifacts": [
            {
                "file_path": "src/auth.py",
                "language": "python",
                "content": "def login(): return 200  # Fixed",
                "artifact_type": "backend",
                "produced_by": "DebugAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "corrected_test_artifacts": [],
        "target_agent": "BackendAgent",
        "resolution_notes": "Fixed status code in src/auth.py to return 200.",
    }


# ===========================================================================
# 1. Constructor Validation
# ===========================================================================


def test_debug_agent_constructor_validation():
    """DebugAgent constructor validates model_name and llm_client."""
    client = FakeLLMClient()

    sandbox = FakeExecutionSandbox()

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        DebugAgent(model_name="", llm_client=client, sandbox=sandbox)

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        DebugAgent(model_name="   ", llm_client=client, sandbox=sandbox)

    with pytest.raises(ValueError, match="llm_client must not be None"):
        DebugAgent(model_name="test-model", llm_client=None, sandbox=FakeExecutionSandbox())

    with pytest.raises(ValueError, match="sandbox must not be None"):
        DebugAgent(model_name="test-model", llm_client=client, sandbox=None)

    # Defaults for log_parser
    sandbox = FakeExecutionSandbox()
    agent = DebugAgent(model_name="test-model", llm_client=client, sandbox=sandbox)
    assert agent.sandbox is sandbox
    assert agent.log_parser is not None
    assert agent.agent_name == "DebugAgent"


# ===========================================================================
# 2. QA Test Failure Entry Path & Fix Verification
# ===========================================================================


def test_debug_agent_resolves_qa_failure():
    """DebugAgent resolves a QA test failure, increments cycles, and clears current_debug_target."""
    client = FakeLLMClient(responses=[_sample_valid_resolution_dict()])
    sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=True,
                exit_code=0,
                output="1 passed in 0.01s",
            )
        ]
    )
    log_parser = FakeLogParser(
        results=[
            ParsedErrorLog(
                failing_files=["src/auth.py", "tests/test_auth.py"],
                exception_type="AssertionError",
                error_message="test_failure: 500 != 200",
                stack_frames=['File "tests/test_auth.py", line 1 in test_login'],
            )
        ]
    )

    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=sandbox,
        log_parser=log_parser,
    )

    err = ErrorRecord(
        error_id="err-qa-1",
        agent="QAAgent",
        stage="debug_required",
        error_type="other",
        message="test_failure: failing tests: [tests/test_auth.py]. Output: 500 != 200",
        traceback='File "tests/test_auth.py", line 1 in test_login',
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["test_artifacts"] = _sample_valid_test_artifacts()
    state["errors"] = [err]
    state["current_debug_target"] = "err-qa-1"
    state["workflow_status"] = "debug_required"
    state["total_debug_cycles"] = 0

    result = agent.execute(state)

    # 1. Total debug cycles incremented by 1 (Decision 8)
    assert result["total_debug_cycles"] == 1

    # 2. current_debug_target cleared to "" (Decision 2)
    assert result["current_debug_target"] == ""

    # 3. workflow_status is left unchanged (Decision 4)
    assert "workflow_status" not in result

    # 4. next_agent is target_agent (BackendAgent) (Decision 4 & 5)
    assert result["next_agent"] == "BackendAgent"
    assert result["current_agent"] == "DebugAgent"

    # 5. Corrected code artifact version incremented from 1 to 2
    assert "src/auth.py" in result["code_artifacts"]
    assert result["code_artifacts"]["src/auth.py"].version == 2
    assert result["code_artifacts"]["src/auth.py"].produced_by == "DebugAgent"

    # 6. Errors list has resolved ErrorRecord appended (Decision 1)
    assert len(result["errors"]) == 1
    resolved_rec = result["errors"][0]
    assert resolved_rec.error_id == "err-qa-1"
    assert resolved_rec.resolved is True
    assert resolved_rec.resolution_agent == "DebugAgent"
    assert "Fixed status code" in resolved_rec.resolution_notes

    # Verify unresolved_errors helper returns empty when resolved copy is merged
    merged_errors = state["errors"] + result["errors"]
    assert unresolved_errors(merged_errors) == []

    # 7. Sandbox and LogParser calls recorded (Decision 7)
    assert len(log_parser.calls) == 1
    assert len(sandbox.calls) == 1


# ===========================================================================
# 3. Reviewer requires_changes Entry Path
# ===========================================================================


def test_debug_agent_resolves_reviewer_requires_changes():
    """DebugAgent ingests ReviewReport context and resolves requires_changes."""
    resolution = _sample_valid_resolution_dict()
    resolution["target_agent"] = "FrontendAgent"
    client = FakeLLMClient(responses=[resolution])
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Fix review")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["review_report"] = ReviewReport(
        overall_status="requires_changes",
        code_quality_score=6.5,
        architecture_compliance=True,
        engineering_standards_compliance=False,
        security_findings=["Missing input sanitization"],
        performance_findings=["Redundant loop in handler"],
        maintainability_findings=["Function exceeds 50 lines"],
        recommendations=["Sanitize inputs and simplify login logic"],
        testing_coverage_assessment="Adequate",
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )
    state["workflow_status"] = "debug_required"

    result = agent.execute(state)

    assert result["next_agent"] == "FrontendAgent"
    assert result["total_debug_cycles"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0].resolved is True
    assert result["errors"][0].agent == "ReviewerAgent"


# ===========================================================================
# 4. Sandbox Failure Routes Straight to Exhaustion (Item 4)
# ===========================================================================


def test_debug_agent_sandbox_failure_routes_to_exhaustion_without_retry():
    """Sandbox execution failure or exception goes straight to exhaustion without regenerating LLM."""
    client = FakeLLMClient(
        responses=[
            _sample_valid_resolution_dict(),
            _sample_valid_resolution_dict(),
        ]
    )
    sandbox = FakeExecutionSandbox(
        results=[
            RuntimeError("Sandbox container crashed"),
        ]
    )
    sleeps = []
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=sandbox,
        sleep_fn=sleeps.append,
    )

    err = ErrorRecord(
        error_id="err-1",
        agent="QAAgent",
        stage="debug_required",
        error_type="other",
        message="test_failure",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )
    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["errors"] = [err]

    result = agent.execute(state)

    # Exhaustion return triggered directly
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "DebugAgent"
    assert "total_debug_cycles" not in result
    assert "workflow_status" not in result
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "Sandbox container crashed" in result["errors"][0].message
    # LLM was only called once, NOT regenerated on sandbox failure
    assert len(client.calls) == 1
    assert len(sleeps) == 0


# ===========================================================================
# 5. total_debug_cycles Never Increments on Exhaustion (Decision 8)
# ===========================================================================


def test_debug_agent_total_debug_cycles_never_increments_on_exhaustion():
    """On retry exhaustion, total_debug_cycles is NOT incremented."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
        ]
    )
    sleeps = []
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
        sleep_fn=sleeps.append,
    )

    err = ErrorRecord(
        error_id="err-1",
        agent="QAAgent",
        stage="debug_required",
        error_type="other",
        message="test_failure",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )
    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["errors"] = [err]
    state["total_debug_cycles"] = 4
    state["workflow_status"] = "debug_required"

    result = agent.execute(state)

    # Exhaustion return triggered
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "DebugAgent"
    # total_debug_cycles must NOT be present/incremented on exhaustion (Decision 8)
    assert "total_debug_cycles" not in result
    # workflow_status is preserved unchanged
    assert "workflow_status" not in result
    assert result["retry_counts"]["DebugAgent"] == 3
    # 2 backoff sleeps for 3 attempts with RuntimeError
    assert len(sleeps) == 2


# ===========================================================================
# 6. Falsy current_debug_target Behavior (Decision 2)
# ===========================================================================


def test_debug_agent_falsy_current_debug_target_falls_back_to_last_unresolved():
    """Empty string current_debug_target is treated as falsy, selecting latest unresolved error."""
    client = FakeLLMClient(responses=[_sample_valid_resolution_dict()])
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
    )

    e1 = ErrorRecord(
        error_id="err-first",
        agent="BackendAgent",
        stage="backend",
        error_type="unhandled_exception",
        message="Older error",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )
    e2 = ErrorRecord(
        error_id="err-latest",
        agent="QAAgent",
        stage="qa",
        error_type="other",
        message="Latest test failure",
        occurred_at="2026-01-01T00:01:00Z",
        resolved=False,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["errors"] = [e1, e2]
    state["current_debug_target"] = ""  # Falsy empty string (Decision 2)

    result = agent.execute(state)

    # Targets the latest unresolved error (err-latest)
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_id == "err-latest"
    assert result["errors"][0].resolved is True


# ===========================================================================
# 7. Fast-Fail on Missing Inputs
# ===========================================================================


def test_debug_agent_fast_fails_when_no_unresolved_errors():
    """DebugAgent returns exhaustion when no unresolved errors or review findings exist."""
    client = FakeLLMClient()
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["errors"] = []

    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert "No unresolved errors or actionable review findings" in result["human_approval_notes"]
    assert "total_debug_cycles" not in result
    assert len(client.calls) == 0


# ===========================================================================
# 8. Lifecycle Invoke Test
# ===========================================================================


def test_debug_agent_invoke_lifecycle():
    """BaseAgent.invoke merges execution metadata and increments total_invocations."""
    client = FakeLLMClient(responses=[_sample_valid_resolution_dict()])
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
    )

    err = ErrorRecord(
        error_id="err-1",
        agent="QAAgent",
        stage="qa",
        error_type="other",
        message="failure",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )
    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["errors"] = [err]

    update = agent.invoke(state)

    assert update["total_invocations"] == 1
    assert update["execution_metadata"]["DebugAgent"].status == "completed"
    assert update["next_agent"] == "BackendAgent"


# ===========================================================================
# 9. Stale retry_exhausted Error Handling (Item 2)
# ===========================================================================


def test_debug_agent_skips_stale_retry_exhausted_errors():
    """DebugAgent ignores unresolved errors of type 'retry_exhausted', selecting real error."""
    client = FakeLLMClient(responses=[_sample_valid_resolution_dict()])
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
    )

    real_err = ErrorRecord(
        error_id="err-real-failure",
        agent="BackendAgent",
        stage="backend",
        error_type="other",
        message="Actual code failure",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )
    stale_exhaustion = ErrorRecord(
        error_id="err-stale-exhaustion",
        agent="BackendAgent",
        stage="backend",
        error_type="retry_exhausted",
        message="BackendAgent reached retry limit",
        occurred_at="2026-01-01T00:01:00Z",
        resolved=False,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["errors"] = [real_err, stale_exhaustion]
    state["current_debug_target"] = ""

    result = agent.execute(state)

    # Resolved the real error, skipping stale retry_exhausted
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_id == "err-real-failure"
    assert result["errors"][0].resolved is True


# ===========================================================================
# 10. Input State Unchanged (Item 7)
# ===========================================================================


def test_debug_agent_input_state_unchanged():
    """Executing DebugAgent must not mutate input state dictionary or its objects."""
    client = FakeLLMClient(responses=[_sample_valid_resolution_dict()])
    agent = DebugAgent(
        model_name="test-model",
        llm_client=client,
        sandbox=FakeExecutionSandbox(),
    )

    initial_plan = _sample_valid_task_plan()
    initial_code = _sample_valid_code_artifacts()
    err = ErrorRecord(
        error_id="err-qa-1",
        agent="QAAgent",
        stage="debug_required",
        error_type="other",
        message="test failure",
        occurred_at="2026-01-01T00:00:00Z",
        resolved=False,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Fix bug")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = initial_plan
    state["code_artifacts"] = initial_code
    state["errors"] = [err]
    state["current_debug_target"] = "err-qa-1"
    state["total_debug_cycles"] = 0

    result = agent.execute(state)

    # Output has version 2 and completed cycle
    assert result["code_artifacts"]["src/auth.py"].version == 2
    assert result["total_debug_cycles"] == 1

    # Input state is unchanged
    assert state["task_plan"] is initial_plan
    assert state["code_artifacts"]["src/auth.py"].version == 1
    assert state["errors"][0].resolved is False
    assert state["current_debug_target"] == "err-qa-1"
    assert state["total_debug_cycles"] == 0
