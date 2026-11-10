"""
tools/qa/test_runner.py

Test runner protocol, result dataclass, FakeTestRunner test double,
and PytestTestRunner delegating execution to CodeExecutionSandbox.

Source of truth:
  - design.md Section 1.6 (QAAgent Tools Required: Test runner)
  - design.md Section 4.6 (QAAgent Tools table)
  - design.md Section 6.1 (tools/qa/test_runner.py owned by QA team)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, Union

from state.models import CodeArtifact, TestArtifact
from tools.debug.sandbox import (
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_SANDBOX_TIMEOUT_SECONDS,
    CodeExecutionSandbox,
    LocalExecutionSandbox,
)

_MODULE_NOT_FOUND_RE = re.compile(
    r"ModuleNotFoundError:\s*No module named\s+['\"]([^'\"]+)['\"]"
)
_FAILED_NODE_RE = re.compile(r"^FAILED\s+(\S+)", re.MULTILINE)
_ERROR_NODE_RE = re.compile(r"^ERROR\s+(?:collecting\s+)?(\S+)", re.MULTILINE)
_VERBOSE_FAIL_RE = re.compile(r"^(\S+::\S+)\s+(?:FAILED|ERROR)\b", re.MULTILINE)
_SUMMARY_BLOCK_RE = re.compile(r"=+\s*(.+?)\s+in\s+[\d.]+s\s*=+")
_PASSED_CNT_RE = re.compile(r"\b(\d+)\s+passed\b")
_FAILED_CNT_RE = re.compile(r"\b(\d+)\s+failed\b")
_ERROR_CNT_RE = re.compile(r"\b(\d+)\s+error(?:s)?\b")
_XFAILED_CNT_RE = re.compile(r"\b(\d+)\s+xfailed\b")
_XPASSED_CNT_RE = re.compile(r"\b(\d+)\s+xpassed\b")
_COVERAGE_TOTAL_RE = re.compile(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%", re.MULTILINE)


@dataclass
class TestRunResult:
    """Structured report returned from test runner execution."""

    __test__ = False
    passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_seconds: float
    output: str
    failing_tests: List[str] = field(default_factory=list)
    coverage_percentage: Optional[float] = None


class TestRunner(Protocol):
    """Protocol for executing tests against code artifacts."""

    def run_tests(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Dict[str, TestArtifact],
    ) -> TestRunResult:
        """Run generated test artifacts against code artifacts and return structured result."""
        ...


class FakeTestRunner:
    """Test double implementing TestRunner returning scripted results in sequential order."""

    def __init__(
        self,
        results: Optional[List[Union[TestRunResult, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[TestRunResult, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[TestRunResult, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted test run results."""
        self.results = list(results)

    def run_tests(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Dict[str, TestArtifact],
    ) -> TestRunResult:
        """Record invocation and return next scripted result in order."""
        self.calls.append(
            {
                "code_artifacts": code_artifacts,
                "test_artifacts": test_artifacts,
            }
        )
        if not self.results:
            return TestRunResult(
                passed=True,
                total_tests=1,
                passed_tests=1,
                failed_tests=0,
                duration_seconds=0.01,
                output="1 passed in 0.01s",
                failing_tests=[],
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return TestRunResult(**next_res)
        return next_res


def _artifact_rel_path(artifact: Any) -> str:
    raw = artifact.get("file_path", "") if isinstance(artifact, dict) else getattr(artifact, "file_path", "")
    normalized = str(raw).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _extract_artifact_module_names(
    code_artifacts: Dict[str, CodeArtifact],
    test_artifacts: Dict[str, TestArtifact],
) -> Set[str]:
    """Collect module and package identifiers defined by the code/test artifacts themselves."""
    local_modules: Set[str] = set()
    for collection in (code_artifacts or {}).values(), (test_artifacts or {}).values():
        for art in collection:
            rel_posix = _artifact_rel_path(art)
            if not rel_posix.lower().endswith(".py"):
                continue
            no_ext = rel_posix[:-3]
            parts = [p for p in no_ext.split("/") if p]
            if not parts:
                continue
            mod_parts = parts[:-1] if parts[-1] == "__init__" else parts
            for idx in range(len(mod_parts)):
                local_modules.add(".".join(mod_parts[: idx + 1]))
            stem = Path(rel_posix).stem
            if stem and stem not in ("__init__", "conftest"):
                local_modules.add(stem)
    return local_modules


def _find_missing_third_party_module(
    output_text: str,
    local_modules: Set[str],
) -> Optional[Tuple[str, str]]:
    """Return (full_module, top_package) if a missing module is third-party, else None."""
    for mod_name in _MODULE_NOT_FOUND_RE.findall(output_text or ""):
        top_pkg = mod_name.split(".", 1)[0]
        if mod_name in local_modules or top_pkg in local_modules:
            continue
        return mod_name, top_pkg
    return None


def _parse_pytest_output(
    output_text: str,
    exit_code: int,
) -> Tuple[int, int, int, List[str], Optional[float]]:
    """Parse test counts, failing nodeids, and optional coverage from pytest output."""
    failing: List[str] = []
    seen_fail: Set[str] = set()
    for pattern in (_FAILED_NODE_RE, _ERROR_NODE_RE, _VERBOSE_FAIL_RE):
        for match in pattern.findall(output_text):
            cleaned = match.strip()
            if cleaned and cleaned not in seen_fail:
                seen_fail.add(cleaned)
                failing.append(cleaned)

    passed_cnt = 0
    failed_cnt = 0
    error_cnt = 0
    xfailed_cnt = 0
    xpassed_cnt = 0

    summary_matches = _SUMMARY_BLOCK_RE.findall(output_text)
    if summary_matches:
        summary = summary_matches[-1]
        if m := _PASSED_CNT_RE.search(summary):
            passed_cnt = int(m.group(1))
        if m := _FAILED_CNT_RE.search(summary):
            failed_cnt = int(m.group(1))
        if m := _ERROR_CNT_RE.search(summary):
            error_cnt = int(m.group(1))
        if m := _XFAILED_CNT_RE.search(summary):
            xfailed_cnt = int(m.group(1))
        if m := _XPASSED_CNT_RE.search(summary):
            xpassed_cnt = int(m.group(1))
    else:
        if m := _PASSED_CNT_RE.search(output_text):
            passed_cnt = int(m.group(1))
        if m := _FAILED_CNT_RE.search(output_text):
            failed_cnt = int(m.group(1))
        if m := _ERROR_CNT_RE.search(output_text):
            error_cnt = int(m.group(1))

    passed_tests = passed_cnt + xfailed_cnt
    failed_tests = failed_cnt + error_cnt + xpassed_cnt
    if exit_code in (1, 2) and failed_tests == 0:
        failed_tests = max(1, len(failing))

    total_tests = passed_tests + failed_tests
    cov_match = _COVERAGE_TOTAL_RE.search(output_text)
    coverage_pct = float(cov_match.group(1)) if cov_match else None
    return total_tests, passed_tests, failed_tests, failing, coverage_pct


class PytestTestRunner:
    """Real TestRunner running pytest inside a hardened CodeExecutionSandbox."""

    def __init__(
        self,
        sandbox: Optional[CodeExecutionSandbox] = None,
        timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        self.sandbox: CodeExecutionSandbox = (
            sandbox
            if sandbox is not None
            else LocalExecutionSandbox(
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
            )
        )

    def run_tests(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Dict[str, TestArtifact],
    ) -> TestRunResult:
        """Execute test_artifacts against code_artifacts and return structured TestRunResult."""
        result = self.sandbox.execute_in_sandbox(
            code_artifacts=code_artifacts,
            test_artifacts=test_artifacts,
        )

        combined_output = result.output or "\n".join(
            part for part in (result.stdout, result.stderr) if part
        )

        if result.refused:
            return TestRunResult(
                passed=False,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                duration_seconds=result.duration_seconds,
                output=combined_output,
                failing_tests=["ast_tripwire"],
            )

        if result.timed_out:
            return TestRunResult(
                passed=False,
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                duration_seconds=result.duration_seconds,
                output=combined_output,
                failing_tests=["timeout"],
            )

        if result.exit_code in (3, 4):
            raise RuntimeError(
                f"Pytest execution failed internally (exit code {result.exit_code}): {combined_output}"
            )

        if not result.passed:
            search_blob = "\n".join(
                part for part in (result.stdout, result.stderr, result.output) if part
            )
            local_modules = _extract_artifact_module_names(code_artifacts, test_artifacts)
            missing = _find_missing_third_party_module(search_blob, local_modules)
            if missing is not None:
                mod_name, top_pkg = missing
                raise RuntimeError(
                    f"Missing third-party module '{mod_name}': install {top_pkg} in your venv"
                )

        if result.exit_code == 5:
            return TestRunResult(
                passed=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                duration_seconds=result.duration_seconds,
                output=combined_output,
                failing_tests=["no_tests_collected"],
            )

        total_tests, passed_tests, failed_tests, failing, coverage_pct = _parse_pytest_output(
            output_text=combined_output,
            exit_code=result.exit_code,
        )

        return TestRunResult(
            passed=(result.passed and result.exit_code == 0),
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            duration_seconds=result.duration_seconds,
            output=combined_output,
            failing_tests=failing,
            coverage_percentage=coverage_pct,
        )

