from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from historical_runtime_receipt import HistoricalReceiptError, validate_historical_receipt
from register_historical_evidence import ROOT, evidence_matrix, register
from runtime_qualification_receipt import RuntimeReceiptError, validate_receipt


HERE = Path(__file__).resolve().parent
ROSTER = HERE / "robot_runtime_roster.v1.json"


def test_reviewed_matrix_is_explicit_and_does_not_promote_unknown_configs(tmp_path):
    report, promoted = register(ROSTER, receipt_dir=tmp_path / "receipts",
                                promoted_roster_path=tmp_path / "promoted.json")
    assert report["robot_count"] == 27
    assert report["qualified_count"] == 15
    assert report["unknown_count"] == 12
    # The opt-in user-ruling path is disabled by default: no ruling block, no
    # promotion authority beyond the historical registration authority.
    assert "ruling" not in report
    assert "ruling_authority" not in promoted["promotion"]
    unknown = {item["instance_id"] for item in report["rows"] if not item["runtime_qualified"]}
    assert unknown == {
        "hc-rt-002", "hc-rt-003", "hc-rt-008", "hc-rt-011", "hc-rt-013",
        "hc-rt-015", "hc-rt-016", "hc-rt-017", "hc-rt-019", "hc-rt-022",
        "hc-rt-023", "hc-rt-024",
    }
    assert sum(row["runtime_qualified"] for row in promoted["robots"]) == 15
    assert all(row["runtime_qualification_receipt"] is None
               for row in promoted["robots"] if row["instance_id"] in unknown)


def test_historical_receipts_dispatch_without_live_rp_c_fields(tmp_path):
    _, promoted = register(ROSTER, receipt_dir=tmp_path / "receipts")
    row = next(item for item in promoted["robots"] if item["instance_id"] == "hc-rt-027")
    receipt = row["runtime_qualification_receipt"]
    assert "run" not in receipt and "cleanup" not in receipt and "gpu_uuid" not in receipt
    assert validate_receipt(receipt, roster_row=row)["runtime_qualified"] is True
    assert receipt["checks"]["functional_record"]["details"]["recorded"].startswith("MicroDuck")


def test_historical_source_bytes_and_content_are_rechecked(tmp_path):
    _, promoted = register(ROSTER)
    row = next(item for item in promoted["robots"] if item["instance_id"] == "hc-rt-005")
    receipt = copy.deepcopy(row["runtime_qualification_receipt"])
    receipt["sources"][0]["sha256"] = "0" * 64
    from historical_runtime_receipt import canonical_sha256
    receipt["receipt_digest"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_digest"})
    with pytest.raises(HistoricalReceiptError, match="does not match file bytes"):
        validate_historical_receipt(receipt, roster_row=row, root=ROOT)


def test_historical_source_commit_must_contain_the_exact_file_bytes():
    _, promoted = register(ROSTER)
    row = next(item for item in promoted["robots"] if item["instance_id"] == "hc-rt-005")
    receipt = copy.deepcopy(row["runtime_qualification_receipt"])
    # This older commit contains the path and result text, but not the current
    # file bytes.  A plausible commit hash must not be accepted as provenance.
    receipt["sources"][0]["source_commit"] = "e8f97448a55feef76d9ec1ed993f01695580efc6"
    from historical_runtime_receipt import canonical_sha256
    receipt["receipt_digest"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_digest"})
    with pytest.raises(HistoricalReceiptError, match="source_commit blob does not match"):
        validate_historical_receipt(receipt, roster_row=row, root=ROOT)


def test_wrong_configuration_and_receipt_tamper_fail_closed():
    _, promoted = register(ROSTER)
    qualified = next(item for item in promoted["robots"] if item["instance_id"] == "hc-rt-014")
    other = next(item for item in promoted["robots"] if item["instance_id"] == "hc-rt-012")
    with pytest.raises(RuntimeReceiptError, match="roster identity"):
        validate_receipt(qualified["runtime_qualification_receipt"], roster_row=other)
    tampered = copy.deepcopy(qualified["runtime_qualification_receipt"])
    tampered["platform_scope"]["motion_controller"] = "NOT_THE_RECORDED_CONFIG"
    with pytest.raises(RuntimeReceiptError, match="seal mismatch"):
        validate_receipt(tampered, roster_row=qualified)


def test_sources_have_exact_path_digest_commit_and_record_locator():
    matrix = evidence_matrix()
    assert set(matrix) == {
        "hc-rt-001", "hc-rt-004", "hc-rt-005", "hc-rt-006", "hc-rt-007",
        "hc-rt-009", "hc-rt-010", "hc-rt-012", "hc-rt-014",
        "hc-rt-018", "hc-rt-020", "hc-rt-021", "hc-rt-025", "hc-rt-026",
        "hc-rt-027",
    }
    for evidence in matrix.values():
        for source in evidence["sources"]:
            assert (ROOT / source["path"]).is_file()
            assert len(source["sha256"]) == 64
            assert len(source["source_commit"]) == 40
            assert source["record_locator"]
            assert source["content_assertions"]


def test_authority_decision_accepts_ruling_variant_only_by_name():
    # The ruling decision is an explicit user acceptance carried by the same
    # contract; an unknown decision string stays rejected (root=None skips the
    # file/commit recheck, isolating the authority branch).
    row = {"instance_id": "hc-rt-002", "source_resume_id": "x", "profile_id": "p",
           "admission_class": "pool", "platform_type": "carter", "configuration_role": "mobile_platform",
           "asset_binding": {"asset_digest": "1" * 64},
           "capability_binding": {"capability_digest": "2" * 64},
           "initial_state_binding": {"initial_state_digest": "3" * 64}}
    source = {"path": "benchmark/robot_resume/RUNTIME_RULING_20261026.zh-CN.md",
              "sha256": "a" * 64, "source_commit": "b" * 40,
              "record_locator": "ruling list", "content_assertions": ["hc-rt-002"]}
    checks = {name: {"status": "passed", "source_refs": ["0"], "details": {"basis": "user final ruling"}}
              for name in ("integration_record", "functional_record", "configuration_binding")}
    from historical_runtime_receipt import RULING_DECISION, make_historical_receipt
    authority = {"decision": RULING_DECISION, "decision_date": "2026-10-26", "scope": "runtime only"}
    receipt = make_historical_receipt(
        roster_row=row, receipt_id="hc-rt-002.user-ruling.20261026", authority=authority,
        platform_scope={"platform_type": "carter", "configuration_role": "mobile_platform"},
        sources=[source], checks=checks, root=None)
    assert receipt["runtime_qualified"] is True
    tampered = dict(authority, decision="user_whatever_accepted")
    with pytest.raises(HistoricalReceiptError, match="decision authority"):
        make_historical_receipt(
            roster_row=row, receipt_id="hc-rt-002.bad", authority=tampered,
            platform_scope={"platform_type": "carter", "configuration_role": "mobile_platform"},
            sources=[source], checks=checks, root=None)
