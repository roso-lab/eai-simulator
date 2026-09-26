"""Tests for the preregistered raw-only communication source matrix."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from demo.benchmark.communication_analysis import CAPACITY_V2_METHOD_ID
from demo.benchmark.goal_communication_metrics import (
    COMMUNICATION_METRICS_SCHEMA,
    COMMUNICATION_METRICS_VERSION,
)
from demo.benchmark.goal_communication_source_binding import (
    COMMUNICATION_FAMILIES,
    EXPECTED_MANIFEST_SCHEMA,
    OBSERVED_IDENTITY_FIELDS,
    RAW_ONLY_DIMENSION_STATUS,
    build_communication_source_matrix,
    communication_expected_manifest_sha256,
)
from demo.benchmark.goal_metric_aggregation import (
    NA_DIMENSION_SOURCE_NOT_WIRED,
    build_protocol_metrics_report,
)


_SHA = "a" * 64
_COMM_SHA = "b" * 64
_PROGRESS_SHA = "c" * 64
_ARCHIVE_SHA = "d" * 64
_ENDPOINT_SHA = "e" * 64
_SPEC_SHA = "f" * 64


def _cell(family="fixed_delay", condition_id="c0", *, comparison_level=None):
    cell = {
        "task_id": "task0", "base_instance_id": "i0", "family": family,
        "condition_id": condition_id, "channel_seed": "s0",
        "condition_spec_sha256": _SPEC_SHA,
    }
    if comparison_level is not None:
        cell["comparison_level"] = comparison_level
    if family == "capacity":
        cell.update({
            "capacity_method_id": CAPACITY_V2_METHOD_ID,
            "capacity_unit": "bits_per_second",
            "capacity_mode": "unlimited",
            "capacity_bits_per_s": None,
        })
    return cell


def _manifest(cells=None):
    if cells is None:
        cells = [_cell()]
    return {
        "schema": EXPECTED_MANIFEST_SCHEMA,
        "version": 1,
        "frozen_before_execution": True,
        "frozen_at": "2026-09-26T00:00:00Z",
        "task_id": "task0",
        "contract_sha256": _SHA,
        "evaluator_ref": "evaluator-01",
        "metric_name": "MCR_comm",
        "excluded_message_ids": ["benchmark-control-1"],
        "archive_schema": "eai.benchmark.goal_episode_archive",
        "archive_version": 1,
        "transport_report_schema": "eai.communication.report",
        "transport_report_version": 1,
        "shared_transport_runtime_ref": "runner.shared_communication_runtime",
        "cells": cells,
    }


def _condition(condition_id="c0", *, metric="MCR_comm", value=.75,
               transport_status="computed", offer_bits=0):
    task = {
        "status": "computed" if value is not None else "na_missing_milestones",
        "SR_comm": 1.0,
        "MCR_comm": value if metric == "MCR_comm" else .8,
        "AUP_comm": value if metric == "AUP_comm" else .6,
        "e_step": value if metric == "e_step" else .1,
        "sr_basis": "Q(H)", "proxy_only": True,
        "global_termination_predicate_available": False,
        "SR_comm_formal": None,
    }
    if value is None:
        task[metric] = None
    return {
        "schema": COMMUNICATION_METRICS_SCHEMA,
        "version": COMMUNICATION_METRICS_VERSION,
        "condition_id": condition_id,
        "status": transport_status,
        "task_performance": task,
        "transport_companion": {"status": transport_status,
                                "delivery_rate": .99},
        "communication_cost": {"status": transport_status,
                               "B_offer_bits": offer_bits,
                               "B_link_bits": offer_bits},
        "scoreable": False, "execution_validated": False,
        "business_outcomes_validated": False,
    }


def _row(manifest, cell=None, *, episode_id="ep0", metric="MCR_comm",
         value=.75, transport_status="computed", offer_bits=0):
    cell = dict(cell or manifest["cells"][0])
    row = {
        "episode_id": episode_id,
        **{key: cell[key] for key in (
            "task_id", "base_instance_id", "family", "condition_id",
            "channel_seed")},
        "metric_name": metric,
        "condition_metrics": _condition(
            cell["condition_id"], metric=metric, value=value,
            transport_status=transport_status, offer_bits=offer_bits),
    }
    if "comparison_level" in cell:
        row["comparison_level"] = cell["comparison_level"]
    bindings = {
        "contract_sha256": manifest["contract_sha256"],
        "expected_manifest_sha256": communication_expected_manifest_sha256(manifest),
        "episode_id": episode_id,
        **{key: row[key] for key in (
            "task_id", "base_instance_id", "family", "condition_id",
            "channel_seed")},
        "condition_spec_sha256": cell["condition_spec_sha256"],
        "metric_name": metric,
        "excluded_message_ids_sha256": sha256(json.dumps(
            manifest["excluded_message_ids"], sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest(),
        "evaluator_ref": manifest["evaluator_ref"],
        "termination_anchor_source": "native_receipt",
        "communication_report_sha256": _COMM_SHA,
        "progress_source_sha256": _PROGRESS_SHA,
        "archive_evidence_ref": "archive://ep0/communication.json",
        "archive_sha256": _ARCHIVE_SHA,
        "archive_schema": "eai.benchmark.goal_episode_archive",
        "archive_version": 1,
        "transport_report_schema": manifest["transport_report_schema"],
        "transport_report_version": manifest["transport_report_version"],
        "transport_replay_validated": True,
        "shared_transport_runtime_ref": manifest["shared_transport_runtime_ref"],
        "endpoint_pool_binding_sha256": _ENDPOINT_SHA,
    }
    if "comparison_level" in cell:
        bindings["comparison_level"] = cell["comparison_level"]
    row["source_bindings"] = bindings
    return row


def _five_family_manifest():
    return _manifest([
        _cell("fixed_delay", "fixed-1", comparison_level="latency-1"),
        _cell("jitter", "jitter-1", comparison_level="latency-1"),
        _cell("iid_loss", "iid-1", comparison_level="loss-1"),
        _cell("burst_loss", "burst-1", comparison_level="loss-1"),
        _cell("capacity", "cap-unlimited"),
    ])


def test_five_family_manifest_happy_path_is_raw_only_not_score():
    manifest = _five_family_manifest()
    rows = [_row(manifest, cell, episode_id=f"ep-{index}")
            for index, cell in enumerate(manifest["cells"])]
    result = build_communication_source_matrix(manifest, rows)
    assert result["status"] == "raw_only_validation_complete"
    assert result["accepted_raw_only_count"] == 5
    assert result["required_families"] == sorted(COMMUNICATION_FAMILIES)
    assert result["formal_scoring_wired"] is False
    assert result["not_protocol_score"] is True
    assert result["dimension_status"] == RAW_ONLY_DIMENSION_STATUS


def test_missing_and_unexpected_are_distinct_and_never_imputed():
    manifest = _manifest([_cell(condition_id="expected")])
    unexpected_cell = _cell(condition_id="unexpected")
    result = build_communication_source_matrix(
        manifest, [_row(manifest, unexpected_cell)])
    assert result["missing_cell_count"] == 1
    assert result["unexpected_row_count"] == 1
    assert result["rejected_count"] == 1
    assert "unexpected_cell" in result["rows"][0]["source_reasons"]
    assert result["missing_is_not_zero"] is True
    assert result["no_imputation"] is True


def test_partial_manifest_is_explicitly_incomplete_for_five_family_protocol():
    manifest = _manifest()
    result = build_communication_source_matrix(
        manifest, [_row(manifest)])
    assert result["accepted_raw_only_count"] == 1
    assert result["manifest_complete_family_coverage"] is False
    assert "capacity" in result["missing_manifest_families"]
    assert result["status"] == "raw_only_incomplete"


def test_duplicate_cell_and_reused_episode_reject_every_affected_row():
    manifest = _manifest()
    first = _row(manifest)
    duplicate = deepcopy(first)
    duplicate["condition_metrics"]["task_performance"]["MCR_comm"] = .1
    result = build_communication_source_matrix(manifest, [first, duplicate])
    assert result["duplicate_row_count"] == 2
    assert result["accepted_raw_only_count"] == 0
    assert all("duplicate_matrix_cell" in row["source_reasons"]
               for row in result["rows"])
    assert all("episode_id_reused_across_cells" in row["source_reasons"]
               for row in result["rows"])

    cells = [_cell(condition_id="c0"), _cell(condition_id="c1")]
    manifest = _manifest(cells)
    reused = [_row(manifest, cell, episode_id="same") for cell in cells]
    result = build_communication_source_matrix(manifest, reused)
    assert all("episode_id_reused_across_cells" in row["source_reasons"]
               for row in result["rows"])


def test_top_level_nested_conflict_and_manifest_digest_fail_closed():
    manifest = _manifest()
    row = _row(manifest)
    row["source_bindings"]["channel_seed"] = "other"
    row["source_bindings"]["expected_manifest_sha256"] = "9" * 64
    result = build_communication_source_matrix(manifest, [row])
    reasons = result["rows"][0]["source_reasons"]
    assert "source_channel_seed_conflict" in reasons
    assert "expected_manifest_sha256_mismatch" in reasons


def test_metric_and_exclusion_policy_are_bound_to_manifest():
    manifest = _manifest()
    row = _row(manifest)
    row["metric_name"] = "AUP_comm"
    row["source_bindings"]["excluded_message_ids_sha256"] = "8" * 64
    result = build_communication_source_matrix(manifest, [row])
    reasons = result["rows"][0]["source_reasons"]
    assert "source_metric_name_conflict" in reasons
    assert "metric_name_not_expected" in reasons
    assert "excluded_message_ids_sha256_mismatch" in reasons


def test_native_communication_provenance_is_independent_and_fail_closed():
    manifest = _manifest()
    row = _row(manifest)
    row["source_bindings"].pop("endpoint_pool_binding_sha256")
    row["source_bindings"]["transport_replay_validated"] = False
    row["source_bindings"]["termination_anchor_source"] = "self_report"
    result = build_communication_source_matrix(manifest, [row])
    reasons = result["rows"][0]["source_reasons"]
    assert "endpoint_pool_binding_sha256_missing_or_invalid" in reasons
    assert "transport_replay_not_validated" in reasons
    assert "termination_anchor_source_not_trusted" in reasons
    assert result["accepted_raw_only_count"] == 0


def test_missing_identity_unknown_family_and_bad_condition_schema_are_audited():
    manifest = _manifest()
    missing = _row(manifest)
    missing["base_instance_id"] = ""
    missing["family"] = "unknown_family"
    missing["condition_metrics"]["schema"] = "unknown"
    missing["condition_metrics"]["version"] = 999
    result = build_communication_source_matrix(manifest, [missing])
    source = result["rows"][0]["source_reasons"]
    measurement = result["rows"][0]["measurement"]["reasons"]
    assert "base_instance_id_missing" in source
    assert "family_unknown" in source
    assert "condition_metrics_schema_unknown" in measurement
    assert "condition_metrics_version_unknown" in measurement


@pytest.mark.parametrize("field", OBSERVED_IDENTITY_FIELDS)
def test_each_missing_observed_identity_fails_closed(field):
    manifest = _manifest()
    row = _row(manifest)
    row[field] = ""
    result = build_communication_source_matrix(manifest, [row])
    assert f"{field}_missing" in result["rows"][0]["source_reasons"]
    assert result["rows"][0]["row_status"] == "rejected"


def test_archive_and_shared_runtime_bindings_are_not_native_booleans():
    manifest = _manifest()
    row = _row(manifest)
    row["source_bindings"].update({
        "archive_evidence_ref": "",
        "archive_schema": "",
        "archive_version": 0,
        "shared_transport_runtime_ref": "some-other-runtime",
        "native": True,
    })
    result = build_communication_source_matrix(manifest, [row])
    reasons = result["rows"][0]["source_reasons"]
    assert "archive_evidence_ref_missing" in reasons
    assert "archive_schema_missing" in reasons
    assert "archive_version_missing_or_invalid" in reasons
    assert "shared_transport_runtime_ref_not_expected" in reasons
    assert result["accepted_raw_only_count"] == 0


def test_untrusted_archive_schema_and_forged_release_claims_are_not_echoed():
    manifest = _manifest()
    row = _row(manifest)
    row["source_bindings"]["archive_schema"] = "synthetic.untrusted"
    row["source_bindings"]["archive_version"] = 999
    row["source_bindings"]["execution_validated"] = True
    row["release"] = True
    row["scoreable"] = True
    result = build_communication_source_matrix(manifest, [row])
    record = result["rows"][0]
    assert record["row_status"] == "rejected"
    assert "archive_schema_not_expected" in record["source_reasons"]
    assert "archive_version_not_expected" in record["source_reasons"]
    assert "row_release_must_be_false" in record["source_reasons"]
    assert "row_scoreable_must_be_false" in record["source_reasons"]
    assert "source_bindings_execution_validated_must_be_false" in (
        record["source_reasons"])
    assert record["raw_input_embedded"] is False
    assert "raw_input" not in record
    assert "synthetic.untrusted" not in json.dumps(result, sort_keys=True)


def test_no_observed_rows_differs_from_observed_but_unaccepted():
    manifest = _manifest()
    empty = build_communication_source_matrix(manifest, [])
    assert empty["status"] == "raw_only_no_observed_rows"
    assert empty["missing_cell_count"] == 1
    invalid = _row(manifest)
    invalid["source_bindings"]["transport_replay_validated"] = False
    observed = build_communication_source_matrix(manifest, [invalid])
    assert observed["status"] == "raw_only_incomplete"
    assert observed["observed_row_count"] == 1
    assert observed["accepted_raw_only_count"] == 0


def test_missing_task_metric_and_proxy_sr_are_diagnostic_not_accepted():
    manifest = _manifest()
    missing = build_communication_source_matrix(
        manifest, [_row(manifest, value=None)])
    assert missing["rows"][0]["row_status"] == "diagnostic_only"
    assert missing["rows"][0]["measurement"]["status"] == "na_task_performance"
    assert missing["accepted_raw_only_count"] == 0

    proxy_manifest = _manifest()
    proxy_manifest["metric_name"] = "SR_comm"
    proxy = build_communication_source_matrix(
        proxy_manifest,
        [_row(proxy_manifest, metric="SR_comm", value=1.0)])
    measurement = proxy["rows"][0]["measurement"]
    assert proxy["rows"][0]["row_status"] == "diagnostic_only"
    assert measurement["status"] == "proxy_only"
    assert measurement["metric_value"] == 1.0
    assert measurement["qualified_metric_value"] is None

    malformed = _row(manifest, value=None)
    malformed["condition_metrics"]["task_performance"]["status"] = "computed"
    rejected = build_communication_source_matrix(manifest, [malformed])
    assert rejected["rows"][0]["row_status"] == "rejected"
    assert "task_performance_metric_missing_from_computed" in (
        rejected["rows"][0]["measurement"]["reasons"])


def test_synthetic_companion_never_backfills_task_metric():
    manifest = _manifest()
    row = _row(manifest, value=None)
    row["condition_metrics"]["transport_companion"]["delivery_rate"] = 1.0
    result = build_communication_source_matrix(manifest, [row])
    measurement = result["rows"][0]["measurement"]
    assert measurement["metric_value"] is None
    assert measurement["qualified_metric_value"] is None
    assert result["synthetic_delivery_is_not_task_performance"] is True

    row = _row(manifest, metric="synthetic_link_delivery_diagnostic")
    rejected = build_communication_source_matrix(manifest, [row])
    assert rejected["rows"][0]["measurement"]["status"] == "invalid_metric"


def test_metric_range_and_nested_release_flags_fail_closed():
    manifest = _manifest()
    row = _row(manifest)
    row["condition_metrics"]["task_performance"]["MCR_comm"] = 1.5
    row["condition_metrics"]["scoreable"] = True
    result = build_communication_source_matrix(manifest, [row])
    reasons = result["rows"][0]["measurement"]["reasons"]
    assert "task_performance_metric_out_of_range" in reasons
    assert "condition_metrics_scoreable_must_be_false" in reasons
    assert result["rows"][0]["row_status"] == "rejected"


def test_legitimate_zero_cost_differs_from_missing_transport_report():
    manifest = _manifest()
    zero = build_communication_source_matrix(
        manifest, [_row(manifest, offer_bits=0)])
    assert zero["accepted_raw_only_count"] == 1
    assert zero["rows"][0]["measurement"]["B_offer_bits"] == 0

    missing = build_communication_source_matrix(
        manifest, [_row(manifest, transport_status="na_missing_communication_report")])
    assert missing["accepted_raw_only_count"] == 0
    reasons = missing["rows"][0]["measurement"]["reasons"]
    assert "transport_companion_not_computed" in reasons
    assert "communication_cost_not_computed" in reasons
    assert "condition_metrics_status_not_computed" in reasons


def test_capacity_manifest_rejects_legacy_or_mixed_units():
    legacy = _cell("capacity", "cap")
    legacy["capacity_method_id"] = "capacity_backhaul_uplink_v1"
    with pytest.raises(ValueError, match="must be v2"):
        build_communication_source_matrix(_manifest([legacy]), [])
    mixed = _cell("capacity", "cap")
    mixed["capacity_unit"] = "bytes_per_second"
    with pytest.raises(ValueError, match="bits_per_second"):
        build_communication_source_matrix(_manifest([mixed]), [])
    nonpositive = _cell("capacity", "cap")
    nonpositive.update({"capacity_mode": "finite", "capacity_bits_per_s": 0})
    with pytest.raises(ValueError, match="positive"):
        build_communication_source_matrix(_manifest([nonpositive]), [])


def test_invalid_manifest_pairing_schema_and_version_fail_closed():
    jitter_only = _manifest([
        _cell("jitter", "j", comparison_level="L")])
    with pytest.raises(ValueError, match="lacks paired fixed_delay"):
        build_communication_source_matrix(jitter_only, [])
    bad_schema = _manifest()
    bad_schema["schema"] = "unknown"
    with pytest.raises(ValueError, match="schema"):
        build_communication_source_matrix(bad_schema, [])
    bad_version = _manifest()
    bad_version["version"] = 2
    with pytest.raises(ValueError, match="version"):
        build_communication_source_matrix(bad_version, [])
    bad_metric = _manifest()
    bad_metric["metric_name"] = "synthetic_link_delivery_diagnostic"
    with pytest.raises(ValueError, match="metric_name"):
        build_communication_source_matrix(bad_metric, [])
    forged_release = _manifest()
    forged_release["release"] = True
    with pytest.raises(ValueError, match="release must be false"):
        build_communication_source_matrix(forged_release, [])


def test_source_matrix_never_flips_dimensions_or_release_flags():
    manifest = _manifest()
    source = build_communication_source_matrix(manifest, [_row(manifest)])
    protocol = build_protocol_metrics_report([])
    for dimension in ("communication_adaptation", "dynamic_reconfiguration",
                      "cross_episode_learning"):
        assert (protocol["dimensions"][dimension]["status"]
                == NA_DIMENSION_SOURCE_NOT_WIRED)
    for flag in ("release", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        assert source[flag] is False
