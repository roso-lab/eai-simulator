"""MicroDuck GUI demo: stand -> forward -> turn -> back -> stand, viewport live.

Runs the velstand policy through the project simulator session with a GUI
window so the gait can be watched directly.  The viewport stays interactive
after the phases complete.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SELECTION_PATH = ROOT / "tmp" / "microduck_selection_example.json"


def run(args: argparse.Namespace) -> int:
    from tools.project_runtime import activate_project_runtime

    activate_project_runtime()
    import torch

    from simulator import SimulatorLaunchConfig, open_simulator_session

    selection_data = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))
    config = SimulatorLaunchConfig(
        env="microduck_plane",
        resolved_env_name="microduck_plane",
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
        return _run_in_session(session, torch)


def _run_in_session(session, torch) -> int:
    from pxr import Gf, UsdGeom, UsdLux

    env = session.base_env
    duck = env.scene.articulations["microduck_1"]
    device = env.device

    def cmd(vx, vy, yaw):
        return {"microduck_1": torch.tensor([[vx, vy, yaw]], device=device, dtype=torch.float32)}

    stage = env.sim.stage
    key = UsdLux.DistantLight.Define(stage, "/World/EaiDemoKeyLight")
    key.CreateIntensityAttr(2500.0)
    cam = UsdGeom.Camera.Define(stage, "/World/EaiDemoCam")
    cam.GetFocalLengthAttr().Set(24.0)

    def look_at(eye, target):
        view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0.0, 0.0, 1.0))
        return view.GetInverse()

    cam_xf = UsdGeom.Xformable(cam.GetPrim())
    cam_op = cam_xf.AddTransformOp()

    def follow_camera(dz=(0.55, 0.45, 0.35)):
        base = duck.data.root_pos_w[0]
        eye = (float(base[0]) + dz[0], float(base[1]) + dz[1], dz[2])
        cam_op.Set(look_at(eye, (float(base[0]), float(base[1]), 0.15)))

    try:
        from omni.kit.viewport.utility import get_active_viewport

        vp = get_active_viewport()
        vp.camera_path = "/World/EaiDemoCam"
    except Exception as exc:
        print(f"[info] viewport camera binding skipped: {exc}", flush=True)

    def phase(name, vx, vy, yaw, steps):
        print(f"[phase] {name}", flush=True)
        c = cmd(vx, vy, yaw)
        for i in range(steps):
            env.step(c)
            if i % 25 == 0:
                follow_camera()

    follow_camera()
    phase("stand 2s", 0.0, 0.0, 0.0, 400)
    phase("forward 3s", 0.25, 0.0, 0.0, 600)
    phase("turn in place 2s", 0.0, 0.0, 0.3, 400)
    phase("stand hold 2s", 0.0, 0.0, 0.0, 400)
    z = float(duck.data.root_pos_w[0, 2].item())
    print(f"KEY final_root_z={z:.3f}; viewport stays interactive, Ctrl+C to quit", flush=True)

    zero = cmd(0.0, 0.0, 0.0)
    while session.simulation_app.is_running():
        env.step(zero)
    return 0


def main() -> int:
    return run(argparse.ArgumentParser(description=__doc__).parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
