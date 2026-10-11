"""Detached, versioned observations for caller-owned simulator sessions.

This is a runtime sampling adapter, not a task or benchmark contract. The
sampler owns how live state is measured; the runner never infers world changes
from a planner's or executor's claims.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from algorithm.common_coordination.contract import RobotCapability
from .contracts import RobotState


@dataclass(frozen=True, init=False)
class WorldSnapshot:
    """JSON-detached observation with a monotonic revision and physics tick.

    Accessors return copies: a planner cannot mutate the sampler's observation
    or another consumer's view. Object payloads are opaque, caller-defined
    JSON; no scene-specific object/skill semantics are introduced here.
    """

    _payload: str

    def __init__(
        self, *, scene_id: str, revision: int, tick: int,
        robots: Sequence[RobotState], objects: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise ValueError("observation scene_id must be nonempty")
        for name, value in (("revision", revision), ("tick", tick)):
            if type(value) is not int or value < 0:
                raise ValueError(f"observation {name} must be a nonnegative integer")
        records = []
        ids = set()
        for robot in robots:
            if not isinstance(robot, RobotState):
                raise TypeError("observation robots must be RobotState records")
            capability = robot.capability
            if capability.robot_id in ids:
                raise ValueError("duplicate observation robot identity")
            ids.add(capability.robot_id)
            if type(capability.active) is not bool:
                raise ValueError("robot active must be a boolean")
            if robot.position is not None and any(
                isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                for v in robot.position
            ):
                raise ValueError("robot position must be finite")
            records.append({
                "robot_id": capability.robot_id,
                "capabilities": sorted(capability.capabilities),
                "active": capability.active,
                "position": robot.position,
                "metadata": dict(robot.metadata),
            })
        if objects is not None and not isinstance(objects, Mapping):
            raise TypeError("observation objects must be a mapping")
        payload = {
            "scene_id": scene_id, "revision": revision, "tick": tick,
            "robots": records, "objects": dict(objects or {}),
        }
        object.__setattr__(self, "_payload", json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ))

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._payload)

    @property
    def scene_id(self) -> str:
        return self.to_dict()["scene_id"]

    @property
    def revision(self) -> int:
        return self.to_dict()["revision"]

    @property
    def tick(self) -> int:
        return self.to_dict()["tick"]

    @property
    def digest(self) -> str:
        return hashlib.sha256(self._payload.encode("utf-8")).hexdigest()

    @property
    def robots(self) -> tuple[RobotState, ...]:
        return tuple(RobotState(
            RobotCapability(item["robot_id"], frozenset(item["capabilities"]), item["active"]),
            tuple(item["position"]) if item["position"] is not None else None,
            item["metadata"],
        ) for item in self.to_dict()["robots"])

    def validate_successor(self, previous: WorldSnapshot) -> None:
        if self.scene_id != previous.scene_id:
            raise ValueError("observation scene changed")
        if self.revision < previous.revision or self.tick < previous.tick:
            raise ValueError("observation revision/tick regressed")
        if self.revision == previous.revision and self.digest != previous.digest:
            raise ValueError("observation changed without a new revision")
