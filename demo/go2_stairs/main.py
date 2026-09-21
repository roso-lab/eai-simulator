# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Go2 mixed-stairs factory validation demo.

Opens the EAI factory scene with the trained Go2 mixed-stairs RLS policy
(GO2_MIXED_STAIRS_CFG) and drives a scripted climb/descend cycle over the
factory stairs so the behavior can be watched in the GUI.
"""

from __future__ import annotations

import argparse
import time


MIN_STAIR_ELEVATION_CHANGE_M = 1.40
MAX_STAIR_PROJECTED_GRAVITY_Z = -0.75
MIN_LANDING_BASE_HEIGHT_M = 1.70
MAX_GROUND_BASE_HEIGHT_M = 0.55


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0", help="Simulation device.")
    parser.add_argument("--headless", action="store_true", help="Run Isaac headless.")
    parser.add_argument("--speed", type=float, default=0.8, help="Forward speed m/s.")
    parser.add_argument("--cycles", type=int, default=1, help="Climb/descend cycles.")
    parser.add_argument("--climb-s", type=float, default=40.0, help="Seconds per climb.")
    parser.add_argument("--descend-s", type=float, default=40.0, help="Seconds per descent.")
    parser.add_argument("--hold-s", type=float, default=3.0, help="Standstill between phases.")
    parser.add_argument("--real-time", action="store_true", help="Pace the loop to wall clock.")
    parser.add_argument(
        "--mode",
        choices=("both", "descend", "climb"),
        default="both",
        help="descend = start on the stair landing; climb = start at the stair base.",
    )
    parser.add_argument("--_result-file", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    from demo.go2_stairs._worker_status import run_worker_process, write_worker_result

    if args.cycles < 1:
        raise SystemExit("--cycles must be at least 1")
    if args.speed <= 0.2:
        raise SystemExit("--speed must exceed the policy command threshold of 0.2 m/s")
    if args.climb_s <= 0.0 or args.descend_s <= 0.0:
        raise SystemExit("--climb-s and --descend-s must be positive")
    if args.hold_s < 0.0:
        raise SystemExit("--hold-s cannot be negative")
    if args._result_file is None:
        raise SystemExit(run_worker_process("demo.go2_stairs.main"))

    from simulator import (
        SimulatorLaunchConfig,
        _ensure_repo_sources_on_path,
        open_simulator_session,
    )

    _ensure_repo_sources_on_path()
    # All required assets (factory USD, go2 USD, controller bundle) are local;
    # resolve the saved selection directly to skip the preflight subprocess.
    from EAI.hmrs_env.env_diy.flow import (
        interactive_selection_from_dict,
        interactive_selection_to_dict,
    )
    from EAI.hmrs_env.env_diy.storage import load_task

    selection = interactive_selection_from_dict(load_task("go2_factory_stairs"))
    launch_config = SimulatorLaunchConfig(
        env="go2_factory_stairs",
        num_envs=1,
        device=args.device,
        headless=bool(args.headless),
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        resolved_env_name="go2_factory_stairs",
        selection_data=interactive_selection_to_dict(selection),
    )

    with open_simulator_session(launch_config) as sim:
        import torch

        base_env = sim.base_env
        step_env = sim.env
        agent = sim.possible_agents[0]
        robot = base_env.scene.articulations[agent]
        step_dt = float(base_env.step_dt)
        steps_per_s = 1.0 / step_dt

        def p(*a):
            print(*a, flush=True)

        def teleport(
            position: tuple[float, float, float],
            rotation_wxyz: tuple[float, float, float, float],
        ) -> None:
            pose = torch.tensor(
                [position + rotation_wxyz], dtype=torch.float32, device=sim.device
            ).repeat(sim.num_envs, 1)
            robot.write_root_pose_to_sim(pose)
            robot.write_root_velocity_to_sim(
                torch.zeros(sim.num_envs, 6, device=sim.device)
            )
            robot.reset()
            base_env.actions_dict.pop(agent, None)
            p(f"[go2-stairs] teleported to {position} rot {rotation_wxyz}")

        def world_forward_xy():
            quat = robot.data.root_quat_w[0]
            w, x, y, z = (quat[index] for index in range(4))
            forward_xy = torch.stack((1 - 2 * (y * y + z * z), 2 * (x * y + w * z)))
            return forward_xy / torch.linalg.vector_norm(forward_xy).clamp_min(1.0e-6)

        def run_phase(
            command: tuple[float, float, float],
            seconds: float,
            label: str,
            *,
            hold_heading: bool = False,
            target_dz: float | None = None,
        ):
            cmd = torch.tensor(
                [command], dtype=torch.float32, device=sim.device
            ).repeat(sim.num_envs, 1)
            steps = max(1, int(seconds * steps_per_s))
            p(f"[go2-stairs] {label}: cmd={command} for {seconds:.1f}s")
            start_z = float(robot.data.root_pos_w[0, 2])
            start_pos = robot.data.root_pos_w[0, :3].clone()
            forward_xy = world_forward_xy()
            min_base_z = start_z
            max_gravity_z = float(robot.data.projected_gravity_b[0, 2])
            elapsed = 0.0
            for step_index in range(steps):
                if not sim.simulation_app.is_running():
                    return None
                loop_start = time.monotonic()
                if hold_heading:
                    current_forward = world_forward_xy()
                    cross = (
                        current_forward[0] * forward_xy[1]
                        - current_forward[1] * forward_xy[0]
                    )
                    dot = torch.dot(current_forward, forward_xy).clamp(-1.0, 1.0)
                    yaw_error = torch.atan2(cross, dot)
                    cmd[:, 2] = torch.clamp(2.0 * yaw_error, -1.0, 1.0)
                step_env.step({agent: cmd})
                if not bool(torch.isfinite(robot.data.root_state_w).all()):
                    raise RuntimeError(f"{label}: non-finite robot root state")
                min_base_z = min(min_base_z, float(robot.data.root_pos_w[0, 2]))
                max_gravity_z = max(
                    max_gravity_z, float(robot.data.projected_gravity_b[0, 2])
                )
                elapsed = (step_index + 1) * step_dt
                current_dz = float(robot.data.root_pos_w[0, 2]) - start_z
                if target_dz is not None and (
                    (target_dz >= 0.0 and current_dz >= target_dz)
                    or (target_dz < 0.0 and current_dz <= target_dz)
                ):
                    break
                if args.real_time:
                    pace = step_dt - (time.monotonic() - loop_start)
                    if pace > 0:
                        time.sleep(pace)
            end_pos = robot.data.root_pos_w[0, :3]
            signed_progress = float(torch.dot(end_pos[:2] - start_pos[:2], forward_xy))
            dz = float(end_pos[2] - start_z)
            p(
                f"[go2-stairs] {label} done: elapsed={elapsed:.1f}s dz={dz:+.2f}m "
                f"signed_forward={signed_progress:+.2f}m min_z={min_base_z:.2f}m "
                f"worst_gravity_z={max_gravity_z:+.2f} "
                f"pos=({float(end_pos[0]):.2f},{float(end_pos[1]):.2f},{float(end_pos[2]):.2f})"
            )
            return {
                "dz": dz,
                "final_z": float(end_pos[2]),
                "signed_progress": signed_progress,
                "min_base_z": min_base_z,
                "max_gravity_z": max_gravity_z,
            }

        zero = (0.0, 0.0, 0.0)
        forward = (args.speed, 0.0, 0.0)
        failures: list[str] = []
        # The base faces -Y toward the stairs; the landing faces +Y toward them.
        base_pos = (1.70, -2.60, 0.65)
        base_rot = (0.7071, 0.0, 0.0, -0.7071)
        landing_pos = (1.70, -7.80, 1.95)
        landing_rot = (0.7071, 0.0, 0.0, 0.7071)

        if args.mode in ("climb", "both"):
            for cycle in range(1, args.cycles + 1):
                p(f"[go2-stairs] === climb cycle {cycle}/{args.cycles} ===")
                teleport(base_pos, base_rot)
                run_phase(zero, args.hold_s, "base hold")
                result = run_phase(
                    forward,
                    args.climb_s,
                    "climb",
                    hold_heading=True,
                    target_dz=MIN_STAIR_ELEVATION_CHANGE_M,
                )
                if result is None or result["dz"] < MIN_STAIR_ELEVATION_CHANGE_M:
                    actual = "interrupted" if result is None else f"dz={result['dz']:+.2f}m"
                    failures.append(f"climb cycle {cycle} failed ({actual})")
                elif result["max_gravity_z"] > MAX_STAIR_PROJECTED_GRAVITY_Z:
                    failures.append(
                        f"climb cycle {cycle} lost upright posture "
                        f"(gravity_z={result['max_gravity_z']:+.2f})"
                    )
                hold_result = run_phase(zero, args.hold_s, "post-climb hold")
                if (
                    hold_result is None
                    or hold_result["final_z"] < MIN_LANDING_BASE_HEIGHT_M
                ):
                    actual = (
                        "interrupted"
                        if hold_result is None
                        else f"base_z={hold_result['final_z']:.2f}m"
                    )
                    failures.append(
                        f"climb cycle {cycle} did not settle on the landing ({actual})"
                    )

        if args.mode in ("descend", "both"):
            for cycle in range(1, args.cycles + 1):
                p(f"[go2-stairs] === descent cycle {cycle}/{args.cycles} ===")
                teleport(landing_pos, landing_rot)
                run_phase(zero, args.hold_s, "landing hold")
                result = run_phase(
                    forward,
                    args.descend_s,
                    "descend",
                    target_dz=-MIN_STAIR_ELEVATION_CHANGE_M,
                )
                if result is None or result["dz"] > -MIN_STAIR_ELEVATION_CHANGE_M:
                    actual = "interrupted" if result is None else f"dz={result['dz']:+.2f}m"
                    failures.append(f"descent cycle {cycle} failed ({actual})")
                elif result["max_gravity_z"] > MAX_STAIR_PROJECTED_GRAVITY_Z:
                    failures.append(
                        f"descent cycle {cycle} lost upright posture "
                        f"(gravity_z={result['max_gravity_z']:+.2f})"
                    )
                hold_result = run_phase(zero, args.hold_s, "post-descent hold")
                if (
                    hold_result is None
                    or hold_result["final_z"] > MAX_GROUND_BASE_HEIGHT_M
                ):
                    actual = (
                        "interrupted"
                        if hold_result is None
                        else f"base_z={hold_result['final_z']:.2f}m"
                    )
                    failures.append(
                        f"descent cycle {cycle} did not settle on the ground ({actual})"
                    )

        if failures:
            for failure in failures:
                p(f"[go2-stairs] FAIL: {failure}")
        else:
            p("[go2-stairs] PASS: requested stair directions reached the expected elevation")
        write_worker_result(args._result_file, ok=not failures, failures=failures)


if __name__ == "__main__":
    main()
