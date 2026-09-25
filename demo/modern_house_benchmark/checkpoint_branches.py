"""Run MH-DAG-01 once, restore its standby checkpoint, and fan out GPU branches.

The runner uses one Isaac Kit process, one CUDA PhysX batch, one audited
Modern House collision copy, and 1..32 resident task rows.  The public checkpoint
contains articulation, rigid-object, controller, actuator, environment-buffer,
and RNG state.  Private PhysX solver/contact/warm-start caches are outside the
checkpoint by design.

Modern House transport follows the benchmark's declared kinematic baseline.  It
does not claim grasp, arm-IK, contact-load, or payload-dynamics validation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO = ROOT / "demo/modern_house_benchmark/scenarios/home_recovery_01.json"
CHECKPOINT_KEYS = (
    "entry_scan_complete",
    "access_complete",
    "furniture_reposition_complete",
    "keepsake_relocation_complete",
    "reviewer_standby_complete",
    "hall_release_complete",
    "home_recovery_complete",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=lambda item: item.tolist() if hasattr(item, "tolist") else str(item),
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scenario_selection(scenario: dict, *, include_dormant_camera: bool = True) -> dict:
    """Build the equivalent V1 selection so Isaac Lab may clone the scene."""
    robots = []
    for robot in scenario["robots"]:
        item = deepcopy(robot)
        # Native aerial ROS camera suites are single-environment.  Preserve
        # the camera declaration for the tiny dormant tiled proof sensor.
        if item["type"] == "iris":
            item["attachments"] = (
                [{"type": "camera", "controller": None}]
                if include_dormant_camera else []
            )
        robots.append(item)
    return {
        "version": 1,
        "task_name": "modern_house_home_recovery_checkpoint_branches",
        "scene_key": scenario["scene_key"],
        "robots": robots,
    }


def branch_targets(center: tuple[float, float], count: int,
                   radius: float = 0.18) -> list[tuple[float, float]]:
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("branch count must be between 1 and 32")
    if not math.isfinite(radius) or not 0 <= radius < 0.5:
        raise ValueError("branch radius must be finite and inside primary_standby_zone")
    if count == 1:
        return [tuple(map(float, center))]
    return [
        (
            float(center[0] + radius * math.cos(2 * math.pi * index / count)),
            float(center[1] + radius * math.sin(2 * math.pi * index / count)),
        )
        for index in range(count)
    ]


def _plan(scenario: dict) -> list[dict]:
    from EAI.scenario_spec.geometry import provider_map
    from demo.benchmark.runtime import build_plan, planning_template

    occupancy = provider_map(scene_key=scenario["scene_key"])
    return build_plan(
        scenario,
        occupancy,
        planning_template(scenario["scene_key"], occupancy),
    )


def _branch_routes(scenario: dict, count: int):
    target = next(
        item for item in scenario["composition"]["targets"]
        if item["id"] == "primary_standby_zone"
    )
    targets = branch_targets(tuple(target["position"][:2]), count)
    assignment = list(range(count))
    if count > 1 and count & (count - 1) == 0:
        width = count.bit_length() - 1
        assignment = [int(f"{index:0{width}b}"[::-1], 2) for index in range(count)]
        targets = [targets[index] for index in assignment]
    routes = []
    for xy in targets:
        candidate = deepcopy(scenario)
        candidate_target = next(
            item for item in candidate["composition"]["targets"]
            if item["id"] == "primary_standby_zone"
        )
        candidate_target["position"][:2] = xy
        entry = next(item for item in _plan(candidate) if item["node"] == "hall_release")
        routes.append([tuple(map(float, point[:2])) for point in entry["waypoints"]])
    return targets, routes, assignment


def _yaw(quaternion):
    import torch

    w, x, y, z = quaternion.unbind(-1)
    return torch.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _steering_rows(position, quaternion, goals, active, *, speed=0.5, k_turn=2.0):
    import torch

    yaw = _yaw(quaternion)
    delta = goals - position[:, :2]
    distance = delta.norm(dim=-1)
    desired = torch.atan2(delta[:, 1], delta[:, 0])
    error = torch.atan2(torch.sin(desired - yaw), torch.cos(desired - yaw))
    command = torch.zeros((len(position), 3), device=position.device)
    command[:, 0] = (
        speed
        * (1.0 - error.abs() / 0.65).clamp(0.0, 1.0)
        * (distance / 1.0).clamp(0.2, 1.0)
    )
    command[:, 2] = (k_turn * error).clamp(-1.2, 1.2)
    command[~active] = 0
    return command, distance


def _velocity_policy_rows(position, quaternion, goals, active, *, speed=0.5,
                          k_turn=2.0, minimum_distance_scale=0.75):
    """Project waypoint velocity into the M20 policy's body-frame axes."""
    import torch

    yaw = _yaw(quaternion)
    delta = goals - position[:, :2]
    distance = delta.norm(dim=-1)
    desired = torch.atan2(delta[:, 1], delta[:, 0])
    error = torch.atan2(torch.sin(desired - yaw), torch.cos(desired - yaw))
    magnitude = speed * (distance / 1.0).clamp(minimum_distance_scale, 1.0)
    command = torch.stack((
        magnitude * torch.cos(error),
        magnitude * torch.sin(error),
        (k_turn * error).clamp(-1.2, 1.2),
    ), dim=-1)
    command[~active] = 0
    return command, distance


@dataclass
class _NullCamera:
    generation: int = 0

    def invalidate(self) -> None:
        self.generation += 1


class ModernHouseCheckpoint:
    """Public state checkpoint for the batched Modern House diagnostic task."""

    def __init__(self, env):
        import torch
        from tools.physics_replay.wrench_state import WrenchState
        from tools.physics_replay.resources import ReplayResource
        from tools.physics_replay.runtime_state import ActuatorStateComponent, StateCodec

        self.env = env
        self.codec = StateCodec()
        self.camera = _NullCamera()
        self.env.replay_external_wrench_sources = {"modern_house": WrenchState(env.scene)}
        self.actuators = ActuatorStateComponent(env)
        self.resources = {}
        self.modules = {}
        for name, values in env._controllers_dict.items():
            for key in ("policy", "preprocessor"):
                value = values.get(key)
                if value is None:
                    continue
                resource_name = f"{name}/{key}"
                if isinstance(value, torch.nn.Module):
                    self.modules[resource_name] = value
                else:
                    self.resources[resource_name] = ReplayResource(resource_name, value)

    def physics(self) -> dict:
        result = {"articulations": {}, "rigid_objects": {}}
        for name, articulation in self.env.scene.articulations.items():
            view = articulation.root_physx_view
            result["articulations"][name] = {
                field: getattr(view, f"get_{field}")().clone()
                for field in (
                    "root_transforms", "root_velocities", "dof_positions", "dof_velocities")
            }
        for name, rigid in self.env.scene.rigid_objects.items():
            view = rigid.root_physx_view
            result["rigid_objects"][name] = {
                field: getattr(view, f"get_{field}")().clone()
                for field in ("transforms", "velocities")
            }
        return result

    def capture(self, task: dict) -> dict:
        import random
        import torch
        from tools.physics_replay.runtime_state import EnvironmentBuffersComponent

        fields = tuple(EnvironmentBuffersComponent.fields) + ("actions_dict", "_commands_dict")
        values = {
            "physics": self.physics(),
            "task": deepcopy(task),
            "buffers": {
                key: getattr(self.env, key) for key in fields if hasattr(self.env, key)
            },
            "controllers": {
                name: {
                    key: value for key, value in controller.items()
                    if key not in ("policy", "preprocessor")
                }
                for name, controller in self.env._controllers_dict.items()
            },
            "modules": {
                name: {"state": dict(module.state_dict()), "training": module.training}
                for name, module in self.modules.items()
            },
            "rng": {
                "python": random.getstate(),
                "numpy": np.random.get_state(),
                "torch": torch.get_rng_state(),
                "cuda": torch.cuda.get_rng_state_all(),
            },
        }
        return {
            "values": self.codec.encode(values),
            "actuators": self.actuators.capture_state(),
            "resources": {
                name: value.capture_state() for name, value in self.resources.items()
            },
        }

    def restore(self, state: dict) -> tuple[dict, float]:
        import random
        import torch

        values = self.codec.decode(state["values"])
        for name, resource in self.resources.items():
            resource.validate_state(state["resources"][name])
        self.actuators.validate_state(state["actuators"])
        self.camera.invalidate()
        ids = torch.arange(self.env.num_envs, device=self.env.device)
        entity_maps = {
            "articulations": self.env.scene.articulations,
            "rigid_objects": self.env.scene.rigid_objects,
        }
        for kind, entities in values["physics"].items():
            for name, fields in entities.items():
                view = entity_maps[kind][name].root_physx_view
                for field, value in fields.items():
                    getattr(view, f"set_{field}")(value, ids)
        for name, value in values["buffers"].items():
            setattr(self.env, name, value)
        for name, commands in values["buffers"].get("_commands_dict", {}).items():
            for key, value in commands.items():
                self.env.set_command(name, key, value)
        for name, data in values["controllers"].items():
            target = self.env._controllers_dict[name]
            handles = {
                key: target[key] for key in ("policy", "preprocessor") if key in target
            }
            target.clear()
            target.update(data | handles)
        for name, module in self.modules.items():
            module.load_state_dict(values["modules"][name]["state"])
            module.train(values["modules"][name]["training"])
        for name, resource in self.resources.items():
            resource.restore_state(state["resources"][name])
        self.actuators.restore_state(state["actuators"])
        random.setstate(values["rng"]["python"])
        np.random.set_state(values["rng"]["numpy"])
        torch.set_rng_state(values["rng"]["torch"])
        torch.cuda.set_rng_state_all(values["rng"]["cuda"])
        self.env.sim.forward()
        for articulation in self.env.scene.articulations.values():
            for key, buffer in vars(articulation.data).items():
                if type(buffer).__name__ == "TimestampedBuffer" and key != "_joint_acc":
                    buffer.timestamp = -1.0
        actual = self.physics()
        errors = []
        for kind, entities in values["physics"].items():
            for name, fields in entities.items():
                for field, expected in fields.items():
                    if expected.numel():
                        errors.append(float(
                            (actual[kind][name][field] - expected).abs().max()))
        return deepcopy(values["task"]), max(errors, default=0.0)


@contextmanager
def _modern_house_visual_clones(*, source_usd: Path, clone_mode: str):
    """Share SceneXR visuals while keeping task rows independently addressable."""
    from isaaclab.scene import InteractiveScene
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    from demo.benchmark.visual_static import (
        retain_static_physics,
        validate_visual_composition,
    )

    original = InteractiveScene.clone_environments
    retained_layers = []

    def clone(scene, copy_from_source=False):
        source_root = scene.stage.GetPrimAtPath(
            "/World/envs/env_0/ModernHouse/SceneXR")
        if not source_root.IsValid():
            raise ValueError("missing ModernHouse/SceneXR visual source")
        descendants = list(Usd.PrimRange(source_root, Usd.TraverseInstanceProxies()))
        if any(prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.IsA(UsdPhysics.Joint)
               for prim in descendants):
            raise ValueError("Modern House SceneXR visual source owns dynamic physics")
        native_colliders = [
            prim for prim in descendants if prim.HasAPI(UsdPhysics.CollisionAPI)
        ]
        source = Usd.Stage.CreateInMemory("modern_house_checkpoint_visual.usda")
        visual = UsdGeom.Xform.Define(source, "/Visual").GetPrim()
        visual.GetReferences().AddReference(str(source_usd))
        source.SetDefaultPrim(visual)
        if UsdGeom.Xformable(visual).ComputeLocalToWorldTransform(0) != Gf.Matrix4d(1):
            raise ValueError("Modern House visual wrapper root transform must be identity")
        source.OverridePrim("/Visual/BenchCollision").SetActive(False)
        for prim in native_colliders:
            path = str(prim.GetPath()).replace(
                "/World/envs/env_0/ModernHouse", "/Visual", 1)
            target = source.OverridePrim(path)
            target.RemoveAPI(UsdPhysics.CollisionAPI)
            target.RemoveAPI(UsdPhysics.MeshCollisionAPI)
        retained_layers.append(source.GetRootLayer())
        instance = scene.stage.DefinePrim(
            "/World/envs/env_0/ModernHouse/RenderInstance", "Xform")
        instance.GetReferences().AddReference(source.GetRootLayer().identifier)
        instance.SetInstanceable(True)
        scene.modern_house_visual_verified_prims = validate_visual_composition(
            source_root,
            scene.stage.GetPrimAtPath(str(instance.GetPath()) + "/SceneXR"),
        )
        if native_colliders:
            scene.modern_house_visual_pruning = retain_static_physics(
                source_root, native_colliders)
        else:
            source_root.SetActive(False)
            scene.modern_house_visual_pruning = None
        return original(scene, copy_from_source=clone_mode == "copy")

    InteractiveScene.clone_environments = clone
    try:
        yield
    finally:
        if vars(InteractiveScene).get("clone_environments") is clone:
            InteractiveScene.clone_environments = original


def _configure_env(cfg, *, scenario: dict, slots: int, static_audit: Path | None,
                   clone_mode: str, clone_stack,
                   include_dormant_camera: bool = True,
                   compact_physx_capacity: bool = False) -> None:
    import isaaclab.sim as sim_utils
    from isaaclab.scene import InteractiveScene
    from EAI_hmrs.scenario_builder import add_scenario_entities

    cfg.sim.physx.enable_enhanced_determinism = True
    if compact_physx_capacity:
        # Bounded diagnostic task: reduce default multi-million-pair GPU
        # reservations.  Overflow warnings/errors still fail the run.
        cfg.sim.physx.gpu_max_rigid_contact_count = 2 ** 20
        cfg.sim.physx.gpu_max_rigid_patch_count = 2 ** 14
        cfg.sim.physx.gpu_found_lost_pairs_capacity = 2 ** 20
        cfg.sim.physx.gpu_found_lost_aggregate_pairs_capacity = 2 ** 25
        cfg.sim.physx.gpu_total_aggregate_pairs_capacity = 2 ** 21
        cfg.sim.physx.gpu_collision_stack_size = 2 ** 24
        cfg.sim.physx.gpu_heap_capacity = 2 ** 24
        cfg.sim.physx.gpu_temp_buffer_capacity = 2 ** 22
    cfg.scene.world.prim_path = "{ENV_REGEX_NS}/ModernHouse"
    cfg.scene.num_envs = slots
    cfg.scene.env_spacing = 0.0
    cfg.scene.replicate_physics = True
    cfg.scene.clone_in_fabric = False
    cfg.scene.filter_collisions = True
    cfg.episode_length_s = 10000.0
    entity_attrs = {"__annotations__": {}}
    add_scenario_entities(entity_attrs, scenario["composition"])
    for key in entity_attrs["__annotations__"]:
        if hasattr(cfg.scene, key):
            raise ValueError(f"scenario entity collides with scene cfg key: {key}")
        entity_cfg = entity_attrs[key]
        spawn = entity_cfg.spawn
        if getattr(spawn, "native_physics", None) is None:
            raise ValueError(f"Modern House branch task requires native prop physics: {key}")
        collision = sim_utils.CollisionPropertiesCfg(collision_enabled=False)
        rigid = sim_utils.RigidBodyPropertiesCfg(
            rigid_body_enabled=True,
            kinematic_enabled=False,
            disable_gravity=True,
        )
        spawn.collision_props = collision
        spawn.rigid_props = rigid
        spawn.native_physics = {
            **spawn.native_physics,
            "collision_props": collision,
            "rigid_props": rigid,
        }
        setattr(cfg.scene, key, entity_cfg)
    if include_dormant_camera:
        from isaaclab.sensors import TiledCameraCfg

        mount = cfg.scene.camera_iris_1
        mount.spawn.clipping_range = (0.05, 40.0)
        cfg.scene.aerial_rgb_iris_1 = TiledCameraCfg(
            prim_path=mount.prim_path,
            spawn=None,
            width=64,
            height=64,
            data_types=["rgb"],
            update_period=10000.0,
            update_latest_camera_pose=False,
        )
    if static_audit is None or include_dormant_camera:
        clone_stack.enter_context(_modern_house_visual_clones(
            source_usd=ROOT / "usd/scene/modern_house_nvd/modern_house_nvd_bench.usd",
            clone_mode=clone_mode,
        ))
    if static_audit is None:
        return

    from EAI.hmrs_env.multi_robot_direct_env import MultiRobotDirectEnv
    from demo.fire_rescue.branching.parallel.shared_factory import (
        partition_factory_physics_scenes,
    )
    from demo.modern_house_benchmark.shared_static import (
        finalize_gpu_shared_static,
        prepare_gpu_shared_static_source,
    )

    source_clone = InteractiveScene.clone_environments

    def clone_with_shared_static(scene, copy_from_source=False):
        try:
            prepare_gpu_shared_static_source(scene, static_audit)
            return source_clone(scene, copy_from_source=clone_mode == "copy")
        finally:
            if vars(InteractiveScene).get("clone_environments") is clone_with_shared_static:
                InteractiveScene.clone_environments = source_clone

    InteractiveScene.clone_environments = clone_with_shared_static
    original_setup = MultiRobotDirectEnv._setup_scene
    owned_setup = vars(MultiRobotDirectEnv).get("_setup_scene")

    def setup(environment):
        try:
            original_setup(environment)
            partition_factory_physics_scenes(environment.scene, shared_solver=True)
            finalize_gpu_shared_static(environment.scene, static_audit)
        finally:
            if owned_setup is None:
                del MultiRobotDirectEnv._setup_scene
            else:
                MultiRobotDirectEnv._setup_scene = owned_setup

    MultiRobotDirectEnv._setup_scene = setup


def _run_worker(args) -> int:
    from contextlib import ExitStack
    from functools import partial
    import torch

    from simulator import SimulatorLaunchConfig, open_simulator_session
    from tools.physics_replay.checkpoints import CheckpointStore
    from tools.physics_replay.components import ComponentSnapshot
    from demo.benchmark.branch_data import digest_state
    from demo.benchmark.route_tracking import advance_visible_waypoints
    from demo.benchmark.runtime import route_waypoints
    from demo.benchmark.branch_data import open_batch, source_revision, validate_run
    from demo.modern_house_benchmark.branch_data_adapter import ModernHouseBranchDataAdapter

    scenario_path = args.scenario.resolve()
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    if scenario.get("benchmark_task_id") != "MH-DAG-01":
        raise ValueError("this runner accepts only Modern House MH-DAG-01")
    include_dormant_camera = not args.disable_dormant_camera
    selection = _scenario_selection(
        scenario,
        include_dormant_camera=include_dormant_camera,
    )
    plan = _plan(scenario)
    entries = {entry["node"]: entry for entry in plan}
    expected_task_nodes = {
        node["id"] for node in scenario["phase_graph"]["nodes"]
    }
    targets, branch_routes, target_assignment = _branch_routes(scenario, args.slots)
    branch_data_adapter = ModernHouseBranchDataAdapter(
        scenario=scenario,
        scenario_path=scenario_path,
        targets=targets,
        routes=branch_routes,
    )
    from EAI.scenario_spec.geometry import provider_map, static_blocked_grid
    from EAI.scenario_spec.oracle import InflatedGrid

    occupancy = provider_map(scene_key=scenario["scene_key"])
    branch_entry = entries["hall_release"]
    branch_tracking_grid = InflatedGrid(
        occupancy,
        branch_entry["route_planning_radius_m"],
        static_blocked_grid(
            occupancy,
            scenario,
            ground_tracking_reserve_m=branch_entry["ground_tracking_reserve_m"],
        ),
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    recording_static_audit = bool(args.record_static_audit)
    static_audit = (
        output / "modern_house_static_audit.json"
        if recording_static_audit else args.static_audit.resolve()
    )
    static_names = ()
    static_digest = None
    if not recording_static_audit:
        if not static_audit.is_file():
            raise FileNotFoundError(f"Modern House static audit does not exist: {static_audit}")
        from demo.modern_house_benchmark.shared_static import audited_group_names

        static_names, static_digest = audited_group_names(static_audit)

    imported_state = None
    imported_report = None
    imported_verified_steps = ()
    if args.import_checkpoint is not None:
        if recording_static_audit:
            raise ValueError("static audit recording cannot import a checkpoint")
        source_dir = args.import_checkpoint.resolve()
        imported_report = json.loads((source_dir / "report.json").read_text(encoding="utf-8"))
        if (not imported_report.get("passed")
                or imported_report.get("requested") != 1
                or imported_report.get("scenario_sha256") != _sha256(scenario_path)
                or imported_report.get("dormant_camera_enabled") != include_dormant_camera
                or imported_report.get("checkpoint_steps", {}).get("reviewer_standby_complete") is None):
            raise ValueError("imported checkpoint lacks a passing matching one-slot task")
        source_store = CheckpointStore(source_dir)
        imported_verified_steps = source_store.verify_all()
        if len(imported_verified_steps) != len(CHECKPOINT_KEYS):
            raise ValueError("source checkpoint store does not verify all task milestones")
        step = imported_report["checkpoint_steps"]["reviewer_standby_complete"]
        imported_state = source_store.read(step)["modern_house"].state
        if (imported_report.get("restore", {}).get("passed") is not True
                or imported_report.get("checkpoint_store_verified") is not True):
            raise ValueError("source task did not pass checkpoint restore gates")

    store = CheckpointStore(output)
    started = time.monotonic()
    report = {
        "passed": False,
        "stage": "starting",
        "scenario": str(scenario_path),
        "scenario_sha256": _sha256(scenario_path),
        "benchmark_task_id": scenario["benchmark_task_id"],
        "mode": "record_static_audit" if recording_static_audit else "shared_static_branches",
        "clone_mode": "copy" if recording_static_audit else args.clone_mode,
        "shared_static_audit": None if recording_static_audit else str(static_audit),
        "shared_static_audit_sha256": static_digest,
        "shared_static_groups": len(static_names),
        "requested": args.slots,
        "resident": 0,
        "peak_active": 0,
        "completed": 0,
        "active": 0,
        "queued": 0,
        "independently_verified": 0,
        "checkpoint_keys": list(CHECKPOINT_KEYS),
        "checkpoint_steps": {},
        "checkpoint_scope": (
            "public articulation/rigid-object/controller/actuator/runtime/RNG state; "
            "private PhysX solver/contact/warm-start caches excluded"
        ),
        "transport_scope": (
            "benchmark-declared kinematic transport baseline; no grasp, arm IK, "
            "contact-load, or payload-dynamics claim"
        ),
        "branch_scope": (
            f"{args.slots} simultaneous physical slots restored from reviewer_standby_complete; "
            "distinct M20 hall_release targets remain inside primary_standby_zone"
        ),
        "sensor_scope": (
            "tiny dormant tiled Iris camera; motion/checkpoint branching is tested without "
            "a 32-way image-fidelity claim"
            if include_dormant_camera else
            "Iris remains a physical branch row; dormant RGB/RTX is disabled because both "
            "declared scans record timed-hover boundaries with frame_count=0"
        ),
        "dormant_camera_enabled": include_dormant_camera,
        "compact_physx_capacity": args.compact_physx_capacity,
        "branch_targets": targets,
        "branch_target_assignment": target_assignment,
        "visible_waypoint_lookahead_m": args.visible_waypoint_lookahead,
        "visible_waypoint_advances": [0] * args.slots,
        "prefix_visible_waypoint_lookahead_m": args.prefix_visible_waypoint_lookahead,
        "prefix_visible_waypoint_advances": {},
        "branch_completion_zone_tolerance_m": args.branch_completion_zone_tolerance,
        "completed_row_hold": (
            "after a branch reaches its terminal zone, preserve that measured M20 root "
            "transform and clear its root velocity after every remaining shared barrier"
        ),
        "completed_task_nodes_before_branch": [],
    }
    if imported_state is not None:
        report["import_checkpoint"] = {
            "source": str(source_dir),
            "source_report_sha256": _sha256(source_dir / "report.json"),
            "source_verified_steps": list(imported_verified_steps),
            "source_prefix_steps": imported_report["checkpoint_steps"]["reviewer_standby_complete"],
            "source_slot_count": 1,
        }
    save = lambda: _write_json(output / "report.json", report)
    save()
    branch_batch = open_batch(
        output / "branch_data",
        scene_key=branch_data_adapter.scene_key,
        task_id=branch_data_adapter.task_id,
        requested=args.slots,
        execution_scope="measured",
        task_version=branch_data_adapter.task_version,
        source_revision_value=source_revision(ROOT),
        formal=args.slots == 32,
        metadata={
            "scenario": str(scenario_path),
            "scenario_sha256": report["scenario_sha256"],
            "runner": "demo.modern_house_benchmark.checkpoint_branches",
            "transport_scope": report["transport_scope"],
            "sensor_scope": report["sensor_scope"],
        },
        autosave_every=max(128, args.slots * 8),
    )
    branch_id_by_slot: dict[int, str] = {}
    branch_runtime = {"route_indices": None, "completed": set(), "phase": "prefix"}
    report["execution_scope"] = "measured"
    report["branch_data_output"] = str(output / "branch_data")
    save()

    clone_stack = ExitStack()
    config = SimulatorLaunchConfig(
        env=selection["task_name"],
        resolved_env_name=selection["task_name"],
        selection_data=selection,
        scenario_file=str(scenario_path),
        num_envs=args.slots,
        device="cuda:0",
        seed=int(scenario.get("seed", 17)),
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        aerial_sensor_backend=(
            "tiled_rgb" if include_dormant_camera else "disabled"
        ),
        env_cfg_hook=partial(
            _configure_env,
            scenario=scenario,
            slots=args.slots,
            static_audit=None if recording_static_audit else static_audit,
            clone_mode="copy" if recording_static_audit else args.clone_mode,
            clone_stack=clone_stack,
            include_dormant_camera=include_dormant_camera,
            compact_physx_capacity=args.compact_physx_capacity,
        ),
        app_launcher_args={
            "headless": True,
            "enable_cameras": include_dormant_camera,
            "device": "cuda:0",
            "kit_args": "--/app/settings/persistent=false",
        },
    )

    try:
        with open_simulator_session(config) as session:
            from isaacsim.core.simulation_manager import SimulationManager
            from pxr import PhysxSchema

            env = session.base_env
            origins = env.scene.env_origins
            report["actual_device"] = str(env.sim.device)
            report["simulation_manager_device"] = SimulationManager.get_physics_sim_device()
            scene_api = PhysxSchema.PhysxSceneAPI(
                env.scene.stage.GetPrimAtPath(env.cfg.sim.physics_prim_path))
            report["gpu_dynamics"] = bool(scene_api.GetEnableGPUDynamicsAttr().Get())
            if (report["actual_device"] != "cuda:0"
                    or report["simulation_manager_device"] != "cuda:0"):
                raise RuntimeError("Modern House branch test requires actual CUDA PhysX")
            articulation_counts = {
                name: int(robot.root_physx_view.count)
                for name, robot in env.scene.articulations.items()
            }
            rigid_counts = {
                name: int(body.root_physx_view.count)
                for name, body in env.scene.rigid_objects.items()
            }
            report["articulation_counts"] = articulation_counts
            report["rigid_object_counts"] = rigid_counts
            if set(articulation_counts.values()) != {args.slots}:
                raise RuntimeError(f"missing articulation rows: {articulation_counts}")
            if set(rigid_counts.values()) != {args.slots}:
                raise RuntimeError(f"missing rigid-object rows: {rigid_counts}")
            required_rigid = {
                "scenario_armchair_proxy", "scenario_keepsake_box_proxy"}
            if not required_rigid <= set(rigid_counts):
                raise RuntimeError(f"scenario props missing: {sorted(required_rigid - set(rigid_counts))}")
            report["resident"] = args.slots
            report["active"] = args.slots
            report["origins"] = origins.cpu().tolist()
            report["control_dt_s"] = float(env.step_dt)
            report["physics_dt_s"] = float(env.physics_dt)

            common_static = getattr(env.scene, "replay_static_modern_house", None)
            static_recorder = None
            if recording_static_audit:
                from demo.modern_house_benchmark.shared_static import ModernHouseStaticAudit

                static_recorder = ModernHouseStaticAudit(
                    env.scene.stage, args.slots, static_audit)
                report["recorded_static_audit"] = str(static_audit)
            else:
                if common_static is None:
                    raise RuntimeError("shared Modern House static geometry was not registered")
                from demo.modern_house_benchmark.shared_static import validate_slot_topology

                report["slot_topology"] = validate_slot_topology(
                    env.scene.stage,
                    args.slots,
                    common_static=common_static,
                    allow_slot_inheritance=args.clone_mode == "inherit",
                )
            report["stage"] = "prefix"
            save()

            checkpoint = ModernHouseCheckpoint(env)
            control_step = 0
            checkpoints = {}
            actions = {
                name: torch.zeros((args.slots, 3), device=session.device)
                for name in session.possible_agents
            }
            expected_agents = {"iris_1", "m20_1", "carter_1"}
            if expected_agents - set(actions):
                raise RuntimeError(f"unexpected Modern House agent set: {session.possible_agents}")
            completed_nodes = []
            prefix_visible_waypoint_advances = {}
            held_root_rows = {}

            def restore_held_root_rows() -> None:
                for name, rows in held_root_rows.items():
                    if not rows:
                        continue
                    robot = env.scene.articulations[name]
                    transforms = robot.root_physx_view.get_root_transforms().clone()
                    velocities = robot.root_physx_view.get_root_velocities().clone()
                    for slot, transform in rows.items():
                        transforms[slot] = transform
                        velocities[slot] = 0
                    robot.root_physx_view.set_root_transforms(transforms, ids)
                    robot.root_physx_view.set_root_velocities(velocities, ids)
                if held_root_rows:
                    env.sim.forward()

            def advance() -> None:
                nonlocal control_step
                before = int(env._sim_step_counter)
                env.step(actions)
                if int(env._sim_step_counter) - before != env.cfg.decimation:
                    raise RuntimeError("shared Modern House barrier advanced unexpected substeps")
                control_step += 1
                restore_held_root_rows()
                if branch_id_by_slot and branch_runtime["route_indices"] is not None:
                    route_indices_local = branch_runtime["route_indices"]
                    completed_local = branch_runtime["completed"]
                    post_states = branch_data_adapter.read_all_branch_states({
                        "env": env,
                        "origins": origins,
                    })
                    command_rows = branch_data_adapter.command_rows(actions)
                    positions = (
                        env.scene.articulations["m20_1"].data.root_pos_w - origins
                    ).detach().cpu().tolist()
                    records = []
                    for slot, branch_id in branch_id_by_slot.items():
                        records.append((branch_id, {
                            "simulator_step": control_step,
                            "sim_time_s": control_step * float(env.step_dt),
                            "commands": command_rows[slot],
                            "post_step_state": post_states[slot],
                            "phase_state": {
                                "phase": branch_runtime["phase"],
                                "route_index": int(route_indices_local[slot]),
                                "completed_nodes": list(completed_nodes),
                            },
                            "task_state": {
                                "completed_slots": len(completed_local),
                                "requested_slots": args.slots,
                                "completed_nodes": list(completed_nodes),
                            },
                            "active": slot not in completed_local,
                            "completed": slot in completed_local,
                            "scene_payload": branch_data_adapter.scene_payload(
                                slot,
                                position_xy=positions[slot][:2],
                                route_index=int(route_indices_local[slot]),
                                completed=slot in completed_local,
                            ),
                        }))
                    branch_batch.record_step_batch(records)

            def save_checkpoint(name: str, logical: dict) -> dict:
                state = checkpoint.capture({
                    "checkpoint": name,
                    "control_step": control_step,
                    "completed_nodes": list(completed_nodes),
                    **logical,
                })
                snapshots = {"modern_house": ComponentSnapshot("modern_house", 1, state)}
                store.write(control_step, snapshots)
                reread = store.read(control_step)["modern_house"].state
                physics = checkpoint.codec.decode(reread["values"])["physics"]
                digest = digest_state(physics)
                report["checkpoint_steps"][name] = control_step
                checkpoints[name] = {"state": reread, "digest": digest}
                report.setdefault("checkpoints", {})[name] = {
                    "control_step": control_step,
                    "physics_digest": digest,
                    "json": f"checkpoints/{control_step:08d}.json",
                    "npz": f"checkpoints/{control_step:08d}.npz",
                    "completed_nodes": list(completed_nodes),
                }
                save()
                return reread

            def articulation_local(name):
                robot = env.scene.articulations[name]
                return robot.data.root_pos_w - origins

            def follow_routes(route_by_agent, *, max_steps, carry=None):
                indices = {
                    name: torch.zeros(args.slots, dtype=torch.long, device=session.device)
                    for name in route_by_agent
                }
                visible_advances = {
                    name: np.zeros(args.slots, dtype=np.int64)
                    for name in route_by_agent
                }
                tensors = {
                    name: torch.tensor(route, dtype=torch.float32, device=session.device)
                    for name, route in route_by_agent.items()
                }
                for _ in range(max_steps):
                    all_done = True
                    for name, route in route_by_agent.items():
                        positions = articulation_local(name)
                        if name == "m20_1":
                            local_xy = positions[:, :2].detach().cpu().numpy()
                            for slot in range(args.slots):
                                index = int(indices[name][slot].item())
                                if index >= len(route):
                                    continue
                                advanced = advance_visible_waypoints(
                                    route[index:],
                                    position=tuple(map(float, local_xy[slot])),
                                    grid=branch_tracking_grid,
                                    lookahead_m=args.prefix_visible_waypoint_lookahead,
                                )
                                if advanced:
                                    indices[name][slot] += advanced
                                    visible_advances[name][slot] += advanced
                        active = indices[name] < len(route)
                        if bool(active.any()):
                            all_done = False
                        selected = tensors[name][torch.clamp(indices[name], max=len(route) - 1)]
                        command_fn = (
                            _velocity_policy_rows if name == "m20_1" else _steering_rows
                        )
                        command, _ = command_fn(
                            positions,
                            env.scene.articulations[name].data.root_quat_w,
                            selected,
                            active,
                            speed=args.ground_speed,
                        )
                        actions[name] = command
                    if all_done:
                        break
                    advance()
                    if carry is not None:
                        carry()
                    for name, route in route_by_agent.items():
                        active = indices[name] < len(route)
                        selected = tensors[name][torch.clamp(indices[name], max=len(route) - 1)]
                        distance = (articulation_local(name)[:, :2] - selected).norm(dim=1)
                        terminal = indices[name] == len(route) - 1
                        tolerance = torch.where(
                            terminal,
                            distance.new_tensor(args.terminal_tolerance),
                            distance.new_tensor(args.waypoint_tolerance),
                        )
                        indices[name][active & (distance <= tolerance)] += 1
                failed = {
                    name: torch.nonzero(index < len(route)).flatten().cpu().tolist()
                    for (name, route), index in zip(route_by_agent.items(), indices.values())
                    if bool((index < len(route)).any())
                }
                for name in route_by_agent:
                    actions[name].zero_()
                    total = prefix_visible_waypoint_advances.setdefault(
                        name, np.zeros(args.slots, dtype=np.int64))
                    total += visible_advances[name]
                    report["prefix_visible_waypoint_advances"][name] = total.tolist()
                if failed:
                    diagnostics = {
                        name: {
                            "failed_slots": slots,
                            "final_xy": articulation_local(name)[slots, :2].detach().cpu().tolist(),
                            "next_waypoint_xy": [
                                route_by_agent[name][int(indices[name][slot].item())]
                                for slot in slots
                            ],
                        }
                        for name, slots in failed.items()
                    }
                    raise RuntimeError(f"ground routes exceeded budget: {diagnostics}")
                return {name: value.cpu().tolist() for name, value in indices.items()}

            ids = torch.arange(args.slots, device=session.device)
            joint = entries["coordinated_furniture_reposition"]
            armchair_name = "scenario_armchair_proxy"
            keepsake_name = "scenario_keepsake_box_proxy"
            keepsake = entries["keepsake_relocation"]

            def clear_root_velocity(name, rows=ids):
                robot = env.scene.articulations[name]
                velocities = robot.root_physx_view.get_root_velocities().clone()
                velocities[rows] = 0
                robot.root_physx_view.set_root_velocities(velocities, ids)

            def move_aerial(goal, *, steps, dwell_steps):
                iris = env.scene.articulations["iris_1"]
                initial = iris.root_physx_view.get_root_transforms().clone()
                goal_world = origins + torch.tensor(goal, device=session.device)
                actions["iris_1"] = goal_world.clone()
                for index in range(steps):
                    advance()
                    alpha = float(index + 1) / steps
                    transforms = iris.root_physx_view.get_root_transforms().clone()
                    transforms[:, :3] = initial[:, :3] * (1 - alpha) + goal_world * alpha
                    iris.root_physx_view.set_root_transforms(transforms, ids)
                    iris.root_physx_view.set_root_velocities(torch.zeros_like(
                        iris.root_physx_view.get_root_velocities()), ids)
                    env.sim.forward()
                for _ in range(dwell_steps):
                    advance()

            def set_object_position(name, xyz):
                body = env.scene.rigid_objects[name]
                transforms = body.root_physx_view.get_transforms().clone()
                transforms[:, :3] = xyz
                body.root_physx_view.set_transforms(transforms, ids)
                body.root_physx_view.set_velocities(torch.zeros_like(
                    body.root_physx_view.get_velocities()), ids)
                env.sim.forward()

            if imported_state is not None:
                from demo.modern_house_benchmark.checkpoint_import import expand_checkpoint

                live = checkpoint.capture({"checkpoint": "fresh_batch"})
                expanded = expand_checkpoint(imported_state, live, args.slots)
                imported_task, import_error = checkpoint.restore(expanded)
                if import_error > args.restore_tolerance:
                    raise RuntimeError(f"imported checkpoint readback error: {import_error}")
                if imported_task["checkpoint"] != "reviewer_standby_complete":
                    raise ValueError("imported checkpoint task node differs from expected branch point")
                completed_nodes.extend(imported_task["completed_nodes"])
                control_step = int(imported_task["control_step"])
                report["completed_task_nodes_before_branch"] = list(completed_nodes)
                report["import_checkpoint"].update(
                    readback_max_abs_error=import_error,
                    source_completed_nodes=list(completed_nodes),
                )
                for name in CHECKPOINT_KEYS[:4]:
                    report["checkpoint_steps"][name] = imported_report["checkpoint_steps"][name]
                    report.setdefault("checkpoints", {})[name] = {
                        **imported_report["checkpoints"][name],
                        "source": str(source_dir),
                    }
                branch_state = save_checkpoint("reviewer_standby_complete", {})
                # The source, fresh-batch and expanded snapshots are CPU
                # staging data.  Releasing the decoded scratch tensors before
                # the first PhysX step leaves room for its lazy GPU buffers.
                import gc

                del live, expanded
                gc.collect()
                torch.cuda.empty_cache()
                report["cuda_before_restore_probe"] = {
                    "allocated_bytes": torch.cuda.memory_allocated(),
                    "reserved_bytes": torch.cuda.memory_reserved(),
                }
                save()
            else:
                # 1. Room-entry scan.
                move_aerial(entries["room_entry_scan"]["scan_goal"],
                            steps=args.aerial_steps, dwell_steps=args.scan_dwell_steps)
                completed_nodes.append("room_entry_scan")
                save_checkpoint("entry_scan_complete", {
                    "observation_evidence": {
                        "kind": "timed_hover_boundary", "frame_count": 0,
                        "duration_s": args.scan_dwell_steps * float(env.step_dt),
                    },
                })

                # 2. Both furniture access legs.
                follow_routes({
                    "m20_1": entries["furniture_side_access"]["waypoints"],
                    "carter_1": entries["doorway_access_check"]["waypoints"],
                }, max_steps=args.route_max_steps)
                completed_nodes.extend(["furniture_side_access", "doorway_access_check"])
                save_checkpoint("access_complete", {})

                # 3. Joint furniture reposition.  The two robots navigate with
                # their configured controllers; the armchair follows their measured
                # midpoint at the benchmark-declared carry height.
                joint = entries["coordinated_furniture_reposition"]
                follow_routes(joint["team_to_object"], max_steps=args.route_max_steps)
                armchair_name = "scenario_armchair_proxy"

                def carry_armchair():
                    positions = torch.stack((
                        articulation_local("m20_1")[:, :3],
                        articulation_local("carter_1")[:, :3],
                    )).mean(dim=0)
                    world = positions + origins
                    world[:, 2] = joint["carry_z"] + origins[:, 2]
                    set_object_position(armchair_name, world)

                follow_routes(
                    joint["team_to_target"],
                    max_steps=args.route_max_steps,
                    carry=carry_armchair,
                )
                armchair_goal = torch.tensor(
                    [*joint["goal"][:2], joint["release_z"]],
                    device=session.device,
                )
                set_object_position(armchair_name, origins + armchair_goal)
                advance()
                completed_nodes.append("coordinated_furniture_reposition")
                save_checkpoint("furniture_reposition_complete", {})

                # 4. Verify furniture clearance, then move the keepsake.
                clearance = entries["furniture_clearance_scan"]
                move_aerial(clearance["scan_goal"], steps=args.aerial_steps,
                            dwell_steps=args.scan_dwell_steps)
                completed_nodes.append("furniture_clearance_scan")
                keepsake_name = "scenario_keepsake_box_proxy"
                keepsake = entries["keepsake_relocation"]
                follow_routes({"m20_1": keepsake["to_object"]},
                              max_steps=args.route_max_steps)

                def carry_keepsake():
                    world = env.scene.articulations["m20_1"].data.root_pos_w.clone()
                    world[:, 2] = keepsake["carry_z"] + origins[:, 2]
                    set_object_position(keepsake_name, world)

                follow_routes({"m20_1": keepsake["to_target"]},
                              max_steps=args.route_max_steps, carry=carry_keepsake)
                keepsake_goal = torch.tensor(
                    [*keepsake["goal"][:2], keepsake["release_z"]],
                    device=session.device,
                )
                set_object_position(keepsake_name, origins + keepsake_goal)
                advance()
                completed_nodes.append("keepsake_relocation")
                save_checkpoint("keepsake_relocation_complete", {})

                # 5. Carter verifies the room entrance and leaves the living path.
                follow_routes({"carter_1": entries["room_entry_verification"]["waypoints"]},
                              max_steps=args.route_max_steps)
                completed_nodes.append("room_entry_verification")
                follow_routes({"carter_1": entries["reviewer_standby"]["waypoints"]},
                              max_steps=args.route_max_steps)
                completed_nodes.append("reviewer_standby")
                report["completed_task_nodes_before_branch"] = list(completed_nodes)
                branch_state = save_checkpoint("reviewer_standby_complete", {})

            # Mutate public state and prove persisted restoration before fanout.
            for _ in range(args.restore_probe_steps):
                actions["carter_1"][:, 0] = 0.25
                advance()
            actions["carter_1"].zero_()
            restored_task, restore_error = checkpoint.restore(branch_state)
            report["restore"] = {
                "source": "reviewer_standby_complete",
                "restored_task": restored_task,
                "max_abs_error": restore_error,
                "threshold": args.restore_tolerance,
                "passed": bool(restore_error <= args.restore_tolerance),
            }
            if restore_error > args.restore_tolerance:
                raise RuntimeError(
                    f"Modern House checkpoint restore error exceeds tolerance: {restore_error}")
            control_step = int(restored_task["control_step"])

            branch_checkpoint_digest = checkpoints["reviewer_standby_complete"]["digest"]
            for slot in range(args.slots):
                identity = branch_data_adapter.identity(
                    slot,
                    checkpoint_digest=branch_checkpoint_digest,
                    source_revision=branch_batch.manifest["source_revision"],
                )
                branch_batch.register_branch(identity)
                branch_id_by_slot[slot] = identity.branch_id
                branch_batch.record_checkpoint(
                    identity.branch_id,
                    checkpoint_id="reviewer_standby_complete",
                    simulator_step=control_step,
                    snapshot_digest=branch_checkpoint_digest,
                    snapshot_schema=branch_data_adapter.snapshot_schema,
                    restore_error=restore_error,
                    restore_passed=bool(restore_error <= args.restore_tolerance),
                    restore_status="measured",
                    restore_error_source="max_abs_public_modern_house_physics_tensor_error",
                    payload={
                        "checkpoint_store_step": report["checkpoint_steps"][
                            "reviewer_standby_complete"],
                        "continued_after_restore": True,
                    },
                )
                branch_batch.record_event(
                    identity.branch_id,
                    event_type="checkpoint_restored",
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    payload={
                        "checkpoint_id": "reviewer_standby_complete",
                        "restore_error": restore_error,
                    },
                )
                branch_batch.record_progress(
                    identity.branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    progress={
                        "phase": "hall_release",
                        "completed_nodes": list(completed_nodes),
                        "completed_slots": 0,
                        "requested_slots": args.slots,
                    },
                )

            # 6. Release 32 simultaneous physical futures from the checkpoint.
            report["stage"] = "branching"
            report["peak_active"] = args.slots
            save()
            m20 = env.scene.articulations["m20_1"]
            route_indices = np.zeros(args.slots, dtype=np.int64)
            completed = set()
            branch_runtime["route_indices"] = route_indices
            branch_runtime["completed"] = completed
            branch_runtime["phase"] = "hall_release"
            first_complete_step = {}
            active_history = []
            visible_waypoint_advances = np.zeros(args.slots, dtype=np.int64)
            branch_replans = []
            last_progress = np.zeros(args.slots, dtype=np.int64)
            last_positions = (
                m20.data.root_pos_w - origins)[:, :2].detach().cpu().numpy().copy()
            last_indices = route_indices.copy()
            replan_counts = np.zeros(args.slots, dtype=np.int64)
            recovery_remaining = np.zeros(args.slots, dtype=np.int64)
            branch_started = time.monotonic()
            for branch_step in range(args.branch_max_steps):
                # A completed row has no further route command, but its
                # controller may still leave a small residual root velocity.
                # Clear that velocity before the next shared physics barrier
                # so a row cannot drift back outside the terminal zone while
                # the other branches finish.
                if completed:
                    completed_ids = torch.tensor(
                        sorted(completed), dtype=torch.long, device=session.device)
                    root_velocities = m20.root_physx_view.get_root_velocities().clone()
                    root_velocities[completed_ids] = 0
                    m20.root_physx_view.set_root_velocities(root_velocities, ids)
                local_pos = m20.data.root_pos_w - origins
                local_xy = local_pos[:, :2].detach().cpu().numpy()
                for slot in range(args.slots):
                    if slot in completed:
                        continue
                    route = branch_routes[slot]
                    index = int(route_indices[slot])
                    remaining = route[index:]
                    advanced = advance_visible_waypoints(
                        remaining,
                        position=tuple(map(float, local_xy[slot])),
                        grid=branch_tracking_grid,
                        lookahead_m=args.visible_waypoint_lookahead,
                    )
                    if advanced:
                        route_indices[slot] += advanced
                        visible_waypoint_advances[slot] += advanced
                active = torch.tensor(
                    [index not in completed for index in range(args.slots)],
                    dtype=torch.bool,
                    device=session.device,
                )
                goals = torch.zeros((args.slots, 2), device=session.device)
                for slot in range(args.slots):
                    route = branch_routes[slot]
                    goals[slot] = goals.new_tensor(
                        route[min(int(route_indices[slot]), len(route) - 1)])
                actions["m20_1"], _ = _velocity_policy_rows(
                    local_pos,
                    m20.data.root_quat_w,
                    goals,
                    active,
                    speed=args.ground_speed,
                )
                for slot in range(args.slots):
                    if recovery_remaining[slot] <= 0 or slot in completed:
                        continue
                    turn = args.recovery_turn_rate * (-1 if replan_counts[slot] % 2 else 1)
                    actions["m20_1"][slot] = actions["m20_1"].new_tensor(
                        [-args.recovery_speed, 0.0, turn])
                    recovery_remaining[slot] -= 1
                advance()
                local_pos = m20.data.root_pos_w - origins
                standby_center = np.asarray(next(
                    item for item in scenario["composition"]["targets"]
                    if item["id"] == "primary_standby_zone")["position"][:2])
                for slot in range(args.slots):
                    if slot in completed:
                        continue
                    route = branch_routes[slot]
                    index = int(route_indices[slot])
                    waypoint_index = min(index, len(route) - 1)
                    waypoint = np.asarray(route[waypoint_index])
                    distance = float(np.linalg.norm(
                        local_pos[slot, :2].detach().cpu().numpy() - waypoint))
                    tolerance = (
                        args.terminal_tolerance if index >= len(route) - 1
                        else args.waypoint_tolerance)
                    if index < len(route) and distance <= tolerance:
                        route_indices[slot] += 1
                    zone_error = float(np.linalg.norm(
                        local_pos[slot, :2].detach().cpu().numpy() - standby_center))
                    if (route_indices[slot] >= len(route)
                            and zone_error <= args.branch_completion_zone_tolerance):
                        completed.add(slot)
                        held_root_rows.setdefault("m20_1", {})[slot] = (
                            m20.root_physx_view.get_root_transforms()[slot].clone()
                        )
                        first_complete_step[str(slot)] = branch_step + 1
                if (branch_step + 1) % args.stall_check_steps == 0:
                    sample_xy = local_pos[:, :2].detach().cpu().numpy()
                    for slot in range(args.slots):
                        if slot in completed:
                            continue
                        moved = float(np.linalg.norm(sample_xy[slot] - last_positions[slot]))
                        if (moved >= args.stall_distance_m
                                or route_indices[slot] > last_indices[slot]):
                            last_progress[slot] = branch_step + 1
                            last_positions[slot] = sample_xy[slot]
                            last_indices[slot] = route_indices[slot]
                            continue
                        if (branch_step + 1 - last_progress[slot] < args.stall_check_steps
                                or replan_counts[slot] >= args.max_branch_replans):
                            continue
                        start_xy = tuple(map(float, sample_xy[slot]))
                        path = branch_tracking_grid.path(start_xy, targets[slot])
                        if path is None:
                            branch_replans.append({
                                "slot": slot, "step": branch_step + 1,
                                "status": "no_passable_path", "start_xy": start_xy,
                            })
                            replan_counts[slot] = args.max_branch_replans
                            continue
                        revised = route_waypoints(
                            branch_tracking_grid, path, targets[slot], preserve_goal=True)
                        if not revised:
                            raise RuntimeError(f"empty replanned route for slot {slot}")
                        branch_routes[slot] = [tuple(map(float, point[:2])) for point in revised]
                        route_indices[slot] = 0
                        replan_counts[slot] += 1
                        recovery_remaining[slot] = args.recovery_steps
                        last_progress[slot] = branch_step + 1
                        last_positions[slot] = sample_xy[slot]
                        last_indices[slot] = 0
                        branch_replans.append({
                            "slot": slot, "step": branch_step + 1,
                            "status": "passable_path", "start_xy": start_xy,
                            "waypoints": len(revised),
                            "recovery_steps": args.recovery_steps,
                        })
                if (branch_step == 0 or len(completed) == args.slots
                        or (branch_step + 1) % 100 == 0):
                    active_count = args.slots - len(completed)
                    root_quaternions = m20.data.root_quat_w.detach().cpu()
                    root_velocities = m20.root_physx_view.get_root_velocities().detach().cpu()
                    active_history.append({
                        "branch_step": branch_step + 1,
                        "active": active_count,
                        "completed": len(completed),
                    })
                    report.update(
                        completed=len(completed),
                        active=active_count,
                        branch_step=branch_step + 1,
                        active_history=active_history,
                        route_indices=route_indices.tolist(),
                        visible_waypoint_advances=visible_waypoint_advances.tolist(),
                        branch_replans=branch_replans,
                        branch_progress=[
                            {
                                "slot": slot,
                                "position_xy": local_pos[slot, :2].detach().cpu().tolist(),
                                "position_xyz": local_pos[slot, :3].detach().cpu().tolist(),
                                "root_quaternion_wxyz": root_quaternions[slot].tolist(),
                                "root_velocity": root_velocities[slot].tolist(),
                                "next_waypoint_xy": list(branch_routes[slot][min(
                                    int(route_indices[slot]), len(branch_routes[slot]) - 1)]),
                                "next_waypoint_distance_m": float(np.linalg.norm(
                                    local_pos[slot, :2].detach().cpu().numpy()
                                    - np.asarray(branch_routes[slot][min(
                                        int(route_indices[slot]),
                                        len(branch_routes[slot]) - 1)]))),
                                "command": actions["m20_1"][slot].detach().cpu().tolist(),
                            }
                            for slot in range(args.slots)
                        ],
                    )
                    save()
                    print(
                        f"[ModernHouse32] step={branch_step + 1} active={active_count} "
                        f"completed={len(completed)}/{args.slots}",
                        flush=True,
                    )
                if len(completed) == args.slots:
                    break

            actions["m20_1"].zero_()
            clear_root_velocity("m20_1")
            completed_nodes.append("hall_release")
            save_checkpoint("hall_release_complete", {
                "completed_slots": sorted(completed),
                "distinct_targets": len({tuple(value) for value in targets}),
            })

            # 7. Every restored branch completes the declared room scan and hold.
            final_scan = entries["room_condition_scan"]
            move_aerial(final_scan["scan_goal"], steps=args.aerial_steps,
                        dwell_steps=args.scan_dwell_steps)
            clear_root_velocity("m20_1")
            completed_nodes.append("room_condition_scan")
            hold_steps = math.ceil(
                float(entries["placement_confirmation_hold"]["duration_s"])
                / float(env.step_dt))
            actions["carter_1"].zero_()
            for _ in range(hold_steps):
                clear_root_velocity("m20_1")
                advance()
            clear_root_velocity("m20_1")
            completed_nodes.append("placement_confirmation_hold")

            local_final = (m20.data.root_pos_w - origins).detach().cpu().numpy()
            standby_center = np.asarray(next(
                item for item in scenario["composition"]["targets"]
                if item["id"] == "primary_standby_zone")["position"][:2])
            reviewer_center = np.asarray(next(
                item for item in scenario["composition"]["targets"]
                if item["id"] == "reviewer_standby_zone")["position"][:2])
            carter_final = (
                env.scene.articulations["carter_1"].data.root_pos_w - origins
            ).detach().cpu().numpy()
            iris_final = (
                env.scene.articulations["iris_1"].data.root_pos_w - origins
            ).detach().cpu().numpy()
            armchair_final = (
                env.scene.rigid_objects[armchair_name].data.root_pos_w - origins
            ).detach().cpu().numpy()
            keepsake_final = (
                env.scene.rigid_objects[keepsake_name].data.root_pos_w - origins
            ).detach().cpu().numpy()
            armchair_expected = np.asarray([*joint["goal"][:2], joint["release_z"]])
            keepsake_expected = np.asarray(
                [*keepsake["goal"][:2], keepsake["release_z"]])
            iris_expected = np.asarray(final_scan["scan_goal"])
            branches = []
            for slot in range(args.slots):
                target_error = float(np.linalg.norm(
                    local_final[slot, :2] - np.asarray(targets[slot])))
                zone_error = float(np.linalg.norm(
                    local_final[slot, :2] - standby_center))
                reviewer_error = float(np.linalg.norm(
                    carter_final[slot, :2] - reviewer_center))
                iris_error = float(np.linalg.norm(iris_final[slot] - iris_expected))
                armchair_error = float(np.linalg.norm(
                    armchair_final[slot] - armchair_expected))
                keepsake_error = float(np.linalg.norm(
                    keepsake_final[slot] - keepsake_expected))
                verified = bool(
                    slot in completed
                    and zone_error <= 0.5
                    and reviewer_error <= 0.5
                    and iris_error <= args.aerial_tolerance
                    and armchair_error <= args.object_tolerance
                    and keepsake_error <= args.object_tolerance
                )
                branches.append({
                    "slot": slot,
                    "target_xy": list(targets[slot]),
                    "final_xy": local_final[slot, :2].tolist(),
                    "target_error_m": target_error,
                    "zone_error_m": zone_error,
                    "reviewer_standby_error_m": reviewer_error,
                    "room_scan_error_m": iris_error,
                    "armchair_error_m": armchair_error,
                    "keepsake_error_m": keepsake_error,
                    "completed": slot in completed,
                    "independently_verified": verified,
                    "first_complete_branch_step": first_complete_step.get(str(slot)),
                    "route_waypoints": len(branch_routes[slot]),
                })
            report["branches"] = branches
            report["completed"] = len(completed)
            report["active"] = args.slots - len(completed)
            report["independently_verified"] = sum(
                branch["independently_verified"] for branch in branches)
            report["distinct_targets"] = len({tuple(value) for value in targets})
            report["execution_s"] = time.monotonic() - branch_started
            save_checkpoint("home_recovery_complete", {
                "completed_slots": sorted(completed),
                "task_nodes_complete": len(completed) == args.slots,
                "hold_duration_s": hold_steps * float(env.step_dt),
                "observation_evidence": {
                    "kind": "timed_hover_boundary", "frame_count": 0,
                },
            })
            recovery_digest = checkpoints["home_recovery_complete"]["digest"]
            for branch in branches:
                slot = branch["slot"]
                branch_id = branch_id_by_slot[slot]
                branch_batch.record_checkpoint(
                    branch_id,
                    checkpoint_id="home_recovery_complete",
                    simulator_step=control_step,
                    snapshot_digest=recovery_digest,
                    snapshot_schema=branch_data_adapter.snapshot_schema,
                    restore_error=None,
                    restore_passed=None,
                    restore_status="pending",
                    restore_error_source="home_recovery_checkpoint_not_restored_before_close",
                    payload={
                        "checkpoint_store_step": report["checkpoint_steps"][
                            "home_recovery_complete"],
                    },
                )
                branch_batch.record_progress(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    progress={
                        "phase": "home_recovery_complete",
                        "completed_nodes": list(completed_nodes),
                        "completed": bool(branch["completed"]),
                        "completed_slots": len(completed),
                        "requested_slots": args.slots,
                    },
                )
                branch_batch.record_network(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    network={
                        "messages_offered": 0,
                        "messages_delivered": 0,
                        "scope": "Modern House navigation-only branch",
                    },
                )
                branch_batch.record_cost(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    cost={
                        "control_steps": branch.get(
                            "first_complete_branch_step", args.branch_max_steps),
                        "system_commands": control_step,
                        "participant_messages": 0,
                        "effective_execution": bool(branch["independently_verified"]),
                    },
                )
                branch_batch.record_event(
                    branch_id,
                    event_type="task_complete" if branch["completed"] else "task_failed",
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    payload={
                        "node": "home_recovery_complete",
                        "independently_verified": bool(branch["independently_verified"]),
                    },
                )
                branch_batch.finish_branch(
                    branch_id,
                    completed=bool(branch["completed"]),
                    independently_verified=bool(branch["independently_verified"]),
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    outcome=branch,
                )
            branch_batch.close_batch(
                status="completed",
                resident=report["resident"],
                peak_active=report["peak_active"],
                queued=report["queued"],
                details={"transport_scope": report["transport_scope"]},
            )
            report["branch_data_validation"] = validate_run(branch_batch.output)
            report["branch_data_counts"] = report["branch_data_validation"]["metrics"]
            verified_steps = store.verify_all()
            report["checkpoint_store_verified_steps"] = list(verified_steps)
            report["checkpoint_store_verified"] = (
                len(verified_steps) == (3 if imported_state is not None else len(CHECKPOINT_KEYS))
                and (imported_state is None or len(imported_verified_steps) == len(CHECKPOINT_KEYS))
            )
            report["completed_task_nodes"] = list(completed_nodes)
            report["full_task_completed"] = bool(
                len(completed) == args.slots
                and set(completed_nodes) == expected_task_nodes
            )
            report["passed"] = bool(
                report["resident"] == args.slots
                and report["peak_active"] == args.slots
                and report["completed"] == args.slots
                and report["independently_verified"] == args.slots
                and report["distinct_targets"] == args.slots
                and report["restore"]["passed"]
                and report["checkpoint_store_verified"]
                and report["full_task_completed"]
            )
            report["stage"] = "finished" if report["passed"] else "incomplete"
            report["wall_s"] = time.monotonic() - started
            report["process_peak_ram_gib"] = (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2)
            if static_recorder is not None:
                static_recorder.close()
                from demo.modern_house_benchmark.shared_static import audited_group_names

                names, digest = audited_group_names(static_audit)
                report["recorded_static_audit_sha256"] = digest
                report["recorded_static_groups"] = len(names)
            else:
                common_static.validate_immutable()
                report["shared_static_guard"] = common_static.report()
                common_static.close()
            save()
    finally:
        clone_stack.close()
    return 0 if report["passed"] else 1


def _supervise(args) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        "-m",
        "demo.modern_house_benchmark.checkpoint_branches",
        *(sys.argv[1:] if args.argv is None else args.argv),
        "--worker",
    ]
    _write_json(output / "launch.json", {"command": command})
    environment = dict(
        os.environ,
        PYTHONUNBUFFERED="1",
        MALLOC_ARENA_MAX="2",
        OPENBLAS_NUM_THREADS="8",
        CUBLAS_WORKSPACE_CONFIG=":4096:8",
    )
    with (output / "worker.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        from tools.physics_replay.execution.budget import LinuxResourceProbe

        probe = LinuxResourceProbe()
        samples = []
        abort_reason = None
        deadline = time.monotonic() + args.timeout
        while child.poll() is None:
            sample = probe.sample([child.pid])
            sample["wall_s"] = time.monotonic()
            samples.append(sample)
            with (output / "resources.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample) + "\n")
            if sample["host_available"] < args.host_headroom_gib * 1024 ** 3:
                abort_reason = f"host headroom below {args.host_headroom_gib:g} GiB"
            elif sample["gpu_free"] < args.gpu_headroom_gib * 1024 ** 3:
                abort_reason = f"GPU headroom below {args.gpu_headroom_gib:g} GiB"
            elif time.monotonic() > deadline:
                abort_reason = f"worker exceeded {args.timeout:g} seconds"
            if abort_reason:
                os.killpg(child.pid, 15)
                break
            try:
                child.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        try:
            status = child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, 9)
            status = child.wait(timeout=10)
    path = output / "report.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "passed": False,
        "stage": "failed",
        "error": "worker produced no report",
    }
    workers = [sample.get("workers", {}).get(str(child.pid), {}) for sample in samples]
    report["resources"] = {
        "peak_rss_bytes": max((item.get("rss", 0) for item in workers), default=0),
        "peak_pss_bytes": max((item.get("pss", 0) for item in workers), default=0),
        "peak_gpu_used_bytes": max(
            (sample.get("gpu_used", 0) for sample in samples), default=0),
        "min_gpu_free_bytes": min(
            (sample.get("gpu_free", 0) for sample in samples), default=0),
        "min_host_available_bytes": min(
            (sample.get("host_available", 0) for sample in samples), default=0),
    }
    report["worker_exit"] = status
    log_text = (output / "worker.log").read_text(encoding="utf-8", errors="replace")
    physics_errors = [message for message in (
        "will miss interactions", "getRigidDynamicData: CUDA error",
        "getArticulationData: CUDA error", "Scene state is corrupted",
    ) if message in log_text]
    report["physics_errors"] = physics_errors
    if physics_errors:
        report["passed"] = False
    if abort_reason or status != 0 or not report.get("passed"):
        report.update(
            passed=False,
            supervisor_error=abort_reason,
            last_worker_stage=report.get("stage"),
            stage="failed",
        )
    _write_json(path, report)
    print(json.dumps({
        key: report.get(key) for key in (
            "passed", "stage", "requested", "resident", "peak_active",
            "completed", "queued", "independently_verified", "worker_exit",
            "supervisor_error")
    }, ensure_ascii=False))
    return 0 if status == 0 and report.get("passed") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument(
        "--import-checkpoint", type=Path,
        help="reuse the verified single-slot task prefix and restore it into every live physical row",
    )
    parser.add_argument("--static-audit", type=Path)
    parser.add_argument(
        "--record-static-audit",
        action="store_true",
        help="run one complete private Modern House slot and record its static write audit",
    )
    parser.add_argument("--slots", type=int, default=32)
    parser.add_argument(
        "--disable-dormant-camera",
        action="store_true",
        help=(
            "disable the unused tiled RGB/RTX path for branch-capacity runs; "
            "the task's scan checkpoints remain timed-hover boundaries with frame_count=0"
        ),
    )
    parser.add_argument("--clone-mode", choices=("copy", "inherit"), default="inherit")
    parser.add_argument("--compact-physx-capacity", action="store_true",
                        help="reserve smaller, task-bounded GPU PhysX pair/contact buffers")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--aerial-steps", type=int, default=100)
    parser.add_argument("--scan-dwell-steps", type=int, default=25)
    parser.add_argument("--route-max-steps", type=int, default=2400)
    parser.add_argument("--ground-speed", type=float, default=0.5)
    parser.add_argument("--waypoint-tolerance", type=float, default=0.18)
    parser.add_argument(
        "--visible-waypoint-lookahead",
        type=float,
        default=0.5,
        help=(
            "skip a nearby intermediate hall-release waypoint only when the next "
            "segment is passable in the same inflated occupancy grid"
        ),
    )
    parser.add_argument(
        "--prefix-visible-waypoint-lookahead",
        type=float,
        default=0.7,
        help=(
            "skip a nearby M20 prefix waypoint only when the next segment is "
            "passable in the same inflated occupancy grid"
        ),
    )
    parser.add_argument(
        "--terminal-tolerance",
        type=float,
        default=0.5,
        help="Modern House target-zone terminal radius in metres",
    )
    parser.add_argument(
        "--branch-completion-zone-tolerance",
        type=float,
        default=0.45,
        help=(
            "safer in-zone radius required before a completed branch is held; "
            "final verification remains bounded by the declared 0.5 m zone"
        ),
    )
    parser.add_argument("--object-tolerance", type=float, default=0.03)
    parser.add_argument(
        "--aerial-tolerance",
        type=float,
        default=0.5,
        help="declared room_review_zone radius used for the final Iris scan",
    )
    parser.add_argument("--restore-probe-steps", type=int, default=5)
    parser.add_argument("--restore-tolerance", type=float, default=1e-5)
    parser.add_argument("--branch-max-steps", type=int, default=3600)
    parser.add_argument("--stall-check-steps", type=int, default=100)
    parser.add_argument("--stall-distance-m", type=float, default=0.06)
    parser.add_argument("--max-branch-replans", type=int, default=3)
    parser.add_argument("--recovery-steps", type=int, default=60)
    parser.add_argument("--recovery-speed", type=float, default=0.25)
    parser.add_argument("--recovery-turn-rate", type=float, default=0.8)
    parser.add_argument("--timeout", type=float, default=2400.0)
    parser.add_argument("--host-headroom-gib", type=float, default=1.0)
    parser.add_argument("--gpu-headroom-gib", type=float, default=1.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.argv = argv
    if not 1 <= args.slots <= 32:
        parser.error("slots must be between 1 and 32")
    if args.record_static_audit and args.slots != 1:
        parser.error("--record-static-audit requires --slots 1")
    if not args.record_static_audit and args.static_audit is None:
        parser.error("--static-audit is required unless --record-static-audit is used")
    positive = (
        args.aerial_steps, args.scan_dwell_steps, args.route_max_steps,
        args.ground_speed, args.waypoint_tolerance, args.terminal_tolerance,
        args.branch_completion_zone_tolerance,
        args.visible_waypoint_lookahead, args.prefix_visible_waypoint_lookahead,
        args.object_tolerance, args.aerial_tolerance,
        args.restore_probe_steps, args.restore_tolerance,
        args.branch_max_steps, args.timeout, args.host_headroom_gib,
        args.gpu_headroom_gib, args.stall_check_steps, args.stall_distance_m,
        args.max_branch_replans, args.recovery_steps, args.recovery_speed,
        args.recovery_turn_rate,
    )
    if any(not math.isfinite(float(value)) or float(value) <= 0 for value in positive):
        parser.error("step counts, tolerances, speeds and resource limits must be positive")
    try:
        return _run_worker(args) if args.worker else _supervise(args)
    except BaseException as error:
        args.output.mkdir(parents=True, exist_ok=True)
        _write_json(args.output / "report.json", {
            "passed": False,
            "stage": "failed",
            "requested": args.slots,
            "resident": 0,
            "peak_active": 0,
            "completed": 0,
            "queued": 0,
            "independently_verified": 0,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        print(
            f"[Modern House checkpoint branches] failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
