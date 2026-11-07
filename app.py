"""
app.py

Main entrypoint and CLI runner for the AI Engineering Team multi-agent system.
Executes the full 11-agent pipeline end-to-end using injected dependencies and
a scripted FakeLLMClient.

Source of truth:
  - design.md Section 6.1 (workflows/ runner and composition root)
  - workflows/runner.py (run_pipeline)
  - workflows/factory.py (create_agents)
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Dict, List, Optional

from tools.shared.budget import BudgetExceededError, BudgetedLLMClient, RunBudget
from tools.shared.llm_client import FakeLLMClient
from workflows.factory import create_agents, load_agent_configs
from workflows.runner import run_pipeline


def build_scripted_llm_responses() -> List[Dict[str, Any]]:
    """Construct scripted structured LLM responses for all 10 forward-stage agents."""
    return [
        # 1. ProductManagerAgent
        {
            "functional_requirements": [
                "User authentication and authorization API",
                "Project task management endpoints",
                "Responsive dashboard frontend interface",
            ],
            "non_functional_requirements": [
                "Sub-second endpoint latency",
                "99.9% application uptime",
                "Zero path traversal vulnerabilities",
            ],
            "user_stories": [
                "As an engineer I want an automated pipeline that delivers production code",
            ],
            "acceptance_criteria": [
                "All automated unit and integration tests pass",
                "Code review passes with zero critical findings",
            ],
            "constraints": ["Python 3.11+", "TypeScript 5.0+"],
            "business_goals": [
                "Enable autonomous multi-agent end-to-end software development",
            ],
            "produced_by": "ProductManagerAgent",
            "produced_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
        # 2. ArchitectAgent
        {
            "overview": "Microservice container architecture with FastAPI and React",
            "technology_stack": {"backend": "FastAPI", "frontend": "React", "db": "PostgreSQL"},
            "api_specifications": [{"endpoint": "/health", "method": "GET", "description": "Health check"}],
            "database_design": {"users": {"id": "UUID PK"}},
            "security_architecture": {"auth": "JWT Bearer"},
            "scalability_strategy": "Horizontal scaling with stateless containers",
            "deployment_model": "Docker containerized",
            "produced_by": "ArchitectAgent",
            "produced_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
        # 3. TaskPlannerAgent
        {
            "tasks": [
                {
                    "task_id": "TASK-001",
                    "title": "Implement core API",
                    "description": "FastAPI health and core endpoints",
                    "assigned_agent": "BackendAgent",
                    "priority": 10,
                    "dependencies": [],
                    "acceptance_criteria": ["Health check returns ok"],
                    "status": "pending",
                },
                {
                    "task_id": "TASK-002",
                    "title": "Implement Web Dashboard",
                    "description": "React UI views",
                    "assigned_agent": "FrontendAgent",
                    "priority": 20,
                    "dependencies": ["TASK-001"],
                    "acceptance_criteria": ["Dashboard renders"],
                    "status": "pending",
                },
                {
                    "task_id": "TASK-003",
                    "title": "Implement Test Suite",
                    "description": "Comprehensive tests for backend and frontend",
                    "assigned_agent": "QAAgent",
                    "priority": 30,
                    "dependencies": ["TASK-001", "TASK-002"],
                    "acceptance_criteria": ["All tests pass"],
                    "status": "pending",
                },
            ],
            "execution_order": ["TASK-001", "TASK-002", "TASK-003"],
            "produced_by": "TaskPlannerAgent",
            "produced_at": "2026-01-01T00:00:00Z",
            "version": 1,
        },
        # 4. BackendAgent
        {
            "artifacts": [
                {
                    "file_path": "src/app.py",
                    "language": "python",
                    "content": (
                        "from fastapi import FastAPI\n\n"
                        "app = FastAPI(title='Autonomous Engineering API')\n\n"
                        "@app.get('/health')\n"
                        "def health():\n"
                        "    return {'status': 'ok'}\n"
                    ),
                    "artifact_type": "backend",
                    "produced_by": "BackendAgent",
                    "produced_at": "2026-01-01T00:00:00Z",
                    "version": 1,
                }
            ],
            "task_statuses": {"TASK-001": "completed"},
            "summary": "Implemented FastAPI backend health check",
        },
        # 5. FrontendAgent
        {
            "artifacts": [
                {
                    "file_path": "frontend/App.tsx",
                    "language": "typescript",
                    "content": (
                        "import React from 'react';\n\n"
                        "export const App: React.FC = () => (\n"
                        "  <div><h1>AI Engineering System</h1></div>\n"
                        ");\n"
                    ),
                    "artifact_type": "frontend",
                    "produced_by": "FrontendAgent",
                    "produced_at": "2026-01-01T00:00:00Z",
                    "version": 1,
                }
            ],
            "task_statuses": {"TASK-002": "completed"},
            "summary": "Implemented React frontend",
        },
        # 6. QAAgent
        {
            "test_plan": {
                "strategy": "Unit and integration tests for backend and frontend",
                "coverage_goals": {"src/app.py": "90%"},
                "test_suites": ["unit"],
                "quality_metrics": {"target_pass_rate": 1.0},
                "produced_by": "QAAgent",
                "produced_at": "2026-01-01T00:00:00Z",
                "version": 1,
            },
            "test_artifacts": [
                {
                    "file_path": "tests/test_app.py",
                    "content": "def test_health(): assert True\n",
                    "test_type": "unit",
                    "coverage_target": "src/app.py",
                    "produced_by": "QAAgent",
                    "produced_at": "2026-01-01T00:00:00Z",
                    "version": 1,
                }
            ],
            "ai_findings": ["All core endpoints covered by unit tests"],
        },
        # 7. ReviewerAgent
        {
            "overall_status": "approved",
            "rejection_scope": None,
            "code_quality_score": 9.5,
            "architecture_compliance": True,
            "engineering_standards_compliance": True,
            "security_findings": [],
            "performance_findings": [],
            "maintainability_findings": [],
            "recommendations": ["Ready for production deployment"],
            "testing_coverage_assessment": "Comprehensive test plan and 100% passing tests",
        },
        # 8. DevOpsAgent
        {
            "artifacts": [
                {
                    "file_path": "Dockerfile",
                    "content": "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"src/app.py\"]\n",
                    "artifact_type": "dockerfile",
                },
                {
                    "file_path": ".github/workflows/ci.yml",
                    "content": "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v3\n",
                    "artifact_type": "ci_cd_pipeline",
                },
            ],
            "summary": "Generated Dockerfile and GitHub Actions workflow",
        },
        # 9. DocumentationAgent
        {
            "artifacts": [
                {"file_path": "README.md", "doc_type": "readme", "content": "# Autonomous App\nOverview"},
                {"file_path": "docs/architecture.md", "doc_type": "architecture", "content": "# Architecture\nDesign"},
                {"file_path": "docs/api_reference.md", "doc_type": "api_reference", "content": "# API\nEndpoints"},
                {"file_path": "docs/deployment.md", "doc_type": "deployment_guide", "content": "# Deploy\nSteps"},
            ],
            "summary": "Generated full project documentation suite",
        },
        # 10. FinalProjectAgent
        {
            "summary": (
                "Complete delivery of autonomous engineering project. Features a high-performance "
                "FastAPI backend, React frontend, 100% verified test suite, Dockerized infrastructure, "
                "and exhaustive documentation."
            )
        },
    ]


def run_cli(
    project_name: str = "Autonomous E2E Engineering System",
    prompt: str = (
        "Build a production-ready web application with FastAPI backend, "
        "React frontend, unit test suite, Docker deployment, and comprehensive documentation."
    ),
    config_path: Optional[str] = None,
    max_debug_cycles: int = 10,
    deadline: Optional[str] = None,
    provider: str = "fake",
    max_total_calls: Optional[int] = None,
    max_prompt_chars_per_call: Optional[int] = None,
    max_total_prompt_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute the end-to-end multi-agent pipeline via the composition root factory."""
    print("=" * 70)
    print(" AI ENGINEERING TEAM - AUTONOMOUS MULTI-AGENT PIPELINE")
    print("=" * 70)
    print(f" Project Name:        {project_name}")
    print(f" Provider:            {provider}")
    print(f" Max Debug Cycles:    {max_debug_cycles}")
    if deadline:
        print(f" Pipeline Deadline:   {deadline}")
    print(f" User Requirements:   {prompt}")
    print("-" * 70)

    # Establish RunBudget if limits are specified or running a live provider
    budget: Optional[RunBudget] = None
    if provider in ("gemini", "groq") or any(
        lim is not None for lim in (max_total_calls, max_prompt_chars_per_call, max_total_prompt_chars)
    ):
        b_cfg = (load_agent_configs(config_path).get("workflow") or {}).get("budget") or {}
        budget = RunBudget(
            max_total_calls=max_total_calls if max_total_calls is not None else b_cfg.get("max_total_calls", 30),
            max_prompt_chars_per_call=max_prompt_chars_per_call
            if max_prompt_chars_per_call is not None
            else b_cfg.get("max_prompt_chars_per_call", 40000),
            max_total_prompt_chars=max_total_prompt_chars
            if max_total_prompt_chars is not None
            else b_cfg.get("max_total_prompt_chars", 150000),
        )

    # 1. Initialize agents based on selected provider mode
    if provider == "fake":
        # Scripted fake LLM client covering all forward stages
        scripted_client = FakeLLMClient(responses=build_scripted_llm_responses())
        if budget is not None:
            scripted_client = BudgetedLLMClient(inner_client=scripted_client, budget=budget, provider_name="fake")
        agents = create_agents(
            config_path=config_path,
            llm_client=scripted_client,
            sleep_fn=lambda _: None,
            budget=budget,
        )
    else:
        # Real provider mode: credentials read from environment / .env
        agents = create_agents(
            config_path=config_path,
            provider=provider,
            sleep_fn=time.sleep,
            budget=budget,
        )

    print(f" [Factory] Initialized {len(agents)} pipeline agents from configuration:")
    for node_name, agent in agents.items():
        print(f"   - {node_name:<18} -> {agent.agent_name:<22} (model: {agent.model_name})")
    print("-" * 70)
    print(" [Execution] Streaming pipeline execution through LangGraph...")

    # 2. Run pipeline to completion
    try:
        final_state = run_pipeline(
            project_name=project_name,
            user_input=prompt,
            agents=agents,
            pipeline_deadline_at=deadline,
            max_debug_cycles=max_debug_cycles,
        )
    finally:
        if budget is not None:
            print(budget.generate_report())

    print(" [Execution] Pipeline execution completed!")
    print("=" * 70)
    print(f" Final Workflow Status: {final_state.get('workflow_status')}")
    print(f" Total Invocations:     {final_state.get('total_invocations')}")
    print(f" Forward Stage:         {final_state.get('forward_stage')}")
    print(f" Total Debug Cycles:    {final_state.get('total_debug_cycles')}")

    final_output = final_state.get("final_output")
    if final_output:
        print("-" * 70)
        print(" DELIVERABLES SUMMARY:")
        print(f" Total Files Generated: {final_output.total_files_generated}")
        print(f" Overall Review Status: {final_output.overall_review_status}")
        print(f" Pipeline Duration:     {final_output.pipeline_duration_seconds:.2f}s")
        print(f" Executive Summary:\n   {final_output.summary}")
        print(f" Code Paths ({len(final_output.code_paths)}):          {final_output.code_paths}")
        print(f" Test Paths ({len(final_output.test_paths)}):          {final_output.test_paths}")
        print(f" Deployment Paths ({len(final_output.deployment_paths)}):    {final_output.deployment_paths}")
        print(f" Documentation Paths ({len(final_output.documentation_paths)}): {final_output.documentation_paths}")
    print("=" * 70)

    return final_state


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Autonomous Multi-Agent AI Engineering Team Runner"
    )
    parser.add_argument(
        "--project",
        default="Autonomous E2E Engineering System",
        help="Project name",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "Build a production-ready web application with FastAPI backend, "
            "React frontend, unit test suite, Docker deployment, and comprehensive documentation."
        ),
        help="Natural language requirements prompt",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to agents.yaml configuration file",
    )
    parser.add_argument(
        "--provider",
        choices=["fake", "gemini", "groq"],
        default="fake",
        help="LLM inference provider: 'fake' (default scripted demo), or 'gemini'/'groq' for live API execution",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run a quick single-call smoke test against the selected provider and exit",
    )
    parser.add_argument(
        "--max-debug-cycles",
        type=int,
        default=10,
        help="Maximum global debug cycles (default: 10)",
    )
    parser.add_argument(
        "--deadline",
        default=None,
        help="Optional ISO 8601 pipeline deadline timestamp",
    )
    parser.add_argument(
        "--max-total-calls",
        type=int,
        default=None,
        help="Maximum total LLM calls across all providers for this run",
    )
    parser.add_argument(
        "--max-prompt-chars-per-call",
        type=int,
        default=None,
        help="Maximum prompt characters allowed in a single LLM call",
    )
    parser.add_argument(
        "--max-total-prompt-chars",
        type=int,
        default=None,
        help="Maximum cumulative prompt characters across all LLM calls",
    )

    args = parser.parse_args()

    if args.smoke_test:
        from tools.shared.smoke_test import run_smoke_test

        return run_smoke_test(provider=args.provider)

    try:
        final_state = run_cli(
            project_name=args.project,
            prompt=args.prompt,
            config_path=args.config,
            max_debug_cycles=args.max_debug_cycles,
            deadline=args.deadline,
            provider=args.provider,
            max_total_calls=args.max_total_calls,
            max_prompt_chars_per_call=args.max_prompt_chars_per_call,
            max_total_prompt_chars=args.max_total_prompt_chars,
        )
        return 0 if final_state.get("workflow_status") == "completed" else 1
    except Exception as exc:
        print(f"\n[Error] Pipeline execution failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
