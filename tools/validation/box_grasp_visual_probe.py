#!/usr/bin/env python3
"""Headless Isaac Sim visual/pose audit for the G1/Pepper box skill.

This probe deliberately inspects the live articulation and rigid-object
state.  A completed state-machine phase is not considered sufficient: the
report records end-effector distances and arm joint motion, and captures one
RGB frame at each phase boundary.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for package in ("EAI", "EAI_assets", "EAI_hmrs"):
    sys.path.insert(0, str(ROOT / "source" / package))
sys.path.insert(0, str(ROOT))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _live_snapshot(base_env: Any, robot_name: str, runtime: Any) -> dict[str, Any]:
    robot = base_env.scene.articulations[robot_name]
    snapshot: dict[str, Any] = {
        "phase": runtime.state.phase.value,
        "phase_elapsed_s": float(runtime.state.phase_elapsed_s),
        "attached": bool(runtime.state.attached),
        "grasp_ready": bool(runtime.state.grasp_ready),
        "root_position_w": _jsonable(robot.data.root_pos_w[0]),
        "root_quaternion_w": _jsonable(robot.data.root_quat_w[0]),
        "ee_position_w": {},
        "arm_joint_positions": {},
        "target_distances_m": dict(runtime.last_target_distances),
    }
    for side in ("left", "right"):
        joint_ids, body_id = runtime._resolve(robot, side)
        if body_id is not None:
            snapshot["ee_position_w"][side] = _jsonable(robot.data.body_pos_w[0, body_id])
        snapshot["arm_joint_positions"][side] = _jsonable(robot.data.joint_pos[0, joint_ids])
    box_name = runtime.state.config.cube_prim_name
    box = base_env.scene.rigid_objects[box_name]
    snapshot["box_position_w"] = _jsonable(box.data.root_pos_w[0])
    try:
        masses = box.root_physx_view.get_masses()
        snapshot["box_mass_kg"] = float(masses[0, 0].item())
    except Exception:
        snapshot["box_mass_kg"] = None
    return snapshot


class _RgbCapture:
    def __init__(self, app: Any, output_dir: Path) -> None:
        from isaacsim.core.utils.viewports import set_camera_view
        import omni.kit.renderer_capture as renderer_capture

        self._app = app
        self._renderer_capture = renderer_capture.acquire_renderer_capture_interface()
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        set_camera_view(eye=(2.35, -2.8, 1.85), target=(0.34, 0.0, 0.72))

    def capture(self, name: str) -> str:
        path = self._output_dir / f"{name}.png"
        self._renderer_capture.capture_next_frame_swapchain(str(path))
        for _ in range(12):
            self._app.update()
            if path.exists() and path.stat().st_size > 0:
                break
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"viewport capture did not produce {path}")
        return str(path)

    def close(self) -> None:
        wait = getattr(self._renderer_capture, "wait_async_capture", None)
        if wait is not None:
            wait()


def run(args: argparse.Namespace) -> dict[str, Any]:
    import torch

    from simulator import SimulatorLaunchConfig, open_simulator_session

    if args.box_size is not None:
        os.environ["EAI_BOX_GRASP_SIZE"] = ",".join(str(item) for item in args.box_size)
    selection_path = ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "envs" / f"{args.env}.json"
    selection_data = json.loads(selection_path.read_text(encoding="utf-8"))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = SimulatorLaunchConfig(
        env=args.env,
        device=args.device,
        seed=17,
        headless=not args.gui,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        resolved_env_name=args.env,
        selection_data=selection_data,
        app_launcher_args={"headless": not args.gui, "enable_cameras": True, "device": args.device},
    )

    report: dict[str, Any] = {
        "env": args.env,
        "selection": selection_data,
        "frames": {},
        "samples": [],
        "errors": [],
        "box_size_override": list(args.box_size) if args.box_size is not None else None,
    }
    report_path = output_dir / "report.json"

    def flush_report() -> None:
        report_path.write_text(
            json.dumps(_jsonable(report), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(f"[box-grasp-probe] opening {args.env} on {args.device}", flush=True)
    with open_simulator_session(config) as session:
        print("[box-grasp-probe] session ready", flush=True)
        base_env = session.base_env
        if len(session.possible_agents) != 1:
            raise RuntimeError(f"expected one robot agent, got {session.possible_agents}")
        robot_name = session.possible_agents[0]
        controller_dict = base_env._controllers_dict[robot_name]
        runtime = controller_dict["runtime"]
        capture = _RgbCapture(session.simulation_app, output_dir) if args.gui else None
        try:
            if capture is not None:
                print("[box-grasp-probe] RGB capture ready", flush=True)
                report["frames"]["initial"] = capture.capture("initial")
                print("[box-grasp-probe] initial frame saved", flush=True)
            previous_phase = runtime.state.phase.value
            actions = {robot_name: torch.zeros((1, 3), device=session.device)}
            for step_index in range(args.max_steps):
                if step_index == 0:
                    print("[box-grasp-probe] first env.step", flush=True)
                session.env.step(actions)
                if step_index == 0:
                    print("[box-grasp-probe] first env.step done", flush=True)
                phase = runtime.state.phase.value
                if step_index % args.sample_every == 0 or phase != previous_phase:
                    sample = _live_snapshot(base_env, robot_name, runtime)
                    sample["step"] = step_index
                    report["samples"].append(sample)
                if phase != previous_phase:
                    frame_name = {
                        "approach": "approach",
                        "grasp": "grasp",
                        "carry": "carry",
                        "place": "place",
                        "release": "release",
                        "return": "return",
                        "done": "done",
                    }.get(phase)
                    if frame_name and capture is not None:
                        report["frames"][frame_name] = capture.capture(frame_name)
                    previous_phase = phase
                if runtime.last_error and runtime.last_error not in report["errors"]:
                    report["errors"].append(runtime.last_error)
                if phase == "done":
                    # Allow a few extra steps for the released dynamic box to
                    # settle under gravity before the final physical sample.
                    for _ in range(30):
                        session.env.step(actions)
                    report["final"] = _live_snapshot(base_env, robot_name, runtime)
                    break
            else:
                report["final"] = _live_snapshot(base_env, robot_name, runtime)
            # Isaac Sim's native shutdown can terminate the interpreter while
            # leaving the session context. Persist the evidence while Kit is
            # still alive so a headless audit always leaves a report behind.
            flush_report()
        finally:
            if capture is not None:
                capture.close()
            flush_report()
    print(json.dumps({"report": str(report_path), "frames": report["frames"], "errors": report["errors"], "final": report.get("final")}, ensure_ascii=False), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=("g1_box_grasp", "pepper_box_grasp"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--gui", action="store_true", help="Keep a viewport open and save phase screenshots.")
    parser.add_argument("--max-steps", type=int, default=900)
    parser.add_argument("--sample-every", type=int, default=20)
    parser.add_argument(
        "--box-size",
        type=lambda value: tuple(float(item.strip()) for item in value.split(",")),
        default=None,
        metavar="X,Y,Z",
        help="Override the dynamic box dimensions in metres for this validation run.",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
