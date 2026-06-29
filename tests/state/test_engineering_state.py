"""
tests/test_engineering_state.py

Comprehensive test suite for state/engineering_state.py, per Phase 1
Technical Design Document Section 10 and the explicit verification
requirements for this implementation step:
  - all approved fields exist
  - reducer assignments are correct per field
  - initial state creation behaves correctly
  - serialization compatibility (JSON round-trip)
  - LangGraph compatibility (TypedDict + Annotated[T, reducer_fn] shape,
    and an actual compiled-graph smoke test if langgraph is installed)

IMPORTANT FIELD-COUNT NOTE: this suite asserts 40 fields, not 41. An
exhaustive enumeration of every field named across requirements.md Req
1-21 and design.md's State Ownership Matrix -- independently cross-
checked against the Phase 1 Technical Design Document's own Section 1.2
group table and Section 2 field table -- yields exactly 40 unique field
names. The "41" figure in upstream prose (final-sync-validation-report.md,
Technical Design Document narrative text) does not correspond to an
actual 41st field anywhere in the approved requirements/design documents.
This is flagged here as a discovered discrepancy in the approved
documents, not resolved by fabricating a field in engineering_state.py or
in this test suite.
"""

import json
import sys
from typing import Annotated, get_args, get_origin, get_type_hints

sys.path.insert(0, "/home/claude")

import pytest

from state.engineering_state import (
    DEFAULT_MAX_DEBUG_CYCLES,
    ENGINEERING_STATE_FIELDS,
    EngineeringState,
    create_initial_state,
)
from state.models import (
    AgentExecutionMetadata,
    CodeArtifact,
    DeploymentArtifact,
    DocumentationArtifact,
    ErrorRecord,
    FinalProjectOutput,
    MemoryReferences,
    ProductRequirements,
    ReviewReport,
    SystemArchitecture,
    TaskPlan,
    TestArtifact,
    TestPlan,
)
from state.reducers import (
    append_reducer,
    dict_merge_reducer,
    memory_references_reducer,
    replace_reducer,
)


# ---------------------------------------------------------------------------
# Helper: extract the {field_name: reducer_fn} mapping straight from the
# TypedDict's __annotations__ (each value is Annotated[T, reducer_fn]).
# ---------------------------------------------------------------------------


def _extract_reducer_map():
    """Extract the {field_name: reducer_fn} mapping from EngineeringState's
    annotations.

    engineering_state.py uses `from __future__ import annotations`, which
    means every class-level annotation is stored as a lazy string
    (ForwardRef) in `__annotations__` rather than as a live
    `Annotated[...]` object. `typing.get_type_hints(..., include_extras=True)`
    is the correct, standard way to resolve these back into real
    `Annotated[T, reducer_fn]` objects so the Annotated metadata (the
    reducer function) can actually be inspected -- reading
    `__annotations__` directly would only yield unresolved ForwardRef
    strings and lose the reducer reference entirely.
    """
    import state.engineering_state as es_module

    resolved = get_type_hints(EngineeringState, include_extras=True, globalns=vars(es_module))
    reducer_map = {}
    for field_name, annotation in resolved.items():
        assert get_origin(annotation) is Annotated, (
            f"Field '{field_name}' is not wrapped in Annotated[...]"
        )
        args = get_args(annotation)
        # args[0] is the inner type, args[1:] are the metadata (reducer fn)
        assert len(args) >= 2, f"Field '{field_name}' has no reducer metadata"
        reducer_map[field_name] = args[1]
    return reducer_map


REDUCER_MAP = _extract_reducer_map()


# ---------------------------------------------------------------------------
# 1. Field existence — all approved fields are present
# ---------------------------------------------------------------------------


def test_field_manifest_has_forty_fields():
    assert len(ENGINEERING_STATE_FIELDS) == 40
    assert len(set(ENGINEERING_STATE_FIELDS)) == 40, "duplicate field names found"


def test_typeddict_annotations_match_field_manifest_exactly():
    class_fields = set(EngineeringState.__annotations__.keys())
    assert class_fields == set(ENGINEERING_STATE_FIELDS)


def test_typeddict_has_no_extra_or_missing_fields():
    class_fields = set(EngineeringState.__annotations__.keys())
    manifest_fields = set(ENGINEERING_STATE_FIELDS)
    assert class_fields - manifest_fields == set(), "class has fields not in manifest"
    assert manifest_fields - class_fields == set(), "manifest has fields not in class"


@pytest.mark.parametrize("field_name", ENGINEERING_STATE_FIELDS)
def test_every_field_individually_present_on_typeddict(field_name):
    assert field_name in EngineeringState.__annotations__


# Explicit presence checks for every field named in Req 1-21 / design.md
# Section 2, grouped exactly as the Technical Design Document groups them.
# This duplicates the parametrized check above deliberately, as a
# directly-readable manifest a reviewer can diff against the requirements
# documents without running pytest -v.

IDENTITY_FIELDS = ["run_id", "session_id", "project_name", "created_at", "updated_at"]
USER_INPUT_FIELDS = ["user_input", "user_context"]
DOC_OUTPUT_FIELDS = ["requirements", "architecture", "task_plan", "test_plan", "review_report"]
ARTIFACT_COLLECTION_FIELDS = [
    "code_artifacts", "test_artifacts", "deployment_artifacts", "documentation_artifacts",
]
WORKFLOW_ROUTING_FIELDS = ["workflow_status", "current_agent", "next_agent", "pipeline_stage"]
ERROR_TRACKING_FIELDS = ["errors"]
RETRY_ESCALATION_FIELDS = [
    "retry_counts", "max_retries", "human_approval_required", "human_approval_notes",
]
EXECUTION_MEMORY_FIELDS = ["execution_metadata", "agent_scratchpad", "memory_references"]
CHECKPOINT_VERSIONING_FIELDS = ["checkpoint_stage", "state_version", "checkpoints"]
FINAL_OUTPUT_FIELDS = ["final_output"]
WORKFLOW_CONTROL_FIELDS = [
    "total_debug_cycles", "pipeline_started_at", "pipeline_deadline_at",
    "current_debug_target", "rejection_scope", "forward_stage",
    "total_invocations", "max_debug_cycles",
]

ALL_GROUPS = (
    IDENTITY_FIELDS + USER_INPUT_FIELDS + DOC_OUTPUT_FIELDS
    + ARTIFACT_COLLECTION_FIELDS + WORKFLOW_ROUTING_FIELDS + ERROR_TRACKING_FIELDS
    + RETRY_ESCALATION_FIELDS + EXECUTION_MEMORY_FIELDS + CHECKPOINT_VERSIONING_FIELDS
    + FINAL_OUTPUT_FIELDS + WORKFLOW_CONTROL_FIELDS
)


def test_group_breakdown_sums_to_forty_and_matches_manifest():
    assert len(ALL_GROUPS) == 40
    assert set(ALL_GROUPS) == set(ENGINEERING_STATE_FIELDS)
    assert len(set(ALL_GROUPS)) == 40  # no duplicates across groups


def test_group_field_counts_match_technical_design_section_1_2():
    assert len(IDENTITY_FIELDS) == 5
    assert len(USER_INPUT_FIELDS) == 2
    assert len(DOC_OUTPUT_FIELDS) == 5
    assert len(ARTIFACT_COLLECTION_FIELDS) == 4
    assert len(WORKFLOW_ROUTING_FIELDS) == 4
    assert len(ERROR_TRACKING_FIELDS) == 1
    assert len(RETRY_ESCALATION_FIELDS) == 4
    assert len(EXECUTION_MEMORY_FIELDS) == 3
    assert len(CHECKPOINT_VERSIONING_FIELDS) == 3
    assert len(FINAL_OUTPUT_FIELDS) == 1
    assert len(WORKFLOW_CONTROL_FIELDS) == 8


# ---------------------------------------------------------------------------
# 2. Reducer assignment correctness — every field maps to the correct
#    reducer function per Technical Design Document Section 3.1/3.5.
# ---------------------------------------------------------------------------


EXPECTED_REPLACE_REDUCER_FIELDS = {
    "run_id", "session_id", "project_name", "created_at", "updated_at",
    "user_input",
    "requirements", "architecture", "task_plan", "test_plan", "review_report",
    "workflow_status", "current_agent", "next_agent", "pipeline_stage",
    "human_approval_required", "human_approval_notes",
    "checkpoint_stage", "state_version",
    "final_output",
    "total_debug_cycles", "pipeline_started_at", "pipeline_deadline_at",
    "current_debug_target", "rejection_scope", "forward_stage",
    "total_invocations", "max_debug_cycles",
}

EXPECTED_DICT_MERGE_REDUCER_FIELDS = {
    "user_context",
    "code_artifacts", "test_artifacts", "deployment_artifacts", "documentation_artifacts",
    "retry_counts", "max_retries",
    "execution_metadata", "agent_scratchpad",
    "checkpoints",
}

EXPECTED_APPEND_REDUCER_FIELDS = {"errors"}

EXPECTED_MEMORY_REFERENCES_REDUCER_FIELDS = {"memory_references"}


def test_replace_reducer_field_set_is_correct():
    actual = {f for f, r in REDUCER_MAP.items() if r is replace_reducer}
    assert actual == EXPECTED_REPLACE_REDUCER_FIELDS


def test_dict_merge_reducer_field_set_is_correct():
    actual = {f for f, r in REDUCER_MAP.items() if r is dict_merge_reducer}
    assert actual == EXPECTED_DICT_MERGE_REDUCER_FIELDS


def test_append_reducer_field_set_is_correct():
    actual = {f for f, r in REDUCER_MAP.items() if r is append_reducer}
    assert actual == EXPECTED_APPEND_REDUCER_FIELDS


def test_memory_references_reducer_field_set_is_correct():
    actual = {f for f, r in REDUCER_MAP.items() if r is memory_references_reducer}
    assert actual == EXPECTED_MEMORY_REFERENCES_REDUCER_FIELDS


def test_every_field_has_exactly_one_of_the_four_known_reducers():
    known_reducers = {replace_reducer, dict_merge_reducer, append_reducer, memory_references_reducer}
    for field_name, reducer_fn in REDUCER_MAP.items():
        assert reducer_fn in known_reducers, (
            f"Field '{field_name}' uses an unrecognized reducer: {reducer_fn}"
        )


def test_reducer_field_sets_partition_all_forty_fields_with_no_overlap():
    all_sets = [
        EXPECTED_REPLACE_REDUCER_FIELDS,
        EXPECTED_DICT_MERGE_REDUCER_FIELDS,
        EXPECTED_APPEND_REDUCER_FIELDS,
        EXPECTED_MEMORY_REFERENCES_REDUCER_FIELDS,
    ]
    union = set()
    total = 0
    for s in all_sets:
        union |= s
        total += len(s)
    assert total == 40, "reducer field sets overlap or do not sum to 40"
    assert union == set(ENGINEERING_STATE_FIELDS)


@pytest.mark.parametrize("field_name", sorted(EXPECTED_REPLACE_REDUCER_FIELDS))
def test_individual_replace_reducer_assignment(field_name):
    assert REDUCER_MAP[field_name] is replace_reducer


@pytest.mark.parametrize("field_name", sorted(EXPECTED_DICT_MERGE_REDUCER_FIELDS))
def test_individual_dict_merge_reducer_assignment(field_name):
    assert REDUCER_MAP[field_name] is dict_merge_reducer


def test_errors_field_uses_append_reducer_not_replace():
    # Explicitly distinguishes from a common mistake: errors must
    # accumulate, never silently replace the whole list.
    assert REDUCER_MAP["errors"] is append_reducer
    assert REDUCER_MAP["errors"] is not replace_reducer


def test_memory_references_does_not_use_generic_append_or_dict_merge():
    # Section 3.5: memory_references gets its OWN composite reducer,
    # distinct from both append_reducer and dict_merge_reducer.
    assert REDUCER_MAP["memory_references"] is memory_references_reducer
    assert REDUCER_MAP["memory_references"] is not append_reducer
    assert REDUCER_MAP["memory_references"] is not dict_merge_reducer


# ---------------------------------------------------------------------------
# 3. Architectural invariants preserved (caller's explicit point 7)
# ---------------------------------------------------------------------------


def test_pipeline_stage_field_exists_and_defaults_to_zero():
    state = create_initial_state(project_name="p", user_input="build x")
    assert "pipeline_stage" in state
    assert state["pipeline_stage"] == 0


def test_pipeline_stage_uses_replace_reducer_and_no_increment_logic_present():
    assert REDUCER_MAP["pipeline_stage"] is replace_reducer
    # There is no function in this module that increments pipeline_stage;
    # confirmed by source inspection (no occurrence of pipeline_stage + 1
    # or pipeline_stage += anywhere in engineering_state.py).
    import inspect
    import state.engineering_state as es_module
    source = inspect.getsource(es_module)
    assert "pipeline_stage + 1" not in source
    assert "pipeline_stage +=" not in source
    assert "pipeline_stage] + 1" not in source


def test_final_output_has_no_field_level_guard():
    state = create_initial_state(project_name="p", user_input="build x")
    assert state["final_output"] is None
    # Confirm no guard logic exists tying final_output writes to
    # workflow_status anywhere in this module.
    import inspect
    import state.engineering_state as es_module
    source = inspect.getsource(es_module)
    assert "final_output" in source  # sanity: field is referenced at all
    # The only "if" statements in the module pertain to create_initial_state's
    # own input validation (project_name, user_input), not to final_output.
    assert "workflow_status ==" not in source
    assert "workflow_status !=" not in source


def test_checkpoints_field_accepts_forensic_debug_keys_via_dict_merge():
    # engineering_state.py performs no key-format validation on
    # checkpoints (that is validators.py's deliberate no-op, per
    # Req 17 crit 9). Confirm the reducer itself imposes no key
    # restriction either, by directly invoking it.
    existing = {"backend_complete": "2026-06-20T10:00:00Z"}
    incoming = {"debug_1_start": "2026-06-20T10:01:00Z", "debug_1_complete": "2026-06-20T10:02:00Z"}
    merged = dict_merge_reducer(existing, incoming)
    assert merged["debug_1_start"] == "2026-06-20T10:01:00Z"
    assert merged["debug_1_complete"] == "2026-06-20T10:02:00Z"
    assert merged["backend_complete"] == "2026-06-20T10:00:00Z"
    assert REDUCER_MAP["checkpoints"] is dict_merge_reducer


def test_total_debug_cycles_field_exists_defaults_to_zero_and_is_replace_reducer():
    state = create_initial_state(project_name="p", user_input="build x")
    assert state["total_debug_cycles"] == 0
    assert REDUCER_MAP["total_debug_cycles"] is replace_reducer


def test_total_debug_cycles_never_reset_by_this_module():
    # create_initial_state is the only function in this module that
    # touches total_debug_cycles, and it does so exactly once at
    # construction. There is no other code path here that could reset it.
    import inspect
    import state.engineering_state as es_module
    source = inspect.getsource(es_module)
    occurrences = source.count("total_debug_cycles")
    # Appears in: docstring comment(s), class annotation, initial-state
    # dict literal. Should NOT appear inside any reset/zero-out helper
    # function, since no such function exists in this module.
    assert occurrences >= 2  # class field + initial value, at minimum
    assert "total_debug_cycles\": 0" in source or '"total_debug_cycles": 0' in source


def test_current_debug_target_field_exists_and_defaults_to_none():
    state = create_initial_state(project_name="p", user_input="build x")
    assert "current_debug_target" in state
    assert state["current_debug_target"] is None
    assert REDUCER_MAP["current_debug_target"] is replace_reducer


def test_forward_stage_field_exists_and_defaults_to_zero():
    state = create_initial_state(project_name="p", user_input="build x")
    assert "forward_stage" in state
    assert state["forward_stage"] == 0
    assert REDUCER_MAP["forward_stage"] is replace_reducer


def test_total_invocations_field_exists_and_defaults_to_zero():
    state = create_initial_state(project_name="p", user_input="build x")
    assert "total_invocations" in state
    assert state["total_invocations"] == 0
    assert REDUCER_MAP["total_invocations"] is replace_reducer


def test_max_debug_cycles_defaults_to_ten():
    assert DEFAULT_MAX_DEBUG_CYCLES == 10
    state = create_initial_state(project_name="p", user_input="build x")
    assert state["max_debug_cycles"] == 10


def test_rejection_scope_field_exists_and_defaults_to_none():
    state = create_initial_state(project_name="p", user_input="build x")
    assert "rejection_scope" in state
    assert state["rejection_scope"] is None


def test_engineering_state_module_does_not_import_validators():
    # Caller's explicit requirement 6: validation logic belongs to
    # validators.py, not engineering_state.py.
    import inspect
    import state.engineering_state as es_module
    source = inspect.getsource(es_module)
    assert "import validators" not in source
    assert "from .validators" not in source
    assert "from state.validators" not in source


# ---------------------------------------------------------------------------
# 4. Initial state creation correctness
# ---------------------------------------------------------------------------


def test_create_initial_state_returns_all_forty_fields():
    state = create_initial_state(project_name="demo", user_input="build a thing")
    assert set(state.keys()) == set(ENGINEERING_STATE_FIELDS)
    assert len(state) == 40


def test_create_initial_state_sets_workflow_status_initiated():
    state = create_initial_state(project_name="demo", user_input="build a thing")
    assert state["workflow_status"] == "initiated"


def test_create_initial_state_generates_run_id_and_session_id():
    state = create_initial_state(project_name="demo", user_input="build a thing")
    assert isinstance(state["run_id"], str) and len(state["run_id"]) > 0
    assert isinstance(state["session_id"], str) and len(state["session_id"]) > 0


def test_create_initial_state_two_calls_produce_distinct_run_ids():
    s1 = create_initial_state(project_name="demo", user_input="x")
    s2 = create_initial_state(project_name="demo", user_input="x")
    assert s1["run_id"] != s2["run_id"]
    assert s1["session_id"] != s2["session_id"]  # auto-generated, also distinct


def test_create_initial_state_honors_explicit_session_id():
    state = create_initial_state(
        project_name="demo", user_input="x", session_id="fixed-session-123",
    )
    assert state["session_id"] == "fixed-session-123"


def test_create_initial_state_created_at_equals_updated_at_initially():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["created_at"] == state["updated_at"]


def test_create_initial_state_pipeline_started_at_equals_created_at():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["pipeline_started_at"] == state["created_at"]


def test_create_initial_state_user_context_defaults_to_empty_dict():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["user_context"] == {}


def test_create_initial_state_honors_explicit_user_context():
    state = create_initial_state(
        project_name="demo", user_input="x", user_context={"locale": "en-US"},
    )
    assert state["user_context"] == {"locale": "en-US"}


def test_create_initial_state_optional_outputs_default_to_none():
    state = create_initial_state(project_name="demo", user_input="x")
    for field in ["requirements", "architecture", "task_plan", "test_plan", "review_report", "final_output"]:
        assert state[field] is None


def test_create_initial_state_artifact_collections_default_to_empty_dict():
    state = create_initial_state(project_name="demo", user_input="x")
    for field in ["code_artifacts", "test_artifacts", "deployment_artifacts", "documentation_artifacts"]:
        assert state[field] == {}


def test_create_initial_state_errors_defaults_to_empty_list():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["errors"] == []


def test_create_initial_state_memory_references_is_a_memory_references_instance():
    state = create_initial_state(project_name="demo", user_input="x")
    assert isinstance(state["memory_references"], MemoryReferences)
    assert state["memory_references"].long_term == []


def test_create_initial_state_honors_pipeline_deadline_at():
    state = create_initial_state(
        project_name="demo", user_input="x", pipeline_deadline_at="2026-07-01T00:00:00Z",
    )
    assert state["pipeline_deadline_at"] == "2026-07-01T00:00:00Z"


def test_create_initial_state_pipeline_deadline_at_defaults_to_none():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["pipeline_deadline_at"] is None


def test_create_initial_state_honors_custom_max_debug_cycles():
    state = create_initial_state(project_name="demo", user_input="x", max_debug_cycles=25)
    assert state["max_debug_cycles"] == 25


def test_create_initial_state_rejects_empty_project_name():
    with pytest.raises(ValueError):
        create_initial_state(project_name="", user_input="x")


def test_create_initial_state_rejects_empty_user_input():
    with pytest.raises(ValueError):
        create_initial_state(project_name="demo", user_input="")


def test_create_initial_state_rejects_whitespace_only_user_input():
    with pytest.raises(ValueError):
        create_initial_state(project_name="demo", user_input="    \n\t  ")


def test_create_initial_state_rejects_oversized_user_input():
    with pytest.raises(ValueError):
        create_initial_state(project_name="demo", user_input="x" * 100_001)


def test_create_initial_state_accepts_user_input_at_upper_boundary():
    state = create_initial_state(project_name="demo", user_input="x" * 100_000)
    assert len(state["user_input"]) == 100_000


def test_create_initial_state_accepts_user_input_at_lower_boundary():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["user_input"] == "x"


def test_create_initial_state_state_version_starts_at_zero():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["state_version"] == 0


def test_create_initial_state_checkpoint_stage_starts_at_initiated():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["checkpoint_stage"] == "initiated"


def test_create_initial_state_human_approval_required_defaults_false():
    state = create_initial_state(project_name="demo", user_input="x")
    assert state["human_approval_required"] is False


# ---------------------------------------------------------------------------
# 5. Serialization compatibility (JSON round-trip / checkpoint design)
# ---------------------------------------------------------------------------


def _serialize_state(state: EngineeringState) -> dict:
    """Mirrors the serialization contract described in the Technical
    Design Document Section 7.2: Pydantic sub-models are dumped via
    .model_dump(); plain dict/list/scalar fields pass through as-is.
    """
    result = {}
    for field_name, value in state.items():
        if value is None:
            result[field_name] = None
        elif hasattr(value, "model_dump"):
            result[field_name] = value.model_dump()
        elif isinstance(value, dict):
            result[field_name] = {
                k: (v.model_dump() if hasattr(v, "model_dump") else v)
                for k, v in value.items()
            }
        elif isinstance(value, list):
            result[field_name] = [
                (item.model_dump() if hasattr(item, "model_dump") else item)
                for item in value
            ]
        else:
            result[field_name] = value
    return result


def test_initial_state_serializes_to_json_with_zero_custom_encoders():
    state = create_initial_state(project_name="demo", user_input="build a thing")
    serialized = _serialize_state(state)
    json_str = json.dumps(serialized)
    assert isinstance(json_str, str)


def test_initial_state_json_round_trip_preserves_scalar_fields():
    state = create_initial_state(
        project_name="demo", user_input="build a thing", session_id="sess-fixed",
    )
    serialized = _serialize_state(state)
    json_str = json.dumps(serialized)
    restored = json.loads(json_str)

    for field in ["run_id", "session_id", "project_name", "created_at", "updated_at",
                  "workflow_status", "pipeline_stage", "state_version",
                  "total_debug_cycles", "forward_stage", "total_invocations",
                  "max_debug_cycles"]:
        assert restored[field] == state[field]


def test_populated_state_with_pydantic_models_round_trips_via_json():
    state = create_initial_state(project_name="demo", user_input="build a thing")
    state["requirements"] = ProductRequirements(
        functional_requirements=["log in"], produced_by="ProductManagerAgent",
        produced_at="2026-06-20T10:00:00Z",
    )
    state["code_artifacts"] = {
        "src/x.py": CodeArtifact(
            file_path="src/x.py", language="python", content="print(1)",
            artifact_type="backend", produced_by="BackendAgent", produced_at="t",
        )
    }
    state["errors"] = [
        ErrorRecord(error_id="e1", agent="A", stage="s", error_type="timeout",
                    message="m", occurred_at="t")
    ]

    serialized = _serialize_state(state)
    json_str = json.dumps(serialized)
    restored = json.loads(json_str)

    assert restored["requirements"]["functional_requirements"] == ["log in"]
    assert restored["code_artifacts"]["src/x.py"]["language"] == "python"
    assert restored["errors"][0]["error_id"] == "e1"


def test_no_datetime_or_set_objects_anywhere_in_serialized_initial_state():
    import datetime as _dt

    state = create_initial_state(project_name="demo", user_input="x")
    serialized = _serialize_state(state)

    def _walk(value):
        assert not isinstance(value, (_dt.date, _dt.datetime))
        assert not isinstance(value, set)
        if isinstance(value, dict):
            for k, v in value.items():
                assert isinstance(k, str)
                _walk(v)
        elif isinstance(value, list):
            for item in value:
                _walk(item)

    _walk(serialized)


def test_all_timestamp_fields_are_strings_not_datetime_objects():
    state = create_initial_state(project_name="demo", user_input="x")
    for field in ["created_at", "updated_at", "pipeline_started_at"]:
        assert isinstance(state[field], str)
    assert state["pipeline_deadline_at"] is None or isinstance(state["pipeline_deadline_at"], str)


# ---------------------------------------------------------------------------
# 6. LangGraph compatibility
# ---------------------------------------------------------------------------


def test_engineering_state_is_a_typeddict_not_a_pydantic_model():
    from pydantic import BaseModel
    assert not issubclass(EngineeringState, BaseModel) if isinstance(EngineeringState, type) else True
    # TypedDict classes are not regular classes at runtime in the way
    # isinstance/issubclass works for BaseModel; the authoritative check
    # is that it behaves as a plain dict at runtime and exposes
    # __annotations__ in the TypedDict style.
    assert hasattr(EngineeringState, "__annotations__")
    state = create_initial_state(project_name="demo", user_input="x")
    assert isinstance(state, dict)  # TypedDict instances are plain dicts at runtime


def test_every_field_annotation_uses_annotated_wrapper():
    resolved = get_type_hints(EngineeringState, include_extras=True)
    for field_name, annotation in resolved.items():
        assert get_origin(annotation) is Annotated, (
            f"Field '{field_name}' is not Annotated[...] -- found {annotation}"
        )


def test_no_bare_unannotated_fields_exist():
    # Every single field must carry reducer metadata; this is exhaustive
    # over all 40 fields, not just a sample.
    resolved = get_type_hints(EngineeringState, include_extras=True)
    for field_name in ENGINEERING_STATE_FIELDS:
        annotation = resolved[field_name]
        args = get_args(annotation)
        assert len(args) == 2, (
            f"Field '{field_name}' should have exactly one reducer in its "
            f"Annotated metadata, found: {args[1:]}"
        )


def _langgraph_is_installed() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _langgraph_is_installed(), reason="langgraph not installed"
)
def test_langgraph_graph_compiles_with_engineering_state():
    """If langgraph is installed, attempt an actual StateGraph compile
    using EngineeringState as the schema, with a trivial single-node
    happy-path graph. This is the most direct possible proof of LangGraph
    compatibility -- it doesn't just check shape, it proves LangGraph's
    own state-merge machinery accepts the schema and executes correctly
    with these reducers wired in.
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        pytest.skip("langgraph not installed in this environment")

    def dummy_node(state: EngineeringState) -> dict:
        # Return a partial update touching several different reducer
        # types to confirm LangGraph actually applies them.
        return {
            "workflow_status": "requirements_complete",
            "code_artifacts": {
                "src/a.py": CodeArtifact(
                    file_path="src/a.py", language="python", content="x",
                    artifact_type="backend", produced_by="dummy_node", produced_at="t",
                )
            },
            "errors": [],
            "total_invocations": 1,
        }

    builder = StateGraph(EngineeringState)
    builder.add_node("dummy", dummy_node)
    builder.set_entry_point("dummy")
    builder.add_edge("dummy", END)
    graph = builder.compile()

    initial_state = create_initial_state(project_name="demo", user_input="build x")
    result = graph.invoke(initial_state)

    assert result["workflow_status"] == "requirements_complete"
    assert "src/a.py" in result["code_artifacts"]
    # run_id must be unchanged -- proves replace_reducer didn't get
    # accidentally triggered for a field the node didn't touch.
    assert result["run_id"] == initial_state["run_id"]


def test_langgraph_importability_does_not_crash_if_absent():
    # This test exists purely to document that the module itself has NO
    # hard dependency on langgraph being installed -- engineering_state.py
    # only needs typing.TypedDict / typing.Annotated, both stdlib.
    import state.engineering_state as es_module
    assert "import langgraph" not in open(es_module.__file__).read()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))