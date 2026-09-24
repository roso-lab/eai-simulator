"""Runtime bridge between EAI env stepping and the city accident logic."""
from __future__ import annotations

import math
from dataclasses import dataclass
from copy import deepcopy
from typing import Any, Callable, Iterable, Mapping

from demo.city_benchmark.guide_admission import (
    ASSEMBLY_AFFORDANCE_SCHEMA,
    ASSEMBLY_AFFORDANCE_VERSION,
    CityGuideAdmissionError,
    REQUESTED_TARGET_PROFILE,
    REQUESTED_TARGET_PROFILES,
    REQUESTED_TARGET_V2_PROFILE,
    assembly_footprint_target_xyz,
    assembly_target_xyz,
    canonical_state_sha256,
    requested_route,
    route_length_m,
    validate_semantic_map_sha256,
    xyz,
)
from demo.city_benchmark.assembly_support import (
    PROFILE as ASSEMBLY_SUPPORT_PROFILE,
    validate_assembly_footprint_profile,
)
from demo.city_benchmark.scene.scenario import CityScenarioConfig, Polyline


@dataclass(frozen=True)
class CityRuntimeConfig(CityScenarioConfig):
    city_root_path: str = "/World/City"
    use_flow: bool = True
    target_path: str | None = None
    traffic_root_path: str = "/World/CityTraffic"
    episode_id: str | None = None


class CityEpisodeServiceRuntime:
    """Concrete episode service over City actors and native region readback."""

    def __init__(self, city: "CityEaiRuntime", *,
                 affected_people: Mapping[str, Mapping[str, Any]],
                 following_vehicle_ids: Iterable[str],
                 vehicle_divert_routes: Mapping[str, Iterable[Iterable[float]]],
                 vehicle_half_extents_m: Mapping[str, Iterable[float]],
                 vehicle_center_offsets_m: Mapping[str, Iterable[float]],
                 assembly_regions: Mapping[str, Mapping[str, Any]],
                 hazard_region_id: str, access_region_id: str,
                 region_reader: Any, assignment: Mapping[str, Mapping[str, str]],
                 incident_receiver: Any):
        from demo.city_benchmark.scene.people import EpisodeAffectedPeopleController, EpisodeAffectedPersonRoster
        self.city = city
        self.region_reader = region_reader
        self.assembly_regions = {str(key): dict(value) for key, value in assembly_regions.items()}
        self.hazard_region_id = str(hazard_region_id)
        self.access_region_id = str(access_region_id)
        self.assignment_rows = {str(key): dict(value) for key, value in assignment.items()}
        self.incident_receiver = incident_receiver
        if not callable(getattr(incident_receiver, "receive", None)):
            raise ValueError("City episode service requires incident_receiver.receive")
        self.people_roster = EpisodeAffectedPersonRoster(
            city.people, affected_people, episode_id=city.episode_id,
            source_id=city.city_service_source_id)
        self.people_controller = EpisodeAffectedPeopleController(city.people, self.people_roster)
        self.vehicle_ids = tuple(dict.fromkeys(str(value) for value in following_vehicle_ids))
        if not self.vehicle_ids or any(value not in city._traffic_state for value in self.vehicle_ids):
            raise ValueError("following vehicle roster contains an unknown City vehicle")
        self.divert_routes = {}
        self.vehicle_half_extents = {}
        self.vehicle_center_offsets = {}
        for vehicle_id in self.vehicle_ids:
            points = tuple(tuple(float(value) for value in point)
                           for point in vehicle_divert_routes.get(vehicle_id, ()))
            if len(points) < 1 or any(len(point) not in (2, 3) for point in points):
                raise ValueError(f"vehicle divert route is missing: {vehicle_id}")
            self.divert_routes[vehicle_id] = points
            half = tuple(float(value) for value in vehicle_half_extents_m.get(vehicle_id, ()))
            if len(half) != 3 or any(value <= 0 for value in half):
                raise ValueError(f"vehicle physical half extents are missing: {vehicle_id}")
            self.vehicle_half_extents[vehicle_id] = half
            offset = tuple(float(value) for value in vehicle_center_offsets_m.get(vehicle_id, ()))
            if len(offset) != 3 or not all(math.isfinite(value) for value in offset):
                raise ValueError(f"vehicle collision center offset is missing: {vehicle_id}")
            self.vehicle_center_offsets[vehicle_id] = offset
        for actor_id, row in self.people_roster.snapshot().items():
            region_id = str(row.get("assembly_region_id", ""))
            half = tuple(row.get("body_half_extents_m", ()))
            offset = tuple(row.get("body_center_offset_m", ()))
            if (region_id not in self.assembly_regions or not row.get("assembly_waypoints")
                    or len(half) != 3 or any(float(value) <= 0 for value in half)
                    or len(offset) != 3 or not all(math.isfinite(float(value)) for value in offset)):
                raise ValueError(f"affected person lacks assembly geometry: {actor_id}")
        for service in ("guide_people", "control_traffic", "report_incident"):
            row = self.assignment_rows.get(service, {})
            if not row.get("agent_id") or not row.get("region_id"):
                raise ValueError(f"City service assignment is missing: {service}")
        self._world_samples: list[dict[str, Any]] = []
        self._person_service_starts: dict[str, float] = {}
        self._vehicle_service_starts: dict[str, float] = {}
        self._guidance_profile: str | None = None
        self._requested_target_route_provider: Callable[..., Any] | None = None
        self._semantic_map_sha256: str | None = None
        self._assembly_footprint_profile: dict[str, Any] | None = None
        self._assembly_support_reader: Callable[[], Mapping[str, Any]] | None = None
        self._assembly_support_cache: tuple[
            float, dict[str, dict[str, Any]], dict[str, Any] | None] | None = None
        self._pending_guidance_proposals: dict[str, dict[str, Any]] = {}
        self._active_guidance_targets: dict[str, str] = {}
        self._last_guidance_admission_report: dict[str, Any] | None = None

    def affected_person_roster(self) -> dict[str, dict[str, Any]]:
        return self.people_roster.snapshot()

    def following_vehicle_roster(self) -> dict[str, dict[str, Any]]:
        return {vehicle_id: {
            "id": vehicle_id, "kind": "vehicle", "episode_id": self.city.episode_id,
            "source_id": self.city.city_service_source_id, "following_incident": True,
            "is_accident_vehicle": False,
        } for vehicle_id in self.vehicle_ids}

    def assignment(self, service: str) -> dict[str, str]:
        return deepcopy(self.assignment_rows[str(service)])

    def enable_requested_target_guidance(
            self, *, route_provider: Callable[..., Any], semantic_map_sha256: str,
            profile: str = REQUESTED_TARGET_PROFILE,
            assembly_footprint_profile: Mapping[str, Any] | None = None,
            assembly_support_reader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        """Enable the opt-in public-target profile before any guide request."""
        if not callable(route_provider):
            raise ValueError("requested-target guidance requires a route provider")
        if self._person_service_starts or self._pending_guidance_proposals \
                or self._active_guidance_targets:
            raise ValueError("requested-target guidance must be enabled before guidance starts")
        if profile not in REQUESTED_TARGET_PROFILES:
            raise ValueError("unsupported requested-target guidance profile")
        digest = validate_semantic_map_sha256(semantic_map_sha256)
        footprint = None
        if profile == REQUESTED_TARGET_V2_PROFILE:
            footprint = validate_assembly_footprint_profile(
                assembly_footprint_profile,
                expected_semantic_map_sha256=digest)
            if not callable(assembly_support_reader):
                raise ValueError("requested-target v2 requires a trusted support reader")
            self._assembly_support_reader = assembly_support_reader
        elif assembly_footprint_profile is not None or assembly_support_reader is not None:
            raise ValueError("assembly footprint inputs require requested-target v2")
        self._requested_target_route_provider = route_provider
        self._semantic_map_sha256 = digest
        self._assembly_footprint_profile = footprint
        self._assembly_support_cache = None
        self._guidance_profile = profile

    @property
    def guidance_profile(self) -> str | None:
        return self._guidance_profile

    def assembly_affordances(self) -> dict[str, Any]:
        """Measure every affected-person/candidate pair without reserving it."""
        if self._guidance_profile not in REQUESTED_TARGET_PROFILES:
            raise ValueError("requested-target guidance profile is not enabled")
        captured = float(self.city.elapsed)
        candidate_ids = (sorted(self._v2_footprints())
                         if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE
                         else sorted(self.assembly_regions))
        candidates = [self._candidate_affordance(region_id)
                      for region_id in candidate_ids]
        by_region = {row["region_id"]: row for row in candidates}
        routes = [self._requested_route_affordance(person_id, region_id, by_region[region_id])
                  for person_id in sorted(self.affected_person_roster())
                  for region_id in candidate_ids]
        result = {
            "schema": ASSEMBLY_AFFORDANCE_SCHEMA,
            "version": (2 if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE
                        else ASSEMBLY_AFFORDANCE_VERSION),
            "profile": self._guidance_profile,
            "episode_id": self.city.episode_id,
            "source_id": self.city.city_service_source_id,
            "captured_at_s": captured,
            "valid_until_s": captured + 1.0,
            "semantic_map_sha256": self._semantic_map_sha256,
            **({"candidate_geometry_sha256":
                self._assembly_footprint_profile["candidate_geometry_sha256"]}
               if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE else {}),
            "candidates": candidates,
            "routes": routes,
        }
        if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE:
            cached = self._assembly_support_cache
            if cached is not None and cached[0] == captured and cached[2] is not None:
                # Evaluator-only source artifact. Public projection uses an
                # explicit allowlist and never copies rays or collider paths.
                result["assembly_support_measurement"] = deepcopy(cached[2])
        return result

    def guide_person(self, person_id: str, *, target_region_id: str | None = None) -> dict[str, Any]:
        person_id = str(person_id)
        requested = getattr(self, "_guidance_profile", None) in REQUESTED_TARGET_PROFILES
        proposal = None
        if requested:
            proposal = self._pending_guidance_proposals.pop(person_id, None)
            reason = None
            if proposal is None:
                reason = "route_proposal_missing"
            elif (proposal["captured_at_s"] != float(self.city.elapsed)
                  or proposal["target_region_id"] != str(target_region_id)):
                reason = "route_proposal_stale"
            if reason is not None:
                raise CityGuideAdmissionError(
                    [reason], infrastructure_failure=True,
                    report={"profile": self._guidance_profile, "person_id": person_id,
                            "target_region_id": target_region_id,
                            "captured_at_s": float(self.city.elapsed)})
            route = deepcopy(proposal["route_readback"])
            waypoints = tuple(tuple(point) for point in proposal["proposal_waypoints_m"])
            self._active_guidance_targets[person_id] = str(target_region_id)
        else:
            route = self._person_route(person_id)
            waypoints = None
        if route.get("status") != "measured" or route.get("clear") is not True:
            self._active_guidance_targets.pop(person_id, None)
            raise RuntimeError("affected-person assembly route is not natively clear")
        from pxr import Usd
        try:
            with Usd.EditContext(self.city.stage, self.city.stage.GetSessionLayer()):
                result = self.people_controller.guide(
                    person_id, assembly_region_id=(str(target_region_id) if requested else None),
                    waypoints=waypoints)
        except Exception:
            self._active_guidance_targets.pop(person_id, None)
            raise
        self._person_service_starts[str(person_id)] = float(self.city.elapsed)
        return {**result, "episode_id": self.city.episode_id,
                "source_id": self.city.city_service_source_id, "route_readback": route}

    def stop_person(self, person_id: str) -> dict[str, Any]:
        person_id = str(person_id)
        try:
            result = self.people_controller.stop(person_id)
        finally:
            self._person_service_starts.pop(person_id, None)
            self._pending_guidance_proposals.pop(person_id, None)
            self._active_guidance_targets.pop(person_id, None)
        return {**result, "episode_id": self.city.episode_id,
                "source_id": self.city.city_service_source_id,
                "observed_time_s": float(self.city.elapsed),
                "evidence_ref": self._ref("person_stop", person_id)}

    def person_readback(self, person_id: str) -> dict[str, Any]:
        if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE:
            return self._v2_person_readback(person_id)
        result = self.people_controller.readback(person_id, self.assembly_regions)
        assembly = self._latest_region(result["assembly_region_id"])
        occupants = set(assembly.get("dynamic_envelopes", {}))
        capacity = assembly.get("region_metadata", {}).get("capacity")
        envelope = assembly.get("dynamic_envelopes", {}).get(str(person_id))
        region = self.assembly_regions[result["assembly_region_id"]]
        contained = envelope is not None and all(
            region["min"][axis] <= envelope["min"][axis]
            and envelope["max"][axis] <= region["max"][axis] for axis in range(3))
        trajectory = self._trajectory("people", str(person_id), self._person_service_starts[str(person_id)])
        route_clear = all(row.get("route_readback", {}).get("status") == "measured"
                          and row["route_readback"].get("clear") is True
                          for row in trajectory[1:])
        safe = (assembly.get("status") == "measured"
                and not assembly.get("static_hits")
                and occupants <= set(self.affected_person_roster())
                and type(capacity) is int and 0 < len(occupants) <= capacity
                and contained and len(trajectory) > 1 and route_clear)
        return {**result, "safe_route_and_assembly": safe,
                "full_body_inside_assembly": contained, "trajectory_samples": trajectory,
                "world_readback": assembly,
                "trajectory_evidence_ref": self._ref("person_trajectory", person_id),
                "safety_evidence_ref": self._ref("person_safety", person_id)}

    def _v2_person_readback(self, person_id: str) -> dict[str, Any]:
        result = self.people_controller.motion_readback(person_id)
        region_id = result["assembly_region_id"]
        candidate = self._v2_candidate_affordance(region_id)
        readback = candidate["native_readback"]
        position = tuple(float(value) for value in result["position_xyz"])
        center = tuple(float(value) for value in candidate["center_xy_m"])
        root_inside = sum((position[index] - center[index]) ** 2
                          for index in range(2)) <= float(candidate["radius_m"]) ** 2
        contained = str(person_id) in set(readback.get("contained_body_ids") or ())
        trajectory = self._trajectory(
            "people", str(person_id), self._person_service_starts[str(person_id)])
        route_clear = all(row.get("route_readback", {}).get("status") == "measured"
                          and row["route_readback"].get("clear") is True
                          for row in trajectory[1:])
        claims = (set(candidate.get("occupant_ids") or ())
                  | set(candidate.get("reservation_ids") or ()))
        capacity_ok = (candidate["capacity"] == 1 and person_id in claims
                       and len(claims) <= candidate["capacity"])
        support_ok = candidate.get("support_status") == "sampled_supported"
        person_envelope = (readback.get("dynamic_envelopes") or {}).get(str(person_id))
        foreign = readback.get("untrusted_dynamic_envelopes") or {}

        def overlaps_3d(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
            return all(float(left["min"][axis]) <= float(right["max"][axis])
                       and float(right["min"][axis]) <= float(left["max"][axis])
                       for axis in range(3))

        foreign_clear = (person_envelope is not None
                         and not any(overlaps_3d(person_envelope, envelope)
                                     for envelope in foreign.values()))
        safe = (result["path_finished"] and support_ok and contained
                and capacity_ok and foreign_clear
                and len(trajectory) > 1 and route_clear)
        return {
            **result,
            "inside_assembly": root_inside,
            "arrived": result["path_finished"] and contained,
            "safe_route_and_assembly": safe,
            "full_body_inside_assembly": contained,
            "trajectory_samples": trajectory,
            "world_readback": deepcopy(readback),
            "trajectory_evidence_ref": self._ref("person_trajectory", person_id),
            "safety_evidence_ref": self._ref("person_safety", person_id),
        }

    def control_vehicle(self, vehicle_id: str, mode: str) -> dict[str, Any]:
        route = None
        if mode == "divert":
            route = self._vehicle_route(vehicle_id)
            if route.get("status") != "measured" or route.get("clear") is not True:
                raise RuntimeError("vehicle divert route is not natively clear")
        result = self.city.control_following_vehicle(
            vehicle_id, mode, divert_waypoints=self.divert_routes.get(str(vehicle_id)))
        self._vehicle_service_starts[str(vehicle_id)] = float(self.city.elapsed)
        if route is not None:
            result["route_readback"] = route
        return result

    def stop_vehicle(self, vehicle_id: str) -> dict[str, Any]:
        result = self.city.control_following_vehicle(vehicle_id, "hold")
        self._vehicle_service_starts.pop(str(vehicle_id), None)
        return {**result, "position_xyz": list(self.city.following_vehicle_pose(vehicle_id)),
                "evidence_ref": self._ref("vehicle_stop", str(vehicle_id))}

    def vehicle_pose(self, vehicle_id: str) -> tuple[float, float, float]:
        return self.city.following_vehicle_pose(vehicle_id)

    def hazard_readback(self, vehicle_id: str, now_s: float) -> dict[str, Any]:
        if float(now_s) != float(self.city.elapsed):
            raise ValueError("hazard readback must use the current City episode time")
        hazard = self._latest_region(self.hazard_region_id)
        access = self._latest_region(self.access_region_id)
        if hazard.get("status") != "measured" or access.get("status") != "measured":
            raise RuntimeError("native hazard/access state is unknown")
        start = self._vehicle_service_starts[str(vehicle_id)]
        trajectory = self._trajectory("vehicles", str(vehicle_id), start)
        samples = [row for row in self._world_samples if start <= row["time_s"] <= float(now_s)]
        reads = [(row["regions"][self.hazard_region_id], row["regions"][self.access_region_id])
                 for row in samples]
        if len(trajectory) < 2 or any(h.get("status") != "measured" or a.get("status") != "measured"
                                      for h, a in reads):
            raise RuntimeError("continuous vehicle hazard/access readback is incomplete")
        return {
            "no_hazard_ingress": all(str(vehicle_id) not in h.get("dynamic_envelopes", {}) for h, _ in reads),
            "rescue_access_preserved": all(a.get("clear") is True for _, a in reads),
            "trajectory_samples": trajectory,
            "safety_samples": [{"time_s": row["time_s"],
                "hazard": row["regions"][self.hazard_region_id],
                "access": row["regions"][self.access_region_id]} for row in samples],
            "hazard_evidence_ref": self._ref("hazard", vehicle_id),
            "access_evidence_ref": self._ref("vehicle_access", vehicle_id),
            "hazard_readback": deepcopy(hazard), "access_readback": deepcopy(access),
        }

    def _candidate_affordance(self, region_id: str) -> dict[str, Any]:
        if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE:
            return self._v2_candidate_affordance(region_id)
        region_id = str(region_id)
        registered = self.assembly_regions[region_id]
        capacity = registered.get("capacity")
        reservations = sorted(person_id for person_id, target in
                              getattr(self, "_active_guidance_targets", {}).items()
                              if target == region_id)
        reasons: list[str] = []
        readback: dict[str, Any] | None = None
        occupants: list[str] = []
        try:
            readback = self._latest_region(region_id)
        except (KeyError, RuntimeError, ValueError):
            reasons.append("region_readback_unavailable")
        if readback is not None:
            if readback.get("status") != "measured":
                reasons.append("region_measurement_unknown")
            else:
                occupants = sorted(str(value) for value in
                                   (readback.get("dynamic_envelopes") or {}))
                if readback.get("static_hits"):
                    reasons.append("target_static_blocked")
                measured_capacity = (readback.get("region_metadata") or {}).get("capacity")
                if measured_capacity != capacity:
                    reasons.append("capacity_contract_invalid")
        if type(capacity) is not int or capacity <= 0:
            reasons.append("capacity_contract_invalid")
        if type(capacity) is int and capacity > 0 \
                and len(set(occupants) | set(reservations)) >= capacity:
            reasons.append("target_capacity_full")
        unavailable = {"region_readback_unavailable", "region_measurement_unknown",
                       "capacity_contract_invalid"}
        return {
            "region_id": region_id,
            "min": [float(value) for value in registered.get("min", ())],
            "max": [float(value) for value in registered.get("max", ())],
            "capacity": capacity,
            "occupancy_count": len(occupants),
            "reservation_count": len(reservations),
            "status": "unavailable" if unavailable & set(reasons) else "measured",
            "reason_codes": sorted(set(reasons)),
            # Evaluator-only detail. Public projection uses counts and reason codes.
            "occupant_ids": occupants,
            "reservation_ids": reservations,
            "native_readback": deepcopy(readback),
        }

    def _v2_footprints(self) -> dict[str, dict[str, Any]]:
        profile = self._assembly_footprint_profile
        if profile is None:
            raise RuntimeError("requested-target v2 footprint profile is unavailable")
        return {row["region_id"]: row for row in profile["candidates"]}

    def _v2_supports(self) -> dict[str, dict[str, Any]]:
        """Capture at most once per native tick and never reuse on a later tick."""
        now = float(self.city.elapsed)
        if self._assembly_support_cache is not None \
                and self._assembly_support_cache[0] == now:
            return deepcopy(self._assembly_support_cache[1])
        footprints = self._v2_footprints()
        unknown = {region_id: {
            "status": "unknown", "reason": "support_evidence_unavailable",
            "profile": ASSEMBLY_SUPPORT_PROFILE,
            "captured_native_s": now,
            "episode_id": self.city.episode_id,
            "source_id": self.city.city_service_source_id,
            "semantic_map_sha256": self._semantic_map_sha256,
            "candidate_geometry_sha256":
                self._assembly_footprint_profile["candidate_geometry_sha256"],
            "geometry_sha256": footprint["geometry_sha256"],
            "evidence_ref": self._ref("assembly_support", region_id),
        } for region_id, footprint in footprints.items()}
        raw_measurement = None
        try:
            raw = self._assembly_support_reader()
            if not isinstance(raw, Mapping):
                raise ValueError("support reader returned no mapping")
            raw_measurement = deepcopy(dict(raw))
            if (raw.get("schema") != "eai.city.assembly_support"
                    or raw.get("version") != 1
                    or raw.get("profile") != ASSEMBLY_SUPPORT_PROFILE
                    or raw.get("episode_id") != self.city.episode_id
                    or raw.get("source_id") != self.city.city_service_source_id
                    or raw.get("semantic_map_sha256") != self._semantic_map_sha256
                    or raw.get("candidate_geometry_sha256") !=
                    self._assembly_footprint_profile["candidate_geometry_sha256"]
                    or raw.get("captured_native_s") != now
                    or raw.get("native_clock_after_s") != now
                    or raw.get("same_tick") is not True):
                raise ValueError("support source identity or freshness mismatch")
            rows = raw.get("candidates")
            if not isinstance(rows, list) or len(rows) != 4:
                raise ValueError("support evidence must cover four candidates")
            by_id = {}
            for row in rows:
                region_id = row.get("region_id") if isinstance(row, Mapping) else None
                footprint = footprints.get(region_id)
                if (footprint is None or region_id in by_id
                        or row.get("geometry_sha256") != footprint["geometry_sha256"]
                        or row.get("center_xy_m") != footprint["center_xy_m"]
                        or row.get("radius_m") != footprint["radius_m"]
                        or row.get("support_plane_z_m") != footprint["support_plane_z_m"]
                        or row.get("capacity") != footprint["capacity"]
                        or row.get("status") not in {
                            "sampled_supported", "unsupported", "unknown"}):
                    raise ValueError("support candidate geometry or status mismatch")
                by_id[region_id] = {
                    "status": row["status"],
                    "reason": row.get("reason"),
                    "profile": ASSEMBLY_SUPPORT_PROFILE,
                    "captured_native_s": now,
                    "episode_id": raw["episode_id"],
                    "source_id": raw["source_id"],
                    "semantic_map_sha256": raw["semantic_map_sha256"],
                    "candidate_geometry_sha256": raw["candidate_geometry_sha256"],
                    "geometry_sha256": row["geometry_sha256"],
                    "evidence_ref": self._ref("assembly_support", region_id),
                }
            if set(by_id) != set(footprints):
                raise ValueError("support candidate identities mismatch")
            result = by_id
        except Exception as exc:
            reason = f"support_evidence_unknown:{type(exc).__name__}:{exc}"
            result = {region_id: {**row, "reason": reason}
                      for region_id, row in unknown.items()}
        self._assembly_support_cache = (
            now, deepcopy(result), deepcopy(raw_measurement))
        return deepcopy(result)

    def _v2_reservations(self, region_id: str) -> list[str]:
        pending = {person_id for person_id, proposal in self._pending_guidance_proposals.items()
                   if proposal.get("target_region_id") == region_id}
        active = {person_id for person_id, target in self._active_guidance_targets.items()
                  if target == region_id}
        return sorted(pending | active)

    def _v2_candidate_affordance(self, region_id: str) -> dict[str, Any]:
        region_id = str(region_id)
        footprint = self._v2_footprints().get(region_id)
        if footprint is None:
            raise ValueError("requested-target v2 candidate is unknown")
        support = self._v2_supports()[region_id]
        readback = self.region_reader.read_assembly_footprint(
            footprint, episode_id=self.city.episode_id, time_s=self.city.elapsed,
            support=support, affected_person_ids=self.affected_person_roster())
        occupants = sorted(readback.get("occupant_ids") or ())
        reservations = self._v2_reservations(region_id)
        reasons = []
        if support["status"] == "unknown" or readback.get("status") == "unknown":
            reasons.append("target_support_unknown")
        elif support["status"] == "unsupported" or readback.get("status") == "unsupported":
            reasons.append("target_support_unsupported")
        if len(set(occupants) | set(reservations)) >= footprint["capacity"]:
            reasons.append("target_capacity_full")
        # Unknown support dominates any simultaneously measured factual block.
        status = ("unavailable" if "target_support_unknown" in reasons
                  else "blocked" if "target_support_unsupported" in reasons
                  else "measured")
        return {
            **deepcopy(footprint),
            "occupancy_count": len(occupants),
            "reservation_count": len(reservations),
            "status": status,
            "reason_codes": sorted(set(reasons)),
            "support_status": support["status"],
            "support_evidence_ref": support["evidence_ref"],
            "occupant_ids": occupants,
            "reservation_ids": reservations,
            "native_readback": deepcopy(readback),
        }

    def _requested_route_affordance(self, person_id: str, region_id: str,
                                    candidate: Mapping[str, Any]) -> dict[str, Any]:
        person_id, region_id = str(person_id), str(region_id)
        evidence_ref = self._ref("assembly_route", f"{person_id}:{region_id}")
        # Candidate fullness is global state. Pair admission recomputes the
        # union so an occupant/reservation held by this same person is counted
        # once rather than making its own current target look unavailable.
        reasons = [reason for reason in (candidate.get("reason_codes") or ())
                   if reason != "target_capacity_full"]
        roster = self.affected_person_roster()
        spec = roster.get(person_id)
        proposal: tuple[tuple[float, float, float], ...] = ()
        readback: dict[str, Any] | None = None
        provider_error = None
        body_half: tuple[float, float, float] = ()
        body_offset: tuple[float, float, float] = ()
        target: tuple[float, float, float] = ()
        if spec is None:
            reasons.append("person_not_in_roster")
        else:
            active = getattr(self, "_active_guidance_targets", {}).get(person_id)
            if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE:
                pending = self._pending_guidance_proposals.get(person_id)
                if pending is not None:
                    active = pending.get("target_region_id")
            if active is not None and active != region_id:
                reasons.append("person_reserved_for_other_target")
            capacity = candidate.get("capacity")
            claims = (set(candidate.get("occupant_ids") or ())
                      | set(candidate.get("reservation_ids") or ()) | {person_id})
            if type(capacity) is int and capacity > 0 and len(claims) > capacity:
                reasons.append("target_capacity_full")
            try:
                current = xyz(self.city.people.position(person_id), "current person position")
                target = (assembly_footprint_target_xyz(candidate)
                          if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE
                          else assembly_target_xyz(self.assembly_regions[region_id]))
                body_half = xyz(spec.get("body_half_extents_m", ()), "body half extents")
                body_offset = xyz(spec.get("body_center_offset_m", ()), "body center offset")
                if any(value <= 0 for value in body_half):
                    raise ValueError("body half extents must be positive")
                provider = getattr(self, "_requested_target_route_provider", None)
                if not callable(provider):
                    reasons.append("route_provider_unavailable")
                else:
                    supplied = provider(
                        person_id=person_id,
                        current_xyz=current,
                        target_region_id=region_id,
                        target_xyz=target,
                        body_half_extents_m=body_half,
                        body_center_offset_m=body_offset)
                    if supplied is None:
                        reasons.append("route_provider_returned_none")
                    else:
                        proposal = requested_route(
                            supplied, current_xyz=current, target_xyz=target)
            except Exception as exc:
                reasons.append("route_provider_contract_violation")
                provider_error = f"{type(exc).__name__}:{exc}"
        if proposal:
            centered = tuple(tuple(point[axis] + body_offset[axis] for axis in range(3))
                             for point in proposal)
            try:
                readback = self.region_reader.read_route(
                    evidence_ref, centered, half_extents_m=body_half,
                    episode_id=self.city.episode_id, time_s=self.city.elapsed,
                    moving_entity_id=person_id)
            except Exception as exc:
                reasons.append("route_measurement_unknown")
                provider_error = f"native_read:{type(exc).__name__}:{exc}"
            if readback is None:
                reasons.append("route_measurement_unknown")
            else:
                fresh = (readback.get("episode_id") == self.city.episode_id
                         and readback.get("observed_time_s") == float(self.city.elapsed))
                if readback.get("status") != "measured" or not fresh:
                    reasons.append("route_measurement_unknown")
                elif type(readback.get("clear")) is not bool:
                    reasons.append("route_measurement_unknown")
                elif readback["clear"] is False:
                    static_hits = readback.get("static_hits")
                    dynamic_envelopes = readback.get("dynamic_envelopes")
                    if static_hits:
                        reasons.append("route_static_blocked")
                    if dynamic_envelopes:
                        reasons.append("route_dynamic_blocked")
                    if not static_hits and not dynamic_envelopes:
                        reasons.append("route_measurement_unknown")
            if target:
                center = tuple(target[axis] + body_offset[axis] for axis in range(3))
                low = tuple(center[axis] - body_half[axis] for axis in range(3))
                high = tuple(center[axis] + body_half[axis] for axis in range(3))
                if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE:
                    xy = candidate["center_xy_m"]
                    radius_sq = float(candidate["radius_m"]) ** 2
                    corners = ((low[0], low[1]), (low[0], high[1]),
                               (high[0], low[1]), (high[0], high[1]))
                    if not all(sum((corner[axis] - float(xy[axis])) ** 2
                                   for axis in range(2)) <= radius_sq
                               for corner in corners):
                        reasons.append("target_body_outside_footprint")
                else:
                    region = self.assembly_regions[region_id]
                    if any(float(region["min"][axis]) > low[axis]
                           or high[axis] > float(region["max"][axis]) for axis in range(3)):
                        reasons.append("target_body_outside_region")
        reasons = sorted(set(reasons))
        unavailable = {
            "person_not_in_roster", "region_readback_unavailable", "region_measurement_unknown",
            "capacity_contract_invalid", "route_provider_unavailable",
            "route_provider_returned_none", "route_provider_contract_violation",
            "route_measurement_unknown", "target_body_outside_region",
            "target_support_unknown",
        }
        status = ("unavailable" if unavailable & set(reasons) else
                  "blocked" if reasons else "admissible")
        length = route_length_m(proposal) if proposal else None
        speed = float(spec["speed_mps"]) if spec is not None else None
        state = {
            "profile": self._guidance_profile,
            "semantic_map_sha256": self._semantic_map_sha256,
            "person_id": person_id,
            "region_id": region_id,
            "body_half_extents_m": list(body_half),
            "body_center_offset_m": list(body_offset),
            "target": ({key: deepcopy(candidate[key]) for key in (
                "footprint", "center_xy_m", "radius_m", "support_plane_z_m",
                "geometry_sha256", "capacity", "occupancy_count", "reservation_count")}
                       if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE else
                       {"min": list(candidate["min"]), "max": list(candidate["max"]),
                        "capacity": candidate["capacity"],
                       "occupancy_count": candidate["occupancy_count"],
                        "reservation_count": candidate["reservation_count"]}),
            "proposal_waypoints_m": [list(point) for point in proposal],
            "status": status,
            "reason_codes": reasons,
        }
        return {
            "person_id": person_id,
            "region_id": region_id,
            "status": status,
            "reason_codes": reasons,
            "length_m": length,
            "estimated_travel_s": (length / speed if length is not None and speed else None),
            "evidence_ref": evidence_ref,
            "state_sha256": canonical_state_sha256(state),
            # Evaluator-only proposal/readback; public projection allowlists fields.
            "proposal_waypoints_m": [list(point) for point in proposal],
            "target_xyz": list(target),
            "body_half_extents_m": list(body_half),
            "body_center_offset_m": list(body_offset),
            "route_readback": deepcopy(readback),
            "provider_error": provider_error,
        }

    def _requested_target_admission(self, ids: tuple[str, ...],
                                    target_region_id: str | None,
                                    now_s: float) -> bool:
        if float(now_s) != float(self.city.elapsed):
            raise CityGuideAdmissionError(
                ["episode_clock_mismatch"], infrastructure_failure=True,
                report={"profile": self._guidance_profile, "now_s": float(now_s),
                        "city_elapsed_s": float(self.city.elapsed)})
        target = str(target_region_id or "")
        target_ids = (set(self._v2_footprints())
                      if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE
                      else set(self.assembly_regions))
        if target not in target_ids:
            raise CityGuideAdmissionError(
                ["target_region_unknown"], infrastructure_failure=True,
                report={"profile": self._guidance_profile,
                        "target_region_id": target_region_id,
                        "captured_at_s": float(self.city.elapsed)})
        roster = self.affected_person_roster()
        people = tuple(dict.fromkeys(str(person_id) for person_id in ids))
        reasons = []
        if any(person_id not in roster for person_id in people):
            reasons.append("person_not_in_roster")
        for person_id in people:
            active = getattr(self, "_active_guidance_targets", {}).get(person_id)
            if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE:
                pending = self._pending_guidance_proposals.get(person_id)
                if pending is not None:
                    active = pending.get("target_region_id")
            if active is not None and active != target:
                reasons.append("person_reserved_for_other_target")
        candidate = self._candidate_affordance(target)
        reasons.extend(reason for reason in candidate["reason_codes"]
                       if reason != "target_capacity_full")
        capacity = candidate["capacity"]
        claims = set(candidate["occupant_ids"]) | set(candidate["reservation_ids"]) | set(people)
        if type(capacity) is int and capacity > 0 and len(claims) > capacity:
            reasons.append("target_capacity_full")
        routes = []
        if not any(person_id not in roster for person_id in people):
            routes = [self._requested_route_affordance(person_id, target, candidate)
                      for person_id in people]
            for route in routes:
                reasons.extend(route["reason_codes"])
        reasons = sorted(set(reasons))
        report = {
            "schema": ASSEMBLY_AFFORDANCE_SCHEMA,
            "version": (2 if self._guidance_profile == REQUESTED_TARGET_V2_PROFILE
                        else ASSEMBLY_AFFORDANCE_VERSION),
            "profile": self._guidance_profile,
            "episode_id": self.city.episode_id,
            "source_id": self.city.city_service_source_id,
            "captured_at_s": float(self.city.elapsed),
            "semantic_map_sha256": self._semantic_map_sha256,
            "request": {"person_ids": list(people), "target_region_id": target},
            "candidate": candidate,
            "routes": routes,
            "reason_codes": reasons,
            "admitted": not reasons,
        }
        self._last_guidance_admission_report = deepcopy(report)
        if reasons:
            infrastructure = bool(set(reasons) & {
                "episode_clock_mismatch", "target_region_unknown", "person_not_in_roster",
                "region_readback_unavailable", "region_measurement_unknown",
                "capacity_contract_invalid", "route_provider_unavailable",
                "route_provider_returned_none", "route_provider_contract_violation",
                "route_measurement_unknown", "route_proposal_missing", "route_proposal_stale",
                "target_body_outside_region", "target_support_unknown",
            })
            raise CityGuideAdmissionError(
                reasons, infrastructure_failure=infrastructure, report=report)
        self._pending_guidance_proposals = {
            **self._pending_guidance_proposals,
            **{route["person_id"]: {
                "captured_at_s": float(self.city.elapsed),
                "target_region_id": target,
                "proposal_waypoints_m": deepcopy(route["proposal_waypoints_m"]),
                "route_readback": deepcopy(route["route_readback"]),
                "state_sha256": route["state_sha256"],
            } for route in routes},
        }
        return True

    def admit(self, service: str, ids: tuple[str, ...], region_id: str,
              target_region_id: str | None, mode: str | None, now_s: float) -> bool:
        if service == "guide_people" and getattr(
                self, "_guidance_profile", None) in REQUESTED_TARGET_PROFILES:
            return self._requested_target_admission(ids, target_region_id, now_s)
        if float(now_s) != float(self.city.elapsed):
            return False
        try:
            if service == "guide_people":
                result = self._latest_region(str(target_region_id))
                capacity = result.get("region_metadata", {}).get("capacity")
                occupants = result.get("dynamic_envelopes", {})
                roster = self.affected_person_roster()
                in_flight = {person_id for person_id in self._person_service_starts
                             if roster[person_id]["assembly_region_id"] == target_region_id}
                routes_clear = all(self._person_route(entity_id).get("clear") is True for entity_id in ids)
                return (set(ids) <= set(roster)
                        and all(roster[entity_id]["assembly_region_id"] == target_region_id
                                for entity_id in ids)
                        and result.get("status") == "measured" and not result.get("static_hits")
                        and type(capacity) is int
                        and len(set(occupants) | in_flight | set(ids)) <= capacity
                        and routes_clear)
            if service == "control_traffic":
                hazard = self._latest_region(self.hazard_region_id)
                access = self._latest_region(self.access_region_id)
                route_clear = mode != "divert" or all(
                    self._vehicle_route(entity_id).get("clear") is True for entity_id in ids)
                return (set(ids) <= set(self.vehicle_ids) and mode in {"hold", "divert", "release"}
                        and hazard.get("status") == access.get("status") == "measured" and route_clear)
            if service == "report_incident":
                return self._latest_region(self.access_region_id).get("status") == "measured"
        except (KeyError, RuntimeError, ValueError):
            return False
        return False

    def observe_step(self) -> dict[str, Any]:
        regions = {self.hazard_region_id, self.access_region_id, *self.assembly_regions}
        sample = {"time_s": float(self.city.elapsed), "regions": {}}
        for region_id in sorted(regions):
            sample["regions"][region_id] = self.region_reader.read(
                region_id, episode_id=self.city.episode_id, time_s=self.city.elapsed)
        sample["people"] = {}
        for person_id, spec in self.people_roster.snapshot().items():
            pose = list(self.city.people.position(person_id))
            row = {"position_xyz": pose}
            if person_id in self._person_service_starts and self._world_samples:
                previous = self._world_samples[-1]["people"][person_id]["position_xyz"]
                points = [tuple(p[i]+spec["body_center_offset_m"][i] for i in range(3))
                          for p in (previous, pose)]
                row["route_readback"] = self.region_reader.read_route(
                    f"person:{person_id}:step", points,
                    half_extents_m=spec["body_half_extents_m"], episode_id=self.city.episode_id,
                    time_s=self.city.elapsed, moving_entity_id=person_id)
            sample["people"][person_id] = row
        sample["vehicles"] = {entity_id: {
            "position_xyz": list(self.city.following_vehicle_pose(entity_id)),
            "mode": self.city._traffic_state[entity_id]["mode"]} for entity_id in self.vehicle_ids}
        self._world_samples.append(sample)
        return deepcopy(sample)

    def _trajectory(self, kind: str, entity_id: str, start_s: float) -> list[dict[str, Any]]:
        samples = [row for row in self._world_samples if start_s <= row["time_s"] <= self.city.elapsed]
        if not samples or samples[0]["time_s"] != start_s or samples[-1]["time_s"] != self.city.elapsed:
            raise RuntimeError("native actor trajectory interval is incomplete")
        return [{"time_s": row["time_s"], **deepcopy(row[kind][entity_id])} for row in samples]

    def reset(self) -> None:
        self._world_samples.clear()
        self._person_service_starts.clear()
        self._vehicle_service_starts.clear()
        self._pending_guidance_proposals.clear()
        self._active_guidance_targets.clear()
        self._last_guidance_admission_report = None
        self._assembly_support_cache = None

    def continuous_access(self, request: Mapping[str, Any], start_s: float,
                          end_s: float) -> dict[str, Any]:
        start, end = float(start_s), float(end_s)
        samples = [row for row in self._world_samples if start <= row["time_s"] <= end]
        if not samples or samples[0]["time_s"] > start or samples[-1]["time_s"] < end:
            raise RuntimeError("continuous access interval is not fully sampled")
        reads = [row["regions"][self.access_region_id] for row in samples]
        if any(row.get("status") != "measured" for row in reads):
            raise RuntimeError("continuous access contains an unknown native sample")
        clear = all(row.get("clear") is True for row in reads)
        return {"measured_clear_throughout": clear,
                "rescue_and_evacuation_routes_admitted": clear,
                "evidence_ref": self._ref("continuous_access", request.get("request_id", "request")),
                "sample_count": len(samples), "samples": deepcopy(reads)}

    def _latest_region(self, region_id: str) -> dict[str, Any]:
        if not self._world_samples or region_id not in self._world_samples[-1]["regions"]:
            raise RuntimeError(f"City service region has no current sample: {region_id}")
        sample = self._world_samples[-1]
        if sample["time_s"] != float(self.city.elapsed):
            raise RuntimeError("City service region sample is stale")
        return sample["regions"][region_id]

    def _person_route(self, person_id: str) -> dict[str, Any]:
        spec = self.people_roster.snapshot().get(str(person_id))
        if spec is None:
            raise ValueError("person is outside the affected roster")
        current = self.city.people.position(str(person_id))
        offset = tuple(float(value) for value in spec["body_center_offset_m"])
        route_points = tuple(tuple(float(point[axis]) + offset[axis] for axis in range(3))
                             for point in (current, *spec["assembly_waypoints"]))
        return self.region_reader.read_route(
            f"person:{person_id}:assembly", route_points,
            half_extents_m=spec["body_half_extents_m"], episode_id=self.city.episode_id,
            time_s=self.city.elapsed, moving_entity_id=str(person_id))

    def _vehicle_route(self, vehicle_id: str) -> dict[str, Any]:
        vehicle_id = str(vehicle_id)
        current = self.city.following_vehicle_pose(vehicle_id)
        offset = self.vehicle_center_offsets[vehicle_id]
        route_points = tuple(tuple(float(point[axis]) + offset[axis] for axis in range(3))
                             for point in (current, *self.divert_routes[vehicle_id]))
        return self.region_reader.read_route(
            f"vehicle:{vehicle_id}:divert", route_points,
            half_extents_m=self.vehicle_half_extents[vehicle_id],
            episode_id=self.city.episode_id, time_s=self.city.elapsed,
            moving_entity_id=vehicle_id)

    def _ref(self, kind: str, entity_id: str) -> str:
        return f"{self.city.city_service_source_id}:{kind}:{entity_id}:{int(self.city.elapsed * 1000):09d}"


class CityEaiRuntime:
    """Own one city episode while the caller owns Kit and ``env.step``."""

    def __init__(self, stage, *, config: CityRuntimeConfig | None = None):
        self.config = config or CityRuntimeConfig()
        self.stage = stage
        from demo.city_benchmark.scene.prebuilt import consume
        prebuilt = consume()
        if prebuilt is None:
            raise RuntimeError("City spawn callback did not publish a prebuilt scene")
        if prebuilt[0] != stage:
            raise RuntimeError("Prebuilt city belongs to another USD stage")
        _, self.scene, self.flow = prebuilt
        self.config = self.scene.config
        self.incident_vehicle = self.scene.incident_vehicle
        self.traffic = self.scene.traffic
        self.people = getattr(self.scene, "people_runtime", None)
        self.effects = self.flow.paths if self.flow is not None else []
        configured_episode = getattr(config or self.config, "episode_id", None)
        self.episode_id = (str(configured_episode).strip() if configured_episode else
                           f"city-seed-{int(self.config.seed)}-episode-0001")
        self.city_service_source_id = "isaac.city.runtime"
        self._city_service_sources: dict[str, Any] = {}
        self._episode_service: CityEpisodeServiceRuntime | None = None
        self.elapsed = 0.0
        self.phase = None
        self.last_speed_mps = 0.0
        self.events = []
        self._traffic_distances = [entry[3] for entry in self.traffic]
        records = tuple(getattr(self.scene, "traffic_records", ()))
        self._traffic_state = {}
        for index, entry in enumerate(self.traffic):
            record = records[index] if index < len(records) else {}
            vehicle_id = str(record.get("id", f"following_vehicle_{index:02d}"))
            self._traffic_state[vehicle_id] = {
                "index": index, "base_route": entry[2], "active_route": entry[2],
                "distance": float(entry[3]), "speed_mps": float(entry[4]),
                "mode": "release", "prim_path": record.get(
                    "prim_path", f"{self.config.traffic_root_path}/Vehicle_{index:02d}"),
            }
        self._last_time = 0.0

    def bind_city_service_sources(self, *, affected_people: Mapping[str, Mapping[str, Any]],
                                  following_vehicle_ids: Iterable[str],
                                  vehicle_divert_routes: Mapping[str, Iterable[Iterable[float]]],
                                  vehicle_half_extents_m: Mapping[str, Iterable[float]],
                                  vehicle_center_offsets_m: Mapping[str, Iterable[float]],
                                  assembly_regions: Mapping[str, Mapping[str, Any]],
                                  hazard_region_id: str, access_region_id: str,
                                  region_reader: Any,
                                  assignment: Mapping[str, Mapping[str, str]],
                                  incident_receiver: Any) -> None:
        """Create the concrete actor-control and native-readback service."""
        service = CityEpisodeServiceRuntime(
            self, affected_people=affected_people,
            following_vehicle_ids=following_vehicle_ids,
            vehicle_divert_routes=vehicle_divert_routes,
            vehicle_half_extents_m=vehicle_half_extents_m,
            vehicle_center_offsets_m=vehicle_center_offsets_m,
            assembly_regions=assembly_regions, hazard_region_id=hazard_region_id,
            access_region_id=access_region_id, region_reader=region_reader,
            assignment=assignment, incident_receiver=incident_receiver)
        self._episode_service = service
        self._city_service_sources = {
            "affected_person_roster": service.affected_person_roster,
            "following_vehicle_roster": service.following_vehicle_roster,
            "person_controller": service.guide_person,
            "person_stop": service.stop_person,
            "person_readback": service.person_readback,
            "vehicle_controller": service.control_vehicle,
            "vehicle_stop": service.stop_vehicle,
            "vehicle_pose_reader": service.vehicle_pose,
            "admission": service.admit,
            "assembly_affordances": service.assembly_affordances,
            "hazard_readback": service.hazard_readback,
            "access_readback": service.continuous_access,
            "assignment": service.assignment,
            "incident_receiver": incident_receiver,
            "pull_episode_witness": self._pull_city_episode_witness,
        }
        service.observe_step()

    def enable_requested_target_guidance(
            self, *, route_provider: Callable[..., Any], semantic_map_sha256: str,
            profile: str = REQUESTED_TARGET_PROFILE,
            assembly_footprint_profile: Mapping[str, Any] | None = None,
            assembly_support_reader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        if self._episode_service is None:
            raise RuntimeError("City service sources must be bound before requested-target guidance")
        self._episode_service.enable_requested_target_guidance(
            route_provider=route_provider, semantic_map_sha256=semantic_map_sha256,
            profile=profile, assembly_footprint_profile=assembly_footprint_profile,
            assembly_support_reader=assembly_support_reader)

    def assembly_affordances(self) -> dict[str, Any]:
        if self._episode_service is None:
            raise RuntimeError("City service sources are not bound")
        return self._episode_service.assembly_affordances()

    def following_vehicle_pose(self, vehicle_id: str) -> tuple[float, float, float]:
        state = self._traffic_state.get(str(vehicle_id))
        if state is None:
            raise ValueError("unknown following vehicle")
        value = self.traffic[state["index"]][0].Get()
        result = tuple(float(component) for component in value)
        if len(result) != 3 or not all(math.isfinite(component) for component in result):
            raise ValueError("following vehicle pose is invalid")
        return result

    def control_following_vehicle(self, vehicle_id: str, mode: str, *,
                                  divert_waypoints: Iterable[Iterable[float]] | None = None) -> dict[str, Any]:
        vehicle_id, mode = str(vehicle_id), str(mode)
        state = self._traffic_state.get(vehicle_id)
        if state is None or mode not in {"hold", "divert", "release"}:
            raise ValueError("invalid following vehicle control")
        before = str(state["mode"])
        if mode == "divert":
            points = tuple(tuple(float(value) for value in point) for point in (divert_waypoints or ()))
            if not points:
                raise ValueError("divert requires episode route geometry")
            current = self.following_vehicle_pose(vehicle_id)
            state["active_route"] = Polyline((current, *points))
            state["distance"] = 0.0
        state["mode"] = mode
        return {"vehicle_id": vehicle_id, "episode_id": self.episode_id,
                "source_id": self.city_service_source_id,
                "status_before": before, "status_after": mode,
                "observed_time_s": float(self.elapsed),
                "evidence_ref": f"{self.city_service_source_id}:controller:{vehicle_id}:{mode}:"
                                f"{int(self.elapsed * 1000):09d}"}

    def city_service_native_binding(self) -> dict[str, Any]:
        """Expose callbacks to the benchmark-owned native adapter."""
        result = {
            "episode_id": self.episode_id, "source_id": self.city_service_source_id,
            "scoreable": False,
            "guidance_profile": (self._episode_service.guidance_profile
                                 if self._episode_service is not None else None),
        }
        result.update(self._city_service_sources)
        required = (
            "affected_person_roster", "following_vehicle_roster", "person_controller",
            "person_stop", "person_readback", "vehicle_controller", "vehicle_stop",
            "vehicle_pose_reader", "admission", "access_readback", "assignment",
            "hazard_readback", "incident_receiver",
        )
        blockers = []
        for name in required:
            value = result.get(name)
            if (name == "incident_receiver" and not callable(getattr(value, "receive", None))) or \
                    (name != "incident_receiver" and not callable(value)):
                blockers.append(f"missing_runtime_service_source:{name}")
        result["blockers"] = blockers
        return result

    def _pull_city_episode_witness(self, host: Any) -> None:
        binding = self.city_service_native_binding()
        people = binding.get("affected_person_roster")
        vehicles = binding.get("following_vehicle_roster")
        pose_reader = binding.get("vehicle_pose_reader")
        if not all(callable(callback) for callback in (people, vehicles, pose_reader)):
            raise RuntimeError("City live witness pull requires both rosters and vehicle poses")
        people_rows = self._validated_service_roster(people, "person")
        vehicle_rows = self._validated_service_roster(vehicles, "vehicle")
        host.bind_affected_person_roster(people_rows)
        host.bind_following_vehicle_roster(vehicle_rows)
        entities = []
        for person_id in people_rows:
            entities.append({"id": person_id, "kind": "person",
                             "position_xyz": list(self.people.position(person_id))})
        for vehicle_id, metadata in vehicle_rows.items():
            entities.append({
                "id": vehicle_id, "kind": "vehicle",
                "position_xyz": list(pose_reader(vehicle_id)),
                "following_incident": metadata.get("following_incident") is True,
                "is_accident_vehicle": metadata.get("is_accident_vehicle") is True,
            })
        if entities:
            now = float(self.elapsed)
            host.deliver_observation({
                "id": f"city_runtime_observation_{int(now * 1000):09d}",
                "episode_id": self.episode_id, "source_id": self.city_service_source_id,
                "producer_agent_id": "city_runtime", "region_id": "city_runtime",
                "captured_at_s": now, "delivered_at_s": now,
                "valid_until_s": now + 1.0, "entities": entities,
            })

    def _validated_service_roster(self, callback: Callable[[], Mapping[str, Mapping[str, Any]]],
                                  kind: str) -> dict[str, dict[str, Any]]:
        rows = callback()
        if not isinstance(rows, Mapping) or not rows:
            raise ValueError(f"episode-owned {kind} roster is empty or invalid")
        result = {}
        for entity_id, row in rows.items():
            entity_id = str(entity_id)
            if (not isinstance(row, Mapping) or row.get("id") != entity_id
                    or row.get("kind") != kind
                    or row.get("episode_id") != self.episode_id
                    or row.get("source_id") != self.city_service_source_id):
                raise ValueError(f"episode-owned {kind} roster provenance mismatch")
            result[entity_id] = dict(row)
        return result

    def city_service_witness_source(self) -> dict[str, Any]:
        """Return a fail-closed source contract for live service collection."""
        binding = self.city_service_native_binding()
        roster_callbacks = (binding.get("affected_person_roster"),
                            binding.get("following_vehicle_roster"))
        rosters_ready = False
        if all(callable(callback) for callback in roster_callbacks):
            try:
                rosters_ready = bool(self._validated_service_roster(roster_callbacks[0], "person")) \
                    and bool(self._validated_service_roster(roster_callbacks[1], "vehicle"))
            except Exception:
                rosters_ready = False
        vehicle_ready = (rosters_ready and callable(binding.get("vehicle_controller"))
                         and callable(binding.get("vehicle_pose_reader")))
        access_ready = callable(binding.get("access_readback"))
        hazard_ready = callable(binding.get("hazard_readback"))
        admission_ready = callable(binding.get("admission"))
        receipt_ready = callable(getattr(binding.get("incident_receiver"), "receive", None))
        capabilities = {
            "affected_person_source": rosters_ready,
            "following_vehicle_source": vehicle_ready,
            "service_ack_source": rosters_ready and vehicle_ready,
            "measured_person_trajectory": rosters_ready and self.people is not None,
            "measured_vehicle_trajectory": vehicle_ready,
            "dynamic_hazard_and_assembly_access": admission_ready and hazard_ready and access_ready,
            "external_response_receipt": receipt_ready,
            "episode_owned_affected_person_roster": rosters_ready,
            "episode_owned_following_vehicle_roster": rosters_ready,
            "continuous_access_readback": access_ready,
        }
        blockers = list(binding["blockers"])
        blockers.extend(f"missing_live_capability:{name}" for name, ready in capabilities.items()
                        if not ready)
        return {
            "episode_id": self.episode_id, "source_id": self.city_service_source_id,
            "evidence_class": "runtime", "clock": lambda: self.elapsed,
            "admit": binding.get("admission"), "assignment": binding.get("assignment"),
            "pull_episode_witness": binding.get("pull_episode_witness"),
            "capabilities": capabilities, "blockers": list(dict.fromkeys(blockers)),
            "execution_validated": False, "scoreable": False,
        }

    @classmethod
    def from_current_stage(cls, *, config: CityRuntimeConfig | None = None):
        from omni.usd import get_context
        stage = get_context().get_stage()
        if stage is None:
            raise RuntimeError("The EAI session has no current USD stage")
        return cls(stage, config=config)

    def reset(self) -> None:
        """Reset incident, traffic, events and effect activation for an episode."""
        self.elapsed = 0.0
        self.phase = None
        self.last_speed_mps = 0.0
        self.events.clear()
        self._last_time = 0.0
        self._traffic_distances = [entry[3] for entry in self.traffic]
        for state, distance in zip(self._traffic_state.values(), self._traffic_distances):
            state.update(active_route=state["base_route"], distance=float(distance), mode="release")
        if self.people is not None:
            from pxr import Usd
            with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                for actor_id in self.people.actor_ids:
                    self.people.reset(actor_id)
        if self._episode_service is not None:
            self._episode_service.reset()
        self.step(0.0)
        if self.flow is not None:
            self.flow.reset()

    def step(self, elapsed: float | None = None, *, dt: float = 0.02):
        from pxr import Gf, Usd
        if elapsed is None:
            elapsed = self.elapsed + max(0.0, float(dt))
        value = float(elapsed)
        if value < self.elapsed:
            raise ValueError("CityEaiRuntime.step requires monotonic elapsed time; call reset() to rewind")
        self.elapsed = value
        state = self.scene.plan.sample(value)
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            translate, rotate = self.incident_vehicle
            translate.Set(Gf.Vec3d(*state.car_position, self.scene.plan.road_height))
            rotate.Set(math.degrees(state.car_yaw))
            delta = max(0.0, value - self._last_time)
            for vehicle_id, traffic_state in self._traffic_state.items():
                index = traffic_state["index"]
                position, heading, _route, _offset, _speed = self.traffic[index]
                route = traffic_state["active_route"]
                loop = route is traffic_state["base_route"]
                candidate = float(traffic_state["distance"])
                if traffic_state["mode"] != "hold":
                    candidate += float(traffic_state["speed_mps"]) * delta
                point, yaw = route.sample(candidate, loop=loop)
                blocked_by_incident = (traffic_state["mode"] != "divert" and value >= 2.5
                                       and math.dist(point[:2], state.car_position) < 7.0)
                if blocked_by_incident:
                    point, yaw = route.sample(float(traffic_state["distance"]), loop=loop)
                else:
                    traffic_state["distance"] = candidate
                    self._traffic_distances[index] = candidate
                position.Set(Gf.Vec3d(*point[:2], self.scene.plan.road_height))
                heading.Set(math.degrees(yaw))
            if self.people is not None:
                self.people.update(delta)
        if self.flow is not None:
            self.flow.update(state)
        if state.phase != self.phase:
            self.phase = state.phase
            self.events.append({
                "phase": state.phase, "time": round(value, 3),
                "vehicle": list(state.car_position), "effect": list(state.effect_position),
                "car_speed_mps": state.car_speed_mps,
                "pre_impact_speed_mps": self.last_speed_mps if state.phase == "impact" else None,
            })
        self.last_speed_mps = state.car_speed_mps
        self._last_time = value
        if self._episode_service is not None:
            self._episode_service.observe_step()
        return state

    def close(self) -> None:
        if self.people is not None:
            from pxr import Usd
            with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
                self.people.close()
        if self.flow is not None:
            self.flow.close()


__all__ = ["CityEaiRuntime", "CityEpisodeServiceRuntime", "CityRuntimeConfig"]
