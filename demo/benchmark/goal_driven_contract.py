"""Goal-driven task and participant contracts for the benchmark task generator.

This module is deliberately independent of the fixed-DAG publication
contract.  It validates the information boundary and participant-generated
plans; it does not execute Isaac, schedule experiments, or score trajectories.
"""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from .provenance import canonical_sha256
from .goal_constraints import validate_constraint_model, validate_plan_constraints
from .skill_ontology import (
    SKILL_CATALOG_SCHEMA, SKILL_CATALOG_VERSION, CITY_NATIVE_SKILL_CATALOG_VERSION,
    CITY_AERIAL_SKILL_CATALOG_VERSION, minimum_agents, public_skill_catalog,
    required_capabilities, skill_names_for_catalog, validate_skill_arguments,
    validate_skill_name,
)


GOAL_TASK_SCHEMA = "eai.benchmark.goal_task_spec"
GOAL_API_SCHEMA = "eai.benchmark.goal_participant_api"
GOAL_HIDDEN_EVALUATOR_SCHEMA = "eai.benchmark.goal_hidden_evaluator"
GOAL_BASELINE_SCHEMA = "eai.benchmark.goal_baseline_manifest"
GOAL_HETEROGENEITY_SCHEMA = "eai.benchmark.goal_heterogeneity_certificate"
# The public task/API package is v2, while the two nested participant
# envelopes intentionally have their own independently frozen versions.  Keep
# these constants together so adapters cannot infer an envelope version from
# the package version (a common integration mistake).
GOAL_CONTRACT_VERSION = 2
GOAL_DEPLOYMENT_CONTRACT_VERSION = 3
GOAL_ACTION_SCHEMA = "eai.benchmark.goal_action"
GOAL_ACTION_VERSION = 1
GOAL_PLAN_SCHEMA = "eai.benchmark.goal_plan"
# v3 records the participant-path physical feasibility boundary: protocol
# legality remains fail-closed, while admitted physical transport failures are
# reported by the execution host.  Older plans must fail fast rather than being
# interpreted under the new execution semantics.
GOAL_PLAN_VERSION = 3
GOAL_EVENT_SCHEMA = "eai.benchmark.goal_event"
GOAL_EVENT_VERSION = 1


_PUBLIC_TASK_FIELDS = {
    "schema", "version", "task_id", "evaluation_track", "mission",
    "required_outcomes", "candidate_robot_pool", "skill_catalog_version",
    "public_world", "business_constraints", "budgets", "experiment_factor", "execution_contract",
}
_PUBLIC_TASK_FIELDS_V3 = _PUBLIC_TASK_FIELDS | {"deployment_assessment"}
_MISSION_FIELDS = {"scene_key", "goal", "context"}
_OUTCOME_FIELDS = {"id", "description", "acceptance", "evidence_expectations"}
_ROBOT_FIELDS = {"id", "type", "mobility", "public_capabilities", "interfaces", "physical_profile"}
_WORLD_FIELDS = {"regions", "objects", "vertical_links", "public_notes", "routes",
                  "robot_spawn_positions"}
_REGION_FIELDS = {"id", "kind", "public_description", "access_modes", "resource_capacity"}
_OBJECT_FIELDS = {"id", "category", "public_role", "handling_constraints", "physical_properties"}
_VERTICAL_FIELDS = {"id", "from_region_id", "to_region_id", "traversal_modes", "public_description"}
_FACTOR_FIELDS = {"id", "level", "public_rule", "frozen_inputs"}
_FACTOR_LEVELS = {
    "communication_quality": {"immediate", "delayed", "dropped", "bandwidth_limited", "stale"},
    "team_continuity": {"stable", "member_exit", "member_join", "capability_change"},
}
_FORBIDDEN_PUBLIC_KEYS = {
    "affected_node_ids", "baseline_assignment", "baseline_decision", "consumer_id",
    "evaluator_fixture", "expected_marker_id", "ground_truth", "hidden_fault_time_s",
    "preferred_agent", "reference_assignment", "reference_decision", "reference_dag",
    "reference_plan", "true_marker_id", "eligible_agent_ids", "initial_fleet",
    "objectives", "phase_graph", "task_nodes", "execution_steps",
    "witness_plans", "hidden_event_schedule", "capability_audit", "heterogeneity_certificate",
    "capability_patch", "target_selector", "future_event_time",
}
_OUTCOME_FORBIDDEN_KEYS = {
    "assigned_agents", "agent_ids", "coalition", "node_id", "prerequisites",
    "preferred_agent", "robot", "robots", "skill", "skills", "target",
    "target_ref", "task", "task_id", "order", "sequence", "execution_node",
    "execution_nodes", "reference_plan", "reference_dag",
}
_ACTION_FIELDS = {
    "select_team": {"agent_ids"},
    "submit_plan": {"plan"},
    "revise_plan": {"trigger_event_sequence", "replaced_plan_id", "reason", "plan"},
    "dispatch_skill": {"task_id", "evidence_refs"},
    "cancel_skill": {"task_id", "reason"},
    "consume_message": {"message_id"},
}
_DEPLOYMENT_ACTION_FIELDS = {
    "deployment_assessment": {
        "assessment_id", "assessment_epoch", "roster_version",
        "capability_versions", "decision", "reason_codes", "evidence_refs",
    },
}
_EVENT_FIELDS = {
    "episode_started": {"public_task_digest"},
    "observation": {"message_id", "producer_id", "kind", "data", "valid_until_s"},
    "member_join": {"agent_id", "capability_declaration", "capability_version"},
    "member_exit": {"agent_id"},
    "capability_change": {"agent_id", "capability_declaration", "capability_version"},
    "communication_status": {"status", "available_bytes"},
    "action_result": {"action_id", "status", "reason"},
    "tick": {"state_digest"},
}
_DEPLOYMENT_EVENT_FIELDS = {
    "deployment_assessment_required": {
        "assessment_id", "assessment_epoch", "trigger_event_sequence",
        "trigger_event_type", "roster_version", "capability_versions",
        "current_agent_ids",
    },
    "human_assistance_requested": {
        "assessment_id", "assessment_epoch", "decision_action_id",
        "reason_codes", "evidence_refs",
    },
    "resource_status": {"resource_id", "resource_version", "available_capacity"},
    "deployment_change": {"change_id", "change_version", "public_facts"},
}
_SKILL_INTERFACE_REQUIREMENTS = {
    "observe": {"camera"},
    "pick": {"manipulator"},
    "place": {"manipulator"},
    "cooperative_carry": {"cooperative_transport"},
    "relay": {"event_bus"},
    "synchronize": {"event_bus"},
}
# Cross-version fail-closed City catalog admission: each pinned City skill
# catalog serves exactly its own frozen task ids and every pinned City task id
# must carry exactly its own catalog.  v3 admits only NATIVE-01/02 (the frozen
# bound_native_v1/v2 rosters share it), v4 admits only NATIVE-03 (where
# aerial_broadcast first publishes), and no task may cross versions.
_SKILL_CATALOG_TASK_ADMISSION = {
    CITY_NATIVE_SKILL_CATALOG_VERSION: ("CITY-GOAL-NATIVE-01", "CITY-GOAL-NATIVE-02"),
    CITY_AERIAL_SKILL_CATALOG_VERSION: ("CITY-GOAL-NATIVE-03",),
}
_PINNED_CITY_TASK_IDS = frozenset(
    task_id for ids in _SKILL_CATALOG_TASK_ADMISSION.values() for task_id in ids)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a nonempty string list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must contain nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must contain unique values")
    return list(value)


def _nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return result


def _find_keys(value: Any, *, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in _FORBIDDEN_PUBLIC_KEYS:
                found.append(child)
            found.extend(_find_keys(item, path=child))
    elif isinstance(value, (list, tuple)):
        # json.dumps serializes tuples as arrays too; inspect them before any
        # participant delivery, not only after a JSON round trip.
        for index, item in enumerate(value):
            found.extend(_find_keys(item, path=f"{path}[{index}]"))
    return found


def _check_exact(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        raise ValueError(f"{label} fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")


def _public_ids(task_spec: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    world = task_spec["public_world"]
    region_ids = {row["id"] for row in world["regions"]}
    object_ids = {row["id"] for row in world["objects"]}
    link_ids = {row["id"] for row in world["vertical_links"]}
    return region_ids, object_ids, link_ids


def validate_goal_task_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    spec = _mapping(spec, "goal task spec")
    version = spec.get("version")
    expected_fields = (_PUBLIC_TASK_FIELDS_V3
                       if version == GOAL_DEPLOYMENT_CONTRACT_VERSION
                       else _PUBLIC_TASK_FIELDS)
    _check_exact(spec, expected_fields, "goal task spec")
    if (spec["schema"] != GOAL_TASK_SCHEMA
            or version not in {GOAL_CONTRACT_VERSION, GOAL_DEPLOYMENT_CONTRACT_VERSION}):
        raise ValueError("unsupported goal task schema")
    if version == GOAL_DEPLOYMENT_CONTRACT_VERSION:
        from .goal_deployment_assessment import validate_deployment_assessment_contract
        validate_deployment_assessment_contract(spec["deployment_assessment"])
    _string(spec["task_id"], "task id")
    _string(spec["evaluation_track"], "evaluation track")
    if spec["evaluation_track"] != "goal_driven_team_planning_v1":
        raise ValueError("goal task must use goal_driven_team_planning_v1")
    mission = _mapping(spec["mission"], "mission")
    _check_exact(mission, _MISSION_FIELDS, "mission")
    _string(mission["scene_key"], "mission scene key")
    _string(mission["goal"], "mission goal")
    _string(mission["context"], "mission context")
    catalog_version = spec["skill_catalog_version"]
    default_catalog = f"{SKILL_CATALOG_SCHEMA}.v{SKILL_CATALOG_VERSION}"
    # Cross-version fail-closed: a pinned City catalog serves only its own
    # frozen task ids, a pinned City task id must use exactly its own catalog,
    # and the default catalog serves every non-pinned task.
    admitted_ids = next(
        (ids for version, ids in _SKILL_CATALOG_TASK_ADMISSION.items()
         if catalog_version == f"{SKILL_CATALOG_SCHEMA}.v{version}"), None)
    if not ((catalog_version == default_catalog
             and spec["task_id"] not in _PINNED_CITY_TASK_IDS)
            or (admitted_ids is not None and mission["scene_key"] == "city"
                and spec["task_id"] in admitted_ids)):
        raise ValueError("goal task uses an unsupported skill catalog")
    # Capability and skill vocabulary follow the spec's own admitted catalog
    # revision, so a v4 City task may publish aerial_broadcast while the
    # frozen v2/v3 vocabularies stay exact.
    known_capabilities = set(skill_names_for_catalog(catalog_version))

    outcomes = spec["required_outcomes"]
    if not isinstance(outcomes, list) or len(outcomes) < 2:
        raise ValueError("goal task requires at least two business outcomes")
    outcome_ids: list[str] = []
    for index, raw in enumerate(outcomes):
        outcome = _mapping(raw, f"required outcome {index}")
        _check_exact(outcome, _OUTCOME_FIELDS, f"required outcome {index}")
        outcome_ids.append(_string(outcome["id"], f"required outcome {index} id"))
        _string(outcome["description"], f"required outcome {index} description")
        _string(outcome["acceptance"], f"required outcome {index} acceptance")
        expectations = _string_list(outcome["evidence_expectations"],
                                     f"required outcome {index} evidence_expectations")
        if not expectations:
            raise ValueError("required outcomes need public business evidence expectations")
        if set(outcome) & _OUTCOME_FORBIDDEN_KEYS:
            raise ValueError(f"required outcome {outcome['id']} contains execution fields")
    if len(outcome_ids) != len(set(outcome_ids)):
        raise ValueError("required outcome ids must be unique")

    pool = spec["candidate_robot_pool"]
    if not isinstance(pool, list) or not pool:
        raise ValueError("goal task requires a nonempty candidate robot pool")
    agent_ids: list[str] = []
    for index, raw in enumerate(pool):
        robot = _mapping(raw, f"candidate robot {index}")
        _check_exact(robot, _ROBOT_FIELDS, f"candidate robot {index}")
        agent_ids.append(_string(robot["id"], f"candidate robot {index} id"))
        if "reserve" in robot["id"].lower():
            raise ValueError("candidate robot pool must not expose reserve identity")
        _string(robot["type"], f"candidate robot {index} type")
        _string(robot["mobility"], f"candidate robot {index} mobility")
        capabilities = _string_list(robot["public_capabilities"],
                                     f"candidate robot {index} public_capabilities")
        unknown = set(capabilities) - known_capabilities
        if unknown:
            raise ValueError(f"candidate robot {index} has unknown public capabilities {sorted(unknown)}")
        _string_list(robot["interfaces"], f"candidate robot {index} interfaces", allow_empty=True)
    if len(agent_ids) != len(set(agent_ids)):
        raise ValueError("candidate robot ids must be unique")

    world = _mapping(spec["public_world"], "public world")
    _WORLD_OPTIONAL = {"robot_spawn_positions"}
    missing = (_WORLD_FIELDS - _WORLD_OPTIONAL) - set(world)
    extra = set(world) - _WORLD_FIELDS
    if missing or extra:
        raise ValueError(f"public world fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
    _string(world["public_notes"], "public world notes")
    spawns = world.get("robot_spawn_positions")
    if spawns is not None:
        # Optional author-frozen spawn map (public data: the participant sees
        # where every candidate robot starts, so team selection can reason
        # about distance; only finite 2D/3D coordinates are accepted).
        if not isinstance(spawns, Mapping) or not spawns:
            raise ValueError("robot_spawn_positions must be a non-empty object")
        pool_ids = set(agent_ids)
        spawn_ids = set(spawns)
        if spawn_ids != pool_ids:
            raise ValueError(
                "robot_spawn_positions must cover exactly the candidate robot pool"
            )
        for agent_id, position in spawns.items():
            _string(agent_id, "spawn robot id")
            if (not isinstance(position, (list, tuple)) or len(position) not in (2, 3)
                    or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                           or not math.isfinite(v) for v in position)):
                raise ValueError(f"robot_spawn_positions[{agent_id}] must be finite 2D/3D")
    region_ids: list[str] = []
    for index, raw in enumerate(world["regions"]):
        region = _mapping(raw, f"public region {index}")
        _check_exact(region, _REGION_FIELDS, f"public region {index}")
        region_ids.append(_string(region["id"], f"public region {index} id"))
        _string(region["kind"], f"public region {index} kind")
        _string(region["public_description"], f"public region {index} description")
        _string_list(region["access_modes"], f"public region {index} access_modes")
    if len(region_ids) != len(set(region_ids)):
        raise ValueError("public region ids must be unique")
    object_ids: list[str] = []
    for index, raw in enumerate(world["objects"]):
        obj = _mapping(raw, f"public object {index}")
        _check_exact(obj, _OBJECT_FIELDS, f"public object {index}")
        object_ids.append(_string(obj["id"], f"public object {index} id"))
        _string(obj["category"], f"public object {index} category")
        _string(obj["public_role"], f"public object {index} public_role")
        _string_list(obj["handling_constraints"], f"public object {index} handling_constraints", allow_empty=True)
    if len(object_ids) != len(set(object_ids)):
        raise ValueError("public object ids must be unique")
    link_ids: list[str] = []
    for index, raw in enumerate(world["vertical_links"]):
        link = _mapping(raw, f"public vertical link {index}")
        _check_exact(link, _VERTICAL_FIELDS, f"public vertical link {index}")
        link_ids.append(_string(link["id"], f"public vertical link {index} id"))
        if link["from_region_id"] not in region_ids or link["to_region_id"] not in region_ids:
            raise ValueError(f"public vertical link {link['id']} references an unknown region")
        _string_list(link["traversal_modes"], f"public vertical link {index} traversal_modes")
        _string(link["public_description"], f"public vertical link {index} description")
    if len(link_ids) != len(set(link_ids)):
        raise ValueError("public vertical link ids must be unique")

    constraints = spec["business_constraints"]
    if not isinstance(constraints, list) or not constraints or any(
            not isinstance(item, str) or not item for item in constraints):
        raise ValueError("business_constraints must be a nonempty string list")
    budgets = _mapping(spec["budgets"], "goal budgets")
    required_budgets = {
        "simulation_s", "planning_s", "message_bytes", "model_tokens", "max_active_agents",
    }
    if set(budgets) != required_budgets:
        raise ValueError("goal budgets must declare simulation, planning, message and team limits")
    for name in ("simulation_s", "planning_s", "message_bytes", "model_tokens"):
        if _nonnegative(budgets[name], name) <= 0:
            raise ValueError(f"{name} must be positive")
    if (isinstance(budgets["max_active_agents"], bool) or
            not isinstance(budgets["max_active_agents"], int) or budgets["max_active_agents"] < 1):
        raise ValueError("max_active_agents must be a positive integer")
    if budgets["max_active_agents"] > len(pool):
        raise ValueError("max_active_agents cannot exceed the public candidate pool")

    factor = _mapping(spec["experiment_factor"], "experiment factor")
    _check_exact(factor, _FACTOR_FIELDS, "experiment factor")
    _string(factor["id"], "experiment factor id")
    _string(factor["level"], "experiment factor level")
    if factor["id"] not in _FACTOR_LEVELS:
        raise ValueError("goal task uses an unsupported experiment factor")
    if factor["level"] not in _FACTOR_LEVELS[factor["id"]]:
        raise ValueError("goal task uses an unsupported factor level")
    _string(factor["public_rule"], "experiment factor public_rule")
    _string_list(factor["frozen_inputs"], "experiment factor frozen_inputs")

    forbidden = _find_keys(spec)
    if forbidden:
        raise ValueError(f"goal public task leaks private or v1 execution fields: {forbidden}")
    if any(key in _OUTCOME_FORBIDDEN_KEYS for outcome in outcomes for key in outcome):
        raise ValueError("required outcomes must remain business-level")
    if (version == GOAL_CONTRACT_VERSION
            and not {"aerial_navigation", "ground_navigation"} <= {
                capability for robot in pool for capability in robot["public_capabilities"]}):
        raise ValueError("goal task must expose heterogeneous aerial and ground capabilities")
    constraints_report = validate_constraint_model(spec)
    return {"task_id": spec["task_id"], "validated": True, "sha256": canonical_sha256(spec),
            "constraint_review": constraints_report}


def build_goal_participant_api(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    validate_goal_task_spec(task_spec)
    deployment_enabled = task_spec["version"] == GOAL_DEPLOYMENT_CONTRACT_VERSION
    action_fields = {**_ACTION_FIELDS,
                     **(_DEPLOYMENT_ACTION_FIELDS if deployment_enabled else {})}
    event_fields = {**_EVENT_FIELDS,
                    **(_DEPLOYMENT_EVENT_FIELDS if deployment_enabled else {})}
    api = {
        "schema": GOAL_API_SCHEMA,
        "version": task_spec["version"],
        "task_id": task_spec["task_id"],
        "public_task_sha256": canonical_sha256(task_spec),
        "skill_catalog_version": task_spec["skill_catalog_version"],
        "skill_catalog": public_skill_catalog(
            version=int(task_spec["skill_catalog_version"].rsplit(".v", 1)[1])),
        "plan_contract": {
            "schema": GOAL_PLAN_SCHEMA, "version": GOAL_PLAN_VERSION,
            "fields": ["plan_id", "assigned_team", "tasks"],
            "task_fields": ["task_id", "skill", "arguments", "prerequisites",
                            "required_capabilities", "assigned_agents", "evidence_refs", "execution"],
            "execution_fields": ["start_s", "duration_s"],
            "timing": "absolute simulation seconds; half-open windows; no start before submission",
            "runtime_state": (
                "Only the trusted runtime supplies current_agents, joined_agents, capability_updates "
                "(complete robot declarations), current_time_s, visible_events, robot_regions and object_states. "
                "Plans submitted after time zero require complete object and selected-robot position snapshots. "
                "Participants cannot override these through a plan or action."
            ),
        },
        "event_envelope": {"schema": GOAL_EVENT_SCHEMA, "version": GOAL_EVENT_VERSION,
                           "fields": ["sequence", "time_s", "type", "payload"]},
        "action_envelope": {"schema": GOAL_ACTION_SCHEMA, "version": GOAL_ACTION_VERSION,
                            "fields": ["action_id", "submitted_at_s", "type", "payload"]},
        "event_types": {name: {"payload_fields": sorted(fields)}
                        for name, fields in event_fields.items()},
        "action_types": {name: {"payload_fields": sorted(fields)}
                         for name, fields in action_fields.items()},
        "ordering": "visible event sequence is monotonic; action submission uses simulation time",
        "truth_boundary": (
            "Only current public world, current roster, delivered messages and observed events are visible. "
            "No phase eligibility, affected node, reference assignment, future schedule or consumer binding is exposed. "
            "Expired messages may be read as history but cannot support current plan or dispatch evidence."
        ),
        "implementation_status": {
            "contract_validation": "available",
            "plan_submission": "contract_only",
            "runtime_execution": "external_pending",
            "dynamic_team_events": "event_contract_only",
            "skill_dispatch": "contract_only",
            **({"deployment_assessment": "trusted_gate"}
               if deployment_enabled else {}),
        },
    }
    validate_goal_participant_api(api, task_spec=task_spec)
    return api


def validate_goal_participant_api(api: Mapping[str, Any], *, task_spec: Mapping[str, Any]) -> dict[str, Any]:
    validate_goal_task_spec(task_spec)
    api = _mapping(api, "goal participant API")
    expected = {"schema", "version", "task_id", "public_task_sha256", "skill_catalog_version",
                "event_envelope", "action_envelope", "event_types", "action_types", "ordering",
                "truth_boundary", "implementation_status", "skill_catalog", "plan_contract"}
    _check_exact(api, expected, "goal participant API")
    if api["schema"] != GOAL_API_SCHEMA or api["version"] != task_spec["version"]:
        raise ValueError("unsupported goal participant API schema")
    if api["task_id"] != task_spec["task_id"] or api["public_task_sha256"] != canonical_sha256(task_spec):
        raise ValueError("goal participant API is not bound to the public task")
    if api["skill_catalog_version"] != task_spec["skill_catalog_version"]:
        raise ValueError("goal participant API skill catalog differs from task")
    if api["skill_catalog"] != public_skill_catalog(
            version=int(task_spec["skill_catalog_version"].rsplit(".v", 1)[1])):
        raise ValueError("goal participant API must publish the complete current skill catalog")
    plan_contract = _mapping(api["plan_contract"], "plan contract")
    _check_exact(plan_contract, {"schema", "version", "fields", "task_fields", "execution_fields",
                                 "timing", "runtime_state"}, "plan contract")
    if (plan_contract.get("schema") != GOAL_PLAN_SCHEMA
            or plan_contract.get("version") != GOAL_PLAN_VERSION
            or set(plan_contract.get("fields", ())) != {"plan_id", "assigned_team", "tasks"}
            or set(plan_contract.get("execution_fields", ())) != {"start_s", "duration_s"}
            or set(plan_contract.get("task_fields", ())) != {
                "task_id", "skill", "arguments", "prerequisites", "required_capabilities",
                "assigned_agents", "evidence_refs", "execution"}):
        raise ValueError("goal participant API plan declaration is incomplete")
    _string(plan_contract["timing"], "plan timing")
    _string(plan_contract["runtime_state"], "plan runtime_state")
    envelopes = {
        "event_envelope": {"schema": GOAL_EVENT_SCHEMA, "version": GOAL_EVENT_VERSION,
                           "fields": ["sequence", "time_s", "type", "payload"]},
        "action_envelope": {"schema": GOAL_ACTION_SCHEMA, "version": GOAL_ACTION_VERSION,
                            "fields": ["action_id", "submitted_at_s", "type", "payload"]},
    }
    for name, expected_envelope in envelopes.items():
        if api[name] != expected_envelope:
            raise ValueError(f"goal participant API has an invalid {name}")
    expected_status = {
        "contract_validation": "available", "plan_submission": "contract_only",
        "runtime_execution": "external_pending", "dynamic_team_events": "event_contract_only",
        "skill_dispatch": "contract_only",
        **({"deployment_assessment": "trusted_gate"}
           if task_spec["version"] == GOAL_DEPLOYMENT_CONTRACT_VERSION else {}),
    }
    if api["implementation_status"] != expected_status:
        raise ValueError("goal participant API implementation_status differs from the available contract")
    deployment_enabled = task_spec["version"] == GOAL_DEPLOYMENT_CONTRACT_VERSION
    action_fields = {**_ACTION_FIELDS,
                     **(_DEPLOYMENT_ACTION_FIELDS if deployment_enabled else {})}
    event_fields = {**_EVENT_FIELDS,
                    **(_DEPLOYMENT_EVENT_FIELDS if deployment_enabled else {})}
    if set(api["action_types"]) != set(action_fields):
        raise ValueError("goal participant API must expose the complete goal action vocabulary")
    if set(api["event_types"]) != set(event_fields):
        raise ValueError("goal participant API must expose the complete event vocabulary")
    for name, fields in action_fields.items():
        declared = _mapping(api["action_types"][name], f"goal action {name}")
        if dict(declared) != {"payload_fields": sorted(fields)}:
            raise ValueError(f"goal action {name} has an invalid payload declaration")
    for name, fields in event_fields.items():
        declared = _mapping(api["event_types"][name], f"goal event {name}")
        if dict(declared) != {"payload_fields": sorted(fields)}:
            raise ValueError(f"goal event {name} has an invalid payload declaration")
    forbidden = _find_keys(api)
    if forbidden:
        raise ValueError(f"goal participant API leaks private fields: {forbidden}")
    return {"task_id": api["task_id"], "validated": True, "sha256": canonical_sha256(api)}


def _current_agents(task_spec: Mapping[str, Any], context: Mapping[str, Any] | None) -> set[str]:
    pool = {row["id"] for row in task_spec["candidate_robot_pool"]}
    if context is None or "current_agents" not in context:
        return pool
    agents = set(_string_list(list(context["current_agents"]), "current_agents", allow_empty=True))
    if not agents <= pool and not context.get("allow_joined_agents", False):
        raise ValueError("runtime roster contains an agent outside the public pool")
    return agents


def _spec_skill_names(task_spec: Mapping[str, Any]) -> set[str]:
    """Skill vocabulary of the task spec's own admitted catalog revision."""
    return set(skill_names_for_catalog(task_spec["skill_catalog_version"]))


def _validate_robot_declaration(value: Any, label: str,
                                known_skills: set[str] | None = None) -> Mapping[str, Any]:
    from .goal_constraints import validate_robot_profile

    item = _mapping(value, label)
    _check_exact(item, _ROBOT_FIELDS, label)
    _string(item["id"], f"{label} id")
    _string(item["type"], f"{label} type")
    capabilities = _string_list(item["public_capabilities"], f"{label} capabilities", allow_empty=True)
    allowed = (known_skills if known_skills is not None
               else set(public_skill_catalog()["skills"]))
    if not set(capabilities) <= allowed:
        raise ValueError(f"{label} contains an unknown public capability")
    _string_list(item["interfaces"], f"{label} interfaces", allow_empty=True)
    validate_robot_profile(item)
    return item


def _robot_rows(task_spec: Mapping[str, Any], context: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    rows = {row["id"]: row for row in task_spec["candidate_robot_pool"]}
    if context:
        known_skills = _spec_skill_names(task_spec)
        for row in context.get("joined_agents", ()):
            item = _validate_robot_declaration(row, "joined agent", known_skills)
            identifier = item["id"]
            if identifier in rows:
                raise ValueError("joined agent id collides with public candidate pool")
            rows[identifier] = item
        updated = set()
        for row in context.get("capability_updates", ()):
            item = _validate_robot_declaration(row, "capability update", known_skills)
            identifier = item["id"]
            if identifier not in rows or identifier in updated:
                raise ValueError("capability update requires a unique known agent")
            if any(item[key] != rows[identifier][key] for key in ("type", "mobility")):
                raise ValueError("capability update cannot change robot identity or mobility")
            rows[identifier] = item
            updated.add(identifier)
    return rows


def _validate_public_ref(key: str, value: Any, task_spec: Mapping[str, Any]) -> None:
    if not isinstance(value, str) or not value:
        return
    regions, objects, links = _public_ids(task_spec)
    if key in {"region_id", "pickup_region_id", "placement_region_id", "receiving_region_id", "destination_region_id", "exit_region_id"}:
        if value not in regions:
            raise ValueError(f"unknown public region reference: {value}")
    elif key == "object_id" or key == "subject_ref":
        if value not in objects and value not in regions:
            raise ValueError(f"unknown public object or region reference: {value}")
    elif key == "link_id" and value not in links:
        raise ValueError(f"unknown public vertical link reference: {value}")


def _validate_skill_access(skill: str, arguments: Mapping[str, Any], assigned: list[str],
                           rows: Mapping[str, Mapping[str, Any]],
                           task_spec: Mapping[str, Any], task_id: str) -> None:
    """Structural validation only.

    User principle (2026-09-22): interface, mobility-skill and
    region-accessibility eligibility are physical realities of the world,
    not protocol rules - an algorithm assigning an unsuited agent must be
    measurable as an execution failure, never filtered at submission. The
    plan protocol therefore validates only that a referenced region exists;
    every physical-eligibility consequence moved to the execution layer.
    """
    del skill, assigned, rows
    region_id = next((arguments.get(key) for key in (
        "region_id", "pickup_region_id", "placement_region_id", "receiving_region_id",
        "destination_region_id", "exit_region_id") if arguments.get(key) is not None), None)
    if region_id is None:
        return
    if not any(row["id"] == region_id for row in task_spec["public_world"]["regions"]):
        raise ValueError(f"{task_id}: unknown region {region_id}")


def _validate_task_id_graph(tasks: list[Mapping[str, Any]]) -> None:
    ids = [task["task_id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("plan task ids must be unique")
    known = set(ids)
    pending = {task["task_id"]: set(task["prerequisites"]) for task in tasks}
    if any(dep not in known for deps in pending.values() for dep in deps):
        raise ValueError("plan prerequisites may reference only task ids in this plan")
    done: set[str] = set()
    while pending:
        ready = sorted(task_id for task_id, deps in pending.items() if deps <= done)
        if not ready:
            raise ValueError("plan prerequisites contain a cycle")
        for task_id in ready:
            pending.pop(task_id)
            done.add(task_id)


def semantic_goal_plan_signature(plan: Mapping[str, Any]) -> str:
    """Hash plan semantics while ignoring author-selected plan and task IDs."""
    plan = _mapping(plan, "goal plan")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("goal plan requires at least one task")
    task_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(tasks):
        task = _mapping(raw, f"plan task {index}")
        task_id = _string(task.get("task_id"), f"plan task {index} id")
        if task_id in task_by_id:
            raise ValueError("plan task ids must be unique")
        task_by_id[task_id] = task
    _validate_task_id_graph(list(task_by_id.values()))

    successors = {task_id: set() for task_id in task_by_id}
    for task_id, task in task_by_id.items():
        for prerequisite in task["prerequisites"]:
            successors[prerequisite].add(task_id)

    labels = {
        task_id: canonical_sha256({
            "skill": task["skill"],
            "arguments": task["arguments"],
            "required_capabilities": sorted(task["required_capabilities"]),
            "assigned_agents": sorted(task["assigned_agents"]),
        })
        for task_id, task in task_by_id.items()
    }
    colors = dict(labels)
    for _ in range(len(task_by_id)):
        colors = {
            task_id: canonical_sha256({
                "label": labels[task_id],
                "predecessors": sorted(colors[item] for item in task["prerequisites"]),
                "successors": sorted(colors[item] for item in successors[task_id]),
            })
            for task_id, task in task_by_id.items()
        }
    edges = sorted(
        (colors[prerequisite], colors[task_id])
        for task_id, task in task_by_id.items()
        for prerequisite in task["prerequisites"]
    )
    return canonical_sha256({
        "assigned_team": sorted(plan.get("assigned_team", [])),
        "node_colors": sorted(colors.values()),
        "edges": edges,
    })


def _validate_carrier_binding_structure(task_id: str, arguments: Mapping[str, Any],
                                        task_spec: Mapping[str, Any],
                                        assigned: list[str]) -> None:
    """Structural carrier-binding consistency shared by both admission modes.

    Checks references and set equality only: the handling mode and its slot
    points exist, bindings carry distinct agents and points, the bound robot
    set equals the task's assigned agents, and the bound points equal the
    mode's slots. Payload, reach and mobility matching stay in the full
    constraint model and the execution layer.
    """
    object_id = arguments.get("object_id")
    objects = {row["id"]: row for row in task_spec["public_world"]["objects"]}
    obj = objects.get(object_id) if isinstance(object_id, str) else None
    if obj is None:
        raise ValueError(f"{task_id}: carrier_bindings need a known object_id")
    modes = {mode["id"]: mode
             for mode in obj["physical_properties"]["handling_modes"]}
    mode = modes.get(arguments.get("handling_mode_id"))
    if mode is None:
        raise ValueError(f"{task_id}: unknown handling_mode_id")
    raw = arguments["carrier_bindings"]
    if not isinstance(raw, list) or len(raw) != len(mode["slots"]):
        raise ValueError(f"{task_id}: carrier_bindings must match handling slots")
    bound_agents: list[str] = []
    bound_points: list[str] = []
    for binding in raw:
        if not isinstance(binding, Mapping) or set(binding) != {"agent_id", "point_id"}:
            raise ValueError(f"{task_id}: carrier binding fields are invalid")
        bound_agents.append(_string(binding["agent_id"], f"{task_id} carrier agent"))
        bound_points.append(_string(binding["point_id"], f"{task_id} carrier point"))
    if len(set(bound_agents)) != len(bound_agents) or len(set(bound_points)) != len(bound_points):
        raise ValueError(f"{task_id}: carrier bindings need distinct agents and points")
    if (set(bound_agents) != set(assigned)
            or set(bound_points) != {slot["point_id"] for slot in mode["slots"]}):
        raise ValueError(
            f"{task_id}: carrier bindings must match assigned agents and mode points")


def validate_goal_plan(plan: Mapping[str, Any], *, task_spec: Mapping[str, Any],
                       context: Mapping[str, Any] | None = None,
                       allow_physical_infeasibility: bool = False,
                       admission: str = "full") -> dict[str, Any]:
    validate_goal_task_spec(task_spec)
    plan = _mapping(plan, "goal plan")
    expected = {"schema", "version", "plan_id", "assigned_team", "tasks"}
    _check_exact(plan, expected, "goal plan")
    if plan["schema"] != GOAL_PLAN_SCHEMA or plan["version"] != GOAL_PLAN_VERSION:
        raise ValueError("unsupported goal plan schema")
    if admission not in {"full", "interface"}:
        raise ValueError("plan admission must be full or interface")
    _string(plan["plan_id"], "plan id")
    agents = _current_agents(task_spec, context)
    team = _string_list(plan["assigned_team"], "plan assigned_team")
    if not set(team) <= agents:
        raise ValueError("plan assigned_team contains an unavailable agent")
    if admission != "interface" and len(team) > task_spec["budgets"]["max_active_agents"]:
        raise ValueError("plan assigned_team exceeds the public team budget")
    tasks = plan["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("goal plan requires at least one task")
    rows = _robot_rows(task_spec, context)
    if not agents <= set(rows):
        raise ValueError("runtime roster has agents without complete public declarations")
    task_rows: list[dict[str, Any]] = []
    expected_task_fields = {"task_id", "skill", "arguments", "prerequisites",
                            "required_capabilities", "assigned_agents", "evidence_refs", "execution"}
    for index, raw in enumerate(tasks):
        task = _mapping(raw, f"plan task {index}")
        _check_exact(task, expected_task_fields, f"plan task {index}")
        task_id = _string(task["task_id"], f"plan task {index} id")
        skill = validate_skill_name(task["skill"])
        # Structural vocabulary check (both admission modes): the named skill
        # must be published by this task's own admitted catalog revision.
        # Full mode already fails such plans through the constraint model's
        # duration table; interface mode has no later net, so it needs it here.
        if skill not in _spec_skill_names(task_spec):
            raise ValueError(f"{task_id}: unknown skill {skill} for the task skill catalog")
        arguments = validate_skill_arguments(skill, task["arguments"])
        for key, value in arguments.items():
            _validate_public_ref(key, value, task_spec)
        prerequisites = _string_list(task["prerequisites"], f"plan task {index} prerequisites", allow_empty=True)
        capabilities = _string_list(task["required_capabilities"], f"plan task {index} required_capabilities")
        required = set(required_capabilities(skill))
        if not required <= set(capabilities):
            raise ValueError(f"{task_id}: declared required_capabilities omit {sorted(required)}")
        if not set(capabilities) <= _spec_skill_names(task_spec):
            raise ValueError(f"{task_id}: unknown required capability")
        required = set(capabilities)
        assigned = _string_list(task["assigned_agents"], f"plan task {index} assigned_agents")
        if not set(assigned) <= agents:
            raise ValueError(f"{task_id}: assigned agent is unavailable")
        if not set(assigned) <= set(team):
            raise ValueError(f"{task_id}: assigned agent is outside the selected plan team")
        if admission != "interface" and len(assigned) < minimum_agents(skill):
            raise ValueError(f"{task_id}: skill {skill} requires at least {minimum_agents(skill)} agents")
        if skill == "synchronize" and set(arguments["agent_ids"]) != set(assigned):
            raise ValueError(f"{task_id}: synchronize agent_ids must match assigned_agents")
        if "carrier_bindings" in arguments:
            # Structural binding consistency (both admission modes): the named
            # handling mode and its slot points must exist, and the bound
            # robot set must equal assigned_agents, so a plan can never
            # dispatch to robot A while the backend executes carrier B.
            # Payload, reach and mobility stay execution measurements.
            _validate_carrier_binding_structure(task_id, arguments, task_spec, assigned)
        # Deliberately NO capability-eligibility check on assigned agents
        # (user principle, 2026-09-22): assigning a task to an agent that
        # does not declare the required capabilities is an algorithm
        # decision the benchmark must not pre-filter - it surfaces as a
        # deterministic execution failure, and only the task/business
        # outcomes evaluate it. The protocol validates structure only.
        del required
        _validate_skill_access(skill, arguments, assigned, rows, task_spec, task_id)
        evidence_refs = _string_list(task["evidence_refs"], f"plan task {index} evidence_refs", allow_empty=True)
        task_rows.append({**dict(task), "arguments": arguments, "prerequisites": prerequisites,
                          "required_capabilities": capabilities, "assigned_agents": assigned,
                          "evidence_refs": evidence_refs})
    _validate_task_id_graph(task_rows)
    visible = context.get("visible_events", []) if context else []
    _validate_evidence_refs(task_rows, visible, context.get("current_time_s") if context else None)
    forbidden = _find_keys(plan)
    if forbidden:
        raise ValueError(f"goal plan contains forbidden private fields: {forbidden}")
    if context and _nonnegative(context.get("current_time_s", 0), "current_time_s") > 0:
        object_states = _mapping(context.get("object_states"), "runtime object_states snapshot")
        robot_regions = _mapping(context.get("robot_regions"), "runtime robot_regions snapshot")
        object_ids = {obj["id"] for obj in task_spec["public_world"]["objects"]}
        if set(object_states) != object_ids or not set(team) <= set(robot_regions):
            raise ValueError("runtime plan requires complete current object and selected-robot position snapshots")
    if admission == "interface":
        # Second-mission measurement: schema and robot/skill references only.
        # Capability, mobility and region matching stay execution results.
        constraints_report = {
            "validated": True, "scope": "interface", "execution_validated": False,
            "admission": "interface",
        }
    else:
        constraints_report = validate_plan_constraints(
            plan, task_spec=task_spec, robot_rows=rows, context=context,
            allow_physical_infeasibility=allow_physical_infeasibility)
    return {"plan_id": plan["plan_id"], "validated": True,
            "task_ids": [task["task_id"] for task in task_rows],
            "sha256": canonical_sha256(plan),
            "semantic_sha256": semantic_goal_plan_signature(plan),
            "constraint_review": constraints_report}


# Second missions keep an aerial candidate in the pool because the goal
# contract requires both aerial and ground capabilities, but their jobs do
# not use that aircraft.  Exempting these ids leaves every GOAL-01
# certificate on the original cross-mobility path.
_GROUND_ONLY_AUTHORING_TASK_IDS = frozenset({
    "FACTORY-FIRE-01",
    "WAREHOUSE-INBOUND-01",
    "MODERN_HOUSE-DELIVERY-01",
    "CITY-EXIT-01",
})


def _authoring_omits_forced_aerial(task_spec: Mapping[str, Any]) -> bool:
    if task_spec["mission"]["scene_key"] == "restaurant":
        return True
    return task_spec["task_id"] in _GROUND_ONLY_AUTHORING_TASK_IDS


def build_goal_heterogeneity_certificate(
        task_spec: Mapping[str, Any], witnesses: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Report witness sensitivity and independent local capability supply."""
    from .goal_capability_audit import build_capability_audit
    validate_goal_task_spec(task_spec)
    witness_rows = list(witnesses)
    if len(witness_rows) < 2:
        raise ValueError("heterogeneity certificate requires at least two witnesses")
    mobility_by_agent = {
        row["id"]: row["mobility"] for row in task_spec["candidate_robot_pool"]
    }
    outcome_ids = {row["id"] for row in task_spec["required_outcomes"]}
    certificate_rows = []
    for witness in witness_rows:
        witness = _mapping(witness, "goal witness")
        witness_id = _string(witness.get("id"), "goal witness id")
        plan = _mapping(witness.get("plan"), "goal witness plan")
        validate_goal_plan(plan, task_spec=task_spec)
        support = _mapping(witness.get("outcome_support"), "goal witness outcome_support")
        if set(support) != outcome_ids:
            raise ValueError("heterogeneity certificate requires complete outcome support")
        task_by_id = {task["task_id"]: task for task in plan["tasks"]}

        cross_edges = []
        for task_id, task in task_by_id.items():
            destination = sorted({mobility_by_agent[agent] for agent in task["assigned_agents"]})
            for prerequisite in task["prerequisites"]:
                source = sorted({
                    mobility_by_agent[agent]
                    for agent in task_by_id[prerequisite]["assigned_agents"]
                })
                if {"aerial", "ground"} <= set(source) | set(destination) and source != destination:
                    cross_edges.append({
                        "prerequisite_task_id": prerequisite,
                        "dependent_task_id": task_id,
                        "from_mobility": source,
                        "to_mobility": destination,
                    })
        # Restaurant opening readiness, and the second-mission drafts below,
        # are ground-only authoring witnesses.  Requiring an Iris step only to
        # satisfy this generic certificate would manufacture an aerial
        # dependency the job does not have.  GOAL-01 packages are not in the
        # exemption set, so their certificates stay on the cross-mobility path.
        if not cross_edges and not _authoring_omits_forced_aerial(task_spec):
            raise ValueError(f"witness {witness_id} has no cross-mobility dependency")

        counterfactuals = []
        for removed_mobility in ("aerial", "ground"):
            invalid_tasks = {
                task_id for task_id, task in task_by_id.items()
                if any(mobility_by_agent[agent] == removed_mobility
                       for agent in task["assigned_agents"])
            }
            changed = True
            while changed:
                changed = False
                for task_id, task in task_by_id.items():
                    if task_id not in invalid_tasks and set(task["prerequisites"]) & invalid_tasks:
                        invalid_tasks.add(task_id)
                        changed = True
            invalid_outcomes = sorted(
                outcome_id for outcome_id, refs in support.items()
                if set(refs) & invalid_tasks
            )
            if not invalid_outcomes and not _authoring_omits_forced_aerial(task_spec):
                raise ValueError(
                    f"witness {witness_id} remains outcome-complete without {removed_mobility} members")
            counterfactuals.append({
                "removed_mobility": removed_mobility,
                "invalidated_outcome_ids": invalid_outcomes,
                **({"status": "not_in_witness_team"}
                   if not invalid_tasks and _authoring_omits_forced_aerial(task_spec)
                   else {}),
            })
        certificate_rows.append({
            "witness_id": witness_id,
            "cross_mobility_edges": cross_edges,
            "counterfactuals": counterfactuals,
        })
    return {
        "schema": GOAL_HETEROGENEITY_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "visibility": "author_evaluator_only",
        "basis": ("public_capability_declarations_and_witness_dependency_graphs"
                  if not _authoring_omits_forced_aerial(task_spec)
                  else "authoring_pending_business_heterogeneity_review"),
        "counterfactual_scope": ("fixed_witness_sensitivity_not_task_necessity"
                                  if not _authoring_omits_forced_aerial(task_spec)
                                  else "ground_only_authoring_witness_no_forced_aerial_dependency"),
        "witnesses": certificate_rows,
        "capability_audit": build_capability_audit(task_spec),
        "declaration_level_validated": not _authoring_omits_forced_aerial(task_spec),
        "whole_task_necessity_validated": False,
        "physical_necessity_validated": False,
        "execution_validated": False,
    }


def validate_goal_heterogeneity_certificate(
        certificate: Mapping[str, Any], *, task_spec: Mapping[str, Any],
        witnesses: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    certificate = _mapping(certificate, "goal heterogeneity certificate")
    expected = build_goal_heterogeneity_certificate(task_spec, witnesses)
    if dict(certificate) != expected:
        raise ValueError("goal heterogeneity certificate does not match the witness plans")
    return {"validated": True, "sha256": canonical_sha256(certificate),
            "physical_necessity_validated": False}


def _visible_event_index(events: Iterable[Mapping[str, Any]]) -> tuple[set[str], set[int]]:
    event_ids: set[str] = set()
    sequences: set[int] = set()
    for event in events:
        payload = event.get("payload", {})
        if isinstance(payload, Mapping) and isinstance(payload.get("event_id"), str):
            event_ids.add(payload["event_id"])
        if isinstance(payload, Mapping) and isinstance(payload.get("message_id"), str):
            if payload["message_id"] in event_ids:
                raise ValueError("visible message ids must be unique")
            event_ids.add(payload["message_id"])
        if isinstance(event.get("sequence"), int) and not isinstance(event.get("sequence"), bool):
            if event["sequence"] in sequences:
                raise ValueError("visible event sequences must be unique")
            sequences.add(event["sequence"])
    return event_ids, sequences


def _validate_evidence_refs(tasks: Iterable[Mapping[str, Any]], events: Iterable[Mapping[str, Any]],
                            current_time_s: Any = None) -> None:
    tasks = list(tasks)
    event_rows = list(events)
    event_ids, _ = _visible_event_index(event_rows)
    for task in tasks:
        for ref in task["evidence_refs"]:
            if ref not in event_ids:
                raise ValueError(f"evidence reference is not a currently visible event: {ref}")
    if current_time_s is not None:
        now = _nonnegative(current_time_s, "current_time_s")
        for event in event_rows:
            if "time_s" in event and _nonnegative(event["time_s"], "visible event time") > now:
                raise ValueError("plan context contains a future event")
        referenced = {ref for task in tasks for ref in task["evidence_refs"]}
        for event in event_rows:
            payload = event.get("payload", {})
            if isinstance(payload, Mapping) and payload.get("message_id") in referenced:
                if "valid_until_s" in payload and now >= _nonnegative(payload["valid_until_s"], "evidence expiry"):
                    raise ValueError("evidence reference is expired at the current simulation time")


def validate_goal_event(event: Mapping[str, Any], *, api: Mapping[str, Any],
                        task_spec: Mapping[str, Any], current_time_s: Any | None = None) -> dict[str, Any]:
    validate_goal_participant_api(api, task_spec=task_spec)
    event = _mapping(event, "goal participant event")
    expected = {"schema", "version", "sequence", "time_s", "type", "payload"}
    _check_exact(event, expected, "goal participant event")
    if event["schema"] != "eai.benchmark.goal_event" or event["version"] != 1:
        raise ValueError("unsupported goal event schema")
    if (not isinstance(event["sequence"], int) or isinstance(event["sequence"], bool)
            or event["sequence"] < 0):
        raise ValueError("goal event sequence must be a nonnegative integer")
    event_time = _nonnegative(event["time_s"], "goal event time")
    if current_time_s is not None and event_time > _nonnegative(current_time_s, "current_time_s"):
        raise ValueError("future event cannot be delivered to the participant")
    event_type = event["type"]
    event_fields = {**_EVENT_FIELDS,
                    **(_DEPLOYMENT_EVENT_FIELDS
                       if task_spec["version"] == GOAL_DEPLOYMENT_CONTRACT_VERSION else {})}
    if event_type not in event_fields:
        raise ValueError("goal event type is not declared")
    payload = _mapping(event["payload"], "goal event payload")
    _check_exact(payload, event_fields[event_type], f"goal event {event_type}")
    if event_type == "observation":
        _string(payload["message_id"], "observation message id")
        _string(payload["producer_id"], "observation producer id")
        _string(payload["kind"], "observation kind")
        _mapping(payload["data"], "observation data")
        if _nonnegative(payload["valid_until_s"], "observation valid_until_s") < event_time:
            raise ValueError("observation expires before publication")
    elif event_type in {"member_join", "capability_change"}:
        _string(payload["agent_id"], f"{event_type} agent id")
        declaration = _validate_robot_declaration(
            payload["capability_declaration"], "capability declaration",
            _spec_skill_names(task_spec))
        if declaration["id"] != payload["agent_id"]:
            raise ValueError("capability event declaration id mismatch")
        if type(payload["capability_version"]) is not int or payload["capability_version"] < 1:
            raise ValueError("capability version must be a positive integer")
    elif event_type == "communication_status":
        _string(payload["status"], "communication status")
        if (isinstance(payload["available_bytes"], bool) or
                not isinstance(payload["available_bytes"], int) or payload["available_bytes"] < 0):
            raise ValueError("available_bytes must be a nonnegative integer")
    elif event_type == "action_result":
        _string(payload["action_id"], "action result action_id")
        _string(payload["status"], "action result status")
        _string(payload["reason"], "action result reason")
    elif event_type == "member_exit":
        _string(payload["agent_id"], "member_exit agent id")
    elif event_type == "deployment_assessment_required":
        _string(payload["assessment_id"], "assessment id")
        for name in ("assessment_epoch", "trigger_event_sequence", "roster_version"):
            if type(payload[name]) is not int or payload[name] < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        _string(payload["trigger_event_type"], "assessment trigger event type")
        agents = _string_list(payload["current_agent_ids"], "current assessment agents",
                              allow_empty=True)
        versions = _mapping(payload["capability_versions"], "assessment capability_versions")
        if set(versions) != set(agents) or any(
                type(version) is not int or version < 0 for version in versions.values()):
            raise ValueError("assessment capability_versions must cover current agents")
    elif event_type == "human_assistance_requested":
        _string(payload["assessment_id"], "assistance assessment id")
        _string(payload["decision_action_id"], "assistance decision action id")
        if type(payload["assessment_epoch"]) is not int or payload["assessment_epoch"] < 0:
            raise ValueError("assistance assessment_epoch must be nonnegative")
        _string_list(payload["reason_codes"], "assistance reason_codes")
        _string_list(payload["evidence_refs"], "assistance evidence_refs", allow_empty=True)
    elif event_type == "resource_status":
        _string(payload["resource_id"], "resource id")
        if type(payload["resource_version"]) is not int or payload["resource_version"] < 1:
            raise ValueError("resource_version must be a positive integer")
        _nonnegative(payload["available_capacity"], "available_capacity")
    elif event_type == "deployment_change":
        _string(payload["change_id"], "deployment change id")
        if type(payload["change_version"]) is not int or payload["change_version"] < 1:
            raise ValueError("change_version must be a positive integer")
        _mapping(payload["public_facts"], "deployment change public_facts")
    elif event_type in {"episode_started", "tick"}:
        _string(payload[next(iter(event_fields[event_type]))], f"{event_type} digest")
    forbidden = _find_keys(event)
    if forbidden:
        raise ValueError(f"goal event contains private fields: {forbidden}")
    return deepcopy(dict(event))


def validate_goal_action(action: Mapping[str, Any], *, api: Mapping[str, Any],
                         task_spec: Mapping[str, Any], context: Mapping[str, Any] | None = None,
                         allow_physical_infeasibility: bool = False,
                         admission: str = "full") -> dict[str, Any]:
    validate_goal_participant_api(api, task_spec=task_spec)
    action = _mapping(action, "goal participant action")
    expected = {"schema", "version", "action_id", "submitted_at_s", "type", "payload"}
    _check_exact(action, expected, "goal participant action")
    if action["schema"] != GOAL_ACTION_SCHEMA or action["version"] != GOAL_ACTION_VERSION:
        raise ValueError("unsupported goal action schema")
    _string(action["action_id"], "goal action id")
    submitted_at = _nonnegative(action["submitted_at_s"], "goal action submission time")
    if context and "current_time_s" in context:
        if submitted_at != _nonnegative(context["current_time_s"], "current_time_s"):
            raise ValueError("action submission time must match current simulation time")
    context = {**dict(context or {}), "current_time_s": submitted_at}
    action_type = action["type"]
    action_fields = {**_ACTION_FIELDS,
                     **(_DEPLOYMENT_ACTION_FIELDS
                        if task_spec["version"] == GOAL_DEPLOYMENT_CONTRACT_VERSION else {})}
    if action_type not in action_fields:
        raise ValueError("goal action type is not declared")
    payload = _mapping(action["payload"], "goal action payload")
    _check_exact(payload, action_fields[action_type], f"goal action {action_type}")
    current_agents = _current_agents(task_spec, context)
    if action_type == "select_team":
        agents = _string_list(payload["agent_ids"], "selected team")
        if not set(agents) <= current_agents:
            raise ValueError("selected team contains an unavailable agent")
        if admission != "interface" and len(agents) > task_spec["budgets"]["max_active_agents"]:
            raise ValueError("selected team exceeds the public team budget")
    elif action_type in {"submit_plan", "revise_plan"}:
        if action_type == "revise_plan":
            sequence = payload["trigger_event_sequence"]
            if (not isinstance(sequence, list) or not sequence or
                    any(type(item) is not int or item < 0 for item in sequence)):
                raise ValueError("revise_plan requires a nonempty event sequence")
            _, visible_sequences = _visible_event_index(context.get("visible_events", []) if context else [])
            if not set(sequence) <= visible_sequences:
                raise ValueError("revise_plan references an event that is not currently visible")
            _string(payload["replaced_plan_id"], "replaced_plan_id")
            _string(payload["reason"], "revision reason")
            known_plans = set(context.get("known_plan_ids", [])) if context else set()
            if payload["replaced_plan_id"] not in known_plans:
                raise ValueError("revise_plan must replace a known submitted plan")
        plan_context = dict(context or {})
        plan_context["current_time_s"] = submitted_at
        validate_goal_plan(
            payload["plan"], task_spec=task_spec, context=plan_context,
            allow_physical_infeasibility=allow_physical_infeasibility,
            admission=admission)
    elif action_type == "dispatch_skill":
        _string(payload["task_id"], "dispatch task_id")
        refs = _string_list(payload["evidence_refs"], "dispatch evidence_refs", allow_empty=True)
        _validate_evidence_refs([{"evidence_refs": refs}], context.get("visible_events", []) if context else [],
                                context.get("current_time_s") if context else None)
        known_tasks = set(context.get("known_task_ids", [])) if context else set()
        if payload["task_id"] not in known_tasks:
            raise ValueError("dispatch_skill must reference a task in a submitted plan")
        if payload["task_id"] in set(context.get("completed_task_ids", [])):
            raise ValueError("dispatch_skill cannot repeat a completed task")
    elif action_type == "cancel_skill":
        _string(payload["task_id"], "cancel task_id")
        _string(payload["reason"], "cancel reason")
        active = set(context.get("active_task_ids", [])) if context else set()
        if payload["task_id"] not in active:
            raise ValueError("cancel_skill may only cancel an active task")
    elif action_type == "consume_message":
        _string(payload["message_id"], "message id")
        visible_ids, _ = _visible_event_index(context.get("visible_events", []) if context else [])
        if payload["message_id"] not in visible_ids:
            raise ValueError("consume_message may only consume a delivered visible message")
        consumed = set(context.get("consumed_message_ids", [])) if context else set()
        if payload["message_id"] in consumed:
            raise ValueError("message has already been consumed by this participant")
    elif action_type == "deployment_assessment":
        from .goal_deployment_assessment import DEPLOYMENT_DECISIONS, DEPLOYMENT_REASON_CODES
        _string(payload["assessment_id"], "deployment assessment id")
        for name in ("assessment_epoch", "roster_version"):
            if type(payload[name]) is not int or payload[name] < 0:
                raise ValueError(f"deployment {name} must be a nonnegative integer")
        versions = _mapping(payload["capability_versions"], "deployment capability_versions")
        if any(type(value) is not int or value < 0 for value in versions.values()):
            raise ValueError("deployment capability_versions must be nonnegative integers")
        if payload["decision"] not in DEPLOYMENT_DECISIONS:
            raise ValueError("deployment decision must be deployable or not_deployable")
        reasons = _string_list(payload["reason_codes"], "deployment reason_codes",
                               allow_empty=True)
        if not set(reasons) <= set(DEPLOYMENT_REASON_CODES):
            raise ValueError("deployment reason_codes contain an unknown declaration")
        if payload["decision"] == "not_deployable" and not reasons:
            raise ValueError("not_deployable requires at least one reason_code")
        if payload["decision"] == "deployable" and reasons:
            raise ValueError("deployable cannot carry not_deployable reason_codes")
        refs = _string_list(payload["evidence_refs"], "deployment evidence_refs",
                            allow_empty=True)
        visible_events = context.get("visible_events", [])
        public_refs = set(current_agents)
        public_refs |= {row["id"] for row in task_spec["candidate_robot_pool"]}
        public_refs |= {
            event["payload"]["agent_id"]
            for event in visible_events
            if (event.get("type") in {"member_join", "member_exit", "capability_change"}
                and isinstance(event.get("payload"), Mapping)
                and isinstance(event["payload"].get("agent_id"), str))
        }
        regions, objects, links = _public_ids(task_spec)
        public_refs |= regions | objects | links
        public_refs |= {row["id"] for row in task_spec["required_outcomes"]}
        visible_ids, _ = _visible_event_index(visible_events)
        public_refs |= visible_ids
        if not set(refs) <= public_refs:
            raise ValueError("deployment evidence_refs must name public entities or messages")
    forbidden = _find_keys(action)
    if forbidden:
        raise ValueError(f"goal action contains private fields: {forbidden}")
    return deepcopy(dict(action))


def _validate_hidden_event_schedule(schedule: Mapping[str, Any], *, task_spec: Mapping[str, Any]) -> None:
    schedule = _mapping(schedule, "hidden event schedule")
    expected = {"visibility", "factor_id", "factor_level", "decision_window",
                "communication_events", "team_events"}
    _check_exact(schedule, expected, "hidden event schedule")
    if schedule["visibility"] != "author_evaluator_only":
        raise ValueError("hidden event schedule must remain author/evaluator-only")
    factor = task_spec["experiment_factor"]
    if schedule["factor_id"] != factor["id"] or schedule["factor_level"] != factor["level"]:
        raise ValueError("hidden event schedule does not match the public factor selection")
    window = _mapping(schedule["decision_window"], "hidden decision window")
    _check_exact(window, {"opens_after", "closes_before"}, "hidden decision window")
    _string(window["opens_after"], "hidden decision window opens_after")
    _string(window["closes_before"], "hidden decision window closes_before")

    communication_events = schedule["communication_events"]
    if not isinstance(communication_events, list) or len(communication_events) != 1:
        raise ValueError("hidden schedule requires one concrete communication treatment")
    communication = _mapping(communication_events[0], "communication treatment")
    communication_fields = {"id", "trigger", "operation", "delay_s", "drop",
                            "bytes_per_window", "window_s", "publication_validity_s"}
    _check_exact(communication, communication_fields, "communication treatment")
    _string(communication["id"], "communication treatment id")
    _string(communication["trigger"], "communication treatment trigger")
    if communication["operation"] not in {"deliver", "drop"}:
        raise ValueError("communication treatment operation must be deliver or drop")
    delay = _nonnegative(communication["delay_s"], "communication delay_s")
    window_s = _nonnegative(communication["window_s"], "communication window_s")
    validity_s = _nonnegative(
        communication["publication_validity_s"], "communication publication_validity_s")
    if window_s <= 0 or validity_s <= 0:
        raise ValueError("communication treatment windows must be positive")
    byte_limit = communication["bytes_per_window"]
    if isinstance(byte_limit, bool) or not isinstance(byte_limit, int) or byte_limit <= 0:
        raise ValueError("communication bytes_per_window must be a positive integer")
    if not isinstance(communication["drop"], bool):
        raise ValueError("communication drop must be boolean")

    team_events = schedule["team_events"]
    if not isinstance(team_events, list):
        raise ValueError("hidden team treatment must be a list")
    team_fields = {"id", "trigger", "event_type", "target_selector", "agent_declaration",
                   "capability_patch", "capability_version"}
    pool_by_id = {row["id"]: row for row in task_spec["candidate_robot_pool"]}
    for raw in team_events:
        event = _mapping(raw, "team treatment")
        _check_exact(event, team_fields, "team treatment")
        _string(event["id"], "team treatment id")
        _string(event["trigger"], "team treatment trigger")
        if event["event_type"] not in {"member_exit", "member_join", "capability_change"}:
            raise ValueError("team treatment uses an unsupported event type")
        if type(event["capability_version"]) is not int or event["capability_version"] < 1:
            raise ValueError("team treatment capability_version must be a positive integer")
        selector = _mapping(event["target_selector"], "team treatment target_selector")
        declaration = event["agent_declaration"]
        patch = event["capability_patch"]
        if event["event_type"] == "member_join":
            _check_exact(selector, {"mode", "agent_id"}, "member_join target_selector")
            if selector["mode"] != "fixed_hidden_identity":
                raise ValueError("member_join treatment must use a fixed hidden identity")
            agent_id = _string(selector["agent_id"], "member_join agent id")
            if agent_id in pool_by_id:
                raise ValueError("member_join identity must remain outside the initial candidate pool")
            declaration = _validate_robot_declaration(
                declaration, "team treatment agent_declaration", _spec_skill_names(task_spec))
            _check_exact(declaration, _ROBOT_FIELDS, "team treatment agent_declaration")
            if declaration["id"] != agent_id:
                raise ValueError("team treatment declaration id mismatch")
            _string(declaration["type"], "team treatment robot type")
            if declaration["mobility"] not in {"ground", "aerial"}:
                raise ValueError("team treatment mobility must be ground or aerial")
            capabilities = _string_list(
                declaration["public_capabilities"], "team treatment capabilities")
            unknown = set(capabilities) - _spec_skill_names(task_spec)
            if unknown:
                raise ValueError(f"team treatment has unknown capabilities {sorted(unknown)}")
            _string_list(declaration["interfaces"], "team treatment interfaces", allow_empty=True)
            if patch is not None:
                raise ValueError("member_join treatment must not include a capability patch")
        else:
            _check_exact(
                selector, {"mode", "required_capability", "tie_break"},
                "selected member target_selector")
            if (selector["mode"] != "selected_active_capability_member"
                    or selector["tie_break"] != "lowest_agent_id"):
                raise ValueError("team treatment must deterministically target a selected active member")
            capability = _string(
                selector["required_capability"], "team treatment required capability")
            eligible = [
                row for row in pool_by_id.values()
                if capability in row["public_capabilities"]
            ]
            if len(eligible) < 2:
                raise ValueError("team treatment requires a public backup for the targeted capability")
            if declaration is not None:
                raise ValueError("selected-member treatment resolves its declaration at runtime")
            if event["event_type"] == "member_exit":
                if patch is not None:
                    raise ValueError("member_exit treatment must not include a capability patch")
            else:
                patch = _mapping(patch, "capability change patch")
                _check_exact(patch, {"remove", "add"}, "capability change patch")
                removed = _string_list(patch["remove"], "capability change removals")
                added = _string_list(patch["add"], "capability change additions", allow_empty=True)
                if capability not in removed or set(removed) & set(added):
                    raise ValueError("capability change must remove the targeted capability")

    factor_id, level = factor["id"], factor["level"]
    if factor_id == "communication_quality":
        if team_events:
            raise ValueError("communication factor must freeze the team treatment")
        if level == "immediate" and (
                delay != 0 or communication["drop"] or communication["operation"] != "deliver"):
            raise ValueError("immediate communication treatment is inconsistent")
        if level == "delayed" and (
                delay <= 0 or communication["drop"] or delay >= validity_s
                or communication["operation"] != "deliver"):
            raise ValueError("delayed communication treatment is inconsistent")
        if level == "dropped" and (
                not communication["drop"] or communication["operation"] != "drop"):
            raise ValueError("dropped communication treatment is inconsistent")
        if level == "bandwidth_limited" and (
                communication["drop"] or communication["operation"] != "deliver"
                or byte_limit >= task_spec["budgets"]["message_bytes"]):
            raise ValueError("bandwidth-limited treatment must reduce the public message budget")
        if level == "stale" and (
                communication["drop"] or communication["operation"] != "deliver"
                or delay < validity_s):
            raise ValueError("stale treatment must deliver after its validity window")
    else:
        if delay != 0 or communication["drop"] or communication["operation"] != "deliver":
            raise ValueError("team-continuity trials must freeze communication at immediate delivery")
        if level == "stable" and team_events:
            raise ValueError("stable team treatment must not schedule a lifecycle event")
        if level != "stable":
            if len(team_events) != 1 or team_events[0]["event_type"] != level:
                raise ValueError("team treatment does not implement the selected level")


def validate_goal_hidden_evaluator(evaluator: Mapping[str, Any], *, task_spec: Mapping[str, Any]) -> dict[str, Any]:
    validate_goal_task_spec(task_spec)
    evaluator = _mapping(evaluator, "goal hidden evaluator")
    required = {"schema", "version", "task_id", "public_task_sha256", "outcome_predicates",
                "witness_plans", "heterogeneity_certificate", "hidden_event_schedule",
                "factor_effect", "truth_sources", "measurement_contract",
                "measurement_status", "mechanism_template", "team_necessity_certificate",
                "release_gate"}
    _check_exact(evaluator, required, "goal hidden evaluator")
    if evaluator["schema"] != GOAL_HIDDEN_EVALUATOR_SCHEMA or evaluator["version"] != GOAL_CONTRACT_VERSION:
        raise ValueError("unsupported goal hidden evaluator schema")
    if evaluator["task_id"] != task_spec["task_id"] or evaluator["public_task_sha256"] != canonical_sha256(task_spec):
        raise ValueError("goal hidden evaluator is not bound to the public task")
    outcome_ids = {item["id"] for item in task_spec["required_outcomes"]}
    predicates = evaluator["outcome_predicates"]
    if (not isinstance(predicates, list) or len(predicates) != len(outcome_ids)
            or any(not isinstance(item, Mapping) for item in predicates)
            or {item.get("id") for item in predicates} != outcome_ids):
        raise ValueError("hidden evaluator must define one predicate per required outcome")
    for predicate in predicates:
        _string(predicate.get("id"), "outcome predicate id")
        _string_list(predicate.get("evidence_types"), "outcome predicate evidence_types")
        _string(predicate.get("predicate"), "outcome predicate")
        _string_list(predicate.get("failure_codes"), "outcome predicate failure_codes")
    witnesses = evaluator["witness_plans"]
    if not isinstance(witnesses, list) or len(witnesses) < 2:
        raise ValueError("goal hidden evaluator requires at least two author witnesses")
    from .goal_driven_tasks import review_goal_business_witness

    for witness in witnesses:
        _mapping(witness, "goal witness")
        _string(witness.get("id"), "goal witness id")
        _string_list(witness.get("satisfies_outcomes"), "goal witness outcomes")
        plan = witness.get("plan")
        plan_review = validate_goal_plan(plan, task_spec=task_spec)
        if set(witness["satisfies_outcomes"]) != outcome_ids:
            raise ValueError("each goal witness must satisfy every required outcome")
        support = _mapping(witness.get("outcome_support"), "goal witness outcome_support")
        if set(support) != outcome_ids:
            raise ValueError("each goal witness must bind every outcome to evidence tasks")
        task_ids = {task["task_id"] for task in plan["tasks"]}
        for outcome_id, task_refs in support.items():
            refs = _string_list(task_refs, f"goal witness support for {outcome_id}")
            if not set(refs) <= task_ids:
                raise ValueError(f"goal witness support for {outcome_id} references an unknown plan task")
        review_goal_business_witness(task_spec, witness, constraint_review=plan_review["constraint_review"])
    signatures = {semantic_goal_plan_signature(witness["plan"]) for witness in witnesses}
    if len(signatures) < 2:
        raise ValueError("goal task witnesses must represent semantically distinct legal plans")
    validate_goal_heterogeneity_certificate(
        evaluator["heterogeneity_certificate"], task_spec=task_spec, witnesses=witnesses)
    _validate_hidden_event_schedule(evaluator["hidden_event_schedule"], task_spec=task_spec)
    factor_effect = _mapping(evaluator["factor_effect"], "factor effect")
    _check_exact(
        factor_effect, {"changed_dimension", "frozen_inputs", "effect_status"},
        "factor effect")
    if factor_effect.get("changed_dimension") != task_spec["experiment_factor"]["id"]:
        raise ValueError("hidden factor effect does not match the public factor")
    if factor_effect.get("effect_status") != "execution_pending":
        raise ValueError("goal factor effect must remain execution_pending before replay")
    frozen_inputs = _string_list(
        factor_effect.get("frozen_inputs"), "factor effect frozen_inputs")
    if frozen_inputs != task_spec["experiment_factor"]["frozen_inputs"]:
        raise ValueError("hidden factor effect does not preserve the public frozen inputs")
    _mapping(evaluator["truth_sources"], "truth sources")
    from .measurement_contract import validate_measurement_contract
    measurement = validate_measurement_contract(evaluator["measurement_contract"])
    if measurement["normalized"]["measurement_target"]["scene_key"] != task_spec["mission"]["scene_key"]:
        raise ValueError("goal measurement contract is not bound to the task scene")
    if evaluator["measurement_status"] not in {"unclassified", "classified"}:
        raise ValueError("goal measurement_status must be unclassified or classified")
    mechanism_template = evaluator["mechanism_template"]
    necessity_certificate = evaluator["team_necessity_certificate"]
    if evaluator["measurement_status"] == "unclassified":
        if mechanism_template is not None or necessity_certificate is not None:
            raise ValueError("unclassified goal bundles cannot attach a canonical mechanism template")
    else:
        if not isinstance(mechanism_template, Mapping) or not isinstance(necessity_certificate, Mapping):
            raise ValueError("classified goal bundles require a mechanism template and necessity certificate")
        from .mechanism_templates import validate_mechanism_template, validate_team_necessity_certificate
        template = validate_mechanism_template(mechanism_template)["normalized"]
        certificate = validate_team_necessity_certificate(necessity_certificate)["normalized"]
        if template["scene_key"] != task_spec["mission"]["scene_key"]:
            raise ValueError("mechanism template is not bound to the task scene")
        if template["measurement_contract"] != measurement["normalized"]:
            raise ValueError("mechanism template measurement contract differs from evaluator contract")
        if certificate != template["team_necessity_certificate"]:
            raise ValueError("evaluator necessity certificate differs from mechanism template")
    gate = _mapping(evaluator["release_gate"], "goal release gate")
    # Bundle validation is also the writer's publication boundary. Constraint
    # review alone cannot attest native execution; this contract has no schema
    # for promoting a package with externally verified execution evidence yet.
    _check_exact(gate, {"authoring_status", "execution_status", "benchmark_ready",
                        "scoreable", "blockers"}, "goal release gate")
    _string_list(gate["blockers"], "goal release gate blockers")
    if gate["authoring_status"] != "passed" or gate["execution_status"] != "external_pending":
        raise ValueError("goal release gate requires passed authoring and external_pending execution")
    if gate["benchmark_ready"] is not False or gate["scoreable"] is not False:
        raise ValueError("goal release gate cannot claim readiness before native execution evidence")
    return {"task_id": evaluator["task_id"], "validated": True,
            "sha256": canonical_sha256(evaluator)}


def validate_goal_bundle(bundle: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    expected = {"public_task", "participant_api", "hidden_evaluator", "baseline_manifest"}
    if set(bundle) != expected:
        raise ValueError(f"goal bundle requires exactly {sorted(expected)}")
    public = bundle["public_task"]
    api = bundle["participant_api"]
    hidden = bundle["hidden_evaluator"]
    validate_goal_task_spec(public)
    validate_goal_participant_api(api, task_spec=public)
    validate_goal_hidden_evaluator(hidden, task_spec=public)
    baseline = _mapping(bundle["baseline_manifest"], "goal baseline manifest")
    if baseline.get("schema") != GOAL_BASELINE_SCHEMA or baseline.get("version") != GOAL_CONTRACT_VERSION:
        raise ValueError("unsupported goal baseline manifest")
    if baseline.get("task_id") != public["task_id"] or baseline.get("public_task_sha256") != canonical_sha256(public):
        raise ValueError("goal baseline is not bound to public task")
    if any(key in baseline for key in {"assignment", "decision", "reference_plan"}):
        raise ValueError("goal baseline must not contain a reference answer")
    forbidden = _find_keys(baseline)
    if forbidden:
        raise ValueError(f"goal baseline leaks private fields: {forbidden}")
    # A key denylist cannot detect an author answer stored under an innocuous
    # name such as solver.plan. Publish only the declared solver descriptors.
    _check_exact(baseline, {"schema", "version", "task_id", "public_task_sha256",
                            "role", "solvers", "limitations"}, "goal baseline")
    _string(baseline["role"], "goal baseline role")
    _string_list(baseline["limitations"], "goal baseline limitations")
    solvers = baseline["solvers"]
    if not isinstance(solvers, list) or not solvers:
        raise ValueError("goal baseline solvers must be a nonempty list")
    solver_ids = set()
    for solver in solvers:
        solver = _mapping(solver, "goal baseline solver")
        _check_exact(solver, {"id", "version", "behavior"}, "goal baseline solver")
        identifier = _string(solver["id"], "goal baseline solver id")
        if identifier in solver_ids:
            raise ValueError("goal baseline solver ids must be unique")
        solver_ids.add(identifier)
        if type(solver["version"]) is not int or solver["version"] < 1:
            raise ValueError("goal baseline solver version must be a positive integer")
        _string(solver["behavior"], "goal baseline solver behavior")
    return {"task_id": public["task_id"], "validated": True,
            "public_task_sha256": canonical_sha256(public),
            "api_sha256": canonical_sha256(api),
            "hidden_sha256": canonical_sha256(hidden)}


def write_goal_task_bundle(bundle: Mapping[str, Mapping[str, Any]], output_dir: str | Path) -> dict[str, Path]:
    """Write the goal bundle with the evaluator artifact outside the release directory."""
    validate_goal_bundle(bundle)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"goal challenge output already exists: {output_dir}")
    public_dir = output_dir / "release"
    evaluator_dir = output_dir / "evaluator"
    public_dir.mkdir(parents=True)
    evaluator_dir.mkdir()
    import json

    paths = {
        "public_task": public_dir / "public_task.json",
        "participant_api": public_dir / "participant_api.json",
        "baseline_manifest": public_dir / "baseline_manifest.json",
        "hidden_evaluator": evaluator_dir / "hidden_evaluator.json",
    }
    for key, path in paths.items():
        path.write_text(json.dumps(bundle[key], indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return paths


__all__ = [
    "GOAL_ACTION_SCHEMA", "GOAL_ACTION_VERSION", "GOAL_API_SCHEMA",
    "GOAL_BASELINE_SCHEMA", "GOAL_CONTRACT_VERSION", "GOAL_EVENT_SCHEMA",
    "GOAL_DEPLOYMENT_CONTRACT_VERSION",
    "GOAL_EVENT_VERSION", "GOAL_PLAN_SCHEMA", "GOAL_PLAN_VERSION",
    "GOAL_HETEROGENEITY_SCHEMA", "GOAL_HIDDEN_EVALUATOR_SCHEMA", "GOAL_TASK_SCHEMA",
    "build_goal_heterogeneity_certificate", "build_goal_participant_api",
    "semantic_goal_plan_signature",
    "validate_goal_action", "validate_goal_bundle", "validate_goal_event",
    "validate_goal_heterogeneity_certificate", "validate_goal_hidden_evaluator", "validate_goal_participant_api",
    "validate_goal_plan", "validate_goal_task_spec", "write_goal_task_bundle",
]
