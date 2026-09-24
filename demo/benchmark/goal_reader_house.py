"""Modern House remaining-task reader wiring (development track, synthetic backend).

This module wires the generic trusted-state/oracle contracts onto the
Modern House business family ``resident_home_recovery_and_access_restoration``.
It closes the STAGE29 inventory gaps for stable placement (chair footprint,
book-stack states), full-corridor clearance, member-exit footprints and
entity-version binding.

The backend here is deliberately synthetic: host-installed ``native_probes``
replace the synthetic samples per source, and their output is the only path
to a ``native_remaining_task`` validation scope.  Until then every query is
development evidence: binary labels exist only from the registered solver,
reader failures fail closed to ``oracle_status=failed`` /
``feasibility_label=null``, and release flags stay false.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .development_track_marker import (
    build_development_marker,
    stamp_evidence_record,
)
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

HOUSE_SCENE_KEY = "modern_house_nvd"
HOUSE_DEVELOPMENT_SCOPE = "synthetic_contract_development"
HOUSE_NATIVE_SCOPE = "native_remaining_task"
HOUSE_NATIVE_BLOCKER = "house_native_admission_pending"

HOUSE_FACT_IDS = (
    "chair_blocking", "chair_restored",
    "books_on_floor", "books_staged", "books_shelved",
    "access_restored", "team_withdrawn",
)
HOUSE_ENTITY_IDS = ("chair_1", "book_stack_1", "corridor_a", "member_exit_footprint_1")
HOUSE_SOURCE_VERSIONS = {
    "house_physx_objects": "physx-objects-dev-v1",
    "house_path_clearance": "path-clearance-dev-v1",
    "house_team_lifecycle": "team-lifecycle-dev-v1",
}
# Member-exit footprints are corridor entities for the clearance reader; the
# STAGE29 "成员退出占地" contrast is expressed through entity versions and
# clearance measurements, not a third task truth.
HOUSE_PREDICATE_BINDINGS = {
    "chair_blocking": {"source_id": "house_physx_objects",
                       "evidence_kind": "physical_completion",
                       "entity_ids": ["chair_1"]},
    "chair_restored": {"source_id": "house_physx_objects",
                       "evidence_kind": "physical_completion",
                       "entity_ids": ["chair_1"]},
    "books_on_floor": {"source_id": "house_physx_objects",
                       "evidence_kind": "physical_completion",
                       "entity_ids": ["book_stack_1"]},
    "books_staged": {"source_id": "house_physx_objects",
                     "evidence_kind": "physical_completion",
                     "entity_ids": ["book_stack_1"]},
    "books_shelved": {"source_id": "house_physx_objects",
                      "evidence_kind": "physical_completion",
                      "entity_ids": ["book_stack_1"]},
    "access_restored": {"source_id": "house_path_clearance",
                        "evidence_kind": "physical_completion",
                        "entity_ids": ["corridor_a", "member_exit_footprint_1"]},
    "team_withdrawn": {"source_id": "house_team_lifecycle",
                       "evidence_kind": "observation_record",
                       "entity_ids": ["member_exit_footprint_1"]},
}
HOUSE_TERMINAL_ALL = ("chair_restored", "books_shelved", "access_restored",
                      "team_withdrawn")

HOUSE_MODEL = {
    "fact_ids": list(HOUSE_FACT_IDS),
    "terminal_all": list(HOUSE_TERMINAL_ALL),
    "resource_bounds": {"time_budget": 8},
    "transitions": [
        {"id": "restore_chair", "requires_all": ["chair_blocking"],
         "requires_absent": [], "adds": ["chair_restored"],
         "deletes": ["chair_blocking"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 8}],
         "resource_costs": {"time_budget": 1}},
        {"id": "stage_books", "requires_all": ["books_on_floor"],
         "requires_absent": [], "adds": ["books_staged"],
         "deletes": ["books_on_floor"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 4}],
         "resource_costs": {"time_budget": 1}},
        {"id": "shelve_books_from_floor", "requires_all": ["books_on_floor"],
         "requires_absent": [], "adds": ["books_shelved"],
         "deletes": ["books_on_floor"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 4}],
         "resource_costs": {"time_budget": 1}},
        {"id": "shelve_books_from_stage", "requires_all": ["books_staged"],
         "requires_absent": [], "adds": ["books_shelved"],
         "deletes": ["books_staged"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 4}],
         "resource_costs": {"time_budget": 1}},
        # Full-corridor clearance is measured after the furniture predicate;
        # a ground unit must still traverse the corridor to certify clearance.
        {"id": "restore_access", "requires_all": ["chair_restored"],
         "requires_absent": [], "adds": ["access_restored"], "deletes": [],
         "capability_slots": [{"requires_all": ["navigate"], "min_payload_kg": 0}],
         "resource_costs": {"time_budget": 1}},
        {"id": "withdraw_team",
         "requires_all": ["chair_restored", "books_shelved", "access_restored"],
         "requires_absent": [], "adds": ["team_withdrawn"], "deletes": [],
         "capability_slots": [], "resource_costs": {"time_budget": 1}},
    ],
}

HOUSE_MEASUREMENT_POINTS = (
    {"id": "house_initial", "trigger": "initial_deployment",
     "required_true": ["chair_blocking", "books_on_floor"],
     "required_false": ["chair_restored", "books_staged", "books_shelved",
                        "access_restored", "team_withdrawn"],
     "varying_facts": []},
    {"id": "house_books_intermediate", "trigger": "milestone_state_change",
     "required_true": ["chair_blocking", "books_staged"],
     "required_false": ["chair_restored", "books_on_floor", "books_shelved",
                        "access_restored", "team_withdrawn"],
     "varying_facts": []},
    {"id": "house_furniture_restored", "trigger": "milestone_state_change",
     "required_true": ["chair_restored", "books_on_floor"],
     "required_false": ["chair_blocking", "books_staged", "books_shelved",
                        "access_restored", "team_withdrawn"],
     "varying_facts": []},
    # Belongings done and furniture restored: the remaining predicates need
    # no carry capability, so a carrier exit here must stay deployable.
    {"id": "house_belongings_done", "trigger": "member_exit",
     "required_true": ["chair_restored", "books_shelved"],
     "required_false": ["chair_blocking", "books_on_floor", "books_staged",
                        "access_restored", "team_withdrawn"],
     "varying_facts": []},
)

HOUSE_CARRIER = {"id": "house_carter_1", "capabilities": ["carry"],
                 "payload_kg": 40}
HOUSE_NAVIGATOR = {"id": "house_go2_1", "capabilities": ["navigate"],
                   "payload_kg": 5}

HOUSE_DEV_CASES = {
    # case_id: (measurement_id, robots, resources)
    "full_team_initial": ("house_initial", [HOUSE_CARRIER, HOUSE_NAVIGATOR],
                          {"time_budget": 8}),
    "carrier_exits_early": ("house_initial", [HOUSE_NAVIGATOR],
                            {"time_budget": 8}),
    "books_intermediate": ("house_books_intermediate",
                           [HOUSE_CARRIER, HOUSE_NAVIGATOR], {"time_budget": 8}),
    "carrier_exits_after_belongings_done": ("house_belongings_done",
                                            [HOUSE_NAVIGATOR],
                                            {"time_budget": 8}),
    "navigator_exits_before_access_restored": ("house_belongings_done",
                                               [HOUSE_CARRIER],
                                               {"time_budget": 8}),
    "budget_exhausted": ("house_initial", [HOUSE_CARRIER, HOUSE_NAVIGATOR],
                         {"time_budget": 2}),
}


def house_development_marker(*, evidence_class: str = "contract") -> dict[str, Any]:
    return build_development_marker(
        HOUSE_SCENE_KEY, asset_status="development_unverified",
        evidence_class=evidence_class,
        infrastructure_blockers=[HOUSE_NATIVE_BLOCKER])


def build_house_public_task() -> dict[str, Any]:
    return build_goal_task_package(HOUSE_SCENE_KEY)["public_task"]


def build_house_registration(
    *, validation_scope: str = HOUSE_DEVELOPMENT_SCOPE,
    evidence_refs: tuple[str, ...] = ("dev:house-model-review-v1",),
) -> dict[str, Any]:
    """Freeze the house model; native scope needs native evidence refs."""
    if validation_scope == HOUSE_NATIVE_SCOPE and not any(
            ref.startswith("native:") for ref in evidence_refs):
        raise ValueError("native scope requires native-prefixed evidence")
    return freeze_oracle_registration(
        deepcopy(HOUSE_MODEL),
        task_sha256=canonical_sha256(build_house_public_task()),
        measurement_points=deepcopy(list(HOUSE_MEASUREMENT_POINTS)),
        validation={"status": "verified", "scope": validation_scope,
                    "evidence_refs": list(evidence_refs)})


def build_house_state_contract(registration: Mapping[str, Any]) -> dict[str, Any]:
    public_task = build_house_public_task()
    return freeze_state_contract(
        public_task, registration,
        predicate_bindings=deepcopy(HOUSE_PREDICATE_BINDINGS),
        outcome_predicates={
            "furniture_repositioned": ["chair_restored"],
            "belonging_staged": ["books_shelved"],
            "resident_path_restored": ["access_restored"],
            "team_withdrawn": ["team_withdrawn"],
        },
        source_versions=dict(HOUSE_SOURCE_VERSIONS),
        entity_ids=list(HOUSE_ENTITY_IDS),
        asset_manifest_sha256="c" * 64, skill_contract_sha256="d" * 64)


def house_native_samples(measurement_id: str, facts: set[str]) -> dict[str, dict[str, bool]]:
    """Independent synthetic samples per source; never derived from state."""
    by_source: dict[str, dict[str, bool]] = {}
    for fact, binding in HOUSE_PREDICATE_BINDINGS.items():
        by_source.setdefault(binding["source_id"], {})[fact] = fact in facts
    return {"measurement_id": measurement_id, "sources": by_source}


def build_house_development_certificate(registration: Mapping[str, Any],
                                         *, independent_review: str = "pending"):
    """Development completeness certificate; review stays pending in dev."""
    return build_alternative_path_certificate(
        HOUSE_SCENE_KEY, registration,
        path_classes={
            "direct_shelve_vs_staged_alternatives": {
                "description": "书堆从地面直接到位或经稳定中间位两段完成的合法替代",
                "covered_transitions": ["shelve_books_from_floor",
                                        "stage_books",
                                        "shelve_books_from_stage"]},
            "carrier_capability_necessity_boundary": {
                "description": "搬运能力仅在家具/书堆谓词未满足时必要；完成后退出不改变可行性",
                "covered_transitions": ["restore_chair", "restore_access",
                                        "withdraw_team"]},
        },
        covered_measurement_ids=[point["id"] for point in
                                 HOUSE_MEASUREMENT_POINTS],
        uncovered_path_policy="treated_as_uncovered",
        independent_review=independent_review,
        evidence_refs=["dev:house-path-review-v1"])


def make_house_source_readers(
    native_probes: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, bool]]] | None = None,
):
    """Build trusted source readers; probes are host-installed, never participant data."""
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
            expected = {fact for fact, binding in HOUSE_PREDICATE_BINDINGS.items()
                        if binding["source_id"] == source_id}
            if set(values) != expected:
                return {}
            entities = sorted({entity for fact in expected
                               for entity in HOUSE_PREDICATE_BINDINGS[fact]["entity_ids"]})
            return {
                "source_id": source_id,
                "source_version": HOUSE_SOURCE_VERSIONS[source_id],
                "measurement_id": observation["measurement_id"],
                "state_sha256": canonical_sha256(state),
                "measured_at_s": state["time_s"],
                "entity_versions": {entity: state["entity_versions"][entity]
                                    for entity in entities},
                "predicates": {fact: values[fact] is True for fact in sorted(values)},
                "evidence_refs": [f"house:{source_id}:{observation['measurement_id']}"],
            }
        return measure
    return {source_id: reader(source_id) for source_id in HOUSE_SOURCE_VERSIONS}


def make_house_state_provider(measurement_id: str, facts: set[str], *,
                              robots: list[dict[str, Any]],
                              resources: Mapping[str, int], time_s: float = 3.0,
                              entity_versions: Mapping[str, int] | None = None):
    """State provider bound to one preregistered point; synthetic by default."""
    versions = dict(entity_versions or {entity: 1 for entity in HOUSE_ENTITY_IDS})
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
            "resource_versions": {"time_budget": 1},
        }
        return {"measurement_id": measurement_id, "state": state,
                "native_samples": house_native_samples(measurement_id, facts)}

    return provide


def query_house_development_case(
    case_id: str, *, episode_id: str = "house-dev-episode",
    registration: Mapping[str, Any] | None = None,
    state_contract: Mapping[str, Any] | None = None,
    certificate: Mapping[str, Any] | None = None,
    native_probes: Mapping[str, Callable] | None = None,
) -> dict[str, Any]:
    """Run one development case end-to-end and stamp it as non-formal evidence.

    ``not_deployable`` answers pass the alternative-path certificate gate,
    matching the warehouse/factory readers: without a verified certificate
    the label downgrades to a platform failure with a null label.
    """
    if case_id not in HOUSE_DEV_CASES:
        raise ValueError(f"unknown house development case: {case_id}")
    measurement_id, robots, resources = HOUSE_DEV_CASES[case_id]
    point = next(p for p in HOUSE_MEASUREMENT_POINTS if p["id"] == measurement_id)
    facts = set(point["required_true"])
    registration = registration or build_house_registration()
    contract = state_contract or build_house_state_contract(registration)
    certificate = certificate or build_house_development_certificate(registration)
    provider = make_house_state_provider(
        measurement_id, facts, robots=deepcopy(robots), resources=resources)
    readers = make_house_source_readers(native_probes)
    snapshot = {"assessment_id": f"{case_id}-a1", "assessment_epoch": 0}
    observation = provider(snapshot)
    packets = {source: readers[source](snapshot, observation)
               for source in HOUSE_SOURCE_VERSIONS}
    grounding = ground_state(contract, registration,
                             measurement_id=measurement_id,
                             state=observation["state"], readbacks=packets)
    result = query_remaining_feasibility(
        registration, measurement_id=measurement_id, episode_id=episode_id,
        assessment_id=snapshot["assessment_id"], assessment_epoch=0,
        task_sha256=registration["task_sha256"],
        state=observation["state"], grounding=grounding)
    gated = gate_not_deployable_result(result, certificate, registration)
    return stamp_evidence_record(gated, house_development_marker())


__all__ = [
    "HOUSE_DEVELOPMENT_SCOPE", "HOUSE_ENTITY_IDS", "HOUSE_FACT_IDS",
    "HOUSE_MEASUREMENT_POINTS", "HOUSE_MODEL", "HOUSE_NATIVE_BLOCKER",
    "HOUSE_NATIVE_SCOPE", "HOUSE_PREDICATE_BINDINGS", "HOUSE_SCENE_KEY",
    "HOUSE_SOURCE_VERSIONS", "HOUSE_TERMINAL_ALL",
    "build_house_development_certificate", "build_house_public_task",
    "build_house_registration", "build_house_state_contract",
    "house_development_marker", "house_native_samples",
    "make_house_source_readers", "make_house_state_provider",
    "query_house_development_case",
]
