from pathlib import Path
from types import SimpleNamespace

import pytest

import demo.fire_rescue.main as main_module
from demo.fire_rescue.config import FireRescueConfig
from demo.fire_rescue.experiment import build_robot_baseline_args
from demo.fire_rescue.main import build_parser


def test_replay_verification_is_disabled_by_default():
    args = build_parser().parse_args([])
    assert args.replay_verify_dir == ""
    assert args.replay_verify_steps == 50
    assert args.replay_local_fallback is False


def test_replay_verification_flags_are_forwarded():
    fake_session = SimpleNamespace(
        env_name="EAI-Factory-v0",
        num_envs=1,
        device="cpu",
    )
    config = FireRescueConfig(
        map_yaml=Path("factory_map.yaml"),
        replay_verify_dir=Path("tmp/replay/fire"),
        replay_verify_steps=12,
        replay_local_fallback=True,
    )
    args = build_robot_baseline_args(fake_session, config)
    assert args.replay_verify_steps == 12
    assert args.replay_local_fallback is True
    assert args.replay_verify_dir == str(Path("tmp/replay/fire"))


def test_default_map_resolution_bootstraps_repository_sources(monkeypatch):
    events = []
    monkeypatch.setattr(main_module, "_ensure_repo_sources_on_path", lambda: events.append("path"))
    monkeypatch.setattr(
        main_module,
        "FireRescueConfig",
        lambda: SimpleNamespace(map_yaml=Path("factory_map.yaml")),
    )

    assert main_module.resolve_default_map_yaml() == Path("factory_map.yaml")
    assert events == ["path"]


@pytest.mark.parametrize("flags", [
    ["--replay-native", "--device", "cuda"],
    ["--replay-native", "--device", "cpu", "--headless"],
    ["--replay-native", "--device", "cpu", "--branch-interactive"],
    ["--replay-native-test-steps", "20"],
    ["--replay-restore-tolerance", "nan"],
    ["--replay-restore-tolerance", "0"],
])
def test_native_replay_rejects_unsupported_configuration(flags):
    args = build_parser().parse_args(flags)
    with pytest.raises(ValueError):
        main_module.validate_fire_rescue_cli_args(args)


def test_native_replay_headless_validation_is_opt_in():
    args = build_parser().parse_args([
        "--replay-native", "--device", "cpu", "--headless", "--replay-native-test-steps", "20",
    ])
    main_module.validate_fire_rescue_cli_args(args)


def test_exact_replay_is_explicit_and_forwarded():
    with pytest.raises(ValueError, match="requires --replay-native"):
        main_module.validate_fire_rescue_cli_args(build_parser().parse_args(["--replay-exact"]))
    args = build_parser().parse_args(["--replay-exact", "--task-checkpoints", "--device", "cpu", "--trials", "1", "--headless"])
    main_module.validate_fire_rescue_cli_args(args)
    forwarded = build_robot_baseline_args(
        SimpleNamespace(env_name="factory", num_envs=1, device="cpu"),
        FireRescueConfig(map_yaml=Path("map.yaml"), task_checkpoints=True, replay_exact=True, trials=1),
    )
    assert forwarded.replay_exact and forwarded.replay_native


def test_exact_replay_enables_physx_determinism_before_scene_construction():
    from demo.fire_rescue.config import ROBOT_SPAWN_POSES
    from demo.fire_rescue.experiment import configure_fire_rescue_env_cfg

    scene = SimpleNamespace(**{name: SimpleNamespace(init_state=SimpleNamespace()) for name in ROBOT_SPAWN_POSES})
    config = SimpleNamespace(scene=scene, sim=SimpleNamespace(physx=SimpleNamespace(enable_enhanced_determinism=False)))
    configure_fire_rescue_env_cfg(config, exact=True)
    assert config.sim.physx.enable_enhanced_determinism


def test_native_scope_restores_determinism_flags_without_disabling_noise():
    torch = pytest.importorskip("torch")
    from demo.fire_rescue.runtime.native_replay import NativeReplayScope

    previous = torch.are_deterministic_algorithms_enabled()
    scope = NativeReplayScope(0.02)
    try:
        scope.enable_deterministic_algorithms()
        assert torch.are_deterministic_algorithms_enabled()
        assert not torch.equal(torch.rand(16), torch.rand(16))
    finally:
        scope.__exit__(None, None, None)
    assert torch.are_deterministic_algorithms_enabled() is previous


def test_failed_demo_sets_kit_exit_status_before_session_cleanup(monkeypatch):
    from contextlib import contextmanager
    import sys
    from types import ModuleType

    events = []

    @contextmanager
    def session(config):
        try:
            yield SimpleNamespace()
        finally:
            events.append("cleanup")

    def fail(*args):
        raise RuntimeError("physical audit failed")

    omni = ModuleType("omni")
    omni.kit = ModuleType("omni.kit")
    omni.kit.app = ModuleType("omni.kit.app")
    omni.kit.app.get_app = lambda: SimpleNamespace(post_quit=lambda code: events.append(code))
    for name, module in (("omni", omni), ("omni.kit", omni.kit), ("omni.kit.app", omni.kit.app)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, "argv", ["fire_rescue", "--env", "EAI-Factory-v0", "--map-yaml", "map.yaml"])
    monkeypatch.setattr(main_module, "open_simulator_session", session)
    monkeypatch.setattr(main_module, "run_robot_baseline_experiment", fail)
    with pytest.raises(RuntimeError, match="physical audit failed"):
        main_module.main()
    assert events == [1, "cleanup"]


def test_task_checkpoints_support_headless_with_a_separate_algorithm_entry():
    args = build_parser().parse_args([
        "--task-checkpoints", "--trials", "1", "--device", "cpu", "--headless", "--checkpoint-dir", "tmp/replay/task",
        "--assignment-policy", "algorithm.emos.example_policy:assign",
    ])
    main_module.validate_fire_rescue_cli_args(args)
    assert args.branch_checkpoint_dir == "tmp/replay/task"
    assert not args.replay_native
    config = FireRescueConfig(
        map_yaml=Path("factory_map.yaml"), task_checkpoints=True, trials=1,
        assignment_policy=args.assignment_policy, headless=True,
    )
    forwarded = build_robot_baseline_args(SimpleNamespace(env_name="factory", num_envs=1, device="cpu"), config)
    assert forwarded.replay_native and forwarded.task_checkpoints and forwarded.headless
    assert forwarded.assignment_policy == args.assignment_policy


@pytest.mark.parametrize("flags", [
    ["--task-checkpoints", "--trials", "1"],
    ["--task-checkpoints", "--device", "cpu", "--trials", "2"],
    ["--task-checkpoints", "--trials", "1", "--device", "cpu", "--num-envs", "2"],
    ["--task-checkpoints", "--trials", "1", "--device", "cpu", "--branch-interactive"],
    ["--task-checkpoints", "--trials", "1", "--device", "cpu", "--replay-verify-dir", "tmp/legacy"],
    ["--assignment-policy", "algorithm.emos.example_policy:assign", "--replay-local-fallback"],
])
def test_task_checkpoint_cli_preserves_physics_and_algorithm_boundaries(flags):
    with pytest.raises(ValueError):
        main_module.validate_fire_rescue_cli_args(build_parser().parse_args(flags))


def test_task_runner_owns_native_scope_without_requiring_native_ui_flag(monkeypatch):
    from contextlib import nullcontext
    from demo.fire_rescue.experiment import run_robot_baseline_experiment
    import demo.fire_rescue.runtime.native_replay as native_module

    scope = SimpleNamespace()
    monkeypatch.setattr(native_module, "NativeReplayScope", lambda step_dt: nullcontext(scope))
    session = SimpleNamespace(
        env_name="factory", num_envs=1, device="cpu", base_env=SimpleNamespace(step_dt=0.02),
        simulation_app=object(), env=object(), env_cfg=object(), possible_agents=[],
    )
    config = FireRescueConfig(map_yaml=Path("factory_map.yaml"), task_checkpoints=True, trials=1)

    def run_platform(**kwargs):
        assert kwargs["args_cli"].replay_scope is scope
        assert kwargs["args_cli"].replay_native
        assert kwargs["args_cli"].task_checkpoints
        return 3

    assert run_robot_baseline_experiment(
        session, config, run_platform=run_platform, build_manager=lambda *args, **kwargs: SimpleNamespace(),
        build_scenario=lambda: SimpleNamespace(), interpret_factory_tasks_fn=lambda result: [],
    ) == 3


def test_native_clock_freezes_and_restores():
    from demo.fire_rescue.runtime.native_replay import SimulationClock

    clock = SimulationClock(0.02)
    saved = clock.capture_state()
    initial = clock.time()
    clock.step_index += 50
    assert clock.time() == pytest.approx(initial + 1)
    clock.restore_state(saved)
    assert clock.time() == initial


def test_native_scope_restores_clocks_even_when_trial_fails(monkeypatch):
    import demo.fire_rescue.runtime.native_replay as native_module

    names = ["experiment_loop", "navigation", "obstacle_rescue", "ur5", "sim_helpers"]
    originals = {f"demo.fire_rescue.runtime.{name}": SimpleNamespace(time=object()) for name in names}
    originals["algorithm.emos.engine"] = SimpleNamespace(time=object())
    clocks = {name: module.time for name, module in originals.items()}
    monkeypatch.setattr(native_module.importlib, "import_module", lambda name: originals[name])
    closed = []
    with pytest.raises(RuntimeError, match="trial failed"):
        with native_module.NativeReplayScope(0.02) as scope:
            assert all(module.time is scope.clock for module in originals.values())
            scope.window = SimpleNamespace(close=lambda: closed.append(True))
            raise RuntimeError("trial failed")
    assert closed == [True]
    assert all(module.time is clocks[name] for name, module in originals.items())
