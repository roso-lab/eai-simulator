"""Pre-action availability events with explicit information/restoration contracts.

Adapters own physical operations and guarded component call paths. The fleet
owns admission and audit, never allocation, task synthesis or hidden symptoms.
"""
from types import MappingProxyType
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .fleet import FleetRuntime, FleetMessage


@dataclass(frozen=True)
class AvailabilityEvent:
    event_id: str
    robot_id: str
    function: str
    dependency_version: str
    onset_tick: int
    checkpoint_tick: int
    affected_obligations: tuple[str, ...]
    notification_policy: str = "symptoms_only"
    notice_tick: int | None = None
    recipients: tuple[str, ...] = ()
    public_fields: Mapping[str, Any] = field(default_factory=dict)
    restoration_policy: str = "none"
    restoration_tick: int | None = None
    aftermath_contract: str = ""

    def __post_init__(self):
        if any(not str(v).strip() for v in (self.event_id, self.robot_id, self.function,
                self.dependency_version, self.aftermath_contract)):
            raise ValueError("event identity, dependency and physical aftermath contract required")
        for value in (self.onset_tick, self.checkpoint_tick):
            if type(value) is not int or value < 0:
                raise ValueError("event ticks must be nonnegative integers")
        if self.notification_policy not in {"advance_notice", "onset", "symptoms_only"}:
            raise ValueError("unregistered notification policy")
        if self.notification_policy == "symptoms_only":
            if self.notice_tick is not None or self.recipients or self.public_fields:
                raise ValueError("symptoms-only event cannot disclose evaluator fields")
        else:
            if type(self.notice_tick) is not int or not self.recipients:
                raise ValueError("notice requires explicit tick and recipients")
            if self.notification_policy == "onset" and self.notice_tick != self.onset_tick:
                raise ValueError("onset notice must be sent at onset")
            if self.notification_policy == "advance_notice" and not 0 <= self.notice_tick < self.onset_tick:
                raise ValueError("advance notice must precede onset")
        earliest = min(self.onset_tick, self.notice_tick if self.notice_tick is not None else self.onset_tick)
        if self.checkpoint_tick >= earliest:
            raise ValueError("untreated checkpoint must precede earliest treatment operation")
        if self.restoration_policy not in {"none", "fixed", "maintenance"}:
            raise ValueError("unregistered restoration policy")
        if self.restoration_policy == "fixed":
            if type(self.restoration_tick) is not int or self.restoration_tick <= self.onset_tick:
                raise ValueError("fixed restore must follow onset")
        elif self.restoration_tick is not None:
            raise ValueError("only exogenous fixed restoration has an assigned tick")
        if not self.affected_obligations or len(set(self.affected_obligations)) != len(self.affected_obligations):
            raise ValueError("affected obligations must be frozen in untreated history")
        # Serialize now to detach caller-owned nested values. Public payloads
        # are decoded for each send; mutation cannot change a frozen schedule.
        object.__setattr__(self, "_public_json", json.dumps(dict(self.public_fields), sort_keys=True, allow_nan=False))
        object.__setattr__(self, "public_fields", MappingProxyType(json.loads(self._public_json)))
        object.__setattr__(self, "affected_obligations", tuple(self.affected_obligations))
        object.__setattr__(self, "recipients", tuple(self.recipients))


class AvailabilityScheduler:
    """One branch schedule; physical adapter returns verified operation evidence.

    The receipt must identify the exact component, state, tick, dependency
    version and aftermath contract; guard coverage and current-world preservation
    are independently checked by that adapter. No generic teleport/repair exists.
    """
    def __init__(self, events: tuple[AvailabilityEvent, ...], *,
                 operation: Callable[[AvailabilityEvent, bool, int], Mapping[str, Any]]):
        self.events = tuple(events)
        if len({e.event_id for e in events}) != len(events):
            raise ValueError("duplicate event IDs")
        if not callable(operation):
            raise TypeError("physical operation adapter must be callable")
        # Multiple independent events may coexist; the same component needs
        # one ownership contract until its previous fixed restoration finishes.
        components = {}
        for event in sorted(self.events, key=lambda e: e.onset_tick):
            key = (event.robot_id, event.function)
            previous = components.get(key)
            if previous is not None and (previous.restoration_tick is None or
                                          previous.restoration_tick >= event.onset_tick):
                raise ValueError("overlapping component availability events")
            components[key] = event
        self.operation = operation
        self._applied: set[tuple[str, str]] = set()
        self._last_tick = -1
        self._restored: dict[str, int] = {}

    def advance(self, tick: int, fleet: FleetRuntime) -> None:
        if type(tick) is not int or tick < 0 or tick < self._last_tick:
            raise ValueError("event clock must be monotonic")
        # Missed treatment boundaries cannot be silently moved to a later tick.
        for event in self.events:
            for phase, due in (("notice", event.notice_tick), ("loss", event.onset_tick),
                               ("restore", event.restoration_tick)):
                if due is not None and due < tick and (event.event_id, phase) not in self._applied:
                    raise RuntimeError("registered event boundary was skipped")
        self._last_tick = tick
        for event in self.events:
            if event.notice_tick == tick and (event.event_id, "notice") not in self._applied:
                for recipient in event.recipients:
                    fleet.send(FleetMessage(f"event-notice-{event.event_id}-{recipient}",
                        "event-interface", recipient, "availability_notice", tick,
                        json.loads(event._public_json)))
                self._applied.add((event.event_id, "notice"))
            if event.onset_tick == tick and (event.event_id, "loss") not in self._applied:
                self._operate(event, False, tick, fleet)
            if event.restoration_tick == tick and (event.event_id, "restore") not in self._applied:
                self._operate(event, True, tick, fleet)

    def restore_after_maintenance(self, event_id: str, *, tick: int, evidence_hash: str,
                                  fleet: FleetRuntime) -> None:
        event = next(e for e in self.events if e.event_id == event_id)
        if event.restoration_policy != "maintenance" or not evidence_hash:
            raise ValueError("maintenance restore requires registered action evidence")
        if (event_id, "loss") not in self._applied or (event_id, "restore") in self._applied or type(tick) is not int or tick <= event.onset_tick or tick < self._last_tick:
            raise ValueError("invalid maintenance restoration state/tick")
        self.advance(tick, fleet)
        self._operate(event, True, tick, fleet, maintenance_evidence=evidence_hash)

    def _operate(self, event, available, tick, fleet, maintenance_evidence=""):
        expected = {"event_id": event.event_id, "robot_id": event.robot_id,
                    "function": event.function, "available": available, "tick": tick,
                    "dependency_version": event.dependency_version,
                    "aftermath_contract": event.aftermath_contract}
        try:
            receipt = dict(self.operation(event, available, tick))
            if any(type(receipt.get(k)) is not type(v) or receipt[k] != v for k, v in expected.items()) or not receipt.get("evidence_hash"):
                raise ValueError("physical operation receipt identity mismatch")
            if receipt.get("guard_verified") is not True or receipt.get("world_preserved") is not True:
                raise ValueError("guard coverage/current world preservation unverified")
            fleet.set_function_available(event.robot_id, event.function, available, tick=tick)
            fleet.audit.append("availability_operation", tick=tick, source="event.adapter", payload={
                **expected, "evidence_hash": receipt["evidence_hash"],
                "affected_obligations": list(event.affected_obligations),
                "notification_policy": event.notification_policy,
                "restoration_policy": event.restoration_policy,
                "maintenance_evidence": maintenance_evidence,
                "remaining_functions": sorted(fleet.available_capabilities(event.robot_id)),
                "locks": [x.__dict__ for x in fleet.locks.snapshot()]})
        except Exception:
            # A partially changed physical interface is unsafe to continue.
            fleet.request_safe_stop(tick=tick, reason="event_operation_unverified")
            raise
        self._applied.add((event.event_id, "restore" if available else "loss"))
        if available:
            self._restored[event.event_id] = tick

    def snapshot(self) -> dict:
        return {"schema": "availability.schedule.v1", "last_tick": self._last_tick,
                "applied": [list(p) for p in sorted(self._applied)],
                "actual_restoration_ticks": dict(self._restored),
                "strict_replay_qualified": False}
