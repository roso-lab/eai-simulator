"""Vertical protocol checks never start Isaac or assert locomotion capability."""

from dataclasses import replace
import inspect
import math

import pytest

from demo.benchmark.vertical_traversal import (
    VerticalProbe, admit_vertical_probe, advance_ground_health, advance_vertical_progress,
)


PROBE = VerticalProbe("measured_test_ramp", "ramp", (0., 0., 0.), (8., 0., .85),
                      1.3, .15, 0.)


def admission(probe=PROBE, mobility="quadruped", **kwargs):
    return admit_vertical_probe(probe, mobility=mobility, physical_radius_m=.55,
                                slope_limit_rad=.2, step_limit_m=.18, **kwargs)


def health(z=.32, support=0., quaternion=(1., 0., 0., 0.), state=None, dt=.1):
    return advance_ground_health({} if state is None else state, root_z_m=z,
                                 quaternion_wxyz=quaternion, support_z_m=support,
                                 reference_clearance_m=.32, dt_s=dt)


def progress(state, root, probe=PROBE, support=.85, pose_health=None):
    return advance_vertical_progress(state, probe, root_xyz=root,
        reference_clearance_m=.32, dt_s=.1,
        health=health(root[2], support) if pose_health is None else pose_health)


def test_morphology_is_only_probe_admission_not_physical_evidence():
    stairs = replace(PROBE, kind="stair", maximum_step_m=.16)
    for mobility in ("quadruped", "biped"):
        result = admission(stairs, mobility)
        assert result["admitted_for_probe"] and not result["execution_validated"]
    assert admission(stairs, "wheeled")["reasons"] == ["stairs_require_legged_mobility"]
    assert admission(PROBE, "wheeled")["admitted_for_probe"]
    assert not admission(PROBE, "aerial")["admitted_for_probe"]


def test_low_ramp_requires_configuration_limits_and_physical_clearance():
    assert admission(replace(PROBE, clear_width_m=.54))["reasons"] == ["insufficient_clear_width"]
    assert "insufficient_clear_width" in admission(clearance_reserve_m=.11)["reasons"]
    assert admission(replace(PROBE, maximum_slope_rad=.3), "wheeled")["reasons"] == ["slope_limit_exceeded"]
    assert admission(replace(PROBE, maximum_step_m=.2), "wheeled")["reasons"] == ["step_limit_exceeded"]
    result = admit_vertical_probe(PROBE, mobility="wheeled", physical_radius_m=.2)
    assert result["reasons"] == ["missing_slope_limit", "missing_step_limit"]


@pytest.mark.parametrize("changes", [
    {"goal_support_xyz": (8., 0., 0.)}, {"clear_width_m": math.nan},
    {"maximum_slope_rad": math.pi / 2}, {"kind": "unknown"},
    {"start_support_xyz": (1., 2.)}, {"maximum_step_m": -.1},
])
def test_invalid_probe_fails_closed(changes):
    with pytest.raises(ValueError):
        replace(PROBE, **changes)


def test_low_go2_pose_is_sustained_failure_not_waypoint_deadlock():
    state = {}
    for _ in range(4):
        assert health(.06, state=state)["status"] == "unstable"
    result = health(.06, state=state)
    assert result["status"] == "fallen"
    assert result["reasons"] == ["low_support_clearance"]
    assert health(.32, state=state)["status"] == "healthy"
    assert health(.06, state=state)["status"] == "unstable"


def test_support_height_not_world_height_and_tilt_is_quaternion_based():
    assert health(1.89, 1.57)["status"] == "healthy"
    assert health(1.63, 1.57, dt=.5)["status"] == "fallen"
    assert health(quaternion=(0., 2., 0., 0.), dt=.5)["reasons"] == ["excessive_tilt"]
    assert health(support=None)["status"] == "support_unknown"
    assert health(support=None, quaternion=(0., 1., 0., 0.), dt=.5)["failed"]
    assert health(z=math.nan)["status"] == "invalid_state"
    assert health(quaternion=(0., 0., 0., 0.))["status"] == "invalid_state"


def test_xy_arrival_at_wrong_height_never_completes():
    state = {}
    for _ in range(10):
        assert not progress(state, (8., 0., .32), support=0.)["complete"]
    # Correct root altitude without a matching support surface also fails.
    for _ in range(10):
        assert not progress(state, (8., 0., 1.17), support=0.)["complete"]


def test_arrival_requires_continuous_healthy_xyz_dwell():
    state = {}
    for _ in range(4):
        assert not progress(state, (8., 0., 1.17))["complete"]
    assert not progress(state, (7., 0., 1.17))["complete"]
    for _ in range(4):
        assert not progress(state, (8., 0., 1.17))["complete"]
    assert progress(state, (8., 0., 1.17))["complete"]
    assert not progress(state, (8., 0., 1.17), support=None)["complete"]


def test_ascent_and_descent_progress_reject_oscillation():
    state = {}
    assert progress(state, (1., 0., .52), support=.2)["made_height_progress"]
    for z in (.42, .51, .5, .52):
        assert not progress(state, (1., 0., z), support=z - .32)["made_height_progress"]
    descent = replace(PROBE, start_support_xyz=PROBE.goal_support_xyz,
                      goal_support_xyz=PROBE.start_support_xyz)
    state = {}
    assert progress(state, (7., 0., .97), descent, support=.65)["made_height_progress"]
    for _ in range(5):
        result = progress(state, (0., 0., .32), descent, support=0.)
    assert result["complete"]


def test_unknown_or_unhealthy_support_cannot_create_progress():
    assert not progress({}, (8., 0., 1.17), support=None)["made_height_progress"]
    bad_health = health(1.17, .85, quaternion=(0., 1., 0., 0.), dt=.5)
    result = progress({}, (8., 0., 1.17), pose_health=bad_health)
    assert not result["complete"] and not result["made_height_progress"]


def test_support_observation_is_calibrated_evidence_and_not_navigation_radius():
    from tools.scenario_generation.vertical_task import (
        StairSupportRaycaster, SupportObservationSpec, support_observation_error,
        support_stop_decision, support_terminal_reason,
        vertical_support_terminal_reason, zero_body_command,
    )

    unavailable = SupportObservationSpec.unavailable()
    assert support_observation_error(unavailable) == "uncalibrated_support_observation"
    raycaster = object.__new__(StairSupportRaycaster)
    result = raycaster.patch((1.0, 2.0, .4), unavailable)
    assert result["status"] == "unknown"
    assert result["reason"] == "uncalibrated_support_observation"
    assert support_terminal_reason(result) == "support_unknown"
    assert vertical_support_terminal_reason(0, 1, result) is None
    assert vertical_support_terminal_reason(1, 1, result) == "support_unknown"
    assert support_terminal_reason({"status": "unknown", "reason": "support_patch_gap"}) == "support_patch_gap"
    assert support_terminal_reason({"status": "measured"}) is None
    decision = support_stop_decision(result)
    assert decision["status"] == "support_unknown"
    assert decision["reason"] == "uncalibrated_support_observation"
    assert decision["command_body_xyz_m_s"] == [0.0, 0.0, 0.0]
    assert decision["posture_failure"] is False
    assert support_stop_decision({"status": "unknown", "reason": "support_patch_gap"})["status"] == "support_patch_gap"
    calibrated = SupportObservationSpec(
        "factory_stair_support_observation_v1", "foot_contact", True, "cal-1",
        ((0.0, 0.0), (0.4, 0.0)))
    gap_raycaster = object.__new__(StairSupportRaycaster)
    gap_raycaster.maximum_step_m = .20
    gap_raycaster.cast = lambda origin: {
        "status": "measured", "support_z_m": 0.06 if origin[0] < 1.2 else .36,
    }
    gap = gap_raycaster.patch((1.0, 2.0, .4), calibrated)
    assert gap["reason"] == "support_patch_gap"
    assert support_stop_decision(gap)["posture_failure"] is False
    assert zero_body_command() == (0.0, 0.0, 0.0)
    assert "radius" not in inspect.signature(StairSupportRaycaster.patch).parameters


def test_stair_micro_probes_are_independent_observation_only_stages():
    from tools.scenario_generation.vertical_task import (
        MICRO_PROBE_STAGES, micro_probe_record,
    )

    first = micro_probe_record("first_tread")
    second = micro_probe_record("second_tread")
    assert first["schema"] == second["schema"]
    assert first["independent"] and second["independent"]
    assert first["success_claim_allowed"] is False
    assert second["success_claim_allowed"] is False
    assert first["goal_xy_m"] != second["goal_xy_m"]
    assert first["max_steps"] == MICRO_PROBE_STAGES["first_tread"]["max_steps"]
    assert second["speed_cap_m_s"] == MICRO_PROBE_STAGES["second_tread"]["speed_cap_m_s"]
    assert first["speed_cap_m_s"] >= 0.55
    assert second["speed_cap_m_s"] >= 0.55
    assert micro_probe_record("full")["success_claim_allowed"] is True


def test_factory_exclusions_stay_scoped_to_three_legacy_ground_tasks():
    from EAI.scenario_spec.geometry import ground_surface_exclusions

    for task in ("F-INS-01", "F-TRAFFIC-01", "F-COV-01"):
        assert len(ground_surface_exclusions({"scene_key": "factory", "benchmark_task_id": task})) == 3
    for task in (None, "TRN-RAMP", "SAR-01"):
        assert ground_surface_exclusions({"scene_key": "factory", "benchmark_task_id": task}) == []
    assert ground_surface_exclusions({"scene_key": "warehouse_nvd", "benchmark_task_id": "F-INS-01"}) == []


def test_explicit_vertical_objective_opens_the_declared_factory_surface_link():
    from EAI.scenario_spec.geometry import ground_surface_exclusions

    data = {"scene_key": "factory", "benchmark_task_id": "F-INS-01",
            "phase_graph": {"nodes": [{"id": "climb", "vertical": {
                "link_id": "main_stair", "kind": "stair", "start_level": "ground",
                "goal_level": "mezzanine", "support_probe": "required"}}]}}
    assert ground_surface_exclusions(data) == []


def test_mixed_vertical_episode_keeps_ground_navigation_exclusions():
    from EAI.scenario_spec.geometry import ground_surface_exclusions

    data = {
        "scene_key": "factory", "benchmark_task_id": "F-INS-01",
        "robots": [{"id": "go2_1", "type": "go2"}],
        "phase_graph": {"nodes": [
            {"id": "climb", "preferred_agent": "go2_1", "vertical": {
                "link_id": "main_stair", "kind": "stair", "start_level": "ground",
                "goal_level": "mezzanine", "support_probe": "required"}},
            {"id": "ground_check", "preferred_agent": "go2_1"},
        ]},
    }
    assert {item["id"] for item in ground_surface_exclusions(data)} == {
        "main_stair", "west_ramp", "south_high_ramp"}


def test_legacy_height_evidence_does_not_invent_fall_diagnosis():
    from demo.benchmark.vertical_traversal import summarize_vertical_episode

    result = summarize_vertical_episode({"status": "deadlocked",
        "final_poses": {"go2_1": [-2.3, -3.31, .06]}}, robot_id="go2_1", reference_root_z_m=.32)
    assert result["maximum_observed_root_height_drop_m"] == pytest.approx(.26)
    assert result["health_verdict"] == "unknown"
    assert result["finite_trajectory_samples"] == 0
    assert not result["execution_validated"]


def test_diagnosis_cli_extracts_trajectory_and_rejects_invalid_pose(tmp_path, capsys):
    import json
    from tools.scenario_generation.diagnose_vertical_episode import main

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"pose_trajectory": {"samples": [
        {"step": 0, "robot_poses_xyzyaw": {"go2_1": [1., 2., .32, 0.]}},
        {"step": 10, "positions": {"go2_1": [1., 2., .06]}},
        {"step": 20, "positions": {"go2_1": [1., 2., None]}},
    ]}}))
    assert main([str(report), "--reference-root-z-m", "0.32"]) == 0
    result = json.loads(capsys.readouterr().out)["reports"][0]
    assert result["lowest_trajectory_sample"]["step"] == 10
    assert result["invalid_pose_samples"] == 1
    assert result["finite_trajectory_samples"] == 2
    assert result["health_verdict"] == "unknown"


def test_vertical_support_terminal_stops_only_after_surface_route_entry():
    from tools.scenario_generation.vertical_task import (
        vertical_support_terminal_reason, support_terminal_reason,
        zero_body_command,
    )

    gap = {"status": "unknown", "reason": "support_patch_gap"}
    unknown = {"status": "unknown", "reason": "uncalibrated_support_observation"}
    assert vertical_support_terminal_reason(2, 3, gap) is None
    assert vertical_support_terminal_reason(3, 3, gap) == "support_patch_gap"
    assert vertical_support_terminal_reason(3, 3, unknown) == "support_unknown"
    assert support_terminal_reason({"status": "measured"}) is None
    assert support_terminal_reason({"status": "temporarily_unobserved"}) is None
    assert zero_body_command() == (0.0, 0.0, 0.0)


def test_support_observation_never_reuses_navigation_radius():
    import inspect
    from tools.scenario_generation.vertical_task import (
        SupportObservationSpec, StairSupportRaycaster, support_observation_error,
    )

    observation = SupportObservationSpec.unavailable()
    assert support_observation_error(observation) == "uncalibrated_support_observation"
    assert "radius" not in observation.as_record()
    assert "radius" not in inspect.signature(StairSupportRaycaster.patch).parameters


def test_first_and_second_tread_micro_probe_schema_is_observation_only():
    from tools.scenario_generation.vertical_task import (
        MICRO_PROBE_LOG_SCHEMA, micro_probe_record,
    )

    first = micro_probe_record("first_tread")
    second = micro_probe_record("second_tread")
    assert first["schema"] == MICRO_PROBE_LOG_SCHEMA
    assert second["schema"] == MICRO_PROBE_LOG_SCHEMA
    assert first["independent"] and second["independent"]
    assert first["goal_xy_m"] != second["goal_xy_m"]
    assert first["execution_validated"] is False
    assert second["execution_validated"] is False
    assert first["success_claim_allowed"] is False
    assert second["success_claim_allowed"] is False


def test_s13_foot_mapping_requires_real_unique_articulation_bodies():
    from demo.benchmark.vertical_traversal import foot_body_indices

    names = ["base", "RR_foot", "FL_foot", "RL_foot", "FR_foot"]
    for robot in ("go2", "b2"):
        assert foot_body_indices(robot, names) == {
            "FL_foot": 2, "FR_foot": 4, "RL_foot": 3, "RR_foot": 1}
        for invalid in (names[:-1], names + ["FL_foot"]):
            with pytest.raises(ValueError, match="missing_or_ambiguous"):
                foot_body_indices(robot, invalid)
    lite3_names = ["TORSO", "HR_FOOT", "FL_FOOT", "HL_FOOT", "FR_FOOT"]
    assert foot_body_indices("lite3", lite3_names) == {
        "FL_FOOT": 2, "FR_FOOT": 4, "HL_FOOT": 3, "HR_FOOT": 1}
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        foot_body_indices("lite3", lite3_names[:-1])
    with pytest.raises(ValueError, match="unsupported"):
        foot_body_indices("m20", ["fl_wheel", "fr_wheel", "hl_wheel", "hr_wheel"])


def _s13_contact_buffers():
    # Unused slots and empty-pair starts are not initialized by PhysX.
    return ([[12.], [math.nan]], [[1., 2., .21], [math.nan] * 3],
            [[0., 0., 1.], [math.nan] * 3], [[-.001], [math.nan]],
            [[1, 0]], [[0, -1]])


def test_s13_contact_decoder_keeps_identity_and_does_not_invent_support():
    from demo.benchmark.vertical_traversal import (
        decode_foot_contact_buffer, support_from_foot_observation,
    )

    decoded = decode_foot_contact_buffer(_s13_contact_buffers(),
        collision_paths=["/World/Stair", "/World/Floor"], capacity=2)
    assert decoded["status"] == "observed"
    assert decoded["contacts"] == [{"collision_path": "/World/Stair",
        "position_world_m": [1., 2., .21], "normal_world": [0., 0., 1.],
        "normal_force_N": 12., "separation_m": -.001}]
    for observation in (decoded, {"status": "observed", "feet": [decoded] * 4}):
        support = support_from_foot_observation(observation)
        assert support["status"] == "unknown"
        assert support["support_z_m"] is None
        assert not progress({}, (8., 0., 1.17), support=support["support_z_m"])["complete"]


@pytest.mark.parametrize("failure", ["saturated", "overlap", "range", "nan", "filter", "shape"])
def test_s13_contact_buffer_counterexamples_fail_closed(failure):
    from demo.benchmark.vertical_traversal import decode_foot_contact_buffer

    raw = _s13_contact_buffers()
    paths = ["/World/Stair", "/World/Floor"]
    if failure == "saturated":
        for buffer in raw[:4]:
            buffer[1] = list(buffer[0])
        raw[4][0] = [1, 1]
        raw[5][0] = [0, 1]
    elif failure == "overlap":
        raw[4][0] = [1, 1]
        raw[5][0] = [0, 0]
    elif failure == "range":
        raw[5][0][0] = 2
    elif failure == "nan":
        raw[2][0][2] = math.nan
    elif failure == "filter":
        paths[0] = "/World/*"
    else:
        raw[4][0].pop()
    decoded = decode_foot_contact_buffer(raw, collision_paths=paths, capacity=2)
    assert decoded["status"] == "unknown"
    assert not decoded["contacts"]
    assert decoded["buffer_saturated"] is (failure == "saturated")


def test_s13_calibration_label_cannot_promote_rays_to_foot_contacts():
    from tools.scenario_generation.vertical_task import (
        StairSupportRaycaster, SupportObservationSpec, support_stop_decision,
    )

    rays = object.__new__(StairSupportRaycaster)
    rays.maximum_step_m = .20
    rays.cast = lambda origin: {"status": "measured", "support_z_m": .21}
    spec = SupportObservationSpec("factory_stair_support_observation_v1",
                                  "foot_contact", True, "label-only", ((0., 0.),))
    sample = rays.patch((1., 2., .55), spec)
    assert sample["reason"] == "raycast_is_not_contact_evidence"
    assert "support_z_m" not in sample
    assert support_stop_decision(sample)["command_body_xyz_m_s"] == [0., 0., 0.]


def test_s14_contact_timestamp_uses_control_step_dt():
    import inspect
    from tools.scenario_generation.vertical_task import (
        FootContactSampler, control_step_timestamp,
    )

    assert control_step_timestamp(1, .02) == pytest.approx(.02)
    assert control_step_timestamp(4, .02) == pytest.approx(.08)
    assert control_step_timestamp(4, .005) == pytest.approx(.02)
    assert "step_dt" in inspect.signature(FootContactSampler).parameters
    assert "control_step" in inspect.signature(FootContactSampler.sample).parameters
    assert "step" not in inspect.signature(FootContactSampler.sample).parameters


def test_s14_calibration_budget_is_rejected_when_stage_would_shorten_it():
    from tools.scenario_generation.vertical_task import resolve_probe_budget

    resolved = resolve_probe_budget(3000, 200, 900)
    assert resolved == {"requested_steps": 3000, "stage_limit": 900,
                        "effective_steps": 900, "calibration_steps": 200,
                        "execution_steps": 700}
    with pytest.raises(ValueError, match="exceeds effective"):
        resolve_probe_budget(3000, 900, 900)
    with pytest.raises(ValueError, match="exceeds effective"):
        resolve_probe_budget(3000, 901, 900)


def test_s14_step_outcome_fails_closed_and_preserves_termination_class():
    from tools.scenario_generation.vertical_task import step_outcome_reason

    empty = ({}, {"go2_1": 0}, {"go2_1": False}, {"go2_1": False}, {})
    assert step_outcome_reason(empty, "go2_1") is None
    assert step_outcome_reason(({}, {}, {"go2_1": True}, {"go2_1": False}, {}),
                               "go2_1") == "env_terminated"
    assert step_outcome_reason(({}, {}, {"go2_1": False}, {"go2_1": True}, {}),
                               "go2_1") == "env_truncated"
    assert step_outcome_reason(({}, {}, {"other": False}, {"go2_1": False}, {}),
                               "go2_1") == "invalid_step_result"
    assert step_outcome_reason(({}, {}, {"go2_1": []}, {"go2_1": False}, {}),
                               "go2_1") == "invalid_step_result"
    assert step_outcome_reason(({}, {}, {"go2_1": None}, {"go2_1": False}, {}),
                               "go2_1") == "invalid_step_result"
    assert step_outcome_reason(({}, {}, {"go2_1": False}), "go2_1") == "invalid_step_result"


def _support_calibration_rows(count=12, *, drifting=False, missing_contact=False):
    body_names = ("FL_foot", "FR_foot", "RL_foot", "RR_foot")
    offsets = ((.2, .12), (.2, -.12), (-.2, .12), (-.2, -.12))
    rows = []
    for sample_index in range(count):
        root_x = .1 * sample_index if drifting else .001 * sample_index
        feet = []
        for foot_index, (name, offset) in enumerate(zip(body_names, offsets)):
            contacts = [] if missing_contact and foot_index == 3 else [{
                "collision_path": "/World/Factory/Floor",
                "position_world_m": [root_x + offset[0], offset[1], .06],
                "normal_world": [0., 0., 1.],
                "normal_force_N": 25.,
                "separation_m": -.001,
            }]
            feet.append({
                "status": "observed", "body_name": name,
                "body_path": f"/World/envs/env_0/go2_1/{name}",
                "body_position_world_m": [root_x + offset[0], offset[1], .08],
                "contacts": contacts,
            })
        rows.append({
            "root_xyz_m": [root_x, 0., .38],
            "root_quaternion_wxyz": [1., 0., 0., 0.],
            "contact": {"status": "observed", "feet": feet},
        })
    return rows


def test_runtime_foot_support_calibration_opens_contact_measurement_only():
    from tools.scenario_generation.vertical_task import (
        FOOT_SUPPORT_CALIBRATION_SCHEMA, calibrate_foot_support,
        support_from_calibrated_contacts,
    )

    rows = _support_calibration_rows()
    calibration = calibrate_foot_support(
        rows, static_collision_paths=["/World/Factory/Floor"])
    assert calibration.schema == FOOT_SUPPORT_CALIBRATION_SCHEMA
    assert calibration.reference_clearance_m == pytest.approx(.32)
    assert calibration.calibration_minimum_stance_feet == 3
    assert calibration.runtime_minimum_stance_feet == 1
    record = calibration.as_record()
    assert record["calibration_minimum_stance_feet"] == 3
    assert record["runtime_minimum_stance_feet"] == 1
    assert "minimum_stance_feet" not in record
    assert len(calibration.calibration_id) == 64
    support = support_from_calibrated_contacts(rows[-1]["contact"], calibration)
    assert support["status"] == "measured"
    assert support["support_z_m"] == pytest.approx(.06)
    assert support["calibration_id"] == calibration.calibration_id
    assert support["evidence_kind"] == "calibrated_foot_contact"


def test_runtime_calibration_keeps_settling_outside_the_trailing_stable_window():
    from tools.scenario_generation.vertical_task import calibrate_foot_support

    settling = _support_calibration_rows(4)
    for row in settling:
        for foot in row["contact"]["feet"]:
            foot["contacts"] = []
    calibration = calibrate_foot_support(
        settling + _support_calibration_rows(10),
        static_collision_paths=["/World/Factory/Floor"], minimum_samples=10)
    assert calibration.sample_count == 10


def test_runtime_support_allows_single_dynamic_contact_but_rejects_zero_and_gaps():
    from tools.scenario_generation.vertical_task import (
        calibrate_foot_support, support_from_calibrated_contacts,
    )

    rows = _support_calibration_rows()
    calibration = calibrate_foot_support(
        rows, static_collision_paths=["/World/Factory/Floor"])
    two_feet = _support_calibration_rows(1, missing_contact=True)[0]["contact"]
    two_feet["feet"][2]["contacts"] = []
    two_support = support_from_calibrated_contacts(two_feet, calibration)
    assert two_support["status"] == "measured"
    assert two_support["progress_eligible"] is True

    one_foot = _support_calibration_rows(1, missing_contact=True)[0]["contact"]
    one_foot["feet"][1]["contacts"] = []
    one_foot["feet"][2]["contacts"] = []
    one_support = support_from_calibrated_contacts(one_foot, calibration)
    assert one_support["status"] == "measured"
    assert one_support["progress_eligible"] is False

    zero_feet = _support_calibration_rows(1)[0]["contact"]
    for foot in zero_feet["feet"]:
        foot["contacts"] = []
    assert support_from_calibrated_contacts(
        zero_feet, calibration)["reason"] == "insufficient_supporting_feet"

    changed = _support_calibration_rows(1)[0]["contact"]
    changed["feet"][0]["body_path"] = "/World/other/FL_foot"
    assert support_from_calibrated_contacts(changed, calibration)["reason"] == "foot_contact_identity_changed"

    stair = _support_calibration_rows(1)[0]["contact"]
    stair["feet"][0]["contacts"][0]["position_world_m"][2] = .36
    assert support_from_calibrated_contacts(stair, calibration)["reason"] == "support_patch_gap"


def test_runtime_support_tracker_allows_only_a_bounded_gait_contact_gap():
    from tools.scenario_generation.vertical_task import (
        FootSupportTracker, calibrate_foot_support, support_terminal_reason,
    )

    rows = _support_calibration_rows()
    calibration = calibrate_foot_support(
        rows, static_collision_paths=["/World/Factory/Floor"])
    tracker = FootSupportTracker(calibration, maximum_unobserved_s=.08)
    measured = tracker.observe(rows[-1]["contact"], dt_s=0.)
    assert measured["status"] == "measured"

    gap = _support_calibration_rows(1)[0]["contact"]
    for foot in gap["feet"]:
        foot["contacts"] = []
    for expected_age in (.02, .04, .06, .08):
        held = tracker.observe(gap, dt_s=.02)
        assert held["status"] == "temporarily_unobserved"
        assert held["support_z_m"] is None
        assert held["unobserved_s"] == pytest.approx(expected_age)
        assert support_terminal_reason(held) is None
    timed_out = tracker.observe(gap, dt_s=.02)
    assert timed_out["status"] == "unknown"
    assert timed_out["reason"] == "support_contact_gap_timeout"
    assert support_terminal_reason(timed_out) == "support_unknown"


def test_runtime_support_tracker_does_not_grace_identity_changes():
    from tools.scenario_generation.vertical_task import (
        FootSupportTracker, calibrate_foot_support,
    )

    rows = _support_calibration_rows()
    calibration = calibrate_foot_support(
        rows, static_collision_paths=["/World/Factory/Floor"])
    tracker = FootSupportTracker(calibration)
    tracker.observe(rows[-1]["contact"], dt_s=0.)
    changed = _support_calibration_rows(1)[0]["contact"]
    changed["feet"][0]["body_path"] = "/World/other/FL_foot"
    result = tracker.observe(changed, dt_s=.02)
    assert result["status"] == "unknown"
    assert result["reason"] == "foot_contact_identity_changed"


def test_runtime_support_calibration_rejects_motion_and_missing_foot_persistence():
    from tools.scenario_generation.vertical_task import calibrate_foot_support

    with pytest.raises(ValueError, match="root_drift"):
        calibrate_foot_support(
            _support_calibration_rows(drifting=True),
            static_collision_paths=["/World/Factory/Floor"])

    with pytest.raises(ValueError, match="invalid_calibration_minimum_stance_feet"):
        calibrate_foot_support(
            _support_calibration_rows(),
            static_collision_paths=["/World/Factory/Floor"],
            calibration_minimum_stance_feet=2)
    with pytest.raises(ValueError, match="per_foot_contact_persistence"):
        calibrate_foot_support(
            _support_calibration_rows(missing_contact=True),
            static_collision_paths=["/World/Factory/Floor"])
