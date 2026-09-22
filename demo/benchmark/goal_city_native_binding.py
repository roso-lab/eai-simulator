"""Private City goal roster admission against a frozen native episode."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

from demo.city_benchmark.contracts import review_episode_binding

from .goal_driven_contract import validate_goal_bundle, write_goal_task_bundle
from .goal_driven_tasks import build_goal_task_package
from .provenance import canonical_sha256, file_sha256


def _exact_roster(values: Any, expected: set[str], label: str) -> None:
    if (not isinstance(values, list) or len(values) != len(expected)
            or any(not isinstance(value, str) or not value for value in values)
            or set(values) != expected):
        raise ValueError(f"{label} differs from the frozen City roster")


def _guidance_timing_review(bundle: Mapping[str, Any], binding: Mapping[str, Any],
                            services: Mapping[str, Any]) -> dict[str, Any]:
    """Measure later arrival without treating it as an ACK-terminal skill."""
    centers = {row["region_id"]: row for row in binding["assembly_capacity"]}
    durations: dict[str, list[float]] = {}
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        for task in witness["plan"]["tasks"]:
            if task["skill"] == "guide_people":
                for person_id in task["arguments"]["person_ids"]:
                    durations.setdefault(person_id, []).append(task["execution"]["duration_s"])
    blocked = []
    measured = {}
    for person in binding["affected_person_roster"]:
        person_id, region_id = person["person_id"], person["assembly_region_id"]
        row = services.get(f"guide_people:{region_id}", {})
        review = row.get("review", {})
        readback = row.get("readback", {}).get(person_id, {})
        actors = review.get("evidence", {}).get("actors", ())
        matches = [actor for actor in actors if actor.get("id") == person_id]
        samples = readback.get("native_readback", {}).get("trajectory_samples", ())
        if (review.get("status") != "evidence_complete" or len(matches) != 1
                or person_id not in durations or not isinstance(samples, list)
                or len(samples) < 2):
            return {"status": "unassessed", "reason": "missing_complete_native_guidance_trace"}
        ack = matches[0].get("ack_time_s")
        if isinstance(ack, bool) or not isinstance(ack, (int, float)) or not math.isfinite(ack):
            return {"status": "unassessed", "reason": "missing_native_ack_time"}
        times = [sample.get("time_s") for sample in samples]
        if (any(isinstance(time, bool) or not isinstance(time, (int, float))
                or not math.isfinite(time) for time in times)
                or abs(times[0] - ack) > .025
                or times[-1] < ack + max(durations[person_id])
                or any(not 0 < later - earlier <= .05
                       for earlier, later in zip(times, times[1:]))):
            return {"status": "unassessed", "reason": "native_guidance_trace_has_gaps"}
        center = centers[region_id]["center_xy_m"]
        radius = centers[region_id]["radius_m"]
        entry = None
        for sample in samples:
            point = sample.get("position_xyz")
            if (not isinstance(point, list) or len(point) != 3
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in point)):
                return {"status": "unassessed", "reason": "invalid_native_person_position"}
            if math.dist(point[:2], center) <= radius:
                entry = sample["time_s"]
                break
        latency = None if entry is None else max(0., entry - ack)
        measured[person_id] = {"first_center_entry_latency_s": latency,
                               "planned_duration_s": min(durations[person_id])}
        if entry is None or any(latency > duration + .05 for duration in durations[person_id]):
            blocked.append(person_id)
    return {"status": ("arrival_after_contact_window" if blocked
                       else "center_entry_within_contact_window"),
            "arrival_later_person_ids": sorted(blocked), "measurements": measured,
            "full_body_arrival_validated": False}


def _guidance_ack_review(bundle: Mapping[str, Any], binding: Mapping[str, Any],
                         services: Mapping[str, Any]) -> dict[str, Any]:
    durations: dict[str, list[float]] = {}
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        for task in witness["plan"]["tasks"]:
            if task["skill"] == "guide_people":
                for person_id in task["arguments"]["person_ids"]:
                    durations.setdefault(person_id, []).append(task["execution"]["duration_s"])
    measured = {}
    for person in binding["affected_person_roster"]:
        person_id, region_id = person["person_id"], person["assembly_region_id"]
        row = services.get(f"guide_people:{region_id}", {})
        request, contact = row.get("request", {}), row.get("contact", {})
        actors = contact.get("actor_acks")
        if actors is None:
            actors = row.get("review", {}).get("evidence", {}).get("actors", ())
        start, ack = request.get("submitted_time_s"), contact.get("ack_time_s")
        refs = contact.get("evidence_refs")
        if (request.get("service") != "guide_people"
                or request.get("entity_ids") != [person_id]
                or request.get("target_region_id") != region_id
                or contact.get("request_id") != request.get("request_id")
                or contact.get("agent_id") != request.get("agent_id")
                or contact.get("status") != "contact_confirmed"
                or contact.get("business_outcome_validated") is not False
                or contact.get("scoreable") is not False
                or not isinstance(actors, list) or len(actors) != 1
                or not isinstance(actors[0], Mapping)
                or actors[0].get("id") != person_id
                or person_id not in durations
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) for value in (start, ack))
                or not isinstance(refs, list) or len(refs) != 1
                or not isinstance(refs[0], str) or not refs[0]
                or actors[0].get("ack_time_s") != ack
                or actors[0].get("ack_evidence_ref") != refs[0]):
            return {"status": "unassessed", "reason": "missing_native_contact_readback"}
        latency = ack - start
        measured[person_id] = {"ack_latency_s": latency,
                               "planned_duration_s": min(durations[person_id]),
                               "evidence_ref": refs[0]}
        if latency < 0 or any(latency > duration + .05 for duration in durations[person_id]):
            return {"status": "blocked_by_native_ack_window", "measurements": measured}
    return {"status": "not_disproved_by_native_ack", "measurements": measured,
            "goal_session_execution_validated": False}


def review_city_goal_native_binding(
        bundle: Mapping[str, Any], private: Mapping[str, Any],
        semantic_map: Mapping[str, Any], native_report: Mapping[str, Any]) -> dict[str, Any]:
    """Attest identity only; service completion and scoring need later gates."""
    validate_goal_bundle(bundle)
    public = bundle["public_task"]
    if public["task_id"] != "CITY-GOAL-NATIVE-01":
        raise ValueError("City native binding requires the opt-in goal task")
    binding = private.get("episode_binding")
    review_episode_binding(binding, semantic_map)
    if (native_report.get("schema") != "eai.city.native_service_probe_v1"
            or native_report.get("geometry_scope") != "author_frozen_episode_binding"
            or native_report.get("status") not in {"blocked", "completed"}
            or not isinstance(native_report.get("episode_id"), str)
            or native_report["episode_id"] != native_report.get("run_id")):
        raise ValueError("native report is not a terminal frozen-binding episode")
    geometry = native_report.get("trace", {}).get("geometry", {})
    roster = {row["person_id"]: row["assembly_region_id"]
              for row in binding["affected_person_roster"]}
    assemblies = {row["region_id"]: row for row in binding["assembly_capacity"]}
    public_assemblies = {row["id"]: row for row in public["public_world"]["regions"]
                         if row["kind"] == "assembly_candidate"}
    _exact_roster(geometry.get("roster_ids"), set(roster), "native person roster")
    if (set(assemblies) != set(geometry.get("assembly_regions", {}))
            or set(assemblies) != set(public_assemblies)
            or any(public_assemblies[region_id]["resource_capacity"] !=
                   row["capacity_people"] for region_id, row in assemblies.items())):
        raise ValueError("public/native City person or assembly roster differs")
    vehicles = geometry.get("vehicle_ids")
    if (not isinstance(vehicles, list) or not vehicles
            or any(not isinstance(vehicle, str) or not vehicle for vehicle in vehicles)
            or len(vehicles) != len(set(vehicles))):
        raise ValueError("native following-vehicle roster is missing or duplicated")
    hold = native_report.get("trace", {}).get("services", {}).get("traffic_hold", {})
    _exact_roster(hold.get("request", {}).get("entity_ids"), set(vehicles),
                  "native traffic request/readback")
    _exact_roster(hold.get("review", {}).get("entity_ids"), set(vehicles),
                  "native traffic request/readback")
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        guided: dict[str, str] = {}
        controls = []
        for task in witness["plan"]["tasks"]:
            if task["skill"] == "guide_people":
                for person_id in task["arguments"]["person_ids"]:
                    if person_id in guided:
                        raise ValueError("City witness guides a person more than once")
                    guided[person_id] = task["arguments"]["assembly_region_id"]
            elif task["skill"] == "control_following_traffic":
                controls.append(set(task["arguments"]["vehicle_ids"]))
        if guided != roster or not controls or any(ids != set(vehicles) for ids in controls):
            raise ValueError("City witness differs from the frozen native roster")
    if binding["response_service_endpoint"]["service_id"] != "city_dispatch":
        raise ValueError("City native response endpoint differs from the goal contract")
    return {
        "schema": "eai.benchmark.city_goal_native_binding", "version": 1,
        "task_id": public["task_id"], "episode_id": native_report["episode_id"],
        "public_task_sha256": canonical_sha256(public),
        "episode_binding_sha256": canonical_sha256(binding),
        "person_count": len(roster), "assembly_count": len(assemblies),
        "following_vehicle_count": len(vehicles),
        "identity_binding_validated": True,
        "temporal_witness_review": _guidance_timing_review(
            bundle, binding, native_report["trace"]["services"]),
        "ack_window_review": _guidance_ack_review(
            bundle, binding, native_report["trace"]["services"]),
        "native_report_status": native_report["status"],
        "execution_validated": False, "scoreable": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--semantic-map", type=Path, required=True)
    parser.add_argument("--native-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path,
                        help="write an isolated opt-in City goal bundle after identity review")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists")
    private = json.loads(args.private.read_text(encoding="utf-8"))
    semantic_map = json.loads(args.semantic_map.read_text(encoding="utf-8"))
    report = json.loads(args.native_report.read_text(encoding="utf-8"))
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    result = review_city_goal_native_binding(bundle, private, semantic_map, report)
    result["native_report_sha256"] = file_sha256(args.native_report)
    result["semantic_map_sha256"] = file_sha256(args.semantic_map)
    if args.bundle_root is not None:
        write_goal_task_bundle(bundle, args.bundle_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
