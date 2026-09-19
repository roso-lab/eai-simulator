"""Measure EAI Z1 mounts at unscaled mass on freely supported mobile robots.

The host uses its authored bounded joint actuators to hold the standing pose.
This tests stationary mounting and arm motion, not a learned locomotion policy.
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

ROOT = Path(__file__).resolve().parents[2]
for package in ("EAI", "EAI_assets", "EAI_hmrs"):
    sys.path.insert(0, str(ROOT / "source" / package))


def run(args):
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation
    from isaaclab.sensors import ContactSensor, ContactSensorCfg
    from pxr import UsdPhysics
    from EAI_assets.robots.z1_mount import Z1_MOUNT_PROFILES, build_mounted_z1_asset_cfg
    from EAI_assets.robots.z1 import Z1_JOINT_NAMES
    from EAI_assets.robots.ur5_mount import (
        UR5_MOUNT_PROFILES, UR5_JOINT_NAMES, build_mounted_ur5_asset_cfg,
        build_mounted_ur5_bare_asset_cfg,
    )
    from EAI_assets.robots.kinova_mount import KINOVA_MOUNT_PROFILES, build_candidate_kinova_gen3_cfg
    from isaaclab.utils.math import matrix_from_quat
    from runtime_mount_physics import aggregate_runtime_physics
    from EAI.physics.articulation_reset import capture_root_poses, set_spawn_root_defaults
    from mobile_mount_checks import ground_support_summary, support_polygon_margin
    if args.host == "b2":
        from EAI_assets.robots.b2 import B2_CFG as host_template
        if args.host_control == "stationary":
            from EAI_assets.robots.b2 import build_b2_stationary_cfg
            host_template = build_b2_stationary_cfg()
    elif args.host == "go2":
        from EAI_assets.robots.go2 import GO2_CFG as host_template
    elif args.host == "m20":
        from EAI_assets.robots.deeprobotics import DEEPROBOTICS_M20_CFG as host_template
    elif args.host == "lite3":
        from EAI_assets.robots.deeprobotics import DEEPROBOTICS_LITE3_CFG as host_template
    elif args.host == "carter":
        from EAI_assets.robots.carter import CARTER_CFG as host_template
    else:
        from EAI_assets.robots.scout import SCOUT_CFG as host_template
    sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=1 / 240, device="cpu",
        physx=sim_utils.PhysxCfg(min_position_iteration_count=16, min_velocity_iteration_count=4)))
    ground = sim_utils.CuboidCfg(size=(6, 6, 0.1), collision_props=sim_utils.CollisionPropertiesCfg(),
        physics_material=sim_utils.RigidBodyMaterialCfg(static_friction=0.8, dynamic_friction=0.6))
    ground.func("/World/Ground", ground, translation=(0, 0, -0.05))
    host_cfg = host_template.copy()
    host_cfg.prim_path = "/World/Host"
    if args.host == "scout" and os.environ.get("EAI_SCOUT_PHYSICS_USD"):
        host_cfg.spawn.usd_path = os.environ["EAI_SCOUT_PHYSICS_USD"]
    if args.host_stiffness is not None:
        for actuator in host_cfg.actuators.values():
            if actuator.stiffness == 0:
                continue
            actuator.stiffness = args.host_stiffness
            actuator.damping = args.host_damping
    if args.host == "scout":
        host_cfg.actuators["base_wheels"].effort_limit_sim = 20.0
        host_cfg.actuators["base_wheels"].damping = 100.0
    host = Articulation(host_cfg)
    reporting_before_sensors = sim.carb_settings.get_as_bool("/physics/disableContactProcessing")
    sensor_body_paths = [str(prim.GetPath()) for prim in sim.stage.Traverse()
                         if str(prim.GetPath()).startswith("/World/Host/")
                         and prim.HasAPI(UsdPhysics.RigidBodyAPI)
                         and prim.GetName().lower().endswith((
                             "_foot", "_wheel", "_wheel_link",
                             "wheel_left", "wheel_right",
                             "caster_wheel_left", "caster_wheel_right"))]
    if len(sensor_body_paths) != 4:
        raise ValueError(f"Expected four wheel/foot sensor bodies, found {sensor_body_paths}")
    host_contact_sensors = []
    for body_path in sensor_body_paths:
        host_contact_sensors.append(ContactSensor(ContactSensorCfg(
            prim_path=body_path,
            filter_prim_paths_expr=["/World/Ground/geometry/mesh"],
            track_air_time=True,
            track_contact_points=True,
            max_contact_data_count_per_prim=64,
        )))
    reporting_after_sensors = sim.carb_settings.get_as_bool("/physics/disableContactProcessing")
    sim.carb_settings.set_bool("/physics/disableContactProcessing", args.disable_contact_processing)
    profiles = Z1_MOUNT_PROFILES if args.arm == "z1" else KINOVA_MOUNT_PROFILES if args.arm == "kinova_gen3" else UR5_MOUNT_PROFILES
    profile = (replace(profiles[args.host], mass_scale=1.0, enable_self_collisions=True)
               if args.arm in {"z1", "ur5", "ur5_bare"} else profiles[args.host])
    if args.mount_body:
        profile = replace(profile, mount_body_path=args.mount_body)
    arm = None
    arm_root_path = None
    if not args.bare_host:
        builder = {"z1": build_mounted_z1_asset_cfg, "ur5": build_mounted_ur5_asset_cfg,
                   "kinova_gen3": build_candidate_kinova_gen3_cfg,
                   "ur5_bare": build_mounted_ur5_bare_asset_cfg}[args.arm]
        arm_cfg = builder("/World/Host_arm", profile)
        arm_root_path = f"{arm_cfg.prim_path.rstrip('/')}/{arm_cfg.articulation_root_prim_path.lstrip('/')}"
        if args.arm == "z1":
            arm_cfg.actuators["arm"].effort_limit_sim = {"joint2": 60.0, "joint[13456]": 30.0}
        elif args.arm == "kinova_gen3":
            arm_cfg.init_state.joint_pos = {name: value for name, value in zip(
                (f"joint_{index}" for index in range(1, 8)), (0.0, 0.65, 0.0, 1.89, 0.0, 0.6, -1.57))}
        elif args.arm_pose == "fixture":
            arm_cfg.init_state.joint_pos = dict(zip(UR5_JOINT_NAMES, (0, -1.2, 1.7, -2.07, -1.57, 0)))
        arm = Articulation(arm_cfg)
    if args.scout_box_inertia:
        from pxr import Gf, UsdPhysics
        mass_api = UsdPhysics.MassAPI(sim.stage.GetPrimAtPath("/World/Host/inertial_link"))
        length, width, height = (0.925, 0.38, 0.21)
        mass_api.GetDiagonalInertiaAttr().Set(Gf.Vec3f(40 / 12 * (width ** 2 + height ** 2),
            40 / 12 * (length ** 2 + height ** 2), 40 / 12 * (length ** 2 + width ** 2)))
    if arm is not None:
        arm_poses = capture_root_poses(sim.stage, [arm_root_path])
    sim.reset()
    root_after_reset = host.data.root_state_w[0].tolist()
    host.write_joint_state_to_sim(host.data.default_joint_pos, torch.zeros_like(host.data.default_joint_pos))
    if args.reset_root_state:
        host.write_root_state_to_sim(host.data.default_root_state)
    if arm is not None:
        arm.write_joint_state_to_sim(arm.data.default_joint_pos, torch.zeros_like(arm.data.default_joint_pos))
        if args.reset_root_state:
            set_spawn_root_defaults(arm, arm_poses)
            arm.write_root_state_to_sim(arm.data.default_root_state)
        arm_joint_names = (list(Z1_JOINT_NAMES) if args.arm == "z1" else
                           list(UR5_JOINT_NAMES) if args.arm in {"ur5", "ur5_bare"} else
                           [f"joint_{index}" for index in range(1, 8)])
        arm_ids, _ = arm.find_joints(arm_joint_names, preserve_order=True)
    assets = [host] if arm is None else [host, arm]
    host_contact_bodies = [str(prim.GetPath()) for prim in
                           sim.stage.Traverse()
                           if prim.HasAPI(UsdPhysics.RigidBodyAPI)
                           and str(prim.GetPath()).startswith("/World/Host/")]
    ground_contact_path = "/World/Ground/geometry/mesh"
    host_contact_view = host._physics_sim_view.create_rigid_contact_view(
        host_contact_bodies, [[ground_contact_path] for _ in host_contact_bodies],
        max_contact_data_count=4096)
    host_net_contact_view = host._physics_sim_view.create_rigid_contact_view(
        host_contact_bodies, max_contact_data_count=4096)
    ground_collision_prims = []
    host_collision_prims = []
    for prim in sim.stage.Traverse():
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        path = str(prim.GetPath())
        if path.startswith("/World/Ground/"):
            ground_collision_prims.append(path)
        elif path.startswith("/World/Host/"):
            host_collision_prims.append(path)
    masses = torch.cat([asset.root_physx_view.get_masses()[0].clone() for asset in assets]).double()
    local_coms = torch.cat([asset.root_physx_view.get_coms()[0, :, :3].clone() for asset in assets]).double()
    body_inertias = torch.cat([
        asset.root_physx_view.get_inertias()[0].clone().reshape(-1, 3, 3).transpose(-1, -2)
        for asset in assets]).double()
    body_properties = {
        "body_names": [f"{asset.cfg.prim_path}/{name}" for asset in assets for name in asset.body_names],
        "masses_kg": masses.tolist(), "local_coms_m": local_coms.tolist(),
        "inertias_at_com_body_frame_kg_m2": body_inertias.tolist(),
        "provenance": "PhysX observations, may include inferred mass properties; no hardware calibration",
        "sampling": "mass properties frozen after reset; this trial does not modify them",
    }
    gripper_metadata = None
    massless_interface_metadata = None
    source_inertia_metadata = None
    if args.arm == "kinova_gen3" and arm is not None:
        massless_interface_metadata = json.loads(sim.stage.GetPrimAtPath("/World/Host_arm").GetAttribute(
            "eai:masslessInterface").Get())
        source_inertia_metadata = json.loads(sim.stage.GetPrimAtPath("/World/Host_arm").GetAttribute(
            "eai:sourceInertias").Get())
        if "end_effector_link" in arm.body_names or len(arm.body_names) != 8 or arm.num_joints != 7:
            raise RuntimeError("Kinova interface conversion must leave eight massive links and seven DOFs")
        if arm.is_fixed_base:
            raise RuntimeError("Mobile Kinova must not be fixed to the world")
    if args.arm == "ur5" and arm is not None:
        from EAI_assets.robots.ur5_robotiq import robotiq_85_mount_metadata
        gripper_metadata = robotiq_85_mount_metadata(sim.stage, arm_path="/World/Host_arm")
    physics_snapshots = []
    reference = host.data.root_pos_w[0].clone()
    samples = []
    for index in range(3600):
        time_s = index / 240
        phase = "settle" if time_s < 3 else "arm_motion" if time_s < 12 else "hold"
        if arm is not None:
            arm_target = arm.data.default_joint_pos.clone()
            if 3 <= time_s < 12:
                arm_target[:, arm_ids[0]] += 0.3 * math.sin(2 * math.pi * (time_s - 3) / 9)
                arm_target[:, arm_ids[1]] += 0.15 * math.sin(math.pi * (time_s - 3) / 9)
        host.set_joint_position_target(host.data.default_joint_pos)
        host.set_joint_velocity_target(torch.zeros_like(host.data.joint_pos))
        host.write_data_to_sim()
        if arm is not None:
            arm.set_joint_position_target(arm_target)
            arm.write_data_to_sim()
        sim.step(render=args.gui)
        host.update(1 / 240)
        if arm is not None:
            arm.update(1 / 240)
        quat = host.data.root_quat_w[0]
        vertical_cosine = 1 - 2 * float(quat[1] ** 2 + quat[2] ** 2)
        positions = torch.cat([asset.data.body_pos_w[0] for asset in assets]).double()
        quaternions = torch.cat([asset.data.body_quat_w[0] for asset in assets]).double()
        rotations = matrix_from_quat(quaternions)
        system_physics = aggregate_runtime_physics(masses, local_coms, body_inertias, positions, rotations)
        host_ground_forces = host_contact_view.get_contact_force_matrix(dt=1 / 240)
        if host_ground_forces.ndim == 4:
            host_ground_forces = host_ground_forces[0]
        if host_ground_forces.ndim == 3:
            host_ground_forces = host_ground_forces.sum(dim=1)
        host_ground_force_vectors = host_ground_forces.tolist()
        host_net_forces = host_net_contact_view.get_net_contact_forces(dt=1 / 240)
        if host_net_forces.ndim == 3:
            host_net_forces = host_net_forces[0]
        _, _, _, _, contact_counts, _ = host_contact_view.get_contact_data(dt=1 / 240)
        contact_point_count = int(contact_counts.sum().item())
        sensor_forces = []
        sensor_contact = []
        sensor_ground_forces = []
        ground_contacts = []
        contact_buffer_max_difference = 0.0
        for sensor in host_contact_sensors:
            sensor.update(1 / 240)
            force = sensor.data.net_forces_w[0, 0]
            sensor_forces.append(float(torch.linalg.vector_norm(force)))
            sensor_contact.append(bool(sensor.data.current_contact_time[0, 0] > 0))
            ground_matrix = sensor.data.force_matrix_w
            if tuple(ground_matrix.shape) != (1, 1, 1, 3):
                raise ValueError(f"Unexpected single-body ground sensor shape {ground_matrix.shape}")
            sensor_ground_forces.append(ground_matrix[0, 0, 0].tolist())
            normal_forces, contact_points, normals, separations, counts, starts = (
                sensor.contact_physx_view.get_contact_data(dt=1 / 240)
            )
            count, start = int(counts[0, 0]), int(starts[0, 0])
            if count >= 64:
                raise RuntimeError("Contact buffer capacity reached; contact hull evidence may be incomplete")
            reconstructed_force = torch.zeros(3, device=ground_matrix.device)
            for contact_index in range(start, start + count):
                normal_force = float(normal_forces[contact_index, 0])
                reconstructed_force += normal_forces[contact_index, 0] * normals[contact_index]
                if normal_force > 1.0e-3:
                    ground_contacts.append({"body_path": sensor.cfg.prim_path,
                                            "position_world_m": contact_points[contact_index].tolist(),
                                            "normal_world": normals[contact_index].tolist(),
                                            "normal_force_N": normal_force,
                                            "separation_m": float(separations[contact_index, 0])})
            contact_buffer_max_difference = max(contact_buffer_max_difference,
                float((reconstructed_force - ground_matrix[0, 0, 0]).abs().max()))
        if index in (0, 720, 1800, 2880, 3599):
            physics_snapshots.append({"sample_index": index, "positions_m": positions.tolist(),
                                      "rotations_body_to_world": rotations.tolist()})
        samples.append({"time_s": time_s, "phase": phase,
            "host_position_m": host.data.root_pos_w[0].tolist(),
            "host_tilt_rad": math.acos(max(-1, min(1, vertical_cosine))),
            "host_velocity": host.data.root_vel_w[0].tolist(),
            "system_com_m": system_physics["center_of_mass_m"],
            "system_physics": system_physics,
            "host_ground_contact_force_vectors_N": host_ground_force_vectors,
            "host_ground_contact_force_total_N": float(torch.linalg.vector_norm(
                host_ground_forces, dim=-1).sum()),
            "host_net_contact_force_total_N": float(torch.linalg.vector_norm(
                host_net_forces, dim=-1).sum()),
            "host_ground_contact_point_count": contact_point_count,
            "contact_sensor_force_norms_N": sensor_forces,
            "contact_sensor_contact_flags": sensor_contact,
            "contact_sensor_ground_force_vectors_N": sensor_ground_forces,
            "ground_contacts": ground_contacts,
            "ground_contact_hull_margin_m": support_polygon_margin(
                system_physics["center_of_mass_m"], [contact["position_world_m"] for contact in ground_contacts]),
            "contact_buffer_force_matrix_max_abs_difference_N": contact_buffer_max_difference,
            "host_joint_position_rad": host.data.joint_pos[0].tolist(),
            "host_joint_velocity_rad_s": host.data.joint_vel[0].tolist(),
            "support_positions_m": [host.data.body_pos_w[0, body_id].tolist() for body_id, name in enumerate(host.body_names)
                                    if "wheel_link" in name or name.endswith("_foot")
                                    or name in ("wheel_left", "wheel_right",
                                                "caster_wheel_left", "caster_wheel_right")],
            "arm_joint_position_rad": [] if arm is None else arm.data.joint_pos[0, arm_ids].tolist(),
            "arm_joint_target_rad": [] if arm is None else arm_target[0, arm_ids].tolist(),
            "arm_joint_efforts_Nm": [] if arm is None else arm.root_physx_view.get_dof_projected_joint_forces()[0].tolist(),
            "host_joint_efforts_Nm": host.data.applied_torque[0].tolist()})
    evaluated = [sample for sample in samples if sample["time_s"] >= 3]
    max_tilt = max(sample["host_tilt_rad"] for sample in evaluated)
    final_drift = float(torch.linalg.vector_norm(host.data.root_pos_w[0, :2] - reference[:2]))
    excursion = 0.0 if arm is None else max(sample["arm_joint_position_rad"][0] for sample in evaluated) - min(
        sample["arm_joint_position_rad"][0] for sample in evaluated)
    ground_support = ground_support_summary(
        [sample["contact_sensor_ground_force_vectors_N"] for sample in evaluated],
        float(masses.sum()) * 9.81,
        not sim.carb_settings.get_as_bool("/physics/disableContactProcessing"),
    )
    max_host_ground_contact_force = ground_support["max_vertical_force_N"]
    ground_contact_observed = ground_support["contact_observed"]
    for sample in samples:
        sample["geometric_support_margin_m"] = support_polygon_margin(sample["system_com_m"], sample["support_positions_m"])
    report = {"schema": "eai.mobile_arm_mount_trial.v2", "host": args.host, "arm": None if arm is None else args.arm,
        "ground_support": ground_support,
        "ground_support_implementation_sha256": hashlib.sha256(
            Path(__file__).with_name("mobile_mount_checks.py").read_bytes()).hexdigest(),
        "contact_reporting": {"disable_processing_before_sensors": reporting_before_sensors,
                              "disable_processing_after_sensors": reporting_after_sensors,
                              "disable_processing_during_trial": sim.carb_settings.get_as_bool("/physics/disableContactProcessing"),
                              "sensor_body_paths": sensor_body_paths,
                              "filter_path": ground_contact_path,
                              "net_force_fields_are_diagnostic_only": True},
        "host_asset_path": host_cfg.spawn.usd_path,
        "runtime_mass_properties": body_properties, "runtime_physics_snapshots": physics_snapshots,
        "host_ground_contact_body_pattern": host_contact_bodies,
        "host_ground_contact_filter": [[ground_contact_path] for _ in host_contact_bodies],
        "stage_physics_diagnostics": {
            "physics_scene_paths": [str(p.GetPath()) for p in sim.stage.Traverse()
                                     if p.GetTypeName() == "PhysicsScene"],
            "ground_collision_prims": ground_collision_prims,
            "host_collision_prim_count": len(host_collision_prims),
            "host_collision_prims": host_collision_prims,
        },
        "gripper_physics_metadata": gripper_metadata,
        "massless_interface_metadata": massless_interface_metadata,
        "source_inertia_metadata": source_inertia_metadata,
        "runtime_physics_implementation_sha256": hashlib.sha256(
            Path(__file__).with_name("runtime_mount_physics.py").read_bytes()).hexdigest(),
        "diagnostic_options": {key: value for key, value in vars(args).items() if key != "report_dir"},
        "root_state_after_sim_reset": root_after_reset,
        "host_joint_names": host.joint_names,
        "host_body_inertias_kg_m2": host.root_physx_view.get_inertias()[0].tolist(),
        "mass_scale": 1.0, "host_fixed_to_world": False, "actuation": "bounded joint drives; B2 stationary gains are opt-in outside this fixture",
        "host_actuator_parameters": {name: {key: getattr(actuator, key) for key in
             ("stiffness", "damping", "effort_limit", "effort_limit_sim")} for name, actuator in host_cfg.actuators.items()},
        "host_body_names": host.body_names, "host_body_masses_kg": host.root_physx_view.get_masses()[0].tolist(),
        "arm_body_names": [] if arm is None else arm.body_names,
        "arm_joint_names": [] if arm is None else arm.joint_names,
        "arm_effort_limits_Nm": [] if arm is None else arm.data.joint_effort_limits[0].tolist(),
        "arm_asset_dependencies": [] if arm is None else list(arm_cfg.asset_dependencies),
        "robotiq_present": sim.stage.GetPrimAtPath("/World/Host_arm/robotiq").IsValid(),
        "arm_body_masses_kg": [] if arm is None else arm.root_physx_view.get_masses()[0].tolist(),
        "arm_body_inertias_kg_m2": [] if arm is None else arm.root_physx_view.get_inertias()[0].tolist(),
        "mount_profile": None if arm is None else vars(profile), "max_tilt_rad_after_settle": max_tilt,
        "final_planar_drift_m": final_drift, "joint1_excursion_rad": excursion,
        "host_ground_contact_observed": ground_contact_observed,
        "max_host_ground_contact_force_N": max_host_ground_contact_force,
        "stationary_motion_pass": (max_tilt < 0.15 and final_drift < 0.10 and excursion > 0.45
                                    and ground_support["persistent_support_pass"]),
        "bare_host_standing_pass": (max_tilt < 0.15 and final_drift < 0.10 and ground_support["persistent_support_pass"]
                                     if arm is None else None),
        "samples": samples, "limitations": ["No locomotion, object grasp, wind or hardware certification",
            "Contact friction is assumed; authored asset masses and motor limits need hardware calibration"],
        "runner_sha256": args.runner_sha256,
        "source_layers": [{"path": layer.realPath, "sha256": hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()}
                          for layer in sim.stage.GetUsedLayers() if layer.realPath and Path(layer.realPath).is_file()]}
    (args.report_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    sim.stage.Export(str(args.report_dir / "assembly.usda"))
    print(json.dumps({key: value for key, value in report.items() if key not in {
        "samples", "source_layers", "arm_body_inertias_kg_m2",
        "runtime_mass_properties", "runtime_physics_snapshots"}}), flush=True)
    print("REPORT_COMPLETE", str(args.report_dir.resolve()), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("b2", "go2", "m20", "lite3", "scout", "carter"), required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--bare-host", action="store_true")
    parser.add_argument("--gui", action="store_true", help="Show the Isaac Sim viewport")
    parser.add_argument("--arm", choices=("z1", "ur5", "ur5_bare", "kinova_gen3"), default="z1")
    parser.add_argument("--gripper-usd", help="Override the default UR5 Robotiq asset")
    parser.add_argument("--arm-pose", choices=("fixture", "default"), default="fixture")
    parser.add_argument("--mount-body")
    parser.add_argument("--reset-root-state", action="store_true", default=True)
    parser.add_argument("--legacy-reset", action="store_false", dest="reset_root_state")
    parser.add_argument("--host-control", choices=("stationary", "authored"), default="stationary")
    parser.add_argument("--host-stiffness", type=float)
    parser.add_argument("--host-damping", type=float, default=20.0)
    parser.add_argument("--scout-box-inertia", action="store_true")
    parser.add_argument("--disable-contact-processing", action="store_true",
                        help="Negative control: disable CPU contact reporting without disabling collisions")
    args = parser.parse_args()
    if args.gripper_usd and (args.arm != "ur5" or args.bare_host):
        parser.error("Robotiq requires a mounted UR5")
    if args.arm == "kinova_gen3" and args.gripper_usd:
        parser.error("Kinova candidate has no verified gripper assembly")
    if args.scout_box_inertia and args.host != "scout":
        parser.error("Box inertia diagnostic requires Scout")
    if args.host_stiffness is not None and (not math.isfinite(args.host_stiffness) or args.host_stiffness <= 0
            or not math.isfinite(args.host_damping) or args.host_damping <= 0):
        parser.error("Positive finite gains required")
    if args.report_dir.exists():
        parser.error("Choose a new report directory")
    args.report_dir.mkdir(parents=True)
    args.runner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if args.gripper_usd:
        os.environ["EAI_ROBOTIQ_2F85_USD"] = str(Path(args.gripper_usd).expanduser().resolve())
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


if __name__ == "__main__":
    main()
