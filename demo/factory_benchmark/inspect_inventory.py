"""Evaluate printed-label detections against independently loaded scene geometry."""

import argparse
import json
import math
from pathlib import Path

import numpy as np

from demo.benchmark.camera_capture import verify_archived_frame
from demo.benchmark.marker_detection import detect_markers, fuse_marker_views
from demo.benchmark.provenance import canonical_sha256, file_sha256, validate_execution_provenance
from tools.scenario_generation.common import write_json


def replay_marker_frames(report):
    observations = {item["observation_id"]: item for item in report["observation_store"]["observations"]}
    frames = report["inventory_detection"]["frames"]
    identifiers = [frame["source_observation_id"] for frame in frames]
    if len(set(identifiers)) != len(identifiers) or set(identifiers) != set(observations):
        raise ValueError("inventory detector must retain exactly one result for every captured frame")
    for frame in frames:
        config = observations[frame["source_observation_id"]]["sensor_config"]
        verify_archived_frame(config)
        result = detect_markers(np.load(config["archive_path"], allow_pickle=False), config["camera_calibration"])
        result["source_observation_id"] = frame["source_observation_id"]
        if canonical_sha256(result) != canonical_sha256(frame):
            raise ValueError("recorded inventory detection differs from pixel replay")
    return observations


def required_inventory_views(scenario):
    if scenario.get("scene_key") != "factory" or scenario.get("benchmark_task_id") != "F-INS-01":
        raise ValueError("inventory evaluation requires Factory F-INS-01")
    nodes = {node["id"]: node for node in scenario["phase_graph"]["nodes"]}
    return {node_id: f"{nodes[node_id]['preferred_agent']}/camera_optical_frame"
            for node_id in ("aerial_view", "ground_view")}


def replay_marker_views(report, observations):
    fused = fuse_marker_views(report["inventory_detection"]["frames"], list(observations.values()))
    if canonical_sha256(fused) != canonical_sha256(report["inventory_detection"].get("fused_view_estimates")):
        raise ValueError("recorded inventory view estimates differ from fixed image-only fusion")
    return fused


def summarize_view_evidence(results, required_views, localized_nodes=()):
    observed = {node: [] for node in required_views}
    for result in results:
        node = result["observation_id"].split(":frame:")[0]
        if node not in required_views or result["view"] != required_views[node]:
            raise ValueError("inventory frame is not from a declared inspection view")
        observed[node].append(result)
    missing = sorted(node for node, frames in observed.items() if not frames)
    localized = sorted(node for node in localized_nodes if observed.get(node))
    return {"required_views": required_views, "missing_view_nodes": missing,
            "all_views_observed": not missing, "localized_view_nodes": localized,
            "all_views_localized": not missing and set(localized) == set(required_views)}


def evaluate_inventory(report, *, scenario, localization_tolerance_m=0.1):
    from pxr import Usd, UsdGeom

    if not math.isfinite(localization_tolerance_m) or localization_tolerance_m <= 0:
        raise ValueError("localization tolerance must be finite and positive")
    observations = replay_marker_frames(report)
    variant = report["inventory_detection"]["world_variant"]
    options = report["execution_provenance"]["request"]["options"]
    if (variant["marker_id"] != options["inventory_label_id"]
            or variant["state"] != options["inventory_label_state"]):
        raise ValueError("inventory world does not match its frozen intervention")
    if file_sha256(variant["path"]) != variant["sha256"] or file_sha256(variant["base_path"]) != variant["base_sha256"]:
        raise ValueError("inventory scene source changed")
    stage = Usd.Stage.Open(variant["path"])
    from demo.factory_benchmark.label_geometry import validate_label_support
    if variant["version"] != 2:
        raise ValueError("inventory authoring version lacks whole-surface support validation")
    support = validate_label_support(Usd.Stage.Open(variant["base_path"]), stage, variant)
    ink = stage.GetPrimAtPath(variant["label_prim_path"] + "/Ink")
    center = None
    if variant["state"] != "absent":
        if not ink:
            raise ValueError("declared printed marker is absent from the scene")
        bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"]).ComputeWorldBound(ink).ComputeAlignedRange()
        center = ((np.array(bounds.GetMin()) + np.array(bounds.GetMax())) / 2).tolist()
        if math.dist(center, variant["world_center_m"]) > 1e-6:
            raise ValueError("label fixture position differs from composed scene geometry")
    elif ink:
        raise ValueError("absent condition still contains label geometry")
    results = []
    for frame in report["inventory_detection"]["frames"]:
        observation = observations[frame["source_observation_id"]]
        matches = [item for item in frame["detections"] if item["marker_id"] == variant["marker_id"]]
        errors = [math.dist(item["estimated_position_m"], center) for item in matches
                  if item["pose_valid"] and center is not None]
        correct = bool(matches) and len(matches) == len(frame["detections"])
        results.append({"observation_id": frame["source_observation_id"], "view": observation["frame_id"],
                        "detected_ids": [item["marker_id"] for item in frame["detections"]],
                        "identification_validated": correct,
                        "localization_errors_m": errors,
                        "localization_validated": correct and bool(errors) and len(errors) == len(matches)
                        and max(errors) <= localization_tolerance_m})
    views = sorted({item["view"] for item in results})
    fused = replay_marker_views(report, observations)
    fused_results, localized_nodes = [], []
    for estimate in fused:
        error = math.dist(estimate["estimated_position_m"], center) if center is not None else None
        correct = estimate["marker_id"] == variant["marker_id"] and all(
            frame["detected_ids"] in ([], [variant["marker_id"]]) for frame in results
            if frame["observation_id"].split(":frame:")[0] == estimate["view_node"])
        localized = correct and error is not None and error <= localization_tolerance_m
        fused_results.append({**estimate, "identification_validated": correct,
                              "localization_error_m": error, "localization_validated": localized})
        if localized:
            localized_nodes.append(estimate["view_node"])
    view_evidence = summarize_view_evidence(results, required_inventory_views(scenario), localized_nodes)
    return {"schema": "eai.factory_inventory_evaluation", "version": 2,
            "state": variant["state"], "expected_marker_id": variant["marker_id"],
            "ground_truth_center_m": center, "ground_truth_source": "evaluator_only_composed_USD_label_geometry",
            "localization_tolerance_m": localization_tolerance_m, "pixel_replay_validated": True,
            "surface_support_validation": support,
            "frames": results, "observed_views": views,
            "localized_views": sorted({item["view"] for item in fused_results if item["localization_validated"]}),
            "fused_views": fused_results,
            "localization_acceptance": "fixed per-view coordinate median, not best frame chosen with truth",
            **view_evidence,
            "negative_condition_observed": variant["state"] in {"covered", "absent"}
            and view_evidence["all_views_observed"] and not any(item["detected_ids"] for item in results),
            "business_task_validated": False,
            "limitations": ["Printed marker recognition; unlabelled contents are not identified",
                            "World localization uses simulator camera pose, not estimated robot localization",
                            "This checks image-derived detections but does not establish downstream decision use",
                            "A single scene/label is not recognition reliability or an occlusion robustness distribution"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--localization-tolerance-m", type=float, default=0.1)
    args = parser.parse_args(argv)
    attempt, report = json.loads(args.attempt.read_text()), json.loads(args.report.read_text())
    validate_execution_provenance(attempt, check_runtime_files=True)
    if canonical_sha256(report) != attempt["report_sha256"]:
        raise ValueError("inventory report differs from its execution attempt")
    if file_sha256(args.scenario) != attempt["scenario_sha256"]:
        raise ValueError("inventory scenario differs from its execution attempt")
    from EAI.scenario_spec import load_scenario
    result = evaluate_inventory(report, scenario=load_scenario(args.scenario),
                                localization_tolerance_m=args.localization_tolerance_m)
    result["sources"] = {"scenario_sha256": file_sha256(args.scenario), "report_sha256": file_sha256(args.report),
                         "attempt_sha256": file_sha256(args.attempt)}
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
