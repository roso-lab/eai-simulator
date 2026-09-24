"""Warehouse remaining-task reader wiring (development track, synthetic backend).

Wires the trusted-state/oracle contracts onto the Warehouse business family
``order_fulfilment_inventory_and_dock_closeout``.  Closes the STAGE29 gaps
for both order-object identities, stable dock placement, dock receipts, the
"arrived at dock but not yet handed over" intermediate state, closeout and
corridor-closure contrasts, plus the single-robot substitute vs dual-robot
load-combination alternative paths.

TRUSTED_STATE_CONTRACT semantics under test: when a crate is already stably
at the dock and only the receipt remains, the grabber's exit cannot flip the
state to not_deployable — the carrying predicate is already satisfied and
only a receipt-bearing capability is still necessary.
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

WAREHOUSE_SCENE_KEY = "warehouse_nvd"
WAREHOUSE_DEVELOPMENT_SCOPE = "synthetic_contract_development"
WAREHOUSE_NATIVE_BLOCKER = "warehouse_native_admission_pending"

WAREHOUSE_FACT_IDS = (
    "wood_crate_stored", "carton_stored",
    "wood_crate_at_dock", "carton_at_dock",
    "wood_crate_receipted", "carton_receipted",
    "corridor_open", "closeout_done",
)
WAREHOUSE_ENTITY_IDS = ("wood_crate_1", "carton_1", "dock_1", "corridor_b")
WAREHOUSE_SOURCE_VERSIONS = {
    "warehouse_physx_objects": "physx-objects-dev-v1",
    "warehouse_dock_receipts": "dock-receipts-dev-v1",
    "warehouse_corridor_service": "corridor-service-dev-v1",
}
WAREHOUSE_PREDICATE_BINDINGS = {
    "wood_crate_stored": {"source_id": "warehouse_physx_objects",
                          "evidence_kind": "physical_completion",
                          "entity_ids": ["wood_crate_1"]},
    "carton_stored": {"source_id": "warehouse_physx_objects",
                      "evidence_kind": "physical_completion",
                      "entity_ids": ["carton_1"]},
    "wood_crate_at_dock": {"source_id": "warehouse_physx_objects",
                           "evidence_kind": "physical_completion",
                           "entity_ids": ["wood_crate_1", "dock_1"]},
    "carton_at_dock": {"source_id": "warehouse_physx_objects",
                       "evidence_kind": "physical_completion",
                       "entity_ids": ["carton_1", "dock_1"]},
    "wood_crate_receipted": {"source_id": "warehouse_dock_receipts",
                             "evidence_kind": "business_receipt",
                             "entity_ids": ["wood_crate_1", "dock_1"]},
    "carton_receipted": {"source_id": "warehouse_dock_receipts",
                         "evidence_kind": "business_receipt",
                         "entity_ids": ["carton_1", "dock_1"]},
    "corridor_open": {"source_id": "warehouse_corridor_service",
                      "evidence_kind": "physical_completion",
                      "entity_ids": ["corridor_b"]},
    "closeout_done": {"source_id": "warehouse_dock_receipts",
                      "evidence_kind": "business_receipt",
                      "entity_ids": ["dock_1"]},
}
WAREHOUSE_TERMINAL_ALL = ("wood_crate_receipted", "carton_receipted",
                          "corridor_open", "closeout_done")

WAREHOUSE_MODEL = {
    "fact_ids": list(WAREHOUSE_FACT_IDS),
    "terminal_all": list(WAREHOUSE_TERMINAL_ALL),
    "resource_bounds": {"shift_time_budget": 8, "dollies": 3},
    "transitions": [
        # Alternative path A: one strong single robot carries both objects.
        {"id": "single_robot_carry_wood", "requires_all": ["wood_crate_stored"],
         "requires_absent": [], "adds": ["wood_crate_at_dock"],
         "deletes": ["wood_crate_stored"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 30}],
         "resource_costs": {"shift_time_budget": 1, "dollies": 1}},
        {"id": "single_robot_carry_carton", "requires_all": ["carton_stored"],
         "requires_absent": [], "adds": ["carton_at_dock"],
         "deletes": ["carton_stored"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 30}],
         "resource_costs": {"shift_time_budget": 1, "dollies": 1}},
        # Alternative path B: two lighter robots jointly bear the heavy crate.
        {"id": "dual_robot_carry_wood", "requires_all": ["wood_crate_stored"],
         "requires_absent": [], "adds": ["wood_crate_at_dock"],
         "deletes": ["wood_crate_stored"],
         "capability_slots": [
             {"requires_all": ["carry"], "min_payload_kg": 12},
             {"requires_all": ["carry"], "min_payload_kg": 12}],
         "resource_costs": {"shift_time_budget": 1, "dollies": 2}},
        {"id": "light_carry_carton", "requires_all": ["carton_stored"],
         "requires_absent": [], "adds": ["carton_at_dock"],
         "deletes": ["carton_stored"],
         "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 6}],
         "resource_costs": {"shift_time_budget": 1, "dollies": 1}},
        {"id": "receive_wood_receipt",
         "requires_all": ["wood_crate_at_dock"], "requires_absent": [],
         "adds": ["wood_crate_receipted"], "deletes": [],
         "capability_slots": [{"requires_all": ["dock_handoff"], "min_payload_kg": 0}],
         "resource_costs": {"shift_time_budget": 1}},
        {"id": "receive_carton_receipt",
         "requires_all": ["carton_at_dock"], "requires_absent": [],
         "adds": ["carton_receipted"], "deletes": [],
         "capability_slots": [{"requires_all": ["dock_handoff"], "min_payload_kg": 0}],
         "resource_costs": {"shift_time_budget": 1}},
        {"id": "reopen_corridor", "requires_all": [], "requires_absent": [],
         "adds": ["corridor_open"], "deletes": [],
         "capability_slots": [{"requires_all": ["navigate"], "min_payload_kg": 0}],
         "resource_costs": {"shift_time_budget": 1}},
        {"id": "closeout_shift",
         "requires_all": ["wood_crate_receipted", "carton_receipted",
                          "corridor_open"],
         "requires_absent": [], "adds": ["closeout_done"], "deletes": [],
         "capability_slots": [{"requires_all": ["dock_handoff"], "min_payload_kg": 0}],
         "resource_costs": {"shift_time_budget": 1}},
    ],
}

WAREHOUSE_MEASUREMENT_POINTS = (
    {"id": "warehouse_initial", "trigger": "initial_deployment",
     "required_true": ["wood_crate_stored", "carton_stored"],
     "required_false": ["wood_crate_at_dock", "carton_at_dock",
                        "wood_crate_receipted", "carton_receipted",
                        "corridor_open", "closeout_done"],
     "varying_facts": []},
    # The STAGE29 intermediate state: carrying predicate satisfied, receipt
    # predicate still outstanding for the wood crate.
    {"id": "warehouse_at_dock_before_receipt", "trigger": "milestone_state_change",
     "required_true": ["wood_crate_at_dock", "carton_receipted", "corridor_open"],
     "required_false": ["wood_crate_stored", "carton_stored", "carton_at_dock",
                        "wood_crate_receipted", "closeout_done"],
     "varying_facts": []},
    {"id": "warehouse_carton_only_remaining", "trigger": "capability_change",
     "required_true": ["wood_crate_receipted", "corridor_open"],
     "required_false": ["wood_crate_stored", "carton_stored", "wood_crate_at_dock",
                        "carton_at_dock", "carton_receipted", "closeout_done"],
     "varying_facts": []},
)

WAREHOUSE_STRONG_UNIT = {"id": "warehouse_m20_1",
                         "capabilities": ["carry", "dock_handoff", "navigate"],
                         "payload_kg": 40}
WAREHOUSE_LIGHT_UNITS = [
    {"id": "warehouse_carter_1", "capabilities": ["carry"],
     "payload_kg": 15},
    {"id": "warehouse_carter_2", "capabilities": ["carry", "navigate"],
     "payload_kg": 15},
]
WAREHOUSE_DOCK_UNIT = {"id": "warehouse_go2_1",
                       "capabilities": ["navigate", "dock_handoff"],
                       "payload_kg": 5}

WAREHOUSE_DEV_CASES = {
    # case_id: (measurement_id, robots, resources)
    "strong_single_robot_path": ("warehouse_initial", [WAREHOUSE_STRONG_UNIT],
                                 {"shift_time_budget": 8, "dollies": 2}),
    # Two light robots jointly bear the heavy crate (2 dollies) and the
    # carton follows on the last dolly; the dock unit issues the receipts.
    "dual_light_robot_path": ("warehouse_initial",
                              list(WAREHOUSE_LIGHT_UNITS) + [WAREHOUSE_DOCK_UNIT],
                              {"shift_time_budget": 8, "dollies": 3}),
    "insufficient_dollies": ("warehouse_initial", [WAREHOUSE_STRONG_UNIT],
                             {"shift_time_budget": 8, "dollies": 0}),
    "no_carry_capability_left": ("warehouse_initial", [WAREHOUSE_DOCK_UNIT],
                                 {"shift_time_budget": 8, "dollies": 2}),
    "grabber_exits_after_crate_at_dock": (
        "warehouse_at_dock_before_receipt", [WAREHOUSE_DOCK_UNIT],
        {"shift_time_budget": 8, "dollies": 2}),
    "carrier_exits_before_crate_moves": (
        "warehouse_initial", [WAREHOUSE_DOCK_UNIT],
        {"shift_time_budget": 8, "dollies": 2}),
    "light_only_cannot_take_wood_path": (
        "warehouse_carton_only_remaining", [WAREHOUSE_LIGHT_UNITS[1]],
        {"shift_time_budget": 8, "dollies": 2}),
}


def warehouse_development_marker(*, evidence_class: str = "contract") -> dict[str, Any]:
    return build_development_marker(
        WAREHOUSE_SCENE_KEY, asset_status="development_unverified",
        evidence_class=evidence_class,
        infrastructure_blockers=[WAREHOUSE_NATIVE_BLOCKER])


def build_warehouse_public_task() -> dict[str, Any]:
    return build_goal_task_package(WAREHOUSE_SCENE_KEY)["public_task"]


def build_warehouse_registration(
    *, validation_scope: str = WAREHOUSE_DEVELOPMENT_SCOPE,
    evidence_refs: tuple[str, ...] = ("dev:warehouse-model-review-v1",),
) -> dict[str, Any]:
    return freeze_oracle_registration(
        deepcopy(WAREHOUSE_MODEL),
        task_sha256=canonical_sha256(build_warehouse_public_task()),
        measurement_points=deepcopy(list(WAREHOUSE_MEASUREMENT_POINTS)),
        validation={"status": "verified", "scope": validation_scope,
                    "evidence_refs": list(evidence_refs)})


def build_warehouse_state_contract(registration: Mapping[str, Any]) -> dict[str, Any]:
    return freeze_state_contract(
        build_warehouse_public_task(), registration,
        predicate_bindings=deepcopy(WAREHOUSE_PREDICATE_BINDINGS),
        outcome_predicates={
            "order_inventory_reconciled": ["wood_crate_receipted",
                                           "carton_receipted"],
            "order_consolidated": ["corridor_open"],
            "dock_handoff_confirmed": ["closeout_done"],
            "warehouse_closeout": ["closeout_done"],
        },
        source_versions=dict(WAREHOUSE_SOURCE_VERSIONS),
        entity_ids=list(WAREHOUSE_ENTITY_IDS),
        asset_manifest_sha256="1" * 64, skill_contract_sha256="2" * 64)


def build_warehouse_development_certificate(registration: Mapping[str, Any]):
    """Development certificate: structurally valid, review pending."""
    return build_alternative_path_certificate(
        WAREHOUSE_SCENE_KEY, registration,
        path_classes={
            "single_robot_substitute": {
                "description": "一台强载荷单机顺序完成两件订单对象的搬运",
                "covered_transitions": ["single_robot_carry_wood",
                                        "single_robot_carry_carton",
                                        "light_carry_carton"]},
            "dual_robot_load_combination": {
                "description": "两台轻载荷机器人共同承载重型木箱的合法组合",
                "covered_transitions": ["dual_robot_carry_wood"]},
            "resource_exhaustion_contrast": {
                "description": "剩余资源不足时所有搬运转移同时不可用",
                "covered_transitions": ["receive_wood_receipt",
                                        "receive_carton_receipt"]},
        },
        covered_measurement_ids=[point["id"] for point in
                                 WAREHOUSE_MEASUREMENT_POINTS],
        uncovered_path_policy="treated_as_uncovered",
        independent_review="pending",
        evidence_refs=["dev:warehouse-path-review-v1"])


def make_warehouse_source_readers(
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
            expected = {fact for fact, binding in WAREHOUSE_PREDICATE_BINDINGS.items()
                        if binding["source_id"] == source_id}
            if set(values) != expected:
                return {}
            entities = sorted({entity for fact in expected
                               for entity in WAREHOUSE_PREDICATE_BINDINGS[fact]["entity_ids"]})
            return {
                "source_id": source_id,
                "source_version": WAREHOUSE_SOURCE_VERSIONS[source_id],
                "measurement_id": observation["measurement_id"],
                "state_sha256": canonical_sha256(state),
                "measured_at_s": state["time_s"],
                "entity_versions": {entity: state["entity_versions"][entity]
                                    for entity in entities},
                "predicates": {fact: values[fact] is True for fact in sorted(values)},
                "evidence_refs": [f"warehouse:{source_id}:{observation['measurement_id']}"],
            }
        return measure
    return {source_id: reader(source_id) for source_id in WAREHOUSE_SOURCE_VERSIONS}


def make_warehouse_state_provider(measurement_id: str, facts: set[str], *,
                                  robots: list[dict[str, Any]],
                                  resources: Mapping[str, int], time_s: float = 4.0):
    versions = {entity: 1 for entity in WAREHOUSE_ENTITY_IDS}
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
            "resource_versions": {"shift_time_budget": 1, "dollies": 1},
        }
        by_source: dict[str, dict[str, bool]] = {}
        for fact, binding in WAREHOUSE_PREDICATE_BINDINGS.items():
            by_source.setdefault(binding["source_id"], {})[fact] = fact in facts
        return {"measurement_id": measurement_id, "state": state,
                "native_samples": {"measurement_id": measurement_id,
                                   "sources": by_source}}

    return provide


def query_warehouse_development_case(
    case_id: str, *, episode_id: str = "warehouse-dev-episode",
    registration: Mapping[str, Any] | None = None,
    state_contract: Mapping[str, Any] | None = None,
    certificate: Mapping[str, Any] | None = None,
    native_probes: Mapping[str, Callable] | None = None,
) -> dict[str, Any]:
    """Run one development case; not_deployable answers pass the certificate gate."""
    if case_id not in WAREHOUSE_DEV_CASES:
        raise ValueError(f"unknown warehouse development case: {case_id}")
    measurement_id, robots, resources = WAREHOUSE_DEV_CASES[case_id]
    point = next(p for p in WAREHOUSE_MEASUREMENT_POINTS
                 if p["id"] == measurement_id)
    facts = set(point["required_true"])
    registration = registration or build_warehouse_registration()
    contract = state_contract or build_warehouse_state_contract(registration)
    certificate = certificate or build_warehouse_development_certificate(registration)
    provider = make_warehouse_state_provider(
        measurement_id, facts, robots=deepcopy(robots), resources=resources)
    readers = make_warehouse_source_readers(native_probes)
    snapshot = {"assessment_id": f"{case_id}-a1", "assessment_epoch": 0}
    observation = provider(snapshot)
    packets = {source: readers[source](snapshot, observation)
               for source in WAREHOUSE_SOURCE_VERSIONS}
    grounding = ground_state(contract, registration,
                             measurement_id=measurement_id,
                             state=observation["state"], readbacks=packets)
    result = query_remaining_feasibility(
        registration, measurement_id=measurement_id, episode_id=episode_id,
        assessment_id=snapshot["assessment_id"], assessment_epoch=0,
        task_sha256=registration["task_sha256"],
        state=observation["state"], grounding=grounding)
    gated = gate_not_deployable_result(result, certificate, registration)
    return stamp_evidence_record(gated, warehouse_development_marker())


__all__ = [
    "WAREHOUSE_DEVELOPMENT_SCOPE", "WAREHOUSE_ENTITY_IDS", "WAREHOUSE_FACT_IDS",
    "WAREHOUSE_MEASUREMENT_POINTS", "WAREHOUSE_MODEL", "WAREHOUSE_NATIVE_BLOCKER",
    "WAREHOUSE_PREDICATE_BINDINGS", "WAREHOUSE_SCENE_KEY",
    "WAREHOUSE_SOURCE_VERSIONS", "WAREHOUSE_TERMINAL_ALL",
    "build_warehouse_development_certificate", "build_warehouse_public_task",
    "build_warehouse_registration", "build_warehouse_state_contract",
    "make_warehouse_source_readers", "make_warehouse_state_provider",
    "query_warehouse_development_case", "warehouse_development_marker",
]
