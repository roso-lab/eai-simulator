"""Regression checks against false-positive contact-grasp evidence."""
from copy import deepcopy

import pytest

from ur5_grasp_checks import PHASE_SECONDS, evaluate, summarize_component_evidence
from mobile_mount_checks import box_separation, support_polygon_margin


def trial():
    samples = []
    for phase, seconds in PHASE_SECONDS.items():
        for _ in range(seconds * 10):
            raised = phase in ("lift", "transfer", "hold")
            transferred = phase in ("transfer", "hold", "lower", "release", "retreat")
            position = [0.5, 0.15 if transferred else 0, 0.4 if raised else 0.25]
            samples.append({"time_s": len(samples) * 0.1, "phase": phase,
                "object_position_m": position, "object_velocity_m_s": [0, 0, 0],
                "other_contact_force_N": 0,
                "wrist_position_m": [position[0], position[1], position[2] + 0.13],
                "contact_forces_N": [5, 5, 0] if raised else [0, 0, 1.962]})
    return {"schema": "eai.ur5_robotiq_contact_trial.v1", "dt_s": 0.1,
            "object_mass_kg": 0.2, "host": "fixed", "mass_scale": 1,
            "payload_pose_writes_during_task": 0, "payload_fixed_joint": False,
            "open_control": False, "initial_object_position_m": [0.5, 0, 0.25], "samples": samples}


def test_success_requires_contact_and_release():
    assert evaluate(trial())["grasp_pass"]


def test_table_intersection_is_distinct_from_clearance():
    chassis_min, chassis_max = (-0.465, -0.273, 0.124), (0.465, 0.273, 0.356)
    assert box_separation(chassis_min, chassis_max, (-0.744, -0.46, 0.061), (-0.444, 0.24, 0.161)) < 0
    assert box_separation(chassis_min, chassis_max, (-0.694, -0.46, 0.061), (-0.494, 0.24, 0.161)) == pytest.approx(0.029)
    with pytest.raises(ValueError):
        box_separation(chassis_min, chassis_max, (float("nan"), 0, 0), (1, 1, 1))


def test_support_margin_rejects_tipping_and_degenerate_support():
    supports = [(-0.25, -0.29), (0.25, -0.29), (0.25, 0.29), (-0.25, 0.29), (0, 0)]
    assert support_polygon_margin((0.05, 0, 0.4), supports) == pytest.approx(0.2)
    assert support_polygon_margin((0.3, 0, 0.4), supports) == pytest.approx(-0.05)
    assert support_polygon_margin((0, 0, 0.4), [(0, 0), (1, 0), (2, 0)]) is None


@pytest.mark.parametrize("failure", ["teleport", "weld", "scale", "no_contact", "one_finger",
                                     "table_support", "palm_support", "not_released", "slip", "not_transferred"])
def test_false_success_rejected(failure):
    report = trial()
    if failure == "teleport":
        report["payload_pose_writes_during_task"] = 1
    elif failure == "weld":
        report["payload_fixed_joint"] = True
    elif failure == "scale":
        report["mass_scale"] = 0.05
    for row in report["samples"]:
        if row["phase"] == "hold":
            if failure == "no_contact":
                row["contact_forces_N"] = [0, 0, 0]
            elif failure == "one_finger":
                row["contact_forces_N"][0] = 0
            elif failure == "table_support":
                row["contact_forces_N"][2] = 1.962
            elif failure == "palm_support":
                row["other_contact_force_N"] = 1.962
            elif failure == "slip":
                row["object_position_m"][2] -= (row["time_s"] - 11) * 0.02
            elif failure == "not_transferred":
                row["object_position_m"][1] = 0
        if row["phase"] == "retreat" and failure == "not_released":
            row["contact_forces_N"] = [5, 5, 0]
    assert not evaluate(report)["grasp_pass"]


@pytest.mark.parametrize("failure", ["nan", "truncated", "phase_order", "time_gap", "missing_host"])
def test_invalid_evidence_rejected(failure):
    report = trial()
    if failure == "nan":
        report["samples"][30]["contact_forces_N"][0] = float("nan")
    elif failure == "truncated":
        report["samples"].pop()
    elif failure == "phase_order":
        report["samples"][0]["phase"] = "hold"
    elif failure == "time_gap":
        report["samples"][30]["time_s"] += 0.1
    else:
        report["host"] = "scout"
    with pytest.raises(ValueError):
        evaluate(report)


def test_open_control_requires_stationary_object():
    report = trial()
    report["open_control"] = True
    for row in report["samples"]:
        row["object_position_m"] = deepcopy(report["initial_object_position_m"])
        row["contact_forces_N"] = [0, 0, 1.962]
    result = evaluate(report)
    assert result["open_control_pass"] and not result["grasp_pass"]
    report["samples"][40]["object_position_m"][2] += 0.05
    assert not evaluate(report)["open_control_pass"]


def test_component_summary_requires_paired_repeats_and_returns_scoped_e7():
    reports = []
    for repeat in range(3):
        positive = trial()
        positive.update(repeat=repeat, runner_sha256="a" * 64,
                        assembly_helper_sha256="b" * 64,
                        source_layers=[{"path": "ur5.usd", "sha256": "c" * 64},
                                       {"path": "gripper.usd", "sha256": "d" * 64}])
        reports.append(positive)
        control = trial()
        control.update(repeat=repeat, open_control=True, runner_sha256="a" * 64,
                       assembly_helper_sha256="b" * 64,
                       source_layers=[{"path": "ur5.usd", "sha256": "c" * 64},
                                      {"path": "gripper.usd", "sha256": "d" * 64}])
        for row in control["samples"]:
            row["object_position_m"] = list(control["initial_object_position_m"])
            row["contact_forces_N"] = [0, 0, 1.962]
        reports.append(control)

    reports[1]["source_layers"] = list(reversed(reports[1]["source_layers"]))

    result = summarize_component_evidence(reports)

    assert result["tier"] == "E7"
    assert result["validated_masses_kg"] == [0.2]
    assert result["scope"] == "standalone_fixed_base_grasp_lift_transfer_release"


def test_component_summary_rejects_mobile_trial():
    report = trial()
    report.update(repeat=0, host="scout", runner_sha256="a" * 64,
                  assembly_helper_sha256="b" * 64,
                  source_layers=[{"path": "ur5.usd", "sha256": "c" * 64}])
    with pytest.raises(ValueError, match="fixed-base"):
        summarize_component_evidence([report])
