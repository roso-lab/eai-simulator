#!/usr/bin/env python3
"""Isaac Sim evidence probe for autonomous table-to-table box transport."""

from __future__ import annotations

import argparse
import json
import math
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
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _planar_pose(base_env: Any, robot_name: str, controller_dict: dict[str, Any]) -> list[float]:
    robot = base_env.scene.articulations[robot_name]
    if controller_dict.get("locomotion_controller") == "PEPPER_HOLONOMIC_CFG":
        from EAI_assets.controller.traditional.pepper_holonomic.pepper_holonomic import _get_pepper_base_ids

        return [float(item) for item in robot.data.joint_pos[0, _get_pepper_base_ids(robot)].detach().cpu().tolist()]
    root = robot.data.root_pos_w[0]
    quat = robot.data.root_quat_w[0]
    w, x, y, z = (float(item) for item in quat.detach().cpu().tolist())
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return [float(root[0].item()), float(root[1].item()), yaw]


def _body_id(robot: Any, candidates: tuple[str, ...]) -> int | None:
    for candidate in candidates:
        try:
            ids, _ = robot.find_bodies(candidate, preserve_order=True)
        except (AttributeError, TypeError, ValueError):
            continue
        if ids:
            return int(ids[0])
    return None


def _base_body_state(robot: Any, locomotion_controller: str | None) -> dict[str, Any]:
    """Return the moving physical base state, not only a virtual joint value."""

    candidates = (
        ("base_link", "base", "torso")
        if locomotion_controller == "PEPPER_HOLONOMIC_CFG"
        else ("pelvis", "torso_link", "base_link", "base")
    )
    body_id = _body_id(robot, candidates)
    if body_id is None:
        return {
            "body_name_candidates": list(candidates),
            "body_id": None,
            "position_w": _jsonable(robot.data.root_pos_w[0]),
            "quaternion_w": _jsonable(robot.data.root_quat_w[0]),
            "velocity_w": _jsonable(robot.data.root_vel_w[0]),
        }
    velocity = getattr(robot.data, "body_vel_w", None)
    return {
        "body_name_candidates": list(candidates),
        "body_id": body_id,
        "position_w": _jsonable(robot.data.body_pos_w[0, body_id]),
        "quaternion_w": _jsonable(robot.data.body_quat_w[0, body_id]),
        "velocity_w": _jsonable(velocity[0, body_id]) if velocity is not None else None,
    }


def _snapshot(base_env: Any, robot_name: str, controller_dict: dict[str, Any]) -> dict[str, Any]:
    robot = base_env.scene.articulations[robot_name]
    runtime = controller_dict["runtime"]
    transport = controller_dict["transport"]
    box = base_env.scene.rigid_objects[runtime.state.config.cube_prim_name]
    locomotion_controller = controller_dict.get("locomotion_controller")
    data: dict[str, Any] = {
        "grasp": runtime.state.snapshot(),
        "transport": transport.snapshot(),
        "locomotion_controller": locomotion_controller,
        "world_velocity_command": list(controller_dict.get("world_velocity_command", (0.0, 0.0, 0.0))),
        "filtered_world_velocity": _jsonable(controller_dict.get("filtered_world_velocity")),
        "internal_velocity_command": list(controller_dict.get("internal_velocity_command", (0.0, 0.0, 0.0))),
        "locomotion_position_target": _jsonable(controller_dict.get("pepper_position_target")),
        "locomotion_compute_calls": int(controller_dict.get("locomotion_compute_calls", 0)),
        "locomotion_apply_calls": int(controller_dict.get("locomotion_apply_calls", 0)),
        "planar_pose": _planar_pose(base_env, robot_name, controller_dict),
        "root_position_w": _jsonable(robot.data.root_pos_w[0]),
        "root_quaternion_w": _jsonable(robot.data.root_quat_w[0]),
        "root_velocity_w": _jsonable(robot.data.root_vel_w[0]),
        "base_body": _base_body_state(robot, locomotion_controller),
        "box_position_w": _jsonable(box.data.root_pos_w[0]),
        "box_velocity_w": _jsonable(box.data.root_vel_w[0]),
        "target_distances_m": dict(runtime.last_target_distances),
        "ee_position_w": {},
        "arm_joint_positions": {},
    }
    if locomotion_controller == "PEPPER_HOLONOMIC_CFG":
        from EAI_assets.controller.traditional.pepper_holonomic.pepper_holonomic import _get_pepper_base_ids

        base_ids = _get_pepper_base_ids(robot)
        data["base_joint_velocities"] = _jsonable(robot.data.joint_vel[0, base_ids])
    else:
        data["base_joint_velocities"] = None
    for side in ("left", "right"):
        joint_ids, body_id = runtime._resolve(robot, side)
        data["arm_joint_positions"][side] = _jsonable(robot.data.joint_pos[0, joint_ids])
        if body_id is not None:
            data["ee_position_w"][side] = _jsonable(robot.data.body_pos_w[0, body_id])
    try:
        data["box_mass_kg"] = float(box.root_physx_view.get_masses()[0, 0].item())
    except Exception:
        data["box_mass_kg"] = None
    return data


class _RgbCapture:
    def __init__(self, app: Any, output_dir: Path) -> None:
        from isaacsim.core.utils.viewports import set_camera_view
        import omni.kit.renderer_capture as renderer_capture

        self._app = app
        self._capture = renderer_capture.acquire_renderer_capture_interface()
        self._output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        set_camera_view(eye=(2.45, -2.65, 1.85), target=(0.22, 0.38, 0.68))

    def save(self, name: str) -> str:
        if self._capture is None:
            raise RuntimeError("viewport capture is closed")
        path = self._output_dir / f"{name}.png"
        self._capture.capture_next_frame_swapchain(str(path))
        for _ in range(15):
            self._app.update()
            if path.exists() and path.stat().st_size:
                break
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"viewport capture failed: {path}")
        return str(path)

    def close(self) -> None:
        capture, self._capture = self._capture, None
        self._app = None
        if capture is not None:
            wait = getattr(capture, "wait_async_capture", None)
            if wait is not None:
                wait()
        # Drop the native interface while Kit is still alive. Do not shut down
        # the shared capture service: other viewport clients may still own it.


def _evaluate(report: dict[str, Any], controller_dict: dict[str, Any]) -> None:
    final = report.get("final") or {}
    errors = report["errors"]
    transport_cfg = controller_dict["transport"].config
    runtime = controller_dict["runtime"]
    expected_offset = transport_cfg.destination_offset
    expected_box = [
        runtime.state.config.place_position[index] + expected_offset[index]
        for index in range(3)
    ]
    final_transport = final.get("transport", {})
    if final_transport.get("phase") != "done":
        errors.append(f"transport did not finish: {final_transport.get('phase')}")
    samples = report["samples"]
    if not any(sample["grasp"]["attached"] for sample in samples):
        errors.append("box never became attached")
    peak_command = max(
        (math.hypot(*sample["internal_velocity_command"][:2]) for sample in samples),
        default=0.0,
    )
    report["metrics"]["peak_internal_linear_command_m_s"] = peak_command
    if peak_command < 0.05:
        errors.append("composite controller never generated a movement command")
    if int(final.get("locomotion_compute_calls", 0)) <= 0:
        errors.append("movement controller compute path was never called")
    if int(final.get("locomotion_apply_calls", 0)) <= 0:
        errors.append("movement controller apply path was never called")
    if final.get("locomotion_controller") == "PEPPER_HOLONOMIC_CFG" and final.get(
        "locomotion_position_target"
    ) is None:
        errors.append("Pepper holonomic position target was never produced")
    pre_attach = [
        sample
        for sample in samples
        if not sample["grasp"]["attached"]
        and sample["grasp"]["phase"] in {"approach", "grasp"}
    ]
    if not pre_attach:
        errors.append("no pre-attachment tabletop evidence was sampled")
    else:
        pickup_box_z = float(pre_attach[-1]["box_position_w"][2])
        pickup_bottom_error = abs(
            pickup_box_z - runtime.state.config.box_size[2] * 0.5 - transport_cfg.table_top_z
        )
        report["metrics"]["pickup_box_bottom_to_table_top_error_m"] = pickup_bottom_error
        if pickup_bottom_error > 0.025:
            errors.append(
                "dynamic box was not supported by the source table before attachment: "
                f"error={pickup_bottom_error:.3f} m"
            )
    displacement = final_transport.get("displacement", [0.0, 0.0, 0.0])
    displacement_error = math.hypot(
        displacement[0] - expected_offset[0],
        displacement[1] - expected_offset[1],
    )
    report["metrics"]["destination_displacement_error_m"] = displacement_error
    if displacement_error > transport_cfg.arrival_tolerance_m + 0.02:
        errors.append(f"robot did not reach destination: error={displacement_error:.3f} m")
    if samples:
        initial_base = samples[0]["base_body"]["position_w"]
        final_base = final.get("base_body", {}).get("position_w", initial_base)
        actual_base_displacement = [
            float(final_base[index] - initial_base[index]) for index in range(3)
        ]
        actual_base_error = math.hypot(
            actual_base_displacement[0] - expected_offset[0],
            actual_base_displacement[1] - expected_offset[1],
        )
        report["metrics"]["actual_base_body_displacement_m"] = actual_base_displacement
        report["metrics"]["actual_base_body_destination_error_m"] = actual_base_error
        if actual_base_error > transport_cfg.arrival_tolerance_m + 0.03:
            errors.append(
                f"physical base body did not reach destination: error={actual_base_error:.3f} m"
            )
    box_position = final.get("box_position_w", [0.0, 0.0, 0.0])
    box_position_error = math.dist(box_position[:3], expected_box)
    report["metrics"]["final_box_position_error_m"] = box_position_error
    report["metrics"]["expected_box_position_w"] = expected_box
    bottom_error = abs(
        box_position[2] - runtime.state.config.box_size[2] * 0.5 - transport_cfg.table_top_z
    )
    report["metrics"]["box_bottom_to_table_top_error_m"] = bottom_error
    if bottom_error > 0.035:
        errors.append(f"released box is not supported at table height: error={bottom_error:.3f} m")
    if math.hypot(box_position[0] - expected_box[0], box_position[1] - expected_box[1]) > 0.08:
        errors.append(f"released box is not centred on destination table: error={box_position_error:.3f} m")
    if final.get("box_mass_kg") is None or abs(final["box_mass_kg"] - 8.0) > 1e-3:
        errors.append(f"unexpected box mass: {final.get('box_mass_kg')}")
    final_base_velocity = final.get("base_body", {}).get("velocity_w")
    if final_base_velocity is not None:
        final_base_speed = math.hypot(*final_base_velocity[:2])
    else:
        final_base_speed = math.hypot(*(final.get("base_joint_velocities") or [0.0, 0.0])[:2])
    final_box_speed = math.sqrt(sum(float(value) ** 2 for value in final.get("box_velocity_w", [0, 0, 0])[:3]))
    report["metrics"]["final_base_linear_speed_m_s"] = final_base_speed
    report["metrics"]["final_box_linear_speed_m_s"] = final_box_speed
    if final_base_speed > 0.08:
        errors.append(f"robot did not settle after placement: speed={final_base_speed:.3f} m/s")
    if final_box_speed > 0.08:
        errors.append(f"released box did not settle: speed={final_box_speed:.3f} m/s")
    if report["metrics"].get("max_planar_step_jump_m", 0.0) > 0.10:
        errors.append(
            "robot planar trajectory is discontinuous: "
            f"jump={report['metrics']['max_planar_step_jump_m']:.3f} m/step"
        )
    if report["metrics"].get("max_base_body_step_jump_m", 0.0) > 0.10:
        errors.append(
            "physical base trajectory is discontinuous: "
            f"jump={report['metrics']['max_base_body_step_jump_m']:.3f} m/step"
        )
    if report["metrics"].get("max_box_step_jump_m", 0.0) > 0.15:
        errors.append(
            "box trajectory is discontinuous: "
            f"jump={report['metrics']['max_box_step_jump_m']:.3f} m/step"
        )


def run(args: argparse.Namespace) -> dict[str, Any]:
    import torch

    from simulator import SimulatorLaunchConfig, open_simulator_session

    if args.box_size is not None:
        os.environ["EAI_BOX_GRASP_SIZE"] = ",".join(str(item) for item in args.box_size)
    selection_path = ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "envs" / f"{args.env}.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    report: dict[str, Any] = {
        "env": args.env,
        "selection": selection,
        "box_size_override": list(args.box_size) if args.box_size else None,
        "samples": [],
        "frames": {},
        "metrics": {},
        "errors": [],
    }

    def flush() -> None:
        report_path.write_text(json.dumps(_jsonable(report), indent=2, ensure_ascii=False), encoding="utf-8")

    config = SimulatorLaunchConfig(
        env=args.env,
        device=args.device,
        seed=17,
        headless=not args.gui,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        resolved_env_name=args.env,
        selection_data=selection,
        app_launcher_args={"headless": not args.gui, "enable_cameras": True, "device": args.device},
    )
    print(f"[box-transport-probe] opening {args.env} on {args.device}", flush=True)
    with open_simulator_session(config) as session:
        base_env = session.base_env
        robot_name = session.possible_agents[0]
        controller_dict = base_env._controllers_dict[robot_name]
        transport_cfg = controller_dict["transport"].config
        report["fixture"] = {
            "source_table_name": transport_cfg.source_table_name,
            "source_table_position": list(transport_cfg.source_table_position),
            "destination_table_name": transport_cfg.destination_table_name,
            "destination_table_position": list(transport_cfg.destination_table_position),
            "table_size": list(transport_cfg.table_size),
            "table_top_z": transport_cfg.table_top_z,
        }
        capture = _RgbCapture(session.simulation_app, output_dir) if args.gui else None
        previous_transport = controller_dict["transport"].phase.value
        previous_grasp = controller_dict["runtime"].state.phase.value
        actions = {robot_name: torch.zeros((1, 3), device=session.device)}
        previous_continuity: dict[str, list[float]] = {}

        def update_continuity() -> None:
            robot = base_env.scene.articulations[robot_name]
            box = base_env.scene.rigid_objects[controller_dict["runtime"].state.config.cube_prim_name]
            current = {
                "planar": _planar_pose(base_env, robot_name, controller_dict)[:2],
                "base_body": _base_body_state(
                    robot, controller_dict.get("locomotion_controller")
                )["position_w"][:2],
                "box": _jsonable(box.data.root_pos_w[0])[:3],
            }
            for key, values in current.items():
                previous = previous_continuity.get(key)
                if previous is not None:
                    jump = math.dist(values, previous)
                    metric = {
                        "planar": "max_planar_step_jump_m",
                        "base_body": "max_base_body_step_jump_m",
                        "box": "max_box_step_jump_m",
                    }[key]
                    report["metrics"][metric] = max(
                        float(report["metrics"].get(metric, 0.0)), jump
                    )
                previous_continuity[key] = list(values)
        try:
            if capture:
                report["frames"]["initial"] = capture.save("initial")
            for step in range(args.max_steps):
                session.env.step(actions)
                update_continuity()
                transport_phase = controller_dict["transport"].phase.value
                grasp_phase = controller_dict["runtime"].state.phase.value
                changed = transport_phase != previous_transport or grasp_phase != previous_grasp
                if step % args.sample_every == 0 or changed:
                    sample = _snapshot(base_env, robot_name, controller_dict)
                    sample["step"] = step
                    report["samples"].append(sample)
                if changed and capture:
                    key = f"{transport_phase}_{grasp_phase}"
                    if key not in report["frames"]:
                        report["frames"][key] = capture.save(key)
                previous_transport = transport_phase
                previous_grasp = grasp_phase
                runtime_error = controller_dict["runtime"].last_error
                if runtime_error and runtime_error not in report["errors"]:
                    report["errors"].append(runtime_error)
                if transport_phase == "done":
                    for _ in range(50):
                        session.env.step(actions)
                        update_continuity()
                    report["final"] = _snapshot(base_env, robot_name, controller_dict)
                    break
            else:
                report["final"] = _snapshot(base_env, robot_name, controller_dict)
            _evaluate(report, controller_dict)
            if capture:
                report["frames"]["final"] = capture.save("final")
            flush()
        finally:
            if capture:
                capture.close()
            flush()
    print(json.dumps({"report": str(report_path), "errors": report["errors"], "metrics": report["metrics"]}, ensure_ascii=False), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=("g1_box_transport", "pepper_box_transport"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--max-steps", type=int, default=1800)
    parser.add_argument("--sample-every", type=int, default=10)
    parser.add_argument(
        "--box-size",
        type=lambda value: tuple(float(item.strip()) for item in value.split(",")),
        default=None,
        metavar="X,Y,Z",
    )
    args = parser.parse_args()
    report = run(args)
    raise SystemExit(1 if report["errors"] else 0)


if __name__ == "__main__":
    main()
