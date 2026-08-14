# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Registry-driven human spawner with SpawnPlan and actor handles."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Sequence

from .animation_runtime import HumanMotionController, MotionEvent, MotionUpdate
from .path_follower import (
    MovementPauseController,
    MovementPolicy,
    PathFollower,
    PathFollowerOutput,
    PauseLease,
)
from .registry import HumanAssetRegistry, HumanAssetSpec


@dataclass(frozen=True)
class SpawnPlan:
    """Resolved spawn constants for one asset."""

    actor_id: str
    asset_id: str
    root_path: str
    scale: tuple[float, float, float]
    yaw_offset: float
    adapter_kind: str
    locomotion: str


class SpawnedHuman:
    """Actor handle bridging path following and motion playback."""

    def __init__(
        self,
        actor_id: str,
        asset_id: str,
        *,
        movement_mode: str,
        controller: HumanMotionController | None = None,
        follower: PathFollower | None = None,
        movement_gate: MovementPauseController | None = None,
    ) -> None:
        self.actor_id = str(actor_id)
        self.asset_id = str(asset_id)
        self._movement_mode = movement_mode
        self._controller = controller
        self._follower = follower
        self._movement_gate = movement_gate or MovementPauseController()
        self._closed = False
        self._pending_pose: PathFollowerOutput | None = None

    # ------------------------------------------------------------------
    # pose
    # ------------------------------------------------------------------

    def apply_pose(
        self,
        position: Sequence[float],
        yaw: float,
        *,
        speed: float,
        locomotion: str,
    ) -> None:
        if self._closed:
            raise RuntimeError(f"SpawnedHuman '{self.actor_id}' is closed")
        if len(position) != 3:
            raise ValueError("position must have exactly three components")
        self._pending_pose = PathFollowerOutput(
            position=(float(position[0]), float(position[1]), float(position[2])),
            yaw=float(yaw),
            speed=float(speed),
            locomotion=locomotion,
            finished=False,
        )

    # ------------------------------------------------------------------
    # action
    # ------------------------------------------------------------------

    def play_action(
        self,
        motion_id: str,
        *,
        loop: bool | None = None,
        playback_speed: float = 1.0,
    ) -> MotionEvent | None:
        if self._controller is None:
            return None
        _ = self._controller.play_action(
            self.actor_id,
            motion_id,
            loop=loop,
            playback_speed=playback_speed,
        )
        return None  # events are drained by update()

    def cancel_action(self) -> MotionEvent | None:
        if self._controller is None:
            return None
        self._controller.stop_action(self.actor_id)
        return None  # events are drained by update()

    # ------------------------------------------------------------------
    # path
    # ------------------------------------------------------------------

    def acquire_movement_pause(self, owner: str, reason: str) -> PauseLease:
        if self._closed:
            raise RuntimeError(f"SpawnedHuman '{self.actor_id}' is closed")
        return self._movement_gate.acquire(owner, reason)

    def advance_path(
        self, dt: float, *, context: Any = None
    ) -> PathFollowerOutput | None:
        if self._closed:
            raise RuntimeError(f"SpawnedHuman '{self.actor_id}' is closed")
        if self._follower is None:
            return self._pending_pose
        self._pending_pose = self._follower.update(dt, context=context)
        return self._pending_pose

    def reset_path(self) -> PathFollowerOutput | None:
        if self._closed:
            raise RuntimeError(f"SpawnedHuman '{self.actor_id}' is closed")
        if self._follower is None:
            return self._pending_pose
        self._pending_pose = self._follower.reset()
        return self._pending_pose

    # ------------------------------------------------------------------
    # tick
    # ------------------------------------------------------------------

    def update(self, dt: float, *, context: Any = None) -> tuple[MotionEvent, ...]:
        if self._closed:
            raise RuntimeError(f"SpawnedHuman '{self.actor_id}' is closed")
        events: tuple[MotionEvent, ...] = ()
        if self._controller is not None:
            motion_update = self._controller.update(dt)
            events = motion_update.events
        if self._follower is not None:
            self.advance_path(dt, context=context)
        elif self._movement_mode == "path":
            raise ValueError("waypoints are required for path movement_mode")
        return events

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------

    @property
    def movement_allowed(self) -> bool:
        if self._movement_gate.paused:
            return False
        if self._follower is not None:
            return not self._follower.paused
        return True

    @property
    def pending_pose(self) -> PathFollowerOutput | None:
        return self._pending_pose

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        if self._controller is not None:
            self._controller.unregister_actor(self.actor_id)


class HumanSpawner:
    """Factory that resolves SpawnPlans and manages spawned actors."""

    def __init__(
        self,
        registry: HumanAssetRegistry,
        controller: HumanMotionController | None = None,
        *,
        movement_gate: MovementPauseController | None = None,
    ) -> None:
        self.registry = registry
        self._controller = controller
        self._movement_gate = movement_gate
        self._spawned: dict[str, SpawnedHuman] = {}

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    _ACTIVITY_LOCOMOTION: dict[str, str] = {
        "pedestrian": "walk",
        "cyclist": "ride",
        "scooter_rider": "ride",
        "skateboarder": "roll",
        "wheelchair": "roll",
    }

    @staticmethod
    def plan_for(
        asset: HumanAssetSpec,
        *,
        actor_id: str,
        root_path: str,
    ) -> SpawnPlan:
        """Resolve transform and adapter kind from asset metadata."""
        adapter_kind = "usd_skel" if asset.articulated else "transform_only"
        locomotion = HumanSpawner._ACTIVITY_LOCOMOTION.get(
            asset.activity_type, asset.activity_type
        )
        return SpawnPlan(
            actor_id=str(actor_id),
            asset_id=asset.id,
            root_path=str(root_path),
            scale=asset.scale,
            yaw_offset=asset.yaw_offset,
            adapter_kind=adapter_kind,
            locomotion=locomotion,
        )

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------

    @staticmethod
    def select(
        registry: HumanAssetRegistry,
        *,
        activity_type: str | None = None,
        count: int = 1,
        seed: int | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> tuple[HumanAssetSpec, ...]:
        """Return a deterministic random sample of eligible assets."""
        count = max(0, int(count))
        pool = [
            asset
            for asset in registry.assets(activity_type=activity_type)
            if asset.path_following and asset.id not in exclude
        ]
        if count >= len(pool):
            return tuple(pool)
        rng = random.Random(seed)
        return tuple(rng.sample(pool, count))

    # ------------------------------------------------------------------
    # spawn / despawn
    # ------------------------------------------------------------------

    def spawn(
        self,
        actor_id: str,
        asset_id: str,
        root_path: str,
        initial_pose: Sequence[float],
        *,
        movement_mode: str = "external",
        waypoints: Sequence[Sequence[float]] | None = None,
        speed: float | None = None,
        loop: bool = False,
        policy: MovementPolicy | None = None,
        movement_gate: MovementPauseController | None = None,
        phase: float = 0.0,
    ) -> SpawnedHuman:
        actor_id = str(actor_id)
        asset_id = str(asset_id)
        if actor_id in self._spawned:
            raise ValueError(f"actor '{actor_id}' is already spawned")
        if movement_mode not in ("external", "path"):
            raise ValueError(f"unsupported movement_mode: {movement_mode}")

        asset = self.registry.asset(asset_id)
        plan = self.plan_for(asset, actor_id=actor_id, root_path=root_path)
        actor_gate = movement_gate or self._movement_gate or MovementPauseController()

        follower: PathFollower | None = None
        if movement_mode == "path":
            if not waypoints:
                raise ValueError("waypoints are required in path mode")
            follower_speed = speed if speed is not None else asset.default_speed
            if follower_speed <= 0.0:
                raise ValueError("speed must be positive for path following")
            follower = PathFollower(
                actor_id=actor_id,
                waypoints=waypoints,
                speed=follower_speed,
                loop=loop,
                locomotion=plan.locomotion,
                policy=policy,
                movement_gate=actor_gate,
            )

        if self._controller is not None and asset.articulated:
            self._controller.register_actor(actor_id, asset_id, phase=phase)

        spawned = SpawnedHuman(
            actor_id=actor_id,
            asset_id=asset_id,
            movement_mode=movement_mode,
            controller=self._controller if asset.articulated else None,
            follower=follower,
            movement_gate=actor_gate,
        )

        spawned.apply_pose(
            (float(initial_pose[0]), float(initial_pose[1]), float(initial_pose[2])),
            yaw=float(initial_pose[3]) if len(initial_pose) > 3 else 0.0,
            speed=speed if speed is not None else asset.default_speed,
            locomotion=plan.locomotion,
        )

        self._spawned[actor_id] = spawned
        return spawned

    def despawn(self, actor_id: str) -> None:
        spawned = self._spawned.pop(str(actor_id), None)
        if spawned is not None:
            spawned.close()

    def close(self) -> None:
        for spawned in list(self._spawned.values()):
            spawned.close()
        self._spawned.clear()
