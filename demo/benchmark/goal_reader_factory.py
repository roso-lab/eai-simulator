"""Factory native remaining-task reader (contract-only development track).

Wires the trusted-state/oracle contracts onto the Factory business family
``production_anomaly_confirmation_and_maintenance_dispatch``.  Closes the
STAGE29 gaps for anomaly identity with an independent current observation,
actual service arrival, service state and the maintenance handover receipt.

This backend is contract-only: the frozen Factory evaluator fixture requires
a USD whose SHA256 does not match any trusted provider version, so every
output here carries the ``factory_fixture_hash_mismatch`` development marker.
Nothing in this module claims real Factory execution was verified, and the
mismatch blocks release only — contract development continues.

Per the oracle acceptance matrix, the fixed response service vehicle is not
assigned a fabricated carrying requirement: its transition needs navigation
to the response zone and nothing more.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .development_track_marker import factory_hash_mismatch_marker, stamp_evidence_record
from .goal_alternative_path_certificate import (
    build_alternative_path_certificate,
    gate_not_deployable_result,
)
from .goal_driven_tasks import build_goal_task_package
from .goal_remaining_feasibility_oracle import (
    freeze_oracle_registration,
    query_remaining_feasibility,
)
from .goal_trusted_state import freeze_state_contract, ground_state
from .provenance import canonical_sha256

FACTORY_SCENE_KEY = "factory"
FACTORY_DEVELOPMENT_SCOPE = "synthetic_contract_development"

FACTORY_FACT_IDS = ("anomaly_pending_observation", "anomaly_confirmed",
                    "response_area_served", "maintenance_handover_recorded")
FACTORY_ENTITY_IDS = ("anomaly_station_1", "response_zone_1",
                      "maintenance_desk_1")
FACTORY_SOURCE_VERSIONS = {
    "factory_anomaly_observation": "anomaly-observation-dev-v1",
    "factory_service_arrival": "service-arrival-dev-v1",
    "factory_maintenance_handover": "maintenance-handover-dev-v1",
}
FACTORY_PREDICATE_BINDINGS = {
    "anomaly_pending_observation": {
        "source_id": "factory_anomaly_observation",
        "evidence_kind": "observation_record",
        "entity_ids": ["anomaly_station_1"]},
    "anomaly_confirmed": {
        "source_id": "factory_anomaly_observation",
        "evidence_kind": "observation_record",
        "entity_ids": ["anomaly_station_1"]},
    "response_area_served": {
        "source_id": "factory_service_arrival",
        "evidence_kind": "physical_completion",
        "entity_ids": ["response_zone_1"]},
    "maintenance_handover_recorded": {
        "source_id": "factory_maintenance_handover",
        "evidence_kind": "business_receipt",
        "entity_ids": ["maintenance_desk_1"]},
}
FACTORY_TERMINAL_ALL = ("anomaly_confirmed", "response_area_served",
                        "maintenance_handover_recorded")

FACTORY_MODEL = {
    "fact_ids": list(FACTORY_FACT_IDS),
    "terminal_all": list(FACTORY_TERMINAL_ALL),
    "resource_bounds": {"shift_time_budget": 6},
    "transitions": [
        # Independent current observation of the anomaly identity; a plan or
        # a self-report from the participant can never set this predicate.
        {"id": "observe_anomaly_independently",
         "requires_all": ["anomaly_pending_observation"],
         "requires_absent": ["anomaly_confirmed"],
         "adds": ["anomaly_confirmed"],
         "deletes": ["anomaly_pending_observation"],
         "capability_slots": [{"requires_all": ["observe"], "min_payload_kg": 0}],
         "resource_costs": {"shift_time_budget": 1}},
        # Actual service arrival: navigation to the response zone only.  The
        # fixed service vehicle gets no fabricated carrying requirement.
        {"id": "serve_response_area", "requires_all": ["anomaly_confirmed"],
         "requires_absent": [], "adds": ["response_area_served"], "deletes": [],
         "capability_slots": [{"requires_all": ["navigate"], "min_payload_kg": 10}],
         "resource_costs": {"shift_time_budget": 1}},
        {"id": "record_maintenance_handover",
         "requires_all": ["response_area_served"], "requires_absent": [],
         "adds": ["maintenance_handover_recorded"], "deletes": [],
         "capability_slots": [{"requires_all": ["relay"], "min_payload_kg": 0}],
         "resource_costs": {"shift_time_budget": 1}},
    ],
}

FACTORY_MEASUREMENT_POINTS = (
    {"id": "factory_initial", "trigger": "initial_deployment",
     "required_true": ["anomaly_pending_observation"],
     "required_false": ["anomaly_confirmed", "response_area_served",
                        "maintenance_handover_recorded"],
     "varying_facts": []},
    # Corridor-capability exit contrast before/after the response area is
    # served: the same exit flips feasibility only while service is pending.
    {"id": "factory_before_service_served", "trigger": "capability_change",
     "required_true": ["anomaly_confirmed"],
     "required_false": ["anomaly_pending_observation", "response_area_served",
                        "maintenance_handover_recorded"],
     "varying_facts": []},
    {"id": "factory_service_served", "trigger": "milestone_state_change",
     "required_true": ["anomaly_confirmed", "response_area_served"],
     "required_false": ["anomaly_pending_observation",
                        "maintenance_handover_recorded"],
     "varying_facts": []},
)

FACTORY_OBSERVER = {"id": "factory_carter_1",
                    "capabilities": ["observe", "navigate"], "payload_kg": 40}
FACTORY_SERVICE_VEHICLE = {"id": "factory_carter_2",
                           "capabilities": ["navigate", "relay"], "payload_kg": 40}
FACTORY_RELAY_ONLY = {"id": "factory_go2_1",
                      "capabilities": ["relay"], "payload_kg": 5}

FACTORY_DEV_CASES = {
    # case_id: (measurement_id, robots, resources)
    "full_team_initial": ("factory_initial",
                          [FACTORY_OBSERVER, FACTORY_SERVICE_VEHICLE],
                          {"shift_time_budget": 6}),
    "observer_exits_before_confirmation": (
        "factory_initial", [FACTORY_SERVICE_VEHICLE], {"shift_time_budget": 6}),
    "corridor_capability_exits_before_service": (
        "factory_before_service_served", [FACTORY_RELAY_ONLY],
        {"shift_time_budget": 6}),
    "navigation_exits_after_service_served": (
        "factory_service_served", [FACTORY_RELAY_ONLY],
        {"shift_time_budget": 6}),
    "budget_exhausted": ("factory_initial",
                         [FACTORY_OBSERVER, FACTORY_SERVICE_VEHICLE],
                         {"shift_time_budget": 1}),
}


def factory_development_marker(*, evidence_class: str = "contract") -> dict[str, Any]:
    # Contract-only backend: the marker is the frozen-evaluator hash mismatch
    # itself, not a separate admission-pending reason.
    return factory_hash_mismatch_marker(evidence_class=evidence_class)


def build_factory_public_task() -> dict[str, Any]:
    return build_goal_task_package(FACTORY_SCENE_KEY)["public_task"]


def build_factory_registration(
    *, validation_scope: str = FACTORY_DEVELOPMENT_SCOPE,
    evidence_refs: tuple[str, ...] = ("dev:factory-model-review-v1",),
) -> dict[str, Any]:
    return freeze_oracle_registration(
        deepcopy(FACTORY_MODEL),
        task_sha256=canonical_sha256(build_factory_public_task()),
        measurement_points=deepcopy(list(FACTORY_MEASUREMENT_POINTS)),
        validation={"status": "verified", "scope": validation_scope,
                    "evidence_refs": list(evidence_refs)})


def build_factory_state_contract(registration: Mapping[str, Any]) -> dict[str, Any]:
    return freeze_state_contract(
        build_factory_public_task(), registration,
        predicate_bindings=deepcopy(FACTORY_PREDICATE_BINDINGS),
        outcome_predicates={
            "anomaly_confirmed": ["anomaly_confirmed"],
            "response_area_served": ["response_area_served"],
            "maintenance_handoff_recorded": ["maintenance_handover_recorded"],
        },
        source_versions=dict(FACTORY_SOURCE_VERSIONS),
        entity_ids=list(FACTORY_ENTITY_IDS),
        asset_manifest_sha256="3" * 64, skill_contract_sha256="4" * 64)


def build_factory_development_certificate(
    registration: Mapping[str, Any], *, independent_review: str = "pending",
):
    return build_alternative_path_certificate(
        FACTORY_SCENE_KEY, registration,
        path_classes={
            "observation_then_service_then_handover": {
                "description": "独立观察确认、服务车实际到达、维护交接回执的唯一合法业务序列",
                "covered_transitions": ["observe_anomaly_independently",
                                        "serve_response_area",
                                        "record_maintenance_handover"]},
        },
        covered_measurement_ids=[point["id"] for point in
                                 FACTORY_MEASUREMENT_POINTS],
        uncovered_path_policy="treated_as_uncovered",
        independent_review=independent_review,
        evidence_refs=["dev:factory-path-review-v1"])


def make_factory_source_readers(
    native_probes: Mapping[str, Callable] | None = None,
):
    probes = dict(native_probes or {})

    def reader(source_id: str):
        def measure(snapshot: Mapping[str, Any], observation: Mapping[str, Any]):
            state = observation["state"]
            if source_id in probes:
                values = probes[source_id](state)
            else:
                samples = observation.get("native_samples", {})
                if samples.get("measurement_id") != observation["measurement_id"]:
                    return {}
                values = samples.get("sources", {}).get(source_id)
            if not isinstance(values, Mapping):
                return {}
            expected = {fact for fact, binding in FACTORY_PREDICATE_BINDINGS.items()
                        if binding["source_id"] == source_id}
            if set(values) != expected:
                return {}
            entities = sorted({entity for fact in expected
                               for entity in FACTORY_PREDICATE_BINDINGS[fact]["entity_ids"]})
            return {
                "source_id": source_id,
                "source_version": FACTORY_SOURCE_VERSIONS[source_id],
                "measurement_id": observation["measurement_id"],
                "state_sha256": canonical_sha256(state),
                "measured_at_s": state["time_s"],
                "entity_versions": {entity: state["entity_versions"][entity]
                                    for entity in entities},
                "predicates": {fact: values[fact] is True for fact in sorted(values)},
                "evidence_refs": [f"factory:{source_id}:{observation['measurement_id']}"],
            }
        return measure
    return {source_id: reader(source_id) for source_id in FACTORY_SOURCE_VERSIONS}


def make_factory_state_provider(measurement_id: str, facts: set[str], *,
                                robots: list[dict[str, Any]],
                                resources: Mapping[str, int], time_s: float = 6.0):
    versions = {entity: 1 for entity in FACTORY_ENTITY_IDS}
    capability_versions = {robot["id"]: 1 for robot in robots}

    def provide(snapshot: Mapping[str, Any]):
        state = {
            "time_s": time_s,
            "true_facts": sorted(facts),
            "entity_versions": versions,
            "resources": dict(resources),
            "max_active_agents": len(robots),
            "robots": deepcopy(robots),
            "capability_versions": capability_versions,
            "roster_version": 1,
            "resource_versions": {"shift_time_budget": 1},
        }
        by_source: dict[str, dict[str, bool]] = {}
        for fact, binding in FACTORY_PREDICATE_BINDINGS.items():
            by_source.setdefault(binding["source_id"], {})[fact] = fact in facts
        return {"measurement_id": measurement_id, "state": state,
                "native_samples": {"measurement_id": measurement_id,
                                   "sources": by_source}}

    return provide


def query_factory_development_case(
    case_id: str, *, episode_id: str = "factory-dev-episode",
    registration: Mapping[str, Any] | None = None,
    state_contract: Mapping[str, Any] | None = None,
    certificate: Mapping[str, Any] | None = None,
    native_probes: Mapping[str, Callable] | None = None,
) -> dict[str, Any]:
    """Run one contract-only case; outputs always carry the hash-mismatch marker."""
    if case_id not in FACTORY_DEV_CASES:
        raise ValueError(f"unknown factory development case: {case_id}")
    measurement_id, robots, resources = FACTORY_DEV_CASES[case_id]
    point = next(p for p in FACTORY_MEASUREMENT_POINTS
                 if p["id"] == measurement_id)
    facts = set(point["required_true"])
    registration = registration or build_factory_registration()
    contract = state_contract or build_factory_state_contract(registration)
    certificate = certificate or build_factory_development_certificate(registration)
    provider = make_factory_state_provider(
        measurement_id, facts, robots=deepcopy(robots), resources=resources)
    readers = make_factory_source_readers(native_probes)
    snapshot = {"assessment_id": f"{case_id}-a1", "assessment_epoch": 0}
    observation = provider(snapshot)
    packets = {source: readers[source](snapshot, observation)
               for source in FACTORY_SOURCE_VERSIONS}
    grounding = ground_state(contract, registration,
                             measurement_id=measurement_id,
                             state=observation["state"], readbacks=packets)
    result = query_remaining_feasibility(
        registration, measurement_id=measurement_id, episode_id=episode_id,
        assessment_id=snapshot["assessment_id"], assessment_epoch=0,
        task_sha256=registration["task_sha256"],
        state=observation["state"], grounding=grounding)
    gated = gate_not_deployable_result(result, certificate, registration)
    return stamp_evidence_record(gated, factory_development_marker())


__all__ = [
    "FACTORY_DEVELOPMENT_SCOPE", "FACTORY_ENTITY_IDS", "FACTORY_FACT_IDS",
    "FACTORY_MEASUREMENT_POINTS", "FACTORY_MODEL", "FACTORY_PREDICATE_BINDINGS",
    "FACTORY_SCENE_KEY", "FACTORY_SOURCE_VERSIONS", "FACTORY_TERMINAL_ALL",
    "build_factory_development_certificate", "build_factory_public_task",
    "build_factory_registration", "build_factory_state_contract",
    "factory_development_marker", "make_factory_source_readers",
    "make_factory_state_provider", "query_factory_development_case",
]
