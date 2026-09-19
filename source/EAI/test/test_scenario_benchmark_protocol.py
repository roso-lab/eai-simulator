"""Tests for fixed, leakage-safe scenario-pool benchmark protocol tooling."""

import json
from pathlib import Path
from benchmark_evidence_support import frozen_attempt

from tools.scenario_generation.benchmark_protocol import build_protocol, file_sha256, summarize_protocol


def _record(identifier, family, signature, *, skeleton="COV-01", labels=None):
    return {
        "id": identifier,
        "file": f"instances/{identifier}.json",
        "family_id": family,
        "layout_signature": signature,
        "skeleton": skeleton,
        "labels": ["spawn_validated"] if labels is None else labels,
    }


def _write_instances(pool_dir: Path, records: list[dict]) -> None:
    instances = pool_dir / "instances"
    instances.mkdir(parents=True)
    for record in records:
        (instances / f"{record['id']}.json").write_text(
            json.dumps({"id": record["id"]}), encoding="utf-8"
        )


def test_protocol_freezes_dossier_and_payload_provenance(tmp_path: Path) -> None:
    record = _record("provenance", "family", "layout")
    second = _record("provenance_2", "family_2", "layout_2")
    record.update({"robot_dossier_snapshot_sha256": "a" * 64, "payload_policy_hash": "b" * 64})
    _write_instances(tmp_path, [record, second])
    protocol = build_protocol(tmp_path, {"pool": "unit", "instances": [record, second]}, split_seed=1, test_fraction=0.5)
    frozen = next(item for item in protocol["instances"] if item["id"] == "provenance")
    assert frozen["robot_dossier_snapshot_sha256"] == "a" * 64
    assert frozen["payload_policy_hash"] == "b" * 64


def test_protocol_keeps_paired_and_duplicate_layout_records_together(tmp_path: Path) -> None:
    records = [
        _record("f1_low", "family_1", "layout_1"),
        _record("f1_high", "family_1", "layout_1"),
        _record("f2", "family_2", "layout_shared"),
        _record("f3", "family_3", "layout_shared"),
        _record("f4", "family_4", "layout_4"),
    ]
    _write_instances(tmp_path, records)
    pool = {"pool": "unit", "template": {"key": "factory"}, "instances": records}
    protocol = build_protocol(tmp_path, pool, split_seed=23, test_fraction=0.4)

    assignment = {item["id"]: item["group_id"] for item in protocol["instances"]}
    assert assignment["f1_low"] == assignment["f1_high"]
    assert assignment["f2"] == assignment["f3"]
    development = {group["id"] for group in protocol["split"]["development_groups"]}
    testing = {group["id"] for group in protocol["split"]["test_groups"]}
    assert development.isdisjoint(testing)
    assert development | testing == set(assignment.values())


def test_summary_requires_current_input_hash_and_separates_evidence_tiers(tmp_path: Path) -> None:
    records = [
        _record("nav", "family_nav", "layout_nav", skeleton="COV-01"),
        _record("kin", "family_kin", "layout_kin", skeleton="TRN-01"),
        _record("stale", "family_stale", "layout_stale"),
        _record("pending", "family_pending", "layout_pending"),
    ]
    _write_instances(tmp_path, records)
    pool = {"pool": "unit", "instances": records}
    protocol = build_protocol(tmp_path, pool, split_seed=7, test_fraction=0.5)
    for record in records:
        input_hash = file_sha256(tmp_path / record["file"])
        if record["id"] != "pending":
            mode = "kinematic" if record["id"] == "kin" else "none"
            tier = "kinematic_execution_validated" if mode == "kinematic" else "navigation_execution_validated"
            record["execution"] = frozen_attempt(
                tmp_path, scenario_sha256=input_hash, options={"transport_mode": mode}, tier=tier)
    (tmp_path / "instances" / "stale.json").write_text('{"id":"changed"}', encoding="utf-8")
    from EAI.scenario_spec.benchmark_physics import BENCHMARK_PHYSICS_CONTRACT
    for record in records:
        if "execution" in record:
            record["execution"]["report_body"]["benchmark_mass_audit"] = {
                "contract": dict(BENCHMARK_PHYSICS_CONTRACT), "validated": True,
                "arms": {}, "violations": []}

    report = summarize_protocol(tmp_path, pool, protocol, split_name="all")
    summary = report["summary"]
    assert summary["frozen_instances"] == 4
    assert summary["changed_or_missing_input"] == 1
    assert summary["attempted_with_matching_input"] == 2
    assert summary["validated_execution"] == 2
    assert summary["unattempted"] == 1
    assert summary["tiers"] == {
        "navigation_execution_validated": 1,
        "kinematic_execution_validated": 1,
    }
    assert summary["complete_matching_execution"] is False
    records[0]["execution"]["report_body"].pop("benchmark_mass_audit")
    assert summarize_protocol(tmp_path, pool, protocol, split_name="all")["summary"]["validated_execution"] == 1


def test_summary_separates_harness_ineligibility_from_runtime_attempts(tmp_path: Path) -> None:
    records = [
        _record("unsupported", "family_1", "layout_1"),
        _record("other", "family_2", "layout_2"),
    ]
    _write_instances(tmp_path, records)
    pool = {"pool": "unit", "instances": records}
    protocol = build_protocol(tmp_path, pool, split_seed=3, test_fraction=0.5)
    current = records[0]
    current["execution"] = {
        "attempted_at": "2099-01-01T00:00:00+00:00",
        "status": "not_executable",
        "scenario_sha256": file_sha256(tmp_path / current["file"]),
        "report_body": {"execution_validation": {"tier": "not_executable_by_current_harness", "validated": False}},
    }

    report = summarize_protocol(tmp_path, pool, protocol, split_name="all")
    assert report["summary"]["not_executable_by_current_harness"] == 1
    assert report["summary"]["attempted_with_matching_input"] == 0
    assert report["summary"]["unattempted"] == 1


def test_test_split_excludes_matching_execution_that_predates_freeze(tmp_path: Path) -> None:
    records = [
        _record("test_candidate", "family_1", "layout_1"),
        _record("development_candidate", "family_2", "layout_2"),
    ]
    _write_instances(tmp_path, records)
    pool = {"pool": "unit", "instances": records}
    protocol = build_protocol(tmp_path, pool, split_seed=3, test_fraction=0.5)
    test_group_ids = {group["id"] for group in protocol["split"]["test_groups"]}
    test_instance = next(item for item in protocol["instances"] if item["group_id"] in test_group_ids)
    current = next(item for item in records if item["id"] == test_instance["id"])
    current["execution"] = {
        "attempted_at": "2000-01-01T00:00:00+00:00",
        "scenario_sha256": file_sha256(tmp_path / current["file"]),
        "report_body": {"execution_validation": {"tier": "navigation_execution_validated", "validated": True}},
    }

    report = summarize_protocol(tmp_path, pool, protocol, split_name="test")
    assert report["summary"]["pre_freeze_execution"] == 1
    assert report["summary"]["validated_execution"] == 0
    assert report["summary"]["unattempted"] == 1


def test_protocol_rejects_unbound_legacy_report_and_changed_frozen_sources(tmp_path):
    records = [_record("first", "family_1", "layout_1"), _record("second", "family_2", "layout_2")]
    _write_instances(tmp_path, records)
    pool = {"pool": "unit", "instances": records}
    protocol = build_protocol(tmp_path, pool, split_seed=3, test_fraction=0.5)
    for record in records:
        record["execution"] = frozen_attempt(tmp_path, scenario_sha256=file_sha256(tmp_path / record["file"]))
    assert summarize_protocol(tmp_path, pool, protocol, split_name="all")["summary"]["validated_execution"] == 2
    records[0]["execution"]["report_body"].pop("execution_provenance")
    assert summarize_protocol(tmp_path, pool, protocol, split_name="all")["summary"]["validated_execution"] == 1
    protocol["execution_sources"]["demo/factory_benchmark/evaluation.py"] = "b" * 64
    summary = summarize_protocol(tmp_path, pool, protocol, split_name="all")["summary"]
    assert summary["validated_execution"] == 0
    assert summary["attempt_not_validated"] == 2
