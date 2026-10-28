import hashlib

import pytest

from benchmark.room06_observation_bridge_v1 import (
    OBSERVED_SKILL_EVENT_SCHEMA,
    Room06ObservationBridge,
    Room06ObservationBridgeError,
)
from benchmark.tests.test_room06_task_runtime_v1 import RUNTIME_BINDING_DIGEST, _runtime


ROBOTS = ("hc-rt-006", "hc-rt-020", "hc-rt-021")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _event(
    session_id,
    event_type,
    tick,
    robot_id,
    object_id,
    target_ref,
):
    return {
        "schema": OBSERVED_SKILL_EVENT_SCHEMA,
        "session_id": session_id,
        "event_type": event_type,
        "status": "completed",
        "tick": tick,
        "robot_id": robot_id,
        "object_id": object_id,
        "target_ref": target_ref,
        "evidence_digest": _digest(session_id),
    }


def _bridge():
    return Room06ObservationBridge(
        _runtime(), robot_ids=ROBOTS, runtime_binding_digest=RUNTIME_BINDING_DIGEST
    )


def _relay(bridge):
    bridge.observe_assignment(
        tick=0, assigned_robot_ids=ROBOTS, decision_digest=_digest("decision")
    )
    bridge.observe_skill_event(
        _event(
            "pickup", "relay_pickup_completed", 1, ROBOTS[0],
            "book_batch_01", "floor1-bookshelf",
        )
    )
    bridge.observe_skill_event(
        _event(
            "handoff", "topology_handoff_completed", 2, ROBOTS[0],
            "book_batch_01", "node:transition",
        )
    )
    for tick, target in enumerate(
        ("floor2-bookshelf-1", "floor2-bookshelf-2", "floor2-bookshelf-3"),
        start=3,
    ):
        bridge.observe_skill_event(
            _event(
                f"deliver-{tick}", "relay_delivery_completed", tick,
                ROBOTS[1], "book_batch_01", target,
            )
        )


def test_observed_sessions_and_joint_holds_produce_runtime_evidence():
    bridge = _bridge()
    _relay(bridge)
    bridge.sample_holds(
        tick=7,
        held_objects={ROBOTS[0]: "heavy_object", ROBOTS[1]: "heavy_object", ROBOTS[2]: None},
    )
    bridge.sample_holds(
        tick=8,
        held_objects={ROBOTS[0]: "heavy_object", ROBOTS[1]: "heavy_object", ROBOTS[2]: "heavy_object"},
    )
    bridge.sample_holds(
        tick=8,
        held_objects={ROBOTS[0]: None, ROBOTS[1]: "heavy_object", ROBOTS[2]: "heavy_object"},
    )
    bridge.observe_skill_event(
        _event(
            "carry", "heavy_carry_completed", 9, ROBOTS[1],
            "heavy_object", "floor1-reading desk",
        )
    )
    bridge.observe_skill_event(
        _event(
            "accept", "task_accepted", 10, ROBOTS[0], "liv-t-04", "accepted"
        )
    )
    evidence = bridge.evidence_material()
    assert evidence["runtime_evidence"]["runtime_state"] == "complete"
    assert evidence["runtime_evidence"]["streams"]["hold_intervals"] == [
        {"object_id": "heavy_object", "robot_id": ROBOTS[0], "start_tick": 7, "end_tick": 8},
        {"object_id": "heavy_object", "robot_id": ROBOTS[1], "start_tick": 7, "end_tick": 9},
        {"object_id": "heavy_object", "robot_id": ROBOTS[2], "start_tick": 8, "end_tick": 9},
    ]


def test_missing_roster_hold_sample_fails_closed():
    bridge = _bridge()
    _relay(bridge)
    with pytest.raises(Room06ObservationBridgeError, match="exactly cover"):
        bridge.sample_holds(tick=7, held_objects={ROBOTS[0]: "heavy_object"})


def test_one_real_delivery_session_can_also_support_task_acceptance():
    bridge = _bridge()
    _relay(bridge)
    bridge.sample_holds(tick=7, held_objects={ROBOTS[0]: "heavy_object", ROBOTS[1]: "heavy_object", ROBOTS[2]: None})
    bridge.observe_skill_event(_event("final-session", "heavy_carry_completed", 9, ROBOTS[1], "heavy_object", "floor1-reading desk"))
    accepted = _event("final-session", "task_accepted", 9, ROBOTS[1], "liv-t-04", "accepted")
    changed = dict(accepted, evidence_digest="f" * 64)
    with pytest.raises(Room06ObservationBridgeError, match="different evidence"):
        bridge.observe_skill_event(changed)
    bridge.observe_skill_event(accepted)
    bridge.observe_skill_event(accepted)
    material = bridge.evidence_material()
    assert material["runtime_evidence"]["runtime_state"] == "complete"
    assert len([e for e in material["observed_skill_events"] if e["session_id"] == "final-session"]) == 2


def test_single_holder_records_safety_failure_and_cannot_become_success():
    bridge = _bridge()
    _relay(bridge)
    with pytest.raises(Room06ObservationBridgeError, match="at least 2 distinct holders"):
        bridge.sample_holds(
            tick=7,
            held_objects={ROBOTS[0]: "heavy_object", ROBOTS[1]: None, ROBOTS[2]: None},
        )
    material = bridge.evidence_material()["runtime_evidence"]
    assert material["runtime_state"] == "failed"
    assert material["safety_failures"][0]["code"] == "joint_hold_below_minimum_at_lift"


def test_duplicate_session_is_idempotent_but_tampering_is_rejected():
    bridge = _bridge()
    bridge.observe_assignment(
        tick=0, assigned_robot_ids=ROBOTS, decision_digest=_digest("decision")
    )
    event = _event(
        "pickup", "relay_pickup_completed", 1, ROBOTS[0],
        "book_batch_01", "floor1-bookshelf",
    )
    bridge.observe_skill_event(event)
    bridge.observe_skill_event(event)
    bridge.observe_skill_event(
        _event(
            "handoff", "topology_handoff_completed", 2, ROBOTS[0],
            "book_batch_01", "node:transition",
        )
    )
    # Observer delivery can replay an already accepted session after the
    # bridge has advanced.  Identical evidence remains idempotent.
    bridge.observe_skill_event(event)
    event["evidence_digest"] = _digest("tampered")
    with pytest.raises(Room06ObservationBridgeError, match="different evidence"):
        bridge.observe_skill_event(event)


def test_command_or_noncompleted_event_cannot_advance_milestones():
    bridge = _bridge()
    bridge.observe_assignment(
        tick=0, assigned_robot_ids=ROBOTS, decision_digest=_digest("decision")
    )
    event = _event(
        "pickup", "relay_pickup_completed", 1, ROBOTS[0],
        "book_batch_01", "floor1-bookshelf",
    )
    event["status"] = "commanded"
    with pytest.raises(Room06ObservationBridgeError, match="only completed"):
        bridge.observe_skill_event(event)


def test_rejected_event_replay_is_not_a_cached_success():
    bridge = _bridge()
    bridge.observe_assignment(tick=0, assigned_robot_ids=ROBOTS, decision_digest=_digest("decision"))
    event = _event("bad-pick", "relay_pickup_completed", 1, ROBOTS[0],
                   "book_batch_01", "wrong-source")
    for _ in range(2):
        with pytest.raises(Room06ObservationBridgeError, match="relay pickup"):
            bridge.observe_skill_event(event)
    bridge.finalize_at_horizon()
    evidence = bridge.evidence_material()
    assert evidence["observed_skill_events"] == []
    assert evidence["runtime_evidence"]["streams"]["milestones"] == [{"label": "assigned", "tick": 0}]


def test_empty_decision_cannot_authorize_events_or_joint_holds():
    bridge = _bridge()
    bridge.observe_assignment(tick=0, assigned_robot_ids=[], decision_digest=_digest("no-choice"))
    with pytest.raises(Room06ObservationBridgeError, match="outside the observed assignment"):
        bridge.observe_skill_event(_event("pick", "relay_pickup_completed", 1,
            ROBOTS[0], "book_batch_01", "floor1-bookshelf"))
    with pytest.raises(Room06ObservationBridgeError, match="outside the observed assignment"):
        bridge.sample_holds(tick=1, held_objects={ROBOTS[0]: "heavy_object", ROBOTS[1]: "heavy_object", ROBOTS[2]: None})
    bridge.finalize_at_horizon()
    assert bridge.evidence_material()["runtime_evidence"]["streams"]["milestones"] == []


def test_unassigned_robot_cannot_contribute_event_or_joint_hold_evidence():
    bridge = _bridge()
    bridge.observe_assignment(
        tick=0,
        assigned_robot_ids=ROBOTS[:2],
        decision_digest=_digest("two-robot-decision"),
    )
    with pytest.raises(Room06ObservationBridgeError, match="observed assignment"):
        bridge.observe_skill_event(
            _event(
                "unassigned-pickup", "relay_pickup_completed", 1, ROBOTS[2],
                "book_batch_01", "floor1-bookshelf",
            )
        )
    with pytest.raises(Room06ObservationBridgeError, match="holder is outside"):
        bridge.sample_holds(
            tick=1,
            held_objects={
                ROBOTS[0]: "heavy_object",
                ROBOTS[1]: None,
                ROBOTS[2]: "heavy_object",
            },
        )
