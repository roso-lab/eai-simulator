import hashlib
import json
from pathlib import Path

import pytest

from benchmark.canonical_package_v3 import (
    SCHEMA, catalog_input_inventory, load_canonical_package,
)
from benchmark.canonical_deployment_v3 import Status, check_witness, solve
from benchmark.scorebook_v3 import PAPER_COMMIT


def _write_package(root: Path, *, contract=None):
    catalog = Path("benchmark/task_catalog.json").read_bytes()
    (root / "catalog.json").write_bytes(catalog)
    (root / "scenario.json").write_text('{"scene":"factory"}\n', encoding="utf-8")
    package = {
        "schema": SCHEMA, "paper_commit": PAPER_COMMIT,
        "instance_id": "prod-p1-01-instance-01", "anchor_id": "prod-p1-01",
        "scene_key": "factory",
        "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
        "scenario_sha256": hashlib.sha256((root / "scenario.json").read_bytes()).hexdigest(),
        "contract": contract or {"schema": "heterocoord.canonical_deployment.v3"},
    }
    raw = json.dumps(package, sort_keys=True, separators=(",", ":")).encode()
    (root / "package.json").write_bytes(raw)
    return root / "package.json", hashlib.sha256(raw).hexdigest()


def test_package_binds_bytes_paper_catalog_scenario_and_anchor(tmp_path):
    package, digest = _write_package(tmp_path)
    result = load_canonical_package(
        package, expected_package_sha256=digest,
        expected_instance_id="prod-p1-01-instance-01",
        catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
        project_root=tmp_path,
    )
    assert result.anchor_id == "prod-p1-01"
    assert result.anchor_horizon_ticks == 6000
    assert result.provenance()["catalog_semantic_equivalence_verified"] is False


def test_bound_package_parses_only_when_contract_matches_anchor_identity(tmp_path):
    obligations = []
    for index, obligation_id in enumerate(("work_unit_a", "work_unit_b", "reconcile_units")):
        obligations.append({
            "id": obligation_id, "required_capabilities": ["mobile_base", "barcode_scan"],
            "role_requirements": ({"picker": [1, 1]} if index < 2 else {"counter": [1, 1]}),
            "participant_bounds": [1, 1], "duration_ticks": 1,
            "release_tick": 0, "deadline_tick": 6000,
            "participant_tick_offsets": [0, 0], "sync_window_ticks": 0,
            "resource_demands": {},
        })
    contract = {
        "schema": "heterocoord.canonical_deployment.v3", "task_id": "prod-p1-01-instance-01",
        "horizon_ticks": 6000, "tick_dt_s": 0.02, "team_bounds": {"min": 1, "max": 1},
        "robots": [{"id": "r1", "capabilities": ["mobile_base", "barcode_scan"], "roles": ["picker", "counter"]}],
        "obligations": obligations, "resources": [], "temporal_edges": [], "mutex_pairs": [],
    }
    package, digest = _write_package(tmp_path, contract=contract)
    bound = load_canonical_package(package, expected_package_sha256=digest,
                                   expected_instance_id="prod-p1-01-instance-01",
                                   catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
                                   project_root=tmp_path)
    parsed = bound.parse_contract()
    assert parsed.task_id == "prod-p1-01-instance-01"


def test_bound_package_runs_explicit_contract_through_solver_and_witness_check(tmp_path):
    obligations = []
    for index, obligation_id in enumerate(("work_unit_a", "work_unit_b", "reconcile_units")):
        obligations.append({
            "id": obligation_id,
            "required_capabilities": ["mobile_base", "barcode_scan"],
            "role_requirements": ({"picker": [1, 1]} if index < 2 else {"counter": [1, 1]}),
            "participant_bounds": [1, 1],
            "duration_ticks": 1,
            "release_tick": 0,
            "deadline_tick": 6000,
            "participant_tick_offsets": [0, 0],
            "sync_window_ticks": 0,
            "resource_demands": {},
        })
    contract = {
        "schema": "heterocoord.canonical_deployment.v3",
        "task_id": "prod-p1-01-instance-01",
        "horizon_ticks": 6000,
        "tick_dt_s": 0.02,
        "team_bounds": {"min": 1, "max": 1},
        "robots": [{
            "id": "r1",
            "capabilities": ["mobile_base", "barcode_scan"],
            "roles": ["picker", "counter"],
        }],
        "obligations": obligations,
        "resources": [],
        "temporal_edges": [],
        "mutex_pairs": [],
    }
    package, digest = _write_package(tmp_path, contract=contract)
    bound = load_canonical_package(
        package, expected_package_sha256=digest,
        expected_instance_id="prod-p1-01-instance-01",
        catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
        project_root=tmp_path,
    )
    parsed = bound.parse_contract()
    result = solve(parsed)
    assert result.status is Status.DEPLOYABLE
    assert result.witness is not None
    assert check_witness(parsed, result.witness) == (True, ())
    assert bound.provenance()["physical_qualification_verified"] is False


@pytest.mark.parametrize("field", ["paper_commit", "anchor_id", "scene_key", "catalog_sha256", "scenario_sha256"])
def test_package_rejects_tampered_binding(tmp_path, field):
    package, _ = _write_package(tmp_path)
    value = json.loads(package.read_text())
    value[field] = "tampered"
    package.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    with pytest.raises(ValueError):
        load_canonical_package(package, expected_package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
                               expected_instance_id="prod-p1-01-instance-01",
                               catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
                               project_root=tmp_path)


def test_changed_package_cannot_reuse_old_external_pin(tmp_path):
    package, digest = _write_package(tmp_path)
    package.write_bytes(package.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="external package hash mismatch"):
        load_canonical_package(package, expected_package_sha256=digest,
                               expected_instance_id="prod-p1-01-instance-01",
                               catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
                               project_root=tmp_path)


@pytest.mark.parametrize("source", ["catalog.json", "scenario.json"])
def test_source_change_is_detected_without_package_change(tmp_path, source):
    package, digest = _write_package(tmp_path)
    path = tmp_path / source
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="source mismatch"):
        load_canonical_package(package, expected_package_sha256=digest,
                               expected_instance_id="prod-p1-01-instance-01",
                               catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
                               project_root=tmp_path)


def test_json_duplicate_keys_are_not_silently_discarded(tmp_path):
    package, _ = _write_package(tmp_path)
    package.write_bytes(package.read_bytes()[:-1] + b',"contract":{}}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_canonical_package(package, expected_package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
                               expected_instance_id="prod-p1-01-instance-01",
                               catalog_path=tmp_path / "catalog.json", scenario_path=tmp_path / "scenario.json",
                               project_root=tmp_path)


def test_inventory_reports_missing_machine_contract_without_certifying_anchors():
    report = catalog_input_inventory(catalog_path="benchmark/task_catalog.json")
    assert report["anchor_count"] == 30
    assert all(row["certification_status"] == "planned" for row in report["anchors"])
    assert all("frozen_robot_pool_and_capability_matrix" in row["required_instance_inputs"] for row in report["anchors"])


def test_package_path_must_be_project_local(tmp_path):
    package, digest = _write_package(tmp_path)
    with pytest.raises(ValueError, match="inside the project"):
        load_canonical_package(package, expected_package_sha256=digest,
                               expected_instance_id="prod-p1-01-instance-01",
                               catalog_path=Path("benchmark/task_catalog.json"), scenario_path=tmp_path / "scenario.json",
                               project_root=tmp_path)
