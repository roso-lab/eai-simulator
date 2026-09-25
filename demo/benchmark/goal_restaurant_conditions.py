"""Exact Restaurant task-condition binding with a fail-closed runtime boundary.

The authoring schedule defines one treatment for the first cross-mobility
observation published before the first business-state commit. This original
v1 resolver remains a binding-only interface: a triggered event is unsupported
here instead of being broadened into a pool mutation. The explicit extension
in ``goal_restaurant_publication_treatment`` connects the real execution-result
uplink to the runtime's versioned single-publication policy. Its application
archive is separate; old binding-only reports retain their original meaning.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any

from .communication import validate_communication_contract
from .goal_driven_contract import validate_goal_hidden_evaluator, validate_goal_task_spec
from .goal_driven_tasks import build_goal_task_package
from .provenance import canonical_sha256


SCHEMA = "eai.restaurant.task_conditions.v1"
VERSION = 1
EVENT_SCHEMA = "eai.restaurant.task_condition_event.v1"
OBSERVATION_SCHEMA = "eai.restaurant.cross_mobility_observation.v1"

_DERIVED_TASK_ID = "RESTAURANT-GOAL-NATIVE-02"
_TREATMENT_ID = "cross_mobility_observation_delivery"
_TRIGGER = "first_cross_mobility_observation_before_first_business_commit"
_REQUIRED_POOLS = frozenset({"backhaul_uplink", "backhaul_downlink", "p2p"})
_PRESERVED_SOURCE_FIELDS = (
    "required_outcomes", "candidate_robot_pool", "budgets",
    "execution_contract", "experiment_factor",
)
_CLOSED_FLAGS = {
    "execution_validated": False,
    "business_outcomes_validated": False,
    "task_exact_communication_treatment_validated": False,
    "release_eligible": False,
    "scoreable": False,
}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


def _source_package(public_task: Mapping[str, Any]) -> dict[str, Any]:
    factor = public_task.get("experiment_factor")
    if not isinstance(factor, Mapping):
        raise ValueError("Restaurant public task requires experiment_factor")
    factor_id = factor.get("id")
    factor_level = factor.get("level")
    if factor_id != "communication_quality":
        raise ValueError("Restaurant task conditions require communication_quality")
    _text(factor_level, "experiment factor level")
    return build_goal_task_package(
        "restaurant", factor_id=factor_id, factor_level=factor_level)


def _task_identity(
    public_task: dict[str, Any],
    source_task: dict[str, Any],
    deployment_companion: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    source_hash = canonical_sha256(source_task)
    effective_hash = canonical_sha256(public_task)
    if public_task == source_task:
        if deployment_companion is not None:
            raise ValueError("canonical Restaurant source task does not use a deployment companion")
        return ({
            "relationship": "canonical_source_task",
            "source_task_id": source_task["task_id"],
            "source_public_task_sha256": source_hash,
            "effective_task_id": public_task["task_id"],
            "effective_public_task_sha256": effective_hash,
            "deployment_companion_sha256": None,
            "preserved_source_fields": list(_PRESERVED_SOURCE_FIELDS),
        }, None)

    if public_task.get("task_id") != _DERIVED_TASK_ID:
        raise ValueError("Restaurant public task is neither the canonical source nor NATIVE-02")
    if deployment_companion is None:
        raise ValueError("NATIVE-02 task conditions require the deployment companion")

    # Import lazily so original authoring variants stay a pure task-package path.
    from .goal_restaurant_deployment_v2 import build_restaurant_deployment_v2

    maintained = build_restaurant_deployment_v2()
    companion = _mapping(deployment_companion, "deployment companion")
    if public_task != maintained["public_task"] or companion != maintained["deployment"]:
        raise ValueError("NATIVE-02 public task or deployment companion is foreign")
    if (companion.get("original_task_id") != source_task["task_id"]
            or companion.get("original_public_task_sha256") != source_hash
            or companion.get("task_id") != public_task["task_id"]
            or companion.get("public_task_sha256") != effective_hash):
        raise ValueError("NATIVE-02 deployment source identity chain is invalid")
    if any(public_task[field] != source_task[field]
           for field in _PRESERVED_SOURCE_FIELDS):
        raise ValueError("NATIVE-02 changed a treatment-relevant source field")
    return ({
        "relationship": "deployment_derived_from_canonical_source",
        "source_task_id": source_task["task_id"],
        "source_public_task_sha256": source_hash,
        "effective_task_id": public_task["task_id"],
        "effective_public_task_sha256": effective_hash,
        "deployment_companion_sha256": canonical_sha256(companion),
        "preserved_source_fields": list(_PRESERVED_SOURCE_FIELDS),
    }, companion)


def build_restaurant_task_conditions(
    *,
    public_task: Mapping[str, Any],
    hidden_evaluator: Mapping[str, Any],
    communication_contract: Mapping[str, Any],
    deployment_companion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind an exact source schedule to one public task and initial transport.

    ``RESTAURANT-GOAL-NATIVE-02`` is a public deployment derivative.  Its
    hidden schedule remains authored against ``RESTAURANT-GOAL-01``; the
    companion's two task hashes and preserved source fields prove that chain.
    """
    public = _mapping(public_task, "public_task")
    hidden = _mapping(hidden_evaluator, "hidden_evaluator")
    validate_goal_task_spec(public)
    source_package = _source_package(public)
    source = source_package["public_task"]
    canonical_hidden = source_package["hidden_evaluator"]
    if hidden != canonical_hidden:
        raise ValueError("hidden evaluator differs from the canonical Restaurant source package")
    validate_goal_hidden_evaluator(hidden, task_spec=source)
    identity, companion = _task_identity(
        public, source, deployment_companion)

    contract = validate_communication_contract(communication_contract)
    pools = contract.get("pools")
    if not isinstance(pools, Mapping) or not _REQUIRED_POOLS <= set(pools):
        raise ValueError("Restaurant task conditions require explicit shared communication pools")

    schedule = deepcopy(hidden["hidden_event_schedule"])
    treatment = deepcopy(schedule["communication_events"][0])
    if (treatment.get("id") != _TREATMENT_ID
            or treatment.get("trigger") != _TRIGGER):
        raise ValueError("Restaurant hidden schedule has an unsupported communication treatment")
    factor = public["experiment_factor"]
    if (schedule.get("factor_id") != factor["id"]
            or schedule.get("factor_level") != factor["level"]):
        raise ValueError("Restaurant hidden treatment label differs from the effective task")

    mobility = {
        row["id"]: row["mobility"]
        for row in public["candidate_robot_pool"]
    }
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "visibility": "author_evaluator_only",
        "binding_status": "bound_not_applied",
        "task_identity": identity,
        "hidden_evaluator_sha256": canonical_sha256(hidden),
        "hidden_event_schedule_sha256": canonical_sha256(schedule),
        "authoritative_hidden_event_schedule": schedule,
        "factor": {"id": factor["id"], "level": factor["level"]},
        "treatment": treatment,
        "trigger": {
            "id": _TRIGGER,
            "first_only": True,
            "opens_after": schedule["decision_window"]["opens_after"],
            "closes_before": schedule["decision_window"]["closes_before"],
            "cross_mobility_basis": (
                "evaluator-supplied producer and downstream consumer identities with "
                "session/transport join references; mobility is derived from the "
                "effective public candidate pool"),
        },
        "target": {
            "scope": "single_future_publication",
            "channel": "observation",
            "message_class": "restaurant_service_observation",
            "pool": "backhaul_uplink",
            "direction": "uplink",
            "transport_recipient": "cloud",
        },
        "agent_mobility": dict(sorted(mobility.items())),
        "initial_communication_contract": contract,
        "initial_communication_contract_sha256": canonical_sha256(contract),
        "runtime_units": {
            "treatment_bytes_per_window": "bytes_per_window",
            "treatment_window_s": "seconds",
            "legacy_runtime_capacity_bps": "bytes_per_second",
            "conversion_applied": False,
        },
        "c0_status": "not_declared",
        "c0_capacity_bps": None,
        "runtime_support": {
            "runtime": "CommunicationRuntime shared-pool mode",
            "mapping_status": "unsupported_runtime_mapping",
            "targeted_message_policy_supported": False,
            "required_capability": "per_message_shared_pool_policy_override",
            "unsupported_reason": (
                "the runtime has no per-message override for the complete pooled treatment; "
                "set_conditions(pool=...) would make its byte window and delay pool-wide"),
            "global_pool_mutation_allowed": False,
        },
        **_CLOSED_FLAGS,
    }
    if companion is not None:
        result["deployment_profile"] = companion["profile"]
    result["binding_sha256"] = canonical_sha256(result)
    return result


def validate_restaurant_task_conditions(
    conditions: Mapping[str, Any],
    *,
    public_task: Mapping[str, Any],
    hidden_evaluator: Mapping[str, Any],
    communication_contract: Mapping[str, Any],
    deployment_companion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild a binding from authorities and reject any archived drift."""
    expected = build_restaurant_task_conditions(
        public_task=public_task,
        hidden_evaluator=hidden_evaluator,
        communication_contract=communication_contract,
        deployment_companion=deployment_companion,
    )
    if not isinstance(conditions, Mapping) or dict(conditions) != expected:
        raise ValueError("Restaurant task conditions differ from their authoritative inputs")
    return deepcopy(expected)


def _validated_binding(conditions: Mapping[str, Any]) -> dict[str, Any]:
    row = _mapping(conditions, "conditions")
    digest = row.pop("binding_sha256", None)
    if (row.get("schema") != SCHEMA or row.get("version") != VERSION
            or digest != canonical_sha256(row)):
        raise ValueError("Restaurant task-condition binding is malformed or has drifted")
    row["binding_sha256"] = digest
    if (row.get("binding_status") != "bound_not_applied"
            or row.get("runtime_support", {}).get(
                "targeted_message_policy_supported") is not False):
        raise ValueError("Restaurant task-condition runtime support was overstated")
    if any(row.get(flag) is not False for flag in _CLOSED_FLAGS):
        raise ValueError("Restaurant task-condition validation flags must remain false")
    return row


def _event(
    conditions: Mapping[str, Any],
    *,
    status: str,
    trigger_status: str,
    application_status: str,
    reason: str,
    observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "version": VERSION,
        "visibility": "evaluator_only",
        "binding_sha256": conditions["binding_sha256"],
        "status": status,
        "binding_status": "bound_not_applied",
        "trigger_status": trigger_status,
        "application_status": application_status,
        "effect_status": "not_observed",
        "runtime_mapping_status": (
            "unsupported_runtime_mapping"
            if application_status == "unsupported" else "not_exercised"),
        "source_provenance_status": (
            "unverified" if observation is not None else "not_applicable"),
        "reason": reason,
        "target": None,
        "treatment": None,
        "runtime_support": deepcopy(conditions["runtime_support"]),
        **_CLOSED_FLAGS,
    }
    if observation is not None:
        result["observation"] = deepcopy(dict(observation))
    result["event_sha256"] = canonical_sha256(result)
    return result


def _validate_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    observation = _mapping(value, "cross_mobility_observation")
    expected = {
        "schema", "version", "observation_id", "message_id",
        "producer_agent_id", "consumer_agent_id", "published_at_s",
        "channel", "message_class", "pool", "direction", "provenance",
    }
    if set(observation) != expected:
        raise ValueError("cross-mobility observation fields mismatch")
    if (observation["schema"] != OBSERVATION_SCHEMA
            or observation["version"] != VERSION):
        raise ValueError("unsupported cross-mobility observation schema/version")
    for field in expected - {"version", "published_at_s", "provenance"}:
        _text(observation[field], f"cross_mobility_observation.{field}")
    provenance = observation["provenance"]
    provenance_fields = {
        "kind", "producer_task_id", "consumer_task_id",
        "session_event_ref", "transport_message_id",
    }
    if not isinstance(provenance, Mapping) or set(provenance) != provenance_fields:
        raise ValueError("cross-mobility observation provenance fields mismatch")
    provenance = deepcopy(dict(provenance))
    for field in provenance_fields:
        _text(provenance[field], f"cross_mobility_observation.provenance.{field}")
    if provenance["kind"] != "goal_session_transport_join_reference":
        raise ValueError("cross-mobility observation provenance kind is unsupported")
    if provenance["transport_message_id"] != observation["message_id"]:
        raise ValueError("cross-mobility observation transport provenance mismatches")
    if provenance["producer_task_id"] == provenance["consumer_task_id"]:
        raise ValueError("cross-mobility observation requires a downstream task")
    observation["provenance"] = provenance
    observation["published_at_s"] = _time(
        observation["published_at_s"], "cross_mobility_observation.published_at_s")
    return observation


def resolve_restaurant_condition_event(
    conditions: Mapping[str, Any],
    *,
    cross_mobility_observation: Mapping[str, Any] | None,
    first_business_commit_at_s: float | None,
    communication_contract: Mapping[str, Any],
    prior_events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Resolve the one-shot trigger without mutating the shared runtime.

    Producer and downstream consumer are public robot identities.  ``cloud`` is
    merely the transport endpoint and does not establish cross mobility.
    """
    binding = _validated_binding(conditions)
    contract = validate_communication_contract(communication_contract)
    if (canonical_sha256(contract)
            != binding["initial_communication_contract_sha256"]
            or contract != binding["initial_communication_contract"]):
        raise ValueError("runtime communication contract differs from the condition binding")
    commit_at = (None if first_business_commit_at_s is None else
                 _time(first_business_commit_at_s, "first_business_commit_at_s"))
    if not isinstance(prior_events, Sequence) or isinstance(prior_events, (str, bytes)):
        raise ValueError("prior_events must be a sequence")
    for previous in prior_events:
        if (not isinstance(previous, Mapping)
                or previous.get("schema") != EVENT_SCHEMA
                or previous.get("version") != VERSION
                or previous.get("visibility") != "evaluator_only"
                or previous.get("binding_sha256") != binding["binding_sha256"]
                or previous.get("event_sha256") != canonical_sha256({
                    key: value for key, value in previous.items()
                    if key != "event_sha256"
                })
                or any(previous.get(flag) is not False for flag in _CLOSED_FLAGS)):
            raise ValueError("prior condition event is foreign or malformed")
        if (previous.get("status") not in {"not_triggered", "unsupported"}
                or previous.get("trigger_status") not in {
                    "not_triggered", "triggered"}
                or previous.get("application_status") not in {
                    "not_applied", "unsupported"}
                or previous.get("effect_status") not in {
                    "not_observed", "unverified"}
                or previous.get("runtime_mapping_status") not in {
                    "not_exercised", "unsupported_runtime_mapping"}
                or previous.get("source_provenance_status") not in {
                    "not_applicable", "unverified"}):
            raise ValueError("prior condition event has an unsupported state")
        if previous.get("trigger_status") == "triggered":
            return _event(
                binding, status="not_triggered", trigger_status="not_triggered",
                application_status="not_applied", reason="first_trigger_already_resolved")

    if cross_mobility_observation is None:
        return _event(
            binding, status="not_triggered", trigger_status="not_triggered",
            application_status="not_applied", reason="no_cross_mobility_observation")
    observation = _validate_observation(cross_mobility_observation)
    target = binding["target"]
    if any(observation[field] != target[field]
           for field in ("channel", "message_class", "pool", "direction")):
        return _event(
            binding, status="not_triggered", trigger_status="not_triggered",
            application_status="not_applied", reason="observation_outside_target",
            observation=observation)

    mobility = binding["agent_mobility"]
    producer = observation["producer_agent_id"]
    consumer = observation["consumer_agent_id"]
    if producer not in mobility or consumer not in mobility:
        raise ValueError("cross-mobility observation references an unknown public agent")
    if mobility[producer] == mobility[consumer]:
        return _event(
            binding, status="not_triggered", trigger_status="not_triggered",
            application_status="not_applied", reason="same_mobility_observation",
            observation=observation)

    if commit_at is not None and observation["published_at_s"] >= commit_at:
        return _event(
            binding, status="not_triggered", trigger_status="not_triggered",
            application_status="not_applied",
            reason="observation_not_before_first_business_commit",
            observation=observation)

    result = _event(
        binding, status="unsupported", trigger_status="triggered",
        application_status="unsupported",
        reason="targeted_shared_pool_publication_policy_is_unavailable",
        observation=observation)
    result["target"] = {
        **deepcopy(target),
        "selector": {
            "message_id": observation["message_id"],
            "observation_id": observation["observation_id"],
            "producer_agent_id": producer,
            "consumer_agent_id": consumer,
        },
    }
    result["treatment"] = deepcopy(binding["treatment"])
    # The resolver checks shape, task identities and joined record references;
    # an independent archive verifier must still authenticate those sources.
    result["effect_status"] = "unverified"
    result["event_sha256"] = canonical_sha256(
        {key: value for key, value in result.items() if key != "event_sha256"})
    return result


__all__ = [
    "EVENT_SCHEMA", "OBSERVATION_SCHEMA", "SCHEMA", "VERSION",
    "build_restaurant_task_conditions", "resolve_restaurant_condition_event",
    "validate_restaurant_task_conditions",
]
