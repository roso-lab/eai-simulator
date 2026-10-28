from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

from tools.benchmark_branch.generate_paper_identity_release import (
    CATALOG,
    CONTRACT_ARTIFACT,
    CONTRACT_MANIFEST,
    DECISION_SHEET,
    P4_FIXTURE,
    PAPER_COMMIT,
    PILOT_CANDIDATES,
    PREDICATE_BINDING,
    ROBOT_RESUME,
    ROOM06_BINDING,
    generate_release,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUTS = (
    CATALOG,
    CONTRACT_ARTIFACT,
    CONTRACT_MANIFEST,
    P4_FIXTURE / "package.json",
    P4_FIXTURE / "catalog_contract_mapping.json",
    P4_FIXTURE / "catalog_contract_mapping_receipt.json",
)


def _copy(source_root: Path, target_root: Path, relative: Path) -> None:
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_root / relative, target)


def _project_copy(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    inputs = (
        CATALOG,
        DECISION_SHEET,
        ROOM06_BINDING,
        PREDICATE_BINDING,
        PILOT_CANDIDATES,
        ROBOT_RESUME,
        P4_FIXTURE / "catalog.json",
        P4_FIXTURE / "scenario.json",
        P4_FIXTURE / "contract.json",
        P4_FIXTURE / "catalog_contract_mapping.json",
    )
    for relative in inputs:
        _copy(PROJECT_ROOT, root, relative)
    (root / CONTRACT_ARTIFACT).parent.mkdir(parents=True, exist_ok=True)
    (root / CONTRACT_MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    for relative in ("source/EAI", "source/EAI_assets", "source/EAI_hmrs", "usd"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {relative.as_posix(): (root / relative).read_bytes() for relative in OUTPUTS}


def test_release_is_idempotent_and_checkout_root_independent(tmp_path):
    roots = [_project_copy(tmp_path / "one"), _project_copy(tmp_path / "two")]
    snapshots = []
    for root in roots:
        generate_release(root)
        first = _snapshot(root)
        generate_release(root)
        second = _snapshot(root)
        assert first == second
        snapshots.append(second)
    assert snapshots[0] == snapshots[1]
    assert all(b"/home/pupil/" not in payload for payload in snapshots[0].values())


def test_release_keeps_all_certification_gates_closed(tmp_path):
    root = _project_copy(tmp_path)
    generate_release(root)
    catalog = json.loads((root / CATALOG).read_text(encoding="utf-8"))
    assert len(catalog["anchors"]) == 31
    assert {
        item["difficulty_certification_status"] for item in catalog["anchors"]
    } == {"difficulty_certification_pending"}

    artifact = json.loads((root / CONTRACT_ARTIFACT).read_text(encoding="utf-8"))
    manifest = json.loads((root / CONTRACT_MANIFEST).read_text(encoding="utf-8"))
    assert artifact["paper_sha"] == PAPER_COMMIT
    assert manifest["paper_sha"] == PAPER_COMMIT
    assert manifest["certification"] == {
        "case_certified": False,
        "paper_score_eligible": False,
        "formal_qualification_gates": False,
    }
    assert manifest["source_catalog_sha256"] == hashlib.sha256(
        (root / CATALOG).read_bytes()
    ).hexdigest()
    assert manifest["contract_artifact_sha256"] == hashlib.sha256(
        (root / CONTRACT_ARTIFACT).read_bytes()
    ).hexdigest()

    package = json.loads((root / P4_FIXTURE / "package.json").read_text())
    mapping = json.loads(
        (root / P4_FIXTURE / "catalog_contract_mapping.json").read_text()
    )
    receipt = json.loads(
        (root / P4_FIXTURE / "catalog_contract_mapping_receipt.json").read_text()
    )
    assert package["paper_commit"] == mapping["paper_commit"] == PAPER_COMMIT
    assert receipt["paper_commit"] == PAPER_COMMIT
    assert receipt["catalog_certified"] is False
    assert receipt["catalog_semantic_equivalence_verified"] is False
    assert receipt["physical_qualified"] is False
    assert receipt["formal_measured_qualified"] is False
    assert receipt["paper_score_eligible"] is False
