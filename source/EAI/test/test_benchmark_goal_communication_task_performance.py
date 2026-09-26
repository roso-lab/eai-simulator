"""Focused tests for Stage71 preregistered terminal MCR replay."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_communication_task_performance import (
    TASK_PERFORMANCE_MAPPING_SCHEMA,
    TASK_PERFORMANCE_METHOD_ID,
    TASK_PERFORMANCE_REPLAY_SCHEMA,
    replay_communication_task_performance,
    validate_communication_task_performance_mapping,
)
from demo.benchmark.goal_contract_v4 import (
    ACTIVATION_EPISODE_START,
    freeze_contract_v4,
    migrate_v3_to_contract_v4,
)
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_progress_evidence import GoalProgressRecorder
from demo.benchmark.provenance import canonical_sha256


def _public(scene="factory"):
    return build_goal_task_package(scene)["public_task"]


def _frozen_contract(public, *, weights=None):
    ids = [row["id"] for row in public["required_outcomes"]]
    weights = weights or {key: 1.0 for key in ids}
    specs = {
        key: {
            "weight": weights[key],
            "activation_rule": ACTIVATION_EPISODE_START,
            "stability_duration_s": 0.0,
            "monotonicity": "monotonic",
            "anti_double_count_rule": "first_external_satisfaction_only",
        }
        for key in ids
    }
    return freeze_contract_v4(
        migrate_v3_to_contract_v4(
            public, milestone_specs=specs)["task_v4"])


def _mapping(public, *, weights=None, reasons=None):
    return {
        "schema": TASK_PERFORMANCE_MAPPING_SCHEMA,
        "version": 1,
        "method_id": TASK_PERFORMANCE_METHOD_ID,
        "metric_name": "MCR_comm",
        "frozen_contract_v4": _frozen_contract(public, weights=weights),
        "progress_source": {
            "schema": "eai.benchmark.goal_progress_evidence",
            "version": 1,
            "evaluator_ref": "stage71-evaluator",
            "clock_source": "contract_clock",
        },
        "sample_selector": "terminal",
        "predicate_binding": (
            "contract_outcome_ref_equals_progress_predicate_id"),
        "unknown_policy": "metric_na",
        "revocation_policy": "monotonic_contract_violation_na",
        "stability_policy": "require_zero_duration",
        "eligible_termination_reasons": reasons or ["participant_stopped"],
        "higher_is_better": True,
        "release": False,
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }


def _progress(public, states, *, reason="participant_stopped",
              evaluator_ref="stage71-evaluator", clock="contract_clock"):
    recorder = GoalProgressRecorder(public, "stage71-episode")
    for index, values in enumerate(states):
        predicates = {
            key: {
                "satisfied": value,
                "evidence_refs": ([] if value is None else [f"test://{index}/{key}"]),
            }
            for key, value in values.items()
        }
        recorder.append({
            "episode_id": "stage71-episode",
            "time_s": float(index),
            "clock_source": clock,
            "evaluator_ref": evaluator_ref,
            "predicates": predicates,
        }, boundary=("initial" if index == 0 else
                     "terminal" if index == len(states) - 1
                     else "skill_completed"),
            host_time_s=float(index))
    return recorder.report(
        termination_reason=reason, host_cutoff_s=float(len(states) - 1))


def test_explicit_unequal_weights_produce_terminal_mcr_without_defaults():
    public = _public()
    ids = [row["id"] for row in public["required_outcomes"]]
    weights = {ids[0]: 1.0, ids[1]: 3.0, ids[2]: 0.0}
    mapping = _mapping(public, weights=weights)
    report = _progress(public, [
        {key: None for key in ids},
        {ids[0]: True, ids[1]: False, ids[2]: None},
    ])

    replay = replay_communication_task_performance(
        mapping, report, public, episode_id="stage71-episode")

    assert replay["schema"] == TASK_PERFORMANCE_REPLAY_SCHEMA
    assert replay["status"] == "computed"
    assert replay["MCR_comm"] == pytest.approx(.25)
    assert replay["SR_comm"] is None
    assert replay["AUP_comm"] is None
    assert replay["e_step"] is None
    assert replay["unknown_predicate_ids"] == []
    assert replay["mapping_spec_sha256"] == canonical_sha256(mapping)
    assert replay["release"] is False
    assert replay["scoreable"] is False


def test_known_zero_is_not_unknown_and_unknown_is_not_zero():
    public = _public()
    ids = [row["id"] for row in public["required_outcomes"]]
    mapping = _mapping(public)
    known = _progress(public, [
        {key: None for key in ids},
        {key: False for key in ids},
    ])
    zero = replay_communication_task_performance(
        mapping, known, public, episode_id="stage71-episode")
    assert zero["status"] == "computed"
    assert zero["MCR_comm"] == 0.0

    unknown = _progress(public, [
        {key: None for key in ids},
        {**{key: False for key in ids}, ids[0]: None},
    ])
    missing = replay_communication_task_performance(
        mapping, unknown, public, episode_id="stage71-episode")
    assert missing["status"] == "na_unknown_terminal_predicates"
    assert missing["MCR_comm"] is None
    assert missing["unknown_predicate_ids"] == [ids[0]]


@pytest.mark.parametrize("terminal, expected_status", [
    (False, "na_monotonicity_violation"),
    (None, "na_unknown_terminal_predicates"),
])
def test_true_then_revoked_or_unknown_never_uses_first_or_last_wins(
        terminal, expected_status):
    public = _public()
    ids = [row["id"] for row in public["required_outcomes"]]
    report = _progress(public, [
        {key: None for key in ids},
        {**{key: False for key in ids}, ids[0]: True},
        {**{key: False for key in ids}, ids[0]: terminal},
    ])
    replay = replay_communication_task_performance(
        _mapping(public), report, public, episode_id="stage71-episode")
    assert replay["status"] == expected_status
    assert replay["MCR_comm"] is None


def test_ineligible_termination_is_na_even_when_terminal_is_known():
    public = _public()
    ids = [row["id"] for row in public["required_outcomes"]]
    report = _progress(public, [
        {key: None for key in ids},
        {key: True for key in ids},
    ], reason="execution_error")
    replay = replay_communication_task_performance(
        _mapping(public), report, public, episode_id="stage71-episode")
    assert replay["status"] == "na_ineligible_termination_reason"
    assert replay["eligible_termination"] is False
    assert replay["MCR_comm"] is None


@pytest.mark.parametrize("field, value, match", [
    ("metric_name", "AUP_comm", "only MCR_comm"),
    ("method_id", "unknown", "schema/version/method"),
    ("sample_selector", "first_true", "sample_selector"),
    ("unknown_policy", "count_zero", "unknown_policy"),
    ("release", True, "release must be false"),
])
def test_mapping_is_closed_and_policy_values_are_not_negotiated_at_replay(
        field, value, match):
    public = _public()
    mapping = _mapping(public)
    mapping[field] = value
    with pytest.raises(ValueError, match=match):
        validate_communication_task_performance_mapping(
            mapping, contract_sha256=canonical_sha256(public),
            task_id=public["task_id"])


@pytest.mark.parametrize(("mutation", "match"), [
    ("extra_mapping_field", "fields mismatch"),
    ("missing_mapping_field", "fields mismatch"),
    ("extra_progress_source_field", "fields mismatch"),
    ("unknown_mapping_version", "schema/version/method"),
    ("empty_termination_domain", "unique nonempty strings"),
    ("non_json_termination_domain", "unique nonempty strings"),
    ("duplicate_termination_reason", "unique nonempty strings"),
])
def test_mapping_and_progress_source_use_closed_versioned_schemas(
        mutation, match):
    public = _public()
    mapping = _mapping(public)
    if mutation == "extra_mapping_field":
        mapping["inferred_default"] = True
    elif mutation == "missing_mapping_field":
        mapping.pop("unknown_policy")
    elif mutation == "extra_progress_source_field":
        mapping["progress_source"]["fallback_reader"] = "forbidden"
    elif mutation == "unknown_mapping_version":
        mapping["version"] = 2
    elif mutation == "empty_termination_domain":
        mapping["eligible_termination_reasons"] = []
    elif mutation == "non_json_termination_domain":
        mapping["eligible_termination_reasons"] = ("participant_stopped",)
    else:
        mapping["eligible_termination_reasons"] = [
            "participant_stopped", "participant_stopped"]
    with pytest.raises(ValueError, match=match):
        validate_communication_task_performance_mapping(
            mapping, contract_sha256=canonical_sha256(public),
            task_id=public["task_id"])


@pytest.mark.parametrize("field, value, match", [
    ("stability_duration_s", 1.0, "zero stability"),
    ("activation_rule", "exogenous_event:ready", "episode-start active"),
    ("monotonicity", "non_monotonic", "not monotonic"),
    ("anti_double_count_rule", "last_wins", "anti-double-count"),
])
def test_unsupported_contract_semantics_fail_at_mapping_validation(
        field, value, match):
    public = _public()
    mapping = _mapping(public)
    task = deepcopy(mapping["frozen_contract_v4"]["task"])
    task["contract_milestones"][0][field] = value
    mapping["frozen_contract_v4"] = freeze_contract_v4(task)
    with pytest.raises(ValueError, match=match):
        validate_communication_task_performance_mapping(
            mapping, contract_sha256=canonical_sha256(public),
            task_id=public["task_id"])


def test_seal_base_task_and_progress_source_are_bound():
    public = _public()
    mapping = _mapping(public)
    mapping["frozen_contract_v4"]["seal_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="seal mismatch"):
        validate_communication_task_performance_mapping(
            mapping, contract_sha256=canonical_sha256(public),
            task_id=public["task_id"])

    other = _mapping(_public("warehouse_nvd"))
    with pytest.raises(ValueError, match="base task"):
        validate_communication_task_performance_mapping(
            other, contract_sha256=canonical_sha256(public),
            task_id=public["task_id"])

    ids = [row["id"] for row in public["required_outcomes"]]
    report = _progress(public, [
        {key: None for key in ids},
        {key: False for key in ids},
    ], evaluator_ref="another-reader")
    with pytest.raises(ValueError, match="evaluator/clock"):
        replay_communication_task_performance(
            _mapping(public), report, public, episode_id="stage71-episode")
