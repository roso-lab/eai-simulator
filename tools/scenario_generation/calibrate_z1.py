"""Measure unscaled standalone Z1 contact grasping in a bounded Isaac fixture.

The payload starts prepositioned between the open jaws. This is a component
calibration, not scene pickup, mobile manipulation, or a hardware rating.
Only joint drive targets change during trials; payload poses are reset only
before a trial. Raw post-solve samples are retained for independent validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    import common
else:
    from . import common

sys.path.insert(0, str(common.REPO_ROOT))


def run_trials(args):
    from isaaclab.app import AppLauncher
    from importlib.metadata import version

    runner_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    config_path = common.REPO_ROOT / "source/EAI_assets/EAI_assets/robots/z1.py"
    config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest()

    launcher = AppLauncher(headless=not args.gui, device="cpu", enable_cameras=True)
    try:
        import torch
        import isaaclab.sim as sim_utils
        from isaaclab.assets import Articulation, RigidObject, RigidObjectCfg
        from isaaclab.utils.math import combine_frame_transforms, compute_pose_error, quat_from_euler_xyz, quat_mul
        from pxr import PhysxSchema, UsdPhysics
        from EAI_assets.robots.z1 import Z1_CFG

        sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
            dt=1 / 240, device="cpu", physx=sim_utils.PhysxCfg(enable_ccd=True)))
        sim.set_camera_view((1.2, 1.2, 1.0), (0.0, 0.0, 0.4))
        ground = RigidObject(RigidObjectCfg(
            prim_path="/World/Ground",
            spawn=sim_utils.CuboidCfg(size=(4.0, 4.0, 0.05),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
                collision_props=sim_utils.CollisionPropertiesCfg(),
                mass_props=sim_utils.MassPropertiesCfg(mass=1.0)),
            init_state=RigidObjectCfg.InitialStateCfg(pos=(0, 0, -0.025))))
        light = sim_utils.DomeLightCfg(intensity=2000)
        light.func("/World/Light", light)
        cfg = Z1_CFG.copy()
        cfg.prim_path = "/World/Z1"
        cfg.init_state.pos = (0, 0, 0.25)
        cfg.init_state.joint_pos.update(joint2=0.8, joint3=-1.2, joint4=0.4, jointGripper=-0.7)
        cfg.actuators["arm"].effort_limit_sim = {"joint2": 60.0, "joint[13456]": 30.0}
        cfg.spawn.activate_contact_sensors = True
        arm = Articulation(cfg)
        from tools.scenario_generation.benchmark_mass_audit import authored_inertials
        authored_bodies, source_layers = authored_inertials(cfg.spawn.usd_path)
        for prim in sim.stage.Traverse():
            if prim.IsA(UsdPhysics.RevoluteJoint):
                UsdPhysics.DriveAPI(prim, "angular").GetTypeAttr().Set("force")
        material = sim_utils.RigidBodyMaterialCfg(static_friction=0.8, dynamic_friction=0.6,
                                                  restitution=0.0)
        cube = RigidObject(RigidObjectCfg(
            prim_path="/World/Payload",
            spawn=sim_utils.CuboidCfg(size=(0.03, 0.07, 0.025),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False),
                mass_props=sim_utils.MassPropertiesCfg(mass=0.2),
                collision_props=sim_utils.CollisionPropertiesCfg(contact_offset=0.001,
                                                                 rest_offset=0.0),
                physics_material=material, activate_contact_sensors=True),
            init_state=RigidObjectCfg.InitialStateCfg(pos=(2, 0, 0.02)),
        ))
        PhysxSchema.PhysxRigidBodyAPI.Apply(sim.stage.GetPrimAtPath("/World/Payload")).CreateEnableCCDAttr(True)
        print("[calibration] resetting physics", flush=True)
        sim.reset()
        print("[calibration] physics ready", flush=True)
        import carb
        carb.settings.get_settings().set_bool("/physics/disableContactProcessing", False)
        arm_ids, _ = arm.find_joints([f"joint{index}" for index in range(1, 7)], preserve_order=True)
        grip_ids, _ = arm.find_joints("jointGripper")
        stator_id = arm.find_bodies("gripperStator")[0][0]
        contact_filters = ["/World/Z1/gripperStator", "/World/Z1/gripperMover", "/World/Ground"]
        contact_filters += [f"/World/Z1/link0{index}" for index in range(7)]
        view = arm._physics_sim_view.create_rigid_contact_view(
            "/World/Payload", contact_filters,
            max_contact_data_count=512)
        print(json.dumps({"fixed_base": arm.is_fixed_base, "bodies": arm.body_names,
                          "joints": arm.joint_names}), flush=True)
        dt = sim.get_physics_dt()
        trials = []
        for mass in args.masses:
            for repeat in range(args.repeats):
                for control in ("close", "open_control"):
                    joint_start = arm.data.default_joint_pos.clone()
                    arm.write_joint_state_to_sim(joint_start, torch.zeros_like(joint_start))
                    arm.set_joint_position_target(joint_start)
                    arm.reset()
                    cube_pose = cube.data.default_root_state[:, :7].clone()
                    cube_pose[:, :3] = torch.tensor([[2.0, 0, 0.02]])
                    cube.write_root_pose_to_sim(cube_pose)
                    cube.write_root_velocity_to_sim(torch.zeros((1, 6)))
                    cube.root_physx_view.set_masses(torch.tensor([[mass]]), torch.tensor([0]))
                    cube.root_physx_view.set_inertias(torch.tensor([[
                        mass * (0.07**2 + 0.025**2) / 12, 0, 0,
                        0, mass * (0.03**2 + 0.025**2) / 12, 0,
                        0, 0, mass * (0.03**2 + 0.07**2) / 12]]), torch.tensor([0]))
                    for _ in range(round(1 / dt)):
                        arm.write_data_to_sim()
                        sim.step(render=args.gui)
                        arm.update(dt)
                        cube.update(dt)
                    start_position = arm.data.body_pos_w[:, stator_id].clone()
                    start_quat = arm.data.body_quat_w[:, stator_id].clone()
                    object_position, object_quat = combine_frame_transforms(
                        start_position, start_quat, torch.tensor([[0.13, 0.0, 0.0135]]))
                    cube.write_root_pose_to_sim(torch.cat((object_position, object_quat), dim=-1))
                    cube.write_root_velocity_to_sim(torch.zeros((1, 6)))
                    cube.reset()
                    actual_mass = float(cube.root_physx_view.get_masses()[0, 0])
                    samples = []
                    for step in range(round(10 / dt)):
                        time_s = step * dt
                        phase = ("close" if time_s < 1.5 else "lift" if time_s < 3.0
                                 else "transfer" if time_s < 4.5 else "hold" if time_s < 6.0
                                 else "release")
                        target_position = start_position.clone()
                        target_position[:, 2] += 0.10 * max(0.0, min(1.0, (time_s - 1.5) / 1.5))
                        target_position[:, 1] += 0.15 * max(0.0, min(1.0, (time_s - 3.0) / 1.5))
                        current_position = arm.data.body_pos_w[:, stator_id]
                        current_quat = arm.data.body_quat_w[:, stator_id]
                        tip_angle = 1.2 * max(0.0, min(1.0, (time_s - 6.0) / 1.5))
                        target_quat = quat_mul(start_quat, quat_from_euler_xyz(
                            torch.zeros(1), torch.tensor([tip_angle]), torch.zeros(1)))
                        position_error, rotation_error = compute_pose_error(
                            current_position, current_quat, target_position, target_quat,
                            rot_error_type="axis_angle")
                        jacobian = arm.root_physx_view.get_jacobians()[:, stator_id - 1, :, arm_ids]
                        error = torch.cat((position_error, rotation_error), dim=-1)
                        delta = jacobian.transpose(1, 2) @ torch.linalg.solve(
                            jacobian @ jacobian.transpose(1, 2) + 0.01**2 * torch.eye(6),
                            error.unsqueeze(-1))
                        target = arm.data.joint_pos.clone()
                        target[:, arm_ids] += delta.squeeze(-1).clamp(-0.15, 0.15)
                        limits = arm.data.soft_joint_pos_limits
                        target = torch.minimum(torch.maximum(target, limits[:, :, 0]), limits[:, :, 1])
                        target[:, grip_ids] = -0.7 if control == "open_control" or phase == "release" else 0.0
                        arm.set_joint_position_target(target)
                        arm.write_data_to_sim()
                        sim.step(render=args.gui)
                        arm.update(dt)
                        cube.update(dt)
                        forces = view.get_contact_force_matrix(dt=dt)[0]
                        _, _, _, separations, counts, starts = view.get_contact_data(dt=dt)
                        distances = []
                        for count, start in zip(counts[0].tolist(), starts[0].tolist(), strict=True):
                            if count:
                                distances.extend(separations[start:start+count].flatten().tolist())
                        samples.append({
                            "time_s": (step + 1) * dt, "phase": phase,
                            "object_position_m": cube.data.root_pos_w[0].tolist(),
                            "object_velocity_m_s": cube.data.root_lin_vel_w[0].tolist(),
                            "stator_position_m": arm.data.body_pos_w[0, stator_id].tolist(),
                            "jaw_contact_force_N": torch.linalg.vector_norm(forces[:2], dim=-1).tolist(),
                            "net_contact_force_N": view.get_net_contact_forces(dt=dt)[0].tolist(),
                            "support_contact_force_N": float(torch.linalg.vector_norm(forces[2:], dim=-1).sum()),
                            "maximum_penetration_m": max(0.0, -min(distances, default=0.0)),
                            "contact_count": sum(counts[0].tolist()),
                            "gripper_joint_rad": float(arm.data.joint_pos[0, grip_ids[0]]),
                            "joint_position_rad": arm.data.joint_pos[0].tolist(),
                            "joint_velocity_rad_s": arm.data.joint_vel[0].tolist(),
                        })
                    trials.append({"mass_kg": mass, "runtime_mass_kg": actual_mass,
                                   "repeat": repeat, "control": control,
                                   "initial_object_position_m": object_position[0].tolist(),
                                   "samples": samples})
                    print(json.dumps({"mass_kg": mass, "repeat": repeat, "control": control,
                                      "final_position_m": samples[-1]["object_position_m"]}), flush=True)
        asset = Path(cfg.spawn.usd_path)
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner_sha256:
            raise ValueError("calibration runner changed during execution")
        if hashlib.sha256(config_path.read_bytes()).hexdigest() != config_sha256:
            raise ValueError("calibration source configuration changed during execution")
        for layer in source_layers:
            if hashlib.sha256(Path(layer["path"]).read_bytes()).hexdigest() != layer["sha256"]:
                raise ValueError("source asset changed during execution")
        layers = [{"path": str(layer.realPath),
                   "sha256": hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()}
                  for layer in sim.stage.GetUsedLayers() if layer.realPath and Path(layer.realPath).is_file()]
        report = {"schema_id": "eai.physical_calibration", "schema_version": "1.0",
                "experiment": "z1_prepositioned_grasp", "domain": "simulation",
                "subject": "z1_standalone_unscaled", "asset": str(asset), "asset_layers": layers,
                "runner_sha256": runner_sha256,
                "runtime_versions": {"isaacsim": version("isaacsim"), "isaaclab": version("isaaclab"),
                                     "torch": torch.__version__, "physics_device": "cpu"},
                "protocol": {"dt_s": dt, "mass_scale": 1.0, "gravity_m_s2": 9.81,
                             "payload_size_m": [0.03, 0.07, 0.025], "drive_type": "force",
                             "arm_effort_limit_Nm": {"joint2": 60.0, "other_joints": 30.0},
                             "gripper_effort_limit_Nm": 30.0,
                             "scripted_payload_motion": False, "fixed_attachment": False,
                             "payload_gravity_enabled": True, "payload_collision_enabled": True,
                             "self_collision_enabled": False, "repeats": args.repeats,
                             "contact_filters": contact_filters,
                             "collision_geometry": "asset_authored_convex_hulls",
                             "payload_ccd_enabled": True, "target_lift_m": 0.10,
                             "target_transfer_m": 0.15,
                             "position_ik": True, "orientation_ik": True},
                "trials": trials,
                "arm_runtime_masses_kg": dict(zip(arm.body_names,
                    arm.root_physx_view.get_masses()[0].tolist(), strict=True)),
                "arm_runtime_inertias_kg_m2": dict(zip(arm.body_names,
                    arm.root_physx_view.get_inertias()[0].tolist(), strict=True)),
                "authored_arm_inertials": authored_bodies,
                "source_asset_layers": source_layers,
                "fixed_dummy_bodies": ["world"],
                "source_config_sha256": config_sha256,
                "limitations": ["Prepositioned object; no autonomous approach or scene pickup",
                                "Standalone fixed base; no mounted robot or team validation",
                                "Authored simulation drive limits are not hardware ratings",
                                "No self-collision safety, wind or hardware certification"]}
        common.write_json(args.report, report)
        from tools.scenario_generation.physical_evidence import content_hash, verify_calibration
        digest = content_hash(report)
        common.write_json(str(args.report) + ".manifest.json",
                          {"report_sha256": digest, "runner_sha256": runner_sha256,
                           "scope": "local_run_ledger_not_authentication"})
        if args.repeats >= 3:
            validation = verify_calibration(report, expected_report_sha256=digest,
                                            expected_runner_sha256=runner_sha256)
            common.write_json(str(args.report) + ".validation.json", validation)
            print(json.dumps({"physical_validation": validation["validation_status"],
                              "validated_masses_kg": validation["validated_masses_kg"]}), flush=True)
        return report
    finally:
        print("[calibration] shutting down", flush=True)
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--masses", type=float, nargs="+", default=[0.2, 2.0, 8.0, 30.0])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    import math
    if args.repeats < 1 or any(not math.isfinite(mass) or mass <= 0 for mass in args.masses):
        parser.error("positive finite masses and at least one repeat are required")
    if Path(args.report).exists():
        parser.error("report already exists; choose a new output path")
    run_trials(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
