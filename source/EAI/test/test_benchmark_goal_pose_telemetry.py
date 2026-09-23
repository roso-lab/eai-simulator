"""Bounded pose telemetry tests; no Isaac, assets, or external services."""

from copy import deepcopy
import math

import pytest

from demo.benchmark.communication import CommunicationRuntime, validate_communication_report
from demo.benchmark.goal_pose_telemetry import GoalPoseTelemetry


def sample(*, time_s=0.0, source="physx"):
    return {
        "sampled_at_s": time_s,
        "position_m": [1.25, -2.5, 0.4],
        "yaw_rad": -0.75,
        "frame_id": "world",
        "source": source,
        "quality": {"position_error_m": None, "readback_current": True},
    }


def test_delivered_payload_is_exact_preserves_source_and_emits_once():
    telemetry = GoalPoseTelemetry(message_prefix="run-7")
    host_record = telemetry.publish("go2_1", sample(source="synthetic"), 5.0)
    message_id = host_record["payload"]["message_id"]

    assert telemetry.owns_message(message_id)
    assert telemetry.advance(0.0) == [{
        "message_id": message_id,
        "producer_id": "go2_1",
        "kind": "pose_update",
        "data": {**sample(source="synthetic"), "published_at_s": 0.0},
        "valid_until_s": 5.0,
    }]
    assert telemetry.advance(0.0) == []
    assert host_record["recipients"]["participant"]["status"] == "delivered"


@pytest.mark.parametrize(("change", "match"), [
    ({"sampled_at_s": float("nan")}, "sampled_at_s"),
    ({"position_m": [0.0, 1.0]}, "position_m"),
    ({"position_m": [0.0, float("inf"), 1.0]}, r"position_m\[1\]"),
    ({"yaw_rad": math.pi + 0.001}, "normalized"),
    ({"frame_id": ""}, "frame_id"),
    ({"source": "estimated"}, "source"),
    ({"quality": {"error_m": float("nan")}}, "finite JSON"),
])
def test_pose_sample_rejects_invalid_values_without_reserving_message(change, match):
    telemetry = GoalPoseTelemetry(message_prefix="validation")
    invalid = {**sample(), **change}
    with pytest.raises(ValueError, match=match):
        telemetry.publish("robot", invalid, 4.0)
    assert telemetry.communication_report()["messages"] == {}


def test_pose_sample_is_exact_json_and_must_be_current():
    telemetry = GoalPoseTelemetry()
    with pytest.raises(ValueError, match="fields mismatch"):
        telemetry.publish("robot", {**sample(), "velocity_mps": 1.0}, 2.0)
    with pytest.raises(ValueError, match="three-element list"):
        telemetry.publish("robot", {**sample(), "position_m": (1.0, 2.0, 3.0)}, 2.0)
    with pytest.raises(ValueError, match="current communication time"):
        telemetry.publish("robot", sample(time_s=1.0), 2.0)


def test_delay_drop_and_ttl_boundary_never_leak_hidden_transport_state():
    delayed = GoalPoseTelemetry(CommunicationRuntime({"delay_s": 2.0}))
    delayed_record = delayed.publish("robot", sample(), 3.0)
    delayed_id = delayed_record["payload"]["message_id"]
    assert delayed.advance(1.999) == []
    assert not delayed.can_consume(delayed_id, 1.999)
    delivered = delayed.advance(2.0)
    assert delivered[0]["message_id"] == delayed_id
    assert "conditions" not in repr(delivered)
    assert "scheduled_delivery" not in repr(delivered)

    dropped = GoalPoseTelemetry(CommunicationRuntime({"drop": True}))
    dropped.publish("robot", sample(), 10.0)
    assert dropped.advance(10.0) == []

    expires_on_arrival = GoalPoseTelemetry(CommunicationRuntime({"delay_s": 2.0}))
    expiring = expires_on_arrival.publish("robot", sample(), 2.0)
    assert expires_on_arrival.advance(2.0) == []
    state = expires_on_arrival.communication_report()["messages"]
    delivery = state[expiring["payload"]["message_id"]]["recipients"]["participant"]
    assert delivery["status"] == "expired"


def test_bandwidth_and_delay_gate_delivery_until_the_computed_arrival():
    budget = 64
    delay = 0.5
    runtime = CommunicationRuntime({
        "bytes_per_window": budget, "window_s": 1.0, "delay_s": delay,
    })
    telemetry = GoalPoseTelemetry(runtime)
    record = telemetry.publish("robot", sample(), 100.0)
    windows = math.ceil(record["size_bytes"] / budget)
    arrival = windows - 1 + delay
    assert telemetry.advance(arrival - 0.001) == []
    assert len(telemetry.advance(arrival)) == 1


def test_consume_is_action_idempotent_without_refreshing_time_and_reuse_is_rejected():
    telemetry = GoalPoseTelemetry(message_prefix="consume")
    first_id = telemetry.publish("robot", sample(), 10.0)["payload"]["message_id"]
    assert not telemetry.can_consume(first_id, 0.0)
    with pytest.raises(ValueError, match="not been emitted"):
        telemetry.consume(first_id, "too-early", 0.0)
    telemetry.advance(0.0)
    assert telemetry.can_consume(first_id, 0.0)
    first = telemetry.consume(first_id, "action-1", 0.0)
    assert not telemetry.can_consume(first_id, 0.0)

    second_id = telemetry.publish("robot", sample(), 10.0)["payload"]["message_id"]
    telemetry.advance(0.0)
    before_retry = telemetry.communication_report()
    assert telemetry.consume(first_id, "action-1", 9.0) == first
    assert telemetry.communication_report() == before_retry
    with pytest.raises(ValueError, match="already bound"):
        telemetry.consume(second_id, "action-1", 0.0)
    assert telemetry.communication_report() == before_retry


def test_can_consume_does_not_predict_or_advance_future_delivery():
    telemetry = GoalPoseTelemetry(CommunicationRuntime({"delay_s": 1.0}))
    message_id = telemetry.publish("robot", sample(), 5.0)["payload"]["message_id"]
    before = deepcopy(telemetry.communication_report())
    assert not telemetry.can_consume(message_id, 1.0)
    assert telemetry.communication_report() == before
    assert telemetry.advance(1.0)[0]["message_id"] == message_id
    assert telemetry.can_consume(message_id, 1.0)


def test_communication_report_replays_and_makes_no_isaac_claim():
    telemetry = GoalPoseTelemetry(CommunicationRuntime({"delay_s": 0.25}))
    message_id = telemetry.publish("robot", sample(), 2.0)["payload"]["message_id"]
    telemetry.advance(0.25)
    telemetry.consume(message_id, "consume-action", 0.25)
    report = telemetry.communication_report()
    assert report["isaac_integrated"] is False
    assert validate_communication_report(report) == {
        "schema": "eai.benchmark.communication_replay",
        "validated": True,
        "mismatches": [],
        "isaac_integrated": False,
        "published_count": 1,
        "ledger_event_count": len(report["ledger"]),
    }
