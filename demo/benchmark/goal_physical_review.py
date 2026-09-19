"""Audit native configuration diagnostics; full task/score gates stay closed."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from .goal_physical_probe import load_probe
from .provenance import canonical_sha256, file_sha256


def _vector(value, length):
    if (not isinstance(value, list) or len(value) != length
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError("native state must have finite numeric coordinates")
    return value


def _rows(rows, expected, label):
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError(f"{label} inventory mismatch")
    actual = {r["runtime_id"]: r for r in rows}
    if len(actual) != len(rows) or set(actual) != set(expected):
        raise ValueError(f"{label} independent identity mismatch")
    return actual


def audit_physical_probe(root):
    root = Path(root).absolute()
    request, deployment, _ = load_probe(root)
    report = json.loads((root / "physical_report.json").read_text())
    process = json.loads((root / "process_result.json").read_text())
    launch = json.loads((root / "launch.json").read_text())
    if (process.get("returncode") != 0 or process.get("timed_out") is not False
            or report.get("status") != "diagnostic_completed"):
        raise ValueError("native process or diagnostic did not complete")
    for payload in (process, report):
        if payload.get("run_id") != request["run_id"] or payload.get("request_sha256") != canonical_sha256(request):
            raise ValueError("native evidence does not belong to this launch")
    if (launch.get("request_sha256") != canonical_sha256(request) or launch.get("run_id") != request["run_id"]
            or launch.get("command") != [sys.executable, "-m", "demo.benchmark.goal_physical_probe", "--worker", "--output-root", str(root)]
            or process.get("launch_sha256") != file_sha256(root / "launch.json")):
        raise ValueError("native parent launch differs from the frozen worker request")
    if process.get("physical_report_sha256") != file_sha256(root / "physical_report.json"):
        raise ValueError("physical report differs from parent process archive")
    if report.get("deployment_sha256") != deployment["sha256"] or report.get("scene_key") != request["scene_key"]:
        raise ValueError("native deployment identity mismatch")
    for key in ("physical_task_execution_validated", "business_outcomes_validated", "scoreable"):
        if report.get(key) is not False:
            raise ValueError("configuration diagnostic cannot claim full physical/business task execution")
    if report.get("actual_device") != request["effective_device"]:
        raise ValueError("actual physics device differs from frozen device resolution")
    sources = report.get("runtime_source_files")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("native report requires loaded source/layer fingerprints")
    manifest_path = root / "runtime_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if (process.get("runtime_manifest_sha256") != file_sha256(manifest_path)
            or report.get("runtime_manifest_sha256") != file_sha256(manifest_path)
            or manifest.get("run_id") != request["run_id"]
            or manifest.get("request_sha256") != canonical_sha256(request)
            or canonical_sha256(manifest.get("source_files")) != canonical_sha256(sources)):
        raise ValueError("native source inventory differs from pre-control manifest")
    stage = manifest.get("stage", {})
    layers = stage.get("used_file_layers")
    from .goal_physical_probe import ROOT, _LOCAL_SOURCES
    required_code = {str(ROOT / "demo/benchmark" / name) for name in _LOCAL_SOURCES} | {str(ROOT / "simulator.py")}
    if (not required_code <= set(sources) or not isinstance(layers, list) or not layers
            or not set(layers) <= set(sources) or stage.get("up_axis") != "Z"
            or stage.get("meters_per_unit") != 1.0 or not stage.get("root_layer_identifier")):
        raise ValueError("native code/stage manifest coverage incomplete")
    for path, digest in sources.items():
        if not Path(path).is_absolute() or file_sha256(Path(path)) != digest:
            raise ValueError(f"native runtime source changed: {path}")
    robots = {r["runtime_id"]: r for r in deployment["robot_instances"]}
    initial = _rows(report.get("initial_robots"), robots, "initial")
    final = _rows(report.get("final_robots"), robots, "final")
    if report.get("possible_agents") != list(robots):
        raise ValueError("native controller inventory differs from full deployment")
    for snapshot in (initial, final):
        state_ids = set()
        for rid, row in snapshot.items():
            if row.get("status") != "native_articulation_read_back":
                raise ValueError("native articulation missing")
            paths = row.get("actual_articulation_paths")
            root_path = robots[rid]["prim_path"]
            if (not isinstance(paths, list) or len(paths) != 1 or not isinstance(paths[0], str)
                    or not (paths[0] == root_path or paths[0].startswith(root_path + "/"))):
                raise ValueError("native articulation aliases a different member or prim")
            if row.get("public_id") != robots[rid]["public_id"]:
                raise ValueError("native/public identity mismatch")
            state_id = row.get("controller_state_identity")
            if not isinstance(state_id, str) or not state_id or state_id in state_ids:
                raise ValueError("native controller state is shared or missing")
            state_ids.add(state_id)
            _vector(row.get("root_state_w"), 13)
            masses = row.get("body_masses_kg")
            if not isinstance(masses, list) or not masses or len(masses) != len(row.get("body_names", [])):
                raise ValueError("native body mass inventory mismatch")
            _vector(masses, len(masses))
            if any(m < 0 for m in masses) or sum(masses) <= 0:
                raise ValueError("invalid native body masses")
    samples = report.get("samples")
    if not isinstance(samples, list) or len(samples) != request["steps"] or report.get("native_step_count") != len(samples):
        raise ValueError("native control-step sequence incomplete")
    timing = report.get("timing", {})
    initial_time = timing.get("initial_simulation_time_s")
    physics_dt, step_dt, decimation = (timing.get(k) for k in ("physics_dt_s", "control_step_dt_s", "control_decimation"))
    if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (initial_time,physics_dt,step_dt))
            or initial_time < 0 or physics_dt <= 0 or step_dt <= 0 or type(decimation) is not int or decimation <= 0
            or not math.isclose(step_dt,physics_dt*decimation,rel_tol=1e-8,abs_tol=1e-10)):
        raise ValueError("native clock configuration invalid")
    previous = initial_time
    for index, sample in enumerate(samples):
        if type(sample.get("step")) is not int or sample["step"] != index:
            raise ValueError("native control-step ordering invalid")
        now = sample.get("time_s")
        if (type(now) not in (int, float) or not math.isfinite(now) or now < 0 or now <= previous
                or not math.isclose(now,initial_time+(index+1)*step_dt,rel_tol=1e-6,abs_tol=1e-6)):
            raise ValueError("native simulation time does not advance")
        previous = now
        states, commands = sample.get("robot_root_state_w"), sample.get("commands")
        if not isinstance(states, dict) or not isinstance(commands, dict) or set(states) != set(robots) or set(commands) != set(robots):
            raise ValueError("native step inventory incomplete")
        for rid, row in robots.items():
            _vector(states[rid], 13)
            _vector(commands[rid], 3)
            expected = initial[rid]["root_state_w"][:3] if row["public_declaration"]["mobility"] == "aerial" else [0.,0.,0.]
            if commands[rid] != expected:
                raise ValueError("native diagnostic did not hold its declared initial command")
    health = []
    for rid, row in robots.items():
        if final[rid]["root_state_w"] != samples[-1]["robot_root_state_w"][rid]:
            raise ValueError("final native state differs from the last control step")
        poses = [initial[rid]["root_state_w"]] + [sample["robot_root_state_w"][rid] for sample in samples]
        start = poses[0]
        planar = max(math.dist(p[:2], start[:2]) for p in poses)
        vertical = max(abs(p[2]-start[2]) for p in poses)
        tilt = 0.
        for p in poses:
            w,x,y,z = p[3:7]
            norm = math.sqrt(w*w+x*x+y*y+z*z)
            if abs(norm-1.) > .01:
                raise ValueError("native orientation not normalized")
            tilt = max(tilt, math.acos(max(-1., min(1., 1.-2.*(x*x+y*y)/(norm*norm)))))
        health.append({"runtime_id": rid, "max_planar_drift_m": planar, "max_vertical_change_m": vertical,
                       "max_tilt_rad": tilt, "bounded_settle": planar <= .35 and vertical <= .35 and tilt <= .35,
                       "limits": {"planar_m": .35, "vertical_m": .35, "tilt_rad": .35},
                       "support_contact_validated": False})
    captures = report.get("captures", [])
    if not isinstance(captures, list) or (request["gui"] and len(captures) < 2):
        raise ValueError("GUI diagnostic lacks initial/final viewport captures")
    for capture in captures:
        if file_sha256(Path(capture["path"])) != capture["sha256"]:
            raise ValueError("viewport image changed")
    result = {"schema": "eai.benchmark.goal_physical_review", "version": 1, "scene_key": request["scene_key"],
        "run_id": request["run_id"], "physical_report_sha256": file_sha256(root / "physical_report.json"),
        "native_instance_and_step_readback_validated": True, "robot_count": len(robots),
        "step_count": len(samples), "robot_health": health,
        "physics_substep_count": len(samples)*decimation, "timing": timing,
        "requested_device": request["requested_device"], "actual_device": report["actual_device"],
        "device_policy": request["device_policy"],
        "all_robots_bounded_settle": all(row["bounded_settle"] for row in health),
        "complete_profile_configuration_validated": False, "physical_task_execution_validated": False,
        "business_outcomes_validated": False, "scoreable": False,
        "limits": ["configuration_diagnostic_not_navigation_or_manipulation_task",
                   "trusted_local_process_artifacts_not_remote_attestation",
                   "profile_envelope_payload_interfaces_and_support_require_separate_evidence"]}
    return {**result, "sha256": canonical_sha256(result)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(audit_physical_probe(args.output_root), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
