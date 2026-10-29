"""Pure coalition request admission for the caller-owned logical runtime.

This module deliberately stops before execution.  It validates that a native
coalition can be handed to a runtime with the same member set and returns a
stable request payload; it never splits the request into single-robot skills.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from algorithm.common_coordination import CoalitionExecutionReceipt, CoalitionSkillRequest, ContractError
from task.resources import ResourceLease, ResourceManager


def prepare_coalition_request(
    request: CoalitionSkillRequest,
    *,
    deployed_robot_ids: Sequence[str],
    task_id: str,
) -> dict[str, Any]:
    """Validate and serialize a coalition request without starting execution."""
    if not isinstance(request, CoalitionSkillRequest):
        raise TypeError("request must be CoalitionSkillRequest")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ContractError("task_id is required")
    roster = tuple(sorted(str(robot_id).strip() for robot_id in deployed_robot_ids))
    if not roster or any(not robot_id for robot_id in roster) or len(set(roster)) != len(roster):
        raise ContractError("deployed robot roster must be unique and nonempty")
    if request.assignment.task_id != task_id:
        raise ContractError("coalition request task identity mismatch")
    if not set(request.assignment.robot_ids).issubset(roster):
        raise ContractError("coalition request contains a robot outside the deployed roster")
    payload = request.to_dict()
    payload["deployed_robot_ids"] = list(roster)
    payload["admission"] = "validated_native_member_set"
    return payload


def execute_coalition_skill(
    runtime: Any,
    request: CoalitionSkillRequest,
    *,
    object_id: str,
    semantic_point: str,
) -> Mapping[str, Any]:
    """Execute one host session with the complete native member set.

    The runtime must expose the logical-object ``execute`` contract.  Exactly
    one call is made with all members; this function never fans out requests
    and never turns a session result into an evaluator verdict.
    """
    if not hasattr(runtime, "execute") or not callable(runtime.execute):
        raise TypeError("runtime must provide logical-object execute")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ContractError("object_id is required")
    if not isinstance(semantic_point, str) or not semantic_point.strip():
        raise ContractError("semantic_point is required")
    result = runtime.execute(
        request_id=request.coalition_id,
        operation=request.skill,
        object_id=object_id,
        robot_ids=request.assignment.robot_ids,
        semantic_point=semantic_point,
    )
    if not isinstance(result, Mapping):
        raise ContractError("logical runtime returned a non-mapping session result")
    observed = tuple(sorted(str(item) for item in result.get("robot_ids", ())))
    if observed != request.assignment.robot_ids:
        raise ContractError("logical runtime session changed coalition membership")
    return dict(result)


def acquire_coalition_resources(
    manager: ResourceManager,
    request: CoalitionSkillRequest,
    *,
    extra_resources: Sequence[str] = (),
) -> ResourceLease:
    """Atomically lease every native member resource for one coalition."""
    if not isinstance(manager, ResourceManager):
        raise TypeError("manager must be ResourceManager")
    if isinstance(extra_resources, (str, bytes)):
        raise ContractError("extra_resources must be a sequence")
    resources = tuple(f"robot:{robot_id}" for robot_id in request.assignment.robot_ids)
    resources += tuple(str(item).strip() for item in extra_resources)
    if any(not item for item in resources) or len(set(resources)) != len(resources):
        raise ContractError("coalition resource identifiers must be unique and nonempty")
    return manager.acquire(request.coalition_id, resources)


def build_coalition_receipt(request: CoalitionSkillRequest, session: Mapping[str, Any]) -> CoalitionExecutionReceipt:
    """Bind a host session result to the native request without scoring it."""
    if tuple(sorted(str(item) for item in session.get("robot_ids", ()))) != request.assignment.robot_ids:
        raise ContractError("session receipt changed native coalition membership")
    return CoalitionExecutionReceipt(
        coalition_id=request.coalition_id, task_id=request.assignment.task_id,
        robot_ids=request.assignment.robot_ids, status=str(session.get("status", "")),
        session_id=str(session.get("session_id", "")),
        evidence_digest=str(session.get("evidence_digest", "")),
        evaluator_evidence_ref=session.get("evaluator_evidence_ref"),
    )


def bind_coalition_receipt_identity(
    receipt: CoalitionExecutionReceipt,
    identity_records: Mapping[str, Any],
) -> dict[str, Any]:
    """Add a coalition session reference to evaluator-owned identity records.

    This preserves the supplied records and adds provenance only.  It does not
    create milestones, base-task success, holder intervals, or a verdict.
    """
    if not isinstance(receipt, CoalitionExecutionReceipt):
        raise TypeError("receipt must be CoalitionExecutionReceipt")
    if not isinstance(identity_records, Mapping):
        raise ContractError("identity_records must be a mapping")
    result = dict(identity_records)
    if "coalition_execution" in result:
        raise ContractError("identity_records already contain coalition_execution")
    result["coalition_execution"] = receipt.to_dict()
    return result


def validate_coalition_holder_evidence(
    receipt: CoalitionExecutionReceipt,
    hold_intervals: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Bind completed coalition sessions to independently recorded holders."""
    if not isinstance(receipt, CoalitionExecutionReceipt):
        raise TypeError("receipt must be CoalitionExecutionReceipt")
    if not isinstance(hold_intervals, Sequence) or isinstance(hold_intervals, (str, bytes)):
        raise ContractError("hold_intervals must be a sequence")
    observed = set()
    for row in hold_intervals:
        if not isinstance(row, Mapping):
            raise ContractError("hold interval must be a mapping")
        if row.get("object_id") != "heavy_object":
            continue
        robot_id = row.get("robot_id")
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise ContractError("hold interval robot_id is required")
        observed.add(robot_id)
    if receipt.status == "completed" and observed != set(receipt.robot_ids):
        raise ContractError("completed coalition receipt does not match independent holder evidence")
    if not observed.issubset(set(receipt.robot_ids)):
        raise ContractError("holder evidence contains a robot outside the coalition receipt")
    return tuple(sorted(observed))


def validate_coalition_terminal_evidence(
    receipt: CoalitionExecutionReceipt,
    hold_intervals: Sequence[Mapping[str, Any]],
    *,
    sync_window_ticks: tuple[int, int],
    release_tick: int | None = None,
) -> dict[str, Any]:
    """Check independent continuity/release evidence for a completed session."""
    if receipt.status != "completed":
        return {"status": "not_evaluable", "reason": "coalition_session_not_completed"}
    if (not isinstance(sync_window_ticks, tuple) or len(sync_window_ticks) != 2
            or any(type(item) is not int or item < 0 for item in sync_window_ticks)
            or sync_window_ticks[0] > sync_window_ticks[1]):
        raise ContractError("sync_window_ticks must be an ordered nonnegative pair")
    start, end = sync_window_ticks
    if release_tick is not None and (type(release_tick) is not int or release_tick < end):
        raise ContractError("release_tick must follow the sync window")
    coverage: dict[str, list[tuple[int, int]]] = {robot: [] for robot in receipt.robot_ids}
    for row in hold_intervals:
        if not isinstance(row, Mapping) or row.get("object_id") != "heavy_object":
            continue
        robot = row.get("robot_id")
        if robot in coverage:
            begin, finish = row.get("start_tick"), row.get("end_tick")
            if type(begin) is not int or type(finish) is not int or finish <= begin:
                raise ContractError("holder interval ticks are invalid")
            coverage[robot].append((begin, finish))
    missing = [robot for robot, intervals in coverage.items()
               if not any(begin <= start and finish >= end for begin, finish in intervals)]
    if missing:
        raise ContractError(f"coalition members lack continuous sync-window evidence: {missing}")
    return {"status": "evidence_complete", "coalition_id": receipt.coalition_id,
            "robot_ids": list(receipt.robot_ids), "sync_window_ticks": [start, end],
            "release_tick": release_tick}


__all__ = ["prepare_coalition_request", "execute_coalition_skill", "acquire_coalition_resources", "build_coalition_receipt", "bind_coalition_receipt_identity", "validate_coalition_holder_evidence", "validate_coalition_terminal_evidence"]
