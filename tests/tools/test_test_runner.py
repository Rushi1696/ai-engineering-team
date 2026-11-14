"""
tests/tools/test_test_runner.py

Unit and real temp-directory execution tests for LocalExecutionSandbox and PytestTestRunner.
All real tmp-dir runs operate offline with scrubbed environments and zero API keys.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sys
import tempfile
import time
from unittest.mock import patch
import pytest

from agents.qa_agent import QAAgent, STATUS_DEBUG_REQUIRED
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    ImplementationTask,
    ProductRequirements,
    SystemArchitecture,
    TaskPlan,
    TestArtifact,
)
from tools.debug.sandbox import (
    FakeExecutionSandbox,
    LocalExecutionSandbox,
    SandboxExecutionResult,
    _BoundedHeadTailCapture,
    build_scrubbed_env,
)
from tools.qa.test_runner import PytestTestRunner, TestRunResult
from tools.shared.llm_client import FakeLLMClient

TestArtifact.__test__ = False


def _code(path: str, content: str, artifact_type: str = "backend") -> CodeArtifact:
    return CodeArtifact(
        file_path=path,
        language="python",
        content=content,
        artifact_type=artifact_type,
        produced_by="BackendAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _test(path: str, content: str) -> TestArtifact:
    return TestArtifact(
        file_path=path,
        content=content,
        test_type="unit",
        coverage_target="app.py",
        produced_by="QAAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process PID is currently alive on Windows or POSIX."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Mocked / unit tests
# ---------------------------------------------------------------------------


def test_pytest_runner_parses_passing_report_via_fake_sandbox():
    """PytestTestRunner maps passing pytest summary into TestRunResult."""
    fake_sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=True,
                exit_code=0,
                stdout="=== 3 passed in 0.14s ===",
                stderr="",
                output="=== 3 passed in 0.14s ===",
                duration_seconds=0.14,
            )
        ]
    )
    runner = PytestTestRunner(sandbox=fake_sandbox)
    res = runner.run_tests({"app.py": _code("app.py", "x = 1\n")}, {"test_app.py": _test("test_app.py", "def test_x(): assert True\n")})

    assert res.passed is True
    assert res.total_tests == 3
    assert res.passed_tests == 3
    assert res.failed_tests == 0
    assert res.failing_tests == []
    assert res.coverage_percentage is None


def test_pytest_runner_parses_failing_tests_and_counts():
    """PytestTestRunner extracts failing test nodeids and failed/passed counts."""
    output = (
        "FAILED tests/test_api.py::test_create_todo - assert 500 == 201\n"
        "FAILED tests/test_api.py::test_delete_todo - assert 404 == 204\n"
        "=== 2 failed, 4 passed in 0.25s ==="
    )
    fake_sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=False,
                exit_code=1,
                stdout=output,
                stderr="",
                output=output,
                duration_seconds=0.25,
            )
        ]
    )
    runner = PytestTestRunner(sandbox=fake_sandbox)
    res = runner.run_tests({"app.py": _code("app.py", "")}, {"tests/test_api.py": _test("tests/test_api.py", "")})

    assert res.passed is False
    assert res.total_tests == 6
    assert res.passed_tests == 4
    assert res.failed_tests == 2
    assert res.failing_tests == [
        "tests/test_api.py::test_create_todo",
        "tests/test_api.py::test_delete_todo",
    ]


def test_pytest_runner_raises_on_missing_third_party_module():
    """Missing non-artifact dependency raises tool exception instructing venv install."""
    err_out = (
        "ERROR collecting tests/test_api.py\n"
        "E   ModuleNotFoundError: No module named 'flask_cors'\n"
        "=== 1 error in 0.05s ==="
    )
    fake_sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=False,
                exit_code=2,
                stdout=err_out,
                stderr="",
                output=err_out,
                duration_seconds=0.05,
            )
        ]
    )
    runner = PytestTestRunner(sandbox=fake_sandbox)
    with pytest.raises(RuntimeError, match="install flask_cors in your venv"):
        runner.run_tests(
            {"app.py": _code("app.py", "import flask_cors\n")},
            {"tests/test_api.py": _test("tests/test_api.py", "import app\n")},
        )


def test_pytest_runner_keeps_missing_local_module_as_failed_report():
    """ModuleNotFoundError referencing an artifact's own module namespace stays a failed TestRunResult."""
    err_out = (
        "ERROR collecting tests/test_api.py\n"
        "E   ModuleNotFoundError: No module named 'my_pkg.missing_sub'\n"
        "=== 1 error in 0.04s ==="
    )
    fake_sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=False,
                exit_code=2,
                stdout=err_out,
                stderr="",
                output=err_out,
                duration_seconds=0.04,
            )
        ]
    )
    runner = PytestTestRunner(sandbox=fake_sandbox)
    res = runner.run_tests(
        {"my_pkg/app.py": _code("my_pkg/app.py", "def ok(): return 1\n")},
        {"tests/test_api.py": _test("tests/test_api.py", "from my_pkg.missing_sub import x\n")},
    )

    assert res.passed is False
    assert res.failed_tests >= 1
    assert "No module named 'my_pkg.missing_sub'" in res.output


def test_pytest_runner_raises_on_internal_or_usage_error():
    """Pytest exit codes 3 and 4 raise RuntimeError to trigger agent exhaustion."""
    for exit_code in (3, 4):
        fake_sandbox = FakeExecutionSandbox(
            results=[
                SandboxExecutionResult(
                    passed=False,
                    exit_code=exit_code,
                    stdout="",
                    stderr="pytest internal usage error",
                    output="pytest internal usage error",
                    duration_seconds=0.01,
                )
            ]
        )
        runner = PytestTestRunner(sandbox=fake_sandbox)
        with pytest.raises(RuntimeError, match=f"exit code {exit_code}"):
            runner.run_tests({}, {"test_a.py": _test("test_a.py", "")})


def test_qa_agent_routes_missing_third_party_to_exhaustion_and_failure_to_debug():
    """QAAgent sends third-party install errors to exhaustion and failed TestRunResult to DebugAgent."""
    qa_llm_payload = {
        "test_plan": {
            "strategy": "Unit tests",
            "coverage_goals": {"app.py": "80%"},
            "test_suites": ["unit"],
            "quality_metrics": {"target": 1.0},
        },
        "test_artifacts": [
            {
                "file_path": "tests/test_app.py",
                "content": "import missing_third_party_pkg\ndef test_ok(): assert True\n",
                "test_type": "unit",
                "coverage_target": "app.py",
            }
        ],
        "ai_findings": [],
    }
    base_state = create_initial_state(project_name="Todo", user_input="Todo app")
    base_state["requirements"] = ProductRequirements(
        functional_requirements=["Todos"],
        non_functional_requirements=["Fast"],
        user_stories=["As user"],
        acceptance_criteria=["Pass"],
        constraints=["Python"],
        business_goals=["MVP"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
    )
    base_state["architecture"] = SystemArchitecture(
        overview="Flask",
        technology_stack={"backend": "Flask"},
        api_specifications=[{"endpoint": "/todos", "method": "GET", "description": "List todos"}],
        database_design={},
        security_architecture={},
        scalability_strategy="Simple",
        deployment_model="Local",
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
    )
    base_state["task_plan"] = TaskPlan(
        tasks=[
            ImplementationTask(
                task_id="T1",
                title="QA",
                description="Run tests",
                assigned_agent="QAAgent",
                priority=1,
                dependencies=[],
                acceptance_criteria=["Pass"],
            )
        ],
        execution_order=["T1"],
        produced_by="TaskPlannerAgent",
        produced_at="2026-01-01T00:00:00Z",
    )
    base_state["code_artifacts"] = {"app.py": _code("app.py", "def f(): return 1\n")}

    # Case A: third-party missing module raises -> QAAgent exhaustion
    missing_pkg_sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=False,
                exit_code=2,
                output="ModuleNotFoundError: No module named 'missing_third_party_pkg'",
            )
        ]
    )
    agent_exhaust = QAAgent(
        model_name="m",
        llm_client=FakeLLMClient(responses=[qa_llm_payload]),
        test_runner=PytestTestRunner(sandbox=missing_pkg_sandbox),
    )
    res_exhaust = agent_exhaust.execute(dict(base_state))
    assert res_exhaust["human_approval_required"] is True
    assert "install missing_third_party_pkg in your venv" in res_exhaust["errors"][0].message

    # Case B: normal failed test report (or timeout) -> DebugAgent
    timeout_sandbox = FakeExecutionSandbox(
        results=[
            SandboxExecutionResult(
                passed=False,
                exit_code=-1,
                output="Execution timed out after 5s",
                timed_out=True,
            )
        ]
    )
    agent_debug = QAAgent(
        model_name="m",
        llm_client=FakeLLMClient(responses=[qa_llm_payload]),
        test_runner=PytestTestRunner(sandbox=timeout_sandbox),
    )
    res_debug = agent_debug.execute(dict(base_state))
    assert res_debug["workflow_status"] == STATUS_DEBUG_REQUIRED
    assert res_debug["next_agent"] == "DebugAgent"
    assert "timed out after 5s" in res_debug["errors"][0].message


def test_scrubbed_env_excludes_parent_secrets_and_dotenv_keys(monkeypatch, tmp_path: Path):
    """Scrubbed subprocess env drops parent API keys and points TEMP/HOME to sandbox dir."""
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini-123")
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq-456")
    monkeypatch.setenv("CUSTOM_SECRET_TOKEN", "top-secret-xyz")

    env = build_scrubbed_env(tmp_path)
    assert "GEMINI_API_KEY" not in env
    assert "GROQ_API_KEY" not in env
    assert "CUSTOM_SECRET_TOKEN" not in env
    assert env["PYTHONPATH"] == str(tmp_path)
    assert env["TEMP"] == str(tmp_path)
    assert env["TMP"] == str(tmp_path)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_bounded_capture_keeps_head_and_tail_never_unbounded():
    """_BoundedHeadTailCapture retains head and tail cleanly under massive streaming input."""
    cap = _BoundedHeadTailCapture(max_chars=200)
    cap.append("HEAD_START_" + ("A" * 50))
    for i in range(5000):
        cap.append(f"LINE_{i:05d}_" + ("B" * 200))
    cap.append("_TAIL_SUMMARY_FINAL")

    rendered = cap.render()
    assert len(rendered) <= 200
    assert rendered.startswith("HEAD_START_")
    assert "output truncated" in rendered
    assert rendered.endswith("_TAIL_SUMMARY_FINAL")


# ---------------------------------------------------------------------------
# Real tmp-dir / subprocess tests (no network, no API keys)
# ---------------------------------------------------------------------------


def test_real_pytest_runner_passes_generated_code_with_same_interpreter_and_scrubbed_env(monkeypatch):
    """Real pytest execution uses sys.executable, excludes secrets, passes, and deletes temp dir."""
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "should-not-leak-groq")

    code_artifacts = {
        "calc.py": _code("calc.py", "def add(a, b):\n    return a + b\n"),
    }
    test_artifacts = {
        "tests/test_calc.py": _test(
            "tests/test_calc.py",
            (
                "import os\n"
                "import pathlib\n"
                "import sys\n"
                "from calc import add\n\n"
                "def test_add_and_isolation():\n"
                "    assert add(2, 3) == 5\n"
                "    assert 'GEMINI_API_KEY' not in os.environ\n"
                "    assert 'GROQ_API_KEY' not in os.environ\n"
                f"    assert pathlib.Path(sys.executable).resolve() == pathlib.Path({sys.executable!r}).resolve()\n"
            ),
        ),
    }

    before_dirs = set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*"))
    runner = PytestTestRunner()
    res = runner.run_tests(code_artifacts, test_artifacts)
    after_dirs = set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*"))

    assert res.passed is True
    assert res.total_tests == 1
    assert res.passed_tests == 1
    assert res.failed_tests == 0
    assert res.failing_tests == []
    assert after_dirs == before_dirs


def test_real_pytest_runner_missing_third_party_pkg_raises_install_error():
    """Real pytest failing on missing third-party package raises tool exception with install hint."""
    code_artifacts = {
        "app.py": _code("app.py", "import imaginary_nonexistent_pkg_98765\n"),
    }
    test_artifacts = {
        "test_app.py": _test("test_app.py", "import app\ndef test_ok(): assert True\n"),
    }

    runner = PytestTestRunner()
    with pytest.raises(RuntimeError, match="install imaginary_nonexistent_pkg_98765 in your venv"):
        runner.run_tests(code_artifacts, test_artifacts)


def test_real_pytest_runner_missing_local_module_returns_failed_report():
    """Real pytest failing on missing local artifact module returns failed TestRunResult (not crash)."""
    code_artifacts = {
        "my_local_pkg/core.py": _code("my_local_pkg/core.py", "VAL = 42\n"),
    }
    test_artifacts = {
        "tests/test_core.py": _test(
            "tests/test_core.py",
            "from my_local_pkg.nonexistent_helper import fn\ndef test_ok(): assert fn() == 42\n",
        ),
    }

    runner = PytestTestRunner()
    res = runner.run_tests(code_artifacts, test_artifacts)

    assert res.passed is False
    assert res.failed_tests >= 1
    assert "No module named 'my_local_pkg.nonexistent_helper'" in res.output


@pytest.mark.parametrize(
    "bad_path",
    [
        "../evil.py",
        r"..\..\evil.py",
        "/etc/passwd",
        r"C:\evil.py",
        "C:evil.py",
        r"\\server\share\evil.py",
        "app.py:stream",
        "app.py.",
        "app.py ",
        "dir /app.py",
        "dir./app.py",
        "CON",
        "CON.txt",
        "subdir/nul.py",
        "COM1.json",
        "PROGRA~1/app.py",
        "test~1.py",
    ],
)
def test_real_pytest_runner_rejects_path_traversal_and_special_windows_paths(bad_path: str):
    """Path validator rejects traversal, absolute/drive paths, trailing dots/spaces, devices, and ~."""
    before_dirs = set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*"))
    runner = PytestTestRunner()
    with pytest.raises(ValueError, match="Unsafe artifact path"):
        runner.run_tests(
            {"good.py": _code("good.py", "x = 1\n")},
            {"bad": _test(bad_path, "def test_bad(): assert True\n")},
        )
    assert set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*")) == before_dirs


def test_real_pytest_runner_rejects_within_set_duplicate_paths():
    """Duplicate relative paths within code_artifacts or within test_artifacts raise ValueError."""
    before_dirs = set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*"))
    runner = PytestTestRunner()

    # 1. Within code_artifacts
    with pytest.raises(ValueError, match="Duplicate relative code artifact path"):
        runner.run_tests(
            {"c1": _code("app.py", "x = 1\n"), "c2": _code("APP.PY", "x = 2\n")},
            {"t": _test("test_other.py", "def test_other(): assert True\n")},
        )

    # 2. Within test_artifacts
    with pytest.raises(ValueError, match="Duplicate relative test artifact path"):
        runner.run_tests(
            {"c": _code("app.py", "x = 1\n")},
            {"t1": _test("test_app.py", "def test_1(): assert True\n"), "t2": _test(r"TEST_APP.PY", "def test_2(): assert True\n")},
        )
    assert set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*")) == before_dirs


@pytest.mark.parametrize(
    "code_path,test_path",
    [
        ("test_app.py", "TEST_APP.py"),
        ("src/test_utils.py", r"SRC\test_utils.py"),
    ],
)
def test_real_pytest_runner_cross_set_duplicate_runs_test_version_with_note(code_path: str, test_path: str):
    """When a path exists in both code and test artifacts, test artifact wins and note is emitted."""
    runner = PytestTestRunner()
    # Code version would fail test run if executed; test version passes
    code_artifacts = {"c": _code(code_path, "def test_clash(): assert False\n")}
    test_artifacts = {"t": _test(test_path, "def test_clash(): assert True\n")}

    res = runner.run_tests(code_artifacts, test_artifacts)
    assert res.passed is True
    assert res.passed_tests == 1
    # Normalized test path used in note
    from tools.debug.sandbox import validate_relative_artifact_path
    expected_path = "/".join(validate_relative_artifact_path(test_path))
    expected_note = f"[sandbox] test file {expected_path} replaced code artifact with the same path"
    assert expected_note in res.output


def test_debug_sandbox_call_with_cross_set_clash_runs_test_version_and_emits_note():
    """Debug sandbox direct execution with cross-set clash drops code artifact and emits note."""
    sandbox = LocalExecutionSandbox()
    code_artifacts = {"c": _code("test_app.py", "def test_run(): assert False\n")}
    test_artifacts = {"t": _test("test_app.py", "def test_run(): assert True\n")}

    res = sandbox.execute_in_sandbox(code_artifacts=code_artifacts, test_artifacts=test_artifacts)
    assert res.passed is True
    assert "[sandbox] test file test_app.py replaced code artifact with the same path" in res.output
    assert "[sandbox] test file test_app.py replaced code artifact with the same path" in res.stdout



def test_real_pytest_runner_timeout_kills_process_tree_deletes_tempdir_and_returns_failed_result():
    """Timeout kills entire process tree, deletes temp dir, and returns failed TestRunResult."""
    # 1. Default PytestTestRunner with AST tripwire active (pure time.sleep hang)
    before_dirs = set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*"))
    runner = PytestTestRunner(timeout_seconds=1.0)
    res = runner.run_tests(
        {},
        {"test_hang.py": _test("test_hang.py", "import time\ndef test_hang():\n    time.sleep(15)\n")},
    )
    assert res.passed is False
    assert "timed out after 1s" in res.output
    assert set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*")) == before_dirs

    # 2. Explicitly verify grandchild process-tree termination on Windows/POSIX
    tree_sandbox = LocalExecutionSandbox(timeout_seconds=2.5, enable_ast_tripwire=False)
    tree_test_code = (
        "import subprocess, sys, time\n"
        "def test_spawn_tree():\n"
        "    child = subprocess.Popen(\n"
        "        [sys.executable, '-c', 'import time; time.sleep(30)'],\n"
        "        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    )\n"
        "    print(f'CHILD_PID={child.pid}', flush=True)\n"
        "    time.sleep(30)\n"
    )
    tree_res = tree_sandbox.execute_in_sandbox(
        {},
        {"test_tree.py": _test("test_tree.py", tree_test_code)},
        command="pytest -s --tb=short -p no:cacheprovider test_tree.py",
    )
    assert tree_res.passed is False
    assert tree_res.timed_out is True
    assert "timed out after 2.5s" in tree_res.output
    assert set(Path(tempfile.gettempdir()).glob("ai_eng_sandbox_*")) == before_dirs

    m = re.search(r"CHILD_PID=(\d+)", tree_res.output)
    assert m is not None, f"Expected CHILD_PID in output: {tree_res.output!r}"
    child_pid = int(m.group(1))
    # Give OS a tiny moment to finish reaping handle
    deadline = time.monotonic() + 2.0
    while _is_pid_alive(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _is_pid_alive(child_pid), f"Grandchild PID {child_pid} survived timeout tree-kill!"


def test_real_pytest_runner_caps_runaway_output_preserving_summary():
    """Runaway stdout is capped to max_output_chars while retaining failing summary tail."""
    spam_test = (
        "def test_spam():\n"
        "    for i in range(3000):\n"
        "        print('SPAM_LINE_' + 'X' * 50)\n"
        "    assert False, 'boom'\n"
    )
    runner = PytestTestRunner(max_output_chars=2500)
    res = runner.run_tests({}, {"test_spam.py": _test("test_spam.py", spam_test)})

    assert res.passed is False
    assert len(res.output) <= 2500
    assert res.failed_tests == 1
    assert any("test_spam" in t for t in res.failing_tests)


@pytest.mark.parametrize(
    "snippet",
    [
        "import socket",
        "import subprocess",
        "import ctypes",
        "import requests",
        "import urllib.request",
        "import http.client",
        "from http import client",
        "import ftplib",
        "import smtplib",
        "import os\nos.system('echo hi')",
        "import os\nos.remove('x')",
        "import os\nos.rmdir('x')",
        "import shutil\nshutil.rmtree('x')",
        "x = eval('1+1')",
        "exec('x = 1')",
    ],
)
def test_real_pytest_runner_ast_tripwire_blocks_forbidden_imports_and_calls(snippet: str):
    """AST tripwire refuses execution without spawning any subprocess."""
    runner = PytestTestRunner()
    with patch("tools.debug.sandbox.subprocess.Popen") as popen_spy:
        res = runner.run_tests(
            {},
            {"test_unsafe.py": _test("test_unsafe.py", f"{snippet}\ndef test_x(): assert True\n")},
        )
    popen_spy.assert_not_called()
    assert res.passed is False
    assert "AST safety tripwire refused execution" in res.output


def test_scan_test_artifacts_safety_detects_forbidden_test_code():
    """scan_test_artifacts_safety detects forbidden imports and calls in test artifacts."""
    from tools.qa.test_runner import scan_test_artifacts_safety

    arts = [
        _test("tests/test_ok.py", "def test_ok(tmp_path): assert True"),
        _test("tests/test_bad.py", "import subprocess\nimport os\n\ndef test_bad():\n    os.remove('x')\n"),
    ]
    violations = scan_test_artifacts_safety(arts)
    assert len(violations) == 2
    assert "tests/test_bad.py:1: forbidden import 'subprocess'" in violations[0]
    assert "tests/test_bad.py:5: forbidden call 'os.remove()'" in violations[1]

