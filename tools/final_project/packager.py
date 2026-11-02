"""
tools/final_project/packager.py

Project packaging tool protocol, report dataclass, path-safety validator,
and FakeProjectPackager test double.

Source of truth:
  - design.md Section 1.11 (FinalProjectAgent Contract)
  - design.md Section 4.11 (FinalProjectAgent Tools table: File path enumerator & packaging)
  - design.md Section 6.1 (Folder ownership)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union


def validate_safe_path(path: str) -> None:
    """Validate that a path is safe and does not attempt traversal or absolute escapes.

    Rejects:
      - Absolute paths (POSIX or Windows, e.g. /etc/passwd, C:\\secret, \\\\network)
      - Paths containing '..' directory traversal segments
      - Empty or whitespace-only paths

    Raises:
        ValueError: If path is unsafe or invalid.
    """
    if not path or not isinstance(path, str) or not path.strip():
        raise ValueError("Invalid path: path must be a non-empty string")

    clean_path = path.strip()

    # Reject leading slashes (POSIX or UNC)
    if clean_path.startswith("/") or clean_path.startswith("\\"):
        raise ValueError(f"Insecure path detected (absolute path not allowed): {path!r}")

    # Reject Windows drive letters (e.g. C:)
    if len(clean_path) > 1 and clean_path[1] == ":":
        raise ValueError(f"Insecure path detected (drive letter not allowed): {path!r}")

    p = Path(clean_path)
    if p.is_absolute():
        raise ValueError(f"Insecure path detected (absolute path not allowed): {path!r}")

    # Reject '..' traversal segments
    if ".." in p.parts:
        raise ValueError(f"Insecure path detected ('..' traversal segment not allowed): {path!r}")


@dataclass
class PackageReport:
    """Structured report returned from packaging project deliverables."""

    __test__ = False
    success: bool = True
    total_files_packaged: int = 0
    package_path: str = ""
    errors: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class ProjectPackager(Protocol):
    """Protocol for packaging and bundling project deliverables."""

    def package_project(
        self,
        project_name: str,
        artifacts: Dict[str, Any],
        output_dir: str = "dist",
    ) -> PackageReport:
        """Package project artifacts into distribution bundle after validating path safety."""
        ...


class FakeProjectPackager:
    """Test double implementing ProjectPackager with path-safety enforcement."""

    __test__ = False

    def __init__(
        self,
        results: Optional[List[Union[PackageReport, Dict[str, Any], Exception]]] = None,
    ) -> None:
        self.results = list(results) if results is not None else []
        self.calls: List[Dict[str, Any]] = []

    def package_project(
        self,
        project_name: str,
        artifacts: Dict[str, Any],
        output_dir: str = "dist",
    ) -> PackageReport:
        """Validate path safety across all artifact keys and return scripted report."""
        # Validate output_dir path safety
        validate_safe_path(output_dir)

        # Validate every artifact relative file path
        for path_key in artifacts.keys():
            validate_safe_path(path_key)

        self.calls.append(
            {
                "project_name": project_name,
                "artifacts": dict(artifacts),
                "output_dir": output_dir,
            }
        )

        if self.results:
            next_res = self.results.pop(0)
            if isinstance(next_res, Exception):
                raise next_res
            if isinstance(next_res, dict):
                return PackageReport(**next_res)
            return next_res

        return PackageReport(
            success=True,
            total_files_packaged=len(artifacts),
            package_path=f"{output_dir}/{project_name}.zip",
        )


__all__ = [
    "PackageReport",
    "ProjectPackager",
    "FakeProjectPackager",
    "validate_safe_path",
]
