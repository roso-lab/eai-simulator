"""Stage-5 smoke: MicroDuck velstand stand/walk/turn with real renders.

Builds the microduck plane selection through the project runtime
(``simulator.open_simulator_session``), then checks zero-command standing,
forward walking and yaw turning under the ONNX velstand policy, capturing
Replicator frames as evidence.  Exit code 0 means every checkpoint passed.
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

SELECTION_PATH = ROOT / "tmp" / "microduck_selection_example.json"
CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return bool(ok)


def yaw_from_wxyz(rotation) -> float:
    w, x, y, z = (float(v) for v in rotation)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def look_at_matrix(eye, target):
    from pxr import Gf

    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0.0, 0.0, 1.0))
    return view.GetInverse()


def run(args: argparse.Namespace) -> bool:
    from tools.project_runtime import activate_project_runtime

    activate_project_runtime()

    import torch

    from simulator import SimulatorLaunchConfig, open_simulator_session

    selection_data = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    args.shots_dir.mkdir(parents=True, exist_ok=True)

    config = SimulatorLaunchConfig(
        env="microduck_plane",
        resolved_env_name="microduck_plane",
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
        return _run_in_session(session, args, torch)


def _run_in_session(session, args, torch) -> bool:
    import numpy as np
    import omni.replicator.core as rep
    import omni.timeline
    from PIL import Image
    from pxr import Gf, UsdGeom, UsdLux

    from EAI_assets.controller.rl.microduck_velstand.microduck_velstand import (
        MICRODUCK_VELSTAND_DEFAULT_POSE,
    )

    env = session.base_env
    duck = env.scene.articulations["microduck_1"]

    # 控制器常量是运行时读取的模块全局，会话内（omni 就绪后）补丁即时生效
    import EAI_assets.controller.rl.microduck_velstand.microduck_velstand as velstand

    if args.gyro == "com":
        velstand.MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO = False
    velstand.MICRODUCK_VELSTAND_DECIMATION = args.decim
    print(f"[cfg] gyro={'link' if velstand.MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO else 'com'} "
          f"decim={velstand.MICRODUCK_VELSTAND_DECIMATION}", flush=True)
    device = env.device

    def cmd(vx: float, vy: float, yaw: float):
        return {"microduck_1": torch.tensor([[vx, vy, yaw]], device=device, dtype=torch.float32)}

    zero = cmd(0.0, 0.0, 0.0)

    # ------------------------------------------------------------- camera
    stage = env.sim.stage
    key = UsdLux.DistantLight.Define(stage, "/World/EaiSmokeKeyLight")
    key.CreateIntensityAttr(2500.0)
    camera_prim = UsdGeom.Camera.Define(stage, "/World/EaiSmokeCam")
    camera_prim.GetFocalLengthAttr().Set(24.0)
    transform_op = UsdGeom.Xformable(camera_prim).AddTransformOp()
    timeline = omni.timeline.get_timeline_interface()
    render_product = rep.create.render_product("/World/EaiSmokeCam", (1280, 720))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach(render_product)

    def capture(filename: str, warmup: int | None = None):
        warmup = args.rt_warmup if warmup is None else warmup
        timeline.pause()
        try:
            for _ in range(warmup):
                session.simulation_app.update()
            frame = np.ascontiguousarray(np.asarray(rgb.get_data())[:, :, :3])
        finally:
            timeline.play()
        if frame.size == 0 or frame.shape[0] < 2:
            raise RuntimeError(f"empty frame for {filename}")
        path = args.shots_dir / filename
        Image.fromarray(frame).save(path)
        record(f"shot.{filename}", float(frame.mean()) > 20.0,
               f"brightness={frame.mean():.1f}")

    # -------------------------------------------------------------- stand
    heights = []
    for _ in range(150):
        env.step(zero)
        heights.append(float(duck.data.root_pos_w[0, 2].item()))
    # 会话构建已被控制器推进，初始角无法事后断言；改为零指令站立后的稳态姿态检查
    from EAI_assets.robots.microduck import MICRODUCK_DEFAULT_JOINT_POS
    max_dev = 0.0
    for j, name in enumerate(duck.data.joint_names):
        default = MICRODUCK_DEFAULT_JOINT_POS[str(name)]
        dev = abs(float(duck.data.joint_pos[0, j].item()) - default)
        max_dev = max(max_dev, dev)
    # articulation 关节顺序与 MICRODUCK_JOINT_NAMES 不同，必须按名字对齐
    record("stand.pose_near_default", max_dev <= 0.15,
           f"max |q - default_pose| after stand = {max_dev:.4f} rad (policy holding pose)")
    settled = heights[75:]
    finite = bool(torch.isfinite(duck.data.root_state_w).all() and torch.isfinite(duck.data.joint_pos).all())
    stand_mean = sum(settled) / len(settled)
    record("stand.finite", finite, f"root/joint states finite={finite}")
    record("stand.root_height", 0.09 <= stand_mean <= 0.16,
           f"settled mean z={stand_mean:.3f} m (min {min(settled):.3f}, max {max(settled):.3f}; "
           f"this USD's stand height ≈0.117, official init z 0.125)")
    record("stand.stability", max(settled) - min(settled) < 0.03,
           f"settled span={max(settled) - min(settled):.3f} m")

    base = (float(duck.data.root_pos_w[0, 0].item()), float(duck.data.root_pos_w[0, 1].item()))
    transform_op.Set(look_at_matrix((base[0] + 0.55, base[1] + 0.45, 0.35), (base[0], base[1], 0.20)))
    capture("01_stand.png", warmup=args.rt_warmup_first)

    # -------------------------------------------------------------- walk
    walk_cmd = cmd(0.25, 0.0, 0.0)
    x0 = float(duck.data.root_pos_w[0, 0].item())
    y0 = float(duck.data.root_pos_w[0, 1].item())
    min_h = math.inf
    for _ in range(400):
        env.step(walk_cmd)
        min_h = min(min_h, float(duck.data.root_pos_w[0, 2].item()))
    x1 = float(duck.data.root_pos_w[0, 0].item())
    y1 = float(duck.data.root_pos_w[0, 1].item())
    forward = math.hypot(x1 - x0, y1 - y0)
    record("walk.forward", forward > 0.30, f"displacement={forward:.3f} m in 400 steps (2 s)")
    record("walk.no_fall", min_h > 0.08, f"min root height={min_h:.3f} m")
    record("walk.finite", bool(torch.isfinite(duck.data.root_state_w).all()), "finite after walk")

    base = (float(duck.data.root_pos_w[0, 0].item()), float(duck.data.root_pos_w[0, 1].item()))
    transform_op.Set(look_at_matrix((base[0] + 0.50, base[1] + 0.50, 0.34), (base[0], base[1], 0.18)))
    capture("02_walk.png")

    # -------------------------------------------------------------- turn
    yaw0 = yaw_from_wxyz(duck.data.root_quat_w[0])
    turn_cmd = cmd(0.0, 0.0, 0.5)
    min_h = math.inf
    for _ in range(300):
        env.step(turn_cmd)
        min_h = min(min_h, float(duck.data.root_pos_w[0, 2].item()))
    yaw1 = yaw_from_wxyz(duck.data.root_quat_w[0])
    dyaw = (yaw1 - yaw0 + math.pi) % (2 * math.pi) - math.pi
    record("turn.yaw", abs(dyaw) > 0.30, f"yaw change={dyaw:+.3f} rad in 300 steps (1.5 s)")
    record("turn.no_fall", min_h > 0.08, f"min root height={min_h:.3f} m")

    transform_op.Set(look_at_matrix((base[0] + 0.55, base[1] + 0.45, 0.35), (base[0], base[1], 0.18)))
    capture("03_turn.png")

    print(f"KEY_NUMBERS stand_z={stand_mean:.3f} walk_forward_m={forward:.3f} "
          f"turn_yaw_rad={dyaw:+.3f} gyro={'link' if velstand.MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO else 'com'}",
          flush=True)
    ok = all(check[1] for check in CHECKS)
    print(f"SUMMARY {'PASS' if ok else 'FAIL'} ({sum(1 for c in CHECKS if c[1])}/{len(CHECKS)} checks)", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shots-dir", type=Path, default=ROOT / "tmp" / "microduck_smoke_shots")
    parser.add_argument("--gyro", choices=("link", "com"), default="link")
    parser.add_argument("--stiff", type=float, default=None)
    parser.add_argument("--decim", type=int, default=4)
    parser.add_argument("--rt-warmup", type=int, default=8)
    parser.add_argument("--rt-warmup-first", type=int, default=16)
    args = parser.parse_args()
    return 0 if run(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
