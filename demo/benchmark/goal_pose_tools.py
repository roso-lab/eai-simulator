"""Optional participant pose tools; caller owns the trusted simulation clock.

This companion interface leaves frozen GoalSession task/API packets unchanged.
It meters pose responses through CommunicationRuntime. Tool-call bytes are
recorded separately as ingress volume, not simulated downlink transmission.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any, Callable, Mapping

from .goal_pose_telemetry import GoalPoseTelemetry
from .provenance import canonical_sha256


POSE_TOOL_SCHEMA = "eai.benchmark.goal_pose_tool_call"


class GoalPoseTools:
    """Query now or subscribe; no sampling until the participant opts in.

    ``sample(robot_id, time_s)`` must read the current trusted state and return
    a telemetry sample at that exact episode time. ``tick`` must be driven by
    the backend's real clock for periodic operation. Missed ticks are recorded,
    never reconstructed from a later pose. A stopped subscription does not
    cancel already transmitted messages.
    """

    def __init__(self, public_task: Mapping[str, Any], *, episode_id: str,
                 sample: Callable[[str, float], Mapping[str, Any]],
                 telemetry: GoalPoseTelemetry, period_s: float = 1.0,
                 validity_s: float = 5.0, frame_id: str = "world"):
        for name, value in (("period_s", period_s), ("validity_s", validity_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not isinstance(frame_id, str) or not frame_id.strip() or not callable(sample):
            raise ValueError("pose tools require a frame and trusted sampler")
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError("pose tools require episode identity")
        self.episode_id = episode_id
        self.public_task_sha256 = canonical_sha256(public_task)
        self.robot_ids = frozenset(row["id"] for row in public_task["candidate_robot_pool"])
        self._known_robot_ids = set(self.robot_ids)
        self._simulation_budget_s = public_task["budgets"]["simulation_s"]
        self.telemetry = telemetry
        self._sample = sample
        self.period_s = float(period_s)
        self.validity_s = float(validity_s)
        self.frame_id = frame_id
        self._now = 0.0
        self._subscriptions: dict[str, float] = {}
        self._calls: dict[str, dict[str, Any]] = {}
        self._sampling: list[dict[str, Any]] = []
        self._ready: list[dict[str, Any]] = []
        self._pending_queries: dict[str, dict[str, Any]] = {}
        self._control_events: list[dict[str, Any]] = []
        self._control_ledger: list[dict[str, Any]] = []
        self._event_index = 0

    def public_profile(self) -> dict[str, Any]:
        return {
            "schema": "eai.benchmark.goal_pose_tools", "version": 1,
            "episode_id": self.episode_id, "public_task_sha256": self.public_task_sha256,
            "optional": True, "robot_ids": sorted(self.robot_ids),
            "robot_scope": "initial public pool plus subsequently delivered member_join declarations; requests require a current member",
            "frame_id": self.frame_id, "position_units": "m", "yaw_units": "rad",
            "subscription_period_s": self.period_s, "validity_s": self.validity_s,
            "call_schema": POSE_TOOL_SCHEMA, "call_version": 1,
            "call_fields": ["call_id", "submitted_at_s", "tool", "arguments"],
            "tools": {name: {"arguments": ["robot_ids"]}
                      for name in ("query_pose", "subscribe_pose", "unsubscribe_pose")},
            "response": "observation(kind=pose_update); delivered messages only",
            "control_response": "pose_tool_result acknowledges local admission; pose_query_timeout reports no fresh response by deadline, not a drop diagnosis",
            "consumption": "existing consume_message action; receipt is not proof of decision dependence",
            "sampling": "current trusted sample on query or due backend tick; missed periods are not backfilled",
            "cost": "canonical ingress call bytes and metered response bytes; no token price",
        }

    def owns_call(self, call_id: str) -> bool:
        return isinstance(call_id, str) and call_id in self._calls

    def register_public_robot(self, robot_id: str) -> None:
        """Trusted host hook after a member declaration crosses to the model."""
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise ValueError("public robot ID must be nonempty")
        self._known_robot_ids.add(robot_id)

    def _control(self, kind: str, data: Mapping[str, Any], now: float) -> None:
        self._control_events.append({
            "message_id": f"{self.episode_id}:pose-control:{self._event_index}",
            "producer_id": "benchmark_pose_tools", "kind": kind,
            "data": deepcopy(dict(data)), "valid_until_s": now + self.validity_s,
        })
        payload = self._control_events[-1]
        size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self._control_ledger.append({"payload": deepcopy(payload), "created_at_s": now,
                                     "size_bytes": size,
                                     "accounting_scope": "benchmark_to_model_control_not_radio_transport"})
        self._event_index += 1

    def _validate_time(self, time_s: float) -> float:
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(time_s) or time_s < self._now):
            raise ValueError("pose tool time must be finite and monotonic")
        return float(time_s)

    def _publish(self, robot_id: str, now: float) -> str:
        sample = deepcopy(dict(self._sample(robot_id, now)))
        if sample.get("sampled_at_s") != now or sample.get("frame_id") != self.frame_id:
            raise ValueError("trusted pose sampler clock/frame mismatch")
        record = self.telemetry.publish(robot_id, sample, valid_until_s=now + self.validity_s)
        return record["payload"]["message_id"]

    def submit(self, call: Mapping[str, Any], *, time_s: float,
               current_agents: set[str] | None = None) -> dict[str, Any]:
        now = self._validate_time(time_s)
        fields = {"schema", "version", "call_id", "submitted_at_s", "tool", "arguments"}
        if (not isinstance(call, Mapping) or set(call) != fields
                or call["schema"] != POSE_TOOL_SCHEMA or type(call["version"]) is not int
                or call["version"] != 1):
            raise ValueError("invalid pose tool call envelope")
        call_id = call["call_id"]
        if not isinstance(call_id, str) or not call_id.strip() or call_id in self._calls:
            raise ValueError("pose tool call_id must be new and nonempty")
        if (isinstance(call["submitted_at_s"], bool)
                or not isinstance(call["submitted_at_s"], (int, float))
                or call["submitted_at_s"] != now):
            raise ValueError("pose tool submission must match current episode time")
        name, arguments = call["tool"], call["arguments"]
        if not isinstance(name, str) or name not in {"query_pose", "subscribe_pose", "unsubscribe_pose"}:
            raise ValueError("unknown pose tool")
        if name != "unsubscribe_pose" and now >= self._simulation_budget_s:
            raise ValueError("episode simulation budget has ended")
        if not isinstance(arguments, Mapping) or set(arguments) != {"robot_ids"}:
            raise ValueError("pose tool arguments must contain robot_ids only")
        ids = arguments["robot_ids"]
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids) or not set(ids) <= self._known_robot_ids):
            raise ValueError("pose tool requires distinct public robot IDs")
        if current_agents is not None and name != "unsubscribe_pose" and not set(ids) <= current_agents:
            raise ValueError("pose request contains an inactive robot")
        canonical = json.dumps(dict(call), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        row = {"call": deepcopy(dict(call)), "received_at_s": now,
               "size_bytes": len(canonical), "status": "received",
               "accounting_scope": "model_to_benchmark_ingress_not_radio_transport"}
        self._calls[call_id] = row
        self._now = now
        self._ready.extend(self.telemetry.advance(now))
        try:
            if name == "query_pose":
                messages = {}
                for robot_id in ids:
                    messages[self._publish(robot_id, now)] = robot_id
                self._pending_queries[call_id] = {
                    "messages": messages, "deadline_s": now + self.validity_s}
            elif name == "subscribe_pose":
                for robot_id in ids:
                    # Repeating a subscription does not force a free refresh.
                    self._subscriptions.setdefault(robot_id, now)
            else:
                for robot_id in ids:
                    self._subscriptions.pop(robot_id, None)
            row["status"] = "accepted"
            self._control("pose_tool_result", {
                "call_id": call_id, "tool": name, "accepted": True,
                "response_deadline_s": now + self.validity_s if name == "query_pose" else None,
            }, now)
        except Exception as exc:
            row.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
            raise
        return {"accepted": True, "call_id": call_id, "tool": name,
                "execution_validated": False}

    def tick(self, time_s: float, *, current_agents: set[str] | None = None) -> list[dict[str, Any]]:
        now = self._validate_time(time_s)
        self._now = now
        self._ready.extend(self.telemetry.advance(now))
        for robot_id, due in list(self._subscriptions.items()):
            if current_agents is not None and robot_id not in current_agents:
                del self._subscriptions[robot_id]
                continue
            if now < due:
                continue
            missed = int(math.floor((now - due) / self.period_s))
            self._publish(robot_id, now)
            self._sampling.append({"robot_id": robot_id, "scheduled_at_s": due,
                                   "sampled_at_s": now, "missed_periods": missed})
            self._subscriptions[robot_id] = due + (missed + 1) * self.period_s
        self._ready.extend(self.telemetry.advance(now))
        result, self._ready = self._ready, []
        # Queued responses may expire between submit and the next host tick.
        fresh = [row for row in result if now < row["valid_until_s"]]
        received = {row["message_id"] for row in fresh}
        for call_id, query in list(self._pending_queries.items()):
            query["messages"] = {mid: robot for mid, robot in query["messages"].items()
                                 if mid not in received}
            if not query["messages"]:
                del self._pending_queries[call_id]
            elif now >= query["deadline_s"]:
                self._control("pose_query_timeout", {
                    "call_id": call_id, "robot_ids": sorted(query["messages"].values()),
                    "response_deadline_s": query["deadline_s"],
                }, now)
                del self._pending_queries[call_id]
        control, self._control_events = self._control_events, []
        return [*control, *fresh]

    def report(self) -> dict[str, Any]:
        return {"schema": "eai.benchmark.goal_pose_tools_report", "version": 1,
                "episode_id": self.episode_id, "profile": self.public_profile(),
                "calls": deepcopy(list(self._calls.values())),
                "control_responses": deepcopy(self._control_ledger),
                "subscription_samples": deepcopy(self._sampling),
                "communication": self.telemetry.communication_report(),
                "execution_validated": False, "scoreable": False}

    def has_pending_work(self) -> bool:
        """Whether real idle stepping could produce another tool response."""
        if self._subscriptions or self._pending_queries:
            return True
        return any(delivery["status"] in {"queued", "in_transit"}
                   for message in self.telemetry.communication_report()["messages"].values()
                   for delivery in message["recipients"].values())
