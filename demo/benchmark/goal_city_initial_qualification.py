"""Read-only initial qualification evidence for the exact autonomous City task.

This collector records what the existing native readers can establish before
the participant is initialized.  It is deliberately not a feasibility oracle:
future service terminals remain unmeasured and every release flag is false.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .goal_city_assembly import PROFILE, project_assembly_affordances
from .goal_driven_contract import validate_goal_task_spec
from .provenance import canonical_sha256


SCHEMA = "eai.benchmark.city_initial_qualification"
VERSION = 1
EXPECTED_TASK_ID = "CITY-GOAL-NATIVE-01"
EXPECTED_PUBLIC_TASK_SHA256 = "143a7ec986e256eaa0ec96c5853164ed558da5b57d52c7ab9f41234561c2be2f"
EXPECTED_PERSON_IDS = tuple(f"affected_person_{index:02d}" for index in range(4))
EXPECTED_VEHICLE_IDS = tuple(f"following_vehicle_{index:02d}" for index in range(6))
_RELEASE = {
    "oracle_status": "failed",
    "feasibility_label": None,
    "platform_valid": False,
    "execution_validated": False,
    "business_outcomes_validated": False,
    "scoreable": False,
}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def _issue(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _declared_robots(public_task: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in public_task["candidate_robot_pool"]:
        result.append({
            "id": row["id"],
            "type": row["type"],
            "mobility": row["mobility"],
            "public_capabilities": sorted(row["public_capabilities"]),
        })
    return sorted(result, key=lambda row: row["id"])


def _coverage(observation: Mapping[str, Any], affordance: Mapping[str, Any], *,
              robots: list[Mapping[str, Any]], candidate_ids: tuple[str, ...],
              source_id: str | None, task_sha256: str,
              clock_stable: bool) -> dict[str, dict[str, Any]]:
    """Map every hard condition to actual initial or future evidence."""
    observed_people = tuple(observation.get("person_ids") or ())
    observed_vehicles = tuple(observation.get("following_vehicle_ids") or ())
    people_exact = observed_people == EXPECTED_PERSON_IDS
    vehicles_exact = observed_vehicles == EXPECTED_VEHICLE_IDS
    observation_current = observation.get("status") == "measured" and clock_stable
    pairs_exact = affordance.get("exact_pair_record_coverage") is True and clock_stable
    derivations = {row.get("derivation") for row in affordance.get("candidates", ())
                   if isinstance(row, Mapping)}
    geometry_qualified = bool(derivations) and all(
        isinstance(value, str) and "author" not in value for value in derivations)
    observation_refs = ([str(observation["observation_ref"])]
                        if observation_current and observation.get("observation_ref") else [])
    route_refs = (sorted(str(row["evidence_ref"]) for row in affordance.get("routes", ())
                         if isinstance(row, Mapping) and row.get("evidence_ref"))
                  if pairs_exact else [])
    native_source = [source_id] if isinstance(source_id, str) and source_id else []
    task_ref = [f"public_task_sha256:{task_sha256}"]

    def providers(*capabilities: str) -> tuple[str, ...]:
        required = set(capabilities)
        return tuple(sorted(
            robot["id"] for robot in robots
            if required & set(robot["public_capabilities"])
        ))

    def row(outcome_id: str, status: str, phase: str, entities: tuple[str, ...],
            sources: list[str], refs: list[str], guarantee: str,
            producers: tuple[str, ...]) -> dict[str, Any]:
        return {
            "outcome_id": outcome_id,
            "status": status,
            "phase": phase,
            "entity_ids": list(entities),
            "source_ids": sources,
            "evidence_refs": refs,
            "guarantee": guarantee,
            "required_producers": list(producers),
        }

    exact_observation = observation_current and people_exact and vehicles_exact
    people_measurement = observation_current and people_exact
    traffic_measurement = observation_current and vehicles_exact
    all_people = EXPECTED_PERSON_IDS
    all_vehicles = EXPECTED_VEHICLE_IDS
    all_observed = (*all_people, *all_vehicles)
    responder_providers = providers("ground_navigation", "aerial_navigation")
    guide_providers = providers("guide_people")
    traffic_providers = providers("control_following_traffic")
    report_providers = providers("report_incident")
    return {
        "incident_observation_delivery": row(
            "incident_assessed", "measured_initial" if exact_observation else "unavailable",
            "initial", all_observed, native_source, observation_refs if exact_observation else [],
            "delivered current observation with exact affected-person and following-traffic scope",
            ("CityEpisodeEvidenceHost.observation",)),
        "incident_decision_confirmation": row(
            "incident_assessed", "execution_only", "execution", all_observed,
            native_source, [], "fresh participant-visible decision within 15 seconds",
            ("GoalAlgorithmAdapter.act",)),
        "responder_capability_manifest": row(
            "response_dispatch", "declared_only", "initial", responder_providers,
            ["public_task_manifest"], task_ref,
            "candidate identity and declared capability; not liveness",
            ("build_city_autonomous_contract",)),
        "responder_route_and_arrival": row(
            "response_dispatch", "execution_only", "execution", responder_providers,
            native_source, [], "active capable responder on an admitted route within 35 seconds",
            ("GoalAlgorithmAdapter.act", "CityNativeGoalBackend.execute",
             "CityRuntimeReadbackAdapter.role_interval")),
        "affected_person_roster": row(
            "exposed_people_accounted_for",
            "measured_initial" if people_measurement else "unavailable", "initial",
            all_people, native_source, observation_refs if people_measurement else [],
            "exact four-person identity and current pose",
            ("CityEpisodeEvidenceHost.observation",)),
        "assembly_candidate_geometry_capacity": row(
            "exposed_people_accounted_for",
            ("measured_initial" if pairs_exact and geometry_qualified else
             "qualification_limited" if pairs_exact else "unavailable"),
            "initial", (*all_people, *candidate_ids), native_source,
            route_refs if pairs_exact else [],
            "current candidate geometry, capacity, occupancy and reservation state",
            ("CityNativeServiceRunner.assembly_affordances",)),
        "person_candidate_route_proposals": row(
            "exposed_people_accounted_for",
            "qualification_limited" if pairs_exact else "unavailable", "initial",
            (*all_people, *candidate_ids), native_source, route_refs,
            "one provider proposal record per pair; unavailable is not measured; alternatives unproven",
            ("CityNativeServiceRunner.assembly_affordances",)),
        "person_guidance_acknowledgement": row(
            "exposed_people_accounted_for", "execution_only", "execution",
            (*guide_providers, *all_people), native_source, [],
            "per-person admitted service acknowledgement within 20 seconds",
            ("GoalAlgorithmAdapter.act", "CityEpisodeEvidenceHost.record_actor_ack")),
        "person_safe_assembly_arrival": row(
            "exposed_people_accounted_for", "execution_only", "execution",
            (*guide_providers, *all_people, *candidate_ids),
            native_source, [],
            "per-person measured safe trajectory and capacity-valid arrival within 60 seconds of ACK",
            ("CityEpisodeEvidenceHost.record_person_readback",)),
        "following_vehicle_roster": row(
            "secondary_traffic_protected",
            "measured_initial" if traffic_measurement else "unavailable", "initial",
            all_vehicles, native_source, observation_refs if traffic_measurement else [],
            "exact six-vehicle following-traffic identity and current pose",
            ("CityEpisodeEvidenceHost.observation",)),
        "traffic_control_acknowledgement": row(
            "secondary_traffic_protected", "execution_only", "execution",
            (*traffic_providers, *all_vehicles), native_source, [],
            "per-vehicle admitted control acknowledgement within 10 seconds",
            ("GoalAlgorithmAdapter.act", "CityEpisodeEvidenceHost.record_actor_ack")),
        "traffic_reaction_and_ingress": row(
            "secondary_traffic_protected", "execution_only", "execution",
            (*traffic_providers, *all_vehicles),
            native_source, [],
            "measured trajectory after the five-second reaction window with no hazard ingress",
            ("CityEpisodeEvidenceHost.record_traffic_readback",)),
        "incident_report_capability": row(
            "response_access_preserved", "declared_only", "initial", report_providers,
            ["public_task_manifest"], task_ref,
            "declared report provider; endpoint liveness unproven",
            ("build_city_autonomous_contract",)),
        "incident_report_receipt": row(
            "response_access_preserved", "execution_only", "execution", report_providers,
            ["city_dispatch.receiver"], [],
            "independent receipt bound to observation refs within 15 seconds",
            ("CityEpisodeEvidenceHost.record_receipt",)),
        "continuous_response_access": row(
            "response_access_preserved", "execution_only", "execution",
            (*report_providers, *all_observed),
            native_source, [],
            "nonempty measured clear interval covering the incident receipt",
            ("CityEpisodeEvidenceHost.record_access_readback",)),
        "response_budget": row(
            "all", "execution_only", "execution", all_observed, native_source, [],
            "all terminal timestamps remain within the 180-second episode budget",
            ("CityNativeGoalBackend.execute",)),
    }


def _skill_coverage(robots: list[Mapping[str, Any]], *,
                    task_sha256: str) -> dict[str, dict[str, Any]]:
    providers: dict[str, list[str]] = {}
    for robot in robots:
        for capability in robot["public_capabilities"]:
            providers.setdefault(capability, []).append(robot["id"])
    result = {}
    for skill, terminal in (
        ("ground_navigation", "native_route_arrival"),
        ("aerial_navigation", "native_route_arrival"),
        ("observe", "delivered_fresh_observation"),
        ("relay", "delivered_message"),
        ("guide_people", "per_person_ack_and_safe_assembly_arrival"),
        ("control_following_traffic", "per_vehicle_ack_and_post_reaction_readback"),
        ("report_incident", "independent_receiver_receipt"),
    ):
        result[skill] = {
            "declared_providers": sorted(providers.get(skill, ())),
            "status": "declared_only",
            "phase": "initial",
            "source_ids": ["public_task_manifest"],
            "evidence_refs": [f"public_task_sha256:{task_sha256}"],
            "terminal_evidence_required": terminal,
            "limitation": "declared_capability_is_not_measured_liveness_or_future_success",
        }
    return result


def _maximum_admissible_matching(routes: list[Mapping[str, Any]]) -> int:
    """Return only a diagnostic matching cardinality over measured proposals."""
    edges: dict[str, list[str]] = {}
    for row in routes:
        if row.get("status") == "admissible":
            edges.setdefault(str(row["person_id"]), []).append(str(row["region_id"]))
    matched: dict[str, str] = {}

    def assign(person_id: str, seen: set[str]) -> bool:
        for region_id in sorted(edges.get(person_id, ())):
            if region_id in seen:
                continue
            seen.add(region_id)
            previous = matched.get(region_id)
            if previous is None or assign(previous, seen):
                matched[region_id] = person_id
                return True
        return False

    return sum(assign(person_id, set()) for person_id in sorted(edges))


def _observation_measurement(ctx: Mapping[str, Any], *, native_episode_id: str | None,
                             source_id: str | None, now_s: float,
                             issues: list[dict[str, str]]) -> tuple[dict[str, Any], set[str]]:
    runner = ctx.get("runner")
    host = getattr(runner, "host", None)
    lookup = getattr(host, "observation", None)
    reference = getattr(runner, "_observation_ref", None) or ctx.get("guidance_observation")
    empty = {"status": "unavailable", "observation_ref": reference,
             "person_ids": [], "following_vehicle_ids": [], "entities": []}
    if not callable(lookup) or not isinstance(reference, str) or not reference:
        issues.append(_issue("initial_observation_reader_missing",
                             "an existing delivered-observation reader/reference is required"))
        return empty, set()
    try:
        row = lookup(reference)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        issues.append(_issue("initial_observation_reader_failed", type(exc).__name__))
        return empty, set()
    if not isinstance(row, Mapping):
        issues.append(_issue("initial_observation_missing", "reader returned no mapping"))
        return empty, set()
    try:
        captured = _time(row.get("captured_at_s"), "observation captured_at_s")
        delivered = _time(row.get("delivered_at_s"), "observation delivered_at_s")
        expires = _time(row.get("valid_until_s"), "observation valid_until_s")
        if captured > delivered or delivered > now_s or expires <= now_s:
            raise ValueError("observation is future, expired, or causally inverted")
        if captured != now_s:
            issues.append(_issue("initial_observation_not_current",
                                 "observation capture does not equal the initial native tick"))
            return empty, set()
        if row.get("episode_id") != native_episode_id or row.get("source_id") != source_id:
            raise ValueError("observation source/native episode mismatch")
        entities = row.get("entities")
        if not isinstance(entities, list):
            raise ValueError("observation entities must be a list")
        projected, identities = [], set()
        for entity in entities:
            if not isinstance(entity, Mapping):
                raise ValueError("observation entity must be an object")
            identity, kind = _text(entity.get("id"), "entity id"), entity.get("kind")
            if kind not in {"person", "vehicle"} or (kind, identity) in identities:
                raise ValueError("invalid or duplicate observed entity identity")
            if (entity.get("episode_id") != native_episode_id
                    or entity.get("source_id") != source_id):
                raise ValueError("observation entity source/native episode mismatch")
            identities.add((kind, identity))
            position = entity.get("position_xyz")
            if (not isinstance(position, (list, tuple)) or len(position) != 3
                    or any(isinstance(value, bool) or not isinstance(value, (int, float))
                           or not math.isfinite(value) for value in position)):
                raise ValueError("entity position must contain three finite values")
            projected_row = {"id": identity, "kind": kind,
                             "position_m": [float(value) for value in position]}
            if kind == "vehicle":
                if (entity.get("following_incident") is not True
                        or entity.get("is_accident_vehicle") is not False):
                    raise ValueError("vehicle is not confirmed following traffic")
                projected_row.update(following_incident=True, is_accident_vehicle=False)
            projected.append(projected_row)
        persons = sorted(identity for kind, identity in identities if kind == "person")
        vehicles = sorted(identity for kind, identity in identities if kind == "vehicle")
        if tuple(persons) != EXPECTED_PERSON_IDS:
            issues.append(_issue("affected_person_roster_incomplete",
                                 "initial observation does not bind the exact four-person roster"))
        if tuple(vehicles) != EXPECTED_VEHICLE_IDS:
            issues.append(_issue("following_vehicle_roster_incomplete",
                                 "initial observation does not bind the exact six-vehicle roster"))
        return {
            "status": "measured",
            "observation_ref": reference,
            "captured_native_s": captured,
            "delivered_native_s": delivered,
            "valid_until_native_s": expires,
            "person_ids": persons,
            "following_vehicle_ids": vehicles,
            "entities": sorted(projected, key=lambda item: (item["kind"], item["id"])),
        }, set(persons)
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(_issue("initial_observation_malformed", str(exc)))
        return empty, set()


def _affordance_measurement(ctx: Mapping[str, Any], public_task: Mapping[str, Any], *,
                            native_episode_id: str | None, source_id: str | None,
                            epoch_s: float, now_s: float, observed_people: set[str],
                            issues: list[dict[str, str]],
                            limitations: set[str]) -> dict[str, Any]:
    city = ctx.get("city")
    service = getattr(city, "_episode_service", None)
    if getattr(service, "guidance_profile", None) != PROFILE:
        issues.append(_issue("requested_target_profile_inactive",
                             "initial qualification requires requested_target_v1"))
        return {"status": "unavailable", "profile": getattr(service, "guidance_profile", None)}
    reader = getattr(service, "assembly_affordances", None)
    if not callable(reader):
        issues.append(_issue("assembly_affordance_reader_missing",
                             "requested-target reader is unavailable"))
        return {"status": "unavailable", "profile": PROFILE}
    if not observed_people:
        issues.append(_issue("assembly_affordance_people_unbound",
                             "route-pair validation requires observed people"))
        return {"status": "unavailable", "profile": PROFILE}
    try:
        raw = reader()
        if not isinstance(raw, Mapping):
            raise ValueError("assembly reader returned no mapping")
        raw_candidates = raw.get("candidates")
        raw_routes = raw.get("routes")
        if (not isinstance(raw_candidates, list)
                or any(not isinstance(row, Mapping) for row in raw_candidates)):
            raise ValueError("assembly candidates must be a list of objects")
        if (not isinstance(raw_routes, list)
                or any(not isinstance(row, Mapping) for row in raw_routes)):
            raise ValueError("assembly routes must be a list of objects")
        for row in raw_candidates:
            readback = row.get("native_readback")
            if not isinstance(readback, Mapping):
                raise ValueError("candidate native_readback must be an object")
            if not isinstance(readback.get("region_metadata"), Mapping):
                raise ValueError("candidate region_metadata must be an object")
        projected = project_assembly_affordances(
            raw, public_task, native_episode_id=str(native_episode_id),
            native_source_id=str(source_id), clock_epoch_s=epoch_s,
            observed_person_ids=observed_people, captured_at_s=now_s)
        raw_candidates = {row.get("region_id"): row for row in raw_candidates}
        for candidate in projected["candidates"]:
            raw_row = raw_candidates.get(candidate["region_id"])
            metadata = ((raw_row or {}).get("native_readback") or {}).get("region_metadata")
            derivation = metadata.get("derivation") if isinstance(metadata, Mapping) else None
            if not isinstance(derivation, str) or not derivation.strip():
                issues.append(_issue("candidate_derivation_missing", candidate["region_id"]))
                candidate["derivation"] = None
            else:
                candidate["derivation"] = derivation
                if "author" in derivation:
                    limitations.add("author_derived_candidate_geometry_not_person_independent")
        route_counts = {status: sum(row["status"] == status for row in projected["routes"])
                        for status in ("admissible", "blocked", "unavailable")}
        expected_pairs = len(observed_people) * len(projected["candidates"])
        capacities = [row["capacity"] for row in projected["candidates"]]
        matching = ({
            "cardinality": _maximum_admissible_matching(projected["routes"]),
            "semantics": "diagnostic_one_proposal_record_per_pair_capacity_one_only",
            "alternative_complete": False,
        } if capacities and all(capacity == 1 for capacity in capacities) else {
            "cardinality": None,
            "semantics": "skipped_non_unit_candidate_capacity",
            "alternative_complete": False,
        })
        exact_pairs = (set(observed_people) == set(EXPECTED_PERSON_IDS)
                       and len(projected["candidates"]) == 4
                       and len(projected["routes"]) == 16)
        if not exact_pairs:
            issues.append(_issue("assembly_affordance_exact_pair_coverage_incomplete",
                                 "exact task requires all four people by four candidates"))
        projected.update(
            status=("measured" if exact_pairs and not route_counts["unavailable"]
                    else "qualification_limited" if exact_pairs else "incomplete"),
            recorded_pair_count=len(projected["routes"]),
            measured_pair_count=route_counts["admissible"] + route_counts["blocked"],
            observed_pair_denominator=expected_pairs,
            expected_pair_count=16,
            exact_pair_record_coverage=exact_pairs,
            route_status_counts=route_counts,
            admissible_graph_matching=matching,
        )
        return projected
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        issues.append(_issue("assembly_affordance_reader_failed", f"{type(exc).__name__}: {exc}"))
        return {"status": "unavailable", "profile": PROFILE}


def capture_city_initial_qualification(
        ctx: Mapping[str, Any], public_task: Mapping[str, Any], *, episode_id: str,
        clock_epoch_s: float) -> dict[str, Any]:
    """Capture evaluator-only initial evidence without changing native state.

    Missing or malformed native reader evidence is recorded as a quality issue.
    Invalid caller inputs and a non-exact public task remain programming errors.
    """
    validate_goal_task_spec(public_task)
    task_sha256 = canonical_sha256(public_task)
    if (public_task.get("task_id") != EXPECTED_TASK_ID
            or public_task.get("version") != 3
            or public_task.get("mission", {}).get("scene_key") != "city"
            or task_sha256 != EXPECTED_PUBLIC_TASK_SHA256):
        raise ValueError("initial qualification requires the exact frozen v3 CITY-GOAL-NATIVE-01 task")
    goal_episode_id = _text(episode_id, "episode_id")
    epoch = _time(clock_epoch_s, "clock_epoch_s")
    if not isinstance(ctx, Mapping):
        raise ValueError("native City context must be a mapping")
    city, runner = ctx.get("city"), ctx.get("runner")
    if city is None or runner is None:
        raise ValueError("native City context requires city and runner")
    before = _time(getattr(city, "elapsed", None), "native clock before capture")
    if before < epoch:
        raise ValueError("native clock predates the supplied epoch")
    adapter = getattr(runner, "adapter", None)
    native_episode_id = getattr(adapter, "episode_id", None)
    source_id = getattr(adapter, "source_id", None)
    issues: list[dict[str, str]] = []
    if not isinstance(native_episode_id, str) or not native_episode_id:
        issues.append(_issue("native_episode_id_missing", "runner adapter has no native episode id"))
        native_episode_id = None
    if not isinstance(source_id, str) or not source_id:
        issues.append(_issue("native_source_id_missing", "runner adapter has no native source id"))
        source_id = None

    limitations = {
        "initial_only_no_future_service_terminals",
        "declared_robot_capabilities_not_measured_liveness",
        "one_route_proposal_per_pair_not_alternative_complete",
        "no_whole_task_schedule_or_continuous_interval_proof",
        "evidence_collection_not_registered_feasibility_oracle",
    }
    observation, observed_people = _observation_measurement(
        ctx, native_episode_id=native_episode_id, source_id=source_id,
        now_s=before, issues=issues)
    affordance = _affordance_measurement(
        ctx, public_task, native_episode_id=native_episode_id, source_id=source_id,
        epoch_s=epoch, now_s=before, observed_people=observed_people,
        issues=issues, limitations=limitations)
    after = _time(getattr(city, "elapsed", None), "native clock after capture")
    if after != before:
        issues.append(_issue("native_clock_changed_during_capture",
                             "read-only capture did not retain one native tick"))

    robots = _declared_robots(public_task)
    candidate_ids = tuple(sorted(
        row["id"] for row in public_task["public_world"]["regions"]
        if row["kind"] == "assembly_candidate"
    ))
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "visibility": "evaluator_only",
        "boundary": "before_participant_initialize",
        "measurement_scope": "initial_only",
        "episode_id": goal_episode_id,
        "native_episode_id": native_episode_id,
        "source_id": source_id,
        "captured_native_s": before,
        "clock_epoch_s": epoch,
        "captured_at_s": before - epoch,
        "public_task_sha256": task_sha256,
        "measurements": {
            "native_clock": {"before_s": before, "after_s": after, "stable": before == after},
            "task": {
                "task_id": public_task["task_id"],
                "task_version": public_task["version"],
                "simulation_budget_s": public_task["budgets"]["simulation_s"],
                "max_active_agents": public_task["budgets"]["max_active_agents"],
            },
            "declared_robots": robots,
            "observation": observation,
            "assembly_affordance": affordance,
        },
        "quality_issues": issues,
        "limitation_codes": sorted(limitations),
        "hard_predicate_coverage": _coverage(
            observation, affordance, robots=robots, candidate_ids=candidate_ids,
            source_id=source_id,
            task_sha256=task_sha256, clock_stable=before == after),
        "skill_evidence_coverage": _skill_coverage(
            robots, task_sha256=task_sha256),
        **_RELEASE,
    }


__all__ = ["SCHEMA", "VERSION", "capture_city_initial_qualification"]
