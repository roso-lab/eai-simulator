"""Lightweight phase-3 checks for Warehouse template and protocol boundaries.

Run explicitly because repository-wide test ignore rules intentionally exclude
most ad hoc test modules::

    PYTHONPATH=source/EAI:source/EAI_assets python -m pytest -q \
      tools/scenario_generation/warehouse_benchmark_checks.py
"""

from __future__ import annotations

import pytest

from tools.scenario_generation import warehouse_benchmark
from tools.scenario_generation.execute_episode import planning_template


def test_warehouse_planning_delegates_scene_key_to_template_for(monkeypatch) -> None:
    calls = []
    sentinel = object()

    def fake_template_for(scene_key, occupancy):
        calls.append((scene_key, occupancy))
        return sentinel

    import EAI.scenario_spec.templates as templates

    monkeypatch.setattr(templates, "template_for", fake_template_for)
    occupancy = object()
    assert planning_template("warehouse_nvd", occupancy) is sentinel
    assert calls == [("warehouse_nvd", occupancy)]


def test_factory_protocol_scope_rejects_warehouse_records() -> None:
    records = [{"id": "warehouse_r1_low", "scene_key": "warehouse_nvd"}]
    with pytest.raises(ValueError, match="cannot include scene_key"):
        warehouse_benchmark.validate_protocol_scope(records, scope="factory")


def test_warehouse_r1_lists_quality_and_heterogeneous_candidates() -> None:
    metadata = warehouse_benchmark.WAREHOUSE_R1_METADATA
    assert metadata["scene_key"] == "warehouse_nvd"
    assert metadata["instance_count"] == 4
    assert metadata["quality_variant_count"] == 2
    assert metadata["heterogeneous_candidate_count"] == 2
    assert metadata["quality_variants_require_heterogeneous_team"] is False


def test_warehouse_has_an_explicitly_separate_protocol_scope() -> None:
    assert warehouse_benchmark.protocol_scope_metadata("factory")["scene_keys"] == ["factory"]
    assert warehouse_benchmark.protocol_scope_metadata("warehouse")["scene_keys"] == ["warehouse_nvd"]
    assert warehouse_benchmark.protocol_scope_metadata("modern_house")["scene_keys"] == ["modern_house_nvd"]


def test_modern_house_planning_delegates_scene_key_to_template_for(monkeypatch) -> None:
    calls = []
    sentinel = object()

    def fake_template_for(scene_key, occupancy):
        calls.append((scene_key, occupancy))
        return sentinel

    import EAI.scenario_spec.templates as templates

    monkeypatch.setattr(templates, "template_for", fake_template_for)
    occupancy = object()
    assert planning_template("modern_house_nvd", occupancy) is sentinel
    assert calls == [("modern_house_nvd", occupancy)]




def _write_pool(tmp_path, scene_key, names=("a", "b")):
    from pathlib import Path
    import json

    instances = tmp_path / "instances"
    instances.mkdir(parents=True)
    records = []
    for name in names:
        path = instances / f"{name}.json"
        path.write_text(json.dumps({"scene_key": scene_key, "id": name}), encoding="utf-8")
        records.append({
            "id": name,
            "file": f"instances/{name}.json",
            "family_id": name,
            "layout_signature": name,
            "scene_key": scene_key,
            "labels": ["spawn_validated"],
        })
    return {"pool": scene_key, "scene_key": scene_key, "instances": records}


def test_protocol_freeze_supports_warehouse_and_modern_house_independently(tmp_path) -> None:
    from tools.scenario_generation.benchmark_protocol import build_protocol

    warehouse = _write_pool(tmp_path / "warehouse", "warehouse_nvd")
    warehouse_protocol = build_protocol(
        tmp_path / "warehouse", warehouse, split_seed=7, test_fraction=0.5,
        scope="warehouse", scene_key="warehouse_nvd",
    )
    assert warehouse_protocol["scope"]["name"] == "warehouse"
    assert warehouse_protocol["scene_key"] == "warehouse_nvd"

    house = _write_pool(tmp_path / "house", "modern_house_nvd")
    house_protocol = build_protocol(
        tmp_path / "house", house, split_seed=7, test_fraction=0.5,
        scope="modern_house", scene_key="modern_house_nvd",
    )
    assert house_protocol["scope"]["name"] == "modern_house"
    assert house_protocol["scene_key"] == "modern_house_nvd"


def test_protocol_freeze_rejects_mixed_or_conflicting_scene_keys(tmp_path) -> None:
    from tools.scenario_generation.benchmark_protocol import build_protocol

    pool = _write_pool(tmp_path, "warehouse_nvd")
    pool["instances"][1]["scene_key"] = "factory"
    with pytest.raises(ValueError, match="cannot include scene_key"):
        build_protocol(tmp_path, pool, split_seed=7, test_fraction=0.5, scope="warehouse")

    pool = _write_pool(tmp_path / "conflict", "warehouse_nvd")
    with pytest.raises(ValueError, match="conflicts with requested scene_key"):
        build_protocol(
            tmp_path / "conflict", pool, split_seed=7, test_fraction=0.5,
            scope="warehouse", scene_key="modern_house_nvd",
        )


def test_modern_house_protocol_build_and_summarize(tmp_path) -> None:
    from tools.scenario_generation.benchmark_protocol import build_protocol, summarize_protocol

    house = _write_pool(tmp_path / "summary_house", "modern_house_nvd")
    protocol = build_protocol(
        tmp_path / "summary_house", house, split_seed=11, test_fraction=0.5,
        scope="modern_house", scene_key="modern_house_nvd",
    )
    summary = summarize_protocol(
        tmp_path / "summary_house", house, protocol, split_name="all",
        scope="modern_house", scene_key="modern_house_nvd",
    )
    assert protocol["scope"]["name"] == "modern_house"
    assert protocol["scene_key"] == "modern_house_nvd"
    assert summary["summary"]["frozen_instances"] == 2


def test_modern_house_has_separate_scope_and_manifest_candidates() -> None:
    from pathlib import Path

    paths = sorted(Path("tmp/validated_pool/modern_house_r1/instances").glob("*.json"))
    manifest = warehouse_benchmark.build_modern_house_manifest(
        paths, payload_policy={"cf2x": 1.0, "m20": 40.0, "go2": 40.0, "carter": 40.0}
    )
    assert manifest["scope"]["name"] == "modern_house"
    assert len(manifest["instances"]) == 3
    assert "reports" not in manifest
    warehouse_benchmark.validate_modern_house_manifest(manifest)

