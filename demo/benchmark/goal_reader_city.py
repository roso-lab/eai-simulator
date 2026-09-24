"""City native remaining-task reader (development track).

Wires the trusted-state/oracle contracts onto the City business family
``urban_incident_response``.  Closes the STAGE29 gaps for a complete runtime
state provider, person/vehicle/service entity-version binding and legal
alternative paths: ground guidance and aerial service are two distinct legal
transitions toward the same terminal predicate, and their failure contrast is
preregistered as measurement points.

The backend is synthetic until host-installed ``native_probes`` replace it;
development queries keep binary labels solver-only, fail closed on reader
errors, and never promote release flags.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .development_track_marker import (
    build_development_marker,
    stamp_evidence_record,
)
from .goal_driven_tasks import build_goal_task_package
from .goal_remaining_feasibility_oracle import (
    freeze_oracle_registration,
    query_remaining_feasibility,
)
from .goal_trusted_state import freeze_state_contract, ground_state
from .provenance import canonical_sha256

CITY_SCENE_KEY = "city"
CITY_DEVELOPMENT_SCOPE = "synthetic_contract_development"
CITY_NATIVE_SCOPE = "native_remaining_task"
CITY_NATIVE_BLOCKER = "city_native_admission_pending"

CITY_FACT_IDS = ("incident_assessed", "people_accounted",
                 "traffic_protected", "access_preserved")
CITY_ENTITY_IDS = ("incident_1", "person_1", "person_2",
                   "vehicle_1", "response_corridor_1")
CITY_SOURCE_VERSIONS = {
    "city_incident_system": "incident-system-dev-v1",
    "city_people_tracking": "people-tracking-dev-v1",
    "city_traffic_service": "traffic-service-dev-v1",
    "city_access_service": "access-service-dev-v1",
}
CITY_PREDICATE_BINDINGS = {
    "incident_assessed": {"source_id": "city_incident_system",
                          "evidence_kind": "business_receipt",
                          "entity_ids": ["incident_1"]},
    "people_accounted": {"source_id": "city_people_tracking",
                         "evidence_kind": "observation_record",
                         "entity_ids": ["person_1", "person_2"]},
    "traffic_protected": {"source_id": "city_traffic_service",
                          "evidence_kind": "business_receipt",
                          "entity_ids": ["vehicle_1"]},
    "access_preserved": {"source_id": "city_access_service",
                         "evidence_kind": "physical_completion",
                         "entity_ids": ["response_corridor_1"]},
}
CITY_TERMINAL_ALL = tuple(CITY_FACT_IDS)

CITY_MODEL = {
    "fact_ids": list(CITY_FACT_IDS),
    "terminal_all": list(CITY_TERMINAL_ALL),
    "resource_bounds": {"dispatch_budget": 6},
    "transitions": [
        {"id": "assess_incident", "requires_all": [], "requires_absent": [],
         "adds": ["incident_assessed"], "deletes": [],
         "capability_slots": [{"requires_all": ["assess"], "min_payload_kg": 0}],
         "resource_costs": {"dispatch_budget": 1}},
        # Ground guidance and aerial service are separate legal paths to the
        # same terminal predicate; the solver enumerates both alternatives.
        {"id": "guide_people_by_ground", "requires_all": ["incident_assessed"],
         "requires_absent": ["people_accounted"],
         "adds": ["people_accounted"], "deletes": [],
         "capability_slots": [{"requires_all": ["ground_guide"], "min_payload_kg": 0}],
         "resource_costs": {"dispatch_budget": 1}},
        {"id": "serve_people_by_air", "requires_all": ["incident_assessed"],
         "requires_absent": ["people_accounted"],
         "adds": ["people_accounted"], "deletes": [],
         "capability_slots": [{"requires_all": ["aerial_service"], "min_payload_kg": 0}],
         "resource_costs": {"dispatch_budget": 1}},
        {"id": "protect_traffic", "requires_all": ["incident_assessed"],
         "requires_absent": [], "adds": ["traffic_protected"], "deletes": [],
         "capability_slots": [{"requires_all": ["traffic_control"], "min_payload_kg": 0}],
         "resource_costs": {"dispatch_budget": 1}},
        {"id": "preserve_response_access", "requires_all": ["traffic_protected"],
         "requires_absent": [], "adds": ["access_preserved"], "deletes": [],
         "capability_slots": [{"requires_all": ["traffic_control"], "min_payload_kg": 0}],
         "resource_costs": {"dispatch_budget": 1}},
    ],
}

CITY_MEASUREMENT_POINTS = (
    {"id": "city_initial", "trigger": "initial_deployment",
     "required_true": [],
     "required_false": list(CITY_FACT_IDS), "varying_facts": []},
    {"id": "city_ground_only", "trigger": "capability_change",
     "required_true": [],
     "required_false": list(CITY_FACT_IDS), "varying_facts": []},
    {"id": "city_aerial_service_exit", "trigger": "member_exit",
     "required_true": [],
     "required_false": list(CITY_FACT_IDS), "varying_facts": []},
    # People already accounted: the guiding capability is no longer
    # necessary and its exit must not flip the remaining task to infeasible.
    {"id": "city_people_accounted", "trigger": "milestone_state_change",
     "required_true": ["incident_assessed", "people_accounted"],
     "required_false": ["traffic_protected", "access_preserved"],
     "varying_facts": []},
)

CITY_GROUND_UNIT = {"id": "city_carter_1",
                    "capabilities": ["assess", "ground_guide", "traffic_control"],
                    "payload_kg": 40}
CITY_AERIAL_UNIT = {"id": "city_iris_1",
                    "capabilities": ["assess", "aerial_service"],
                    "payload_kg": 2}
CITY_TRAFFIC_UNIT = {"id": "city_m20_1",
                     "capabilities": ["traffic_control"],
                     "payload_kg": 20}

CITY_DEV_CASES = {
    # case_id: (measurement_id, robots, resources)
    "full_team_initial": ("city_initial", [CITY_GROUND_UNIT, CITY_AERIAL_UNIT],
                          {"dispatch_budget": 6}),
    "aerial_exits_ground_remains": ("city_aerial_service_exit",
                                    [CITY_GROUND_UNIT], {"dispatch_budget": 6}),
    "ground_only_capability_change": ("city_ground_only", [CITY_GROUND_UNIT],
                                      {"dispatch_budget": 6}),
    # The aerial unit covers the guiding alternative, but traffic protection
    # has no aerial provider: the solver integrates every terminal predicate,
    # not only the alternative under test.
    "ground_exits_aerial_remains": ("city_aerial_service_exit",
                                    [CITY_AERIAL_UNIT], {"dispatch_budget": 6}),
    "guides_exit_after_people_accounted": ("city_people_accounted",
                                           [CITY_TRAFFIC_UNIT],
                                           {"dispatch_budget": 6}),
    "no_guide_capability_left": ("city_initial", [], {"dispatch_budget": 6}),
    "budget_exhausted": ("city_initial", [CITY_GROUND_UNIT, CITY_AERIAL_UNIT],
                         {"dispatch_budget": 2}),
}


def city_development_marker(*, evidence_class: str = "contract") -> dict[str, Any]:
    return build_development_marker(
        CITY_SCENE_KEY, asset_status="development_unverified",
        evidence_class=evidence_class,
        infrastructure_blockers=[CITY_NATIVE_BLOCKER])


def build_city_public_task() -> dict[str, Any]:
    return build_goal_task_package(CITY_SCENE_KEY)["public_task"]


def build_city_registration(
    *, validation_scope: str = CITY_DEVELOPMENT_SCOPE,
    evidence_refs: tuple[str, ...] = ("dev:city-model-review-v1",),
) -> dict[str, Any]:
    if validation_scope == CITY_NATIVE_SCOPE and not any(
            ref.startswith("native:") for ref in evidence_refs):
        raise ValueError("native scope requires native-prefixed evidence")
    return freeze_oracle_registration(
        deepcopy(CITY_MODEL),
        task_sha256=canonical_sha256(build_city_public_task()),
        measurement_points=deepcopy(list(CITY_MEASUREMENT_POINTS)),
        validation={"status": "verified", "scope": validation_scope,
                    "evidence_refs": list(evidence_refs)})


def build_city_state_contract(registration: Mapping[str, Any]) -> dict[str, Any]:
    return freeze_state_contract(
        build_city_public_task(), registration,
        predicate_bindings=deepcopy(CITY_PREDICATE_BINDINGS),
        outcome_predicates={
            "incident_assessed": ["incident_assessed"],
            "exposed_people_accounted_for": ["people_accounted"],
            "secondary_traffic_protected": ["traffic_protected"],
            "response_access_preserved": ["access_preserved"],
        },
        source_versions=dict(CITY_SOURCE_VERSIONS),
        entity_ids=list(CITY_ENTITY_IDS),
        asset_manifest_sha256="e" * 64, skill_contract_sha256="f" * 64)


def city_native_samples(measurement_id: str, facts: set[str]) -> dict[str, Any]:
    by_source: dict[str, dict[str, bool]] = {}
    for fact, binding in CITY_PREDICATE_BINDINGS.items():
        by_source.setdefault(binding["source_id"], {})[fact] = fact in facts
    return {"measurement_id": measurement_id, "sources": by_source}


def make_city_source_readers(
    native_probes: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, bool]]] | None = None,
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
            expected = {fact for fact, binding in CITY_PREDICATE_BINDINGS.items()
                        if binding["source_id"] == source_id}
            if set(values) != expected:
                return {}
            entities = sorted({entity for fact in expected
                               for entity in CITY_PREDICATE_BINDINGS[fact]["entity_ids"]})
            return {
                "source_id": source_id,
                "source_version": CITY_SOURCE_VERSIONS[source_id],
                "measurement_id": observation["measurement_id"],
                "state_sha256": canonical_sha256(state),
                "measured_at_s": state["time_s"],
                "entity_versions": {entity: state["entity_versions"][entity]
                                    for entity in entities},
                "predicates": {fact: values[fact] is True for fact in sorted(values)},
                "evidence_refs": [f"city:{source_id}:{observation['measurement_id']}"],
            }
        return measure
    return {source_id: reader(source_id) for source_id in CITY_SOURCE_VERSIONS}


def make_city_state_provider(measurement_id: str, facts: set[str], *,
                             robots: list[dict[str, Any]],
                             resources: Mapping[str, int], time_s: float = 5.0,
                             entity_versions: Mapping[str, int] | None = None):
    versions = dict(entity_versions or {entity: 1 for entity in CITY_ENTITY_IDS})
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
            "resource_versions": {"dispatch_budget": 1},
        }
        return {"measurement_id": measurement_id, "state": state,
                "native_samples": city_native_samples(measurement_id, facts)}

    return provide


def query_city_development_case(
    case_id: str, *, episode_id: str = "city-dev-episode",
    registration: Mapping[str, Any] | None = None,
    state_contract: Mapping[str, Any] | None = None,
    native_probes: Mapping[str, Callable] | None = None,
) -> dict[str, Any]:
    if case_id not in CITY_DEV_CASES:
        raise ValueError(f"unknown city development case: {case_id}")
    measurement_id, robots, resources = CITY_DEV_CASES[case_id]
    point = next(p for p in CITY_MEASUREMENT_POINTS if p["id"] == measurement_id)
    facts = set(point["required_true"])
    registration = registration or build_city_registration()
    contract = state_contract or build_city_state_contract(registration)
    provider = make_city_state_provider(
        measurement_id, facts, robots=deepcopy(robots), resources=resources)
    readers = make_city_source_readers(native_probes)
    snapshot = {"assessment_id": f"{case_id}-a1", "assessment_epoch": 0}
    observation = provider(snapshot)
    packets = {source: readers[source](snapshot, observation)
               for source in CITY_SOURCE_VERSIONS}
    grounding = ground_state(contract, registration,
                             measurement_id=measurement_id,
                             state=observation["state"], readbacks=packets)
    result = query_remaining_feasibility(
        registration, measurement_id=measurement_id, episode_id=episode_id,
        assessment_id=snapshot["assessment_id"], assessment_epoch=0,
        task_sha256=registration["task_sha256"],
        state=observation["state"], grounding=grounding)
    return stamp_evidence_record(result, city_development_marker())


__all__ = [
    "CITY_DEVELOPMENT_SCOPE", "CITY_ENTITY_IDS", "CITY_FACT_IDS",
    "CITY_MEASUREMENT_POINTS", "CITY_MODEL", "CITY_NATIVE_BLOCKER",
    "CITY_NATIVE_SCOPE", "CITY_PREDICATE_BINDINGS", "CITY_SCENE_KEY",
    "CITY_SOURCE_VERSIONS", "CITY_TERMINAL_ALL",
    "build_city_public_task", "build_city_registration",
    "build_city_state_contract", "city_development_marker",
    "city_native_samples", "make_city_source_readers",
    "make_city_state_provider", "query_city_development_case",
]
