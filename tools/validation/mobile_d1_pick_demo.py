"""Stage-5 GUI demo: Go2 + mounted D1 arm driven through simulator.py's own
session entry with the shared differential-IK controller.

Motion phases: settle -> reach forward-down -> close gripper -> lift ->
open gripper -> return home.  Runs with a live GUI window so the motion can
be watched directly; two Replicator frames are saved for the record.  After
the phases the session keeps stepping so the viewport stays interactive.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV_PATH = ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "envs" / "go2_d1_plane.json"


def look_at_matrix(eye, target):
    from pxr import Gf

    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0.0, 0.0, 1.0))
    return view.GetInverse()


def vec3(tensor):
    return (float(tensor[0]), float(tensor[1]), float(tensor[2]))


def run(args: argparse.Namespace) -> int:
    from tools.project_runtime import activate_project_runtime

    activate_project_runtime()
    import numpy as np
    import torch
    from PIL import Image

    from simulator import SimulatorLaunchConfig, open_simulator_session

    selection_data = json.loads(ENV_PATH.read_text(encoding="utf-8"))
    args.shots_dir.mkdir(parents=True, exist_ok=True)

    config = SimulatorLaunchConfig(
        env="go2_d1_plane",
        resolved_env_name="go2_d1_plane",
        selection_data=selection_data,
        num_envs=1,
        device="cpu",
        seed=0,
        headless=False,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        app_launcher_args={
            "headless": False,
            "device": "cpu",
            "enable_cameras": True,
            "renderer": "RTX",
            "width": 1600,
            "height": 900,
        },
    )
    with open_simulator_session(config) as session:
        return _run_in_session(session, args, np, torch, Image)


def _run_in_session(session, args, np, torch, Image) -> int:
    import omni.replicator.core as rep
    import omni.timeline
    from pxr import Gf, UsdGeom, UsdLux

    from EAI.hmrs_ros.d1_omnigraph import D1_MODEL_SPEC, d1_gripper_targets
    from EAI.controllers.ik_controller import (
        apply_end_effector_pose,
        hold_gripper_position,
    )

    env = session.base_env
    device = env.device
    actions = {
        agent: torch.zeros((env.num_envs, 3), device=device)
        for agent in session.possible_agents
    }

    go2 = env.scene.articulations["go2_1"]
    arm = env.scene.articulations["go2_1_arm"]
    gripper_ids, _ = arm.find_joints(list(D1_MODEL_SPEC.gripper_joint_names), preserve_order=True)
    ee_ids, _ = arm.find_bodies(["Link6"], preserve_order=True)
    ee_id = int(ee_ids[0])
    default_targets = arm.data.default_joint_pos.clone()

    stage = env.sim.stage
    key = UsdLux.DistantLight.Define(stage, "/World/EaiDemoKeyLight")
    key.CreateIntensityAttr(2500.0)
    UsdGeom.Xformable(key).AddRotateXYZOp().Set(Gf.Vec3f(35.0, -30.0, 0.0))

    camera_prim = UsdGeom.Camera.Define(stage, "/World/EaiDemoCam")
    camera_prim.GetFocalLengthAttr().Set(24.0)
    transform_op = UsdGeom.Xformable(camera_prim).AddTransformOp()

    def place_camera(eye, target) -> None:
        transform_op.Set(look_at_matrix(eye, target))

    def set_viewport_camera() -> None:
        try:
            from omni.kit.viewport.utility import get_active_viewport

            get_active_viewport().set_camera_path("/World/EaiDemoCam")
        except Exception as exc:
            print(f"[info] viewport camera binding skipped: {exc}", flush=True)

    render_product = None
    rgb = None
    timeline = omni.timeline.get_timeline_interface()
    if not args.no_captures:
        render_product = rep.create.render_product("/World/EaiDemoCam", (1280, 720))
        rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb.attach(render_product)

    def capture(filename: str, warmup: int = 10) -> None:
        if rgb is None:
            return
        timeline.pause()
        try:
            for _ in range(warmup):
                session.simulation_app.update()
            frame = np.ascontiguousarray(np.asarray(rgb.get_data())[:, :, :3])
        finally:
            timeline.play()
        path = args.shots_dir / filename
        Image.fromarray(frame).save(path)
        print(f"[shot] {path} brightness={frame.mean():.1f}", flush=True)

    def hold(steps: int) -> None:
        for _ in range(steps):
            arm.set_joint_position_target(default_targets)
            hold_gripper_position(arm, D1_MODEL_SPEC)
            arm.write_data_to_sim()
            env.step(actions)

    def reach(target, quat_wxyz, steps: int) -> float:
        error = math.inf
        for step in range(steps):
            apply_end_effector_pose(
                env, arm, "go2_1", D1_MODEL_SPEC, list(target), quat_wxyz,
            )
            hold_gripper_position(arm, D1_MODEL_SPEC)
            arm.write_data_to_sim()
            env.step(actions)
            error = math.dist(vec3(arm.data.body_pos_w[0, ee_id]), target)
            if step % 50 == 0:
                print(f"[ik ] step {step}: EE error {error:.4f} m", flush=True)
        return error

    def command_gripper(closure01: float, steps: int) -> None:
        stroke, mirrored = d1_gripper_targets(closure01)
        wanted = torch.tensor([[stroke, mirrored]], device=device, dtype=torch.float32)
        for _ in range(steps):
            targets = arm.data.joint_pos.clone()
            targets[:, gripper_ids] = wanted
            arm.set_joint_position_target(targets)
            arm.write_data_to_sim()
            env.step(actions)

    print("[phase] settle", flush=True)
    hold(120)
    base = vec3(go2.data.root_pos_w[0])
    place_camera(
        (base[0] + 1.70, base[1] + 2.10, 1.55),
        (base[0], base[1], 0.45),
    )
    set_viewport_camera()

    ee_start = vec3(arm.data.body_pos_w[0, ee_id])
    quat_start = [float(v) for v in arm.data.body_quat_w[0, ee_id]]
    target = (base[0] + 0.38, base[1], 0.15)

    print(f"[phase] reach target=({target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f}) "
          f"EE start=({ee_start[0]:.3f}, {ee_start[1]:.3f}, {ee_start[2]:.3f})", flush=True)
    error = reach(target, quat_start, 350)
    capture("01_reach.png")

    print("[phase] close gripper", flush=True)
    command_gripper(1.0, 100)

    print("[phase] lift", flush=True)
    lifted = (target[0], target[1], target[2] + 0.28)
    error = reach(lifted, quat_start, 250)
    place_camera(
        (base[0] + 1.40, base[1] + 2.30, 1.45),
        (base[0] + 0.10, base[1], 0.50),
    )
    capture("02_lift.png")

    print("[phase] open gripper", flush=True)
    command_gripper(0.0, 100)

    print("[phase] home", flush=True)
    hold(350)
    capture("03_home.png")

    print(f"KEY_NUMBERS reach_error_m={error:.4f} final_EE="
          f"{tuple(round(v, 3) for v in vec3(arm.data.body_pos_w[0, ee_id]))}", flush=True)
    print("[demo] phases complete; viewport stays interactive. Ctrl+C to quit.", flush=True)

    while session.simulation_app.is_running():
        hold(1)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots-dir", type=Path, default=ROOT / "tmp" / "d1_demo_shots")
    parser.add_argument("--no-captures", action="store_true", help="GUI-only, skip PNG frames")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
