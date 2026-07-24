from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from simulator import SimulatorLaunchConfig, open_simulator_session


def _tensor_values(value):
    return [float(item) for item in value.detach().cpu().flatten()]


def _robot_snapshot(robot):
    masses = _tensor_values(robot.root_physx_view.get_masses()[0])
    return {
        "root_pos": _tensor_values(robot.data.root_pos_w[0]),
        "root_quat": _tensor_values(robot.data.root_quat_w[0]),
        "root_lin_vel": _tensor_values(robot.data.root_lin_vel_w[0]),
        "root_ang_vel": _tensor_values(robot.data.root_ang_vel_w[0]),
        "total_mass": sum(masses),
        "body_masses": dict(zip(robot.body_names, masses)),
        "joint_count": len(robot.joint_names),
        "joint_positions": dict(zip(robot.joint_names, _tensor_values(robot.data.joint_pos[0]))),
        "joint_velocities": dict(zip(robot.joint_names, _tensor_values(robot.data.joint_vel[0]))),
        "default_root_state": _tensor_values(robot.data.default_root_state[0]),
    }


def _collision_bounds(stage, prefix):
    from pxr import Usd, UsdGeom, UsdPhysics

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    bounds = []
    root = stage.GetPrimAtPath(prefix)
    if not root.IsValid():
        return bounds
    for prim in Usd.PrimRange(root, Usd.TraverseInstanceProxies()):
        path = str(prim.GetPath())
        collision_attr = prim.GetAttribute("physics:collisionEnabled")
        collision_enabled = collision_attr.Get() if collision_attr.IsValid() else None
        if not prim.HasAPI(UsdPhysics.CollisionAPI) and collision_enabled is not True:
            continue
        box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
        minimum = box.GetMin()
        maximum = box.GetMax()
        bounds.append(
            {
                "path": path,
                "min": [float(minimum[index]) for index in range(3)],
                "max": [float(maximum[index]) for index in range(3)],
            }
        )
    return bounds


def _overlaps(host_bounds, arm_bounds):
    overlaps = []
    for host in host_bounds:
        for arm in arm_bounds:
            extent = [
                min(host["max"][axis], arm["max"][axis])
                - max(host["min"][axis], arm["min"][axis])
                for axis in range(3)
            ]
            if all(value > 1.0e-5 for value in extent):
                overlaps.append(
                    {
                        "host": host["path"],
                        "arm": arm["path"],
                        "extent": extent,
                        "volume": extent[0] * extent[1] * extent[2],
                    }
                )
    return sorted(overlaps, key=lambda item: item["volume"], reverse=True)


def _mount_diagnostics(stage, name, robot_type):
    from pxr import Gf, UsdGeom, UsdPhysics

    from EAI_assets.robots.ur5_mount import UR5_MOUNT_PROFILES

    profile = UR5_MOUNT_PROFILES.get(robot_type)
    if profile is None:
        return {}
    host_path = f"/World/envs/env_0/{name}"
    arm_path = f"{host_path}_arm"
    body0_path = f"{host_path}/{profile.mount_body_path}"
    body1_path = f"{arm_path}/base_link"
    body0 = stage.GetPrimAtPath(body0_path)
    body1 = stage.GetPrimAtPath(body1_path)
    if not body0.IsValid() or not body1.IsValid():
        return {"mounted": False, "body0": body0_path, "body1": body1_path}
    body0_world = UsdGeom.Xformable(body0).ComputeLocalToWorldTransform(0)
    body1_world = UsdGeom.Xformable(body1).ComputeLocalToWorldTransform(0)
    anchor0 = body0_world.Transform(Gf.Vec3d(*profile.mount_position))
    anchor1 = body1_world.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
    mount_rotation = Gf.Quatd(profile.mount_rotation[0], Gf.Vec3d(*profile.mount_rotation[1:]))
    frame0_rotation = body0_world.ExtractRotationQuat() * mount_rotation
    frame1_rotation = body1_world.ExtractRotationQuat()
    dot = abs(
        float(frame0_rotation.GetReal()) * float(frame1_rotation.GetReal())
        + sum(
            float(frame0_rotation.GetImaginary()[index])
            * float(frame1_rotation.GetImaginary()[index])
            for index in range(3)
        )
    )
    rotation_error = 2.0 * math.acos(min(1.0, max(-1.0, dot)))
    roots = [
        str(prim.GetPath())
        for prim in stage.Traverse()
        if (str(prim.GetPath()).startswith(host_path) or str(prim.GetPath()).startswith(arm_path))
        and prim.HasAPI(UsdPhysics.ArticulationRootAPI)
    ]
    arm_rigid_bodies = []
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if not path.startswith(arm_path) or not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
        position = transform.ExtractTranslation()
        arm_rigid_bodies.append(
            {
                "path": path,
                "position": [float(position[index]) for index in range(3)],
            }
        )
    return {
        "body0": body0_path,
        "body1": body1_path,
        "anchor0": [float(anchor0[index]) for index in range(3)],
        "anchor1": [float(anchor1[index]) for index in range(3)],
        "anchor_error": [float(anchor1[index] - anchor0[index]) for index in range(3)],
        "frame0_rotation": [
            float(frame0_rotation.GetReal()),
            *[float(frame0_rotation.GetImaginary()[index]) for index in range(3)],
        ],
        "frame1_rotation": [
            float(frame1_rotation.GetReal()),
            *[float(frame1_rotation.GetImaginary()[index]) for index in range(3)],
        ],
        "rotation_error_radians": rotation_error,
        "articulation_roots": roots,
        "arm_rigid_bodies": arm_rigid_bodies,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="123")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--hold-seconds", type=float, default=10.0)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()

    selection_path = Path("source/EAI_hmrs/EAI_hmrs/envs") / f"{args.env}.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    config = SimulatorLaunchConfig(
        env=args.env,
        num_envs=1,
        device=args.device,
        headless=True,
        enable_ros_bridge_extension=True,
        resolved_env_name=args.env,
        selection_data=selection,
        app_launcher_args={"headless": True, "device": args.device},
    )

    with open_simulator_session(config) as session:
        import omni.usd
        import torch

        stage = omni.usd.get_context().get_stage()
        names = list(session.possible_agents)
        robots = session.base_env.scene.articulations
        manager = getattr(session.base_env, "_ur5_ros2_manager", None)
        payload = {
            "possible_agents": names,
            "ros_robot_names": list(getattr(manager, "robot_names", ())),
            "checkpoints": {"0": {name: _robot_snapshot(robots[name]) for name in names}},
            "articulation_checkpoints": {
                "0": {name: _robot_snapshot(robot) for name, robot in robots.items()}
            },
            "collisions": {},
        }
        for name in names:
            robot_type = name.rsplit("_", 1)[0]
            host_prefix = f"/World/envs/env_0/{name}"
            arm_prefix = f"{host_prefix}_arm"
            host_bounds = _collision_bounds(stage, host_prefix)
            arm_bounds = _collision_bounds(stage, arm_prefix)
            payload["collisions"][name] = {
                "host_collision_count": len(host_bounds),
                "arm_collision_count": len(arm_bounds),
                "overlaps": _overlaps(host_bounds, arm_bounds)[:20],
                "mount": _mount_diagnostics(stage, name, robot_type),
            }
        Path(args.ready_file).write_text("ready\n", encoding="utf-8")
        hold_until = time.monotonic() + args.hold_seconds
        while time.monotonic() < hold_until:
            session.simulation_app.update()
            time.sleep(0.05)

        checkpoints = {1, 10, 50, 100, args.steps}
        for step in range(1, args.steps + 1):
            session.env.step(
                {
                    name: torch.zeros((1, 3), device=session.device)
                    for name in names
                }
            )
            if step in checkpoints:
                payload["checkpoints"][str(step)] = {
                    name: _robot_snapshot(robots[name]) for name in names
                }
                payload["articulation_checkpoints"][str(step)] = {
                    name: _robot_snapshot(robot) for name, robot in robots.items()
                }
        Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
