"""Factor matrices separate declared comparisons from executed evidence."""

import pytest

from demo.benchmark.factor_matrix import (
    build_hidden_factor_policy,
    build_public_factor_manifest,
    validate_factor_manifest,
)
from demo.benchmark.factor_experiment import (
    compile_factor_pair, replay_protocol_factor_pair, run_communication_trial,
    validate_factor_pair,
)


@pytest.mark.parametrize("scene_key", ["factory", "warehouse_nvd", "modern_house_nvd"])
def test_every_scene_exposes_lifecycle_and_communication_axes(scene_key):
    public = build_public_factor_manifest(scene_key)
    result = validate_factor_manifest(public, scene_key=scene_key)
    assert result["validated"]
    axes = {row["id"]: row for row in public["axes"]}
    assert {"team_continuity", "communication_quality"} <= set(axes)
    assert axes["communication_quality"]["levels"] == [
        "immediate", "delayed", "dropped", "bandwidth_limited", "stale"
    ]
    assert "selected_level" not in public["participant_visibility"]


def test_hidden_policy_binds_exact_public_axes_without_revealing_episode_condition():
    public = build_public_factor_manifest("warehouse_nvd")
    hidden = build_hidden_factor_policy("warehouse_nvd")
    assert hidden["axes"] == public["axes"]
    assert hidden["one_factor_at_a_time"] is True
    assert "selected_level" in hidden["private_fields"]
    assert "message_delivery_schedule" in hidden["private_fields"]


def test_factor_manifest_rejects_tampered_comparison_rule():
    public = build_public_factor_manifest("modern_house_nvd")
    public["comparison_rule"] = "change every factor"
    with pytest.raises(ValueError, match="comparison rule"):
        validate_factor_manifest(public, scene_key="modern_house_nvd")


@pytest.mark.parametrize("scene_key", ["factory", "warehouse_nvd", "modern_house_nvd"])
def test_protocol_factor_pair_is_private_condition_plan(scene_key):
    pair = compile_factor_pair(scene_key, "communication_quality", "immediate", "delayed",
                               frozen_inputs={"task_id": f"{scene_key}-fixture", "seed": 7})
    assert validate_factor_pair(pair, scene_key=scene_key)["execution_scope"] == "protocol_components"
    assert pair["public"]["levels"] == ["immediate", "delayed"]
    assert pair["private"]["condition_recipe"]["variant"]["conditions"]["delay_s"] == 2.0
    assert "event_time_s" not in pair["public"]
    assert "message_delivery_schedule" not in pair["public"]


def test_domain_factor_pair_stays_fixture_pending_and_rejects_truth_in_frozen_inputs():
    pair = compile_factor_pair("warehouse_nvd", "order_priority", "routine", "priority",
                               frozen_inputs={"task_id": "warehouse-fixture", "seed": 7})
    assert validate_factor_pair(pair, scene_key="warehouse_nvd")["execution_scope"] == "fixture_pending"
    with pytest.raises(ValueError, match="evaluator-only"):
        compile_factor_pair("warehouse_nvd", "order_priority", "routine", "priority",
                            frozen_inputs={"selected_level": "priority"})


@pytest.mark.parametrize("level,expected", [
    ("immediate", ["m"]), ("delayed", ["m"]), ("dropped", []), ("stale", []),
])
def test_communication_trial_returns_replayable_protocol_evidence(level, expected):
    result = run_communication_trial(level, messages=[
        {"message_id": "m", "producer_id": "camera", "estimate": {"kind": "rack"},
         "published_at_s": 0.0, "consume": True, "advance_to_s": 3.0},
    ])
    assert result["observed_message_ids"] == expected
    assert result["protocol_validated"]
    assert result["execution_validated"] is False


def test_protocol_pair_replay_requires_window_and_observable_effect():
    pair = compile_factor_pair(
        "warehouse_nvd", "communication_quality", "immediate", "delayed",
        frozen_inputs={"task_id": "warehouse-fixture", "seed": 11})
    seen = []

    def runner(recipe):
        seen.append(recipe)
        return {
            "status": "completed",
            "intervention_time_s": 2.0,
            "decision_window": {"opened_at_s": 1.0, "closed_at_s": 3.0},
            "observable_signature_before": {"message_ids": []},
            "observable_signature_after": {
                "message_ids": ["rack"],
                "available_at_s": recipe["conditions"]["delay_s"],
            },
            "feasibility_observed": True,
        }

    result = replay_protocol_factor_pair(pair, scene_key="warehouse_nvd", runner=runner)
    assert result["status"] == "validated"
    assert result["factor_effect_validated"] is True
    assert result["paired_observable_difference"] is True
    assert result["execution_validated"] is False
    assert result["physical_execution_validated"] is False
    assert [item["conditions"]["delay_s"] for item in seen] == [0.0, 2.0]


def test_protocol_pair_replay_is_inconclusive_when_intervention_is_outside_window():
    pair = compile_factor_pair(
        "modern_house_nvd", "communication_quality", "immediate", "delayed",
        frozen_inputs={"task_id": "house-fixture", "seed": 12})
    result = replay_protocol_factor_pair(pair, scene_key="modern_house_nvd", runner=lambda _recipe: {
        "status": "completed",
        "intervention_time_s": 5.0,
        "decision_window": {"opened_at_s": 1.0, "closed_at_s": 3.0},
        "observable_signature_before": {"ready": True},
        "observable_signature_after": {"ready": True},
        "feasibility_observed": True,
    })
    assert result["status"] == "inconclusive"
    assert result["factor_effect_validated"] is False
    assert result["paired_observable_difference"] is False


def test_protocol_pair_compares_trials_when_dropped_side_has_no_within_trial_change():
    pair = compile_factor_pair(
        "factory", "communication_quality", "immediate", "dropped",
        frozen_inputs={"task_id": "factory-fixture", "seed": 15})

    def runner(recipe):
        delivered = [] if recipe["conditions"]["drop"] else ["station-observation"]
        return {
            "status": "completed",
            "intervention_time_s": 1.0,
            "decision_window": {"opened_at_s": 0.5, "closed_at_s": 2.0},
            "observable_signature_before": {"message_ids": []},
            "observable_signature_after": {"message_ids": delivered},
            "feasibility_observed": True,
        }

    result = replay_protocol_factor_pair(pair, scene_key="factory", runner=runner)
    assert result["status"] == "validated"
    assert result["trials"][1]["observable_change_within_trial"] is False
    assert result["paired_observable_difference"] is True


def test_protocol_pair_replay_rejects_physical_scope_and_runner_errors():
    pair = compile_factor_pair(
        "warehouse_nvd", "order_priority", "routine", "priority",
        frozen_inputs={"task_id": "warehouse-fixture", "seed": 13})
    with pytest.raises(ValueError, match="protocol-components"):
        replay_protocol_factor_pair(pair, scene_key="warehouse_nvd", runner=lambda _recipe: {})

    pair = compile_factor_pair(
        "warehouse_nvd", "communication_quality", "immediate", "dropped",
        frozen_inputs={"task_id": "warehouse-fixture", "seed": 14})
    result = replay_protocol_factor_pair(pair, scene_key="warehouse_nvd",
                                         runner=lambda _recipe: {"status": "completed"})
    assert result["status"] == "failed"
    assert all(trial["status"] == "error" for trial in result["trials"])
