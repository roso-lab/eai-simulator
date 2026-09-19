"""Headless UR5/2F-85 approach, grasp, lift, transfer and release experiment.

Run with Isaac Lab Python. Only initialization writes body poses; task execution
uses bounded joint drives and contact. The payload is never welded or teleported.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--host", choices=("fixed", "scout"), default="fixed")
    parser.add_argument("--mass", type=float, default=0.2)
    parser.add_argument("--open-control", action="store_true")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--gripper-torque", type=float, default=2.0)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--clean-reset", action="store_true", default=True)
    parser.add_argument("--legacy-reset", action="store_false", dest="clean_reset")
    parser.add_argument("--scout-box-inertia", action="store_true")
    parser.add_argument("--arm-stiffness", type=float, default=6000.0)
    parser.add_argument("--arm-damping", type=float, default=300.0)
    parser.add_argument("--solver-iterations", type=int, default=16)
    parser.add_argument("--host-height", type=float)
    parser.add_argument("--table-length", type=float)
    args = parser.parse_args()
    if args.table_length is None:
        args.table_length = 0.2 if args.host == "scout" else 0.3
    if args.report_dir.exists():
        parser.error("Choose a new report directory")
    if not math.isfinite(args.mass) or not math.isfinite(args.gripper_torque) or args.mass <= 0 or args.gripper_torque <= 0:
        parser.error("Positive mass and gripper torque required")
    if any(not math.isfinite(value) or value <= 0 for value in
           (args.table_length, args.arm_stiffness, args.arm_damping)) or not 1 <= args.solver_iterations <= 255:
        parser.error("Positive finite dimensions/gains and 1..255 solver iterations required")
    if args.host_height is not None and (not math.isfinite(args.host_height) or args.host_height <= 0):
        parser.error("Positive finite host height required")
    if args.scout_box_inertia and args.host != "scout":
        parser.error("Box inertia diagnostic requires Scout")
    args.report_dir.mkdir(parents=True)
    args.runner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.helper_sha256 = hashlib.sha256((ROOT / "source/EAI_assets/EAI_assets/robots/ur5_robotiq.py").read_bytes()).hexdigest()
    os.environ["EAI_ROBOTIQ_2F85_USD"] = str(
        (args.asset_root / "Robotiq/2F-85/Robotiq_2F_85_edit.usd").expanduser().resolve())
    from isaaclab.app import AppLauncher
    launcher = AppLauncher(headless=not args.gui, device="cpu", enable_cameras=True)
    failed = False
    try:
        run(args)
    except Exception:
        failed = True
        import traceback
        failure = traceback.format_exc()
        (args.report_dir / "error.txt").write_text(failure)
        print(failure, flush=True)
    finally:
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)
    if failed:
        raise SystemExit(1)


def run(args):
    import torch
    import carb
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.utils.math import quat_apply, quat_mul
    from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade, PhysxSchema
    from EAI_assets.robots.ur5_mount import UR5_STANDALONE_USD_PATH, UR5_JOINT_NAMES, UR5_JOINT_EFFORT_LIMITS
    from EAI_assets.robots.ur5_robotiq import (
        attach_robotiq_85, default_robotiq_85_usd, robotiq_85_mount_metadata,
    )
    from EAI_hmrs.controller_loader import load_controller_attr
    from EAI.hmrs_ros.manipulator_omnigraph import ManipulatorModelSpec, ManipulatorPoseCommand, ManipulatorActiveCommand
    from EAI.physics.articulation_reset import capture_root_poses, set_spawn_root_defaults
    from mobile_mount_checks import box_separation, support_polygon_margin

    timestep = 1 / 240
    sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
        dt=timestep, device="cpu", physx=sim_utils.PhysxCfg(
            solver_type=1, min_position_iteration_count=args.solver_iterations, min_velocity_iteration_count=4)))
    carb.settings.get_settings().set_bool("/physics/disableContactProcessing", False)
    sim.set_camera_view((1.8, 1.8, 1.5), (0.4, 0, 0.5))
    ground_cfg = sim_utils.CuboidCfg(size=(20, 20, 0.1), collision_props=sim_utils.CollisionPropertiesCfg())
    ground_cfg.func("/World/Ground", ground_cfg, translation=(0, 0, -0.05))
    light_cfg = sim_utils.DomeLightCfg(intensity=2000)
    light_cfg.func("/World/Light", light_cfg)
    host = None
    if args.host == "scout":
        from EAI_assets.robots.scout import SCOUT_CFG
        from EAI_assets.robots.ur5_mount import UR5_MOUNT_PROFILES, build_mounted_ur5_asset_cfg
        host_cfg = SCOUT_CFG.copy()
        host_cfg.prim_path = "/World/Host"
        if args.host_height is not None:
            host_cfg.init_state.pos = (0.0, 0.0, args.host_height)
        host_cfg.actuators["base_wheels"].effort_limit_sim = 20.0
        host_cfg.actuators["base_wheels"].damping = 100.0
        host = Articulation(host_cfg)
        profile = replace(UR5_MOUNT_PROFILES["scout"], mass_scale=1.0, enable_self_collisions=True)
        arm_cfg = build_mounted_ur5_asset_cfg("/World/Host_arm", profile)
        arm_path = "/World/Host_arm"
    else:
        arm_path = "/World/Arm"
        arm_cfg = ArticulationCfg(prim_path=arm_path,
            spawn=sim_utils.UsdFileCfg(usd_path=UR5_STANDALONE_USD_PATH,
                articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                    fix_root_link=True, enabled_self_collisions=True)),
            init_state=ArticulationCfg.InitialStateCfg(pos=(0, 0, 0.4)), actuators={})
    arm_cfg.init_state.joint_pos = dict(zip(UR5_JOINT_NAMES, (0, -1.2, 1.7, -2.07, -1.57, 0)))
    arm_cfg.init_state.joint_pos["finger_joint"] = 0.0
    arm_cfg.actuators = {
        "arm": ImplicitActuatorCfg(joint_names_expr=list(UR5_JOINT_NAMES),
            effort_limit_sim=dict(UR5_JOINT_EFFORT_LIMITS),
            velocity_limit_sim=1.0, stiffness=args.arm_stiffness, damping=args.arm_damping),
        "gripper": ImplicitActuatorCfg(joint_names_expr=["finger_joint"],
            effort_limit_sim=args.gripper_torque, velocity_limit_sim=0.8, stiffness=50.0, damping=1.0),
        "passive": ImplicitActuatorCfg(joint_names_expr=["right_outer_knuckle_joint", ".*inner.*joint"],
            effort_limit_sim=0.0, stiffness=0.0, damping=0.0),
    }
    arm = Articulation(arm_cfg)
    if args.host == "fixed":
        attach_robotiq_85(sim.stage, arm_path=arm_path, gripper_usd=default_robotiq_85_usd())
    mounting = robotiq_85_mount_metadata(sim.stage, arm_path=arm_path)
    material_cfg = sim_utils.RigidBodyMaterialCfg(static_friction=0.8, dynamic_friction=0.6,
                                               restitution=0.0, friction_combine_mode="min")
    material_cfg.func("/World/ContactMaterial", material_cfg)
    UsdShade.MaterialBindingAPI.Apply(sim.stage.GetPrimAtPath(mounting["root_path"])).Bind(
        UsdShade.Material(sim.stage.GetPrimAtPath("/World/ContactMaterial")),
        bindingStrength=UsdShade.Tokens.strongerThanDescendants, materialPurpose="physics")
    for prim in Usd.PrimRange(sim.stage.GetPrimAtPath(arm_path), Usd.TraverseInstanceProxies()):
        if prim.HasAPI(UsdPhysics.CollisionAPI) and not prim.IsInstanceProxy():
            sim_utils.bind_physics_material(str(prim.GetPath()), "/World/ContactMaterial")
            PhysxSchema.PhysxCollisionAPI.Apply(prim).CreateContactOffsetAttr().Set(0.001)
        if prim.IsA(UsdPhysics.RevoluteJoint):
            UsdPhysics.DriveAPI.Apply(prim, "angular").CreateTypeAttr().Set("force")
    fingertip_materials = {}
    for prim in Usd.PrimRange(sim.stage.GetPrimAtPath(mounting["root_path"]), Usd.TraverseInstanceProxies()):
        if prim.HasAPI(UsdPhysics.CollisionAPI) and "inner_finger" in str(prim.GetPath()):
            material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial("physics")
            if not material or str(material.GetPath()) != "/World/ContactMaterial":
                raise RuntimeError(f"Missing fingertip physics material: {prim.GetPath()}")
            fingertip_materials[str(prim.GetPath())] = str(material.GetPath())
    if len(fingertip_materials) < 2:
        raise RuntimeError("Expected opposing fingertip colliders")
    size = (0.05, 0.06, 0.15)
    payload = RigidObject(RigidObjectCfg(prim_path="/World/Payload",
        spawn=sim_utils.CuboidCfg(size=size, mass_props=sim_utils.MassPropertiesCfg(mass=args.mass),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False, max_depenetration_velocity=1.0),
            collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=0.001, rest_offset=0),
            physics_material=material_cfg, activate_contact_sensors=True),
        init_state=RigidObjectCfg.InitialStateCfg(pos=(1, 1, 0.03))))
    table = RigidObject(RigidObjectCfg(prim_path="/World/Table",
        spawn=sim_utils.CuboidCfg(size=(args.table_length, 0.7, 0.1), mass_props=sim_utils.MassPropertiesCfg(mass=10),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
            collision_props=sim_utils.CollisionPropertiesCfg(), physics_material=material_cfg),
        init_state=RigidObjectCfg.InitialStateCfg(pos=(1, 1, 0.1))))
    initial_arm_poses = capture_root_poses(sim.stage, [f"{arm_path}/base_link"])
    if args.scout_box_inertia:
        mass_api = UsdPhysics.MassAPI(sim.stage.GetPrimAtPath("/World/Host/inertial_link"))
        length, width, height = (0.925, 0.38, 0.21)
        mass_api.GetDiagonalInertiaAttr().Set(Gf.Vec3f(40 / 12 * (width ** 2 + height ** 2),
            40 / 12 * (length ** 2 + height ** 2), 40 / 12 * (length ** 2 + width ** 2)))
    sim.reset()
    reset_diagnostic = {"arm": arm.data.root_state_w[0].tolist(),
                        "host": None if host is None else host.data.root_state_w[0].tolist()}
    (args.report_dir / "reset_diagnostic.json").write_text(json.dumps(reset_diagnostic, indent=2))
    carb.settings.get_settings().set_bool("/physics/disableContactProcessing", False)
    arm_ids, _ = arm.find_joints(list(UR5_JOINT_NAMES), preserve_order=True)
    grip_ids, _ = arm.find_joints("finger_joint")
    wrist_id = arm.find_bodies("wrist_3_link")[0][0]
    controller_type = load_controller_attr("traditional/manipulator_ik/manipulator_ik.py", "ManipulatorIkControllerCfg")
    controller_path = Path(sys.modules[controller_type.__module__].__file__)
    controller_source = {"path": str(controller_path),
                         "sha256": hashlib.sha256(controller_path.read_bytes()).hexdigest()}
    controller = controller_type(model_spec=ManipulatorModelSpec("ur5_robotiq", UR5_JOINT_NAMES,
                                ("wrist_3_link",), "finger_joint"), max_delta_rad=0.04)
    environment = SimpleNamespace(scene=SimpleNamespace(articulations={"host_arm": arm}))
    contact_paths = [str(prim.GetPath()) for prim in sim.stage.Traverse()
                     if prim.HasAPI(UsdPhysics.RigidBodyAPI) and str(prim.GetPath()) != "/World/Payload"]
    contact_paths.append("/World/Ground")
    left_contacts = [index for index, path in enumerate(contact_paths)
                     if path.rsplit("/", 1)[-1] in {"left_inner_finger", "left_outer_finger"}]
    right_contacts = [index for index, path in enumerate(contact_paths)
                      if path.rsplit("/", 1)[-1] in {"right_inner_finger", "right_outer_finger"}]
    table_contact = contact_paths.index("/World/Table")
    other_contacts = [index for index in range(len(contact_paths))
                      if index not in {*left_contacts, *right_contacts, table_contact}]
    view = arm._physics_sim_view.create_rigid_contact_view(
        "/World/Payload", contact_paths, max_contact_data_count=1024)
    def step():
        if host is not None:
            host.set_joint_velocity_target(torch.zeros_like(host.data.joint_pos))
            host.write_data_to_sim()
        arm.write_data_to_sim()
        sim.step(render=args.gui)
        arm.update(timestep)
        payload.update(timestep)
        table.update(timestep)
        if host is not None:
            host.update(timestep)
    arm.write_joint_state_to_sim(arm.data.default_joint_pos, torch.zeros_like(arm.data.default_joint_pos))
    if args.clean_reset:
        root_poses = {path: next(iter(initial_arm_poses.values())) for path in arm.root_physx_view.prim_paths}
        set_spawn_root_defaults(arm, root_poses)
        arm.write_root_state_to_sim(arm.data.default_root_state)
        if host is not None:
            host.write_root_state_to_sim(host.data.default_root_state)
            host.write_joint_state_to_sim(host.data.default_joint_pos, torch.zeros_like(host.data.default_joint_pos))
        sim.forward()
    arm.set_joint_position_target(arm.data.default_joint_pos)
    for _ in range(480):
        step()
    print(json.dumps({"fixed_base": arm.is_fixed_base, "bodies": arm.body_names, "joints": arm.joint_names,
                      "wrist_position": arm.data.body_pos_w[0, wrist_id].tolist(),
                      "wrist_quat": arm.data.body_quat_w[0, wrist_id].tolist(),
                      "masses": arm.root_physx_view.get_masses().tolist(),
                      "gripper_paths": mounting}), flush=True)
    if host is not None:
        quaternion = host.data.root_quat_w[0]
        tilt = math.acos(max(-1, min(1, 1 - 2 * float(quaternion[1] ** 2 + quaternion[2] ** 2))))
        speed = float(torch.linalg.vector_norm(host.data.root_lin_vel_w[0]))
        preflight = {"host": args.host, "host_pose": host.data.root_state_w[0, :7].tolist(),
            "tilt_rad": tilt, "linear_speed_m_s": speed,
            "host_body_masses_kg": host.root_physx_view.get_masses()[0].tolist(),
            "arm_body_masses_kg": arm.root_physx_view.get_masses()[0].tolist(),
            "settled": tilt < 0.15 and speed < 0.05, "runner_sha256": args.runner_sha256}
        (args.report_dir / "preflight.json").write_text(json.dumps(preflight, indent=2, allow_nan=False) + "\n")
        if not preflight["settled"]:
            sim.stage.Export(str(args.report_dir / "assembly.usda"))
            raise RuntimeError("Mobile host did not settle upright; grasp was not started")
    wrist_start = arm.data.body_pos_w[:, wrist_id].clone()
    wrist_quat = arm.data.body_quat_w[:, wrist_id].clone()
    grasp_offset = 0.15
    grasp_depth = 0.015
    direction = quat_apply(quat_mul(wrist_quat, torch.tensor([mounting["mount_rotation_wxyz"]])),
                           torch.tensor([[0.0, 0.0, grasp_offset]]))
    print('TCP offset',direction.tolist(),flush=True)
    if args.probe:
        sim.stage.Export(str(args.report_dir / "assembly.usda"))
        return
    object_position = wrist_start + direction
    object_position[:, 2] -= 0.12 + size[2] / 2 - grasp_depth
    object_pose = torch.cat((object_position, torch.tensor([[1.0, 0, 0, 0]])), dim=-1)
    table_pose = object_pose.clone()
    table_pose[:, 2] -= size[2] / 2 + 0.05
    if float(table_pose[0, 2]) < 0.05:
        raise RuntimeError("Computed table would intersect the ground; grasp was not started")
    table_clearances = {}
    if host is not None:
        cache = UsdGeom.BBoxCache(0, ["default", "render", "proxy"])
        half_size = torch.tensor([args.table_length / 2, 0.35, 0.05])
        table_min, table_max = table_pose[0, :3] - half_size, table_pose[0, :3] + half_size
        for body_id, path in enumerate(host.root_physx_view.link_paths[0]):
            bounds = cache.ComputeUntransformedBound(sim.stage.GetPrimAtPath(path)).ComputeAlignedRange()
            if bounds.IsEmpty():
                continue
            corners = torch.tensor([list(bounds.GetCorner(index)) for index in range(8)])
            corners = quat_apply(host.data.body_quat_w[0, body_id].expand(8, -1), corners) + host.data.body_pos_w[0, body_id]
            table_clearances[path] = box_separation(corners.amin(0).tolist(), corners.amax(0).tolist(),
                                                  table_min.tolist(), table_max.tolist())
        (args.report_dir / "table_clearances.json").write_text(json.dumps(table_clearances, indent=2))
        if not table_clearances or min(table_clearances.values()) < 0.005:
            raise RuntimeError("Table lacks 5 mm bounding-box clearance from the mobile host")
    table.write_root_pose_to_sim(table_pose)
    payload.write_root_pose_to_sim(object_pose)
    payload.write_root_velocity_to_sim(torch.zeros((1, 6)))
    for _ in range(120):
        step()
    object_initial = payload.data.root_pos_w[0].tolist()
    host_initial = host.data.root_pos_w[0].clone() if host is not None else None
    stages = [("approach", 3.0, (0, 0, -0.12), 0.0),
              ("close", 2.0, (0, 0, -0.12), 0.8),
              ("lift", 3.0, (0, 0, 0.03), 0.8),
              ("transfer", 3.0, (0, 0.15, 0.03), 0.8),
              ("hold", 2.0, (0, 0.15, 0.03), 0.8),
              ("lower", 3.0, (0, 0.15, -0.12), 0.8),
              ("release", 2.0, (0, 0.15, -0.12), 0.0),
              ("retreat", 3.0, (0, 0.15, 0.03), 0.0)]
    samples = []
    def mount_geometry():
        if host is None:
            return {}
        assets = (host, arm)
        weighted = args.mass * payload.data.root_pos_w[0]
        total_mass = args.mass
        for asset in assets:
            masses = asset.root_physx_view.get_masses()[0]
            weighted += (masses[:, None] * asset.data.body_com_pos_w[0]).sum(0)
            total_mass += float(masses.sum())
        center = (weighted / total_mass).tolist()
        supports = [host.data.body_pos_w[0, index].tolist() for index, name in enumerate(host.body_names) if "wheel_link" in name]
        return {"system_com_m": center, "geometric_support_margin_m": support_polygon_margin(center, supports)}
    previous = torch.zeros((1, 3))
    count = 0
    for phase, duration, offset, grip_target in stages:
        final = torch.tensor([offset])
        for index in range(int(duration / timestep)):
            fraction = min(1.0, (index + 1) * timestep / duration)
            blend = fraction * fraction * (3 - 2 * fraction)
            target = wrist_start + previous * (1 - blend) + final * blend
            command = ManipulatorPoseCommand("world", tuple(target[0].tolist()),
                         tuple(wrist_quat[0, [1, 2, 3, 0]].tolist()), count)
            controller._apply_pose(environment, arm, "host", arm_ids, ManipulatorActiveCommand("pose", command))
            arm.set_joint_position_target(torch.tensor([[0.0 if args.open_control else grip_target]]), joint_ids=grip_ids)
            step()
            forces = view.get_contact_force_matrix(dt=timestep)[0]
            jaw_table_forces = torch.stack((forces[left_contacts].sum(dim=0),
                                           forces[right_contacts].sum(dim=0), forces[table_contact]))
            row = {"time_s": count * timestep, "phase": phase,
                   **mount_geometry(),
                   "object_position_m": payload.data.root_pos_w[0].tolist(),
                   "object_velocity_m_s": payload.data.root_lin_vel_w[0].tolist(),
                   "wrist_position_m": arm.data.body_pos_w[0, wrist_id].tolist(),
                   "wrist_target_m": target[0].tolist(),
                   "contact_forces_N": torch.linalg.vector_norm(jaw_table_forces, dim=-1).tolist(),
                   "contact_body_force_vectors_N": forces.tolist(),
                   "other_contact_force_N": float(torch.linalg.vector_norm(forces[other_contacts], dim=-1).sum()),
                   "joint_position_rad": arm.data.joint_pos[0].tolist(),
                   "joint_velocity_rad_s": arm.data.joint_vel[0].tolist(),
                   "joint_efforts_Nm": arm.root_physx_view.get_dof_projected_joint_forces()[0].tolist(),
                   "host_pose": None if host is None else host.data.root_state_w[0, :7].tolist()}
            samples.append(row)
            count += 1
        previous = final
        print(phase, json.dumps({key: row[key] for key in ("object_position_m", "contact_forces_N", "wrist_position_m")}), flush=True)
    report = {"schema": "eai.ur5_robotiq_contact_trial.v1", "host": args.host,
              "run_options": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
              "table_size_m": [args.table_length, 0.7, 0.1], "table_host_aabb_clearances_m": table_clearances,
              "support_margin_scope": "robot and object COM projected into wheel-center hull; not measured contact or safety margin",
              "object_mass_kg": args.mass, "open_control": args.open_control,
              "mass_scale": 1.0, "payload_pose_writes_during_task": 0, "payload_fixed_joint": False,
              "gripper": mounting, "dt_s": timestep, "object_size_m": size,
              "grasp_axial_offset_m": grasp_offset, "grasp_depth_below_top_m": grasp_depth,
              "static_friction": 0.8, "dynamic_friction": 0.6,
              "fingertip_materials": fingertip_materials,
              "contact_filter_paths": contact_paths,
              "jaw_contact_definition": "sum of inner and outer finger body force vectors on each side",
              "gripper_drive_limit_Nm": args.gripper_torque,
              "body_names": arm.body_names, "body_masses_kg": arm.root_physx_view.get_masses()[0].tolist(),
              "joint_names": arm.joint_names,
              "joint_effort_limits_Nm": arm.data.joint_effort_limits[0].tolist(),
              "body_inertias_kg_m2": arm.root_physx_view.get_inertias()[0].tolist(),
              "initial_object_position_m": object_initial,
              "host_displacement_m": None if host is None else float(torch.linalg.vector_norm(host.data.root_pos_w[0] - host_initial)),
              "host_body_masses_kg": None if host is None else host.root_physx_view.get_masses()[0].tolist(),
              "host_wheel_effort_limit_Nm": None if host is None else 20.0,
              "controller_source": controller_source,
              "samples": samples, "limitations": ["Estimated gripper mass distribution and shape-derived inertias",
                    "Contact friction is declared, not hardware measured", "No hardware certification or full task benchmark validation"]}
    report["source_layers"] = [{"path": layer.realPath,
        "sha256": hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()}
        for layer in sim.stage.GetUsedLayers() if layer.realPath and Path(layer.realPath).is_file()]
    report["runner_sha256"] = args.runner_sha256
    report["assembly_helper_sha256"] = args.helper_sha256
    (args.report_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    sim.stage.Export(str(args.report_dir / "assembly.usda"))
    print("REPORT_COMPLETE", str(args.report_dir.resolve()), flush=True)


if __name__ == "__main__":
    main()
