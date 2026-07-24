from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List

from simulator import SimulatorSession

from .algorithm_paths import ensure_fire_rescue_algorithm_paths
from .config import FireRescueConfig, ROBOT_SPAWN_POSES
from .llm_compat import ensure_openai_typeddict_compat


def parse_trial_hazard_ids(trials: int, raw: str) -> List[int]:
    parts = [int(item.strip()) for item in str(raw or "").split(",") if item.strip()]
    if not parts:
        parts = [1, 2, 3, 4]
    count = max(1, int(trials))
    while len(parts) < count:
        parts.extend([1, 2, 3, 4])
    return parts[:count]


def configure_fire_rescue_env_cfg(env_cfg: Any) -> None:
    if hasattr(env_cfg, "people"):
        env_cfg.people = None
    if hasattr(env_cfg, "people_waypoints"):
        env_cfg.people_waypoints = None
    scene = getattr(env_cfg, "scene", None)
    for robot_name, (position, rotation) in ROBOT_SPAWN_POSES.items():
        robot_cfg = getattr(scene, robot_name, None)
        if robot_cfg is None:
            raise ValueError(f"Fire Rescue requires robot '{robot_name}' in the selected JSON env.")
        robot_cfg.init_state.pos = position
        robot_cfg.init_state.rot = rotation


def build_robot_baseline_args(sim: SimulatorSession, config: FireRescueConfig) -> SimpleNamespace:
    return SimpleNamespace(
        task=sim.env_name,
        env=sim.env_name,
        num_envs=sim.num_envs,
        device=sim.device,
        headless=bool(config.headless),
        max_steps=0,
        real_time=bool(config.real_time),
        factory_map_yaml=str(Path(config.map_yaml).expanduser().resolve()),
        waypoint_step=float(config.waypoint_step),
        nav_prefer_astar=bool(config.prefer_astar),
        auto_fire_delay=float(config.auto_fire_delay),
        emos_llm_preset=str(config.emos_llm_preset),
        trials=max(1, int(config.trials)),
        trial_hazard_ids=str(config.trial_hazard_ids),
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_robot_baseline_experiment(
    sim: SimulatorSession,
    config: FireRescueConfig,
    *,
    run_platform: Callable[..., int] | None = None,
    build_manager: Callable[..., Any] | None = None,
    build_scenario: Callable[[], Any] | None = None,
    interpret_factory_tasks_fn: Callable[..., Any] | None = None,
) -> int:
    ensure_fire_rescue_algorithm_paths()
    ensure_openai_typeddict_compat()

    from .runtime.llm_presets import DEFAULT_EMOS_LLM_PRESET, preset_to_group_discussion_kwargs

    if run_platform is None:
        from .runtime.experiment_loop import run_robot_factory_platform as run_platform
    if build_manager is None or build_scenario is None or interpret_factory_tasks_fn is None:
        from .scenario import build_factory_emos_manager, build_factory_emos_scenario, interpret_factory_tasks

        build_manager = build_manager or build_factory_emos_manager
        build_scenario = build_scenario or build_factory_emos_scenario
        interpret_factory_tasks_fn = interpret_factory_tasks_fn or interpret_factory_tasks

    args_cli = build_robot_baseline_args(sim, config)
    scenario = build_scenario()
    hazard_ids = parse_trial_hazard_ids(args_cli.trials, args_cli.trial_hazard_ids)
    print(f"[Fire Rescue] full robot experiment trials={len(hazard_ids)} hazards={hazard_ids}")

    total_steps = 0
    for index, hazard_id in enumerate(hazard_ids):
        if index > 0:
            sim.env.reset()
        print(f"\n--- Fire Rescue full robot trial {index + 1}/{len(hazard_ids)} | hazard {hazard_id} ---\n")
        preset_id = args_cli.emos_llm_preset or DEFAULT_EMOS_LLM_PRESET
        emos_mgr = build_manager(
            sim.base_env,
            get_group_discussion_llm_kwargs=lambda preset_id=preset_id: preset_to_group_discussion_kwargs(preset_id),
        )
        total_steps += int(
            run_platform(
                simulation_app=sim.simulation_app,
                env=sim.env,
                base_env=sim.base_env,
                env_cfg=sim.env_cfg,
                args_cli=args_cli,
                emos_mgr=emos_mgr,
                scenario=scenario,
                map_yaml=args_cli.factory_map_yaml,
                repo_root=_repo_root(),
                device=sim.device,
                num_envs=sim.num_envs,
                possible_agents=sim.possible_agents,
                auto_fire_delay_s=float(args_cli.auto_fire_delay),
                interpret_factory_tasks_fn=interpret_factory_tasks_fn,
                fixed_auto_hazard_id=int(hazard_id),
                trial_index=index + 1,
                total_trials=len(hazard_ids),
                skip_http_init=(index > 0),
            )
        )
    return total_steps
