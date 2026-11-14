"""
tests/agents/test_task_planner_agent.py

Comprehensive unit and behavioral tests for TaskPlannerAgent in agents/task_planner_agent.py.
"""

from typing import Any, Dict
import pytest

from agents.task_planner_agent import TaskPlannerAgent
from state.engineering_state import create_initial_state
from state.models import ImplementationTask, ProductRequirements, SystemArchitecture, TaskPlan
from tools.shared.llm_client import FakeLLMClient
from workflows.constants import STATUS_PLANNING_COMPLETE


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
        overview="Microservices-based event-driven architecture with API Gateway.",
        technology_stack={
            "backend": "Python 3.11 / FastAPI",
            "database": "PostgreSQL 16",
            "cache": "Redis 7",
        },
        api_specifications=[
            {
                "endpoint": "/api/v1/auth/login",
                "method": "POST",
                "description": "Authenticate user and issue JWT",
            },
        ],
        database_design={"users": {"id": "UUID PK"}},
        security_architecture={"auth": "JWT RS256"},
        scalability_strategy="Horizontal autoscaling.",
        deployment_model="Kubernetes cluster.",
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_task_plan_dict() -> Dict[str, Any]:
    return {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Setup FastAPI project and user auth model",
                "description": "Initialize backend structure and implement JWT auth endpoints.",
                "assigned_agent": "BackendAgent",
                "priority": 10,
                "dependencies": [],
                "acceptance_criteria": ["JWT login endpoint returns 200 and valid token"],
                "status": "pending",
            },
            {
                "task_id": "TASK-002",
                "title": "Build frontend login UI",
                "description": "Create login page and integrate with auth API.",
                "assigned_agent": "FrontendAgent",
                "priority": 20,
                "dependencies": ["TASK-001"],
                "acceptance_criteria": ["User can login through form and token is stored"],
                "status": "pending",
            },
            {
                "task_id": "TASK-003",
                "title": "End-to-end auth integration test",
                "description": "Write automated QA tests validating complete auth flow.",
                "assigned_agent": "QAAgent",
                "priority": 30,
                "dependencies": ["TASK-001", "TASK-002"],
                "acceptance_criteria": ["Automated test suite passes with 100% coverage on auth"],
                "status": "pending",
            },
        ],
        "execution_order": ["TASK-001", "TASK-002", "TASK-003"],
        "produced_by": "TaskPlannerAgent",
        "produced_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }


def test_task_planner_constructor_validation():
    """Constructor validates agent arguments."""
    client = FakeLLMClient()
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client, max_retries=3)
    assert agent.agent_name == "TaskPlannerAgent"
    assert agent.max_retries == 3
    assert agent.model_name == "test-model"

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        TaskPlannerAgent(model_name="", llm_client=client)

    with pytest.raises(ValueError, match="llm_client must not be None"):
        TaskPlannerAgent(model_name="test-model", llm_client=None)  # type: ignore

    with pytest.raises(ValueError, match="max_retries must be a positive integer"):
        TaskPlannerAgent(model_name="test-model", llm_client=client, max_retries=0)


def test_task_planner_success_path_valid_plan():
    """TaskPlannerAgent.execute succeeds with valid task plan and stamps all required fields."""
    client = FakeLLMClient(responses=[_sample_valid_task_plan_dict()])
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build an order app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert result["next_agent"] == "BackendAgent"
    assert result["current_agent"] == "TaskPlannerAgent"
    assert result["forward_stage"] == 3
    assert result["retry_counts"] == {"TaskPlannerAgent": 0}
    assert result["checkpoint_stage"] == "planning_complete"
    assert "planning_complete" in result["checkpoints"]
    assert "TaskPlannerAgent" in result["agent_scratchpad"]
    assert result["state_version"] == 1

    plan: TaskPlan = result["task_plan"]
    assert isinstance(plan, TaskPlan)
    assert plan.produced_by == "TaskPlannerAgent"
    assert plan.version == 1
    assert len(plan.tasks) == 3
    assert plan.execution_order == ["TASK-001", "TASK-002", "TASK-003"]
    assert plan.tasks[0].assigned_agent == "BackendAgent"
    assert plan.tasks[1].assigned_agent == "FrontendAgent"
    assert plan.tasks[2].assigned_agent == "QAAgent"


def test_task_planner_version_increment_on_revision():
    """TaskPlan version increments when task_plan already exists in state (Req 5 crit 4)."""
    client = FakeLLMClient(responses=[_sample_valid_task_plan_dict()])
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client)

    existing_plan = TaskPlan(**_sample_valid_task_plan_dict())
    existing_plan.version = 2

    state = create_initial_state(project_name="OrderApp", user_input="Revise task plan")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["task_plan"] = existing_plan

    result = agent.execute(state)
    assert result["task_plan"].version == 3


def test_task_planner_forward_stage_idempotency_on_reruns():
    """forward_stage advances to 3 and does not double-count on re-runs (Req 21 crit 6)."""
    client = FakeLLMClient(
        responses=[
            _sample_valid_task_plan_dict(),
            _sample_valid_task_plan_dict(),
            _sample_valid_task_plan_dict(),
        ]
    )
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client)

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()

    state["forward_stage"] = 0
    res1 = agent.execute(state)
    assert res1["forward_stage"] == 3

    state["forward_stage"] = 2
    res2 = agent.execute(state)
    assert res2["forward_stage"] == 3

    state["forward_stage"] = 5
    res3 = agent.execute(state)
    assert res3["forward_stage"] == 5


@pytest.mark.parametrize(
    "missing_requirements",
    [
        None,
        ProductRequirements(
            functional_requirements=[],
            non_functional_requirements=["Latency < 1s"],
            user_stories=["Story"],
            acceptance_criteria=["AC"],
            constraints=["Python"],
            business_goals=["Goal"],
            produced_by="PM",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
        "not-a-product-requirements-object",
        {"invalid_key": "broken"},
    ],
)
def test_task_planner_missing_or_invalid_requirements_fast_fails(missing_requirements):
    """Missing or empty requirements input fast-fails to exhaustion without LLM calls or backoff."""
    client = FakeLLMClient(responses=[])
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = missing_requirements
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)

    assert len(client.calls) == 0
    assert len(sleep_calls) == 0
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "TaskPlannerAgent"
    assert result["retry_counts"]["TaskPlannerAgent"] == 3
    assert result["execution_metadata"]["TaskPlannerAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result


@pytest.mark.parametrize(
    "missing_architecture",
    [
        None,
        SystemArchitecture(
            overview="",
            technology_stack={"backend": "FastAPI"},
            produced_by="ArchitectAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
        SystemArchitecture(
            overview="Some architecture",
            technology_stack={},
            produced_by="ArchitectAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        ),
        "not-a-system-architecture-object",
        {"invalid_key": "broken"},
    ],
)
def test_task_planner_missing_or_invalid_architecture_fast_fails(missing_architecture):
    """Missing or empty architecture input fast-fails to exhaustion without LLM calls or backoff."""
    client = FakeLLMClient(responses=[])
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = missing_architecture

    result = agent.execute(state)

    assert len(client.calls) == 0
    assert len(sleep_calls) == 0
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "TaskPlannerAgent"
    assert result["retry_counts"]["TaskPlannerAgent"] == 3
    assert result["execution_metadata"]["TaskPlannerAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result


def test_task_planner_fast_fail_when_retry_limit_already_reached():
    """Fast-fails if start retry count has already reached effective_max before execution."""
    client = FakeLLMClient(responses=[])
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["retry_counts"] = {"TaskPlannerAgent": 3}

    result = agent.execute(state)

    assert len(client.calls) == 0
    assert len(sleep_calls) == 0
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "TaskPlannerAgent"
    assert result["retry_counts"]["TaskPlannerAgent"] == 3
    assert result["execution_metadata"]["TaskPlannerAgent"].status == "failed"
    assert "Retry limit already reached" in result["errors"][0].message


def test_task_planner_validation_failure_and_internal_recovery():
    """Validation failure on attempt 1 triggers revision prompt on attempt 2 without backoff sleep."""
    invalid_plan = _sample_valid_task_plan_dict()
    # Violate assigned_agent constraint
    invalid_plan["tasks"][0]["assigned_agent"] = "InvalidAgent"

    valid_plan = _sample_valid_task_plan_dict()

    client = FakeLLMClient(responses=[invalid_plan, valid_plan])
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)

    assert len(client.calls) == 2
    assert len(sleep_calls) == 0
    assert "validation_failure" in client.calls[1]["prompt"]
    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert result["retry_counts"]["TaskPlannerAgent"] == 0


def test_task_planner_topological_order_violation_triggers_retry():
    """Plan with invalid dependency ordering in execution_order triggers revision prompt."""
    invalid_plan = _sample_valid_task_plan_dict()
    # Put TASK-002 before its dependency TASK-001 in execution_order
    invalid_plan["execution_order"] = ["TASK-002", "TASK-001", "TASK-003"]

    valid_plan = _sample_valid_task_plan_dict()

    client = FakeLLMClient(responses=[invalid_plan, valid_plan])
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)

    assert len(client.calls) == 2
    assert len(sleep_calls) == 0
    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert result["retry_counts"]["TaskPlannerAgent"] == 0


def test_task_planner_unhandled_exception_applies_backoff_and_recovers():
    """Unhandled exception triggers exponential backoff (2.0s on attempt 1)."""
    valid_plan = _sample_valid_task_plan_dict()
    client = FakeLLMClient(responses=[RuntimeError("Connection timeout"), valid_plan])
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)

    assert len(client.calls) == 2
    assert sleep_calls == [2.0]
    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert result["retry_counts"]["TaskPlannerAgent"] == 0


def test_task_planner_retry_exhaustion_after_configured_attempts():
    """All attempts failing returns exhaustion update with failed metadata and human approval hold."""
    client = FakeLLMClient(
        responses=[
            RuntimeError("Network error 1"),
            RuntimeError("Network error 2"),
            RuntimeError("Network error 3"),
        ]
    )
    sleep_calls = []

    agent = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client,
        max_retries=3,
        sleep_fn=lambda s: sleep_calls.append(s),
    )

    state = create_initial_state(project_name="OrderApp", user_input="Build app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)

    assert len(client.calls) == 3
    assert sleep_calls == [2.0, 4.0]
    assert result["human_approval_required"] is True
    assert result["next_agent"] == "TaskPlannerAgent"
    assert result["retry_counts"]["TaskPlannerAgent"] == 3
    assert result["execution_metadata"]["TaskPlannerAgent"].status == "failed"
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "workflow_status" not in result


def test_task_planner_retry_limits_below_and_above_three():
    """TaskPlannerAgent supports retry limits below 3 (1) and above 3 (4)."""
    # 1 attempt limit
    client1 = FakeLLMClient(responses=[RuntimeError("Fail")])
    sleep1 = []
    agent1 = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client1,
        max_retries=1,
        sleep_fn=lambda s: sleep1.append(s),
    )
    state1 = create_initial_state(project_name="App", user_input="Input")
    state1["requirements"] = _sample_valid_requirements()
    state1["architecture"] = _sample_valid_architecture()
    res1 = agent1.execute(state1)
    assert len(client1.calls) == 1
    assert len(sleep1) == 0
    assert res1["retry_counts"]["TaskPlannerAgent"] == 1

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
    agent4 = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client4,
        max_retries=3,
        sleep_fn=lambda s: sleep4.append(s),
    )
    state4 = create_initial_state(project_name="App", user_input="Input")
    state4["requirements"] = _sample_valid_requirements()
    state4["architecture"] = _sample_valid_architecture()
    state4["max_retries"] = {"TaskPlannerAgent": 4}
    res4 = agent4.execute(state4)
    assert len(client4.calls) == 4
    assert sleep4 == [2.0, 4.0, 8.0]
    assert res4["retry_counts"]["TaskPlannerAgent"] == 4


def test_task_planner_preserves_other_agents_retry_counts():
    """TaskPlannerAgent preserves other agents' retry counts on success and exhaustion."""
    # On success
    client_ok = FakeLLMClient(responses=[_sample_valid_task_plan_dict()])
    agent_ok = TaskPlannerAgent(model_name="test-model", llm_client=client_ok)
    state_ok = create_initial_state(project_name="App", user_input="Input")
    state_ok["requirements"] = _sample_valid_requirements()
    state_ok["architecture"] = _sample_valid_architecture()
    state_ok["retry_counts"] = {"ProductManagerAgent": 0, "ArchitectAgent": 1}
    res_ok = agent_ok.execute(state_ok)
    assert res_ok["retry_counts"] == {
        "ProductManagerAgent": 0,
        "ArchitectAgent": 1,
        "TaskPlannerAgent": 0,
    }

    # On exhaustion
    client_fail = FakeLLMClient(responses=[RuntimeError("Err")])
    agent_fail = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client_fail,
        max_retries=1,
        sleep_fn=lambda s: None,
    )
    state_fail = create_initial_state(project_name="App", user_input="Input")
    state_fail["requirements"] = _sample_valid_requirements()
    state_fail["architecture"] = _sample_valid_architecture()
    state_fail["retry_counts"] = {"ProductManagerAgent": 0, "ArchitectAgent": 1}
    res_fail = agent_fail.execute(state_fail)
    assert res_fail["retry_counts"] == {
        "ProductManagerAgent": 0,
        "ArchitectAgent": 1,
        "TaskPlannerAgent": 1,
    }


def test_task_planner_invoke_lifecycle_success_and_exhaustion():
    """BaseAgent.invoke merges metadata correctly on success and exhaustion."""
    # Success
    client_ok = FakeLLMClient(responses=[_sample_valid_task_plan_dict()])
    agent_ok = TaskPlannerAgent(model_name="test-model", llm_client=client_ok)
    state_ok = create_initial_state(project_name="App", user_input="Input")
    state_ok["requirements"] = _sample_valid_requirements()
    state_ok["architecture"] = _sample_valid_architecture()
    update_ok = agent_ok.invoke(state_ok)
    assert update_ok["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert update_ok["execution_metadata"]["TaskPlannerAgent"].status == "completed"
    assert update_ok["total_invocations"] == 1

    # Exhaustion
    client_fail = FakeLLMClient(responses=[RuntimeError("API Err")])
    agent_fail = TaskPlannerAgent(
        model_name="test-model",
        llm_client=client_fail,
        max_retries=1,
        sleep_fn=lambda s: None,
    )
    state_fail = create_initial_state(project_name="App", user_input="Input")
    state_fail["requirements"] = _sample_valid_requirements()
    state_fail["architecture"] = _sample_valid_architecture()
    update_fail = agent_fail.invoke(state_fail)
    assert update_fail["human_approval_required"] is True
    assert update_fail["execution_metadata"]["TaskPlannerAgent"].status == "failed"
    assert update_fail["total_invocations"] == 1
    assert "workflow_status" not in update_fail


def test_task_planner_rejects_frontend_tasks_when_backend_owns_all_app_work():
    """TaskPlanner rejects FrontendAgent tasks when constraints assign all app work to BackendAgent."""
    plan_with_frontend = _sample_valid_task_plan_dict()
    # Plan has TASK-002 assigned to FrontendAgent

    plan_all_backend = {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Setup app and database in app.py",
                "description": "Initialize Flask and SQLite.",
                "assigned_agent": "BackendAgent",
                "priority": 10,
                "dependencies": [],
                "acceptance_criteria": ["Database schema created"],
                "status": "pending",
            },
            {
                "task_id": "TASK-002",
                "title": "Implement endpoints and HTML UI in app.py",
                "description": "Serve UI and endpoints from app.py.",
                "assigned_agent": "BackendAgent",
                "priority": 20,
                "dependencies": ["TASK-001"],
                "acceptance_criteria": ["API and UI work"],
                "status": "pending",
            },
            {
                "task_id": "TASK-003",
                "title": "Write unit tests in test_app.py",
                "description": "Test all endpoints with pytest.",
                "assigned_agent": "QAAgent",
                "priority": 30,
                "dependencies": ["TASK-002"],
                "acceptance_criteria": ["All tests pass"],
                "status": "pending",
            },
        ],
        "execution_order": ["TASK-001", "TASK-002", "TASK-003"],
    }

    client = FakeLLMClient(responses=[plan_with_frontend, plan_all_backend])
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client, sleep_fn=lambda s: None)

    reqs = _sample_valid_requirements()
    reqs.constraints = [
        "All backend code, including the HTML/JS page, must reside in a single file named app.py."
    ]

    state = create_initial_state(project_name="SingleFileApp", user_input="Build minimal app with one app file")
    state["requirements"] = reqs
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert len(client.calls) == 2
    # Verify retry prompt contains explicit ownership rejection
    retry_prompt = client.calls[1]["prompt"]
    assert "FrontendAgent must receive 0 tasks" in retry_prompt
    # Final plan tasks have 0 FrontendAgent tasks
    for task in result["task_plan"].tasks:
        assert task.assigned_agent != "FrontendAgent"


def test_task_planner_rejects_forbidden_integration_and_e2e_testing_tasks():
    """TaskPlanner rejects forbidden integration and e2e testing tasks when prohibited by constraints."""
    forbidden_plan = {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Implement backend",
                "description": "Create API endpoints.",
                "assigned_agent": "BackendAgent",
                "priority": 10,
                "dependencies": [],
                "acceptance_criteria": ["Endpoints return 200"],
                "status": "pending",
            },
            {
                "task_id": "TASK-002",
                "title": "Validate full application integration and concurrency",
                "description": "Perform end-to-end integration and concurrency testing.",
                "assigned_agent": "QAAgent",
                "priority": 20,
                "dependencies": ["TASK-001"],
                "acceptance_criteria": ["Simulated concurrent requests pass"],
                "status": "pending",
            },
        ],
        "execution_order": ["TASK-001", "TASK-002"],
    }

    valid_unit_plan = {
        "tasks": [
            {
                "task_id": "TASK-001",
                "title": "Implement backend",
                "description": "Create API endpoints.",
                "assigned_agent": "BackendAgent",
                "priority": 10,
                "dependencies": [],
                "acceptance_criteria": ["Endpoints return 200"],
                "status": "pending",
            },
            {
                "task_id": "TASK-002",
                "title": "Write unit tests for API endpoints",
                "description": "Create unit tests in test_app.py using test client.",
                "assigned_agent": "QAAgent",
                "priority": 20,
                "dependencies": ["TASK-001"],
                "acceptance_criteria": ["Unit tests pass"],
                "status": "pending",
            },
        ],
        "execution_order": ["TASK-001", "TASK-002"],
    }

    client = FakeLLMClient(responses=[forbidden_plan, valid_unit_plan])
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client, sleep_fn=lambda s: None)

    reqs = _sample_valid_requirements()
    reqs.constraints = [
        "Do not use Docker, CI pipelines, authentication, HTTPS, load or end-to-end testing, or additional metrics.",
        "no integration or concurrency tests",
    ]

    state = create_initial_state(project_name="NoIntegrationApp", user_input="no load or end-to-end tests")
    state["requirements"] = reqs
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert len(client.calls) == 2
    retry_prompt = client.calls[1]["prompt"]
    assert "explicitly prohibited by constraints" in retry_prompt


def test_task_planner_enforces_maximum_task_count():
    """TaskPlanner enforces maximum task count constraint and rejects plans exceeding limit."""
    too_many_tasks = [
        {
            "task_id": f"TASK-00{i}",
            "title": f"Task {i}",
            "description": f"Description for task {i}",
            "assigned_agent": "BackendAgent",
            "priority": i * 10,
            "dependencies": [f"TASK-00{i-1}"] if i > 1 else [],
            "acceptance_criteria": [f"Criteria {i}"],
            "status": "pending",
        }
        for i in range(1, 8)  # 7 tasks
    ]
    plan_7_tasks = {
        "tasks": too_many_tasks,
        "execution_order": [f"TASK-00{i}" for i in range(1, 8)],
    }

    within_limit_tasks = [
        {
            "task_id": f"TASK-00{i}",
            "title": f"Task {i}",
            "description": f"Description for task {i}",
            "assigned_agent": "BackendAgent",
            "priority": i * 10,
            "dependencies": [f"TASK-00{i-1}"] if i > 1 else [],
            "acceptance_criteria": [f"Criteria {i}"],
            "status": "pending",
        }
        for i in range(1, 5)  # 4 tasks
    ]
    plan_4_tasks = {
        "tasks": within_limit_tasks,
        "execution_order": [f"TASK-00{i}" for i in range(1, 5)],
    }

    client = FakeLLMClient(responses=[plan_7_tasks, plan_4_tasks])
    agent = TaskPlannerAgent(model_name="test-model", llm_client=client, sleep_fn=lambda s: None)

    reqs = _sample_valid_requirements()
    reqs.constraints = ["Planning must consist of no more than six tasks."]

    state = create_initial_state(project_name="LimitedApp", user_input="Plan at most 6 tasks total.")
    state["requirements"] = reqs
    state["architecture"] = _sample_valid_architecture()

    result = agent.execute(state)
    assert result["workflow_status"] == STATUS_PLANNING_COMPLETE
    assert len(client.calls) == 2
    retry_prompt = client.calls[1]["prompt"]
    assert "exceeding maximum allowed limit of 6 tasks" in retry_prompt
    assert len(result["task_plan"].tasks) <= 6

