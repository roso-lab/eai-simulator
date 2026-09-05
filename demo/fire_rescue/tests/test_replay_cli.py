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
