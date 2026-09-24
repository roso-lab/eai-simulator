from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch


def _root(x, y, z):
    return [float(x), float(y), float(z), 1.0, 0.0, 0.0, 0.0] + [0.0] * 6


def _session(*, drift_public_id=None, drift_m=0.0):
    from demo.benchmark.goal_restaurant_fleet import FLEET_CONTROLLER_REGISTRY
    from demo.restaurant_benchmark.config import BENCHMARK_ROBOT_SPAWN_XYZ_M

    articulations = {}
    for public_id, binding in FLEET_CONTROLLER_REGISTRY.items():
        native = binding["native_agent_id"]
        xyz = BENCHMARK_ROBOT_SPAWN_XYZ_M[binding["spawn_key"]]
        articulations[native] = SimpleNamespace(
            data=SimpleNamespace(root_state_w=torch.tensor([_root(*xyz)])))

    class Sim:
        current_time_step_index = 20

        def get_physics_dt(self):
            return 0.005

    sim = Sim()

    class Env:
        def step(self, commands):
            assert len(commands) == 8
            sim.current_time_step_index += 4
            if drift_public_id is not None:
                native = FLEET_CONTROLLER_REGISTRY[drift_public_id]["native_agent_id"]
                articulations[native].data.root_state_w[0, 0] += drift_m

    return SimpleNamespace(
        num_envs=1,
        device="cpu",
        possible_agents=[row["native_agent_id"]
                         for row in FLEET_CONTROLLER_REGISTRY.values()],
        env=Env(),
        base_env=SimpleNamespace(
            step_dt=0.02,
            sim=sim,
            scene=SimpleNamespace(articulations=articulations),
        ),
    )


def test_native_initial_state_binds_full8_roots_but_only_proven_region(tmp_path):
    from demo.benchmark.goal_restaurant_fleet import (
        FLEET_CONTROLLER_REGISTRY,
        RESTAURANT_PUBLIC_TASK_SHA256,
        RestaurantFleetController,
    )
    from demo.benchmark.goal_restaurant_native_state import (
        build_restaurant_native_initial_state,
    )

    fleet = RestaurantFleetController(_session(), products_root=tmp_path)
    fleet.begin_episode("native-state", warmup_ticks=2)
    state = build_restaurant_native_initial_state(fleet)

    assert state["task_id"] == "RESTAURANT-GOAL-01"
    assert state["public_task_sha256"] == RESTAURANT_PUBLIC_TASK_SHA256
    assert state["episode_id"] == "native-state"
    assert state["origin_physics_step"] == 28
    assert state["time_s"] == 0.0
    assert tuple(state["native_initial_roots"]) == tuple(FLEET_CONTROLLER_REGISTRY)
    assert tuple(state["initial_regions"]) == tuple(FLEET_CONTROLLER_REGISTRY)
    assert all(row["post_warmup_displacement_from_spawn_m"] < 1e-5
               for row in state["native_initial_roots"].values())
    assert state["robot_regions"] == {
        "restaurant_carter_1": "restaurant_staging"}
    assert state["initial_regions"]["restaurant_carter_1"]["status"] == (
        "measured_region_membership")
    for public_id in tuple(FLEET_CONTROLLER_REGISTRY)[1:]:
        row = state["initial_regions"][public_id]
        assert row["status"] == "platform_gap"
        assert row["region_id"] is None
        assert row["reason"] == "missing_maintained_spawn_to_initial_region_binding"
    assert state["status"] == "platform_gap"
    assert state["participant_error"] is False
    assert state["all_eight_initial_regions_available"] is False
    assert state["post_warmup_sample_measured"] is True
    assert state["warmup_stability_certified"] is False
    assert state["known_threshold_distinction"] == {
        "m20_navigation_final_tolerance_m": 0.50,
        "legacy_ground_observation_tolerance_m": 0.25,
        "thresholds_equated": False,
        "admission_promoted": False,
    }
    assert state["execution_validated"] is False
    assert state["business_outcomes_validated"] is False
    assert state["feasibility_oracle_verified"] is False
    assert state["release_ready"] is False
    assert state["scoreable"] is False


def test_initial_state_missing_epoch_is_platform_gap_and_does_not_poison_begin(tmp_path):
    from demo.benchmark.goal_restaurant_fleet import RestaurantFleetController
    from demo.benchmark.goal_restaurant_native_state import (
        build_restaurant_native_initial_state,
    )

    fleet = RestaurantFleetController(_session(), products_root=tmp_path)
    gap = build_restaurant_native_initial_state(fleet)
    assert gap["status"] == "platform_gap"
    assert gap["platform_gaps"][0]["reason"] == "post_warmup_epoch_unavailable"
    assert gap["participant_error"] is False
    assert gap["native_initial_roots"] == {}
    # episode_metadata() is a lifecycle query, not a snapshot/route/step.
    fleet.begin_episode("after-gap-query", warmup_ticks=1)


def test_initial_state_records_post_warmup_displacement_without_spawn_gate(tmp_path):
    from demo.benchmark.goal_restaurant_fleet import RestaurantFleetController
    from demo.benchmark.goal_restaurant_native_state import (
        build_restaurant_native_initial_state,
    )

    public_id = "restaurant_m20_1"
    fleet = RestaurantFleetController(
        _session(drift_public_id=public_id, drift_m=0.1), products_root=tmp_path)
    fleet.begin_episode("drifted", warmup_ticks=1)
    state = build_restaurant_native_initial_state(fleet)
    root = state["native_initial_roots"][public_id]
    assert root["post_warmup_displacement_from_spawn_m"] == pytest.approx(0.1)
    assert "spawn_binding_validated" not in root
    assert {row["reason"] for row in state["platform_gaps"]
            if row.get("public_agent_id") == public_id} == {
                "missing_maintained_spawn_to_initial_region_binding",
            }
    assert state["participant_error"] is False


def test_initial_state_rejects_changed_or_foreign_epoch_as_platform_gap(tmp_path):
    from demo.benchmark.goal_restaurant_fleet import RestaurantFleetController
    from demo.benchmark.goal_restaurant_native_state import (
        build_restaurant_native_initial_state,
    )

    fleet = RestaurantFleetController(_session(), products_root=tmp_path)
    fleet.begin_episode("changed-root", warmup_ticks=1)
    fleet.session.base_env.scene.articulations["carter_1"].data.root_state_w[0, 0] += 0.01
    changed = build_restaurant_native_initial_state(fleet)
    assert changed["platform_gaps"][0] == {
        "reason": "invalid_or_changed_post_warmup_root",
        "detail": "restaurant_carter_1",
    }
    assert changed["participant_error"] is False

    class Foreign:
        def episode_metadata(self):
            return {"schema": "foreign", "lifecycle_state": "post_warmup"}

        def snapshot(self):
            raise AssertionError("foreign metadata must fail before snapshot")

    foreign = build_restaurant_native_initial_state(Foreign())
    assert foreign["platform_gaps"][0]["reason"] == (
        "foreign_or_invalid_post_warmup_epoch")
    assert foreign["participant_error"] is False
