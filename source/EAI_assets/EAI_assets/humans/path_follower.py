# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Pure waypoint following for human and human-activity actors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence


_LOCOMOTION_TYPES = {"walk", "jog", "ride", "roll"}
_EPSILON = 1.0e-9


class MovementPolicy(Protocol):
    """Hook for traffic systems to permit or pause movement per path segment."""

    def can_advance(self, actor_id: str, segment_index: int, context: Any) -> bool:
        ...


class AllowAllMovementPolicy:
    """Default movement policy used when no traffic rules are attached."""

    def can_advance(self, actor_id: str, segment_index: int, context: Any) -> bool:
        return True


@dataclass(frozen=True)
class PauseLease:
    """A revocable pause lease owned by exactly one system.

    Releasing one lease never clears a pause held by a different owner.
    """

    owner: str
    reason: str
    _controller: "MovementPauseController"

    def release(self) -> None:
        self._controller._release(self)


class MovementPauseController:
    """Manages reason-owned pause leases.

    Multiple systems (actions, traffic, caller) may hold independent
    leases; the path remains paused until every lease is released.
    """

    def __init__(self) -> None:
        self._leases: dict[str, PauseLease] = {}

    @property
    def paused(self) -> bool:
        return len(self._leases) > 0

    def acquire(self, owner: str, reason: str) -> PauseLease:
        key = str(owner)
        if key in self._leases:
            raise ValueError(f"pause already held by '{owner}'")
        lease = PauseLease(owner=key, reason=str(reason), _controller=self)
        self._leases[key] = lease
        return lease

    def _release(self, lease: PauseLease) -> None:
        self._leases.pop(lease.owner, None)


@dataclass(frozen=True)
class PathFollowerOutput:
    position: tuple[float, float, float]
    yaw: float
    speed: float
    locomotion: str
    finished: bool


@dataclass(frozen=True)
class _Segment:
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    length: float
    yaw: float


def _point(value: Sequence[float]) -> tuple[float, float, float]:
    if len(value) != 3:
        raise ValueError("each waypoint must contain three coordinates")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise ValueError("waypoint coordinates must be finite")
    return result  # type: ignore[return-value]


def _segment(start: tuple[float, float, float], end: tuple[float, float, float]) -> _Segment | None:
    delta = tuple(end[index] - start[index] for index in range(3))
    length = math.sqrt(sum(component * component for component in delta))
    if length <= _EPSILON:
        return None
    return _Segment(start=start, end=end, length=length, yaw=math.atan2(delta[1], delta[0]))


class PathFollower:
    """Advance one actor along a polyline using an actor-local distance clock."""

    def __init__(
        self,
        actor_id: str,
        waypoints: Iterable[Sequence[float]],
        *,
        speed: float,
        loop: bool = False,
        locomotion: str = "walk",
        policy: MovementPolicy | None = None,
        movement_gate: MovementPauseController | None = None,
    ) -> None:
        speed = float(speed)
        if not math.isfinite(speed) or speed <= 0.0:
            raise ValueError("speed must be positive and finite")
        if locomotion not in _LOCOMOTION_TYPES:
            raise ValueError(f"unsupported locomotion: {locomotion}")

        points = tuple(_point(value) for value in waypoints)
        segments = [
            segment
            for start, end in zip(points, points[1:])
            if (segment := _segment(start, end)) is not None
        ]
        if loop and len(points) >= 2:
            closing = _segment(points[-1], points[0])
            if closing is not None:
                segments.append(closing)
        if not segments:
            raise ValueError("path must contain at least one non-zero segment")

        self.actor_id = str(actor_id)
        self.speed = speed
        self.loop = bool(loop)
        self.locomotion = locomotion
        self.policy = policy or AllowAllMovementPolicy()
        self._movement_gate = movement_gate
        self._segments = tuple(segments)
        self._segment_index = 0
        self._segment_distance = 0.0
        self._distance = 0.0
        self._paused = False
        self._finished = False

    @property
    def distance(self) -> float:
        return self._distance

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def paused(self) -> bool:
        return self._paused or (
            self._movement_gate is not None and self._movement_gate.paused
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def reset(self) -> PathFollowerOutput:
        self._segment_index = 0
        self._segment_distance = 0.0
        self._distance = 0.0
        self._finished = False
        return self._output(moving=False)

    def _position(self) -> tuple[float, float, float]:
        segment = self._segments[self._segment_index]
        fraction = min(self._segment_distance / segment.length, 1.0)
        return tuple(
            segment.start[index] + (segment.end[index] - segment.start[index]) * fraction
            for index in range(3)
        )  # type: ignore[return-value]

    def _output(self, *, moving: bool) -> PathFollowerOutput:
        segment = self._segments[self._segment_index]
        return PathFollowerOutput(
            position=self._position(),
            yaw=segment.yaw,
            speed=self.speed if moving else 0.0,
            locomotion=self.locomotion if moving else "idle",
            finished=self._finished,
        )

    def update(self, dt: float, *, context: Any = None) -> PathFollowerOutput:
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0.0:
            raise ValueError("dt must be non-negative and finite")
        if self._finished or dt == 0.0:
            return self._output(moving=False)
        if self.paused:
            return self._output(moving=False)

        remaining = self.speed * dt
        advanced = False
        while remaining > _EPSILON:
            if not self.policy.can_advance(self.actor_id, self._segment_index, context):
                break
            segment = self._segments[self._segment_index]
            available = segment.length - self._segment_distance
            step = min(remaining, available)
            self._segment_distance += step
            self._distance += step
            remaining -= step
            advanced = advanced or step > _EPSILON

            if self._segment_distance < segment.length - _EPSILON:
                break
            self._segment_distance = segment.length
            if self._segment_index + 1 < len(self._segments):
                self._segment_index += 1
                self._segment_distance = 0.0
            elif self.loop:
                self._segment_index = 0
                self._segment_distance = 0.0
            else:
                self._finished = True
                break

        return self._output(moving=advanced and not self._finished)
