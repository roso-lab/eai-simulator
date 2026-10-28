"""Unified local pick/place Isaac smoke for the arm backends (UR5/Z1).

This is the rebuilt simulation host for the unified PickPlace validation.  Each
process runs exactly one host simulation.  Every trial performs one boolean
``pick`` and one boolean ``place`` through ``PickPlaceExecutor`` while the
caller (this script) owns navigation-free holding steps, records measured lift,
single attach/release, final origin error and resource cleanup.

Rebuilt 2026-10-19 for the HeteroCoord working tree.  The original script from
the 2026-10-14 validation round was never committed; this rebuild covers the
arm backend. The current HeteroCoord branch supports ``cartesian_pose`` only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for package in ("EAI", "EAI_assets", "EAI_hmrs"):
    sys.path.insert(0, str(ROOT / "source" / package))

SOURCE_FILES = (
    "tools/validation/unified_pick_place_smoke.py",
    "tools/validation/pick_place_clearance.py",
    "source/EAI/EAI/controllers/idealized_manipulation.py",
    "source/EAI/EAI/controllers/ik_controller.py",
    "source/EAI/EAI/manipulation_geometry.py",
    "task/executors/pick_place.py",
    "task/executors/pick_place_backends/arm.py",
    "algorithm/skills/pick_place/provider.py",
    "algorithm/skills/pick_place/contracts.py",
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("arm",), default="arm",
                        help="This rebuilt smoke currently hosts the arm backend")
    parser.add_argument("--arm", choices=("ur5", "z1"), default="ur5")
    parser.add_argument("--mode", choices=("cartesian_pose",), default="cartesian_pose")
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--approach-degrees", type=float, default=15.0,
                        help="Cartesian approach tilt from vertical toward +X")
    parser.add_argument("--timeout-s", type=float, default=100.0)
    parser.add_argument("--gui", action="store_true",
                        help="Show the Isaac Sim viewport while the trial runs")
    parser.add_argument("--object-position", type=float, nargs=3, metavar=("X", "Y", "Z"),
                        help="Resting object origin in world metres; support height follows Z")
    parser.add_argument("--place-offset", type=float, nargs=2, metavar=("DX", "DY"),
                        help="Horizontal place offset per trial, in metres")
    parser.add_argument("--place-position", type=float, nargs=3, metavar=("X", "Y", "Z"),
                        help="Explicit absolute world target; overrides relative place-offset")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--video", type=Path, help="Optional real Isaac RGB recording (.mp4)")
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("trials must be positive")
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error("timeout-s must be positive and finite")
    if not math.isfinite(args.approach_degrees):
        parser.error("approach-degrees must be finite")
    if args.object_position is not None and (
        not all(math.isfinite(v) for v in args.object_position) or args.object_position[2] <= 0.075
    ):
        parser.error("object-position must be finite with Z > 0.075 m (support above ground)")
    if args.place_offset is not None and not all(math.isfinite(v) for v in args.place_offset):
        parser.error("place-offset must be finite")
    if args.place_position is not None and not all(math.isfinite(v) for v in args.place_position):
        parser.error("place-position must be finite")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.video is not None:
        args.video.parent.mkdir(parents=True, exist_ok=True)
    return args


def main():
    args = parse_args()
    args.source_sha256 = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in SOURCE_FILES}
    os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    from isaaclab.app import AppLauncher
    launcher = AppLauncher(headless=not args.gui, device="cpu", enable_cameras=args.gui or args.video is not None)
    failed = False
    result = {"passed": False, "completed_trials": 0, "error": "did not run"}
    try:
        result = run(args)
    except Exception:
        failed = True
        import traceback
        traceback.print_exc()
        result = {"passed": False, "completed_trials": 0, "error": traceback.format_exc()}
    finally:
        # Kit may hard-exit during app.close(); persist the report first.
        args.report.write_text(json.dumps(result, indent=2))
        print(json.dumps({k: result.get(k) for k in
                          ("passed", "completed_trials")}, indent=2), flush=True)
        # Kit may terminate Python here, so set its native return code first.
        import omni.kit.app
        omni.kit.app.get_app().post_quit(1 if failed or not result.get("passed") else 0)
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)
    if failed or not result.get("passed"):
        raise SystemExit(1)


def run(args):
    import logging
    logging.basicConfig(level=logging.WARNING)
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    from pxr import UsdGeom

    from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
    from task.executors import PickPlaceExecutor
    from task.executors.pick_place_backends.arm import ArmPickPlaceAdapter
    from EAI.controllers.idealized_manipulation import IdealizedManipulationRuntime

    if args.arm == "ur5":
        from EAI_assets.robots.ur5_mount import (
            UR5_JOINT_NAMES, UR5_JOINT_EFFORT_LIMITS, UR5_STANDALONE_USD_PATH,
        )
        from EAI_assets.robots.ur5_robotiq import attach_robotiq_85, default_robotiq_85_usd
        model_spec = SimpleNamespace(
            model="ur5", joint_names=UR5_JOINT_NAMES,
            ee_body_names=("wrist_3_link",), gripper_joint_name="finger_joint",
        )
        # Start from the bent, downward-tool posture used by the contact fixture.
        init_joints = dict(zip(UR5_JOINT_NAMES, (0.0, -1.57, 1.2, -1.2, -1.57, 0.0)))
        init_joints["finger_joint"] = 0.0
        object_xy = (0.45, 0.0)
    else:
        from EAI_assets.robots.z1 import Z1_CFG, Z1_JOINT_NAMES, Z1_GRIPPER_JOINT_NAME
        model_spec = SimpleNamespace(
            model="z1", joint_names=Z1_JOINT_NAMES,
            ee_body_names=("link06", "gripperStator"),
            gripper_joint_name=Z1_GRIPPER_JOINT_NAME,
        )
        init_joints = dict(Z1_CFG.init_state.joint_pos)
        object_xy = (0.33, 0.0)

    timestep = 0.005
    device = os.environ.get("SMOKE_DEVICE", "cpu")
    sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=timestep, device=device))
    sim.set_camera_view((1.6, 1.6, 1.4), (0.35, 0.0, 0.35))
    contact_material = sim_utils.RigidBodyMaterialCfg(
        static_friction=0.9, dynamic_friction=0.7, restitution=0.0)
    contact_material.func("/World/ContactMaterial", contact_material)
    ground_cfg = sim_utils.CuboidCfg(size=(20, 20, 0.1),
                                     collision_props=sim_utils.CollisionPropertiesCfg(),
                                     physics_material=contact_material)
    ground_cfg.func("/World/Ground", ground_cfg, translation=(0, 0, -0.05))
    light_cfg = sim_utils.DomeLightCfg(intensity=2000)
    light_cfg.func("/World/Light", light_cfg)

    object_path = "/World/Object"
    table_top = 0.45
    pedestal_top = 0.45
    box_size = 0.05
    if args.object_position is not None:
        object_xy = tuple(args.object_position[:2])
        table_top = args.object_position[2] - box_size / 2
    # Keep the workbench outside the fixed pedestal, including near-target
    # cases. The old centred 0.7 m table intersected the robot's base.
    table_x_min = max(.23 if args.arm == "ur5" else .10,
                      object_xy[0] - (.35 if args.arm == "ur5" else .15))
    table_x_max = object_xy[0] + .35
    table_size = (table_x_max - table_x_min, .7, .05)
    table_center = ((table_x_min + table_x_max) / 2, object_xy[1], table_top - .025)
    pedestal_cfg = sim_utils.CuboidCfg(size=(0.16, 0.16, pedestal_top),
                                       collision_props=sim_utils.CollisionPropertiesCfg())
    pedestal_cfg.func("/World/Pedestal", pedestal_cfg, translation=(0, 0, pedestal_top / 2))
    table = RigidObject(RigidObjectCfg(
        prim_path="/World/Table",
        spawn=sim_utils.CuboidCfg(
            size=table_size, mass_props=sim_utils.MassPropertiesCfg(mass=10.0),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            physics_material=contact_material,
            visual_material=sim_utils.PreviewSurfaceCfg(
                diffuse_color=(0.25, 0.35, 0.45), opacity=1.0)),
        init_state=RigidObjectCfg.InitialStateCfg(pos=table_center)))
    # Only the payload is isolated during pick. Robot/table contacts remain.
    box = RigidObject(RigidObjectCfg(
        prim_path=object_path,
        spawn=sim_utils.CuboidCfg(
            size=(box_size,) * 3, mass_props=sim_utils.MassPropertiesCfg(mass=0.01),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            physics_material=contact_material,
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.15, 0.65, 0.25))),
        init_state=RigidObjectCfg.InitialStateCfg(
            pos=(object_xy[0], object_xy[1], table_top + box_size / 2 + 0.005))))

    camera = None
    if args.video is not None:
        from isaaclab.sensors import Camera, CameraCfg
        camera = Camera(CameraCfg(
            prim_path="/World/AcceptanceCamera", update_period=0.0,
            width=960, height=720, data_types=["rgb"],
            spawn=sim_utils.PinholeCameraCfg(focal_length=24.0, clipping_range=(0.01, 100.0))))

    if args.arm == "ur5":
        arm_cfg = ArticulationCfg(
            prim_path="/World/ur5_1_arm",
            spawn=sim_utils.UsdFileCfg(
                usd_path=UR5_STANDALONE_USD_PATH,
                rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=True),
                articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                    fix_root_link=True, enabled_self_collisions=False)),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, pedestal_top)),
            actuators={})
        arm_cfg.init_state.joint_pos = dict(init_joints)
        arm_cfg.actuators = {
            "arm": ImplicitActuatorCfg(joint_names_expr=list(UR5_JOINT_NAMES),
                                       effort_limit_sim=dict(UR5_JOINT_EFFORT_LIMITS),
                                       velocity_limit_sim=1.0,
                                       stiffness=6000.0, damping=300.0),
            "gripper": ImplicitActuatorCfg(joint_names_expr=["finger_joint"],
                                           effort_limit_sim=2.0, velocity_limit_sim=0.8,
                                           stiffness=50.0, damping=1.0),
            "passive": ImplicitActuatorCfg(
                joint_names_expr=["right_outer_knuckle_joint", ".*inner.*joint"],
                effort_limit_sim=0.0, stiffness=0.0, damping=0.0),
        }
        arm = Articulation(arm_cfg)
        attach_robotiq_85(sim.stage, arm_path="/World/ur5_1_arm",
                          gripper_usd=default_robotiq_85_usd())
    else:
        arm_cfg = Z1_CFG.copy()
        arm_cfg.prim_path = "/World/z1_1_arm"
        arm_cfg.spawn.rigid_props.disable_gravity = True
        arm_cfg.spawn.articulation_props.enabled_self_collisions = False
        arm_cfg.init_state.joint_pos = dict(init_joints)
        arm_cfg.init_state.pos = (0.0, 0.0, pedestal_top)
        arm = Articulation(arm_cfg)

    # Preserve environment contacts. Disable gravity on all links because the
    # existing idealized controller does not include gravity compensation.
    from pxr import PhysxSchema, Usd, UsdPhysics
    arm_prim = sim.stage.GetPrimAtPath(f"/World/{args.arm}_1_arm")
    before_colliders = [p for p in Usd.PrimRange(arm_prim, Usd.TraverseInstanceProxies())
                        if p.HasAPI(UsdPhysics.CollisionAPI)]
    scene_audit = {"arm_collider_count": len(before_colliders),
                   "instance_proxy_colliders_before": sum(p.IsInstanceProxy() for p in before_colliders)}
    # Instance proxies cannot receive local attribute overrides.
    while True:
        instances = [p for p in Usd.PrimRange(arm_prim) if p.IsInstance()]
        if not instances:
            break
        for prim in instances:
            prim.SetInstanceable(False)
    for prim in Usd.PrimRange(sim.stage.GetPrimAtPath(f"/World/{args.arm}_1_arm")):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            api = PhysxSchema.PhysxRigidBodyAPI.Get(sim.stage, prim.GetPath())
            if not api:
                api = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            api.CreateDisableGravityAttr().Set(True)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            api = UsdPhysics.CollisionAPI(prim)
            attr = api.GetCollisionEnabledAttr()
            if not attr or not attr.IsValid():
                attr = api.CreateCollisionEnabledAttr()
            attr.Set(True)

    colliders = [p for p in Usd.PrimRange(arm_prim, Usd.TraverseInstanceProxies())
                 if p.HasAPI(UsdPhysics.CollisionAPI)]
    scene_audit["enabled_arm_colliders"] = sum(
        bool(UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get()) for p in colliders)
    if not colliders or scene_audit["enabled_arm_colliders"] != len(colliders):
        raise RuntimeError("Robot environment collision geometry must remain enabled")

    # CPU articulations are put to sleep by PhysX once every joint is quiet;
    # sleeping links stop integrating and ignore position-target writes, which
    # stalls the manipulation trajectory. Disable sleeping and wake the view
    # every tick.
    payload_root = sim.stage.GetPrimAtPath(object_path)
    def payload_filter_snapshot():
        relation = payload_root.GetRelationship("physics:filteredPairs")
        return {
            "has_api": payload_root.HasAPI(UsdPhysics.FilteredPairsAPI),
            "authored_targets": bool(relation and relation.HasAuthoredTargets()),
            "targets": [str(p) for p in relation.GetTargets()] if relation else [],
        }
    original_payload_filter = payload_filter_snapshot()
    payload_api = PhysxSchema.PhysxRigidBodyAPI.Get(sim.stage, payload_root.GetPath())
    if not payload_api:
        payload_api = PhysxSchema.PhysxRigidBodyAPI.Apply(payload_root)
    payload_api.CreateSleepThresholdAttr().Set(0.0)
    payload_colliders = [UsdPhysics.CollisionAPI(p) for p in Usd.PrimRange(payload_root)
                         if p.HasAPI(UsdPhysics.CollisionAPI)]
    if not payload_colliders or not all(c.GetCollisionEnabledAttr().Get() for c in payload_colliders):
        raise RuntimeError("Payload must start with enabled support collision")
    # Kit initializes PhysX before we can write the articulation reset pose.
    # Do not let the temporary USD zero pose hit the payload during reset.
    for collider in payload_colliders:
        collider.GetCollisionEnabledAttr().Set(False)

    arm_root = sim.stage.GetPrimAtPath(f"/World/{args.arm}_1_arm")
    articulation_api = PhysxSchema.PhysxArticulationAPI.Get(sim.stage, arm_root.GetPath())
    if not articulation_api:
        articulation_api = PhysxSchema.PhysxArticulationAPI.Apply(arm_root)
    articulation_api.CreateSleepThresholdAttr().Set(0.0)

    sim.reset()
    video_writer = None
    if camera is not None:
        import imageio.v2 as imageio
        camera.set_world_poses_from_view(
            torch.tensor([[1.40, 0.90, 1.35]], device=device),
            torch.tensor([[0.35, 0.0, 0.75]], device=device))
        video_writer = imageio.get_writer(str(args.video), fps=20, codec="libx264", quality=8)
    arm.write_joint_state_to_sim(arm.data.default_joint_pos,
                                 torch.zeros_like(arm.data.default_joint_pos))
    # Writing state does not update Isaac's initially zero drive targets.
    # Match MultiRobotDirectEnv._reset_idx before the first physics step.
    arm.set_joint_position_target(arm.data.default_joint_pos.clone())
    arm.set_joint_velocity_target(torch.zeros_like(arm.data.default_joint_pos))
    arm.write_data_to_sim()
    box.write_root_pose_to_sim(box.data.default_root_state[:, :7])
    box.write_root_velocity_to_sim(torch.zeros_like(box.data.default_root_state[:, 7:]))
    for collider in payload_colliders:
        collider.GetCollisionEnabledAttr().Set(True)
    scene_audit["fixed_base"] = arm.is_fixed_base
    scene_audit["jacobian_shape"] = list(arm.root_physx_view.get_jacobians().shape)
    from EAI.controllers.ik_controller import resolve_jacobian_row
    ee_id = arm.find_bodies(list(model_spec.ee_body_names))[0][0]
    scene_audit["ee_jacobian_row"] = resolve_jacobian_row(
        arm, ee_id, model_spec.ee_body_names, row_count=scene_audit["jacobian_shape"][1])
    print("SCENE_AUDIT", json.dumps(scene_audit), flush=True)

    host = SimpleNamespace(num_envs=1, scene=SimpleNamespace(articulations={
        f"{args.arm}_1_arm": arm}), sim=sim)
    runtime = IdealizedManipulationRuntime(host, {f"{args.arm}_1": model_spec})

    decimation = int(os.environ.get("SMOKE_DECIMATION", "4"))
    if decimation < 1:
        raise ValueError("SMOKE_DECIMATION must be positive")
    wake = getattr(arm.root_physx_view, "wake_up", None)
    phase_events = []
    gripper_samples = []
    gripper_id = arm.find_joints([model_spec.gripper_joint_name], preserve_order=True)[0][0]
    physics_steps = 0
    last_active_sample = None
    from tools.validation.pick_place_clearance import ArmTableClearance
    clearance = ArmTableClearance(sim.stage, arm, table_center, table_size)
    initial_arm_joints = arm.data.default_joint_pos.clone()
    arm_joint_ids = arm.find_joints(list(model_spec.joint_names), preserve_order=True)[0]
    startup_max_drift_rad = 0.0
    payload_filter_steps = 0

    def host_step():
        nonlocal physics_steps, last_active_sample, startup_max_drift_rad, payload_filter_steps
        # Runtime rates and slew limits use physics dt, so invoke it at every
        # physics tick, as MultiRobotDirectEnv does, including decimated hosts.
        for _ in range(decimation):
            before = runtime.state(f"{args.arm}_1").value
            runtime.run_post_apply()
            active_state = runtime._states.get(f"{args.arm}_1")
            if active_state is not None and active_state.payload_collision_filter is not None:
                payload_filter_steps += 1
            after = runtime.state(f"{args.arm}_1").value
            if before != after:
                binding = runtime._binding(f"{args.arm}_1", disable_collisions=False)
                p, q = runtime._end_effector_pose(binding)
                event = {"simulation_time_s": sim.current_time, "from": before, "to": after,
                         "ee_position_w": p, "ee_orientation_wxyz": q,
                         "gripper_position_rad": float(arm.data.joint_pos[0, gripper_id])}
                tool = runtime._states[f"{args.arm}_1"].grasp_tool
                if tool is not None:
                    tcp, tcp_q = tool.tcp_pose(p, q)
                    event.update(tcp_position_w=tcp, tcp_orientation_wxyz=tcp_q)
                    if before == "grasp" and after == "lift":
                        obj_p, _ = runtime._rigid_body_pose(object_path)
                        event["tcp_object_center_error_m"] = math.dist(tcp, obj_p)
                phase_events.append(event)
            if wake is not None:
                wake()
            arm.write_data_to_sim()
            sim.step(render=args.gui or (camera is not None and physics_steps % 10 == 0))
            arm.update(timestep)
            box.update(timestep)
            physics_steps += 1
            clearance.sample(sim.current_time)
            if not runtime._states:
                startup_max_drift_rad = max(startup_max_drift_rad, float(
                    (arm.data.joint_pos[:, arm_joint_ids] - initial_arm_joints[:, arm_joint_ids]).abs().max()))
            if physics_steps % 10 == 0:
                state = runtime._states.get(f"{args.arm}_1")
                gripper_samples.append({
                    "simulation_time_s": sim.current_time,
                    "phase": None if state is None else state.phase.value,
                    "position_rad": float(arm.data.joint_pos[0, gripper_id]),
                    "command_rad": None if state is None else state.gripper_command_rad,
                })
                if video_writer is not None:
                    camera.update(10 * timestep, force_recompute=True)
                    video_writer.append_data(camera.data.output["rgb"][0, ..., :3].cpu().numpy())
            if physics_steps % 50 == 0:
                state = runtime._states.get(f"{args.arm}_1")
                if state is not None and state.operation is not None:
                    binding = runtime._binding(f"{args.arm}_1", disable_collisions=False)
                    p, q = runtime._end_effector_pose(binding)
                    if state.cartesian is not None:
                        goal, orientation = state.cartesian_goal_position_w, state.cartesian_goal_orientation_wxyz
                    else:
                        goal, orientation = runtime._target_world_pose(binding, state)
                    from EAI.manipulation_geometry import _quat_angle_rad
                    last_active_sample = {
                        "operation": state.operation, "phase": state.phase.value,
                        "simulation_time_s": sim.current_time,
                        "ee_position_world_m": p, "goal_world_m": goal,
                        "position_error_m": None if goal is None else math.dist(p, goal),
                        "orientation_error_rad": None if orientation is None else _quat_angle_rad(q, orientation),
                        "joint_position_rad": arm.data.joint_pos[0].tolist(),
                    }

    for _ in range(60):
        host_step()

    # Let the payload settle on the table before any skill runs.
    for _ in range(40):
        host_step()
    initial_object_position, _ = runtime._rigid_body_pose(object_path)
    print("PAYLOAD SETTLED", [round(v, 4) for v in initial_object_position], flush=True)

    robot_id = f"{args.arm}_1"
    name = robot_id
    attach_releases = {"attaches": 0, "releases": 0, "last_held": False}

    def track_hold_transitions():
        held = runtime.held_object(name) is not None
        if held and not attach_releases["last_held"]:
            attach_releases["attaches"] += 1
        if not held and attach_releases["last_held"]:
            attach_releases["releases"] += 1
        attach_releases["last_held"] = held

    def stepping_advance():
        host_step()
        track_hold_transitions()

    executor = PickPlaceExecutor(advance=stepping_advance, simulation_time=lambda: sim.current_time,
        physical_object_experiment_authorization="algorithm/heterocoord/ADAPTATION_PUBLICATION_20261028.zh-CN.md#bounded-host")
    adapter = ArmPickPlaceAdapter(robot_id, runtime, position_tolerance_m=0.035)
    executor.register_adapter(adapter)


    def sampled_base():
        position = arm.data.root_pos_w[0].tolist()
        quat_wxyz = arm.data.root_quat_w[0].tolist()
        return (tuple(float(v) for v in position),
                (float(quat_wxyz[1]), float(quat_wxyz[2]), float(quat_wxyz[3]), float(quat_wxyz[0])))

    def request(request_id, operation, target, *, cartesian):
        base_position, base_orientation = sampled_base()
        parameters = {
            "operation": operation,
            "object_ref": object_path,
            "base_position_world_m": base_position,
            "base_orientation_xyzw": base_orientation,
            "world_state_seq": 1,
            "simulation_time_s": sim.current_time,
            "timeout_s": args.timeout_s,
        }
        if cartesian:
            tilt = math.radians(args.approach_degrees)
            approach = (math.sin(tilt), 0.0, -math.cos(tilt))
            half = math.sin(tilt / 2.0)
            parameters.update({
                "approach_mode": "cartesian_pose",
                "grasp_pose_object": {
                    "position_m": (0.0, 0.0, 0.0),
                    "orientation_xyzw": (0.0, -half, 0.0, math.cos(tilt / 2.0)),
                },
                "approach_direction_world": approach,
                "pregrasp_distance_m": 0.08,
                "retreat_distance_m": 0.14,
                "approach_speed_m_s": 0.05,
            })
        return SkillRequest(request_id, PICK_PLACE_SKILL_ID, robot_id,
                            target_pose=tuple(target), parameters=parameters)

    def proxy_visual_world_z():
        state = runtime._states.get(name)
        held = None if state is None else state.held
        if held is None or held.proxy_path is None:
            return None
        visual = held.proxy_path + "/Visual"
        prim = sim.stage.GetPrimAtPath(visual)
        if not prim or not prim.IsValid():
            return None
        world = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        return float(world.ExtractTranslation()[2])

    trials = []
    cartesian = args.mode == "cartesian_pose"
    for trial in range(1, args.trials + 1):
        attach_releases.update(attaches=0, releases=0, last_held=False)
        phase_events.clear()
        gripper_samples.clear()
        pick_target = tuple(runtime._rigid_body_pose(object_path)[0])
        drift = 0.025 if trial % 2 == 0 else -0.025
        dx, dy = (drift, 0.01) if args.place_offset is None else args.place_offset
        place_target = (tuple(args.place_position) if getattr(args, "place_position", None) is not None
                        else (pick_target[0] + dx, pick_target[1] + dy, pick_target[2]))

        last_active_sample = None
        pick_start_time = sim.current_time
        pick_ok = executor.execute(request(
            f"trial-{trial}/pick", "pick", pick_target, cartesian=cartesian))
        pick_duration = sim.current_time - pick_start_time
        pick_diagnostic = last_active_sample
        lift_m = None
        hold_steps = 0
        if pick_ok:
            floor_z = pick_target[2]
            lift_m = proxy_visual_world_z() - floor_z
            for _ in range(120):
                stepping_advance()
                hold_steps += 1
                z = proxy_visual_world_z()
                if z is not None and floor_z is not None:
                    lift_m = min(lift_m, z - floor_z)
        place_ok = False
        place_diagnostic = None
        place_duration = None
        if pick_ok:
            last_active_sample = None
            place_start_time = sim.current_time
            place_ok = executor.execute(request(
                f"trial-{trial}/place", "place", place_target, cartesian=cartesian))
            place_duration = sim.current_time - place_start_time
            place_diagnostic = last_active_sample
        for _ in range(120):
            stepping_advance()
        final_position, _ = runtime._rigid_body_pose(object_path)
        final_error = math.dist(final_position, place_target)

        state = runtime._states.get(name)
        tool = None if state is None else state.grasp_tool
        open_angle, closed_angle = ((tool.open_position_rad, tool.closed_position_rad) if tool is not None
                                    else ((0.0, 0.8) if args.arm == "ur5" else (-0.8, 0.0)))
        low, high = sorted((open_angle, closed_angle))
        closing = [s for s in gripper_samples if s["phase"] == "grasp"
                   and low + 0.02 < s["position_rad"] < high - 0.02]
        opening = [s for s in gripper_samples if s["phase"] == "release"
                   and low + 0.02 < s["position_rad"] < high - 0.02]
        closed_errors = [abs(s["position_rad"] - closed_angle) for s in gripper_samples
                         if s["phase"] == "attached_hold"]
        final_open_error = abs(float(arm.data.joint_pos[0, gripper_id]) - open_angle)
        # Animation acceptance checks the measured closing/opening trajectory
        # and its endpoints. Keep the full hold transient peak separately.
        gripper_animation_ok = (len(closing) >= 3 and len(opening) >= 3 and bool(closed_errors)
                                and closed_errors[-1] <= 0.02 and final_open_error <= 0.02)
        record = {
            "trial": trial,
            "mode": args.mode,
            "pick": bool(pick_ok),
            "place": bool(place_ok),
            "pick_duration_s": pick_duration,
            "place_duration_s": place_duration,
            "pick_last_active_sample": pick_diagnostic,
            "place_last_active_sample": place_diagnostic,
            "hold_steps": hold_steps,
            "lift_m": None if lift_m is None else round(lift_m, 6),
            "final_origin_error_m": round(final_error, 6),
            "attaches": attach_releases["attaches"],
            "releases": attach_releases["releases"],
            "phase_after": None if state is None else state.phase.value,
            "last_error": runtime.last_error(name),
            "object_owner_cleared": object_path not in runtime._object_owners,
            "resources_cleared": not executor._resources.snapshot()["resources"],
            "proxy_cleared": not runtime._owned_proxy_paths,
            "source_collision_restored": all(bool(c.GetCollisionEnabledAttr().Get()) for c in payload_colliders),
            "source_dynamic_restored": not bool(UsdPhysics.RigidBodyAPI(payload_root).GetKinematicEnabledAttr().Get()),
            "source_gravity_restored": not bool(payload_api.GetDisableGravityAttr().Get()),
            "payload_filter_restored": (payload_filter_snapshot() == original_payload_filter
                                        and (state is None or state.payload_collision_filter is None)),
            "payload_filter_snapshot_after": payload_filter_snapshot(),
            "payload_filter_steps": payload_filter_steps,
            "pick_target_world_m": pick_target,
            "place_target_world_m": place_target,
            "final_position_world_m": final_position,
            "phase_events": list(phase_events),
            "gripper_samples": list(gripper_samples),
            "gripper_animation": {
                "passed": gripper_animation_ok,
                "closing_intermediate_samples": len(closing),
                "opening_intermediate_samples": len(opening),
                "max_hold_closed_error_rad": max(closed_errors) if closed_errors else None,
                "final_hold_closed_error_rad": closed_errors[-1] if closed_errors else None,
                "final_open_error_rad": final_open_error,
            },
            "grasp_tool": None if tool is None else vars(tool),
            "tcp_alignment_passed": any(
                e.get("tcp_object_center_error_m", float('inf')) <= .008 for e in phase_events),
        }
        record["ok"] = bool(
            pick_ok and place_ok
            and (lift_m is not None and lift_m >= 0.05)
            and final_error <= 0.035
            and attach_releases["attaches"] == 1
            and attach_releases["releases"] == 1
            and record["object_owner_cleared"]
            and record["phase_after"] == "idle" and not record["last_error"]
            and gripper_animation_ok
            and record["tcp_alignment_passed"]
            and all(record[key] for key in ("resources_cleared", "proxy_cleared", "source_collision_restored",
                                            "source_dynamic_restored", "source_gravity_restored",
                                            "payload_filter_restored"))
        )
        trials.append(record)
        print(json.dumps({key: value for key, value in record.items()
                          if key not in ("phase_events", "gripper_samples")}), flush=True)
        if not record["ok"]:
            break

    passed = len(trials) == args.trials and all(record["ok"] for record in trials)
    if video_writer is not None:
        video_writer.close()
    source_unchanged = all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
                           for path, digest in args.source_sha256.items())
    clearance_report = clearance.report()
    scene_audit["enabled_arm_colliders_after"] = sum(
        bool(UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get()) for p in colliders)
    scene_audit["startup_max_arm_drift_rad"] = startup_max_drift_rad
    scene_audit["startup_hold_passed"] = startup_max_drift_rad <= .02
    passed = (passed and source_unchanged and clearance_report["passed"]
              and scene_audit["startup_hold_passed"]
              and scene_audit["enabled_arm_colliders_after"] == len(colliders))
    return {
        "schema": "eai.unified_pick_place_smoke.rebuild.v1",
        "passed": passed,
        "requested_trials": args.trials,
        "completed_trials": len(trials),
        "gui": bool(args.gui),
        "backend": "arm",
        "arm": args.arm,
        "mode": args.mode,
        "approach_degrees": args.approach_degrees if cartesian else None,
        "device": device,
        "physics_dt_s": timestep,
        "scene_parameters": {
            "requested_object_position_world_m": args.object_position,
            "settled_initial_object_position_world_m": initial_object_position,
            "arm_base_position_world_m": arm.data.root_pos_w[0].tolist(),
            "table_top_world_m": table_top,
            "table_center_world_m": table_center,
            "table_size_m": table_size,
            "place_offset_m": args.place_offset,
            "requested_place_position_world_m": getattr(args, "place_position", None),
            "joint_names": arm.joint_names,
            "joint_limits_rad": arm.data.soft_joint_pos_limits[0].tolist(),
        },
        "scene_audit": scene_audit,
        "arm_table_clearance": clearance_report,
        "evidence_scope": "idealized_follow_proxy_pick_place; not contact grasp",
        "video": str(args.video) if args.video is not None else None,
        "transparent_support_for_video": False,
        "trials": trials,
        "source_unchanged_during_run": source_unchanged,
        "source_sha256": args.source_sha256,
    }


if __name__ == "__main__":
    main()
