"""Scoring extension isolation and independently recomputed dual-time facts."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_restaurant_business_facts import (
    ARCHIVE_NAME, FLAGS, build_restaurant_business_fact_archive,
    build_restaurant_business_facts, replay_restaurant_business_fact_archive,
    restaurant_business_fact_observations, export_restaurant_business_facts,
)
from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.goal_scoring_adapter import build_scoring_adapter_metadata
from demo.benchmark.provenance import canonical_sha256


@pytest.fixture
def public_task():
    return build_restaurant_deployment_v2()["public_task"]


def _metadata(root, public):
    return build_scoring_adapter_metadata(public, episode_id="business-facts-test", output_root=root)


def _assert_existing_observations_unchanged(left, right):
    for key in ("cost_evidence", "deployment_assessment", "plan_admission",
                "task_allocation", "communication_analysis", "communication_capacity",
                "checkpoint_branch", "native_oracle_registry"):
        assert left[key] == right[key], key


def test_opt_in_missing_source_is_explicit_and_does_not_change_legacy_metrics(public_task, tmp_path):
    legacy = _metadata(tmp_path, public_task)
    assert "restaurant_business_facts" not in legacy
    assert legacy["protocol_mapping_revision"].endswith("v7")
    (tmp_path / "episode_manifest.json").write_text(json.dumps({
        "restaurant_business_facts": {"version": 1, "archive": ARCHIVE_NAME}}))
    current = _metadata(tmp_path, public_task)
    assert current["protocol_mapping_revision"].endswith("v6")
    source = current["restaurant_business_facts"]
    assert source["status"] == "missing"
    assert source["report"] is None
    assert all(source[key] is False for key in FLAGS)
    _assert_existing_observations_unchanged(legacy, current)


def test_malformed_source_is_isolated_from_existing_observations(public_task, tmp_path):
    legacy = _metadata(tmp_path, public_task)
    (tmp_path / ARCHIVE_NAME).write_text("{")
    current = _metadata(tmp_path, public_task)
    source = current["restaurant_business_facts"]
    assert source["status"] == "invalid"
    assert source["report"] is None
    assert all(source[key] is False for key in FLAGS)
    assert ARCHIVE_NAME in current["archive_index"]["files"]
    _assert_existing_observations_unchanged(legacy, current)


@pytest.fixture
def native_inputs():
    from test_benchmark_goal_restaurant_business_v3 import build_restaurant_business_v3_fixture

    return build_restaurant_business_v3_fixture()


def _archive(inputs):
    return build_restaurant_business_fact_archive(
        inputs["public_task"], episode_id=inputs["session_report"]["episode_id"],
        verifier_inputs=inputs)


def test_real_verifier_retains_both_clocks_without_inventing_continuous_progress(native_inputs):
    before = canonical_sha256(native_inputs)
    archive = _archive(native_inputs)
    facts = replay_restaurant_business_fact_archive(
        archive, native_inputs["public_task"],
        episode_id=native_inputs["session_report"]["episode_id"])
    assert facts["status"] == "observed"
    assert len(facts["physical_milestone_facts"]) == 3
    assert all(row["available_at_s"] > row["physical_time_s"]
               for row in facts["physical_milestone_facts"])
    assert set(facts["initial_predicate_truth"].values()) == {None}
    assert facts["weighted_progress"] is None
    assert facts["protocol_score"] is None
    assert facts["formal_statistics_eligible"] is False
    assert all(facts[key] is False for key in FLAGS)
    assert canonical_sha256(native_inputs) == before


def test_rehashing_forged_derived_fact_does_not_authorize_it(native_inputs, tmp_path):
    archive = _archive(native_inputs)
    archive["facts"]["physical_milestone_facts"][0]["physical_time_s"] = 0.0
    archive.pop("archive_sha256")
    archive["archive_sha256"] = canonical_sha256(archive)
    (tmp_path / ARCHIVE_NAME).write_text(json.dumps(archive))
    result = restaurant_business_fact_observations(
        tmp_path, native_inputs["public_task"],
        episode_id=native_inputs["session_report"]["episode_id"])
    assert result["status"] == "invalid"
    assert result["report"] is None


def test_foreign_episode_and_task_sources_cannot_be_joined(native_inputs, tmp_path):
    archive = _archive(native_inputs)
    (tmp_path / ARCHIVE_NAME).write_text(json.dumps(archive))
    public = native_inputs["public_task"]
    result = restaurant_business_fact_observations(tmp_path, public, episode_id="foreign")
    assert result["status"] == "invalid"
    wrong_task = deepcopy(public)
    wrong_task["task_id"] += "-foreign"
    with pytest.raises(ValueError, match="public task"):
        build_restaurant_business_facts(
            wrong_task, episode_id=native_inputs["session_report"]["episode_id"],
            verifier_inputs=native_inputs)


def test_export_connects_existing_raw_costs_without_fabricating_missing_sources(native_inputs, tmp_path):
    output = tmp_path / "export"
    public, session = native_inputs["public_task"], native_inputs["session_report"]
    before = canonical_sha256(native_inputs)
    metadata = export_restaurant_business_facts(
        output, public, episode_id=session["episode_id"], verifier_inputs=native_inputs)
    assert metadata["restaurant_business_facts"]["status"] == "observed"
    cost = metadata["cost_evidence"]
    assert cost["accepted_actions"]["total_count"] == len(session["actions"])
    expected_bytes = sum(row["bytes"] for row in native_inputs["communication_report"]["ledger"]
                         if row["event"] == "bytes_served")
    assert cost["communication"]["total_bytes"] == expected_bytes > 0
    assert cost["source_binding"]["status"] == "verified"
    assert cost["activation_cost_proxy"]["value"] is None
    assert cost["planning_compute_cost"]["total_s"] is None
    assert canonical_sha256(native_inputs) == before
    archived_hash = (output / ARCHIVE_NAME).read_bytes()
    with pytest.raises(FileExistsError):
        export_restaurant_business_facts(
            output, public, episode_id=session["episode_id"], verifier_inputs=native_inputs)
    assert (output / ARCHIVE_NAME).read_bytes() == archived_hash


def test_bad_business_bundle_does_not_discard_independently_bound_costs(native_inputs, tmp_path):
    output = tmp_path / "export"
    public, session = native_inputs["public_task"], native_inputs["session_report"]
    metadata = export_restaurant_business_facts(
        output, public, episode_id=session["episode_id"], verifier_inputs=native_inputs)
    archive = json.loads((output / ARCHIVE_NAME).read_text())
    archive["verifier_inputs"]["session_report"]["episode_id"] = "foreign"
    (output / ARCHIVE_NAME).write_text(json.dumps(archive))
    changed = build_scoring_adapter_metadata(public, episode_id=session["episode_id"], output_root=output)
    assert changed["restaurant_business_facts"]["status"] == "invalid"
    _assert_existing_observations_unchanged(metadata, changed)
    assert changed["cost_evidence"]["communication"]["total_bytes"] > 0


def test_missing_receipt_evidence_preserves_independent_inspection_progress(native_inputs):
    native_inputs["pump_report"]["physical_records"] = [
        row for row in native_inputs["pump_report"]["physical_records"]
        if row["task"]["skill"] != "relay"]
    facts = build_restaurant_business_facts(
        native_inputs["public_task"], episode_id=native_inputs["session_report"]["episode_id"],
        verifier_inputs=native_inputs)
    assert facts["status"] == "partial"
    assert facts["unknown_predicate_ids"] == ["readiness_receipt_committed"]
    assert {row["predicate_id"] for row in facts["physical_milestone_facts"]} == {
        "kitchen_service_inspected", "dining_service_inspected"}
    assert facts["weighted_progress"] is None


@pytest.mark.parametrize("phase", ["observation", "relay_result"])
def test_real_result_loss_preserves_physical_facts_with_unknown_availability(phase):
    from test_benchmark_goal_restaurant_business_v3 import build_restaurant_business_v3_fixture

    inputs = build_restaurant_business_v3_fixture(**(
        {"drop_observation_results": True} if phase == "observation"
        else {"drop_relay_result": True}))
    facts = build_restaurant_business_facts(
        inputs["public_task"], episode_id=inputs["session_report"]["episode_id"],
        verifier_inputs=inputs)
    assert facts["public_availability_complete"] is False
    if phase == "observation":
        assert facts["status"] == "partial"
        assert facts["unknown_predicate_ids"] == ["readiness_receipt_committed"]
        unavailable = {"kitchen_service_inspected", "dining_service_inspected"}
    else:
        assert facts["status"] == "observed"
        assert facts["business_review"]["business_predicates_complete"] is True
        assert facts["unknown_predicate_ids"] == []
        unavailable = {"readiness_receipt_committed"}
    assert set(facts["unavailable_predicate_ids"]) == unavailable
    for row in facts["physical_milestone_facts"]:
        if row["predicate_id"] in unavailable:
            assert row["satisfied_at_measurement"] is True
            assert row["physical_time_s"] is not None
            assert row["available_at_s"] is None
            assert row["result_availability_lag_s"] is None
