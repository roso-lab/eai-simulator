"""Independent native evidence binding for Restaurant readiness service v2.

This module verifies an already archived business chain.  It does not launch a
worker, infer a participant plan, or change any release gate.  Its inputs must
come from independently validated request/session/native/transport archives.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any

from demo.restaurant_benchmark.service_backend_v2 import (
    SCHEMA as READINESS_SCHEMA,
    evaluate_readiness_receipt_v2,
)

from .communication import validate_communication_report
from .provenance import canonical_sha256


REPORT_SCHEMA = "eai.restaurant.native_business_review.v2"
RELAY_WIRE_SCHEMA = "eai.restaurant.readiness_relay_wire.v1"
RELAY_CHANNEL = "restaurant_readiness"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _step(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def _stamp(value: Any, label: str) -> tuple[int, float]:
    row = _mapping(value, label)
    return _step(row.get("simulation_step"), f"{label}.simulation_step"), _time(
        row.get("time_s"), f"{label}.time_s")


def _strictly_before(left: Any, right: Any, label: str) -> None:
    left_step, left_time = _stamp(left, f"{label} earlier stamp")
    right_step, right_time = _stamp(right, f"{label} later stamp")
    if not left_step < right_step or not left_time < right_time:
        raise ValueError(f"{label} must advance native step and time")


def _evidence_index(native_evidence: Any, episode_id: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(native_evidence, list):
        raise ValueError("native_evidence must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for row in native_evidence:
        row = _mapping(row, "native evidence row")
        if row.get("episode_id") != episode_id:
            raise ValueError("native evidence carries a foreign episode_id")
        ref = _text(row.get("evidence_ref"), "native evidence_ref")
        body = {key: value for key, value in row.items() if key != "evidence_ref"}
        if ref != "sha256:" + canonical_sha256(body):
            raise ValueError("native evidence does not match its evidence_ref")
        if ref in result:
            raise ValueError("duplicate native evidence_ref")
        result[ref] = row
    return result


def _receipt_index(skill_receipts: Any, episode_id: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(skill_receipts, list):
        raise ValueError("skill_receipts must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for receipt in skill_receipts:
        receipt = _mapping(receipt, "skill receipt")
        if receipt.get("episode_id") != episode_id:
            raise ValueError("skill receipt carries a foreign episode_id")
        task_id = _text(receipt.get("task_id"), "skill receipt task_id")
        if task_id in result:
            raise ValueError("duplicate skill receipt task_id")
        result[task_id] = receipt
    return result


def _bind_task(
    native: Mapping[str, Any], *, skill: str, producer: str,
    session_tasks: Mapping[str, Any], receipts: Mapping[str, Mapping[str, Any]],
    evidence_refs: Sequence[str], evidence_time_s: float, episode_id: str,
    evidence_start_s: float | None = None,
) -> Mapping[str, Any]:
    task_id = _text(native.get("task_id"), "native task_id")
    session_row = _mapping(session_tasks.get(task_id), f"session task {task_id}")
    task = _mapping(session_row.get("task"), f"session task payload {task_id}")
    action_id = _text(session_row.get("dispatch_action_id"), "dispatch_action_id")
    receipt = _mapping(receipts.get(task_id), f"archived skill receipt {task_id}")
    session_receipt = _mapping(session_row.get("receipt"), f"session receipt {task_id}")
    refs = receipt.get("evidence_refs")
    dispatched_at = _time(session_row.get("dispatched_at_s"), "task dispatched_at_s")
    execution = _mapping(task.get("execution"), "task execution window")
    start = _time(execution.get("start_s"), "task execution start")
    duration = _time(execution.get("duration_s"), "task execution duration")
    completed = _time(receipt.get("completed_at_s"), "receipt completed_at_s")
    evidence_time = _time(evidence_time_s, "native evidence time")
    evidence_start = (
        None if evidence_start_s is None
        else _time(evidence_start_s, "native evidence start time"))
    if (session_row.get("status") != "succeeded"
            or task.get("task_id") != task_id
            or task.get("skill") != skill
            or task.get("assigned_agents") != [producer]
            or receipt != session_receipt
            or receipt.get("episode_id") != episode_id
            or receipt.get("task_id") != task_id
            or receipt.get("action_id") != action_id
            or receipt.get("status") != "succeeded"
            or not isinstance(refs, list)
            or len(refs) != len(set(refs))
            or not set(evidence_refs) <= set(refs)
            or (evidence_start is not None
                and (evidence_start < dispatched_at or evidence_start < start))
            or not dispatched_at <= evidence_time <= start + duration
            or evidence_time < start
            or evidence_time > completed):
        raise ValueError(f"native {skill} evidence is not bound to a succeeded dispatch receipt")
    return task


def _binding(robot_bindings: Mapping[str, Any], producer: str,
             native: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = _mapping(robot_bindings.get(producer), f"robot binding {producer}")
    if native.get("native_agent_id") != binding.get("native_agent_id"):
        raise ValueError("native evidence producer differs from its trusted robot binding")
    return binding


def _verify_arrival(
    row: Mapping[str, Any], *, episode_id: str, session_tasks: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]], robot_bindings: Mapping[str, Any],
    region_bindings: Mapping[str, Any],
) -> tuple[str, str, tuple[int, float]]:
    if row.get("kind") != "arrival":
        raise ValueError("arrival evidence has an invalid kind")
    producer = _text(row.get("producer_id"), "arrival producer_id")
    binding = _binding(robot_bindings, producer, row)
    arrival_stamp = _stamp(row.get("arrival"), "arrival stamp")
    task = _bind_task(
        row, skill="ground_navigation", producer=producer,
        session_tasks=session_tasks, receipts=receipts,
        evidence_refs=[row["evidence_ref"]], evidence_time_s=arrival_stamp[1],
        episode_id=episode_id,
        evidence_start_s=_time(row.get("started", {}).get("time_s"),
                               "arrival start time"))
    route_ids = (task.get("arguments") or {}).get("route_ids")
    region = _text(row.get("region_id"), "arrival region_id")
    if (not isinstance(route_ids, list) or not route_ids
            or any(not isinstance(route_id, str) or not route_id
                   for route_id in route_ids)
            or row.get("public_route_id") != route_ids[-1]
            or (task.get("arguments") or {}).get("region_id") != region):
        raise ValueError("arrival evidence does not match its dispatched route task")
    prim = _text(binding.get("robot_prim_path"), "trusted robot_prim_path").rstrip("/")
    identities = _mapping(row.get("contact_view_identities"),
                          "arrival contact view identities")
    for wheel, identity in identities.items():
        sensor_paths = (_mapping(identity, f"contact identity {wheel}")
                        .get("sensor_paths"))
        if (not isinstance(sensor_paths, list) or len(sensor_paths) != 1
                or not isinstance(sensor_paths[0], str)
                or not sensor_paths[0].startswith(prim + "/")):
            raise ValueError("arrival contact sensor does not belong to its trusted robot prim")
    from .goal_restaurant_episode import _verify_archived_contact

    _verify_archived_contact(row)
    _strictly_before(row.get("started"), row.get("arrival"), "native arrival")
    _verify_region_position(
        row.get("arrival"), region, region_bindings, "native arrival",
        require_3d=binding.get("mobility") == "aerial")
    return producer, region, arrival_stamp


def _verify_observation_physics(
    row: Mapping[str, Any], *, producer: str, region: str,
    region_bindings: Mapping[str, Any],
) -> None:
    scene = _mapping(row.get("scene_observation"), "native scene observation")
    if (row.get("producer_id") != producer
            or row.get("region_id") != region
            or scene.get("producer_id") != producer
            or scene.get("region_id") != region
            or scene.get("stamp") != row.get("stamp")):
        raise ValueError("native observation identity differs before physical validation")

    # The complete witness validator predates multi-agent evidence and embeds
    # ROBOT_ID.  Identity is checked above against the real task and binding;
    # only a deep copy is adapted for that legacy physical predicate checker.
    from .goal_restaurant_episode import ROBOT_ID, _verify_archived_scene_observation

    adapted = deepcopy(dict(row))
    adapted_scene = deepcopy(dict(scene))
    adapted_scene["producer_id"] = ROBOT_ID
    adapted["scene_observation"] = adapted_scene
    validated_region = _verify_archived_scene_observation(
        adapted, {"region_bindings": region_bindings})
    if validated_region != region:
        raise ValueError("legacy physical validator changed the observation region")


def _verify_region_position(
    stamp: Any, region: str, region_bindings: Mapping[str, Any], label: str,
    *, require_3d: bool = False,
) -> None:
    from demo.restaurant_benchmark import navigation_probe

    row = _mapping(stamp, label)
    _stamp(row, label)
    root = row.get("root_state_w")
    region_binding = _mapping(region_bindings.get(region), f"region binding {region}")
    expected = region_binding.get("center_xyz_m", region_binding.get("center_xy_m"))
    if (not isinstance(expected, (list, tuple)) or len(expected) not in {2, 3}
            or (require_3d and len(expected) != 3)
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in expected)):
        raise ValueError("trusted region binding lacks a finite center")
    dimensions = len(expected)
    tolerance = _time(
        region_binding.get("tolerance_m", navigation_probe.FINAL_TOLERANCE_M),
        f"region binding {region} tolerance_m")
    if (not isinstance(root, list) or not navigation_probe._finite_root_state(root)
            or math.dist(root[:dimensions], expected[:dimensions])
            > tolerance):
        raise ValueError(f"{label} is outside its trusted region binding")


def _relay_wire(
    *, episode_id: str, producer: str, publication: Mapping[str, Any],
    source_evidence_ref: str,
) -> dict[str, Any]:
    return {
        "schema": RELAY_WIRE_SCHEMA,
        "version": 1,
        "episode_id": episode_id,
        "producer_id": producer,
        "relay_message_id": publication["message_id"],
        "observation_refs": list(publication["observation_refs"]),
        "relay_publication": deepcopy(dict(publication)),
        "source_evidence_ref": source_evidence_ref,
    }


def trusted_task_regions_from_replayed_audit(
    journal: Mapping[str, Any], replay: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    """Extract completion-time regions only after independent audit replay.

    For each accepted ``complete_skill``, the first returned
    ``update_runtime_state`` before another completion must be at exactly the
    receipt completion time.  A final-report snapshot is never used as a
    historical fallback.
    """
    from .goal_session_audit import (
        GOAL_SESSION_AUDIT_REPLAY_SCHEMA,
        validate_goal_session_audit,
    )

    journal = validate_goal_session_audit(journal)
    replay = _mapping(replay, "goal session audit replay")
    if (replay.get("schema") != GOAL_SESSION_AUDIT_REPLAY_SCHEMA
            or replay.get("validated") is not True
            or replay.get("journal_sha256") != journal.get("journal_sha256")):
        raise ValueError("trusted task regions require a matching successful audit replay")
    final_tasks = _mapping(
        _mapping(journal.get("final_report"), "audit final report").get("tasks"),
        "audit final report tasks")
    calls = journal["calls"]
    result: dict[str, dict[str, str]] = {}
    for index, call in enumerate(calls):
        if (call.get("method") != "complete_skill"
                or call.get("outcome") != "returned"
                or not isinstance(call.get("result"), Mapping)
                or call["result"].get("accepted") is not True):
            continue
        arguments = _mapping(call.get("arguments"), "complete_skill arguments")
        task_id = _text(arguments.get("task_id"), "completed task_id")
        receipt = _mapping(arguments.get("receipt"), "completed task receipt")
        completed = _time(receipt.get("completed_at_s"), "completed receipt time")
        if (_time(arguments.get("time_s"), "complete_skill time") != completed
                or receipt.get("task_id") != task_id):
            raise ValueError("audit completion call and receipt time/task differ")
        update = None
        for candidate in calls[index + 1:]:
            if candidate.get("method") == "complete_skill":
                break
            if (candidate.get("method") == "update_runtime_state"
                    and candidate.get("outcome") == "returned"):
                update = candidate
                break
        if update is None:
            raise ValueError(f"completed task {task_id} lacks committed runtime state")
        update_args = _mapping(update.get("arguments"), "runtime update arguments")
        update_result = _mapping(update.get("result"), "runtime update result")
        if (_time(update_args.get("time_s"), "runtime update time") != completed
                or update_result != {
                    "time_s": update_args.get("time_s"),
                    "robot_regions": update_args.get("robot_regions"),
                    "object_states": update_args.get("object_states"),
                }):
            raise ValueError("completed task runtime update is not the matching committed state")
        task_row = _mapping(final_tasks.get(task_id), f"final task {task_id}")
        task = _mapping(task_row.get("task"), f"final task payload {task_id}")
        if (task_row.get("dispatch_action_id") != receipt.get("action_id")
                or task_row.get("receipt") != receipt):
            raise ValueError("final task dispatch/receipt differs from replayed completion")
        robots = _mapping(update_result.get("robot_regions"), "committed robot regions")
        assigned = task.get("assigned_agents")
        if (not isinstance(assigned, list) or not assigned
                or any(agent not in robots for agent in assigned)):
            raise ValueError("committed runtime state lacks a completed task producer")
        result[task_id] = {agent: _text(robots[agent], "committed robot region")
                           for agent in assigned}
    return result


def verify_restaurant_business_v2(
    *, episode_id: str, service_recipient: str,
    readiness_service: Mapping[str, Any], session_tasks: Mapping[str, Any],
    skill_receipts: list[Mapping[str, Any]], native_evidence: list[Mapping[str, Any]],
    robot_bindings: Mapping[str, Any], region_bindings: Mapping[str, Any],
    trusted_task_regions: Mapping[str, Mapping[str, str]],
    transport_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind readiness v2 to replayed session, physical, receipt and delivery facts.

    ``session_tasks`` must come from an independently replayed GoalSession
    report. ``transport_report`` must be the archived shared CommunicationRuntime
    report.  A successful return validates archive facts only; it never changes
    release flags or proves that a native worker has been wired to this API.
    """
    episode_id = _text(episode_id, "episode_id")
    service_recipient = _text(service_recipient, "service_recipient")
    service = _mapping(readiness_service, "readiness_service")
    session_tasks = _mapping(session_tasks, "session_tasks")
    robot_bindings = _mapping(robot_bindings, "robot_bindings")
    region_bindings = _mapping(region_bindings, "region_bindings")
    trusted_task_regions = _mapping(trusted_task_regions, "trusted_task_regions")
    if service.get("schema") != READINESS_SCHEMA or service.get("episode_id") != episode_id:
        raise ValueError("readiness service identity mismatch")
    observations = _mapping(service.get("observations"), "readiness observations")
    publications = service.get("relay_publications")
    receipt = _mapping(service.get("receipt"), "readiness receipt")
    review = evaluate_readiness_receipt_v2(
        episode_id=episode_id, receipt=receipt, observations=observations,
        relay_publications=publications)
    receipts = _receipt_index(skill_receipts, episode_id)
    evidence = _evidence_index(native_evidence, episode_id)

    arrivals: list[tuple[Mapping[str, Any], str, str, tuple[int, float]]] = []
    for row in evidence.values():
        if row.get("kind") == "arrival":
            task_id = row.get("task_id")
            task_row = session_tasks.get(task_id)
            archived_receipt = receipts.get(task_id)
            if (not isinstance(task_row, Mapping)
                    or not isinstance(archived_receipt, Mapping)):
                raise ValueError("arrival attempt lacks session task or receipt identity")
            if (task_row.get("status") != "succeeded"
                    or archived_receipt.get("status") != "succeeded"):
                continue
            producer, region, stamp = _verify_arrival(
                row, episode_id=episode_id, session_tasks=session_tasks,
                receipts=receipts, robot_bindings=robot_bindings,
                region_bindings=region_bindings)
            arrivals.append((row, producer, region, stamp))

    observation_bindings: dict[str, str] = {}
    for observation_id, service_row in observations.items():
        matches = [row for row in evidence.values()
                   if row.get("kind") == "observation"
                   and row.get("service_record") == service_row]
        if len(matches) != 1:
            raise ValueError("service observation lacks one unique native evidence row")
        native = matches[0]
        producer = _text(service_row.get("producer_id"), "observation producer_id")
        region = _text(service_row.get("region_id"), "observation region_id")
        _binding(robot_bindings, producer, native)
        task = _bind_task(
            native, skill="observe", producer=producer, session_tasks=session_tasks,
            receipts=receipts, evidence_refs=[native["evidence_ref"]],
            evidence_time_s=_time(native.get("stamp", {}).get("time_s"),
                                  "observation native time"),
            episode_id=episode_id)
        if (native.get("producer_id") != producer
                or native.get("region_id") != region
                or (task.get("arguments") or {}).get("region_id") != region
                or native.get("stamp", {}).get("simulation_step")
                != service_row.get("simulation_step")
                or native.get("stamp", {}).get("time_s") != service_row.get("time_s")):
            raise ValueError("service observation differs from its native task and stamp")
        expected_digest = "sha256:" + canonical_sha256({
            "episode_id": episode_id, "region_id": region,
            "producer_id": producer, "scene_observation": native.get("scene_observation"),
        })
        if service_row.get("evidence_digest") != expected_digest:
            raise ValueError("service observation digest differs from native scene evidence")
        _verify_observation_physics(
            native, producer=producer, region=region,
            region_bindings=region_bindings)
        observation_stamp = _stamp(native.get("stamp"), "observation stamp")
        prior = [row for row, arrival_producer, arrival_region, arrival_stamp in arrivals
                 if arrival_producer == producer and arrival_region == region
                 and arrival_stamp[0] < observation_stamp[0]
                 and arrival_stamp[1] < observation_stamp[1]]
        if not prior:
            raise ValueError("native observation lacks a prior physical arrival by its producer")
        observation_bindings[str(observation_id)] = native["evidence_ref"]

    transport_review = validate_communication_report(transport_report)
    if transport_review.get("validated") is not True:
        raise ValueError("readiness transport report failed deterministic replay")
    messages = _mapping(transport_report.get("messages"), "transport messages")
    publication_bindings: dict[str, str] = {}
    publication_producers: dict[str, str] = {}
    publication_tasks: dict[str, Mapping[str, Any]] = {}
    for publication in publications:
        matches = [row for row in evidence.values()
                   if row.get("kind") == "relay_publication"
                   and row.get("relay_publication") == publication]
        if len(matches) != 1:
            raise ValueError("service relay publication lacks one unique native evidence row")
        native = matches[0]
        producer = _text(native.get("producer_id"), "relay producer_id")
        binding = _binding(robot_bindings, producer, native)
        task = _bind_task(
            native, skill="relay", producer=producer, session_tasks=session_tasks,
            receipts=receipts, evidence_refs=[native["evidence_ref"]],
            evidence_time_s=_time(native.get("publication_stamp", {}).get("time_s"),
                                  "relay publication native time"),
            episode_id=episode_id)
        arguments = _mapping(task.get("arguments"), "relay task arguments")
        task_regions = _mapping(
            trusted_task_regions.get(native["task_id"]), "trusted relay task regions")
        region = _text(
            task_regions.get(producer), "trusted relay task producer region")
        declared_region = arguments.get("region_id")
        _text(arguments.get("message_kind"), "relay task message_kind")
        if (declared_region is not None
                and _text(declared_region, "relay task region_id") != region):
            raise ValueError("relay publication differs from replayed current robot region")
        stamp = _mapping(native.get("publication_stamp"), "relay publication stamp")
        if (_step(stamp.get("simulation_step"), "publication stamp step")
                != publication.get("simulation_step")
                or _time(stamp.get("time_s"), "publication stamp time")
                != publication.get("time_s")):
            raise ValueError("service relay publication differs from its native stamp")
        _verify_region_position(
            stamp, region, region_bindings, "relay publication stamp",
            require_3d=binding.get("mobility") == "aerial")
        message_id = _text(publication.get("message_id"), "relay message_id")
        message = _mapping(messages.get(message_id), f"transport message {message_id}")
        payload = _mapping(message.get("payload"), f"transport payload {message_id}")
        expected_wire = _relay_wire(
            episode_id=episode_id, producer=producer, publication=publication,
            source_evidence_ref=native["evidence_ref"])
        if (message.get("channel") != RELAY_CHANNEL
                or payload.get("message_id") != message_id
                or payload.get("producer_id") != producer
                or _time(payload.get("published_at_s"), "wire publication time")
                != _time(publication.get("time_s"), "service publication time")
                or payload.get("estimate") != expected_wire
                or service_recipient not in _mapping(
                    message.get("recipients"), "relay transport recipients")):
            raise ValueError("relay transport payload or trusted service recipient binding mismatch")
        publication_bindings[message_id] = native["evidence_ref"]
        publication_producers[message_id] = producer
        publication_tasks[message_id] = task

    selected_id = review["selected_relay_message_id"]
    selected_publication = next(
        row for row in publications if row["message_id"] == selected_id)
    selected_ref = publication_bindings[selected_id]
    selected_producer = publication_producers[selected_id]
    selected_task = publication_tasks[selected_id]
    terminal_matches = [row for row in evidence.values()
                        if row.get("kind") == "readiness_receipt"
                        and row.get("business_receipt") == receipt
                        and row.get("relay_evidence_ref") == selected_ref]
    if len(terminal_matches) != 1:
        raise ValueError("selected readiness receipt lacks one native terminal evidence row")
    terminal = terminal_matches[0]
    binding = _binding(robot_bindings, selected_producer, terminal)
    if (terminal.get("producer_id") != selected_producer
            or terminal.get("task_id")
            != evidence[selected_ref].get("task_id")):
        raise ValueError("terminal readiness receipt changed relay producer or task identity")
    _bind_task(
        terminal, skill="relay", producer=selected_producer,
        session_tasks=session_tasks, receipts=receipts,
        evidence_refs=[selected_ref, terminal["evidence_ref"]],
        evidence_time_s=_time(terminal.get("receipt_stamp", {}).get("time_s"),
                              "terminal receipt native time"),
        episode_id=episode_id)
    receipt_stamp = _mapping(terminal.get("receipt_stamp"), "native receipt stamp")
    if (_step(receipt_stamp.get("simulation_step"), "receipt stamp step")
            != receipt.get("simulation_step")
            or _time(receipt_stamp.get("time_s"), "receipt stamp time")
            != receipt.get("time_s")):
        raise ValueError("service receipt differs from its native terminal stamp")
    selected_arguments = _mapping(
        selected_task.get("arguments"), "selected relay task arguments")
    selected_task_regions = _mapping(
        trusted_task_regions.get(terminal["task_id"]),
        "trusted terminal task regions")
    selected_region = _text(
        selected_task_regions.get(selected_producer),
        "trusted terminal task producer region")
    declared_selected_region = selected_arguments.get("region_id")
    if (declared_selected_region is not None
            and _text(declared_selected_region, "selected relay region_id")
            != selected_region):
        raise ValueError("terminal receipt differs from replayed current robot region")
    _verify_region_position(
        receipt_stamp, selected_region, region_bindings, "readiness receipt stamp",
        require_3d=binding.get("mobility") == "aerial")
    _strictly_before(evidence[selected_ref]["publication_stamp"], receipt_stamp,
                     "selected relay publication and receipt")

    selected_message = _mapping(messages[selected_id], "selected relay message")
    delivery = _mapping(
        _mapping(selected_message.get("recipients"), "selected recipients").get(
            service_recipient), "selected service delivery")
    if delivery.get("status") != "delivered" or delivery.get("delivered_at_s") is None:
        raise ValueError("selected relay was not delivered to the trusted service before receipt")
    delivered_at = _time(delivery.get("delivered_at_s"), "selected delivered_at_s")
    published_at = _time(selected_publication.get("time_s"), "selected publication time")
    receipt_time = _time(receipt.get("time_s"), "selected receipt time")
    if not published_at <= delivered_at <= receipt_time:
        raise ValueError("selected relay was not delivered to the trusted service before receipt")

    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "episode_id": episode_id,
        "validated": True,
        "archive_facts_verified": True,
        "readiness_review": review,
        "service_recipient": service_recipient,
        "selected_delivery": {
            "message_id": selected_id,
            "producer_id": selected_producer,
            "delivered_at_s": delivered_at,
        },
        "observation_native_bindings": observation_bindings,
        "relay_native_bindings": publication_bindings,
        "transport_report_sha256": canonical_sha256(transport_report),
        "native_worker_wiring_verified": False,
        "physical_authentication_verified": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }


__all__ = [
    "RELAY_CHANNEL", "RELAY_WIRE_SCHEMA", "REPORT_SCHEMA",
    "trusted_task_regions_from_replayed_audit",
    "verify_restaurant_business_v2",
]
