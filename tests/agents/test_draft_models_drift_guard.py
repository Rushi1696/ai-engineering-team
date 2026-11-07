"""
tests/agents/test_draft_models_drift_guard.py

Drift guard tests ensuring draft models match their full model counterparts
minus the agent-stamped metadata fields.
"""

from agents.architect_agent import DraftSystemArchitecture
from agents.product_manager_agent import DraftProductRequirements
from agents.task_planner_agent import DraftImplementationTask, DraftTaskPlan
from state.models import ImplementationTask, ProductRequirements, SystemArchitecture, TaskPlan
from tools.shared.llm_client import FakeLLMClient


def test_product_manager_draft_model_drift_guard():
    """DraftProductRequirements fields must exactly equal ProductRequirements minus stamped set."""
    full_fields = set(ProductRequirements.model_fields.keys())
    stamped_fields = {"produced_by", "produced_at", "version"}
    draft_fields = set(DraftProductRequirements.model_fields.keys())

    assert draft_fields == full_fields - stamped_fields
    for field_name in draft_fields:
        assert (
            DraftProductRequirements.model_fields[field_name].annotation
            == ProductRequirements.model_fields[field_name].annotation
        )


def test_architect_draft_model_drift_guard():
    """DraftSystemArchitecture fields must exactly equal SystemArchitecture minus stamped set."""
    full_fields = set(SystemArchitecture.model_fields.keys())
    stamped_fields = {"produced_by", "produced_at", "version"}
    draft_fields = set(DraftSystemArchitecture.model_fields.keys())

    assert draft_fields == full_fields - stamped_fields
    for field_name in draft_fields:
        assert (
            DraftSystemArchitecture.model_fields[field_name].annotation
            == SystemArchitecture.model_fields[field_name].annotation
        )


def test_task_planner_draft_model_drift_guard():
    """DraftTaskPlan and DraftImplementationTask fields must match full models minus stamped set."""
    # Top-level TaskPlan
    full_plan_fields = set(TaskPlan.model_fields.keys())
    stamped_plan_fields = {"produced_by", "produced_at", "version"}
    draft_plan_fields = set(DraftTaskPlan.model_fields.keys())
    assert draft_plan_fields == full_plan_fields - stamped_plan_fields

    # Nested ImplementationTask (status is agent-owned and stamped as "pending")
    full_task_fields = set(ImplementationTask.model_fields.keys())
    stamped_task_fields = {"status"}
    draft_task_fields = set(DraftImplementationTask.model_fields.keys())
    assert draft_task_fields == full_task_fields - stamped_task_fields

    for field_name in draft_task_fields:
        assert (
            DraftImplementationTask.model_fields[field_name].annotation
            == ImplementationTask.model_fields[field_name].annotation
        )


def test_fake_llm_client_slicing_compatibility_with_full_models():
    """FakeLLMClient automatically slices full models down to requested draft models."""
    full_req = ProductRequirements(
        functional_requirements=["F1"],
        non_functional_requirements=["N1"],
        user_stories=["U1"],
        acceptance_criteria=["A1"],
        constraints=["C1"],
        business_goals=["B1"],
        produced_by="PM",
        produced_at="2026-01-01T00:00:00Z",
        version=1,
    )

    client = FakeLLMClient(responses=[full_req])
    draft_res = client.generate_structured(
        prompt="test prompt",
        response_model=DraftProductRequirements,
    )

    assert isinstance(draft_res, DraftProductRequirements)
    assert draft_res.functional_requirements == ["F1"]
    assert not hasattr(draft_res, "produced_by")
