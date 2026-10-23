"""Stage-4 physics smoke test: Unitree Go2 quadruped with a mounted D1 arm.

Builds the interactive ``go2 + d1`` plane selection through the project
runtime (``simulator.open_simulator_session``), then checks standing, mount
integrity, world-frame differential IK reach, and mirrored gripper closure
with real Replicator renders as acceptance evidence.  Exit code 0 means every
checkpoint passed, 1 means at least one failed.
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

SELECTION_PATH = ROOT / "tmp" / "d1_go2_selection_example.json"

# Mount geometry from EAI_assets.robots.d1_mount.D1_MOUNT_PROFILES["go2"]:
# arm base_link welded to go2 "base" at (0, 0, 0.15).
MOUNT_OFFSET_M = (0.0, 0.0, 0.15)
MOUNT_TOLERANCE_M = 0.03

CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return bool(ok)


def look_at_matrix(eye, target):
    """World transform of a USD camera (-Z forward, +Y up) at eye looking at target."""
    from pxr import Gf

    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0.0, 0.0, 1.0))
    return view.GetInverse()


def vec3(tensor):
    return (float(tensor[0]), float(tensor[1]), float(tensor[2]))


def run(args: argparse.Namespace) -> bool:
    from tools.project_runtime import activate_project_runtime

    activate_project_runtime()
    import numpy as np
    import torch
    from PIL import Image

    from simulator import SimulatorLaunchConfig, open_simulator_session

    selection_data = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    args.shots_dir.mkdir(parents=True, exist_ok=True)

    config = SimulatorLaunchConfig(
        env="go2_d1_plane",
        resolved_env_name="go2_d1_plane",
        selection_data=selection_data,
        num_envs=1,
        device="cpu",
        seed=0,
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        app_launcher_args={
            "headless": True,
            "device": "cpu",
            "enable_cameras": True,
            "renderer": "RTX",
        },
    )
    with open_simulator_session(config) as session:
        return _run_in_session(session, args, np, torch, Image)


def _run_in_session(session, args, np, torch, Image) -> bool:
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

    # ------------------------------------------------------------------ build
    joint_names = (*D1_MODEL_SPEC.joint_names, *D1_MODEL_SPEC.gripper_joint_names)
    arm_joint_ids, resolved = arm.find_joints(list(joint_names), preserve_order=True)
    arm_ok = (
        arm is not None
        and arm.num_joints == len(joint_names)
        and len(arm_joint_ids) == len(joint_names)
        and list(resolved) == list(joint_names)
    )
    record(
        "build.mounted_articulation",
        arm_ok,
        f"go2_1_arm joints={arm.num_joints} resolved={[str(name) for name in resolved]} "
        f"bodies={list(arm.body_names)}",
    )
    if not arm_ok:
        return False

    ee_ids, _ = arm.find_bodies(["Link6"], preserve_order=True)
    base_link_ids, _ = arm.find_bodies(["base_link"], preserve_order=True)
    ee_id = int(ee_ids[0])
    if not base_link_ids:
        record("build.base_link_body", False, "base_link missing from arm articulation")
        return False
    arm_base_id = int(base_link_ids[0])

    # ------------------------------------------------------- camera + lights
    timeline = omni.timeline.get_timeline_interface()
    stage = env.sim.stage
    key = UsdLux.DistantLight.Define(stage, "/World/EaiSmokeKeyLight")
    key.CreateIntensityAttr(2500.0)
    UsdGeom.Xformable(key).AddRotateXYZOp().Set(Gf.Vec3f(35.0, -30.0, 0.0))

    camera_prim = UsdGeom.Camera.Define(stage, "/World/EaiSmokeCam")
    camera_prim.GetFocalLengthAttr().Set(24.0)
    transform_op = UsdGeom.Xformable(camera_prim).AddTransformOp()
    render_product = rep.create.render_product("/World/EaiSmokeCam", (1280, 720))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach(render_product)

    def place_camera(eye, target) -> None:
        transform_op.Set(look_at_matrix(eye, target))

    def capture(filename: str, warmup: int | None = None) -> Path:
        import time

        warmup = args.rt_warmup if warmup is None else warmup
        # Pause the physics timeline, then pump the Kit update loop so the
        # RTX render product and the RGB annotator converge on demand.
        # rep.orchestrator.step blocks forever inside a live Isaac Lab env
        # session because its render gate never ticks without app updates;
        # simulation_app.update() is the pattern this repo's rendering
        # evidence probes already use.
        timeline.pause()
        try:
            started = time.time()
            for index in range(warmup):
                session.simulation_app.update()
                if time.time() - started > args.rt_budget_s:
                    print(f"[info] render {filename}: warm-up budget reached "
                          f"after {index + 1} updates", flush=True)
                    break
            frame = np.ascontiguousarray(np.asarray(rgb.get_data())[:, :, :3])
        finally:
            timeline.play()
        if frame.size == 0 or frame.shape[0] < 2:
            raise RuntimeError(f"RGB annotator returned an empty frame for {filename}")
        path = args.shots_dir / filename
        Image.fromarray(frame).save(path)
        brightness = float(frame.mean())
        record(f"shot.{filename}", brightness > 20.0,
               f"mean_brightness={brightness:.1f} ({time.time() - started:.1f} s, "
               f"{warmup} updates)")
        return path

    default_targets = arm.data.default_joint_pos.clone()

    def step_env(hold_arm_defaults: bool = True, hold_grip: bool = True) -> None:
        if hold_arm_defaults:
            arm.set_joint_position_target(default_targets)
        if hold_grip:
            hold_gripper_position(arm, D1_MODEL_SPEC)
        arm.write_data_to_sim()
        env.step(actions)

    # ------------------------------------------------------------ stand check
    heights = []
    for _ in range(100):
        step_env()
        heights.append(float(go2.data.root_pos_w[0, 2].item()))
    finite = bool(
        torch.isfinite(go2.data.root_state_w).all()
        and torch.isfinite(arm.data.root_state_w).all()
        and torch.isfinite(go2.data.joint_pos).all()
        and torch.isfinite(arm.data.joint_pos).all()
    )
    mean_height = sum(heights) / len(heights)
    height_span = max(heights) - min(heights)
    settled = heights[50:]
    settled_span = max(settled) - min(settled)
    record("stand.base_height", 0.25 <= mean_height <= 0.50,
           f"mean_z={mean_height:.3f} m over 100 steps")
    # The first ~1 s absorbs the drop from the 0.4 m spawn pose to stance;
    # judge fluctuation on the settled second half, as on hardware.
    record("stand.height_stability", settled_span < 0.05,
           f"settled_span={settled_span:.3f} m (full span {height_span:.3f} m, "
           f"max {max(heights):.3f}, min {min(heights):.3f})")
    record("stand.no_nan", finite, f"go2+arm root/joint states finite={finite}")

    go2_base = vec3(go2.data.root_pos_w[0])
    place_camera(
        (go2_base[0] + 1.80, go2_base[1] - 2.05, 1.70),
        (go2_base[0] - 0.10, go2_base[1] + 0.05, 0.50),
    )
    # First capture absorbs one-time RTX shader/material compilation.
    capture("01_stand.png", warmup=args.rt_warmup_first)

    # ------------------------------------------------------ mount follow check
    def mount_offset():
        return tuple(
            float(arm.data.body_pos_w[0, arm_base_id, axis].item()
                  - go2.data.root_pos_w[0, axis].item())
            for axis in range(3)
        )

    offset_a = mount_offset()
    for _ in range(50):
        step_env()
    offset_b = mount_offset()

    def offset_ok(offset):
        return all(
            abs(offset[axis] - MOUNT_OFFSET_M[axis]) <= MOUNT_TOLERANCE_M
            for axis in range(3)
        )

    record("mount.offset_before", offset_ok(offset_a),
           f"arm base_link - go2 base = ({offset_a[0]:+.3f}, {offset_a[1]:+.3f}, {offset_a[2]:+.3f}) m")
    record("mount.offset_after_50_steps", offset_ok(offset_b),
           f"arm base_link - go2 base = ({offset_b[0]:+.3f}, {offset_b[1]:+.3f}, {offset_b[2]:+.3f}) m")

    # --------------------------------------------------------------- IK reach
    ee_start = vec3(arm.data.body_pos_w[0, ee_id])
    quat_start = arm.data.body_quat_w[0, ee_id]
    target = (
        ee_start[0] + 0.15,
        ee_start[1],
        ee_start[2] - 0.10,
    )
    target_quat_wxyz = [float(value) for value in quat_start]
    print(
        f"[info] ik: EE start=({ee_start[0]:.3f}, {ee_start[1]:.3f}, {ee_start[2]:.3f}) "
        f"target=({target[0]:.3f}, {target[1]:.3f}, {target[2]:.3f})",
        flush=True,
    )

    ee_error = math.inf
    for step in range(300):
        applied = apply_end_effector_pose(
            env, arm, "go2_1", D1_MODEL_SPEC,
            list(target), target_quat_wxyz,
        )
        hold_gripper_position(arm, D1_MODEL_SPEC)
        arm.write_data_to_sim()
        env.step(actions)
        ee_now = vec3(arm.data.body_pos_w[0, ee_id])
        ee_error = math.dist(ee_now, target)
        if step % 50 == 0 or step == 299:
            print(f"[info] ik step {step}: EE=({ee_now[0]:.3f}, {ee_now[1]:.3f}, "
                  f"{ee_now[2]:.3f}) error={ee_error:.4f} m applied={applied}", flush=True)
    record("ik.apply_returned_true", applied is True, f"last apply_end_effector_pose -> {applied}")
    record("ik.position_error", ee_error < 0.06, f"final EE error={ee_error:.4f} m (< 0.06)")

    ee_final = vec3(arm.data.body_pos_w[0, ee_id])
    base_now = vec3(go2.data.root_pos_w[0])
    place_camera(
        (base_now[0] + 0.90, base_now[1] + 2.60, 1.35),
        (base_now[0] + 0.15, base_now[1], 0.55),
    )
    capture("02_ik_reach.png")
    place_camera(
        (ee_final[0] + 0.55, ee_final[1] + 0.55, ee_final[2] + 0.35),
        ee_final,
    )
    capture("03_ik_close.png")

    # ------------------------------------------------------------ gripper
    gripper_ids, gripper_names = arm.find_joints(
        list(D1_MODEL_SPEC.gripper_joint_names), preserve_order=True)

    def command_gripper(closure01: float, steps: int) -> tuple[float, float]:
        stroke, mirrored = d1_gripper_targets(closure01)
        wanted = torch.tensor([[stroke, mirrored]], device=device, dtype=torch.float32)
        # Hold every arm joint at its measured pose; only the fingers move.
        targets = arm.data.joint_pos.clone()
        targets[:, gripper_ids] = wanted
        positions = (0.0, 0.0)
        for _ in range(steps):
            arm.set_joint_position_target(targets)
            arm.write_data_to_sim()
            env.step(actions)
            positions = (
                float(arm.data.joint_pos[0, gripper_ids[0]].item()),
                float(arm.data.joint_pos[0, gripper_ids[1]].item()),
            )
        return positions

    closed = command_gripper(1.0, 80)
    closed_error = max(abs(closed[0] - 0.0), abs(closed[1] - 0.0))
    record("gripper.close", closed_error < 0.005,
           f"fingers=({closed[0]:+.4f}, {closed[1]:+.4f}) m, target (0, 0), err={closed_error:.4f}")

    opened = command_gripper(0.0, 80)
    open_error = max(abs(opened[0] - 0.03), abs(opened[1] + 0.03))
    record("gripper.open", open_error < 0.005,
           f"fingers=({opened[0]:+.4f}, {opened[1]:+.4f}) m, target (0.03, -0.03), err={open_error:.4f}")

    ee_open = vec3(arm.data.body_pos_w[0, ee_id])
    place_camera(
        (ee_open[0] + 0.45, ee_open[1] + 0.45, ee_open[2] + 0.30),
        ee_open,
    )
    capture("04_gripper.png")

    rgb.detach(render_product)
    render_product.destroy()

    print("-" * 72, flush=True)
    passed = sum(1 for _name, ok, _detail in CHECKS if ok)
    for name, ok, detail in CHECKS:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<28} {detail}", flush=True)
    print(f"SUMMARY {passed}/{len(CHECKS)} checks passed", flush=True)
    print(
        "KEY_NUMBERS "
        f"go2_base_height_m={mean_height:.3f} settled_span_m={settled_span:.3f} "
        f"mount_offset_m=({offset_b[0]:+.3f},{offset_b[1]:+.3f},{offset_b[2]:+.3f}) "
        f"ik_ee_error_m={ee_error:.4f} "
        f"gripper_close_m=({closed[0]:+.4f},{closed[1]:+.4f}) "
        f"gripper_open_m=({opened[0]:+.4f},{opened[1]:+.4f})",
        flush=True,
    )
    return passed == len(CHECKS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots-dir", type=Path, default=ROOT / "tmp" / "d1_smoke_shots")
    parser.add_argument("--rt-warmup", type=int, default=8,
                        help="Kit update ticks per screenshot after the first")
    parser.add_argument("--rt-warmup-first", type=int, default=16,
                        help="Kit update ticks for the first screenshot (shader warm-up)")
    parser.add_argument("--rt-budget-s", type=float, default=600.0,
                        help="wall-clock cap on the warm-up loop per screenshot")
    args = parser.parse_args()
    args.shots_dir = args.shots_dir.expanduser().resolve()
    try:
        ok = run(args)
    except Exception:
        import traceback

        traceback.print_exc()
        for name, _ok, detail in CHECKS:
            print(f"{_name_status(_ok)}  {name:<28} {detail}", flush=True)
        print(f"SUMMARY {sum(1 for _n, ok, _d in CHECKS if ok)}/{len(CHECKS)} checks passed "
              "(aborted by exception)", flush=True)
        return 1
    return 0 if ok else 1


def _name_status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


if __name__ == "__main__":
    raise SystemExit(main())
