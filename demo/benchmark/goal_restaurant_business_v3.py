"""Full-fleet Restaurant business facts from the versioned execution archive.

The verifier consumes an already archived episode.  It independently replays
the GoalSession journal and shared communication ledger, then binds the frozen
Restaurant outcomes to immutable physical roots, native support observations,
and their actual delivery/consumption chain.  It does not launch Isaac Sim,
score progress, authenticate the host that supplied an accepted source
manifest, or promote any release flag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any

from demo.restaurant_benchmark import navigation_probe
from demo.restaurant_benchmark.service_backend_v2 import (
    evaluate_readiness_receipt_v2,
)

from .communication import validate_communication_report
from .goal_restaurant_episode import ROBOT_ID, _verify_archived_scene_observation
from .goal_session_audit import (
    replay_goal_session_audit,
    validate_goal_session_audit,
)
from .provenance import canonical_sha256


REPORT_SCHEMA = "eai.restaurant.native_business_review.v3"
REPORT_VERSION = 1
INSPECTION_AVAILABILITY = "public_observation_host_consumed"
RECEIPT_AVAILABILITY = "participant_result_host_consumed"
REQUIRED_OUTCOMES = {
    "kitchen_service": "kitchen_service_inspected",
    "dining_service": "dining_service_inspected",
}
RECEIPT_OUTCOME = "readiness_receipt_committed"
RESULT_CHANNEL = "goal_execution_result"
RESULT_RECIPIENT = "trusted_execution_host"
RELAY_CHANNEL = "restaurant_readiness"
SERVICE_RECIPIENT = "restaurant_opening_service"
_CLOSED_FLAGS = (
    "execution_validated",
    "business_outcomes_validated",
    "feasibility_oracle_verified",
    "release_ready",
    "scoreable",
)
_HASH_CHARS = frozenset("0123456789abcdef")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def _step(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _sha256(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _HASH_CHARS for char in value)):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _same(left: Any, right: Any, label: str) -> None:
    if canonical_sha256(left) != canonical_sha256(right):
        raise ValueError(f"{label} mismatch")


def _closed(report: Mapping[str, Any], label: str) -> None:
    for flag in _CLOSED_FLAGS:
        if flag in report and report[flag] is not False:
            raise ValueError(f"{label} cannot promote {flag}")


def _sealed(row: Any, label: str) -> Mapping[str, Any]:
    row = _mapping(row, label)
    ref = _text(row.get("evidence_ref"), f"{label}.evidence_ref")
    body = {key: value for key, value in row.items() if key != "evidence_ref"}
    if ref != "sha256:" + canonical_sha256(body):
        raise ValueError(f"{label} does not match its evidence_ref")
    return row


def _string_list(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    result = _list(value, label)
    if ((nonempty and not result)
            or any(not isinstance(item, str) or not item.strip() for item in result)
            or len(result) != len(set(result))):
        raise ValueError(f"{label} must contain distinct nonempty strings")
    return result


def _root(value: Any, label: str) -> list[float]:
    if (not isinstance(value, list) or len(value) < 7
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in value)):
        raise ValueError(f"{label} must be a finite native root state")
    return [float(item) for item in value]


def _region_geometry(
    deployment: Mapping[str, Any], region_id: str,
) -> tuple[list[float], float]:
    bindings = _mapping(deployment.get("region_bindings"), "deployment region_bindings")
    binding = _mapping(bindings.get(region_id), f"region binding {region_id}")
    if binding.get("geometry_status") != "defined":
        raise ValueError(f"region {region_id} lacks defined geometry")
    center = binding.get("center_xy_m")
    if (not isinstance(center, list) or len(center) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in center)):
        raise ValueError(f"region {region_id} lacks finite center_xy_m")
    tolerance = _time(binding.get("tolerance_m"), f"region {region_id} tolerance_m")
    if tolerance <= 0:
        raise ValueError(f"region {region_id} tolerance_m must be positive")
    return [float(item) for item in center], tolerance


def _source_identity(
    archived: Any, expected: Any,
) -> tuple[dict[str, str], str]:
    archived = _mapping(archived, "archived_source_sha256")
    expected = _mapping(expected, "expected_source_sha256")
    if not archived or set(archived) != set(expected):
        raise ValueError("archived and expected source manifests differ")
    result: dict[str, str] = {}
    for path in sorted(archived):
        path = _text(path, "source manifest path")
        archived_digest = _sha256(archived[path], f"archived source {path}")
        expected_digest = _sha256(expected[path], f"expected source {path}")
        if archived_digest != expected_digest:
            raise ValueError(f"archived source differs from expected source: {path}")
        result[path] = archived_digest
    return result, canonical_sha256(result)


def _validate_archive_identity(
    *, public_task: Mapping[str, Any], deployment: Mapping[str, Any],
    episode_metadata: Mapping[str, Any], context: Mapping[str, Any],
    session: Mapping[str, Any], audit_journal: Mapping[str, Any],
    audit_replay: Mapping[str, Any], pump: Mapping[str, Any],
    communication: Mapping[str, Any], communication_replay: Mapping[str, Any],
) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    task_id = _text(public_task.get("task_id"), "public task_id")
    task_digest = canonical_sha256(public_task)
    deployment_digest = canonical_sha256(deployment)
    outcomes = _list(public_task.get("required_outcomes"), "public required_outcomes")
    outcome_ids = [_text(row.get("id"), "required outcome id")
                   for row in map(lambda item: _mapping(item, "required outcome"), outcomes)]
    required = set(REQUIRED_OUTCOMES.values()) | {RECEIPT_OUTCOME}
    if set(outcome_ids) != required or len(outcome_ids) != len(required):
        raise ValueError("public task does not declare exactly the Restaurant business outcomes")
    if (deployment.get("task_id") != task_id
            or deployment.get("public_task_sha256") != task_digest):
        raise ValueError("deployment is not bound to the exact public task")
    _closed(deployment, "deployment")

    episode_id = _text(episode_metadata.get("episode_id"), "episode metadata episode_id")
    if (episode_metadata.get("schema") != "eai.restaurant.native_episode_epoch"
            or episode_metadata.get("version") != 1
            or episode_metadata.get("task_id") != task_id
            or episode_metadata.get("public_task_sha256") != task_digest
            or episode_metadata.get("deployment_sha256") != deployment_digest):
        raise ValueError("episode metadata identity mismatch")
    _step(episode_metadata.get("origin_physics_step"), "origin_physics_step")
    _time(episode_metadata.get("origin_time_s"), "origin_time_s")
    _closed(episode_metadata, "episode metadata")

    if (context.get("schema") != "eai.restaurant.autonomous_context"
            or context.get("episode_id", episode_id) not in {None, episode_id}):
        # The context schema stores the episode in its binding rather than at
        # top level.  A non-null top-level value, when present, must still bind.
        raise ValueError("autonomous context schema or episode mismatch")
    contract = _mapping(context.get("public_contract"), "context public_contract")
    _same(contract.get("public_task"), public_task, "context public task")
    _same(contract.get("deployment"), deployment, "context deployment")
    participant_api = deepcopy(dict(_mapping(
        contract.get("participant_api"), "context participant_api")))
    binding = _mapping(context.get("binding"), "context binding")
    if (binding.get("episode_id") != episode_id
            or binding.get("public_task_sha256") != task_digest
            or binding.get("deployment_sha256") != deployment_digest):
        raise ValueError("context deployment acknowledgement mismatch")
    if (context.get("execution_profile_acknowledged") is not True
            or context.get("execution_loop_connected") is not True):
        raise ValueError("context did not acknowledge and connect the execution profile")
    _closed(context, "autonomous context")

    journal = validate_goal_session_audit(audit_journal)
    if journal.get("episode_id") != episode_id:
        raise ValueError("goal session audit carries a foreign episode")
    _same(session, journal.get("final_report"), "session and audit final report")
    _same(context.get("session_audit"), journal, "context and audit journal")
    recomputed_audit = replay_goal_session_audit(
        journal, public_task=public_task, participant_api=participant_api)
    _same(audit_replay, recomputed_audit, "supplied and recomputed audit replay")

    recomputed_communication = validate_communication_report(communication)
    if recomputed_communication.get("validated") is not True:
        raise ValueError("communication report failed independent replay")
    _same(
        communication_replay, recomputed_communication,
        "supplied and recomputed communication replay")

    if (pump.get("schema") != "eai.restaurant.execution_pump"
            or pump.get("episode_id") != episode_id):
        raise ValueError("execution pump schema or episode mismatch")
    _same(pump.get("context"), context, "pump and context archive")
    _same(pump.get("session_audit_replay"), recomputed_audit,
          "pump and audit replay")
    _same(pump.get("communication"), communication,
          "pump and communication report")
    readiness = _mapping(pump.get("readiness"), "pump readiness archive")
    _same(readiness.get("transport_report"), communication,
          "readiness and communication report")
    _closed(session, "session")
    _closed(pump, "execution pump")
    _closed(readiness, "readiness archive")
    return episode_id, task_id, task_digest, participant_api, {
        "audit": recomputed_audit,
        "communication": recomputed_communication,
    }


def _physical_index(
    pump: Mapping[str, Any], episode_id: str,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, list[Mapping[str, Any]]]]:
    by_ref: dict[str, Mapping[str, Any]] = {}
    by_task: dict[str, list[Mapping[str, Any]]] = {}
    for raw in _list(pump.get("physical_records"), "pump physical_records"):
        row = _sealed(raw, "physical terminal record")
        if row.get("schema") != "eai.restaurant.native_task_terminal":
            raise ValueError("physical terminal record schema mismatch")
        if row.get("episode_id") != episode_id:
            raise ValueError("physical terminal record carries a foreign episode")
        ref = row["evidence_ref"]
        if ref in by_ref:
            raise ValueError("duplicate physical terminal evidence_ref")
        snapshot = _mapping(row.get("fleet_snapshot"), "physical fleet_snapshot")
        region = _sealed(row.get("region_record"), "physical region_record")
        if (row.get("fleet_snapshot_sha256") != canonical_sha256(snapshot)
                or row.get("region_record_sha256") != canonical_sha256(region)):
            raise ValueError("physical snapshot digest mismatch")
        physical = _time(row.get("physical_terminal_at_s"), "physical terminal time")
        physics_step = _step(snapshot.get("physics_step"), "fleet physics_step")
        if (_time(snapshot.get("time_s"), "fleet time") != physical
                or _step(region.get("physics_step"), "region physics_step") != physics_step
                or _time(region.get("time_s"), "region time") != physical):
            raise ValueError("physical terminal, fleet and region stamps differ")
        task_id = _text(row.get("task_id"), "physical task_id")
        by_ref[ref] = row
        by_task.setdefault(task_id, []).append(row)
    return by_ref, by_task


def _task_binding(
    task_id: str, task: Mapping[str, Any], *, pump: Mapping[str, Any],
    session: Mapping[str, Any], physical: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    pump_task = _mapping(
        _mapping(pump.get("tasks"), "pump tasks").get(task_id),
        f"pump task {task_id}")
    session_task = _mapping(
        _mapping(session.get("tasks"), "session tasks").get(task_id),
        f"session task {task_id}")
    if (task.get("task_id") != task_id
            or pump_task.get("task") != task
            or session_task.get("task") != task
            or physical.get("task") != task
            or physical.get("dispatch_action_id") != pump_task.get("dispatch_action_id")
            or session_task.get("dispatch_action_id") != pump_task.get("dispatch_action_id")):
        raise ValueError(f"task {task_id} identity differs across archives")
    return pump_task, session_task


def _dispatch_binding(
    task_id: str, *, episode_id: str, task: Mapping[str, Any],
    pump_task: Mapping[str, Any], session_task: Mapping[str, Any],
    communication: Mapping[str, Any], physical_started_at_s: float,
) -> str:
    message_id = _text(pump_task.get("dispatch_message_id"), "dispatch message_id")
    message = _mapping(
        _mapping(communication.get("messages"), "communication messages").get(message_id),
        f"dispatch message {message_id}")
    payload = _mapping(message.get("payload"), "dispatch payload")
    wire = _mapping(payload.get("estimate"), "dispatch wire")
    agents = _string_list(task.get("assigned_agents"), "task assigned_agents")
    if (message.get("channel") != "goal_execution_dispatch"
            or wire.get("schema") != "eai.benchmark.goal_execution_command"
            or wire.get("episode_id") != episode_id
            or wire.get("task_id") != task_id
            or wire.get("dispatch_action_id") != pump_task.get("dispatch_action_id")
            or wire.get("operation") != "dispatch"
            or wire.get("task") != task
            or wire.get("assigned_agents") != agents
            or payload.get("message_id") != message_id):
        raise ValueError(f"task {task_id} dispatch wire identity mismatch")
    recipients = _mapping(message.get("recipients"), "dispatch recipients")
    delivered_times: list[float] = []
    consumed: list[float] = []
    for agent in agents:
        delivery = _mapping(recipients.get(agent), f"dispatch recipient {agent}")
        consumption = _mapping(delivery.get("consumption"), "dispatch consumption")
        if delivery.get("delivered_at_s") is None:
            raise ValueError(f"task {task_id} dispatch was not delivered")
        delivered = _time(delivery["delivered_at_s"], "dispatch delivered_at_s")
        consumed_at = _time(consumption.get("time_s"), "dispatch consumed_at_s")
        if consumption.get("message_id") != message_id or delivered > consumed_at:
            raise ValueError(f"task {task_id} dispatch delivery/consumption mismatch")
        delivered_times.append(delivered)
        consumed.append(consumed_at)
    ready = _time(pump_task.get("ready_at_s"), "task ready_at_s")
    if (ready != max(delivered_times) or max(consumed) > physical_started_at_s
            or _time(session_task.get("dispatched_at_s"), "session dispatched_at_s")
            != _time(pump_task.get("dispatch_accepted_at_s"), "dispatch accepted_at_s")):
        raise ValueError(f"task {task_id} began before its delivered dispatch")
    return message_id


def _result_binding(
    task_id: str, *, episode_id: str, task: Mapping[str, Any],
    physical: Mapping[str, Any], pump_task: Mapping[str, Any],
    session_task: Mapping[str, Any], pump: Mapping[str, Any],
    communication: Mapping[str, Any], audit_journal: Mapping[str, Any],
) -> dict[str, Any]:
    physical_time = _time(physical.get("physical_terminal_at_s"), "physical time")
    ref = physical["evidence_ref"]
    messages: list[tuple[str, Mapping[str, Any]]] = []
    for message_id, raw_message in _mapping(
            communication.get("messages"), "communication messages").items():
        if not isinstance(raw_message, Mapping) or raw_message.get("channel") != RESULT_CHANNEL:
            continue
        payload = raw_message.get("payload")
        wire = payload.get("estimate") if isinstance(payload, Mapping) else None
        if isinstance(wire, Mapping) and wire.get("source_record_ref") == ref:
            messages.append((message_id, raw_message))
    if len(messages) != 1:
        raise ValueError(f"task {task_id} lacks one result wire for its physical record")
    message_id, message = messages[0]
    payload = _mapping(message.get("payload"), "result payload")
    wire = _mapping(payload.get("estimate"), "result wire")
    receipt = _mapping(wire.get("receipt"), "result receipt")
    if (message.get("channel") != RESULT_CHANNEL
            or payload.get("message_id") != message_id
            or _time(payload.get("published_at_s"), "result published_at_s") != physical_time
            or wire.get("schema") != "eai.restaurant.execution_result_wire"
            or wire.get("episode_id") != episode_id
            or wire.get("task_id") != task_id
            or wire.get("dispatch_action_id") != pump_task.get("dispatch_action_id")
            or _time(wire.get("physical_completed_at_s"), "wire physical time") != physical_time
            or wire.get("status") != "succeeded"
            or wire.get("source_record_ref") != ref
            or receipt.get("task_id") != task_id
            or receipt.get("action_id") != pump_task.get("dispatch_action_id")
            or receipt.get("episode_id") != episode_id
            or receipt.get("status") != "succeeded"
            or _time(receipt.get("completed_at_s"), "receipt completed_at_s")
            != physical_time
            or ref not in _string_list(receipt.get("evidence_refs"), "receipt evidence_refs")):
        raise ValueError(f"task {task_id} result wire does not bind physical completion")
    recipient = _mapping(
        _mapping(message.get("recipients"), "result recipients").get(RESULT_RECIPIENT),
        "result recipient")
    records = [row for row in _list(
        pump.get("result_delivery_records"), "result_delivery_records")
        if isinstance(row, Mapping) and row.get("message_id") == message_id]
    if len(records) > 1:
        raise ValueError(f"task {task_id} has duplicate result delivery records")
    record = records[0] if records else None
    recipient_status = _text(recipient.get("status"), "result recipient status")
    record_status = record.get("publication_status") if record is not None else None
    delivered_record = record_status == "delivered"
    if record is not None:
        if (record.get("task_id") != task_id or record.get("status") != "succeeded"
                or _time(record.get("physical_completed_at_s"), "record physical time")
                != physical_time
                or record.get("source_record_ref") != ref
                or record.get("receipt") != receipt
                or record.get("participant_observation")
                != wire.get("participant_observation")
                or record.get("wire_size_bytes") != message.get("size_bytes")
                or (record_status != recipient_status
                    and not (delivered_record
                             and recipient_status == "expired"
                             and recipient.get("delivered_at_s") is not None
                             and recipient.get("consumption") is not None))):
            raise ValueError(f"task {task_id} result delivery record was substituted")

    terminal = pump_task.get("terminal_delivery")
    invalidated = bool(session_task.get("invalidated_by"))
    rejected_delivery = bool(
        delivered_record and record is not None
        and (record.get("superseded") is not False
             or record.get("terminal_reason") is not None))
    qualifying = bool(
        record is not None
        and delivered_record
        and record.get("superseded") is False
        and record.get("terminal_reason") is None
        and isinstance(terminal, Mapping)
        and terminal.get("message_id") == message_id
        and session_task.get("status") == "succeeded"
        and session_task.get("invalidated_by") == [])
    result_link_time = None
    available = None
    if delivered_record:
        result_link_time = _time(
            recipient.get("delivered_at_s"), "recipient delivered_at_s")
        consumption = _mapping(recipient.get("consumption"), "result consumption")
        consumed_at = _time(consumption.get("time_s"), "result consumed_at_s")
        if (record is None
                or _time(record.get("delivered_at_s"), "record delivered_at_s")
                != result_link_time
                or _time(record.get("consumed_at_s"), "record consumed_at_s") != consumed_at
                or consumption.get("message_id") != message_id
                or not physical_time <= result_link_time <= consumed_at):
            raise ValueError(f"task {task_id} delivered result was unmetered")
        if qualifying:
            available = consumed_at
    else:
        if (recipient.get("delivered_at_s") is not None
                or recipient.get("consumption") is not None):
            raise ValueError(f"task {task_id} undelivered result has delivery evidence")
        if recipient_status in {"dropped", "expired"}:
            if (record is None
                    or record.get("delivered_at_s") is not None
                    or record.get("consumed_at_s") is not None
                    or record.get("terminal_reason")
                    != (recipient.get("drop_reason") or recipient_status)):
                raise ValueError(f"task {task_id} result transport outcome was substituted")
        elif recipient_status not in {"queued", "serializing", "in_transit"}:
            raise ValueError(f"task {task_id} has an unknown result transport outcome")

    if delivered_record and not qualifying and not invalidated and not rejected_delivery:
        raise ValueError(f"task {task_id} delivered result lacks its host/session commitment")

    if qualifying:
        terminal = _mapping(terminal, "pump terminal_delivery")
        if (session_task.get("receipt") != receipt
                or _time(session_task.get("completed_at_s"), "session physical time")
                != physical_time
                or _time(session_task.get("result_delivered_at_s"), "session result delivery")
                != available
                or _time(terminal.get("physical_completed_at_s"), "terminal physical time")
                != physical_time
                or _time(terminal.get("result_delivered_at_s"), "terminal delivery time")
                != available
                or terminal.get("status") != "succeeded"):
            raise ValueError(f"task {task_id} dual-time session binding mismatch")
    calls = [call for call in audit_journal["calls"]
             if call.get("method") == "complete_skill_delivery"
             and call.get("outcome") == "returned"
             and isinstance(call.get("arguments"), Mapping)
             and call["arguments"].get("task_id") == task_id]
    if qualifying:
        matches = [call for call in calls
                   if call["arguments"].get("receipt") == receipt]
        if len(matches) != 1:
            raise ValueError(f"task {task_id} lacks one audited delivered completion")
        arguments = matches[0]["arguments"]
        if (_time(arguments.get("physical_completed_at_s"), "audit physical time")
                != physical_time
                or _time(arguments.get("delivered_at_s"), "audit delivery time") != available
                or arguments.get("status") != "succeeded"):
            raise ValueError(f"task {task_id} audited dual-time completion mismatch")
    elif not invalidated and not rejected_delivery:
        if (isinstance(terminal, Mapping) and terminal.get("message_id") == message_id
                or session_task.get("receipt") == receipt
                or any(call["arguments"].get("receipt") == receipt for call in calls)):
            raise ValueError(
                f"task {task_id} undelivered result conflicts with host/session completion")

    transport_outcome = (
        "delivered" if qualifying
        else record_status if record_status is not None else recipient_status)
    transport_reason = (
        None if qualifying else record.get("terminal_reason") if record is not None
        else recipient.get("drop_reason"))
    return {
        "message_id": message_id,
        "physical_time_s": physical_time,
        "transport_delivered_at_s": result_link_time,
        "available_at_s": available,
        "availability_status": "delivered" if qualifying else "not_delivered",
        "transport_outcome": transport_outcome,
        "transport_reason": transport_reason,
        "candidate_eligible": not invalidated and not rejected_delivery,
        "receipt_ref": receipt["receipt_ref"],
        "evidence_refs": list(receipt["evidence_refs"]),
        "participant_observation": wire.get("participant_observation"),
    }


def _member_exit_between(
    audit_journal: Mapping[str, Any], producer: str, start: float, end: float,
) -> bool:
    for call in audit_journal["calls"]:
        args = call.get("arguments")
        if (call.get("method") != "emit" or call.get("outcome") != "returned"
                or not isinstance(args, Mapping) or args.get("type") != "member_exit"):
            continue
        payload = args.get("payload")
        if (isinstance(payload, Mapping) and payload.get("agent_id") == producer
                and start < _time(args.get("time_s"), "member_exit time") <= end):
            return True
    return False


def _navigation_candidate(
    physical: Mapping[str, Any], *, episode_id: str, public_task: Mapping[str, Any],
    deployment: Mapping[str, Any], pump: Mapping[str, Any],
    session: Mapping[str, Any], communication: Mapping[str, Any],
    audit_journal: Mapping[str, Any],
) -> dict[str, Any] | None:
    task_id = _text(physical.get("task_id"), "navigation task_id")
    task = _mapping(physical.get("task"), "navigation task")
    if task.get("skill") != "ground_navigation":
        raise ValueError("navigation physical record has the wrong skill")
    agents = _string_list(task.get("assigned_agents"), "navigation assigned_agents")
    if len(agents) != 1:
        raise ValueError("business navigation must have one physical producer")
    producer = agents[0]
    arguments = _mapping(task.get("arguments"), "navigation arguments")
    region_id = _text(arguments.get("region_id"), "navigation region_id")
    routes = _string_list(arguments.get("route_ids"), "navigation route_ids")
    pump_task, session_task = _task_binding(
        task_id, task, pump=pump, session=session, physical=physical)
    if (physical.get("status") != "succeeded"
            or physical.get("disposition") != "native_navigation_arrival"):
        raise ValueError("selected navigation record is not a native arrival")
    started = _time(pump_task.get("physical_started_at_s"), "navigation start")
    dispatch_id = _dispatch_binding(
        task_id, episode_id=episode_id, task=task, pump_task=pump_task,
        session_task=session_task, communication=communication,
        physical_started_at_s=started)
    public_routes = {_text(row.get("id"), "public route id"): row for row in map(
        lambda item: _mapping(item, "public route"),
        _list(_mapping(public_task.get("public_world"), "public_world").get("routes"),
              "public routes"))}
    route_bindings = _mapping(deployment.get("route_bindings"), "route_bindings")
    previous_to = None
    for route_id in routes:
        route = _mapping(public_routes.get(route_id), f"public route {route_id}")
        _mapping(route_bindings.get(route_id), f"deployment route {route_id}")
        if previous_to is not None and route.get("from_region_id") != previous_to:
            raise ValueError("navigation route chain is discontinuous")
        previous_to = _text(route.get("to_region_id"), "route to_region_id")
    if previous_to != region_id:
        raise ValueError("navigation route chain does not end in its requested region")

    snapshot = _mapping(physical.get("fleet_snapshot"), "navigation fleet snapshot")
    commands = _mapping(snapshot.get("submitted_commands"), "submitted full-fleet commands")
    declared_agents = {_text(row.get("id"), "candidate robot id") for row in map(
        lambda item: _mapping(item, "candidate robot"),
        _list(public_task.get("candidate_robot_pool"), "candidate_robot_pool"))}
    if set(commands) != declared_agents or set(_mapping(
            snapshot.get("agents"), "fleet snapshot agents")) != declared_agents:
        raise ValueError("navigation snapshot is not the exact declared full fleet")
    operation_ids = _string_list(pump_task.get("operation_ids"), "operation_ids")
    if len(operation_ids) != len(routes):
        raise ValueError("navigation route and operation chains differ")
    operations = _mapping(snapshot.get("operations"), "fleet operations")
    prior_step = -1
    prior_time = -1.0
    final_operation = None
    for operation_id, route_id in zip(operation_ids, routes, strict=True):
        operation = _mapping(operations.get(operation_id), f"operation {operation_id}")
        completed_step = _step(operation.get("completed_physics_step"), "operation step")
        completed_time = _time(operation.get("completed_at_s"), "operation completion")
        if (operation.get("operation_id") != operation_id
                or operation.get("route_id") != route_id
                or operation.get("public_agent_id") != producer
                or operation.get("status") != "arrived"
                or completed_step <= prior_step or completed_time <= prior_time):
            raise ValueError("navigation operation identity or order mismatch")
        prior_step, prior_time = completed_step, completed_time
        final_operation = operation
    assert final_operation is not None
    physical_time = _time(physical.get("physical_terminal_at_s"), "navigation terminal time")
    physics_step = _step(snapshot.get("physics_step"), "navigation terminal step")
    if (prior_step != physics_step or prior_time != physical_time
            or final_operation.get("goal_binding", {}).get("region_id") != region_id):
        raise ValueError("final operation differs from the immutable physical terminal")
    region_record = _mapping(physical.get("region_record"), "navigation region record")
    region_agent = _mapping(
        _mapping(region_record.get("agents"), "region agents").get(producer),
        "navigation region agent")
    snapshot_agent = _mapping(
        _mapping(snapshot.get("agents"), "snapshot agents").get(producer),
        "navigation snapshot agent")
    root = _root(region_agent.get("root_state_w"), "navigation terminal root")
    if (region_agent.get("selected_region_id") != region_id
            or _mapping(region_record.get("robot_regions"), "robot regions").get(producer)
            != region_id
            or root != _root(snapshot_agent.get("root_state_w"), "snapshot terminal root")
            or root != _root(final_operation.get("last_root_state_w"), "operation terminal root")):
        raise ValueError("navigation terminal root or membership was substituted")
    center, tolerance = _region_geometry(deployment, region_id)
    distance = math.dist(root[:2], center)
    matches = [row for row in _list(region_agent.get("matches"), "region matches")
               if isinstance(row, Mapping) and row.get("region_id") == region_id]
    if len(matches) != 1:
        raise ValueError("navigation terminal lacks one exact region match")
    match = matches[0]
    if (not math.isclose(_time(match.get("distance_m"), "membership distance"), distance,
                         rel_tol=0, abs_tol=1e-9)
            or _time(match.get("tolerance_m"), "membership tolerance") != tolerance
            or distance > tolerance
            or _time(final_operation.get("distance_to_goal_m"), "operation distance")
            > tolerance
            or _time(final_operation.get("maximum_tilt_rad"), "operation maximum tilt")
            > navigation_probe.MAXIMUM_TILT_RAD
            or navigation_probe._tilt(root) > navigation_probe.MAXIMUM_TILT_RAD):
        raise ValueError("navigation terminal is out-of-region or tipped")
    evidence = _list(physical.get("native_evidence"), "navigation native_evidence")
    if len(evidence) != 1 or evidence[0] != final_operation:
        raise ValueError("navigation physical record changed its terminal operation")
    result = _result_binding(
        task_id, episode_id=episode_id, task=task, physical=physical,
        pump_task=pump_task, session_task=session_task, pump=pump,
        communication=communication, audit_journal=audit_journal)
    if not result["candidate_eligible"]:
        return None
    return {
        "task_id": task_id,
        "producer_id": producer,
        "region_id": region_id,
        "physical_time_s": physical_time,
        "physics_step": physics_step,
        "evidence_ref": physical["evidence_ref"],
        "dispatch_message_id": dispatch_id,
        "result": result,
    }


def _observation_candidate(
    physical: Mapping[str, Any], *, episode_id: str, deployment: Mapping[str, Any],
    pump: Mapping[str, Any], session: Mapping[str, Any],
    communication: Mapping[str, Any], audit_journal: Mapping[str, Any],
) -> dict[str, Any] | None:
    task_id = _text(physical.get("task_id"), "observation task_id")
    task = _mapping(physical.get("task"), "observation task")
    arguments = _mapping(task.get("arguments"), "observation arguments")
    if (task.get("skill") != "observe"
            or arguments.get("observation_mode") != "service_ground_readiness"):
        raise ValueError("observation physical record has the wrong skill or mode")
    agents = _string_list(task.get("assigned_agents"), "observation assigned_agents")
    if len(agents) != 1:
        raise ValueError("business observation must have one producer")
    producer = agents[0]
    region_id = _text(arguments.get("region_id"), "observation region_id")
    pump_task, session_task = _task_binding(
        task_id, task, pump=pump, session=session, physical=physical)
    if (physical.get("status") != "succeeded"
            or physical.get("disposition") != "native_ground_readiness_observation"):
        raise ValueError("selected observation record is not a native readiness result")
    started = _time(pump_task.get("physical_started_at_s"), "observation start")
    dispatch_id = _dispatch_binding(
        task_id, episode_id=episode_id, task=task, pump_task=pump_task,
        session_task=session_task, communication=communication,
        physical_started_at_s=started)
    evidence = _list(physical.get("native_evidence"), "observation native_evidence")
    if len(evidence) != 1:
        raise ValueError("observation physical record lacks one native observation")
    native = _sealed(evidence[0], "native observation")
    stamp = _mapping(native.get("stamp"), "native observation stamp")
    physical_time = _time(physical.get("physical_terminal_at_s"), "observation time")
    physics_step = _step(stamp.get("simulation_step"), "observation physics_step")
    scene = _mapping(native.get("scene_observation"), "scene observation")
    service = _mapping(native.get("service_record"), "service observation record")
    service_id = _text(service.get("observation_id"), "service observation_id")
    if (native.get("kind") != "observation"
            or native.get("episode_id") != episode_id
            or native.get("task_id") != task_id
            or native.get("producer_id") != producer
            or native.get("region_id") != region_id
            or _time(stamp.get("time_s"), "observation stamp time") != physical_time
            or physics_step != _step(
                _mapping(physical.get("fleet_snapshot"), "observation snapshot").get(
                    "physics_step"), "observation fleet step")
            or scene.get("producer_id") != producer
            or scene.get("region_id") != region_id
            or scene.get("stamp") != stamp
            or service.get("episode_id") != episode_id
            or service.get("producer_id") != producer
            or service.get("region_id") != region_id
            or service.get("simulation_step") != physics_step
            or _time(service.get("time_s"), "service observation time") != physical_time):
        raise ValueError("native observation identity, root or stamp mismatch")
    center, tolerance = _region_geometry(deployment, region_id)
    root = _root(stamp.get("root_state_w"), "observation root")
    if (scene.get("region_center_xy_m") != center
            or scene.get("observed_robot_xy_m") != root[:2]
            or _time(scene.get("region_tolerance_m"), "observation tolerance") != tolerance
            or not math.isclose(_time(scene.get("region_error_m"), "observation error"),
                                math.dist(root[:2], center), rel_tol=0, abs_tol=1e-9)
            or math.dist(root[:2], center) > tolerance
            or navigation_probe._tilt(root) > navigation_probe.MAXIMUM_TILT_RAD):
        raise ValueError("native readiness observation is out-of-region or tipped")
    expected_digest = "sha256:" + canonical_sha256({
        "episode_id": episode_id,
        "region_id": region_id,
        "producer_id": producer,
        "scene_observation": scene,
    })
    if service.get("evidence_digest") != expected_digest:
        raise ValueError("service observation digest differs from native scene evidence")
    adapted = deepcopy(dict(native))
    adapted_scene = deepcopy(dict(scene))
    adapted_scene["producer_id"] = ROBOT_ID
    adapted["scene_observation"] = adapted_scene
    if _verify_archived_scene_observation(
            adapted, {"region_bindings": deployment["region_bindings"]}) != region_id:
        raise ValueError("native readiness physical predicate changed region")
    region_record = _mapping(physical.get("region_record"), "observation region record")
    region_agent = _mapping(
        _mapping(region_record.get("agents"), "observation region agents").get(producer),
        "observation producer membership")
    snapshot_agent = _mapping(
        _mapping(
            _mapping(physical.get("fleet_snapshot"), "observation fleet snapshot").get(
                "agents"), "observation snapshot agents").get(producer),
        "observation snapshot producer")
    if (region_agent.get("selected_region_id") != region_id
            or _mapping(region_record.get("robot_regions"), "observation robot regions").get(
                producer) != region_id
            or _root(region_agent.get("root_state_w"), "observation membership root")
            != root
            or _root(snapshot_agent.get("root_state_w"), "observation snapshot root")
            != root):
        raise ValueError("support query is under an out-of-region body")
    result = _result_binding(
        task_id, episode_id=episode_id, task=task, physical=physical,
        pump_task=pump_task, session_task=session_task, pump=pump,
        communication=communication, audit_journal=audit_journal)
    if not result["candidate_eligible"]:
        return None
    participant = _mapping(result.get("participant_observation"), "participant observation")
    data = _mapping(participant.get("data"), "participant observation data")
    if (participant.get("kind") != "restaurant_ground_readiness_observation"
            or data.get("task_id") != task_id
            or data.get("region_id") != region_id
            or data.get("producer_id") != producer
            or data.get("service_observation_id") != service_id
            or data.get("evidence_ref") != native["evidence_ref"]
            or data.get("scene_observation_validated") is not True
            or _time(data.get("measured_at_s"), "public observation measured_at_s")
            != physical_time):
        raise ValueError("public observation differs from native readiness evidence")
    message_id = result["message_id"]
    events = [event for event in _list(session.get("events"), "session events")
              if isinstance(event, Mapping) and event.get("type") == "observation"
              and isinstance(event.get("payload"), Mapping)
              and event["payload"].get("message_id") == message_id]
    if result["availability_status"] == "delivered":
        if (len(events) != 1
                or _time(events[0].get("time_s"), "observation event time")
                != result["available_at_s"]
                or events[0]["payload"].get("data") != data):
            raise ValueError("native observation was not emitted at host consumption")
    elif events:
        raise ValueError("undelivered native observation appears in public session events")
    return {
        "task_id": task_id,
        "producer_id": producer,
        "region_id": region_id,
        "physical_time_s": physical_time,
        "physics_step": physics_step,
        "evidence_ref": physical["evidence_ref"],
        "native_evidence_ref": native["evidence_ref"],
        "service_observation_id": service_id,
        "dispatch_message_id": dispatch_id,
        "result": result,
        "native": native,
    }


def _gap(
    milestone_id: str, code: str, detail: str, *,
    classification: str = "platform_evidence_gap",
) -> dict[str, str]:
    return {
        "classification": classification,
        "milestone_id": milestone_id,
        "code": code,
        "detail": detail,
    }


def _missing(
    milestone_id: str, code: str, detail: str, *,
    classification: str = "platform_evidence_gap",
) -> tuple[dict[str, Any], dict[str, str]]:
    return ({
        "status": "missing_evidence",
        "physical_time_s": None,
        "available_at_s": None,
        "availability_status": "not_delivered",
        "availability_event_kind": (
            RECEIPT_AVAILABILITY if milestone_id == RECEIPT_OUTCOME
            else INSPECTION_AVAILABILITY),
        "transport_outcome": None,
        "transport_reason": None,
        "producer_id": None,
        "region_id": None,
        "task_ids": [],
        "physics_steps": [],
        "evidence_refs": [],
        "message_ids": [],
        "source_refs": {},
    }, _gap(milestone_id, code, detail, classification=classification))


def _inspection_milestone(chain: Mapping[str, Any]) -> dict[str, Any]:
    arrival = _mapping(chain.get("arrival"), "inspection arrival")
    observation = _mapping(chain.get("observation"), "inspection observation")
    arrival_result = _mapping(arrival.get("result"), "arrival result")
    observation_result = _mapping(observation.get("result"), "observation result")
    return {
        "status": "verified",
        "physical_time_s": observation["physical_time_s"],
        "arrival_physical_time_s": arrival["physical_time_s"],
        "available_at_s": observation_result["available_at_s"],
        "arrival_available_at_s": arrival_result["available_at_s"],
        "availability_status": observation_result["availability_status"],
        "availability_event_kind": INSPECTION_AVAILABILITY,
        "transport_delivered_at_s": observation_result["transport_delivered_at_s"],
        "transport_outcome": observation_result["transport_outcome"],
        "transport_reason": observation_result["transport_reason"],
        "producer_id": observation["producer_id"],
        "arrival_producer_id": arrival["producer_id"],
        "region_id": observation["region_id"],
        "task_ids": [arrival["task_id"], observation["task_id"]],
        "physics_steps": [arrival["physics_step"], observation["physics_step"]],
        "evidence_refs": [
            arrival["evidence_ref"], observation["evidence_ref"],
            observation["native_evidence_ref"],
        ],
        "message_ids": [
            arrival["dispatch_message_id"], arrival_result["message_id"],
            observation["dispatch_message_id"], observation_result["message_id"],
        ],
        "source_refs": {
            "arrival_receipt_ref": arrival_result["receipt_ref"],
            "observation_receipt_ref": observation_result["receipt_ref"],
            "service_observation_id": observation["service_observation_id"],
        },
    }


def verify_restaurant_business_v3(
    *,
    public_task: Mapping[str, Any],
    deployment_companion: Mapping[str, Any],
    episode_metadata: Mapping[str, Any],
    context_report: Mapping[str, Any],
    session_report: Mapping[str, Any],
    audit_journal: Mapping[str, Any],
    audit_replay: Mapping[str, Any],
    pump_report: Mapping[str, Any],
    communication_report: Mapping[str, Any],
    communication_replay: Mapping[str, Any],
    archived_source_sha256: Mapping[str, str],
    expected_source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Recompute auditable full8 Restaurant business milestone facts.

    Missing otherwise well-formed evidence produces an ``incomplete`` review
    and explicit platform gaps.  Contradictory, foreign, stale, substituted or
    unmetered evidence raises ``ValueError`` because it invalidates the archive
    rather than describing a participant planning failure.
    """
    public_task = _mapping(public_task, "public_task")
    deployment = _mapping(deployment_companion, "deployment_companion")
    metadata = _mapping(episode_metadata, "episode_metadata")
    context = _mapping(context_report, "context_report")
    session = _mapping(session_report, "session_report")
    journal = _mapping(audit_journal, "audit_journal")
    supplied_audit = _mapping(audit_replay, "audit_replay")
    pump = _mapping(pump_report, "pump_report")
    communication = _mapping(communication_report, "communication_report")
    supplied_communication = _mapping(communication_replay, "communication_replay")
    sources, source_digest = _source_identity(
        archived_source_sha256, expected_source_sha256)
    episode_id, task_id, task_digest, _participant_api, replays = (
        _validate_archive_identity(
            public_task=public_task, deployment=deployment,
            episode_metadata=metadata, context=context, session=session,
            audit_journal=journal, audit_replay=supplied_audit, pump=pump,
            communication=communication,
            communication_replay=supplied_communication))

    _, physical_by_task = _physical_index(pump, episode_id)
    navigation: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    relay_physical: list[Mapping[str, Any]] = []
    failed_relay_transport = False
    for rows in physical_by_task.values():
        for physical in rows:
            task = _mapping(physical.get("task"), "physical task")
            if (task.get("skill") == "relay"
                    and str(physical.get("disposition", "")).startswith(
                        "relay_transport_")):
                failed_relay_transport = True
            if physical.get("status") != "succeeded":
                continue
            skill = task.get("skill")
            if skill == "ground_navigation":
                candidate = _navigation_candidate(
                    physical, episode_id=episode_id, public_task=public_task,
                    deployment=deployment, pump=pump, session=session,
                    communication=communication, audit_journal=journal)
                if candidate is not None:
                    navigation.append(candidate)
            elif skill == "observe" and (task.get("arguments") or {}).get(
                    "observation_mode") == "service_ground_readiness":
                candidate = _observation_candidate(
                    physical, episode_id=episode_id, deployment=deployment,
                    pump=pump, session=session, communication=communication,
                    audit_journal=journal)
                if candidate is not None:
                    observations.append(candidate)
            elif skill == "relay":
                relay_physical.append(physical)

    readiness = _mapping(pump.get("readiness"), "readiness archive")
    readiness_service = _mapping(
        readiness.get("readiness_service"), "readiness service")
    service_observations = _mapping(
        readiness_service.get("observations"), "readiness observations")
    relay_publications = _list(
        readiness_service.get("relay_publications"), "relay publications")
    service_receipt = readiness_service.get("receipt")
    readiness_native: dict[str, Mapping[str, Any]] = {}
    for raw in _list(readiness.get("native_evidence"), "readiness native_evidence"):
        row = _sealed(raw, "readiness native evidence")
        if row.get("episode_id") != episode_id:
            raise ValueError("readiness evidence carries a foreign episode")
        if row["evidence_ref"] in readiness_native:
            raise ValueError("duplicate readiness native evidence_ref")
        readiness_native[row["evidence_ref"]] = row
    for observation in observations:
        native = observation["native"]
        if (readiness_native.get(native["evidence_ref"]) != native
                or service_observations.get(observation["service_observation_id"])
                != native.get("service_record")):
            raise ValueError("pump observation differs from readiness service archive")

    milestones: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, str]] = []
    valid_inspections: dict[str, list[dict[str, Any]]] = {}
    for region_id, milestone_id in REQUIRED_OUTCOMES.items():
        region_observations = [row for row in observations if row["region_id"] == region_id]
        region_arrivals = [row for row in navigation if row["region_id"] == region_id]
        chains = []
        for observation in region_observations:
            for arrival in region_arrivals:
                if arrival["physical_time_s"] >= observation["physical_time_s"]:
                    continue
                if (_member_exit_between(
                        journal, arrival["producer_id"], arrival["physical_time_s"],
                        observation["physical_time_s"])
                        or _member_exit_between(
                            journal, observation["producer_id"], arrival["physical_time_s"],
                            observation["physical_time_s"])):
                    continue
                chains.append({"arrival": arrival, "observation": observation})
        chains.sort(key=lambda row: (
            row["observation"]["physical_time_s"],
            (row["observation"]["result"]["available_at_s"]
             if row["observation"]["result"]["available_at_s"] is not None
             else math.inf),
            row["arrival"]["physical_time_s"],
            row["observation"]["task_id"], row["arrival"]["task_id"]))
        valid_inspections[region_id] = chains
        if chains:
            milestones[milestone_id] = _inspection_milestone(chains[0])
        else:
            milestone, gap = _missing(
                milestone_id, "inspection_chain_missing",
                f"no later valid native ground-readiness observation follows a {region_id} arrival")
            milestone["region_id"] = region_id
            milestones[milestone_id] = milestone
            gaps.append(gap)

    valid_relays: list[dict[str, Any]] = []
    if service_receipt is not None:
        service_receipt = _mapping(service_receipt, "readiness service receipt")
        review = evaluate_readiness_receipt_v2(
            episode_id=episode_id, receipt=service_receipt,
            observations=service_observations,
            relay_publications=relay_publications)
        selected_message_id = _text(
            review.get("selected_relay_message_id"), "selected relay message_id")
        for physical in relay_physical:
            task_id_value = _text(physical.get("task_id"), "relay task_id")
            task = _mapping(physical.get("task"), "relay task")
            pump_task, session_task = _task_binding(
                task_id_value, task, pump=pump, session=session, physical=physical)
            result = _result_binding(
                task_id_value, episode_id=episode_id, task=task, physical=physical,
                pump_task=pump_task, session_task=session_task, pump=pump,
                communication=communication, audit_journal=journal)
            started = _time(pump_task.get("physical_started_at_s"), "relay start")
            dispatch_id = _dispatch_binding(
                task_id_value, episode_id=episode_id, task=task,
                pump_task=pump_task, session_task=session_task,
                communication=communication, physical_started_at_s=started)
            publication = _mapping(pump_task.get("relay_publication"), "relay publication evidence")
            publication = _sealed(publication, "relay publication evidence")
            native_rows = _list(physical.get("native_evidence"), "relay native evidence")
            if len(native_rows) != 1:
                raise ValueError("relay terminal lacks one native service receipt")
            terminal_native = _sealed(native_rows[0], "native readiness receipt")
            if (readiness_native.get(publication["evidence_ref"]) != publication
                    or readiness_native.get(terminal_native["evidence_ref"]) != terminal_native
                    or publication.get("relay_publication") not in relay_publications
                    or publication.get("relay_publication", {}).get("message_id")
                    != selected_message_id
                    or terminal_native.get("business_receipt") != service_receipt
                    or terminal_native.get("relay_evidence_ref") != publication["evidence_ref"]):
                raise ValueError("relay publication/receipt native binding mismatch")
            relay_message = _mapping(
                _mapping(communication.get("messages"), "communication messages").get(
                    selected_message_id), "metered relay message")
            relay_payload = _mapping(relay_message.get("payload"), "relay payload")
            relay_wire = _mapping(relay_payload.get("estimate"), "relay wire")
            service_delivery = _mapping(
                _mapping(relay_message.get("recipients"), "relay recipients").get(
                    SERVICE_RECIPIENT), "relay service delivery")
            service_consumption = _mapping(
                service_delivery.get("consumption"), "relay service consumption")
            service_commit = _time(service_receipt.get("time_s"), "service commit time")
            relay_link = _time(service_delivery.get("delivered_at_s"), "relay link delivery")
            if (relay_message.get("channel") != RELAY_CHANNEL
                    or relay_payload.get("message_id") != selected_message_id
                    or relay_wire.get("episode_id") != episode_id
                    or relay_wire.get("relay_message_id") != selected_message_id
                    or relay_wire.get("relay_publication")
                    != publication.get("relay_publication")
                    or relay_wire.get("source_evidence_ref") != publication["evidence_ref"]
                    or relay_wire.get("observation_refs")
                    != service_receipt.get("observation_refs")
                    or service_consumption.get("message_id") != selected_message_id
                    or _time(service_consumption.get("time_s"), "service consumption time")
                    != service_commit
                    or not _time(publication.get("publication_stamp", {}).get(
                        "time_s"), "relay publication time") <= relay_link <= service_commit
                    or _time(physical.get("physical_terminal_at_s"), "relay physical time")
                    != service_commit):
                raise ValueError("readiness relay was forged, lost or unmetered")
            refs = _string_list(service_receipt.get("observation_refs"), "receipt observations")
            if len(refs) != len(REQUIRED_OUTCOMES):
                raise ValueError("readiness receipt does not bind exactly the required regions")
            selected_chains: dict[str, dict[str, Any]] = {}
            for region_id in REQUIRED_OUTCOMES:
                matches = [chain for chain in valid_inspections.get(region_id, [])
                           if chain["observation"]["service_observation_id"] in refs]
                if matches:
                    selected_chains[region_id] = matches[0]
            if set(selected_chains) != set(REQUIRED_OUTCOMES):
                continue
            valid_relays.append({
                "physical": physical,
                "task": task,
                "pump_task": pump_task,
                "result": result,
                "publication": publication,
                "terminal_native": terminal_native,
                "service_receipt": service_receipt,
                "service_commit_at_s": service_commit,
                "service_transport_delivered_at_s": relay_link,
                "dispatch_message_id": dispatch_id,
                "selected_chains": selected_chains,
                "review": review,
            })
    valid_relays.sort(key=lambda row: (
        row["service_commit_at_s"],
        (row["result"]["available_at_s"]
         if row["result"]["available_at_s"] is not None else math.inf),
        row["physical"]["task_id"]))

    if valid_relays:
        selected = valid_relays[0]
        relay = selected
        relay_result = relay["result"]
        relay_physical_row = relay["physical"]
        milestones[RECEIPT_OUTCOME] = {
            "status": "verified",
            "physical_time_s": relay["service_commit_at_s"],
            "available_at_s": relay_result["available_at_s"],
            "availability_status": relay_result["availability_status"],
            "availability_event_kind": RECEIPT_AVAILABILITY,
            "transport_outcome": relay_result["transport_outcome"],
            "transport_reason": relay_result["transport_reason"],
            "service_committed_at_s": relay["service_commit_at_s"],
            "service_transport_delivered_at_s": relay[
                "service_transport_delivered_at_s"],
            "result_transport_delivered_at_s": relay_result[
                "transport_delivered_at_s"],
            "producer_id": _string_list(
                relay["task"].get("assigned_agents"), "relay assigned_agents")[0],
            "region_id": None,
            "task_ids": [relay_physical_row["task_id"]],
            "physics_steps": [_step(
                relay["service_receipt"].get("simulation_step"),
                "service receipt step")],
            "evidence_refs": [
                relay["publication"]["evidence_ref"],
                relay["terminal_native"]["evidence_ref"],
                relay_physical_row["evidence_ref"],
            ],
            "message_ids": [
                relay["dispatch_message_id"],
                relay["publication"]["relay_publication"]["message_id"],
                relay_result["message_id"],
            ],
            "source_refs": {
                "service_receipt_id": relay["service_receipt"]["receipt_id"],
                "result_receipt_ref": relay_result["receipt_ref"],
                "selected_observation_ids": list(
                    relay["service_receipt"]["observation_refs"]),
            },
        }
    else:
        if RECEIPT_OUTCOME not in milestones:
            observed_incomplete_chain = (failed_relay_transport
                or bool(observations) or bool(relay_publications) or any(
                row["result"]["transport_outcome"] in {"dropped", "expired"}
                for row in observations))
            milestone, gap = _missing(
                RECEIPT_OUTCOME, "readiness_receipt_chain_missing",
                "no valid metered readiness relay, service commit and participant result chain",
                classification=("observed_incomplete_business_chain"
                                if observed_incomplete_chain
                                else "platform_evidence_gap"))
            milestones[RECEIPT_OUTCOME] = milestone
            gaps.append(gap)

    for milestone_id in set(REQUIRED_OUTCOMES.values()) | {RECEIPT_OUTCOME}:
        if milestone_id not in milestones:
            milestone, gap = _missing(
                milestone_id, "milestone_missing", "required outcome has no verified evidence")
            milestones[milestone_id] = milestone
            gaps.append(gap)
    complete = all(row["status"] == "verified" for row in milestones.values())
    availability_complete = complete and all(
        row["availability_status"] == "delivered" for row in milestones.values())
    any_verified = any(row["status"] == "verified" for row in milestones.values())
    archive_hashes = {
        "public_task_sha256": task_digest,
        "deployment_sha256": canonical_sha256(deployment),
        "episode_metadata_sha256": canonical_sha256(metadata),
        "context_report_sha256": canonical_sha256(context),
        "session_report_sha256": canonical_sha256(session),
        "audit_journal_sha256": journal["journal_sha256"],
        "pump_report_sha256": canonical_sha256(pump),
        "communication_report_sha256": canonical_sha256(communication),
        "source_manifest_sha256": source_digest,
    }
    return {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "episode_id": episode_id,
        "task_id": task_id,
        "status": ("complete" if complete and availability_complete
                   else "partial" if any_verified else "incomplete"),
        "archive_facts_verified": True,
        "business_predicates_complete": complete,
        "public_availability_complete": availability_complete,
        "milestones": milestones,
        "evidence_gaps": gaps,
        "archive_hashes": archive_hashes,
        "source_identity": {
            "matched_expected_manifest": True,
            "file_count": len(sources),
            "manifest_sha256": source_digest,
            "native_truth_authenticated": False,
        },
        "replay": {
            "audit_recomputed": True,
            "audit_journal_sha256": replays["audit"]["journal_sha256"],
            "communication_recomputed": True,
            "communication_report_sha256": canonical_sha256(communication),
        },
        "integrity_scope": (
            "archive_identity_and_internal_replay_with_external_expected_source_manifest;"
            "not_independent_native_host_authentication"
        ),
        "native_worker_wiring_verified": False,
        "physical_authentication_verified": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "feasibility_oracle_verified": False,
        "release_ready": False,
        "scoreable": False,
    }


__all__ = [
    "INSPECTION_AVAILABILITY",
    "RECEIPT_AVAILABILITY",
    "REPORT_SCHEMA",
    "REPORT_VERSION",
    "verify_restaurant_business_v3",
]
