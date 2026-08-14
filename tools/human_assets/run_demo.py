#!/usr/bin/env python3
"""Load and validate every enabled human asset in Isaac Sim 5.1."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
for _relative in (".", "source/EAI_assets"):
    _source = str(REPO_ROOT / _relative)
    if _source not in sys.path:
        sys.path.insert(0, _source)

from EAI_assets.humans import (  # noqa: E402
    HumanActorConfig,
    HumanAssetRegistry,
    RetargetCacheEntry,
    RetargetCacheError,
    UsdHumanStageRuntime,
    resolve_retarget_cache_path,
)
from tools.human_assets.motion_controls import (  # noqa: E402
    MOTION_NUMBER_CATALOG,
    ActorControlSpec,
    HumanDemoControlResult,
    HumanDemoControls,
)
from tools.human_assets.scene import (  # noqa: E402
    create_demo_environment,
    create_route_curve,
    create_selection_rings,
    focus_camera_on_prim,
    grid_origins,
    update_selection_rings,
    validate_actor_bounds,
    world_bounds,
)


EXPECTED_ASSET_COUNT = 44
EXPECTED_ACTION_ASSET_COUNT = 39
EXPECTED_RIGID_MOVABLE_COUNT = 4
EXPECTED_STATIC_COUNT = 1
ROUTE_DISTANCE = 2.5
ROUTE_COLORS = (
    (0.90, 0.25, 0.20),
    (0.20, 0.70, 0.35),
    (0.20, 0.50, 0.95),
    (0.95, 0.75, 0.15),
    (0.10, 0.75, 0.80),
    (0.85, 0.35, 0.70),
    (0.95, 0.50, 0.15),
    (0.75, 0.78, 0.82),
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the complete 39x12 + 4 + 1 validation matrix and exit",
    )
    return parser.parse_args(argv)


def load_demo_registry(human_root: str | Path) -> HumanAssetRegistry:
    human_root = Path(human_root).resolve()
    return HumanAssetRegistry.load(
        human_root / "manifest.json",
        asset_root=human_root,
        file_policy="metadata",
    )


def enabled_demo_assets(registry: HumanAssetRegistry) -> tuple[Any, ...]:
    assets = registry.assets()
    action_assets = tuple(
        asset
        for asset in assets
        if asset.articulated and asset.can_play_actions
    )
    rigid_movable = tuple(
        asset
        for asset in assets
        if not asset.can_play_actions and asset.path_following
    )
    static_assets = tuple(
        asset
        for asset in assets
        if not asset.can_play_actions and not asset.path_following
    )
    counts = (
        len(assets),
        len(action_assets),
        len(rigid_movable),
        len(static_assets),
    )
    expected = (
        EXPECTED_ASSET_COUNT,
        EXPECTED_ACTION_ASSET_COUNT,
        EXPECTED_RIGID_MOVABLE_COUNT,
        EXPECTED_STATIC_COUNT,
    )
    if counts != expected:
        raise RuntimeError(
            "human demo capability matrix is unexpected: "
            f"total/action/movable/static={counts}, expected={expected}"
        )
    for asset in action_assets:
        if tuple(asset.motions) != MOTION_NUMBER_CATALOG:
            raise RuntimeError(
                f"human asset '{asset.id}' does not advertise the canonical "
                "12-motion demo catalog"
            )
    return assets


def actor_configs(assets: Sequence[Any]) -> tuple[HumanActorConfig, ...]:
    assets = tuple(assets)
    origins = grid_origins(len(assets))
    action_count = sum(bool(asset.can_play_actions) for asset in assets)
    action_index = 0
    configs: list[HumanActorConfig] = []
    for index, (asset, origin) in enumerate(zip(assets, origins), start=1):
        if asset.path_following:
            waypoints = (
                origin,
                (origin[0] + ROUTE_DISTANCE, origin[1], origin[2]),
                origin,
            )
        else:
            waypoints = ()
        phase = action_index / action_count if asset.can_play_actions else 0.0
        action_index += int(bool(asset.can_play_actions))
        configs.append(
            HumanActorConfig(
                actor_id=f"human-{index}",
                asset_id=asset.id,
                prim_path=f"/World/Humans/human_{index}",
                initial_pose=(*origin, 0.0),
                waypoints=waypoints,
                speed=asset.default_speed,
                loop=False,
                phase=phase,
                locomotion_motion_id="walk" if asset.can_play_actions else None,
            )
        )
    return tuple(configs)


def actor_control_specs(
    assets: Sequence[Any],
    configs: Sequence[HumanActorConfig],
) -> tuple[ActorControlSpec, ...]:
    assets = tuple(assets)
    configs = tuple(configs)
    if len(assets) != len(configs):
        raise ValueError("asset and actor configuration counts differ")
    specs = []
    for asset, config in zip(assets, configs):
        if asset.id != config.asset_id:
            raise ValueError("asset and actor configuration order differs")
        specs.append(
            ActorControlSpec(
                actor_id=config.actor_id,
                asset_id=asset.id,
                label=asset.label,
                can_play_actions=asset.can_play_actions,
                path_following=asset.path_following,
            )
        )
    return tuple(specs)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_demo_files(
    registry: HumanAssetRegistry,
    assets: Sequence[Any],
) -> None:
    required = [asset.usd_path for asset in assets]
    required.extend(
        registry.motion(motion_id).usd_path
        for motion_id in MOTION_NUMBER_CATALOG
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "human demo files are missing: "
            + ", ".join(path.as_posix() for path in missing)
        )


def _validate_retarget_caches(
    registry: HumanAssetRegistry,
    cache_root: Path,
    assets: Sequence[Any],
) -> dict[tuple[str, str], RetargetCacheEntry]:
    entries: dict[tuple[str, str], RetargetCacheEntry] = {}
    source_hashes = {
        motion_id: _sha256(registry.motion(motion_id).usd_path)
        for motion_id in MOTION_NUMBER_CATALOG
    }
    for asset in assets:
        if not asset.can_play_actions:
            continue
        target_hash = _sha256(asset.usd_path)
        for motion_id in MOTION_NUMBER_CATALOG:
            motion = registry.require_motion(asset.id, motion_id)
            cache_path, cached_motion_id = resolve_retarget_cache_path(
                cache_root, asset.id, motion_id
            )
            try:
                document = json.loads(cache_path.read_text(encoding="utf-8"))
                entry = RetargetCacheEntry.from_document(
                    document,
                    expected_source_sha256=source_hashes[motion.id],
                    expected_target_sha256=target_hash,
                )
            except (OSError, json.JSONDecodeError, RetargetCacheError) as exc:
                raise RuntimeError(
                    f"missing or invalid retarget cache for "
                    f"{asset.id}/{motion_id}: {cache_path}"
                ) from exc
            if entry.asset_id != asset.id or entry.motion_id != cached_motion_id:
                raise RuntimeError(f"retarget cache identity mismatch: {cache_path}")
            entries[(asset.id, motion_id)] = entry
    expected = EXPECTED_ACTION_ASSET_COUNT * len(MOTION_NUMBER_CATALOG)
    if len(entries) != expected:
        raise RuntimeError(
            f"validated {len(entries)} retarget caches; expected {expected}"
        )
    return entries


@dataclass(frozen=True)
class ActiveDemoCommand:
    actor_id: str
    kind: Literal["action", "movement"]
    origin: tuple[float, float, float]
    motion_id: str | None = None


def _positions_close(
    left: Sequence[float],
    right: Sequence[float],
    *,
    tolerance: float = 1.0e-6,
) -> bool:
    return math.dist(tuple(float(value) for value in left), tuple(float(value) for value in right)) <= tolerance


class HumanDemoBackend:
    """Demo-scoped command lifecycle and exact path-origin restoration."""

    def __init__(
        self,
        runtime: Any,
        registry: Any,
        actors: Sequence[ActorControlSpec],
    ) -> None:
        self.runtime = runtime
        self.registry = registry
        self.actors = {actor.actor_id: actor for actor in actors}
        self._origins = {
            actor_id: tuple(runtime.position(actor_id))
            for actor_id in self.actors
        }
        self._active: dict[str, ActiveDemoCommand] = {}

    def _actor(self, actor_id: str) -> ActorControlSpec:
        try:
            return self.actors[str(actor_id)]
        except KeyError as exc:
            raise KeyError(f"unknown demo actor: {actor_id}") from exc

    def active_command(self, actor_id: str) -> ActiveDemoCommand | None:
        self._actor(actor_id)
        return self._active.get(str(actor_id))

    def pause_all(self) -> None:
        for actor in self.actors.values():
            if actor.path_following:
                self.runtime.pause(actor.actor_id)

    def stop_and_restore(self, actor_id: str) -> None:
        actor = self._actor(actor_id)
        actor_id = actor.actor_id
        active = self._active.pop(actor_id, None)
        origin = active.origin if active is not None else self._origins[actor_id]
        if actor.path_following:
            self.runtime.pause(actor_id)
        if actor.can_play_actions:
            self.runtime.cancel_action(actor_id)
            self.runtime.update(0.0)
        self.runtime.reset(actor_id)
        restored = self.runtime.position(actor_id)
        if not _positions_close(restored, origin):
            raise AssertionError(
                f"actor '{actor_id}' did not restore to its pre-command position: "
                f"expected={origin}, actual={restored}"
            )

    def start_action(self, actor_id: str, motion_id: str) -> None:
        actor = self._actor(actor_id)
        if not actor.can_play_actions:
            raise ValueError(f"actor '{actor_id}' cannot play actions")
        motion = self.registry.motion(motion_id)
        self.stop_and_restore(actor_id)
        origin = tuple(self.runtime.position(actor_id))
        self.runtime.play_action(actor_id, motion.id)
        if motion.path_policy == "continue":
            self.runtime.resume(actor_id)
        else:
            self.runtime.pause(actor_id)
        self._active[actor_id] = ActiveDemoCommand(
            actor_id=actor_id,
            kind="action",
            origin=origin,
            motion_id=motion.id,
        )

    def start_movement(self, actor_id: str) -> None:
        actor = self._actor(actor_id)
        if actor.can_play_actions or not actor.path_following:
            raise ValueError(f"actor '{actor_id}' does not support rigid movement")
        self.stop_and_restore(actor_id)
        origin = tuple(self.runtime.position(actor_id))
        self.runtime.resume(actor_id)
        self._active[actor_id] = ActiveDemoCommand(
            actor_id=actor_id,
            kind="movement",
            origin=origin,
        )

    def tick(self, dt: float) -> Any:
        update = self.runtime.update(dt)
        completed = {
            event.actor_id
            for event in update.events
            if event.kind == "completed"
            and (active := self._active.get(event.actor_id)) is not None
            and active.kind == "action"
            and active.motion_id == event.motion_id
        }
        completed.update(
            actor_id
            for actor_id, pose in update.poses
            if pose.finished
            and (active := self._active.get(actor_id)) is not None
            and active.kind == "movement"
        )
        for actor_id in sorted(completed):
            self.stop_and_restore(actor_id)
        return update


def _verify_stage_actors(
    stage: Any,
    registry: HumanAssetRegistry,
    configs: Sequence[HumanActorConfig],
    runtime: UsdHumanStageRuntime,
) -> None:
    from pxr import Usd, UsdGeom, UsdSkel

    animation_paths: set[str] = set()
    for config in configs:
        asset = registry.asset(config.asset_id)
        actor_prim = stage.GetPrimAtPath(config.prim_path)
        if not actor_prim.IsValid():
            raise RuntimeError(f"actor root is missing: {config.prim_path}")
        descendants = tuple(Usd.PrimRange(actor_prim))
        if not any(prim.IsA(UsdGeom.Boundable) for prim in descendants):
            raise RuntimeError(f"actor geometry is missing: {config.actor_id}")
        minimum, size = world_bounds(stage, config.prim_path)
        validate_actor_bounds(
            minimum=minimum,
            size=size,
            asset_id=asset.id,
        )
        if not asset.can_play_actions:
            continue
        if not any(prim.IsA(UsdSkel.Skeleton) for prim in descendants):
            raise RuntimeError(f"actor skeleton is missing: {config.actor_id}")
        animation_path = runtime.animation_path(config.actor_id)
        if not stage.GetPrimAtPath(animation_path).IsA(UsdSkel.Animation):
            raise RuntimeError(f"runtime animation is missing: {config.actor_id}")
        animation_paths.add(str(animation_path))
    if len(animation_paths) != EXPECTED_ACTION_ASSET_COUNT:
        raise RuntimeError("runtime animation paths are not action-actor-local")


def _animation_signature(stage: Any, path: Any) -> tuple[tuple[float, ...], ...]:
    from pxr import UsdSkel

    values = UsdSkel.Animation(stage.GetPrimAtPath(path)).GetRotationsAttr().Get()
    if not values:
        return ()
    return tuple(
        (
            float(value.GetReal()),
            float(value.GetImaginary()[0]),
            float(value.GetImaginary()[1]),
            float(value.GetImaginary()[2]),
        )
        for value in values
    )


def horizontal_root_error(
    *,
    root_translation: Sequence[float],
    rest_translation: Sequence[float],
    root_up: Sequence[float],
) -> float:
    delta = tuple(
        float(root_translation[axis]) - float(rest_translation[axis])
        for axis in range(3)
    )
    vertical = sum(delta[axis] * float(root_up[axis]) for axis in range(3))
    horizontal = tuple(
        delta[axis] - vertical * float(root_up[axis]) for axis in range(3)
    )
    return math.sqrt(sum(component * component for component in horizontal))


def _runtime_root_error(
    stage: Any,
    animation_path: Any,
    entry: RetargetCacheEntry,
) -> float:
    from pxr import UsdSkel

    animation = UsdSkel.Animation(stage.GetPrimAtPath(animation_path))
    joints = tuple(str(joint) for joint in animation.GetJointsAttr().Get() or [])
    translations = animation.GetTranslationsAttr().Get() or []
    if len(translations) != len(joints):
        raise RuntimeError("runtime animation root translation sample is missing")
    root_index = entry.plan.target_root_index
    if joints[root_index] != entry.plan.target_joints[root_index]:
        raise RuntimeError("runtime animation root joint does not match retarget cache")
    return horizontal_root_error(
        root_translation=translations[root_index],
        rest_translation=entry.plan.target_rest_translations[root_index],
        root_up=entry.plan.target_root_up,
    )


def _keyboard_mapping(carb_input: Any) -> dict[Any, str]:
    keyboard = carb_input.KeyboardInput
    mapping = {
        keyboard.Q: "Q",
        keyboard.ENTER: "ENTER",
        keyboard.BACKSPACE: "BACKSPACE",
        keyboard.X: "X",
        keyboard.ESCAPE: "ESCAPE",
    }
    for digit in range(10):
        mapping[getattr(keyboard, f"KEY_{digit}")] = str(digit)
    return mapping


def _print_catalog(registry: HumanAssetRegistry) -> None:
    print("Controls: Q next actor; digits + Enter select action; X restore; Esc close")
    print("Canonical articulated actions:")
    for number, motion_id in enumerate(MOTION_NUMBER_CATALOG, start=1):
        motion = registry.motion(motion_id)
        print(
            f"  {number:2d}  {motion.id:<22} "
            f"duration={motion.duration:7.3f}s "
            f"loop={str(motion.loop).lower():5} path={motion.path_policy}"
        )
    print("Movable rigid actors accept only action 1; static actors reject actions")


def _submit_number(
    controls: HumanDemoControls,
    number: int,
) -> HumanDemoControlResult:
    for digit in str(number):
        controls.handle_key(digit)
    return controls.handle_key("ENTER")


def _assert_restored(
    runtime: UsdHumanStageRuntime,
    actor_id: str,
    origin: Sequence[float],
) -> None:
    actual = runtime.position(actor_id)
    if not _positions_close(actual, origin):
        raise AssertionError(
            f"actor '{actor_id}' was not restored: expected={tuple(origin)}, "
            f"actual={actual}"
        )


def _run_headless_matrix(
    *,
    stage: Any,
    registry: HumanAssetRegistry,
    runtime: UsdHumanStageRuntime,
    backend: HumanDemoBackend,
    controls: HumanDemoControls,
    actors: Sequence[Any],
    specs: Sequence[ActorControlSpec],
    cache_entries: dict[tuple[str, str], RetargetCacheEntry],
) -> None:
    action_checks = 0
    rigid_checks = 0
    static_checks = 0
    for index, (asset, expected_spec) in enumerate(zip(actors, specs)):
        spec = controls.selected_actor
        if spec != expected_spec or spec.asset_id != asset.id:
            raise AssertionError(
                f"headless selection order mismatch: expected={asset.id}, "
                f"actual={spec.asset_id}"
            )
        origin = runtime.position(spec.actor_id)
        if spec.can_play_actions:
            animation_path = runtime.animation_path(spec.actor_id)
            for number, motion_id in enumerate(MOTION_NUMBER_CATALOG, start=1):
                result = _submit_number(controls, number)
                if result.submitted_motion_id != motion_id:
                    raise AssertionError(
                        f"{asset.id}: action {number} selected "
                        f"{result.submitted_motion_id}, expected {motion_id}"
                    )
                motion = registry.motion(motion_id)
                sample_dt = max(min(motion.duration * 0.25, 1.0 / 30.0), 1.0e-4)
                backend.tick(sample_dt)
                if not _animation_signature(stage, animation_path):
                    raise AssertionError(
                        f"{asset.id}/{motion_id} produced no animation sample"
                    )
                if motion.root_motion == "in_place":
                    root_error = _runtime_root_error(
                        stage,
                        animation_path,
                        cache_entries[(asset.id, motion_id)],
                    )
                    if root_error > 1.0e-4:
                        raise AssertionError(
                            f"{asset.id}/{motion_id} in-place horizontal root "
                            f"error is {root_error}"
                        )
                moved = not _positions_close(runtime.position(spec.actor_id), origin)
                if moved != (motion.path_policy == "continue"):
                    raise AssertionError(
                        f"{asset.id}/{motion_id}: path={motion.path_policy}, "
                        f"moved={moved}"
                    )
                if motion.loop:
                    controls.handle_key("X")
                else:
                    backend.tick(motion.duration + 1.0 / 30.0)
                if backend.active_command(spec.actor_id) is not None:
                    raise AssertionError(
                        f"{asset.id}/{motion_id} remained active after completion"
                    )
                _assert_restored(runtime, spec.actor_id, origin)
                action_checks += 1
                print(
                    f"Verified {asset.id}/{motion_id}: "
                    f"path={motion.path_policy} loop={motion.loop} restored=true"
                )
        elif spec.path_following:
            result = _submit_number(controls, 1)
            if not result.started_movement:
                raise AssertionError(
                    f"{asset.id}: rigid movement 1 was not started: {result.message}"
                )
            route_duration = 2.0 * ROUTE_DISTANCE / asset.default_speed
            backend.tick(min(route_duration * 0.25, 1.0 / 30.0))
            if _positions_close(runtime.position(spec.actor_id), origin):
                raise AssertionError(f"{asset.id}: rigid actor did not move outbound")
            backend.tick(route_duration + 1.0 / 30.0)
            if backend.active_command(spec.actor_id) is not None:
                raise AssertionError(f"{asset.id}: rigid movement did not finish")
            _assert_restored(runtime, spec.actor_id, origin)
            rigid_checks += 1
            print(f"Verified {asset.id}: rigid outbound-return restored=true")
        else:
            result = _submit_number(controls, 1)
            if result.started_movement or result.submitted_motion_id is not None:
                raise AssertionError(f"{asset.id}: static actor accepted action 1")
            if "static" not in result.message.lower():
                raise AssertionError(
                    f"{asset.id}: static rejection message is missing: {result.message}"
                )
            _assert_restored(runtime, spec.actor_id, origin)
            static_checks += 1
            print(f"Verified {asset.id}: static action rejected restored=true")

        if index + 1 < len(specs):
            next_result = controls.handle_key("Q")
            _assert_restored(runtime, spec.actor_id, origin)
            if next_result.selected_actor != specs[index + 1]:
                raise AssertionError("Q did not select the next actor")

    expected_actions = EXPECTED_ACTION_ASSET_COUNT * len(MOTION_NUMBER_CATALOG)
    if (action_checks, rigid_checks, static_checks) != (
        expected_actions,
        EXPECTED_RIGID_MOVABLE_COUNT,
        EXPECTED_STATIC_COUNT,
    ):
        raise AssertionError(
            "headless matrix count mismatch: "
            f"actions={action_checks}, rigid={rigid_checks}, static={static_checks}"
        )
    print(
        "Verified unified human matrix: "
        f"{EXPECTED_ACTION_ASSET_COUNT}x{len(MOTION_NUMBER_CATALOG)} + "
        f"{EXPECTED_RIGID_MOVABLE_COUNT} + {EXPECTED_STATIC_COUNT}"
    )


def _run_demo(args: argparse.Namespace, simulation_app: Any) -> int:
    from EAI_assets import asset_resolver
    from pxr import UsdGeom
    import omni.usd

    human_root = Path(asset_resolver.asset_path("human")).resolve()
    registry = load_demo_registry(human_root)
    assets = enabled_demo_assets(registry)
    configs = actor_configs(assets)
    specs = actor_control_specs(assets, configs)
    cache_root = human_root / "motions/cache"
    _require_demo_files(registry, assets)
    cache_entries = _validate_retarget_caches(
        registry, cache_root, assets
    )

    context = omni.usd.get_context()
    stage = context.get_stage()
    if stage is None:
        context.new_stage()
        simulation_app.update()
        stage = context.get_stage()
    if stage is None:
        raise RuntimeError("Isaac Sim did not create a USD Stage")
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    origins = tuple(config.initial_pose[:3] for config in configs)
    create_demo_environment(stage, origins)
    route_index = 0
    for config in configs:
        if not config.waypoints:
            continue
        create_route_curve(
            stage,
            f"/World/Demo/Routes/route_{route_index + 1}",
            config.waypoints,
            ROUTE_COLORS[route_index % len(ROUTE_COLORS)],
        )
        route_index += 1

    runtime = UsdHumanStageRuntime(stage, registry, cache_root=cache_root)
    input_interface = None
    keyboard = None
    subscription = None
    backend: HumanDemoBackend | None = None
    try:
        for config in configs:
            runtime.spawn(config)
            print(f"Spawned {config.actor_id}: {config.asset_id}")
        backend = HumanDemoBackend(runtime, registry, specs)
        backend.pause_all()
        runtime.update(0.0)
        simulation_app.update()
        simulation_app.update()
        _verify_stage_actors(stage, registry, configs, runtime)

        rings = create_selection_rings(stage, runtime.actor_ids)
        controls = HumanDemoControls(backend, specs)
        positions = {
            actor_id: runtime.position(actor_id) for actor_id in runtime.actor_ids
        }
        update_selection_rings(
            rings, (controls.selected_actor.actor_id,), positions
        )
        simulation_app.update()
        _print_catalog(registry)

        if args.headless:
            _run_headless_matrix(
                stage=stage,
                registry=registry,
                runtime=runtime,
                backend=backend,
                controls=controls,
                actors=assets,
                specs=specs,
                cache_entries=cache_entries,
            )
            return 0

        import carb.input
        import omni.appwindow

        focus_camera_on_prim(stage, configs[0].prim_path)
        input_interface = carb.input.acquire_input_interface()
        keyboard = omni.appwindow.get_default_app_window().get_keyboard()
        mapping = _keyboard_mapping(carb.input)
        pending_focus: list[str] = []

        def on_keyboard(event: Any, *_unused: Any) -> bool:
            if event.type != carb.input.KeyboardEventType.KEY_PRESS:
                return True
            mapped = mapping.get(event.input)
            if mapped is None:
                return True
            try:
                result = controls.handle_key(mapped)
                if result.message:
                    print(result.message)
                if mapped == "Q":
                    pending_focus[:] = [result.selected_actor.actor_id]
            except Exception as exc:
                print(f"command error: {exc}", file=sys.stderr)
            return True

        subscription = input_interface.subscribe_to_keyboard_events(
            keyboard, on_keyboard
        )
        config_by_actor = {config.actor_id: config for config in configs}
        previous = time.perf_counter()
        while simulation_app.is_running() and not controls.should_quit:
            now = time.perf_counter()
            dt = min(max(now - previous, 0.0), 0.1)
            update = backend.tick(dt)
            for event in update.events:
                print(f"{event.actor_id}: {event.motion_id} {event.kind}")
            positions = {
                actor_id: runtime.position(actor_id)
                for actor_id in runtime.actor_ids
            }
            update_selection_rings(
                rings, (controls.selected_actor.actor_id,), positions
            )
            if pending_focus:
                actor_id = pending_focus.pop()
                focus_camera_on_prim(stage, config_by_actor[actor_id].prim_path)
            simulation_app.update()
            previous = now
        return 0
    finally:
        if subscription is not None and input_interface is not None and keyboard is not None:
            input_interface.unsubscribe_to_keyboard_events(keyboard, subscription)
        if backend is not None:
            for actor_id in tuple(backend.actors):
                backend.stop_and_restore(actor_id)
        runtime.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    from isaacsim import SimulationApp

    simulation_app = SimulationApp(
        {
            "headless": args.headless,
            "create_new_stage": True,
            "window_width": 1440,
            "window_height": 900,
            "renderer": "RaytracedLighting",
        }
    )
    try:
        return _run_demo(args, simulation_app)
    except Exception as exc:
        print(f"human demo error: {exc}", file=sys.stderr)
        return 1
    finally:
        simulation_app.close()


if __name__ == "__main__":
    raise SystemExit(main())
