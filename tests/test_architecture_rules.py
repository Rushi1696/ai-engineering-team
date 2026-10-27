"""
tests/test_architecture_rules.py

Architecture boundary tests enforcing design.md Section 6 rules:
1. Rule 3: No agent imports from workflows/
2. Concrete agents must not import other concrete agents (agents.base_agent is allowed).
3. All local status string constants defined in agents are members of
   state.validators.WORKFLOW_STATUS_VALUES.
"""

import ast
from pathlib import Path

from state.validators import WORKFLOW_STATUS_VALUES


AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def _get_agent_python_files() -> list[Path]:
    """Return all non-init Python files in agents/."""
    return sorted(
        [
            p
            for p in AGENTS_DIR.glob("*.py")
            if p.is_file() and p.name != "__init__.py"
        ]
    )


def test_agents_do_not_import_workflows():
    """Rule 3: Agents must never import from workflows/."""
    agent_files = _get_agent_python_files()
    assert len(agent_files) > 0, "No agent files found in agents/"

    violations = []
    for file_path in agent_files:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "workflows" or alias.name.startswith("workflows."):
                        violations.append(
                            f"{file_path.name}:{node.lineno} imports '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "workflows" or mod.startswith("workflows."):
                    violations.append(
                        f"{file_path.name}:{node.lineno} imports from '{mod}'"
                    )

    assert not violations, (
        f"Found forbidden imports from workflows/ in agents/:\n"
        + "\n".join(violations)
    )


def test_agents_do_not_import_other_concrete_agents():
    """Concrete agents must not import other concrete agents (only agents.base_agent is allowed)."""
    agent_files = _get_agent_python_files()
    assert len(agent_files) > 0

    violations = []
    for file_path in agent_files:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("agents.") and alias.name != "agents.base_agent":
                        violations.append(
                            f"{file_path.name}:{node.lineno} imports '{alias.name}'"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "agents":
                    # Check imported names, e.g. from agents import product_manager_agent
                    for alias in node.names:
                        if alias.name != "base_agent":
                            violations.append(
                                f"{file_path.name}:{node.lineno} imports '{alias.name}' from 'agents'"
                            )
                elif mod.startswith("agents.") and mod != "agents.base_agent":
                    violations.append(
                        f"{file_path.name}:{node.lineno} imports from concrete agent module '{mod}'"
                    )

    assert not violations, (
        f"Found forbidden inter-agent imports in agents/:\n"
        + "\n".join(violations)
    )


def test_agent_status_constants_are_valid_workflow_statuses():
    """All local STATUS_* constants in agents must be members of WORKFLOW_STATUS_VALUES."""
    import importlib
    import sys

    # Ensure repository root is on sys.path
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    agent_files = _get_agent_python_files()
    checked_constants = {}

    for file_path in agent_files:
        module_name = f"agents.{file_path.stem}"
        module = importlib.import_module(module_name)
        for attr_name in dir(module):
            if attr_name.startswith("STATUS_"):
                val = getattr(module, attr_name)
                assert isinstance(val, str), f"{module_name}.{attr_name} must be a str"
                assert val in WORKFLOW_STATUS_VALUES, (
                    f"{module_name}.{attr_name} = {val!r} is not a valid workflow status in "
                    f"state.validators.WORKFLOW_STATUS_VALUES"
                )
                checked_constants[f"{module_name}.{attr_name}"] = val

    # Verify that the expected status constants were actually found and checked
    assert "agents.product_manager_agent.STATUS_REQUIREMENTS_COMPLETE" in checked_constants
    assert "agents.architect_agent.STATUS_ARCHITECTURE_COMPLETE" in checked_constants
    assert "agents.task_planner_agent.STATUS_PLANNING_COMPLETE" in checked_constants
    assert "agents.backend_agent.STATUS_BACKEND_COMPLETE" in checked_constants
    assert "agents.frontend_agent.STATUS_FRONTEND_COMPLETE" in checked_constants
