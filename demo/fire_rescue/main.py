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
        "--branch-checkpoint-dir", "--checkpoint-dir",
        default="tmp/replay/fire_rescue_branch",
        help="Output root for task/native checkpoints or interactive assignment branches.",
    )
    parser.add_argument("--replay-native", action="store_true", help="Native Isaac checkpoint toolbar; no HTTP dashboard.")
    parser.add_argument("--replay-exact", action="store_true", help="Zero-tolerance physical replay audit; unsupported state or any measured deviation fails, not a zero-error guarantee.")
    parser.add_argument("--replay-restore-tolerance", type=float, default=1e-5, help="Maximum checkpoint read-back error.")
    parser.add_argument("--replay-native-test-steps", type=int, default=0, help="Opt-in native checkpoint round-trip test; exit after the comparison.")
    parser.add_argument("--task-checkpoints", action="store_true", help="Task-owned automatic checkpoints; supports unattended headless trials.")
    parser.add_argument(
        "--assignment-policy", "--algorithm", dest="assignment_policy", default="emos",
        help="emos, or a trusted module:assign callback for task assignment.",
    )
    parser.add_argument(
        "--rewind", action="store_true",
        help="One-command mode: CPU, one environment, task-owned checkpoints and native restore.",
    )
    parser.add_argument(
        "--rewind-check", action="store_true",
        help="With --rewind, also run the strict native trajectory audit before exiting.",
    )
    return parser


def resolve_default_map_yaml() -> Path:
    """Resolve the default Factory map after repository packages are on sys.path."""

    _ensure_repo_sources_on_path()
    return FireRescueConfig().map_yaml


def validate_fire_rescue_cli_args(args: argparse.Namespace) -> None:
    """Reject interactive workflows that cannot receive dashboard commands."""

    import math

    if getattr(args, "num_envs", 1) != 1:
        raise ValueError(
            "Fire Rescue task logic currently reads env 0 and cannot run independent vectorized missions; "
            "use python -m demo.fire_rescue.checks.parallel_replay --workers 2 --rounds 2 for isolated checks"
        )
    if getattr(args, "rewind_check", False) and not getattr(args, "rewind", False):
        raise ValueError("--rewind-check requires --rewind")
    if getattr(args, "rewind", False):
        if getattr(args, "device", "cuda:0") not in {"cuda:0", "cpu"}:
            raise ValueError("--rewind supports CPU PhysX only; omit --device or use --device cpu")
        args.device = "cpu"
        args.num_envs = 1
        args.trials = 1
        args.task_checkpoints = True
        args.replay_native = True
        if getattr(args, "rewind_check", False):
            args.replay_exact = True
            if not getattr(args, "replay_native_test_steps", 0):
                args.replay_native_test_steps = 20

    policy = getattr(args, "assignment_policy", "emos")
    if getattr(args, "replay_exact", False) and not (getattr(args, "replay_native", False) or getattr(args, "task_checkpoints", False)):
        raise ValueError("--replay-exact requires --replay-native or --task-checkpoints")
    if getattr(args, "task_checkpoints", False) and getattr(args, "trials", 1) != 1:
        raise ValueError("task checkpoints require --trials 1; use a fresh simulator process for each trial")
    if policy != "emos":
        from algorithm.emos.policy import load_assignment_policy

        load_assignment_policy(policy)
        if getattr(args, "replay_local_fallback", False):
            raise ValueError("custom assignment policy cannot be combined with --replay-local-fallback")
    if bool(getattr(args, "replay_native", False) or getattr(args, "task_checkpoints", False)):
        if getattr(args, "branch_interactive", False) or getattr(args, "replay_verify_dir", ""):
            raise ValueError("--replay-native cannot be combined with legacy branch/verification modes")
        if getattr(args, "device", "cpu") != "cpu" or getattr(args, "num_envs", 1) != 1:
            raise ValueError("native high-precision checkpoints require --device cpu --num-envs 1")
        if getattr(args, "headless", False) and not (getattr(args, "replay_native_test_steps", 0) or getattr(args, "task_checkpoints", False)):
            raise ValueError("native replay requires a visible Isaac window; remove --headless")
    if getattr(args, "replay_native_test_steps", 0) < 0:
        raise ValueError("--replay-native-test-steps must be non-negative")
    if getattr(args, "replay_native_test_steps", 0) and not getattr(args, "replay_native", False):
        raise ValueError("--replay-native-test-steps requires --replay-native")
    tolerance = getattr(args, "replay_restore_tolerance", 1e-5)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("--replay-restore-tolerance must be finite and positive")

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
        branch_checkpoint_dir=Path(args.branch_checkpoint_dir) if args.branch_interactive or args.replay_native or args.task_checkpoints else None,
        replay_native=bool(args.replay_native),
        replay_exact=bool(args.replay_exact),
        replay_restore_tolerance=args.replay_restore_tolerance,
        replay_native_test_steps=args.replay_native_test_steps,
        task_checkpoints=bool(args.task_checkpoints),
        assignment_policy=str(args.assignment_policy),
    )
    from functools import partial

    launch_config = SimulatorLaunchConfig(
        env=args.env,
        num_envs=args.num_envs,
        device=args.device,
        headless=bool(args.headless),
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        env_cfg_hook=partial(configure_fire_rescue_env_cfg, exact=bool(args.replay_exact)),
    )

    with open_simulator_session(launch_config) as sim:
        try:
            run_robot_baseline_experiment(sim, demo_config)
        except Exception:
            traceback.print_exc()
            import omni.kit.app

            omni.kit.app.get_app().post_quit(1)
            raise


if __name__ == "__main__":
    main()
