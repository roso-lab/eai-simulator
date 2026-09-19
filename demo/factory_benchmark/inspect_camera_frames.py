"""Evaluator-only projection checks for archived Factory inspection frames."""

import argparse
from itertools import product
import json
from pathlib import Path

from demo.benchmark.camera_capture import project_world_points, verify_archived_frame
from demo.benchmark.provenance import file_sha256
from demo.factory_benchmark.evaluator import load_fixture, validate_inspection_fixture
from demo.factory_benchmark.prim_bindings import audit_factory_prim_bindings
from tools.scenario_generation.common import write_json


def audit_frames(report, fixture, prim_audit):
    target = fixture["target"]
    binding = next(item for item in prim_audit["bindings"] if item["id"] == target["binding_id"])
    bounds = binding["world_bounds_m"]
    corners = list(product(*zip(bounds["min"], bounds["max"])))
    observations = report["observation_store"]["observations"]
    by_id = {item["observation_id"]: item for item in observations}
    results = []
    for view in fixture["views"]:
        identifiers = []
        for event in report["events"]:
            if event.get("node") != view["node_id"]:
                continue
            if event.get("event") == "aerial_scan":
                identifiers.extend(event.get("observation_ids", ()))
            elif event.get("event") == "coverage_observation":
                identifiers.append(event["observation_id"])
        for identifier in identifiers:
            observation = by_id[identifier]
            if observation["target_ref"] != target["subject_ref"]:
                raise ValueError("inspection frame has a different subject")
            config = observation["sensor_config"]
            verify_archived_frame(config)
            if not config.get("archive_path"):
                raise ValueError("inspection projection requires archived sensor pixels")
            calibration = config["camera_calibration"]
            projected = project_world_points([target["ground_truth_position_m"], *corners], calibration)
            results.append({"view_id": view["id"], "observation_id": identifier,
                            "camera_prim_path": calibration["camera_prim_path"],
                            "archive_path": config["archive_path"], "preview_path": config["preview_path"],
                            "projected_center_px": projected["pixels"][0],
                            "center_in_front": projected["in_front"][0],
                            "center_in_image": projected["in_image"][0],
                            "center_depth_m": projected["depth_m"][0],
                            "corners_in_image": sum(projected["in_image"][1:]),
                            "reference_time": calibration["reference_time"]})
    return {"schema": "eai.factory_inspection_projection_audit", "version": 1, "frames": results,
            "required_views": [view["id"] for view in fixture["views"]],
            "views_with_center_in_image": sorted({item["view_id"] for item in results if item["center_in_image"]}),
            "source": "evaluator_only_target_geometry_and_render_product_parameters",
            "limitations": ["Projected points in the image do not prove unoccluded target visibility",
                            "This audit supplies no target pose or identity to the runtime policy",
                            "No recognition or localization accuracy is established by projection"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "evaluator/inspection_01.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from EAI_assets.asset_resolver import usd_root

    usd_path = usd_root() / "scene/factory/factory.usd"
    fixture = load_fixture(args.fixture)
    prim_audit = audit_factory_prim_bindings(usd_path)
    fixture_validation = validate_inspection_fixture(
        scenario_path=args.scenario, usd_path=usd_path, fixture=fixture, prim_audit=prim_audit)
    if not fixture_validation["validated"]:
        raise ValueError(f"invalid inspection fixture: {fixture_validation['errors']}")
    report = json.loads(args.report.read_text())
    if report["execution_provenance"]["request"]["scenario_sha256"] != file_sha256(args.scenario):
        raise ValueError("inspection report belongs to a different scenario")
    result = audit_frames(report, fixture, prim_audit)
    result["source_hashes"] = {"report_sha256": file_sha256(args.report), "fixture_sha256": file_sha256(args.fixture),
                               "scenario_sha256": file_sha256(args.scenario), "usd_sha256": file_sha256(usd_path)}
    write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
