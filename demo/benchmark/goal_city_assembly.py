"""Public target route proposals and allowlisted City assembly measurements.

The static map proposes paths, never certifies them. Native body-envelope
queries must measure every proposed path before admission. No author route,
person-to-target binding, or actor relocation is used here.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any

from demo.city_benchmark.response import _astar_leg, _obstacle_grid, _segment_obstruction
from demo.city_benchmark.guide_admission import REQUESTED_TARGET_V2_PROFILE
from .provenance import canonical_sha256, file_sha256

PROFILE = "requested_target_v1"
SUPPORT_FOOTPRINT_PROFILE = REQUESTED_TARGET_V2_PROFILE


def _point(value: Any, name: str) -> list[float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in value)):
        raise ValueError(f"{name} requires three finite metres")
    return [float(v) for v in value]


def _number(value: Any, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


class CityRequestedTargetRouteProvider:
    """Bounded static proposal; failure means unknown, not proven unreachable."""

    def __init__(self, semantic_map: Mapping[str, Any], *, source_sha256: str | None = None):
        if not isinstance(semantic_map, Mapping):
            raise ValueError("City route provider requires a semantic map")
        obstacles = semantic_map.get("static_obstacles")
        if not isinstance(obstacles, list):
            raise ValueError("semantic map must explicitly contain static_obstacles")
        for row in obstacles:
            if not isinstance(row, Mapping) or not isinstance(row.get("bounds_m"), list) \
                    or len(row["bounds_m"]) != 2:
                raise ValueError("invalid static obstacle bounds")
            lo, hi = (_point(v, "obstacle bounds") for v in row["bounds_m"])
            if any(a > b for a, b in zip(lo, hi)):
                raise ValueError("inverted static obstacle bounds")
        self._map = deepcopy(dict(semantic_map))
        self.semantic_map_sha256 = source_sha256 or canonical_sha256(self._map)
        if (len(self.semantic_map_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.semantic_map_sha256)):
            raise ValueError("semantic map digest must be SHA256")
        self._grids: dict[float, Any] = {}

    @classmethod
    def from_path(cls, path: str | Path) -> "CityRequestedTargetRouteProvider":
        path = Path(path)
        return cls(json.loads(path.read_text(encoding="utf-8")), source_sha256=file_sha256(path))

    def __call__(self, *, person_id: str, current_xyz: Any, target_region_id: str,
                 target_xyz: Any, body_half_extents_m: Any,
                 body_center_offset_m: Any) -> tuple[tuple[float, ...], ...] | None:
        _text(person_id, "person_id")
        _text(target_region_id, "target_region_id")
        current, target = _point(current_xyz, "current_xyz"), _point(target_xyz, "target_xyz")
        half = _point(body_half_extents_m, "body_half_extents_m")
        _point(body_center_offset_m, "body_center_offset_m")
        if any(v <= 0 for v in half) or abs(current[2] - target[2]) > 1e-6:
            return None
        z = current[2]
        if z not in self._grids:
            # Reuse the City's existing coarse proposal map, including its
            # canopy proxy convention. This is NOT a clearance certificate;
            # the full actual body envelope is checked by the native reader.
            self._grids[z] = _obstacle_grid(
                self._map["static_obstacles"], half_xy=0.2,
                z_window=(z - 1.5, z + 1.8))
        grid = self._grids[z]
        leg = _astar_leg(grid, current[:2], target[:2])
        if leg is None:
            return None
        points = [tuple(current)]
        for xy in leg:
            point = (float(xy[0]), float(xy[1]), z)
            if point != points[-1]:
                points.append(point)
        if tuple(target) != points[-1]:
            points.append(tuple(target))
        if len(points) < 2 or _segment_obstruction(points, grid=grid):
            return None
        return tuple(points)


def project_assembly_affordances(raw: Mapping[str, Any], public_task: Mapping[str, Any], *,
                                native_episode_id: str, native_source_id: str,
                                clock_epoch_s: float, observed_person_ids: set[str],
                                captured_at_s: float) -> dict[str, Any]:
    """Publish measurements for every candidate; never publish a chosen answer."""
    profile = raw.get("profile")
    footprint = profile == SUPPORT_FOOTPRINT_PROFILE
    version = 2 if footprint else 1
    if (raw.get("schema") != "eai.city.assembly_affordance" or raw.get("version") != version
            or profile not in {PROFILE, SUPPORT_FOOTPRINT_PROFILE}
            or (footprint and type(raw.get("version")) is not int)
            or raw.get("episode_id") != native_episode_id
            or raw.get("source_id") != native_source_id):
        raise ValueError("assembly affordance source/profile mismatch")
    stamp = _number(raw.get("captured_at_s"), "captured_at_s")
    expiry = _number(raw.get("valid_until_s"), "valid_until_s")
    epoch = _number(clock_epoch_s, "clock_epoch_s")
    if stamp != captured_at_s or not epoch <= stamp < expiry:
        raise ValueError("assembly affordance is not from the entity snapshot tick")
    digest = _text(raw.get("semantic_map_sha256"), "semantic_map_sha256")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid semantic map hash")
    public_regions = {row["id"]: row for row in public_task["public_world"]["regions"]
                      if row["kind"] == "assembly_candidate"}
    geometry_by_id = {}
    if footprint:
        from demo.city_benchmark.assembly_support import validate_assembly_footprint_profile

        geometry = validate_assembly_footprint_profile({
            "schema": "eai.city.assembly_footprint_profile", "version": 2,
            "profile": profile, "semantic_map_sha256": digest,
            "candidate_geometry_sha256": raw.get("candidate_geometry_sha256"),
            "candidates": raw.get("candidates"),
        })
        geometry_by_id = {row["region_id"]: row for row in geometry["candidates"]}
    candidates, seen, claims_by_region = [], set(), {}
    for row in raw.get("candidates", ()):
        identity = _text(row.get("region_id"), "region_id")
        if identity not in public_regions or identity in seen:
            raise ValueError("unknown/duplicate assembly candidate")
        seen.add(identity)
        if not footprint:
            lo, hi = _point(row.get("min"), "candidate.min"), _point(row.get("max"), "candidate.max")
            if any(a >= b for a, b in zip(lo, hi)):
                raise ValueError("invalid assembly bounds")
        counts = {}
        for key in ("capacity", "occupancy_count", "reservation_count"):
            val = row.get(key)
            if type(val) is not int or val < 0 or (key == "capacity" and val == 0):
                raise ValueError(f"invalid assembly {key}")
            counts[key] = val
        if counts["capacity"] != public_regions[identity]["resource_capacity"]:
            raise ValueError("native capacity differs from public task")
        if row.get("status") not in ({"measured", "blocked", "unavailable"} if footprint
                                     else {"measured", "unavailable"}):
            raise ValueError("invalid candidate measurement status")
        reasons = _reasons(row)
        if footprint:
            claims = set()
            for identities, count in (("occupant_ids", "occupancy_count"),
                                      ("reservation_ids", "reservation_count")):
                values = row.get(identities)
                if (not isinstance(values, list)
                        or any(not isinstance(value, str) for value in values)
                        or len(values) != counts[count] or len(set(values)) != len(values)
                        or not set(values) <= observed_person_ids):
                    raise ValueError("invalid trusted candidate capacity identities")
                claims.update(values)
            claims_by_region[identity] = claims
            support = row.get("support_status")
            if support not in {"sampled_supported", "unsupported", "unknown"}:
                raise ValueError("invalid assembly support status")
            if support == "unknown" and (row["status"] != "unavailable"
                                          or "target_support_unknown" not in reasons):
                raise ValueError("unknown support must remain unavailable")
            if support == "unsupported" and (row["status"] == "measured"
                                              or "target_support_unsupported" not in reasons):
                raise ValueError("unsupported candidate cannot be measured available")
            candidate = {**geometry_by_id[identity], **counts, "status": row["status"],
                         "reason_codes": reasons, "support_status": support,
                         "support_evidence_ref": _text(row.get("support_evidence_ref"),
                                                       "support_evidence_ref")}
        else:
            candidate = {"region_id": identity, "min_m": lo, "max_m": hi, **counts,
                         "status": row["status"], "reason_codes": reasons}
        candidates.append(candidate)
    if seen != set(public_regions):
        raise ValueError("assembly affordance must cover every public candidate")
    routes, seen = [], set()
    for row in raw.get("routes", ()):
        key = (row.get("person_id"), row.get("region_id"))
        if key in seen or key[0] not in observed_person_ids or key[1] not in public_regions:
            raise ValueError("unknown/duplicate person-target measurement")
        seen.add(key)
        status = row.get("status")
        if status not in {"admissible", "blocked", "unavailable"}:
            raise ValueError("invalid route measurement status")
        if footprint:
            candidate = next(c for c in candidates if c["region_id"] == key[1])
            # Global fullness does not block the person already owning the
            # claim. Validate private identity deduplication before publishing
            # the per-person result; never expose those identities themselves.
            own_capacity_claim = (
                set(candidate["reason_codes"]) == {"target_capacity_full"}
                and candidate["support_status"] == "sampled_supported"
                and key[0] in claims_by_region[key[1]]
                and len(claims_by_region[key[1]]) == candidate["capacity"])
            if ((candidate["status"] == "unavailable" and status != "unavailable")
                    or (candidate["status"] == "blocked" and status == "admissible"
                        and not own_capacity_claim)):
                raise ValueError("route status contradicts candidate support/measurement")
        route = {"person_id": key[0], "region_id": key[1], "status": status,
                 "reason_codes": _reasons(row), "evidence_ref": _text(row.get("evidence_ref"), "evidence_ref")}
        fingerprint = _text(row.get("state_sha256"), "state_sha256")
        if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
            raise ValueError("invalid route state hash")
        route["state_sha256"] = fingerprint
        for field in ("length_m", "estimated_travel_s"):
            val = row.get(field)
            if status == "admissible" or val is not None:
                val = _number(val, field)
            route[field] = val
        routes.append(route)
    if seen != {(person, region) for person in observed_person_ids for region in public_regions}:
        raise ValueError("assembly routes must cover all observed person-target pairs")
    result = {"schema": "eai.benchmark.city_assembly_affordance", "version": version,
              "profile": profile, "captured_at_s": stamp - epoch,
              "valid_until_s": expiry - epoch, "semantic_map_sha256": digest,
              "candidates": sorted(candidates, key=lambda row: row["region_id"]),
              "routes": sorted(routes, key=lambda row: (row["person_id"], row["region_id"]))}
    if footprint:
        result["candidate_geometry_sha256"] = geometry["candidate_geometry_sha256"]
    return result


def _reasons(row: Mapping[str, Any]) -> list[str]:
    from demo.city_benchmark.guide_admission import PUBLIC_GUIDE_REASON_CODES

    reasons = row.get("reason_codes")
    if not isinstance(reasons, list):
        raise ValueError("measurement reason_codes must be a list")
    result = sorted({_text(value, "reason_code") for value in reasons})
    if not set(result) <= set(PUBLIC_GUIDE_REASON_CODES):
        raise ValueError("unknown public guide reason code")
    return result
