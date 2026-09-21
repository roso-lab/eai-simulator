"""Offline checks for participant adapter entrypoints and diagnostic admission."""

import json
from pathlib import Path

import pytest

from demo.benchmark import runtime


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("scene_key", "scenario_file", "track_id"),
    [
        ("warehouse_nvd", "demo/warehouse_benchmark/scenarios/business_logistics_01.json", "W-DAG-01"),
        ("modern_house_nvd", "demo/modern_house_benchmark/scenarios/home_recovery_01.json", "MH-DAG-01"),
    ],
)
def test_indoor_diagnostic_tracks_are_admitted_by_scene_profile(
    scene_key, scenario_file, track_id
):
    spec = json.loads((ROOT / scenario_file).read_text(encoding="utf-8"))

    result = runtime._validate_benchmark_task_spec(spec)

    assert result["validated"] is True
    assert result["task_id"] == track_id
    assert result["design"]["validated"] is True
    assert spec["scene_key"] == scene_key


@pytest.mark.parametrize(
    ("scene_key", "track_id", "expected"),
    [
        ("warehouse_nvd", "MH-DAG-01", "W-DAG-01"),
        ("modern_house_nvd", "W-DAG-01", "MH-DAG-01"),
    ],
)
def test_indoor_diagnostic_track_cannot_cross_scene(scene_key, track_id, expected):
    with pytest.raises(ValueError, match=expected):
        runtime._validate_benchmark_task_spec({
            "scene_key": scene_key,
            "benchmark_task_id": track_id,
            "phase_graph": {"nodes": []},
        })


@pytest.mark.parametrize("package", ["warehouse_benchmark", "modern_house_benchmark"])
def test_indoor_main_forwards_programmatic_participant_adapter(monkeypatch, package):
    application = __import__(f"demo.{package}.main", fromlist=["main"])
    adapter = object()
    calls = []

    def fake_run_demo(config, argv, **kwargs):
        calls.append((config, argv, kwargs))
        return 23

    monkeypatch.setattr(application, "run_demo", fake_run_demo)
    assert application.main(["--scenario", "episode.json"], participant_adapter=adapter) == 23
    assert calls[0][1] == ["--scenario", "episode.json"]
    assert calls[0][2]["participant_adapter"] is adapter
