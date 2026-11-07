"""
tests/workflows/test_staged_run.py

Unit tests for staged workflow execution (--stop-after) and artifact state dumping (--dump-state).
Ensures checkpoints contain expected stage outputs, exit codes reflect completion,
and no environment secrets or API keys are ever leaked into dump files.
"""

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from app import _dump_state_artifacts, build_scripted_llm_responses, main, run_cli
from state.models import ErrorRecord
from tools.shared.llm_client import FakeLLMClient
from workflows.factory import create_agents
from workflows.runner import PipelineIncompleteError, run_pipeline


def test_runner_stop_after_product_manager_exits_cleanly():
    """run_pipeline halts cleanly after ProductManagerAgent when stop_after is set."""
    scripted_client = FakeLLMClient(responses=build_scripted_llm_responses())
    agents = create_agents(llm_client=scripted_client)

    final_state = run_pipeline(
        project_name="Stop After PM Test",
        user_input="Build web application",
        agents=agents,
        stop_after="ProductManagerAgent",
    )

    assert final_state["workflow_status"] == "requirements_complete"
    assert final_state.get("requirements") is not None
    assert final_state.get("architecture") is None
    assert final_state.get("task_plan") is None
    assert final_state.get("stopped_after") == "ProductManagerAgent"
    assert final_state["total_invocations"] == 1


def test_runner_stop_after_task_planner_exits_cleanly():
    """run_pipeline halts cleanly after TaskPlannerAgent when stop_after='task_planner'."""
    scripted_client = FakeLLMClient(responses=build_scripted_llm_responses())
    agents = create_agents(llm_client=scripted_client)

    final_state = run_pipeline(
        project_name="Stop After TaskPlanner Test",
        user_input="Build web application",
        agents=agents,
        stop_after="task_planner",
    )

    assert final_state["workflow_status"] == "planning_complete"
    assert final_state.get("requirements") is not None
    assert final_state.get("architecture") is not None
    assert final_state.get("task_plan") is not None
    assert final_state.get("code_artifacts") == {}
    assert final_state.get("stopped_after") == "task_planner"
    assert final_state["total_invocations"] == 3


def test_stop_after_checkpointed_state_contains_agent_output():
    """Checkpointed state after --stop-after contains full stamped artifacts for executed agents."""
    scripted_client = FakeLLMClient(responses=build_scripted_llm_responses())
    agents = create_agents(llm_client=scripted_client)

    final_state = run_pipeline(
        project_name="Checkpoint Verification Test",
        user_input="Build web application",
        agents=agents,
        stop_after="TaskPlannerAgent",
    )

    # Requirements
    req = final_state.get("requirements")
    assert req is not None
    assert req.produced_by == "ProductManagerAgent"
    assert req.version == 1

    # Architecture
    arch = final_state.get("architecture")
    assert arch is not None
    assert arch.produced_by == "ArchitectAgent"
    assert arch.version == 1

    # TaskPlan
    plan = final_state.get("task_plan")
    assert plan is not None
    assert plan.produced_by == "TaskPlannerAgent"
    assert plan.version == 1
    assert len(plan.tasks) > 0
    assert all(t.status == "pending" for t in plan.tasks)


def test_runner_stop_after_invalid_agent_raises_value_error():
    """run_pipeline raises ValueError if an unknown agent or node name is passed to stop_after."""
    agents = create_agents(provider="fake")
    with pytest.raises(ValueError, match="Unknown agent or node for stop_after: 'NonExistentAgent'"):
        run_pipeline(
            project_name="Invalid Agent Test",
            user_input="Build web application",
            agents=agents,
            stop_after="NonExistentAgent",
        )


def test_app_dump_state_writes_artifacts_without_keys_or_env(tmp_path: Path, monkeypatch):
    """--dump-state exports json files and ensures zero API keys or environment values are leaked."""
    secret_key_1 = "secret-gemini-test-key-9999"
    secret_key_2 = "secret-groq-test-key-8888"
    monkeypatch.setenv("GEMINI_API_KEY", secret_key_1)
    monkeypatch.setenv("GROQ_API_KEY", secret_key_2)

    dump_dir = tmp_path / "dump_out"

    final_state = run_cli(
        project_name="Dump State Test",
        prompt="Build web application with secure credentials",
        stop_after="TaskPlannerAgent",
        dump_state=str(dump_dir),
    )

    assert (dump_dir / "requirements.json").exists()
    assert (dump_dir / "architecture.json").exists()
    assert (dump_dir / "task_plan.json").exists()
    assert (dump_dir / "errors.json").exists()

    # Verify no secret appears anywhere in any dumped file
    for file_path in dump_dir.glob("*.json"):
        content = file_path.read_text(encoding="utf-8")
        assert secret_key_1 not in content, f"Secret leaked in {file_path.name}"
        assert secret_key_2 not in content, f"Secret leaked in {file_path.name}"


def test_app_dump_state_on_failure_writes_errors_without_traceback(tmp_path: Path):
    """--dump-state records errors.json containing agent, error_type, message only (no traceback)."""
    dump_dir = tmp_path / "error_dump"

    failed_state = {
        "workflow_status": "failed",
        "current_agent": "ProductManagerAgent",
        "errors": [
            ErrorRecord(
                error_id="err-1",
                agent="ProductManagerAgent",
                stage="requirements",
                error_type="validation_failure",
                message="Invalid input requirements format",
                traceback="Traceback (most recent call last):\n  File 'agent.py', line 123 in run\n    raise ValueError()",
                occurred_at="2026-01-01T00:00:00Z",
            )
        ],
    }

    _dump_state_artifacts(failed_state, str(dump_dir))

    errors_file = dump_dir / "errors.json"
    assert errors_file.exists()

    errors_data = json.loads(errors_file.read_text(encoding="utf-8"))
    assert len(errors_data) == 1
    err = errors_data[0]
    assert err["agent"] == "ProductManagerAgent"
    assert err["error_type"] == "validation_failure"
    assert err["message"] == "Invalid input requirements format"
    # Ensure no traceback is included
    assert "traceback" not in err
    assert "Traceback" not in errors_file.read_text(encoding="utf-8")


def test_app_main_stop_after_returns_code_0_and_prints_line(capsys):
    """app.py main exits with code 0 and prints 'stopped after <Agent>' when stop-after is reached."""
    with patch("sys.argv", ["app.py", "--stop-after", "TaskPlannerAgent"]):
        exit_code = main()
        assert exit_code == 0

    captured = capsys.readouterr()
    assert "stopped after TaskPlannerAgent" in captured.out
