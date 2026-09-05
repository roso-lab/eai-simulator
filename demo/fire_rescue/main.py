from __future__ import annotations

import argparse
import traceback
from pathlib import Path

from simulator import (
    SimulatorLaunchConfig,
    _ensure_repo_sources_on_path,
    open_simulator_session,
)

from .algorithm_paths import ensure_fire_rescue_algorithm_paths
from .config import FireRescueConfig
from .experiment import configure_fire_rescue_env_cfg, run_robot_baseline_experiment


def build_parser() -> argparse.ArgumentParser:
    ensure_fire_rescue_algorithm_paths()
    from .runtime.llm_presets import DEFAULT_EMOS_LLM_PRESET, EMOS_LLM_PRESETS

    defaults = FireRescueConfig.__dataclass_fields__
    parser = argparse.ArgumentParser(
        description="Fire Rescue robot-only demo. Opens the EAI simulator through simulator.py.",
    )
    parser.add_argument(
        "--env",
        default="EAI-Factory-v0",
        help="JSON environment name from source/EAI_hmrs/EAI_hmrs/envs, without .json.",
    )
    parser.add_argument("--device", default="cuda:0", help="Simulation device.")
    parser.add_argument("--num-envs", "--num_envs", dest="num_envs", type=int, default=1)
    parser.add_argument("--headless", action="store_true", help="Run Isaac headless.")
    parser.add_argument("--map-yaml", default="", help="Override factory map YAML path.")
    parser.add_argument(
        "--emos-llm-preset",
        type=str,
        default=DEFAULT_EMOS_LLM_PRESET,
        choices=list(EMOS_LLM_PRESETS.keys()),
        help="EMOS discussion LLM preset.",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=defaults["trials"].default,
        help="Number of robot-only trials.",
    )
    parser.add_argument(
        "--trial-hazard-ids",
        default=defaults["trial_hazard_ids"].default,
        help="Comma-separated hazard ids for robot-only trials.",
    )
    parser.add_argument(
        "--auto-fire-delay",
        type=float,
        default=defaults["auto_fire_delay"].default,
        help="Seconds before the robot-only experiment auto-spawns the fire.",
    )
    parser.add_argument(
        "--real-time",
        dest="real_time",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Throttle the robot-only loop to env.step_dt.",
    )
    parser.add_argument(
        "--replay-verify-dir",
        default="",
        help="Enable deterministic replay verification and write the episode here.",
    )
    parser.add_argument(
        "--replay-verify-steps",
        type=int,
        default=50,
        help="Number of factual/replay steps in verification mode.",
    )
    parser.add_argument(
        "--replay-local-fallback",
        action="store_true",
        help="Use deterministic local EMOS assignment during replay verification.",
    )
    parser.add_argument(
        "--branch-interactive",
        action="store_true",
        help="Keep the Factory session open after the fact run for assignment branching.",
    )
    parser.add_argument(
        "--branch-checkpoint-dir",
        default="tmp/replay/fire_rescue_branch",
        help="Directory for the interactive assignment checkpoint.",
    )
    return parser


def resolve_default_map_yaml() -> Path:
    """Resolve the default Factory map after repository packages are on sys.path."""

    _ensure_repo_sources_on_path()
    return FireRescueConfig().map_yaml


def validate_fire_rescue_cli_args(args: argparse.Namespace) -> None:
    """Reject interactive workflows that cannot receive dashboard commands."""

    if bool(getattr(args, "branch_interactive", False)) and bool(
        getattr(args, "headless", False)
    ):
        raise ValueError("interactive branch requires a dashboard; remove --headless")


def main() -> None:
    args = build_parser().parse_args()
    try:
        validate_fire_rescue_cli_args(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    real_time = bool(args.real_time) if args.real_time is not None else False
    demo_config = FireRescueConfig(
        map_yaml=Path(args.map_yaml) if args.map_yaml else resolve_default_map_yaml(),
        emos_llm_preset=str(args.emos_llm_preset),
        trials=max(1, int(args.trials)),
        trial_hazard_ids=str(args.trial_hazard_ids),
        auto_fire_delay=float(args.auto_fire_delay),
        headless=bool(args.headless),
        real_time=real_time,
        replay_verify_dir=Path(args.replay_verify_dir) if args.replay_verify_dir else None,
        replay_verify_steps=max(1, int(args.replay_verify_steps)),
        replay_local_fallback=bool(args.replay_local_fallback),
        branch_interactive=bool(args.branch_interactive),
        branch_checkpoint_dir=Path(args.branch_checkpoint_dir) if args.branch_interactive else None,
    )
    launch_config = SimulatorLaunchConfig(
        env=args.env,
        num_envs=args.num_envs,
        device=args.device,
        headless=bool(args.headless),
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        env_cfg_hook=configure_fire_rescue_env_cfg,
    )

    with open_simulator_session(launch_config) as sim:
        try:
            run_robot_baseline_experiment(sim, demo_config)
        except Exception:
            traceback.print_exc()
            raise


if __name__ == "__main__":
    main()
