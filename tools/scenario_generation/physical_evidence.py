"""Independent, fail-closed verification of bounded physical calibration traces."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

PHASES = ("close", "lift", "transfer", "hold", "release")


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("measurements must be finite numbers")
    return float(value)


def _vector(value, length):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"measurement vector must have {length} entries")
    return [_number(item) for item in value]


def verify_trial(trial, dt):
    samples = trial.get("samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise ValueError("trial requires a complete sampled trace")
    initial = _vector(trial.get("initial_object_position_m"), 3)
    last_phase = 0
    phases = {phase: [] for phase in PHASES}
    for index, sample in enumerate(samples):
        if abs(_number(sample.get("time_s")) - (index + 1) * dt) > dt * 1e-4:
            raise ValueError("trace timestamps must cover every physics step")
        phase = sample.get("phase")
        if phase not in PHASES or PHASES.index(phase) < last_phase:
            raise ValueError("trace phases must be complete and ordered")
        last_phase = PHASES.index(phase)
        for field in ("object_position_m", "stator_position_m", "object_velocity_m_s"):
            _vector(sample.get(field), 3)
        forces = _vector(sample.get("jaw_contact_force_N"), 2)
        support = _number(sample.get("support_contact_force_N"))
        if min(forces) < 0 or support < 0:
            raise ValueError("contact force magnitudes cannot be negative")
        _number(sample.get("gripper_joint_rad"))
        if _number(sample.get("maximum_penetration_m")) < 0:
            raise ValueError("penetration magnitude cannot be negative")
        phases[phase].append(sample)
    if any(len(group) * dt < 1.0 for group in phases.values()):
        raise ValueError("each phase requires at least one second of measurements")
    hold = phases["hold"]
    bilateral = [min(sample["jaw_contact_force_N"]) > 0.01 for sample in hold]
    contact_fraction = sum(bilateral) / len(hold)
    minimum_lift = min(sample["object_position_m"][2] - initial[2] for sample in hold)
    minimum_transfer = min(sample["object_position_m"][1] - initial[1] for sample in hold)
    offsets = [[sample["object_position_m"][axis] - sample["stator_position_m"][axis]
                for axis in range(3)] for sample in hold]
    slip = max(math.dist(offset, offsets[0]) for offset in offsets)
    release = phases["release"][-max(1, int(0.5 / dt)):]
    release_drop = hold[-1]["object_position_m"][2] - release[-1]["object_position_m"][2]
    release_clear = all(max(sample["jaw_contact_force_N"]) < 0.01 for sample in release)
    settled = all(math.dist(sample["object_velocity_m_s"], [0, 0, 0]) < 0.05 for sample in release)
    checks = {
        "penetration": max(sample["maximum_penetration_m"] for sample in samples) <= 0.005,
        "lift": minimum_lift >= 0.05,
        "transfer": minimum_transfer >= 0.05,
        "bilateral_contact": contact_fraction >= 0.95,
        "no_external_support": max(sample["support_contact_force_N"] for sample in hold) < 0.01,
        "retention": slip <= 0.015,
        "release": release_drop >= 0.05 and release_clear and settled,
    }
    return {"passed": all(checks.values()), "checks": checks,
            "minimum_lift_m": minimum_lift, "minimum_transfer_m": minimum_transfer,
            "hold_contact_fraction": contact_fraction, "hold_slip_m": slip,
            "release_drop_m": release_drop,
            "maximum_penetration_m": max(sample["maximum_penetration_m"] for sample in samples),
            "hold_jaw_force_range_N": [min(min(sample["jaw_contact_force_N"]) for sample in hold),
                                       max(max(sample["jaw_contact_force_N"]) for sample in hold)]}


def verify_calibration(report, *, expected_report_sha256, expected_runner_sha256):
    """Recompute results; a caller-provided 'physical_validated' flag has no authority.

Hashes bind artifacts, not authorship. The caller must obtain them from its
trusted local run ledger rather than accept hashes supplied by a candidate.
"""
    if content_hash(report) != expected_report_sha256:
        raise ValueError("physical report hash does not match frozen report")
    if report.get("runner_sha256") != expected_runner_sha256:
        raise ValueError("physical runner does not match frozen implementation")
    if report.get("schema_id") != "eai.physical_calibration" or report.get("schema_version") != "1.0":
        raise ValueError("unsupported physical report schema")
    if report.get("experiment") != "z1_prepositioned_grasp" or report.get("subject") != "z1_standalone_unscaled":
        raise ValueError("unsupported physical experiment/subject")
    if report.get("domain") != "simulation":
        raise ValueError("this verifier accepts simulation evidence only")
    authored = report.get("authored_arm_inertials")
    runtime_masses = report.get("arm_runtime_masses_kg", {})
    runtime_inertias = report.get("arm_runtime_inertias_kg_m2", {})
    if not isinstance(authored, dict) or not authored:
        raise ValueError("authored arm inertials are required")
    if set(runtime_masses) - set(authored) != {"world"} or report.get("fixed_dummy_bodies") != ["world"]:
        raise ValueError("standalone fixture permits only the fixed world dummy body")
    for body, inertial in authored.items():
        mass = _number(inertial.get("mass_kg"))
        actual_mass = _number(runtime_masses.get(body))
        expected_tensor = _vector(inertial.get("inertia_kg_m2"), 9)
        actual_tensor = _vector(runtime_inertias.get(body), 9)
        if mass <= 0 or not math.isclose(mass, actual_mass, rel_tol=1e-4, abs_tol=1e-6):
            raise ValueError("runtime arm mass differs from unscaled source")
        if any(not math.isclose(expected, actual, rel_tol=1e-4, abs_tol=1e-7)
               for expected, actual in zip(expected_tensor, actual_tensor)):
            raise ValueError("runtime arm inertia differs from unscaled source")
    protocol = report.get("protocol", {})
    for field, required in {"scripted_payload_motion": False, "fixed_attachment": False,
                            "payload_gravity_enabled": True, "payload_collision_enabled": True,
                            "position_ik": True, "orientation_ik": True}.items():
        if protocol.get(field) is not required:
            raise ValueError(f"physical protocol requires {field}={required}")
    if protocol.get("drive_type") != "force" or _number(protocol.get("mass_scale")) != 1.0:
        raise ValueError("physical protocol requires force drives and unscaled masses")
    if _number(protocol.get("gravity_m_s2")) != 9.81:
        raise ValueError("physical protocol requires Earth gravity")
    dt = _number(protocol.get("dt_s"))
    if not 0 < dt <= 1 / 60:
        raise ValueError("physical timestep must be in (0, 1/60]")
    repeats = protocol.get("repeats")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 3:
        raise ValueError("physical evidence requires at least three repeats per mass and control")
    layers = report.get("source_asset_layers")
    if not isinstance(layers, list) or not layers:
        raise ValueError("physical evidence requires asset layer provenance")
    for layer in layers:
        digest = layer.get("sha256", "")
        if not layer.get("path") or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid asset layer provenance")
    evaluated = {}
    for trial in report.get("trials", []):
        mass = _number(trial.get("mass_kg"))
        actual_mass = _number(trial.get("runtime_mass_kg"))
        if not math.isclose(actual_mass, mass, rel_tol=1e-5, abs_tol=1e-7):
            raise ValueError("runtime payload mass differs from requested mass")
        repeat = trial.get("repeat")
        control = trial.get("control")
        if mass <= 0 or isinstance(repeat, bool) or not isinstance(repeat, int) or not 0 <= repeat < repeats:
            raise ValueError("invalid mass/repeat")
        if control not in {"close", "open_control"}:
            raise ValueError("trial needs close/open_control classification")
        key = (mass, repeat, control)
        if key in evaluated:
            raise ValueError("duplicate calibration trial")
        evaluated[key] = verify_trial(trial, dt)
    masses = sorted({key[0] for key in evaluated})
    if not masses:
        raise ValueError("no calibration trials")
    summaries = []
    for mass in masses:
        keys = [(mass, repeat, control) for repeat in range(repeats) for control in ("close", "open_control")]
        if any(key not in evaluated for key in keys):
            raise ValueError("missing paired negative control or repeat")
        positive = [evaluated[(mass, repeat, "close")] for repeat in range(repeats)]
        controls = [evaluated[(mass, repeat, "open_control")] for repeat in range(repeats)]
        controls_fail_motion = all(not (item["checks"]["lift"] and item["checks"]["transfer"]
                                       and item["checks"]["retention"]) for item in controls)
        summaries.append({"mass_kg": mass,
                          "validated": all(item["passed"] for item in positive) and controls_fail_motion,
                          "negative_control_rejects_motion": controls_fail_motion,
                          "close_trials": positive, "open_controls": controls})
    passed = [summary["mass_kg"] for summary in summaries if summary["validated"]]
    return {"validated": bool(passed), "tier": "E7" if passed else None,
            "validation_status": "physical_validated" if passed else "physical_rejected",
            "domain": "simulation", "subject": report["subject"],
            "scope": "standalone_prepositioned_grasp_lift_transfer_release",
            "validated_masses_kg": passed, "mass_results": summaries,
            "report_sha256": expected_report_sha256, "runner_sha256": expected_runner_sha256,
            "limitations": ["Discrete tested masses only; not a rated payload or maximum capacity",
                            "Cannot promote a mobile robot, scene DAG, or team to E7",
                            "No measured hardware, swept collision margin, or wind tolerance"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--report-sha256", required=True)
    parser.add_argument("--runner-sha256", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = json.loads(Path(args.report).read_text())
    result = verify_calibration(report, expected_report_sha256=args.report_sha256,
                                expected_runner_sha256=args.runner_sha256)
    Path(args.out).write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "mass_results"}))
    return 0 if result["validated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
