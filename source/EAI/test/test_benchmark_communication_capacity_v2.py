"""Opt-in capacity-v2 unit, reference, and identity checks."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.communication_analysis import (
    CAPACITY_V2_ANALYSIS_SCHEMA,
    summarize_capacity_curve_v2,
)
from demo.benchmark.communication_capacity_v2 import (
    CAPACITY_V2_REPORT_SCHEMA,
    bits_per_second_to_runtime_bytes,
    run_capacity_cell_v2,
    summarize_capacity_experiments_v2,
)


def cells(*, c0=16000.0):
    common = {"pool": "backhaul_uplink", "base_instance": "base-a",
              "seed": 7, "count": 4, "interval_s": 0.25, "ttl_s": 2.0,
              "c0_bits_per_s": c0}
    return [
        run_capacity_cell_v2(capacity_bits_per_s=None, **common),
        run_capacity_cell_v2(capacity_bits_per_s=8000.0, **common),
    ]


def test_v2_converts_bits_to_runtime_bytes_exactly_once():
    assert bits_per_second_to_runtime_bytes(8000) == 1000.0
    row = cells()[1]
    assert row["capacity_bits_per_s"] == 8000.0
    assert row["runtime_capacity_bytes_per_s"] == 1000.0
    assert row["capacity_unit"] == "bits_per_second"
    assert row["runtime_capacity_unit"] == "bytes_per_second"


def test_v2_serialization_uses_actual_wire_bytes_and_bit_rate():
    row = run_capacity_cell_v2(
        pool="backhaul_uplink", capacity_bits_per_s=1_000_000.0,
        base_instance="wire", seed=1, count=1)
    assert row["serialization_duration_s"] == pytest.approx(
        8 * row["wire_size_bytes"] / 1_000_000.0, abs=1e-12)


def test_legacy_runtime_capacity_field_remains_bytes_per_second():
    runtime = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"capacity_bps": 1_000_000.0, "ttl_s": 10.0}}})
    message = runtime.publish(
        message_id="legacy", producer_id="robot", recipients=["edge"],
        channel="observation", estimate={"value": 1}, valid_until_s=10.0,
        pool="backhaul_uplink", direction="uplink")
    delivery = message["recipients"]["edge"]
    duration = (delivery["scheduled_finish_at_s"]
                - delivery["scheduled_transmit_at_s"])
    assert duration == pytest.approx(message["size_bytes"] / 1_000_000.0, abs=1e-12)


def test_v2_reference_is_actual_runtime_unlimited():
    row = cells()[0]
    assert row["capacity_mode"] == "unlimited"
    assert row["capacity_bits_per_s"] is None
    assert row["runtime_capacity_bytes_per_s"] is None
    assert row["serialization_duration_s"] == 0.0
    assert row["metric_value"] == 1.0


def test_v2_expired_low_capacity_still_reports_scheduled_serialization():
    row = run_capacity_cell_v2(
        pool="backhaul_uplink", capacity_bits_per_s=8.0,
        base_instance="expired", seed=1, count=1, ttl_s=0.25)
    assert row["delivered_count"] == 0
    assert row["serialization_duration_s"] == pytest.approx(
        row["wire_size_bytes"], abs=1e-12)
    assert row["serialization_duration_s"] > 0.25


def test_v2_analysis_uses_separate_schema_and_explicit_bit_units():
    report = summarize_capacity_curve_v2(cells(), bootstrap_samples=10)
    assert report["schema"] == CAPACITY_V2_ANALYSIS_SCHEMA
    assert report["version"] == 2
    assert report["capacity_unit"] == "bits_per_second"
    assert report["reference"]["capacity_bits_per_s"] is None
    assert report["points"][0]["capacity_bits_per_s"] == 8000.0
    assert report["points"][0]["capacity_ratio_c0"] == 0.5
    assert report["not_system_business_performance"] is True


def test_v2_analysis_requires_an_actual_unlimited_reference():
    report = summarize_capacity_curve_v2(cells()[1:])
    assert report["status"] == "missing_reference"
    assert "reference" not in report


@pytest.mark.parametrize("field,value", [
    ("capacity_bps", 1000.0),
    ("capacity_bytes_per_s", 1000.0),
    ("c0_bps", 2000.0),
    ("c0_bytes_per_s", 2000.0),
])
def test_v2_analysis_rejects_legacy_or_mixed_unit_fields(field, value):
    rows = cells()
    rows[0][field] = value
    with pytest.raises(ValueError, match="legacy or byte-unit fields"):
        summarize_capacity_curve_v2(rows)


def test_v2_analysis_rejects_partial_or_drifting_c0():
    rows = cells()
    rows[0]["c0_bits_per_s"] = None
    with pytest.raises(ValueError, match="every v2 row or none"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[0]["c0_bits_per_s"] = 32000.0
    with pytest.raises(ValueError, match="frozen"):
        summarize_capacity_curve_v2(rows)


def test_v2_analysis_rejects_schema_method_and_conversion_drift():
    rows = cells()
    rows[0]["schema"] = "eai.benchmark.communication_capacity_experiments"
    with pytest.raises(ValueError, match="schema/version"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[0]["method_id"] = "capacity_backhaul_uplink_v1"
    with pytest.raises(ValueError, match="different method_id"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[1]["runtime_capacity_bytes_per_s"] = 8000.0
    with pytest.raises(ValueError, match="unique bit/s to byte/s conversion"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[0]["metric"] = "business_success_rate"
    with pytest.raises(ValueError, match="different metric"):
        summarize_capacity_curve_v2(rows)


def test_v2_analysis_requires_strict_integer_version_and_fixed_metric_name():
    rows = cells()
    rows[0]["version"] = 2.0
    with pytest.raises(ValueError, match="schema/version"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    for row in rows:
        row["metric"] = "business_success_rate"
    with pytest.raises(ValueError, match="metric is fixed"):
        summarize_capacity_curve_v2(rows, metric="business_success_rate")


def test_v2_analysis_rejects_direction_and_serialization_evidence_drift():
    rows = cells()
    rows[0]["direction"] = "p2p"
    with pytest.raises(ValueError, match="direction conflicts"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[1]["wire_size_bytes"] = 0
    with pytest.raises(ValueError, match="positive integer"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[1]["serialization_duration_s"] = float("nan")
    with pytest.raises(ValueError, match="finite and nonnegative"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[1]["serialization_duration_s"] += 0.01
    with pytest.raises(ValueError, match="differs from wire size"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[0]["serialization_duration_s"] = 0.01
    with pytest.raises(ValueError, match="zero serialization"):
        summarize_capacity_curve_v2(rows)


def test_v2_analysis_rejects_unknown_fields_or_metric_count_drift():
    rows = cells()
    rows[0]["capacity_mbps"] = 1.0
    with pytest.raises(ValueError, match="incomplete or unknown"):
        summarize_capacity_curve_v2(rows)
    rows = cells()
    rows[0]["delivered_count"] -= 1
    with pytest.raises(ValueError, match="differs from its counts"):
        summarize_capacity_curve_v2(rows)


def test_v2_report_labels_diagnostic_scope_without_capability_claim():
    rows = cells(c0=None)
    report = summarize_capacity_experiments_v2(
        pools=("backhaul_uplink",),
        capacity_rows_by_pool={"backhaul_uplink": rows},
        thresholds=(.5,), seed=3)
    assert report["schema"] == CAPACITY_V2_REPORT_SCHEMA
    assert report["reference"] == "actual_unlimited_runtime_capacity_none"
    assert report["legacy_probe_labels"]["iid_loss"].endswith(
        "not_bernoulli_iid")
    assert "not a system business score" in report["metric_interpretation"]
    assert report["workload_design"] == "deterministic_fixed_message_sequence"
    assert "not independent stochastic evidence" in report["seed_interpretation"]
    assert report["diagnostic_identity"] == "transport_diagnostic"
    assert report["diagnostic_identity_is_task_bundle_scene_key"] is False
    assert report["development_track"]["scene_key"] == "transport_diagnostic"
    assert report["formal_statistics"] is False
    assert report["execution_validated"] is False
    assert report["business_outcomes_validated"] is False
    assert report["scoreable"] is False


def test_v2_report_rejects_cross_pool_rows():
    rows = cells(c0=None)
    bad = deepcopy(rows)
    bad[0]["pool"] = "p2p"
    with pytest.raises(ValueError, match="mix pool identities"):
        summarize_capacity_experiments_v2(
            pools=("backhaul_uplink",),
            capacity_rows_by_pool={"backhaul_uplink": bad})


def test_v2_direction_is_derived_from_pool_and_conflicts_are_rejected():
    p2p = run_capacity_cell_v2(
        pool="p2p", capacity_bits_per_s=None,
        base_instance="direction", seed=1, count=1)
    assert p2p["direction"] == "p2p"
    with pytest.raises(ValueError, match="requires direction 'p2p'"):
        run_capacity_cell_v2(
            pool="p2p", capacity_bits_per_s=None,
            base_instance="direction", seed=1, count=1, direction="uplink")


def test_v2_report_refuses_to_publish_without_unlimited_reference():
    with pytest.raises(ValueError, match="actual unlimited reference"):
        summarize_capacity_experiments_v2(
            pools=("backhaul_uplink",),
            capacity_rows_by_pool={"backhaul_uplink": cells(c0=None)[1:]})
