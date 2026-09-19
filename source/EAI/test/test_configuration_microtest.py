"""Tests for configuration-specific Factory admission records."""

import pytest
import json
from copy import deepcopy
from benchmark_evidence_support import configuration_attempt

from tools.scenario_generation.configuration_microtest import (
    aggregate_records, make_record, measurement_for_profile, validate_microtest,
)


def _record(*, condition="layout-a", protocol="factory_navigation_v1"):
    metrics = ({"route_length_m": 4.0, "sim_time_s": 8.0, "final_error_m": 0.1,
                "steps_taken": 400} if protocol == "factory_navigation_v1" else
               {"frame_count": 4, "valid_frame_count": 4, "max_age_s": 0.2})
    return make_record(
        configuration_id="simple_car@test-config", robot_key="simple_car",
        protocol_id=protocol, scenario_sha256="a" * 64, report_sha256=("b" if condition == "layout-a" else "c") * 64,
        condition={"layout": condition, "render_period_s": 0.2}, metrics=metrics,
        acceptance={"final_error_bound_m": 0.5}, source_refs=["report"])


def test_microtest_keeps_raw_metrics_and_configuration_identity():
    record = _record()
    assert validate_microtest(record)["metrics"]["route_length_m"] == 4.0
    with pytest.raises(ValueError, match="legacy"):
        measurement_for_profile(record)


def test_microtest_summary_refuses_mixed_conditions():
    first = _record()
    second = _record(condition="layout-b")
    with pytest.raises(ValueError, match="mixes"):
        aggregate_records([first, second])


def test_sensor_protocol_requires_valid_frames():
    record = _record(protocol="factory_sensor_capture_v1")
    assert validate_microtest(record)["metrics"]["frame_count"] == 4
    invalid = dict(record, metrics={"frame_count": 2, "valid_frame_count": 1, "max_age_s": 0.2})
    with pytest.raises(ValueError, match="valid frames"):
        validate_microtest(invalid)


def test_changed_condition_and_nonfinite_acceptance_are_rejected():
    record = deepcopy(_record())
    record["condition"]["render_period_s"] = 1.0
    with pytest.raises(ValueError, match="condition digest"):
        validate_microtest(record)
    record = _record()
    record["acceptance"]["final_error_bound_m"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        validate_microtest(record)


def test_duplicate_report_does_not_inflate_repeat_count():
    record = _record()
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_records([record, record])
    assert aggregate_records([record])["pass_rate"] is None


def test_navigation_check_uses_actual_arrival_and_keeps_radar_unknown(tmp_path):
    from tools.scenario_generation.record_configuration_microtests import derive_records
    from tools.scenario_generation.capability_profile import profile_for_dossier
    from tools.scenario_generation.robot_dossier import robot_dossier

    scenario, attempt = configuration_attempt(tmp_path)
    record, = derive_records(scenario, attempt)
    assert record["metrics"]["arrival_error_m"] == 0.0
    assert record["metrics"]["sampled_prefix_path_m"] == 1.0
    assert record["metrics"]["arrival_step"] == 50
    profile = profile_for_dossier(robot_dossier("simple_car", attachments=[]), [measurement_for_profile(record)])
    assert profile["protocol_check_count"] == 1
    assert profile["passing_evidence_tier"] == "E6"
    assert all(axis["normalized"] is None and axis["status"] == "unknown" for axis in profile["radar"])


def test_camera_check_retains_the_whole_sampling_window(tmp_path):
    from tools.scenario_generation.record_configuration_microtests import derive_records

    scenario, attempt = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    record, = derive_records(scenario, attempt)
    assert record["metrics"] == {"frame_count": 3, "window_duration_s": 1.0, "max_gap_s": 0.5,
                                 "max_capture_position_error_m": 0.0}
    check = measurement_for_profile(record)
    assert check["scope"]["duration_s"] == 1.0
    assert "axis" not in check


@pytest.mark.parametrize("violation", [None, "missing_alignment", "premature_finish", "wrong_frame_yaw", "trace_drift"])
def test_oriented_camera_window_excludes_alignment_and_rejects_heading_drift(tmp_path, violation):
    from demo.benchmark.camera_validation import capture_window_metrics
    from tools.scenario_generation.record_configuration_microtests import task_scope

    scenario_path, attempt_path = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    scenario = json.loads(scenario_path.read_text())
    node = scenario["phase_graph"]["nodes"][0]
    node["wait_yaw_rad"] = 0.0
    report = json.loads(attempt_path.read_text())["report_body"]
    state = report["scheduling"]["states"][node["id"]]
    state["finished_at_s"] += 1.0
    for event in report["events"]:
        event["step"] += 50
    report["events"].append({"event": "observation_window_started", "node": node["id"],
        "agent": node["preferred_agent"], "step": 51, "time_s": 1.0, "wait_yaw_rad": 0.0,
        "measured_yaw_rad": 0.0, "yaw_error_rad": 0.0, "alignment_duration_s": 0.5})
    for frame in report["observation_store"]["observations"]:
        frame["timestamp_s"] += 1.0
        frame["sensor_config"]["observer_yaw_rad"] = 0.0
    for sample in report["pose_trajectory"]["samples"]:
        sample["step"] += 50
        sample["robot_poses_xyzyaw"] = {node["preferred_agent"]: [1.0, 0.0, 0.34, 0.0]}
    if violation == "missing_alignment":
        report["events"].pop()
    elif violation == "premature_finish":
        state["finished_at_s"] = 1.5
    elif violation == "wrong_frame_yaw":
        report["observation_store"]["observations"][0]["sensor_config"]["observer_yaw_rad"] = 0.3
    elif violation == "trace_drift":
        report["pose_trajectory"]["samples"][0]["robot_poses_xyzyaw"][node["preferred_agent"]][3] = 0.3
    if violation:
        with pytest.raises(ValueError):
            capture_window_metrics(report, node, task_scope(scenario, node))
    else:
        metrics, acceptance = capture_window_metrics(report, node, task_scope(scenario, node))
        assert metrics["window_duration_s"] == 1.0
        assert acceptance["observer_yaw_bound_rad"] == 0.05


@pytest.mark.parametrize("violation,reason", [
    ("wrong_site", "outside its declared observation region"),
    ("missing_position", "recorded observer position"),
    ("nonfinite_position", "finite numbers"),
    ("wrong_node", "different observation node"),
    ("wrong_time", "capture event"),
    ("left_site", "left its region"),
])
def test_camera_check_rejects_frames_from_wrong_observation_position(tmp_path, violation, reason):
    from demo.benchmark.provenance import canonical_sha256
    from tools.scenario_generation.record_configuration_microtests import derive_records

    scenario, attempt_path = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    attempt = json.loads(attempt_path.read_text())
    report = attempt["report_body"]
    frame = report["observation_store"]["observations"][0]
    if violation == "wrong_site":
        frame["target_estimate"]["position_m"][0] = 3.0
    elif violation == "missing_position":
        del frame["target_estimate"]
    elif violation == "nonfinite_position":
        frame["target_estimate"]["position_m"][0] = "NaN"
    elif violation == "wrong_node":
        frame["sensor_config"]["node"] = "other_watch"
    elif violation == "wrong_time":
        frame["timestamp_s"] += 0.01
    else:
        report["pose_trajectory"]["samples"].insert(1, {"step": 25, "positions": {"simple_car_1": [3.0, 0.0]}})
    attempt["report_sha256"] = canonical_sha256(report)
    attempt_path.write_text(json.dumps(attempt))
    with pytest.raises(ValueError, match=reason):
        derive_records(scenario, attempt_path)


def test_aerial_camera_requires_measured_hover_at_the_target():
    from tools.scenario_generation.record_configuration_microtests import _camera_metrics

    node = {"id": "view", "preferred_agent": "cf2x_1", "skill_key": "aerial_scan"}
    scope = {"target": {"position": [1.0, 2.0, 0.0], "radius_m": 0.5}, "subject": {"id": "site"}}
    event = {"event": "aerial_scan", "node": "view", "agent": "cf2x_1", "target_ref": "site",
             "observation_ids": ["frame"], "observation_source": "live_isaac_rgb_frame", "step": 50,
             "hover_goal": [1.0, 2.0, 1.5], "measured_position": [1.0, 2.0, 1.6],
             "position_error_m": 0.1, "hover_duration_s": 0.5, "confirmed_after_sim_step": True}
    report = {"control_dt_s": 0.02, "scheduling": {"states": {"view": {"started_at_s": 0.0, "finished_at_s": 0.98}}},
              "events": [event], "observation_store": {"observations": [{
                  "observation_id": "frame", "producer": "cf2x_1", "sensor": "camera", "target_ref": "site",
                  "timestamp_s": 1.0, "sensor_config": {"frame_shape": [2, 2, 4], "frame_dtype": "uint8", "frame_sha256": "a" * 64},
                  "quality": {"frame_acquired": True, "valid_pixel_count": 16},
                  "target_estimate": {"kind": "sensor_pose_only", "position_m": [1.0, 2.0, 1.6]},
              }]}}
    metrics, acceptance = _camera_metrics(report, node, scope)
    assert metrics["window_duration_s"] == 0.5
    assert metrics["max_capture_position_error_m"] == pytest.approx(0.1)
    assert acceptance["observer_position_bound_m"] == 0.2
    node["scan_height_m"] = 2.3
    with pytest.raises(ValueError, match="outside its declared observation region"):
        _camera_metrics(report, node, scope)
    event["hover_goal"][2] = 2.3
    event["measured_position"][2] = 2.4
    report["observation_store"]["observations"][0]["target_estimate"]["position_m"][2] = 2.4
    assert _camera_metrics(report, node, scope)[0]["max_capture_position_error_m"] == pytest.approx(0.1)
    node["scan_yaw_rad"] = 0.0
    event.update(scan_yaw_rad=0.0, measured_yaw_rad=0.1, yaw_error_rad=0.1)
    report["observation_store"]["observations"][0]["sensor_config"]["observer_yaw_rad"] = 0.1
    metrics, acceptance = _camera_metrics(report, node, scope)
    assert metrics["max_capture_yaw_error_rad"] == pytest.approx(0.1)
    assert acceptance["observer_yaw_bound_rad"] == 0.15
    event["scan_yaw_rad"] = 1.0
    with pytest.raises(ValueError, match="matching measured scan heading"):
        _camera_metrics(report, node, scope)
    event["scan_yaw_rad"] = 0.0
    event["hover_duration_s"] = 0.1
    with pytest.raises(ValueError, match="matching measured hover"):
        _camera_metrics(report, node, scope)
    event["hover_duration_s"] = 0.5
    report["observation_store"]["observations"][0]["target_estimate"]["position_m"][2] = 0.0
    with pytest.raises(ValueError, match="outside its declared observation region"):
        _camera_metrics(report, node, scope)


def test_camera_microtest_scope_binds_effective_scan_height():
    from tools.scenario_generation.record_configuration_microtests import task_scope

    node = {"id": "view", "skill_key": "aerial_scan", "target_ref": "site"}
    data = {"scene_key": "factory", "composition": {"entities": [], "targets": [
        {"id": "site", "position": [1.0, 2.0, 0.0], "radius_m": 0.5}]}}
    original = task_scope(data, node)
    assert original["scan_height_m"] == 1.5
    assert task_scope(data, {**node, "scan_height_m": 1.5}) == original
    changed = task_scope(data, {**node, "scan_height_m": 2.3})
    assert changed["world_sha256"] == original["world_sha256"]
    assert changed != original
    assert task_scope(data, {**node, "scan_yaw_rad": 0.0}) != original


def test_inventory_detection_protocol_keeps_raw_identification_and_localization_metrics(tmp_path):
    from tools.scenario_generation.configuration_microtest import validate_microtest
    from tools.scenario_generation.record_configuration_microtests import (
        _inventory_detection_metrics, derive_records,
    )

    scenario, attempt = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    base, = derive_records(scenario, attempt)
    evaluation = {
        "localization_tolerance_m": 0.1,
        "frames": [
            {"observation_id": "phase:frame:1", "identification_validated": True},
            {"observation_id": "phase:frame:2", "identification_validated": False},
        ],
        "fused_views": [{"view_node": "phase", "marker_id": 17,
                         "identification_validated": True, "localization_validated": True,
                         "localization_error_m": 0.04}],
    }
    metrics, acceptance = _inventory_detection_metrics(evaluation, {"id": "phase"})
    record = {**base, "protocol_id": "factory_inventory_detection_v2",
              "metrics": metrics, "acceptance": acceptance}
    assert validate_microtest(record)["metrics"] == metrics
    with pytest.raises(ValueError, match="identification or localization"):
        validate_microtest({**record, "metrics": {**metrics, "identified_frame_count": 0}})
    with pytest.raises(ValueError, match="identification or localization"):
        validate_microtest({**record, "metrics": {**metrics, "fused_localization_error_m": 0.11}})
    evaluation["fused_views"][0]["localization_validated"] = False
    with pytest.raises(ValueError, match="did not pass"):
        _inventory_detection_metrics(evaluation, {"id": "phase"})


def test_runtime_and_converter_share_observation_position_validation(tmp_path):
    from demo.benchmark.camera_validation import validate_camera_windows

    scenario_path, attempt_path = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    scenario = json.loads(scenario_path.read_text())
    report = json.loads(attempt_path.read_text())["report_body"]
    assert validate_camera_windows(scenario, report)["validated"]
    report["observation_store"]["observations"][0]["target_estimate"]["position_m"][0] = 9.0
    result = validate_camera_windows(scenario, report)
    assert not result["validated"]
    assert "outside its declared observation region" in result["nodes"]["phase"]["reason"]


def test_runtime_checks_partial_recovered_camera_window(tmp_path):
    from demo.benchmark.camera_validation import validate_camera_windows

    scenario_path, attempt_path = configuration_attempt(tmp_path, skill="wait", attachments=["camera"])
    scenario = json.loads(scenario_path.read_text())
    scenario["phase_graph"]["nodes"][0]["duration_s"] = 5.0
    report = json.loads(attempt_path.read_text())["report_body"]
    report["scheduling"]["states"]["phase"]["status"] = "failed"
    assert not validate_camera_windows(scenario, report)["validated"]
    report["scheduling"]["recovered_nodes"] = ["phase"]
    result = validate_camera_windows(scenario, report)
    assert result["validated"]
    assert result["nodes"]["phase"]["partial_recovered_window"]


def test_microtest_cannot_rewrite_metrics_or_task_scope(tmp_path):
    from tools.scenario_generation.record_configuration_microtests import derive_records

    scenario, attempt = configuration_attempt(tmp_path)
    record, = derive_records(scenario, attempt)
    for field, value in (("metrics", {**record["metrics"], "arrival_error_m": 0.1}),
                         ("scope", {**record["scope"], "world_sha256": "f" * 64})):
        with pytest.raises(ValueError, match="differs from measurements"):
            measurement_for_profile({**record, field: value})
    attempt.write_text(attempt.read_text() + "\n")
    with pytest.raises(ValueError, match="source evidence"):
        measurement_for_profile(record)


def test_failed_attempt_remains_in_ledger_without_a_passing_measurement(tmp_path):
    from tools.scenario_generation.record_configuration_microtests import main

    scenario, attempt = configuration_attempt(tmp_path)
    payload = json.loads(attempt.read_text())
    payload["outcome"]["returncode"] = 1
    attempt.write_text(json.dumps(payload))
    output = tmp_path / "records.json"
    assert main(["--scenario", str(scenario), "--attempt", str(attempt), "--output", str(output)]) == 1
    result = json.loads(output.read_text())
    assert result["records"] == []
    assert result["attempts"][0]["status"] == "rejected"


def test_changed_installation_invalidates_microtest(tmp_path):
    from tools.scenario_generation.record_configuration_microtests import derive_records

    scenario, attempt = configuration_attempt(tmp_path)
    record, = derive_records(scenario, attempt)
    (tmp_path / "evidence_asset.usda").write_text('#usda 1.0\ndef Xform "Changed" {}\n')
    with pytest.raises(ValueError, match="runtime files differ"):
        measurement_for_profile(record)
