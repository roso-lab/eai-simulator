"""Fail-closed aggregation of Stage17 scene evidence.

This is an author/release review utility. It reads archived reports and
classifies diagnostic evidence; it never promotes a task to scoreable and never
derives success from a scheduler return code alone.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "eai.benchmark.stage17_evidence_gate"


def _read(value: Mapping[str, Any] | str | Path, label: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return deepcopy(dict(value))
    path = Path(value)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} report: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} report must be an object")
    return data


def _status(status: str, blockers: list[str], *, diagnostic: bool = False) -> dict[str, Any]:
    return {
        "status": status,
        "diagnostic_completed": diagnostic,
        "business_outcomes_validated": False,
        "scoreable": False,
        "blockers": sorted(set(blockers)),
    }


def _city(report: Mapping[str, Any]) -> dict[str, Any]:
    blockers = []
    if report.get("status") != "diagnostic_completed":
        blockers.append(f"city_report_status:{report.get('status', 'missing')}")
    if report.get("sources_unchanged") is not True:
        blockers.append("city_source_hashes_changed_or_missing")
    trace = report.get("incident_trace")
    if not isinstance(trace, Mapping):
        blockers.append("city_incident_trace_missing")
    else:
        receipt = trace.get("receipt")
        if not isinstance(receipt, Mapping) or receipt.get("status") != "received":
            blockers.append("city_independent_receipt_missing")
        review = trace.get("service_review")
        if not isinstance(review, Mapping) or review.get("scoreable") is not False:
            blockers.append("city_review_did_not_remain_non_scoreable")
    if report.get("full_city_service") != "pending":
        blockers.append("city_full_service_status_not_explicit_pending")
    if blockers:
        return _status("blocked", blockers)
    return _status("diagnostic_only", ["city_full_business_service_pending"], diagnostic=True)


def _house(report: Mapping[str, Any]) -> dict[str, Any]:
    blockers = []
    if report.get("status") != "diagnostic_completed":
        blockers.append(f"house_report_status:{report.get('status', 'missing')}")
    path = report.get("resident_path")
    if not isinstance(path, Mapping):
        blockers.append("house_resident_path_report_missing")
    elif path.get("status") not in {"blocked", "current_resident_corridor_clear", "unknown"}:
        blockers.append("house_resident_path_status_invalid")
    objects = report.get("final_objects", {}).get("objects", [])
    book = next((row for row in objects if row.get("public_id") == "resident_bookstack_01"), None)
    if not isinstance(book, Mapping):
        blockers.append("house_bookstack_readback_missing")
    else:
        physical = book.get("physx", {})
        size = book.get("usd", {}).get("world_mesh_bounds_m", {}).get("size")
        position = physical.get("position_m")
        if (not isinstance(size, list) or len(size) != 3 or not isinstance(position, list)
                or len(position) != 3 or any(type(x) not in (int, float) or not math.isfinite(x)
                                             for x in [*size, *position])):
            blockers.append("house_bookstack_physical_readback_incomplete")
        elif position[2] - size[2] / 2 < 0.249:
            blockers.append("house_bookstack_below_floor")
    if blockers:
        return _status("blocked", blockers)
    return _status("diagnostic_only", ["house_live_business_detector_and_path_backend_pending"], diagnostic=True)


def _warehouse(report: Mapping[str, Any]) -> dict[str, Any]:
    blockers = []
    if report.get("status") != "diagnostic_completed":
        blockers.append(f"warehouse_report_status:{report.get('status', 'missing')}")
    carry = report.get("carry_readback")
    if not isinstance(carry, Mapping):
        blockers.append("warehouse_carry_readback_missing")
    else:
        displacement = carry.get("object_displacement_m")
        if (type(displacement) not in (int, float) or not math.isfinite(displacement)
                or displacement < 0.05):
            blockers.append("warehouse_transport_displacement_insufficient")
    dock = report.get("dock_receipt")
    if not isinstance(dock, Mapping) or dock.get("state") != "ready":
        blockers.append("warehouse_post_release_dock_receipt_missing")
    if report.get("physical_task_execution_validated") is not False:
        blockers.append("warehouse_physical_gate_not_fail_closed")
    return _status("blocked" if blockers else "diagnostic_only", blockers or
                   ["warehouse_full_business_episode_pending"], diagnostic=not blockers)


def review_stage17_evidence(reports: Mapping[str, Any]) -> dict[str, Any]:
    """Review City, House and Warehouse reports without trusting summary flags."""
    if not isinstance(reports, Mapping) or set(reports) != {"city", "house", "warehouse"}:
        raise ValueError("reports must contain exactly city, house and warehouse")
    scenes = {"city": _city(_read(reports["city"], "City")),
              "house": _house(_read(reports["house"], "House")),
              "warehouse": _warehouse(_read(reports["warehouse"], "Warehouse"))}
    blockers = [f"{scene}:{reason}" for scene, result in scenes.items()
                for reason in result["blockers"]]
    return {
        "schema": SCHEMA, "version": 1, "scenes": scenes,
        "release_status": "pending", "benchmark_ready": False, "scoreable": False,
        "blockers": sorted(set(blockers)),
        "review_scope": "stage17_diagnostic_and_blocker_classification",
    }


__all__ = ["SCHEMA", "review_stage17_evidence"]
