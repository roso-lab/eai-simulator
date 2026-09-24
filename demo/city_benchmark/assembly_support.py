"""Bounded, read-only support evidence for person-independent City candidates.

This does not enable the v2 execution profile. It measures frozen map disks
against live PhysX support samples and the hit static USD collider identities. No
person binding, body size, reservation or task assignment is an input.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .guide_admission import (
    REQUESTED_TARGET_V2_PROFILE,
    canonical_state_sha256,
    validate_semantic_map_sha256,
)

PROFILE = "city_static_assembly_support_v1"
EXECUTION_PROFILE = REQUESTED_TARGET_V2_PROFILE
FOOTPRINT_SCHEMA = "eai.city.assembly_footprint_profile"
FOOTPRINT_VERSION = 2
TOLERANCE_M = 1e-5
_IDS = {f"assembly_candidate_{index:03d}" for index in range(1, 5)}


def _number(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("support measurement requires finite numeric values")
    return float(value)


def _point(value: Any) -> tuple[float, float, float]:
    result = tuple(_number(v) for v in value)
    if len(result) != 3:
        raise ValueError("support XYZ must have three components")
    return result


def _ray_coordinate_tolerance(value: float) -> float:
    # PhysX returns float32 world coordinates. Four ULPs cover input/output
    # rounding at large world offsets without changing any candidate bounds.
    exponent = math.frexp(max(abs(value), 1.0))[1]
    return max(TOLERANCE_M, 4 * math.ldexp(1.0, exponent - 24))


def _footprints(public_regions: Any, semantic_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (semantic_map.get("schema") != "eai.city.semantic_map"
            or type(semantic_map.get("version")) is not int or semantic_map.get("version") != 1
            or semantic_map.get("coordinate_frame") != "city_world"
            or semantic_map.get("up_axis") != "Z"
            or type(semantic_map.get("meters_per_unit")) not in (int, float)
            or semantic_map.get("meters_per_unit") != 1.0):
        raise ValueError("unsupported City semantic-map frame or version")
    public = [row for row in public_regions if row["kind"] == "assembly_candidate"]
    if len(public) != 4 or {row["id"] for row in public} != _IDS:
        raise ValueError("support profile requires all four public City candidates")
    if any(type(row["resource_capacity"]) is not int or row["resource_capacity"] != 1
           for row in public):
        raise ValueError("support profile preserves capacity one")
    map_regions = [row for row in semantic_map["regions"] if row["kind"] == "assembly_candidate"]
    if (len(map_regions) != 4 or {row["id"] for row in map_regions} != _IDS
            or any(type(row.get("resource_capacity")) is not int
                   or row["resource_capacity"] != 1 for row in map_regions)):
        raise ValueError("public/map candidate identity or capacity mismatch")
    bindings = [row for row in semantic_map["bindings"] if row["id"] in _IDS]
    if len(bindings) != 4 or {row["id"] for row in bindings} != _IDS:
        raise ValueError("support profile requires one map binding per candidate")
    plane = _number(semantic_map["surface_heights_m"]["sidewalks"])
    if plane != 0.3:
        raise ValueError("support profile preserves the City sidewalk reference plane")
    result = []
    for row in sorted(bindings, key=lambda item: item["id"]):
        geometry = row["geometry"]
        coordinates = tuple(_number(v) for v in geometry["coordinates"])
        radius = _number(row["candidate_radius_m"])
        if geometry["type"] != "Point" or len(coordinates) != 2 or radius != 0.75:
            raise ValueError("support profile preserves map Point/radius 0.75m")
        result.append({"region_id": row["id"], "footprint": "disk",
                       "center_xy_m": list(coordinates), "radius_m": radius,
                       "support_plane_z_m": plane, "capacity": 1})
    return result


def resolve_assembly_footprint_profile(
        *, semantic_map_path: str | Path, expected_semantic_map_sha256: str,
        public_regions: Any) -> dict[str, Any]:
    """Resolve the exact public/map v2 disks without person-derived geometry."""
    digest = validate_semantic_map_sha256(expected_semantic_map_sha256)
    payload = Path(semantic_map_path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("semantic map differs from the frozen expected digest")
    geometries = _footprints(public_regions, json.loads(payload))
    candidates = [{**row, "geometry_sha256": canonical_state_sha256(row)}
                  for row in geometries]
    return {
        "schema": FOOTPRINT_SCHEMA,
        "version": FOOTPRINT_VERSION,
        "profile": EXECUTION_PROFILE,
        "semantic_map_sha256": digest,
        "candidate_geometry_sha256": canonical_state_sha256(
            {"candidates": geometries}),
        "candidates": candidates,
    }


def validate_assembly_footprint_profile(
        value: Mapping[str, Any], *, expected_semantic_map_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate and copy one resolved v2 profile for execution consumers."""
    if (not isinstance(value, Mapping) or value.get("schema") != FOOTPRINT_SCHEMA
            or type(value.get("version")) is not int
            or value.get("version") != FOOTPRINT_VERSION
            or value.get("profile") != EXECUTION_PROFILE):
        raise ValueError("unsupported City assembly footprint profile")
    digest = validate_semantic_map_sha256(value.get("semantic_map_sha256"))
    if expected_semantic_map_sha256 is not None \
            and digest != validate_semantic_map_sha256(expected_semantic_map_sha256):
        raise ValueError("assembly footprint semantic map mismatch")
    rows = value.get("candidates")
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("assembly footprint profile requires four candidates")
    candidates = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("region_id") not in _IDS:
            raise ValueError("invalid assembly footprint candidate identity")
        geometry = {
            "region_id": row["region_id"],
            "footprint": row.get("footprint"),
            "center_xy_m": list(row.get("center_xy_m", ())),
            "radius_m": row.get("radius_m"),
            "support_plane_z_m": row.get("support_plane_z_m"),
            "capacity": row.get("capacity"),
        }
        if (geometry["footprint"] != "disk"
                or len(geometry["center_xy_m"]) != 2
                or any(type(component) not in (int, float)
                       or not math.isfinite(component)
                       for component in geometry["center_xy_m"])
                or _number(geometry["radius_m"]) != 0.75
                or _number(geometry["support_plane_z_m"]) != 0.3
                or type(geometry["capacity"]) is not int
                or geometry["capacity"] != 1
                or row.get("geometry_sha256") != canonical_state_sha256(geometry)):
            raise ValueError("assembly footprint candidate geometry mismatch")
        candidates.append({**geometry, "center_xy_m": [
            float(component) for component in geometry["center_xy_m"]],
            "radius_m": float(geometry["radius_m"]),
            "support_plane_z_m": float(geometry["support_plane_z_m"]),
            "geometry_sha256": row["geometry_sha256"]})
    if len({row["region_id"] for row in candidates}) != 4:
        raise ValueError("assembly footprint candidate identities must be unique")
    candidates.sort(key=lambda row: row["region_id"])
    geometry_digest = canonical_state_sha256({
        "candidates": [{key: item[key] for key in (
            "region_id", "footprint", "center_xy_m", "radius_m",
            "support_plane_z_m", "capacity")} for item in candidates]})
    if value.get("candidate_geometry_sha256") != geometry_digest:
        raise ValueError("assembly footprint geometry digest mismatch")
    return {
        "schema": FOOTPRINT_SCHEMA, "version": FOOTPRINT_VERSION,
        "profile": EXECUTION_PROFILE, "semantic_map_sha256": digest,
        "candidate_geometry_sha256": geometry_digest,
        "candidates": candidates,
    }


def _support_sources(stage: Any, paths: set[str]) -> list[dict[str, Any]]:
    from pxr import UsdGeom, UsdPhysics

    if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z \
            or UsdGeom.GetStageMetersPerUnit(stage) != 1.0:
        raise ValueError("native stage must use metres and Z-up")
    sources = []
    for path in sorted(paths):
        prim = stage.GetPrimAtPath(path)
        if (not prim or not prim.IsActive() or not prim.IsLoaded()
                or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not True):
            raise ValueError("support requires enabled static colliders")
        parent = prim
        while parent and not parent.IsPseudoRoot():
            if parent.HasAPI(UsdPhysics.RigidBodyAPI):
                raise ValueError("dynamic/rigid-body support is outside this profile")
            parent = parent.GetParent()
        sources.append({"collision": path, "prim_type": prim.GetTypeName(),
                        "collision_enabled": True, "rigid_body_ancestor": False})
    return sources


def _measure_candidate(stage: Any, query: Any, geometry: Mapping[str, Any]) -> dict[str, Any]:
    row = {**geometry, "geometry_sha256": canonical_state_sha256(geometry),
           "status": "unknown", "support_samples": [], "support_sources": []}
    x, y = geometry["center_xy_m"]
    plane, radius = geometry["support_plane_z_m"], geometry["radius_m"]
    samples = [(x, y)] + [(x + radius * math.cos(index * math.pi / 4),
                           y + radius * math.sin(index * math.pi / 4)) for index in range(8)]
    try:
        _support_sources(stage, set())  # Frame validity precedes factual negatives.
        paths = set()
        unknown, unsupported = [], []
        for sx, sy in samples:
            origin = (sx, sy, plane + 0.5)
            sample = {"origin_m": list(origin)}
            row["support_samples"].append(sample)
            try:
                raw = query.raycast_closest(origin, (0.0, 0.0, -1.0), 1.0)
                if not isinstance(raw, Mapping) or type(raw.get("hit")) is not bool:
                    raise ValueError("malformed native support ray response")
                sample["hit"] = raw["hit"]
                if not raw["hit"]:
                    unsupported.append("support_sample_missing")
                    continue
                position, normal = _point(raw["position"]), _point(raw["normal"])
                collision = raw["collision"]
                if not isinstance(collision, str) or not collision.startswith("/"):
                    raise ValueError("native support ray lacks collision identity")
                paths.add(collision)
                distance = _number(raw["distance"])
                xy_tolerance = [_ray_coordinate_tolerance(origin[i]) for i in range(2)]
                sample.update(position_m=list(position), normal=list(normal),
                              distance_m=distance, collision=collision,
                              ray_xy_consistency_tolerance_m=xy_tolerance)
                if not 0 <= distance <= 1.0 or abs(origin[2] - position[2] - distance) > TOLERANCE_M:
                    raise ValueError("native support hit position/distance mismatch")
                if any(abs(position[i] - origin[i]) > xy_tolerance[i] for i in range(2)):
                    raise ValueError("native support hit is outside the requested ray")
                if (abs(position[2] - plane) > TOLERANCE_M
                        or any(abs(normal[i] - (1.0 if i == 2 else 0.0)) > TOLERANCE_M
                               for i in range(3))):
                    unsupported.append("support_plane_or_normal_mismatch")
            except Exception as exc:
                sample["error"] = f"{type(exc).__name__}:{exc}"
                unknown.append(sample["error"])
        try:
            row["support_sources"] = _support_sources(stage, paths)
        except Exception as exc:
            unknown.append(f"{type(exc).__name__}:{exc}")
        row.update(status="unknown" if unknown else "unsupported" if unsupported else "sampled_supported",
                   reason=("support_read_failed:" + ";".join(sorted(set(unknown))) if unknown
                           else ";".join(sorted(set(unsupported))) if unsupported else None))
    except Exception as exc:
        row.update(status="unknown", reason=f"support_read_failed:{type(exc).__name__}:{exc}")
    return row


def measure_assembly_supports(stage: Any, query: Any, *, semantic_map_path: str | Path,
                              expected_semantic_map_sha256: str, public_regions: Any,
                              episode_id: str, source_id: str,
                              native_clock: Callable[[], float], scene_queries_enabled: bool,
                              device: str, current_gpu_geometry: bool = False) -> dict[str, Any]:
    """Measure all four frozen disks; no controls, reservations or file writes.

    Malformed host inputs raise. Unsupported or missing native evidence remains
    explicit per-candidate evidence and never becomes a deployment label.
    """
    profile = resolve_assembly_footprint_profile(
        semantic_map_path=semantic_map_path,
        expected_semantic_map_sha256=expected_semantic_map_sha256,
        public_regions=public_regions)
    digest = profile["semantic_map_sha256"]
    geometries = [{key: row[key] for key in (
        "region_id", "footprint", "center_xy_m", "radius_m",
        "support_plane_z_m", "capacity")} for row in profile["candidates"]]
    if not all(isinstance(v, str) and v.strip() for v in (episode_id, source_id)):
        raise ValueError("support measurement requires episode/source identity")
    before = _number(native_clock())
    if before < 0:
        raise ValueError("native clock must be nonnegative")
    unavailable = ("scene_query_unavailable" if scene_queries_enabled is not True or query is None
                   else "dynamic_query_pose_not_synchronized"
                   if device.startswith("cuda") and current_gpu_geometry is not True else None)
    rows = ([{**geometry, "geometry_sha256": canonical_state_sha256(geometry),
              "status": "unknown", "reason": unavailable,
              "support_samples": [], "support_sources": []} for geometry in geometries]
            if unavailable else [_measure_candidate(stage, query, row) for row in geometries])
    after = _number(native_clock())
    if after < 0:
        raise ValueError("native clock after capture must be nonnegative")
    stable = before == after
    if not stable:
        for row in rows:
            row.update(status="unknown", reason="native_clock_changed_during_support_capture")
    return {"schema": "eai.city.assembly_support", "version": 1, "profile": PROFILE,
            "episode_id": episode_id, "source_id": source_id, "captured_native_s": before,
            "native_clock_after_s": after, "same_tick": stable,
            "semantic_map_sha256": digest, "candidate_geometry_sha256": canonical_state_sha256(
                {"candidates": geometries}), "measurement_tolerance_m": TOLERANCE_M,
            "ray_xy_tolerance_policy": "max_1e-5m_or_4_float32_world_coordinate_ULPs",
            "candidates": rows, "all_candidates_sampled_supported": all(
                row["status"] == "sampled_supported" for row in rows),
            "qualification_scope": "nine_support_samples_per_map_disk_not_continuous_support_or_body_clearance",
            "continuous_support_validated": False,
            "execution_profile_enabled": False, "scoreable": False,
            "execution_validated": False, "business_outcomes_validated": False}


__all__ = [
    "EXECUTION_PROFILE", "FOOTPRINT_SCHEMA", "FOOTPRINT_VERSION", "PROFILE",
    "TOLERANCE_M", "measure_assembly_supports",
    "resolve_assembly_footprint_profile", "validate_assembly_footprint_profile",
]
