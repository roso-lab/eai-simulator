# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Live USD Stage bridge for path-driven animated human actors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .animation_runtime import (
    HumanMotionController,
    MotionEvent,
    UsdHumanAnimationAdapter,
)
from .asset_placement import apply_asset_placement
from .path_follower import PathFollowerOutput, PauseLease
from .registry import HumanAssetCapabilityError, HumanAssetRegistry
from .spawner import HumanSpawner, SpawnedHuman


@dataclass(frozen=True)
class HumanActorConfig:
    actor_id: str
    asset_id: str
    prim_path: str
    initial_pose: tuple[float, float, float, float]
    waypoints: tuple[tuple[float, float, float], ...]
    speed: float
    loop: bool = True
    phase: float = 0.0
    locomotion_motion_id: str | None = "walk"


@dataclass(frozen=True)
class HumanStageUpdate:
    events: tuple[MotionEvent, ...]
    poses: tuple[tuple[str, PathFollowerOutput], ...]


@dataclass
class _StageActorBinding:
    prim_path: Any
    asset_path: Any
    translate_op: Any
    orient_op: Any


class UsdHumanStageRuntime:
    """Own human motion, paths, and their authored state on one USD Stage."""

    def __init__(
        self,
        stage: Any,
        registry: HumanAssetRegistry,
        *,
        cache_root: str | Path,
        verify_hashes: bool = True,
    ) -> None:
        from pxr import Gf, Sdf, Usd, UsdGeom

        self.stage = stage
        self.registry = registry
        self._Gf = Gf
        self._Sdf = Sdf
        self._Usd = Usd
        self._UsdGeom = UsdGeom
        self._controller = HumanMotionController(registry)
        self._spawner = HumanSpawner(registry, self._controller)
        self._adapter = UsdHumanAnimationAdapter(
            stage,
            registry,
            cache_root=cache_root,
            verify_hashes=verify_hashes,
        )
        self._actors: dict[str, SpawnedHuman] = {}
        self._configs: dict[str, HumanActorConfig] = {}
        self._bindings: dict[str, _StageActorBinding] = {}
        self._adapter_actor_ids: set[str] = set()
        self._manual_leases: dict[str, PauseLease] = {}
        self._action_leases: dict[str, PauseLease] = {}
        self._sampled_motion_ids: dict[str, str] = {}
        self._pending_sample_regrounds: set[str] = set()
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("UsdHumanStageRuntime is closed")

    def _actor(self, actor_id: str) -> SpawnedHuman:
        self._require_open()
        try:
            return self._actors[str(actor_id)]
        except KeyError as exc:
            raise KeyError(f"unknown USD human actor: {actor_id}") from exc

    def _write_binding_pose(
        self,
        binding: _StageActorBinding,
        pose: PathFollowerOutput,
        *,
        facing_yaw_offset: float = 0.0,
    ) -> None:
        binding.translate_op.Set(self._Gf.Vec3d(*pose.position))
        half_yaw = 0.5 * (pose.yaw + float(facing_yaw_offset))
        binding.orient_op.Set(
            self._Gf.Quatd(
                math.cos(half_yaw),
                self._Gf.Vec3d(0.0, 0.0, math.sin(half_yaw)),
            )
        )

    def _write_pose(self, actor_id: str, pose: PathFollowerOutput) -> None:
        motion_id = self._sampled_motion_ids.get(actor_id)
        facing_yaw_offset = (
            self.registry.motion(motion_id).facing_yaw_offset
            if motion_id is not None
            else 0.0
        )
        self._write_binding_pose(
            self._bindings[actor_id],
            pose,
            facing_yaw_offset=facing_yaw_offset,
        )

    def _reground_for_current_pose(self, actor_id: str) -> None:
        actor = self._actors[actor_id]
        pose = actor.pending_pose
        if pose is None:
            raise RuntimeError(f"actor '{actor_id}' has no pose")
        apply_asset_placement(
            self._UsdGeom.Xformable(
                self.stage.GetPrimAtPath(self._bindings[actor_id].asset_path)
            ),
            self.registry.asset(actor.asset_id),
            pose.position[2],
        )

    def spawn(self, config: HumanActorConfig) -> SpawnedHuman:
        self._require_open()
        actor_id = str(config.actor_id)
        if actor_id in self._actors:
            raise ValueError(f"actor '{actor_id}' is already spawned")
        if len(config.initial_pose) != 4:
            raise ValueError("initial_pose must contain x, y, z, and yaw")
        prim_path = self._Sdf.Path(str(config.prim_path))
        if not prim_path.IsAbsolutePath() or not prim_path.IsPrimPath():
            raise ValueError(f"invalid actor prim path: {config.prim_path}")
        if self.stage.GetPrimAtPath(prim_path).IsValid():
            raise ValueError(f"actor prim already exists: {prim_path}")

        asset = self.registry.asset(config.asset_id)
        root_xform = self._UsdGeom.Xform.Define(self.stage, prim_path)
        root_xform.ClearXformOpOrder()
        translate_op = root_xform.AddTranslateOp(
            precision=self._UsdGeom.XformOp.PrecisionDouble,
            opSuffix="eaiRuntime",
        )
        orient_op = root_xform.AddOrientOp(
            precision=self._UsdGeom.XformOp.PrecisionDouble,
            opSuffix="eaiRuntime",
        )

        asset_path = prim_path.AppendChild("Asset")
        asset_xform = self._UsdGeom.Xform.Define(self.stage, asset_path)
        payload_path = asset_path.AppendChild("Payload")
        payload_xform = self._UsdGeom.Xform.Define(self.stage, payload_path)
        payload_prim = payload_xform.GetPrim()
        payload_prim.GetReferences().AddReference(asset.usd_path.as_posix())
        payload_prim.SetInstanceable(False)
        asset_xform.ClearXformOpOrder()

        binding = _StageActorBinding(
            prim_path=prim_path,
            asset_path=asset_path,
            translate_op=translate_op,
            orient_op=orient_op,
        )

        actor: SpawnedHuman | None = None
        adapter_registered = False
        try:
            animation_capable = asset.articulated and asset.can_play_actions
            actor = self._spawner.spawn(
                actor_id,
                config.asset_id,
                prim_path.pathString,
                config.initial_pose,
                movement_mode="path" if config.waypoints else "external",
                waypoints=config.waypoints or None,
                speed=config.speed,
                loop=config.loop,
                phase=config.phase,
            )
            if animation_capable and config.locomotion_motion_id is not None:
                self._controller.set_locomotion(
                    actor_id, config.locomotion_motion_id
                )
            if animation_capable:
                self._adapter.register_actor(
                    actor_id, config.asset_id, payload_path
                )
                adapter_registered = True
                initial_motion = self._controller.update(0.0)
                self._adapter.apply_all(initial_motion.requests)
                for request in initial_motion.requests:
                    self._sampled_motion_ids[request.actor_id] = request.motion_id
            initial = actor.pending_pose
            if initial is None:
                raise RuntimeError(f"actor '{actor_id}' has no initial pose")
            initial_motion_id = self._sampled_motion_ids.get(actor_id)
            initial_facing_yaw_offset = (
                self.registry.motion(initial_motion_id).facing_yaw_offset
                if initial_motion_id is not None
                else 0.0
            )
            self._write_binding_pose(
                binding,
                initial,
                facing_yaw_offset=initial_facing_yaw_offset,
            )
            apply_asset_placement(asset_xform, asset, initial.position[2])
        except Exception:
            if adapter_registered:
                self._adapter.unregister_actor(actor_id)
            if actor is not None:
                self._spawner.despawn(actor_id)
            self.stage.RemovePrim(prim_path)
            raise

        self._actors[actor_id] = actor
        self._configs[actor_id] = config
        self._bindings[actor_id] = binding
        if adapter_registered:
            self._adapter_actor_ids.add(actor_id)
        return actor

    @property
    def actor_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._actors))

    def animation_path(self, actor_id: str) -> Any:
        actor = self._actor(actor_id)
        actor_id = str(actor_id)
        if actor_id not in self._adapter_actor_ids:
            raise HumanAssetCapabilityError(
                f"human asset '{actor.asset_id}' does not support animation"
            )
        return self._adapter.animation_path(actor_id)

    def position(self, actor_id: str) -> tuple[float, float, float]:
        pose = self._actor(actor_id).pending_pose
        if pose is None:
            raise RuntimeError(f"actor '{actor_id}' has no pose")
        return pose.position

    def pause(self, actor_id: str) -> None:
        actor = self._actor(actor_id)
        actor_id = str(actor_id)
        if actor_id not in self._manual_leases:
            self._manual_leases[actor_id] = actor.acquire_movement_pause(
                "manual", "user"
            )

    def resume(self, actor_id: str) -> None:
        self._actor(actor_id)
        lease = self._manual_leases.pop(str(actor_id), None)
        if lease is not None:
            lease.release()

    def reset(self, actor_id: str) -> PathFollowerOutput:
        actor = self._actor(actor_id)
        pose = actor.reset_path()
        if pose is None:
            raise RuntimeError(f"actor '{actor_id}' has no path")
        self._write_pose(str(actor_id), pose)
        return pose

    def play_action(
        self,
        actor_id: str,
        motion_id: str,
        *,
        loop: bool | None = None,
        playback_speed: float = 1.0,
    ) -> None:
        actor = self._actor(actor_id)
        actor_id = str(actor_id)
        motion = self.registry.require_motion(actor.asset_id, motion_id)
        existing_lease = self._action_leases.get(actor_id)
        acquired_lease: PauseLease | None = None
        if motion.path_policy == "pause" and existing_lease is None:
            acquired_lease = actor.acquire_movement_pause("action", motion.id)
            self._action_leases[actor_id] = acquired_lease
        try:
            actor.play_action(
                motion.id,
                loop=loop,
                playback_speed=playback_speed,
            )
        except Exception:
            if acquired_lease is not None:
                self._action_leases.pop(actor_id, None)
                acquired_lease.release()
            raise
        if motion.path_policy != "pause" and existing_lease is not None:
            self._action_leases.pop(actor_id, None)
            existing_lease.release()

    def cancel_action(self, actor_id: str) -> None:
        actor = self._actor(actor_id)
        actor.cancel_action()
        self._pending_sample_regrounds.add(str(actor_id))
        lease = self._action_leases.pop(str(actor_id), None)
        if lease is not None:
            lease.release()

    def update(self, dt: float, *, context: Any = None) -> HumanStageUpdate:
        self._require_open()
        motion_update = self._controller.update(dt)
        self._adapter.apply_all(motion_update.requests)
        restarted = {
            event.actor_id
            for event in motion_update.events
            if event.kind == "started"
        }
        for request in motion_update.requests:
            previous = self._sampled_motion_ids.get(request.actor_id)
            if (
                request.actor_id in restarted
                or request.actor_id in self._pending_sample_regrounds
                or previous != request.motion_id
            ):
                self._reground_for_current_pose(request.actor_id)
            self._pending_sample_regrounds.discard(request.actor_id)
            self._sampled_motion_ids[request.actor_id] = request.motion_id

        poses: list[tuple[str, PathFollowerOutput]] = []
        for actor_id in self.actor_ids:
            pose = self._actors[actor_id].advance_path(dt, context=context)
            if pose is None:
                continue
            self._write_pose(actor_id, pose)
            poses.append((actor_id, pose))

        for event in motion_update.events:
            if event.kind == "completed":
                self._pending_sample_regrounds.add(event.actor_id)
                lease = self._action_leases.pop(event.actor_id, None)
                if lease is not None:
                    lease.release()
        return HumanStageUpdate(events=motion_update.events, poses=tuple(poses))

    def close(self) -> None:
        if self._closed:
            return
        for leases in (self._manual_leases, self._action_leases):
            for lease in leases.values():
                lease.release()
            leases.clear()
        for actor_id in tuple(self._actors):
            if actor_id in self._adapter_actor_ids:
                self._adapter.unregister_actor(actor_id)
            self._spawner.despawn(actor_id)
            self.stage.RemovePrim(self._bindings[actor_id].prim_path)
        self._actors.clear()
        self._configs.clear()
        self._bindings.clear()
        self._adapter_actor_ids.clear()
        self._sampled_motion_ids.clear()
        self._pending_sample_regrounds.clear()
        self._closed = True


__all__ = ["HumanActorConfig", "HumanStageUpdate", "UsdHumanStageRuntime"]
