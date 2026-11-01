"""
tests/agents/test_reviewer_agent.py

Comprehensive unit and behavioral tests for ReviewerAgent in agents/reviewer_agent.py.
"""

import copy
from typing import Any, Dict, List, Optional
import pytest

from agents.reviewer_agent import (
    REJECTION_SCOPE_MAP,
    STATUS_DEBUG_REQUIRED,
    STATUS_ESCALATION_REQUIRED,
    STATUS_REVIEW_COMPLETE,
    ReviewerAgent,
    ReviewerOutput,
)
from state.engineering_state import create_initial_state
from state.models import (
    CodeArtifact,
    ErrorRecord,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TestArtifact,
    TestPlan,
)

TestArtifact.__test__ = False
TestPlan.__test__ = False
from state.validators import REJECTION_SCOPE_VALUES
from tools.reviewer.coverage_reader import CoverageReport, FakeCoverageReader
from tools.reviewer.static_analyzer import FakeStaticAnalyzer, StaticAnalysisReport
from tools.shared.llm_client import FakeLLMClient


def _sample_valid_requirements() -> ProductRequirements:
    return ProductRequirements(
        functional_requirements=["User can authenticate", "User can query orders"],
        non_functional_requirements=["Response time < 200ms"],
        user_stories=["As a user I want to log in"],
        acceptance_criteria=["Given credentials, returns 200"],
        constraints=["Python 3.11+"],
        business_goals=["High reliability"],
        produced_by="ProductManagerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_architecture() -> SystemArchitecture:
    return SystemArchitecture(
        overview="Clean service architecture",
        technology_stack={"backend": "FastAPI", "frontend": "React"},
        api_specifications=[
            {
                "endpoint": "/api/login",
                "method": "POST",
                "description": "Authenticate user",
            }
        ],
        components=[{"name": "AuthService", "description": "Authentication"}],
        data_models=[{"name": "User", "fields": {"id": "str"}}],
        security_considerations=["JWT auth"],
        scalability_considerations=["Stateless"],
        produced_by="ArchitectAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_valid_code_artifacts() -> Dict[str, CodeArtifact]:
    return {
        "src/auth.py": CodeArtifact(
            file_path="src/auth.py",
            language="python",
            content="def login(): return {'status': 200}",
            artifact_type="backend",
            produced_by="BackendAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_valid_test_artifacts() -> Dict[str, TestArtifact]:
    return {
        "tests/test_auth.py": TestArtifact(
            file_path="tests/test_auth.py",
            content="def test_login(): assert login()['status'] == 200",
            test_type="unit",
            coverage_target="src/auth.py",
            produced_by="QAAgent",
            produced_at="2026-01-01T00:00:00Z",
            version=1,
        )
    }


def _sample_valid_test_plan() -> TestPlan:
    return TestPlan(
        strategy="Unit and integration tests",
        coverage_goals={"src/auth.py": "90%"},
        test_suites=["UnitSuite"],
        quality_metrics={"pass_rate": 100.0},
        produced_by="QAAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )


def _sample_reviewer_output_dict(
    overall_status: str = "approved",
    rejection_scope: Optional[str] = None,
    code_quality_score: float = 9.0,
) -> Dict[str, Any]:
    return {
        "overall_status": overall_status,
        "code_quality_score": code_quality_score,
        "architecture_compliance": True,
        "engineering_standards_compliance": True,
        "security_findings": [],
        "performance_findings": [],
        "maintainability_findings": [],
        "recommendations": ["Add more inline comments"],
        "testing_coverage_assessment": "95% coverage meets target",
        "rejection_scope": rejection_scope,
        "summary": f"Review finished with status {overall_status}",
    }


def _build_test_state(
    workflow_status: str = "qa_complete",
    retry_counts: Optional[Dict[str, int]] = None,
    errors: Optional[List[ErrorRecord]] = None,
    review_report: Optional[ReviewReport] = None,
) -> Dict[str, Any]:
    state = create_initial_state(project_name="TestApp", user_input="Build test app")
    state["requirements"] = _sample_valid_requirements()
    state["architecture"] = _sample_valid_architecture()
    state["code_artifacts"] = _sample_valid_code_artifacts()
    state["test_artifacts"] = _sample_valid_test_artifacts()
    state["test_plan"] = _sample_valid_test_plan()
    state["workflow_status"] = workflow_status
    state["forward_stage"] = 6
    if retry_counts is not None:
        state["retry_counts"] = retry_counts
    if errors is not None:
        state["errors"] = errors
    if review_report is not None:
        state["review_report"] = review_report
    return state


# ===========================================================================
# 1. Decision 5: REJECTION_SCOPE_MAP keys must match REJECTION_SCOPE_VALUES
# ===========================================================================


def test_rejection_scope_map_keys_match_validator():
    """Verify local REJECTION_SCOPE_MAP keys strictly equal REJECTION_SCOPE_VALUES."""
    assert set(REJECTION_SCOPE_MAP.keys()) == set(REJECTION_SCOPE_VALUES)
    assert REJECTION_SCOPE_MAP["code_level"] == "DebugAgent"
    assert REJECTION_SCOPE_MAP["design_level"] == "ArchitectAgent"
    assert REJECTION_SCOPE_MAP["requirements_level"] == "ProductManagerAgent"


# ===========================================================================
# 2. Constructor Argument Validation (Decision 7: required params)
# ===========================================================================


def test_constructor_validates_required_arguments():
    """ReviewerAgent constructor rejects empty model_name, None llm_client, or missing tools."""
    client = FakeLLMClient(responses=[])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()

    with pytest.raises(ValueError, match="model_name must be a non-empty string"):
        ReviewerAgent(
            model_name="",
            llm_client=client,
            static_analyzer=analyzer,
            coverage_reader=reader,
        )

    with pytest.raises(ValueError, match="llm_client must not be None"):
        ReviewerAgent(
            model_name="test-model",
            llm_client=None,  # type: ignore
            static_analyzer=analyzer,
            coverage_reader=reader,
        )

    with pytest.raises(ValueError, match="static_analyzer must not be None"):
        ReviewerAgent(
            model_name="test-model",
            llm_client=client,
            static_analyzer=None,  # type: ignore
            coverage_reader=reader,
        )

    with pytest.raises(ValueError, match="coverage_reader must not be None"):
        ReviewerAgent(
            model_name="test-model",
            llm_client=client,
            static_analyzer=analyzer,
            coverage_reader=None,  # type: ignore
        )


# ===========================================================================
# 3. Fast-Fail Guards: Retry Ceiling & Missing Inputs
# ===========================================================================


def test_execute_fast_fails_when_retry_limit_reached():
    """Fast-fails with retry_exhausted if retry count already equals or exceeds ceiling."""
    client = FakeLLMClient(responses=[])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
        max_retries=3,
    )

    state = _build_test_state(retry_counts={"ReviewerAgent": 3})
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert result["retry_counts"]["ReviewerAgent"] == 3
    assert result["current_agent"] == "ReviewerAgent"
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "Retry limit already reached" in result["errors"][0].message
    assert len(analyzer.calls) == 0


def test_execute_fast_fails_when_mandatory_inputs_missing():
    """Fast-fails with validation_failure if requirements, architecture, or code_artifacts are missing."""
    client = FakeLLMClient(responses=[])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state()
    state["code_artifacts"] = {}
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert len(result["errors"]) == 1
    assert result["errors"][0].error_type == "retry_exhausted"
    assert "Missing or empty" in result["errors"][0].message


# ===========================================================================
# 4. Decision 7: Tool Exceptions Outside LLM Loop Route to Exhaustion
# ===========================================================================


def test_static_analyzer_exception_routes_to_exhaustion():
    """A static analyzer failure outside the loop immediately exhausts without LLM regeneration."""
    client = FakeLLMClient(responses=[_sample_reviewer_output_dict("approved")])
    analyzer = FakeStaticAnalyzer(results=[RuntimeError("Analyzer segfault")])
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert len(result["errors"]) == 1
    assert "Static analysis failed: Analyzer segfault" in result["errors"][0].message
    # LLM should not have been called
    assert len(client.calls) == 0


def test_coverage_reader_exception_routes_to_exhaustion():
    """A coverage reader failure outside the loop immediately exhausts without LLM regeneration."""
    client = FakeLLMClient(responses=[_sample_reviewer_output_dict("approved")])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader(results=[RuntimeError("Coverage report corrupted")])
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert len(result["errors"]) == 1
    assert "Coverage report reading failed: Coverage report corrupted" in result["errors"][0].message
    # LLM should not have been called
    assert len(client.calls) == 0


# ===========================================================================
# 5. Decision 6: Scope Validation Inside LLM Loop & Retry Behavior
# ===========================================================================


def test_invalid_scope_retry_and_recovery():
    """Invalid rejection_scope on rejected retries without sleeping and recovers on second attempt."""
    sleep_calls = []

    def mock_sleep(d: float) -> None:
        sleep_calls.append(d)

    # First attempt: rejected with invalid scope "unknown_scope" -> triggers ValueError
    # Second attempt: rejected with valid scope "code_level" -> succeeds
    client = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("rejected", rejection_scope="unknown_scope"),
            _sample_reviewer_output_dict("rejected", rejection_scope="code_level"),
        ]
    )
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
        sleep_fn=mock_sleep,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_ESCALATION_REQUIRED
    assert result["next_agent"] == "DebugAgent"
    assert result["rejection_scope"] == "code_level"
    assert len(client.calls) == 2
    # Validation error must not perform backoff sleep
    assert len(sleep_calls) == 0


def test_approved_with_scope_retries_and_recovers():
    """Approved outcome with non-empty rejection_scope retries and recovers once cleared."""
    client = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("approved", rejection_scope="code_level"),
            _sample_reviewer_output_dict("approved", rejection_scope=None),
        ]
    )
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_REVIEW_COMPLETE
    assert result["next_agent"] == "DevOpsAgent"
    assert "rejection_scope" not in result
    assert len(client.calls) == 2


def test_requires_changes_with_scope_retries_and_exhausts_when_not_corrected():
    """Requires_changes with non-empty rejection_scope exhausts if persistent."""
    client = FakeLLMClient(
        responses=[
            _sample_reviewer_output_dict("requires_changes", rejection_scope="code_level"),
            _sample_reviewer_output_dict("requires_changes", rejection_scope="code_level"),
            _sample_reviewer_output_dict("requires_changes", rejection_scope="code_level"),
        ]
    )
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
        max_retries=3,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["human_approval_required"] is True
    assert len(result["errors"]) == 1
    assert "rejection_scope must be None for approved or requires_changes" in result["errors"][0].message
    assert len(client.calls) == 3


def test_unhandled_exception_performs_backoff_sleep():
    """Unhandled exception during LLM generation triggers exponential backoff sleep."""
    sleep_calls = []

    def mock_sleep(d: float) -> None:
        sleep_calls.append(d)

    client = FakeLLMClient(
        responses=[
            RuntimeError("Network timeout connecting to LLM"),
            _sample_reviewer_output_dict("approved"),
        ]
    )
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
        sleep_fn=mock_sleep,
    )

    state = _build_test_state()
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_REVIEW_COMPLETE
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == 2.0  # 2.0 ** 1


# ===========================================================================
# 6. Outcomes: approved, requires_changes, and rejected
# ===========================================================================


def test_outcome_approved_writes_expected_state():
    """Approved outcome sets review_complete, DevOpsAgent, forward_stage, and checkpoints."""
    client = FakeLLMClient(responses=[_sample_reviewer_output_dict("approved", code_quality_score=9.5)])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state(workflow_status="qa_complete")
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_REVIEW_COMPLETE
    assert result["next_agent"] == "DevOpsAgent"
    assert result["current_agent"] == "ReviewerAgent"
    assert result["forward_stage"] == 7
    assert result["checkpoint_stage"] == "review_complete"
    assert "review_complete" in result["checkpoints"]
    assert result["retry_counts"]["ReviewerAgent"] == 0
    assert "rejection_scope" not in result
    assert result["review_report"].overall_status == "approved"
    assert result["review_report"].code_quality_score == 9.5
    assert result["review_report"].version == 1


def test_outcome_requires_changes_writes_expected_state():
    """Requires_changes sets debug_required, DebugAgent, appends ErrorRecord, and sets current_debug_target."""
    output_dict = _sample_reviewer_output_dict("requires_changes")
    output_dict["recommendations"] = ["Fix edge case in auth.py"]
    client = FakeLLMClient(responses=[output_dict])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state(workflow_status="qa_complete")
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_DEBUG_REQUIRED
    assert result["next_agent"] == "DebugAgent"
    assert result["current_agent"] == "ReviewerAgent"
    assert result["retry_counts"]["ReviewerAgent"] == 0
    assert "rejection_scope" not in result  # Decision 4: No rejection_scope on requires_changes

    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err.error_type == "other"
    assert err.agent == "ReviewerAgent"
    assert err.stage == STATUS_DEBUG_REQUIRED
    assert "review_requires_changes:" in err.message
    assert "Fix edge case in auth.py" in err.message
    assert result["current_debug_target"] == err.error_id


@pytest.mark.parametrize(
    "scope,expected_target",
    [
        ("code_level", "DebugAgent"),
        ("design_level", "ArchitectAgent"),
        ("requirements_level", "ProductManagerAgent"),
    ],
)
def test_outcome_rejected_at_all_three_scopes(scope: str, expected_target: str):
    """Rejected outcome sets escalation_required, human_approval_required, scope, and mapped next_agent."""
    client = FakeLLMClient(
        responses=[_sample_reviewer_output_dict("rejected", rejection_scope=scope, code_quality_score=4.0)]
    )
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state(workflow_status="qa_complete")
    result = agent.execute(state)

    assert result["workflow_status"] == STATUS_ESCALATION_REQUIRED
    assert result["next_agent"] == expected_target  # Decision 5: points to target agent, not HumanApprovalNode
    assert result["human_approval_required"] is True
    assert result["rejection_scope"] == scope
    assert "ReviewerAgent rejected code" in result["human_approval_notes"]
    assert result["retry_counts"]["ReviewerAgent"] == 0
    assert result["review_report"].overall_status == "rejected"


# ===========================================================================
# 7. Version Bump on Re-Review
# ===========================================================================


def test_review_report_version_bump_on_re_review():
    """Re-reviewing when a previous ReviewReport exists increments the version number."""
    client = FakeLLMClient(responses=[_sample_reviewer_output_dict("approved")])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    prior_report = ReviewReport(
        overall_status="requires_changes",
        code_quality_score=6.0,
        architecture_compliance=True,
        engineering_standards_compliance=False,
        security_findings=[],
        performance_findings=[],
        maintainability_findings=[],
        recommendations=["Improve standards"],
        testing_coverage_assessment="",
        produced_by="ReviewerAgent",
        produced_at="2026-01-01T00:00:00Z",
        version=3,
    )

    state = _build_test_state(review_report=prior_report)
    result = agent.execute(state)

    assert result["review_report"].version == 4


# ===========================================================================
# 8. Input State Unchanged
# ===========================================================================


def test_input_state_unchanged_after_execute():
    """Verify that ReviewerAgent.execute does not mutate any fields of the incoming state dictionary."""
    client = FakeLLMClient(responses=[_sample_reviewer_output_dict("approved")])
    analyzer = FakeStaticAnalyzer()
    reader = FakeCoverageReader()
    agent = ReviewerAgent(
        model_name="test-model",
        llm_client=client,
        static_analyzer=analyzer,
        coverage_reader=reader,
    )

    state = _build_test_state()
    state_before = copy.deepcopy(state)

    _ = agent.execute(state)

    assert state == state_before
