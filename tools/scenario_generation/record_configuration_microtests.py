"""Derive scoped configuration checks from current, verifiable execution attempts."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.scenario_generation import common
from tools.scenario_generation import configuration_microtest
from tools.scenario_generation.configuration_microtest import SCHEMA, PROTOCOLS, canonical_sha256, validate_microtest
from demo.benchmark.provenance import file_sha256


def task_scope(data, node):
    world = {key: value for key, value in data.items()
             if key not in {"robots", "phase_graph", "task_name", "benchmark_task_id"}}
    targets = {item["id"]: item for item in data["composition"]["targets"]}
    target = targets.get(node.get("target_ref"))
    if target is None:
        raise ValueError("configuration checks currently require a declared target region")
    subject_id = node.get("observation_ref") or node["target_ref"]
    subjects = {**targets, **{item["id"]: item for item in data["composition"]["entities"]}}
    scope = {"scene_key": data["scene_key"], "world_sha256": canonical_sha256(world),
            "primitive": node["skill_key"], "target": deepcopy(target),
            "subject": deepcopy(subjects.get(subject_id)), "duration_s": node.get("duration_s")}
    if node["skill_key"] == "aerial_scan":
        from demo.benchmark.control import scan_hover_goal
        scope["scan_height_m"] = scan_hover_goal(
            target["position"], scan_height_m=node.get("scan_height_m"))[2]
        scope["scan_yaw_rad"] = node.get("scan_yaw_rad")
    if node.get("wait_yaw_rad") is not None:
        scope["wait_yaw_rad"] = node["wait_yaw_rad"]
    if (data.get("benchmark_task_id") == "F-INS-01"
            and node["id"] in {"aerial_view", "ground_view"}):
        scope["observation_protocol"] = {
            "target_class": "printed_inventory_marker",
            "dictionary": "DICT_4X4_50",
            "localization_frame": "world",
            "fusion": "coordinate_median_per_view_and_decoded_id",
        }
    return scope


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("execution measurements require finite numbers")
    return float(value)


def _navigation_metrics(report, node, target):
    agent = node["preferred_agent"]
    events = [event for event in report["events"]
              if event.get("event") == "node_complete" and event.get("node") == node["id"]]
    if len(events) != 1:
        raise ValueError("navigation requires one completion event")
    event = events[0]
    expected = {"fn": "robot_visited_zone", "args": [agent, target["id"]]}
    if (event.get("agent") != agent or event.get("arrival_predicate") != expected
            or event.get("confirmed_after_sim_step") is not True):
        raise ValueError("navigation requires a measured zone-visit event")
    trace = report.get("pose_trajectory", {})
    if trace.get("frame") != "world" or trace.get("position_unit") != "m":
        raise ValueError("navigation requires a world/metre pose trace")
    positions = {}
    last_step = -1
    for sample in trace.get("samples", []):
        step = sample["step"]
        if isinstance(step, bool) or not isinstance(step, int) or step < last_step:
            raise ValueError("pose trace steps must be ordered integers")
        position = tuple(_finite_number(value) for value in sample["positions"][agent][:2])
        if len(position) != 2 or (step in positions and positions[step] != position):
            raise ValueError("pose trace has inconsistent coordinates")
        positions[step] = position
        last_step = step
    arrival_step = event["step"]
    if 0 not in positions or report["steps_taken"] not in positions or arrival_step not in positions:
        raise ValueError("pose trace is missing initial, arrival or final samples")
    if positions[arrival_step] != tuple(event["position"]):
        raise ValueError("arrival event differs from its pose sample")
    prefix = [position for step, position in positions.items() if step <= arrival_step]
    metrics = {
        "sampled_prefix_path_m": sum(math.dist(first, second) for first, second in zip(prefix, prefix[1:])),
        "arrival_time_s": arrival_step * report["control_dt_s"],
        "arrival_error_m": math.dist(positions[arrival_step], target["position"][:2]),
        "arrival_step": arrival_step,
    }
    return metrics, {"arrival_radius_m": target["radius_m"]}


def _camera_metrics(report, node, scope):
    from demo.benchmark.camera_validation import capture_window_metrics

    return capture_window_metrics(
        report, node, scope, max_gap_bound_s=PROTOCOLS["factory_camera_capture_v2"]["max_gap_bound_s"])


def _inventory_detection_metrics(evaluation, node):
    frames = [item for item in evaluation["frames"]
              if item["observation_id"].startswith(f"{node['id']}:frame:")]
    fused = [item for item in evaluation["fused_views"] if item["view_node"] == node["id"]]
    if len(fused) != 1 or not frames:
        raise ValueError("inventory configuration check requires one fused view and its source frames")
    result = fused[0]
    if not result["identification_validated"] or not result["localization_validated"]:
        raise ValueError("inventory view did not pass identification and localization")
    identified = sum(item["identification_validated"] for item in frames)
    return {
        "frame_count": len(frames),
        "identified_frame_count": identified,
        "fused_localization_error_m": result["localization_error_m"],
        "observed_marker_id": result["marker_id"],
    }, {"localization_error_bound_m": evaluation["localization_tolerance_m"]}


def derive_records(scenario_path, attempt_path):
    from EAI.scenario_spec import load_scenario
    from tools.scenario_generation.capability_profile import profile_for_dossier
    from tools.scenario_generation.robot_dossier import (
        all_dossiers_snapshot_hash, dossier_snapshot_hash, robot_dossier,
    )
    from tools.scenario_generation.runtime_evidence import import_execution_attempt

    scenario_path, attempt_path = Path(scenario_path).resolve(), Path(attempt_path).resolve()
    data = load_scenario(scenario_path)
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    dossiers = {robot["id"]: robot_dossier(robot["type"], attachments=[item["type"] for item in robot["attachments"]])
                for robot in data["robots"]}
    for robot in data["robots"]:
        if robot["controller"]["cfg"] != dossiers[robot["id"]]["controller"]["default_cfg"]:
            raise ValueError("micro-test requires an explicit supported configuration controller")
    snapshots = {all_dossiers_snapshot_hash(), dossier_snapshot_hash(dossiers)}
    distinct = {dossier_snapshot_hash(dossier) for dossier in dossiers.values()}
    if len(distinct) == 1:
        snapshots.update(distinct)
    snapshot = attempt.get("robot_dossier_snapshot_sha256")
    if snapshot not in snapshots:
        raise ValueError("execution dossier does not match the current selected configurations")
    imported = import_execution_attempt(attempt, scenario_sha256=file_sha256(scenario_path),
                                        robot_dossier_snapshot_sha256=snapshot)
    report = attempt["report_body"]
    if report["execution_validation"]["tier"] != "navigation_execution_validated":
        raise ValueError("configuration checks require navigation-only execution evidence")
    if data["scene_key"] != "factory" or data.get("fault_events"):
        raise ValueError("current micro-test protocols require Factory without fault interventions")
    runtime = report["execution_provenance"]["runtime"]
    request = report["execution_provenance"]["request"]
    inventory_evaluation = None
    resolved_data = data
    if report.get("inventory_dispatch") is not None:
        from demo.factory_benchmark.inspect_dispatch import evaluate_dispatch
        from demo.factory_benchmark.inspect_inventory import evaluate_inventory
        from demo.factory_benchmark.inventory_dispatch import DISPATCH_NODE, resolved_scenario

        dispatch_evaluation = evaluate_dispatch(report, scenario=data)
        if not dispatch_evaluation["decision_replay_validated"]:
            raise ValueError("dynamic dispatch decision did not replay")
        inventory_evaluation = evaluate_inventory(report, scenario=data)
        decision = report["inventory_dispatch"]["decision"]
        resolved_data = resolved_scenario(data, decision["target_ref"])
    records = []
    for node in data["phase_graph"]["nodes"]:
        if node["skill_key"] not in {"navigate", "wait", "aerial_scan"}:
            raise ValueError("configuration checks cannot certify manipulation primitives")
        if report["scheduling"]["states"][node["id"]]["status"] != "succeeded":
            raise ValueError("configuration check node did not succeed")
        effective_node = node
        if report.get("inventory_dispatch") is not None and node["id"] == DISPATCH_NODE:
            effective_node = next(item for item in resolved_data["phase_graph"]["nodes"]
                                  if item["id"] == node["id"])
        scope = task_scope(resolved_data, effective_node)
        if node["skill_key"] == "navigate":
            protocol = "factory_navigation_visit_v2"
            metrics, acceptance = _navigation_metrics(report, effective_node, scope["target"])
        elif inventory_evaluation is not None and node["id"] in {"aerial_view", "ground_view"}:
            protocol = "factory_inventory_detection_v2"
            metrics, acceptance = _inventory_detection_metrics(inventory_evaluation, node)
        else:
            protocol = "factory_camera_capture_v2"
            metrics, acceptance = _camera_metrics(report, node, scope)
        dossier = dossiers[node["preferred_agent"]]
        condition = {
            "attachments": dossier["selected_attachments"], "scope": scope,
            "options": request["options"], "effective": runtime["effective"],
            "runtime_files_sha256": canonical_sha256(runtime["files"]),
            "code_sha256": canonical_sha256(request["code_provenance"]),
        }
        record = {
            "schema": SCHEMA, "version": 2, "status": "passed", "evidence_tier": imported["tier"],
            "configuration_id": profile_for_dossier(dossier)["identity"]["configuration_id"],
            "robot_key": dossier["robot_key"], "producer_id": node["preferred_agent"], "node_id": node["id"],
            "protocol_id": protocol, "scenario_sha256": file_sha256(scenario_path),
            "report_sha256": attempt["report_sha256"], "attempted_at": attempt["attempted_at"],
            "condition": condition, "condition_sha256": canonical_sha256(condition),
            "scope": scope, "metrics": metrics, "acceptance": acceptance,
            "evidence": {"scenario_path": str(scenario_path), "attempt_path": str(attempt_path),
                         "attempt_sha256": file_sha256(attempt_path), "converter_sha256": file_sha256(__file__),
                         "protocol_source_sha256": file_sha256(configuration_microtest.__file__)},
            "limitations": [
                "Single scoped protocol check, not a normalized radar score or success probability",
                "Sampled prefix path is an episode-prefix lower bound, not a full-rate phase trajectory",
                ("Printed-marker detection is task-scoped, not generic object recognition"
                 if protocol == "factory_inventory_detection_v2" else
                 "Capture does not establish detection, visibility, localization, or native sensor fidelity"),
                "Recorded capture positions are robot-base proxies, not calibrated camera extrinsics",
                "Window pose checks cover sampled positions, not continuous containment",
                "New assignments still require geometry and execution validation",
                "No payload, grasp force, collision margin, wind or load-dynamics claim",
            ],
        }
        records.append(validate_microtest(record))
    return records


def verify_record_sources(record):
    evidence = record["evidence"]
    if (evidence["converter_sha256"] != file_sha256(__file__)
            or evidence["protocol_source_sha256"] != file_sha256(configuration_microtest.__file__)
            or evidence["attempt_sha256"] != file_sha256(evidence["attempt_path"])):
        raise ValueError("micro-test source evidence or converter has changed")
    candidates = derive_records(evidence["scenario_path"], evidence["attempt_path"])
    if not any(candidate == record for candidate in candidates):
        raise ValueError("micro-test differs from measurements derived from its source evidence")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    records, ledger = [], []
    seen = set()
    for path in args.attempt:
        digest = file_sha256(path)
        if digest in seen:
            raise ValueError("duplicate attempt cannot be counted as an independent run")
        seen.add(digest)
        entry = {"attempt_path": str(path.resolve()), "attempt_sha256": digest}
        try:
            derived = derive_records(args.scenario, path)
        except (ValueError, KeyError, OSError, TypeError) as error:
            entry.update(status="rejected", reason=str(error), record_count=0)
        else:
            records.extend(derived)
            entry.update(status="accepted", record_count=len(derived))
        ledger.append(entry)
    common.write_json(args.output, {"schema": "eai.configuration_microtest_bundle", "version": 2,
                                   "records": records, "attempts": ledger,
                                   "limitations": ["The supplied attempt list is not a preregistered failure denominator"]})
    print(json.dumps({"records": len(records), "attempts": ledger}, ensure_ascii=False))
    return int(any(entry["status"] == "rejected" for entry in ledger))


if __name__ == "__main__":
    raise SystemExit(main())
