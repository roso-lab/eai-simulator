from __future__ import annotations

from algorithm.heterocoord.fire_rescue_bridge import FireRescueCoordinationBridge


def test_neutral_bridge_records_submitted_assignment_without_planning():
    bridge = FireRescueCoordinationBridge(
        "emos",
        task_id="fire-rescue-vertical-slice",
        scene_id="EAI-Factory-Z1-v0",
    )
    bridge.observe_assignment(
        {"m20_1": "red", "m20_2": "yellow"},
        request=object(),
        timestamp_s=1.0,
    )
    assert bridge.plan_result is None
    payload = bridge.to_dict()
    assert payload["submitted_decisions"][0]["source"] == "submitted_system"
    assert payload["submitted_decisions"][0]["allocation_provider"] == "emos_submitted_system"
    assignment_events = [event for event in payload["ledger"]["events"] if event["kind"] == "assignment_applied"]
    assert assignment_events[-1]["payload"]["matches_algorithm_plan"] is None
