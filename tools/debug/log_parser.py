"""
tools/debug/log_parser.py

Error log parser protocol, structured report dataclass, and FakeLogParser test double.

Source of truth:
  - design.md Section 1.7 (DebugAgent Tools Required: Error log parser)
  - design.md Section 4.7 (DebugAgent Tools table)
  - design.md Section 6.1 (tools/debug/ owned by Debug team)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union


@dataclass
class ParsedErrorLog:
    """Structured report returned from error log parsing."""

    __test__ = False
    failing_files: List[str] = field(default_factory=list)
    exception_type: str = ""
    error_message: str = ""
    stack_frames: List[str] = field(default_factory=list)
    raw_truncated: str = ""


class ErrorLogParser(Protocol):
    """Protocol for parsing raw error messages and tracebacks into structured forms."""

    def parse_error_log(
        self,
        message: str,
        traceback: Optional[str] = None,
    ) -> ParsedErrorLog:
        """Parse error message and traceback into a structured ParsedErrorLog."""
        ...


class FakeLogParser:
    """Default rule-based or scripted ErrorLogParser implementation."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[ParsedErrorLog, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results: List[Union[ParsedErrorLog, Dict[str, Any], Exception]] = (
            list(results) if results is not None else []
        )
        self.calls: List[Dict[str, Any]] = []

    def set_results(
        self, results: List[Union[ParsedErrorLog, Dict[str, Any], Exception]]
    ) -> None:
        """Configure or append scripted log parse results."""
        self.results = list(results)

    def parse_error_log(
        self,
        message: str,
        traceback: Optional[str] = None,
    ) -> ParsedErrorLog:
        """Record invocation and return next scripted or parsed result."""
        self.calls.append({"message": message, "traceback": traceback})
        if self.results:
            next_res = self.results.pop(0)
            if isinstance(next_res, Exception):
                raise next_res
            if isinstance(next_res, dict):
                return ParsedErrorLog(**next_res)
            return next_res

        # Rule-based fallback parser
        failing_files: List[str] = []
        msg = message or ""
        tb = traceback or ""

        # Extract file paths from patterns like File "path", line X or path::test
        for m in re.finditer(r'File ["\']([^"\']+)["\']', tb + "\n" + msg):
            path = m.group(1).replace("\\", "/")
            if path not in failing_files:
                failing_files.append(path)
        for m in re.finditer(r'([a-zA-Z0-9_\-\./]+\.py)', msg):
            path = m.group(1).replace("\\", "/")
            if path not in failing_files:
                failing_files.append(path)

        exc_type = "Error"
        if ":" in msg:
            exc_type = msg.split(":", 1)[0].strip()

        return ParsedErrorLog(
            failing_files=failing_files,
            exception_type=exc_type,
            error_message=msg,
            stack_frames=[tb] if tb else [],
            raw_truncated=(msg + "\n" + tb)[:2000],
        )

