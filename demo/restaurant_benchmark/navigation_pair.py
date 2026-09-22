"""Validate the two immutable Restaurant Carter navigation reports as a pair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import navigation_probe
from . import runtime_probe


def _load_attempt(root: Path) -> tuple[dict, dict, dict]:
    request_path = root / "request.json"
    report_path = root / "navigation_probe_report.json"
    log_path = root / "isaac.log"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    navigation_probe._validate_request_contract(request)
    route_id = request["route"]["route_id"]
    if (
        report.get("schema") != "eai.restaurant.native_navigation_probe"
        or report.get("version") != 1
        or report.get("status") != "diagnostic_completed"
        or report.get("scope") != navigation_probe.PROBE_SCOPE
        or report.get("route_id") != route_id
        or report.get("route") != request["route"]
        or report.get("request_sha256") != runtime_probe._canonical_sha256(request)
        or report.get("source_files") != request["source_files"]
        or report.get("route_navigation_validated") is not True
        or report.get("contact_health_validated") is not True
        or report.get("navigation_validated") is not False
        or report.get("navigation_ready") is not False
        or report.get("business_episode_execution_validated") is not False
        or report.get("scoreable") is not False
        or report.get("failure") is not None
        or report.get("arrival_step") != report.get("steps_taken")
        or len(report.get("trajectory", ())) != report.get("steps_taken")
        or report.get("control_step_evidence", {}).get("validated") is not True
        or report.get("control_step_evidence", {}).get("sample_count")
        != report.get("steps_taken")
    ):
        raise ValueError(f"invalid Restaurant navigation attempt {route_id}")
    return request, report, {
        "request_sha256": runtime_probe._sha256(request_path),
        "report_sha256": runtime_probe._sha256(report_path),
        "isaac_log_sha256": runtime_probe._sha256(log_path),
    }


def validate_pair(ae_root: str | Path, ab_root: str | Path) -> dict:
    attempts = {}
    for root in (Path(ae_root).resolve(), Path(ab_root).resolve()):
        request, report, hashes = _load_attempt(root)
        route_id = request["route"]["route_id"]
        if route_id in attempts:
            raise ValueError(f"duplicate Restaurant route {route_id}")
        attempts[route_id] = (request, report, hashes)
    if set(attempts) != set(navigation_probe.ROUTES):
        raise ValueError("Restaurant navigation pair must contain A-E and A-B")
    ae_request, _, _ = attempts["A-E"]
    for request, report, _hashes in attempts.values():
        if (
            request["source_sha256"] != ae_request["source_sha256"]
            or request["metadata_sha256"] != ae_request["metadata_sha256"]
            or request["bundle_manifest_sha256"]
            != ae_request["bundle_manifest_sha256"]
            or request["route"]["map_sha256"]
            != ae_request["route"]["map_sha256"]
            or request["source_files"] != ae_request["source_files"]
            or report["source_files"] != ae_request["source_files"]
        ):
            raise ValueError("Restaurant navigation attempts use different inputs")

    rows = {}
    for route_id in sorted(attempts):
        _request, report, hashes = attempts[route_id]
        rows[route_id] = {
            **hashes,
            "steps_taken": report["steps_taken"],
            "final_distance_to_goal_m": report["final_distance_to_goal_m"],
            "maximum_route_deviation_m": report["maximum_route_deviation_m"],
            "wheel_contact_sample_ratios": report["wheel_contact_sample_ratios"],
            "median_weight_support_ratio": report["median_weight_support_ratio"],
            "control_step_evidence_sha256": report["control_step_evidence"]["sha256"],
            "route_navigation_validated": True,
        }
    result = {
        "schema": "eai.restaurant.native_navigation_pair",
        "version": 1,
        "status": "validated",
        "scope": "candidate_v7_carter_routes_A-E_and_A-B_only",
        "source_sha256": ae_request["source_sha256"],
        "metadata_sha256": ae_request["metadata_sha256"],
        "bundle_manifest_sha256": ae_request["bundle_manifest_sha256"],
        "map_sha256": ae_request["route"]["map_sha256"],
        "source_files": ae_request["source_files"],
        "attempts": rows,
        "two_route_navigation_validated": True,
        "navigation_ready": False,
        "business_episode_execution_validated": False,
        "scoreable": False,
        "pair_validator_source_sha256": runtime_probe._sha256(Path(__file__).resolve()),
    }
    return {**result, "sha256": runtime_probe._canonical_sha256(result)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ae-root", type=Path, required=True)
    parser.add_argument("--ab-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("--output must not already exist")
    result = validate_pair(args.ae_root, args.ab_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    runtime_probe._write(args.output, result)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
