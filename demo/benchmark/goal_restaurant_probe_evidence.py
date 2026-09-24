"""Derive remaining-task probe evidence from a formal Restaurant episode.

The verified formal episode archive already carries per-leg native evidence
(arrival root states, contact-health measurements, control-step evidence,
service observations and the readiness receipt publication).  This adapter
replays that archive into the Restaurant remaining-task reader: every model
fact is derived from its **own** evidence record — zone arrival facts from
arrival/contact records, zone observation facts from observation records,
the receipt fact from the relay publication record — never from a provider's
asserted truth, and each derived fact carries its archive ``evidence_ref``.

The output is development-track evidence (``evidence_class="diagnostic"``):
registration keeps the synthetic-contract scope until an independent native
admission review promotes the evidence chain.  Release flags stay false.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .development_track_marker import build_development_marker, stamp_evidence_record
from .goal_alternative_path_certificate import gate_not_deployable_result
from .goal_reader_restaurant import (
    RESTAURANT_DEV_CASES,
    RESTAURANT_ENTITY_IDS,
    RESTAURANT_PREDICATE_BINDINGS,
    RESTAURANT_SOURCE_VERSIONS,
    build_restaurant_development_certificate,
    build_restaurant_public_task,
    build_restaurant_registration,
    build_restaurant_state_contract,
)
from .goal_remaining_feasibility_oracle import query_remaining_feasibility
from .goal_trusted_state import ground_state
from .provenance import canonical_sha256

RESTAURANT_PROBE_SCHEMA = "eai.benchmark.restaurant_probe_evidence"
RESTAURANT_PROBE_VERSION = 1
RESTAURANT_NATIVE_BLOCKER = "restaurant_native_candidate_evidence_pending"
NATIVE_CLOCK_ID = "restaurant-native-evidence-log"
# The reader's service_time_budget uses 30 s units; the public task's 240 s
# opening budget therefore maps to 8 units, consumed with elapsed time.
BUDGET_UNIT_S = 30.0
SUPPORT_RATIO_RANGE = (0.70, 1.30)

_ZONE_FACTS = {
    "kitchen_service": ("kitchen_arrived", "kitchen_observed"),
    "dining_service": ("dining_arrived", "dining_observed"),
}


class ProbeEvidenceError(ValueError):
    """The archive evidence is malformed; the measurement fails closed."""


def _records(archive_root: Path) -> list[dict[str, Any]]:
    path = archive_root / "goal_episode" / "native_evidence_log.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "eai.restaurant.formal_goal_native_evidence":
        raise ProbeEvidenceError("unexpected native evidence schema")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ProbeEvidenceError("native evidence log has no records")
    return records


def _episode_id(archive_root: Path) -> str:
    report = json.loads(
        (archive_root / "restaurant_algorithm_episode_report.json").read_text(
            encoding="utf-8"))
    episode_id = report.get("episode_id")
    if report.get("business_episode_execution_validated") is not True:
        raise ProbeEvidenceError(
            "probe evidence requires a business-validated formal episode")
    if not isinstance(episode_id, str) or not episode_id:
        raise ProbeEvidenceError("episode report lacks an episode id")
    return episode_id


def _arrival_time(record: Mapping[str, Any]) -> float:
    stamp = record.get("arrival") or record.get("stamp") or {}
    value = stamp.get("time_s")
    if type(value) not in (int, float):
        raise ProbeEvidenceError(f"record {record.get('task_id')!r} lacks a time")
    return float(value)


def _zone_evidence(records: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """One independent evidence bundle per zone fact, from its own records."""
    bundles: dict[str, dict[str, Any]] = {}
    for record in records:
        kind = record.get("kind")
        region = record.get("region_id")
        if kind == "arrival" and region in _ZONE_FACTS:
            support = record.get("median_weight_support_ratio")
            valid = (record.get("contact_health_validated") is True
                     and type(support) in (int, float)
                     and SUPPORT_RATIO_RANGE[0] <= support <= SUPPORT_RATIO_RANGE[1])
            fact = _ZONE_FACTS[region][0]
            bundles[fact] = {
                "time_s": _arrival_time(record),
                "valid": valid,
                "evidence_ref": record.get("evidence_ref"),
                "source": "restaurant_zone_arrival",
                "entity": "kitchen_service_zone_1" if region == "kitchen_service"
                          else "dining_service_zone_1",
            }
        elif kind == "observation" and region in _ZONE_FACTS:
            fact = _ZONE_FACTS[region][1]
            bundles[fact] = {
                "time_s": _arrival_time(record),
                "valid": (isinstance(record.get("scene_observation"), Mapping)
                          and isinstance(record.get("service_record"), Mapping)),
                "evidence_ref": record.get("evidence_ref"),
                "source": "restaurant_service_observations",
                "entity": "kitchen_service_zone_1" if region == "kitchen_service"
                          else "dining_service_zone_1",
            }
        elif kind == "relay_and_receipt":
            publication = record.get("relay_publication") or {}
            receipt = record.get("business_receipt") or {}
            refs = receipt.get("observation_refs")
            published_at = publication.get("time_s")
            if type(published_at) not in (int, float):
                raise ProbeEvidenceError(
                    "relay record lacks a publication time")
            pub_refs = publication.get("observation_refs")
            covers_both_zones = (
                isinstance(pub_refs, list)
                and any("kitchen_service" in ref for ref in pub_refs)
                and any("dining_service" in ref for ref in pub_refs))
            bundles["readiness_receipt_committed"] = {
                "time_s": float(published_at),
                "valid": (receipt.get("kind") == "readiness_receipt"
                          and isinstance(refs, list) and len(refs) >= 2
                          and publication.get("kind") == "relay_publication"
                          and covers_both_zones),
                "evidence_ref": record.get("evidence_ref"),
                "source": "restaurant_readiness_backend",
                "entity": "readiness_backend_1",
            }
    missing = {fact for fact in ("kitchen_arrived", "kitchen_observed",
                                 "dining_arrived", "dining_observed",
                                 "readiness_receipt_committed")} - set(bundles)
    if missing:
        raise ProbeEvidenceError(f"archive lacks evidence for {sorted(missing)}")
    return bundles


def _initial_roster(archive_root: Path) -> set[str]:
    """The episode's selected team, from the participant trace's select_team."""
    trace = json.loads(
        (archive_root / "goal_episode" / "participant_trace.json").read_text(
            encoding="utf-8"))
    actions = trace.get("actions") if isinstance(trace, Mapping) else trace
    for action in actions or []:
        if action.get("type") == "select_team":
            ids = action.get("payload", {}).get("agent_ids")
            if isinstance(ids, list) and ids:
                return set(ids)
    raise ProbeEvidenceError("episode trace lacks a select_team action")


def _team(records: list[Mapping[str, Any]], public_task: Mapping[str, Any],
          time_s: float, initial_roster: set[str]) -> list[dict[str, Any]]:
    """Actual roster up to this point, with pool-declared capabilities.

    The roster starts from the episode's own ``select_team`` choice and only
    grows with later evidence producers; this episode has no member exits.
    """
    pool = {row["id"]: row for row in public_task["candidate_robot_pool"]}
    active = {agent for agent in initial_roster if agent in pool}
    for record in records:
        stamp = record.get("arrival") or record.get("stamp") or {}
        if float(stamp.get("time_s", float("inf"))) <= time_s:
            agent = record.get("producer_id")
            if agent in pool:
                active.add(agent)
    return [{"id": row["id"], "capabilities": list(row["public_capabilities"]),
             "payload_kg": 1} for row in
            (pool[agent] for agent in sorted(active))]


def _readbacks(bundles: Mapping[str, Mapping[str, Any]], facts: set[str],
               measurement_id: str, state: Mapping[str, Any],
               episode_id: str) -> dict[str, dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    for source, version in RESTAURANT_SOURCE_VERSIONS.items():
        expected = {fact for fact, binding in RESTAURANT_PREDICATE_BINDINGS.items()
                    if binding["source_id"] == source}
        entities = sorted({entity for fact in expected
                           for entity in RESTAURANT_PREDICATE_BINDINGS[fact]["entity_ids"]})
        predicates: dict[str, bool] = {}
        refs: list[str] = []
        for fact in sorted(expected):
            bundle = bundles[fact]
            if bundle["source"] != source:
                raise ProbeEvidenceError(f"fact {fact} is not bound to {source}")
            holds = fact in facts and bundle["valid"]
            if fact in facts and not bundle["valid"]:
                raise ProbeEvidenceError(
                    f"evidence for {fact} exists but failed its health gate")
            predicates[fact] = holds
            if holds:
                refs.append(bundle["evidence_ref"])
        packets[source] = {
            "source_id": source,
            "source_version": version,
            "measurement_id": measurement_id,
            "state_sha256": canonical_sha256(dict(state)),
            "measured_at_s": state["time_s"],
            "entity_versions": {entity: state["entity_versions"][entity]
                                for entity in entities},
            "predicates": predicates,
            "evidence_refs": refs or [f"{episode_id}:{source}:{measurement_id}"],
        }
    return packets


def derive_restaurant_probe_evidence(archive_root: str | Path) -> dict[str, Any]:
    """Replay one verified formal episode into reader-ready probe evidence."""
    root = Path(archive_root)
    episode_id = _episode_id(root)
    records = _records(root)
    initial_roster = _initial_roster(root)
    bundles = _zone_evidence(records)
    public_task = build_restaurant_public_task()
    registration = build_restaurant_registration()
    contract = build_restaurant_state_contract(registration)
    certificate = build_restaurant_development_certificate(registration)

    # Measurement cut points: each milestone's own evidence time.
    cut_points = [
        ("restaurant_initial", 0.0),
        ("restaurant_kitchen_inspected",
         max(bundles["kitchen_arrived"]["time_s"],
             bundles["kitchen_observed"]["time_s"])),
        ("restaurant_both_zones_inspected",
         max(bundles["dining_arrived"]["time_s"],
             bundles["dining_observed"]["time_s"],
             bundles["kitchen_observed"]["time_s"])),
        ("restaurant_receipt_committed",
         bundles["readiness_receipt_committed"]["time_s"]),
    ]
    if not (cut_points[0][1] < cut_points[1][1] < cut_points[2][1] < cut_points[3][1]):
        raise ProbeEvidenceError("archive timeline is not strictly increasing")

    measurements: list[dict[str, Any]] = []
    for measurement_id, time_s in cut_points:
        facts = {fact for fact, bundle in bundles.items()
                 if bundle["time_s"] <= time_s}
        versions: dict[str, int] = {}
        for entity in RESTAURANT_ENTITY_IDS:
            versions[entity] = sum(
                1 for fact, bundle in bundles.items()
                if bundle["time_s"] <= time_s and (
                    bundle["entity"] == entity or entity == "event_bus_channel_1"
                    and fact == "readiness_receipt_committed"))
        robots = _team(records, public_task, time_s, initial_roster)
        units_used = int(time_s // BUDGET_UNIT_S)
        state = {
            "time_s": time_s,
            "true_facts": sorted(facts),
            "entity_versions": versions,
            "resources": {"service_time_budget": max(0, 8 - units_used)},
            "max_active_agents": len(robots),
            "robots": robots,
            "capability_versions": {robot["id"]: 1 for robot in robots},
            "roster_version": 1,
            "resource_versions": {"service_time_budget": 1},
        }
        readbacks = _readbacks(bundles, facts, measurement_id, state, episode_id)
        grounding = ground_state(contract, registration,
                                 measurement_id=measurement_id,
                                 state=state, readbacks=readbacks)
        result = query_remaining_feasibility(
            registration, measurement_id=measurement_id, episode_id=episode_id,
            assessment_id=f"{episode_id}:probe", assessment_epoch=0,
            task_sha256=registration["task_sha256"], state=state,
            grounding=grounding)
        gated = gate_not_deployable_result(result, certificate, registration)
        measurements.append({
            "measurement_id": measurement_id,
            "native_clock": {"measurement_id": measurement_id,
                             "clock_epoch": episode_id,
                             "clock_id": NATIVE_CLOCK_ID,
                             "time_s": time_s},
            "facts": sorted(facts),
            "fact_evidence": {fact: bundles[fact]["evidence_ref"]
                              for fact in sorted(facts)},
            "entity_versions": versions,
            "grounding": grounding,
            "feasibility": {key: gated.get(key) for key in (
                "feasibility_label", "oracle_status", "platform_valid",
                "quality_failure", "alternative_path_gate")},
        })
    labels = [row["feasibility"]["feasibility_label"] for row in measurements]
    report = {
        "schema": RESTAURANT_PROBE_SCHEMA,
        "version": RESTAURANT_PROBE_VERSION,
        "visibility": "evaluator_only",
        "episode_id": episode_id,
        "archive_root": str(root),
        "registration_sha256": registration["registration_sha256"],
        "state_contract_sha256": contract["contract_sha256"],
        "validation_scope": registration["validation"]["scope"],
        "measurements": measurements,
        "all_points_binary_or_failed": all(
            label in {"deployable", "not_deployable", None} for label in labels),
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    return stamp_evidence_record(
        report, build_development_marker(
            "restaurant", asset_status="development_unverified",
            evidence_class="diagnostic",
            infrastructure_blockers=[RESTAURANT_NATIVE_BLOCKER]))


def write_restaurant_probe_evidence(archive_root: str | Path,
                                    output_path: str | Path) -> Path:
    report = derive_restaurant_probe_evidence(archive_root)
    path = Path(output_path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                               sort_keys=True), encoding="utf-8")
    return path


__all__ = [
    "RESTAURANT_NATIVE_BLOCKER", "RESTAURANT_PROBE_SCHEMA",
    "RESTAURANT_PROBE_VERSION", "ProbeEvidenceError",
    "derive_restaurant_probe_evidence", "write_restaurant_probe_evidence",
]
