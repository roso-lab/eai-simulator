"""Bounded diagnostic command -> controller -> measured endpoint loop.

This is deliberately not a topology route, benchmark task, controller
qualification, or D1--D6 observation.  It checks only that the diagnostic
compatibility adapter can submit a short Carter velocity command to a real
project-local EAI session and that an independent pose predicate can evaluate
the resulting physical state.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace
import traceback

from tools.project_runtime import PROJECT_ROOT, activate_project_runtime, provenance_report


def _vector(value) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    while isinstance(value, (tuple, list)) and len(value) == 1:
        value = value[0]
    return [float(item) for item in value]


def _state(session, robot_id: str) -> dict[str, object]:
    robot = session.base_env.scene.articulations[robot_id]
    position = _vector(robot.data.root_pos_w)[:3]
    quaternion = _vector(robot.data.root_quat_w)[:4]
    velocity = _vector(robot.data.root_lin_vel_w)[:3]
    angular = _vector(robot.data.root_ang_vel_w)[:3]
    w, x, y, z = quaternion
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return {
        "position_m": position,
        "quaternion_wxyz": quaternion,
        "yaw_rad": yaw,
        "linear_velocity_m_s": velocity,
        "angular_velocity_rad_s": angular,
        "speed_m_s": math.hypot(velocity[0], velocity[1]),
        "simulation_time_s": float(session.base_env.sim.current_time),
        "step": int(session.base_env.common_step_counter),
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("diagnostic evidence must stay inside HeteroCoord")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(resolved.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="EAI-Factory-Z1-v0")
    parser.add_argument("--robot", default="carter_1")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--speed", type=float, default=0.12)
    parser.add_argument("--drive-steps", type=int, default=100)
    parser.add_argument("--stop-steps", type=int, default=60)
    parser.add_argument("--settle-steps", type=int, default=40)
    parser.add_argument("--target-distance", type=float, default=0.20)
    parser.add_argument("--target-radius", type=float, default=0.15)
    parser.add_argument("--stopped-speed", type=float, default=0.08)
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "artifacts" / "project_runtime" / "command_loop.json")
    args = parser.parse_args()
    if (not math.isfinite(args.speed) or args.speed <= 0
            or not all(type(value) is int and 1 <= value <= 200
                       for value in (args.drive_steps, args.stop_steps, args.settle_steps))
            or not all(math.isfinite(value) and value > 0 for value in (
                args.target_distance, args.target_radius, args.stopped_speed))):
        parser.error("speeds/distances must be positive finite and step counts in [1, 200]")

    output = args.output.expanduser().resolve()
    if output.exists():
        parser.error("--output already exists; use a new path to preserve the prior receipt")

    activate_project_runtime()
    report: dict[str, object] = {
        "schema": "heterocoord.command_loop_diagnostic.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_scope": "diagnostic_command_controller_external_pose_loop",
        "formal_topology_navigation": False,
        "physical_skill_qualified": False,
        "paper_score_eligible": False,
        "environment": args.env,
        "robot_id": args.robot,
        "command": {
            "type": "velocity",
            "value": [args.speed, 0.0, 0.0],
            "drive_steps": args.drive_steps,
            "stop_value": [0.0, 0.0, 0.0],
            "stop_steps": args.stop_steps,
        },
        "external_endpoint_rule": {
            "target_distance_m": args.target_distance,
            "target_radius_m": args.target_radius,
            "maximum_final_speed_m_s": args.stopped_speed,
        },
        "provenance": provenance_report(),
        "status": "failed_before_session",
        "diagnostic_pass": False,
        "trajectory": [],
    }
    try:
        from algorithm.common_coordination.contract import Assignment, FailureCode
        from algorithm.skills.contracts import SkillRequest
        from benchmark.eai_adapter import (
            CommandNavigationSkillAdapter,
            EaiSessionRuntime,
            EaiTaskExecutor,
        )
        from simulator import SimulatorLaunchConfig, open_simulator_session

        selection_path = (
            PROJECT_ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "envs"
            / f"{args.env}.json")
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        config = SimulatorLaunchConfig(
            env=args.env, resolved_env_name=args.env, selection_data=selection,
            num_envs=1, device=args.device, seed=0, headless=True,
            enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
            app_launcher_args={"headless": True, "device": args.device},
        )
        with open_simulator_session(config) as session:
            if args.robot not in session.possible_agents:
                raise ValueError(f"unknown robot: {args.robot}")
            runtime = EaiSessionRuntime(session)
            for _ in range(args.settle_steps):
                runtime.step({})
            initial = _state(session, args.robot)
            yaw = float(initial["yaw_rad"])
            start = initial["position_m"]
            target = [
                start[0] + args.target_distance * math.cos(yaw),
                start[1] + args.target_distance * math.sin(yaw),
                start[2],
            ]
            report.update(status="session_opened", initial_state=initial,
                          target_position_m=target, runtime_device=str(session.device),
                          agents=list(session.possible_agents))

            adapter = CommandNavigationSkillAdapter(
                skill_id="eai.navigation.velocity", command_type="velocity")
            executor = EaiTaskExecutor(
                max_steps=max(args.drive_steps, args.stop_steps),
                command_name="base_velocity",
            )
            assignment = Assignment("diagnostic-forward", args.robot, "diagnostic")
            task = SimpleNamespace(metadata={})

            def step(actions):
                runtime.step(actions)
                state = _state(session, args.robot)
                report["trajectory"].append(state)
                return SimpleNamespace(sim_time_s=state["simulation_time_s"])

            drive = adapter.plan(SkillRequest(
                "diagnostic-drive", "eai.navigation.velocity", args.robot,
                target_pose=tuple(target), task_id=assignment.task_id,
                parameters={"value": [args.speed, 0.0, 0.0],
                            "horizon_ticks": args.drive_steps},
            ))
            drive_feedback = executor.execute(
                assignment, task, scene=session, plan=drive, step=step)
            stop = adapter.plan(SkillRequest(
                "diagnostic-stop", "eai.navigation.velocity", args.robot,
                target_pose=tuple(target), task_id=assignment.task_id,
                parameters={"value": [0.0, 0.0, 0.0],
                            "horizon_ticks": args.stop_steps},
            ))
            stop_feedback = executor.execute(
                assignment, task, scene=session, plan=stop, step=step)
            final = _state(session, args.robot)
            target_error = math.dist(final["position_m"][:2], target[:2])
            progress = ((final["position_m"][0] - start[0]) * math.cos(yaw)
                        + (final["position_m"][1] - start[1]) * math.sin(yaw))
            lateral = abs(-(final["position_m"][0] - start[0]) * math.sin(yaw)
                          + (final["position_m"][1] - start[1]) * math.cos(yaw))
            applied = (drive_feedback.failure_code is FailureCode.NONE
                       and stop_feedback.failure_code is FailureCode.NONE)
            endpoint = target_error <= args.target_radius
            stopped = float(final["speed_m_s"]) <= args.stopped_speed
            report.update(
                status="diagnostic_completed",
                drive_feedback=asdict(drive_feedback),
                stop_feedback=asdict(stop_feedback),
                final_state=final,
                along_track_progress_m=progress,
                lateral_displacement_m=lateral,
                target_error_m=target_error,
                command_chain_applied=applied,
                endpoint_predicate=endpoint,
                stopped_predicate=stopped,
                diagnostic_pass=applied and endpoint and stopped,
                session_close_observed=False,
            )
            _write(args.output, report)
        report["session_close_observed"] = True
        _write(args.output, report)
        return 0 if report["diagnostic_pass"] else 1
    except BaseException as exc:
        report.update(status="failed", diagnostic_pass=False,
                      error_type=type(exc).__name__, error=str(exc),
                      traceback=traceback.format_exc())
        _write(args.output, report)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
