"""Cloud/edge/robot shared communication-pool checks."""

import pytest

from demo.benchmark.communication import CommunicationRuntime, validate_communication_report


def _publish(runtime, message_id, *, producer="robot_a", recipient="cloud",
             channel="pose", pool=None, expiry=100., information_id=None,
             estimate=None):
    return runtime.publish(
        message_id=message_id, producer_id=producer, recipients=[recipient],
        channel=channel, pool=pool, estimate=estimate or {"value": message_id},
        valid_until_s=expiry, information_id=information_id)


def test_backhaul_is_shared_across_robot_uplinks_and_directions_are_separate():
    runtime = CommunicationRuntime({
        "pools": {
            "backhaul_uplink": {"capacity_bps": 100.},
            "backhaul_downlink": {"capacity_bps": 100.},
        }
    })
    first = _publish(runtime, "a", producer="robot_a", pool="backhaul_uplink")
    second = _publish(runtime, "b", producer="robot_b", pool="backhaul_uplink")
    down = _publish(runtime, "c", producer="cloud", recipient="robot_a",
                    channel="dispatch", pool="backhaul_downlink")
    size_a = first["size_bytes"]
    size_b = second["size_bytes"]
    assert runtime.report()["messages"]["b"]["recipients"]["cloud"][
        "scheduled_transmit_at_s"] == pytest.approx(size_a / 100.)
    assert runtime.report()["messages"]["c"]["recipients"]["robot_a"][
        "scheduled_transmit_at_s"] == 0.
    runtime.advance(20.)
    assert runtime.report()["messages"]["b"]["recipients"]["cloud"]["delivered_at_s"] == pytest.approx((size_a + size_b) / 100.)
    assert runtime.report()["messages"]["c"]["recipients"]["robot_a"]["status"] == "delivered"


def test_p2p_uses_shared_pool_and_configured_robot_access_capacity():
    runtime = CommunicationRuntime({
        "pools": {"p2p": {"capacity_bps": 1000.}},
        "access": {
            "robot_a": {"capacity_bps": 100.},
            "robot_b": {"capacity_bps": 1000.},
        },
    })
    sent = _publish(runtime, "relay", producer="robot_a", recipient="robot_b",
                     channel="p2p_relay", pool="p2p")
    delivery = runtime.report()["messages"]["relay"]["recipients"]["robot_b"]
    assert delivery["access_endpoints"] == ["robot_a", "robot_b"]
    assert delivery["scheduled_finish_at_s"] == pytest.approx(sent["size_bytes"] / 100.)
    runtime.advance(20.)
    assert runtime.report()["messages"]["relay"]["recipients"]["robot_b"]["status"] == "delivered"


def test_pool_drop_reasons_distinguish_active_buffer_and_ttl():
    active = CommunicationRuntime({"pools": {"p2p": {"capacity_bps": 100., "active_drop": True}}})
    _publish(active, "active", producer="robot_a", recipient="robot_b", channel="p2p", pool="p2p")
    assert active.report()["messages"]["active"]["recipients"]["robot_b"]["drop_reason"] == "active"

    buffered = CommunicationRuntime({"pools": {"p2p": {"capacity_bps": 100., "buffer_bytes": 1}}})
    _publish(buffered, "buffer", producer="robot_a", recipient="robot_b", channel="p2p", pool="p2p")
    assert buffered.report()["messages"]["buffer"]["recipients"]["robot_b"]["drop_reason"] == "buffer_overflow"

    ttl = CommunicationRuntime({"pools": {"p2p": {"capacity_bps": 1.}}})
    _publish(ttl, "head", producer="robot_a", recipient="robot_b", channel="p2p", pool="p2p")
    _publish(ttl, "expires", producer="robot_a", recipient="robot_b", channel="p2p",
             pool="p2p", expiry=.1)
    ttl.advance(.1)
    assert ttl.report()["messages"]["expires"]["recipients"]["robot_b"]["drop_reason"] == "ttl"
    assert {event.get("reason") for event in ttl.report()["ledger"] if event["event"] == "expired"} == {"ttl"}


def test_pool_ttl_is_an_independent_transport_expiry():
    runtime = CommunicationRuntime({"pools": {"p2p": {"capacity_bps": 1., "ttl_s": .1}}})
    _publish(runtime, "ttl", producer="robot_a", recipient="robot_b", channel="p2p",
             pool="p2p", expiry=10.)
    runtime.advance(.1)
    row = runtime.report()["messages"]["ttl"]["recipients"]["robot_b"]
    assert row["status"] == "expired"
    assert row["drop_reason"] == "ttl"


def test_aoi_reports_delivery_age_mean_and_p95_and_replays():
    runtime = CommunicationRuntime({"pools": {"backhaul_uplink": {"capacity_bps": 100., "fixed_delay_s": 1.}}})
    _publish(runtime, "fresh", pool="backhaul_uplink", information_id="pose")
    runtime.advance(10.)
    stats = runtime.report()["aoi"]
    assert stats["sample_count"] == 1
    assert stats["mean_s"] == pytest.approx(stats["p95_s"])
    assert stats["samples"][0]["information_id"] == "pose"
    assert validate_communication_report(runtime.report())["validated"]
