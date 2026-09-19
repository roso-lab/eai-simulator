"""Regression checks for measured three-dimensional hover completion."""

import math

import pytest

from demo.benchmark.control import advance_hover, advance_view_alignment, scan_hover_goal


def test_scan_target_preserves_floor_height():
    assert scan_hover_goal((2.0, 3.0, 1.57)) == pytest.approx((2.0, 3.0, 2.57))
    assert scan_hover_goal((2.0, 3.0, 0.0)) == (2.0, 3.0, 1.5)
    with pytest.raises(ValueError, match="three coordinates"):
        scan_hover_goal((2.0, 3.0))


def test_non_finite_physics_state_is_reported_without_invalid_json_numbers():
    from demo.benchmark.runtime import finite_root_state_report, json_safe_finite_values

    report = finite_root_state_report({
        "articulation:carter_1": [1.0, float("nan"), float("inf"), float("-inf")],
        "rigid_object:cargo": [0.0, 1.0, 2.0],
    })
    assert report == {
        "validated": False,
        "invalid_entities": [{
            "entity": "articulation:carter_1",
            "non_finite_components": [
                {"component": "position_y", "value": "nan"},
                {"component": "position_z", "value": "inf"},
                {"component": "quaternion_w", "value": "-inf"},
            ],
        }],
    }
    assert json_safe_finite_values([1.234, float("nan"), float("inf")]) == [1.23, None, None]


def test_final_ownership_is_explicit_for_every_present_declared_object():
    from demo.benchmark.runtime import explicit_final_ownership

    spec = {"composition": {"entities": [{"id": "crate"}, {"id": "chair"}]}}
    result = explicit_final_ownership(
        spec, {"crate": ["m20_1", "carter_1"]},
        runtime_object_ids={"crate", "chair"})
    assert result == {"crate": ["m20_1", "carter_1"], "chair": None}
    assert set(result) == {"crate", "chair"}


def test_missing_runtime_object_cannot_be_serialized_as_unowned_null():
    from demo.benchmark.runtime import explicit_final_ownership

    spec = {"composition": {"entities": [{"id": "crate"}, {"id": "chair"}]}}
    with pytest.raises(RuntimeError, match="missing runtime objects chair"):
        explicit_final_ownership(spec, {}, runtime_object_ids={"crate"})


def test_final_ownership_scope_excludes_static_landmarks_but_keeps_deliveries_explicit():
    from demo.benchmark.runtime import explicit_final_ownership

    spec = {"composition": {"entities": [
        {"id": "crate"}, {"id": "static_landmark"},
    ]}}
    result = explicit_final_ownership(
        spec, {}, runtime_object_ids={"crate"}, ownership_object_ids={"crate"})
    assert result == {"crate": None}


@pytest.mark.parametrize("carrying", [
    {"undeclared": "m20_1"}, {"crate": None}, {"crate": []}, {"crate": ["m20_1", "m20_1"]},
])
def test_final_ownership_rejects_ambiguous_active_owner_state(carrying):
    from demo.benchmark.runtime import explicit_final_ownership

    spec = {"composition": {"entities": [{"id": "crate"}]}}
    with pytest.raises(ValueError):
        explicit_final_ownership(spec, carrying, runtime_object_ids={"crate"})


def test_view_alignment_requires_continuous_dwell_and_bounded_yaw_command():
    entry = {"wait_yaw_rad": math.pi}
    aligned, command = advance_view_alignment(entry, 0.0, 0.1)
    assert not aligned and abs(command) == 1
    for _ in range(5):
        assert not advance_view_alignment(entry, -math.pi + 0.01, 0.1)[0]
    assert advance_view_alignment(entry, -math.pi + 0.01, 0.1)[0]
    assert not advance_view_alignment(entry, 0.0, 0.1)[0]
    assert entry["alignment_elapsed_s"] == 0


def test_slow_final_approach_is_progress_but_stationary_pose_stalls():
    from demo.benchmark.control import update_waypoint_progress
    from demo.benchmark.runtime import stall_action

    state = {"step": 0}
    for step in range(201):
        distance = 0.55 * math.exp(-0.55 * step * 0.02)
        update_waypoint_progress(state, waypoint=(1, 0), distance_m=distance,
                                 heading_error_rad=0.0, step=step)
        assert step - state["step"] < 100
    last_progress = state["step"]
    for step in range(201, 401):
        update_waypoint_progress(state, waypoint=(1, 0), distance_m=distance,
                                 heading_error_rad=0.0, step=step)
    assert state["step"] == last_progress
    assert stall_action(step=400, last_progress_step=state["step"], replans=0,
                        stall_window=150, max_replans=12) == "replan"


def test_turning_progress_is_bounded_and_oscillation_is_not_new_progress():
    from demo.benchmark.control import update_waypoint_progress

    state = {"step": 0}
    for step in range(160):
        update_waypoint_progress(state, waypoint=(1, 0), distance_m=1.,
                                 heading_error_rad=max(0., 3.14 - step * .02), step=step)
        assert step - state["step"] < 100
    for step in range(160, 360):
        update_waypoint_progress(state, waypoint=(1, 0), distance_m=1. + (step % 2) * .1,
                                 heading_error_rad=.1 + (step % 2) * .1, step=step)
    assert 360 - state["step"] > 150


def test_intermediate_waypoint_tolerance_stays_within_half_a_grid_cell():
    from demo.benchmark.runtime import intermediate_waypoint_tolerance

    assert intermediate_waypoint_tolerance(0.55, 0.05) == pytest.approx(0.025)
    assert intermediate_waypoint_tolerance(0.01, 0.05) == pytest.approx(0.01)
    for configured, resolution in ((0.0, 0.05), (0.5, math.nan)):
        with pytest.raises(ValueError, match="finite and positive"):
            intermediate_waypoint_tolerance(configured, resolution)


def test_kinematic_aerial_step_is_speed_bounded_and_does_not_overshoot():
    from demo.benchmark.runtime import kinematic_aerial_speed_report, kinematic_aerial_step

    assert kinematic_aerial_step(
        (0, 0, 0), (3, 4, 0), 0.5, speed_m_s=2.0
    ) == pytest.approx((0.6, 0.8, 0.0))
    assert kinematic_aerial_step((0, 0, 0), (0.1, 0.0, 0.0), 1.0) == (0.1, 0.0, 0.0)
    assert kinematic_aerial_speed_report((0, 0, 0), (0.012, 0, 0), 0.02)["validated"]
    exceeded = kinematic_aerial_speed_report((0, 0, 0), (0.013, 0, 0), 0.02)
    assert exceeded["validated"] is False
    assert exceeded["maximum_distance_m"] == pytest.approx(0.012)
    with pytest.raises(ValueError, match="finite XYZ"):
        kinematic_aerial_step((0, 0, 0), (math.nan, 0, 0), 1.0)


def test_route_waypoints_preserve_turns_and_exact_goal():
    from demo.benchmark.runtime import route_waypoints

    class Occupancy:
        @staticmethod
        def world(x, y):
            return x, y

    class Grid:
        resolution = 1.0
        _occupancy = Occupancy()

        @staticmethod
        def cell(x, y):
            return math.floor(x), math.floor(y)

        @staticmethod
        def passable(*cell):
            return True

    path = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (3, 3)]
    assert route_waypoints(Grid(), path, (3.25, 3.25), preserve_goal=True) == [
        (0.5, 0.5), (2.5, 0.5), (2.5, 2.5), (3.5, 3.5), (3.25, 3.25)]


def test_wait_heading_survives_compilation_and_rejects_unsupported_motion():
    from types import SimpleNamespace
    from EAI.scenario_spec.operators import _compile
    from EAI.scenario_spec.validation import validate_node_timing, validate_scenario
    from demo.benchmark.runtime import execution_contract
    from tools.scenario_generation.record_configuration_microtests import task_scope

    template = SimpleNamespace(scene_key="factory", zones=[SimpleNamespace(id="west", level=0)],
                               levels=[SimpleNamespace(floor_z=0.0)])
    spec = {"skeleton": "COV-01", "robots": [
        {"ref": "observer", "type": "simple_car", "zones": ["west"], "attachments": []}],
        "entities": [], "targets": [{"ref": "site", "zones": ["west"], "radius_m": 0.5}],
        "phase_nodes": [{"id": "view", "skill": "wait", "agent_ref": "observer", "duration_s": 2.,
                         "target_ref": "site", "object_ref": None, "prerequisites": [], "wait_yaw_rad": -2.7}]}
    compiled = _compile(spec, template, {"observer": SimpleNamespace(x=1., y=1.),
                                        "site": SimpleNamespace(x=2., y=2.)}, seed=1, task_name="wait_heading")
    data = validate_scenario(compiled)
    node = data["phase_graph"]["nodes"][0]
    assert node["wait_yaw_rad"] == -2.7
    assert task_scope(data, node)["wait_yaw_rad"] == -2.7
    execution_contract(data, transport_mode="none")
    data["robots"][0]["type"] = "mushr"
    with pytest.raises(ValueError, match="stationary-turn"):
        execution_contract(data, transport_mode="none")
    for heading in (True, float("nan"), 4.0):
        with pytest.raises(ValueError, match="finite"):
            validate_node_timing({**node, "wait_yaw_rad": heading})
    with pytest.raises(ValueError, match="only supported for wait"):
        validate_node_timing({**node, "skill_key": "navigate"})


@pytest.mark.parametrize("height", [0, -1, True, math.nan, math.inf, 101, "2"])
def test_scan_height_rejects_invalid_world_altitudes(height):
    with pytest.raises(ValueError, match="scan_height_m"):
        scan_hover_goal((2.0, 3.0, 0.0), scan_height_m=height)


def test_scan_height_is_absolute_and_above_target():
    assert scan_hover_goal((2.0, 3.0, 1.57), scan_height_m=2.3) == (2.0, 3.0, 2.3)
    with pytest.raises(ValueError, match="above the target"):
        scan_hover_goal((2.0, 3.0, 1.57), scan_height_m=1.5)


def test_scan_parameters_survive_task_spec_compilation():
    from types import SimpleNamespace
    from EAI.scenario_spec.operators import _compile
    from EAI.scenario_spec.validation import ScenarioError, validate_node_timing, validate_scenario

    template = SimpleNamespace(scene_key="factory", zones=[SimpleNamespace(id="west", level=0)],
                               levels=[SimpleNamespace(floor_z=0.0)])
    spec = {"skeleton": "SAR-01", "robots": [
        {"ref": "observer", "type": "iris", "zones": ["west"], "attachments": []}],
        "entities": [], "targets": [{"ref": "site", "zones": ["west"], "radius_m": 0.5}],
        "phase_nodes": [{"id": "view", "skill": "aerial_scan", "agent_ref": "observer",
                         "target_ref": "site", "object_ref": None, "prerequisites": [],
                         "scan_height_m": 2.3, "scan_yaw_rad": 3.0}]}
    validate_node_timing(spec["phase_nodes"][0], skill_field="skill")
    compiled = _compile(spec, template, {
        "observer": SimpleNamespace(x=1.0, y=1.0), "site": SimpleNamespace(x=2.0, y=2.0)},
        seed=1, task_name="scan_altitude")
    assert validate_scenario(compiled)["phase_graph"]["nodes"][0]["scan_height_m"] == 2.3
    assert compiled["phase_graph"]["nodes"][0]["scan_yaw_rad"] == 3.0
    spec["phase_nodes"][0]["skill"] = "navigate"
    with pytest.raises(ScenarioError, match="only supported for aerial_scan"):
        validate_node_timing(spec["phase_nodes"][0], skill_field="skill")


def test_hover_requires_measured_altitude_and_continuous_dwell():
    entry = {"scan_goal": (2.0, 3.0, 2.57)}
    for _ in range(50):
        assert not advance_hover(entry, (2.0, 3.0, 0.1), 0.1)
    for _ in range(5):
        assert not advance_hover(entry, (2.0, 3.0, 2.57), 0.1)
    assert not advance_hover(entry, (3.0, 3.0, 2.57), 0.1)
    assert entry["hover_elapsed_s"] == 0.0
    for _ in range(5):
        assert not advance_hover(entry, (2.0, 3.0, 2.57), 0.1)
    assert advance_hover(entry, (2.0, 3.0, 2.57), 0.1)
    assert not advance_hover(entry, (math.nan, 3.0, 2.57), 0.1)


def test_hover_heading_requires_continuous_measurements_and_wraps_at_pi():
    entry = {"scan_goal": (2.0, 3.0, 2.3), "scan_yaw_rad": math.pi}
    position = entry["scan_goal"]
    for _ in range(10):
        assert not advance_hover(entry, position, 0.1, measured_yaw_rad=0.0)
    assert entry["hover_elapsed_s"] == 0.0
    for _ in range(5):
        assert not advance_hover(entry, position, 0.1, measured_yaw_rad=-math.pi + 0.05)
    assert advance_hover(entry, position, 0.1, measured_yaw_rad=-math.pi + 0.05)
    assert entry["hover_yaw_error_rad"] == pytest.approx(0.05)
    assert not advance_hover(entry, position, 0.1)
    assert entry["hover_elapsed_s"] == 0.0


def test_plan_keeps_world_height_while_ground_router_receives_xy(monkeypatch):
    from demo.benchmark import runtime

    class Grid:
        resolution = 1.0

        def __init__(self, *args):
            self._occupancy = self

        def path(self, start, goal):
            assert len(start) == len(goal) == 2
            return [(2, 3)]

        def world(self, x, y):
            return (x, y)

    monkeypatch.setattr("EAI.scenario_spec.geometry.static_blocked_grid", lambda *args, **kwargs: None)
    monkeypatch.setattr("EAI.scenario_spec.oracle.InflatedGrid", Grid)
    data = {
        "robots": [{"id": "cf2x_1", "type": "cf2x", "attachments": [],
                    "spawn_pose": {"position": [0.0, 0.0, 1.2]}}],
        "composition": {"entities": [], "targets": [{"id": "rack", "position": [2.0, 3.0, 1.57]}]},
        "phase_graph": {"nodes": [{"id": "scan", "preferred_agent": "cf2x_1",
                                   "target_ref": "rack", "skill_key": "aerial_scan", "prerequisites": []}]},
    }
    plan = runtime.build_plan(data, object(), object())
    assert plan[0]["goal"] == (2.0, 3.0, 1.57)
    assert plan[0]["scan_goal"] == pytest.approx((2.0, 3.0, 2.57))
    data["phase_graph"]["nodes"][0]["scan_height_m"] = 2.3
    assert runtime.build_plan(data, object(), object())[0]["scan_goal"] == (2.0, 3.0, 2.3)


def test_navigation_target_tolerance_matches_business_target_radius(monkeypatch):
    from demo.benchmark import runtime

    class Grid:
        resolution = 1.0

        def __init__(self, *args):
            self._occupancy = self

        def path(self, start, goal):
            return [(2, 3)]

        def world(self, x, y):
            return (x, y)

        def cell(self, x, y):
            return (math.floor(x), math.floor(y))

        def passable(self, *cell):
            return True

    monkeypatch.setattr("EAI.scenario_spec.geometry.static_blocked_grid", lambda *args, **kwargs: None)
    monkeypatch.setattr("EAI.scenario_spec.oracle.InflatedGrid", Grid)
    data = {
        "robots": [{"id": "carter_1", "type": "carter", "attachments": [],
                    "spawn_pose": {"position": [0.0, 0.0, 0.2]}}],
        "composition": {"entities": [], "targets": [{"id": "dock",
                         "position": [2.0, 3.0, 0.0], "radius_m": 0.5}]},
        "phase_graph": {"nodes": [{"id": "approach", "preferred_agent": "carter_1",
                                   "target_ref": "dock", "skill_key": "navigate", "prerequisites": []}]},
    }
    plan = runtime.build_plan(data, object(), object())
    assert plan[0]["terminal_tolerance"] == pytest.approx(0.5)
    assert plan[0]["waypoints"][-1] == (2.0, 3.0)
    assert runtime.navigation_arrived(data, plan[0], (2.4, 3.0))
    assert not runtime.navigation_arrived(data, plan[0], (2.6, 3.0))


@pytest.mark.parametrize("position,expected", [
    ((0.0, 0.0), True), ((0.05, 0.0), True), ((0.051, 0.0), False),
    ((0.035, 0.035), True), ((0.05, 0.05), False), ((math.nan, 0.0), False),
])
def test_small_navigation_region_uses_exact_center_and_radius(position, expected):
    from demo.benchmark.runtime import navigation_arrived

    data = {"composition": {"targets": [{"id": "dock", "position": [0.0, 0.0, 0.0],
                                        "radius_m": 0.05}], "entities": []}}
    entry = {"agent": "carter_1", "terminal_tolerance": 0.75,
             "arrival_predicate": {"fn": "robot_visited_zone", "args": ["carter_1", "dock"]}}
    assert navigation_arrived(data, entry, position) == expected


def test_virtual_base_feedback_uses_moving_body_and_world_yaw():
    from types import SimpleNamespace
    import numpy as np
    from demo.benchmark.runtime import navigation_state, velocity_policy_command

    robot = SimpleNamespace(data=SimpleNamespace(
        root_state_w=np.array([[10.0, 20.0, 0.0, 1.0, 0.0, 0.0, 0.0]]),
        body_state_w=np.array([[[12.0, 23.0, 0.1, math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)]]]),
    ))
    assert navigation_state(robot) == (10.0, 20.0, 0.0, 0.0)
    assert navigation_state(robot, body_index=0) == pytest.approx((12.0, 23.0, 0.1, math.pi / 2))
    command = velocity_policy_command(distance=2.0, heading_error=-math.pi / 2,
                                      speed=0.5, k_turn=0.0)
    assert command == pytest.approx((0.0, -0.5, 0.0))
