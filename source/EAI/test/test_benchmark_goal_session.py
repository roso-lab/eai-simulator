"""State-machine tests, using explicit test receipts rather than fake execution.

No Isaac/ROS backend is invoked here; these checks cannot establish physical
conformance. Warehouse declarations exercise the same pure validators used by
the real session integration.
"""

from copy import deepcopy
import json
import math

import pytest

from demo.benchmark.goal_driven_contract import build_goal_participant_api, validate_goal_event
from demo.benchmark.goal_driven_tasks import build_goal_task_package, build_goal_task_spec
from demo.benchmark.goal_session import GoalSession


GROUND = "warehouse_ground_1"
CARTER = "warehouse_ground_2"
OTHER = "warehouse_ground_3"
AIR = "warehouse_aerial_1"


@pytest.fixture
def spec():
    return build_goal_task_spec("warehouse_nvd")


def _task(tid="nav", *, agent=GROUND, start=0.0, duration=8.0,
          route="consolidation_to_rack_a", destination="rack_aisle_a", deps=(), refs=()):
    return {"task_id": tid, "skill": "ground_navigation",
            "arguments": {"region_id": destination, "route_ids": [route]},
            "required_capabilities": ["ground_navigation"], "assigned_agents": [agent],
            "prerequisites": list(deps), "evidence_refs": list(refs),
            "execution": {"start_s": start, "duration_s": duration}}


def _observe(tid="observe", *, agent=GROUND, region="rack_aisle_b", start=0.0, refs=()):
    row = _task(tid, agent=agent, start=start, duration=4.0, refs=refs)
    row.update({"skill": "observe", "required_capabilities": ["observe"],
                "arguments": {"region_id": region}})
    return row


def _pick(*, start=0.0):
    row = _task("pick", start=start, duration=4.0)
    row.update({"skill": "pick", "required_capabilities": ["pick"], "arguments": {
        "object_id": "order_carton_01", "pickup_region_id": "rack_aisle_b",
        "handling_mode_id": "single_grip", "carrier_bindings": [
            {"agent_id": GROUND, "point_id": "center_grip"}],
    }})
    return row


def _plan(*tasks, pid="plan-1", team=None):
    return {"schema": "eai.benchmark.goal_plan", "version": 3, "plan_id": pid,
            "assigned_team": team or sorted({a for t in tasks for a in t["assigned_agents"]}),
            "tasks": list(tasks)}


def _action(session, type, payload, *, aid=None):
    report = session.report()
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": aid or f"action-{len(report['actions'])}",
            "submitted_at_s": report["current_time_s"], "type": type, "payload": payload}


def _submit(session, type, payload, *, aid=None):
    return session.submit_action(_action(session, type, payload, aid=aid))


def _ready(spec, *tasks, team=None):
    session = GoalSession(spec)
    plan = _plan(*tasks, team=team)
    _submit(session, "select_team", {"agent_ids": plan["assigned_team"]})
    _submit(session, "submit_plan", {"plan": plan})
    return session


def _dispatch(session, tid="nav", *, refs=(), aid=None):
    return _submit(session, "dispatch_skill", {"task_id": tid, "evidence_refs": list(refs)}, aid=aid)


def _objects(spec):
    return {o["id"]: {"region_id": o["physical_properties"]["initial_region_id"]}
            for o in spec["public_world"]["objects"]}


def _snapshot(session, spec, now, positions=None, objects=None):
    return session.update_runtime_state(now, positions or {GROUND: "consolidation_area"},
                                        _objects(spec) if objects is None else objects)


def _receipt(session, tid="nav", *, now=5.08, status="succeeded", ref=None):
    return {"backend_id": "unit-test-receipt-source", "receipt_ref": ref or f"test-only/{tid}",
            "task_id": tid, "action_id": session.report()["tasks"][tid]["dispatch_action_id"],
            "status": status, "completed_at_s": now, "evidence_refs": [f"test-only/{tid}/arrival"]}


def _complete(session, tid="nav", *, now=5.08, status="succeeded", ref=None):
    return session.complete_skill(tid, time_s=now, status=status,
                                  receipt=_receipt(session, tid, now=now, status=status, ref=ref))


def _tick(session, now):
    return session.emit("tick", now, {"state_digest": f"test-state-{now}"})


def _revise(session, plan, *, replaced=None, trigger=None):
    report = session.report()
    return _submit(session, "revise_plan", {
        "plan": plan, "replaced_plan_id": replaced or report["current_plan_id"],
        "trigger_event_sequence": [report["events"][-1]["sequence"]] if trigger is None else trigger,
        "reason": "current runtime requires remaining-work replanning",
    })


def _observation(session, *, now=0.0, until=20.0, mid="observation-1"):
    return session.emit("observation", now, {
        "message_id": mid, "producer_id": AIR, "kind": "rack_identity",
        "data": {"object_id": "order_carton_01"}, "valid_until_s": until,
    })


def _change(session, spec, *, agent=GROUND, now=1.0, version=1, remove=None, payload=None):
    declaration = deepcopy(next(r for r in spec["candidate_robot_pool"] if r["id"] == agent))
    if remove:
        declaration["public_capabilities"].remove(remove)
    if payload is not None:
        declaration["physical_profile"]["effective_payload_kg"] = payload
    return session.emit("capability_change", now, {
        "agent_id": agent, "capability_declaration": declaration, "capability_version": version,
    })


def test_carter_only_snapshot_early_arrival_does_not_certify_business(spec):
    session = _ready(spec, _task(agent=CARTER, duration=30.0))
    _snapshot(session, spec, 0.0, {CARTER: "consolidation_area"})
    result = _dispatch(session)
    binding = result["pending_task"]
    assert binding["task"]["assigned_agents"] == [CARTER]
    assert binding["plan_id"] == "plan-1" and binding["plan_version"] == 1
    assert binding["dispatch_action_id"] == result["action_id"]
    assert binding["status"] == "pending" and result["execution_validated"] is False
    assert _complete(session, now=5.08)["status"] == "succeeded"
    report = session.report()
    assert report["current_time_s"] == 5.08
    assert report["tasks"]["nav"]["completed_at_s"] == 5.08
    assert report["runtime_state"]["robot_regions"] == {CARTER: "consolidation_area"}
    assert report["runtime_snapshot_refresh_required"] is True
    assert all(r["status"] == "unassessed" for r in report["business_outcomes"])
    assert report["execution_validated"] is report["business_outcomes_validated"] is report["scoreable"] is False
    json.dumps(report, allow_nan=False)
    api = build_goal_participant_api(spec)
    for event in report["events"]:
        validate_goal_event(event, api=api, task_spec=spec, current_time_s=5.08)


def test_team_selection_is_required_and_must_match_plan(spec):
    session = GoalSession(spec)
    with pytest.raises(ValueError, match="select_team"):
        _submit(session, "submit_plan", {"plan": _plan(_task())})
    _submit(session, "select_team", {"agent_ids": [CARTER]})
    with pytest.raises(ValueError, match="select_team"):
        _submit(session, "submit_plan", {"plan": _plan(_task())})


def test_participant_cannot_advance_time_or_smuggle_runtime_snapshot(spec):
    session = _ready(spec, _task(start=2.0))
    before = session.report()
    action = _action(session, "dispatch_skill", {"task_id": "nav", "evidence_refs": []})
    action["submitted_at_s"] = 2.0
    with pytest.raises(ValueError, match="simulation time"):
        session.submit_action(action)
    action["submitted_at_s"] = 0.0
    action["payload"]["robot_regions"] = {GROUND: "consolidation_area"}
    with pytest.raises(ValueError, match="fields mismatch"):
        session.submit_action(action)
    assert session.report() == before


def test_accepted_action_ids_are_global_and_task_dispatch_is_single_use(spec):
    session = _ready(spec, _task())
    action = _action(session, "dispatch_skill", {"task_id": "nav", "evidence_refs": []}, aid="dispatch")
    session.submit_action(action)
    before = session.report()
    with pytest.raises(ValueError, match="action_id"):
        session.submit_action(action)
    with pytest.raises(ValueError, match="already been dispatched"):
        _dispatch(session, aid="different-action")
    assert session.report() == before
    _complete(session)
    with pytest.raises(ValueError, match="completed task"):
        _dispatch(session)


@pytest.mark.parametrize("missing", [None, "backend_id", "receipt_ref", "task_id", "action_id",
                                     "status", "completed_at_s", "evidence_refs", "empty_evidence"])
def test_missing_backend_evidence_keeps_request_pending_without_advancing_time(spec, missing):
    session = _ready(spec, _task())
    _dispatch(session)
    receipt = _receipt(session)
    if missing is None:
        receipt = None
    elif missing == "empty_evidence":
        receipt["evidence_refs"] = []
    else:
        receipt.pop(missing)
    before = session.report()
    result = session.complete_skill("nav", time_s=5.08, status="succeeded", receipt=receipt)
    assert result["accepted"] is False and result["status"] == "pending"
    assert session.report() == before


@pytest.mark.parametrize("field,value", [("task_id", "other"), ("action_id", "other-action"),
                                         ("status", "failed"), ("completed_at_s", 3.0),
                                         ("backend_id", " "), ("evidence_refs", "path")])
def test_mismatched_or_malformed_backend_receipt_is_rejected_transactionally(spec, field, value):
    session = _ready(spec, _task())
    _dispatch(session)
    receipt = _receipt(session)
    receipt[field] = value
    before = session.report()
    with pytest.raises(ValueError):
        session.complete_skill("nav", time_s=5.08, status="succeeded", receipt=receipt)
    assert session.report() == before


def test_unbound_or_replayed_completion_is_rejected(spec):
    session = _ready(spec, _task())
    with pytest.raises(ValueError, match="pending dispatched"):
        _complete(session)
    _dispatch(session)
    _complete(session)
    with pytest.raises(ValueError, match="pending dispatched"):
        _complete(session)


def test_action_result_event_and_clock_do_not_complete_pending_skill(spec):
    session = _ready(spec, _task())
    result = _dispatch(session)
    session.emit("action_result", 8.0, {"action_id": result["action_id"], "status": "succeeded",
                                        "reason": "external status without backend evidence"})
    _tick(session, 9.0)
    assert session.report()["tasks"]["nav"]["status"] == "pending"
    assert session.report()["tasks"]["nav"]["reservations"]
    with pytest.raises(ValueError, match="time window"):
        _complete(session, now=9.0)
    assert _complete(session, now=9.0, status="failed")["status"] == "failed"


@pytest.mark.parametrize("now", [0.0, 8.0, 12.0])
def test_dispatch_respects_half_open_window_and_does_not_advance_clock(spec, now):
    session = _ready(spec, _task(start=4.0, duration=8.0))
    _snapshot(session, spec, now)
    before = session.report()
    # t=0 is too early; t=8 leaves less than the contract's admissible budget;
    # t=12 is the excluded end boundary.
    with pytest.raises(ValueError, match="time window|skill minimum"):
        _dispatch(session)
    assert session.report() == before


def test_late_dispatch_with_sufficient_reserved_budget_and_early_completion(spec):
    session = _ready(spec, _task(duration=20.0))
    _snapshot(session, spec, 2.0)
    _dispatch(session)
    assert _complete(session, now=3.0)["status"] == "succeeded"


@pytest.mark.parametrize("terminal", [None, "failed", "succeeded"])
def test_dependency_requires_trusted_success_and_current_robot_position(spec, terminal):
    first = _task()
    second = _task("return", start=8.0, route="rack_a_to_consolidation",
                   destination="consolidation_area", deps=("nav",))
    session = _ready(spec, first, second)
    _dispatch(session)
    if terminal:
        _complete(session, now=5.0, status=terminal)
    _snapshot(session, spec, 8.0, {GROUND: "rack_aisle_a"})
    if terminal == "succeeded":
        assert _dispatch(session, "return")["pending_task"]["status"] == "pending"
    else:
        with pytest.raises(ValueError, match="prerequisite"):
            _dispatch(session, "return")


def test_pick_receipt_does_not_invent_ownership_for_place(spec):
    place = _task("place", start=4.0, duration=8.0, deps=("pick",))
    place.update({"skill": "place", "required_capabilities": ["place"], "arguments": {
        "object_id": "order_carton_01", "placement_region_id": "rack_aisle_b"}})
    session = _ready(spec, _pick(), place)
    _snapshot(session, spec, 0, {GROUND: "rack_aisle_b"})
    _dispatch(session, "pick")
    _complete(session, "pick", now=2.0)
    _snapshot(session, spec, 4.0, {GROUND: "rack_aisle_b"})
    with pytest.raises(ValueError, match="ownership"):
        _dispatch(session, "place")
    objects = _objects(spec)
    objects["order_carton_01"].update({"handling_mode_id": "single_grip", "carrier_bindings": [
        {"agent_id": GROUND, "point_id": "center_grip"}]})
    _snapshot(session, spec, 4.0, {GROUND: "rack_aisle_b"}, objects)
    assert _dispatch(session, "place")["accepted"]


@pytest.mark.parametrize("stage", ["submit", "dispatch"])
def test_nonzero_time_requires_trusted_snapshot_and_never_reinfers_spawn(spec, stage):
    session = GoalSession(spec)
    _submit(session, "select_team", {"agent_ids": [GROUND]})
    if stage == "dispatch":
        _submit(session, "submit_plan", {"plan": _plan(_task(start=1.0))})
    _tick(session, 1.0)
    with pytest.raises(ValueError, match="snapshot"):
        if stage == "submit":
            _submit(session, "submit_plan", {"plan": _plan(_task(start=1.0))})
        else:
            _dispatch(session)
    _snapshot(session, spec, 1.0, {GROUND: "outbound_dock"})
    with pytest.raises(ValueError, match="continuity"):
        if stage == "submit":
            _submit(session, "submit_plan", {"plan": _plan(_task(start=1.0))})
        else:
            _dispatch(session)


def test_stale_snapshot_or_missing_selected_position_is_not_current_state(spec):
    session = _ready(spec, _task(start=1.0))
    _snapshot(session, spec, 0.0)
    _tick(session, 1.0)
    with pytest.raises(ValueError, match="fresh trusted"):
        _dispatch(session)
    _snapshot(session, spec, 1.0, {CARTER: "consolidation_area"})
    with pytest.raises(ValueError, match="positions"):
        _dispatch(session)


def test_invalid_state_updates_are_atomic_and_require_complete_object_inventory(spec):
    session = GoalSession(spec)
    before = session.report()
    with pytest.raises(ValueError, match="complete"):
        session.update_runtime_state(1.0, {}, {})
    with pytest.raises(ValueError, match="unknown"):
        _snapshot(session, spec, 1.0, {"unknown": "rack_aisle_b"})
    with pytest.raises(ValueError, match="mobility"):
        _snapshot(session, spec, 1.0, {AIR: "rack_aisle_b"})
    objects = _objects(spec)
    objects["order_carton_01"]["completed_task"] = "pick"
    with pytest.raises(ValueError, match="fields"):
        _snapshot(session, spec, 1.0, objects=objects)
    assert session.report() == before


def test_runtime_state_preflight_never_changes_ledger(spec):
    session = _ready(spec, _task())
    _dispatch(session)
    objects = _objects(spec)
    before = session.report()
    snapshot = session.validate_runtime_state(
        1.0, {GROUND: "consolidation_area"}, objects)
    assert snapshot["time_s"] == 1.0
    snapshot["object_states"]["order_crate_01"]["region_id"] = "rack_aisle_a"
    assert session.report() == before

    invalid_binding = deepcopy(objects)
    invalid_binding["order_carton_01"] = {
        "region_id": "rack_aisle_b", "handling_mode_id": "single_grip",
        "carrier_bindings": [{"agent_id": GROUND, "point_id": "unbound_point"}],
    }
    for time_s, positions, states in (
            (-1.0, {GROUND: "consolidation_area"}, objects),
            (math.inf, {GROUND: "consolidation_area"}, objects),
            (1.0, {GROUND: "consolidation_area"}, {}),
            (1.0, {GROUND: "rack_aisle_b"}, invalid_binding)):
        with pytest.raises(ValueError):
            session.validate_runtime_state(time_s, positions, states)
        assert session.report() == before


def test_observation_consumption_is_current_for_dispatch_but_history_is_readable(spec):
    session = GoalSession(spec)
    _observation(session, until=4.0)
    _submit(session, "select_team", {"agent_ids": [GROUND]})
    _submit(session, "submit_plan", {"plan": _plan(_observe(start=4.0, refs=("observation-1",)))})
    _snapshot(session, spec, 4.0, {GROUND: "rack_aisle_b"})
    _submit(session, "consume_message", {"message_id": "observation-1"})
    with pytest.raises(ValueError, match="expired"):
        _dispatch(session, "observe")
    with pytest.raises(ValueError, match="already been consumed"):
        _submit(session, "consume_message", {"message_id": "observation-1"})


def test_dispatch_must_consume_declared_and_added_evidence_refs(spec):
    session = GoalSession(spec)
    _observation(session)
    _observation(session, mid="observation-2")
    _submit(session, "select_team", {"agent_ids": [GROUND]})
    _submit(session, "submit_plan", {"plan": _plan(_observe(refs=("observation-1",)))})
    _submit(session, "consume_message", {"message_id": "observation-2"})
    with pytest.raises(ValueError, match="consumed"):
        _dispatch(session, "observe", refs=("observation-2",))
    _submit(session, "consume_message", {"message_id": "observation-1"})
    assert _dispatch(session, "observe", refs=("observation-2",))["accepted"]


def test_delivered_observation_ids_are_unique_and_private_fields_are_rejected(spec):
    session = GoalSession(spec)
    _observation(session)
    before = session.report()
    with pytest.raises(ValueError, match="already been delivered"):
        _observation(session, now=1.0)
    with pytest.raises(ValueError, match="private"):
        session.emit("observation", 1.0, {"message_id": "leak", "producer_id": AIR,
                    "kind": "evidence", "data": {"reference_plan": {}}, "valid_until_s": 9.0})
    with pytest.raises(ValueError, match="delivered visible"):
        _submit(session, "consume_message", {"message_id": "never-delivered"})
    assert session.report() == before


@pytest.mark.parametrize("private_key", ["hidden_event_schedule", "witness_plans"])
@pytest.mark.parametrize("json_round_trip", [False, True])
def test_tuple_nested_private_observation_is_rejected_without_session_mutation(private_key, json_round_trip):
    bundle = build_goal_task_package("modern_house_nvd")
    session = GoalSession(bundle["public_task"])
    before = session.report()
    payload = {
        "message_id": "tuple-leak", "producer_id": "sensor", "kind": "diagnostic",
        "data": {"rows": ({"nested": [({private_key: deepcopy(bundle["hidden_evaluator"][private_key])},)]},)},
        "valid_until_s": 2.,
    }
    if json_round_trip:
        payload = json.loads(json.dumps(payload))
    with pytest.raises(ValueError, match="private fields"):
        session.emit("observation", 1., payload)
    assert session.report() == before
    # Rejection must not consume the message ID, clock, or event sequence.
    payload["data"] = {"public_subject": bundle["public_task"]["public_world"]["objects"][0]["id"]}
    accepted = session.emit("observation", 1., payload)
    assert accepted["payload"]["message_id"] == "tuple-leak"


@pytest.mark.parametrize("change", ["member_exit", "skill", "payload"])
def test_dispatch_revalidates_current_roster_capabilities_and_payload(spec, change):
    session = _ready(spec, _pick(start=2.0))
    if change == "member_exit":
        session.emit("member_exit", 1.0, {"agent_id": GROUND})
    else:
        _change(session, spec, remove="pick" if change == "skill" else None,
                payload=1.0 if change == "payload" else None)
    _snapshot(session, spec, 2.0, {GROUND: "rack_aisle_b"})
    if change == "member_exit":
        # Structural (the exited agent no longer exists in the roster), not
        # capability policing - stays fail-closed.
        with pytest.raises(ValueError, match="team"):
            _dispatch(session, "pick")
    else:
        # User principle (2026-09-22): no protocol-side capability policing -
        # skill/payload mismatches are admitted and surface as
        # execution-layer failures.
        assert _dispatch(session, "pick")["accepted"]


@pytest.mark.parametrize("event_type", ["member_exit", "capability_change"])
def test_running_skill_invalidation_retains_resources_and_rejects_silent_success(spec, event_type):
    session = _ready(spec, _task())
    _dispatch(session)
    if event_type == "member_exit":
        event = session.emit(event_type, 1.0, {"agent_id": GROUND})
    else:
        event = _change(session, spec)
    assert session.report()["tasks"]["nav"]["invalidated_by"] == [event["sequence"]]
    assert session.report()["tasks"]["nav"]["reservations"]
    with pytest.raises(ValueError, match="invalidated"):
        _complete(session, now=2.0)
    assert _complete(session, now=2.0, status="failed")["status"] == "failed"


def test_lifecycle_versions_and_identity_cannot_be_replayed(spec):
    session = GoalSession(spec)
    _change(session, spec, version=2)
    before = session.report()
    for version in (1, 2):
        with pytest.raises(ValueError, match="strictly increase"):
            _change(session, spec, version=version)
    assert session.report() == before
    session.emit("member_exit", 1.0, {"agent_id": GROUND})
    declaration = deepcopy(next(r for r in spec["candidate_robot_pool"] if r["id"] == GROUND))
    session.emit("member_join", 2.0, {"agent_id": GROUND, "capability_declaration": declaration,
                                      "capability_version": 3})
    assert session.report()["capability_versions"][GROUND] == 3
    with pytest.raises(ValueError, match="active member"):
        session.emit("member_join", 2.0, {"agent_id": GROUND, "capability_declaration": declaration,
                                          "capability_version": 4})
    declaration["type"] = "new-identity"
    with pytest.raises(ValueError, match="identity"):
        session.emit("capability_change", 2.0, {"agent_id": GROUND, "capability_declaration": declaration,
                                                "capability_version": 4})


def test_joined_robot_is_usable_only_after_visible_declaration_and_selection(spec):
    session = GoalSession(spec)
    with pytest.raises(ValueError, match="unavailable"):
        _submit(session, "select_team", {"agent_ids": ["joined"]})
    declaration = deepcopy(spec["candidate_robot_pool"][1])
    declaration["id"] = "joined"
    session.emit("member_join", 1.0, {"agent_id": "joined", "capability_declaration": declaration,
                                      "capability_version": 1})
    _submit(session, "select_team", {"agent_ids": ["joined"]})
    _snapshot(session, spec, 1.0, {"joined": "consolidation_area"})
    _submit(session, "submit_plan", {"plan": _plan(_task(agent="joined", start=1.0))})
    assert _dispatch(session)["accepted"]


def test_cancel_keeps_pending_reservations_until_backend_acknowledgement(spec):
    session = _ready(spec, _task())
    dispatched = _dispatch(session)
    cancelled = _submit(session, "cancel_skill", {"task_id": "nav", "reason": "replan"})
    row = cancelled["pending_task"]
    assert row["status"] == "cancel_requested" and row["reservations"]
    assert row["dispatch_action_id"] == dispatched["action_id"]
    with pytest.raises(ValueError, match="already been requested"):
        _submit(session, "cancel_skill", {"task_id": "nav", "reason": "again"})
    with pytest.raises(ValueError, match="cancel-requested"):
        _complete(session, now=1.0)
    with pytest.raises(ValueError, match="deselect"):
        _submit(session, "select_team", {"agent_ids": [CARTER]})
    receipt = _receipt(session, now=1.0, status="cancelled")
    receipt["action_id"] = cancelled["action_id"]
    with pytest.raises(ValueError, match="task/action"):
        session.complete_skill("nav", time_s=1.0, status="cancelled", receipt=receipt)
    _complete(session, now=1.0, status="cancelled")
    assert not session.report()["tasks"]["nav"]["reservations"]
    _submit(session, "select_team", {"agent_ids": [CARTER]})


def test_unfinished_route_reservation_blocks_other_robot_even_after_planned_end(spec):
    session = _ready(spec, _task(), _task("other-nav", agent=OTHER, start=8.0,
                     route="rack_a_to_consolidation", destination="consolidation_area"))
    _dispatch(session)
    _snapshot(session, spec, 8.0, {GROUND: "consolidation_area", OTHER: "rack_aisle_a"})
    with pytest.raises(ValueError, match="resource conflict: route crate_aisle"):
        _dispatch(session, "other-nav")
    _complete(session, now=8.0, status="failed")
    with pytest.raises(ValueError, match="fresh trusted"):
        _dispatch(session, "other-nav")
    _snapshot(session, spec, 8.0, {OTHER: "rack_aisle_a"})
    assert _dispatch(session, "other-nav")["accepted"]


def test_unfinished_operation_holds_region_capacity_across_nominal_windows(spec):
    session = _ready(spec, _observe("first"), _observe("next", agent=OTHER, start=4.0))
    _dispatch(session, "first")
    _snapshot(session, spec, 4.0, {GROUND: "rack_aisle_b", OTHER: "rack_aisle_b"})
    with pytest.raises(ValueError, match="resource conflict: region rack_aisle_b"):
        _dispatch(session, "next")


def test_shared_route_capacity_two_allows_two_concurrent_actions(spec):
    for route in spec["public_world"]["routes"]:
        if route["resource_id"] == "crate_aisle":
            route["capacity"] = 2
    session = _ready(spec, _task(), _task("other-nav", agent=OTHER))
    _dispatch(session)
    assert _dispatch(session, "other-nav")["accepted"]


def test_revision_keeps_inflight_ledger_and_reservations_and_rejects_reexecution(spec):
    session = _ready(spec, _task(), team=[GROUND, OTHER])
    _dispatch(session)
    _snapshot(session, spec, 1.0, {GROUND: "consolidation_area", OTHER: "consolidation_area"})
    _revise(session, _plan(_task("new-nav", agent=OTHER, start=1.0),
                          pid="plan-2", team=[GROUND, OTHER]))
    report = session.report()
    assert report["plan_version"] == 2
    assert report["tasks"]["nav"]["plan_id"] == "plan-1"
    assert report["tasks"]["nav"]["status"] == "pending"
    with pytest.raises(ValueError, match="resource conflict"):
        _dispatch(session, "new-nav")
    with pytest.raises(ValueError, match="dispatched task_id"):
        _revise(session, _plan(_task(start=1.0), pid="plan-3", team=[GROUND, OTHER]))
    _submit(session, "cancel_skill", {"task_id": "nav", "reason": "release old request"})


def test_revision_rejects_stale_plan_id_invisible_trigger_and_reused_plan_id(spec):
    session = _ready(spec, _task(start=10.0))
    _snapshot(session, spec, 1.0)
    with pytest.raises(ValueError, match="currently visible"):
        _revise(session, _plan(_task(start=10.0), pid="plan-2"), trigger=[999])
    _revise(session, _plan(_task(start=10.0), pid="plan-2"))
    before = session.report()
    with pytest.raises(ValueError, match="current plan version"):
        _revise(session, _plan(_task(start=10.0), pid="plan-3"), replaced="plan-1")
    with pytest.raises(ValueError, match="plan_id must be new"):
        _revise(session, _plan(_task(start=10.0), pid="plan-1"))
    with pytest.raises(ValueError, match="revise_plan"):
        _submit(session, "submit_plan", {"plan": _plan(_task(start=10.0), pid="plan-3")})
    assert session.report() == before


def test_receipt_reference_cannot_be_rebound_to_new_task(spec):
    session = _ready(spec, _task())
    _dispatch(session)
    _complete(session, now=1.0, ref="same-receipt")
    _snapshot(session, spec, 1.0, {GROUND: "rack_aisle_a"})
    _revise(session, _plan(_task("new-nav", start=1.0, route="rack_a_to_consolidation",
                                destination="consolidation_area"), pid="plan-2"))
    _dispatch(session, "new-nav")
    with pytest.raises(ValueError, match="already been used"):
        _complete(session, "new-nav", now=2.0, ref="same-receipt")


@pytest.mark.parametrize("bad_time", [-1, True, float("inf"), float("nan")])
def test_trusted_methods_reject_invalid_clock(spec, bad_time):
    session = _ready(spec, _task())
    _dispatch(session)
    before = session.report()
    with pytest.raises(ValueError, match="finite nonnegative"):
        _tick(session, bad_time)
    with pytest.raises(ValueError, match="finite nonnegative"):
        _snapshot(session, spec, bad_time)
    with pytest.raises(ValueError, match="finite nonnegative"):
        _complete(session, now=bad_time)
    assert session.report() == before


def test_trusted_clock_never_regresses(spec):
    session = _ready(spec, _task())
    _dispatch(session)
    _tick(session, 2.0)
    for call in (lambda: _tick(session, 1), lambda: _snapshot(session, spec, 1),
                 lambda: _complete(session, now=1)):
        with pytest.raises(ValueError, match="backwards"):
            call()


def test_inputs_outputs_and_report_cannot_mutate_session_state(spec):
    api = build_goal_participant_api(spec)
    session = GoalSession(spec, api=api)
    spec["candidate_robot_pool"].clear()
    api["version"] = 999
    action = _action(session, "select_team", {"agent_ids": [GROUND]})
    result = session.submit_action(action)
    action["payload"]["agent_ids"].clear()
    result["event"]["payload"]["status"] = "succeeded"
    report = session.report()
    report["selected_team"].clear()
    assert session.report()["selected_team"] == [GROUND]
    assert session.report()["events"][-1]["payload"]["status"] == "accepted"
