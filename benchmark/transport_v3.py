"""Deterministic full-fleet shared-medium transport for D2/D6.

Unlike the legacy router, capacity is consumed from one FIFO byte/bit budget
for the whole fleet.  A message is only counted in ``transmitted_bits`` as
bits actually sent by the simulated clock; queued or dropped bytes remain
separately visible.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, asdict
import json
import random
from typing import Any, Mapping


@dataclass(frozen=True)
class SharedFrame:
    message_id: str
    sender_id: str
    receiver_id: str
    payload: Mapping[str, Any]
    submitted_tick: int
    bits: int
    remaining_bits: int
    started_tick: int | None = None
    completed_tick: int | None = None
    delivered_tick: int | None = None
    status: str = "queued"
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SharedChannelV3:
    """One FIFO channel shared by all directed sender/receiver pairs."""

    def __init__(self, *, bits_per_tick: int, max_queue_bits: int, delay_ticks: int = 0, ttl_ticks: int | None = None, loss_probability: float = 0.0, seed: int = 0):
        if type(bits_per_tick) is not int or bits_per_tick <= 0:
            raise ValueError("bits_per_tick must be positive")
        if type(max_queue_bits) is not int or max_queue_bits <= 0:
            raise ValueError("max_queue_bits must be positive")
        if delay_ticks < 0 or (ttl_ticks is not None and ttl_ticks <= 0):
            raise ValueError("delay/TTL must be nonnegative/positive")
        if not 0 <= loss_probability <= 1:
            raise ValueError("loss_probability must be in [0,1]")
        self.bits_per_tick = bits_per_tick
        self.max_queue_bits = max_queue_bits
        self.delay_ticks = int(delay_ticks)
        self.ttl_ticks = ttl_ticks
        self.loss_probability = float(loss_probability)
        self._rng = random.Random(seed)
        self._queue: deque[SharedFrame] = deque()
        self._frames: dict[str, SharedFrame] = {}
        self._delivered: deque[SharedFrame] = deque()
        self._in_flight: deque[SharedFrame] = deque()
        self.submitted_bits = 0
        self.queued_bits = 0
        self.transmitted_bits = 0
        self.delivered_bits = 0
        self.dropped_bits = 0
        self._tick = 0

    @staticmethod
    def _bits(sender_id: str, receiver_id: str, kind: str, payload: Mapping[str, Any]) -> int:
        value = {"sender": sender_id, "receiver": receiver_id, "kind": kind, "payload": payload}
        return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")) * 8

    def submit(self, *, message_id: str, sender_id: str, receiver_id: str, kind: str, payload: Mapping[str, Any], tick: int) -> SharedFrame:
        if type(tick) is not int or tick < 0 or tick < self._tick - 1:
            raise ValueError("submission tick must be nonnegative and not in the past")
        if not all(isinstance(v, str) and v for v in (message_id, sender_id, receiver_id, kind)):
            raise ValueError("message identities and kind must be nonempty")
        if self._queue and tick < self._queue[-1].submitted_tick:
            raise ValueError("submissions must follow chronological FIFO order")
        if message_id in self._frames:
            raise ValueError(f"duplicate message_id: {message_id}")
        # The delivered data must be the serialized data whose bits were
        # metered, independent of later sender/receiver mutations.
        payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
        bits = self._bits(sender_id, receiver_id, kind, payload)
        self.submitted_bits += bits
        if self.queued_bits + bits > self.max_queue_bits:
            frame = SharedFrame(message_id, sender_id, receiver_id, dict(payload), tick, bits, bits, status="dropped_queue_full", kind=kind)
            self._frames[message_id] = frame
            self.dropped_bits += bits
            return deepcopy(frame)
        frame = SharedFrame(message_id, sender_id, receiver_id, dict(payload), tick, bits, bits, kind=kind)
        self._queue.append(frame)
        self._frames[message_id] = frame
        self.queued_bits += bits
        return deepcopy(frame)

    def advance(self, tick: int) -> tuple[SharedFrame, ...]:
        if type(tick) is not int or tick < 0:
            raise ValueError("tick must be a nonnegative integer")
        if tick < self._tick - 1:
            raise ValueError("channel tick must be monotonic")
        completed: list[SharedFrame] = []
        while self._tick <= tick:
            budget = self.bits_per_tick
            while budget > 0 and self._queue:
                frame = self._queue[0]
                if frame.submitted_tick > self._tick:
                    break
                if frame.status != "queued" and frame.status != "transmitting":
                    self._queue.popleft()
                    continue
                if self.ttl_ticks is not None and self._tick >= frame.submitted_tick + self.ttl_ticks:
                    self._queue.popleft()
                    self.queued_bits -= frame.remaining_bits
                    self.dropped_bits += frame.remaining_bits
                    frame = SharedFrame(**{**frame.to_dict(), "remaining_bits": 0, "status": "dropped_ttl"})
                    self._frames[frame.message_id] = frame
                    continue
                sent = min(budget, frame.remaining_bits)
                budget -= sent
                self.queued_bits -= sent
                self.transmitted_bits += sent
                remaining = frame.remaining_bits - sent
                started = frame.started_tick if frame.started_tick is not None else self._tick
                frame = SharedFrame(**{**frame.to_dict(), "remaining_bits": remaining, "started_tick": started, "status": "transmitting" if remaining else "transmitted"})
                self._frames[frame.message_id] = frame
                self._queue[0] = frame
                if remaining:
                    break
                self._queue.popleft()
                if self._rng.random() < self.loss_probability:
                    self.dropped_bits += frame.bits
                    frame = SharedFrame(**{**frame.to_dict(), "status": "dropped_loss"})
                    self._frames[frame.message_id] = frame
                else:
                    delivered = SharedFrame(**{**frame.to_dict(), "completed_tick": self._tick, "delivered_tick": self._tick + self.delay_ticks, "status": "transmitted"})
                    self._frames[frame.message_id] = delivered
                    self._in_flight.append(delivered)
            self._tick += 1
        pending: deque[SharedFrame] = deque()
        for frame in self._in_flight:
            if frame.delivered_tick <= tick:
                self._delivered.append(frame)
                self.delivered_bits += frame.bits
                completed.append(frame)
            else:
                pending.append(frame)
        self._in_flight = pending
        return tuple(deepcopy(completed))

    def receive(self, *, receiver_id: str, tick: int, max_items: int | None = None, message_id: str | None = None) -> tuple[SharedFrame, ...]:
        if max_items is not None and (type(max_items) is not int or max_items < 0):
            raise ValueError("max_items must be nonnegative")
        ready: list[SharedFrame] = []
        pending: deque[SharedFrame] = deque()
        while self._delivered:
            frame = self._delivered.popleft()
            if frame.receiver_id == receiver_id and frame.delivered_tick is not None and frame.delivered_tick <= tick and (max_items is None or len(ready) < max_items) and (message_id is None or frame.message_id == message_id):
                ready.append(frame)
            else:
                pending.append(frame)
        self._delivered = pending
        return tuple(deepcopy(ready))

    def snapshot(self) -> dict[str, Any]:
        return {"schema_version": "transport.v3", "tick": self._tick, "queue": [item.to_dict() for item in self._queue], "frames": {key: value.to_dict() for key, value in self._frames.items()}, "delivered": [item.to_dict() for item in self._delivered], "in_flight": [item.to_dict() for item in self._in_flight], "rng_state": self._rng.getstate(), "counters": self.accounting(), "policy": {"bits_per_tick": self.bits_per_tick, "max_queue_bits": self.max_queue_bits, "delay_ticks": self.delay_ticks, "ttl_ticks": self.ttl_ticks, "loss_probability": self.loss_probability}}

    @classmethod
    def restore(cls, snapshot: Mapping[str, Any]) -> "SharedChannelV3":
        """Restore transport-only state, not physics or the submitted brain."""
        if snapshot.get("schema_version") != "transport.v3" or type(snapshot.get("tick")) is not int or snapshot["tick"] < 0:
            raise ValueError("invalid shared channel snapshot")
        channel = cls(**snapshot["policy"])
        channel._tick = snapshot["tick"]
        channel._frames = {key: SharedFrame(**value) for key,value in snapshot["frames"].items()}
        for name in ("queue", "delivered", "in_flight"):
            rows = [SharedFrame(**value) for value in snapshot[name]]
            if any(row.message_id not in channel._frames or row != channel._frames[row.message_id] for row in rows) or len({r.message_id for r in rows}) != len(rows):
                raise ValueError("snapshot queue identity mismatch")
            setattr(channel, "_"+name, deque(rows))
        for key in channel.accounting():
            value = snapshot["counters"][key]
            if type(value) is not int or value < 0:
                raise ValueError("invalid snapshot counters")
            setattr(channel, key, value)
        if sum(f.remaining_bits for f in channel._queue) != channel.queued_bits:
            raise ValueError("snapshot queued-bit accounting mismatch")
        def tuples(value):
            return tuple(tuples(x) for x in value) if isinstance(value, (tuple,list)) else value
        channel._rng.setstate(tuples(snapshot["rng_state"]))
        return channel

    def accounting(self) -> dict[str, int]:
        return {"submitted_bits": self.submitted_bits, "queued_bits": self.queued_bits, "transmitted_bits": self.transmitted_bits, "delivered_bits": self.delivered_bits, "dropped_bits": self.dropped_bits}


__all__ = ["SharedFrame", "SharedChannelV3"]
