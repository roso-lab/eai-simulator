"""Offline communication counterexamples; no Isaac, assets or external services."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.communication import (
    CommunicationRuntime, ParticipantCommunicationBoundary,
    validate_communication_contract, validate_communication_report,
)


def publish(runtime, identifier="m", *, recipients=("r",), channel="observations",
            expiry=100., time=None, estimate=None):
    return runtime.publish(message_id=identifier, producer_id="camera", recipients=recipients,
                           channel=channel, published_at_s=time, valid_until_s=expiry,
                           estimate={"label": "箱子"} if estimate is None else estimate)


def recipient_record(runtime, identifier="m", recipient="r"):
    return runtime.report()["messages"][identifier]["recipients"][recipient]


def events(runtime, name):
    return [event for event in runtime.report()["ledger"] if event["event"] == name]


class _ParticipantEvents:
    def __init__(self):
        self.events = []

    def emit(self, event_type, *, time_s, payload):
        self.events.append({"type": event_type, "time_s": time_s,
                            "payload": deepcopy(payload)})


def _observation():
    return {
        "observation_id": "scan:frame:1", "producer": "iris_1",
        "target_ref": "rack_scan_zone", "timestamp_s": 1.0,
        "valid_until_s": 10.0,
        "target_estimate": {"kind": "sensor_pose_only", "position_m": [1., 2., 3.]},
        "quality": {"frame_acquired": True},
    }


def test_participant_boundary_exposes_only_delivered_runtime_observations():
    participant = _ParticipantEvents()
    boundary = ParticipantCommunicationBoundary(
        participant, contract={"delay_s": 2.0})
    boundary.publish_observation(
        _observation(), consumer_ids=["priority_carton_stage"])
    assert boundary.advance(2.9) == []
    assert participant.events == []
    delivered = boundary.advance(3.0)
    assert delivered[0]["estimate"]["consumer_ids"] == ["priority_carton_stage"]
    assert participant.events[0]["type"] == "observation_message"
    boundary.consume(
        "scan:frame:1", consumer_id="priority_carton_stage", time_s=3.0)
    report = boundary.report()
    replay = validate_communication_report(report)
    assert replay["validated"] and replay["isaac_integrated"]
    assert report["publicly_emitted_message_ids"] == ["scan:frame:1"]


def test_participant_boundary_dropped_observation_never_becomes_public():
    participant = _ParticipantEvents()
    boundary = ParticipantCommunicationBoundary(participant, contract={"drop": True})
    boundary.publish_observation(_observation(), consumer_ids=["downstream"])
    assert boundary.advance(10.0) == []
    assert participant.events == []
    with pytest.raises(ValueError, match="dropped"):
        boundary.consume("scan:frame:1", consumer_id="downstream", time_s=10.0)


def test_participant_boundary_allows_each_declared_downstream_consumer_once():
    participant = _ParticipantEvents()
    boundary = ParticipantCommunicationBoundary(participant)
    boundary.publish_observation(
        _observation(), consumer_ids=["left_branch", "right_branch"])
    delivered = boundary.advance(1.0)
    assert len(delivered) == 1
    assert delivered[0]["estimate"]["consumer_ids"] == [
        "left_branch", "right_branch"]
    boundary.consume("scan:frame:1", consumer_id="left_branch", time_s=1.0)
    boundary.consume("scan:frame:1", consumer_id="right_branch", time_s=1.0)
    with pytest.raises(ValueError, match="already_consumed"):
        boundary.consume("scan:frame:1", consumer_id="left_branch", time_s=1.0)
    with pytest.raises(ValueError, match="consumer_not_declared"):
        boundary.consume("scan:frame:1", consumer_id="hidden_branch", time_s=1.0)
    report = boundary.report()
    delivery = report["messages"]["scan:frame:1"]["recipients"]["participant"]
    assert delivery["consumption"]["consumer_id"] == "left_branch"
    assert [item["consumer_id"] for item in delivery["additional_consumptions"]] == [
        "right_branch"]
    assert validate_communication_report(report)["validated"] is True


def test_immediate_delivery_and_per_recipient_consumption_causality():
    runtime = CommunicationRuntime()
    publish(runtime, recipients=("b", "a"))
    for recipient in ("a", "b"):
        assert len(runtime.public_messages(recipient=recipient)) == 1
    runtime.consume("m", recipient="a", consumer_id="plan_a")
    assert runtime.public_messages(recipient="a") == []
    assert len(runtime.public_messages(recipient="b")) == 1
    with pytest.raises(ValueError, match="already_consumed"):
        runtime.consume("m", recipient="a", consumer_id="other_task")
    with pytest.raises(ValueError, match="not_a_recipient"):
        runtime.consume("m", recipient="intruder", consumer_id="plan")
    with pytest.raises(ValueError, match="unknown_message"):
        runtime.consume("forged", recipient="b", consumer_id="plan")
    runtime.consume("m", recipient="b", consumer_id="plan_b")
    assert len(events(runtime, "consumed")) == 2
    assert len(events(runtime, "consume_rejected")) == 3
    ledger = runtime.report()["ledger"]
    for recipient in ("a", "b"):
        delivery = next(e for e in ledger if e["event"] == "delivered" and e["recipient"] == recipient)
        consumption = next(e for e in ledger if e["event"] == "consumed" and e["recipient"] == recipient)
        assert delivery["sequence"] < consumption["sequence"]


def test_drop_is_selective_for_recipient_and_channel_not_global_withholding():
    runtime = CommunicationRuntime({"rules": [{"recipient": "a", "channel": "secret", "drop": True}]})
    publish(runtime, "private", recipients=("a", "b"), channel="secret")
    publish(runtime, "public", recipients=("a", "b"), channel="ordinary")
    assert [m["message_id"] for m in runtime.public_messages(recipient="a")] == ["public"]
    assert len(runtime.public_messages(recipient="b")) == 2
    assert recipient_record(runtime, "private", "a")["status"] == "dropped"
    assert len(events(runtime, "dropped")) == 1
    assert not any(e["message_id"] == "private" and e["recipient"] == "a"
                   for e in events(runtime, "bytes_served"))
    with pytest.raises(ValueError, match="dropped"):
        runtime.consume("private", recipient="a", consumer_id="plan", time_s=50)


def test_selective_delay_visibility_and_no_future_intervention_leak():
    runtime = CommunicationRuntime({"rules": [{"recipient": "r", "delay_s": 2}]})
    publish(runtime, recipients=("r", "other"))
    assert runtime.public_messages(recipient="r") == []
    assert len(runtime.public_messages(recipient="other")) == 1
    with pytest.raises(ValueError, match="in_transit"):
        runtime.consume("m", recipient="r", consumer_id="plan", time_s=1.99)
    runtime.tick(2)
    public = runtime.public_messages(recipient="r")[0]
    assert set(public) == {"message_id", "producer_id", "published_at_s", "valid_until_s", "estimate"}
    assert "conditions" not in json.dumps(public)
    assert "scheduled_delivery" not in json.dumps(public)
    assert runtime.report()["visibility"] == "evaluator_only"
    assert recipient_record(runtime)["delivered_at_s"] == 2


def test_large_message_crosses_windows_instead_of_starving_or_exceeding_budget():
    runtime = CommunicationRuntime({"bytes_per_window": 50, "window_s": .1, "delay_s": .03})
    message = publish(runtime, estimate={"text": "箱" * 90})
    size = message["size_bytes"]
    expected_size = len(json.dumps(message["payload"], sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False).encode("utf-8"))
    assert size == expected_size
    assert size > len(json.dumps(message["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    assert recipient_record(runtime)["bytes_sent"] == 50
    runtime.advance(10)
    transfers = events(runtime, "bytes_served")
    assert sum(e["bytes"] for e in transfers) == size
    assert all(0 < e["bytes"] <= 50 for e in transfers)
    assert [e["window_index"] for e in transfers] == list(range((size + 49) // 50))
    assert recipient_record(runtime)["delivered_at_s"] == pytest.approx(((size - 1) // 50) * .1 + .03)


def test_fifo_uses_leftover_budget_and_same_tick_does_not_refill():
    probe = CommunicationRuntime()
    size = publish(probe, "a")["size_bytes"]
    runtime = CommunicationRuntime({"bytes_per_window": size + 10, "window_s": 1})
    publish(runtime, "a")
    publish(runtime, "b")
    publish(runtime, "c")
    runtime.tick(0)
    runtime.tick(.99)
    assert recipient_record(runtime, "b")["bytes_sent"] == 10
    assert recipient_record(runtime, "c")["bytes_sent"] == 0
    with pytest.raises(ValueError, match="queued"):
        runtime.consume("b", recipient="r", consumer_id="plan")
    runtime.tick(1)
    assert recipient_record(runtime, "b")["status"] == "delivered"
    assert recipient_record(runtime, "c")["bytes_sent"] == 20
    runtime.advance(2)
    assert [e["message_id"] for e in events(runtime, "delivered")] == ["a", "b", "c"]
    for window in (0, 1, 2):
        assert sum(e["bytes"] for e in events(runtime, "bytes_served")
                   if e["window_index"] == window) <= size + 10


def test_links_have_independent_recipient_channel_budgets():
    runtime = CommunicationRuntime({"bytes_per_window": 20})
    publish(runtime, "a", recipients=("r", "other"))
    publish(runtime, "b", channel="different")
    publish(runtime, "c")
    assert recipient_record(runtime, "a", "other")["bytes_sent"] == 20
    assert recipient_record(runtime, "a")["bytes_sent"] == 20
    assert recipient_record(runtime, "b")["bytes_sent"] == 20
    assert recipient_record(runtime, "c")["bytes_sent"] == 0


def test_no_banked_budget_after_idle_and_midwindow_publish():
    runtime = CommunicationRuntime({"bytes_per_window": 20, "window_s": 1})
    publish(runtime, time=50.5)
    assert recipient_record(runtime)["bytes_sent"] == 20
    runtime.advance(50.99)
    assert recipient_record(runtime)["bytes_sent"] == 20
    runtime.advance(51)
    assert recipient_record(runtime)["bytes_sent"] == 40


def test_tick_partition_invariance_including_delivery_expiry_and_service_ledger():
    def execute(ticks):
        runtime = CommunicationRuntime({"bytes_per_window": 40, "window_s": .1, "delay_s": .07})
        publish(runtime, "a", expiry=.35)
        publish(runtime, "b", expiry=3)
        for time in ticks:
            runtime.advance(time)
        return runtime.report()
    assert execute([3]) == execute([i / 100 for i in range(301)])


def test_expiry_cancels_partial_queue_and_frees_next_message_without_refund():
    runtime = CommunicationRuntime({"bytes_per_window": 40})
    publish(runtime, "a", expiry=.5)
    publish(runtime, "b")
    runtime.advance(.5)
    assert recipient_record(runtime, "a")["status"] == "expired"
    assert recipient_record(runtime, "b")["bytes_sent"] == 0
    runtime.advance(1)
    assert recipient_record(runtime, "b")["bytes_sent"] == 40
    assert sum(e["bytes"] for e in events(runtime, "bytes_served") if e["message_id"] == "a") == 40


@pytest.mark.parametrize("delay,expiry", [(1, 1), (2, 1), (0, 0)])
def test_stale_or_expired_in_transit_never_delivered(delay, expiry):
    runtime = CommunicationRuntime({"delay_s": delay})
    publish(runtime, expiry=expiry)
    runtime.advance(3)
    assert not events(runtime, "delivered")
    assert runtime.public_messages(recipient="r") == []
    with pytest.raises(ValueError, match="expired"):
        runtime.consume("m", recipient="r", consumer_id="plan")


def test_delivered_is_not_enough_consumption_requires_current_freshness():
    runtime = CommunicationRuntime()
    publish(runtime, expiry=1)
    assert len(events(runtime, "delivered")) == 1
    with pytest.raises(ValueError, match="expired"):
        runtime.consume("m", recipient="r", consumer_id="late", time_s=1)
    assert not events(runtime, "consumed")


@pytest.mark.parametrize("contract", [
    {"delay_s": float("nan")}, {"delay_s": float("inf")}, {"delay_s": -1},
    {"delay_s": True}, {"window_s": 0}, {"window_s": float("inf")},
    {"bytes_per_window": 0}, {"bytes_per_window": -1}, {"bytes_per_window": 1.5},
    {"bytes_per_window": True}, {"drop": "false"}, {"typo": 1},
    {"rules": [{"recipient": "", "drop": True}]},
    {"rules": [{"channel": "a", "delay_s": -1}]}, {"rules": "a"},
])
def test_invalid_contracts_fail_before_execution(contract):
    with pytest.raises(ValueError):
        CommunicationRuntime(contract)


def test_last_matching_rule_overrides_only_declared_fields():
    runtime = CommunicationRuntime({"delay_s": 5, "rules": [
        {"channel": "observations", "drop": True}, {"recipient": "r", "drop": False, "delay_s": 1}]})
    publish(runtime, recipients=("r", "other"))
    runtime.advance(1)
    assert recipient_record(runtime)["status"] == "delivered"
    assert recipient_record(runtime, recipient="other")["status"] == "dropped"


@pytest.mark.parametrize("time", [-1, float("inf"), float("nan"), True, "1", 10**400])
def test_invalid_clock_values(time):
    runtime = CommunicationRuntime()
    with pytest.raises(ValueError):
        runtime.advance(time)
    assert runtime.time_s == 0


def test_duplicate_ids_backdating_and_bad_json_do_not_mutate_publication_state():
    runtime = CommunicationRuntime()
    publish(runtime, time=2)
    before = runtime.report()
    for kwargs in ({"identifier": "m", "time": 3}, {"identifier": "backdated", "time": 1},
                   {"identifier": "bad", "time": 3, "estimate": {"x": float("nan")}},
                   {"identifier": "dup_recipient", "recipients": ["r", "r"]}):
        with pytest.raises(ValueError):
            publish(runtime, **kwargs)
        assert runtime.report() == before
    with pytest.raises(ValueError, match="monotonic"):
        runtime.tick(1)


def test_returns_and_inputs_are_detached_from_private_state():
    contract = {"rules": [{"recipient": "r", "delay_s": 1}]}
    runtime = CommunicationRuntime(contract)
    estimate = {"objects": ["crate"]}
    published = publish(runtime, estimate=estimate)
    contract["rules"][0]["delay_s"] = 100
    estimate["objects"].clear()
    published["recipients"]["r"]["status"] = "delivered"
    runtime.advance(1)
    view = runtime.public_messages(recipient="r")
    assert view[0]["estimate"] == {"objects": ["crate"]}
    view[0]["estimate"].clear()
    ledger = runtime.report()
    ledger["messages"].clear()
    assert runtime.public_messages(recipient="r")[0]["estimate"] == {"objects": ["crate"]}
    json.dumps(runtime.report(), allow_nan=False)


def test_factory_legacy_api_unchanged_and_explicit_adapter_is_compatible():
    from demo.factory_benchmark.inventory_dispatch import delivered_messages

    legacy = {"message_id": "view:marker:1", "producer": "cf2x_1", "published_at_s": 0.,
              "valid_until_s": 10., "estimate": {"marker_id": 1}}
    original = deepcopy(legacy)
    visible, ledger = delivered_messages([legacy], mode="shared", delay_s=0., time_s=0.)
    assert visible == [legacy] and ledger[0]["status"] == "available"
    runtime = CommunicationRuntime()
    runtime.publish_legacy(legacy, recipients=["m20_1"])
    public = runtime.public_messages(recipient="m20_1")[0]
    assert public == {k: v for k, v in legacy.items() if k != "producer"} | {"producer_id": "cf2x_1"}
    assert legacy == original
    # Existing Factory's inclusive expiry boundary is deliberately preserved.
    assert delivered_messages([legacy], mode="shared", delay_s=0., time_s=10.)[0] == [legacy]
    runtime.advance(10)
    assert runtime.public_messages(recipient="m20_1") == []


def test_large_finite_delay_does_not_overflow_or_become_an_immediate_delivery():
    runtime = CommunicationRuntime({"delay_s": 1e308})
    publish(runtime, time=1e308, expiry=1.1e308)
    runtime.advance(1.1e308)
    assert recipient_record(runtime)["status"] == "expired"
    assert not events(runtime, "delivered")
    json.dumps(runtime.report(), allow_nan=False)


def test_default_contract_is_detached_and_explicit():
    assert validate_communication_contract() == {
        "delay_s": 0., "drop": False, "bytes_per_window": None, "window_s": 1., "rules": []}


def test_evaluator_report_replay_reconstructs_delivery_and_consumption_ledger():
    runtime = CommunicationRuntime({"bytes_per_window": 40, "window_s": .1, "delay_s": .02})
    publish(runtime, "a", recipients=("r", "s"), expiry=3)
    publish(runtime, "b", recipients=("r",), time=.1, expiry=3)
    runtime.advance(2)
    runtime.consume("a", recipient="r", consumer_id="planner", time_s=2)
    report = runtime.report()
    assert validate_communication_report(report) == {
        "schema": "eai.benchmark.communication_replay", "validated": True,
        "mismatches": [], "isaac_integrated": False,
        "published_count": 2, "ledger_event_count": len(report["ledger"]),
    }


@pytest.mark.parametrize("tamper", [
    lambda report: report["ledger"].pop(),
    lambda report: report["ledger"].__setitem__(0, {**report["ledger"][0], "message_id": "forged"}),
    lambda report: report["messages"]["m"]["recipients"]["r"].__setitem__("status", "expired"),
    lambda report: report["messages"]["m"]["recipients"]["r"].__setitem__("bytes_sent", 999999),
])
def test_evaluator_replay_rejects_tampered_or_unproven_report(tamper):
    runtime = CommunicationRuntime({"delay_s": 1})
    publish(runtime)
    runtime.advance(2)
    report = runtime.report()
    tamper(report)
    checked = validate_communication_report(report)
    assert not checked["validated"]
    assert checked["mismatches"]


def test_replay_does_not_mutate_report_and_rejects_unknown_schema():
    runtime = CommunicationRuntime()
    publish(runtime)
    report = runtime.report()
    original = deepcopy(report)
    assert not validate_communication_report({"schema": "other", "version": 1})["validated"]
    checked = validate_communication_report(report)
    assert checked["validated"]
    assert report == original


def test_replay_does_not_trust_a_claimed_validated_flag():
    runtime = CommunicationRuntime()
    publish(runtime)
    report = runtime.report()
    report["validated"] = False
    assert validate_communication_report(report)["validated"]


def test_replay_detects_consume_event_with_no_matching_delivery_causality():
    runtime = CommunicationRuntime({"drop": True})
    publish(runtime)
    report = runtime.report()
    report["ledger"].append({"sequence": len(report["ledger"]), "event": "consumed",
                              "time_s": 0., "message_id": "m", "recipient": "r",
                              "consumer_id": "forged"})
    checked = validate_communication_report(report)
    assert not checked["validated"] and "replay failed" in checked["mismatches"][0]


def test_public_views_do_not_reveal_pending_drops_arrival_times_or_contracts():
    delayed = CommunicationRuntime({"delay_s": 3})
    dropped = CommunicationRuntime({"drop": True})
    limited = CommunicationRuntime({"bytes_per_window": 1})
    for runtime in (delayed, dropped, limited):
        publish(runtime)
        runtime.advance(1)
    assert delayed.public_messages(recipient="r") == dropped.public_messages(recipient="r") == []
    assert limited.public_messages(recipient="r") == []
    assert len({recipient_record(r)["status"] for r in (delayed, dropped, limited)}) == 3


def test_recipient_order_does_not_change_transfer_or_consumption_evidence():
    left = CommunicationRuntime({"bytes_per_window": 50})
    right = CommunicationRuntime({"bytes_per_window": 50})
    publish(left, recipients=("a", "b"))
    publish(right, recipients=("b", "a"))
    left.advance(10)
    right.advance(10)
    assert left.report() == right.report()


def test_consumption_at_later_time_advances_queue_before_causal_check():
    runtime = CommunicationRuntime({"delay_s": 2})
    publish(runtime)
    result = runtime.consume("m", recipient="r", consumer_id="planner", time_s=2)
    assert result["time_s"] == 2
    assert [e["event"] for e in runtime.report()["ledger"]][-2:] == ["delivered", "consumed"]


def test_same_window_late_small_message_cannot_bypass_partial_fifo_head():
    runtime = CommunicationRuntime({"bytes_per_window": 200})
    publish(runtime, "large", estimate={"text": "x" * 800})
    publish(runtime, "small", time=.5, estimate={})
    runtime.advance(1)
    assert recipient_record(runtime, "large")["bytes_sent"] == 400
    assert recipient_record(runtime, "small")["bytes_sent"] == 0
    runtime.advance(10)
    assert [e["message_id"] for e in events(runtime, "delivered")] == ["large", "small"]


def test_current_time_conditions_change_future_publications_and_freeze_in_flight_copies():
    runtime = CommunicationRuntime({"delay_s": 1.0})
    publish(runtime, "old", expiry=10)
    runtime.set_conditions({"drop": True}, recipient="r", channel="observations")
    publish(runtime, "new", time=0.1, expiry=10)
    assert recipient_record(runtime, "old")["status"] == "in_transit"
    assert recipient_record(runtime, "new")["status"] == "dropped"
    runtime.advance(1.0)
    assert recipient_record(runtime, "old")["status"] == "delivered"
    change = events(runtime, "channel_condition_changed")[0]
    assert change["time_s"] == 0.0
    assert change["applies_to"] == "future_publications"
    assert runtime.public_messages(recipient="r") == [runtime.report()["messages"]["old"]["payload"]]
    assert validate_communication_report(runtime.report())["validated"]


def test_bandwidth_change_rejected_with_nonempty_fifo_and_is_replayable():
    runtime = CommunicationRuntime({"bytes_per_window": 1, "window_s": 1.0})
    publish(runtime, "large", estimate={"text": "x" * 100})
    with pytest.raises(ValueError, match="queue is not empty"):
        runtime.set_conditions({"bytes_per_window": 999}, recipient="r", channel="observations")
    rejected = events(runtime, "channel_condition_change_rejected")
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "selected_fifo_queue_not_empty"
    assert validate_communication_report(runtime.report())["validated"]


def test_successful_condition_change_is_replayed_and_public_view_hides_it():
    runtime = CommunicationRuntime()
    runtime.set_conditions({"delay_s": 2.0}, recipient="r", channel="observations")
    publish(runtime, "after", expiry=10)
    report = runtime.report()
    checked = validate_communication_report(report)
    assert checked["validated"]
    assert all(event["event"] != "channel_condition_changed"
               for event in runtime.public_messages(recipient="r"))
    altered = deepcopy(report)
    change = next(event for event in altered["ledger"]
                  if event["event"] == "channel_condition_changed")
    change["conditions"]["delay_s"] = 9.0
    assert not validate_communication_report(altered)["validated"]


@pytest.mark.parametrize("selector", [{}, {"recipient": "r", "channel": "observations"}])
@pytest.mark.parametrize("conditions", [
    {"delay_s": 2.0}, {"drop": True}, {"bytes_per_window": 40, "window_s": .1},
])
def test_replay_preserves_consumption_before_real_condition_change(selector, conditions):
    runtime = CommunicationRuntime()
    publish(runtime, "before", expiry=10)
    runtime.consume("before", recipient="r", consumer_id="early_plan", time_s=.1)
    runtime.set_conditions(conditions, time_s=1., **selector)
    publish(runtime, "after", time=1.1, expiry=10)
    runtime.advance(4.)
    if conditions.get("drop"):
        with pytest.raises(ValueError, match="dropped"):
            runtime.consume("after", recipient="r", consumer_id="late_plan")
    else:
        runtime.consume("after", recipient="r", consumer_id="late_plan")
    assert recipient_record(runtime, "before")["delivered_at_s"] == 0.
    if "delay_s" in conditions:
        assert recipient_record(runtime, "after")["delivered_at_s"] == pytest.approx(3.1)
    if "bytes_per_window" in conditions:
        assert recipient_record(runtime, "after")["transmitted_at_s"] > 1.1
    report = runtime.report()
    checked = validate_communication_report(report)
    assert checked["validated"], checked["mismatches"]


def test_report_binds_detached_initial_and_final_contracts():
    initial = {"delay_s": .1, "rules": [{"recipient": "s", "drop": True}]}
    frozen = validate_communication_contract(initial)
    runtime = CommunicationRuntime(initial)
    initial["rules"].clear()
    publish(runtime, "before")
    runtime.consume("before", recipient="r", consumer_id="plan", time_s=.1)
    early_report = runtime.report()
    runtime.set_conditions({"delay_s": 2.})
    runtime.set_conditions({"drop": False}, recipient="s")
    publish(runtime, "after", recipients=("r", "s"))
    runtime.advance(3.)
    report = runtime.report()
    assert report["version"] == 2
    assert report["initial_contract"] == frozen == early_report["contract"]
    assert report["contract"]["delay_s"] == 2.
    assert report["contract"]["rules"] == [
        {"recipient": "s", "drop": True}, {"recipient": "s", "drop": False}]
    original = deepcopy(report)
    # Actual JSON serialization must preserve replay, not just Python objects.
    assert validate_communication_report(json.loads(json.dumps(report)),
                                         expected_initial_contract=frozen)["validated"]
    assert report == original
    report["initial_contract"]["rules"].clear()
    report["contract"]["rules"].clear()
    assert runtime.report() == original


def test_replay_preserves_same_time_action_order_and_multiple_condition_changes():
    runtime = CommunicationRuntime()
    publish(runtime, "before")
    runtime.consume("before", recipient="r", consumer_id="plan")
    runtime.set_conditions({"delay_s": 2.})
    publish(runtime, "in_flight")
    runtime.set_conditions({"drop": True}, recipient="r")
    publish(runtime, "dropped")
    runtime.set_conditions({"drop": False, "delay_s": .5}, recipient="r")
    publish(runtime, "later")
    runtime.consume("later", recipient="r", consumer_id="plan", time_s=.5)
    runtime.consume("in_flight", recipient="r", consumer_id="plan", time_s=2.)
    assert recipient_record(runtime, "dropped")["status"] == "dropped"
    assert validate_communication_report(runtime.report(), expected_initial_contract={})["validated"]


def _changed_report():
    runtime = CommunicationRuntime()
    publish(runtime, "before")
    runtime.consume("before", recipient="r", consumer_id="plan", time_s=.1)
    runtime.set_conditions({"delay_s": 2.}, time_s=1.)
    runtime.set_conditions({"drop": True}, recipient="s", time_s=1.)
    publish(runtime, "after", recipients=("r", "s"), time=1.1)
    runtime.consume("after", recipient="r", consumer_id="plan", time_s=4.)
    return runtime.report()


@pytest.mark.parametrize("field", ["initial_contract", "contract"])
@pytest.mark.parametrize("tamper", [
    lambda contract: contract.__setitem__("delay_s", 7.),
    # No publication ever uses this selector: final-contract binding must still catch it.
    lambda contract: contract["rules"].append({"recipient": "unused", "drop": True}),
    lambda contract: contract.__setitem__("drop", 0),
    lambda contract: contract.pop("window_s"),
])
def test_replay_rejects_forged_initial_or_final_contract(field, tamper):
    report = _changed_report()
    tamper(report[field])
    checked = validate_communication_report(report)
    assert not checked["validated"] and checked["mismatches"]


@pytest.mark.parametrize("field", ["initial_contract", "contract"])
@pytest.mark.parametrize("replacement", [None, [], "default"])
def test_report_cannot_default_a_missing_or_malformed_contract(field, replacement):
    report = _changed_report()
    report[field] = replacement
    assert not validate_communication_report(report)["validated"]
    report.pop(field)
    assert not validate_communication_report(report)["validated"]


@pytest.mark.parametrize("tamper", [
    lambda event: event["conditions"].__setitem__("delay_s", 9.),
    lambda event: event.__setitem__("recipient", "unused"),
    lambda event: event.__setitem__("event", "channel_condition_change_rejected"),
    lambda event: event.__setitem__("time_s", 0.),
    lambda event: event.__setitem__("applies_to", "all_messages"),
])
def test_replay_rejects_forged_condition_change_ledger(tamper):
    report = _changed_report()
    event = next(e for e in report["ledger"] if e["event"] == "channel_condition_changed")
    tamper(event)
    checked = validate_communication_report(report)
    assert not checked["validated"] and checked["mismatches"]


def test_replay_rejects_removed_condition_change_even_with_reindexed_ledger():
    report = _changed_report()
    report["ledger"] = [e for e in report["ledger"] if e["event"] != "channel_condition_changed"]
    for sequence, event in enumerate(report["ledger"]):
        event["sequence"] = sequence
    assert not validate_communication_report(report)["validated"]


@pytest.mark.parametrize("tamper", [
    lambda report: report["ledger"][0].__setitem__("sequence", False),
    lambda report: report["ledger"][0].__setitem__("time_s", False),
    lambda report: report.__setitem__("time_s", .5),
    lambda report: report.__setitem__("version", True),
    lambda report: report.__setitem__("version", 2.),
    lambda report: report["messages"]["after"]["recipients"]["s"].__setitem__("bytes_sent", False),
])
def test_replay_rejects_invalid_evidence_types_and_horizon(tamper):
    report = _changed_report()
    tamper(report)
    assert not validate_communication_report(report)["validated"]


def test_expected_initial_contract_binds_even_unexercised_policy():
    report = _changed_report()
    for field in ("initial_contract", "contract"):
        report[field]["rules"].insert(0, {"recipient": "unused", "drop": True})
    # This rewrite is internally consistent. Only the independently frozen
    # initial contract can establish that it is the wrong episode policy.
    assert validate_communication_report(report)["validated"]
    checked = validate_communication_report(report, expected_initial_contract={})
    assert not checked["validated"]
    assert "expected_initial_contract" in checked["mismatches"][0]


def _as_legacy_report(report):
    legacy = deepcopy(report)
    legacy["version"] = 1
    legacy.pop("initial_contract")
    return legacy


@pytest.mark.parametrize("reject_change", [False, True])
def test_legacy_unchanged_or_rejected_only_contract_remains_replayable(reject_change):
    runtime = CommunicationRuntime({"bytes_per_window": 40})
    publish(runtime)
    if reject_change:
        with pytest.raises(ValueError, match="queue is not empty"):
            runtime.set_conditions({"bytes_per_window": 500}, time_s=.1)
    runtime.consume("m", recipient="r", consumer_id="plan", time_s=10.)
    report = _as_legacy_report(runtime.report())
    assert validate_communication_report(report)["validated"]


def test_legacy_selector_changes_recover_initial_rules_without_duplicating_them():
    initial = {"rules": [{"recipient": "s", "drop": True}]}
    runtime = CommunicationRuntime(initial)
    publish(runtime, "before")
    runtime.consume("before", recipient="r", consumer_id="plan", time_s=.1)
    runtime.set_conditions({"delay_s": 2.}, recipient="r", time_s=1.)
    runtime.set_conditions({"drop": False}, recipient="s", channel="observations")
    publish(runtime, "after", recipients=("r", "s"))
    runtime.consume("after", recipient="r", consumer_id="plan", time_s=3.)
    report = _as_legacy_report(runtime.report())
    original = deepcopy(report)
    assert validate_communication_report(report)["validated"]
    assert validate_communication_report(report, expected_initial_contract=initial)["validated"]
    assert report == original
    report["contract"]["rules"].pop()
    assert not validate_communication_report(report)["validated"]


def test_legacy_global_changes_require_external_initial_policy_not_a_guess():
    report = _as_legacy_report(_changed_report())
    checked = validate_communication_report(report)
    assert not checked["validated"]
    assert "requires expected_initial_contract" in checked["mismatches"][0]
    assert validate_communication_report(report, expected_initial_contract={})["validated"]
    assert not validate_communication_report(report, expected_initial_contract={"delay_s": 3.})["validated"]
    report["contract"]["rules"].append({"recipient": "unused", "drop": True})
    assert not validate_communication_report(report, expected_initial_contract={})["validated"]


def test_v2_cannot_be_downgraded_to_bypass_initial_contract_binding():
    report = _changed_report()
    report["version"] = 1
    assert not validate_communication_report(report)["validated"]
