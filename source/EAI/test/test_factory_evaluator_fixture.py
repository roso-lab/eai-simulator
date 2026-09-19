"""Frozen Factory inspection truth/view fixture checks."""

from pathlib import Path

from demo.factory_benchmark.evaluator import load_fixture, validate_inspection_fixture
from demo.factory_benchmark.prim_bindings import audit_factory_prim_bindings


def test_inspection_fixture_matches_factory_scene_and_two_views():
    root = Path(__file__).resolve().parents[3]
    scenario = root / "demo/factory_benchmark/scenarios/inspection_01.json"
    usd = root / "usd/scene/factory/factory.usd"
    fixture = load_fixture(root / "demo/factory_benchmark/evaluator/inspection_01.json")
    report = validate_inspection_fixture(
        scenario_path=scenario, usd_path=usd, fixture=fixture,
        prim_audit=audit_factory_prim_bindings(usd),
    )
    assert report["validated"], report["errors"]
    assert report["ground_truth_source"] == "evaluator_only"


def test_inspection_fixture_rejects_changed_source_hash(tmp_path):
    root = Path(__file__).resolve().parents[3]
    scenario = root / "demo/factory_benchmark/scenarios/inspection_01.json"
    usd = root / "usd/scene/factory/factory.usd"
    fixture = load_fixture(root / "demo/factory_benchmark/evaluator/inspection_01.json")
    fixture["source_hashes"]["scenario_sha256"] = "0" * 64
    report = validate_inspection_fixture(
        scenario_path=scenario, usd_path=usd, fixture=fixture,
        prim_audit=audit_factory_prim_bindings(usd),
    )
    assert report["validated"] is False
    assert "scenario hash mismatch" in report["errors"]
