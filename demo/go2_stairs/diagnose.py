# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Headless diagnostic for the Go2 mixed-stairs controller in the factory scene.

Prints joint order, height-scan ray hit statistics, observation slices, and
action magnitudes while standing, then walks a short forward segment.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda:0", help="Simulation device request.")
    parser.add_argument("--stand-s", type=float, default=3.0)
    parser.add_argument("--walk-s", type=float, default=4.0)
    parser.add_argument("--speed", type=float, default=0.5)
    return parser


def main() -> None:
    args = build_parser().parse_args()

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

    with open_simulator_session(launch_config) as sim:
        import torch

        base_env = sim.base_env
        step_env = sim.env
        agent = sim.possible_agents[0]
        robot = base_env.scene.articulations[agent]
        raycaster = base_env.scene.sensors[f"{agent}_height_scanner"]
        step_dt = float(base_env.step_dt)

        def p(*a):
            print(*a, flush=True)

        p("[diag] joint_names =", list(robot.joint_names))
        p("[diag] root_pos =", robot.data.root_pos_w[0].tolist())
        p("[diag] base height above 0 =", float(robot.data.root_pos_w[0, 2]))

        def scan_stats(tag: str) -> None:
            hits = raycaster.data.ray_hits_w[0]  # (187, 3)
            base_z = float(robot.data.root_pos_w[0, 2])
            rel = hits[:, 2] - base_z
            p(
                f"[diag] {tag}: scan rel z min={float(rel.min()):+.2f} max={float(rel.max()):+.2f} "
                f"mean={float(rel.mean()):+.2f} | hit z min={float(hits[:, 2].min()):.2f} "
                f"max={float(hits[:, 2].max()):.2f} | base_z={base_z:.2f} "
                f"| hit xy spread=({float(hits[:, 0].max() - hits[:, 0].min()):.1f},"
                f"{float(hits[:, 1].max() - hits[:, 1].min()):.1f})"
            )

        def obs_stats(tag: str) -> None:
            obs = base_env._controller_configs[agent].observation_func(base_env, agent)[0]
            p(
                f"[diag] {tag}: obs dim={obs.shape[0]} "
                f"ang_vel={obs[0:3].tolist()} gravity={obs[3:6].tolist()} "
                f"cmd={obs[6:9].tolist()} jpos=[{obs[9]:.2f},{obs[10]:.2f},{obs[11]:.2f},...] "
                f"jvel*0.05=[{obs[21]:.2f},{obs[22]:.2f},...] last_act=[{obs[33]:.2f},...] "
                f"scan mean={float(obs[45:].mean()):+.3f} clipped<=0 frac="
                f"{float((obs[45:] <= -0.999).float().mean()):.2f} >=0.999 frac={float((obs[45:] >= 0.999).float().mean()):.2f}"
            )

        def run(seconds: float, cmd: tuple[float, float, float], tag: str) -> None:
            command = torch.tensor([cmd], dtype=torch.float32, device=sim.device)
            steps = max(1, int(seconds / step_dt))
            start_pos = robot.data.root_pos_w[0, :3].clone()
            for i in range(steps):
                step_env.step({agent: command})
                if i % 50 == 0:
                    gz = float(robot.data.projected_gravity_b[0, 2])
                    p(
                        f"[diag] {tag} t={i * step_dt:4.1f}s base_z={float(robot.data.root_pos_w[0, 2]):.3f} "
                        f"gz_z={gz:+.2f} | act rms="
                        f"{float(base_env.actions_dict[agent].pow(2).mean().sqrt()):.2f}"
                    )
            end_pos = robot.data.root_pos_w[0, :3]
            p(
                f"[diag] {tag} final: dz={float(end_pos[2] - start_pos[2]):+.2f} "
                f"travel={float((end_pos[:2] - start_pos[:2]).norm()):.2f} "
                f"pos=({float(end_pos[0]):.2f},{float(end_pos[1]):.2f},{float(end_pos[2]):.2f})"
            )

        scan_stats("initial scan")
        obs_stats("initial obs")
        run(args.stand_s, (0.0, 0.0, 0.0), "stand")
        scan_stats("after stand")
        obs_stats("after stand")
        run(args.walk_s, (args.speed, 0.0, 0.0), "walk")
        scan_stats("after walk")
        p("[diag] done")


if __name__ == "__main__":
    main()
