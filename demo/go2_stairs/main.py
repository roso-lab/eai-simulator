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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0", help="Simulation device.")
    parser.add_argument("--headless", action="store_true", help="Run Isaac headless.")
    parser.add_argument("--speed", type=float, default=0.6, help="Forward speed m/s.")
    parser.add_argument("--cycles", type=int, default=2, help="Climb/descend cycles.")
    parser.add_argument("--climb-s", type=float, default=10.0, help="Seconds per climb.")
    parser.add_argument("--descend-s", type=float, default=10.0, help="Seconds per descent.")
    parser.add_argument("--hold-s", type=float, default=3.0, help="Standstill between phases.")
    parser.add_argument("--real-time", action="store_true", help="Pace the loop to wall clock.")
    return parser


def main() -> None:
    args = build_parser().parse_args()

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

        def run_phase(command: tuple[float, float, float], seconds: float, label: str) -> None:
            cmd = torch.tensor([command], dtype=torch.float32, device=sim.device).repeat(sim.num_envs, 1)
            steps = max(1, int(seconds * steps_per_s))
            print(f"[go2-stairs] {label}: cmd={command} for {seconds:.1f}s")
            start_z = float(robot.data.root_pos_w[0, 2])
            start_pos = robot.data.root_pos_w[0, :3].clone()
            for _ in range(steps):
                if not sim.simulation_app.is_running():
                    return
                loop_start = time.monotonic()
                step_env.step({agent: cmd})
                if args.real_time:
                    pace = step_dt - (time.monotonic() - loop_start)
                    if pace > 0:
                        time.sleep(pace)
            end_pos = robot.data.root_pos_w[0, :3]
            print(
                f"[go2-stairs] {label} done: dz={float(end_pos[2] - start_z):+.2f}m "
                f"travel={float((end_pos[:2] - start_pos[:2]).norm()):.2f}m "
                f"pos=({float(end_pos[0]):.2f},{float(end_pos[1]):.2f},{float(end_pos[2]):.2f})"
            )

        zero = (0.0, 0.0, 0.0)
        forward = (args.speed, 0.0, 0.0)
        backward = (-args.speed, 0.0, 0.0)

        for cycle in range(1, args.cycles + 1):
            print(f"[go2-stairs] === cycle {cycle}/{args.cycles} ===")
            run_phase(zero, args.hold_s, "stand")
            run_phase(forward, args.climb_s, "climb")
            run_phase(zero, args.hold_s, "landing stand")
            run_phase(backward, args.descend_s, "descend")
            run_phase(zero, args.hold_s, "ground stand")
        run_phase(zero, 2.0, "final stand")
        print("[go2-stairs] demo complete")


if __name__ == "__main__":
    main()
