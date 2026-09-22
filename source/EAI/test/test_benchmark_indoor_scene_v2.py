"""Offline checks for the opt-in indoor bound_native_v2 deployment profile.

Default (legacy_v1) factory / warehouse_nvd / modern_house_nvd packages must
stay byte-identical to the frozen digests captured at c5f94fa.  The opt-in
profile only expands the pool, publishes spawn positions, and renames the
public task id.
"""

from __future__ import annotations

import pytest

from demo.benchmark.goal_driven_tasks import (
    _INDOOR_V2_TASK_IDS,
    build_goal_task_package,
    build_goal_task_spec,
)
from demo.benchmark.provenance import canonical_sha256


_FROZEN_INDOOR_SHA256 = {
    ("factory", "public_task"):
        "49c97523de62278b99e2e544e4ca3b911a96a8f4572c6892dcf6bebf81c30350",
    ("factory", "hidden_evaluator"):
        "dc232ef56604a5c0ed7c132e9b2c5325812351d69ace3c2a4a570c115fca33c9",
    ("warehouse_nvd", "public_task"):
        "25052247bf3176f4903b2fb4838226b9d67f5d069ad6ff93e29c97864ecdabf5",
    ("warehouse_nvd", "hidden_evaluator"):
        "0817399a230f8f4995424be24b61ab68e3e3c7be73038b2c10d1e6f5d1b7b927",
    ("modern_house_nvd", "public_task"):
        "2c808a29dee58f61eba575987b4eefda4f839baec5e47585e004d3c971f28857",
    ("modern_house_nvd", "hidden_evaluator"):
        "3d9c3c570cb727aa69fe7040e7c14ff6a00a7c8cda475340d9303fe78b8e96aa",
}

_INDOOR_SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")
_LEGACY_TASK_IDS = {
    "factory": "FACTORY-GOAL-01",
    "warehouse_nvd": "WAREHOUSE-GOAL-01",
    "modern_house_nvd": "MODERN_HOUSE-GOAL-01",
}
_CORE_SERVICE_SKILLS = {
    "factory": {"stair_traversal", "pick", "place", "handoff", "relay"},
    "warehouse_nvd": {"pick", "place", "handoff", "cooperative_carry", "relay"},
    "modern_house_nvd": {"pick", "place", "cooperative_carry", "relay"},
}


@pytest.mark.parametrize("scene", _INDOOR_SCENES)
def test_default_indoor_package_hashes_are_frozen(scene):
    bundle = build_goal_task_package(scene)
    assert canonical_sha256(bundle["public_task"]) == _FROZEN_INDOOR_SHA256[(scene, "public_task")]
    assert (canonical_sha256(bundle["hidden_evaluator"])
            == _FROZEN_INDOOR_SHA256[(scene, "hidden_evaluator")])
    assert canonical_sha256(build_goal_task_spec(scene)) == _FROZEN_INDOOR_SHA256[(scene, "public_task")]


@pytest.mark.parametrize("scene", _INDOOR_SCENES)
def test_legacy_default_keeps_four_robot_pool_and_task_id(scene):
    public = build_goal_task_spec(scene)
    assert public["task_id"] == _LEGACY_TASK_IDS[scene]
    assert len(public["candidate_robot_pool"]) == 4
    assert public["budgets"]["max_active_agents"] == 3
    assert "robot_spawn_positions" not in public["public_world"]


@pytest.mark.parametrize("scene", _INDOOR_SCENES)
def test_bound_native_v2_pool_spawns_and_budget(scene):
    public = build_goal_task_spec(scene, indoor_roster_profile="bound_native_v2")
    pool = public["candidate_robot_pool"]
    assert public["task_id"] == _INDOOR_V2_TASK_IDS[scene]
    assert len(pool) >= 6
    assert public["budgets"]["max_active_agents"] == len(pool)

    types = {row["type"] for row in pool}
    mobilities = {row["mobility"] for row in pool}
    assert len(types) >= 3
    assert mobilities == {"aerial", "ground"}

    spawns = public["public_world"]["robot_spawn_positions"]
    assert set(spawns) == {row["id"] for row in pool}
    for agent_id, position in spawns.items():
        assert len(position) == 3
        assert all(isinstance(value, float) for value in position)

    decoys = [
        row for row in pool
        if set(row["public_capabilities"]) <= {"ground_navigation", "aerial_navigation", "observe"}
        and "observe" in row["public_capabilities"]
        and len(row["public_capabilities"]) == 2
        and row["type"] in {"lite3", "cf2x"}
    ]
    assert decoys, "opt-in pool must include a navigation+observe decoy"

    core_skills = _CORE_SERVICE_SKILLS[scene]
    holders: dict[str, set[str]] = {}
    for row in pool:
        for skill in core_skills & set(row["public_capabilities"]):
            holders.setdefault(skill, set()).add(row["id"])
    duplicated = [skill for skill, agents in holders.items() if len(agents) >= 2]
    assert duplicated, "at least one core service capability must appear on two robots"
    go2 = next(row for row in pool if row["type"] == "go2")
    assert "pick" in go2["public_capabilities"]
    assert "place" in go2["public_capabilities"]
    assert "manipulator" in go2["interfaces"]
    assert "fixed_gripper_v1" in go2["physical_profile"]["grasp_interfaces"]
    decoy = next(row for row in pool if row["type"] == "lite3")
    assert "manipulator" not in decoy["interfaces"]
    assert "pick" not in decoy["public_capabilities"]


@pytest.mark.parametrize("scene", _INDOOR_SCENES)
def test_bound_native_v2_package_builds(scene):
    bundle = build_goal_task_package(scene, indoor_roster_profile="bound_native_v2")
    public = bundle["public_task"]
    assert public["task_id"] == _INDOOR_V2_TASK_IDS[scene]
    assert len(public["candidate_robot_pool"]) >= 6
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    assert len(witnesses) >= 2
    for witness in witnesses:
        assert "pool_note" in witness
        assert "不产生服务" in witness["pool_note"]


@pytest.mark.parametrize("scene", _INDOOR_SCENES)
def test_bound_native_v2_still_accepts_expanded_v1_measurement_layer(scene):
    public = build_goal_task_spec(
        scene, indoor_roster_profile="bound_native_v2", pool_profile="expanded_v1")
    ids = [row["id"] for row in public["candidate_robot_pool"]]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 11
    assert public["budgets"]["max_active_agents"] == 7


def test_city_roster_profile_still_rejected_on_indoor_scenes():
    with pytest.raises(ValueError, match="City roster profile applies only"):
        build_goal_task_spec("factory", city_roster_profile="bound_native_v2")
    with pytest.raises(ValueError, match="City roster profile applies only"):
        build_goal_task_package("warehouse_nvd", city_roster_profile="bound_native_v1")


def test_indoor_roster_profile_rejected_on_city_and_unknown():
    with pytest.raises(ValueError, match="Indoor roster profile does not apply"):
        build_goal_task_spec("city", indoor_roster_profile="bound_native_v2")
    with pytest.raises(ValueError, match="unsupported indoor roster profile"):
        build_goal_task_spec("factory", indoor_roster_profile="not_a_profile")
