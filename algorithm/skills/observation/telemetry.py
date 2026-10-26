"""Delivered-only camera-frame telemetry for opt-in goal benchmark tools.

Every frame crosses ``CommunicationRuntime`` on channel ``camera`` before it
can become a GoalSession observation. Wire size is the canonical UTF-8 JSON of
the public payload, including the encoded image bytes the model receives.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .communication import CommunicationRuntime


_CHANNEL = "camera"
_REQUIRED_ESTIMATE = {
    "request_id",
    "frame_id",
    "capture_sim_time_s",
    "physics_step",
    "pose",
    "calibration",
    "encoding",
    "payload_b64",
    "payload_sha256",
    "byte_length",
    "cache_hit",
    "status",
}
_FORBIDDEN_KEYS = {
    "ground_truth",
    "gt",
    "hidden_answer",
    "hidden_label",
    "oracle",
    "task_answer",
    "detector_gt",
    "payload_ref",
}
_STATUSES = {"fresh", "stale", "error"}


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _number(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (nonnegative and value < 0)):
        qualifier = " finite nonnegative" if nonnegative else " finite"
        raise ValueError(f"{label} must be a{qualifier} number")
    return float(value)


def _json_copy(value: Any, label: str) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} JSON object keys must be strings")
        lowered = {key.casefold() for key in value}
        if lowered & _FORBIDDEN_KEYS:
            raise ValueError(f"{label} must not include hidden ground-truth fields")
        return {key: _json_copy(item, label) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item, label) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError(f"{label} must contain only finite JSON values")


def _frame_estimate(estimate: Any) -> dict[str, Any]:
    if not isinstance(estimate, Mapping):
        raise ValueError("camera frame estimate must be an object")
    if any(not isinstance(key, str) for key in estimate):
        raise ValueError("camera frame keys must be strings")
    missing = _REQUIRED_ESTIMATE - set(estimate)
    if missing:
        raise ValueError(f"camera frame missing fields: {sorted(missing)}")
    forbidden = {key for key in estimate if key.casefold() in _FORBIDDEN_KEYS}
    if forbidden:
        raise ValueError(f"camera frame must not include hidden fields: {sorted(forbidden)}")
    if "payload_ref" in estimate:
        raise ValueError("payload_ref is not supported in first-edition camera telemetry")
    payload_b64 = estimate["payload_b64"]
    if not isinstance(payload_b64, str):
        raise ValueError("payload_b64 must be a string")
    byte_length = estimate["byte_length"]
    if type(byte_length) is not int or isinstance(byte_length, bool) or byte_length < 0:
        raise ValueError("byte_length must be a nonnegative integer")
    status = estimate["status"]
    if status not in _STATUSES:
        raise ValueError("status must be fresh, stale, or error")
    if type(estimate["cache_hit"]) is not bool:
        raise ValueError("cache_hit must be a boolean")
    physics_step = estimate["physics_step"]
    if type(physics_step) is not int or isinstance(physics_step, bool) or physics_step < 0:
        raise ValueError("physics_step must be a nonnegative integer")
    pose = estimate["pose"]
    if not isinstance(pose, Mapping) or "source" not in pose:
        raise ValueError("pose must include a source")
    if pose["source"] not in {"physx", "synthetic"}:
        raise ValueError("pose source must be physx or synthetic")
    result = {
        "request_id": _name(estimate["request_id"], "request_id"),
        "frame_id": _name(estimate["frame_id"], "frame_id"),
        "capture_sim_time_s": _number(
            estimate["capture_sim_time_s"], "capture_sim_time_s", nonnegative=True),
        "physics_step": physics_step,
        "pose": _json_copy(pose, "pose"),
        "calibration": _json_copy(estimate["calibration"], "calibration"),
        "encoding": _name(estimate["encoding"], "encoding"),
        "payload_b64": payload_b64,
        "payload_sha256": _name(estimate["payload_sha256"], "payload_sha256"),
        "byte_length": byte_length,
        "cache_hit": estimate["cache_hit"],
        "status": status,
    }
    return result


class GoalCameraTelemetry:
    """Transport host-supplied camera frames to one goal participant."""

    def __init__(self, runtime: CommunicationRuntime | None = None, *,
                 recipient_id: str = "participant", message_prefix: str = "episode"):
        if runtime is not None and not isinstance(runtime, CommunicationRuntime):
            raise ValueError("runtime must be a CommunicationRuntime")
        self.runtime = runtime if runtime is not None else CommunicationRuntime()
        self.recipient_id = _name(recipient_id, "recipient_id")
        self.message_prefix = _name(message_prefix, "message_prefix")
        self._next_message_index = 0
        self._owned_messages: set[str] = set()
        self._emitted_messages: set[str] = set()
        self._actions: dict[str, tuple[str, dict[str, Any]]] = {}
        self._request_messages: dict[str, str] = {}

    def publish(self, robot_id: str, estimate: Mapping[str, Any], valid_until_s: float):
        """Publish one captured frame; return host-only transport state."""
        producer_id = _name(robot_id, "robot_id")
        frame = _frame_estimate(estimate)
        expiry = _number(valid_until_s, "valid_until_s", nonnegative=True)
        if frame["capture_sim_time_s"] != self.runtime.time_s:
            raise ValueError("capture_sim_time_s must equal the current communication time")
        message_id = f"{self.message_prefix}:camera:{self._next_message_index}"
        record = self.runtime.publish(
            message_id=message_id,
            producer_id=producer_id,
            recipients=[self.recipient_id],
            channel=_CHANNEL,
            estimate=frame,
            valid_until_s=expiry,
            published_at_s=frame["capture_sim_time_s"],
        )
        self._next_message_index += 1
        self._owned_messages.add(message_id)
        self._request_messages[frame["request_id"]] = message_id
        return record

    def advance(self, time_s: float) -> list[dict[str, Any]]:
        """Return newly delivered Goal observation payloads exactly once."""
        self.runtime.advance(time_s)
        delivered = []
        for payload in self.runtime.public_messages(
                recipient=self.recipient_id, channel=_CHANNEL):
            message_id = payload["message_id"]
            if message_id not in self._owned_messages or message_id in self._emitted_messages:
                continue
            data = deepcopy(payload["estimate"])
            data["published_at_s"] = payload["published_at_s"]
            delivered.append({
                "message_id": message_id,
                "producer_id": payload["producer_id"],
                "kind": "camera_frame",
                "data": data,
                "valid_until_s": payload["valid_until_s"],
            })
            self._emitted_messages.add(message_id)
        return delivered

    def owns_message(self, message_id: str) -> bool:
        return isinstance(message_id, str) and message_id in self._owned_messages

    def message_for_request(self, request_id: str) -> str | None:
        return self._request_messages.get(request_id)

    def can_consume(self, message_id: str, time_s: float) -> bool:
        checked_time = _number(time_s, "time_s", nonnegative=True)
        if (checked_time != self.runtime.time_s
                or message_id not in self._emitted_messages):
            return False
        return any(
            payload["message_id"] == message_id
            and checked_time < payload["valid_until_s"]
            for payload in self.runtime.public_messages(
                recipient=self.recipient_id, channel=_CHANNEL)
        )

    def consume(self, message_id: str, action_id: str, time_s: float) -> dict[str, Any]:
        message_id = _name(message_id, "message_id")
        action_id = _name(action_id, "action_id")
        previous = self._actions.get(action_id)
        if previous is not None:
            previous_message_id, receipt = previous
            if previous_message_id != message_id:
                raise ValueError("action_id is already bound to another message")
            return deepcopy(receipt)
        if message_id not in self._owned_messages:
            raise ValueError("message is not owned by this telemetry adapter")
        if message_id not in self._emitted_messages:
            raise ValueError("message has not been emitted to the participant")
        receipt = self.runtime.consume(
            message_id,
            recipient=self.recipient_id,
            consumer_id=action_id,
            time_s=time_s,
        )
        self._actions[action_id] = (message_id, deepcopy(receipt))
        return deepcopy(receipt)

    def communication_report(self) -> dict[str, Any]:
        return self.runtime.report()

    def next_transport_event_s(self) -> float | None:
        """Earliest CommunicationRuntime service/delivery/expiry wake time."""
        return self.runtime.next_event_time_s()


__all__ = ["GoalCameraTelemetry"]
