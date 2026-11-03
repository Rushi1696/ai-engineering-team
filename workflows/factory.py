"""
workflows/factory.py

Composition root factory creating all 11 concrete pipeline agents
configured from configs/agents.yaml with injected dependencies.

Source of truth:
  - design.md Section 5 (LLM Strategy per agent)
  - design.md Section 6.1 (Folder ownership & composition root)
  - workflow-design-revised.md Section 3.7.4 & Section 3.8.2
  - workflows/constants.py (AGENT_NODE_ORDER)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union
import yaml

from agents.architect_agent import ArchitectAgent
from agents.backend_agent import BackendAgent
from agents.base_agent import BaseAgent, DEFAULT_MAX_RETRIES
from agents.debug_agent import DebugAgent
from agents.devops_agent import DevOpsAgent
from agents.documentation_agent import DocumentationAgent
from agents.final_project_agent import FinalProjectAgent
from agents.frontend_agent import FrontendAgent
from agents.product_manager_agent import ProductManagerAgent
from agents.qa_agent import QAAgent
from agents.reviewer_agent import ReviewerAgent
from agents.task_planner_agent import TaskPlannerAgent
from tools.debug.log_parser import ErrorLogParser, RegexLogParser
from tools.debug.sandbox import CodeExecutionSandbox, FakeExecutionSandbox
from tools.devops.yaml_validator import ConfigValidator, FakeConfigValidator
from tools.documentation.markdown_linter import FakeMarkdownLinter, MarkdownLinter
from tools.final_project.packager import FakeProjectPackager, ProjectPackager
from tools.qa.test_runner import FakeTestRunner, TestRunner
from tools.reviewer.coverage_reader import CoverageReportReader, FakeCoverageReader
from tools.reviewer.static_analyzer import FakeStaticAnalyzer, StaticAnalysisTool
from tools.shared.llm_client import FakeLLMClient, LLMClient
from workflows.constants import AGENT_NODE_ORDER


def load_agent_configs(config_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Load agent configurations from YAML file.

    Args:
        config_path: Optional explicit path to agents.yaml. If omitted, defaults
            to configs/agents.yaml located at the repository root.

    Returns:
        Dictionary loaded from the YAML configuration file.

    Raises:
        FileNotFoundError: If the configuration file cannot be found.
    """
    if config_path is None:
        target_path = Path(__file__).resolve().parent.parent / "configs" / "agents.yaml"
    else:
        target_path = Path(config_path)

    if not target_path.exists():
        raise FileNotFoundError(f"Agent configuration file not found at: {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return data


def create_agents(
    config_path: Optional[Union[str, Path]] = None,
    llm_client: Optional[LLMClient] = None,
    tools: Optional[Dict[str, Any]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
) -> Dict[str, BaseAgent]:
    """Instantiate and configure all 11 concrete agents in exact AGENT_NODE_ORDER.

    Reads model names, max_retries, and timeouts from configs/agents.yaml, injecting
    dependencies (LLM client, tools, sleep function).

    Args:
        config_path: Optional custom path to agents.yaml. Defaults to configs/agents.yaml.
        llm_client: Injected LLM client (defaults to FakeLLMClient).
        tools: Injected dictionary of tools (defaults to fake test doubles for each agent).
        sleep_fn: Injected sleep function for backoff (defaults to a fast no-op lambda).

    Returns:
        Dictionary mapping each node name in AGENT_NODE_ORDER to its concrete agent instance.

    Raises:
        KeyError: If configuration is missing for any node in AGENT_NODE_ORDER.
        RuntimeError: If resulting agent keys do not match AGENT_NODE_ORDER exactly.
    """
    configs = load_agent_configs(config_path)
    agent_configs: Dict[str, Any] = configs.get("agents", {})

    effective_llm_client = llm_client if llm_client is not None else FakeLLMClient()
    effective_sleep_fn = sleep_fn if sleep_fn is not None else (lambda _: None)
    tool_map = tools or {}

    def _get_cfg(node: str) -> Dict[str, Any]:
        if node not in agent_configs:
            raise KeyError(
                f"Missing agent configuration for '{node}' in {config_path or 'configs/agents.yaml'}"
            )
        return agent_configs[node]

    # Resolve required tools, defaulting to fake doubles
    test_runner: TestRunner = tool_map.get("test_runner") or FakeTestRunner()
    sandbox: CodeExecutionSandbox = tool_map.get("sandbox") or FakeExecutionSandbox()
    log_parser: ErrorLogParser = tool_map.get("log_parser") or RegexLogParser()
    static_analyzer: StaticAnalysisTool = tool_map.get("static_analyzer") or FakeStaticAnalyzer()
    coverage_reader: CoverageReportReader = tool_map.get("coverage_reader") or FakeCoverageReader()
    validator: ConfigValidator = tool_map.get("validator") or FakeConfigValidator()
    linter: MarkdownLinter = tool_map.get("linter") or FakeMarkdownLinter()
    packager: ProjectPackager = tool_map.get("packager") or FakeProjectPackager()

    pm_cfg = _get_cfg("product_manager")
    arch_cfg = _get_cfg("architect")
    tp_cfg = _get_cfg("task_planner")
    be_cfg = _get_cfg("backend")
    fe_cfg = _get_cfg("frontend")
    qa_cfg = _get_cfg("qa")
    dbg_cfg = _get_cfg("debug")
    rev_cfg = _get_cfg("reviewer")
    devops_cfg = _get_cfg("devops")
    doc_cfg = _get_cfg("documentation")
    final_cfg = _get_cfg("final_project")

    pm_agent = ProductManagerAgent(
        model_name=pm_cfg["model"],
        llm_client=effective_llm_client,
        max_retries=pm_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    arch_agent = ArchitectAgent(
        model_name=arch_cfg["model"],
        llm_client=effective_llm_client,
        max_retries=arch_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    tp_agent = TaskPlannerAgent(
        model_name=tp_cfg["model"],
        llm_client=effective_llm_client,
        max_retries=tp_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    be_agent = BackendAgent(
        model_name=be_cfg["model"],
        llm_client=effective_llm_client,
        max_retries=be_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    fe_agent = FrontendAgent(
        model_name=fe_cfg["model"],
        llm_client=effective_llm_client,
        max_retries=fe_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    qa_agent = QAAgent(
        model_name=qa_cfg["model"],
        llm_client=effective_llm_client,
        test_runner=test_runner,
        max_retries=qa_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    dbg_agent = DebugAgent(
        model_name=dbg_cfg["model"],
        llm_client=effective_llm_client,
        sandbox=sandbox,
        log_parser=log_parser,
        max_retries=dbg_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    rev_agent = ReviewerAgent(
        model_name=rev_cfg["model"],
        llm_client=effective_llm_client,
        static_analyzer=static_analyzer,
        coverage_reader=coverage_reader,
        max_retries=rev_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    devops_agent = DevOpsAgent(
        model_name=devops_cfg["model"],
        llm_client=effective_llm_client,
        validator=validator,
        max_retries=devops_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    doc_agent = DocumentationAgent(
        model_name=doc_cfg["model"],
        llm_client=effective_llm_client,
        linter=linter,
        max_retries=doc_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )
    final_agent = FinalProjectAgent(
        model_name=final_cfg["model"],
        llm_client=effective_llm_client,
        packager=packager,
        max_retries=final_cfg.get("max_retries", DEFAULT_MAX_RETRIES),
        sleep_fn=effective_sleep_fn,
    )

    agents_map: Dict[str, BaseAgent] = {
        "product_manager": pm_agent,
        "architect": arch_agent,
        "task_planner": tp_agent,
        "backend": be_agent,
        "frontend": fe_agent,
        "qa": qa_agent,
        "debug": dbg_agent,
        "reviewer": rev_agent,
        "devops": devops_agent,
        "documentation": doc_agent,
        "final_project": final_agent,
    }

    if tuple(agents_map.keys()) != AGENT_NODE_ORDER:
        raise RuntimeError(
            f"Factory agents keys {tuple(agents_map.keys())} do not match AGENT_NODE_ORDER {AGENT_NODE_ORDER}"
        )

    return agents_map


__all__ = [
    "load_agent_configs",
    "create_agents",
]
