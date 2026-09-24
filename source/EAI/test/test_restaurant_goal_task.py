"""Offline checks for Restaurant matrix admission with release gates closed."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_contract import validate_goal_task_spec
from demo.benchmark.goal_driven_tasks import (
    build_goal_task_package,
    review_goal_task_design,
    review_goal_business_witness,
    validate_goal_factor_pair,
)
from demo.benchmark.goal_scene_registry import registered_goal_scenes
from demo.benchmark.measurement_contract import AUTHORING_ONLY_SCENE_KEYS, SCENE_KEYS


def test_restaurant_goal_bundle_has_public_pool_spawns_and_pending_release():
    bundle = build_goal_task_package("restaurant")
    public = bundle["public_task"]

    assert public["task_id"] == "RESTAURANT-GOAL-01"
    assert len(public["candidate_robot_pool"]) == 8
    assert public["budgets"]["max_active_agents"] == 8
    assert set(public["public_world"]["robot_spawn_positions"]) == {
        row["id"] for row in public["candidate_robot_pool"]
    }
    assert public["budgets"]["max_active_agents"] == len(
        public["candidate_robot_pool"]
    )
    assert bundle["hidden_evaluator"]["release_gate"]["scoreable"] is False
    assert bundle["hidden_evaluator"]["release_gate"]["execution_status"] == "external_pending"
    assert bundle["hidden_evaluator"]["heterogeneity_certificate"][
        "declaration_level_validated"
    ] is False
    assert "restaurant_candidate_native_admission" in bundle[
        "hidden_evaluator"
    ]["release_gate"]["blockers"]


def test_restaurant_witnesses_cover_two_business_orders_and_cross_mobility():
    bundle = build_goal_task_package("restaurant")
    public = bundle["public_task"]
    witnesses = bundle["hidden_evaluator"]["witness_plans"]

    assert len(witnesses) == 2
    assert {tuple(w["plan"]["assigned_team"]) for w in witnesses} == {
        ("restaurant_carter_1",),
        ("restaurant_carter_2", "restaurant_m20_1"),
    }
    for witness in witnesses:
        report = review_goal_business_witness(public, witness)
        assert report["validated"] is True
        assert report["runtime_outcomes_validated"] is False
        tasks = {row["task_id"]: row for row in witness["plan"]["tasks"]}
        relay = next(row for row in tasks.values() if row["skill"] == "relay")
        assert {ref for ref in relay["prerequisites"] if "observe" in ref} or any(
            row["skill"] == "observe" and row["task_id"] in relay["prerequisites"]
            for row in tasks.values()
        )


def test_restaurant_spawn_map_must_cover_exact_candidate_pool():
    public = deepcopy(build_goal_task_package("restaurant")["public_task"])
    public["public_world"]["robot_spawn_positions"].pop("restaurant_carter_1")
    with pytest.raises(ValueError, match="cover exactly the candidate robot pool"):
        validate_goal_task_spec(public)


def test_restaurant_is_registered_in_full_matrix_without_promoting_release():
    registrations = {row["scene_key"]: row for row in registered_goal_scenes()}
    assert registrations["restaurant"]["package_kind"] == "goal_bundle"
    assert registrations["restaurant"]["scoreable"] is False
    assert "restaurant" in SCENE_KEYS
    assert "restaurant" not in AUTHORING_ONLY_SCENE_KEYS


def test_restaurant_base_v2_pool_rejects_generic_expansion_profile():
    with pytest.raises(ValueError, match="base authoring deployment"):
        build_goal_task_package("restaurant", pool_profile="expanded_v1")


@pytest.mark.parametrize(
    ("factor_id", "factor_level"),
    [("communication_quality", "delayed"),
     ("communication_quality", "dropped"),
     ("communication_quality", "bandwidth_limited"),
     ("communication_quality", "stale"),
     ("team_continuity", "member_exit"),
     ("team_continuity", "member_join"),
     ("team_continuity", "capability_change")],
)
def test_restaurant_all_factor_treatments_preserve_business_and_closed_release(
        factor_id, factor_level):
    control = build_goal_task_package("restaurant", factor_id=factor_id)
    treatment = build_goal_task_package(
        "restaurant", factor_id=factor_id, factor_level=factor_level)
    assert validate_goal_factor_pair(control, treatment, factor_id=factor_id)["validated"]
    assert treatment["hidden_evaluator"]["release_gate"]["scoreable"] is False
    assert treatment["hidden_evaluator"]["release_gate"]["execution_status"] == "external_pending"
    assert treatment["hidden_evaluator"]["witness_plans"] == control["hidden_evaluator"]["witness_plans"]
    events = treatment["hidden_evaluator"]["hidden_event_schedule"]["team_events"]
    if factor_level in {"member_exit", "capability_change"}:
        assert events[0]["target_selector"]["required_capability"] == "relay"
    if factor_level == "capability_change":
        assert events[0]["capability_patch"] == {"remove": ["relay"], "add": []}
    if factor_level == "member_join":
        assert "relay" in events[0]["agent_declaration"]["public_capabilities"]


def test_restaurant_frozen_baseline_hash_and_single_robot_solution_are_preserved():
    from demo.benchmark.provenance import canonical_sha256

    bundle = build_goal_task_package("restaurant")
    assert canonical_sha256(bundle["public_task"]) == "e34fe98668acece2234fda06140f867840f58c272e11216430151ce86eac9084"
    assert canonical_sha256(bundle["hidden_evaluator"]) == "aa61416a7a5476d81062d4d7a180cac602fbe13400119bf635917d51d864b69b"
    assert len(bundle["hidden_evaluator"]["witness_plans"][0]["plan"]["assigned_team"]) == 1


def test_restaurant_mechanism_package_identity_uses_its_actual_tier():
    from demo.benchmark.mechanism_templates import M0_MECHANISMS, M1_MECHANISMS

    for tier, mechanisms in (("M0", M0_MECHANISMS), ("M1", M1_MECHANISMS)):
        for mechanism in mechanisms:
            bundle = build_goal_task_package("restaurant", mechanism_family=mechanism)
            assert bundle["public_task"]["task_id"] == f"RESTAURANT-GOAL-01-{tier}-{mechanism}"
            assert bundle["hidden_evaluator"]["mechanism_template"]["template_id"] == (
                f"restaurant.{tier.lower()}.{mechanism}")


def test_restaurant_observation_requires_same_robot_to_have_arrived():
    bundle = build_goal_task_package("restaurant")
    public = bundle["public_task"]
    witness = deepcopy(bundle["hidden_evaluator"]["witness_plans"][1])
    witness["plan"]["assigned_team"].append("restaurant_m20_2")
    observation = next(
        row for row in witness["plan"]["tasks"]
        if row["task_id"] == "restaurant_b_observe_kitchen"
    )
    observation["assigned_agents"] = ["restaurant_m20_2"]
    with pytest.raises(ValueError, match="kitchen_observation_after_ground_arrival"):
        review_goal_task_design(public, [
            deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]), witness,
        ])
