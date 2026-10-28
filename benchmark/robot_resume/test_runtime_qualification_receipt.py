from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime_qualification_receipt import RuntimeReceiptError, canonical_sha256, make_receipt, validate_receipt
from runtime_qualification_runner import promote_roster, run_queue
from benchmark.case_input_audit_v1 import audit_robot_pool


HERE = Path(__file__).resolve().parent


def _roster():
    return json.loads((HERE / "robot_runtime_roster.v1.json").read_text())["robots"]


def _valid_parts():
    digest = "1" * 64
    checks = {name: {"passed": True, "evidence_digest": digest, "details": {"observed": True}} for name in ("load", "basic_function", "sensor_readback")}
    command = "env_isaaclab/python qualification_entry.py --instance hc-rt"
    run = {"command": command, "command_digest": canonical_sha256(command), "source_revision": "a" * 40,
           "environment_digest": "2" * 64, "requested_device": "cuda:0", "actual_device": "cuda:0",
           "gpu_uuid": "GPU-TEST", "seed": 7, "serializer_version": "runtime.v1"}
    cleanup = {"telemetry_cleanup": True, "cleanup_digest": "3" * 64, "df_before_bytes": 100,
               "df_after_bytes": 90, "free_before_bytes": 200, "free_after_bytes": 180}
    return checks, run, cleanup


def test_valid_mobile_receipt_matches_all_three_digests():
    row = _roster()[0]
    checks, run, cleanup = _valid_parts()
    receipt = make_receipt(roster_row=row, status="qualified", receipt_id="hc-rt-001.valid",
                           checks=checks, run=run, cleanup=cleanup, evaluator_digest="4" * 64)
    assert receipt["runtime_qualified"] is True
    assert validate_receipt(receipt, roster_row=row)["receipt_digest"] == receipt["receipt_digest"]


def test_missing_evidence_is_nonqualified_and_runner_keeps_all_27_receipts(tmp_path):
    summary = run_queue(roster_path=HERE / "robot_runtime_roster.v1.json",
                        evidence_dir=tmp_path / "evidence", output_dir=tmp_path / "out")
    assert summary["total"] == 27
    assert summary["qualified"] == 0
    assert summary["non_qualified"] == 27
    assert summary["simulator_started"] is False and summary["gpu_started"] is False
    receipts = list((tmp_path / "out" / "receipts").glob("*.json"))
    assert len(receipts) == 27
    assert all(json.loads(path.read_text())["status"] == "blocked_missing_evidence" for path in receipts)


def test_failed_receipt_requires_failure_and_is_preserved(tmp_path):
    row = _roster()[0]
    receipt = make_receipt(roster_row=row, status="failed", receipt_id="hc-rt-001.failed",
                           failure={"code": "sensor_readback_failed", "message": "no frame", "evidence_path": "/tmp/raw.json"})
    assert receipt["runtime_qualified"] is False
    with pytest.raises(RuntimeReceiptError):
        bad = dict(receipt, runtime_qualified=True)
        bad["receipt_digest"] = canonical_sha256({k: v for k, v in bad.items() if k != "receipt_digest"})
        validate_receipt(bad, roster_row=row)

    # The queue copies a valid failed receipt as failed evidence; it does not
    # retry or replace it with a synthetic pass.
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    output = tmp_path / "output"
    (evidence / f"{row['instance_id']}.json").write_text(json.dumps(receipt))
    summary = run_queue(roster_path=HERE / "robot_runtime_roster.v1.json", evidence_dir=evidence, output_dir=output)
    retained = json.loads((output / "receipts" / f"{row['instance_id']}.json").read_text())
    assert summary["qualified"] == 0 and retained["status"] == "failed"


def test_three_digest_mismatch_is_rejected():
    row = _roster()[0]
    receipt = make_receipt(roster_row=row, status="failed", receipt_id="hc-rt-001.mismatch",
                           failure={"code": "load_failed", "message": "load failed", "evidence_path": None})
    bad = dict(receipt, capability_digest="f" * 64)
    bad["receipt_digest"] = canonical_sha256({k: v for k, v in bad.items() if k != "receipt_digest"})
    with pytest.raises(RuntimeReceiptError, match="capability_digest"):
        validate_receipt(bad, roster_row=row)


def test_independent_arm_requires_successful_pick_and_place():
    row = next(item for item in _roster() if item["platform_type"] == "ur5")
    checks, run, cleanup = _valid_parts()
    missing = {"required": True,
               "pick": {"attempted": True, "succeeded": True, "evidence_digest": "5" * 64},
               "place": {"attempted": False, "succeeded": False, "evidence_digest": "6" * 64}}
    with pytest.raises(RuntimeReceiptError, match="attempted"):
        make_receipt(roster_row=row, status="qualified", receipt_id="hc-rt-025.bad-arm",
                     checks=checks, run=run, cleanup=cleanup, manipulation=missing, evaluator_digest="4" * 64)
    good = dict(missing, place={"attempted": True, "succeeded": True, "evidence_digest": "6" * 64})
    receipt = make_receipt(roster_row=row, status="qualified", receipt_id="hc-rt-025.good-arm",
                           checks=checks, run=run, cleanup=cleanup, manipulation=good, evaluator_digest="4" * 64)
    assert receipt["runtime_qualified"] is True


def test_promotion_embeds_only_digest_bound_qualified_receipts_and_audit_accepts(tmp_path):
    rows = _roster()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    checks, run, cleanup = _valid_parts()
    for row in rows:
        manipulation = None
        if row["configuration_role"] == "standalone_arm":
            manipulation = {"required": True,
                            "pick": {"attempted": True, "succeeded": True, "evidence_digest": "5" * 64},
                            "place": {"attempted": True, "succeeded": True, "evidence_digest": "6" * 64}}
        receipt = make_receipt(roster_row=row, status="qualified", receipt_id=f"{row['instance_id']}.qualified",
                               checks=checks, run=run, cleanup=cleanup, manipulation=manipulation, evaluator_digest="4" * 64)
        (evidence / f"{row['instance_id']}.json").write_text(json.dumps(receipt))
    promoted_path = tmp_path / "promoted.json"
    promoted = promote_roster(roster_path=HERE / "robot_runtime_roster.v1.json", receipt_dir=evidence, output_path=promoted_path)
    assert sum(row["runtime_qualified"] for row in promoted["robots"]) == 27
    assert audit_robot_pool(promoted_path)["case_ready"] is True


def test_promotion_missing_and_digest_mismatch_stay_fail_closed(tmp_path):
    rows = _roster()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    checks, run, cleanup = _valid_parts()
    row = rows[0]
    receipt = make_receipt(roster_row=row, status="qualified", receipt_id="hc-rt-001.qualified",
                           checks=checks, run=run, cleanup=cleanup, evaluator_digest="4" * 64)
    receipt["capability_digest"] = "f" * 64
    receipt["receipt_digest"] = canonical_sha256({key: value for key, value in receipt.items() if key != "receipt_digest"})
    (evidence / f"{row['instance_id']}.json").write_text(json.dumps(receipt))
    promoted_path = tmp_path / "promoted.json"
    promoted = promote_roster(roster_path=HERE / "robot_runtime_roster.v1.json", receipt_dir=evidence, output_path=promoted_path)
    assert promoted["robots"][0]["runtime_qualified"] is False
    assert promoted["robots"][0]["runtime_qualification_receipt"] is None
    assert sum(item["runtime_qualified"] for item in promoted["robots"]) == 0
    audit = audit_robot_pool(promoted_path)
    assert audit["case_ready"] is False and audit["roster_summary"]["runtime_qualified_count"] == 0
