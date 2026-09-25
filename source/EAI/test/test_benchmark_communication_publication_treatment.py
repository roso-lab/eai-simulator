"""Single-publication treatment checks for the real shared communication pool."""

from copy import deepcopy
import json
import math

import pytest

from demo.benchmark.communication import (
    CommunicationRuntime,
    validate_communication_report,
)


def _treatment(**changes):
    row = {
        "schema": "eai.benchmark.communication_publication_treatment",
        "version": 1,
        "semantics": "shared_fifo_anchored_window_v1",
        "binding_sha256": "a" * 64,
        "event_id": "cross_mobility_observation_delivery",
        "trigger": "first_cross_mobility_observation_before_first_business_commit",
        "operation": "deliver",
        "delay_s": 0.0,
        "drop": False,
        "bytes_per_window": 8192,
        "window_s": 1.0,
        "publication_validity_s": 30.0,
    }
    row.update(changes)
    return row


def _runtime(*, capacity=None, access_capacity=None, pool_policy=None):
    policy = dict(pool_policy or {})
    if capacity is not None:
        policy["capacity_bps"] = capacity
    contract = {"pools": {"backhaul_uplink": policy}}
    if access_capacity is not None:
        contract["access"] = {"robot": {"capacity_bps": access_capacity}}
    return CommunicationRuntime(contract)


def _publish(runtime, message_id, *, treatment=None, time=None, expiry=100.0,
             estimate=None, recipients=None):
    return runtime.publish(
        message_id=message_id,
        producer_id="robot",
        recipients=list(recipients or ["trusted_execution_host"]),
        channel="goal_execution_result",
        estimate=estimate or {"value": message_id},
        valid_until_s=expiry,
        published_at_s=time,
        pool="backhaul_uplink",
        direction="uplink",
        publication_treatment=treatment,
    )


def _delivery(runtime, message_id):
    return runtime.report()["messages"][message_id]["recipients"][
        "trusted_execution_host"]


def _long_treated_runtime():
    runtime = _runtime(capacity=4.0)
    _publish(runtime, "target", treatment=_treatment(
        bytes_per_window=1000, window_s=100.0,
        publication_validity_s=100.0), estimate={"value": "x" * 100})
    return runtime


@pytest.mark.parametrize("mutate", [
    lambda row: row.pop("trigger"),
    lambda row: row.__setitem__("unknown", 1),
    lambda row: row.__setitem__("schema", "other"),
    lambda row: row.__setitem__("version", True),
    lambda row: row.__setitem__("semantics", "continuous_rate"),
    lambda row: row.__setitem__("binding_sha256", "A" * 64),
    lambda row: row.__setitem__("event_id", ""),
    lambda row: row.__setitem__("operation", "drop"),
    lambda row: row.__setitem__("drop", 1),
    lambda row: row.__setitem__("delay_s", -1.0),
    lambda row: row.__setitem__("bytes_per_window", True),
    lambda row: row.__setitem__("window_s", 0.0),
    lambda row: row.__setitem__("publication_validity_s", -1.0),
])
def test_invalid_treatment_is_rejected_before_time_or_identifier_mutation(mutate):
    runtime = _runtime(capacity=100.0)
    before = runtime.report()
    treatment = _treatment()
    mutate(treatment)
    with pytest.raises(ValueError):
        _publish(runtime, "target", treatment=treatment, time=5.0)
    assert runtime.report() == before
    # The rejected identifier remains available.
    _publish(runtime, "target", time=0.0)


def test_treatment_requires_shared_pool_and_exactly_one_recipient_without_mutation():
    no_pool = CommunicationRuntime()
    before = no_pool.report()
    with pytest.raises(ValueError, match="shared-pool"):
        no_pool.publish(
            message_id="target", producer_id="robot", recipients=["host"],
            channel="result", estimate={"value": 1}, valid_until_s=10.0,
            published_at_s=3.0, publication_treatment=_treatment())
    assert no_pool.report() == before

    runtime = _runtime(capacity=100.0)
    before = runtime.report()
    with pytest.raises(ValueError, match="exactly one recipient"):
        _publish(runtime, "target", treatment=_treatment(), time=3.0,
                 recipients=["trusted_execution_host", "other"])
    assert runtime.report() == before


def test_none_preserves_the_untreated_v2_report_byte_for_byte():
    omitted = _runtime(capacity=100.0)
    explicit = _runtime(capacity=100.0)
    _publish(omitted, "ordinary")
    _publish(explicit, "ordinary", treatment=None)
    omitted.advance(5.0)
    explicit.advance(5.0)
    assert omitted.report()["version"] == 2
    assert json.dumps(omitted.report(), sort_keys=True) == json.dumps(
        explicit.report(), sort_keys=True)


def test_target_delay_is_isolated_while_real_shared_serialization_blocks_sibling():
    runtime = _runtime(capacity=1000.0)
    target = _publish(
        runtime, "target", treatment=_treatment(delay_s=5.0),
        estimate={"value": "x" * 40})
    sibling = _publish(runtime, "sibling", estimate={"value": "s"})
    target_row = _delivery(runtime, "target")
    sibling_row = _delivery(runtime, "sibling")
    assert sibling_row["scheduled_transmit_at_s"] == pytest.approx(
        target_row["scheduled_finish_at_s"])
    assert sibling_row["scheduled_finish_at_s"] > sibling_row[
        "scheduled_transmit_at_s"]

    runtime.advance(10.0)
    target_row = _delivery(runtime, "target")
    sibling_row = _delivery(runtime, "sibling")
    assert sibling_row["delivered_at_s"] < target_row["delivered_at_s"]
    assert target_row["delivered_at_s"] == pytest.approx(
        target_row["scheduled_finish_at_s"] + 5.0)
    assert runtime.report()["initial_contract"] == runtime.report()["contract"]
    assert not any(row["event"] == "channel_condition_changed"
                   for row in runtime.report()["ledger"])
    assert target["size_bytes"] + sibling["size_bytes"] == runtime.report()[
        "pool_utilization"]["backhaul_uplink"]["bytes_served"]


def test_selective_drop_reserves_no_capacity_or_bytes_for_sibling():
    runtime = _runtime(capacity=100.0)
    dropped = _treatment(operation="drop", drop=True)
    target = _publish(runtime, "target", treatment=dropped)
    sibling = _publish(runtime, "sibling")
    assert _delivery(runtime, "target")["status"] == "dropped"
    assert _delivery(runtime, "target")["bytes_sent"] == 0
    assert _delivery(runtime, "sibling")["scheduled_transmit_at_s"] == 0.0
    runtime.advance(10.0)
    assert _delivery(runtime, "sibling")["status"] == "delivered"
    assert runtime.report()["pool_utilization"]["backhaul_uplink"][
        "bytes_served"] == sibling["size_bytes"]
    assert target["size_bytes"] > 0


def test_treatment_validity_and_delay_override_pool_ttl_and_delay_for_target_only():
    runtime = _runtime(pool_policy={"fixed_delay_s": 2.0, "ttl_s": 20.0})
    _publish(runtime, "target", treatment=_treatment(
        delay_s=5.0, publication_validity_s=1.0))
    sibling = _publish(runtime, "sibling")
    runtime.advance(3.0)
    target = _delivery(runtime, "target")
    sibling = _delivery(runtime, "sibling")
    assert target["status"] == "expired"
    assert target["transport_valid_until_s"] == 1.0
    assert sibling["status"] == "delivered"
    assert sibling["delivered_at_s"] == 2.0


def test_zero_publication_validity_is_an_immediate_expiry_without_reservation():
    runtime = _runtime(capacity=10.0)
    _publish(runtime, "target", treatment=_treatment(publication_validity_s=0.0))
    target = _delivery(runtime, "target")
    assert target["status"] == "expired"
    assert target["bytes_sent"] == 0
    assert target["transport_valid_until_s"] == 0.0
    _publish(runtime, "sibling")
    assert _delivery(runtime, "sibling")["scheduled_transmit_at_s"] == 0.0


def test_unlimited_pool_uses_zero_anchored_windows_without_carry_over():
    runtime = _runtime()
    published = _publish(
        runtime, "target", time=10.75,
        treatment=_treatment(bytes_per_window=64),
        estimate={"value": "x" * 300})
    row = _delivery(runtime, "target")
    segments = row["serialization_segments"]
    assert published["size_bytes"] > 3 * 64
    assert segments[0] == {
        "window_index": 10, "starts_at_s": 10.75, "ends_at_s": 10.75,
        "bytes": 64, "bytes_sent": 64,
    }
    assert segments[1]["starts_at_s"] == 11.0
    assert [segment["window_index"] for segment in segments] == list(
        range(10, 10 + len(segments)))
    assert all(segment["bytes"] <= 64 for segment in segments)
    assert row["scheduled_finish_at_s"] == segments[-1]["ends_at_s"]
    # Ten idle windows supplied no historical credit to the first partial window.
    assert segments[0]["bytes"] == 64


def test_window_quota_intersects_the_slowest_real_pool_or_access_rate():
    runtime = _runtime(capacity=100.0, access_capacity=16.0)
    _publish(
        runtime, "target", time=0.25,
        treatment=_treatment(bytes_per_window=64),
        estimate={"value": "x" * 100})
    segments = _delivery(runtime, "target")["serialization_segments"]
    assert segments[0]["window_index"] == 0
    assert segments[0]["bytes"] == 12
    assert segments[0]["ends_at_s"] == 1.0
    assert all(segment["bytes"] <= 16 for segment in segments[1:])
    assert all(segment["bytes"] <= 64 for segment in segments)


def test_expiry_mid_byte_counts_whole_partial_service_and_releases_sibling_at_expiry():
    runtime = _runtime(capacity=3.0)
    _publish(runtime, "target", treatment=_treatment(
        bytes_per_window=64, publication_validity_s=0.5),
        estimate={"value": "x" * 100})
    sibling = _publish(runtime, "sibling")
    target = _delivery(runtime, "target")
    assert target["scheduled_finish_at_s"] == 0.5
    assert target["serialization_complete"] is False
    assert target["serialization_segments"] == [{
        "window_index": 0, "starts_at_s": 0.0,
        "ends_at_s": pytest.approx(1.0 / 3.0),
        "bytes": 1, "bytes_sent": 1,
    }]
    assert _delivery(runtime, "sibling")["scheduled_transmit_at_s"] == 0.5
    runtime.advance(0.5)
    assert _delivery(runtime, "target")["status"] == "expired"
    assert _delivery(runtime, "target")["bytes_sent"] == 1
    served = [row for row in runtime.report()["ledger"]
              if row["event"] == "bytes_served"
              and row["message_id"] == "target"]
    assert sum(row["bytes"] for row in served) == 1
    assert runtime.report()["pool_utilization"]["backhaul_uplink"][
        "bytes_served"] == 1 + sibling["size_bytes"]


def test_expiry_before_start_adds_no_reservation_and_preserves_older_finish():
    runtime = _runtime(capacity=10.0)
    _publish(runtime, "older", estimate={"value": "o" * 100})
    older_finish = _delivery(runtime, "older")["scheduled_finish_at_s"]
    assert older_finish > 1.0
    _publish(runtime, "target", treatment=_treatment(
        publication_validity_s=1.0))
    _publish(runtime, "later")
    target = _delivery(runtime, "target")
    assert target["scheduled_transmit_at_s"] is None
    assert target["scheduled_finish_at_s"] is None
    assert target["serialization_segments"] == []
    assert _delivery(runtime, "older")["scheduled_finish_at_s"] == older_finish
    assert _delivery(runtime, "later")["scheduled_transmit_at_s"] == older_finish
    runtime.advance(1.0)
    assert _delivery(runtime, "target")["status"] == "expired"
    assert _delivery(runtime, "target")["bytes_sent"] == 0


def test_queued_target_without_service_opportunity_holds_buffer_until_expiry():
    runtime = _runtime(capacity=10.0, pool_policy={"buffer_bytes": 512})
    _publish(runtime, "older", estimate={"value": "o" * 100})
    older_finish = _delivery(runtime, "older")["scheduled_finish_at_s"]
    _publish(runtime, "target", treatment=_treatment(publication_validity_s=1.0),
             estimate={"value": "t" * 250})
    target = _delivery(runtime, "target")
    assert target["status"] == "queued" and target["scheduled_transmit_at_s"] is None
    _publish(runtime, "overflow", estimate={"value": "s" * 250})
    assert _delivery(runtime, "overflow")["drop_reason"] == "buffer_overflow"
    runtime.advance(1.0)
    assert _delivery(runtime, "target")["status"] == "expired"
    assert _delivery(runtime, "target")["bytes_sent"] == 0
    _publish(runtime, "after-expiry", estimate={"value": "s" * 250})
    assert _delivery(runtime, "after-expiry")["status"] == "queued"
    assert _delivery(runtime, "after-expiry")["scheduled_transmit_at_s"] == older_finish
    assert validate_communication_report(runtime.report())["validated"]


def test_stale_legacy_service_event_cannot_steal_future_treated_head():
    runtime = _runtime(capacity=10.0)
    _publish(runtime, "blocker", estimate={"value": "b" * 100})
    blocker_finish = _delivery(runtime, "blocker")["scheduled_finish_at_s"]
    _publish(runtime, "expired_ordinary", expiry=0.5,
             estimate={"value": "e" * 100})
    stale_start = _delivery(runtime, "expired_ordinary")[
        "scheduled_transmit_at_s"]
    assert stale_start == blocker_finish
    expired_finish = _delivery(runtime, "expired_ordinary")[
        "scheduled_finish_at_s"]
    _publish(runtime, "target", treatment=_treatment(
        bytes_per_window=64, publication_validity_s=100.0),
        estimate={"value": "t" * 100})
    target_start = _delivery(runtime, "target")["scheduled_transmit_at_s"]
    assert target_start == expired_finish > stale_start

    runtime.advance(stale_start)
    target = _delivery(runtime, "target")
    assert target["status"] == "queued"
    assert target["bytes_sent"] == 0
    runtime.advance(target_start)
    assert _delivery(runtime, "target")["status"] == "serializing"


def test_untreated_v2_retains_frozen_expired_head_service_trace():
    runtime = _runtime(capacity=10.0, pool_policy={"buffer_bytes": 4096})
    _publish(runtime, "blocker", estimate={"value": "b" * 100})
    _publish(runtime, "expired_ordinary", expiry=0.5,
             estimate={"value": "e" * 100})
    stale_start = _delivery(runtime, "expired_ordinary")[
        "scheduled_transmit_at_s"]
    expired_finish = _delivery(runtime, "expired_ordinary")[
        "scheduled_finish_at_s"]
    later = _publish(runtime, "later")
    assert _delivery(runtime, "later")["scheduled_transmit_at_s"] == expired_finish
    assert expired_finish > stale_start

    runtime.advance(stale_start)
    later_row = _delivery(runtime, "later")
    assert runtime.report()["version"] == 2
    # Frozen v2 behavior: the stale event skips the expired head and services
    # the next ordinary copy immediately, while retaining its planned finish.
    assert later_row["status"] == "in_transit"
    assert later_row["transmitted_at_s"] == stale_start
    assert later_row["bytes_sent"] == later["size_bytes"]
    assert later_row["scheduled_finish_at_s"] == expired_finish + (
        later["size_bytes"] / 10.0)
    assert validate_communication_report(runtime.report())["validated"]


def _partitioned_report(steps):
    runtime = _runtime(capacity=7.0)
    _publish(runtime, "target", treatment=_treatment(
        bytes_per_window=5, publication_validity_s=4.5),
        estimate={"value": "x" * 100})
    _publish(runtime, "sibling")
    for step in steps:
        runtime.advance(step)
    return runtime.report()


def test_tick_partitioning_does_not_change_integer_bytes_timing_or_report():
    single = _partitioned_report([10.0])
    partitioned = _partitioned_report([0.1, 0.9, 1.0, 2.75, 4.5, 10.0])
    assert json.dumps(single, sort_keys=True) == json.dumps(
        partitioned, sort_keys=True)


def test_v4_replay_recomputes_treatment_and_rejects_all_archived_tampering():
    runtime = _runtime(capacity=100.0)
    _publish(runtime, "target", treatment=_treatment(
        bytes_per_window=64, delay_s=1.0),
        estimate={"value": "x" * 100})
    runtime.advance(10.0)
    report = runtime.report()
    assert report["version"] == 4
    assert validate_communication_report(report)["validated"]
    assert validate_communication_report(
        json.loads(json.dumps(report)),
        expected_initial_contract=report["initial_contract"])["validated"]

    altered = deepcopy(report)
    published = next(row for row in altered["ledger"]
                     if row["event"] == "published")
    published["publication_treatment"]["delay_s"] = 9.0
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["messages"]["target"]["publication_treatment"][
        "binding_sha256"] = "b" * 64
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["messages"]["target"]["recipients"][
        "trusted_execution_host"]["serialization_segments"][0]["bytes"] += 1
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["version"] = 2
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["version"] = 1
    altered.pop("initial_contract")
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    next(row for row in altered["ledger"] if row["event"] == "published").pop(
        "publication_treatment")
    assert not validate_communication_report(altered)["validated"]


def test_v3_partial_archive_retains_segment_event_only_replay_semantics():
    runtime = _long_treated_runtime()
    runtime.advance(1.25)
    archived = runtime.report()
    assert archived["version"] == 4

    snapshot = archived["ledger"].pop()
    assert snapshot["progress_snapshot"] is True
    archived["version"] = 3
    archived.pop("byte_accounting")
    archived["messages"]["target"]["recipients"][
        "trusted_execution_host"]["bytes_sent"] = 0
    archived["pool_utilization"]["backhaul_uplink"]["bytes_served"] = 0

    assert validate_communication_report(archived)["validated"]


def test_mid_segment_progress_is_exact_repeatable_and_cost_visible():
    runtime = _long_treated_runtime()
    runtime.advance(1.25)

    first = runtime.report()
    second = runtime.report()
    assert first == second
    assert first["version"] == 4
    delivery = first["messages"]["target"]["recipients"][
        "trusted_execution_host"]
    assert delivery["status"] == "serializing"
    assert delivery["bytes_sent"] == 5
    snapshots = [row for row in first["ledger"]
                 if row.get("progress_snapshot") is True]
    assert len(snapshots) == 1
    assert snapshots[0]["event"] == "bytes_served"
    assert snapshots[0]["bytes"] == snapshots[0]["bytes_sent"] == 5
    assert first["pool_utilization"]["backhaul_uplink"]["bytes_served"] == 5
    assert runtime.pool_utilization()["backhaul_uplink"]["bytes_served"] == 5
    assert validate_communication_report(first)["validated"]


def test_v4_mixed_runtime_preserves_ordinary_service_start_accounting():
    runtime = _runtime(capacity=4.0)
    _publish(runtime, "treated-drop", treatment=_treatment(
        operation="drop", drop=True))
    ordinary = _publish(
        runtime, "ordinary", estimate={"value": "x" * 100})

    runtime.advance(1.25)
    report = runtime.report()
    delivery = report["messages"]["ordinary"]["recipients"][
        "trusted_execution_host"]
    served = [row for row in report["ledger"]
              if row["event"] == "bytes_served"
              and row["message_id"] == "ordinary"]

    assert report["version"] == 4
    assert report["byte_accounting"] == {
        "schema": "eai.benchmark.communication_byte_accounting",
        "version": 1,
        "treated_copy_semantics": "completed_whole_bytes_at_report_horizon",
        "ordinary_pool_copy_semantics": "whole_copy_charged_at_service_start",
        "total_physical_progress": False,
    }
    assert ordinary["size_bytes"] == 214
    assert 4.0 * report["time_s"] == 5.0
    assert delivery["status"] == "in_transit"
    assert report["time_s"] < delivery["scheduled_finish_at_s"]
    assert delivery["bytes_sent"] == ordinary["size_bytes"]
    assert sum(row["bytes"] for row in served) == ordinary["size_bytes"]
    assert all("progress_snapshot" not in row for row in served)
    assert report["pool_utilization"]["backhaul_uplink"][
        "bytes_served"] == ordinary["size_bytes"]
    assert validate_communication_report(report)["validated"]


def test_report_sampling_does_not_change_later_delivery_or_double_bill():
    coarse = _long_treated_runtime()
    fine = _long_treated_runtime()

    coarse.advance(60.0)
    for horizon in (0.1, 0.25, 1.25, 10.0, 25.5, 52.99, 60.0):
        fine.advance(horizon)
        fine.report()
        fine.pool_utilization()

    coarse_report = coarse.report()
    fine_report = fine.report()
    assert json.dumps(coarse_report, sort_keys=True) == json.dumps(
        fine_report, sort_keys=True)
    served = [row for row in fine_report["ledger"]
              if row["event"] == "bytes_served"
              and row["message_id"] == "target"]
    assert all("progress_snapshot" not in row for row in served)
    assert len(served) == 1
    assert sum(row["bytes"] for row in served) == fine_report[
        "messages"]["target"]["size_bytes"]


def test_partial_progress_obeys_exact_whole_byte_boundaries():
    runtime = _long_treated_runtime()
    runtime.advance(math.nextafter(0.25, 0.0))
    assert _delivery(runtime, "target")["bytes_sent"] == 0
    runtime.advance(0.25)
    assert _delivery(runtime, "target")["bytes_sent"] == 1
    runtime.advance(math.nextafter(0.25, 1.0))
    assert _delivery(runtime, "target")["bytes_sent"] == 1


def test_partial_progress_preserves_half_open_expiry_boundary():
    runtime = _runtime(capacity=4.0)
    _publish(runtime, "target", treatment=_treatment(
        bytes_per_window=1000, window_s=100.0,
        publication_validity_s=0.5), estimate={"value": "x" * 100})

    runtime.advance(0.25)
    assert _delivery(runtime, "target")["bytes_sent"] == 1
    runtime.advance(math.nextafter(0.5, 0.0))
    before = runtime.report()
    assert before["messages"]["target"]["recipients"][
        "trusted_execution_host"]["bytes_sent"] == 1
    runtime.advance(0.5)
    at_expiry = runtime.report()
    delivery = at_expiry["messages"]["target"]["recipients"][
        "trusted_execution_host"]
    assert delivery["status"] == "expired"
    assert delivery["bytes_sent"] == 1
    assert at_expiry["pool_utilization"]["backhaul_uplink"]["bytes_served"] == 1
    runtime.advance(math.nextafter(0.5, 1.0))
    after_expiry = runtime.report()
    assert after_expiry["messages"]["target"]["recipients"][
        "trusted_execution_host"]["bytes_sent"] == 1
    assert after_expiry["pool_utilization"]["backhaul_uplink"][
        "bytes_served"] == 1


def test_v4_partial_snapshot_tampering_is_rejected_by_strict_replay():
    runtime = _long_treated_runtime()
    runtime.advance(1.25)
    report = runtime.report()
    assert validate_communication_report(report)["validated"]

    altered = deepcopy(report)
    next(row for row in altered["ledger"]
         if row.get("progress_snapshot"))["bytes"] += 1
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["messages"]["target"]["recipients"][
        "trusted_execution_host"]["bytes_sent"] += 1
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["pool_utilization"]["backhaul_uplink"]["bytes_served"] += 1
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["ledger"] = [row for row in altered["ledger"]
                         if not row.get("progress_snapshot")]
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered["byte_accounting"]["total_physical_progress"] = True
    assert not validate_communication_report(altered)["validated"]

    altered = deepcopy(report)
    altered.pop("byte_accounting")
    assert not validate_communication_report(altered)["validated"]


def test_participant_view_never_exposes_treatment_or_future_schedule():
    runtime = _runtime()
    _publish(runtime, "target", treatment=_treatment())
    payload = runtime.public_messages(recipient="trusted_execution_host")[0]
    assert set(payload) == {
        "message_id", "producer_id", "published_at_s", "valid_until_s",
        "estimate",
    }
    assert "publication_treatment" not in json.dumps(payload)
    assert "scheduled" not in json.dumps(payload)
