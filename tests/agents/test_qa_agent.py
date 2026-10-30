"""
tests/agents/test_qa_agent.py

Comprehensive unit tests for QAAgent in agents/qa_agent.py.
"""

from typing import Any, Dict
import pytest

from agents.qa_agent import QAAgent, QAResult, STATUS_DEBUG_REQUIRED, STATUS_QA_COMPLETE
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    ImplementationTask,
    ProductRequirements,
    SystemArchitecture,
    TaskPlan,
    TestArtifact,
    TestPlan,
)
from tools.qa.test_runner import FakeTestRunner, TestRunResult
from tools.shared.llm_client import FakeLLMClient

# Suppress PytestCollectionWarning on model classes
TestArtifact.__test__ = False
TestPlan.__test__ = False


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["User can authenticate", "User can submit orders"],
        non_functional_requirements=["Response time under 200ms", "99.9% uptime"],
        user_stories=["As a user I want to sign up so that I can buy items"],
        acceptance_criteria=["Given valid credentials, return 200 and auth token"],
        constraints=["React 18+", "Tailwind CSS"],
        business_goals=["Increase customer conversion rate by 15%"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture() -> SystemArchitecture:
    return SystemArchitecture(
        overview="React frontend communicating with FastAPI backend.",
        technology_stack={
            "frontend": "React 18 / Vite",
            "backend": "Python 3.11 / FastAPI",
        },
        api_specifications=[
            {
                "endpoint": "/api/v1/auth/login",
                "method": "POST",
                "description": "Authenticate user and issue JWT",
            },
        ],
        database_design={"users": {"id": "UUID PK"}},
        security_architecture={"auth": "JWT Bearer"},
        scalability_strategy="CDN caching and horizontal worker scaling.",
        deployment_model="Vercel + AWS ECS.",
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_task_plan() -> TaskPlan:
    return TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="TASK-001",
                title="Auth login endpoint",
                description="Backend API",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["Returns 200 and auth token"],
                status="completed",
            ),
            ImplementationTask(
                task_id="TASK-002",
                title="QA test suite",
                description="Write unit and integration tests",
                assigned_agent="QAAgent",
                priority=20,
                dependencies=["TASK-001"],
                acceptance_criteria=["Unit and integration tests pass"],
                status="pending",
            ),
        ],
        execution_order=["TASK-001", "TASK-002"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "app/main.py": CodeArtifact(
            file_path="app/main.py",
            language="python",
            content=(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "@app.post('/api/v1/auth/login')\n"
                "def login():\n"
                "    return {'token': 'jwt-token-123'}\n"
            ),
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_valid_qa_result_dict() -> Dict[str, Any]:
    return {
        "test_plan": {
            "strategy": "Comprehensive unit, integration, and API contract test strategy.",
            "coverage_goals": {"app/main.py": "90%"},
            "test_suites": ["unit", "integration"],
            "quality_metrics": {"target_pass_rate": 1.0, "statement_coverage": "85%"},
            "produced_by": "QAAgent",
            "produced_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
        "test_artifacts": [
            {
                "file_path": "tests/test_auth.py",
                "content": "def test_login():\n    assert True\n",
                "test_type": "unit",
                "coverage_target": "app/main.py",
                "produced_by": "QAAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            }
        ],
        "ai_findings": ["All endpoints covered by unit tests."],
    }


def test_qa_constructor_validation():
    """QAAgent requires non-empty model_name, llm_client, and test_runner."""
    client = FakeLLMClient()
    runner = FakeTestRunner()

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        QAAgent(model_name="", llm_client=client, test_runner=runner)

    with pytest.raises(ValueError, match="llm_client is required"):
        QAAgent(model_name="test-model", llm_client=None, test_runner=runner)  # type: ignore

    with pytest.raises(ValueError, match="test_runner is required"):
        QAAgent(model_name="test-model", llm_client=client, test_runner=None)  # type: ignore

    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)
    assert agent.agent_name == "QAAgent"
    assert agent.max_retries == 3


def test_qa_success_path_valid_qa_results():
    """Happy path: valid QAResult and passing test runner -> STATUS_QA_COMPLETE and ReviewerAgent."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict()])
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=True,
                total_tests=2,
                passed_tests=2,
                failed_tests=0,
                duration_seconds=0.15,
                output="2 passed in 0.15s",
                failing_tests=[],
                coverage_percentage=92.5,
            )
        ]
    )
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["retry_counts"] = {"QAAgent": 2, "BackendAgent": 1}

    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_QA_COMPLETE
    assert result["next_agent"] == "ReviewerAgent"
    assert result["current_agent"] == "QAAgent"
    assert result["forward_stage"] == 6
    assert result["retry_counts"]["QAAgent"] == 0
    assert result["retry_counts"]["BackendAgent"] == 1
    assert result["checkpoint_stage"] == "qa_complete"

    # QA tasks marked completed on passing run
    updated_tasks = {t.task_id: t for t in result["task_plan"].tasks}
    assert updated_tasks["TASK-002"].status == "completed"

    # Test artifacts and plan stamped
    assert "tests/test_auth.py" in result["test_artifacts"]
    assert result["test_artifacts"]["tests/test_auth.py"].produced_by == "QAAgent"
    assert result["test_artifacts"]["tests/test_auth.py"].version == 1
    assert result["test_plan"].produced_by == "QAAgent"
    assert result["test_plan"].version == 1
    assert len(runner.calls) == 1


def test_qa_test_failure_path_routes_to_debug():
    """Failing test runner -> STATUS_DEBUG_REQUIRED, DebugAgent, error_type='other', retry_counts reset."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict()])
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=False,
                total_tests=2,
                passed_tests=1,
                failed_tests=1,
                duration_seconds=0.22,
                output="FAILED tests/test_auth.py::test_login - AssertionError: 500 != 200",
                failing_tests=["tests/test_auth.py::test_login"],
            )
        ]
    )
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["retry_counts"] = {"QAAgent": 2, "BackendAgent": 1}

    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DEBUG_REQUIRED
    assert result["next_agent"] == "DebugAgent"
    assert result["current_agent"] == "QAAgent"
    # forward_stage must NOT advance on debug required path
    assert "forward_stage" not in result
    # retry_counts["QAAgent"] reset to 0 on debug_required as well
    assert result["retry_counts"]["QAAgent"] == 0
    assert result["retry_counts"]["BackendAgent"] == 1

    # QA tasks must NOT be marked completed on test failure
    assert "task_plan" not in result

    # Appends ErrorRecord with error_type="other" and message prefixed "test_failure:"
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err.error_type == "other"
    assert err.agent == "QAAgent"
    assert err.stage == STATUS_DEBUG_REQUIRED
    assert err.message.startswith("test_failure:")
    assert "tests/test_auth.py::test_login" in err.message
    assert "AssertionError: 500 != 200" in err.message
    assert result["current_debug_target"] == err.error_id


def test_qa_test_failure_truncates_runner_output_to_2000_chars():
    """Runner output in ErrorRecord message is truncated to 2000 chars."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict()])
    huge_output = "x" * 5000
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=False,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                duration_seconds=0.1,
                output=huge_output,
                failing_tests=["test_huge"],
            )
        ]
    )
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()

    result = agent.execute(state)
    err = result["errors"][0]
    # Check that the output portion does not exceed 2000 characters
    output_portion = err.message.split("Output:\n", 1)[1]
    assert len(output_portion) == 2000


def test_qa_versioning_existing_test_plan_and_test_artifacts():
    """When test_plan or test_artifacts already exist in state, their versions increment."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict()])
    runner = FakeTestRunner(
        results=[
            TestRunResult(
                passed=True,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                duration_seconds=0.05,
                output="1 passed",
                failing_tests=[],
            )
        ]
    )
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    existing_art = TestArtifact(
        file_path="tests/test_auth.py",
        content="def old(): pass",
        test_type="unit",
        coverage_target="app/main.py",
        produced_by="QAAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=2,
    )
    existing_plan = TestPlan(
        strategy="Old strategy",
        coverage_goals={},
        test_suites=[],
        quality_metrics={},
        produced_by="QAAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=4,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["test_artifacts"] = {"tests/test_auth.py": existing_art}
    state["test_plan"] = existing_plan

    result = agent.execute(state)

    assert result["test_artifacts"]["tests/test_auth.py"].version == 3
    assert result["test_plan"].version == 5


@pytest.mark.parametrize(
    "missing_field",
    [
        "requirements",
        "architecture",
        "task_plan",
        "code_artifacts",
    ],
)
def test_qa_missing_inputs_fast_fails(missing_field: str):
    """QAAgent fast-fails if any required input field is missing."""
    client = FakeLLMClient()
    runner = FakeTestRunner()
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state[missing_field] = None

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "QAAgent"
    assert result["retry_counts"]["QAAgent"] == 3


def test_qa_transient_failure_and_retry_recovery():
    """First attempt raises exception; second succeeds with exponential backoff."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("Temporary LLM error"),
            _sample_valid_qa_result_dict(),
        ]
    )
    runner = FakeTestRunner()
    sleeps = []
    agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
        sleep_fn=sleeps.append,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_QA_COMPLETE
    assert len(sleeps) == 1
    assert sleeps[0] == 2.0


def test_qa_retry_exhaustion_after_configured_attempts():
    """When all internal attempts fail, returns exhaustion update with human approval."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("API error 1"),
            RuntimeError("API error 2"),
            RuntimeError("API error 3"),
        ]
    )
    runner = FakeTestRunner()
    sleeps = []
    agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
        sleep_fn=sleeps.append,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["workflow_status"] = "frontend_complete"

    result = agent.execute(state)
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "QAAgent"
    assert result["retry_counts"]["QAAgent"] == 3
    # workflow_status is preserved unchanged
    assert "workflow_status" not in result
    assert len(sleeps) == 2


def test_qa_forward_stage_idempotency_on_reruns():
    """forward_stage is monotonic: if already >= 6, it is preserved and not regressed."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict()])
    runner = FakeTestRunner()
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["forward_stage"] = 7

    result = agent.execute(state)
    assert result["forward_stage"] == 7


def test_qa_input_state_task_plan_unchanged():
    """Passing QAAgent execution must not mutate the input state's task_plan."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict()])
    runner = FakeTestRunner()
    agent = QAAgent(model_name="test-model", llm_client=client, test_runner=runner)

    initial_plan = _sample_valid_task_plan()
    qa_tasks = [t for t in initial_plan.tasks if t.assigned_agent == "QAAgent"]
    assert len(qa_tasks) > 0
    assert all(t.status == "pending" for t in qa_tasks)

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = initial_plan
    state["code_artifacts"] = _sample_valid_code_artifacts()

    result = agent.execute(state)

    # Returned task_plan has completed QA task
    updated_tasks = {t.task_id: t for t in result["task_plan"].tasks}
    assert updated_tasks["TASK-002"].status == "completed"

    # Input state task_plan remains unchanged (deep copy verification)
    input_tasks = {t.task_id: t for t in state["task_plan"].tasks}
    assert input_tasks["TASK-002"].status == "pending"
    assert state["task_plan"] is initial_plan


def test_qa_no_sleep_on_validation_errors():
    """Validation errors during retry loop do not backoff-sleep; unhandled exceptions do."""
    # Attempt 1: ValueError (validation error)
    # Attempt 2: QAResult with empty test_artifacts (raises ValueError validation failure)
    # Attempt 3: Valid QAResult
    empty_artifacts_qa = _sample_valid_qa_result_dict()
    empty_artifacts_qa["test_artifacts"] = []

    client = FakeLLMClient(
        responses=[
            ValueError("Invalid schema field"),
            empty_artifacts_qa,
            _sample_valid_qa_result_dict(),
        ]
    )
    runner = FakeTestRunner()
    sleeps = []
    agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
        sleep_fn=sleeps.append,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_QA_COMPLETE
    # Validation errors should have triggered 0 sleep calls
    assert len(sleeps) == 0


def test_qa_test_runner_exception_routes_to_exhaustion_without_retry():
    """TestRunner exception routes straight to exhaustion without regenerating LLM."""
    client = FakeLLMClient(responses=[_sample_valid_qa_result_dict(), _sample_valid_qa_result_dict()])
    runner = FakeTestRunner(results=[RuntimeError("Test runner container crashed")])
    sleeps = []
    agent = QAAgent(
        model_name="test-model",
        llm_client=client,
        test_runner=runner,
        sleep_fn=sleeps.append,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build test suite")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["workflow_status"] = "frontend_complete"

    result = agent.execute(state)

    # Exhaustion return triggered
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "QAAgent"
    # workflow_status preserved unchanged
    assert "workflow_status" not in result
    # Error record captures test runner failure
    assert len(result["errors"]) == 1
    assert "Test runner execution failed" in result["errors"][0].message
    assert "Test runner container crashed" in result["errors"][0].message
    # LLM was only called once, NOT regenerated on runner failure
    assert len(client.calls) == 1
    assert len(sleeps) == 0

