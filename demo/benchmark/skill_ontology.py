"""The public skill vocabulary shared by goal-driven benchmark tasks.

Business outcomes deliberately do not name these skills.  A participant may
compose them into a plan, while the evaluator scores the resulting business
state and execution evidence.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


SKILL_CATALOG_SCHEMA = "eai.skills"
SKILL_CATALOG_VERSION = 2
CITY_NATIVE_SKILL_CATALOG_VERSION = 3
CITY_AERIAL_SKILL_CATALOG_VERSION = 4

# Every admitted catalog revision.  The frozen v2/v3 releases must stay
# byte-identical, so new skills declare the first version that publishes them
# and older versions keep assembling their exact historical vocabularies.
_SKILL_CATALOG_VERSIONS = (SKILL_CATALOG_VERSION,
                           CITY_NATIVE_SKILL_CATALOG_VERSION,
                           CITY_AERIAL_SKILL_CATALOG_VERSION)
_SKILL_INTRODUCED_AT: dict[str, int] = {
    "aerial_broadcast": CITY_AERIAL_SKILL_CATALOG_VERSION,
}
# Canonical "eai.skills.vN" identifiers for consumers that hold a task spec's
# catalog field instead of an integer revision.
_CATALOG_IDENTIFIERS = {f"{SKILL_CATALOG_SCHEMA}.v{version}": version
                        for version in _SKILL_CATALOG_VERSIONS}


_SKILLS: dict[str, dict[str, Any]] = {
    "ground_navigation": {
        "parameters": {
            "region_id": {"required": True, "kind": "public_region"},
            "route_policy": {"required": False, "kind": "string"},
            "route_ids": {"required": True, "kind": "string_list"},
        },
        "preconditions": ["agent_is_active", "ground_navigation_interface_available"],
        "effects": ["agent_reaches_public_region"],
        "capability_requirements": {"all_of": ["ground_navigation"], "min_agents": 1},
        "failure_codes": ["route_unreachable", "navigation_interface_unavailable", "timeout"],
        "evidence_types": ["pose_trace", "region_arrival"],
        "timeout_semantics": "simulation_time_budget",
        "cancel_semantics": "cancel_before_terminal_arrival",
        "execution_owner": "ground_navigation_runtime",
        "example_robot_types": ["m20", "carter", "simple_car"],
    },
    "aerial_navigation": {
        "parameters": {
            "region_id": {"required": True, "kind": "public_region"},
            "altitude_m": {"required": False, "kind": "finite_nonnegative"},
            "route_ids": {"required": True, "kind": "string_list"},
        },
        "preconditions": ["agent_is_active", "aerial_navigation_interface_available"],
        "effects": ["aerial_agent_reaches_public_region"],
        "capability_requirements": {"all_of": ["aerial_navigation"], "min_agents": 1},
        "failure_codes": ["route_blocked", "aerial_navigation_interface_unavailable", "timeout"],
        "evidence_types": ["aerial_pose_trace", "region_arrival"],
        "timeout_semantics": "simulation_time_budget",
        "cancel_semantics": "cancel_before_terminal_arrival",
        "execution_owner": "aerial_navigation_runtime",
        "example_robot_types": ["iris", "cf2x", "pegasus"],
    },
    "stair_traversal": {
        "parameters": {
            "link_id": {"required": True, "kind": "public_vertical_link"},
            "exit_region_id": {"required": True, "kind": "public_region"},
            "route_ids": {"required": True, "kind": "string_list"},
        },
        "preconditions": ["agent_is_active", "stair_capability_declared", "support_surface_available"],
        "effects": ["agent_reaches_connected_exit_region"],
        "capability_requirements": {"all_of": ["stair_traversal"], "min_agents": 1},
        "failure_codes": ["stair_link_unavailable", "support_lost", "vertical_progress_timeout"],
        "evidence_types": ["support_relative_progress", "healthy_support_patch", "upper_region_arrival"],
        "timeout_semantics": "bounded_vertical_progress_window",
        "cancel_semantics": "cancel_and_hold_on_supported_surface",
        "execution_owner": "vertical_traversal_runtime",
        "example_robot_types": ["m20"],
    },
    "observe": {
        "parameters": {
            "region_id": {"required": True, "kind": "public_region"},
            "subject_ref": {"required": False, "kind": "public_object_or_region"},
            "observation_mode": {"required": False, "kind": "string"},
        },
        "preconditions": ["agent_is_active", "observation_interface_available"],
        "effects": ["fresh_observation_is_published"],
        "capability_requirements": {"all_of": ["observe"], "min_agents": 1},
        "failure_codes": ["view_unavailable", "observation_expired", "timeout"],
        "evidence_types": ["observation_frame", "observation_digest", "source_pose"],
        "timeout_semantics": "observation_window",
        "cancel_semantics": "cancel_before_observation_window_closes",
        "execution_owner": "scene_observation_runtime",
        "example_robot_types": ["iris", "cf2x", "m20", "carter"],
    },
    "pick": {
        "parameters": {
            "object_id": {"required": True, "kind": "public_object"},
            "pickup_region_id": {"required": True, "kind": "public_region"},
            "handling_mode_id": {"required": True, "kind": "string"},
            "carrier_bindings": {"required": True, "kind": "carrier_bindings"},
        },
        "preconditions": ["object_is_available", "fixed_grasp_point_reachable", "load_within_effective_capacity", "identity_evidence_current"],
        "effects": ["agent_owns_object_temporarily"],
        "capability_requirements": {"all_of": ["pick"], "min_agents": 1},
        "failure_codes": ["object_not_available", "identity_not_confirmed", "payload_exceeded", "grasp_interface_incompatible", "point_unreachable"],
        "evidence_types": ["object_identity", "pickup_event", "object_ownership"],
        "timeout_semantics": "bounded_pick_window",
        "cancel_semantics": "cancel_before_object_ownership_changes",
        "execution_owner": "manipulation_runtime",
        "example_robot_types": ["m20"],
    },
    "place": {
        "parameters": {
            "object_id": {"required": True, "kind": "public_object"},
            "placement_region_id": {"required": True, "kind": "public_region"},
            "route_ids": {"required": False, "kind": "string_list"},
        },
        "preconditions": ["agent_owns_object", "placement_region_is_allowed"],
        "effects": ["object_is_placed_in_public_region"],
        "capability_requirements": {"all_of": ["place"], "min_agents": 1},
        "failure_codes": ["placement_not_allowed", "ownership_mismatch", "resource_conflict"],
        "evidence_types": ["object_pose", "release_event", "placement_acceptance"],
        "timeout_semantics": "bounded_place_window",
        "cancel_semantics": "cancel_before_release",
        "execution_owner": "manipulation_runtime",
        "example_robot_types": ["m20"],
    },
    "handoff": {
        "parameters": {
            "object_id": {"required": True, "kind": "public_object"},
            "receiving_region_id": {"required": True, "kind": "public_region"},
            "recipient_role": {"required": False, "kind": "string"},
            "route_ids": {"required": False, "kind": "string_list"},
        },
        "preconditions": ["agent_owns_object", "object_identity_current", "receiving_region_available", "handoff_participants_visible"],
        "effects": ["business_recipient_receipt_is_recorded"],
        "capability_requirements": {"all_of": ["handoff"], "min_agents": 1},
        "failure_codes": ["recipient_unavailable", "identity_mismatch", "handoff_rejected", "timeout"],
        "evidence_types": ["handoff_event", "recipient_receipt", "object_identity"],
        "timeout_semantics": "business_handoff_window",
        "cancel_semantics": "cancel_before_receipt_is_committed",
        "execution_owner": "business_handoff_runtime",
        "example_robot_types": ["m20", "carter"],
    },
    "cooperative_carry": {
        "parameters": {
            "object_id": {"required": True, "kind": "public_object"},
            "destination_region_id": {"required": True, "kind": "public_region"},
            "handling_mode_id": {"required": True, "kind": "string"},
            "carrier_bindings": {"required": True, "kind": "carrier_bindings"},
            "route_ids": {"required": True, "kind": "string_list"},
        },
        "preconditions": ["object_is_available", "coalition_is_synchronized", "shared_carry_interface_available"],
        "effects": ["object_attached_to_declared_fixed_points", "object_reaches_destination_as_coalition", "object_released_at_destination"],
        "capability_requirements": {"all_of": ["cooperative_carry"], "min_agents": 2},
        "failure_codes": ["coalition_incomplete", "payload_exceeded", "grasp_interface_incompatible", "loaded_route_blocked", "member_unavailable"],
        "evidence_types": ["coalition_membership", "shared_object_pose", "pickup_release_pair"],
        "timeout_semantics": "bounded_cooperative_transport_window",
        "cancel_semantics": "cancel_and_release_under_shared_ownership_protocol",
        "execution_owner": "cooperative_transport_runtime",
        "example_robot_types": ["m20", "carter"],
    },
    "relay": {
        "parameters": {
            "message_kind": {"required": True, "kind": "string"},
            "region_id": {"required": False, "kind": "public_region"},
        },
        "preconditions": ["communication_interface_available", "agent_is_active"],
        "effects": ["message_is_published_to_current_team"],
        "capability_requirements": {"all_of": ["relay"], "min_agents": 1},
        "failure_codes": ["channel_unavailable", "bandwidth_exhausted", "message_expired"],
        "evidence_types": ["message_publication", "delivery_receipt"],
        "timeout_semantics": "communication_budget_window",
        "cancel_semantics": "cancel_before_publication",
        "execution_owner": "communication_runtime",
        "example_robot_types": ["iris", "cf2x"],
    },
    "aerial_broadcast": {
        "parameters": {
            "person_ids": {"required": True, "kind": "string_list"},
            "observation_refs": {"required": True, "kind": "string_list"},
            "anchor_region_id": {"required": False, "kind": "public_region"},
            "now_s": {"required": False, "kind": "finite_nonnegative"},
        },
        "preconditions": [
            "agent_is_active", "aerial_navigation_interface_available",
            "fresh_delivered_person_observations", "broadcast_channel_available",
        ],
        "effects": ["aerial_arrival_stamp_recorded", "broadcast_acknowledgement_collected"],
        "capability_requirements": {"all_of": ["aerial_navigation", "relay"], "min_agents": 1},
        "failure_codes": [
            "person_not_observed", "evidence_expired", "no_broadcast_acknowledgement",
            "anchor_unreachable", "timeout",
        ],
        "evidence_types": ["broadcast_ack", "arrival_stamp", "observation_provenance"],
        "timeout_semantics": "bounded_aerial_arrival_and_broadcast_acknowledgement_window",
        "cancel_semantics": "cancel_before_broadcast_commit",
        "execution_owner": "aerial_broadcast_runtime",
        "example_robot_types": ["iris"],
    },
    "guide_people": {
        "parameters": {
            "person_ids": {"required": True, "kind": "string_list"},
            "observation_refs": {"required": True, "kind": "string_list"},
            "assembly_region_id": {"required": True, "kind": "public_region"},
            "now_s": {"required": False, "kind": "finite_nonnegative"},
        },
        "preconditions": [
            "agent_is_active", "ground_navigation_interface_available",
            "fresh_delivered_person_observations", "guidance_channel_available",
            "assembly_route_and_capacity_admitted",
        ],
        "effects": ["guidance_request_submitted", "people_receive_guidance"],
        "capability_requirements": {
            "all_of": ["ground_navigation", "relay"], "min_agents": 1,
        },
        "failure_codes": [
            "person_not_observed", "evidence_expired", "no_acknowledgement",
            "route_unsafe", "assembly_capacity_exceeded", "person_unresponsive",
            "timeout",
        ],
        "evidence_types": [
            "guidance_acknowledgement", "measured_person_trajectory",
            "assembly_arrival", "observation_provenance",
        ],
        "timeout_semantics": "bounded_per_person_guidance_acknowledgement_and_arrival_window",
        "cancel_semantics": "acknowledged_safe_hold_or_replacement_guidance",
        "execution_owner": "human_behavior_runtime",
        "example_robot_types": ["go2", "carter"],
    },
    "control_following_traffic": {
        "parameters": {
            "vehicle_ids": {"required": True, "kind": "string_list"},
            "observation_refs": {"required": True, "kind": "string_list"},
            "mode": {
                "required": True, "kind": "enum",
                "values": ["hold", "divert", "release"],
            },
        },
        "preconditions": [
            "agent_is_active", "ground_navigation_interface_available",
            "fresh_delivered_following_traffic_observations",
            "traffic_control_authority_available", "safe_control_point_admitted",
        ],
        "effects": [
            "traffic_control_request_submitted",
            "traffic_service_applies_acknowledged_policy",
        ],
        "capability_requirements": {
            "all_of": ["ground_navigation", "relay"], "min_agents": 1,
        },
        "failure_codes": [
            "vehicle_not_observed", "accident_vehicle_excluded", "evidence_expired",
            "no_authority", "control_point_blocked", "diversion_blocked",
            "no_acknowledgement", "timeout",
        ],
        "evidence_types": [
            "traffic_control_acknowledgement", "measured_vehicle_trajectory",
            "observation_provenance",
        ],
        "timeout_semantics": "explicit_service_response_and_following_vehicle_reaction_window",
        "cancel_semantics": "keep_existing_hold_until_safe_release_is_acknowledged",
        "execution_owner": "traffic_runtime",
        "example_robot_types": ["go2", "carter"],
    },
    "report_incident": {
        "parameters": {
            "observation_refs": {"required": True, "kind": "string_list"},
            "recipient_service_id": {"required": True, "kind": "string"},
            "time_s": {"required": True, "kind": "finite_nonnegative"},
        },
        "preconditions": [
            "agent_is_active", "observation_interface_available",
            "fresh_delivered_incident_evidence", "response_service_reachable",
        ],
        "effects": [
            "incident_record_submitted", "response_service_receives_incident_record",
        ],
        "capability_requirements": {
            "all_of": ["observe", "relay"], "min_agents": 1,
        },
        "failure_codes": [
            "evidence_not_observed", "evidence_expired", "recipient_unavailable",
            "receipt_missing", "timeout",
        ],
        "evidence_types": [
            "response_service_receipt", "observation_provenance",
            "incident_observer_pose",
        ],
        "timeout_semantics": "bounded_message_and_receipt_window",
        "cancel_semantics": "cancel_before_receipt_commit",
        "execution_owner": "domain_service_runtime",
        "example_robot_types": ["iris"],
    },
    "synchronize": {
        "parameters": {
            "coordination_ref": {"required": True, "kind": "string"},
            "agent_ids": {"required": True, "kind": "agent_id_list"},
        },
        "preconditions": ["all_declared_agents_are_active", "communication_interface_available"],
        "effects": ["coalition_barrier_is_satisfied"],
        "capability_requirements": {"all_of": ["synchronize"], "min_agents": 2},
        "failure_codes": ["member_not_ready", "message_not_delivered", "synchronization_timeout"],
        "evidence_types": ["barrier_arrival", "team_message_trace"],
        "timeout_semantics": "bounded_barrier_window",
        "cancel_semantics": "cancel_before_barrier_commit",
        "execution_owner": "team_coordination_runtime",
        "example_robot_types": ["m20", "carter"],
    },
}


def _skills_at(version: int) -> tuple[str, ...]:
    """Skills published by one catalog revision, in definition order."""
    return tuple(name for name in _SKILLS
                 if _SKILL_INTRODUCED_AT.get(name, SKILL_CATALOG_VERSION) <= version)


def skill_names(*, version: int = SKILL_CATALOG_VERSION) -> tuple[str, ...]:
    return _skills_at(version)


def skill_names_for_catalog(identifier: Any) -> tuple[str, ...]:
    """Skill names published by a task's ``eai.skills.vN`` catalog identifier.

    Catalog admission stays with the goal contract; this resolver only maps an
    admitted revision identifier to its exact vocabulary.  Anything that is not
    an admitted identifier resolves to the default catalog, so callers that
    never carried a catalog field keep their historical behavior.
    """
    version = (_CATALOG_IDENTIFIERS.get(identifier)
               if isinstance(identifier, str) else None)
    return _skills_at(version if version is not None else SKILL_CATALOG_VERSION)


def skill_definition(skill: str, *, version: int = SKILL_CATALOG_VERSION) -> dict[str, Any]:
    if skill not in _SKILLS:
        raise ValueError(f"unknown skill: {skill}")
    if version not in set(_SKILL_CATALOG_VERSIONS) or type(version) is not int:
        raise ValueError("unsupported skill catalog version")
    if skill not in _skills_at(version):
        # Cross-version fail-closed: a revision never serves a skill that a
        # later version introduced, so older frozen catalogs stay exact.
        raise ValueError(f"skill {skill} is not published by skill catalog version {version}")
    result = deepcopy(_SKILLS[skill])
    if (skill == "guide_people"
            and version in {CITY_NATIVE_SKILL_CATALOG_VERSION,
                            CITY_AERIAL_SKILL_CATALOG_VERSION}):
        result["timeout_semantics"] = "per_person_acknowledgement_window; arrival_is_later_business_evidence"
        result["evidence_types"] = ["guidance_acknowledgement", "observation_provenance"]
        result["terminal_semantics"] = "all_targets_acknowledged_by_native_service_while_field_role_held"
        result["later_business_evidence"] = ["measured_person_trajectory", "assembly_arrival"]
    if skill == "aerial_broadcast" and version == CITY_AERIAL_SKILL_CATALOG_VERSION:
        result["terminal_semantics"] = "aerial_arrival_and_broadcast_acknowledged_while_field_role_held"
    result["id"] = skill
    result["catalog"] = SKILL_CATALOG_SCHEMA
    result["version"] = version
    result["guarantee"] = "deterministic_effect_if_preconditions_and_invariants_hold"
    result["configuration_basis"] = "task_declared_profile; robot_type_is_not_certification"
    result["execution_fault_policy"] = "valid_call_backend_failure_is_infrastructure_error"
    result["invariants"] = [
        "members_remain_available_with_declared_capabilities",
        "per_member_payload_and_fixed_point_interfaces_remain_valid",
        "body_and_loaded_formation_fit_declared_routes",
        "timed_member_object_and_shared_resource_reservations_are_respected",
        "observations_are_limited_to_scene_bound_visibility",
    ]
    result["interruption_policy"] = (
        "An observed member, capability or resource change invalidates the guarantee until "
        "the runtime reports a safe held/released state and the participant revises its plan. "
        "The backend must never silently complete an invalidated action."
    )
    return result


def public_skill_catalog(*, version: int = SKILL_CATALOG_VERSION) -> dict[str, Any]:
    """Return the reviewable catalog with no implementation imports or paths."""
    if version not in set(_SKILL_CATALOG_VERSIONS) or type(version) is not int:
        raise ValueError("unsupported skill catalog version")
    return {
        "schema": SKILL_CATALOG_SCHEMA,
        "version": version,
        "skills": {name: skill_definition(name, version=version)
                   for name in _skills_at(version)},
    }


def validate_skill_name(skill: Any) -> str:
    if not isinstance(skill, str) or skill not in _SKILLS:
        raise ValueError(f"skill must be one of {sorted(_SKILLS)}")
    return skill


def validate_skill_arguments(skill: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    validate_skill_name(skill)
    if not isinstance(arguments, Mapping):
        raise ValueError("skill arguments must be an object")
    definition = _SKILLS[skill]
    parameters = definition["parameters"]
    unknown = set(arguments) - set(parameters)
    if unknown:
        raise ValueError(f"{skill}: unknown argument fields {sorted(unknown)}")
    missing = [name for name, spec in parameters.items()
               if spec["required"] and name not in arguments]
    if missing:
        raise ValueError(f"{skill}: missing argument fields {sorted(missing)}")
    for name, value in arguments.items():
        if value is None:
            raise ValueError(f"{skill}.{name} must not be null")
        kind = parameters[name]["kind"]
        if kind in {"public_region", "public_object", "public_object_or_region", "public_vertical_link", "string"}:
            if not isinstance(value, str) or not value:
                raise ValueError(f"{skill}.{name} must be a nonempty string")
        elif kind in {"agent_id_list", "string_list"}:
            if (not isinstance(value, list) or not value or
                    any(not isinstance(item, str) or not item for item in value) or
                    len(value) != len(set(value))):
                raise ValueError(f"{skill}.{name} must be a unique nonempty string list")
        elif kind == "finite_nonnegative":
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{skill}.{name} must be a finite nonnegative number")
        elif kind == "enum":
            choices = parameters[name].get("values")
            if not isinstance(choices, list) or value not in choices:
                raise ValueError(f"{skill}.{name} must be one of {choices}")
        elif kind == "carrier_bindings":
            if not isinstance(value, list) or not value:
                raise ValueError(f"{skill}.{name} must bind members to fixed points")
            for binding in value:
                if (not isinstance(binding, Mapping) or set(binding) != {"agent_id", "point_id"}
                        or any(not isinstance(item, str) or not item for item in binding.values())):
                    raise ValueError(f"{skill}.{name} requires agent_id and point_id")
            if (len({row["agent_id"] for row in value}) != len(value)
                    or len({row["point_id"] for row in value}) != len(value)):
                raise ValueError(f"{skill}.{name} must use distinct members and points")
    return deepcopy(dict(arguments))


def required_capabilities(skill: str) -> tuple[str, ...]:
    # Capability requirements are identical across catalog revisions (the
    # version overrides only touch semantics/evidence fields), so this reads
    # the definition directly instead of pinning one revision: callers working
    # with a v4 task can resolve the aerial_broadcast requirements that the
    # frozen v2/v3 catalogs never published.
    if skill not in _SKILLS:
        raise ValueError(f"unknown skill: {skill}")
    return tuple(_SKILLS[skill]["capability_requirements"]["all_of"])


def minimum_agents(skill: str) -> int:
    if skill not in _SKILLS:
        raise ValueError(f"unknown skill: {skill}")
    return int(_SKILLS[skill]["capability_requirements"]["min_agents"])


__all__ = [
    "SKILL_CATALOG_SCHEMA", "SKILL_CATALOG_VERSION", "CITY_NATIVE_SKILL_CATALOG_VERSION",
    "CITY_AERIAL_SKILL_CATALOG_VERSION", "minimum_agents",
    "public_skill_catalog", "required_capabilities", "skill_definition",
    "skill_names", "skill_names_for_catalog", "validate_skill_arguments",
    "validate_skill_name",
]
