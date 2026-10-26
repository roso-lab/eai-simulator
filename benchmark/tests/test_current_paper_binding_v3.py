from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmark import composite_v3
from benchmark import map_ready_case_v1
from benchmark.contract_artifact_v1 import load_canonical_contract_artifact
from benchmark.registration_v3 import ExperimentRegistrationV3, formal_readiness
from benchmark.scorebook_v3 import PAPER_COMMIT, default_scorebook_v3
from benchmark.formal_matrix_v3 import BaselineQualificationV3
from benchmark.task_catalog import load_default_catalog


CURRENT_PAPER = "dac7b94207a2683d494bdb388fc48533c9219bdf"
STALE_PAPERS = (
    "1f329494e755523692f5f96c8e0889c6cec15a82",
    "75ceff6f55202b6b1a31b2e2e0f6de56023940fd",
    "bb82b7bdb1352f1633bc405f41595a4affc79567",
    "af12f6d0d435a41b61f67b591487d63aade0f41c",
    "07dcd4000e836f7e40178bb6df8fed985e6dadd0",
    "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44",
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
P4_FIXTURE = PROJECT_ROOT / "benchmark/fixtures/p4_candidate_factory_20261021"


def test_scorebook_is_bound_to_current_overleaf_commit():
    assert PAPER_COMMIT == CURRENT_PAPER
    assert composite_v3.PAPER_COMMIT == PAPER_COMMIT
    assert composite_v3.PAPER_COMMIT != "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44"
    assert PAPER_COMMIT not in STALE_PAPERS
    assert default_scorebook_v3().paper_commit == CURRENT_PAPER


def test_canonical_release_surfaces_share_current_paper_identity():
    canonical = load_canonical_contract_artifact(project_root=PROJECT_ROOT)
    artifact = json.loads(canonical.artifact_path.read_text(encoding="utf-8"))
    assert canonical.paper_sha == CURRENT_PAPER
    assert artifact["paper_sha"] == CURRENT_PAPER
    assert canonical.artifact_sha256 == hashlib.sha256(
        canonical.artifact_path.read_bytes()
    ).hexdigest()
    assert canonical.catalog_sha256 == hashlib.sha256(
        canonical.catalog_path.read_bytes()
    ).hexdigest()
    assert canonical.payload["certification"] == {
        "case_certified": False,
        "paper_score_eligible": False,
        "formal_qualification_gates": False,
    }
    assert len(artifact["contracts"]) == 31
    assert {
        row["oracle_contract"]["binding_status"] for row in artifact["contracts"]
    } == {"bound_pending_replay"}
    assert all(row["certification"]["case_certified"] is False for row in artifact["contracts"])
    assert all(
        row["certification"]["paper_score_eligible"] is False
        for row in artifact["contracts"]
    )
    assert b"/home/pupil/" not in canonical.artifact_path.read_bytes()


def test_map_ready_and_p4_fixture_share_current_paper_identity():
    assert map_ready_case_v1.ENGINEERING_PAPER_COMMIT == CURRENT_PAPER
    assert map_ready_case_v1.TARGET_PAPER_COMMIT == CURRENT_PAPER
    package = json.loads((P4_FIXTURE / "package.json").read_text(encoding="utf-8"))
    mapping = json.loads(
        (P4_FIXTURE / "catalog_contract_mapping.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (P4_FIXTURE / "catalog_contract_mapping_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert package["paper_commit"] == CURRENT_PAPER
    assert mapping["paper_commit"] == CURRENT_PAPER
    assert receipt["paper_commit"] == CURRENT_PAPER
    assert receipt["mapping_sha256"] == hashlib.sha256(
        (P4_FIXTURE / "catalog_contract_mapping.json").read_bytes()
    ).hexdigest()
    for flag in (
        "catalog_certified",
        "catalog_semantic_equivalence_verified",
        "physical_qualified",
        "formal_measured_qualified",
        "paper_score_eligible",
    ):
        assert receipt[flag] is False


def test_all_catalog_difficulty_certification_remains_pending():
    catalog = load_default_catalog()
    assert len(catalog) == 31
    assert {item.difficulty_certification_status for item in catalog} == {
        "difficulty_certification_pending"
    }


@pytest.mark.parametrize("stale_paper", STALE_PAPERS)
def test_registration_rejects_stale_paper_identity(stale_paper):
    with pytest.raises(ValueError, match="frozen paper"):
        ExperimentRegistrationV3(stale_paper, *([None] * 11))


def test_formal_readiness_no_longer_blocks_on_resolved_d3_partition():
    registration = ExperimentRegistrationV3(PAPER_COMMIT, *([None] * 11))
    report = formal_readiness(
        registration,
        case_report={"status": "na"},
        baseline_report={"status": "na"},
        calibration_report=None,
    )
    assert not report["ready"]
    assert "paper_d3_partition_unresolved" not in report["blockers"]


def test_baseline_qualification_needs_nonempty_unique_condition_coverage():
    import hashlib
    digests = tuple(hashlib.sha256(label.encode()).hexdigest()
                    for label in ("system", "task", "team", "budget", "observation", "manifest"))
    values = ("baseline", "source", "adapter", "checkpoint", "qualified", "observation")
    assert not BaselineQualificationV3(*values, ()).rankable
    assert not BaselineQualificationV3(*values, ("C0", "C0")).rankable
    assert not BaselineQualificationV3(*values, ("C0",)).rankable
    assert BaselineQualificationV3(*values, ("C0",), *digests).rankable
