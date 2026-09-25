import json

from demo.benchmark.goal_independent_recomputation import (
    recompute_episode_facts,
    write_recomputation,
)


def _archive(tmp_path, *, evidence=None, session=None, ledger=None,
             sidecar=None, report=None):
    goal = tmp_path / "goal_episode"
    goal.mkdir(parents=True, exist_ok=True)
    if evidence is not None:
        (goal / "native_evidence_log.json").write_text(
            json.dumps(evidence), encoding="utf-8")
    if session is not None:
        (goal / "goal_session_report.json").write_text(
            json.dumps(session), encoding="utf-8")
    if ledger is not None:
        (tmp_path / "communication_tool_replay.json").write_text(
            json.dumps({"ledger": ledger}), encoding="utf-8")
    if sidecar is not None:
        (tmp_path / "scoring_adapter_metadata.json").write_text(
            json.dumps(sidecar), encoding="utf-8")
    if report is not None:
        (tmp_path / "restaurant_algorithm_episode_report.json").write_text(
            json.dumps(report), encoding="utf-8")
    return tmp_path


def _evidence_records():
    return {
        "schema": "eai.restaurant.formal_goal_native_evidence", "version": 1,
        "records": [
            {"kind": "arrival", "task_id": "arrive-k", "region_id": "kitchen_service",
             "evidence_ref": "sha256:a", "arrival": {"time_s": 20.0}},
            {"kind": "observation", "task_id": "obs-k", "region_id": "kitchen_service",
             "evidence_ref": "sha256:b", "stamp": {"time_s": 45.0}},
            {"kind": "relay_and_receipt", "task_id": "relay",
             "evidence_ref": "sha256:c",
             "relay_publication": {"time_s": 188.0}},
        ]}


def _session_report():
    return {"episode_id": "ep-1", "report": {
        "episode_id": "ep-1", "actions": [], "events": [],
        "deployment_assessment": {
            "history": [{"decision": "deployable"},
                        {"decision": "not_deployable"}],
            "oracle_evaluations": [{"oracle_status": "verified"},
                                   {"oracle_status": "failed"}],
            "terminated": False}}}


def _full_archive(tmp_path):
    return _archive(
        tmp_path, evidence=_evidence_records(), session=_session_report(),
        ledger=[{"event": "published"}, {"event": "bytes_served", "bytes": 10,
                                         "pool": "p2p", "direction": "p2p"}],
        report={"business_episode_execution_validated": True})


def test_all_families_recomputed_from_raw_sources(tmp_path):
    result = recompute_episode_facts(_full_archive(tmp_path))
    assert result["status"] == "recomputed"
    assert result["missing_families"] == []
    progress = result["families"]["progress"]["recomputed"]
    assert progress["arrival_count"] == 1
    assert progress["observation_count"] == 1
    assert progress["receipt_count"] == 1
    assert progress["claim_check"]["claim_supported_by_receipts"] is True
    deployment = result["families"]["deployment"]["recomputed"]
    assert deployment["required_epoch_count"] == 2
    assert deployment["oracle_verified_count"] == 1
    communication = result["families"]["communication"]["recomputed"]
    assert communication["bytes_by_pool"] == {"p2p": 10}
    assert communication["direction_complete"] is True
    assert result["scoreable"] is False


def test_missing_sources_are_explicit_not_zero(tmp_path):
    result = recompute_episode_facts(_archive(tmp_path))
    assert result["status"] == "failed"
    assert set(result["missing_families"]) == {
        "progress", "deployment", "communication", "cost"}
    for name in result["missing_families"]:
        row = result["families"][name]["recomputed"]
        assert row["status"] == "missing_source"
        assert row["reason"]


def test_duplicate_receipt_refs_are_flagged(tmp_path):
    evidence = _evidence_records()
    evidence["records"].append(dict(evidence["records"][2]))
    result = recompute_episode_facts(
        _archive(tmp_path, evidence=evidence, session=_session_report(),
                 ledger=[]))
    progress = result["families"]["progress"]["recomputed"]
    assert progress["duplicate_evidence_refs"] == ["sha256:c"]


def test_archived_claim_mismatch_is_reported(tmp_path):
    result = recompute_episode_facts(_archive(
        tmp_path, evidence=_evidence_records(), session=_session_report(),
        ledger=[], sidecar={"deployment_assessment": {"fake": "claim"}}))
    comparison = result["families"]["deployment"]["comparison"]
    assert comparison["agreement"] == "mismatch"
    assert "mismatched_families" in result
    assert "deployment" in result["mismatched_families"]


def test_business_claim_without_receipt_is_unsupported(tmp_path):
    result = recompute_episode_facts(_archive(
        tmp_path, evidence={"records": []}, session=_session_report(),
        ledger=[], report={"business_episode_execution_validated": True}))
    check = result["families"]["progress"]["recomputed"]["claim_check"]
    assert check["business_episode_execution_validated"] is True
    assert check["receipt_count_recomputed"] == 0
    assert check["claim_supported_by_receipts"] is False


def test_missing_direction_fails_closed_in_communication(tmp_path):
    result = recompute_episode_facts(_archive(
        tmp_path, evidence=_evidence_records(), session=_session_report(),
        ledger=[{"event": "bytes_served", "bytes": 5, "pool": "p2p"}]))
    comm = result["families"]["communication"]["recomputed"]
    assert comm["direction_complete"] is False


def test_writer_round_trips(tmp_path):
    path = write_recomputation(_full_archive(tmp_path),
                               tmp_path / "recomputation.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == (
        "eai.benchmark.goal_independent_recomputation")
    assert payload["version"] == 1
    assert payload["execution_validated"] is False


def test_progress_curve_derived_from_raw_records_with_mapping(tmp_path):
    result = recompute_episode_facts(_full_archive(tmp_path), milestone_kind_map={
        "observation:kitchen_service": "kitchen_service_inspected",
        "observation:dining_service": "dining_service_inspected",
        "relay_and_receipt": "readiness_receipt_committed",
    }, horizon_s=240.0)
    curve = result["families"]["progress"]["recomputed"]["progress_curve"]
    assert curve["status"] == "computed"
    assert curve["weight_policy"] == "protocol-v1-equal"
    # The fixture archive has one observation region (kitchen) and one
    # relay receipt; the unmapped dining milestone stays unsatisfied.
    assert curve["Q_H"] == 2 / 3
    assert curve["completed_milestones"] == [
        "kitchen_service_inspected", "readiness_receipt_committed"]
    assert curve["nPGI_exec"] is not None


def test_progress_curve_absent_mapping_is_explicit_not_zero(tmp_path):
    result = recompute_episode_facts(_full_archive(tmp_path))
    progress = result["families"]["progress"]["recomputed"]
    assert "progress_curve" not in progress
    assert progress["receipt_count"] == 1
