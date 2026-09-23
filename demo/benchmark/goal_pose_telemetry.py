"""Delivered-only pose telemetry for opt-in goal benchmark tools.

The trusted host supplies current pose samples explicitly.  This adapter does
not sample a simulator, backfill missed subscription intervals, or publish on
its own.  Every sample crosses ``CommunicationRuntime`` before it can become a
GoalSession ``observation`` payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .communication import CommunicationRuntime


_SAMPLE_FIELDS = {
    "sampled_at_s", "position_m", "yaw_rad", "frame_id", "source", "quality",
}
_SOURCES = {"physx", "synthetic"}
_CHANNEL = "pose_telemetry"


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
    """Copy strict finite JSON without tuple or non-string-key coercion."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} JSON object keys must be strings")
        return {key: _json_copy(item, label) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item, label) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError(f"{label} must contain only finite JSON values")


def _pose_sample(sample: Any) -> dict[str, Any]:
    if not isinstance(sample, Mapping):
        raise ValueError("pose sample must be an object")
    if any(not isinstance(key, str) for key in sample):
        raise ValueError("pose sample keys must be strings")
    missing = _SAMPLE_FIELDS - set(sample)
    extra = set(sample) - _SAMPLE_FIELDS
    if missing or extra:
        raise ValueError(
            f"pose sample fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")

    sampled_at_s = _number(sample["sampled_at_s"], "sampled_at_s", nonnegative=True)
    position = sample["position_m"]
    if not isinstance(position, list) or len(position) != 3:
        raise ValueError("position_m must be a three-element list")
    position_m = [
        _number(component, f"position_m[{index}]")
        for index, component in enumerate(position)
    ]
    yaw_rad = _number(sample["yaw_rad"], "yaw_rad")
    if yaw_rad < -math.pi or yaw_rad > math.pi:
        raise ValueError("yaw_rad must be normalized to [-pi, pi]")
    frame_id = _name(sample["frame_id"], "frame_id")
    source = sample["source"]
    if not isinstance(source, str) or source not in _SOURCES:
        raise ValueError("source must be 'physx' or 'synthetic'")
    if not isinstance(sample["quality"], Mapping):
        raise ValueError("quality must be a JSON object")
    quality = _json_copy(sample["quality"], "quality")
    return {
        "sampled_at_s": sampled_at_s,
        "position_m": position_m,
        "yaw_rad": yaw_rad,
        "frame_id": frame_id,
        "source": source,
        "quality": quality,
    }


class GoalPoseTelemetry:
    """Transport host-supplied current poses to one goal participant."""

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

    def publish(self, robot_id: str, sample: Mapping[str, Any], valid_until_s: float):
        """Publish one explicitly sampled current pose; return host-only state."""
        producer_id = _name(robot_id, "robot_id")
        pose = _pose_sample(sample)
        expiry = _number(valid_until_s, "valid_until_s", nonnegative=True)
        if pose["sampled_at_s"] != self.runtime.time_s:
            raise ValueError("sampled_at_s must equal the current communication time")
        message_id = f"{self.message_prefix}:pose:{self._next_message_index}"
        record = self.runtime.publish(
            message_id=message_id,
            producer_id=producer_id,
            recipients=[self.recipient_id],
            channel=_CHANNEL,
            estimate=pose,
            valid_until_s=expiry,
            published_at_s=pose["sampled_at_s"],
        )
        self._next_message_index += 1
        self._owned_messages.add(message_id)
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
                "kind": "pose_update",
                "data": data,
                "valid_until_s": payload["valid_until_s"],
            })
            self._emitted_messages.add(message_id)
        return delivered

    def owns_message(self, message_id: str) -> bool:
        return isinstance(message_id, str) and message_id in self._owned_messages

    def can_consume(self, message_id: str, time_s: float) -> bool:
        """Check current delivered eligibility without advancing transport time.

        Future transport state is intentionally not predicted: callers should
        first call ``advance(now)`` and prevalidate consumption at that same
        clock value.
        """
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
        """Account a consume action once, with action-ID retry idempotence."""
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
        """Return the unannotated evaluator report accepted by replay validation."""
        return self.runtime.report()


__all__ = ["GoalPoseTelemetry"]
