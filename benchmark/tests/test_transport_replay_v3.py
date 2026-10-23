from __future__ import annotations

import pytest

from benchmark.observation_v3 import ObservationProjectorV3, ObservationRightsV3
from benchmark.replay_state_v3 import ReplayStateRegistryV3, SnapshotParticipantV3
from benchmark.transport_v3 import SharedChannelV3
import json


def test_metered_payload_is_detached_at_every_public_boundary():
    channel = SharedChannelV3(bits_per_tick=10000, max_queue_bits=100000)
    payload = {"route": ["start", "finish"]}
    submitted = channel.submit(message_id="m", sender_id="a", receiver_id="b",
        kind="command", payload=payload, tick=0)
    expected_bits = submitted.bits
    payload["route"].append("unmetered")
    submitted.payload["route"].append("also-unmetered")
    notice = channel.advance(0)[0]
    notice.payload["route"].append("changed-at-delivery")
    received = channel.receive(receiver_id="b", tick=0)[0]
    assert received.payload == {"route": ["start", "finish"]}
    received.payload["route"].clear()
    assert channel.snapshot()["frames"]["m"]["payload"] == {"route": ["start", "finish"]}
    assert channel.transmitted_bits == expected_bits


def test_nonfinite_wire_data_is_rejected_before_accounting():
    channel = SharedChannelV3(bits_per_tick=10, max_queue_bits=1000)
    with pytest.raises(ValueError):
        channel.submit(message_id="m", sender_id="a", receiver_id="b", kind="state",
            payload={"x": float("nan")}, tick=0)
    assert all(value == 0 for value in channel.accounting().values())


def test_shared_channel_uses_one_fleet_budget():
    channel = SharedChannelV3(bits_per_tick=64, max_queue_bits=4096)
    first = channel.submit(message_id="m1", sender_id="a", receiver_id="b", kind="x", payload={"x": "a" * 50}, tick=0)
    second = channel.submit(message_id="m2", sender_id="c", receiver_id="d", kind="x", payload={"x": "b" * 50}, tick=0)
    assert first.bits == second.bits
    channel.advance(8)
    assert channel.transmitted_bits == 9 * 64
    channel.advance(27)
    assert channel.transmitted_bits == first.bits + second.bits
    assert channel.accounting()["transmitted_bits"] == channel.accounting()["delivered_bits"]


def test_observation_projection_rejects_hidden_evaluator_fields():
    projector = ObservationProjectorV3({"planner": ObservationRightsV3(("pose", "status"), ("oracle",))})
    observation = {"pose": [0, 0], "status": "ready", "oracle": "secret"}
    projector.assert_no_leak(audience="planner", observation=observation, hidden_fields={"oracle"})
    assert projector.project(audience="planner", observation=observation) == {"pose": [0, 0], "status": "ready"}


def test_replay_registry_requires_full_causal_state():
    registry = ReplayStateRegistryV3()
    with pytest.raises(RuntimeError, match="missing participants"):
        registry.capture()
    for key in registry.REQUIRED_PARTICIPANTS:
        registry.register(SnapshotParticipantV3(key, "v1", lambda: {"value": 1}, lambda state: None, lambda: {"value": 1}))
    snapshot = registry.capture()
    registry.restore(snapshot)
    assert set(registry.readback()) == set(registry.REQUIRED_PARTICIPANTS)


def test_physical_state_without_explicit_clock_cannot_claim_causal_snapshot():
    participants = tuple(SnapshotParticipantV3(k, "1", lambda: {}, lambda s: None, lambda: {})
                         for k in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS if k != "clock")
    registry = ReplayStateRegistryV3(participants)
    assert registry.missing_required() == ("clock",)
    with pytest.raises(RuntimeError, match="clock"):
        registry.capture()


def test_snapshot_does_not_alias_mutable_runtime_or_restore_callback():
    live = {"queue": [1]}
    def restore(value):
        value["queue"].append(99)
    registry = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(k, "1", lambda: live,
        restore, lambda: {"queue": [1]}) for k in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    snapshot = registry.capture()
    live["queue"].append(2)
    registry.restore(snapshot)
    assert all(p["state"]["queue"] == [1] for p in snapshot.values())


def test_noop_restore_with_wrong_readback_is_rejected_and_quarantined():
    registry = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(k, "1", lambda: {"value": 1},
        lambda state: None, lambda: {"value": 2}) for k in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    snapshot = registry.capture()
    with pytest.raises(RuntimeError, match="readback mismatch"):
        registry.restore(snapshot)
    with pytest.raises(RuntimeError, match="quarantined"):
        registry.restore(snapshot)


def test_registered_physical_tolerance_does_not_relax_clock_equality():
    registry = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(k, "1", lambda: {"value": 1.},
        lambda state: None, lambda: {"value": 1.000001},
        (lambda a, b: abs(a["value"]-b["value"]) <= .001) if k == "world" else None)
        for k in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    with pytest.raises(RuntimeError, match="mismatch for clock"):
        registry.restore(registry.capture())


def test_partial_restore_cannot_be_reused_as_a_clean_runtime():
    def fail(state):
        raise RuntimeError("controller failed after world write")
    registry = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(k, "1", lambda: {},
        fail if k == "controller" else lambda s: None, lambda: {})
        for k in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    snapshot = registry.capture()
    with pytest.raises(RuntimeError, match="controller failed"):
        registry.restore(snapshot)
    with pytest.raises(RuntimeError, match="quarantined"):
        registry.capture()
    with pytest.raises(RuntimeError, match="quarantined"):
        registry.restore(snapshot)


def test_delayed_frames_survive_until_delivery_and_restore_matches():
    channel = SharedChannelV3(bits_per_tick=10000, max_queue_bits=100000, delay_ticks=5)
    channel.submit(message_id="m", sender_id="a", receiver_id="b", kind="state", payload={}, tick=0)
    assert channel.advance(0) == ()
    assert channel.delivered_bits == 0 and channel.transmitted_bits > 0
    restored = SharedChannelV3.restore(json.loads(json.dumps(channel.snapshot())))
    assert channel.advance(5) == restored.advance(5)
    assert len(channel.receive(receiver_id="b", tick=5)) == 1
    assert channel.delivered_bits == channel.transmitted_bits


def test_channel_cannot_transmit_before_submission_and_restores_random_stream():
    channel = SharedChannelV3(bits_per_tick=1000, max_queue_bits=100000, loss_probability=.5, seed=9)
    for i in range(5):
        channel.submit(message_id=str(i), sender_id="a", receiver_id="b", kind="state", payload={}, tick=10)
    channel.advance(5)
    assert channel.transmitted_bits == 0
    restored = SharedChannelV3.restore(json.loads(json.dumps(channel.snapshot())))
    assert channel.advance(15) == restored.advance(15)
    assert channel.snapshot() == restored.snapshot()


def test_filtered_receive_preserves_other_messages_and_kind():
    channel = SharedChannelV3(bits_per_tick=10000, max_queue_bits=100000)
    for i in range(3):
        channel.submit(message_id=str(i), sender_id="a", receiver_id="b", kind="assignment", payload={}, tick=0)
    channel.advance(0)
    sent = channel.transmitted_bits
    channel.advance(0)  # another phase at the same physical tick adds no budget
    assert channel.transmitted_bits == sent
    assert channel.receive(receiver_id="b", tick=0, max_items=1, message_id="1")[0].kind == "assignment"
    assert [m.message_id for m in channel.receive(receiver_id="b", tick=0)] == ["0", "2"]
