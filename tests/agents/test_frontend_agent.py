"""
tests/agents/test_frontend_agent.py

Comprehensive unit and behavioral tests for FrontendAgent in agents/frontend_agent.py.
"""

from typing import Any, Dict
import pytest

from agents.frontend_agent import FrontendAgent, FrontendArtifacts
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    ImplementationTask,
    ProductRequirements,
    SystemArchitecture,
    TaskPlan,
)
from tools.shared.llm_client import FakeLLMClient
from workflows.constants import STATUS_FRONTEND_COMPLETE


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
        overview="Next.js frontend communicating with FastAPI backend.",
        technology_stack={
            "frontend": "React 18 / TypeScript / Vite",
            "backend": "Python 3.11 / FastAPI",
            "styling": "Tailwind CSS",
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
        scalability_strategy="CDN caching for static UI assets.",
        deployment_model="Vercel deployment.",
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
                description="Implement backend JWT authentication endpoint.",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["POST /api/v1/auth/login returns 200"],
                status="completed",
            ),
            ImplementationTask(
                task_id="TASK-002",
                title="Login page UI component",
                description="Build responsive login page with form validation.",
                assigned_agent="FrontendAgent",
                priority=20,
                dependencies=["TASK-001"],
                acceptance_criteria=["Login form displays and invokes login API"],
                status="pending",
            ),
            ImplementationTask(
                task_id="TASK-003",
                title="E2E auth testing",
                description="Automated tests.",
                assigned_agent="QAAgent",
                priority=30,
                dependencies=["TASK-002"],
                acceptance_criteria=["Auth flow passes"],
                status="pending",
            ),
        ],
        execution_order=["TASK-001", "TASK-002", "TASK-003"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_frontend_artifacts_dict() -> Dict[str, Any]:
    return {
        "artifacts": [
            {
                "file_path": "src/pages/LoginPage.tsx",
                "language": "typescript",
                "content": "import React from 'react';\nexport const LoginPage = () => <form>Login</form>;\n",
                "artifact_type": "frontend",
                "produced_by": "FrontendAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            },
            {
                "file_path": "src/api/authClient.ts",
                "language": "typescript",
                "content": "export async function login() { return fetch('/api/v1/auth/login'); }\n",
                "artifact_type": "frontend",
                "produced_by": "FrontendAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            },
        ]
    }


def test_frontend_constructor_validation():
    """Constructor validates agent arguments."""
    client = FakeLLMClient()
    agent = FrontendAgent(model_name="test-model", llm_client=client, max_retries=3)
    assert agent.agent_name == "FrontendAgent"
    assert agent.max_retries == 3
    assert agent.model_name == "test-model"

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        FrontendAgent(model_name="", llm_client=client)

    with pytest.raises(ValueError, match="llm_client must not be None"):
        FrontendAgent(model_name="test-model", llm_client=None)  # type: ignore

    with pytest.raises(ValueError, match="max_retries must be a positive integer"):
        FrontendAgent(model_name="test-model", llm_client=client, max_retries=0)


def test_frontend_success_valid_artifact_generation():
    """FrontendAgent.execute succeeds with valid artifacts and updates state correctly."""
    client = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    agent = FrontendAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build frontend")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = {
        "app/main.py": CodeArtifact(
            file_path="app/main.py",
            language="python",
            content="# backend main",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }

    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_FRONTEND_COMPLETE
    assert result["next_agent"] == "QAAgent"
    assert result["current_agent"] == "FrontendAgent"
    assert result["forward_stage"] == 5
    assert result["retry_counts"] == {"FrontendAgent": 0}
    assert result["checkpoint_stage"] == "frontend_complete"
    assert "frontend_complete" in result["checkpoints"]
    assert "FrontendAgent" in result["agent_scratchpad"]
    assert result["state_version"] == 1

    artifacts = result["code_artifacts"]
    assert len(artifacts) == 2
    assert "src/pages/LoginPage.tsx" in artifacts
    assert "src/api/authClient.ts" in artifacts
    assert artifacts["src/pages/LoginPage.tsx"].produced_by == "FrontendAgent"
    assert artifacts["src/pages/LoginPage.tsx"].artifact_type == "frontend"
    assert artifacts["src/pages/LoginPage.tsx"].version == 1

    # Task statuses for frontend tasks are marked completed
    plan: TaskPlan = result["task_plan"]
    task_map = {t.task_id: t for t in plan.tasks}
    assert task_map["TASK-001"].status == "completed"  # Backend task preserved
    assert task_map["TASK-002"].status == "completed"  # Frontend task completed
    assert task_map["TASK-003"].status == "pending"    # QA task preserved


def test_frontend_versioning_existing_artifact_increment():
    """Existing frontend code artifact file path increments version upon update (Req 6 crit 4)."""
    client = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    agent = FrontendAgent(model_name="test-model", llm_client=client)

    existing_art = CodeArtifact(
        file_path="src/pages/LoginPage.tsx",
        language="typescript",
        content="// old login component",
        artifact_type="frontend",
        produced_by="FrontendAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=2,
    )

    state = create_initial_state(project_name="OrderApp", user_input="Update frontend")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()
    state["code_artifacts"] = {"src/pages/LoginPage.tsx": existing_art}

    result = agent.execute(state)
    assert result["code_artifacts"]["src/pages/LoginPage.tsx"].version == 3
    assert result["code_artifacts"]["src/api/authClient.ts"].version == 1


def test_frontend_task_selection_ignores_non_frontend_tasks():
    """FrontendAgent only executes tasks assigned to FrontendAgent."""
    client = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    agent = FrontendAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build frontend")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()

    agent.execute(state)

    prompt = client.calls[0]["prompt"]
    assert "Task ID: TASK-002" in prompt
    assert "Task ID: TASK-001" not in prompt  # BackendAgent task excluded from frontend tasks prompt
    assert "Task ID: TASK-003" not in prompt  # QAAgent task excluded from frontend tasks prompt


def test_frontend_no_frontend_tasks_skips_llm():
    """If task_plan has no tasks assigned to FrontendAgent, completes immediately without LLM calls."""
    client = FakeLLMClient(responses=[])
    agent = FrontendAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Backend only")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="TASK-BE",
                title="BE",
                description="Backend only",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["Works"],
                status="completed",
            )
        ],
        execution_order=["TASK-BE"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    result = agent.execute(state)

    assert len(client.calls) == 0
    assert result["workflow_status"] == STATUS_FRONTEND_COMPLETE
    assert result["next_agent"] == "QAAgent"
    assert result["forward_stage"] == 5
    assert result["retry_counts"] == {"FrontendAgent": 0}
    assert "No frontend tasks assigned" in result["agent_scratchpad"]["FrontendAgent"]


def test_frontend_dependency_handling_valid_and_invalid():
    """FrontendAgent validates dependencies: passes on valid, rejects failed or unknown dependencies."""
    # 1. Dependency on failed task raises validation exhaustion
    fail_plan = TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="TASK-001",
                title="Backend API",
                description="API setup",
                assigned_agent="BackendAgent",
                priority=10,
                dependencies=[],
                acceptance_criteria=["API"],
                status="failed",
            ),
            ImplementationTask(
                task_id="TASK-002",
                title="UI",
                description="UI",
                assigned_agent="FrontendAgent",
                priority=20,
                dependencies=["TASK-001"],
                acceptance_criteria=["UI"],
                status="pending",
            ),
        ],
        execution_order=["TASK-001", "TASK-002"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    client = FakeLLMClient(responses=[])
    agent = FrontendAgent(model_name="test-model", llm_client=client, max_retries=3)

    state = create_initial_state(project_name="App", user_input="Input")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = fail_plan

    result = agent.execute(state)
    assert len(client.calls) == 0
    assert result["human_approval_required"] is True
    assert "depends on failed task 'TASK-001'" in result["errors"][0].message

    # 2. Unknown dependency
    unknown_dep_task = ImplementationTask(
        task_id="TASK-002",
        title="UI",
        description="UI",
        assigned_agent="FrontendAgent",
        priority=20,
        dependencies=["TASK-UNKNOWN"],
        acceptance_criteria=["UI"],
        status="pending",
    )
    raw_plan_dict = {
        "tasks": [unknown_dep_task.model_dump()],
        "execution_order": ["TASK-002"],
        "produced_by": "TaskPlannerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }
    state["task_plan"] = raw_plan_dict
    res_unknown = agent.execute(state)
    assert res_unknown["human_approval_required"] is True


@pytest.mark.parametrize(
    "missing_inputs",
    [
        {"req": None, "arch": _sample_valid_architecture(), "plan": _sample_valid_task_plan()},
        {"req": _sample_valid_requirements(), "arch": None, "plan": _sample_valid_task_plan()},
        {"req": _sample_valid_requirements(), "arch": _sample_valid_architecture(), "plan": None},
        {
            "req": ProductRequirements(
                functional_requirements=[],
                non_functional_requirements=["Latency < 1s"],
                user_stories=["Story"],
                acceptance_criteria=["AC"],
                constraints=["React"],
                business_goals=["Goal"],
                produced_by="PM",
                produced_at="2026-01-01T00:00:00Z",
                version=1,
            ),
            "arch": _sample_valid_architecture(),
            "plan": _sample_valid_task_plan(),
        },
        {
            "req": _sample_valid_requirements(),
            "arch": SystemArchitecture(
                overview="",
                technology_stack={"frontend": "React"},
                produced_by="ArchitectAgent",
                produced_at="2026-01-01T00:00:00Z",
                version=1,
            ),
            "plan": _sample_valid_task_plan(),
        },
    ],
)
def test_frontend_missing_or_invalid_inputs_fast_fails(missing_inputs):
    """Missing or invalid inputs fast-fail to exhaustion without LLM calls or backoff."""
    client = FakeLLMClient(responses=[])
    sleep_calls = []

    agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = missing_inputs["req"]
    state["architecture"] = missing_inputs["arch"]
    state["task_plan"] = missing_inputs["plan"]

    result = agent.execute(state)

    assert len(client.calls) == 0
    assert len(sleep_calls) == 0
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "FrontendAgent"
    assert result["retry_counts"]["FrontendAgent"] == 3
    assert result["execution_metadata"]["FrontendAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result


def test_frontend_transient_failure_and_retry_recovery():
    """Validation failure on attempt 1 triggers revision prompt on attempt 2 without backoff sleep."""
    invalid_art = _sample_valid_frontend_artifacts_dict()
    invalid_art["artifacts"][0]["content"] = ""  # empty content violates validation

    valid_art = _sample_valid_frontend_artifacts_dict()

    client = FakeLLMClient(responses=[invalid_art, valid_art])
    sleep_calls = []

    agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()

    result = agent.execute(state)

    assert len(client.calls) == 2
    assert len(sleep_calls) == 0
    assert "validation_failure" in client.calls[1]["prompt"]
    assert result["workflow_status"] == STATUS_FRONTEND_COMPLETE
    assert result["retry_counts"]["FrontendAgent"] == 0


def test_frontend_unhandled_exception_applies_backoff_and_recovers():
    """Unhandled exception triggers exponential backoff (2.0s on attempt 1)."""
    valid_art = _sample_valid_frontend_artifacts_dict()
    client = FakeLLMClient(responses=[RuntimeError("API timeout"), valid_art])
    sleep_calls = []

    agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()

    result = agent.execute(state)

    assert len(client.calls) == 2
    assert sleep_calls == [2.0]
    assert result["workflow_status"] == STATUS_FRONTEND_COMPLETE
    assert result["retry_counts"]["FrontendAgent"] == 0


def test_frontend_retry_exhaustion_after_configured_attempts():
    """All attempts failing returns exhaustion update with failed metadata and human approval hold."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("Network error 1"),
            RuntimeError("Network error 2"),
            RuntimeError("Network error 3"),
        ]
    )
    sleep_calls = []

    agent = FrontendAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()

    result = agent.execute(state)

    assert len(client.calls) == 3
    assert sleep_calls == [2.0, 4.0]
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "FrontendAgent"
    assert result["retry_counts"]["FrontendAgent"] == 3
    assert result["execution_metadata"]["FrontendAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result


def test_frontend_retry_limits_below_and_above_three():
    """FrontendAgent supports retry limits below 3 (1) and above 3 (4)."""
    # 1 attempt limit
    client1 = FakeLLMClient(responses=[RuntimeError("Fail")])
    sleep1 = []
    agent1 = FrontendAgent(
        model_name="test-model",
        llm_client=client1,
        max_retries=1,
        sleep_fn=lambda s: sleep1.append(s),
    )
    state1 = create_initial_state(project_name="App", user_input="Input")
    state1["requirements"] = _sample_valid_requirements()
    state1["architecture"] = _sample_valid_architecture()
    state1["task_plan"] = _sample_valid_task_plan()
    res1 = agent1.execute(state1)
    assert len(client1.calls) == 1
    assert len(sleep1) == 0
    assert res1["retry_counts"]["FrontendAgent"] == 1

    # 4 attempts limit with state override
    client4 = FakeLLMClient(
        responses=[
            RuntimeError("Fail 1"),
            RuntimeError("Fail 2"),
            RuntimeError("Fail 3"),
            RuntimeError("Fail 4"),
        ]
    )
    sleep4 = []
    agent4 = FrontendAgent(
        model_name="test-model",
        llm_client=client4,
        max_retries=3,
        sleep_fn=lambda s: sleep4.append(s),
    )
    state4 = create_initial_state(project_name="App", user_input="Input")
    state4["requirements"] = _sample_valid_requirements()
    state4["architecture"] = _sample_valid_architecture()
    state4["task_plan"] = _sample_valid_task_plan()
    state4["max_retries"] = {"FrontendAgent": 4}
    res4 = agent4.execute(state4)
    assert len(client4.calls) == 4
    assert sleep4 == [2.0, 4.0, 8.0]
    assert res4["retry_counts"]["FrontendAgent"] == 4


def test_frontend_forward_stage_idempotency_on_reruns():
    """forward_stage advances to 5 and does not double-count on re-runs (Req 21 crit 6)."""
    client = FakeLLMClient(
        responses=[
            _sample_valid_frontend_artifacts_dict(),
            _sample_valid_frontend_artifacts_dict(),
            _sample_valid_frontend_artifacts_dict(),
        ]
    )
    agent = FrontendAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = _sample_valid_task_plan()

    state["forward_stage"] = 0
    res1 = agent.execute(state)
    assert res1["forward_stage"] == 5

    state["forward_stage"] = 4
    res2 = agent.execute(state)
    assert res2["forward_stage"] == 5

    state["forward_stage"] = 6
    res3 = agent.execute(state)
    assert res3["forward_stage"] == 6


def test_frontend_preserves_other_agents_retry_counts():
    """FrontendAgent preserves other agents' retry counts on success and exhaustion."""
    # On success
    client_ok = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    agent_ok = FrontendAgent(model_name="test-model", llm_client=client_ok)
    state_ok = create_initial_state(project_name="App", user_input="Input")
    state_ok["requirements"] = _sample_valid_requirements()
    state_ok["architecture"] = _sample_valid_architecture()
    state_ok["task_plan"] = _sample_valid_task_plan()
    state_ok["retry_counts"] = {"ProductManagerAgent": 0, "BackendAgent": 1}
    res_ok = agent_ok.execute(state_ok)
    assert res_ok["retry_counts"] == {
        "ProductManagerAgent": 0,
        "BackendAgent": 1,
        "FrontendAgent": 0,
    }

    # On exhaustion
    client_fail = FakeLLMClient(responses=[RuntimeError("Err")])
    agent_fail = FrontendAgent(
        model_name="test-model",
        llm_client=client_fail,
        max_retries=1,
        sleep_fn=lambda s: None,
    )
    state_fail = create_initial_state(project_name="App", user_input="Input")
    state_fail["requirements"] = _sample_valid_requirements()
    state_fail["architecture"] = _sample_valid_architecture()
    state_fail["task_plan"] = _sample_valid_task_plan()
    state_fail["retry_counts"] = {"ProductManagerAgent": 0, "BackendAgent": 1}
    res_fail = agent_fail.execute(state_fail)
    assert res_fail["retry_counts"] == {
        "ProductManagerAgent": 0,
        "BackendAgent": 1,
        "FrontendAgent": 1,
    }


def test_frontend_invoke_lifecycle_success_and_exhaustion():
    """BaseAgent.invoke merges metadata correctly on success and exhaustion."""
    # Success
    client_ok = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    agent_ok = FrontendAgent(model_name="test-model", llm_client=client_ok)
    state_ok = create_initial_state(project_name="App", user_input="Input")
    state_ok["requirements"] = _sample_valid_requirements()
    state_ok["architecture"] = _sample_valid_architecture()
    state_ok["task_plan"] = _sample_valid_task_plan()
    update_ok = agent_ok.invoke(state_ok)
    assert update_ok["workflow_status"] == STATUS_FRONTEND_COMPLETE
    assert update_ok["execution_metadata"]["FrontendAgent"].status == "completed"
    assert update_ok["total_invocations"] == 1

    # Exhaustion
    client_fail = FakeLLMClient(responses=[RuntimeError("API Err")])
    agent_fail = FrontendAgent(
        model_name="test-model",
        llm_client=client_fail,
        max_retries=1,
        sleep_fn=lambda s: None,
    )
    state_fail = create_initial_state(project_name="App", user_input="Input")
    state_fail["requirements"] = _sample_valid_requirements()
    state_fail["architecture"] = _sample_valid_architecture()
    state_fail["task_plan"] = _sample_valid_task_plan()
    update_fail = agent_fail.invoke(state_fail)
    assert update_fail["human_approval_required"] is True
    assert update_fail["execution_metadata"]["FrontendAgent"].status == "failed"
    assert update_fail["total_invocations"] == 1
    assert "workflow_status" not in update_fail


def test_frontend_input_state_task_plan_unchanged():
    """Executing FrontendAgent must not mutate the input state's task_plan."""
    client = FakeLLMClient(responses=[_sample_valid_frontend_artifacts_dict()])
    agent = FrontendAgent(model_name="test-model", llm_client=client)

    initial_plan = _sample_valid_task_plan()
    # Ensure frontend tasks start in pending status
    assert any(t.assigned_agent == "FrontendAgent" and t.status == "pending" for t in initial_plan.tasks)

    state = create_initial_state(project_name="OrderApp", user_input="Build frontend")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = initial_plan

    result = agent.execute(state)

    # Returned task_plan has completed tasks
    updated_tasks = {t.task_id: t for t in result["task_plan"].tasks}
    assert updated_tasks["TASK-002"].status == "completed"

    # Input state task_plan remains unchanged (deep copy verification)
    input_tasks = {t.task_id: t for t in state["task_plan"].tasks}
    assert input_tasks["TASK-002"].status == "pending"
    assert state["task_plan"] is initial_plan

