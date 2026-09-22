"""Reference effects are distinct from native execution and business outcomes."""

import pytest
from copy import deepcopy

from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.reliable_skill_backend import ReliableSkillReferenceHost
from demo.benchmark.provenance import canonical_sha256

G = "warehouse_ground_1"
C = "warehouse_ground_2"
O = "warehouse_ground_3"
A = "warehouse_aerial_1"


def positions(**overrides):
    return {G: "rack_aisle_b", C: "rack_aisle_a", O: "warehouse_standby_b", A: "rack_overview", **overrides}


@pytest.fixture
def host():
    return ReliableSkillReferenceHost(build_goal_task_spec("warehouse_nvd"), robot_regions=positions())


def task(tid, skill, args, *, agents=(G,), start=0., duration=8., deps=()):
    return {"task_id": tid, "skill": skill, "arguments": args,
            "required_capabilities": [skill], "assigned_agents": list(agents),
            "prerequisites": list(deps), "evidence_refs": [],
            "execution": {"start_s": start, "duration_s": duration}}


def action(host, kind, payload):
    state = host.report()["ledger"]
    return host.submit_action({"schema": "eai.benchmark.goal_action", "version": 1,
                               "action_id": f"a{len(state['actions'])}", "type": kind,
                               "submitted_at_s": state["current_time_s"], "payload": payload})


def prepare(host, tasks):
    team = sorted({a for t in tasks for a in t["assigned_agents"]})
    action(host, "select_team", {"agent_ids": team})
    action(host, "submit_plan", {"plan": {"schema": "eai.benchmark.goal_plan", "version": 3,
        "plan_id": "plan", "assigned_team": team, "tasks": tasks}})


def dispatch(host, tid):
    action(host, "dispatch_skill", {"task_id": tid, "evidence_refs": []})


def occupancy(host, *, blocked_routes=None, blocked_regions=None):
    host.set_occupancy(time_s=host.report()["ledger"]["current_time_s"],
                       blocked_routes=blocked_routes or {}, blocked_regions=blocked_regions or {},
                       evidence_ref="reference-fixture:current-occupancy")


def pick():
    return task("pick", "pick", {"object_id": "order_carton_01", "pickup_region_id": "rack_aisle_b",
                                  "handling_mode_id": "single_grip",
                                  "carrier_bindings": [{"agent_id": G, "point_id": "center_grip"}]}, duration=4.)


def test_complete_initial_robot_inventory_is_required():
    with pytest.raises(ValueError, match="every candidate"):
        ReliableSkillReferenceHost(build_goal_task_spec("warehouse_nvd"), robot_regions={G: "rack_aisle_b"})


def test_pick_loaded_navigation_place_have_committed_independent_effects(host):
    nav = task("nav", "ground_navigation", {"region_id": "consolidation_area",
               "route_ids": ["rack_b_to_consolidation"]}, start=4., deps=("pick",))
    place = task("place", "place", {"object_id": "order_carton_01", "placement_region_id": "consolidation_area"},
                 start=12., deps=("nav",))
    prepare(host, [pick(), nav, place])
    for tid, end in (("pick", 4.), ("nav", 12.), ("place", 20.)):
        dispatch(host, tid)
        occupancy(host)
        result = host.execute_pending(tid, completed_at_s=end)
        assert result["accepted"] and result["execution_origin"] == "deterministic_reference_not_simulator"
        if tid == "pick":
            assert host.state()["object_states"]["order_carton_01"]["carrier_bindings"][0]["agent_id"] == G
    report = host.report()
    assert report["state"]["object_states"]["order_carton_01"] == {"region_id": "consolidation_area"}
    assert report["state"]["robot_regions"][G] == "consolidation_area"
    assert len(report["evidence"]) == 3
    for evidence in report["evidence"]:
        assert evidence["sha256"] == canonical_sha256({k: v for k, v in evidence.items() if k != "sha256"})
        assert evidence["physical_execution_validated"] is False
    assert report["business_outcomes_validated"] is report["scoreable"] is False
    assert all(x["status"] == "unassessed" for x in report["ledger"]["business_outcomes"])


def test_no_predicted_effect_before_dispatch_and_success(host):
    original = host.state()
    prepare(host, [pick()])
    assert host.state() == original
    with pytest.raises(ValueError, match="pending dispatch"):
        host.execute_pending("pick", completed_at_s=4.)
    dispatch(host, "pick")
    assert host.state() == original
    assert host.execute_pending("pick", completed_at_s=4.)["reason"] == "current_occupancy_evidence_required"
    assert host.state() == original


def test_idle_robot_occupancy_holds_without_effects(host):
    prepare(host, [pick()])
    dispatch(host, "pick")
    occupancy(host, blocked_regions={"rack_aisle_b": [O]})
    before = host.report()
    assert host.execute_pending("pick", completed_at_s=4.)["status"] == "held"
    assert host.report() == before
    occupancy(host)
    assert host.execute_pending("pick", completed_at_s=4.)["accepted"]


def test_stale_occupancy_and_invalid_snapshot_are_not_silently_reused(host):
    prepare(host, [task("nav", "ground_navigation", {"region_id": "consolidation_area",
                      "route_ids": ["rack_b_to_consolidation"]}, duration=16.)])
    dispatch(host, "nav")
    occupancy(host)
    host.advance(1.)
    assert host.execute_pending("nav", completed_at_s=9.)["reason"] == "current_occupancy_evidence_required"
    with pytest.raises(ValueError, match="current host time"):
        host.set_occupancy(time_s=0., blocked_routes={}, blocked_regions={}, evidence_ref="old")


@pytest.mark.parametrize("end", [0., 3., 4.1, float("nan")])
def test_timing_failures_are_transactional(host, end):
    prepare(host, [pick()])
    dispatch(host, "pick")
    occupancy(host)
    before = host.report()
    with pytest.raises(ValueError):
        host.execute_pending("pick", completed_at_s=end)
    assert host.report() == before


def test_cancellation_has_no_ownership_effect_and_releases_reservation(host):
    prepare(host, [pick()])
    dispatch(host, "pick")
    original = host.state()["object_states"]
    action(host, "cancel_skill", {"task_id": "pick", "reason": "cancel before attachment"})
    assert host.execute_pending("pick", completed_at_s=0.)["accepted"]
    report = host.report()
    assert report["state"]["object_states"] == original
    assert report["ledger"]["tasks"]["pick"]["status"] == "cancelled"
    assert report["ledger"]["tasks"]["pick"]["reservations"] == []


def test_member_exit_invalidates_pending_skill_without_motion(host):
    prepare(host, [pick()])
    dispatch(host, "pick")
    original = host.state()["object_states"]
    host.emit("member_exit", 1., {"agent_id": G})
    result = host.execute_pending("pick", completed_at_s=1.)
    assert result["accepted"] and result["status"] == "failed"
    assert host.state()["object_states"] == original


def test_cooperative_fixed_points_and_payload_constraints():
    # Current task fixtures use two M20 carriers; the Carter connector is not
    # admitted at this object's support height.
    host = ReliableSkillReferenceHost(build_goal_task_spec("warehouse_nvd"),
                                     robot_regions=positions(**{G: "rack_aisle_a", O: "rack_aisle_a"}))
    carry = task("carry", "cooperative_carry", {"object_id": "order_crate_01",
        "destination_region_id": "consolidation_area", "route_ids": ["rack_a_to_consolidation"],
        "handling_mode_id": "paired_support", "carrier_bindings": [
            {"agent_id": G, "point_id": "support_left"}, {"agent_id": O, "point_id": "support_right"}]},
        agents=(G, O), duration=16.)
    prepare(host, [carry])
    dispatch(host, "carry")
    occupancy(host)
    assert host.execute_pending("carry", completed_at_s=16.)["accepted"]
    assert host.state()["object_states"]["order_crate_01"] == {"region_id": "consolidation_area"}
    assert host.state()["robot_regions"][G] == host.state()["robot_regions"][O] == "consolidation_area"


def test_overweight_single_grasp_is_admitted_then_fails_without_motion(host):
    illegal = task("crate_single", "pick", {
        "object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
        "handling_mode_id": "single_grip",
        "carrier_bindings": [{"agent_id": G, "point_id": "center_grip"}],
    }, duration=4.)
    host._robots[G] = "rack_aisle_a"
    host.advance(0.)
    prepare(host, [illegal])
    dispatch(host, "crate_single")
    occupancy(host)
    before = host.state()
    result = host.execute_pending("crate_single", completed_at_s=4.)
    assert result["accepted"] and result["status"] == "failed"
    assert host.state()["object_states"] == before["object_states"]
    evidence = host.report()["evidence"][-1]
    assert evidence["reason"] == "physical_skill_failure"
    assert evidence["observation"] == {
        "skill_succeeded": False,
        "object_moved": False,
        "task_advanced": False,
        "carrier_load_monitor": [{
            "agent_id": G, "requested_load_kg": 12.0, "effective_payload_kg": 8.0,
        }],
    }
    assert "add another" not in repr(evidence).lower()


def test_failed_single_grasp_can_be_replanned_as_feasible_pair(host):
    illegal = task("crate_single", "pick", {
        "object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
        "handling_mode_id": "single_grip",
        "carrier_bindings": [{"agent_id": G, "point_id": "center_grip"}],
    }, duration=4.)
    host._robots[G] = "rack_aisle_a"
    host._robots[O] = "rack_aisle_a"
    host.advance(0.)
    prepare(host, [illegal])
    dispatch(host, "crate_single")
    occupancy(host)
    assert host.execute_pending("crate_single", completed_at_s=4.)["status"] == "failed"

    carry = task("crate_pair", "cooperative_carry", {
        "object_id": "order_crate_01", "destination_region_id": "consolidation_area",
        "route_ids": ["rack_a_to_consolidation"], "handling_mode_id": "paired_support",
        "carrier_bindings": [
            {"agent_id": G, "point_id": "support_left"},
            {"agent_id": O, "point_id": "support_right"},
        ],
    }, agents=(G, O), start=4., duration=16.)
    action(host, "select_team", {"agent_ids": [G, O]})
    action(host, "revise_plan", {
        "trigger_event_sequence": [len(host.report()["ledger"]["events"]) - 1],
        "replaced_plan_id": "plan", "reason": "observed skill failure",
        "plan": {"schema": "eai.benchmark.goal_plan", "version": 3,
                  "plan_id": "replan", "assigned_team": [G, O], "tasks": [carry]},
    })
    dispatch(host, "crate_pair")
    occupancy(host)
    result = host.execute_pending("crate_pair", completed_at_s=20.)
    assert result["accepted"] and result["status"] == "succeeded"
    assert host.state()["object_states"]["order_crate_01"] == {
        "region_id": "consolidation_area"
    }


def test_member_exit_leaves_single_carrier_attempt_as_observed_capacity_failure(host):
    """A delivered exit must not turn a remaining single attempt into admission failure."""
    host._robots[G] = "rack_aisle_a"
    host._robots[O] = "rack_aisle_a"
    host.advance(0.)
    illegal = task("crate_after_exit", "pick", {
        "object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
        "handling_mode_id": "single_grip",
        "carrier_bindings": [{"agent_id": G, "point_id": "center_grip"}],
    }, duration=4.)
    action(host, "select_team", {"agent_ids": [G, O]})
    action(host, "submit_plan", {"plan": {
        "schema": "eai.benchmark.goal_plan", "version": 3,
        "plan_id": "exit-plan", "assigned_team": [G, O], "tasks": [illegal]}})
    dispatch(host, "crate_after_exit")
    host.emit("member_exit", 0., {"agent_id": O})
    occupancy(host)
    result = host.execute_pending("crate_after_exit", completed_at_s=4.)
    assert result["accepted"] and result["status"] == "failed"
    assert host.report()["evidence"][-1]["reason"] == "physical_skill_failure"
    assert host.report()["evidence"][-1]["observation"]["task_advanced"] is False


def test_observation_and_city_services_cannot_be_fabricated(host):
    observe = task("observe", "observe", {"region_id": "rack_aisle_b"}, duration=4.)
    prepare(host, [observe])
    dispatch(host, "observe")
    occupancy(host)
    assert host.execute_pending("observe", completed_at_s=4.)["reason"] == "external_skill_backend_required"
    assert host.report()["evidence"] == []
    assert "guide_people" not in host.report()["supported_skills"]
    assert "control_following_traffic" not in host.report()["supported_skills"]


def test_returned_state_is_detached(host):
    report = host.report()
    report["state"]["object_states"].clear()
    report["ledger"]["tasks"]["foreign"] = {}
    assert host.state()["object_states"]
    assert host.report()["ledger"]["tasks"] == {}


@pytest.mark.parametrize("at_dispatch", [False, True])
def test_evidence_expiring_while_held_prevents_reference_effect(host, at_dispatch):
    host.emit("observation", 0., {"message_id": "fresh-at-admission", "producer_id": A,
        "kind": "object_identity", "data": {"object_id": "order_carton_01"}, "valid_until_s": 1.})
    pending = pick()
    pending["execution"]["duration_s"] = 8.
    if not at_dispatch:
        pending["evidence_refs"] = ["fresh-at-admission"]
    prepare(host, [pending])
    action(host, "consume_message", {"message_id": "fresh-at-admission"})
    action(host, "dispatch_skill", {"task_id": "pick",
                                    "evidence_refs": ["fresh-at-admission"] if at_dispatch else []})
    occupancy(host, blocked_regions={"rack_aisle_b": [O]})
    assert host.execute_pending("pick", completed_at_s=4.)["status"] == "held"
    host.advance(1.)
    occupancy(host)
    before = host.report()
    with pytest.raises(ValueError, match="expired"):
        host.execute_pending("pick", completed_at_s=5.)
    assert host.report() == before


@pytest.mark.parametrize("region", ["rack_aisle_b", "consolidation_area", "outbound_dock"])
def test_origin_intermediate_and_destination_occupancy_hold_navigation(host, region):
    prepare(host, [task("nav", "ground_navigation", {"region_id": "outbound_dock",
        "route_ids": ["rack_b_to_consolidation", "consolidation_to_dock"]}, duration=16.)])
    dispatch(host, "nav")
    occupancy(host, blocked_regions={region: ["idle-dynamic-actor"]})
    before = host.report()
    assert host.execute_pending("nav", completed_at_s=16.)["status"] == "held"
    assert host.report() == before


@pytest.mark.parametrize("lifecycle", ["cancel", "exit", "capability"])
def test_external_backend_owns_its_cancellation_and_invalidation_receipts(host, lifecycle):
    prepare(host, [task("observe", "observe", {"region_id": "rack_aisle_b"}, duration=4.)])
    dispatch(host, "observe")
    if lifecycle == "cancel":
        action(host, "cancel_skill", {"task_id": "observe", "reason": "external cancellation"})
    elif lifecycle == "exit":
        host.emit("member_exit", 0., {"agent_id": G})
    else:
        declaration = host.report()["ledger"]["current_robot_declarations"][G]
        host.emit("capability_change", 0., {"agent_id": G, "capability_version": 2,
                                           "capability_declaration": declaration})
    before = host.report()
    assert host.execute_pending("observe", completed_at_s=0.)["reason"] == "external_skill_backend_required"
    assert host.report() == before


@pytest.mark.parametrize("dynamic", [False, True])
def test_rejoined_identity_requires_new_region_and_cannot_refresh_old_location(host, dynamic):
    declaration = deepcopy(host.report()["ledger"]["current_robot_declarations"][G])
    member = "new_ground" if dynamic else G
    declaration["id"] = member
    if dynamic:
        host.emit("member_join", 0., {"agent_id": member, "capability_version": 1,
                                     "capability_declaration": declaration})
        host.set_member_region(member, "rack_aisle_b", time_s=0.)
    host.emit("member_exit", 1., {"agent_id": member})
    host.emit("member_join", 2., {"agent_id": member, "capability_version": 2,
                                 "capability_declaration": declaration})
    before = host.report()
    for attempt in (lambda: host.advance(2.), lambda: occupancy(host),
                    lambda: action(host, "select_team", {"agent_ids": [member]})):
        with pytest.raises(ValueError, match="fresh region binding"):
            attempt()
        assert host.report() == before
    assert member not in host.state()["robot_regions"]
    host.set_member_region(member, "consolidation_area", time_s=2.)
    host.advance(2.)
    occupancy(host)
    assert host.state()["robot_regions"][member] == "consolidation_area"
