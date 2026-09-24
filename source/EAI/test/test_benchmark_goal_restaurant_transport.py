"""Causal shared-link checks; synthetic stamps are not native-run evidence."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime, validate_communication_report
from demo.benchmark.goal_restaurant_transport import RestaurantReadinessTransport, SERVICE_RECIPIENT
from source.EAI.test.test_benchmark_goal_restaurant_business_v2 import (
    EPISODE, _build_case, _verify,
)


PRODUCER = "restaurant_carter_1"
BINDINGS = {PRODUCER: {"native_agent_id": "carter_1"}}


def _stamp(step):
    return {"simulation_step": 100 + step, "time_s": step * .1,
            "producer_id": PRODUCER, "native_agent_id": "carter_1",
            "root_state_w": [0., 0., .2, 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.]}


def _ready(contract=None):
    runtime = CommunicationRuntime(contract)
    bridge = RestaurantReadinessTransport(
        episode_id=EPISODE, runtime=runtime, robot_bindings=BINDINGS,
        episode_step_origin=100, physics_dt_s=.1)
    for step, region in enumerate(("kitchen_service", "dining_service"), 1):
        bridge.record_observation(
            task_id=f"observe-{step}", producer_id=PRODUCER,
            observation_id=f"observation-{step}",
            scene_observation={"producer_id": PRODUCER, "region_id": region,
                               "stamp": _stamp(step)})
    return runtime, bridge


def _publish(bridge, *, message_id="relay", step=3, expiry=10.):
    return bridge.publish_relay(
        task_id=f"task-{message_id}", producer_id=PRODUCER, message_id=message_id,
        observation_refs=["observation-2", "observation-1"],
        stamp=_stamp(step), valid_until_s=expiry)


def _poll(bridge, step, message_id="relay"):
    return bridge.poll_relay(message_id=message_id, stamp=_stamp(step), receipt_id="ready")


def test_publication_is_not_completion_and_delay_controls_receipt():
    runtime, bridge = _ready({"delay_s": .5})
    _publish(bridge)
    assert _poll(bridge, 3)["status"] == "pending"
    assert _poll(bridge, 7)["status"] == "pending"
    assert bridge.snapshot()["readiness_service"]["receipt"] is None
    assert _poll(bridge, 9)["status"] == "succeeded"
    message = runtime.report()["messages"]["relay"]
    delivery = message["recipients"][SERVICE_RECIPIENT]
    assert delivery["delivered_at_s"] == pytest.approx(.8)
    assert delivery["consumption"]["consumer_id"] == "ready"
    assert delivery["bytes_sent"] == message["size_bytes"]
    assert validate_communication_report(runtime.report())["validated"]
    with pytest.raises(ValueError, match="terminated"):
        _poll(bridge, 9)


def test_zero_delay_still_requires_a_new_physics_tick():
    _, bridge = _ready()
    _publish(bridge)
    assert _poll(bridge, 3)["status"] == "pending"
    assert _poll(bridge, 4)["status"] == "succeeded"


@pytest.mark.parametrize("policy,expiry,reason", [
    ({"active_drop": True}, 10., "active"),
    ({"buffer_bytes": 1}, 10., "buffer_overflow"),
    ({"fixed_delay_s": 2.}, .5, "ttl"),
])
def test_real_drop_classes_never_commit_receipt(policy, expiry, reason):
    runtime, bridge = _ready({"pools": {
        "backhaul_uplink": {"capacity_bps": 100000., **policy}}})
    _publish(bridge, expiry=expiry)
    result = _poll(bridge, 7)
    assert result["status"] == "failed"
    assert result["reason"] == "relay_transport_" + reason
    assert bridge.snapshot()["readiness_service"]["receipt"] is None
    assert len(bridge.snapshot()["readiness_service"]["relay_publications"]) == 1
    assert validate_communication_report(runtime.report())["validated"]


def test_other_traffic_contends_for_same_pool_and_retry_is_metered():
    runtime, bridge = _ready({"pools": {
        "backhaul_uplink": {"capacity_bps": 1000.}},
        "access": {PRODUCER: {"capacity_bps": 1000.}}})
    runtime.publish(message_id="pose", producer_id="robot_other", recipients=["cloud"],
                    channel="pose", estimate={"payload": "x" * 2000},
                    published_at_s=.2, valid_until_s=20., pool="backhaul_uplink")
    _publish(bridge)
    _publish(bridge, message_id="retry", step=4)
    assert _poll(bridge, 5)["status"] == "pending"
    rows = runtime.report()["messages"]
    old = rows["relay"]["recipients"][SERVICE_RECIPIENT]
    retry = rows["retry"]["recipients"][SERVICE_RECIPIENT]
    assert old["pool"] == retry["pool"] == "backhaul_uplink"
    assert old["access_endpoints"] == [PRODUCER]
    assert old["scheduled_transmit_at_s"] > .3
    assert retry["scheduled_transmit_at_s"] > old["scheduled_transmit_at_s"]
    assert _poll(bridge, 90, "retry")["status"] == "succeeded"
    assert bridge.snapshot()["readiness_service"]["receipt"]["relay_message_id"] == "retry"
    assert validate_communication_report(runtime.report())["validated"]


def test_drop_then_new_dispatch_can_succeed_without_erasing_failed_attempt():
    runtime, bridge = _ready({"drop": True})
    _publish(bridge)
    assert _poll(bridge, 4)["status"] == "failed"
    runtime.set_conditions({"drop": False}, time_s=.4)
    _publish(bridge, message_id="retry", step=5)
    assert _poll(bridge, 6, "retry")["status"] == "succeeded"
    assert len(bridge.snapshot()["readiness_service"]["relay_publications"]) == 2
    assert runtime.report()["messages"]["relay"]["recipients"][SERVICE_RECIPIENT]["status"] == "dropped"


def test_consumed_elsewhere_and_expired_after_delivery_are_not_receipts():
    runtime, bridge = _ready()
    _publish(bridge, expiry=.6)
    runtime.advance(.4)
    runtime.consume("relay", recipient=SERVICE_RECIPIENT, consumer_id="foreign")
    with pytest.raises(ValueError, match="outside its service"):
        _poll(bridge, 4)
    assert bridge.snapshot()["readiness_service"]["receipt"] is None
    assert _poll(bridge, 6)["reason"] == "relay_transport_ttl"


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(simulation_step=True),
    lambda row: row.update(simulation_step=99),
    lambda row: row.update(time_s=float("nan")),
    lambda row: row.update(time_s=17.),
    lambda row: row["root_state_w"].__setitem__(0, float("inf")),
    lambda row: row["root_state_w"].__setitem__(3, 0.),
])
def test_invalid_native_stamp_fails_before_publication(mutate):
    runtime, bridge = _ready()
    before = bridge.snapshot()
    stamp = _stamp(3)
    mutate(stamp)
    with pytest.raises(ValueError):
        bridge.publish_relay(
            task_id="relay", producer_id=PRODUCER, message_id="relay",
            observation_refs=["observation-1", "observation-2"],
            stamp=stamp, valid_until_s=10.)
    assert bridge.snapshot() == before
    assert runtime.report()["messages"] == {}


def test_shared_clock_cannot_be_rewound():
    runtime, bridge = _ready()
    runtime.advance(1.)
    with pytest.raises(ValueError, match="communication clock"):
        _publish(bridge)
    assert bridge.snapshot()["readiness_service"]["relay_publications"] == []


def test_global_message_collision_does_not_orphan_service_publication():
    runtime, bridge = _ready()
    runtime.publish(message_id="relay", producer_id="other", recipients=["cloud"],
                    channel="pose", estimate={}, published_at_s=.2, valid_until_s=10.)
    before = bridge.snapshot()
    with pytest.raises(ValueError, match="duplicate"):
        _publish(bridge)
    assert bridge.snapshot() == before


@pytest.mark.parametrize("field,value", [
    ("producer_id", "restaurant_iris_1"), ("native_agent_id", "carter_2"),
])
def test_cannot_relabel_another_robots_terminal_sample(field, value):
    _, bridge = _ready()
    _publish(bridge)
    before = bridge.snapshot()
    sample = _stamp(4)
    sample[field] = value
    with pytest.raises(ValueError, match="producer/articulation"):
        bridge.poll_relay(message_id="relay", stamp=sample, receipt_id="ready")
    assert bridge.snapshot() == before


def test_constructor_rejects_preexisting_foreign_epoch_and_missing_pool():
    runtime = CommunicationRuntime()
    runtime.advance(.1)
    with pytest.raises(ValueError, match="episode epoch"):
        RestaurantReadinessTransport(
            episode_id=EPISODE, runtime=runtime, robot_bindings=BINDINGS,
            episode_step_origin=100, physics_dt_s=.1)
    with pytest.raises(ValueError, match="uplink pool"):
        RestaurantReadinessTransport(
            episode_id=EPISODE,
            runtime=CommunicationRuntime({"pools": {"p2p": {"capacity_bps": 100.}}}),
            robot_bindings=BINDINGS, episode_step_origin=100, physics_dt_s=.1)


def test_consume_rejection_does_not_commit_service_or_native_receipt(monkeypatch):
    runtime, bridge = _ready()
    _publish(bridge)
    before = bridge.snapshot()

    def reject(*args, **kwargs):
        raise ValueError("injected consume rejection")

    monkeypatch.setattr(runtime, "consume", reject)
    with pytest.raises(ValueError, match="consume rejection"):
        _poll(bridge, 4)
    after = bridge.snapshot()
    assert after["readiness_service"] == before["readiness_service"]
    assert after["native_evidence"] == before["native_evidence"]


@pytest.mark.parametrize("shared_observer,reverse,selected_retry", [
    (False, False, False), (True, True, True),
])
def test_bridge_output_passes_existing_independent_business_verifier(
        shared_observer, reverse, selected_retry):
    # Exercise the real bridge against the independent verifier's complete
    # synthetic native/session fixture; no same-implementation-only oracle.
    case = _build_case(shared_observer=shared_observer, reverse=reverse,
                       selected_retry=selected_retry)
    original = deepcopy(case["native_evidence"])
    runtime = CommunicationRuntime()
    bridge = RestaurantReadinessTransport(
        episode_id=EPISODE, runtime=runtime, robot_bindings=case["robot_bindings"],
        episode_step_origin=0, physics_dt_s=.1)
    for row in original:
        def bound_stamp(stamp):
            return {**stamp, "producer_id": row["producer_id"],
                    "native_agent_id": row["native_agent_id"]}

        if row["kind"] == "observation":
            actual = bridge.record_observation(
                task_id=row["task_id"], producer_id=row["producer_id"],
                observation_id=row["service_record"]["observation_id"],
                scene_observation={**row["scene_observation"],
                                   "stamp": bound_stamp(row["stamp"])})
            assert actual == row
        elif row["kind"] == "relay_publication":
            actual = bridge.publish_relay(
                task_id=row["task_id"], producer_id=row["producer_id"],
                message_id=row["relay_publication"]["message_id"],
                observation_refs=row["relay_publication"]["observation_refs"],
                stamp=bound_stamp(row["publication_stamp"]), valid_until_s=100.)
            assert actual == row
        elif row["kind"] == "readiness_receipt":
            actual = bridge.poll_relay(
                message_id=row["business_receipt"]["relay_message_id"],
                stamp=bound_stamp(row["receipt_stamp"]), receipt_id="ready")
            assert actual["native_evidence"] == row
    snapshot = bridge.snapshot()
    case.update({key: snapshot[key] for key in (
        "readiness_service", "transport_report")})
    case["native_evidence"] = ([row for row in original if row["kind"] == "arrival"]
                                + snapshot["native_evidence"])
    review = _verify(case)
    assert review["validated"] is True
    assert review["native_worker_wiring_verified"] is False
    assert snapshot["scoreable"] is False
