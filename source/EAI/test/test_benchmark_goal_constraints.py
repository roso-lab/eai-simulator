"""Public numerical contract tests; no simulator, ROS, assets, or hardware."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_constraints import (
    transport_options,
    validate_constraint_model,
    validate_plan_constraints,
    validate_robot_profile,
)
from demo.benchmark.skill_ontology import skill_names


def _robot(identifier, *, payload=8.0, width=0.6, reach=1.0):
    return {
        "id": identifier, "type": "benchmark_carrier", "mobility": "ground",
        "public_capabilities": ["ground_navigation", "stair_traversal", "pick", "place",
                                "handoff", "cooperative_carry", "observe", "synchronize"],
        "interfaces": ["manipulator", "cooperative_transport", "camera", "event_bus"],
        "physical_profile": {
            "configuration_id": f"{identifier}_fixture", "body_size_m": [0.8, width, 0.8],
            "effective_payload_kg": payload, "reach_m": reach,
            "grasp_interfaces": ["fixed_socket"], "max_step_height_m": 0.2,
        },
    }


def _route(identifier, origin, destination, *, width=2.0, resource=None, modes=None, step=0.0):
    return {"id": identifier, "from_region_id": origin, "to_region_id": destination,
            "access_modes": modes or ["ground_navigation"], "length_m": 2.0,
            "clearance_m": [width, 2.0], "max_step_height_m": step,
            "resource_id": resource or identifier, "capacity": 1}


@pytest.fixture
def spec():
    points = [
        {"id": "center", "position_m": [0.0, 0.0, 0.2], "interface": "fixed_socket"},
        {"id": "left", "position_m": [0.0, -0.4, 0.2], "interface": "fixed_socket"},
        {"id": "right", "position_m": [0.0, 0.4, 0.2], "interface": "fixed_socket"},
    ]

    def obj(identifier, mass):
        return {"id": identifier, "physical_properties": {
            "mass_kg": mass, "size_m": [0.8, 0.8, 0.6], "initial_region_id": "rack",
            "grasp_points": deepcopy(points),
            "handling_modes": [
                {"id": "single", "slots": [{"point_id": "center", "load_fraction": 1.0}],
                 "envelope_m": [1.0, 0.9, 1.0]},
                {"id": "paired", "slots": [{"point_id": "left", "load_fraction": 0.5},
                                              {"point_id": "right", "load_fraction": 0.5}],
                 "envelope_m": [1.2, 1.5, 1.0]},
            ],
        }}

    return {
        "execution_contract": {
            "schema": "eai.benchmark.reliable_skills", "version": 1,
            "parameter_basis": "benchmark_defined", "scene_binding_status": "pending",
            "guarantee": "conditional_deterministic", "collision_policy": "hold_and_report",
            "observation_policy": "exact_within_declared_visibility", "clearance_margin_m": 0.05,
            "skill_durations_s": {skill: 1.0 for skill in skill_names()},
        },
        "candidate_robot_pool": [_robot("a"), _robot("b"), _robot("c", payload=20.0)],
        "budgets": {"max_active_agents": 3, "simulation_s": 100.0},
        "public_world": {
            "regions": [{"id": name, "resource_capacity": 2, "access_modes": ["ground_navigation", "stair_traversal"]}
                        for name in ["rack", "buffer", "dock", "upper"]],
            "objects": [obj("carton", 4.0), obj("crate", 12.0)],
            "vertical_links": [{"id": "stair", "from_region_id": "rack", "to_region_id": "upper",
                                "traversal_modes": ["stair_traversal"]}],
            "routes": [
                _route("narrow", "rack", "dock", width=0.8, resource="shared_lane"),
                _route("reverse", "dock", "rack", width=0.8, resource="shared_lane"),
                _route("wide", "rack", "dock"),
                _route("first", "rack", "buffer"),
                _route("second", "buffer", "dock"),
                _route("stairs", "rack", "upper", width=1.2, modes=["stair_traversal"], step=0.18),
            ],
        },
    }


def _task(identifier, skill, arguments, agents, *, start=0.0, duration=1.0, deps=()):
    return {"task_id": identifier, "skill": skill, "arguments": arguments,
            "assigned_agents": agents, "prerequisites": list(deps),
            "execution": {"start_s": start, "duration_s": duration}}


def _plan(*tasks, team=None):
    return {"assigned_team": team or sorted({a for t in tasks for a in t["assigned_agents"]}),
            "tasks": list(tasks)}


def _pick(*, agent="a", obj="carton", start=0.0, identifier="pick", deps=()):
    return _task(identifier, "pick", {
        "object_id": obj, "pickup_region_id": "rack", "handling_mode_id": "single",
        "carrier_bindings": [{"agent_id": agent, "point_id": "center"}],
    }, [agent], start=start, deps=deps)


def _carry(*, agents=("a", "b"), start=0.0, routes=None, mode="paired"):
    return _task("carry", "cooperative_carry", {
        "object_id": "crate", "destination_region_id": "dock", "handling_mode_id": mode,
        "carrier_bindings": [{"agent_id": agents[0], "point_id": "left"},
                             {"agent_id": agents[1], "point_id": "right"}],
        "route_ids": ["wide"] if routes is None else routes,
    }, list(agents), start=start)


def _nav(*, identifier="nav", agent="a", route="wide", start=1.0, deps=("pick",), destination="dock"):
    return _task(identifier, "ground_navigation", {"region_id": destination, "route_ids": [route]},
                 [agent], start=start, deps=deps)


def _release(*, skill="place", agent="a", start=2.0, deps=("nav",), region="dock", route=None):
    field = "placement_region_id" if skill == "place" else "receiving_region_id"
    args = {"object_id": "carton", field: region}
    if route is not None:
        args["route_ids"] = [route]
    return _task("release", skill, args, [agent], start=start, deps=deps)


def test_valid_model_and_atomic_joint_transport_do_not_claim_execution(spec):
    assert validate_constraint_model(spec)["execution_validated"] is False
    report = validate_plan_constraints(_plan(_carry()), task_spec=spec)
    assert report["scope"] == "route_envelope_resource_model"
    assert report["execution_validated"] is False
    assert report["object_states"]["crate"] == {"region_id": "dock"}
    assert report["resource_capacity_unit"] == "concurrent_action"


def test_transport_counterfactuals_replan_members_instead_of_reusing_witness(spec):
    options = transport_options(spec, "crate")
    assert [o["assigned_agents"] for o in options if o["skill"] == "pick"] == [["c"]]
    assert len([o for o in options if o["skill"] == "cooperative_carry"]) == 6
    reduced = {r["id"]: r for r in spec["candidate_robot_pool"][:2]}
    counterfactual = transport_options(spec, "crate", robot_rows=reduced)
    assert {tuple(o["assigned_agents"]) for o in counterfactual} == {("a", "b"), ("b", "a")}
    assert counterfactual[0]["member_loads_kg"] == [{"agent_id": "a", "load_kg": 6.0}, {"agent_id": "b", "load_kg": 6.0}]
    assert transport_options(spec, "crate", robot_rows=[]) == []
    assert transport_options(spec, "crate", robot_rows=[reduced["a"]]) == []


def test_total_capacity_does_not_override_individual_load_fraction(spec):
    props = spec["public_world"]["objects"][1]["physical_properties"]
    props["handling_modes"][1]["slots"][0]["load_fraction"] = 0.8
    props["handling_modes"][1]["slots"][1]["load_fraction"] = 0.2
    assert transport_options(spec, "crate", robot_rows=spec["candidate_robot_pool"][:2]) == []
    with pytest.raises(ValueError, match="payload"):
        validate_plan_constraints(_plan(_carry()), task_spec=spec)


@pytest.mark.parametrize("restriction", ["interface", "reach", "capability", "attachment", "mobility"])
def test_transport_eligibility_uses_configuration_and_interface(spec, restriction):
    robot = spec["candidate_robot_pool"][0]
    if restriction == "interface":
        robot["physical_profile"]["grasp_interfaces"] = ["incompatible_hook"]
    elif restriction == "reach":
        robot["physical_profile"]["reach_m"] = 0.1
    elif restriction == "capability":
        robot["public_capabilities"].remove("cooperative_carry")
    elif restriction == "attachment":
        robot["interfaces"].remove("cooperative_transport")
    else:
        robot["mobility"] = "aerial"
    assert transport_options(spec, "crate", robot_rows=spec["candidate_robot_pool"][:2]) == []


def test_fixed_object_can_have_no_transport_modes(spec):
    props = spec["public_world"]["objects"][0]["physical_properties"]
    props["grasp_points"] = []
    props["handling_modes"] = []
    assert validate_constraint_model(spec)["validated"]
    assert transport_options(spec, "carton") == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "4"])
def test_invalid_numeric_payload_is_rejected(spec, bad):
    spec["candidate_robot_pool"][0]["physical_profile"]["effective_payload_kg"] = bad
    with pytest.raises(ValueError, match="effective_payload_kg"):
        validate_constraint_model(spec)


@pytest.mark.parametrize("corruption", ["fractions", "duplicate_point", "unknown_point", "envelope", "unknown_region", "resource_capacity", "route_capacity", "duration", "version"])
def test_model_rejects_inconsistent_or_incomplete_declarations(spec, corruption):
    props = spec["public_world"]["objects"][1]["physical_properties"]
    if corruption == "fractions":
        props["handling_modes"][1]["slots"][0]["load_fraction"] = 0.7
    elif corruption == "duplicate_point":
        props["handling_modes"][1]["slots"][1]["point_id"] = "left"
    elif corruption == "unknown_point":
        props["handling_modes"][1]["slots"][1]["point_id"] = "unknown"
    elif corruption == "envelope":
        props["handling_modes"][0]["envelope_m"][1] = 0.2
    elif corruption == "unknown_region":
        props["initial_region_id"] = "missing"
    elif corruption == "resource_capacity":
        spec["public_world"]["regions"][0]["resource_capacity"] = True
    elif corruption == "route_capacity":
        spec["public_world"]["routes"][1]["capacity"] = 2
    elif corruption == "duration":
        spec["execution_contract"]["skill_durations_s"].pop("pick")
    elif corruption == "version":
        spec["execution_contract"]["version"] = True
    with pytest.raises(ValueError):
        validate_constraint_model(spec)


def test_single_carrier_cannot_pick_heavy_object_before_joint_carry(spec):
    with pytest.raises(ValueError, match="payload"):
        validate_plan_constraints(_plan(_pick(obj="crate")), task_spec=spec)


@pytest.mark.parametrize("binding_error", ["duplicate_agent", "duplicate_point", "unassigned", "wrong_mode"])
def test_fixed_point_binding_must_match_actual_members(spec, binding_error):
    carry = _carry()
    if binding_error == "duplicate_agent":
        carry["arguments"]["carrier_bindings"][1]["agent_id"] = "a"
    elif binding_error == "duplicate_point":
        carry["arguments"]["carrier_bindings"][1]["point_id"] = "left"
    elif binding_error == "unassigned":
        carry["arguments"]["carrier_bindings"][1]["agent_id"] = "c"
    else:
        carry["arguments"]["handling_mode_id"] = "single"
    with pytest.raises(ValueError, match="carrier"):
        validate_plan_constraints(_plan(carry), task_spec=spec)


def test_loaded_navigation_cannot_use_empty_robot_clearance(spec):
    assert validate_plan_constraints(_plan(_nav(route="narrow", deps=())), task_spec=spec)["validated"]
    with pytest.raises(ValueError, match="clearance"):
        validate_plan_constraints(_plan(_pick(), _nav(route="narrow")), task_spec=spec)
    valid = _plan(_pick(), _nav(), _release())
    assert validate_plan_constraints(valid, task_spec=spec)["object_states"]["carton"] == {"region_id": "dock"}


def test_joint_formation_width_and_robot_body_are_both_respected(spec):
    with pytest.raises(ValueError, match="clearance"):
        validate_plan_constraints(_plan(_carry(routes=["narrow"])), task_spec=spec)
    spec["candidate_robot_pool"][0]["physical_profile"]["body_size_m"][1] = 2.1
    with pytest.raises(ValueError, match="clearance"):
        validate_plan_constraints(_plan(_carry()), task_spec=spec)


def test_public_clearance_margin_applies_on_both_sides(spec):
    spec["public_world"]["routes"][0]["clearance_m"][0] = 0.6
    with pytest.raises(ValueError, match="clearance"):
        validate_plan_constraints(_plan(_nav(route="narrow", deps=())), task_spec=spec)
    spec["execution_contract"]["clearance_margin_m"] = 0.0
    assert validate_plan_constraints(_plan(_nav(route="narrow", deps=())), task_spec=spec)["validated"]


@pytest.mark.parametrize("route_ids", [[], ["unknown"], ["second", "first"], ["first"]])
def test_navigation_requires_known_connected_path_to_destination(spec, route_ids):
    nav = _nav(deps=())
    nav["arguments"]["route_ids"] = route_ids
    with pytest.raises(ValueError, match="route"):
        validate_plan_constraints(_plan(nav), task_spec=spec)


def test_navigation_requires_explicit_route_and_accepts_connected_multihop(spec):
    nav = _nav(deps=())
    nav["arguments"].pop("route_ids")
    with pytest.raises(ValueError, match="route_ids"):
        validate_plan_constraints(_plan(nav), task_spec=spec)
    nav["arguments"]["route_ids"] = ["first", "second"]
    assert validate_plan_constraints(_plan(nav), task_spec=spec)["validated"]


def test_stairs_use_declared_link_endpoints_step_and_traversal_capability(spec):
    stair = _task("climb", "stair_traversal", {"link_id": "stair", "exit_region_id": "upper", "route_ids": ["stairs"]}, ["a"])
    assert validate_plan_constraints(_plan(stair), task_spec=spec)["validated"]
    wrong = deepcopy(stair)
    wrong["arguments"].update(exit_region_id="dock", route_ids=["wide"])
    with pytest.raises(ValueError, match="stair route"):
        validate_plan_constraints(_plan(wrong), task_spec=spec)
    spec["candidate_robot_pool"][0]["physical_profile"]["max_step_height_m"] = 0.1
    with pytest.raises(ValueError, match="max_step_height"):
        validate_plan_constraints(_plan(stair), task_spec=spec)


def test_ground_navigation_does_not_implicitly_call_stair_skill(spec):
    nav = _nav(route="stairs", deps=(), destination="upper")
    with pytest.raises(ValueError, match="different navigation skill"):
        validate_plan_constraints(_plan(nav), task_spec=spec)


def test_opposite_directions_share_route_resource_and_half_open_boundaries(spec):
    first = _nav(route="narrow", deps=(), start=0)
    other = _nav(identifier="opposite", agent="b", route="reverse", destination="rack", start=0, deps=())
    with pytest.raises(ValueError, match="route shared_lane capacity"):
        validate_plan_constraints(_plan(first, other), task_spec=spec)
    other["execution"]["start_s"] = 1.0
    assert validate_plan_constraints(_plan(first, other), task_spec=spec)["validated"]


def test_multihop_resources_are_reserved_for_whole_action_window(spec):
    first = _nav(route="first", deps=(), start=0, destination="dock")
    first["arguments"]["route_ids"] = ["first", "second"]
    first["execution"]["duration_s"] = 10.0
    other = _nav(identifier="other", agent="b", route="second", deps=(), start=5)
    with pytest.raises(ValueError, match="route second capacity"):
        validate_plan_constraints(_plan(first, other), task_spec=spec)


def test_shared_resource_capacity_can_allow_two_actions(spec):
    for route in spec["public_world"]["routes"][:2]:
        route["capacity"] = 2
    a = _nav(route="narrow", deps=(), start=0)
    b = _nav(identifier="opposite", agent="b", route="reverse", destination="rack", start=0, deps=())
    assert validate_plan_constraints(_plan(a, b), task_spec=spec)["validated"]


def test_agent_cannot_execute_two_skills_at_once_even_on_different_resources(spec):
    nav = _nav(deps=(), start=0)
    observe = _task("observe", "observe", {"region_id": "rack"}, ["a"])
    with pytest.raises(ValueError, match="agent a capacity"):
        validate_plan_constraints(_plan(nav, observe), task_spec=spec)


def test_region_operation_resources_are_shared(spec):
    spec["public_world"]["regions"][0]["resource_capacity"] = 1
    first = _task("observe1", "observe", {"region_id": "rack"}, ["a"])
    second = _task("observe2", "observe", {"region_id": "rack"}, ["b"])
    with pytest.raises(ValueError, match="region rack capacity"):
        validate_plan_constraints(_plan(first, second), task_spec=spec)
    second["execution"]["start_s"] = 1.0
    assert validate_plan_constraints(_plan(first, second), task_spec=spec)["validated"]


def test_same_object_cannot_be_picked_simultaneously_or_while_held(spec):
    other = _pick(identifier="other", agent="b")
    with pytest.raises(ValueError, match="object carton is already occupied"):
        validate_plan_constraints(_plan(_pick(), other), task_spec=spec)
    other["execution"]["start_s"] = 2
    other["prerequisites"] = ["pick"]
    with pytest.raises(ValueError, match="must be available"):
        validate_plan_constraints(_plan(_pick(), other), task_spec=spec)


@pytest.mark.parametrize("skill", ["place", "handoff"])
def test_release_requires_causal_ownership_and_cannot_teleport(spec, skill):
    release = _release(skill=skill, deps=())
    with pytest.raises(ValueError, match="requires matching object ownership"):
        validate_plan_constraints(_plan(release), task_spec=spec)
    with pytest.raises(ValueError, match="causal prerequisite"):
        validate_plan_constraints(_plan(_pick(), release), task_spec=spec)
    release["prerequisites"] = ["pick"]
    with pytest.raises(ValueError, match="without route_ids"):
        validate_plan_constraints(_plan(_pick(), release), task_spec=spec)
    release["arguments"]["route_ids"] = ["wide"]
    report = validate_plan_constraints(_plan(release, _pick()), task_spec=spec)
    assert report["object_states"]["carton"] == {"region_id": "dock"}


def test_ownership_cannot_transfer_by_changing_release_agent(spec):
    with pytest.raises(ValueError, match="matching object ownership"):
        validate_plan_constraints(_plan(_pick(), _release(agent="b", deps=("pick",), region="rack")), task_spec=spec)


def test_object_causal_order_accepts_transitive_dependencies(spec):
    inspect = _task("inspect", "observe", {"region_id": "rack"}, ["a"], start=1, deps=("pick",))
    nav = _nav(start=2, deps=("inspect",))
    assert validate_plan_constraints(_plan(nav, _pick(), inspect), task_spec=spec)["validated"]


def test_loaded_path_must_begin_at_object_current_region(spec):
    nav = _nav(route="second")
    with pytest.raises(ValueError, match="held object's current region"):
        validate_plan_constraints(_plan(_pick(), nav), task_spec=spec)
    with pytest.raises(ValueError, match="object's current region"):
        validate_plan_constraints(_plan(_carry(routes=["second"])), task_spec=spec)


@pytest.mark.parametrize("execution", [None, {"start_s": -1, "duration_s": 1}, {"start_s": 0, "duration_s": 0.1}, {"start_s": float("nan"), "duration_s": 1}, {"start_s": 0, "duration_s": float("inf")}, {"start_s": 100, "duration_s": 1}])
def test_invalid_or_missing_timing_rejected(spec, execution):
    nav = _nav(deps=())
    nav["execution"] = execution
    with pytest.raises(ValueError):
        validate_plan_constraints(_plan(nav), task_spec=spec)


def test_dependencies_must_finish_before_start_and_cannot_cycle(spec):
    with pytest.raises(ValueError, match="prerequisite.*has not ended"):
        validate_plan_constraints(_plan(_pick(), _nav(start=0.5)), task_spec=spec)
    pick = _pick(deps=("nav",))
    with pytest.raises(ValueError, match="cycle"):
        validate_plan_constraints(_plan(pick, _nav()), task_spec=spec)


def test_runtime_replan_state_preserves_held_object_and_minimum_clock(spec):
    picked = validate_plan_constraints(_plan(_pick()), task_spec=spec)
    ctx = {"current_time_s": 1, "object_states": picked["object_states"]}
    report = validate_plan_constraints(_plan(_nav(deps=()), _release()), task_spec=spec, context=ctx)
    assert report["object_states"]["carton"] == {"region_id": "dock"}
    with pytest.raises(ValueError, match="clearance"):
        validate_plan_constraints(_plan(_nav(route="narrow", deps=())), task_spec=spec, context=ctx)
    with pytest.raises(ValueError, match="before current_time_s"):
        validate_plan_constraints(_plan(_nav(start=0, deps=())), task_spec=spec, context=ctx)


@pytest.mark.parametrize("skill", ["place", "handoff"])
def test_existing_attachment_can_be_released_after_pick_capability_is_lost(spec, skill):
    picked = validate_plan_constraints(_plan(_pick()), task_spec=spec)
    rows = {r["id"]: deepcopy(r) for r in spec["candidate_robot_pool"]}
    rows["a"]["public_capabilities"].remove("pick")
    context = {"current_time_s": 1, "object_states": picked["object_states"],
               "robot_regions": picked["robot_regions"]}
    release = _release(skill=skill, start=1, deps=(), region="rack")
    report = validate_plan_constraints(_plan(release), task_spec=spec,
                                      robot_rows=rows, context=context)
    assert report["object_states"]["carton"] == {"region_id": "rack"}
    assert report["execution_validated"] is False
    # The exception applies only to existing runtime attachments. It cannot
    # restore eligibility to acquire another object or inflate counterfactuals.
    with pytest.raises(ValueError, match="lacks skill capability pick"):
        validate_plan_constraints(_plan(_pick()), task_spec=spec, robot_rows=rows)
    assert not any(o["skill"] == "pick" and o["assigned_agents"] == ["a"]
                   for o in transport_options(spec, "carton", robot_rows=rows))


def test_existing_attachment_payload_reduction_requires_explicit_runtime_recovery(spec):
    picked = validate_plan_constraints(_plan(_pick()), task_spec=spec)
    rows = {r["id"]: deepcopy(r) for r in spec["candidate_robot_pool"]}
    rows["a"]["public_capabilities"].remove("pick")
    rows["a"]["physical_profile"]["effective_payload_kg"] = 3.0
    context = {"current_time_s": 1, "object_states": picked["object_states"],
               "robot_regions": picked["robot_regions"]}
    release = _release(start=1, deps=(), region="rack")
    with pytest.raises(ValueError, match=r"existing attachment payload_exceeded for a: load 4\.0 kg exceeds current capacity 3\.0 kg; runtime safe-state recovery required"):
        validate_plan_constraints(_plan(release), task_spec=spec,
                                  robot_rows=rows, context=context)


@pytest.mark.parametrize("invalid", ["manipulator", "grasp_interface", "reach", "point_binding"])
def test_existing_attachment_still_requires_compatible_interface_reach_and_binding(spec, invalid):
    picked = validate_plan_constraints(_plan(_pick()), task_spec=spec)
    rows = {r["id"]: deepcopy(r) for r in spec["candidate_robot_pool"]}
    rows["a"]["public_capabilities"].remove("pick")
    context = {"current_time_s": 1, "object_states": picked["object_states"],
               "robot_regions": picked["robot_regions"]}
    expected = "existing attachment violates current"
    if invalid == "manipulator":
        rows["a"]["interfaces"].remove("manipulator")
    elif invalid == "grasp_interface":
        rows["a"]["physical_profile"]["grasp_interfaces"] = []
    elif invalid == "reach":
        rows["a"]["physical_profile"]["reach_m"] = 0.1
    else:
        context["object_states"]["carton"]["carrier_bindings"][0]["point_id"] = "left"
        expected = "carrier bindings must match assigned agents and mode points"
    with pytest.raises(ValueError, match=expected):
        validate_plan_constraints(_plan(_release(start=1, deps=(), region="rack")),
                                  task_spec=spec, robot_rows=rows, context=context)


def test_spatial_actions_cannot_teleport_robot_after_initial_location_is_known(spec):
    observe = _task("look", "observe", {"region_id": "buffer"}, ["a"])
    nav = _nav(deps=("look",))
    with pytest.raises(ValueError, match="region continuity"):
        validate_plan_constraints(_plan(observe, nav), task_spec=spec)
    nav["arguments"]["route_ids"] = ["second"]
    report = validate_plan_constraints(_plan(observe, nav), task_spec=spec)
    assert report["robot_regions"] == {"a": "dock"}


def test_position_effects_require_causal_dependencies_and_operations_share_location(spec):
    nav = _nav(start=0, deps=())
    observe = _task("look", "observe", {"region_id": "dock"}, ["a"], start=1)
    with pytest.raises(ValueError, match="position needs causal prerequisite"):
        validate_plan_constraints(_plan(nav, observe), task_spec=spec)
    observe["prerequisites"] = ["nav"]
    assert validate_plan_constraints(_plan(nav, observe), task_spec=spec)["validated"]
    observe["arguments"]["region_id"] = "rack"
    with pytest.raises(ValueError, match="region continuity"):
        validate_plan_constraints(_plan(nav, observe), task_spec=spec)


def test_robot_regions_seed_replan_positions_and_must_agree_with_held_object(spec):
    nav = _nav(deps=())
    with pytest.raises(ValueError, match="region continuity"):
        validate_plan_constraints(_plan(nav), task_spec=spec, context={"robot_regions": {"a": "buffer"}})
    report = validate_plan_constraints(_plan(nav), task_spec=spec, context={"robot_regions": {"a": "rack"}})
    assert report["robot_regions"] == {"a": "dock"}
    picked = validate_plan_constraints(_plan(_pick()), task_spec=spec)
    with pytest.raises(ValueError, match="held object state and robot_regions disagree"):
        validate_plan_constraints(_plan(nav), task_spec=spec,
                                  context={"robot_regions": {"a": "buffer"}, "object_states": picked["object_states"]})


def test_visibility_is_an_explicit_external_validation_requirement(spec):
    report = validate_plan_constraints(_plan(_nav(deps=())), task_spec=spec)
    assert report["observation_visibility_validated"] is False
    assert "declared_observation_visibility" in report["external_validation_requirements"]


def test_context_and_counterfactual_member_changes_affect_plan_admission(spec):
    with pytest.raises(ValueError, match="unknown or inactive"):
        validate_plan_constraints(_plan(_carry()), task_spec=spec, context={"current_agents": ["a", "c"]})
    rows = {r["id"]: deepcopy(r) for r in spec["candidate_robot_pool"]}
    rows["a"]["physical_profile"]["effective_payload_kg"] = 5.0
    with pytest.raises(ValueError, match="payload"):
        validate_plan_constraints(_plan(_carry()), task_spec=spec, robot_rows=rows)


def test_new_member_requires_physical_profile_and_can_replace_existing_carrier(spec):
    joined = _robot("joined")
    carry = _carry(agents=("joined", "b"))
    context = {"joined_agents": [joined], "current_agents": ["joined", "b"]}
    assert validate_plan_constraints(_plan(carry), task_spec=spec, context=context)["validated"]
    joined.pop("physical_profile")
    with pytest.raises(ValueError, match="physical_profile"):
        validate_plan_constraints(_plan(carry), task_spec=spec, context=context)


def test_public_robot_profile_validator_requires_complete_finite_declaration():
    robot = _robot("joined")
    report = validate_robot_profile(robot)
    assert report["robot_id"] == "joined"
    assert report["execution_validated"] is False
    robot["physical_profile"].pop("effective_payload_kg")
    with pytest.raises(ValueError, match="physical_profile fields mismatch"):
        validate_robot_profile(robot)
    with pytest.raises(ValueError, match="robot must be an object"):
        validate_robot_profile(None)


def test_validation_does_not_mutate_task_plan_or_runtime_context(spec):
    plan = _plan(_pick(), _nav(), _release())
    context = {"current_agents": ["a", "b", "c"]}
    original = deepcopy((spec, plan, context))
    validate_constraint_model(spec)
    transport_options(spec, "crate")
    validate_plan_constraints(plan, task_spec=spec, context=context)
    assert (spec, plan, context) == original
