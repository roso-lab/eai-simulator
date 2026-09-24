"""Versioned requested-target guidance admission primitives.

This module contains only the small, simulator-independent contract shared by
the City runtime, the trusted public projection, and focused tests.  Native
scene measurements and reservations remain owned by ``scene.runtime``.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Iterable, Mapping


REQUESTED_TARGET_PROFILE = "requested_target_v1"
REQUESTED_TARGET_V2_PROFILE = "requested_target_v2_support_footprint"
REQUESTED_TARGET_PROFILES = frozenset({
    REQUESTED_TARGET_PROFILE, REQUESTED_TARGET_V2_PROFILE})
ASSEMBLY_AFFORDANCE_SCHEMA = "eai.city.assembly_affordance"
ASSEMBLY_AFFORDANCE_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
GUIDE_ADMISSION_REASON_CODES = frozenset({
    "episode_clock_mismatch",
    "target_region_unknown",
    "person_not_observed",
    "person_not_in_roster",
    "person_reserved_for_other_target",
    "region_readback_unavailable",
    "region_measurement_unknown",
    "capacity_contract_invalid",
    "target_static_blocked",
    "target_capacity_full",
    "target_body_outside_region",
    "target_body_outside_footprint",
    "target_support_unknown",
    "target_support_unsupported",
    "route_provider_unavailable",
    "route_provider_returned_none",
    "route_provider_contract_violation",
    "route_measurement_unknown",
    "route_static_blocked",
    "route_dynamic_blocked",
    "route_proposal_missing",
    "route_proposal_stale",
    "affordance_changed",
    "affordance_expired",
})
# These codes describe only participant-visible request/evidence state. Native
# paths, private bindings, collision identities and evaluator readbacks remain
# in the error/report sidecar and are never part of this allowlist.
PUBLIC_GUIDE_REASON_CODES = GUIDE_ADMISSION_REASON_CODES


class CityGuideAdmissionError(ValueError):
    """Structured native rejection for expected guide-admission outcomes.

    ``infrastructure_failure`` distinguishes missing or contradictory trusted
    evidence from a measured, real-world block such as a full target or a
    colliding route.  ``report`` is evaluator-only and may contain native
    readback detail; callers must project it through an explicit allowlist.
    """

    def __init__(self, reason_codes: Iterable[str], *, infrastructure_failure: bool,
                 report: Mapping[str, Any]):
        codes = tuple(dict.fromkeys(str(code).strip() for code in reason_codes
                                    if isinstance(code, str) and code.strip()))
        if not codes:
            raise ValueError("City guide admission error requires reason codes")
        if any(code not in GUIDE_ADMISSION_REASON_CODES for code in codes):
            raise ValueError("unsupported City guide admission reason code")
        if type(infrastructure_failure) is not bool:
            raise ValueError("infrastructure_failure must be a bool")
        if not isinstance(report, Mapping):
            raise ValueError("City guide admission error report must be a mapping")
        self.reason_codes = codes
        self.infrastructure_failure = infrastructure_failure
        self.report = deepcopy(dict(report))
        super().__init__("City guide admission rejected: " + ",".join(codes))

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason_codes": list(self.reason_codes),
            "infrastructure_failure": self.infrastructure_failure,
            "report": deepcopy(self.report),
        }


def validate_semantic_map_sha256(value: Any) -> str:
    digest = str(value).strip() if isinstance(value, str) else ""
    if not _SHA256.fullmatch(digest):
        raise ValueError("semantic_map_sha256 must be a lowercase SHA-256 digest")
    return digest


def xyz(value: Iterable[Any], label: str) -> tuple[float, float, float]:
    try:
        point = tuple(float(component) for component in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite XYZ values") from exc
    if len(point) != 3 or not all(math.isfinite(component) for component in point):
        raise ValueError(f"{label} must contain finite XYZ values")
    return point


def requested_route(value: Any, *, current_xyz: Iterable[Any],
                    target_xyz: Iterable[Any]) -> tuple[tuple[float, float, float], ...]:
    """Validate a provider route and its exact current/target endpoints."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ValueError("requested-target route must be an iterable of XYZ waypoints")
    try:
        points = tuple(xyz(point, "requested-target route waypoint") for point in value)
    except TypeError as exc:
        raise ValueError("requested-target route must be an iterable of XYZ waypoints") from exc
    current = xyz(current_xyz, "current person position")
    target = xyz(target_xyz, "requested assembly target")
    if len(points) < 2:
        raise ValueError("requested-target route requires at least two waypoints")
    if points[0] != current or points[-1] != target:
        raise ValueError("requested-target route must include exact current and target endpoints")
    return points


def route_length_m(points: Iterable[Iterable[Any]]) -> float:
    route = tuple(xyz(point, "route waypoint") for point in points)
    return sum(math.dist(start, end) for start, end in zip(route, route[1:]))


def canonical_state_sha256(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("affordance state must be a mapping")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assembly_target_xyz(region: Mapping[str, Any]) -> tuple[float, float, float]:
    """Return the registered candidate centre at its authored ground plane.

    City assembly AABBs reserve five centimetres below their authored ground
    waypoint so full-body containment can be measured without numerical edge
    ambiguity.  Recovering that ground plane uses only registered candidate
    geometry; it never consults a private person-to-destination binding.
    """
    if not isinstance(region, Mapping):
        raise ValueError("assembly region must be a mapping")
    low = xyz(region.get("min", ()), "assembly region minimum")
    high = xyz(region.get("max", ()), "assembly region maximum")
    if any(high[index] <= low[index] for index in range(3)):
        raise ValueError("assembly region AABB must have positive extent")
    return ((low[0] + high[0]) * 0.5, (low[1] + high[1]) * 0.5, low[2] + 0.05)


def assembly_footprint_target_xyz(
        footprint: Mapping[str, Any]) -> tuple[float, float, float]:
    """Return the exact v2 disk centre on its frozen support plane."""
    if not isinstance(footprint, Mapping) or footprint.get("footprint") != "disk":
        raise ValueError("assembly footprint must be a disk")
    center = tuple(float(value) for value in footprint.get("center_xy_m", ()))
    plane = footprint.get("support_plane_z_m")
    if (len(center) != 2 or not all(math.isfinite(value) for value in center)
            or type(plane) not in (int, float) or not math.isfinite(plane)):
        raise ValueError("assembly footprint target is invalid")
    return center[0], center[1], float(plane)


__all__ = [
    "ASSEMBLY_AFFORDANCE_SCHEMA", "ASSEMBLY_AFFORDANCE_VERSION",
    "CityGuideAdmissionError", "GUIDE_ADMISSION_REASON_CODES", "PUBLIC_GUIDE_REASON_CODES",
    "REQUESTED_TARGET_PROFILE", "REQUESTED_TARGET_PROFILES",
    "REQUESTED_TARGET_V2_PROFILE",
    "assembly_footprint_target_xyz", "assembly_target_xyz", "requested_route", "route_length_m",
    "canonical_state_sha256", "validate_semantic_map_sha256", "xyz",
]
