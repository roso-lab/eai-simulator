"""Trusted execution dispatch and cancellation over the shared transport.

The downlink owns transport state only.  It never calls an execution backend
and never exposes its report to the participant.  The episode runner remains
the sole owner of physical execute/cancel calls and may act only on newly
returned ready operations.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .communication import CommunicationRuntime
from .provenance import canonical_sha256


_PENDING_DELIVERY = {"queued", "in_transit"}


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _time(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{label} must be a finite nonnegative number")
    return float(value)


class GoalExecutionDownlink:
    """Delivery-gate dispatch and cancellation on one CommunicationRuntime."""

    def __init__(self, public_task: Mapping[str, Any], *, episode_id: str,
                 runtime: CommunicationRuntime, ttl_s: float = 30.0):
        if not isinstance(public_task, Mapping):
            raise ValueError("public_task must be an object")
        if not isinstance(runtime, CommunicationRuntime):
            raise ValueError("runtime must be a CommunicationRuntime")
        self.episode_id = _nonempty(episode_id, "episode_id")
        self.public_task_sha256 = canonical_sha256(public_task)
        self.runtime = runtime
        self.ttl_s = _time(ttl_s, "ttl_s")
        if self.ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        pools = runtime.report().get("contract", {}).get("pools", {})
        if "backhaul_downlink" not in pools:
            raise ValueError(
                "execution downlink requires an explicit backhaul_downlink pool")
        self._task_id = _nonempty(public_task.get("task_id"), "public task id")
        self._dispatches: dict[str, dict[str, Any]] = {}
        self._ledger: list[dict[str, Any]] = []

    def _record(self, event: str, time_s: float, **fields: Any) -> None:
        self._ledger.append({
            "sequence": len(self._ledger), "event": event,
            "time_s": float(time_s), **deepcopy(fields),
        })

    def _publish(self, *, kind: str, task: Mapping[str, Any], action_id: str,
                 time_s: float, generation: int) -> dict[str, Any]:
        task_id = _nonempty(task.get("task_id"), "task_id")
        recipients = list(task.get("assigned_agents") or [])
        if (not recipients or len(recipients) != len(set(recipients))
                or any(not isinstance(item, str) or not item for item in recipients)):
            raise ValueError("assigned_agents must be unique nonempty robot IDs")
        message_id = (
            f"{self.episode_id}:execution:{kind}:{action_id}:g{generation}")
        estimate = {
            "schema": "eai.benchmark.goal_execution_command",
            "version": 1,
            "episode_id": self.episode_id,
            "task_id": task_id,
            "dispatch_action_id": (
                action_id if kind == "dispatch"
                else self._dispatches[task_id]["dispatch"]["action_id"]),
            "operation": kind,
            "generation": generation,
            "assigned_agents": recipients,
        }
        if kind == "dispatch":
            # The wire carries the complete immutable command.  A task ID or
            # digest alone would gate a hidden side channel and undercount the
            # bytes that the executor actually needs.
            estimate["task"] = deepcopy(dict(task))
            estimate["task_sha256"] = canonical_sha256(task)
        if kind == "cancel":
            estimate["cancel_action_id"] = action_id
        message = self.runtime.publish(
            message_id=message_id,
            producer_id="trusted_execution_host",
            recipients=recipients,
            channel=f"goal_execution_{kind}",
            estimate=estimate,
            published_at_s=time_s,
            valid_until_s=time_s + self.ttl_s,
            pool="backhaul_downlink",
            direction="downlink",
        )
        operation = {
            "kind": kind,
            "action_id": action_id,
            "message_id": message_id,
            "generation": generation,
            "recipients": recipients,
            "queued_at_s": time_s,
            "valid_until_s": time_s + self.ttl_s,
            "wire_size_bytes": message["size_bytes"],
            "status": "pending",
            "delivered_at_s": None,
            "failed_at_s": None,
            "failure_reason": None,
        }
        self._record(f"{kind}_queued", time_s, task_id=task_id,
                     action_id=action_id, message_id=message_id,
                     generation=generation, recipients=recipients,
                     wire_size_bytes=message["size_bytes"])
        return operation

    def queue_dispatch(self, task: Mapping[str, Any], *, dispatch_action_id: str,
                       time_s: float) -> dict[str, Any]:
        if not isinstance(task, Mapping):
            raise ValueError("task must be an object")
        task_id = _nonempty(task.get("task_id"), "task_id")
        action_id = _nonempty(dispatch_action_id, "dispatch_action_id")
        now = _time(time_s, "time_s")
        existing = self._dispatches.get(task_id)
        if existing is not None:
            if existing["dispatch"]["action_id"] != action_id:
                raise ValueError("task dispatch is already bound to another action")
            return deepcopy(existing["dispatch"])
        generation = 1
        row = {
            "task": deepcopy(dict(task)),
            "generation": generation,
            "tombstoned": False,
            "tombstone_reason": None,
            "physical_started": False,
            "physical_started_at_s": None,
            "terminal": None,
            "dispatch": None,
            "cancel": None,
        }
        self._dispatches[task_id] = row
        row["dispatch"] = self._publish(
            kind="dispatch", task=task, action_id=action_id,
            time_s=now, generation=generation)
        return deepcopy(row["dispatch"])

    def queue_cancel(self, task: Mapping[str, Any], *,
                     cancel_action_id: str | None, time_s: float) -> dict[str, Any]:
        if not isinstance(task, Mapping):
            raise ValueError("task must be an object")
        task_id = _nonempty(task.get("task_id"), "task_id")
        now = _time(time_s, "time_s")
        row = self._dispatches.get(task_id)
        if row is None:
            raise ValueError("cancellation requires a queued dispatch")
        action_id = (cancel_action_id if cancel_action_id is not None
                     else f"safety-stop:{row['dispatch']['action_id']}")
        action_id = _nonempty(action_id, "cancel_action_id")
        if row["cancel"] is not None:
            if row["cancel"]["action_id"] != action_id:
                raise ValueError("task cancellation is already bound to another action")
            return deepcopy(row["cancel"])
        row["tombstoned"] = True
        row["tombstone_reason"] = "cancel_requested"
        if row["dispatch"]["status"] == "pending":
            row["dispatch"]["status"] = "withdrawn"
        row["cancel"] = self._publish(
            kind="cancel", task=task, action_id=action_id,
            time_s=now, generation=row["generation"])
        self._record("dispatch_tombstoned", now, task_id=task_id,
                     dispatch_action_id=row["dispatch"]["action_id"],
                     reason="cancel_requested")
        return deepcopy(row["cancel"])

    def _message(self, operation: Mapping[str, Any]) -> Mapping[str, Any]:
        messages = self.runtime.report().get("messages", {})
        message = messages.get(operation["message_id"])
        if not isinstance(message, Mapping):
            raise RuntimeError("execution transport message disappeared from runtime")
        return message

    def _ready_failure(self, operation: Mapping[str, Any], now: float) -> str | None:
        """Revalidate a previously ready command at its actual start time."""
        message = self._message(operation)
        valid_until = float(message["payload"]["valid_until_s"])
        for recipient in operation["recipients"]:
            delivery = message["recipients"][recipient]
            if delivery.get("status") != "delivered":
                return f"{recipient}:{delivery.get('status')}"
            effective = min(
                valid_until,
                float(delivery.get("transport_valid_until_s", valid_until)))
            if now >= effective:
                return f"{recipient}:expired"
            if delivery.get("consumption") is None:
                return f"{recipient}:not_consumed"
        return None

    def _delivery_state(self, operation: dict[str, Any], now: float) -> str:
        """Return pending, ready, or failed and consume every fresh delivery."""
        message = self._message(operation)
        deliveries = message.get("recipients", {})
        statuses = {recipient: deliveries.get(recipient, {}).get("status")
                    for recipient in operation["recipients"]}
        failed = {recipient: status for recipient, status in statuses.items()
                  if status in {"dropped", "expired"}}
        if failed:
            operation.update(
                status="failed", failed_at_s=now,
                failure_reason=";".join(
                    f"{recipient}:{status}" for recipient, status in sorted(failed.items())))
            return "failed"
        if any(status in _PENDING_DELIVERY for status in statuses.values()):
            return "pending"
        if any(status != "delivered" for status in statuses.values()):
            raise RuntimeError("execution transport has an unknown delivery status")
        for recipient in operation["recipients"]:
            self.runtime.consume(
                operation["message_id"], recipient=recipient,
                consumer_id=(
                    f"goal_execution_downlink:{operation['kind']}:"
                    f"{operation['action_id']}:{recipient}"))
        delivered = [deliveries[recipient]["delivered_at_s"]
                     for recipient in operation["recipients"]]
        operation.update(status="ready", delivered_at_s=max(delivered))
        return "ready"

    def _tombstone(self, task_id: str, row: dict[str, Any], *,
                   reason: str, now: float) -> None:
        if row["tombstoned"]:
            return
        row["tombstoned"] = True
        row["tombstone_reason"] = reason
        if row["dispatch"]["status"] == "pending":
            row["dispatch"]["status"] = "withdrawn"
        self._record("dispatch_tombstoned", now, task_id=task_id,
                     dispatch_action_id=row["dispatch"]["action_id"],
                     reason=reason)

    def advance(self, time_s: float, *, active_agent_ids: Any,
                task_states: Mapping[str, Mapping[str, Any]],
                terminated: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Advance transport at a trusted physical time and return new outcomes."""
        now = _time(time_s, "time_s")
        if not isinstance(task_states, Mapping):
            raise ValueError("task_states must be an object")
        active = set(active_agent_ids)
        if any(not isinstance(item, str) or not item for item in active):
            raise ValueError("active_agent_ids must contain robot IDs")
        self.runtime.advance(now)
        outcomes = {name: [] for name in (
            "ready_dispatches", "ready_cancels",
            "failed_dispatches", "failed_cancels")}
        for task_id, row in self._dispatches.items():
            if row["terminal"] is not None:
                continue
            state = task_states.get(task_id)
            status = state.get("status") if isinstance(state, Mapping) else None
            invalidated = bool(state.get("invalidated_by")) if isinstance(state, Mapping) else True
            assigned = set(row["task"]["assigned_agents"])
            reason = ("episode_terminated" if terminated else
                      "task_removed" if state is None else
                      "task_invalidated" if invalidated else
                      "member_removed" if not assigned <= active else
                      "task_no_longer_pending" if status not in {"pending", "cancel_requested"}
                      else None)
            newly_invalidated = reason is not None and not row["tombstoned"]
            if reason is not None:
                self._tombstone(task_id, row, reason=reason, now=now)

            dispatch = row["dispatch"]
            if newly_invalidated and status == "pending":
                dispatch.update(status="failed", failed_at_s=now,
                                failure_reason=reason)
                outcome = {
                    "task_id": task_id,
                    "dispatch_action_id": dispatch["action_id"],
                    "message_id": dispatch["message_id"],
                    "generation": row["generation"],
                    "failed_at_s": now,
                    "reason": reason,
                    "task": deepcopy(row["task"]),
                }
                outcomes["failed_dispatches"].append(outcome)
                self._record("dispatch_failed", now, **{
                    key: value for key, value in outcome.items() if key != "task"})
            deadline = (float(row["task"]["execution"]["start_s"])
                        + float(row["task"]["execution"]["duration_s"]))
            ready_failure = None
            if (dispatch["status"] == "ready" and not row["physical_started"]
                    and not row["tombstoned"]):
                ready_failure = (
                    reason if status == "pending" and reason is not None
                    else "execution_deadline_elapsed" if now >= deadline
                    else self._ready_failure(dispatch, now))
            if ready_failure is not None:
                self._tombstone(task_id, row, reason=ready_failure, now=now)
                dispatch.update(status="failed", failed_at_s=now,
                                failure_reason=ready_failure)
                outcome = {
                    "task_id": task_id,
                    "dispatch_action_id": dispatch["action_id"],
                    "message_id": dispatch["message_id"],
                    "generation": row["generation"],
                    "failed_at_s": now,
                    "reason": ready_failure,
                    "task": deepcopy(row["task"]),
                }
                outcomes["failed_dispatches"].append(outcome)
                self._record("dispatch_failed", now, **{
                    key: value for key, value in outcome.items() if key != "task"})
            if (dispatch["status"] == "pending" and not row["tombstoned"]
                    and now >= deadline):
                self._tombstone(task_id, row,
                                reason="execution_deadline_elapsed", now=now)
                dispatch.update(
                    status="failed", failed_at_s=now,
                    failure_reason="execution_deadline_elapsed")
                outcome = {
                    "task_id": task_id,
                    "dispatch_action_id": dispatch["action_id"],
                    "message_id": dispatch["message_id"],
                    "generation": row["generation"],
                    "failed_at_s": now,
                    "reason": "execution_deadline_elapsed",
                    "task": deepcopy(row["task"]),
                }
                outcomes["failed_dispatches"].append(outcome)
                self._record("dispatch_failed", now, **{
                    key: value for key, value in outcome.items() if key != "task"})
            if dispatch["status"] == "pending" and not row["tombstoned"]:
                delivery = self._delivery_state(dispatch, now)
                if delivery == "ready":
                    if (status == "pending" and not invalidated
                            and assigned <= active and not terminated):
                        outcome = {
                            "task_id": task_id,
                            "dispatch_action_id": dispatch["action_id"],
                            "message_id": dispatch["message_id"],
                            "generation": row["generation"],
                            "delivered_at_s": dispatch["delivered_at_s"],
                            "task": deepcopy(row["task"]),
                        }
                        outcomes["ready_dispatches"].append(outcome)
                        self._record("dispatch_ready", now, **{
                            key: value for key, value in outcome.items() if key != "task"})
                    else:
                        self._tombstone(task_id, row, reason=reason or "stale_dispatch",
                                        now=now)
                elif delivery == "failed":
                    outcome = {
                        "task_id": task_id,
                        "dispatch_action_id": dispatch["action_id"],
                        "message_id": dispatch["message_id"],
                        "generation": row["generation"],
                        "failed_at_s": dispatch["failed_at_s"],
                        "reason": dispatch["failure_reason"],
                        "task": deepcopy(row["task"]),
                    }
                    outcomes["failed_dispatches"].append(outcome)
                    self._record("dispatch_failed", now, **{
                        key: value for key, value in outcome.items() if key != "task"})

            cancel = row["cancel"]
            if cancel is not None and cancel["status"] == "pending":
                delivery = self._delivery_state(cancel, now)
                if delivery == "ready":
                    disposition = ("physical_stop_required" if row["physical_started"]
                                   else "local_unstarted_withdrawal")
                    outcome = {
                        "task_id": task_id,
                        "dispatch_action_id": dispatch["action_id"],
                        "cancel_action_id": cancel["action_id"],
                        "message_id": cancel["message_id"],
                        "generation": row["generation"],
                        "delivered_at_s": cancel["delivered_at_s"],
                        "disposition": disposition,
                        "task": deepcopy(row["task"]),
                    }
                    outcomes["ready_cancels"].append(outcome)
                    self._record("cancel_ready", now, **{
                        key: value for key, value in outcome.items() if key != "task"})
                elif delivery == "failed":
                    outcome = {
                        "task_id": task_id,
                        "dispatch_action_id": dispatch["action_id"],
                        "cancel_action_id": cancel["action_id"],
                        "message_id": cancel["message_id"],
                        "generation": row["generation"],
                        "failed_at_s": cancel["failed_at_s"],
                        "reason": cancel["failure_reason"],
                        "task": deepcopy(row["task"]),
                    }
                    outcomes["failed_cancels"].append(outcome)
                    self._record("cancel_failed", now, **{
                        key: value for key, value in outcome.items() if key != "task"})
        return deepcopy(outcomes)

    def mark_started(self, task_id: str, *, dispatch_action_id: str,
                     time_s: float) -> None:
        now = _time(time_s, "time_s")
        self.runtime.advance(now)
        row = self._dispatches.get(_nonempty(task_id, "task_id"))
        if row is None or row["dispatch"]["action_id"] != dispatch_action_id:
            raise ValueError("physical start does not match a queued dispatch")
        if row["tombstoned"] or row["dispatch"]["status"] != "ready":
            raise ValueError("physical execution requires a fresh ready dispatch")
        failure = self._ready_failure(row["dispatch"], now)
        if failure is not None:
            raise ValueError(
                f"physical execution requires a fresh ready dispatch: {failure}")
        if row["physical_started"]:
            raise ValueError("physical execution has already started")
        row["physical_started"] = True
        row["physical_started_at_s"] = now
        self._record("physical_execution_started", now, task_id=task_id,
                     dispatch_action_id=dispatch_action_id,
                     message_id=row["dispatch"]["message_id"])

    def mark_terminal(self, task_id: str, *, dispatch_action_id: str,
                      status: str, time_s: float,
                      disposition: str = "physical_execution") -> None:
        now = _time(time_s, "time_s")
        row = self._dispatches.get(_nonempty(task_id, "task_id"))
        if row is None or row["dispatch"]["action_id"] != dispatch_action_id:
            raise ValueError("terminal outcome does not match a queued dispatch")
        if row["terminal"] is not None:
            raise ValueError("execution transport outcome is already terminal")
        row["terminal"] = {
            "status": _nonempty(status, "status"),
            "time_s": now,
            "disposition": _nonempty(disposition, "disposition"),
        }
        self._record("execution_terminal", now, task_id=task_id,
                     dispatch_action_id=dispatch_action_id,
                     status=status, disposition=disposition)

    def has_pending_work(self) -> bool:
        for row in self._dispatches.values():
            if (not row["tombstoned"] and row["dispatch"]["status"] == "pending"):
                return True
            if row["cancel"] is not None and row["cancel"]["status"] == "pending":
                return True
        return False

    def cancellation_state(self, task_id: str, *,
                           dispatch_action_id: str) -> dict[str, Any]:
        """Return one task's cancel state without exposing another task's work."""
        row = self._dispatches.get(_nonempty(task_id, "task_id"))
        if row is None or row["dispatch"]["action_id"] != dispatch_action_id:
            return {"status": "missing", "message_id": None,
                    "failure_reason": "dispatch_not_registered"}
        cancel = row["cancel"]
        if cancel is None:
            return {"status": "missing", "message_id": None,
                    "failure_reason": "cancel_not_registered"}
        return {
            "status": cancel["status"],
            "message_id": cancel["message_id"],
            "failure_reason": cancel["failure_reason"],
        }

    def next_wakeup_s(self) -> float | None:
        if not self.has_pending_work():
            return None
        wakeups = []
        runtime_wakeup = self.runtime.next_event_time_s()
        if runtime_wakeup is not None:
            wakeups.append(runtime_wakeup)
        for row in self._dispatches.values():
            if not row["tombstoned"] and row["dispatch"]["status"] == "pending":
                execution = row["task"]["execution"]
                wakeups.append(float(execution["start_s"])
                               + float(execution["duration_s"]))
        return min(wakeups) if wakeups else None

    def profile(self) -> dict[str, Any]:
        return {
            "schema": "eai.benchmark.goal_execution_downlink_profile",
            "version": 1,
            "episode_id": self.episode_id,
            "public_task_sha256": self.public_task_sha256,
            "pool": "backhaul_downlink",
            "direction": "downlink",
            "recipient_identity": "public_robot_id",
            "ttl_s": self.ttl_s,
            "backend_calls_owned_by": "formal_goal_episode_runner",
            "visibility": "evaluator_only",
        }

    def report(self) -> dict[str, Any]:
        return {
            "schema": "eai.benchmark.goal_execution_downlink",
            "version": 1,
            "episode_id": self.episode_id,
            "public_task_sha256": self.public_task_sha256,
            "task_id": self._task_id,
            "time_s": self.runtime.time_s,
            "dispatches": deepcopy(self._dispatches),
            "ledger": deepcopy(self._ledger),
            "shared_transport_embedded": False,
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
        }


__all__ = ["GoalExecutionDownlink"]
