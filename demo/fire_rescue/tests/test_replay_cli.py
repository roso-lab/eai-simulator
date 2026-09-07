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
