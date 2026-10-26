from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from benchmark.catalog_mapping_v3 import RECEIPT_SCHEMA, verify_catalog_mapping
from benchmark.canonical_deployment_v3 import from_mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "benchmark/fixtures/p4_candidate_factory_20261021"
FILES = (
    "catalog.json",
    "scenario.json",
    "contract.json",
    "package.json",
    "robot_pool_resume.v1.json",
    "catalog_contract_mapping.json",
    "catalog_contract_mapping_receipt.json",
)


def _copy_fixture(tmp_path: Path) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in FILES:
        target = tmp_path / name
        source = (
            (FIXTURE / name)
            if name != "robot_pool_resume.v1.json"
            else REPO_ROOT / "benchmark/robot_resume/robot_pool_resume.v1.json"
        )
        shutil.copyfile(source, target)
        paths[name] = target
    scenario = json.loads(paths["scenario.json"].read_text(encoding="utf-8"))
    scenario["source_root"] = str(tmp_path / "source")
    scenario["usd_root"] = str(tmp_path / "usd")
    scenario["scene_identity"]["eai_root"] = str(tmp_path / "source" / "EAI")
    scenario["scene_identity"]["assets_root"] = str(tmp_path / "source" / "EAI_assets")
    scenario["scene_identity"]["hmrs_root"] = str(tmp_path / "source" / "EAI_hmrs")
    scenario_raw = json.dumps(scenario, sort_keys=True, separators=(",", ":")) + "\n"
    paths["scenario.json"].write_text(scenario_raw, encoding="utf-8")
    package = json.loads(paths["package.json"].read_text(encoding="utf-8"))
    package["scenario_sha256"] = hashlib.sha256(scenario_raw.encode()).hexdigest()
    package_raw = json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n"
    paths["package.json"].write_text(package_raw, encoding="utf-8")
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: value["source_digests"].update(
            scenario_sha256=hashlib.sha256(scenario_raw.encode()).hexdigest(),
            package_sha256=hashlib.sha256(package_raw.encode()).hexdigest(),
            robot_resume_sha256=hashlib.sha256(paths["robot_pool_resume.v1.json"].read_bytes()).hexdigest(),
        ),
    )
    return paths


def _verify(paths: dict[str, Path]):
    mapping = paths["catalog_contract_mapping.json"]
    return verify_catalog_mapping(
        mapping,
        expected_mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(),
        catalog_path=paths["catalog.json"],
        scenario_path=paths["scenario.json"],
        contract_path=paths["contract.json"],
        package_path=paths["package.json"],
        robot_resume_path=paths["robot_pool_resume.v1.json"],
        project_root=paths["catalog.json"].parent,
    )


def _rewrite_mapping(path: Path, mutate) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _rewrite_scenario_and_bind_digests(paths: dict[str, Path], mutate) -> None:
    scenario = json.loads(paths["scenario.json"].read_text(encoding="utf-8"))
    mutate(scenario)
    scenario_raw = json.dumps(scenario, sort_keys=True, separators=(",", ":")) + "\n"
    paths["scenario.json"].write_text(scenario_raw, encoding="utf-8")
    package = json.loads(paths["package.json"].read_text(encoding="utf-8"))
    package["scenario_sha256"] = hashlib.sha256(scenario_raw.encode()).hexdigest()
    package_raw = json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n"
    paths["package.json"].write_text(package_raw, encoding="utf-8")
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: value["source_digests"].update(
            scenario_sha256=hashlib.sha256(scenario_raw.encode()).hexdigest(),
            package_sha256=hashlib.sha256(package_raw.encode()).hexdigest(),
        ),
    )


def test_prod_p4_mapping_is_source_bound_and_receipt_is_deterministic(tmp_path):
    paths = _copy_fixture(tmp_path)
    result = _verify(paths)
    receipt = result.receipt()
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["verified_claim_count"] == 21
    assert receipt["authored_mapping_verified"] is True
    assert receipt["catalog_certified"] is False
    assert receipt["catalog_semantic_equivalence_verified"] is False
    assert receipt["physical_qualified"] is False
    assert receipt["formal_measured_qualified"] is False
    assert receipt["paper_score_eligible"] is False


def test_checked_in_fixture_receipt_is_frozen_exactly():
    base = FIXTURE
    mapping = base / "catalog_contract_mapping.json"
    result = verify_catalog_mapping(
        mapping,
        expected_mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(),
        catalog_path=base / "catalog.json",
        scenario_path=base / "scenario.json",
        contract_path=base / "contract.json",
        package_path=base / "package.json",
        # Resolve the repository root from this test file so the frozen-fixture
        # assertion remains valid in clean worktrees and CI checkouts.
        project_root=Path(__file__).resolve().parents[2],
    )
    assert result.receipt_bytes() == (base / "catalog_contract_mapping_receipt.json").read_bytes()


def test_mapping_pin_rejects_changed_mapping_bytes(tmp_path):
    paths = _copy_fixture(tmp_path)
    mapping = paths["catalog_contract_mapping.json"]
    old_pin = hashlib.sha256(mapping.read_bytes()).hexdigest()
    mapping.write_bytes(mapping.read_bytes() + b" ")
    with pytest.raises(ValueError, match="external mapping hash mismatch"):
        verify_catalog_mapping(
            mapping,
            expected_mapping_sha256=old_pin,
            catalog_path=paths["catalog.json"],
            scenario_path=paths["scenario.json"],
            contract_path=paths["contract.json"],
            package_path=paths["package.json"],
            robot_resume_path=paths["robot_pool_resume.v1.json"],
            project_root=tmp_path,
        )


def test_mapping_rejects_tampered_robot_resume_snapshot_bytes(tmp_path):
    paths = _copy_fixture(tmp_path)
    resume = paths["robot_pool_resume.v1.json"]
    resume.write_bytes(resume.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="robot_resume source digest mismatch"):
        _verify(paths)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value["claims"].pop(), "claim inventory mismatch"),
        (lambda value: value["claims"].append(dict(value["claims"][0])), "duplicate claim"),
        (lambda value: value["claims"][0].update(predicate="unsupported"), "unsupported or inconsistent claim binding"),
        (lambda value: value.update(scene_key="warehouse_nvd"), "mapping identity differs from package"),
        (lambda value: value["claims"][3]["target"].update(horizon_ticks=9499), "unsupported or inconsistent claim binding"),
    ],
)
def test_mapping_rejects_missing_duplicate_extra_or_wrong_claims(tmp_path, mutation, message):
    paths = _copy_fixture(tmp_path)
    mapping = paths["catalog_contract_mapping.json"]
    _rewrite_mapping(mapping, mutation)
    with pytest.raises(ValueError, match=message):
        _verify(paths)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value["contract"].__setitem__("horizon_ticks", 9499), "contract source digest mismatch"),
        (lambda value: value["contract"]["resources"][0].__setitem__("capacity", 2), "contract source digest mismatch"),
    ],
)
def test_mapping_rejects_contract_changes_before_semantic_reuse(tmp_path, mutation, message):
    paths = _copy_fixture(tmp_path)
    contract = json.loads(paths["contract.json"].read_text(encoding="utf-8"))
    mutation({"contract": contract})
    paths["contract.json"].write_text(
        json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=message):
        _verify(paths)


def test_mapping_rejects_wrong_temporal_binding_even_when_claim_inventory_is_complete(tmp_path):
    paths = _copy_fixture(tmp_path)
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: next(
            claim for claim in value["claims"]
            if claim["claim_id"] == "temporal_constraints.release_before_load_b"
        )["target"].update(after="load_a"),
    )
    with pytest.raises(ValueError, match="unsupported or inconsistent claim binding"):
        _verify(paths)


def test_mapping_rejects_wrong_resource_binding(tmp_path):
    paths = _copy_fixture(tmp_path)
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: next(
            claim for claim in value["claims"]
            if claim["claim_id"] == "resource_constraints.dock_capacity_one"
        )["target"].update(capacity=2),
    )
    with pytest.raises(ValueError, match="unsupported or inconsistent claim binding"):
        _verify(paths)


def test_mapping_allows_digest_consistent_missing_role_provider(tmp_path):
    paths = _copy_fixture(tmp_path)
    contract = json.loads(paths["contract.json"].read_text(encoding="utf-8"))
    contract["robots"][0]["roles"] = ["unrelated_role"]
    contract_raw = json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
    paths["contract.json"].write_text(contract_raw, encoding="utf-8")
    package = json.loads(paths["package.json"].read_text(encoding="utf-8"))
    package["contract"] = contract
    package_raw = json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n"
    paths["package.json"].write_text(package_raw, encoding="utf-8")
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: value["source_digests"].update(
            contract_sha256=hashlib.sha256(contract_raw.encode()).hexdigest(),
            package_sha256=hashlib.sha256(package_raw.encode()).hexdigest(),
            canonical_contract_digest=from_mapping(contract).digest(),
        ),
    )
    result = _verify(paths)
    assert result.receipt()["authored_mapping_verified"] is True


def test_mapping_allows_digest_consistent_missing_capability_provider(tmp_path):
    paths = _copy_fixture(tmp_path)
    contract = json.loads(paths["contract.json"].read_text(encoding="utf-8"))
    contract["robots"][0]["capabilities"] = ["mobile_base"]
    contract_raw = json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
    paths["contract.json"].write_text(contract_raw, encoding="utf-8")
    package = json.loads(paths["package.json"].read_text(encoding="utf-8"))
    package["contract"] = contract
    package_raw = json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n"
    paths["package.json"].write_text(package_raw, encoding="utf-8")
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: value["source_digests"].update(
            contract_sha256=hashlib.sha256(contract_raw.encode()).hexdigest(),
            package_sha256=hashlib.sha256(package_raw.encode()).hexdigest(),
            canonical_contract_digest=from_mapping(contract).digest(),
        ),
    )
    result = _verify(paths)
    assert result.receipt()["authored_mapping_verified"] is True


def test_mapping_rejects_stale_paper_commit_and_source_digest(tmp_path):
    paths = _copy_fixture(tmp_path)
    _rewrite_mapping(paths["catalog_contract_mapping.json"], lambda value: value.update(paper_commit="stale"))
    with pytest.raises(ValueError, match="mapping paper commit is stale"):
        _verify(paths)

    paths = _copy_fixture(tmp_path / "second")
    _rewrite_mapping(
        paths["catalog_contract_mapping.json"],
        lambda value: value["source_digests"].update(catalog_sha256="0" * 64),
    )
    with pytest.raises(ValueError, match="catalog source digest mismatch"):
        _verify(paths)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value["initial_state"].update(robots=["carter_2", "carter_1"]), "initial_state robots"),
        (lambda value: value["initial_state"].update(dock_capacity=2), "dock_capacity"),
        (lambda value: value["initial_state"].update(dock_slot="other_slot"), "dock_slot differs"),
        (lambda value: value["scene_identity"].update(backend="external-eai"), "backend must"),
        (lambda value: value["scene_identity"].update(scene_descriptor="factory/other"), "scene_descriptor"),
        (lambda value: value.update(source_root="/tmp/source"), "source_root"),
        (lambda value: value["scene_identity"].update(assets_root="/home/pupil/HeteroCoord/source/other"), "assets_root"),
    ],
)
def test_mapping_rejects_digest_consistent_scenario_semantic_mutations(tmp_path, mutation, message):
    paths = _copy_fixture(tmp_path)
    _rewrite_scenario_and_bind_digests(paths, mutation)
    with pytest.raises(ValueError, match=message):
        _verify(paths)
