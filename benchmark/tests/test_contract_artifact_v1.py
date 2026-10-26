import json
import hashlib
from pathlib import Path
import shutil

import pytest

from benchmark.contract_artifact_v1 import (
    ContractArtifactError,
    load_canonical_contract_artifact,
)


def test_canonical_contract_artifact_is_single_31_anchor_projection():
    root = Path(__file__).resolve().parents[2]
    result = load_canonical_contract_artifact(project_root=root)
    assert result.anchor_count == 31
    assert result.baseline_anchor_count == 30
    assert result.extension_anchor_ids == ("liv-t-04",)
    assert result.artifact_path.name == "pilot_contracts-v3.json"
    assert result.payload["certification"] == {
        "case_certified": False,
        "paper_score_eligible": False,
        "formal_qualification_gates": False,
    }


def test_canonical_contract_artifact_rejects_stale_paper_identity(tmp_path):
    root = Path(__file__).resolve().parents[2]
    relatives = (
        Path("benchmark/task_catalog.json"),
        Path(
            "artifacts/catalog/contracts/room06-extension-20261026/"
            "pilot_contracts-v3.json"
        ),
        Path("artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json"),
    )
    for relative in relatives:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    manifest_path = tmp_path / relatives[-1]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["paper_sha"] = "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ContractArtifactError, match="paper identity is stale"):
        load_canonical_contract_artifact(project_root=tmp_path)


def test_canonical_contract_artifact_rejects_rehashed_nested_certification(tmp_path):
    root = Path(__file__).resolve().parents[2]
    relatives = (
        Path("benchmark/task_catalog.json"),
        Path(
            "artifacts/catalog/contracts/room06-extension-20261026/"
            "pilot_contracts-v3.json"
        ),
        Path("artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json"),
    )
    for relative in relatives:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    artifact_path = tmp_path / relatives[1]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    contract = artifact["contracts"][0]
    contract["status"] = "certified"
    contract["certification"]["case_certified"] = True
    contract["certification"]["paper_score_eligible"] = True
    contract["oracle_contract"]["binding_status"] = "certified"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    manifest_path = tmp_path / relatives[2]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract_artifact_sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ContractArtifactError, match="full pending-contract validation"):
        load_canonical_contract_artifact(project_root=tmp_path)


def test_canonical_contract_artifact_rejects_rehashed_certified_contract_set(tmp_path):
    root = Path(__file__).resolve().parents[2]
    relatives = (
        Path("benchmark/task_catalog.json"),
        Path(
            "artifacts/catalog/contracts/room06-extension-20261026/"
            "pilot_contracts-v3.json"
        ),
        Path("artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json"),
    )
    for relative in relatives:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    artifact_path = tmp_path / relatives[1]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["status"] = "certified"
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
    manifest_path = tmp_path / relatives[2]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["contract_artifact_sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ContractArtifactError, match="full pending-contract validation"):
        load_canonical_contract_artifact(project_root=tmp_path)
