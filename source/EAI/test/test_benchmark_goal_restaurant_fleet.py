from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch


def _root(x, y, z):
    return [float(x), float(y), float(z), 1.0, 0.0, 0.0, 0.0] + [0.0] * 6


def _session():
    from demo.benchmark.goal_restaurant_fleet import FLEET_CONTROLLER_REGISTRY
    from demo.restaurant_benchmark.config import BENCHMARK_ROBOT_SPAWN_XYZ_M

    articulations = {}
    for binding in FLEET_CONTROLLER_REGISTRY.values():
        native = binding["native_agent_id"]
        xyz = BENCHMARK_ROBOT_SPAWN_XYZ_M[binding["spawn_key"]]
        articulations[native] = SimpleNamespace(
            data=SimpleNamespace(root_state_w=torch.tensor([_root(*xyz)])))

    class Sim:
        current_time_step_index = 100
        physics_dt = 0.005

        def get_physics_dt(self):
            return self.physics_dt

    sim = Sim()

    class Env:
        def __init__(self):
            self.calls = []

        def step(self, commands):
            self.calls.append({key: value.detach().cpu().clone()
                               for key, value in commands.items()})
            sim.current_time_step_index += 4

    return SimpleNamespace(
        num_envs=1,
        device="cpu",
        possible_agents=[binding["native_agent_id"]
                         for binding in FLEET_CONTROLLER_REGISTRY.values()],
        env=Env(),
        base_env=SimpleNamespace(
            step_dt=0.02,
            sim=sim,
            scene=SimpleNamespace(articulations=articulations),
        ),
    )


def _install_fake_planner(monkeypatch, expected_start):
    from EAI.scenario_spec import geometry, oracle
    from demo.benchmark import runtime

    class Map:
        sha256 = "a" * 64

        def __init__(self, path):
            assert Path(path).name == "restaurant_benchmark_map.yaml"

    class Grid:
        def __init__(self, occupancy, radius):
            assert occupancy.sha256 == "a" * 64
            assert radius == pytest.approx(0.40)

        def path(self, start, goal):
            assert list(start) == pytest.approx(expected_start)
            assert len(goal) == 2
            return [(0, 0), (1, 0)]

    monkeypatch.setattr(geometry, "FactoryMap", Map)
    monkeypatch.setattr(oracle, "InflatedGrid", Grid)
    monkeypatch.setattr(runtime, "route_waypoints",
                        lambda _grid, _cells, goal, preserve_goal: [tuple(goal)])


def _route(controller, monkeypatch, public_id, region_id, operation):
    state = controller.snapshot()["agents"][public_id]
    _install_fake_planner(monkeypatch, state["position_m"][:2])
    return controller.plan_route(public_id, operation + "-route", region_id)


def test_full8_selection_uses_one_to_one_runtime_instances_and_real_sources():
    from demo.benchmark import goal_restaurant_fleet as fleet

    selection = fleet.build_restaurant_full_fleet_selection()
    assert [row["type"] for row in selection["robots"]] == [
        "carter", "carter", "m20", "m20", "go2", "lite3", "iris", "iris"]
    assert [row["controller"]["cfg"] for row in selection["robots"]] == [
        "CARTER_DIFF_CFG", "CARTER_DIFF_CFG", "M20_NAV_RSL_CFG",
        "M20_NAV_RSL_CFG", "GO2_VELOCITY_RSL_CFG", "LITE3_VELOCITY_RSL_CFG",
        "PEGASUS_IRIS_POSITION_CFG", "PEGASUS_IRIS_POSITION_CFG"]
    assert tuple(binding["native_agent_id"]
                 for binding in fleet.FLEET_CONTROLLER_REGISTRY.values()) == (
        "carter_1", "carter_2", "m20_1", "m20_2",
        "go2_1", "lite3_1", "iris_1", "iris_2")
    manifest = fleet.restaurant_fleet_source_manifest()
    assert set(manifest["controllers"]) == set(fleet.FLEET_CONTROLLER_REGISTRY)
    assert manifest["controllers"]["restaurant_carter_1"][
        "controller_source"].endswith("traditional/carter_diff/carter_diff.py")
    assert all(len(row["controller_source_sha256"]) == 64
               for row in manifest["controllers"].values())
    assert manifest["execution_validated"] is False


def test_controller_manifest_rejects_env_builder_declaration_drift(tmp_path):
    from demo.benchmark import goal_restaurant_fleet as fleet

    drifted = tmp_path / "env_builder.py"
    drifted.write_text(
        "CONTROLLER_CFG_IMPORTS = {'CARTER_DIFF_CFG': "
        "('wrong.py', 'CARTER_DIFF_CFG')}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="declaration drifted"):
        fleet.restaurant_fleet_source_manifest(env_builder_path=drifted)


def test_idle_tick_commands_every_agent_and_holds_goal_controllers_explicitly(tmp_path):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    before = controller.snapshot()
    tick = controller.advance_idle_once()
    assert len(session.env.calls) == 1
    commands = session.env.calls[0]
    assert set(commands) == set(session.possible_agents)
    for public_id in ("restaurant_m20_1", "restaurant_m20_2"):
        native = fleet.FLEET_CONTROLLER_REGISTRY[public_id]["native_agent_id"]
        xyz = before["agents"][public_id]["position_m"]
        assert commands[native][0].tolist() == pytest.approx([xyz[0], xyz[1], 0.0])
    for public_id in ("restaurant_iris_1", "restaurant_iris_2"):
        native = fleet.FLEET_CONTROLLER_REGISTRY[public_id]["native_agent_id"]
        assert commands[native][0].tolist() == pytest.approx(
            before["agents"][public_id]["position_m"])
    assert tick["start_physics_step"] == 100
    assert tick["physics_step"] == 104
    assert tick["time_s"] == pytest.approx(0.02)
    assert tick["execution_validated"] is False
    assert tick["business_outcomes_validated"] is False


def test_begin_episode_warms_full8_archives_old_epoch_and_rebases_time_zero(tmp_path):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    hold_targets = deepcopy(controller._hold_targets)
    hold_yaws = deepcopy(controller._hold_yaws)
    metadata = controller.begin_episode("stage51-test", warmup_ticks=3)

    assert len(session.env.calls) == 3
    assert all(set(commands) == set(session.possible_agents)
               for commands in session.env.calls)
    assert controller._hold_targets == hold_targets
    assert controller._hold_yaws == hold_yaws
    assert metadata["lifecycle_state"] == "post_warmup"
    assert metadata["native_initial_state_ready"] is True
    assert metadata["origin_physics_step"] == 112
    assert metadata["origin_time_s"] == 0.0
    assert metadata["post_warmup_sample"]["physics_step"] == 112
    assert metadata["post_warmup_sample"]["time_s"] == 0.0
    assert set(metadata["post_warmup_sample"]["agents"]) == set(
        fleet.FLEET_CONTROLLER_REGISTRY)
    assert metadata["post_warmup_sample_measured"] is True
    assert metadata["warmup_stability_certified"] is False
    assert metadata["execution_validated"] is False
    assert metadata["scoreable"] is False

    snapshot = controller.snapshot()
    assert snapshot["physics_step"] == 112
    assert snapshot["time_s"] == 0.0
    assert snapshot["agents"] == metadata["post_warmup_sample"]["agents"]
    assert snapshot["episode"]["lifecycle_state"] == "post_warmup"
    assert "post_warmup_sample" not in snapshot["episode"]

    archive_path = Path(metadata["archive"]["path"])
    assert archive_path.parent == tmp_path
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["old_epoch"]["origin_physics_step"] == 100
    assert archive["old_epoch"]["pre_warmup_physics_step"] == 100
    assert len(archive["warmup_ticks"]) == 3
    assert [row["warmup_index"] for row in archive["warmup_ticks"]] == [0, 1, 2]
    assert [row["physics_step"] for row in archive["warmup_ticks"]] == [104, 108, 112]
    assert [row["time_s"] for row in archive["warmup_ticks"]] == pytest.approx(
        [0.02, 0.04, 0.06])
    assert all(set(row["agents"]) == set(fleet.FLEET_CONTROLLER_REGISTRY)
               for row in archive["warmup_ticks"])
    assert archive["post_warmup_sample"] == metadata["post_warmup_sample"]
    assert len(metadata["archive"]["sha256"]) == 64


def test_warmup_preserves_original_m20_hold_when_controller_equilibrium_moves(tmp_path):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    original_step = session.env.step

    def moving_step(commands):
        original_step(commands)
        session.base_env.scene.articulations["m20_1"].data.root_state_w[0, 0] += 0.1

    session.env.step = moving_step
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    target = list(controller._hold_targets["restaurant_m20_1"])
    metadata = controller.begin_episode("moving-m20", warmup_ticks=2)

    assert controller._hold_targets["restaurant_m20_1"] == target
    assert session.env.calls[0]["m20_1"][0].tolist() == pytest.approx(
        [target[0], target[1], 0.0])
    assert session.env.calls[1]["m20_1"][0].tolist() == pytest.approx(
        [target[0], target[1], 0.0])
    assert metadata["post_warmup_sample"]["agents"]["restaurant_m20_1"][
        "position_m"][0] == pytest.approx(target[0] + 0.2)
    assert metadata["warmup_stability_certified"] is False


@pytest.mark.parametrize("warmup_ticks", [True, 0, -1, 10_001, 1.5, "2"])
def test_begin_episode_rejects_unbounded_or_noninteger_warmup(tmp_path, warmup_ticks):
    from demo.benchmark import goal_restaurant_fleet as fleet

    controller = fleet.RestaurantFleetController(_session(), products_root=tmp_path)
    with pytest.raises(ValueError, match="warmup ticks"):
        controller.begin_episode("bad-warmup", warmup_ticks=warmup_ticks)


@pytest.mark.parametrize("activity", ["snapshot", "step", "route"])
def test_begin_episode_rejects_prior_public_fleet_activity(tmp_path, activity):
    from demo.benchmark import goal_restaurant_fleet as fleet

    controller = fleet.RestaurantFleetController(_session(), products_root=tmp_path)
    if activity == "snapshot":
        controller.snapshot()
    elif activity == "step":
        controller.step_once()
    else:
        with pytest.raises(ValueError, match="outside the full8 pool"):
            controller.plan_route("foreign", "route", "restaurant_staging")
    with pytest.raises(RuntimeError, match="before public fleet activity"):
        controller.begin_episode("late", warmup_ticks=1)


def test_begin_episode_is_one_shot_and_detects_clock_or_hold_mutation(tmp_path):
    from demo.benchmark import goal_restaurant_fleet as fleet

    controller = fleet.RestaurantFleetController(_session(), products_root=tmp_path / "one")
    controller.begin_episode("once", warmup_ticks=1)
    with pytest.raises(RuntimeError, match="exactly once"):
        controller.begin_episode("twice", warmup_ticks=1)

    jumped_session = _session()
    jumped = fleet.RestaurantFleetController(jumped_session, products_root=tmp_path / "jump")
    jumped_session.base_env.sim.current_time_step_index += 4
    with pytest.raises(RuntimeError, match="advanced outside fleet control"):
        jumped.begin_episode("jumped", warmup_ticks=1)

    changed_session = _session()
    changed = fleet.RestaurantFleetController(changed_session, products_root=tmp_path / "hold")
    original_step = changed_session.env.step

    def changing_step(commands):
        original_step(commands)
        changed._hold_targets["restaurant_m20_1"][0] += 1.0

    changed_session.env.step = changing_step
    with pytest.raises(RuntimeError, match="hold targets changed"):
        changed.begin_episode("changed", warmup_ticks=1)


def test_measured_route_rejects_free_goal_and_stale_start(tmp_path, monkeypatch):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    with pytest.raises(ValueError, match="unknown public region"):
        controller.plan_route("restaurant_go2_1", "free-goal", "participant_xyz")
    with pytest.raises(ValueError, match="no maintained native goal binding"):
        controller.plan_route("restaurant_go2_1", "standby", "restaurant_standby_a")
    current = controller.snapshot()["agents"]["restaurant_go2_1"]["position_m"]
    route = _route(controller, monkeypatch, "restaurant_go2_1",
                   "dining_service", "go2")
    assert route["start_xy_m"] == pytest.approx(current[:2])
    assert route["start_root_state_w"] == controller.snapshot()["agents"][
        "restaurant_go2_1"]["root_state_w"]
    session.base_env.scene.articulations["go2_1"].data.root_state_w[0, 0] += 0.1
    with pytest.raises(ValueError, match="current measured root"):
        controller.begin_navigation("go2-op", route, deadline_s=10.0)


def test_incremental_concurrent_commands_and_cancel_to_hold_next_tick(tmp_path, monkeypatch):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    m20 = controller.snapshot()["agents"]["restaurant_m20_1"]["position_m"]
    iris = controller.snapshot()["agents"]["restaurant_iris_2"]["position_m"]
    m20_route = _route(controller, monkeypatch, "restaurant_m20_1",
                       "dining_service", "m20")
    iris_route = _route(controller, monkeypatch, "restaurant_iris_2",
                        "restaurant_overview", "iris")
    controller.begin_navigation("m20-op", m20_route, deadline_s=20.0)
    controller.begin_navigation("iris-op", iris_route, deadline_s=20.0)
    first = controller.step_once()
    assert len(session.env.calls) == 1
    assert set(session.env.calls[0]) == set(session.possible_agents)
    assert first["operations"]["m20-op"]["control_steps"] == 1
    assert first["operations"]["iris-op"]["control_steps"] == 1
    assert session.env.calls[0]["m20_1"][0, 0] > m20[0]
    iris_command = session.env.calls[0]["iris_2"][0].tolist()
    assert math.dist(iris_command[:2], iris_route["goal_xyz_m"][:2]) < math.dist(
        iris[:2], iris_route["goal_xyz_m"][:2])

    before_cancel_steps = len(session.env.calls)
    cancelled = controller.cancel_navigation("m20-op")
    assert cancelled["status"] == "cancelled"
    assert len(session.env.calls) == before_cancel_steps
    second = controller.step_once()
    assert second["operations"]["m20-op"]["status"] == "cancelled"
    assert second["operations"]["iris-op"]["status"] == "active"
    held = second["agents"]["restaurant_m20_1"]["position_m"]
    assert session.env.calls[-1]["m20_1"][0].tolist() == pytest.approx(
        [held[0], held[1], 0.0])


def test_route_arrival_is_measured_but_never_promoted_to_validation(tmp_path, monkeypatch):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    from demo.restaurant_benchmark.route_admission import OVERVIEW_ANCHOR_XYZ_M
    near = [OVERVIEW_ANCHOR_XYZ_M[0] - 0.1,
            OVERVIEW_ANCHOR_XYZ_M[1], OVERVIEW_ANCHOR_XYZ_M[2]]
    session.base_env.scene.articulations["iris_1"].data.root_state_w[0] = torch.tensor(
        _root(*near))
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    route = _route(controller, monkeypatch, "restaurant_iris_1",
                   "restaurant_overview", "near")
    controller.begin_navigation("near-op", route, deadline_s=1.0)
    result = controller.step_once()["operations"]["near-op"]
    assert result["status"] == "arrived"
    assert result["terminal_reason"] == "measured_goal_tolerance"
    assert result["execution_validated"] is False
    assert result["business_outcomes_validated"] is False


@pytest.mark.parametrize("fault", ("reset", "forward", "dt", "nonfinite", "quaternion"))
def test_shared_clock_and_state_fail_closed(tmp_path, fault):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    controller.step_once()
    if fault == "reset":
        session.base_env.sim.current_time_step_index = 99
        with pytest.raises(RuntimeError, match="moved backwards"):
            controller.snapshot()
    elif fault == "forward":
        session.base_env.sim.current_time_step_index += 4
        with pytest.raises(RuntimeError, match="advanced outside fleet control"):
            controller.snapshot()
    elif fault == "dt":
        session.base_env.sim.physics_dt = 0.01
        with pytest.raises(RuntimeError, match="interval changed"):
            controller.snapshot()
    elif fault == "nonfinite":
        session.base_env.scene.articulations["carter_1"].data.root_state_w[0, 0] = float("nan")
        with pytest.raises(RuntimeError, match="missing or non-finite"):
            controller.snapshot()
    else:
        session.base_env.scene.articulations["carter_1"].data.root_state_w[0, 3] = 2.0
        with pytest.raises(RuntimeError, match="quaternion must be normalized"):
            controller.snapshot()


def test_constructor_rejects_probe_alias_or_partial_fleet(tmp_path):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    session.possible_agents[-1] = "iris_1"
    with pytest.raises(ValueError, match="ordered full8 bindings"):
        fleet.RestaurantFleetController(session, products_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (("native_agent_id", "m20_2"),
     ("goal_xyz_m", [999.0, 999.0, 0.0]),
     ("map_sha256", "0" * 64),
     ("route_cells", [[999, 999]]),
     ("waypoints_xy_m", [[999.0, 999.0]])),
)
def test_route_seal_rejects_identity_geometry_or_path_mutation(
        tmp_path, monkeypatch, field, replacement):
    from copy import deepcopy
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    route = _route(controller, monkeypatch, "restaurant_m20_1",
                   "dining_service", "sealed")
    changed = deepcopy(route)
    changed[field] = replacement
    with pytest.raises(ValueError, match="integrity check failed"):
        controller.begin_navigation("changed", changed, deadline_s=10.0)


@pytest.mark.parametrize("drift", ("task", "count", "budget", "spawns"))
def test_maintained_binding_rejects_public_full8_drift(monkeypatch, drift):
    from copy import deepcopy
    from demo.benchmark import goal_driven_tasks
    from demo.benchmark import goal_restaurant_fleet as fleet

    original = goal_driven_tasks.build_goal_task_package("restaurant")
    changed = deepcopy(original)
    task = changed["public_task"]
    if drift == "task":
        task["task_id"] = "RESTAURANT-GOAL-OTHER"
    elif drift == "count":
        task["candidate_robot_pool"].pop()
    elif drift == "budget":
        task["budgets"]["max_active_agents"] = 7
    else:
        task["public_world"]["robot_spawn_positions"]["restaurant_m20_2"][0] += 1.0
    monkeypatch.setattr(goal_driven_tasks, "build_goal_task_package",
                        lambda _scene: changed)
    with pytest.raises(RuntimeError, match="public full8 order drifted"):
        fleet._validate_maintained_bindings()


def test_tick_fails_if_registry_cannot_supply_all_eight_commands(tmp_path, monkeypatch):
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    # delitem undo reinserts at the end and silently changes this ordered
    # maintained registry for later tests in the same process.
    monkeypatch.setattr(fleet, "FLEET_CONTROLLER_REGISTRY", {
        key: value for key, value in fleet.FLEET_CONTROLLER_REGISTRY.items()
        if key != "restaurant_lite3_1"})
    with pytest.raises(RuntimeError, match="omitted a native controller"):
        controller.step_once()
    assert session.env.calls == []


def test_detour_waypoint_progress_does_not_false_stall_but_no_progress_does(
        tmp_path, monkeypatch):
    from copy import deepcopy
    from demo.benchmark import goal_restaurant_fleet as fleet

    session = _session()
    controller = fleet.RestaurantFleetController(session, products_root=tmp_path)
    route = _route(controller, monkeypatch, "restaurant_go2_1",
                   "dining_service", "detour")
    start = route["start_xy_m"]
    # First waypoint deliberately moves away from the final dining goal.
    route["waypoints_xy_m"] = [[start[0] - 1.0, start[1]], route["goal_xyz_m"][:2]]
    route["route_seal"] = controller._route_seal(route)
    controller.begin_navigation("detour-op", route, deadline_s=20.0)
    operation = controller._operations["detour-op"]
    limit = int(fleet._profile_thresholds("ground_velocity")["stall_window_steps"])
    operation["control_steps"] = limit
    operation["last_progress_step"] = 0
    operation["best_progress_distance_m"] = 1.0
    operation["progress_waypoint_index"] = 0
    state = deepcopy(controller.snapshot()["agents"]["restaurant_go2_1"])
    state["position_m"][0] -= 0.1
    state["root_state_w"][0] -= 0.1
    controller._post_step(operation, state)
    assert operation["status"] == "active"
    assert operation["last_progress_step"] == limit + 1
    assert operation["distance_to_goal_m"] > route["planned_length_m"] * 0

    # The same current-waypoint metric fails closed once neither distance nor
    # heading improves for a complete admitted stall window.
    stalled = deepcopy(operation)
    stalled["operation_id"] = "stalled-op"
    stalled["control_steps"] = limit
    stalled["last_progress_step"] = 0
    stalled["best_progress_distance_m"] = math.dist(
        state["position_m"][:2], route["waypoints_xy_m"][0])
    stalled["best_progress_heading_rad"] = 0.0
    controller._operations["stalled-op"] = stalled
    controller._active_by_agent[stalled["public_agent_id"]] = "stalled-op"
    controller._post_step(stalled, state)
    assert stalled["status"] == "failed"
    assert stalled["terminal_reason"] == "navigation_stalled"
