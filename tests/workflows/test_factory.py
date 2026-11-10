"""
tests/workflows/test_factory.py

Unit and integration tests for the composition root factory (workflows/factory.py)
and CLI entrypoint (app.py).

Verifies:
  1. Factory output matches AGENT_NODE_ORDER exactly.
  2. Every agent's model_name and max_retries are read from configs/agents.yaml.
  3. Injected tools and sleep_fn are correctly passed to agents.
  4. app.py runs end-to-end with scripted FakeLLMClient.
"""

from pathlib import Path
from unittest.mock import patch
import pytest

from agents.base_agent import BaseAgent
from tools.debug.sandbox import FakeExecutionSandbox, LocalExecutionSandbox
from tools.final_project.packager import FakeProjectPackager
from tools.qa.test_runner import FakeTestRunner, PytestTestRunner
from tools.shared.llm_client import FakeLLMClient
from workflows.constants import AGENT_NODE_ORDER
from workflows.factory import create_agents, load_agent_configs
import app


def test_factory_output_matches_agent_node_order_and_models_from_config():
    """Verify factory output dictionary keys match AGENT_NODE_ORDER exactly and models are read from config."""
    config_data = load_agent_configs()
    agent_configs = config_data.get("agents", {})

    agents = create_agents()

    # 1. Output keys and order match AGENT_NODE_ORDER exactly
    assert tuple(agents.keys()) == AGENT_NODE_ORDER
    assert len(agents) == 11

    # 2. Every node maps to a concrete BaseAgent instance
    for node_name in AGENT_NODE_ORDER:
        agent = agents[node_name]
        assert isinstance(agent, BaseAgent)
        assert callable(agent)  # agent satisfies Callable[[EngineeringState], Dict[str, Any]]

        # 3. Model name is read from configs/agents.yaml
        expected_model = agent_configs[node_name]["model"]
        assert agent.model_name == expected_model, (
            f"Node '{node_name}' model '{agent.model_name}' != expected '{expected_model}'"
        )

        # 4. Max retries is read from config
        expected_retries = agent_configs[node_name].get("max_retries", 3)
        assert agent.max_retries == expected_retries


def test_factory_injects_custom_tools_and_dependencies():
    """Verify that injected LLMClient, custom tools, and sleep_fn are passed to the agents."""
    custom_client = FakeLLMClient(responses=[])
    custom_packager = FakeProjectPackager()
    sleep_calls = []

    def custom_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    agents = create_agents(
        llm_client=custom_client,
        tools={"packager": custom_packager},
        sleep_fn=custom_sleep,
    )

    # Check shared LLMClient
    for agent in agents.values():
        assert agent.llm_client is custom_client
        assert agent.sleep_fn is custom_sleep

    # Check custom tool injected into FinalProjectAgent
    assert agents["final_project"].packager is custom_packager


def test_factory_rejects_missing_config_file():
    """load_agent_configs raises FileNotFoundError when given non-existent file path."""
    with pytest.raises(FileNotFoundError, match="Agent configuration file not found"):
        load_agent_configs("non_existent_config_file.yaml")


def test_factory_rejects_incomplete_config(tmp_path: Path):
    """create_agents raises KeyError if an agent from AGENT_NODE_ORDER is missing in config."""
    incomplete_yaml = tmp_path / "incomplete.yaml"
    incomplete_yaml.write_text("agents:\n  product_manager:\n    model: test-model\n", encoding="utf-8")

    with pytest.raises(KeyError, match="Missing agent configuration for 'architect'"):
        create_agents(config_path=incomplete_yaml)


def test_app_cli_end_to_end_scripted_run():
    """Verify app.py CLI main executes pipeline end-to-end and returns exit code 0."""
    with patch("sys.argv", ["app.py", "--project", "CliTestApp"]):
        exit_code = app.main()
        assert exit_code == 0


def test_factory_defaults_to_fake_tools_even_for_live_providers(monkeypatch):
    """FakeTestRunner and FakeExecutionSandbox remain default even for live LLM providers."""
    monkeypatch.setenv("GEMINI_API_KEY", "mock-key")
    with patch("google.genai.Client"):
        agents = create_agents(provider="gemini")
        assert isinstance(agents["qa"].test_runner, FakeTestRunner)
        assert isinstance(agents["debug"].sandbox, FakeExecutionSandbox)


def test_factory_use_real_tools_injects_separate_qa_and_debug_instances():
    """use_real_tools=True injects PytestTestRunner for QA and separate LocalExecutionSandbox for Debug."""
    agents = create_agents(use_real_tools=True)
    qa_runner = agents["qa"].test_runner
    dbg_sandbox = agents["debug"].sandbox

    assert isinstance(qa_runner, PytestTestRunner)
    assert isinstance(qa_runner.sandbox, LocalExecutionSandbox)
    assert isinstance(dbg_sandbox, LocalExecutionSandbox)
    assert qa_runner.sandbox is not dbg_sandbox


def test_app_cli_tools_flag_default_fake_and_real_prints_warning(capsys, monkeypatch):
    """app.py defaults to --tools fake for all providers and prints warning only on --tools real."""
    monkeypatch.setenv("GROQ_API_KEY", "mock-groq-key")
    captured_calls = []

    def _spy_create_agents(*args, **kwargs):
        captured_calls.append(kwargs.get("use_real_tools"))
        return create_agents(llm_client=FakeLLMClient(responses=app.build_scripted_llm_responses()))

    with patch("app.create_agents", side_effect=_spy_create_agents), patch("groq.Groq"):
        with patch("sys.argv", ["app.py", "--provider", "groq", "--stop-after", "ProductManagerAgent"]):
            rc = app.main()
            assert rc == 0
    out_fake = capsys.readouterr().out
    assert captured_calls[-1] is False
    assert "Executing generated code on this machine" not in out_fake

    # Explicit --tools real passes use_real_tools=True and executes real scripted test cleanly
    with patch("sys.argv", ["app.py", "--provider", "fake", "--tools", "real", "--stop-after", "QAAgent"]):
        rc_real = app.main()
        assert rc_real == 0
    out_real = capsys.readouterr().out
    assert "Executing generated code on this machine" in out_real


