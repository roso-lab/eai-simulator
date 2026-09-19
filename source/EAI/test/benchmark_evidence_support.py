"""Synthetic, explicitly frozen evidence for pure importer and protocol tests."""

from datetime import datetime, timezone

from demo.benchmark.provenance import (
    PROVENANCE_SCHEMA, canonical_sha256, execution_code_provenance, file_sha256, runtime_options,
)
from EAI.scenario_spec.benchmark_physics import BENCHMARK_PHYSICS_CONTRACT


def frozen_attempt(tmp_path, *, scenario_sha256="a" * 64, options=None,
                   tier="navigation_execution_validated"):
    asset_path = tmp_path / "evidence_asset.usda"
    asset_path.write_text('#usda 1.0\ndef Xform "World" {}\n', encoding="utf-8")
    timestamp = datetime.now(timezone.utc).isoformat()
    options = runtime_options(**(options or {}))
    request = {"schema": PROVENANCE_SCHEMA, "scenario_sha256": scenario_sha256,
               "code_provenance": execution_code_provenance(), "options": options}
    request.update(sha256=canonical_sha256(request), captured_at=timestamp)
    runtime = {
        "schema": PROVENANCE_SCHEMA, "complete": True, "missing_dependencies": [],
        "files": {str(asset_path): file_sha256(asset_path)}, "usd_roots": [str(asset_path)],
        "effective": {"device": "cpu", "physics_dt_s": 0.005, "control_dt_s": 0.02,
                      "render_period_s": options["render_period_s"] or 0.02},
    }
    runtime.update(sha256=canonical_sha256(runtime), captured_at=timestamp)
    report = {
        "status": "executed", "control_dt_s": 0.02,
        "rendering": {"physics_dt_s": 0.005, "actual_period_s": runtime["effective"]["render_period_s"]},
        "benchmark_mass_audit": {"contract": dict(BENCHMARK_PHYSICS_CONTRACT),
                                 "validated": True, "arms": {}, "violations": []},
        "execution_validation": {"tier": tier, "validated": True},
        "execution_provenance": {"schema": PROVENANCE_SCHEMA, "validated": True, "violations": [],
                                 "request": request, "runtime": runtime},
    }
    return {
        "status": "completed", "outcome": {"returncode": 0}, "attempted_at": timestamp,
        "scenario_sha256": scenario_sha256,
        "config": {"request": request, "code_provenance": request["code_provenance"],
                   "transport_mode": options["transport_mode"]},
        "report_body": report, "report_sha256": canonical_sha256(report),
    }


def configuration_attempt(tmp_path, *, skill="navigate", attachments=()):
    import json
    from tools.scenario_generation.robot_dossier import robot_dossier, dossier_snapshot_hash

    robot_id = "simple_car_1"
    dossier = robot_dossier("simple_car", attachments=attachments)
    scenario = {
        "version": 2, "task_name": "configuration_fixture", "scene_key": "factory", "seed": 4,
        "robots": [{"id": robot_id, "type": "simple_car",
                    "controller": {"mode": "default", "cfg": "SIMPLE_CAR_PLANAR_CFG"},
                    "attachments": [{"type": item, "controller": None} for item in attachments],
                    "spawn_pose": {"position": [0.0, 0.0, 0.34], "rotation": [1.0, 0.0, 0.0, 0.0]}}],
        "composition": {"entities": [], "targets": [{"id": "goal", "position": [1.0, 0.0, 0.0], "radius_m": 0.5}]},
        "phase_graph": {"nodes": [{"id": "phase", "skill_key": skill, "preferred_agent": robot_id,
                                   "agents": None, "target_ref": "goal", "object_ref": None,
                                   "prerequisites": [], "timeout_s": 30.0,
                                   "duration_s": 1.0 if skill == "wait" else None, "resources": []}]},
    }
    if skill == "wait":
        scenario["robots"][0]["spawn_pose"]["position"] = [1.0, 0.0, 0.34]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario))
    attempt = frozen_attempt(tmp_path, scenario_sha256=file_sha256(scenario_path), options={"steps": 51})
    attempt["robot_dossier_snapshot_sha256"] = dossier_snapshot_hash(dossier)
    report = attempt["report_body"]
    report.update(steps_taken=51, scheduling={"states": {"phase": {
        "status": "succeeded", "started_at_s": 0.0, "finished_at_s": 1.0}}},
        events=[{"step": 50, "event": "node_complete", "node": "phase", "agent": robot_id,
                 "position": [1.0, 0.0], "confirmed_after_sim_step": True,
                 "arrival_predicate": {"fn": "robot_visited_zone", "args": [robot_id, "goal"]}}],
        pose_trajectory={"frame": "world", "position_unit": "m", "samples": [
            {"step": 0, "positions": {robot_id: [0.0, 0.0]}},
            {"step": 50, "positions": {robot_id: [1.0, 0.0]}},
            {"step": 51, "positions": {robot_id: [1.01, 0.0]}}]},
        observation_store={"observations": []})
    if skill == "wait":
        for sample in report["pose_trajectory"]["samples"]:
            sample["positions"][robot_id] = [1.0, 0.0]
        for index, timestamp in enumerate((0.1, 0.6, 1.0)):
            observation_id = f"phase:frame:{index}"
            report["events"].append({"event": "coverage_observation", "node": "phase", "target_ref": "goal",
                                     "step": round(timestamp / report["control_dt_s"]) + 1,
                                     "agent": robot_id, "observation_id": observation_id,
                                     "observation_source": "live_isaac_rgb_frame"})
            report["observation_store"]["observations"].append({
                "observation_id": observation_id, "producer": robot_id, "sensor": "camera",
                "target_ref": "goal", "timestamp_s": timestamp,
                "sensor_config": {"node": "phase", "frame_shape": [2, 3, 4], "frame_dtype": "uint8", "frame_sha256": "c" * 64},
                "target_estimate": {"kind": "sensor_pose_only", "position_m": [1.0, 0.0, 0.34]},
                "quality": {"frame_acquired": True, "valid_pixel_count": 24},
            })
    attempt["report_sha256"] = canonical_sha256(report)
    attempt_path = tmp_path / "attempt.json"
    attempt_path.write_text(json.dumps(attempt))
    return scenario_path, attempt_path
