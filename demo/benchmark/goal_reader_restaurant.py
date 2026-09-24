"""Restaurant remaining-task reader wiring (development track, synthetic backend).

Wires the trusted-state/oracle contracts onto the Restaurant business family
``RESTAURANT-GOAL-01`` (opening-readiness inspection of both service zones).
Closes the STAGE32 admission-audit reader gaps: per-zone arrival-and-support
facts with an independent current observation, the readiness receipt as a
business receipt bound to both observations, the not-yet-inspected/inspected
and relay-pending/relay-done contrasts, and both legal alternative path
classes -- one robot serially inspecting both zones versus two robots
splitting them in parallel, with either one relaying the unified receipt.

The backend is deliberately synthetic: host-installed ``native_probes``
replace the synthetic samples per source, and their output is the only path
to a ``native_remaining_task`` validation scope.  Until then every query is
development evidence under the ``restaurant_native_candidate_evidence_pending``
blocker: binary labels exist only from the registered solver, reader failures
fail closed to ``oracle_status=failed`` / ``feasibility_label=null``, and
release flags stay false.

Model notes (STAGE32 audit + oracle acceptance matrix, restaurant row):

- Capability vocabulary mirrors the public pool: the ground units carry
  ``ground_navigation``+``observe``+``relay``; Lite3 is the legal decoy with
  no ``relay``; Iris carries ``aerial_navigation`` and can relay from its
  current region but cannot ground-arrive at a service zone.  Only the relay
  transition requires ``relay``; navigation/observation transitions never do.
- ``service_time_budget`` counts 30 s units of the 240 s opening-readiness
  simulation budget (bound 8).  A solo navigation leg costs 60 s (45 s
  window + margin), a zone observation 30 s, the relay receipt 30 s.  The
  serial substitute therefore costs 7 units while the two-robot parallel
  joint transition costs its wall-clock maximum (3 units), so a team whose
  remaining budget cannot fund the serial substitute may still be deployable
  through the parallel one.
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

RESTAURANT_SCENE_KEY = "restaurant"
RESTAURANT_DEVELOPMENT_SCOPE = "synthetic_contract_development"
RESTAURANT_NATIVE_SCOPE = "native_remaining_task"
RESTAURANT_NATIVE_BLOCKER = "restaurant_native_candidate_evidence_pending"

RESTAURANT_FACT_IDS = (
    "kitchen_arrived", "kitchen_observed",
    "dining_arrived", "dining_observed",
    "readiness_receipt_committed",
)
RESTAURANT_ENTITY_IDS = ("kitchen_service_zone_1", "dining_service_zone_1",
                         "readiness_backend_1", "event_bus_channel_1")
RESTAURANT_SOURCE_VERSIONS = {
    "restaurant_zone_arrival": "zone-arrival-dev-v1",
    "restaurant_service_observations": "service-observations-dev-v1",
    "restaurant_readiness_backend": "readiness-backend-dev-v1",
}
RESTAURANT_PREDICATE_BINDINGS = {
    # Actual ground arrival plus the native ground-support check per zone.
    "kitchen_arrived": {"source_id": "restaurant_zone_arrival",
                        "evidence_kind": "physical_completion",
                        "entity_ids": ["kitchen_service_zone_1"]},
    "dining_arrived": {"source_id": "restaurant_zone_arrival",
                       "evidence_kind": "physical_completion",
                       "entity_ids": ["dining_service_zone_1"]},
    # The current observation digest, bound to its service zone.
    "kitchen_observed": {"source_id": "restaurant_service_observations",
                         "evidence_kind": "observation_record",
                         "entity_ids": ["kitchen_service_zone_1"]},
    "dining_observed": {"source_id": "restaurant_service_observations",
                        "evidence_kind": "observation_record",
                        "entity_ids": ["dining_service_zone_1"]},
    # The readiness receipt merges both observations; the relay publication
    # travels the event-bus channel and is accepted by the backend.
    "readiness_receipt_committed": {"source_id": "restaurant_readiness_backend",
                                    "evidence_kind": "business_receipt",
                                    "entity_ids": ["readiness_backend_1",
                                                   "event_bus_channel_1"]},
}
RESTAURANT_TERMINAL_ALL = tuple(RESTAURANT_FACT_IDS)

RESTAURANT_MODEL = {
    "fact_ids": list(RESTAURANT_FACT_IDS),
    "terminal_all": list(RESTAURANT_TERMINAL_ALL),
    "resource_bounds": {"service_time_budget": 8},
    "transitions": [
        # Serial substitute: one ground unit visits the zones one by one.
        {"id": "solo_arrive_kitchen", "requires_all": [],
         "requires_absent": [], "adds": ["kitchen_arrived"], "deletes": [],
         "capability_slots": [{"requires_all": ["ground_navigation"],
                               "min_payload_kg": 0}],
         "resource_costs": {"service_time_budget": 2}},
        {"id": "solo_arrive_dining", "requires_all": [],
         "requires_absent": [], "adds": ["dining_arrived"], "deletes": [],
         "capability_slots": [{"requires_all": ["ground_navigation"],
                               "min_payload_kg": 0}],
         "resource_costs": {"service_time_budget": 2}},
        {"id": "observe_kitchen_zone", "requires_all": ["kitchen_arrived"],
         "requires_absent": [], "adds": ["kitchen_observed"], "deletes": [],
         "capability_slots": [{"requires_all": ["observe"], "min_payload_kg": 0}],
         "resource_costs": {"service_time_budget": 1}},
        {"id": "observe_dining_zone", "requires_all": ["dining_arrived"],
         "requires_absent": [], "adds": ["dining_observed"], "deletes": [],
         "capability_slots": [{"requires_all": ["observe"], "min_payload_kg": 0}],
         "resource_costs": {"service_time_budget": 1}},
        # Parallel substitute: two members split the zones concurrently; the
        # joint transition costs the wall-clock maximum of the concurrent
        # legs, not their sum.
        {"id": "parallel_inspect_service_zones", "requires_all": [],
         "requires_absent": [], "adds": ["kitchen_arrived", "kitchen_observed",
                                         "dining_arrived", "dining_observed"],
         "deletes": [],
         "capability_slots": [
             {"requires_all": ["ground_navigation", "observe"],
              "min_payload_kg": 0},
             {"requires_all": ["ground_navigation", "observe"],
              "min_payload_kg": 0}],
         "resource_costs": {"service_time_budget": 3}},
        # The receipt is a relay action from the current region: it requires
        # both observation digests and relay capability, never navigation.
        {"id": "commit_readiness_receipt",
         "requires_all": ["kitchen_observed", "dining_observed"],
         "requires_absent": [], "adds": ["readiness_receipt_committed"],
         "deletes": [],
         "capability_slots": [{"requires_all": ["relay"], "min_payload_kg": 0}],
         "resource_costs": {"service_time_budget": 1}},
    ],
}

RESTAURANT_MEASUREMENT_POINTS = (
    # Both zones not yet inspected; relay still required.
    {"id": "restaurant_initial", "trigger": "initial_deployment",
     "required_true": [],
     "required_false": list(RESTAURANT_FACT_IDS), "varying_facts": []},
    # Kitchen inspected, dining still pending: the per-zone contrast state.
    {"id": "restaurant_kitchen_inspected", "trigger": "milestone_state_change",
     "required_true": ["kitchen_arrived", "kitchen_observed"],
     "required_false": ["dining_arrived", "dining_observed",
                        "readiness_receipt_committed"],
     "varying_facts": []},
    # Both zones inspected, only the relay receipt outstanding: relay still
    # required, and this is where a navigation-only member exit is assessed.
    {"id": "restaurant_both_zones_inspected", "trigger": "member_exit",
     "required_true": ["kitchen_arrived", "kitchen_observed",
                       "dining_arrived", "dining_observed"],
     "required_false": ["readiness_receipt_committed"],
     "varying_facts": []},
    # Relay done: every terminal predicate already true, so no capability
    # (relay included) is still necessary.
    {"id": "restaurant_receipt_committed", "trigger": "milestone_state_change",
     "required_true": list(RESTAURANT_FACT_IDS),
     "required_false": [], "varying_facts": []},
)

RESTAURANT_CARTER_1 = {"id": "restaurant_carter_1",
                       "capabilities": ["ground_navigation", "observe",
                                        "relay"],
                       "payload_kg": 40}
# Lite3 is the legal navigation/observation decoy with no relay capability.
RESTAURANT_LITE3_1 = {"id": "restaurant_lite3_1",
                      "capabilities": ["ground_navigation", "observe"],
                      "payload_kg": 5}
# Iris hovers at the overview anchor: it can relay from its current region
# but cannot ground-arrive at a service zone.
RESTAURANT_IRIS_1 = {"id": "restaurant_iris_1",
                     "capabilities": ["aerial_navigation", "observe", "relay"],
                     "payload_kg": 2}

RESTAURANT_DEV_CASES = {
    # case_id: (measurement_id, robots, resources)
    "solo_serial_path": ("restaurant_initial", [RESTAURANT_CARTER_1],
                         {"service_time_budget": 8}),
    "parallel_dual_unit_path": ("restaurant_initial",
                                [RESTAURANT_CARTER_1, RESTAURANT_LITE3_1],
                                {"service_time_budget": 8}),
    "kitchen_inspected_dining_pending": (
        "restaurant_kitchen_inspected",
        [RESTAURANT_CARTER_1, RESTAURANT_LITE3_1], {"service_time_budget": 8}),
    # Receipt committed: the relay capability is no longer necessary, so a
    # relay-less remaining team must stay deployable.
    "relay_exit_after_receipt_committed": (
        "restaurant_receipt_committed", [RESTAURANT_LITE3_1],
        {"service_time_budget": 8}),
    # Both zones inspected and only the receipt remains: the receipt is a
    # relay action that needs no navigation, so the sole navigating member
    # may exit while an aerial relay platform stays deployable.
    "navigator_exits_receipt_only_remaining": (
        "restaurant_both_zones_inspected", [RESTAURANT_IRIS_1],
        {"service_time_budget": 8}),
    # Zones unchecked: without ground navigation no inspection can ever be
    # measured true, so the aerial-only remainder is not deployable.
    "navigator_exits_before_inspection": (
        "restaurant_initial", [RESTAURANT_IRIS_1], {"service_time_budget": 8}),
    # Lite3 can inspect but never relay: with only the decoy left the
    # receipt predicate is unreachable.
    "lite3_only_cannot_commit_receipt": (
        "restaurant_both_zones_inspected", [RESTAURANT_LITE3_1],
        {"service_time_budget": 8}),
    # Remaining budget funds the parallel substitute (4 units) but not the
    # serial one (7 units): deployable through the parallel path only.
    "serial_budget_infeasible_parallel_remaining": (
        "restaurant_initial", [RESTAURANT_CARTER_1, RESTAURANT_LITE3_1],
        {"service_time_budget": 6}),
    # Serial-only team below the serial cost: no legal substitute remains.
    "solo_serial_budget_exhausted": (
        "restaurant_initial", [RESTAURANT_CARTER_1], {"service_time_budget": 6}),
    # Even the parallel substitute exceeds the remaining budget.
    "budget_exhausted_all_alternatives": (
        "restaurant_initial", [RESTAURANT_CARTER_1, RESTAURANT_LITE3_1],
        {"service_time_budget": 2}),
}


def restaurant_development_marker(*, evidence_class: str = "contract") -> dict[str, Any]:
    return build_development_marker(
        RESTAURANT_SCENE_KEY, asset_status="development_unverified",
        evidence_class=evidence_class,
        infrastructure_blockers=[RESTAURANT_NATIVE_BLOCKER])


def build_restaurant_public_task() -> dict[str, Any]:
    return build_goal_task_package(RESTAURANT_SCENE_KEY)["public_task"]


def build_restaurant_registration(
    *, validation_scope: str = RESTAURANT_DEVELOPMENT_SCOPE,
    evidence_refs: tuple[str, ...] = ("dev:restaurant-model-review-v1",),
) -> dict[str, Any]:
    """Freeze the restaurant model; native scope needs native evidence refs."""
    if validation_scope == RESTAURANT_NATIVE_SCOPE and not any(
            ref.startswith("native:") for ref in evidence_refs):
        raise ValueError("native scope requires native-prefixed evidence")
    return freeze_oracle_registration(
        deepcopy(RESTAURANT_MODEL),
        task_sha256=canonical_sha256(build_restaurant_public_task()),
        measurement_points=deepcopy(list(RESTAURANT_MEASUREMENT_POINTS)),
        validation={"status": "verified", "scope": validation_scope,
                    "evidence_refs": list(evidence_refs)})


def build_restaurant_state_contract(registration: Mapping[str, Any]) -> dict[str, Any]:
    return freeze_state_contract(
        build_restaurant_public_task(), registration,
        predicate_bindings=deepcopy(RESTAURANT_PREDICATE_BINDINGS),
        outcome_predicates={
            "kitchen_service_inspected": ["kitchen_arrived",
                                          "kitchen_observed"],
            "dining_service_inspected": ["dining_arrived",
                                         "dining_observed"],
            "readiness_receipt_committed": ["readiness_receipt_committed"],
        },
        source_versions=dict(RESTAURANT_SOURCE_VERSIONS),
        entity_ids=list(RESTAURANT_ENTITY_IDS),
        asset_manifest_sha256="a" * 64, skill_contract_sha256="b" * 64)


def restaurant_native_samples(measurement_id: str, facts: set[str]) -> dict[str, Any]:
    """Independent synthetic samples per source; never derived from state."""
    by_source: dict[str, dict[str, bool]] = {}
    for fact, binding in RESTAURANT_PREDICATE_BINDINGS.items():
        by_source.setdefault(binding["source_id"], {})[fact] = fact in facts
    return {"measurement_id": measurement_id, "sources": by_source}


def build_restaurant_development_certificate(
        registration: Mapping[str, Any], *, independent_review: str = "pending"):
    """Development completeness certificate; review stays pending in dev."""
    return build_alternative_path_certificate(
        RESTAURANT_SCENE_KEY, registration,
        path_classes={
            "single_robot_serial_substitute": {
                "description": "一台地面单机依次完成两区到达、观察与统一回执的合法替代",
                "covered_transitions": ["solo_arrive_kitchen",
                                        "observe_kitchen_zone",
                                        "solo_arrive_dining",
                                        "observe_dining_zone",
                                        "commit_readiness_receipt"]},
            "dual_robot_parallel_substitute": {
                "description": "两台地面成员分区并行检查、任一台 relay 能力提交统一回执的合法替代",
                "covered_transitions": ["parallel_inspect_service_zones",
                                        "commit_readiness_receipt"]},
            "relay_capability_boundary": {
                "description": "relay 能力仅在回执未提交时必要；两区已检查后导航退出仍可行，"
                               "回执本身是 relay 动作不要求导航；Lite3 无 relay 的差异保留",
                "covered_transitions": ["commit_readiness_receipt"]},
            "service_time_budget_exhaustion": {
                "description": "剩余时间预算（240 s 语义，30 s 单位）不足时串行与并行替代"
                               "先后不可用的对照",
                "covered_transitions": ["solo_arrive_kitchen",
                                        "solo_arrive_dining",
                                        "parallel_inspect_service_zones"]},
        },
        covered_measurement_ids=[point["id"] for point in
                                 RESTAURANT_MEASUREMENT_POINTS],
        uncovered_path_policy="treated_as_uncovered",
        independent_review=independent_review,
        evidence_refs=["dev:restaurant-path-review-v1"])


def make_restaurant_source_readers(
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
            expected = {fact for fact, binding in RESTAURANT_PREDICATE_BINDINGS.items()
                        if binding["source_id"] == source_id}
            if set(values) != expected:
                return {}
            entities = sorted({entity for fact in expected
                               for entity in RESTAURANT_PREDICATE_BINDINGS[fact]["entity_ids"]})
            return {
                "source_id": source_id,
                "source_version": RESTAURANT_SOURCE_VERSIONS[source_id],
                "measurement_id": observation["measurement_id"],
                "state_sha256": canonical_sha256(state),
                "measured_at_s": state["time_s"],
                "entity_versions": {entity: state["entity_versions"][entity]
                                    for entity in entities},
                "predicates": {fact: values[fact] is True for fact in sorted(values)},
                "evidence_refs": [f"restaurant:{source_id}:{observation['measurement_id']}"],
            }
        return measure
    return {source_id: reader(source_id) for source_id in RESTAURANT_SOURCE_VERSIONS}


def make_restaurant_state_provider(measurement_id: str, facts: set[str], *,
                                   robots: list[dict[str, Any]],
                                   resources: Mapping[str, int], time_s: float = 6.0,
                                   entity_versions: Mapping[str, int] | None = None):
    """State provider bound to one preregistered point; synthetic by default."""
    versions = dict(entity_versions or {entity: 1 for entity in RESTAURANT_ENTITY_IDS})
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
            "resource_versions": {"service_time_budget": 1},
        }
        return {"measurement_id": measurement_id, "state": state,
                "native_samples": restaurant_native_samples(measurement_id, facts)}

    return provide


def query_restaurant_development_case(
    case_id: str, *, episode_id: str = "restaurant-dev-episode",
    registration: Mapping[str, Any] | None = None,
    state_contract: Mapping[str, Any] | None = None,
    certificate: Mapping[str, Any] | None = None,
    native_probes: Mapping[str, Callable] | None = None,
) -> dict[str, Any]:
    """Run one development case end-to-end and stamp it as non-formal evidence.

    ``not_deployable`` answers pass the alternative-path certificate gate,
    matching the other scene readers: without a verified certificate the
    label downgrades to a platform failure with a null label.
    """
    if case_id not in RESTAURANT_DEV_CASES:
        raise ValueError(f"unknown restaurant development case: {case_id}")
    measurement_id, robots, resources = RESTAURANT_DEV_CASES[case_id]
    point = next(p for p in RESTAURANT_MEASUREMENT_POINTS if p["id"] == measurement_id)
    facts = set(point["required_true"])
    registration = registration or build_restaurant_registration()
    contract = state_contract or build_restaurant_state_contract(registration)
    certificate = certificate or build_restaurant_development_certificate(registration)
    provider = make_restaurant_state_provider(
        measurement_id, facts, robots=deepcopy(robots), resources=resources)
    readers = make_restaurant_source_readers(native_probes)
    snapshot = {"assessment_id": f"{case_id}-a1", "assessment_epoch": 0}
    observation = provider(snapshot)
    packets = {source: readers[source](snapshot, observation)
               for source in RESTAURANT_SOURCE_VERSIONS}
    grounding = ground_state(contract, registration,
                             measurement_id=measurement_id,
                             state=observation["state"], readbacks=packets)
    result = query_remaining_feasibility(
        registration, measurement_id=measurement_id, episode_id=episode_id,
        assessment_id=snapshot["assessment_id"], assessment_epoch=0,
        task_sha256=registration["task_sha256"],
        state=observation["state"], grounding=grounding)
    gated = gate_not_deployable_result(result, certificate, registration)
    return stamp_evidence_record(gated, restaurant_development_marker())


__all__ = [
    "RESTAURANT_DEVELOPMENT_SCOPE", "RESTAURANT_ENTITY_IDS", "RESTAURANT_FACT_IDS",
    "RESTAURANT_MEASUREMENT_POINTS", "RESTAURANT_MODEL", "RESTAURANT_NATIVE_BLOCKER",
    "RESTAURANT_NATIVE_SCOPE", "RESTAURANT_PREDICATE_BINDINGS", "RESTAURANT_SCENE_KEY",
    "RESTAURANT_SOURCE_VERSIONS", "RESTAURANT_TERMINAL_ALL",
    "build_restaurant_development_certificate", "build_restaurant_public_task",
    "build_restaurant_registration", "build_restaurant_state_contract",
    "make_restaurant_source_readers", "make_restaurant_state_provider",
    "query_restaurant_development_case", "restaurant_development_marker",
    "restaurant_native_samples",
]
