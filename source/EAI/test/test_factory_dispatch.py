"""Dispatch interventions affect allowed information without forcing task failure."""

from copy import deepcopy
from pathlib import Path

import pytest

from EAI.scenario_spec import load_scenario
from demo.benchmark.provenance import canonical_sha256, file_sha256
from demo.benchmark.runtime import summarize_scheduled_trajectory
from demo.factory_benchmark.inspect_dispatch import evaluate_dispatch_arrival
from demo.factory_benchmark.inventory_dispatch import (
    DISPATCH_NODE, PUBLIC_RULES, decide_dispatch, dispatch_policy_decision, dispatch_trajectory,
    preflight_dispatch, retarget_entry, select_destination, validate_dispatch_task, view_messages,
)


SCENARIO_PATH = Path(__file__).resolve().parents[3] / "demo/factory_benchmark/scenarios/inspection_dispatch_01.json"


def _message(marker_id=23, published=4.0, expiry=120.0):
    return {"message_id": f"ground_view:marker:{marker_id}", "published_at_s": published,
            "valid_until_s": expiry, "estimate": {"marker_id": marker_id}}


def test_shared_withheld_and_delayed_use_only_available_messages():
    message = _message()
    shared = decide_dispatch([message], mode="shared", delay_s=60, time_s=5)
    assert shared["target_ref"] == "dock_site" and shared["used_message_ids"]
    for mode, status in (("withheld", "withheld"), ("delayed", "not_yet_available")):
        result = decide_dispatch([message], mode=mode, delay_s=60, time_s=5)
        assert result["target_ref"] == "assembly_site"
        assert not result["available_message_ids"] and not result["used_message_ids"]
        assert result["channel_ledger"][0]["status"] == status
    assert decide_dispatch([message], mode="delayed", delay_s=1, time_s=5)["target_ref"] == "dock_site"


def test_wait_policy_commits_on_first_available_identity_or_budget_expiry():
    message = _message(published=4.0)
    assert dispatch_policy_decision(
        [message], mode="delayed", delay_s=60, time_s=5, policy="wait_for_message",
        ready_at_s=5, max_wait_s=65) is None
    informed = dispatch_policy_decision(
        [message], mode="delayed", delay_s=60, time_s=64, policy="wait_for_message",
        ready_at_s=5, max_wait_s=65)
    assert informed["target_ref"] == "dock_site"
    assert informed["commit_reason"] == "information_available"
    assert informed["waited_s"] == 59
    timeout = dispatch_policy_decision(
        [message], mode="delayed", delay_s=100, time_s=70, policy="wait_for_message",
        ready_at_s=5, max_wait_s=65)
    assert timeout["target_ref"] == "assembly_site"
    assert timeout["commit_reason"] == "wait_budget_exhausted"
    withheld = dispatch_policy_decision(
        [message], mode="withheld", delay_s=60, time_s=5, policy="wait_for_message",
        ready_at_s=5, max_wait_s=65)
    assert withheld["commit_reason"] == "no_pending_information"
    with pytest.raises(ValueError, match="decision policy"):
        dispatch_policy_decision([], mode="shared", delay_s=0, time_s=5, policy="invented",
                                 ready_at_s=5, max_wait_s=0)


def test_expired_future_conflicting_and_unknown_messages_do_not_pick_a_true_id():
    for message, status in ((_message(expiry=4), "expired"), (_message(published=6), "not_yet_available")):
        result = decide_dispatch([message], mode="shared", delay_s=0, time_s=5)
        assert result["channel_ledger"][0]["status"] == status
        assert result["target_ref"] == "assembly_site"
    for messages in ([], [_message(3)], [_message(17), _message(23)]):
        result = select_destination(messages)
        assert not result["used_message_ids"]
        assert result["target_ref"] == "assembly_site"
    with pytest.raises(ValueError, match="duplicate"):
        decide_dispatch([_message(), _message()], mode="shared", delay_s=0, time_s=5)
    with pytest.raises(ValueError, match="finite"):
        decide_dispatch([], mode="shared", delay_s=float("nan"), time_s=5)


def test_message_publication_uses_all_view_frames_without_evaluator_truth():
    observations = [{"observation_id": f"ground_view:frame:{index}", "frame_id": "robot/camera",
                     "producer": "robot", "timestamp_s": float(index)} for index in range(3)]
    frames = [{"source_observation_id": observation["observation_id"], "detections": [
        {"pose_valid": True, "marker_id": 23, "estimated_position_m": position}]}
        for observation, position in zip(observations, [[1, 2, 3], [50, 60, 70], [1.1, 2.1, 3.1]])]
    message, = view_messages(frames, observations, node="ground_view", published_at_s=3)
    assert message["estimate"]["estimated_position_m"] == [1.1, 2.1, 3.1]
    assert message["estimate"]["sample_count"] == 3
    assert message["published_at_s"] == 3 and message["valid_until_s"] == 120
    with pytest.raises(ValueError, match="precede"):
        view_messages(frames, observations, node="ground_view", published_at_s=1)


def test_public_stations_and_both_routes_are_admitted_before_isaac():
    import math
    from EAI.scenario_spec.geometry import ground_surface_exclusions, provider_map
    from demo.benchmark.runtime import PATH_TRACKING_RESERVE_M, planning_template

    scenario = load_scenario(SCENARIO_PATH)
    validate_dispatch_task(scenario)
    occupancy = provider_map(scene_key="factory")
    plans = preflight_dispatch(scenario, occupancy, planning_template("factory", occupancy))
    assert set(plans) == {"assembly_site", "dock_site"}
    for route in plans.values():
        minimum_clearance = min(
            math.hypot(
                max(region["aabb_xy_m"][0] - point[0], 0,
                    point[0] - region["aabb_xy_m"][2]),
                max(region["aabb_xy_m"][1] - point[1], 0,
                    point[1] - region["aabb_xy_m"][3]),
            ) - route["agent_radius"]
            for point in route["waypoints"]
            for region in ground_surface_exclusions(scenario)
        )
        assert minimum_clearance >= PATH_TRACKING_RESERVE_M
    entry = plans["assembly_site"]
    retarget_entry(entry, scenario, "dock_site")
    assert entry["goal"] == (-1.95, 3.4, 0.0)
    assert entry["arrival_predicate"]["args"] == [entry["agent"], "dock_site"]
    assert next(node for node in scenario["phase_graph"]["nodes"] if node["id"] == DISPATCH_NODE)["target_ref"] == "assembly_site"
    scenario["composition"]["targets"][-1]["position"][0] += 0.1
    with pytest.raises(ValueError, match="public Factory station"):
        validate_dispatch_task(scenario)


def _arrival_report(target_ref="assembly_site"):
    scenario = load_scenario(SCENARIO_PATH)
    initial = {robot["id"]: robot["spawn_pose"]["position"][:2] for robot in scenario["robots"]}
    agent = "carter_1"
    destination = PUBLIC_RULES["destinations"][target_ref]["position"][:2]
    samples = [{"step": 0, "positions": initial}, {"step": 10, "positions": deepcopy(initial)},
               {"step": 20, "positions": {**initial, agent: destination}}]
    decision = {"step": 10, "time_s": 1.0, "target_ref": target_ref, "agent": agent,
                "start_position_m": [*initial[agent], 0.2], "used_message_ids": []}
    effective, trajectory = dispatch_trajectory(scenario, samples, decision)
    scheduling = {"states": {DISPATCH_NODE: {"status": "succeeded", "finished_at_s": 2.0}}}
    report = {"steps_taken": 20, "control_dt_s": 0.1, "scheduling": scheduling,
              "pose_trajectory": {"frame": "world", "position_unit": "m", "samples": samples},
              "events": [{"event": "node_complete", "node": DISPATCH_NODE, "step": 20,
                          "agent": agent, "position": destination, "confirmed_after_sim_step": True,
                          "arrival_predicate": {"fn": "robot_visited_zone", "args": [agent, target_ref]}}],
              "evaluation": summarize_scheduled_trajectory(effective, trajectory, scheduling),
              "inventory_dispatch": {"resolved_scenario_sha256": canonical_sha256(effective)}}
    return scenario, decision, report


def test_guessing_correctly_is_business_success_without_receiving_messages():
    scenario, decision, report = _arrival_report()
    result = evaluate_dispatch_arrival(report, scenario=scenario, decision=decision, expected_marker_id=17)
    assert result["dispatch_success"] and result["success_by_fallback"]
    result = evaluate_dispatch_arrival(report, scenario=scenario, decision=decision, expected_marker_id=23)
    assert result["selected_route_completed"] and not result["dispatch_success"]


def test_dynamic_route_replaces_the_original_predicate_without_mutating_task():
    scenario, decision, report = _arrival_report("dock_site")
    assert evaluate_dispatch_arrival(report, scenario=scenario, decision=decision, expected_marker_id=23)["dispatch_success"]
    stale = deepcopy(report)
    stale["events"][0]["arrival_predicate"]["args"][1] = "assembly_site"
    with pytest.raises(ValueError, match="measured visit"):
        evaluate_dispatch_arrival(stale, scenario=scenario, decision=decision, expected_marker_id=23)
    stale = deepcopy(report)
    stale["evaluation"]["verdict"] = "invented"
    with pytest.raises(ValueError, match="resolved route"):
        evaluate_dispatch_arrival(stale, scenario=scenario, decision=decision, expected_marker_id=23)


def test_predecision_visit_cannot_satisfy_dispatch_arrival():
    scenario, decision, report = _arrival_report("dock_site")
    samples = report["pose_trajectory"]["samples"]
    samples[0]["positions"] = {**samples[0]["positions"], "carter_1": [-1.95, 3.4]}
    _, trajectory = dispatch_trajectory(scenario, samples, decision)
    dispatch_predicates = [value for key, value in trajectory[0]["satisfied"].items()
                           if "dock_site" in key]
    assert dispatch_predicates == [False]
    report["events"][0]["step"] = 0
    with pytest.raises(ValueError):
        evaluate_dispatch_arrival(report, scenario=scenario, decision=decision, expected_marker_id=23)


def test_runtime_exception_is_saved_before_simulator_cleanup(tmp_path, capsys):
    import json
    from demo.benchmark.runtime import report_execution_exception

    report_path = tmp_path / "report.json"
    with pytest.raises(ValueError, match="future frame"):
        with report_execution_exception(report_path):
            raise ValueError("future frame")
    failure = json.loads(report_path.with_suffix(".failure.json").read_text())
    assert failure["exception_type"] == "ValueError"
    assert not failure["execution_validation"]["validated"]
    assert not report_path.exists()
    assert "future frame" in capsys.readouterr().err


def test_ground_routes_detour_around_stairs_without_rewriting_the_provider_map():
    import numpy as np
    from EAI.scenario_spec.geometry import ground_surface_exclusions, provider_map, validate_ground_surface_access
    from demo.benchmark.runtime import build_plan, planning_template

    scenario = load_scenario(SCENARIO_PATH)
    occupancy = provider_map(scene_key="factory")
    original_free = occupancy.free.copy()
    template = planning_template("factory", occupancy)
    old = deepcopy(scenario)
    old.pop("benchmark_task_id")
    original_route = next(entry for entry in build_plan(old, occupancy, template) if entry["node"] == DISPATCH_NODE)
    main_stair = next(item["aabb_xy_m"] for item in ground_surface_exclusions(scenario) if item["id"] == "main_stair")

    def inside(point):
        return main_stair[0] <= point[0] <= main_stair[2] and main_stair[1] <= point[1] <= main_stair[3]

    assert any(inside(point) for point in original_route["waypoints"])
    routes = preflight_dispatch(scenario, occupancy, template)
    assert all(not inside(point) for route in routes.values() for point in route["waypoints"])
    assert np.array_equal(occupancy.free, original_free)
    scenario["robots"][-1]["spawn_pose"]["position"] = [-0.4, -2.8, 0.2]
    with pytest.raises(ValueError, match="unverified main_stair"):
        validate_ground_surface_access(scenario)


def test_route_preflight_does_not_import_usd_before_simulator_startup():
    import os
    import subprocess
    import sys

    code = """
import sys
from EAI.scenario_spec import load_scenario
from EAI.scenario_spec.geometry import provider_map
from demo.benchmark.runtime import planning_template
from demo.factory_benchmark.inventory_dispatch import preflight_dispatch
scenario = load_scenario(sys.argv[1])
occupancy = provider_map(scene_key='factory')
preflight_dispatch(scenario, occupancy, planning_template('factory', occupancy))
assert not any(name == 'pxr' or name.startswith('pxr.') for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, "-c", code, str(SCENARIO_PATH)],
                               env=os.environ.copy(), capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr


def _participant_trace(actor, *, marker_id=23, target_ref="dock_site"):
    from demo.benchmark.participant_runtime import ParticipantSession
    from demo.factory_benchmark.challenge import compile_challenge

    bundle = compile_challenge(SCENARIO_PATH)
    session = ParticipantSession(
        adapter=actor, api=bundle["participant_api"], task_spec=bundle["public_task"])
    session.emit("phase_ready", time_s=5.0, payload={
        "node_id": DISPATCH_NODE, "eligible_agent_ids": ["carter_1"],
    })
    message = _message(marker_id=marker_id, published=4.0)
    session.emit("observation_message", time_s=5.0, payload={
        "message_id": message["message_id"], "producer_id": "simple_car_1",
        "published_at_s": message["published_at_s"], "valid_until_s": message["valid_until_s"],
        "estimate": message["estimate"],
    })
    session.emit("tick", time_s=5.0, payload={
        "robot_states": {}, "phase_states": {}, "resource_states": {},
    })
    actions = session.request_actions()
    navigate = next(action for action in actions if action["type"] == "navigate_goal")
    decision = {
        "actor": session.adapter_manifest, "committed_action_id": navigate["action_id"],
        "agent": "carter_1", "node": DISPATCH_NODE, "target_ref": target_ref,
        "time_s": 5.0, "used_message_ids": [message["message_id"]]
        if any(action["type"] == "consume_message" for action in actions) else [],
    }
    report = {
        "execution_provenance": {"request": {"scenario_sha256": file_sha256(SCENARIO_PATH)}},
        "inventory_dispatch": {"participant": session.report()},
    }
    options = {"inventory_dispatch_mode": "shared", "inventory_message_delay_s": 0.0}
    state = {"started_at_s": 5.0}
    source = {**message, "producer": "simple_car_1"}
    return report, [source], decision, options, state


def test_v3_baseline_trace_replays_only_participant_visible_events():
    from demo.factory_benchmark.baseline_solver import BaselineDispatchAdapter
    from demo.factory_benchmark.inspect_dispatch import _evaluate_participant_trace

    values = _participant_trace(BaselineDispatchAdapter(policy="immediate", max_wait_s=65.0))
    result = _evaluate_participant_trace(
        values[0], scenario=load_scenario(SCENARIO_PATH), messages=values[1],
        decision=values[2], options=values[3], state=values[4])
    assert result["participant_action_trace_validated"]
    assert result["baseline_replay_required"]
    assert result["decision_replay_validated"]


def test_v3_participant_trace_is_bound_but_strategy_is_not_replayed():
    from demo.factory_benchmark.inspect_dispatch import _evaluate_participant_trace

    class Participant:
        def manifest(self):
            return {"kind": "participant", "id": "third-party", "version": 1}

        def act(self, events):
            time_s = events[-1]["time_s"]
            return [{
                "schema": "eai.benchmark.participant_action", "version": 1,
                "action_id": "consume", "submitted_at_s": time_s,
                "type": "consume_message", "payload": {
                    "message_id": "ground_view:marker:23", "consumer_id": DISPATCH_NODE,
                },
            }, {
                "schema": "eai.benchmark.participant_action", "version": 1,
                "action_id": "navigate", "submitted_at_s": time_s,
                "type": "navigate_goal", "payload": {
                    "node_id": DISPATCH_NODE, "agent_id": "carter_1", "target_ref": "dock_site",
                },
            }]

    values = _participant_trace(Participant())
    result = _evaluate_participant_trace(
        values[0], scenario=load_scenario(SCENARIO_PATH), messages=values[1],
        decision=values[2], options=values[3], state=values[4])
    assert result["participant_action_trace_validated"]
    assert not result["baseline_replay_required"]
    assert result["decision_replay_validated"] is None
    wrong_contract = deepcopy(values[0])
    wrong_contract["inventory_dispatch"]["participant"]["public_task_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="not bound"):
        _evaluate_participant_trace(
            wrong_contract, scenario=load_scenario(SCENARIO_PATH), messages=values[1],
            decision=values[2], options=values[3], state=values[4])
    tampered = deepcopy(values[0])
    message_event = next(event for event in tampered["inventory_dispatch"]["participant"]["events"]
                         if event["type"] == "observation_message")
    message_event["time_s"] = 3.0
    with pytest.raises(ValueError, match="monotonic|before delivery"):
        _evaluate_participant_trace(
            tampered, scenario=load_scenario(SCENARIO_PATH), messages=values[1],
            decision=values[2], options=values[3], state=values[4])
