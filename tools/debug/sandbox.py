"""
tools/debug/sandbox.py

Code execution sandbox protocol, result dataclass, FakeExecutionSandbox test double,
and isolated LocalExecutionSandbox subprocess implementation.

Source of truth:
  - design.md Section 1.7 (DebugAgent Tools Required: Code execution sandbox)
  - design.md Section 4.7 (DebugAgent Tools table)
  - design.md Section 6.1 (tools/debug/ owned by Debug team)
"""

from __future__ import annotations

import ast
import codecs
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple, Union

from state.models import CodeArtifact, TestArtifact

DEFAULT_SANDBOX_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_OUTPUT_CHARS = 16000

_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_FORBIDDEN_IMPORTS = frozenset(
    {
        "socket",
        "subprocess",
        "ctypes",
        "requests",
        "urllib",
        "http.client",
        "ftplib",
        "smtplib",
    }
)

_FORBIDDEN_BARE_CALLS = frozenset({"eval", "exec"})
_FORBIDDEN_QUALIFIED_CALLS = frozenset(
    {
        ("os", "system"),
        ("os", "remove"),
        ("os", "rmdir"),
        ("shutil", "rmtree"),
    }
)


@dataclass
class SandboxExecutionResult:
    """Structured report returned from sandbox code execution."""

    __test__ = False
    passed: bool
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    refused: bool = False


class CodeExecutionSandbox(Protocol):
    """Protocol for safely executing code and tests in an isolated sandbox."""

    def execute_in_sandbox(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Optional[Dict[str, TestArtifact]] = None,
        command: Optional[str] = None,
    ) -> SandboxExecutionResult:
        """Run code and test artifacts inside an isolated sandbox and return result."""
        ...


class FakeExecutionSandbox:
    """Test double implementing CodeExecutionSandbox returning scripted results in order."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[SandboxExecutionResult, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[SandboxExecutionResult, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[SandboxExecutionResult, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted execution results."""
        self.results = list(results)

    def execute_in_sandbox(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Optional[Dict[str, TestArtifact]] = None,
        command: Optional[str] = None,
    ) -> SandboxExecutionResult:
        """Record invocation and return next scripted result in order."""
        self.calls.append(
            {
                "code_artifacts": code_artifacts,
                "test_artifacts": test_artifacts or {},
                "command": command,
            }
        )
        if not self.results:
            return SandboxExecutionResult(
                passed=True,
                exit_code=0,
                stdout="Sandbox test execution passed",
                stderr="",
                output="Sandbox test execution passed",
                duration_seconds=0.01,
            )

        next_res = self.results.pop(0)
        if isinstance(next_res, Exception):
            raise next_res
        if isinstance(next_res, dict):
            return SandboxExecutionResult(**next_res)
        return next_res


def _artifact_field(artifact: Any, field_name: str, default: Any = "") -> Any:
    """Read a field from a Pydantic artifact model or mapping dict."""
    if isinstance(artifact, dict):
        return artifact.get(field_name, default)
    return getattr(artifact, field_name, default)


def validate_relative_artifact_path(raw_path: str) -> Tuple[str, ...]:
    """Validate and split a relative artifact path, rejecting unsafe host/Windows patterns."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"Unsafe artifact path: empty path {raw_path!r}")
    if "\x00" in raw_path or any(ord(ch) < 32 for ch in raw_path):
        raise ValueError(f"Unsafe artifact path (control character): {raw_path!r}")
    if ":" in raw_path:
        raise ValueError(f"Unsafe artifact path (drive letter or colon): {raw_path!r}")
    if raw_path.startswith(("/", "\\")):
        raise ValueError(f"Unsafe artifact path (absolute or UNC path): {raw_path!r}")
    if PureWindowsPath(raw_path).is_absolute() or PurePosixPath(raw_path).is_absolute():
        raise ValueError(f"Unsafe artifact path (absolute path): {raw_path!r}")

    normalized = raw_path
    while normalized.startswith(("./", ".\\")):
        normalized = normalized[2:]
    if not normalized:
        raise ValueError(f"Unsafe artifact path (resolves to root): {raw_path!r}")

    segments = re.split(r"[/\\]", normalized)
    for seg in segments:
        if not seg:
            raise ValueError(f"Unsafe artifact path (empty path segment): {raw_path!r}")
        if seg == ".." or seg == ".":
            raise ValueError(f"Unsafe artifact path (directory traversal): {raw_path!r}")
        if seg.endswith(".") or seg.endswith(" "):
            raise ValueError(f"Unsafe artifact path (trailing dot or space): {raw_path!r}")
        if seg.startswith(" "):
            raise ValueError(f"Unsafe artifact path (leading space): {raw_path!r}")
        if "~" in seg:
            raise ValueError(f"Unsafe artifact path (short-name '~' segment): {raw_path!r}")
        base_name = seg.split(".", 1)[0].upper()
        if base_name in _WINDOWS_RESERVED_NAMES:
            raise ValueError(f"Unsafe artifact path (Windows reserved device name): {raw_path!r}")

    return tuple(segments)


def prepare_validated_artifacts(
    code_artifacts: Dict[str, CodeArtifact],
    test_artifacts: Optional[Dict[str, TestArtifact]] = None,
) -> Tuple[List[Tuple[Tuple[str, ...], str, str]], List[Tuple[Tuple[str, ...], str, str]]]:
    """Validate all code and test artifact paths and reject case-insensitive duplicates."""
    seen_lower: Dict[str, str] = {}
    validated_code: List[Tuple[Tuple[str, ...], str, str]] = []
    validated_tests: List[Tuple[Tuple[str, ...], str, str]] = []

    def _collect(items: Iterable[Any], target_list: List[Tuple[Tuple[str, ...], str, str]]) -> None:
        for art in items:
            raw_path = _artifact_field(art, "file_path", "")
            parts = validate_relative_artifact_path(raw_path)
            rel_posix = "/".join(parts)
            lower_key = rel_posix.lower()
            if lower_key in seen_lower:
                raise ValueError(
                    f"Duplicate relative artifact path (case-insensitive): "
                    f"{rel_posix!r} conflicts with {seen_lower[lower_key]!r}"
                )
            seen_lower[lower_key] = rel_posix
            content = _artifact_field(art, "content", "")
            if not isinstance(content, str):
                content = str(content)
            target_list.append((parts, rel_posix, content))

    _collect((code_artifacts or {}).values(), validated_code)
    _collect((test_artifacts or {}).values(), validated_tests)
    return validated_code, validated_tests


def _match_forbidden_module(module_name: str) -> Optional[str]:
    """Return the matched forbidden module rule if module_name targets one."""
    if not module_name:
        return None
    for forbidden in _FORBIDDEN_IMPORTS:
        if module_name == forbidden or module_name.startswith(f"{forbidden}."):
            return forbidden
    return None


def scan_python_ast_safety(
    artifacts: Iterable[Tuple[Tuple[str, ...], str, str]],
) -> List[str]:
    """Inspect Python artifact sources via AST for forbidden imports or calls."""
    violations: List[str] = []
    for _, rel_posix, content in artifacts:
        if not rel_posix.lower().endswith(".py"):
            continue
        try:
            tree = ast.parse(content, filename=rel_posix)
        except SyntaxError:
            # Let pytest report syntax errors as normal failed test runs.
            continue

        mod_aliases: Dict[str, str] = {}
        func_aliases: Dict[str, Tuple[str, str]] = {}
        builtin_aliases: Dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    matched = _match_forbidden_module(alias.name)
                    if matched:
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden import '{alias.name}' (matches '{matched}')"
                        )
                    local_name = alias.asname or alias.name.split(".", 1)[0]
                    mod_aliases[local_name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                matched = _match_forbidden_module(mod)
                if matched:
                    violations.append(
                        f"{rel_posix}:{node.lineno}: forbidden import from '{mod}' (matches '{matched}')"
                    )
                for alias in node.names:
                    sub_full = f"{mod}.{alias.name}" if mod else alias.name
                    sub_matched = _match_forbidden_module(sub_full)
                    if sub_matched:
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden import '{sub_full}' (matches '{sub_matched}')"
                        )
                    local_name = alias.asname or alias.name
                    if mod in ("os", "shutil"):
                        func_aliases[local_name] = (mod, alias.name)
                    elif mod == "builtins" and alias.name in _FORBIDDEN_BARE_CALLS:
                        builtin_aliases[local_name] = alias.name
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    if func.id in _FORBIDDEN_BARE_CALLS:
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden call '{func.id}()'"
                        )
                    elif func.id in builtin_aliases:
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden call '{builtin_aliases[func.id]}()'"
                        )
                    elif func.id in func_aliases and func_aliases[func.id] in _FORBIDDEN_QUALIFIED_CALLS:
                        mod_n, attr_n = func_aliases[func.id]
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden call '{mod_n}.{attr_n}()'"
                        )
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    resolved_mod = mod_aliases.get(func.value.id, func.value.id)
                    if (resolved_mod, func.attr) in _FORBIDDEN_QUALIFIED_CALLS:
                        violations.append(
                            f"{rel_posix}:{node.lineno}: forbidden call '{resolved_mod}.{func.attr}()'"
                        )
    return violations


def build_scrubbed_env(work_dir: Path) -> Dict[str, str]:
    """Construct a minimal subprocess environment excluding parent secrets and .env vars."""
    env: Dict[str, str] = {}
    for var_name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT"):
        if var_name in os.environ:
            env[var_name] = os.environ[var_name]

    path_entries: List[str] = []
    py_dir = str(Path(sys.executable).resolve().parent)
    path_entries.append(py_dir)
    for candidate in (
        sys.prefix,
        sys.base_prefix,
        str(Path(sys.base_prefix) / "DLLs"),
    ):
        if candidate and candidate not in path_entries:
            path_entries.append(candidate)

    if os.name == "nt":
        sys_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        sys32 = str(Path(sys_root) / "System32")
        for candidate in (sys32, sys_root):
            if candidate not in path_entries:
                path_entries.append(candidate)
    else:
        for candidate in ("/usr/bin", "/bin"):
            if candidate not in path_entries:
                path_entries.append(candidate)

    work_dir_str = str(work_dir)
    env["PATH"] = os.pathsep.join(path_entries)
    env["PYTHONPATH"] = work_dir_str
    env["TEMP"] = work_dir_str
    env["TMP"] = work_dir_str
    env["HOME"] = work_dir_str
    env["USERPROFILE"] = work_dir_str
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


class _BoundedHeadTailCapture:
    """Bounded character buffer retaining head and tail segments without unbounded memory growth."""

    def __init__(self, max_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> None:
        self.max_chars = max(0, int(max_chars))
        self.marker = "\n...[output truncated]...\n"
        if self.max_chars <= len(self.marker) + 16:
            self._head_limit = self.max_chars // 2
            self._tail_limit = self.max_chars - self._head_limit
        else:
            usable = self.max_chars - len(self.marker)
            self._head_limit = usable // 4
            self._tail_limit = usable - self._head_limit
        self._head_buf: List[str] = []
        self._head_len = 0
        self._tail_buf: deque[str] = deque()
        self._tail_len = 0
        self._overflowed = False

    def append(self, text: str) -> None:
        """Append chunk while capping resident memory to ~max_chars."""
        if not text or self.max_chars == 0:
            return
        if not self._overflowed:
            if self._head_len + len(text) <= self.max_chars:
                self._head_buf.append(text)
                self._head_len += len(text)
                return
            combined = "".join(self._head_buf) + text
            self._overflowed = True
            self._head_buf = [combined[: self._head_limit]]
            self._head_len = len(self._head_buf[0])
            remainder = combined[self._head_limit :]
            if remainder:
                self._append_tail(remainder)
            return
        self._append_tail(text)

    def _append_tail(self, text: str) -> None:
        if self._tail_limit <= 0:
            return
        if len(text) >= self._tail_limit:
            self._tail_buf.clear()
            self._tail_buf.append(text[-self._tail_limit :])
            self._tail_len = self._tail_limit
            return
        self._tail_buf.append(text)
        self._tail_len += len(text)
        while self._tail_len > self._tail_limit and self._tail_buf:
            excess = self._tail_len - self._tail_limit
            oldest = self._tail_buf[0]
            if len(oldest) <= excess:
                self._tail_buf.popleft()
                self._tail_len -= len(oldest)
            else:
                self._tail_buf[0] = oldest[excess:]
                self._tail_len -= excess

    def render(self) -> str:
        """Return captured output guaranteed <= max_chars."""
        if not self._overflowed:
            return "".join(self._head_buf)
        tail_str = "".join(self._tail_buf)
        if self.max_chars <= len(self.marker) + 16:
            return ("".join(self._head_buf) + tail_str)[: self.max_chars]
        return "".join(self._head_buf) + self.marker + tail_str


def cap_head_tail_text(text: str, max_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> str:
    """Trim any string to <= max_chars preserving head and tail slices."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    cap = _BoundedHeadTailCapture(max_chars=max_chars)
    cap.append(text)
    return cap.render()


def _read_pipe_chunked(pipe: Any, capture: _BoundedHeadTailCapture) -> None:
    """Drain a subprocess byte stream in 4KB chunks into bounded capture."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while True:
            chunk = pipe.read1(4096) if hasattr(pipe, "read1") else pipe.read(4096)
            if not chunk:
                break
            capture.append(decoder.decode(chunk, final=False))
        tail = decoder.decode(b"", final=True)
        if tail:
            capture.append(tail)
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _create_windows_job_for_process(proc: subprocess.Popen[bytes]) -> Optional[int]:
    """Create a Win32 Job Object and assign proc so all descendants can be terminated atomically."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            return None
        proc_handle = getattr(proc, "_handle", None)
        if proc_handle is None or not kernel32.AssignProcessToJobObject(job_handle, int(proc_handle)):
            kernel32.CloseHandle(job_handle)
            return None
        return int(job_handle)
    except Exception:
        return None


def _close_windows_job(job_handle: Optional[int], terminate: bool = False) -> None:
    """Optionally terminate a Win32 Job Object (killing all descendants) and close its handle."""
    if os.name != "nt" or not job_handle:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        if terminate:
            kernel32.TerminateJobObject(job_handle, 1)
        kernel32.CloseHandle(job_handle)
    except Exception:
        pass


def _kill_process_tree(
    proc: subprocess.Popen[bytes],
    job_handle: Optional[int] = None,
) -> None:
    """Terminate a subprocess and all its descendants across Windows or POSIX."""
    if os.name == "nt":
        if job_handle:
            _close_windows_job(job_handle, terminate=True)
        elif proc.poll() is None:
            sys_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            taskkill_exe = Path(sys_root) / "System32" / "taskkill.exe"
            cmd = [str(taskkill_exe) if taskkill_exe.exists() else "taskkill", "/F", "/T", "/PID", str(proc.pid)]
            try:
                subprocess.run(cmd, capture_output=True, timeout=5, check=False)
            except Exception:
                pass
    else:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:
        pass


def _cleanup_temp_dir(temp_dir: Path) -> None:
    """Remove temporary sandbox directory with short Windows handle-release retries."""
    if not temp_dir.exists():
        return
    last_exc: Optional[Exception] = None
    for attempt in range(5):
        try:
            shutil.rmtree(temp_dir)
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(0.05 * (attempt + 1))
    if temp_dir.exists() and last_exc is not None:
        raise last_exc


def _format_timeout_label(timeout_seconds: float) -> str:
    """Format timeout duration cleanly (e.g. '30s' or '1.5s')."""
    if float(timeout_seconds).is_integer():
        return f"{int(timeout_seconds)}s"
    return f"{timeout_seconds:g}s"


class LocalExecutionSandbox:
    """Hardened local subprocess execution sandbox implementing CodeExecutionSandbox."""

    __test__ = False

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        enable_ast_tripwire: bool = True,
    ) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_chars = int(max_output_chars)
        self.enable_ast_tripwire = enable_ast_tripwire

    def execute_in_sandbox(
        self,
        code_artifacts: Dict[str, CodeArtifact],
        test_artifacts: Optional[Dict[str, TestArtifact]] = None,
        command: Optional[str] = None,
    ) -> SandboxExecutionResult:
        """Validate artifacts, check AST safety, materialize into temp dir, and run pytest."""
        validated_code, validated_tests = prepare_validated_artifacts(
            code_artifacts=code_artifacts,
            test_artifacts=test_artifacts,
        )

        if self.enable_ast_tripwire:
            violations = scan_python_ast_safety([*validated_code, *validated_tests])
            if violations:
                reason = "AST safety tripwire refused execution:\n" + "\n".join(violations)
                capped = cap_head_tail_text(reason, self.max_output_chars)
                return SandboxExecutionResult(
                    passed=False,
                    exit_code=1,
                    stdout="",
                    stderr=capped,
                    output=capped,
                    duration_seconds=0.0,
                    refused=True,
                )

        cmd = self._build_command(validated_tests=validated_tests, explicit_command=command)
        if cmd is None:
            msg = "No Python test files to execute"
            return SandboxExecutionResult(
                passed=False,
                exit_code=5,
                stdout=msg,
                stderr="",
                output=msg,
                duration_seconds=0.0,
            )

        temp_dir = Path(tempfile.mkdtemp(prefix="ai_eng_sandbox_"))
        start = time.monotonic()
        try:
            root_resolved = temp_dir.resolve()
            self._write_validated_files(root_resolved, validated_code)
            self._write_validated_files(root_resolved, validated_tests)

            scrubbed_env = build_scrubbed_env(root_resolved)
            return self._run_subprocess(cmd=cmd, cwd=root_resolved, env=scrubbed_env, start_time=start)
        finally:
            _cleanup_temp_dir(temp_dir)

    def _build_command(
        self,
        validated_tests: List[Tuple[Tuple[str, ...], str, str]],
        explicit_command: Optional[str],
    ) -> Optional[List[str]]:
        if explicit_command:
            parts = shlex.split(explicit_command, posix=(os.name != "nt"))
            cleaned = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"') else p for p in parts]
            if not cleaned:
                raise ValueError("Sandbox command must not be empty")
            head = cleaned[0].lower()
            if head in ("python", "python.exe", "python3"):
                return [sys.executable, *cleaned[1:]]
            if head in ("pytest", "pytest.exe"):
                return [sys.executable, "-m", "pytest", *cleaned[1:]]
            if head == "-m":
                return [sys.executable, *cleaned]
            return cleaned

        base = [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "-rA",
            "--tb=short",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:deepeval",
            "-p",
            "no:langsmith",
        ]
        if validated_tests:
            py_targets = [rel_posix for _, rel_posix, _ in validated_tests if rel_posix.lower().endswith(".py")]
            if not py_targets:
                return None
            return [*base, *py_targets]
        return base

    def _write_validated_files(
        self,
        root_resolved: Path,
        artifacts: List[Tuple[Tuple[str, ...], str, str]],
    ) -> None:
        for parts, rel_posix, content in artifacts:
            curr = root_resolved
            for dir_part in parts[:-1]:
                curr = curr / dir_part
                if curr.is_symlink():
                    raise ValueError(f"Unsafe artifact path (symlink directory): {rel_posix!r}")
                curr.mkdir(exist_ok=True)
            target = root_resolved.joinpath(*parts)
            if target.is_symlink():
                raise ValueError(f"Unsafe artifact path (symlink target): {rel_posix!r}")
            resolved_target = target.resolve()
            if not resolved_target.is_relative_to(root_resolved) or resolved_target == root_resolved:
                raise ValueError(f"Unsafe artifact path resolves outside sandbox root: {rel_posix!r}")
            resolved_target.write_text(content, encoding="utf-8")

    def _run_subprocess(
        self,
        cmd: List[str],
        cwd: Path,
        env: Dict[str, str],
        start_time: float,
    ) -> SandboxExecutionResult:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        start_new_session = os.name != "nt"

        stdout_cap = _BoundedHeadTailCapture(max_chars=self.max_output_chars)
        stderr_cap = _BoundedHeadTailCapture(max_chars=self.max_output_chars)

        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )

        job_handle = _create_windows_job_for_process(proc)

        t_out = threading.Thread(target=_read_pipe_chunked, args=(proc.stdout, stdout_cap), daemon=True)
        t_err = threading.Thread(target=_read_pipe_chunked, args=(proc.stderr, stderr_cap), daemon=True)
        t_out.start()
        t_err.start()

        timed_out = False
        try:
            exit_code = proc.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_tree(proc, job_handle=job_handle)
            job_handle = None
            exit_code = -1
        finally:
            if job_handle is not None:
                _close_windows_job(job_handle, terminate=True)
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)

        duration = time.monotonic() - start_time
        stdout_text = stdout_cap.render()
        stderr_text = stderr_cap.render()
        combined = stdout_text
        if stderr_text:
            combined = f"{stdout_text}\n{stderr_text}" if stdout_text else stderr_text

        if timed_out:
            timeout_label = _format_timeout_label(self.timeout_seconds)
            prefix = f"Execution timed out after {timeout_label}"
            full_msg = f"{prefix}\n{combined}" if combined else prefix
            capped_output = cap_head_tail_text(full_msg, self.max_output_chars)
            return SandboxExecutionResult(
                passed=False,
                exit_code=-1,
                stdout=stdout_text,
                stderr=stderr_text,
                output=capped_output,
                duration_seconds=duration,
                timed_out=True,
            )

        capped_output = cap_head_tail_text(combined, self.max_output_chars)
        return SandboxExecutionResult(
            passed=(exit_code == 0),
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            output=capped_output,
            duration_seconds=duration,
        )


