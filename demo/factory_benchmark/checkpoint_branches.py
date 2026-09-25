"""Run the Factory inspection prefix once and fan out 1..32 physical branches.

The runner uses one Isaac Kit process and one batched CUDA PhysX scene.  It
persists public checkpoints after every task phase, restores the final
observation boundary, and releases one distinct in-zone Carter goal per slot.
Private PhysX solver/contact caches are intentionally outside the checkpoint.
"""

from __future__ import annotations

import argparse
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
DEFAULT_SCENARIO = ROOT / "demo/factory_benchmark/scenarios/inspection_01.json"
DEFAULT_STATIC_AUDIT = (
    ROOT / "tmp/replay/realsense_tiled_20260917_165723/inputs/factory_static_audit.json"
)
CHECKPOINT_KEYS = (
    "aerial_view_complete",
    "ground_approach_complete",
    "ground_view_complete",
    "mission_complete",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                   default=lambda item: item.tolist() if hasattr(item, "tolist") else str(item))
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scenario_selection(scenario: dict) -> dict:
    """Build the equivalent V1 selection so Isaac Lab may clone the scene."""
    robots = []
    for robot in scenario["robots"]:
        item = deepcopy(robot)
        # Native ROS camera suites are single-environment.  Keep the Iris
        # camera declaration for the tiny tiled proof sensor; the branch test
        # intentionally checks ground observation timing without 32 C01 rigs.
        item["attachments"] = ([{"type": "camera", "controller": None}]
                               if item["type"] == "iris" else [])
        robots.append(item)
    return {
        "version": 1,
        "task_name": "factory_inspection_checkpoint_branches",
        "scene_key": scenario["scene_key"],
        "robots": robots,
    }


def branch_targets(center: tuple[float, float], count: int, radius: float = 0.18) -> list[tuple[float, float]]:
    if type(count) is not int or not 1 <= count <= 32:
        raise ValueError("branch count must be between 1 and 32")
    if not math.isfinite(radius) or not 0 <= radius < 0.5:
        raise ValueError("branch target radius must be finite and inside the task zone")
    if count == 1:
        return [tuple(map(float, center))]
    return [
        (float(center[0] + radius * math.cos(2 * math.pi * index / count)),
         float(center[1] + radius * math.sin(2 * math.pi * index / count)))
        for index in range(count)
    ]


def _branch_routes(scenario: dict, count: int) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    from EAI.scenario_spec.geometry import provider_map
    from demo.benchmark.runtime import build_plan, planning_template

    occupancy = provider_map(scene_key=scenario["scene_key"])
    template = planning_template(scenario["scene_key"], occupancy)
    target = next(item for item in scenario["composition"]["targets"]
                  if item["id"] == "route_checkpoint")
    targets = branch_targets(tuple(target["position"][:2]), count)
    routes = []
    for xy in targets:
        candidate = deepcopy(scenario)
        candidate_target = next(item for item in candidate["composition"]["targets"]
                                if item["id"] == "route_checkpoint")
        candidate_target["position"][:2] = xy
        plan = build_plan(candidate, occupancy, template)
        entry = next(item for item in plan if item["node"] == "route_confirmation")
        routes.append([tuple(map(float, point[:2])) for point in entry["waypoints"]])
    return targets, routes


def _prefix_route(scenario: dict, node_id: str) -> list[tuple[float, float]]:
    from EAI.scenario_spec.geometry import provider_map
    from demo.benchmark.runtime import build_plan, planning_template

    occupancy = provider_map(scene_key=scenario["scene_key"])
    plan = build_plan(scenario, occupancy, planning_template(scenario["scene_key"], occupancy))
    entry = next(item for item in plan if item["node"] == node_id)
    return [tuple(map(float, point[:2])) for point in entry["waypoints"]]


@dataclass
class _NullCamera:
    generation: int = 0

    def invalidate(self) -> None:
        self.generation += 1


class InspectionCheckpoint:
    """Public articulation/controller checkpoint for this navigation-only task."""

    def __init__(self, env):
        import torch
        from tools.physics_replay.wrench_state import WrenchState
        from tools.physics_replay.resources import ReplayResource
        from tools.physics_replay.runtime_state import ActuatorStateComponent, StateCodec

        self.env = env
        self.codec = StateCodec()
        self.camera = _NullCamera()
        self.env.replay_external_wrench_sources = {"inspection": WrenchState(env.scene)}
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
        return {
            name: {
                field: getattr(articulation.root_physx_view, f"get_{field}")().clone()
                for field in ("root_transforms", "root_velocities", "dof_positions", "dof_velocities")
            }
            for name, articulation in self.env.scene.articulations.items()
        }

    def capture(self, task: dict) -> dict:
        import random
        import torch
        from tools.physics_replay.runtime_state import EnvironmentBuffersComponent

        fields = tuple(EnvironmentBuffersComponent.fields) + ("actions_dict", "_commands_dict")
        values = {
            "physics": self.physics(),
            "task": deepcopy(task),
            "buffers": {key: getattr(self.env, key) for key in fields if hasattr(self.env, key)},
            "controllers": {
                name: {key: value for key, value in controller.items()
                       if key not in ("policy", "preprocessor")}
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
            "resources": {name: value.capture_state() for name, value in self.resources.items()},
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
        for name, fields in values["physics"].items():
            view = self.env.scene.articulations[name].root_physx_view
            for field, value in fields.items():
                getattr(view, f"set_{field}")(value, ids)
        for name, value in values["buffers"].items():
            setattr(self.env, name, value)
        for name, commands in values["buffers"].get("_commands_dict", {}).items():
            for key, value in commands.items():
                self.env.set_command(name, key, value)
        for name, data in values["controllers"].items():
            target = self.env._controllers_dict[name]
            handles = {key: target[key] for key in ("policy", "preprocessor") if key in target}
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
        errors = [
            float((actual[name][field] - expected).abs().max())
            for name, fields in values["physics"].items()
            for field, expected in fields.items()
            if expected.numel()
        ]
        return deepcopy(values["task"]), max(errors, default=0.0)


def _yaw(quaternion):
    import torch
    w, x, y, z = quaternion.unbind(-1)
    return torch.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _steering_rows(position, quaternion, goals, active, *, speed=0.55, k_turn=2.0):
    import torch
    yaw = _yaw(quaternion)
    delta = goals - position[:, :2]
    distance = delta.norm(dim=-1)
    desired = torch.atan2(delta[:, 1], delta[:, 0])
    error = torch.atan2(torch.sin(desired - yaw), torch.cos(desired - yaw))
    wz = (k_turn * error).clamp(-1.2, 1.2)
    heading_scale = (1.0 - error.abs() / 0.65).clamp(0.0, 1.0)
    distance_scale = (distance / 1.0).clamp(0.2, 1.0)
    command = torch.zeros((len(position), 3), device=position.device)
    command[:, 0] = speed * heading_scale * distance_scale
    command[:, 2] = wz
    command[~active] = 0
    return command, distance


def _configure_env(
    cfg,
    *,
    slots: int,
    static_audit: Path | None,
    clone_mode: str,
) -> None:
    from isaaclab.sensors import TiledCameraCfg

    cfg.sim.physx.enable_enhanced_determinism = True
    cfg.scene.world.prim_path = "{ENV_REGEX_NS}/Indoor"
    cfg.scene.num_envs = slots
    # Every private robot batch row occupies the same task coordinates. GPU
    # env-id filtering isolates cross-slot dynamics, while the audited Factory
    # colliders are extracted once and shared by all rows.
    cfg.scene.env_spacing = 0.0
    cfg.scene.replicate_physics = True
    cfg.scene.clone_in_fabric = clone_mode == "fabric"
    cfg.scene.filter_collisions = True
    cfg.episode_length_s = 10000.0
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

    if static_audit is None:
        return

    from isaaclab.scene import InteractiveScene
    from EAI.hmrs_env.multi_robot_direct_env import MultiRobotDirectEnv
    from demo.fire_rescue.branching.parallel.shared_factory import (
        partition_factory_physics_scenes,
    )
    from demo.fire_rescue.branching.parallel.shared_static import (
        prepare_gpu_shared_static_source,
        split_factory_static_geometry,
    )

    if clone_mode in ("copy", "inherit"):
        original_clone_environments = InteractiveScene.clone_environments

        def clone_with_shared_static(scene, copy_from_source=False):
            try:
                prepare_gpu_shared_static_source(scene, static_audit)
                return original_clone_environments(
                    scene,
                    copy_from_source=clone_mode == "copy",
                )
            finally:
                if vars(InteractiveScene).get("clone_environments") is clone_with_shared_static:
                    InteractiveScene.clone_environments = original_clone_environments

        InteractiveScene.clone_environments = clone_with_shared_static
    original_setup = MultiRobotDirectEnv._setup_scene
    owned_setup = vars(MultiRobotDirectEnv).get("_setup_scene")

    def setup(environment):
        try:
            original_setup(environment)
            partition_factory_physics_scenes(environment.scene, shared_solver=True)
            split_factory_static_geometry(environment.scene, static_audit, gpu_shared=True)
        finally:
            if owned_setup is None:
                del MultiRobotDirectEnv._setup_scene
            else:
                MultiRobotDirectEnv._setup_scene = owned_setup

    MultiRobotDirectEnv._setup_scene = setup


def _run_worker(args) -> int:
    # Factory normally falls back to CPU PhysX because the converted scene has
    # known GPU-instability modes.  This command is an explicit diagnostic GPU
    # capacity test and fails closed below if Isaac Sim still resolves to CPU.
    os.environ["EAI_FACTORY_GPU_PHYSICS"] = "1"
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from tools.physics_replay.checkpoints import CheckpointStore
    from tools.physics_replay.components import ComponentSnapshot
    from demo.benchmark.branch_data import digest_state
    from demo.benchmark.branch_data import open_batch, source_revision, validate_run
    from demo.factory_benchmark.branch_data_adapter import FactoryBranchDataAdapter

    scenario_path = args.scenario.resolve()
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    if scenario.get("benchmark_task_id") != "F-INS-01":
        raise ValueError("this runner accepts only Factory F-INS-01")
    selection = _scenario_selection(scenario)
    targets, routes = _branch_routes(scenario, args.slots)
    simple_route = _prefix_route(scenario, "ground_approach")
    branch_data_adapter = FactoryBranchDataAdapter(
        scenario=scenario,
        scenario_path=scenario_path,
        targets=targets,
        routes=routes,
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    recording_static_audit = bool(args.record_static_audit)
    static_audit = output / "factory_static_audit.json" if recording_static_audit else args.static_audit.resolve()
    static_group_names = ()
    static_audit_sha256 = None
    if not recording_static_audit:
        if not static_audit.is_file():
            raise FileNotFoundError(f"shared Factory static audit does not exist: {static_audit}")
        from demo.fire_rescue.branching.parallel.shared_static import audited_group_names

        static_group_names, static_audit_sha256 = audited_group_names(static_audit)
    store = CheckpointStore(output)
    started = time.monotonic()
    report = {
        "passed": False,
        "stage": "starting",
        "scenario": str(scenario_path),
        "scenario_sha256": _sha256(scenario_path),
        "mode": "record_static_audit" if recording_static_audit else "shared_static_branches",
        "clone_mode": "private" if recording_static_audit else args.clone_mode,
        "shared_static_audit": None if recording_static_audit else str(static_audit),
        "shared_static_audit_sha256": static_audit_sha256,
        "shared_static_groups": len(static_group_names),
        "requested": args.slots,
        "resident": 0,
        "peak_active": 0,
        "completed": 0,
        "queued": 0,
        "independently_verified": 0,
        "checkpoint_keys": list(CHECKPOINT_KEYS),
        "checkpoint_scope": (
            "public articulation/controller/actuator/runtime/RNG state; "
            "private PhysX solver/contact/warm-start caches excluded"
        ),
        "branch_scope": (
            f"{args.slots} simultaneous physical slots from ground_view_complete; "
            "distinct Carter goals remain inside the original route_checkpoint zone"
        ),
        "sensor_scope": (
            "tiny dormant tiled Iris camera is instantiated for multi-env admission; "
            "this run validates motion/checkpoint branching, not 32-way ground RGB fidelity"
        ),
        "branch_targets": targets,
        "checkpoint_steps": {},
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
            "runner": "demo.factory_benchmark.checkpoint_branches",
            "checkpoint_scope": report["checkpoint_scope"],
            "sensor_scope": report["sensor_scope"],
        },
        autosave_every=max(128, args.slots * 8),
    )
    branch_id_by_slot: dict[int, str] = {}
    branch_runtime = {"route_indices": None, "completed": set(), "phase": "prefix"}
    report["execution_scope"] = "measured"
    report["branch_data_output"] = str(output / "branch_data")
    save()

    from functools import partial
    config = SimulatorLaunchConfig(
        env=selection["task_name"],
        resolved_env_name=selection["task_name"],
        selection_data=selection,
        num_envs=args.slots,
        device="cuda:0",
        seed=int(scenario.get("seed", 43)),
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        aerial_sensor_backend="tiled_rgb",
        env_cfg_hook=partial(
            _configure_env,
            slots=args.slots,
            static_audit=None if recording_static_audit else static_audit,
            clone_mode="copy" if recording_static_audit else args.clone_mode,
        ),
        app_launcher_args={
            "headless": True,
            "enable_cameras": True,
            "device": "cuda:0",
            "kit_args": "--/app/settings/persistent=false",
        },
    )

    with open_simulator_session(config) as session:
        import torch
        from isaacsim.core.simulation_manager import SimulationManager
        from pxr import PhysxSchema

        env = session.base_env
        origins = env.scene.env_origins
        report["actual_device"] = str(env.sim.device)
        report["simulation_manager_device"] = SimulationManager.get_physics_sim_device()
        scene_api = PhysxSchema.PhysxSceneAPI(
            env.scene.stage.GetPrimAtPath(env.cfg.sim.physics_prim_path))
        report["gpu_dynamics"] = bool(scene_api.GetEnableGPUDynamicsAttr().Get())
        if report["actual_device"] != "cuda:0" or report["simulation_manager_device"] != "cuda:0":
            raise RuntimeError("32-way branch test requires actual CUDA PhysX")
        counts = {name: int(robot.root_physx_view.count)
                  for name, robot in env.scene.articulations.items()}
        report["articulation_counts"] = counts
        if set(counts.values()) != {args.slots}:
            raise RuntimeError(f"missing resident articulation rows: {counts}")
        report["resident"] = args.slots
        from demo.fire_rescue.branching.parallel.shared_factory import validate_slot_topology

        common_static = getattr(env.scene, "replay_static_factory", None)
        if common_static is None and not recording_static_audit:
            raise RuntimeError("shared Factory static geometry was not registered")
        report["slot_topology"] = validate_slot_topology(
            env.scene.stage,
            args.slots,
            common_static=common_static,
            gpu_env_ids=not recording_static_audit,
            allow_slot_inheritance=(
                not recording_static_audit and args.clone_mode == "inherit"
            ),
        )
        static_audit_recorder = None
        if recording_static_audit:
            from demo.fire_rescue.branching.parallel.factory_static_audit import FactoryStaticAudit

            static_audit_recorder = FactoryStaticAudit(env.scene.stage, args.slots, static_audit)
            report["recorded_static_audit"] = str(static_audit)
        report["origins"] = origins.cpu().tolist()
        report["control_dt_s"] = float(env.step_dt)
        report["physics_dt_s"] = float(env.physics_dt)
        report["stage"] = "prefix"
        save()

        checkpoint = InspectionCheckpoint(env)
        control_step = 0
        checkpoints = {}
        agents = tuple(session.possible_agents)
        if set(("iris_1", "simple_car_1", "carter_1")) - set(agents):
            raise RuntimeError(f"unexpected agent set: {agents}")
        actions = {name: torch.zeros((args.slots, 3), device=session.device) for name in agents}
        iris_goal_local = torch.tensor([-7.95, -6.35, 2.3], device=session.device)
        iris_goal_world = origins + iris_goal_local
        actions["iris_1"] = iris_goal_world.clone()

        def advance() -> None:
            nonlocal control_step
            before = int(env._sim_step_counter)
            env.step(actions)
            if int(env._sim_step_counter) - before != env.cfg.decimation:
                raise RuntimeError("unexpected shared physics substep count")
            control_step += 1
            if branch_id_by_slot and branch_runtime["route_indices"] is not None:
                route_indices_local = branch_runtime["route_indices"]
                completed_local = branch_runtime["completed"]
                post_states = branch_data_adapter.read_all_branch_states({
                    "env": env,
                    "origins": origins,
                })
                command_rows = branch_data_adapter.command_rows(actions)
                positions = (
                    env.scene.articulations["carter_1"].data.root_pos_w - origins
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
                        },
                        "task_state": {
                            "completed_slots": len(completed_local),
                            "requested_slots": args.slots,
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
            state = checkpoint.capture({"checkpoint": name, "control_step": control_step, **logical})
            snapshots = {"inspection": ComponentSnapshot("inspection", 1, state)}
            store.write(control_step, snapshots)
            reread = store.read(control_step)["inspection"].state
            physics = checkpoint.codec.decode(reread["values"])["physics"]
            digest = digest_state(physics)
            report["checkpoint_steps"][name] = control_step
            checkpoints[name] = {"state": reread, "digest": digest}
            report.setdefault("checkpoints", {})[name] = {
                "control_step": control_step,
                "physics_digest": digest,
                "json": f"checkpoints/{control_step:08d}.json",
                "npz": f"checkpoints/{control_step:08d}.npz",
            }
            save()
            return reread

        # Match the requested kinematic aerial mode while still advancing all
        # 32 physical rows at every barrier.
        iris = env.scene.articulations["iris_1"]
        initial = iris.root_physx_view.get_root_transforms().clone()
        ids = torch.arange(args.slots, device=session.device)
        for index in range(args.aerial_steps):
            advance()
            alpha = float(index + 1) / args.aerial_steps
            transforms = iris.root_physx_view.get_root_transforms().clone()
            transforms[:, :3] = initial[:, :3] * (1 - alpha) + iris_goal_world * alpha
            transforms[:, 3:7] = transforms.new_tensor([0.0, 0.0, 1.0, 0.0])
            iris.root_physx_view.set_root_transforms(transforms, ids)
            iris.root_physx_view.set_root_velocities(torch.zeros_like(
                iris.root_physx_view.get_root_velocities()), ids)
            env.sim.forward()
        for _ in range(args.aerial_dwell_steps):
            advance()
        save_checkpoint("aerial_view_complete", {
            "completed_nodes": ["aerial_view"],
            "observation_evidence": {"kind": "timed_hover_boundary", "frame_count": 0},
        })

        simple = env.scene.articulations["simple_car_1"]
        route_index = torch.zeros(args.slots, dtype=torch.long, device=session.device)
        simple_goals = torch.tensor(simple_route, dtype=torch.float32, device=session.device)
        for _ in range(args.prefix_max_steps):
            local_pos = simple.data.root_pos_w - origins
            active = route_index < len(simple_route)
            if not bool(active.any()):
                break
            selected = simple_goals[torch.clamp(route_index, max=len(simple_route) - 1)]
            command, distance = _steering_rows(local_pos, simple.data.root_quat_w, selected, active)
            actions["simple_car_1"] = command
            advance()
            local_pos = simple.data.root_pos_w - origins
            distance = (local_pos[:, :2] - selected).norm(dim=1)
            terminal = route_index == len(simple_route) - 1
            reached = active & (distance <= torch.where(terminal, distance.new_tensor(0.12),
                                                        distance.new_tensor(0.15)))
            route_index[reached] += 1
        if bool((route_index < len(simple_route)).any()):
            raise RuntimeError("SimpleCar failed to reach ground_checkpoint in the prefix budget")
        actions["simple_car_1"].zero_()
        save_checkpoint("ground_approach_complete", {
            "completed_nodes": ["aerial_view", "ground_approach"],
            "route_waypoints": len(simple_route),
        })

        target_yaw = -2.76
        aligned_steps = 0
        for _ in range(args.alignment_max_steps):
            yaw = _yaw(simple.data.root_quat_w)
            error = torch.atan2(torch.sin(yaw.new_tensor(target_yaw) - yaw),
                                torch.cos(yaw.new_tensor(target_yaw) - yaw))
            actions["simple_car_1"].zero_()
            actions["simple_car_1"][:, 2] = (2.0 * error).clamp(-1.2, 1.2)
            advance()
            if bool((error.abs() <= 0.05).all()):
                aligned_steps += 1
                if aligned_steps >= args.alignment_hold_steps:
                    break
            else:
                aligned_steps = 0
        if aligned_steps < args.alignment_hold_steps:
            raise RuntimeError("SimpleCar failed to align for the ground observation window")
        actions["simple_car_1"].zero_()
        for _ in range(args.ground_view_steps):
            advance()
        branch_state = save_checkpoint("ground_view_complete", {
            "completed_nodes": ["aerial_view", "ground_approach", "ground_view"],
            "observation_evidence": {
                "kind": "timed_stationary_window",
                "duration_s": args.ground_view_steps * float(env.step_dt),
                "frame_count": 0,
            },
        })

        # Mutate the public state, then restore from the persisted checkpoint.
        for _ in range(args.restore_probe_steps):
            actions["carter_1"][:, 0] = 0.25
            advance()
        actions["carter_1"].zero_()
        restored_task, restore_error = checkpoint.restore(branch_state)
        report["restore"] = {
            "source": "ground_view_complete",
            "restored_task": restored_task,
            "max_abs_error": restore_error,
            "threshold": args.restore_tolerance,
            "passed": bool(restore_error <= args.restore_tolerance),
        }
        if restore_error > args.restore_tolerance:
            raise RuntimeError(f"checkpoint restore error exceeds tolerance: {restore_error}")
        control_step = int(restored_task["control_step"])

        branch_checkpoint_digest = checkpoints["ground_view_complete"]["digest"]
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
                checkpoint_id="ground_view_complete",
                simulator_step=control_step,
                snapshot_digest=branch_checkpoint_digest,
                snapshot_schema=branch_data_adapter.snapshot_schema,
                restore_error=restore_error,
                restore_passed=bool(restore_error <= args.restore_tolerance),
                restore_status="measured",
                restore_error_source="max_abs_public_factory_physics_tensor_error",
                payload={
                    "checkpoint_store_step": report["checkpoint_steps"][
                        "ground_view_complete"],
                    "continued_after_restore": True,
                },
            )
            branch_batch.record_event(
                identity.branch_id,
                event_type="checkpoint_restored",
                simulator_step=control_step,
                sim_time_s=control_step * float(env.step_dt),
                payload={"checkpoint_id": "ground_view_complete", "restore_error": restore_error},
            )
            branch_batch.record_progress(
                identity.branch_id,
                simulator_step=control_step,
                sim_time_s=control_step * float(env.step_dt),
                progress={
                    "phase": "route_confirmation",
                    "completed_slots": 0,
                    "requested_slots": args.slots,
                },
            )

        report["stage"] = "branching"
        report["peak_active"] = args.slots
        save()
        carter = env.scene.articulations["carter_1"]
        route_indices = np.zeros(args.slots, dtype=np.int64)
        completed = set()
        branch_runtime["route_indices"] = route_indices
        branch_runtime["completed"] = completed
        branch_runtime["phase"] = "route_confirmation"
        first_complete_step = {}
        active_history = []
        branch_started = time.monotonic()
        for branch_step in range(args.branch_max_steps):
            local_pos = carter.data.root_pos_w - origins
            active = torch.tensor([index not in completed for index in range(args.slots)],
                                  dtype=torch.bool, device=session.device)
            goals = torch.zeros((args.slots, 2), device=session.device)
            for slot in range(args.slots):
                route = routes[slot]
                waypoint = route[min(int(route_indices[slot]), len(route) - 1)]
                goals[slot] = goals.new_tensor(waypoint)
            command, _ = _steering_rows(local_pos, carter.data.root_quat_w, goals, active)
            actions["carter_1"] = command
            advance()
            local_pos = carter.data.root_pos_w - origins
            for slot in range(args.slots):
                if slot in completed:
                    continue
                route = routes[slot]
                index = int(route_indices[slot])
                waypoint = np.asarray(route[index])
                distance = float(np.linalg.norm(local_pos[slot, :2].detach().cpu().numpy() - waypoint))
                tolerance = 0.25 if index == len(route) - 1 else 0.15
                if distance <= tolerance:
                    route_indices[slot] += 1
                center = np.asarray(next(item for item in scenario["composition"]["targets"]
                                         if item["id"] == "route_checkpoint")["position"][:2])
                zone_error = float(np.linalg.norm(local_pos[slot, :2].detach().cpu().numpy() - center))
                if route_indices[slot] >= len(route) and zone_error <= 0.5:
                    completed.add(slot)
                    first_complete_step[str(slot)] = branch_step + 1
            if branch_step == 0 or len(completed) == args.slots or (branch_step + 1) % 100 == 0:
                active_count = args.slots - len(completed)
                active_history.append({"branch_step": branch_step + 1,
                                       "active": active_count, "completed": len(completed)})
                report.update(completed=len(completed), active=active_count,
                              branch_step=branch_step + 1, active_history=active_history,
                              route_indices=route_indices.tolist())
                save()
                print(f"[Factory32] step={branch_step + 1} active={active_count} "
                      f"completed={len(completed)}/{args.slots}", flush=True)
            if len(completed) == args.slots:
                break

        actions["carter_1"].zero_()
        local_final = (carter.data.root_pos_w - origins).detach().cpu().numpy()
        center = np.asarray(next(item for item in scenario["composition"]["targets"]
                                 if item["id"] == "route_checkpoint")["position"][:2])
        branches = []
        for slot in range(args.slots):
            target_error = float(np.linalg.norm(local_final[slot, :2] - np.asarray(targets[slot])))
            zone_error = float(np.linalg.norm(local_final[slot, :2] - center))
            branches.append({
                "slot": slot,
                "target_xy": list(targets[slot]),
                "final_xy": local_final[slot, :2].tolist(),
                "target_error_m": target_error,
                "zone_error_m": zone_error,
                "completed": slot in completed,
                "first_complete_branch_step": first_complete_step.get(str(slot)),
                "route_waypoints": len(routes[slot]),
            })
        report["branches"] = branches
        report["completed"] = len(completed)
        report["active"] = args.slots - len(completed)
        report["independently_verified"] = sum(
            branch["completed"] and branch["zone_error_m"] <= 0.5 for branch in branches)
        report["distinct_targets"] = len({tuple(value) for value in targets})
        report["execution_s"] = time.monotonic() - branch_started
        save_checkpoint("mission_complete", {
            "completed_nodes": (["aerial_view", "ground_approach", "ground_view", "route_confirmation"]
                                if len(completed) == args.slots else
                                ["aerial_view", "ground_approach", "ground_view"]),
            "completed_slots": sorted(completed),
        })
        mission_digest = checkpoints["mission_complete"]["digest"]
        for branch in branches:
            slot = branch["slot"]
            branch_id = branch_id_by_slot[slot]
            branch_batch.record_checkpoint(
                branch_id,
                checkpoint_id="mission_complete",
                simulator_step=control_step,
                snapshot_digest=mission_digest,
                snapshot_schema=branch_data_adapter.snapshot_schema,
                restore_error=None,
                restore_passed=None,
                restore_status="pending",
                restore_error_source="mission_checkpoint_not_restored_before_close",
                payload={"checkpoint_store_step": report["checkpoint_steps"]["mission_complete"]},
            )
            branch_batch.record_progress(
                branch_id,
                simulator_step=control_step,
                sim_time_s=control_step * float(env.step_dt),
                progress={
                    "phase": "route_confirmation",
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
                    "scope": "navigation-only Factory branch",
                },
            )
            branch_batch.record_cost(
                branch_id,
                simulator_step=control_step,
                sim_time_s=control_step * float(env.step_dt),
                cost={
                    "control_steps": branch.get("first_complete_branch_step", args.branch_max_steps),
                    "system_commands": control_step,
                    "participant_messages": 0,
                    "effective_execution": bool(branch["completed"]),
                },
            )
            branch_batch.record_event(
                branch_id,
                event_type="task_complete" if branch["completed"] else "task_failed",
                simulator_step=control_step,
                sim_time_s=control_step * float(env.step_dt),
                payload={"node": "route_confirmation", "zone_error_m": branch["zone_error_m"]},
            )
            branch_batch.finish_branch(
                branch_id,
                completed=bool(branch["completed"]),
                independently_verified=bool(
                    branch["completed"] and branch["zone_error_m"] <= 0.5
                ),
                simulator_step=control_step,
                sim_time_s=control_step * float(env.step_dt),
                outcome=branch,
            )
        branch_batch.close_batch(
            status="completed",
            resident=report["resident"],
            peak_active=report["peak_active"],
            queued=report["queued"],
            details={"transport_scope": report["branch_scope"]},
        )
        report["branch_data_validation"] = validate_run(branch_batch.output)
        report["branch_data_counts"] = report["branch_data_validation"]["metrics"]
        verified_steps = store.verify_all()
        report["checkpoint_store_verified_steps"] = list(verified_steps)
        report["checkpoint_store_verified"] = len(verified_steps) == len(CHECKPOINT_KEYS)
        report["full_task_completed"] = len(completed) == args.slots
        report["passed"] = bool(
            report["resident"] == args.slots
            and report["peak_active"] == args.slots
            and report["completed"] == args.slots
            and report["independently_verified"] == args.slots
            and report["distinct_targets"] == args.slots
            and report["restore"]["passed"]
            and report["checkpoint_store_verified"]
        )
        report["stage"] = "finished" if report["passed"] else "incomplete"
        report["wall_s"] = time.monotonic() - started
        report["process_peak_ram_gib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2
        if static_audit_recorder is not None:
            static_audit_recorder.close()
            from demo.fire_rescue.branching.parallel.shared_static import audited_group_names

            recorded_names, recorded_digest = audited_group_names(static_audit)
            report["recorded_static_audit_sha256"] = recorded_digest
            report["recorded_static_groups"] = len(recorded_names)
        else:
            common_static.validate_immutable()
            report["shared_static_guard"] = common_static.report()
            common_static.close()
        save()
    return 0 if report["passed"] else 1


def _supervise(args) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-m", "demo.factory_benchmark.checkpoint_branches",
               *(sys.argv[1:] if args.argv is None else args.argv), "--worker"]
    _write_json(output / "launch.json", {"command": command})
    environment = dict(os.environ, PYTHONUNBUFFERED="1", MALLOC_ARENA_MAX="2",
                       OPENBLAS_NUM_THREADS="8", CUBLAS_WORKSPACE_CONFIG=":4096:8")
    environment["EAI_FACTORY_GPU_PHYSICS"] = "1"
    with (output / "worker.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
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
        "passed": False, "stage": "failed", "error": "worker produced no report"
    }
    workers = [sample.get("workers", {}).get(str(child.pid), {}) for sample in samples]
    report["resources"] = {
        "peak_rss_bytes": max((item.get("rss", 0) for item in workers), default=0),
        "peak_pss_bytes": max((item.get("pss", 0) for item in workers), default=0),
        "peak_gpu_used_bytes": max((sample.get("gpu_used", 0) for sample in samples), default=0),
        "min_gpu_free_bytes": min((sample.get("gpu_free", 0) for sample in samples), default=0),
        "min_host_available_bytes": min((sample.get("host_available", 0) for sample in samples), default=0),
    }
    report["worker_exit"] = status
    if abort_reason or status != 0 or not report.get("passed"):
        report.update(passed=False, supervisor_error=abort_reason,
                      last_worker_stage=report.get("stage"), stage="failed")
    _write_json(path, report)
    print(json.dumps({key: report.get(key) for key in (
        "passed", "stage", "requested", "resident", "peak_active", "completed",
        "queued", "independently_verified", "worker_exit", "supervisor_error")},
        ensure_ascii=False))
    return 0 if status == 0 and report.get("passed") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--static-audit", type=Path, default=DEFAULT_STATIC_AUDIT)
    parser.add_argument(
        "--record-static-audit",
        action="store_true",
        help="run one complete private Factory slot and write a live static audit below --output",
    )
    parser.add_argument("--slots", type=int, default=32)
    parser.add_argument(
        "--clone-mode",
        choices=("copy", "inherit"),
        default="copy",
        help="copy keeps independent USD specs; inherit shares USD composition while retaining batched PhysX rows",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-spacing", type=float, default=32.0)
    parser.add_argument("--aerial-steps", type=int, default=128)
    parser.add_argument("--aerial-dwell-steps", type=int, default=25)
    parser.add_argument("--prefix-max-steps", type=int, default=800)
    parser.add_argument("--alignment-max-steps", type=int, default=400)
    parser.add_argument("--alignment-hold-steps", type=int, default=25)
    parser.add_argument("--ground-view-steps", type=int, default=100)
    parser.add_argument("--restore-probe-steps", type=int, default=5)
    parser.add_argument("--restore-tolerance", type=float, default=1e-5)
    parser.add_argument("--branch-max-steps", type=int, default=3600)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--host-headroom-gib", type=float, default=4.0)
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
    positive = (args.env_spacing, args.aerial_steps, args.aerial_dwell_steps,
                args.prefix_max_steps, args.alignment_max_steps,
                args.alignment_hold_steps, args.ground_view_steps,
                args.restore_probe_steps, args.restore_tolerance,
                args.branch_max_steps, args.timeout,
                args.host_headroom_gib, args.gpu_headroom_gib)
    if any(not math.isfinite(float(value)) or float(value) <= 0 for value in positive):
        parser.error("step counts, tolerances, spacing and resource limits must be positive")
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
        print(f"[Factory checkpoint branches] failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
