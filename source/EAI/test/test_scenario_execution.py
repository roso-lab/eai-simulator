"""Offline contract checks for the scenario execution harness.

These tests deliberately stop before Isaac.  They protect the boundary between
the deterministic rollout harness and claims that require physical validation.
"""

import pytest

from tools.scenario_generation.execute_episode import (
    auto_step_budget,
    bind_evaluation_agents,
    execution_contract,
    joint_event_validation,
    planning_radius,
    reconcile_episode_end,
    within_interaction_standoff,
    relay_event_validation,
    stall_action,
    steering_command,
    velocity_policy_command,
)


def _node(identifier, skill, agent="carter_1"):
    return {
        "id": identifier,
        "skill_key": skill,
        "preferred_agent": agent,
        "target_ref": "target",
        "object_ref": "cargo" if skill == "transport_object" else None,
        "prerequisites": [],
    }


def _data(*nodes):
    return {"phase_graph": {"nodes": list(nodes)}}


def test_aerial_scan_contract_declares_measured_hover_scope() -> None:
    data = _data(_node("scan", "aerial_scan", agent="cf2x_1"))
    contract = execution_contract(data, transport_mode="none")
    assert contract["tier"] == "navigation_execution"
    assert contract["aerial_scan_model"] == "goal_navigation_measured_hover_and_camera_frame"
    assert contract["skill_support"]["aerial_scan"] == "position_dwell_with_live_camera_frame_evidence"
    assert contract["physical_grasp"] is False
    assert "aerial perception" in contract["does_not_establish"][-1]


@pytest.mark.parametrize("robot_type,controller,accepted", [
    ("cf2x", "QUADCOPTER_GOAL_SKRL_CFG", False),
    ("iris", "PEGASUS_IRIS_POSITION_CFG", True),
    ("pegasus", "PEGASUS_X4_POSITION_CFG", True),
    ("iris", "PEGASUS_IRIS_ROTOR_CFG", False),
    ("cf2x", "PEGASUS_IRIS_POSITION_CFG", False),
])
def test_scan_heading_requires_actual_yaw_control_pairing(robot_type, controller, accepted):
    node = dict(_node("scan", "aerial_scan", agent=f"{robot_type}_1"), scan_yaw_rad=3.0)
    data = dict(_data(node), robots=[{"id": node["preferred_agent"], "type": robot_type,
                                    "controller": {"cfg": controller}}])
    if accepted:
        assert execution_contract(data, transport_mode="none")["tier"] == "navigation_execution"
    else:
        with pytest.raises(ValueError, match="paired EAI goal_yaw controller"):
            execution_contract(data, transport_mode="none")


@pytest.mark.parametrize("yaw", [True, float("nan"), float("inf"), 4.0, "north"])
def test_scan_heading_rejects_invalid_requests(yaw):
    node = dict(_node("scan", "aerial_scan", agent="iris_1"), scan_yaw_rad=yaw)
    with pytest.raises(ValueError, match="scan_yaw_rad must be finite"):
        execution_contract(_data(node), transport_mode="none")


def test_joint_transport_contract_requires_declared_two_robot_team() -> None:
    node = _node("joint", "joint_transport", agent="m20_1")
    node["agents"] = ["m20_1", "carter_1"]
    contract = execution_contract(_data(node), transport_mode="kinematic")
    assert contract["joint_transport_model"] == "idealized_shared_ownership"
    assert contract["transport_mode"] == "kinematic"
    bad = _node("joint", "joint_transport", agent="m20_1")
    bad["agents"] = ["m20_1"]
    with pytest.raises(ValueError, match="exactly two"):
        execution_contract(_data(bad), transport_mode="kinematic")


def test_joint_event_validation_requires_both_owners() -> None:
    plan = [{"node": "joint", "skill": "joint_transport",
             "team": ["m20_1", "carter_1"]}]
    events = [
        {"event": "pickup", "node": "joint", "owner_before": None,
         "owner_after": ["m20_1", "carter_1"], "agents": ["m20_1", "carter_1"]},
        {"event": "release", "node": "joint", "owner_before": ["m20_1", "carter_1"],
         "owner_after": None},
    ]
    result = joint_event_validation(plan, events)
    assert result["validated"] is True
    events[0]["owner_after"] = ["m20_1"]
    assert joint_event_validation(plan, events)["validated"] is False


def test_joint_event_validation_rejects_measured_formation_break() -> None:
    plan = [{"node": "joint", "skill": "joint_transport",
             "team": ["m20_1", "carter_1"], "team_max_separation_m": 3.0}]
    events = [
        {"event": "pickup", "node": "joint", "step": 10, "owner_before": None,
         "owner_after": ["m20_1", "carter_1"], "agents": ["m20_1", "carter_1"]},
        {"event": "release", "node": "joint", "step": 20,
         "owner_before": ["m20_1", "carter_1"], "owner_after": None},
    ]
    trajectory = [
        {"step": 10, "positions": {"m20_1": (0.0, 0.0), "carter_1": (2.0, 0.0)}},
        {"step": 15, "positions": {"m20_1": (0.0, 0.0), "carter_1": (4.0, 0.0)}},
        {"step": 20, "positions": {"m20_1": (2.0, 0.0), "carter_1": (4.0, 0.0)}},
    ]
    result = joint_event_validation(plan, events, trajectory=trajectory)
    assert result["validated"] is False
    assert result["formation"]["joint"]["violations"][0]["step"] == 15


def test_navigation_execution_contract_is_explicit_about_its_scope() -> None:
    contract = execution_contract(_data(_node("navigate", "navigate")), transport_mode="none")
    assert contract["tier"] == "navigation_execution"
    assert contract["transport_mode"] == "none"
    assert contract["physical_grasp"] is False
    assert contract["contact_events_are_allowed"] is True


def test_transport_requires_explicit_kinematic_opt_in() -> None:
    data = _data(_node("carry", "transport_object"))
    with pytest.raises(ValueError, match="--transport-mode kinematic"):
        execution_contract(data, transport_mode="none")
    contract = execution_contract(data, transport_mode="kinematic")
    assert contract["tier"] == "kinematic_execution"
    assert contract["arm_ik"] is False
    assert contract["mass_affects_execution"] is False
    assert contract["manipulation_model"] == "scripted_root_pose"


def test_executor_refuses_partial_or_unassigned_dags() -> None:
    with pytest.raises(ValueError, match="does not support phase skills"):
        execution_contract(_data(_node("handoff", "handoff_object")), transport_mode="none")
    with pytest.raises(ValueError, match="explicit preferred_agent"):
        execution_contract(_data(_node("navigate", "navigate", agent=None)), transport_mode="none")


def test_deadlock_watchdog_uses_waypoint_progress_not_contact_events() -> None:
    assert stall_action(step=150, last_progress_step=1, replans=0,
                        stall_window=150, max_replans=2) == "continue"
    assert stall_action(step=152, last_progress_step=1, replans=0,
                        stall_window=150, max_replans=2) == "replan"
    assert stall_action(step=152, last_progress_step=1, replans=2,
                        stall_window=150, max_replans=2) == "deadlock"


def test_auto_budget_reserves_every_bounded_recovery_window() -> None:
    budget = auto_step_budget(route_cells=406, stall_window=150, max_replans=12)
    assert budget == {
        "route_cells": 406,
        "route_cell_floor_steps": 4872,
        "ground_distance_m": 0.0,
        "ground_speed_m_s": 0.55,
        "ground_tracking_factor": 3.0,
        "ground_distance_steps": 0,
        "route_tracking_steps": 4872,
        "aerial_distance_m": 0.0,
        "aerial_motion_steps": 0,
        "aerial_hover_s": 0.0,
        "aerial_hover_steps": 0,
        "declared_hold_s": 0.0,
        "declared_hold_steps": 0,
        "control_dt_s": 0.02,
        "aerial_speed_m_s": 0.6,
        "deadlock_reserve_steps": 1950,
        "max_steps": 6822,
    }


def test_auto_budget_accounts_for_aerial_distance_hover_and_declared_holds() -> None:
    budget = auto_step_budget(
        route_cells=0, stall_window=150, max_replans=12,
        aerial_distance_m=20.0, aerial_hover_s=1.0,
        declared_hold_s=3.0, control_dt_s=0.02,
    )
    assert budget["aerial_motion_steps"] == 1667
    assert budget["aerial_hover_steps"] == 50
    assert budget["declared_hold_steps"] == 150
    assert budget["max_steps"] == 3817


def test_auto_budget_uses_metric_ground_distance_over_compressed_waypoint_count() -> None:
    budget = auto_step_budget(
        route_cells=2, stall_window=150, max_replans=12,
        ground_distance_m=11.0, ground_speed_m_s=0.55,
        ground_tracking_factor=3.0, control_dt_s=0.02,
    )
    assert budget["route_cell_floor_steps"] == 24
    assert budget["ground_distance_steps"] == 3000
    assert budget["route_tracking_steps"] == 3000
    assert budget["max_steps"] == 4950


def test_tracker_turns_in_place_before_driving() -> None:
    v, wz = steering_command(distance=2.0, heading_error=1.0, speed=0.55, k_turn=1.5)
    assert v == 0.0
    assert wz == 1.2
    v, _ = steering_command(distance=0.25, heading_error=0.0, speed=0.55, k_turn=1.5)
    assert 0.0 < v < 0.55


def test_velocity_policy_tracker_uses_body_frame_lateral_command() -> None:
    vx, vy, wz = velocity_policy_command(
        distance=2.0, heading_error=0.5, speed=0.55, k_turn=1.5)
    assert vx > 0.0
    assert vy > 0.0
    assert wz == pytest.approx(0.75)


def test_velocity_policy_tracker_can_raise_the_near_waypoint_floor() -> None:
    default = velocity_policy_command(
        distance=0.25, heading_error=0.0, speed=0.6, k_turn=1.5)
    calibrated = velocity_policy_command(
        distance=0.25, heading_error=0.0, speed=0.6, k_turn=1.5,
        minimum_distance_scale=0.75)
    assert default[0] == pytest.approx(0.15)
    assert calibrated[0] == pytest.approx(0.45)
    with pytest.raises(ValueError, match="minimum distance scale"):
        velocity_policy_command(
            distance=0.25, heading_error=0.0, speed=0.6, k_turn=1.5,
            minimum_distance_scale=0.0)


def test_transport_approach_finishes_at_the_declared_interaction_standoff() -> None:
    assert within_interaction_standoff((0.0, 0.0), (1.0, 0.0), 1.0)
    assert not within_interaction_standoff((0.0, 0.0), (1.01, 0.0), 1.0)
    with pytest.raises(ValueError, match="interaction range"):
        within_interaction_standoff((0.0, 0.0), (1.0, 0.0), 0.0)


def test_episode_end_includes_the_last_completed_physics_step() -> None:
    report = reconcile_episode_end({"time_s": 1.98}, steps_taken=100, control_dt_s=0.02)
    assert report["time_s"] == pytest.approx(2.0)
    report = reconcile_episode_end({"time_s": 2.1}, steps_taken=100, control_dt_s=0.02)
    assert report["time_s"] == pytest.approx(2.1)


def test_effective_evaluation_follows_participant_navigation_assignment() -> None:
    scenario = {
        "robots": [
            {"id": "carter_1"},
            {"id": "m20_1"},
        ],
        "composition": {
            "entities": [],
            "targets": [{"id": "review", "position": [2.0, 3.0, 0.0], "radius_m": 0.5}],
        },
        "phase_graph": {"nodes": [{
            "id": "review_access", "skill_key": "navigate",
            "preferred_agent": "carter_1", "target_ref": "review",
            "prerequisites": [],
        }]},
    }
    effective = bind_evaluation_agents(scenario, [{
        "node": "review_access", "skill": "navigate", "agent": "m20_1",
    }])

    assert scenario["phase_graph"]["nodes"][0]["preferred_agent"] == "carter_1"
    assert effective["phase_graph"]["nodes"][0]["preferred_agent"] == "m20_1"


def test_planning_radius_accounts_for_mounted_arm_footprint() -> None:
    base = {"type": "scout", "attachments": []}
    mounted = {"type": "scout", "attachments": [{"type": "ur5"}]}
    assert planning_radius(base) == pytest.approx(0.65)
    assert planning_radius(mounted) == pytest.approx(0.9)


def _relay_plan():
    return [
        {"node": "carry_a", "skill": "transport_object", "agent": "a",
         "object": "cargo", "prerequisites": [], "agent_radius": 0.9},
        {"node": "clear_a", "skill": "navigate", "agent": "a",
         "object": None, "prerequisites": ["carry_a"], "agent_radius": 0.9},
        {"node": "carry_b", "skill": "transport_object", "agent": "b",
         "object": "cargo", "prerequisites": ["clear_a", "stage_b"],
         "agent_radius": 0.9},
    ]


def _relay_events():
    return [
        {"event": "pickup", "node": "carry_a", "agent": "a", "object": "cargo",
         "owner_before": None, "owner_after": "a"},
        {"event": "release", "node": "carry_a", "agent": "a", "object": "cargo",
         "position": [0.0, 0.0], "owner_before": "a", "owner_after": None},
        {"event": "node_complete", "node": "clear_a", "agent": "a"},
        {"event": "pickup", "node": "carry_b", "agent": "b", "object": "cargo",
         "object_position": [0.0, 0.0], "owner_before": None, "owner_after": "b",
         "team_positions": {"a": [-2.0, 0.0], "b": [0.0, 0.0]}},
        {"event": "release", "node": "carry_b", "agent": "b", "object": "cargo",
         "position": [4.0, 0.0], "owner_before": "b", "owner_after": None},
    ]


def test_relay_contract_requires_ordered_clearance_and_ownership() -> None:
    result = relay_event_validation(_relay_plan(), _relay_events())
    assert result["required"] is True
    assert result["participants"] == ["a", "b"]
    assert result["validated"] is True


def test_relay_contract_rejects_final_position_without_real_transfer() -> None:
    result = relay_event_validation(_relay_plan(), [])
    assert result["validated"] is False
    assert result["violations"]


def test_relay_contract_rejects_pickup_before_sender_is_clear() -> None:
    events = [event for event in _relay_events()
              if not (event["event"] == "node_complete" and event["node"] == "clear_a")]
    result = relay_event_validation(_relay_plan(), events)
    assert result["validated"] is False
    assert any("sender-clear" in item for item in result["violations"])


def test_relay_contract_rejects_unsafe_robot_separation() -> None:
    events = _relay_events()
    events[3]["team_positions"]["a"] = [-1.0, 0.0]
    result = relay_event_validation(_relay_plan(), events)
    assert result["validated"] is False
    assert any("clearance" in item for item in result["violations"])
