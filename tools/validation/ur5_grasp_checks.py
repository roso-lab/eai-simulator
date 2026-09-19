"""Independently evaluate recorded contact grasp trials without Isaac imports."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


PHASE_SECONDS = {"approach": 3, "close": 2, "lift": 3, "transfer": 3,
                 "hold": 2, "lower": 3, "release": 2, "retreat": 3}
MIN_E7_REPEATS = 3


def canonical_report_hash(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode("utf-8")).hexdigest()


def norm(vector):
    return math.sqrt(sum(value * value for value in vector))


def evaluate(report):
    """Return conservative fixture acceptance, not a hardware capacity rating."""
    if report.get("schema") != "eai.ur5_robotiq_contact_trial.v1":
        raise ValueError("Unsupported report schema")
    timestep = report["dt_s"]
    mass = report["object_mass_kg"]
    if not all(math.isfinite(value) and value > 0 for value in (timestep, mass)) or timestep > 0.1:
        raise ValueError("Invalid timestep or mass")
    rows = report["samples"]
    expected_phases = [phase for phase, duration in PHASE_SECONDS.items()
                       for _ in range(round(duration / timestep))]
    if [row["phase"] for row in rows] != expected_phases:
        raise ValueError("Missing, reordered or truncated phases")
    for index, row in enumerate(rows):
        if not math.isfinite(row["time_s"]) or abs(row["time_s"] - index * timestep) > 1e-5:
            raise ValueError("Nonuniform sample timeline")
        for key in ("object_position_m", "object_velocity_m_s", "wrist_position_m", "contact_forces_N"):
            if len(row[key]) != 3 or not all(math.isfinite(value) for value in row[key]):
                raise ValueError(f"Invalid sample vector: {key}")
        if any(value < 0 for value in row["contact_forces_N"]):
            raise ValueError("Contact force magnitudes must be nonnegative")
        other_force = row.get("other_contact_force_N")
        if other_force is not None and (not math.isfinite(other_force) or other_force < 0):
            raise ValueError("Invalid other contact force")
        if row.get("host_pose") is not None:
            if len(row["host_pose"]) != 7 or not all(math.isfinite(value) for value in row["host_pose"]):
                raise ValueError("Invalid host pose")
    initial = report["initial_object_position_m"]
    if len(initial) != 3 or not all(math.isfinite(value) for value in initial):
        raise ValueError("Invalid initial object pose")
    hold = [row for row in rows if row["phase"] == "hold"]
    final = rows[-round(1 / timestep):]
    weight = mass * 9.81
    min_hold_lift = min(row["object_position_m"][2] - initial[2] for row in hold)
    relative = [[position - wrist for position, wrist in zip(row["object_position_m"], row["wrist_position_m"])]
                for row in hold]
    slip = max(norm([current - start for current, start in zip(offset, relative[0])]) for offset in relative)
    transfer_error = max(norm([row["object_position_m"][0] - initial[0],
                               row["object_position_m"][1] - initial[1] - 0.15]) for row in hold)
    release_error = max(norm([row["object_position_m"][0] - initial[0],
                              row["object_position_m"][1] - initial[1] - 0.15,
                              row["object_position_m"][2] - initial[2]]) for row in final)
    checks = {
        "no_scripted_payload_attachment": report.get("payload_pose_writes_during_task") == 0
                                          and report.get("payload_fixed_joint") is False,
        "unscaled_mass": report.get("mass_scale") == 1.0,
        "sustained_lift": min_hold_lift >= 0.10,
        "bilateral_contact_through_hold": all(min(row["contact_forces_N"][:2]) > 0.5 for row in hold),
        "no_table_support_through_hold": all(row["contact_forces_N"][2] < max(0.05, weight * 0.05) for row in hold),
        "no_other_support_through_hold": all(row.get("other_contact_force_N") is not None
            and row["other_contact_force_N"] < 0.05 for row in hold),
        "transfer_reached": transfer_error < 0.025,
        "hold_slip_below_1cm": slip < 0.01,
        "released_to_target": release_error < 0.025,
        "released_fingers_clear": all(max(row["contact_forces_N"][:2]) < 0.1 for row in final),
        "released_table_supported": all(abs(row["contact_forces_N"][2] - weight) < weight * 0.2 for row in final),
        "released_stationary": all(norm(row["object_velocity_m_s"]) < 0.01 for row in final),
    }
    host_rows = [row["host_pose"] for row in rows if row.get("host_pose") is not None]
    tilt = drift = None
    if report["host"] != "fixed":
        if len(host_rows) != len(rows):
            raise ValueError("Missing mobile host trajectory")
        tilt = max(math.acos(max(-1, min(1, 1 - 2 * (pose[4] ** 2 + pose[5] ** 2)))) for pose in host_rows)
        drift = max(norm([pose[index] - host_rows[0][index] for index in (0, 1)]) for pose in host_rows)
        checks["mobile_host_stable"] = tilt < 0.15 and drift < 0.1
    negative_pass = bool(report["open_control"]) and all(
        norm([current - start for current, start in zip(row["object_position_m"], initial)]) < 0.01
        and max(row["contact_forces_N"][:2]) < 0.1 for row in rows)
    return {"grasp_pass": all(checks.values()) and not report["open_control"],
            "open_control_pass": negative_pass, "checks": checks,
            "min_hold_lift_m": min_hold_lift, "max_hold_slip_m": slip,
            "max_transfer_error_m": transfer_error, "max_release_error_m": release_error,
            "hold_contact_min_N": [min(row["contact_forces_N"][index] for row in hold) for index in range(3)],
            "hold_contact_max_N": [max(row["contact_forces_N"][index] for row in hold) for index in range(3)],
            "max_host_tilt_rad": tilt, "max_host_planar_drift_m": drift}


def summarize_component_evidence(reports, *, minimum_repeats=MIN_E7_REPEATS):
    """Summarize repeated fixed-base UR5 trials as scoped component evidence.

    This deliberately does not accept mobile-host trials or infer a maximum
    payload from the largest successful mass.
    """
    if isinstance(minimum_repeats, bool) or not isinstance(minimum_repeats, int) or minimum_repeats < 3:
        raise ValueError("component evidence requires at least three repeats")
    if not isinstance(reports, (list, tuple)) or not reports:
        raise ValueError("at least one trial report is required")
    groups = {}
    source_identity = None
    report_hashes = []
    for report in reports:
        if not isinstance(report, dict) or report.get("host") != "fixed":
            raise ValueError("component E7 requires fixed-base reports")
        if report.get("mass_scale") != 1.0:
            raise ValueError("component E7 requires unscaled mass")
        layers = report.get("source_layers")
        canonical_layers = tuple(sorted(
            (layer.get("path"), layer.get("sha256")) for layer in layers or ()
        ))
        identity = (report.get("runner_sha256"), report.get("assembly_helper_sha256"),
                    canonical_layers)
        if not report.get("runner_sha256") or not report.get("assembly_helper_sha256") or not layers:
            raise ValueError("component E7 requires runner, helper and asset hashes")
        if source_identity is None:
            source_identity = identity
        elif identity != source_identity:
            raise ValueError("all component trials must use identical source hashes")
        mass = report.get("object_mass_kg")
        if isinstance(mass, bool) or not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass <= 0:
            raise ValueError("invalid trial mass")
        control = "open_control" if report.get("open_control") else "close"
        repeat = report.get("repeat")
        if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 0:
            raise ValueError("each trial requires a nonnegative repeat index")
        key = (float(mass), control, repeat)
        if key in groups:
            raise ValueError("duplicate component trial")
        groups[key] = evaluate(report)
        raw_report = dict(report)
        raw_report.pop("repeat", None)
        report_hashes.append({"mass_kg": float(mass), "control": control, "repeat": repeat,
                              "sha256": canonical_report_hash(raw_report)})
    masses = sorted({key[0] for key in groups})
    results = []
    for mass in masses:
        positive = [groups[(mass, "close", repeat)] for repeat in range(minimum_repeats)]
        controls = [groups[(mass, "open_control", repeat)] for repeat in range(minimum_repeats)]
        results.append({"mass_kg": mass,
                        "validated": all(item["grasp_pass"] for item in positive)
                        and all(item["open_control_pass"] for item in controls),
                        "close_trials": positive, "open_controls": controls})
    validated = [item["mass_kg"] for item in results if item["validated"]]
    return {"validated": bool(validated), "tier": "E7" if validated else None,
            "validation_status": "physical_validated" if validated else "physical_rejected",
            "domain": "simulation", "subject": "ur5_robotiq_standalone",
            "scope": "standalone_fixed_base_grasp_lift_transfer_release",
            "validated_masses_kg": validated, "mass_results": results,
            "source_identity": {"runner_sha256": source_identity[0],
                                 "assembly_helper_sha256": source_identity[1],
                                 "source_layers": [{"path": path, "sha256": digest}
                                                    for path, digest in source_identity[2]]},
            "report_hashes": report_hashes,
            "limitations": ["Discrete tested masses only; no rated payload or maximum capacity",
                            "No mobile host, wind, collision safety or hardware certification"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    results = {}
    passed = True
    for path in args.reports:
        try:
            result = evaluate(json.loads(path.read_text()))
            passed &= result["grasp_pass"] or result["open_control_pass"]
        except (ValueError, TypeError, KeyError, OSError) as error:
            result = {"invalid_report": str(error)}
            passed = False
        results[str(path)] = result
    print(json.dumps(results, indent=2, allow_nan=False))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
