# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Headless acceptance probe for the Go2 mixed-stairs controller."""

from __future__ import annotations

import argparse


MIN_STAND_SECONDS = 3.0
MIN_WALK_SECONDS = 4.0
MIN_BASE_HEIGHT_M = 0.25
MAX_PROJECTED_GRAVITY_Z = -0.90
MIN_FORWARD_PROGRESS_M = 0.20
MIN_STAIR_SCAN_RANGE_M = 0.10
EXPECTED_OBSERVATION_DIM = 232
EXPECTED_ACTION_DIM = 12


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0", help="Simulation device request.")
    parser.add_argument("--stand-s", type=float, default=MIN_STAND_SECONDS)
    parser.add_argument("--walk-s", type=float, default=MIN_WALK_SECONDS)
    parser.add_argument("--speed", type=float, default=0.5)
    parser.add_argument("--_result-file", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    from demo.go2_stairs._worker_status import run_worker_process, write_worker_result

    if args._result_file is None:
        raise SystemExit(run_worker_process("demo.go2_stairs.diagnose"))
    if args.stand_s < MIN_STAND_SECONDS:
        raise SystemExit(f"--stand-s must be at least {MIN_STAND_SECONDS:.1f}")
    if args.walk_s < MIN_WALK_SECONDS:
        raise SystemExit(f"--walk-s must be at least {MIN_WALK_SECONDS:.1f}")
    if args.speed <= 0.2:
        raise SystemExit("--speed must exceed the policy command threshold of 0.2 m/s")

    from simulator import (
        SimulatorLaunchConfig,
        _ensure_repo_sources_on_path,
        open_simulator_session,
    )

    _ensure_repo_sources_on_path()
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
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        resolved_env_name="go2_factory_stairs",
        selection_data=interactive_selection_to_dict(selection),
    )

    failures: list[str] = []
    with open_simulator_session(launch_config) as sim:
        import torch

        base_env = sim.base_env
        step_env = sim.env
        agent = sim.possible_agents[0]
        robot = base_env.scene.articulations[agent]
        controller = base_env._controller_configs[agent]
        raycaster = base_env.scene.sensors[f"{agent}_height_scanner"]
        step_dt = float(base_env.step_dt)

        def p(*values):
            print(*values, flush=True)

        def require_finite(label: str, value) -> None:
            if not bool(torch.isfinite(value).all()):
                raise RuntimeError(f"{label} contains non-finite values")

        def current_observation():
            obs = controller.observation_func(base_env, agent)
            require_finite("observation", obs)
            if obs.shape[-1] != EXPECTED_OBSERVATION_DIM:
                raise RuntimeError(
                    f"observation dim {obs.shape[-1]} != {EXPECTED_OBSERVATION_DIM}"
                )
            return obs

        def validate_step_state() -> None:
            require_finite("root state", robot.data.root_state_w)
            require_finite("joint position target", robot.data.joint_pos_target)
            require_finite("height-scan hits", raycaster.data.ray_hits_w)
            current_observation()
            action = base_env.actions_dict.get(agent)
            if action is None:
                raise RuntimeError("policy action is missing after environment step")
            require_finite("policy action", action)
            if action.shape[-1] != EXPECTED_ACTION_DIM:
                raise RuntimeError(f"action dim {action.shape[-1]} != {EXPECTED_ACTION_DIM}")

        def scan_stats(tag: str) -> float:
            hits = raycaster.data.ray_hits_w[0]
            require_finite(f"{tag} height-scan hits", hits)
            base_z = float(robot.data.root_pos_w[0, 2])
            height_range = float(hits[:, 2].max() - hits[:, 2].min())
            p(
                f"[diag] {tag}: hit_z=[{float(hits[:, 2].min()):.3f},"
                f" {float(hits[:, 2].max()):.3f}] range={height_range:.3f}m "
                f"base_z={base_z:.3f}"
            )
            return height_range

        def world_forward_xy():
            quat = robot.data.root_quat_w[0]
            w, x, y, z = (quat[index] for index in range(4))
            forward = torch.stack((1 - 2 * (y * y + z * z), 2 * (x * y + w * z)))
            return forward / torch.linalg.vector_norm(forward).clamp_min(1.0e-6)

        def run(seconds: float, cmd: tuple[float, float, float], tag: str):
            command = torch.tensor([cmd], dtype=torch.float32, device=sim.device)
            steps = max(1, int(seconds / step_dt))
            start_pos = robot.data.root_pos_w[0, :3].clone()
            forward_xy = world_forward_xy()
            min_base_z = float("inf")
            max_gravity_z = float("-inf")
            for index in range(steps):
                step_env.step({agent: command})
                validate_step_state()
                base_z = float(robot.data.root_pos_w[0, 2])
                gravity_z = float(robot.data.projected_gravity_b[0, 2])
                min_base_z = min(min_base_z, base_z)
                max_gravity_z = max(max_gravity_z, gravity_z)
                if index % 50 == 0:
                    p(
                        f"[diag] {tag} t={index * step_dt:4.1f}s base_z={base_z:.3f} "
                        f"gravity_z={gravity_z:+.3f} action_rms="
                        f"{float(base_env.actions_dict[agent].pow(2).mean().sqrt()):.3f}"
                    )
            displacement = robot.data.root_pos_w[0, :2] - start_pos[:2]
            signed_progress = float(torch.dot(displacement, forward_xy))
            dz = float(robot.data.root_pos_w[0, 2] - start_pos[2])
            p(
                f"[diag] {tag} result: signed_forward={signed_progress:+.3f}m "
                f"dz={dz:+.3f}m min_base_z={min_base_z:.3f} "
                f"worst_gravity_z={max_gravity_z:+.3f}"
            )
            return {
                "signed_progress": signed_progress,
                "min_base_z": min_base_z,
                "max_gravity_z": max_gravity_z,
            }

        p("[diag] joint_names =", list(robot.joint_names))
        p("[diag] root_pos =", robot.data.root_pos_w[0].tolist())
        p(
            "[diag] acceptance: "
            f"stand>={MIN_STAND_SECONDS:.1f}s walk>={MIN_WALK_SECONDS:.1f}s "
            f"min_base_z>={MIN_BASE_HEIGHT_M:.2f}m "
            f"gravity_z<={MAX_PROJECTED_GRAVITY_Z:.2f} "
            f"signed_forward>={MIN_FORWARD_PROGRESS_M:.2f}m "
            f"stair_scan_range>={MIN_STAIR_SCAN_RANGE_M:.2f}m"
        )
        for path_key, mesh in (raycaster.meshes or {}).items():
            p(
                f"[diag] warp mesh {path_key}: points={getattr(mesh, 'points', ()).shape} "
                f"indices={getattr(mesh, 'indices', ()).shape}"
            )

        initial_scan_range = scan_stats("initial stair scan")
        current_observation()
        stand = run(args.stand_s, (0.0, 0.0, 0.0), "stand")
        scan_stats("after stand")
        walk = run(args.walk_s, (args.speed, 0.0, 0.0), "walk")

        if initial_scan_range < MIN_STAIR_SCAN_RANGE_M:
            failures.append(
                f"initial stair scan range {initial_scan_range:.3f}m < {MIN_STAIR_SCAN_RANGE_M:.3f}m"
            )
        for phase_name, metrics in (("stand", stand), ("walk", walk)):
            if metrics["min_base_z"] < MIN_BASE_HEIGHT_M:
                failures.append(
                    f"{phase_name} min base height {metrics['min_base_z']:.3f}m < "
                    f"{MIN_BASE_HEIGHT_M:.3f}m"
                )
            if metrics["max_gravity_z"] > MAX_PROJECTED_GRAVITY_Z:
                failures.append(
                    f"{phase_name} worst gravity z {metrics['max_gravity_z']:+.3f} > "
                    f"{MAX_PROJECTED_GRAVITY_Z:+.3f}"
                )
        if walk["signed_progress"] < MIN_FORWARD_PROGRESS_M:
            failures.append(
                f"signed forward progress {walk['signed_progress']:.3f}m < "
                f"{MIN_FORWARD_PROGRESS_M:.3f}m"
            )

        if failures:
            for failure in failures:
                p(f"[diag] FAIL: {failure}")
        else:
            p("[diag] PASS: flat stability, stair scan, finite tensors, and command response")
        write_worker_result(args._result_file, ok=not failures, failures=failures)


if __name__ == "__main__":
    main()
