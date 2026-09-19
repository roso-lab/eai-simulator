"""Demo scene routing and session ownership without an Isaac application."""

import importlib

import pytest

from demo.benchmark import runtime


@pytest.mark.parametrize("package,scene", [
    ("factory_benchmark", "factory"), ("warehouse_benchmark", "warehouse_nvd"),
    ("modern_house_benchmark", "modern_house_nvd"),
])
def test_each_demo_owns_its_scene(package, scene, monkeypatch):
    application = importlib.import_module(f"demo.{package}.main")
    calls = []

    def fake_main(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return 17

    monkeypatch.setattr(runtime, "main", fake_main)
    assert application.main(["--scenario", "episode.json"]) == 17
    assert calls[0][1]["expected_scene"] == scene
    assert callable(calls[0][1]["asset_check"])


def test_factory_demo_forwards_a_programmatic_participant_adapter(monkeypatch):
    from demo.factory_benchmark import main as factory_main

    adapter = object()
    calls = []

    def fake_main(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return 0

    monkeypatch.setattr(runtime, "main", fake_main)
    assert factory_main.main(["--scenario", "episode.json"], participant_adapter=adapter) == 0
    assert calls[0][1]["participant_adapter"] is adapter


@pytest.mark.parametrize("package", ["warehouse_benchmark", "modern_house_benchmark"])
def test_scene_demo_forwards_hidden_runtime_factor_boundaries(package, monkeypatch):
    application = importlib.import_module(f"demo.{package}.main")
    lifecycle = object()
    contract = {"delay_s": 1.0}
    schedule = [{"time_s": 2.0, "conditions": {"drop": True}}]
    calls = []

    def fake_main(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return 0

    monkeypatch.setattr(runtime, "main", fake_main)
    assert application.main(
        ["--scenario", "episode.json"], lifecycle_callbacks=lifecycle,
        communication_contract=contract, communication_schedule=schedule) == 0
    assert calls[0][1]["lifecycle_callbacks"] is lifecycle
    assert calls[0][1]["communication_contract"] is contract
    assert calls[0][1]["communication_schedule"] is schedule


def test_wrong_scene_rejected_before_asset_check_or_simulator(monkeypatch, tmp_path):
    import EAI.scenario_spec

    monkeypatch.setattr(EAI.scenario_spec, "load_scenario", lambda path: {"scene_key": "factory"})
    scenario = tmp_path / "episode.json"
    scenario.write_text('{"scene_key": "factory"}', encoding="utf-8")
    with pytest.raises(ValueError, match="requires scene_key"):
        runtime.main(["--scenario", str(scenario)], expected_scene="warehouse_nvd",
                     asset_check=lambda: pytest.fail("asset check must not run"))


def test_runtime_uses_launcher_session_with_full_preflight(monkeypatch, tmp_path):
    import simulator
    import EAI.scenario_spec
    import EAI.scenario_spec.geometry

    spec = {"scene_key": "factory", "seed": 42}
    scenario = tmp_path / "episode.json"
    scenario.write_text('{"scene_key": "factory", "seed": 42}', encoding="utf-8")
    monkeypatch.setattr(EAI.scenario_spec, "load_scenario", lambda path: spec)
    monkeypatch.setattr(EAI.scenario_spec.geometry, "provider_map", lambda **kwargs: object())
    monkeypatch.setattr(runtime, "planning_template", lambda *args: object())
    monkeypatch.setattr(runtime, "build_plan", lambda *args: [])
    monkeypatch.setattr(runtime, "execution_contract", lambda *args, **kwargs: {})
    calls = []

    class SessionReached(Exception):
        pass

    def open_session(config):
        calls.append(config)
        raise SessionReached

    monkeypatch.setattr(simulator, "open_simulator_session", open_session)
    with pytest.raises(SessionReached):
        runtime.main(["--scenario", str(scenario), "--headless", "--steps", "2"],
                     expected_scene="factory", asset_check=lambda: {"validated": True})
    config = calls[0]
    assert config.scenario_file.endswith("episode.json")
    assert config.resolved_env_name is None
    assert config.seed == 42 and config.num_envs == 1 and config.headless
    assert not config.enable_ros_bridge_extension


def test_task_profiles_are_owned_by_individual_demos():
    from demo.factory_benchmark import scenario
    from tools.scenario_generation.benchmark_task_design import STRONG_TASK_BLUEPRINTS
    from tools.scenario_generation.benchmark_experiment import SCENE_ASSET_KEYS

    assert STRONG_TASK_BLUEPRINTS["factory"] is scenario.TASK_BLUEPRINT
    assert SCENE_ASSET_KEYS["factory"] is scenario.ASSET_KEYS


def test_asset_audit_runs_in_a_separate_interpreter(monkeypatch):
    from types import SimpleNamespace
    from demo.benchmark import application
    from demo.warehouse_benchmark import config

    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"validated": true}', stderr="")

    monkeypatch.setattr(application.subprocess, "run", run)
    monkeypatch.setattr(application, "check_assets", lambda *args: pytest.fail("USD imported in launcher"))
    assert application.check_assets_isolated(config)["validated"]
    assert calls[0][1:] == ["-m", "demo.warehouse_benchmark.main", "--check-assets"]


@pytest.mark.parametrize("package", ["factory_benchmark", "warehouse_benchmark", "modern_house_benchmark"])
def test_maintained_navigation_examples_pass_geometry(package):
    from pathlib import Path
    from EAI.scenario_spec import load_scenario
    from EAI.scenario_spec.geometry import provider_map, validate_geometry, validate_connectivity

    config = importlib.import_module(f"demo.{package}.config")
    root = Path(__file__).resolve().parents[3]
    if not (root / "usd" / config.MAP_RELATIVE_PATH).exists():
        pytest.skip("Local demo map bundle is not installed")
    spec = load_scenario(root / "demo" / package / "scenarios" / "navigation_smoke.json")
    occupancy = provider_map(scene_key=config.SCENE_KEY)
    validate_geometry(spec, occupancy)
    validate_connectivity(spec, occupancy)
    assert spec["robots"][0]["attachments"][0]["type"] == "z1"
