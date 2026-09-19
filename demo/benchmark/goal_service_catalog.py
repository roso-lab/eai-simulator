"""Authoring proposals for domain services that are NOT active eai.skills.

This registry does not import city runtimes, register skills, assign robot
capabilities, execute requests, or score outcomes. API references describe the
current preparation adapters, not a conformance certification. Version 1 is an
immutable reviewed catalog: nested fields, policy flags, and API parameter
bindings must match the published version; extensions require a catalog update.

``review_service_requirements(requirements, public_task)`` accepts a list of:
* skill/service IDs, or ``{"id": ...}``, for a declaration-only supply review;
* the closed metadata rows returned by city ``contracts.required_services()``;
* ``{"id": ..., "arguments": {...}, "reference_context": {...}}`` for a
  proposed service request with an author/runtime-supplied evidence snapshot.

The last form is an intake check, never dispatch authorization. Its context has
``current_time_s``, ``observations``, and ``field_roles``. Observations have
``id, producer_agent_id, region_id, captured_at_s, delivered_at_s, valid_until_s,
entities``. Each entity has ``id, kind`` (person/vehicle/robot); a vehicle also
has boolean ``following_incident, is_accident_vehicle``. Field-role rows have
``agent_id, role, region_id, observation_refs`` and must cite fresh observations
of that robot in that region. These are proposed evidence envelopes for a
future trusted adapter, not existing city observation payloads. Provenance,
field-role duration, authority, physical presence and service outcomes still
need runtime evidence validation. Participants must not author this context.

The upstream intake owns full public-task schema validation. This module checks
only the public IDs and capability declarations it uses. Core-skill arguments,
physical feasibility and complete plans remain with the shared goal validators.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .skill_ontology import (
    SKILL_CATALOG_SCHEMA, SKILL_CATALOG_VERSION, minimum_agents, skill_names,
)


SERVICE_PROPOSALS_SCHEMA = "eai.benchmark.goal_service_proposals"
SERVICE_PROPOSALS_VERSION = 1
SERVICE_REVIEW_SCHEMA = "eai.benchmark.goal_service_requirement_review"
_LEGACY_FIELDS = {
    "id", "status", "arguments", "preconditions", "effects", "failure_codes",
    "evidence_types", "cancel_semantics", "timeout_semantics", "owner", "outcome_ids",
}
_LEGACY_OPERATION_FIELDS = _LEGACY_FIELDS | {"participant_operation", "required_public_skill"}
_LEGACY_ALIASES = {
    "guide_people": {"observed_person_ids": "person_ids", "assembly_region_id": "assembly_region_id"},
    "control_traffic": {"mode_hold_divert_release": "mode"},
    "report_incident": {"incident_observation_refs": "observation_refs", "recipient_service_id": "recipient_service_id"},
}


def _parameter(kind: str, *, required: bool = True) -> dict[str, Any]:
    return {"required": required, "kind": kind}


def _proposal(identifier: str, *, parameters: dict[str, Any], method: str,
              role: str, preconditions: list[str], effects: list[str], failures: list[str],
              evidence: list[str], outcome_evidence: list[str], gaps: list[str],
              cancel: str, timeout: str) -> dict[str, Any]:
    return {
        "id": identifier, "activation_status": "proposed", "runtime_status": "runtime_pending",
        "is_active_skill": False, "parameters": parameters,
        "preconditions": preconditions, "effects": effects,
        "failure_codes": failures, "evidence_types": evidence,
        "cancel_semantics": cancel, "timeout_semantics": timeout,
        "adapter_reference": {
            "entry_point": f"demo.city_benchmark.response.CityResponseServices.{method}",
            "parameter_bindings": {name: name for name in parameters},
            "status": "reference_only_not_conformance_validated",
            "unimplemented_requirements": gaps,
        },
        "dynamic_reference_policy": {
            "identity_source": "delivered_observation_snapshot",
            "require_observed": True, "require_delivered": True,
            "expiry_boundary": "current_time_s_less_than_valid_until_s",
            "future_observations_allowed": False,
            "unobserved_actor_enumeration_allowed": False,
            "latest_delivered_capture_supersedes_older_state": True,
            "accident_vehicle_control_allowed": False,
        },
        "field_role_contract": {
            "role": role,
            "assignment_policy": "participant_selected_no_fixed_robot_type_or_agent",
            "required_fields": {
                "agent_id": _parameter("public_agent"), "role": _parameter("service_field_role"),
                "region_id": _parameter("public_region"),
                "observation_refs": _parameter("current_observation_ref_list"),
            },
            "evidence_requirements": [
                "fresh_observed_agent_pose_in_role_region", "time_bounded_role_occupancy",
                "role_action_and_service_request_causal_binding",
                "replacement_or_release_after_observed_roster_or_capability_change",
            ],
            "navigation_arrival_alone_is_service_evidence": False,
            "robot_self_report_is_physical_evidence": False,
            "runtime_binding_status": "pending",
        },
        "outcome_boundary": {
            "request_is_acknowledgement": False,
            "acknowledgement_is_business_success": False,
            "required_independent_evidence": outcome_evidence,
            "runtime_outcome_validation": "pending",
        },
    }


def _catalog() -> dict[str, Any]:
    guide = _proposal(
        "guide_people",
        parameters={"person_ids": _parameter("observed_person_id_list"),
                    "observation_refs": _parameter("current_observation_ref_list"),
                    "assembly_region_id": _parameter("public_region"),
                    "now_s": _parameter("runtime_time_s", required=False)},
        method="guide_people", role="guidance_contact",
        preconditions=["fresh_delivered_person_identity", "affected_roster_bound",
                       "guidance_channel_available", "assembly_safety_and_capacity_admitted",
                       "person_route_admitted", "guidance_contact_field_evidence"],
        effects=["guidance_request_submitted", "per_person_acknowledgement_recorded_if_confirmed"],
        failures=["person_not_observed", "evidence_expired", "no_acknowledgement",
                  "route_unsafe", "assembly_capacity_exceeded", "person_unresponsive",
                  "field_role_evidence_missing"],
        evidence=["guidance_acknowledgement", "measured_person_trajectory", "assembly_arrival",
                  "field_role_pose_and_interval"],
        outcome_evidence=["affected_person_identity_continuity", "measured_person_trajectory",
                          "safe_assembly_arrival", "complete_person_accounting"],
        gaps=["route_reference", "explicit_assembly_route_selection",
              "affected_roster_and_route_capacity_admission", "trusted_observation_ttl_binding",
              "field_role_binding", "independent_per_person_acknowledgement"],
        cancel="acknowledged_safe_hold_or_replacement_guidance",
        timeout="bounded_per_person_guidance_acknowledgement_and_arrival_window",
    )
    traffic = _proposal(
        "control_traffic",
        parameters={"vehicle_ids": _parameter("observed_following_vehicle_id_list"),
                    "observation_refs": _parameter("current_observation_ref_list"),
                    "mode": {"required": True, "kind": "enum", "values": ["hold", "divert", "release"]}},
        method="control_following_traffic", role="traffic_control_point",
        preconditions=["fresh_delivered_following_vehicle_identity", "accident_vehicle_excluded",
                       "control_authority_available", "safe_control_point_admitted",
                       "diversion_route_admitted_when_diverting", "traffic_field_role_evidence"],
        effects=["traffic_control_request_submitted", "policy_applied_only_after_acknowledgement"],
        failures=["vehicle_not_observed", "accident_vehicle_excluded", "not_following_traffic",
                  "evidence_expired", "no_authority", "control_point_blocked",
                  "diversion_blocked", "no_acknowledgement", "field_role_evidence_missing"],
        evidence=["traffic_control_acknowledgement", "measured_vehicle_trajectory",
                  "control_point_pose_and_interval"],
        outcome_evidence=["measured_vehicle_trajectory", "reaction_window_and_stopping_distance",
                          "no_hazard_ingress_after_reaction_window", "rescue_access_preserved"],
        gaps=["ingress_region_id", "route_reference", "control_authority_binding",
              "following_vs_accident_vehicle_identity_binding",
              "field_role_binding", "trusted_observation_ttl_binding", "physical_vehicle_readback"],
        cancel="keep_existing_hold_until_safe_release_is_acknowledged",
        timeout="explicit_service_response_and_following_vehicle_reaction_window",
    )
    incident = _proposal(
        "report_incident",
        parameters={"observation_refs": _parameter("current_observation_ref_list"),
                    "recipient_service_id": _parameter("recipient_service_id"),
                    "time_s": _parameter("runtime_time_s")},
        method="report_incident", role="incident_observer",
        preconditions=["fresh_delivered_evidence", "reachable_response_service",
                       "incident_observer_field_evidence"],
        effects=["incident_record_submitted", "response_receipt_recorded_only_when_received"],
        failures=["evidence_not_observed", "evidence_expired", "recipient_unavailable",
                  "receipt_missing", "field_role_evidence_missing"],
        evidence=["response_service_receipt", "observation_provenance", "incident_observer_pose"],
        outcome_evidence=["receipt_bound_to_recipient_and_exact_observation_refs",
                          "current_incident_record", "independent_continuous_access_check"],
        gaps=["trusted_observation_ttl_binding", "recipient_endpoint_binding",
              "field_role_binding", "independent_response_service_receipt"],
        cancel="cancel_before_receipt_commit", timeout="bounded_message_and_receipt_window",
    )
    return {
        "schema": SERVICE_PROPOSALS_SCHEMA, "version": SERVICE_PROPOSALS_VERSION,
        "base_skill_catalog_version": f"{SKILL_CATALOG_SCHEMA}.v{SKILL_CATALOG_VERSION}",
        "scope": "authoring_and_service_intake_only",
        "registers_active_skills": False, "adds_robot_capabilities": False,
        "execution_validated": False,
        "proposals": {row["id"]: row for row in (guide, traffic, incident)},
    }


def public_service_proposals() -> dict[str, Any]:
    """Return an independent public copy; this does not modify eai.skills."""
    return _catalog()


def _closed_version(value: Any, expected: Any, path: str) -> None:
    if isinstance(expected, dict):
        if not isinstance(value, Mapping) or set(value) != set(expected):
            raise ValueError(f"{path}: closed schema fields mismatch")
        for name in expected:
            _closed_version(value[name], expected[name], f"{path}.{name}")
    elif isinstance(expected, list):
        if not isinstance(value, list) or len(value) != len(expected):
            raise ValueError(f"{path}: list does not match published proposal version")
        for index, item in enumerate(expected):
            _closed_version(value[index], item, f"{path}[{index}]")
    elif type(value) is not type(expected) or value != expected:
        raise ValueError(f"{path}: value does not match published proposal version")


def validate_service_proposals(proposals: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the closed, immutable version including its activation boundary."""
    expected = _catalog()
    _closed_version(proposals, expected, "$service_proposals")
    overlap = set(expected["proposals"]) & set(skill_names())
    if overlap:
        # City services now have participant-facing ontology entries.  The
        # proposal catalog remains useful for adapter/receipt intake, but it
        # must not be mistaken for activation of a runtime backend.
        for identifier in sorted(overlap):
            proposal = expected["proposals"][identifier]
            if proposal["activation_status"] != "proposed" or proposal["runtime_status"] != "runtime_pending":
                raise ValueError(f"{identifier}: active public skill proposal must remain runtime_pending")
    return {"validated": True, "schema": SERVICE_PROPOSALS_SCHEMA,
            "version": SERVICE_PROPOSALS_VERSION, "proposal_ids": sorted(expected["proposals"]),
            "registers_active_skills": False, "execution_validated": False}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _fields(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    value = _mapping(value, label)
    if set(value) != keys:
        raise ValueError(f"{label}: closed schema fields mismatch")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _strings(value: Any, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(f"{label} must be a {'possibly empty' if empty else 'nonempty'} list")
    result = [_string(item, label) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must contain unique values")
    return result


def _time(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be finite nonnegative simulation time")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be finite nonnegative simulation time") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be finite nonnegative simulation time")
    return result


def _public_inputs(task: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], set[str], set[str]]:
    task = _mapping(task, "public_task")
    robots = task.get("candidate_robot_pool")
    if not isinstance(robots, list) or not robots:
        raise ValueError("public_task requires a nonempty candidate_robot_pool")
    rows = {}
    for raw in robots:
        row = _mapping(raw, "public robot")
        identifier = _string(row.get("id"), "public robot id")
        if identifier in rows:
            raise ValueError("public robot ids must be unique")
        capabilities = _strings(row.get("public_capabilities"), "public capabilities", empty=True)
        if not set(capabilities) <= set(skill_names()):
            raise ValueError("robot declaration contains a capability outside active eai.skills")
        rows[identifier] = row
    world = _mapping(task.get("public_world"), "public_world")

    def ids(raw: Any, label: str) -> set[str]:
        if not isinstance(raw, list):
            raise ValueError(f"{label} must be a list")
        values = [_string(_mapping(row, label).get("id"), label) for row in raw]
        if len(values) != len(set(values)):
            raise ValueError(f"{label} ids must be unique")
        return set(values)

    return rows, ids(world.get("regions"), "public regions"), ids(task.get("required_outcomes"), "required outcomes")


def _reference_context(raw: Any, robots: Mapping[str, Any], regions: set[str]) -> dict[str, Any]:
    context = _fields(raw, {"current_time_s", "observations", "field_roles"}, "reference_context")
    now = _time(context["current_time_s"], "current_time_s")
    if not isinstance(context["observations"], list) or not isinstance(context["field_roles"], list):
        raise ValueError("observations and field_roles must be lists")
    observations = {}
    for raw_obs in context["observations"]:
        observation = _fields(raw_obs, {"id", "producer_agent_id", "region_id", "captured_at_s",
                                       "delivered_at_s", "valid_until_s", "entities"}, "observation")
        identifier = _string(observation["id"], "observation id")
        if identifier in observations:
            raise ValueError("observation ids must be unique")
        if _string(observation["producer_agent_id"], "producer_agent_id") not in robots:
            raise ValueError("observation producer is not a public agent")
        if _string(observation["region_id"], "observation region_id") not in regions:
            raise ValueError("observation region is not public")
        capture = _time(observation["captured_at_s"], "captured_at_s")
        delivery = _time(observation["delivered_at_s"], "delivered_at_s")
        expiry = _time(observation["valid_until_s"], "valid_until_s")
        if delivery < capture or expiry <= capture:
            raise ValueError("observation timing must follow capture; validity interval must be nonempty")
        if not isinstance(observation["entities"], list):
            raise ValueError("observation entities must be a list")
        entity_ids = set()
        for raw_entity in observation["entities"]:
            entity = _mapping(raw_entity, "observed entity")
            kind = _string(entity.get("kind"), "observed entity kind")
            if kind not in {"person", "vehicle", "robot"}:
                raise ValueError("unknown observed entity kind")
            _fields(entity, {"id", "kind", "following_incident", "is_accident_vehicle"}
                    if kind == "vehicle" else {"id", "kind"}, "observed entity")
            eid = _string(entity["id"], "observed entity id")
            if eid in entity_ids:
                raise ValueError("observation entity ids must be unique")
            entity_ids.add(eid)
            if kind == "robot" and eid not in robots:
                raise ValueError("observed robot is not a public agent")
            if kind == "vehicle" and any(type(entity[key]) is not bool for key in ("following_incident", "is_accident_vehicle")):
                raise ValueError("vehicle classifications must be booleans")
        observations[identifier] = observation
    return {"now": now, "observations": observations, "field_roles": context["field_roles"]}


def _fresh(identifier: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
    observation = context["observations"].get(identifier)
    if observation is None:
        raise ValueError(f"observation reference {identifier} has not been observed")
    if observation["delivered_at_s"] > context["now"]:
        raise ValueError(f"observation reference {identifier} has not been delivered")
    if context["now"] >= observation["valid_until_s"]:
        raise ValueError(f"observation reference {identifier} is expired")
    return observation


def _visible_entity(identifier: str, kind: str, context: Mapping[str, Any]) -> str:
    matches = [(obs, entity) for obs in context["observations"].values()
               if obs["delivered_at_s"] <= context["now"]
               for entity in obs["entities"] if entity["id"] == identifier]
    if not matches:
        raise ValueError(f"{kind} {identifier} has not been observed and delivered")
    if kind == "vehicle" and any(entity.get("is_accident_vehicle") is True for _, entity in matches):
        raise ValueError(f"accident vehicle {identifier} is excluded from control_traffic")
    latest_capture = max(obs["captured_at_s"] for obs, _ in matches)
    latest = [(obs, entity) for obs, entity in matches if obs["captured_at_s"] == latest_capture]
    if any(entity != latest[0][1] for _, entity in latest):
        raise ValueError(f"{identifier} has conflicting latest observed classifications")
    obs, entity = max(latest, key=lambda row: row[0]["delivered_at_s"])
    _fresh(obs["id"], context)
    if entity["kind"] != kind:
        raise ValueError(f"{identifier} does not have observed kind {kind}")
    if kind == "vehicle" and entity["following_incident"] is not True:
        raise ValueError(f"vehicle {identifier} is not observed following traffic")
    return obs["id"]


def _review_request(requirement: Mapping[str, Any], proposal: Mapping[str, Any],
                    robots: Mapping[str, Any], regions: set[str]) -> dict[str, Any]:
    arguments = _mapping(requirement["arguments"], "service request arguments")
    parameters = proposal["parameters"]
    missing = {name for name, spec in parameters.items() if spec["required"]} - set(arguments)
    if missing or set(arguments) - set(parameters):
        raise ValueError(f"{proposal['id']} request parameter fields mismatch")
    context = _reference_context(requirement["reference_context"], robots, regions)
    refs = set()
    for name, value in arguments.items():
        kind = parameters[name]["kind"]
        if kind == "public_region":
            if _string(value, name) not in regions:
                raise ValueError(f"{name} must reference a public region")
        elif kind == "runtime_time_s":
            if _time(value, name) != context["now"]:
                raise ValueError(f"{name} must match trusted current_time_s")
        elif kind == "recipient_service_id":
            _string(value, name)  # Endpoint reachability remains an integration gate.
        elif kind == "enum":
            if _string(value, name) not in parameters[name]["values"]:
                raise ValueError(f"{name} must be one of {parameters[name]['values']}")
        else:
            values = _strings(value, name)
            for identifier in values:
                if kind == "current_observation_ref_list":
                    _fresh(identifier, context)
                    refs.add(identifier)
                else:
                    reference = _visible_entity(identifier, "person" if kind == "observed_person_id_list" else "vehicle", context)
                    cited = _strings(arguments.get("observation_refs"), "observation_refs")
                    if reference not in cited:
                        raise ValueError(f"{identifier}: latest dynamic identity must be bound to cited observation_refs")
                    refs.add(reference)
    roles = []
    seen_roles = set()
    for raw_role in context["field_roles"]:
        role = _fields(raw_role, {"agent_id", "role", "region_id", "observation_refs"}, "field_role")
        agent = _string(role["agent_id"], "field_role agent_id")
        region = _string(role["region_id"], "field_role region_id")
        if agent not in robots or region not in regions:
            raise ValueError("field_role must reference public agent and region")
        if role["role"] != proposal["field_role_contract"]["role"]:
            raise ValueError("field_role does not match requested service role")
        if agent in seen_roles:
            raise ValueError("field_role agents must be unique per service request")
        seen_roles.add(agent)
        role_refs = _strings(role["observation_refs"], "field_role observation_refs")
        evidence = [_fresh(ref, context) for ref in role_refs]
        if not any(obs["region_id"] == region and {"id": agent, "kind": "robot"} in obs["entities"] for obs in evidence):
            raise ValueError("field_role lacks observed robot presence in its declared region")
        latest_pose = _visible_entity(agent, "robot", context)
        if latest_pose not in role_refs or context["observations"][latest_pose]["region_id"] != region:
            raise ValueError("field_role must cite the latest observed robot presence in its region")
        refs.update(role_refs)
        roles.append(agent)
    return {
        "arguments_and_references_validated": True, "observation_refs": sorted(refs),
        "field_role_agent_ids": sorted(roles), "field_role_reference_status": "declared" if roles else "missing",
        "remaining_requirements": list(proposal["adapter_reference"]["unimplemented_requirements"]),
        "evidence_provenance_validated": False, "field_role_execution_validated": False,
        "runtime_authorized": False, "business_outcomes_validated": False,
    }


def review_service_requirements(requirements: list[Any], public_task: Mapping[str, Any]) -> dict[str, Any]:
    """Classify core supply, pending service extensions and unknown requirements.

    Unknown IDs are reported as blockers; malformed requests or evidence raise
    ValueError. A valid proposed-service request always remains runtime_pending.
    Core supply means enough members declare the skill, not a feasible plan or
    an activated robot/service interface. Legacy city parameter names are
    reported separately from actual adapter arguments; unmapped needs stay open.
    """
    catalog = public_service_proposals()
    validate_service_proposals(catalog)
    robots, regions, outcomes = _public_inputs(public_task)
    if not isinstance(requirements, list):
        raise ValueError("requirements must be a list")
    reviews, seen = [], set()
    core_available, core_missing, proposed, unknown = [], [], [], []
    active = set(skill_names())
    for raw in requirements:
        if isinstance(raw, str):
            requirement = {"id": _string(raw, "requirement id")}
        else:
            requirement = _mapping(raw, "service requirement")
        identifier = _string(requirement.get("id"), "requirement id")
        legacy = set(requirement) in (_LEGACY_FIELDS, _LEGACY_OPERATION_FIELDS)
        request = set(requirement) == {"id", "arguments", "reference_context"}
        if not legacy and not request and set(requirement) != {"id"}:
            raise ValueError("service requirement: closed schema fields mismatch")
        if identifier in seen:
            raise ValueError("requirement ids must be unique")
        seen.add(identifier)
        if legacy:
            for key in ("status", "cancel_semantics", "timeout_semantics", "owner"):
                _string(requirement[key], f"legacy {key}")
            for key in ("arguments", "preconditions", "effects", "failure_codes", "evidence_types", "outcome_ids"):
                _strings(requirement[key], f"legacy {key}")
            if not set(requirement["outcome_ids"]) <= outcomes:
                raise ValueError("legacy service requirement references an unknown business outcome")
            if "participant_operation" in requirement:
                _string(requirement["participant_operation"], "participant_operation")
                _string(requirement["required_public_skill"], "required_public_skill")
        review = {"id": identifier, "runtime_authorized": False}
        if identifier in active:
            candidates = sorted(agent for agent, row in robots.items() if identifier in row["public_capabilities"])
            sufficient = len(candidates) >= minimum_agents(identifier)
            (core_available if sufficient else core_missing).append(identifier)
            review.update(catalog_kind=("active_city_service" if identifier in catalog["proposals"] else "active_main_ontology"),
                          status=("registered_runtime_pending" if identifier in catalog["proposals"] else
                                  ("declared_supply_available" if sufficient else "declared_supply_missing")),
                          declared_candidate_ids=candidates, supply_basis="public_capability_declarations_only")
            if identifier in catalog["proposals"]:
                proposal = catalog["proposals"][identifier]
                review["runtime_status"] = "runtime_pending"
                review["integration_requirements"] = deepcopy(
                    proposal["adapter_reference"]["unimplemented_requirements"])
                if legacy:
                    if "participant_operation" in requirement:
                        method = proposal["adapter_reference"]["entry_point"].rsplit(".", 1)[1]
                        if (requirement["participant_operation"] != method
                                or requirement["required_public_skill"] not in {identifier, method}):
                            raise ValueError("legacy participant service mapping does not match the public skill")
                        review["participant_operation"] = method
                        review["requested_public_skill"] = requirement["required_public_skill"]
                        review["canonical_proposal_id"] = identifier
                    aliases = _LEGACY_ALIASES[identifier]
                    review["legacy_parameter_mapping"] = {
                        arg: aliases[arg] for arg in requirement["arguments"] if arg in aliases}
                    review["unmapped_legacy_parameters"] = sorted(
                        set(requirement["arguments"]) - set(aliases))
                    mapped = set(review["legacy_parameter_mapping"].values())
                    review["additional_adapter_parameters"] = sorted(
                        name for name, param in proposal["parameters"].items()
                        if param["required"] and name not in mapped)
                if request:
                    review["request_review"] = _review_request(
                        requirement, proposal, robots, regions)
        elif identifier in catalog["proposals"]:
            proposal = catalog["proposals"][identifier]
            proposed.append(identifier)
            review.update(catalog_kind="service_proposal", status="requires_integration",
                          activation_status="proposed", runtime_status="runtime_pending",
                          declared_candidate_ids=[],
                          integration_requirements=deepcopy(proposal["adapter_reference"]["unimplemented_requirements"]))
            if legacy:
                if "participant_operation" in requirement:
                    method = proposal["adapter_reference"]["entry_point"].rsplit(".", 1)[1]
                    if (requirement["participant_operation"] != method
                            or requirement["required_public_skill"] not in {identifier, method}):
                        raise ValueError("legacy participant service mapping does not match the proposal adapter")
                    review["participant_operation"] = method
                    review["requested_public_skill"] = requirement["required_public_skill"]
                    review["canonical_proposal_id"] = identifier
                aliases = _LEGACY_ALIASES[identifier]
                review["legacy_parameter_mapping"] = {arg: aliases[arg] for arg in requirement["arguments"] if arg in aliases}
                review["unmapped_legacy_parameters"] = sorted(set(requirement["arguments"]) - set(aliases))
                mapped = set(review["legacy_parameter_mapping"].values())
                review["additional_adapter_parameters"] = sorted(name for name, param in proposal["parameters"].items()
                                                                  if param["required"] and name not in mapped)
            if request:
                review["request_review"] = _review_request(requirement, proposal, robots, regions)
        else:
            if request:
                raise ValueError(f"unknown service {identifier}; cannot validate its request")
            unknown.append(identifier)
            review.update(catalog_kind="unknown", status="unknown_requirement", declared_candidate_ids=[])
        reviews.append(review)
    return {
        "schema": SERVICE_REVIEW_SCHEMA, "version": 1,
        "scope": "declaration_and_reference_intake_not_execution",
        "requirements": reviews, "available_main_skills": sorted(core_available),
        "missing_main_skills": sorted(core_missing), "proposed_services": sorted(proposed),
        "unknown_requirements": sorted(unknown),
        "all_requirements_declared_in_main_ontology": not (core_missing or proposed or unknown),
        "integration_required": bool(core_missing or proposed or unknown),
        "public_task_schema_validated": False, "execution_validated": False,
        "business_outcomes_validated": False, "adds_robot_capabilities": False,
    }


__all__ = ["SERVICE_PROPOSALS_SCHEMA", "SERVICE_PROPOSALS_VERSION", "SERVICE_REVIEW_SCHEMA",
           "public_service_proposals", "validate_service_proposals", "review_service_requirements"]
