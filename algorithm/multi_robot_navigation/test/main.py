"""Run the in-process EAI multi-robot navigation component."""

from __future__ import annotations

import argparse
from pathlib import Path
import time

from algorithm.multi_robot_navigation.eai_plugin import (
    EaiMultiRobotNavigationPlugin,
    get_robot_pose_tensors,
)
from simulator import SimulatorLaunchConfig, open_simulator_session


def _goal(value: str) -> tuple[str, tuple[float, float]]:
    try:
        robot_name, coordinates = value.split(":", 1)
        x_text, y_text = coordinates.split(",", 1)
        robot_name = robot_name.strip()
        if not robot_name:
            raise ValueError
        return robot_name, (float(x_text), float(y_text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "goal must use ROBOT:X,Y, for example carter_1:3.0,-2.0"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run EAI multi-robot navigation without a ROS launch file."
    )
    parser.add_argument(
        "--env",
        default="dbcbs_slam_team",
        help="Saved EAI environment JSON name.",
    )
    parser.add_argument(
        "--goal",
        action="append",
        type=_goal,
        default=[],
        metavar="ROBOT:X,Y",
        help="Assign a goal; repeat for multiple robots.",
    )
    parser.add_argument(
        "--exchange",
        action="store_true",
        help="Cycle every ground robot to the next robot's current position.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Open click-to-select viewport controls; this is the default without goals.",
    )
    parser.add_argument("--map-yaml", type=Path, default=None)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--real-time", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=180.0)
    parser.add_argument("--hold-seconds", type=float, default=3.0)
    return parser


def _exchange_goals(
    navigation: EaiMultiRobotNavigationPlugin,
) -> dict[str, tuple[float, float]]:
    names = navigation.possible_agents
    if len(names) < 2:
        raise RuntimeError("--exchange requires at least two ground robots")
    starts = {}
    for name in names:
        position, _ = get_robot_pose_tensors(navigation.base_env, name)
        starts[name] = (float(position[0].item()), float(position[1].item()))
    return {
        name: starts[names[(index + 1) % len(names)]]
        for index, name in enumerate(names)
    }


def _run(args: argparse.Namespace) -> None:
    if args.max_seconds <= 0.0:
        raise SystemExit("--max-seconds must be positive")
    if args.hold_seconds < 0.0:
        raise SystemExit("--hold-seconds cannot be negative")
    interactive = bool(args.interactive or (not args.goal and not args.exchange))
    if interactive and args.headless:
        raise SystemExit("Interactive navigation requires a visible Isaac Sim viewport")

    launch_config = SimulatorLaunchConfig(
        env=args.env,
        num_envs=1,
        device=args.device,
        headless=bool(args.headless),
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
    )
    with open_simulator_session(launch_config) as simulator:
        navigation = EaiMultiRobotNavigationPlugin.from_session(
            simulator, map_yaml=args.map_yaml
        )
        print(
            "[EAI navigation] ground team: "
            + ", ".join(navigation.possible_agents)
        )
        if navigation.excluded_agents:
            print(
                "[EAI navigation] excluded aerial robots: "
                + ", ".join(navigation.excluded_agents)
            )

        interaction = None
        try:
            goals = _exchange_goals(navigation) if args.exchange else dict(args.goal)
            for robot_name, target_xy in goals.items():
                navigation.set_goal(robot_name, target_xy)
                print(
                    f"[EAI navigation] goal {robot_name}: "
                    f"({target_xy[0]:.2f}, {target_xy[1]:.2f})"
                )
            if interactive:
                from algorithm.multi_robot_navigation.ui import EaiNavigationUI

                interaction = EaiNavigationUI(navigation)
                print(
                    "[EAI navigation] interactive viewport ready: "
                    "click robot, click ground, then Start Navigation"
                )
            else:
                result = navigation.start_navigation()
                if not result or not all(result.values()):
                    raise RuntimeError(f"Multi-robot planning failed: {result}")

            step_dt = float(simulator.base_env.step_dt)
            step_count = 0
            completed_at: float | None = None
            while simulator.simulation_app.is_running():
                frame_started = time.perf_counter()
                simulator.env.step(navigation.compute_actions())
                elapsed = step_count * step_dt
                step_count += 1
                state = navigation.state()

                if interaction is not None:
                    interaction.refresh()
                elif state.planning_error:
                    raise RuntimeError(
                        f"Multi-robot planning failed: {state.planning_error}"
                    )
                elif not state.navigating_robots:
                    if completed_at is None:
                        completed_at = elapsed
                        print(f"[EAI navigation] mission complete at {elapsed:.1f}s")
                    if elapsed - completed_at >= args.hold_seconds:
                        break
                if not interactive and elapsed >= args.max_seconds:
                    raise TimeoutError(
                        f"Navigation did not finish within {args.max_seconds:.1f}s"
                    )
                if args.real_time:
                    remaining = step_dt - (time.perf_counter() - frame_started)
                    if remaining > 0.0:
                        time.sleep(remaining)
        finally:
            if interaction is not None:
                interaction.destroy()
            navigation.close()


def main() -> None:
    _run(build_parser().parse_args())


if __name__ == "__main__":
    main()
