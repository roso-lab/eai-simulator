"""Pure lifecycle state-machine tests; no Isaac or runtime integration."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.team_lifecycle import TeamLifecycle


def member(identifier, *, domain="ground", nav=True, arms=()):
    return {"id": identifier, "capabilities": {
        "domain": domain, "navigation_interface": nav,
        "attachment_types": list(arms), "camera": domain == "aerial",
        "aerial_scan": domain == "aerial",
    }}


def req(identifier="move", **extra):
    return {"id": identifier, "domain": "ground", "navigation": True,
            "required_capabilities": {}, **extra}


def lifecycle(*, requirements=None):
    return TeamLifecycle([member("m20", arms=("z1",)), member("carter"),
                          member("drone", domain="aerial")], requirements or [req()])


def test_initial_members_are_active_but_reserve_requires_explicit_registration_and_join():
    state = lifecycle()
    assert state.snapshot()["members"]["m20"]["status"] == "active"
    assert state.snapshot()["reserve_pool"] == {}
    with pytest.raises(ValueError, match="explicitly registered"):
        state.join("backup", time_s=1)
    state.register_reserve(member("backup"), time_s=1)
    assert state.snapshot()["reserve_pool"]["backup"]["status"] == "reserve"
    state.join("backup", time_s=2)
    snapshot = state.snapshot()
    assert snapshot["members"]["backup"]["status"] == "active"
    assert "backup" not in snapshot["reserve_pool"]
    assert [row["type"] for row in state.ledger()] == ["initialized", "reserve_registered", "member_join"]


def test_join_does_not_expose_future_schedule_and_inputs_are_deep_copied():
    backup = member("backup")
    state = lifecycle()
    state.register_reserve(backup, time_s=1)
    backup["capabilities"]["domain"] = "aerial"
    snapshot = state.snapshot()
    assert snapshot["reserve_pool"]["backup"]["capabilities"]["domain"] == "ground"
    snapshot["reserve_pool"]["backup"]["capabilities"]["domain"] = "bad"
    assert state.snapshot()["reserve_pool"]["backup"]["capabilities"]["domain"] == "ground"
    assert all("future" not in json.dumps(row) for row in state.ledger())


def test_dynamic_qualification_uses_current_member_capabilities():
    state = lifecycle(requirements=[req("arm", manipulation_interface=True)])
    assert state.qualified_members("arm") == ["m20"]
    state.register_reserve(member("arm_backup", arms=("ur5",)), time_s=1)
    state.join("arm_backup", time_s=2)
    assert state.qualified_members("arm") == ["arm_backup", "m20"]
    state.exit("m20", time_s=3)
    assert state.qualified_members("arm") == ["arm_backup"]


def test_capability_change_requires_strict_integer_version_and_replaces_declaration():
    state = lifecycle(requirements=[req("arm", manipulation_interface=True)])
    old = state.snapshot()
    with pytest.raises(ValueError, match="strictly increasing"):
        state.capability_change("m20", member("m20")["capabilities"], capability_version=1, time_s=1)
    with pytest.raises(ValueError, match="strictly increasing"):
        state.capability_change("m20", member("m20")["capabilities"], capability_version=1.5, time_s=1)
    replacement = member("m20", arms=("ur5",))["capabilities"]
    state.capability_change("m20", replacement, capability_version=2, time_s=1)
    assert state.snapshot()["members"]["m20"]["capabilities"]["attachment_types"] == ["ur5"]
    assert state.snapshot()["members"]["m20"]["capability_version"] == 2
    assert old["members"]["m20"]["capability_version"] == 1
    with pytest.raises(ValueError):
        state.capability_change("m20", replacement, capability_version=2, time_s=2)


def test_capability_loss_marks_in_flight_objective_and_does_not_reassign_it():
    state = lifecycle(requirements=[req("arm", manipulation_interface=True)])
    state.record_pickup("crate", ["m20"], time_s=0.5)
    state.assign("carry", "arm", ["m20"], time_s=1, carried_object_id="crate")
    state.start_objective("carry", time_s=2)
    event = state.capability_change("m20", member("m20")["capabilities"], capability_version=2, time_s=3)
    assert event["payload"]["affected_objective_ids"] == ["carry"]
    snapshot = state.snapshot()
    assert snapshot["objectives"]["carry"]["status"] == "affected"
    assert snapshot["object_ownership"] == {"crate": ["m20"]}
    assert not state.qualified_members("arm")
    with pytest.raises(ValueError, match="affected"):
        state.assign("carry", "arm", ["carter"], time_s=4, carried_object_id="crate")


def test_exit_reports_all_affected_objectives_and_requires_runtime_release():
    state = lifecycle(requirements=[req("joint", skill="joint_transport"), req("idle")])
    state.record_pickup("crate", ["m20", "carter"], time_s=0.5)
    state.assign("joint_move", "joint", ["m20", "carter"], time_s=1, carried_object_id="crate")
    state.start_objective("joint_move", time_s=2)
    state.assign("idle_move", req("idle"), ["carter"], time_s=3)
    event = state.exit("m20", time_s=4)
    payload = event["payload"]
    assert payload["affected_objective_ids"] == ["joint_move"]
    assert payload["release_required"] == [{
        "object_id": "crate", "carrier_id": "m20", "status": "release_required",
        "runtime_action": "release_at_runtime_carrier_pose", "ownership_transferred": False,
    }]
    assert payload["replacement_allowed"] is False
    assert state.snapshot()["object_ownership"] == {"crate": ["m20", "carter"]}
    assert state.qualified_members("idle") == ["carter"]
    with pytest.raises(ValueError, match="active member"):
        state.exit("m20", time_s=5)


def test_runtime_release_clears_ownership_without_assigning_a_new_owner():
    state = lifecycle()
    state.record_pickup("crate", ["m20"], time_s=0.5)
    state.assign("carry", "move", ["m20"], time_s=1, carried_object_id="crate")
    state.start_objective("carry", time_s=2)
    event = state.release_object("crate", carrier_id="m20", pose_xyz=[1, 2, .3], time_s=3)
    assert event["type"] == "object_release_required_runtime"
    assert event["payload"]["runtime_release_recorded"] is True
    assert event["payload"]["ownership_transferred"] is False
    assert state.snapshot()["object_ownership"] == {}
    assert state.snapshot()["objectives"]["carry"]["release_status"] == "released"
    with pytest.raises(ValueError, match="not carried"):
        state.release_object("crate", carrier_id="carter", pose_xyz=[0, 0, 0], time_s=4)


def test_same_team_requirement_is_dynamic_and_exact():
    state = lifecycle(requirements=[req("first"), req("second", same_team_as="first")])
    state.assign("first", "first", ["m20"], time_s=1)
    with pytest.raises(ValueError, match="same_team_as"):
        state.assign("second", "second", ["carter"], time_s=2)
    state.assign("second", "second", ["m20"], time_s=2)
    assert state.snapshot()["objectives"]["second"]["agent_ids"] == ["m20"]


def test_apply_event_is_participant_session_friendly_and_rejects_future_or_bad_fields():
    state = lifecycle()
    state.apply_event({"type": "reserve_registered", "time_s": 1,
                       "payload": {"member": member("backup")}})
    state.apply_event({"type": "member_join", "time_s": 2,
                       "payload": {"member_id": "backup"}})
    with pytest.raises(ValueError, match="monotonic"):
        state.apply_event({"type": "member_exit", "time_s": 1,
                           "payload": {"member_id": "backup"}})
    with pytest.raises(ValueError, match="exactly"):
        state.apply_event({"type": "member_exit", "time_s": 3,
                           "payload": {"member_id": "backup", "future_time_s": 99}})
    with pytest.raises(ValueError, match="unsupported lifecycle event"):
        state.apply_event({"type": "member_join_scheduled", "time_s": 3,
                           "payload": {"member_id": "backup"}})


def test_bad_inputs_fail_closed_without_partial_state_or_ledger_changes():
    state = lifecycle()
    before = state.snapshot(), state.ledger()
    bad_events = [
        {"type": "member_join", "time_s": 1, "payload": {"member_id": "missing"}},
        {"type": "capability_change", "time_s": 1,
         "payload": {"member_id": "m20", "capabilities": {}, "capability_version": 2, "extra": 1}},
        {"type": "member_exit", "time_s": -1, "payload": {"member_id": "m20"}},
    ]
    for event in bad_events:
        with pytest.raises(ValueError):
            state.apply_event(event)
        assert (state.snapshot(), state.ledger()) == before


def test_input_and_output_ledger_are_deep_copied():
    capabilities = {"domain": "ground", "navigation_interface": True,
                    "attachment_types": []}
    state = TeamLifecycle([{"id": "m20", "capabilities": capabilities}], [req()])
    capabilities["domain"] = "aerial"
    assert state.snapshot()["members"]["m20"]["capabilities"]["domain"] == "ground"
    ledger = state.ledger()
    ledger[0]["payload"]["active_member_ids"].append("forged")
    assert "forged" not in state.ledger()[0]["payload"]["active_member_ids"]


def test_no_physical_join_claim_is_present_in_state_or_ledger():
    state = lifecycle()
    state.register_reserve(member("backup"), time_s=1)
    event = state.join("backup", time_s=2)
    encoded = json.dumps({"event": event, "state": state.snapshot()})
    assert "isaac" not in encoded.lower()
    assert "physical" not in encoded.lower()
    assert state.snapshot()["members"]["backup"]["status"] == "active"


def test_regressions_are_fail_closed_and_public_events_match_session_contract():
    state = lifecycle()
    with pytest.raises(ValueError):
        state.capability_change("m20", {"x": float("nan")}, capability_version=2, time_s=1)
    state.exit("m20", time_s=1)
    before = state.snapshot(), state.ledger()
    with pytest.raises(ValueError, match="active member"):
        state.capability_change("m20", member("m20")["capabilities"], capability_version=2, time_s=2)
    assert (state.snapshot(), state.ledger()) == before
    with pytest.raises(ValueError):
        state.record_pickup("bad", ["carter"], time_s=0)

    state = lifecycle()
    state.register_reserve(member("backup"), time_s=1)
    joined = state.public_event({"type": "member_join", "time_s": 2,
                                 "payload": {"member_id": "backup"}})
    assert set(joined["payload"]) == {"agent_id", "capability_declaration", "capability_version"}
    changed = state.public_event({"type": "capability_change", "time_s": 3,
        "payload": {"member_id": "backup", "capabilities": member("backup", nav=False)["capabilities"],
                     "capability_version": 2}})
    assert set(changed["payload"]) == {"agent_id", "capability_declaration", "capability_version", "affected_node_ids"}
    with pytest.raises(ValueError, match="evaluator-only"):
        state.public_event({"type": "object_release_required_runtime", "time_s": 4,
                            "payload": {"object_id": "x", "carrier_id": "backup", "pose_xyz": [0, 0, 1]}})


def test_replay_validate_rebuilds_from_visible_command_log():
    state = lifecycle()
    state.register_reserve(member("backup"), time_s=1)
    state.join("backup", time_s=2)
    commands = state.ledger()[1:]
    replayed = TeamLifecycle.replay_validate([member("m20"), member("carter"), member("drone", domain="aerial")], [req()], commands)
    assert replayed["members"]["backup"]["status"] == "active"


def test_public_snapshot_hides_reserve_and_termination_is_not_success():
    state = lifecycle()
    state.register_reserve(member("backup"), time_s=1)
    assert "reserve_pool" in state.snapshot()
    assert "reserve_pool" not in state.public_snapshot()
    state.assign("task", "move", ["m20"], time_s=2)
    event = state.terminate_objective("task", time_s=3)
    assert event["payload"]["status"] == "terminated"
    assert state.snapshot()["objectives"]["task"]["status"] == "terminated"


def test_full_report_replays_commands_and_rejects_forged_state():
    state = lifecycle()
    state.record_pickup("crate", ["m20"], time_s=1)
    state.assign("carry", "move", ["m20"], time_s=2, carried_object_id="crate")
    state.start_objective("carry", time_s=3)
    state.terminate_objective("carry", time_s=4)
    report = state.report()
    assert TeamLifecycle.validate_lifecycle_report(report)["objectives"]["carry"]["status"] == "terminated"
    forged = deepcopy(report)
    forged["snapshot"]["time_s"] = 999
    with pytest.raises(ValueError, match="does not match replay"):
        TeamLifecycle.validate_lifecycle_report(forged)


def test_capability_loss_reports_release_requirement_and_termination_removes_in_flight():
    state = TeamLifecycle([member("m20", arms=("z1",)), member("carter", arms=("ur5",)),
                           member("drone", domain="aerial")], [req("arm", manipulation_interface=True)])
    state.record_pickup("crate", ["m20"], time_s=1)
    state.assign("carry", "arm", ["m20"], time_s=2, carried_object_id="crate")
    state.start_objective("carry", time_s=3)
    event = state.capability_change("m20", member("m20")["capabilities"], capability_version=2, time_s=4)
    assert event["payload"]["release_required"][0]["ownership_transferred"] is False
    state.terminate_objective("carry", time_s=5)
    state.assign("new", "arm", ["carter"], time_s=6)
    assert state.snapshot()["object_ownership"] == {"crate": ["m20"]}
