"""Bounded serial probe for the concrete City episode service runtime.

Without ``--episode-binding`` the probe keeps the v8 diagnostic behaviour: it
picks the affected person, the following vehicle, the assembly target, the
hazard box, the access probe and the divert route at run time and reports
``geometry_scope=runtime_selected_candidate_diagnostic``.  With an author
frozen ``eai.city.episode_binding`` the probe runs the formal episode mode:
the bound roster is spawned through ``spawn_response_people``, every active
following vehicle is bound, the hazard box, ingress probes, reaction window
and receipt endpoint come from the binding, and ``formal_episode_binding``
turns true only when the declaration matches the runtime readback item by
item.  Any mismatch fails closed to false with the differences listed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from functools import partial
import hashlib
import dataclasses
import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping
import uuid

from .native_probe import _serial_lock, _write


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "eai.city.native_service_probe_v1"
TRACK_ID = "C-SERVICE-01"
REPORT_VERSION = 2
MAX_EPISODE_S = 45.0
DIAGNOSTIC_GEOMETRY_SCOPE = "runtime_selected_candidate_diagnostic"
FORMAL_GEOMETRY_SCOPE = "author_frozen_episode_binding"
FORMAL_BINDING_MISMATCH_LABELS = {
    "mismatch": "formal_binding_readback_mismatch",
    "incomplete": "formal_binding_evidence_incomplete",
    "blocked": "formal_binding_execution_incomplete",
}
_EPISODE_BINDING_SECTIONS = (
    "affected_person_roster", "assembly_capacity", "dynamic_hazard_boundaries",
    "safe_ingress_control_points", "following_traffic_response_window",
    "response_service_endpoint",
)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _jsonable(value.as_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _hashes() -> dict[str, str]:
    paths = [ROOT / path for path in (
        "demo/city_benchmark/scene/runtime.py", "demo/city_benchmark/scene/people.py", "demo/city_benchmark/scene/usd_scene.py",
        "demo/city_benchmark/live_witness.py", "demo/city_benchmark/native_runner.py",
        "demo/city_benchmark/service_probe.py", "demo/city_benchmark/response.py",
        "demo/city_benchmark/scene/main.py", "simulator.py",
        "usd/scene/city/city_static.usd", "usd/scene/city/city_scenario.json",
        "usd/demo/city/vehicles/car_e4087350c4d84d8391de09b405c96488.usd",
        "usd/demo/city/vehicles/car_03a6d639648540e3ad45ed38c9b19d1f.usd",
    )]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _selection(seed: int) -> dict[str, Any]:
    """Build the probe selection without importing City/Kit modules early."""
    return {
        "version": 1, "scene_key": "modern_house_nvd", "task_name": "city_eai_demo",
        "robots": [
            {"type": "go2", "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"},
             "spawn_pose": {"position": [-69.0, 49.0, 0.62], "rotation": [1, 0, 0, 0]},
             "attachments": []},
            {"type": "carter", "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
             "spawn_pose": {"position": [-65.0, 49.0, 0.22], "rotation": [1, 0, 0, 0]},
             "attachments": []},
            {"type": "iris", "controller": {"mode": "default", "cfg": "PEGASUS_IRIS_POSITION_CFG"},
             "spawn_pose": {"position": [-61.0, 49.0, 3.0], "rotation": [1, 0, 0, 0]},
             "attachments": []},
        ],
        "scenario_seed": int(seed),
    }


def _checkpoint(path: Path, report: Mapping[str, Any]) -> None:
    _write(path, _jsonable(report))


@contextmanager
def _service_report_guard(path: Path, report: dict[str, Any],
                          *, pending_label: str = "pending_formal_episode_geometry") -> Iterator[None]:
    """Keep partial service evidence if a later native phase fails."""
    try:
        yield
    except BaseException as exc:
        report.update(
            status="blocked",
            blocker=f"{type(exc).__name__}: {exc}",
            full_city_service=pending_label,
        )
        # A blocked episode never validated its binding readback; fail closed.
        if "formal_episode_binding" in report:
            report["formal_episode_binding"] = False
            if "formal_binding_review" in report:
                mismatches = list(report["formal_binding_review"].get("mismatches", ()))
                report["formal_binding_review"] = {
                    **report["formal_binding_review"], "formal_episode_binding": False,
                    "mismatches": list(dict.fromkeys(
                        ["execution_blocked_before_completed_readback", *mismatches])),
                }
        _checkpoint(path, report)
        raise
    else:
        _checkpoint(path, report)


# ---------------------------------------------------------------------------
# Author-frozen episode binding ingestion (offline, no Isaac imports).
# ---------------------------------------------------------------------------

def _validate_episode_binding_structure(binding: Any) -> None:
    """Reject anything that is not an author-frozen binding document."""
    from demo.city_benchmark.contracts import (
        CITY_EPISODE_BINDING_SCHEMA,
        CITY_EPISODE_BINDING_VERSION,
    )

    def fail(code: str):
        raise ValueError(f"invalid episode binding: {code}")

    if not isinstance(binding, Mapping):
        fail("binding must be an object")
    if (binding.get("schema") != CITY_EPISODE_BINDING_SCHEMA
            or type(binding.get("version")) is not int
            or binding["version"] != CITY_EPISODE_BINDING_VERSION):
        fail("unsupported episode binding schema or version")
    if binding.get("status") != "author_frozen_runtime_admission_pending":
        fail("only author-frozen bindings are formal; runtime picks are diagnostics")
    for section in _EPISODE_BINDING_SECTIONS:
        if section not in binding:
            fail(f"missing section: {section}")
    roster = binding["affected_person_roster"]
    if not isinstance(roster, list) or not roster:
        fail("empty affected-person roster")
    person_ids = []
    for row in roster:
        if not isinstance(row, Mapping) or not row.get("person_id") or not row.get("asset_id"):
            fail("roster rows require person and asset identity")
        if row["person_id"] in person_ids:
            fail("duplicate person identity")
        person_ids.append(row["person_id"])
        region_id = row.get("assembly_region_id")
        if not isinstance(region_id, str) or not region_id:
            fail("roster rows require an assembly region")
    capacity = binding["assembly_capacity"]
    if not isinstance(capacity, list) or not capacity:
        fail("empty assembly capacity")
    for row in capacity:
        if (not isinstance(row, Mapping) or not row.get("region_id")
                or not isinstance(row.get("bound_person_ids"), list) or not row["bound_person_ids"]
                or any(pid not in person_ids for pid in row["bound_person_ids"])
                or row.get("capacity_people") != len(row["bound_person_ids"])):
            fail("capacity rows must bind roster members exactly")
    hazard = binding["dynamic_hazard_boundaries"]
    if not isinstance(hazard, Mapping):
        fail("hazard template must be an object")
    for key in ("exclusion_half_extents_m", "z_window_above_road_m"):
        window = hazard.get(key)
        if (not isinstance(window, list) or len(window) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v <= 0 for v in window)):
            fail(f"hazard template requires positive {key}")
    ingress = binding["safe_ingress_control_points"]
    if not isinstance(ingress, Mapping) or not isinstance(ingress.get("candidate_offsets_m"), list) \
            or not ingress["candidate_offsets_m"]:
        fail("ingress control points require candidate offsets")
    for key in ("probe_half_extents_xy_m", "probe_z_window_m"):
        window = ingress.get(key)
        if (not isinstance(window, list) or len(window) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v <= 0 for v in window)):
            fail(f"ingress template requires positive {key}")
    window = binding["following_traffic_response_window"]
    if not isinstance(window, Mapping) or window.get("control_modes") != ["hold", "divert", "release"]:
        fail("following traffic window must cover hold, divert and release")
    for key in ("control_ack_deadline_s", "trajectory_reaction_window_s"):
        value = window.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value <= 0:
            fail(f"following traffic window requires positive {key}")
    endpoint = binding["response_service_endpoint"]
    if not isinstance(endpoint, Mapping):
        fail("response service endpoint must be an object")
    for key in ("service_id", "receiver_entrypoint"):
        if not isinstance(endpoint.get(key), str) or not endpoint[key]:
            fail(f"response service endpoint requires {key}")
    for key in ("receipt_deadline_s", "continuous_access_min_span_s"):
        value = endpoint.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value <= 0:
            fail(f"response service endpoint requires positive {key}")


def _load_episode_binding(path: Path) -> dict[str, Any]:
    """Load a binding from a standalone document or a prepare private record."""
    path = Path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    source_kind = "binding_document"
    binding = document
    map_review = None
    if isinstance(document, Mapping) and "episode_binding" in document:
        binding = document["episode_binding"]
        source_kind = "authoring_private"
    _validate_episode_binding_structure(binding)
    if source_kind == "authoring_private":
        semantic_map_path = path.parent.parent / "release" / "maps" / "semantic_map.json"
        if semantic_map_path.is_file():
            from demo.city_benchmark.contracts import review_episode_binding

            semantic_map = json.loads(semantic_map_path.read_text(encoding="utf-8"))
            map_review = review_episode_binding(binding, semantic_map)
    _binding_roster_specs(binding)  # full offline roster validation
    return {"binding": binding, "source": str(path), "source_kind": source_kind,
            "map_review": map_review}


def prepare_probe(output_root: Path, *, device: str = "cpu", seed: int = 17,
                  overwrite: bool = False,
                  episode_binding: Path | None = None) -> dict[str, Any]:
    path = output_root / "service_probe_prepare.json"
    if path.exists() and not overwrite:
        raise FileExistsError("City service probe is already prepared; use a fresh directory")
    formal = episode_binding is not None
    report = {"schema": SCHEMA, "version": REPORT_VERSION, "track_id": TRACK_ID, "status": "prepared",
              "device": str(device), "seed": int(seed), "scoreable": False,
              "formal_episode_binding": False,
              "geometry_scope": DIAGNOSTIC_GEOMETRY_SCOPE,
              "source_hashes": _hashes(),
              "bounded": {"max_steps_per_phase": 800, "max_episode_s": MAX_EPISODE_S},
              "requires": ["scene_query_support", "cpu_current_physx_queries",
                           "one_city_person", "one_following_vehicle"]}
    if formal:
        from demo.benchmark.provenance import canonical_sha256
        from .task_logic import default_deadline_policy

        loaded = _load_episode_binding(episode_binding)
        binding = loaded["binding"]
        # 静态障碍表随记录携带：execute 在取得实例化事故位后据此重导出
        # 疏散路线（与 hazard 的 freeze_at_episode_instantiation 同构）。
        obstacles: list = []
        map_path = Path(episode_binding).parent.parent / "release" / "maps" / "semantic_map.json"
        if map_path.is_file():
            obstacles = list(json.loads(map_path.read_text(encoding="utf-8"))
                             .get("static_obstacles", ()))
        report.update(
            bounded={"people_assembly_arrival_s": default_deadline_policy()["people_assembly_arrival_s"],
                     "max_episode_s": default_deadline_policy()["response_budget_s"]},
            formal_episode_binding=True,
            geometry_scope=FORMAL_GEOMETRY_SCOPE,
            episode_binding=binding,
            episode_binding_sha256=canonical_sha256(binding),
            episode_binding_route_obstacles=obstacles,
            episode_binding_source=loaded["source"],
            episode_binding_source_kind=loaded["source_kind"],
            episode_binding_map_review=loaded["map_review"],
            requires=["scene_query_support", "cpu_current_physx_queries",
                      "bound_roster_spawn_from_episode_binding",
                      "all_active_following_vehicles_bound_at_instantiation"],
        )
    _write(path, report)
    return report


def _load_prepare(output_root: Path) -> dict[str, Any]:
    path = output_root / "service_probe_prepare.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("run --prepare before --execute") from exc
    if result.get("schema") != SCHEMA or result.get("status") != "prepared":
        raise RuntimeError("invalid City service probe prepare record")
    return result


def _prepared_binding(prepared: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the prepare-time binding unchanged, or None for diagnostics."""
    binding = prepared.get("episode_binding")
    if binding is None:
        if prepared.get("formal_episode_binding") is True:
            raise RuntimeError("prepare record claims a binding but carries none")
        return None
    if prepared.get("formal_episode_binding") is not True:
        raise RuntimeError("prepare record carries a binding without the formal flag")
    from demo.benchmark.provenance import canonical_sha256

    if prepared.get("episode_binding_sha256") != canonical_sha256(binding):
        raise RuntimeError("episode binding changed after prepare; prepare again")
    _validate_episode_binding_structure(binding)
    _binding_roster_specs(binding)  # validate the full roster before launching Isaac
    return deepcopy(binding)


# ---------------------------------------------------------------------------
# Geometry helpers shared by both probe modes.
# ---------------------------------------------------------------------------

def _bounds_half(row: Mapping[str, Any], margin: float = 0.05) -> tuple[float, float, float]:
    return tuple((float(row["max"][axis]) - float(row["min"][axis])) * 0.5 + margin
                 for axis in range(3))


def _assembly_region(target, half, offset):
    # Capacity one: contain the full bound around the destination, with 10cm
    # free space. Derive from the person geometry, not a generic square that
    # can accidentally include a neighbouring lamp.
    extent = [half[i]+abs(offset[i])+.1 for i in range(2)]
    return {"min": [target[0]-extent[0], target[1]-extent[1], target[2]-.05],
            "max": [target[0]+extent[0], target[1]+extent[1], target[2]+offset[2]+half[2]+.1],
            "capacity": 1, "query_min_z": target[2]+.02}


def _binding_roster_specs(binding: Mapping[str, Any]) -> list[Any]:
    """Rebuild validated ``ResponsePersonSpec`` rows from the frozen roster."""
    from demo.city_benchmark.response import ResponsePersonSpec

    return [ResponsePersonSpec(
        person_id=row["person_id"], asset_id=row["asset_id"],
        start_xyz=tuple(float(value) for value in row["start_xyz"]),
        evacuation_route=tuple(tuple(float(value) for value in point)
                               for point in row["evacuation_route"]),
        assembly_region_id=row["assembly_region_id"],
        walk_speed_mps=float(row["walk_speed_mps"]),
        jog_speed_mps=float(row["jog_speed_mps"]))
        for row in binding["affected_person_roster"]]


def _binding_assembly_targets(binding: Mapping[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Per-region assembly target from the bound routes' final waypoints."""
    targets: dict[str, tuple[float, float, float]] = {}
    for row in binding["affected_person_roster"]:
        point = tuple(float(value) for value in row["evacuation_route"][-1])
        previous = targets.setdefault(row["assembly_region_id"], point)
        if any(abs(a - b) > 1e-6 for a, b in zip(previous, point)):
            raise ValueError(f"bound routes disagree about assembly target: {row['assembly_region_id']}")
    return targets


def _binding_assembly_regions(binding: Mapping[str, Any], targets, person_half, person_offset):
    """Bound-capacity regions: authored radius floored by envelope containment."""
    regions = {}
    for capacity in binding["assembly_capacity"]:
        region_id = capacity["region_id"]
        target = targets[region_id]
        extent = [float(capacity["radius_m"]), float(capacity["radius_m"])]
        top_z = float(target[2])
        for person_id in capacity["bound_person_ids"]:
            half, offset = person_half[person_id], person_offset[person_id]
            extent = [max(extent[axis], half[axis] + abs(offset[axis]) + .1) for axis in range(2)]
            top_z = max(top_z, target[2] + offset[2] + half[2] + .1)
        regions[region_id] = {
            "min": [target[0] - extent[0], target[1] - extent[1], target[2] - .05],
            "max": [target[0] + extent[0], target[1] + extent[1], top_z],
            "capacity": int(capacity["capacity_people"]),
            "query_min_z": target[2] + .02,
            "derivation": "author_candidate_radius_and_bound_person_envelope_containment",
        }
    return regions


def _binding_hazard_region(binding: Mapping[str, Any], incident_xyz, road_height: float):
    template = binding["dynamic_hazard_boundaries"]
    half_x, half_y = (float(value) for value in template["exclusion_half_extents_m"])
    z_low, z_high = (float(value) for value in template["z_window_above_road_m"])
    return {"min": [float(incident_xyz[0]) - half_x, float(incident_xyz[1]) - half_y,
                    float(road_height) + z_low],
            "max": [float(incident_xyz[0]) + half_x, float(incident_xyz[1]) + half_y,
                    float(road_height) + z_high],
            "derivation": template["derivation"]}


def _binding_access_candidates(binding: Mapping[str, Any], anchor_xyz):
    ingress = binding["safe_ingress_control_points"]
    return [(float(anchor_xyz[0]) + float(dx), float(anchor_xyz[1]) + float(dy),
             float(anchor_xyz[2])) for dx, dy in ingress["candidate_offsets_m"]]


def _aabb_close(a: Mapping[str, Any], b: Mapping[str, Any], tol: float = 1e-6) -> bool:
    return all(abs(float(a[bound][axis]) - float(b[bound][axis])) <= tol
               for bound in ("min", "max") for axis in range(3))


def _xyz_close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        left, right = tuple(float(value) for value in a), tuple(float(value) for value in b)
    except (TypeError, ValueError):
        return False
    return len(left) == len(right) == 3 and all(
        abs(left[axis] - right[axis]) <= tol for axis in range(3))


def _review_formal_binding(binding: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Compare the author-frozen declaration with the runtime readback.

    Pure comparison only: every item must match exactly, any missing or
    contradictory readback fails closed with the difference listed.
    """
    checks: list[dict[str, Any]] = []

    def record(item: str, consistent: bool, detail: dict[str, Any]) -> None:
        checks.append({"item": item, "consistent": consistent is True, "detail": detail})

    bound_roster = {row["person_id"]: row for row in binding["affected_person_roster"]}
    spawn = dict(evidence.get("person_spawn") or {})
    guidance = dict(evidence.get("person_guidance") or {})
    roster_detail = {}
    roster_ok = set(spawn) == set(bound_roster) == set(guidance) and bool(bound_roster)
    if roster_ok:
        for person_id, row in bound_roster.items():
            spawn_row, guide_row = spawn[person_id], guidance[person_id]
            start_ok = _xyz_close(spawn_row.get("readback_start_xyz"), row["start_xyz"])
            region_ok = (guide_row.get("bound_assembly_region_id") == row["assembly_region_id"]
                         and guide_row.get("request_assembly_region_id") == row["assembly_region_id"]
                         and guide_row.get("readback_assembly_region_id") == row["assembly_region_id"])
            arrived_ok = guide_row.get("arrived") is True and guide_row.get("inside_assembly") is True
            roster_detail[person_id] = {
                "bound_start_xyz": list(row["start_xyz"]),
                "readback_start_xyz": list(spawn_row.get("readback_start_xyz", ())),
                "start_matches": start_ok,
                "assembly_region_matches": region_ok,
                "arrived": guide_row.get("arrived"), "inside_assembly": guide_row.get("inside_assembly"),
            }
            roster_ok = roster_ok and start_ok and region_ok and arrived_ok
    record("affected_person_roster", roster_ok, {"persons": roster_detail})

    bound_capacity = {row["region_id"]: row for row in binding["assembly_capacity"]}
    region_rows = dict(evidence.get("assembly_regions") or {})
    capacity_detail = {}
    capacity_ok = set(region_rows) == set(bound_capacity) and bool(bound_capacity)
    if capacity_ok:
        for region_id, bound in bound_capacity.items():
            row = region_rows[region_id]
            occupants = list(row.get("occupants") or ())
            bound_ids = list(bound["bound_person_ids"])
            occupants_ok = (set(occupants) <= set(bound_roster)
                            and len(occupants) <= int(bound["capacity_people"]))
            registered_ok = row.get("registered_capacity") == int(bound["capacity_people"])
            measured_ok = row.get("readback_status") == "measured"
            inside_ok = all(guidance.get(pid, {}).get("inside_assembly") is True for pid in bound_ids)
            capacity_detail[region_id] = {
                "bound_capacity": bound["capacity_people"],
                "registered_capacity": row.get("registered_capacity"),
                "readback_status": row.get("readback_status"),
                "occupants": sorted(occupants), "bound_person_ids": bound_ids,
            }
            capacity_ok = capacity_ok and occupants_ok and registered_ok and measured_ok and inside_ok
    record("assembly_capacity", capacity_ok, {"regions": capacity_detail})

    hazard_row = dict(evidence.get("hazard") or {})
    hazard_ok = False
    hazard_detail: dict[str, Any] = {
        "incident_xyz": hazard_row.get("incident_xyz"),
        "readback_status": None,
    }
    if hazard_row and hazard_row.get("registered_region") is not None:
        derived = _binding_hazard_region(binding, hazard_row["incident_xyz"],
                                         hazard_row["road_height_m"])
        hazard_ok = (_aabb_close(derived, hazard_row["registered_region"])
                     and hazard_row.get("readback_status") == "measured")
        hazard_detail.update(derived_region=derived,
                             registered_region=hazard_row["registered_region"],
                             readback_status=hazard_row.get("readback_status"))
    record("dynamic_hazard_boundaries", hazard_ok, hazard_detail)

    access_row = dict(evidence.get("access") or {})
    access_ok = False
    access_detail: dict[str, Any] = {"selected_region_id": access_row.get("selected_region_id")}
    if access_row and access_row.get("registered_region") is not None:
        ingress = binding["safe_ingress_control_points"]
        anchor = access_row["anchor_xyz"]
        candidates = _binding_access_candidates(binding, anchor)
        selected = access_row["selected_region_id"]
        matches = [index for index, candidate in enumerate(candidates)
                   if _aabb_close({"min": [candidate[0] - ingress["probe_half_extents_xy_m"][0],
                                           candidate[1] - ingress["probe_half_extents_xy_m"][1],
                                           candidate[2] + ingress["probe_z_window_m"][0]],
                                  "max": [candidate[0] + ingress["probe_half_extents_xy_m"][0],
                                          candidate[1] + ingress["probe_half_extents_xy_m"][1],
                                          candidate[2] + ingress["probe_z_window_m"][1]]},
                                  access_row["registered_region"])]
        access_ok = (len(matches) == 1
                     and selected == f"rescue_access_{matches[0]}"
                     and access_row.get("readback_status") == "measured")
        access_detail.update(anchor_region_id=access_row.get("anchor_region_id"),
                             selected_offset_index=matches[0] if len(matches) == 1 else None,
                             selected_offset_m=(ingress["candidate_offsets_m"][matches[0]]
                                                if len(matches) == 1 else None),
                             readback_status=access_row.get("readback_status"))
    record("safe_ingress_control_points", access_ok, access_detail)

    window_row = dict(evidence.get("window") or {})
    bound_window = binding["following_traffic_response_window"]
    window_ok = bool(window_row)
    window_detail: dict[str, Any] = {}
    if window_ok:
        policy = window_row.get("runtime_policy") or {}
        window_ok = (
            policy.get("traffic_control_ack_s") == float(bound_window["control_ack_deadline_s"])
            and policy.get("traffic_reaction_window_s") == float(bound_window["trajectory_reaction_window_s"])
            and set(window_row.get("bound_vehicle_ids") or ()) ==
                set(window_row.get("controlled_vehicle_ids") or ())
            and bool(window_row.get("bound_vehicle_ids"))
            and set(window_row.get("executed_modes") or ()) == set(bound_window["control_modes"])
            and float(window_row.get("reaction_span_s", 0.0)) >=
                float(bound_window["trajectory_reaction_window_s"]) - 1e-9)
        window_detail = {"bound": dict(bound_window), "runtime_policy": policy,
                         "bound_vehicle_ids": list(window_row.get("bound_vehicle_ids") or ()),
                         "controlled_vehicle_ids": list(window_row.get("controlled_vehicle_ids") or ()),
                         "executed_modes": list(window_row.get("executed_modes") or ()),
                         "reaction_span_s": window_row.get("reaction_span_s")}
    record("following_traffic_response_window", window_ok, window_detail)

    endpoint_row = dict(evidence.get("endpoint") or {})
    bound_endpoint = binding["response_service_endpoint"]
    endpoint_ok = bool(endpoint_row)
    endpoint_detail: dict[str, Any] = {}
    if endpoint_ok:
        policy = endpoint_row.get("runtime_policy") or {}
        endpoint_ok = (
            endpoint_row.get("receiver_runtime_class") == bound_endpoint["receiver_entrypoint"]
            and endpoint_row.get("recipient_service_id") == bound_endpoint["service_id"]
            and policy.get("report_receipt_s") == float(bound_endpoint["receipt_deadline_s"])
            and float(endpoint_row.get("continuous_access_span_s", 0.0)) >=
                float(bound_endpoint["continuous_access_min_span_s"]) - 1e-9)
        endpoint_detail = {"bound": dict(bound_endpoint),
                           "receiver_runtime_class": endpoint_row.get("receiver_runtime_class"),
                           "recipient_service_id": endpoint_row.get("recipient_service_id"),
                           "runtime_report_receipt_s": policy.get("report_receipt_s"),
                           "continuous_access_span_s": endpoint_row.get("continuous_access_span_s")}
    record("response_service_endpoint", endpoint_ok, endpoint_detail)

    mismatches = [row["item"] for row in checks if not row["consistent"]]
    return {"formal_episode_binding": not mismatches, "mismatches": mismatches,
            "checks": checks}


def _step(session: Any, city: Any, actions: Mapping[str, Any], count: int = 1) -> None:
    from .native_probe import _robot_tensor_pose
    for _ in range(count):
        if city.elapsed + float(session.base_env.step_dt) > getattr(
                session, "_city_probe_max_episode_s", MAX_EPISODE_S):
            raise RuntimeError("City service probe exceeded its episode deadline")
        before = {"time_s": city.elapsed, "position_m": list(_robot_tensor_pose(session))}
        session.env.step(actions)
        city.step(dt=float(session.base_env.step_dt))
        session._city_diagnostic_role_interval = [before, {
            "time_s": city.elapsed, "position_m": list(_robot_tensor_pose(session)),
            "physics_step": int(session.base_env.sim.current_time_step_index)}]


def _role(runner: Any, role: str, session: Any, trace: dict) -> None:
    now = runner.adapter.now_s()
    samples = session._city_diagnostic_role_interval
    if (samples[-1]["time_s"] != now or samples[0]["time_s"] >= now
            or any(math.dist(row["position_m"][:2], [-69., 49.]) > 2. for row in samples)):
        raise RuntimeError("native robot left diagnostic role region or interval is stale")
    runner.record_role(role, start_time_s=samples[0]["time_s"], end_time_s=now)
    trace.setdefault("role_intervals", []).append({"role": role,
        "agent_id": "go2_1", "source": "articulation.data.root_pos_w", "samples": samples,
        "diagnostic_region_center_xy": [-69., 49.], "radius_m": 2.})


def _rotation_sweep_half(half, angle):
    """Axis-aligned bound of a rectangle over the full signed turn interval."""
    lower, upper = sorted((0., float(angle)))
    x, y, z = half
    critical = [lower, upper]
    for k in range(-3, 4):
        for base in (math.atan2(y, x), -math.atan2(y, x),
                     math.atan2(x, y), -math.atan2(x, y)):
            value = base+k*math.pi
            if lower <= value <= upper:
                critical.append(value)
    return (max(x*abs(math.cos(t))+y*abs(math.sin(t)) for t in critical),
            max(x*abs(math.sin(t))+y*abs(math.cos(t)) for t in critical), z)


def _divert_candidates(city, vehicle_id, reader, half, offset):
    """Bounded author candidates selected at request time, never formal routes."""
    current = city.following_vehicle_pose(vehicle_id)
    state = city._traffic_state[vehicle_id]
    _, yaw = state["active_route"].sample(state["distance"], loop=state["active_route"] is state["base_route"])
    attempts = []
    # `half` is the CURRENT world AABB, not the initial heading's AABB.
    # Rotate this conservative rectangle through the actual commanded turn;
    # maxima include all intermediate headings, not only the endpoints.
    sweep_half = half
    for forward in (6., 3.):
        for lateral in (.5, -.5, 1., -1., 2., -2.):
            endpoint = (current[0] + forward*math.cos(yaw)-lateral*math.sin(yaw),
                        current[1] + forward*math.sin(yaw)+lateral*math.cos(yaw), current[2])
            sweep_half = _rotation_sweep_half(half, math.atan2(lateral, forward))
            points = tuple(tuple(p[i]+offset[i] for i in range(3)) for p in (current, endpoint))
            result = reader.read_route(f"{vehicle_id}:candidate:{len(attempts)}", points,
                half_extents_m=sweep_half, episode_id=city.episode_id, time_s=city.elapsed,
                moving_entity_id=vehicle_id)
            attempts.append({"endpoint": endpoint, "readback": result,
                             "initial_world_half_extents_m": list(half),
                             "turn_angle_rad": math.atan2(lateral, forward)})
            if result.get("status") == "measured" and result.get("clear") is True:
                return (endpoint,), sweep_half, attempts
    return None, sweep_half, attempts


def _select_access(reader_factory, candidates, *, episode_id, time_s,
                   half_xy=(1.0, 1.0), z_window=(0.05, 2.5), base_id="rescue_access"):
    for index, center in enumerate(candidates):
        region_id = f"{base_id}_{index}"
        row = {"min": [center[0] - half_xy[0], center[1] - half_xy[1], center[2] + z_window[0]],
               "max": [center[0] + half_xy[0], center[1] + half_xy[1], center[2] + z_window[1]]}
        reader = reader_factory({region_id: row})
        result = reader.read(region_id, episode_id=episode_id, time_s=time_s)
        if result.get("status") == "measured" and result.get("clear") is True:
            return region_id, row
    raise RuntimeError("no measured-clear rescue access region candidate")


def _along_route(waypoints, distance_m):
    """沿折线走 distance_m 的世界坐标（越界取末端）。"""
    acc = 0.0
    for index in range(len(waypoints) - 1):
        ax, ay = float(waypoints[index][0]), float(waypoints[index][1])
        bx, by = float(waypoints[index + 1][0]), float(waypoints[index + 1][1])
        seg = math.hypot(bx - ax, by - ay)
        if acc + seg >= distance_m:
            t = (distance_m - acc) / seg if seg else 0.0
            return (ax + (bx - ax) * t, ay + (by - ay) * t)
        acc += seg
    return (float(waypoints[-1][0]), float(waypoints[-1][1])) if waypoints else None


def _instantiate_evacuation_routes(binding, specs, incident_xyz, obstacles,
                                   *, extra_grid=None, live_origins=None,
                                   person_extra_grids=None):
    """以实例化事故盘（±11 m 含排队带）为禁区重导出疏散路线。

    与 hazard 的 freeze_at_episode_instantiation 同构：事故位需 USD 射线、
    不可离线复现，故路线在 episode 实例化时以同款栅格 A* 重导出，并同步
    更新 worker 内存中的绑定副本（prepare 原件不变）。返回逐人记录。
    """
    from .response import (_astar_leg, _grid_cell, _nearest_clear_in_goal_component,
                           _nearest_clear_point, _obstacle_grid)
    cells = set(_obstacle_grid(obstacles, half_xy=0.2, z_window=(-1.2, 2.1)))
    if extra_grid:
        cells.update(extra_grid)
    cx, cy = float(incident_xyz[0]), float(incident_xyz[1])
    for ix in range(math.floor((cx - 11.0) * 2), math.floor((cx + 11.0) * 2) + 1):
        for iy in range(math.floor((cy - 11.0) * 2), math.floor((cy + 11.0) * 2) + 1):
            wx, wy = ix * 0.5 + 0.25, iy * 0.5 + 0.25
            if (wx - cx) ** 2 + (wy - cy) ** 2 <= 11.0 ** 2:
                cells.add((ix, iy))
    grid = cells
    targets = _binding_assembly_targets(binding)
    rows, roster_update = {}, {}
    rows["_grid_cells_holder"] = cells  # 供二遍重导出增量的未录障碍
    for spec in specs:
        grid = cells | set((person_extra_grids or {}).get(spec.person_id, ()))
        goal = targets[spec.assembly_region_id]
        # 二遍时人员已沿首路线行进：以实时位置为起点（预热/重试期间会走动）
        live = (live_origins or {}).get(spec.person_id)
        origin = ((float(live[0]), float(live[1])) if live
                  else (float(spec.start_xyz[0]), float(spec.start_xyz[1])))
        relocated = None
        if _grid_cell(origin) in grid:
            if live is not None:
                raise RuntimeError(f"live evacuation origin is occupied: {spec.person_id}")
            relocated = (_nearest_clear_in_goal_component(grid, origin, (goal[0], goal[1]))
                         or _nearest_clear_point(grid, origin))
            origin = relocated or origin
        leg = _astar_leg(grid, origin, (goal[0], goal[1]))
        if leg:
            route = ((origin[0], origin[1], float(spec.start_xyz[2])),
                     *((px, py, float(spec.start_xyz[2])) for px, py in leg[1:-1]),
                     (float(goal[0]), float(goal[1]), float(goal[2])))
            rows[spec.person_id] = {"waypoints": len(route), "relocated_start": relocated,
                                    "keep_out_centre_m": [cx, cy],
                                    "replan_origin_xyz": list(route[0]) if live is not None else None,
                                    "execution_route": [list(point) for point in route]}
            roster_update[spec.person_id] = route
        else:
            rows[spec.person_id] = {"waypoints": len(spec.evacuation_route), "fallback": True,
                                    "keep_out_centre_m": [cx, cy]}
    for index, row in enumerate(binding["affected_person_roster"]):
        route = roster_update.get(row["person_id"])
        if route is not None:
            if row["person_id"] not in (live_origins or {}):
                row["evacuation_route"] = [[float(v) for v in point] for point in route]
                row["start_xyz"] = [float(v) for v in route[0]]
            # frozen dataclass：以 replace 生成实例化实例
            specs[index] = dataclasses.replace(specs[index], start_xyz=tuple(row["start_xyz"]),
                                               evacuation_route=route)
    return rows


def _dynamic_route_grid(envelopes, person_id, half, offset):
    """Convert current world envelopes to forbidden actor-root grid cells.

    This is a planning hint at one instant, never future collision evidence.
    The native admission/trajectory reader still validates the resulting route.
    """
    cells = set()
    for entity_id, bounds in envelopes.items():
        if entity_id == person_id:
            continue
        lo, hi = bounds["min"], bounds["max"]
        low = [math.floor((lo[i] - half[i] - offset[i]) * 2) for i in range(2)]
        high = [math.floor((hi[i] + half[i] - offset[i]) * 2) for i in range(2)]
        cells.update((x, y) for x in range(low[0], high[0] + 1)
                     for y in range(low[1], high[1] + 1))
    return cells


def _execute(output_root: Path, prepared: Mapping[str, Any], *, run_id: str,
             headless: bool) -> dict[str, Any]:
    from simulator import SimulatorLaunchConfig, open_simulator_session

    binding = _prepared_binding(prepared)
    formal = binding is not None
    seed, device = int(prepared["seed"]), str(prepared["device"])
    geometry_scope = (FORMAL_GEOMETRY_SCOPE if formal else DIAGNOSTIC_GEOMETRY_SCOPE)
    pending_label = (FORMAL_BINDING_MISMATCH_LABELS["blocked"] if formal
                     else "pending_formal_episode_geometry")
    report = {"schema": SCHEMA, "version": REPORT_VERSION, "track_id": TRACK_ID, "status": "starting",
              "run_id": run_id, "episode_id": run_id, "scoreable": False,
              "execution_validated": False, "device_requested": device,
              "formal_episode_binding": False,
              "geometry_scope": geometry_scope,
              "full_city_service": pending_label,
              "trace": {"geometry": None, "services": {}}}

    def configure(env_cfg):
        from demo.city_benchmark.scene.main import configure_city_env_cfg

        # Formal episodes replace the background pedestrian with the frozen
        # roster spawned from the binding; diagnostics keep one background person.
        configure_city_env_cfg(env_cfg, seed=seed, use_flow=False,
                               people=0 if formal else 1)
        env_cfg.sim.enable_scene_query_support = True
        report["query_support_configured"] = True

    config = SimulatorLaunchConfig(
        env="city_service_probe", resolved_env_name="EAI-Interactive-v0",
        selection_data=_selection(seed), num_envs=1, device=device, seed=seed,
        headless=headless, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=partial(configure))
    report_path = output_root / "service_probe_report.json"
    _checkpoint(output_root / "service_probe_start.json", report)
    with open_simulator_session(config) as session, _service_report_guard(
            report_path, report, pending_label=pending_label):
        from demo.city_benchmark.scene.main import _zero_actions
        from demo.city_benchmark.scene.runtime import CityEaiRuntime, CityRuntimeConfig

        city = CityEaiRuntime.from_current_stage(
            config=CityRuntimeConfig(seed=seed, use_flow=False, episode_id=run_id))
        city.reset()
        session._city_probe_max_episode_s = prepared.get("bounded", {}).get(
            "max_episode_s", MAX_EPISODE_S)
        actions = _zero_actions(session)
        if formal:
            _run_bound_episode(session, city, actions, report, report_path, prepared,
                               run_id, binding)
        else:
            _run_diagnostic_episode(session, city, actions, report, report_path,
                                    prepared, run_id)
    return report


def _run_diagnostic_episode(session, city, actions, report, report_path, prepared, run_id) -> None:
    from demo.city_benchmark.scene.usd_scene import CityNativeRegionReader, world_envelopes
    from demo.city_benchmark.live_witness import CityIncidentReceiver
    from demo.city_benchmark.native_runner import CityNativeServiceRunner
    import omni.physx

    _step(session, city, actions)
    if not city.scene.people or not city._traffic_state:
        raise RuntimeError("City service probe requires one person and one following vehicle")
    person_id = city.people.actor_ids[0]
    person_record = next(row for row in city.scene.people if row["actor_id"] == person_id)
    vehicle_id = next(iter(city._traffic_state))
    entity_paths = {row["actor_id"]: row["path"] for row in city.scene.people}
    entity_paths.update({key: value["prim_path"] for key, value in city._traffic_state.items()})
    entity_paths["incident_vehicle"] = str(city.incident_vehicle[0].GetAttr().GetPrim().GetPath())

    def envelopes():
        return world_envelopes(city.stage, entity_paths)

    query = omni.physx.get_physx_scene_query_interface()
    ignored_paths = tuple(entity_paths.values())

    def reader_factory(regions):
        return CityNativeRegionReader(
            query=query, regions=regions, envelope_reader=envelopes,
            scene_queries_enabled=session.env_cfg.sim.enable_scene_query_support is True,
            device=str(session.device), current_gpu_geometry=False,
            ignored_collision_paths=ignored_paths)

    current_person = city.people.position(person_id)
    assembly_target = tuple(float(value) for value in person_record["waypoints"][1])
    assembly = {"min": [assembly_target[0] - .7, assembly_target[1] - .7,
                        assembly_target[2] - .05],
                "max": [assembly_target[0] + .7, assembly_target[1] + .7,
                        assembly_target[2] + 2.4], "capacity": 1,
                "query_min_z": assembly_target[2] + .05}
    incident = city.scene.plan.sample(city.elapsed).car_position
    hazard = {"min": [incident[0] - 4.0, incident[1] - 4.0, city.config.road_height + .05],
              "max": [incident[0] + 4.0, incident[1] + 4.0, city.config.road_height + 3.0]}
    candidates = [(assembly_target[0] + dx, assembly_target[1] + dy, assembly_target[2])
                  for dx, dy in ((4, 0), (-4, 0), (0, 4), (0, -4), (6, 6), (-6, -6))]
    access_id, access = _select_access(
        reader_factory, candidates, episode_id=run_id, time_s=city.elapsed)
    regions = {"assembly_1": assembly, "hazard": hazard, access_id: access}
    reader = reader_factory(regions)
    initial_envelopes = envelopes()
    vehicle_half_raw = _bounds_half(initial_envelopes[vehicle_id], margin=.1)
    person_half_raw = _bounds_half(initial_envelopes[person_id], margin=.05)
    vehicle_half = (vehicle_half_raw[0], vehicle_half_raw[1], max(.05, vehicle_half_raw[2] - .05))
    person_half = (person_half_raw[0], person_half_raw[1], max(.05, person_half_raw[2] - .05))
    vehicle_pose = city.following_vehicle_pose(vehicle_id)
    vehicle_center = tuple((float(initial_envelopes[vehicle_id]["min"][axis])
                            + float(initial_envelopes[vehicle_id]["max"][axis])) * .5
                           for axis in range(3))
    person_center = tuple((float(initial_envelopes[person_id]["min"][axis])
                           + float(initial_envelopes[person_id]["max"][axis])) * .5
                          for axis in range(3))
    vehicle_offset = tuple(vehicle_center[axis] - vehicle_pose[axis] + (0.08 if axis == 2 else 0.0)
                           for axis in range(3))
    person_offset = tuple(person_center[axis] - current_person[axis] + (0.05 if axis == 2 else 0.0)
                          for axis in range(3))
    assembly = _assembly_region(assembly_target, person_half, person_offset)
    regions["assembly_1"] = assembly
    reader = reader_factory(regions)
    route = city._traffic_state[vehicle_id]["base_route"]
    ahead, yaw = route.sample(city._traffic_state[vehicle_id]["distance"] + 8.0, loop=True)
    lateral = (-math.sin(yaw) * 4.0, math.cos(yaw) * 4.0)
    divert = ((vehicle_pose[0] + lateral[0], vehicle_pose[1] + lateral[1], vehicle_pose[2]),
              (ahead[0] + lateral[0], ahead[1] + lateral[1], vehicle_pose[2]))
    divert_check = reader.read_route(
        f"vehicle:{vehicle_id}:divert-preflight",
        tuple(tuple(point[axis] + vehicle_offset[axis] for axis in range(3))
              for point in (vehicle_pose, *divert)),
        half_extents_m=vehicle_half, episode_id=run_id, time_s=city.elapsed,
        moving_entity_id=vehicle_id)
    report["trace"]["geometry"] = {
        "assembly": assembly, "hazard": hazard, "access": access,
        "vehicle_id": vehicle_id, "person_id": person_id,
        "initial_envelopes": initial_envelopes, "vehicle_pose": vehicle_pose,
        "vehicle_offset": vehicle_offset, "person_offset": person_offset,
        "vehicle_half": vehicle_half, "person_half": person_half,
        "divert_preflight": divert_check}
    _checkpoint(report_path, report)
    # This candidate is not activated. Recheck/select at the actual divert
    # request time after earlier services have changed world state.

    receiver = CityIncidentReceiver(episode_id=run_id, service_id="city_dispatch")
    assignments = {service: {"agent_id": "go2_1", "region_id": "road_surface"}
                   for service in ("guide_people", "control_traffic", "report_incident")}
    city.bind_city_service_sources(
        affected_people={person_id: {
            "speed_mps": 1.0, "motion": "walk", "assembly_region_id": "assembly_1",
            "assembly_waypoints": [assembly_target], "body_half_extents_m": person_half,
            "body_center_offset_m": person_offset}},
        following_vehicle_ids=(vehicle_id,),
        vehicle_divert_routes={vehicle_id: divert},
        vehicle_half_extents_m={vehicle_id: vehicle_half},
        vehicle_center_offsets_m={vehicle_id: vehicle_offset},
        assembly_regions={"assembly_1": assembly}, hazard_region_id="hazard",
        access_region_id=access_id, region_reader=reader,
        assignment=assignments, incident_receiver=receiver)
    runner = CityNativeServiceRunner(
        city, episode_id=run_id, agent_id="go2_1", region_id="road_surface",
        assembly_region_id="assembly_1", recipient_service_id="city_dispatch",
        role_source_id="isaac.robot_tensor_role", admission_source_id="isaac.native_region_reader",
        scene_query_support=True, clearance_query_provenance="episode_timestamped_physx_and_usd",
        physx_readback_suppressed=False)
    if runner.readiness["status"] != "ready":
        raise RuntimeError("City native runner is not ready: " + ",".join(runner.readiness["blockers"]))

    trace = report["trace"]
    trace["geometry"].update({
        "assembly": assembly, "hazard": hazard, "access": access,
        "divert": [list(point) for point in divert],
        "vehicle_half_extents_m": list(vehicle_half),
        "person_half_extents_m": list(person_half),
        "person_asset": {
            "path": person_record.get("source"),
            "sha256": person_record.get("source_hash"),
            "unchanged_during_spawn": person_record.get("source_unchanged") is True,
            "support_binding": person_record.get("support_binding"),
        },
    })
    _checkpoint(report_path, report)
    observation = runner.observe()
    _role(runner, "guidance_contact", session, trace)
    start_person = city.people.position(person_id)
    trace["guidance_admission"] = {
        "time_s": city.elapsed,
        "world_sample": city._episode_service._world_samples[-1],
        "route": city._episode_service._person_route(person_id),
        "person_id": person_id,
        "target_region_id": "assembly_1",
    }
    _checkpoint(report_path, report)
    guidance = runner.guide_people((person_id,), observation_ref=observation)
    trace["services"]["guide_people"] = {
        "status": "command_accepted_pending_readback",
        "request": guidance["request"],
        "position_before": list(start_person),
    }
    _checkpoint(report_path, report)
    for _ in range(800):
        _step(session, city, actions)
        if city._episode_service.person_readback(person_id)["arrived"]:
            break
    else:
        raise RuntimeError("affected person did not reach assembly within 800 steps")
    person_row = runner.record_person_arrival(
        guidance["request"]["request_id"], person_id, previous_position=start_person,
        assembly_region_id="assembly_1", arrived=False)
    trace["services"]["guide_people"] = {
        "status": "readback_recorded",
        "request": guidance["request"], "readback": person_row,
        "review": runner.review(guidance["request"]["request_id"])}
    _checkpoint(report_path, report)

    for mode in ("hold", "release", "divert"):
        observation = runner.observe()
        _role(runner, "traffic_control_point", session, trace)
        before = city.following_vehicle_pose(vehicle_id)
        if mode == "divert":
            current_bounds = envelopes()[vehicle_id]
            current_half = _bounds_half(current_bounds, margin=.1)
            current_half = (*current_half[:2], max(.05, current_half[2]-.05))
            chosen, swept_half, attempts = _divert_candidates(city, vehicle_id, reader, current_half, vehicle_offset)
            trace["divert_candidates"] = attempts
            _checkpoint(report_path, report)
            if chosen is None:
                raise RuntimeError("all bounded divert candidates are blocked")
            city._episode_service.divert_routes[vehicle_id] = chosen
            city._episode_service.vehicle_half_extents[vehicle_id] = swept_half
        controlled = runner.control_following_traffic((vehicle_id,), mode, observation_ref=observation)
        trace["services"][f"traffic_{mode}"] = {
            "status": "command_accepted_pending_readback",
            "request": controlled["request"],
            "position_before": list(before),
        }
        _checkpoint(report_path, report)
        reaction_steps = max(1, math.ceil(5.1 / float(session.base_env.step_dt)))
        _step(session, city, actions, reaction_steps)
        readback = runner.record_vehicle_readback(
            controlled["request"]["request_id"], vehicle_id,
            previous_position=before, status_before=mode)
        trace["services"][f"traffic_{mode}"] = {
            "status": "readback_recorded",
            "request": controlled["request"], "position_before": list(before),
            "position_after": list(city.following_vehicle_pose(vehicle_id)),
            "readback": readback, "review": runner.review(controlled["request"]["request_id"])}
        _checkpoint(report_path, report)

    observation = runner.observe()
    _role(runner, "incident_observer", session, trace)
    incident_result = runner.report_incident(observation_ref=observation)
    trace["services"]["report_incident"] = {
        "status": "receipt_recorded_pending_continuous_access",
        **incident_result,
    }
    _checkpoint(report_path, report)
    receipt_time = city.elapsed
    _step(session, city, actions, max(1, math.ceil(.5 / float(session.base_env.step_dt))))
    access_result = runner.record_continuous_access(
        incident_result["request"], start_time_s=receipt_time, end_time_s=city.elapsed)
    trace["services"]["report_incident"] = {
        "status": "continuous_access_recorded", **incident_result, "access": access_result,
        "review": runner.review(incident_result["request"]["request_id"])}
    _checkpoint(report_path, report)
    reviews = [row["review"] for row in trace["services"].values()]
    report.update(status="completed", execution_validated=True,
                  candidate_witness="evidence_complete"
                  if all(row.get("status") == "evidence_complete" for row in reviews) else "pending",
                  full_city_service="pending_formal_episode_geometry",
                  device_actual=str(session.device), episode_time_s=city.elapsed,
                  source_hashes_after=_hashes())
    report["sources_unchanged"] = report["source_hashes_after"] == prepared["source_hashes"]
    if not report["sources_unchanged"]:
        raise RuntimeError("City service probe sources changed after prepare")
    _checkpoint(report_path, report)


def _run_bound_episode(session, city, actions, report, report_path, prepared, run_id,
                       binding) -> None:
    """Drive the author-frozen formal episode through the concrete services."""
    import omni.physx
    from demo.city_benchmark.scene.usd_scene import CityNativeRegionReader, world_envelopes
    from demo.city_benchmark.live_witness import CityIncidentReceiver
    from demo.city_benchmark.native_runner import CityNativeServiceRunner
    from demo.city_benchmark.response import spawn_response_people
    from demo.city_benchmark.task_logic import default_deadline_policy

    trace = report["trace"]
    dt = float(session.base_env.step_dt)
    endpoint = binding["response_service_endpoint"]
    window = binding["following_traffic_response_window"]
    ingress = binding["safe_ingress_control_points"]
    specs = _binding_roster_specs(binding)
    roster_ids = [spec.person_id for spec in specs]

    # Freeze the incident sample at episode instantiation (elapsed == 0.0 right
    # after reset) before any simulated step moves the scripted accident car.
    incident_xyz = tuple(float(value) for value in
                         city.scene.plan.sample(city.elapsed).car_position)
    # 实例化路线补全：以事故盘（含排队带）为禁区重导出，绑定副本同步更新
    # （prepare 原件与其哈希不受影响；记录见 trace.route_instantiation）。
    route_grid_cells: set = set()
    report["trace"]["route_instantiation"] = _instantiate_evacuation_routes(
        binding, specs, incident_xyz,
        prepared.get("episode_binding_route_obstacles") or []) or {}
    if isinstance(report["trace"]["route_instantiation"], dict):
        route_grid_cells = report["trace"]["route_instantiation"].pop("_grid_cells_holder", set())
    _checkpoint(report_path, report)
    people_runtime, _evacuation_binding, spawn_records = spawn_response_people(city.stage, specs)
    city.people = people_runtime
    city.scene.people_runtime = people_runtime
    for person_id in roster_ids:
        people_runtime.pause(person_id)
    if sorted(str(actor) for actor in people_runtime.actor_ids) != sorted(roster_ids):
        raise RuntimeError("spawned response actors do not match the bound roster")
    spawn_readback = {person_id: tuple(float(value) for value in people_runtime.position(person_id))
                      for person_id in roster_ids}
    _step(session, city, actions)
    if not city._traffic_state:
        raise RuntimeError("formal episode requires active following traffic")
    vehicle_ids = tuple(city._traffic_state)
    entity_paths = {row["person_id"]: row["prim_path"] for row in spawn_records}
    entity_paths.update({key: value["prim_path"] for key, value in city._traffic_state.items()})
    entity_paths["incident_vehicle"] = str(city.incident_vehicle[0].GetAttr().GetPrim().GetPath())

    envelope_tick = None
    envelope_snapshot = None

    def envelopes():
        nonlocal envelope_tick, envelope_snapshot
        tick = (city.elapsed, int(session.base_env.sim.current_time_step_index))
        if tick != envelope_tick:
            envelope_snapshot = world_envelopes(city.stage, entity_paths)
            envelope_tick = tick
        return envelope_snapshot

    query = omni.physx.get_physx_scene_query_interface()
    ignored_paths = tuple(entity_paths.values())

    def reader_factory(regions):
        return CityNativeRegionReader(
            query=query, regions=regions, envelope_reader=envelopes,
            scene_queries_enabled=session.env_cfg.sim.enable_scene_query_support is True,
            device=str(session.device), current_gpu_geometry=False,
            ignored_collision_paths=ignored_paths)

    initial_envelopes = envelopes()
    person_half, person_offset = {}, {}
    for person_id in roster_ids:
        raw = _bounds_half(initial_envelopes[person_id], margin=.05)
        person_half[person_id] = (raw[0], raw[1], max(.05, raw[2] - .05))
        center = tuple((float(initial_envelopes[person_id]["min"][axis])
                        + float(initial_envelopes[person_id]["max"][axis])) * .5
                       for axis in range(3))
        current = tuple(float(value) for value in city.people.position(person_id))
        person_offset[person_id] = tuple(center[axis] - current[axis] + (0.05 if axis == 2 else 0.0)
                                         for axis in range(3))
    vehicle_pose, vehicle_half, vehicle_offset = {}, {}, {}
    for vehicle_id in vehicle_ids:
        raw = _bounds_half(initial_envelopes[vehicle_id], margin=.1)
        vehicle_half[vehicle_id] = (raw[0], raw[1], max(.05, raw[2] - .05))
        vehicle_pose[vehicle_id] = city.following_vehicle_pose(vehicle_id)
        center = tuple((float(initial_envelopes[vehicle_id]["min"][axis])
                        + float(initial_envelopes[vehicle_id]["max"][axis])) * .5
                       for axis in range(3))
        vehicle_offset[vehicle_id] = tuple(
            center[axis] - vehicle_pose[vehicle_id][axis] + (0.08 if axis == 2 else 0.0)
            for axis in range(3))

    assembly_targets = _binding_assembly_targets(binding)
    assembly_regions = _binding_assembly_regions(binding, assembly_targets,
                                                 person_half, person_offset)
    hazard = _binding_hazard_region(binding, incident_xyz, city.config.road_height)
    anchor_region = binding["assembly_capacity"][0]["region_id"]
    anchor_xyz = assembly_targets[anchor_region]
    access_candidates = _binding_access_candidates(binding, anchor_xyz)
    access_id, access = _select_access(
        reader_factory, access_candidates, episode_id=run_id, time_s=city.elapsed,
        half_xy=tuple(float(value) for value in ingress["probe_half_extents_xy_m"]),
        z_window=tuple(float(value) for value in ingress["probe_z_window_m"]))
    regions = {**assembly_regions, "hazard": hazard, access_id: access}
    reader = reader_factory(regions)

    # Evidence-only preflight divert guesses; the activated route is selected
    # per vehicle at divert request time from measured-clear candidates.
    initial_divert = {}
    divert_preflight = {}
    for vehicle_id in vehicle_ids:
        state = city._traffic_state[vehicle_id]
        ahead, yaw = state["base_route"].sample(state["distance"] + 8.0, loop=True)
        lateral = (-math.sin(yaw) * 4.0, math.cos(yaw) * 4.0)
        pose = vehicle_pose[vehicle_id]
        divert = ((pose[0] + lateral[0], pose[1] + lateral[1], pose[2]),
                  (ahead[0] + lateral[0], ahead[1] + lateral[1], pose[2]))
        initial_divert[vehicle_id] = divert
        divert_preflight[vehicle_id] = reader.read_route(
            f"vehicle:{vehicle_id}:divert-preflight",
            tuple(tuple(point[axis] + vehicle_offset[vehicle_id][axis] for axis in range(3))
                  for point in (pose, *divert)),
            half_extents_m=vehicle_half[vehicle_id], episode_id=run_id, time_s=city.elapsed,
            moving_entity_id=vehicle_id)
    trace["geometry"] = {
        "roster_ids": roster_ids, "vehicle_ids": list(vehicle_ids),
        "assembly_regions": assembly_regions, "assembly_targets": assembly_targets,
        "hazard": hazard, "access": access, "access_region_id": access_id,
        "anchor_region_id": anchor_region, "anchor_xyz": anchor_xyz,
        "incident_sample_xyz": incident_xyz, "incident_sample_time_s": 0.0,
        "initial_envelopes": initial_envelopes,
        "person_half_extents_m": person_half, "person_center_offsets_m": person_offset,
        "vehicle_half_extents_m": vehicle_half, "vehicle_center_offsets_m": vehicle_offset,
        "vehicle_poses": vehicle_pose, "divert_preflight": divert_preflight,
        "spawn_records": spawn_records,
    }
    _checkpoint(report_path, report)

    receiver = CityIncidentReceiver(episode_id=run_id, service_id=endpoint["service_id"])
    assignments = {service: {"agent_id": "go2_1", "region_id": "road_surface"}
                   for service in ("guide_people", "control_traffic", "report_incident")}
    affected_people = {spec.person_id: {
        "speed_mps": float(spec.jog_speed_mps), "motion": "jog",
        "assembly_region_id": spec.assembly_region_id,
        "assembly_waypoints": [list(point) for point in spec.evacuation_route[1:]],
        "body_half_extents_m": list(person_half[spec.person_id]),
        "body_center_offset_m": list(person_offset[spec.person_id])} for spec in specs}
    city.bind_city_service_sources(
        affected_people=affected_people,
        following_vehicle_ids=vehicle_ids,
        vehicle_divert_routes={vehicle_id: initial_divert[vehicle_id] for vehicle_id in vehicle_ids},
        vehicle_half_extents_m={vehicle_id: vehicle_half[vehicle_id] for vehicle_id in vehicle_ids},
        vehicle_center_offsets_m={vehicle_id: vehicle_offset[vehicle_id] for vehicle_id in vehicle_ids},
        assembly_regions=assembly_regions, hazard_region_id="hazard",
        access_region_id=access_id, region_reader=reader,
        assignment=assignments, incident_receiver=receiver)
    runner = CityNativeServiceRunner(
        city, episode_id=run_id, agent_id="go2_1", region_id="road_surface",
        assembly_region_id=anchor_region, recipient_service_id=endpoint["service_id"],
        role_source_id="isaac.robot_tensor_role", admission_source_id="isaac.native_region_reader",
        scene_query_support=True, clearance_query_provenance="episode_timestamped_physx_and_usd",
        physx_readback_suppressed=False)
    if runner.readiness["status"] != "ready":
        raise RuntimeError("City native runner is not ready: " + ",".join(runner.readiness["blockers"]))

    person_start = {person_id: tuple(float(value) for value in city.people.position(person_id))
                    for person_id in roster_ids}
    guidance_requests = {}
    # One roster-wide observation snapshot serves every per-region request:
    # consecutive observes without a step would collide on the same ref.
    # C1 复盘：t=0.02 即提交时，出生车辆包络常压在穿路航点上（dynamic
    # envelopes 使 routes_clear=false）。先预热 ~10 s 仿真让车流离开出生
    # 段，再做首次观察/提交（人员静止等待，语义为"观察后开始引导"）。
    warmup_steps = max(1, math.ceil(10.0 / float(session.base_env.step_dt)))
    _step(session, city, actions, warmup_steps)
    guidance_observation = runner.observe()
    # C1 诊断：首次提交前记录逐人路线 readback（admission 的 routes_clear 谓词输入）
    first_routes = {person_id: city._episode_service._person_route(person_id)
                    for person_id in roster_ids}
    trace["person_routes_before_first_submit"] = first_routes
    _checkpoint(report_path, report)
    # Replan from measured collisions before submitting any service. A hit is
    # not proof that the semantic map omitted that obstacle.
    discovered = []
    _bbox_cache = None
    for person_id, row in first_routes.items():
        for hit in row.get("static_hits") or []:
            # distance 是间距不是沿线里程——真实位置从 stage 的 prim 世界
            # 包围盒解析（错误地把 distance 当里程会把避障盘放错位置）。
            pos = None
            try:
                from pxr import Usd, UsdGeom
                prim = city.stage.GetPrimAtPath(str(hit.get("rigid_body") or hit.get("collision")))
                if prim and prim.IsValid():
                    if _bbox_cache is None:
                        _bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                                                        [UsdGeom.Tokens.default_], False)
                    bounds = _bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
                    lo, hi = bounds.GetMin(), bounds.GetMax()
                    pos = ((lo[0] + hi[0]) * .5, (lo[1] + hi[1]) * .5)
            except Exception:
                pos = None
            if pos:
                discovered.append({"person_id": person_id,
                                   "collision": hit.get("collision"),
                                   "world_xy_m": [round(pos[0], 2), round(pos[1], 2)]})
    dynamic_snapshot = envelopes()
    dynamic_blocked = any(row.get("dynamic_envelopes") for row in first_routes.values())
    if discovered or dynamic_blocked:
        trace["route_collision_obstacles"] = discovered
        trace["replan_dynamic_envelopes"] = dynamic_snapshot
        for item in discovered:
            hx, hy = item["world_xy_m"]
            # 盘半径 0.6：0.35 时 A* 能绕但灯臂+0.5 m 格粒度下仍擦碰
            # （位置解析修正后 0.6 不再误封走廊——早前失败源于错误位置）。
            for ix in range(math.floor((hx - .6) * 2), math.floor((hx + .6) * 2) + 1):
                for iy in range(math.floor((hy - .6) * 2), math.floor((hy + .6) * 2) + 1):
                    route_grid_cells.add((ix, iy))
        second_pass = _instantiate_evacuation_routes(
            binding, specs, incident_xyz, [],
            extra_grid=route_grid_cells,
            live_origins={person_id: tuple(float(v) for v in city.people.position(person_id))
                          for person_id in roster_ids},
            person_extra_grids={person_id: _dynamic_route_grid(
                dynamic_snapshot, person_id, person_half[person_id], person_offset[person_id])
                for person_id in roster_ids})
        second_pass.pop("_grid_cells_holder", None)
        report["trace"]["route_instantiation_second_pass"] = second_pass
        _checkpoint(report_path, report)
        city.bind_city_service_sources(
            affected_people={spec.person_id: {
                "speed_mps": float(spec.jog_speed_mps), "motion": "jog",
                "assembly_region_id": spec.assembly_region_id,
                "assembly_waypoints": [list(point) for point in spec.evacuation_route[1:]],
                "body_half_extents_m": list(person_half[spec.person_id]),
                "body_center_offset_m": list(person_offset[spec.person_id])} for spec in specs},
            following_vehicle_ids=vehicle_ids,
            vehicle_divert_routes={vehicle_id: initial_divert[vehicle_id] for vehicle_id in vehicle_ids},
            vehicle_half_extents_m={vehicle_id: vehicle_half[vehicle_id] for vehicle_id in vehicle_ids},
            vehicle_center_offsets_m={vehicle_id: vehicle_offset[vehicle_id] for vehicle_id in vehicle_ids},
            assembly_regions=assembly_regions, hazard_region_id="hazard",
            access_region_id=access_id, region_reader=reader,
            assignment=assignments, incident_receiver=receiver)
        runner = CityNativeServiceRunner(
            city, episode_id=run_id, agent_id="go2_1", region_id="road_surface",
            assembly_region_id=anchor_region, recipient_service_id=endpoint["service_id"],
            role_source_id="isaac.robot_tensor_role", admission_source_id="isaac.native_region_reader",
            scene_query_support=True, clearance_query_provenance="episode_timestamped_physx_and_usd",
            physx_readback_suppressed=False)
        if runner.readiness["status"] != "ready":
            raise RuntimeError("City native runner is not ready (second pass): "
                               + ",".join(runner.readiness["blockers"]))
        _role(runner, "guidance_contact", session, trace)
        guidance_observation = runner.observe()
        trace["person_routes_before_first_submit"] = {
            person_id: city._episode_service._person_route(person_id)
            for person_id in roster_ids}
        _checkpoint(report_path, report)
    # Stop following traffic through its public service before pedestrians
    # cross. Keep it held until the complete evacuation trace is recorded.
    reaction_steps = max(1, math.ceil(
        (float(window["trajectory_reaction_window_s"]) + 0.1) / dt))
    _role(runner, "traffic_control_point", session, trace)
    hold_before = {vehicle_id: city.following_vehicle_pose(vehicle_id) for vehicle_id in vehicle_ids}
    held = runner.control_following_traffic(vehicle_ids, "hold", observation_ref=guidance_observation)
    hold_start = city.elapsed
    _step(session, city, actions, reaction_steps)
    hold_readbacks = {vehicle_id: runner.record_vehicle_readback(
        held["request"]["request_id"], vehicle_id,
        previous_position=hold_before[vehicle_id], status_before="hold") for vehicle_id in vehicle_ids}
    trace["services"]["traffic_hold"] = {
        "status": "readback_recorded", "request": held["request"], "readback": hold_readbacks,
        "position_before": {key: list(value) for key, value in hold_before.items()},
        "position_after": {key: list(city.following_vehicle_pose(key)) for key in vehicle_ids},
        "review": runner.review(held["request"]["request_id"])}
    hold_span = city.elapsed - hold_start
    _checkpoint(report_path, report)
    guidance_observation = runner.observe()
    for capacity in binding["assembly_capacity"]:
        region_id, group = capacity["region_id"], tuple(capacity["bound_person_ids"])
        _role(runner, "guidance_contact", session, trace)
        # C1 复盘：穿路航点可能被行驶车辆包络间歇覆盖——被拒时步进 1 s
        # 重建观察重试（语义：等待引导时机），20 次后如实失败。
        guidance = None
        for attempt in range(20):
            try:
                guidance = runner.guide_people(group, observation_ref=guidance_observation,
                                               assembly_region_id=region_id)
                break
            except ValueError as exc:
                if "not admitted" not in str(exc) or attempt == 19:
                    raise
                _step(session, city, actions, max(1, math.ceil(1.0 / float(session.base_env.step_dt))))
                # 步进会使 role 区间过期：重试前重建现场角色
                _role(runner, "guidance_contact", session, trace)
                guidance_observation = runner.observe()
        guidance_requests[region_id] = guidance["request"]
        for person_id in group:
            city.people.resume(person_id)
        trace["services"][f"guide_people:{region_id}"] = {
            "status": "command_accepted_pending_readback",
            "request": guidance["request"],
            "position_before": {person_id: list(person_start[person_id]) for person_id in group},
        }
        _checkpoint(report_path, report)
    arrival_budget_s = float(default_deadline_policy()["people_assembly_arrival_s"])
    for step in range(math.ceil(arrival_budget_s / dt)):
        _step(session, city, actions)
        if step % max(1, math.ceil(1.0 / dt)) == 0:
            trace["guidance_progress"] = {
                "time_s": city.elapsed,
                "people": {person_id: city._episode_service.people_controller.readback(
                    person_id, city._episode_service.assembly_regions)
                           for person_id in roster_ids},
            }
            _checkpoint(report_path, report)
        if all(city._episode_service.people_controller.readback(
                   person_id, city._episode_service.assembly_regions)["arrived"]
               for person_id in roster_ids):
            break
    else:
        raise RuntimeError(f"bound roster did not reach assembly within {arrival_budget_s:g} seconds")
    person_readbacks = {}
    for capacity in binding["assembly_capacity"]:
        region_id, request = capacity["region_id"], guidance_requests[capacity["region_id"]]
        rows = {}
        for person_id in capacity["bound_person_ids"]:
            rows[person_id] = runner.record_person_arrival(
                request["request_id"], person_id,
                previous_position=person_start[person_id],
                assembly_region_id=region_id, arrived=False)
        person_readbacks[region_id] = rows
        trace["services"][f"guide_people:{region_id}"] = {
            "status": "readback_recorded",
            "request": request, "readback": rows,
            "review": runner.review(request["request_id"])}
        _checkpoint(report_path, report)

    reaction_steps = max(1, math.ceil(
        (float(window["trajectory_reaction_window_s"]) + 0.1) / dt))
    reaction_span = {"hold": hold_span}
    controlled_ids: set[str] = set(vehicle_ids)
    executed_modes = ["hold"]
    for mode in ("release", "divert"):
        observation = runner.observe()
        _role(runner, "traffic_control_point", session, trace)
        before = {vehicle_id: city.following_vehicle_pose(vehicle_id) for vehicle_id in vehicle_ids}
        if mode == "divert":
            for vehicle_id in vehicle_ids:
                current_half = _bounds_half(envelopes()[vehicle_id], margin=.1)
                current_half = (*current_half[:2], max(.05, current_half[2] - .05))
                chosen, swept_half, attempts = _divert_candidates(
                    city, vehicle_id, reader, current_half, vehicle_offset[vehicle_id])
                trace.setdefault("divert_candidates", {})[vehicle_id] = attempts
                _checkpoint(report_path, report)
                if chosen is None:
                    raise RuntimeError(f"all bounded divert candidates are blocked: {vehicle_id}")
                city._episode_service.divert_routes[vehicle_id] = chosen
                city._episode_service.vehicle_half_extents[vehicle_id] = swept_half
            _checkpoint(report_path, report)
        controlled = runner.control_following_traffic(
            vehicle_ids, mode, observation_ref=observation)
        executed_modes.append(mode)
        controlled_ids.update(controlled["request"]["entity_ids"])
        trace["services"][f"traffic_{mode}"] = {
            "status": "command_accepted_pending_readback",
            "request": controlled["request"],
            "position_before": {vehicle_id: list(before[vehicle_id]) for vehicle_id in vehicle_ids},
        }
        _checkpoint(report_path, report)
        start_reaction = city.elapsed
        _step(session, city, actions, reaction_steps)
        reaction_span[mode] = city.elapsed - start_reaction
        readbacks = {}
        for vehicle_id in vehicle_ids:
            readbacks[vehicle_id] = runner.record_vehicle_readback(
                controlled["request"]["request_id"], vehicle_id,
                previous_position=before[vehicle_id], status_before=mode)
        trace["services"][f"traffic_{mode}"] = {
            "status": "readback_recorded",
            "request": controlled["request"],
            "position_before": {vehicle_id: list(before[vehicle_id]) for vehicle_id in vehicle_ids},
            "position_after": {vehicle_id: list(city.following_vehicle_pose(vehicle_id))
                               for vehicle_id in vehicle_ids},
            "readback": readbacks, "review": runner.review(controlled["request"]["request_id"])}
        _checkpoint(report_path, report)

    observation = runner.observe()
    _role(runner, "incident_observer", session, trace)
    incident_result = runner.report_incident(observation_ref=observation)
    trace["services"]["report_incident"] = {
        "status": "receipt_recorded_pending_continuous_access",
        **incident_result,
    }
    _checkpoint(report_path, report)
    receipt_time = city.elapsed
    access_steps = max(1, math.ceil(float(endpoint["continuous_access_min_span_s"]) / dt)) + 1
    _step(session, city, actions, access_steps)
    access_span = city.elapsed - receipt_time
    access_result = runner.record_continuous_access(
        incident_result["request"], start_time_s=receipt_time, end_time_s=city.elapsed)
    trace["services"]["report_incident"] = {
        "status": "continuous_access_recorded", **incident_result, "access": access_result,
        "review": runner.review(incident_result["request"]["request_id"])}
    _checkpoint(report_path, report)

    evidence = {
        "person_spawn": {person_id: {
            "bound_start_xyz": list(spec.start_xyz),
            "readback_start_xyz": list(spawn_readback[person_id])}
            for person_id, spec in zip(roster_ids, specs)},
        "person_guidance": {},
        "assembly_regions": {},
        "hazard": {
            "incident_xyz": list(incident_xyz),
            "road_height_m": float(city.config.road_height),
            "registered_region": hazard,
            "readback_status": (city._episode_service._latest_region("hazard") or {}).get("status"),
        },
        "access": {
            "anchor_region_id": anchor_region,
            "anchor_xyz": list(anchor_xyz),
            "declared_region_ids": [f"rescue_access_{index}"
                                    for index in range(len(access_candidates))],
            "selected_region_id": access_id,
            "registered_region": access,
            "readback_status": (city._episode_service._latest_region(access_id) or {}).get("status"),
        },
        "window": {
            "bound": dict(window),
            "runtime_policy": default_deadline_policy(),
            "bound_vehicle_ids": list(vehicle_ids),
            "controlled_vehicle_ids": sorted(controlled_ids),
            "executed_modes": executed_modes,
            "reaction_span_s": min(reaction_span.values()) if reaction_span else 0.0,
        },
        "endpoint": {
            "bound": dict(endpoint),
            "receiver_runtime_class": f"{type(receiver).__module__}.{type(receiver).__qualname__}",
            "recipient_service_id": incident_result["request"]["recipient_service_id"],
            "runtime_policy": default_deadline_policy(),
            "continuous_access_span_s": access_span,
        },
    }
    for capacity in binding["assembly_capacity"]:
        region_id = capacity["region_id"]
        region_readback = city._episode_service._latest_region(region_id) or {}
        evidence["assembly_regions"][region_id] = {
            "registered_capacity": assembly_regions[region_id]["capacity"],
            "readback_status": region_readback.get("status"),
            "occupants": sorted((region_readback.get("dynamic_envelopes") or {})),
        }
        for person_id in capacity["bound_person_ids"]:
            native = (person_readbacks[region_id][person_id].get("native_readback") or {})
            evidence["person_guidance"][person_id] = {
                "bound_assembly_region_id": region_id,
                "request_assembly_region_id": region_id,
                "readback_assembly_region_id": native.get("assembly_region_id"),
                "arrived": native.get("arrived"),
                "inside_assembly": native.get("inside_assembly"),
            }
    # Compare against the frozen preparation, never a worker-mutated roster.
    binding_review = _review_formal_binding(_prepared_binding(prepared), evidence)
    reviews = [row["review"] for row in trace["services"].values()]
    all_complete = all(row.get("status") == "evidence_complete" for row in reviews)
    if binding_review["formal_episode_binding"]:
        full_city_service = ("formal_episode_binding_measured" if all_complete
                             else FORMAL_BINDING_MISMATCH_LABELS["incomplete"])
    else:
        full_city_service = FORMAL_BINDING_MISMATCH_LABELS["mismatch"]
    report.update(status="completed", execution_validated=True,
                  candidate_witness="evidence_complete" if all_complete else "pending",
                  formal_episode_binding=binding_review["formal_episode_binding"],
                  formal_binding_review=binding_review,
                  full_city_service=full_city_service,
                  formal_binding_remaining_gaps=[
                      "services_driven_by_host_probe_not_participant_requests",
                      "hidden_evaluator_witness_execution_external_pending",
                      "single_episode_no_determinism_repetition",
                  ],
                  device_actual=str(session.device), episode_time_s=city.elapsed,
                  source_hashes_after=_hashes())
    report["sources_unchanged"] = report["source_hashes_after"] == prepared["source_hashes"]
    if not report["sources_unchanged"]:
        raise RuntimeError("City service probe sources changed after prepare")
    _checkpoint(report_path, report)


def execute_probe(output_root: Path, *, device: str = "cpu", seed: int = 17,
                  headless: bool = True, overwrite: bool = False) -> dict[str, Any]:
    with _serial_lock():
        prepared = _load_prepare(output_root)
        if prepared["device"] != device or int(prepared["seed"]) != int(seed):
            raise ValueError("execute device/seed must match prepare")
        if prepared["source_hashes"] != _hashes():
            raise RuntimeError("City service probe sources changed; prepare again")
        if (output_root / "service_probe_start.json").exists() and not overwrite:
            raise FileExistsError("City service probe launch already exists")
        return _execute(output_root, prepared, run_id=str(uuid.uuid4()), headless=headless)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--episode-binding", type=Path, default=None,
                        help="prepare only: author-frozen binding document or "
                             "a prepare package authoring/private.json")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.episode_binding is not None and not args.prepare:
        parser.error("--episode-binding applies only to --prepare")
    if args.prepare:
        result = prepare_probe(args.output_root, device=args.device, seed=args.seed,
                               overwrite=args.overwrite, episode_binding=args.episode_binding)
    else:
        result = execute_probe(args.output_root, device=args.device, seed=args.seed,
                               headless=not args.gui, overwrite=args.overwrite)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if args.prepare or result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
