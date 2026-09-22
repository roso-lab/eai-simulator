"""Pure contract and authoring tests for the goal-driven benchmark track."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_driven_contract import (
    build_goal_participant_api,
    build_goal_heterogeneity_certificate,
    semantic_goal_plan_signature,
    validate_goal_action,
    validate_goal_bundle,
    validate_goal_event,
    validate_goal_hidden_evaluator,
    validate_goal_plan,
    validate_goal_participant_api,
    validate_goal_task_spec,
    write_goal_task_bundle,
)
from demo.benchmark.goal_driven_tasks import (
    build_goal_authoring_brief,
    build_goal_task_package,
    build_goal_task_spec,
    generate_goal_task_packages,
    review_goal_business_witness,
    review_goal_task_design,
    validate_goal_authoring_brief,
    validate_goal_factor_pair,
)
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.skill_ontology import public_skill_catalog, validate_skill_arguments


def _bundle(scene="warehouse_nvd"):
    return build_goal_task_package(scene)


def _observation_event():
    return {
        "schema": "eai.benchmark.goal_event", "version": 1,
        "sequence": 4, "time_s": 5.0, "type": "observation",
        "payload": {
            "message_id": "msg-rack-1", "producer_id": "warehouse_aerial_1",
            "kind": "rack_identity", "data": {"public_subject": "order_carton_01"},
            "valid_until_s": 20.0,
        },
    }


def _initial_runtime_state(public, plan):
    """A test runtime's authoritative snapshot before these fixture actions."""
    routes = {row["id"]: row for row in public["public_world"]["routes"]}
    positions = {}
    for task in sorted(plan["tasks"], key=lambda row: row["execution"]["start_s"]):
        arguments = task["arguments"]
        path = arguments.get("route_ids", [])
        origin = routes[path[0]]["from_region_id"] if path else arguments.get("region_id")
        if origin:
            for agent in task["assigned_agents"]:
                positions.setdefault(agent, origin)
    return {"robot_regions": positions, "object_states": {
        obj["id"]: {"region_id": obj["physical_properties"]["initial_region_id"]}
        for obj in public["public_world"]["objects"]}}


def test_three_scene_goal_packages_are_outcome_based_and_reviewable():
    for scene in ("factory", "warehouse_nvd", "modern_house_nvd"):
        bundle = _bundle(scene)
        public = bundle["public_task"]
        assert public["evaluation_track"] == "goal_driven_team_planning_v1"
        assert set(public) == {
            "schema", "version", "task_id", "evaluation_track", "mission",
            "required_outcomes", "candidate_robot_pool", "skill_catalog_version",
            "public_world", "business_constraints", "budgets", "experiment_factor", "execution_contract",
        }
        assert validate_goal_task_spec(public)["validated"]
        assert all(set(outcome) == {"id", "description", "acceptance", "evidence_expectations"}
                   for outcome in public["required_outcomes"])
        assert all("skill" not in outcome and "prerequisites" not in outcome
                   for outcome in public["required_outcomes"])
        encoded = json.dumps(public, ensure_ascii=False, sort_keys=True)
        for forbidden in ("preferred_agent", "reference_dag", "reference_plan", "eligible_agent_ids",
                          "affected_node_ids", "ground_truth", "hidden_fault_time_s"):
            assert forbidden not in encoded
        assert len(bundle["hidden_evaluator"]["witness_plans"]) >= 2


def test_factory_goal_keeps_open_stair_as_public_capability_and_business_context():
    public = _bundle("factory")["public_task"]
    links = public["public_world"]["vertical_links"]
    assert links[0]["id"] == "main_stair"
    assert "stair_traversal" in links[0]["traversal_modes"]
    ground_caps = {
        capability for row in public["candidate_robot_pool"]
        if row["mobility"] == "ground" for capability in row["public_capabilities"]
    }
    assert "stair_traversal" in ground_caps
    assert any("楼梯" in rule for rule in public["business_constraints"])
    stair_agents = [
        row["id"] for row in public["candidate_robot_pool"]
        if "stair_traversal" in row["public_capabilities"]
    ]
    assert len(stair_agents) == 2
    assert len(public["candidate_robot_pool"]) == 4
    assert public["budgets"]["max_active_agents"] == 3


def test_public_world_and_budgets_are_sufficient_for_agent_planning():
    for scene in ("factory", "warehouse_nvd", "modern_house_nvd"):
        public = _bundle(scene)["public_task"]
        assert public["budgets"]["model_tokens"] > 0
        public_capabilities = {
            capability for robot in public["candidate_robot_pool"]
            for capability in robot["public_capabilities"]
        }
        for region in public["public_world"]["regions"]:
            assert set(region["access_modes"]) & public_capabilities
        assert all(row["category"] and row["public_role"]
                   for row in public["public_world"]["objects"])


def test_goal_api_does_not_expose_v1_phase_eligibility_or_consumer_binding():
    bundle = _bundle()
    api = bundle["participant_api"]
    assert set(api["action_types"]) == {
        "select_team", "submit_plan", "revise_plan", "dispatch_skill",
        "cancel_skill", "consume_message",
    }
    assert "phase_ready" not in api["event_types"]
    assert "eligible_agent_ids" not in json.dumps(api, sort_keys=True)
    assert "affected_node_ids" not in json.dumps(api, sort_keys=True)
    assert "consumer_id" not in json.dumps(api, sort_keys=True)
    assert build_goal_participant_api(bundle["public_task"]) == api


def test_required_outcome_cannot_be_renamed_into_a_hidden_execution_node():
    public = deepcopy(_bundle()["public_task"])
    public["required_outcomes"][0]["skill"] = "observe"
    with pytest.raises(ValueError, match="fields mismatch|execution fields|business-level"):
        validate_goal_task_spec(public)


def test_public_candidate_pool_cannot_publish_a_reserve_identity():
    public = deepcopy(_bundle()["public_task"])
    public["candidate_robot_pool"][0]["id"] = "reserve_warehouse_aerial"
    with pytest.raises(ValueError, match="reserve identity"):
        validate_goal_task_spec(public)

    public = deepcopy(_bundle()["public_task"])
    public["required_outcomes"][0]["prerequisites"] = []
    with pytest.raises(ValueError, match="fields mismatch|execution fields|business-level"):
        validate_goal_task_spec(public)


def test_plan_is_agent_generated_and_skill_capability_checked():
    bundle = _bundle()
    public = bundle["public_task"]
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    assert validate_goal_plan(plan, task_spec=public)["validated"]

    broken = deepcopy(plan)
    broken["tasks"][0]["skill"] = "priority_carton_dispatch"
    with pytest.raises(ValueError, match="skill must"):
        validate_goal_plan(broken, task_spec=public)

    broken = deepcopy(plan)
    broken["tasks"][0]["required_capabilities"] = ["ground_navigation"]
    with pytest.raises(ValueError, match="omit"):
        validate_goal_plan(broken, task_spec=public)

    broken = deepcopy(plan)
    broken["tasks"][1]["prerequisites"] = [broken["tasks"][1]["task_id"]]
    with pytest.raises(ValueError, match="cycle"):
        validate_goal_plan(broken, task_spec=public)

    broken = deepcopy(plan)
    broken["tasks"][0]["assigned_agents"] = ["answer_robot"]
    with pytest.raises(ValueError, match="unavailable"):
        validate_goal_plan(broken, task_spec=public)


def test_coalition_is_validated_as_a_skill_requirement():
    public = _bundle("warehouse_nvd")["public_task"]
    plan = deepcopy(_bundle("warehouse_nvd")["hidden_evaluator"]["witness_plans"][0]["plan"])
    carry = next(row for row in plan["tasks"] if row["skill"] == "cooperative_carry")
    carry["assigned_agents"] = ["warehouse_ground_1"]
    with pytest.raises(ValueError, match="at least 2 agents"):
        validate_goal_plan(carry_plan := plan, task_spec=public)

    # 双 M20 可行对（ground_2=carter 在实测锚点注册表下不可达，见 goal_constraints）
    carry["assigned_agents"] = ["warehouse_ground_1", "warehouse_ground_3"]
    carry["required_capabilities"] = ["cooperative_carry"]
    assert validate_goal_plan(carry_plan, task_spec=public)["validated"]
    # Carter 参与高位 support 承载在计划级即被拒（阶段18 v10-v14 实测）
    carry["assigned_agents"] = ["warehouse_ground_1", "warehouse_ground_2"]
    with pytest.raises(ValueError, match="assigned agent is outside the selected plan team|infeasible"):
        validate_goal_plan(carry_plan, task_spec=public)


def test_evidence_refs_must_be_visible_and_current():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    visible = [_observation_event()]
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    plan["tasks"][0]["evidence_refs"] = ["msg-rack-1"]
    for task in plan["tasks"]:
        task["execution"]["start_s"] += 6.0
    assert validate_goal_plan(plan, task_spec=public,
                              context={"visible_events": visible, "current_time_s": 6.0,
                                       **_initial_runtime_state(public, plan)})["validated"]

    broken = deepcopy(plan)
    broken["tasks"][0]["evidence_refs"] = ["future-message"]
    with pytest.raises(ValueError, match="currently visible"):
        validate_goal_plan(broken, task_spec=public,
                          context={"visible_events": visible, "current_time_s": 6.0})

    future = deepcopy(visible[0])
    future["time_s"] = 9.0
    with pytest.raises(ValueError, match="future event"):
        validate_goal_plan(plan, task_spec=public,
                          context={"visible_events": [future], "current_time_s": 6.0})


def test_lifecycle_event_cannot_reveal_affected_nodes_or_future_time():
    bundle = _bundle()
    api, public = bundle["participant_api"], bundle["public_task"]
    event = {
        "schema": "eai.benchmark.goal_event", "version": 1,
        "sequence": 2, "time_s": 3.0, "type": "member_exit",
        "payload": {"agent_id": "warehouse_ground_2"},
    }
    assert validate_goal_event(event, api=api, task_spec=public, current_time_s=3.0)["type"] == "member_exit"
    leaked = deepcopy(event)
    leaked["payload"]["affected_node_ids"] = ["dock_handoff_confirmed"]
    with pytest.raises(ValueError, match="fields mismatch|private"):
        validate_goal_event(leaked, api=api, task_spec=public, current_time_s=3.0)
    future = deepcopy(event)
    future["time_s"] = 4.0
    with pytest.raises(ValueError, match="future"):
        validate_goal_event(future, api=api, task_spec=public, current_time_s=3.0)


def test_revise_plan_requires_visible_trigger_and_known_replaced_plan():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    for task in plan["tasks"]:
        task["execution"]["start_s"] += 6.0
    context = {
        "visible_events": [_observation_event()],
        "current_time_s": 6.0,
        "known_plan_ids": ["agent-plan-1"],
        **_initial_runtime_state(public, plan),
    }
    action = {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": "revise-1", "submitted_at_s": 6.0, "type": "revise_plan",
        "payload": {"trigger_event_sequence": [4], "replaced_plan_id": "agent-plan-1",
                    "reason": "fresh rack evidence changed the feasible access order", "plan": plan},
    }
    assert validate_goal_action(action, api=api, task_spec=public, context=context)["type"] == "revise_plan"
    broken = deepcopy(action)
    broken["payload"]["trigger_event_sequence"] = [99]
    with pytest.raises(ValueError, match="not currently visible"):
        validate_goal_action(broken, api=api, task_spec=public, context=context)
    broken = deepcopy(action)
    broken["payload"]["replaced_plan_id"] = "unknown-plan"
    with pytest.raises(ValueError, match="known submitted plan"):
        validate_goal_action(broken, api=api, task_spec=public, context=context)


def test_dispatch_and_message_actions_use_current_plan_state():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    action = {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": "dispatch-1", "submitted_at_s": 8.0, "type": "dispatch_skill",
        "payload": {"task_id": "survey_racks", "evidence_refs": ["msg-rack-1"]},
    }
    context = {"known_task_ids": ["survey_racks"], "visible_events": [_observation_event()],
               "current_time_s": 8.0}
    assert validate_goal_action(action, api=api, task_spec=public, context=context)["type"] == "dispatch_skill"
    broken = deepcopy(action)
    broken["payload"]["task_id"] = "hidden_answer_task"
    with pytest.raises(ValueError, match="submitted plan"):
        validate_goal_action(broken, api=api, task_spec=public, context=context)

    consume = {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": "consume-1", "submitted_at_s": 8.0, "type": "consume_message",
        "payload": {"message_id": "msg-rack-1"},
    }
    assert validate_goal_action(consume, api=api, task_spec=public, context=context)["type"] == "consume_message"
    with pytest.raises(ValueError, match="already"):
        validate_goal_action(consume, api=api, task_spec=public,
                             context={**context, "consumed_message_ids": ["msg-rack-1"]})


def test_author_review_rejects_single_witness_and_keeps_execution_pending():
    bundle = _bundle("modern_house_nvd")
    public = bundle["public_task"]
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    report = review_goal_task_design(public, witnesses)
    assert report["validated"]
    assert report["authoring_status"] == "passed"
    assert report["execution_status"] == "external_pending"
    with pytest.raises(ValueError, match="at least two witnesses|only one"):
        review_goal_task_design(public, witnesses[:1])


def test_city_goal_bundle_registers_the_urban_incident_response_family():
    bundle = _bundle("city")
    public = bundle["public_task"]
    assert public["mission"]["scene_key"] == "city"
    assert public["task_id"] == "CITY-GOAL-01"
    assert {row["id"] for row in public["required_outcomes"]} == {
        "incident_assessed", "exposed_people_accounted_for",
        "secondary_traffic_protected", "response_access_preserved"}
    assert validate_goal_task_spec(public)["validated"]
    assert validate_goal_task_spec(
        build_goal_task_spec("city", factor_level="delayed"))["validated"]
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    assert len(witnesses) == 2
    assert len({semantic_goal_plan_signature(witness["plan"]) for witness in witnesses}) == 2
    report = review_goal_task_design(public, witnesses)
    assert report["authoring_status"] == "passed" and report["execution_status"] == "external_pending"
    assert report["scoreable"] is False
    certificate = bundle["hidden_evaluator"]["heterogeneity_certificate"]
    for row in certificate["witnesses"]:
        assert row["cross_mobility_edges"]
        assert all(counter["invalidated_outcome_ids"] for counter in row["counterfactuals"])
    gate = bundle["hidden_evaluator"]["release_gate"]
    assert gate["benchmark_ready"] is False and gate["scoreable"] is False


def test_city_witness_plans_bind_the_full_frozen_episode_roster():
    bundle = _bundle("city")
    from demo.benchmark.goal_driven_tasks import (
        _CITY_AFFECTED_PERSON_ASSEMBLY, _CITY_FOLLOWING_VEHICLE_IDS,
    )
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        guided = {}
        for task in witness["plan"]["tasks"]:
            if task["skill"] != "guide_people":
                continue
            for person_id in task["arguments"]["person_ids"]:
                assert person_id not in guided
                guided[person_id] = task["arguments"]["assembly_region_id"]
        assert guided == _CITY_AFFECTED_PERSON_ASSEMBLY
        controlled = {vehicle for task in witness["plan"]["tasks"]
                      if task["skill"] == "control_following_traffic"
                      for vehicle in task["arguments"]["vehicle_ids"]}
        assert controlled == set(_CITY_FOLLOWING_VEHICLE_IDS)
        report = next(task for task in witness["plan"]["tasks"]
                      if task["skill"] == "report_incident")
        assert report["arguments"]["recipient_service_id"] == "city_dispatch"


def test_city_business_review_rejects_partial_roster_and_foreign_outcomes():
    bundle = _bundle("city")
    public = bundle["public_task"]
    intact = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0])
    # The business rule is checked in isolation: plan/constraint validity of the
    # intact witness is reused so the removal only tests the roster rule.
    constraint_review = validate_goal_plan(intact["plan"], task_spec=public)["constraint_review"]
    witness = deepcopy(intact)
    plan = witness["plan"]
    # Drop the final guidance task so the surviving guidance chain keeps its
    # observation prerequisites and only the roster completeness rule fails.
    dropped = next(row for row in reversed(plan["tasks"]) if row["skill"] == "guide_people")
    plan["tasks"] = [row for row in plan["tasks"] if row is not dropped]
    for row in plan["tasks"]:
        row["prerequisites"] = [ref for ref in row["prerequisites"] if ref != dropped["task_id"]]
    for outcome, references in witness["outcome_support"].items():
        witness["outcome_support"][outcome] = [ref for ref in references if ref != dropped["task_id"]]
    with pytest.raises(ValueError, match="city:full_roster_guided"):
        review_goal_business_witness(public, witness, constraint_review=constraint_review)

    foreign = deepcopy(public)
    foreign["required_outcomes"][0]["id"] = "unrelated_outcome"
    with pytest.raises(ValueError, match="registered_business_review"):
        review_goal_business_witness(foreign, deepcopy(bundle["hidden_evaluator"]["witness_plans"][1]))


def test_hidden_witness_must_bind_each_business_outcome_to_plan_tasks():
    bundle = _bundle("warehouse_nvd")
    broken = deepcopy(bundle["hidden_evaluator"])
    broken["witness_plans"][0].pop("outcome_support")
    with pytest.raises(ValueError, match="outcome_support|outcome evidence bindings"):
        validate_goal_hidden_evaluator(broken, task_spec=bundle["public_task"])

    broken = deepcopy(bundle["hidden_evaluator"])
    broken["witness_plans"][0]["outcome_support"]["warehouse_closeout"] = ["invented_task"]
    with pytest.raises(ValueError, match="unknown plan task"):
        validate_goal_hidden_evaluator(broken, task_spec=bundle["public_task"])


@pytest.mark.parametrize("scene", [
    "warehouse_nvd",
    "modern_house_nvd",
])
def test_candidate_pool_requires_team_selection_and_carter_cannot_pick(scene):
    bundle = _bundle(scene)
    public = bundle["public_task"]
    assert len(public["candidate_robot_pool"]) == 4
    assert public["budgets"]["max_active_agents"] == 3
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    # 阶段19 起可行承载对唯一（双 M20），两见证共享队合但分工互换：picker
    # 角色对调（assigned_team 顺序不同 + pick/承载任务的 assigned_agents 不同）。
    assert witnesses[0]["plan"]["assigned_team"] != witnesses[1]["plan"]["assigned_team"]
    pick0 = next(row for row in witnesses[0]["plan"]["tasks"] if row["skill"] == "pick")
    pick1 = next(row for row in witnesses[1]["plan"]["tasks"] if row["skill"] == "pick")
    assert pick0["assigned_agents"] != pick1["assigned_agents"]

    carter = next(row for row in public["candidate_robot_pool"] if row["type"] == "carter")
    assert not {"pick", "place"} & set(carter["public_capabilities"])
    plan = deepcopy(witnesses[0]["plan"])
    pick = next(row for row in plan["tasks"] if row["skill"] == "pick")
    original_picker = pick["assigned_agents"][0]
    # 预算 3：队内以 carter 整体替换原 picker 的全部任务分工，验证 pick
    # 能力缺失在计划级仍被拒（carter 无 pick/place 能力）
    for row in plan["tasks"]:
        row["assigned_agents"] = [carter["id"] if agent == original_picker else agent
                                  for agent in row["assigned_agents"]]
    plan["assigned_team"] = [carter["id"]] + [
        member for member in plan["assigned_team"] if member != original_picker]
    with pytest.raises(ValueError, match="does not declare"):
        validate_goal_plan(plan, task_spec=public)


def test_aerial_observation_cannot_use_ground_only_region():
    bundle = _bundle("warehouse_nvd")
    public = bundle["public_task"]
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    observation = next(row for row in plan["tasks"] if row["skill"] == "observe")
    observation["arguments"]["region_id"] = "rack_aisle_b"
    with pytest.raises(ValueError, match="not publicly aerial-accessible"):
        validate_goal_plan(plan, task_spec=public)


@pytest.mark.parametrize("scene,final_prefix,withdraw_prefix", [
    ("warehouse_nvd", "scan_closeout", "withdraw_"),
    ("modern_house_nvd", "verify_path", "withdraw_"),
])
def test_final_scene_review_waits_for_both_ground_members(scene, final_prefix, withdraw_prefix):
    bundle = _bundle(scene)
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        tasks = witness["plan"]["tasks"]
        final = next(row for row in tasks if row["task_id"].startswith(final_prefix))
        expected = {row["task_id"] for row in tasks if row["task_id"].startswith(withdraw_prefix)}
        assert len(expected) == 2
        assert set(final["prerequisites"]) == expected


def test_witness_renaming_does_not_create_a_second_semantic_plan():
    bundle = _bundle("warehouse_nvd")
    public = bundle["public_task"]
    original = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0])
    renamed = deepcopy(original)
    renamed["id"] = "renamed_copy"
    renamed["plan"]["plan_id"] = "renamed_plan"
    id_map = {
        task["task_id"]: f"renamed_{index}"
        for index, task in enumerate(renamed["plan"]["tasks"])
    }
    for task in renamed["plan"]["tasks"]:
        old_id = task["task_id"]
        task["task_id"] = id_map[old_id]
        task["prerequisites"] = [id_map[item] for item in task["prerequisites"]]
    renamed["plan"]["tasks"].reverse()
    renamed["outcome_support"] = {
        outcome_id: [id_map[item] for item in task_ids]
        for outcome_id, task_ids in renamed["outcome_support"].items()
    }
    with pytest.raises(ValueError, match="semantically distinct"):
        review_goal_task_design(public, [original, renamed])

    hidden = deepcopy(bundle["hidden_evaluator"])
    hidden["witness_plans"] = [original, renamed]
    with pytest.raises(ValueError, match="semantically distinct"):
        validate_goal_hidden_evaluator(hidden, task_spec=public)


@pytest.mark.parametrize("scene", ["factory", "warehouse_nvd", "modern_house_nvd"])
def test_heterogeneity_certificate_has_cross_mobility_and_counterfactual_evidence(scene):
    certificate = _bundle(scene)["hidden_evaluator"]["heterogeneity_certificate"]
    assert certificate["visibility"] == "author_evaluator_only"
    assert certificate["declaration_level_validated"] is True
    assert certificate["physical_necessity_validated"] is False
    assert certificate["execution_validated"] is False
    for witness in certificate["witnesses"]:
        assert witness["cross_mobility_edges"]
        counterfactuals = {row["removed_mobility"]: row for row in witness["counterfactuals"]}
        assert set(counterfactuals) == {"aerial", "ground"}
        assert all(row["invalidated_outcome_ids"] for row in counterfactuals.values())


@pytest.mark.parametrize("factor_id,control_level,variant_level", [
    ("communication_quality", "immediate", "delayed"),
    ("communication_quality", "immediate", "dropped"),
    ("communication_quality", "immediate", "bandwidth_limited"),
    ("communication_quality", "immediate", "stale"),
    ("team_continuity", "stable", "member_exit"),
    ("team_continuity", "stable", "member_join"),
    ("team_continuity", "stable", "capability_change"),
])
def test_hidden_factor_treatments_are_concrete_and_pair_isolated(
        factor_id, control_level, variant_level):
    control = build_goal_task_package(
        "warehouse_nvd", factor_id=factor_id, factor_level=control_level)
    variant = build_goal_task_package(
        "warehouse_nvd", factor_id=factor_id, factor_level=variant_level)
    result = validate_goal_factor_pair(control, variant, factor_id=factor_id)
    assert result["validated"]
    assert result["visibility"] == "author_evaluator_only"
    assert result["factor_effect_validated"] is False
    assert result["execution_validated"] is False
    for bundle in (control, variant):
        schedule = bundle["hidden_evaluator"]["hidden_event_schedule"]
        assert schedule["visibility"] == "author_evaluator_only"
        assert schedule["communication_events"]
        assert schedule["decision_window"]["closes_before"] == "first_business_state_commit"


def test_goal_factor_pair_rejects_a_changed_frozen_budget():
    control = build_goal_task_package(
        "modern_house_nvd", factor_id="communication_quality", factor_level="immediate")
    variant = build_goal_task_package(
        "modern_house_nvd", factor_id="communication_quality", factor_level="delayed")
    variant["public_task"]["budgets"]["planning_s"] += 1
    public_hash = canonical_sha256(variant["public_task"])
    variant["participant_api"]["public_task_sha256"] = public_hash
    variant["baseline_manifest"]["public_task_sha256"] = public_hash
    variant["hidden_evaluator"]["public_task_sha256"] = public_hash
    variant["hidden_evaluator"]["heterogeneity_certificate"] = build_goal_heterogeneity_certificate(
        variant["public_task"], variant["hidden_evaluator"]["witness_plans"])
    with pytest.raises(ValueError, match="frozen task, team, budget, or evaluator input"):
        validate_goal_factor_pair(control, variant, factor_id="communication_quality")


def test_hidden_schedule_rejects_a_label_without_matching_treatment():
    bundle = build_goal_task_package(
        "factory", factor_id="communication_quality", factor_level="delayed")
    hidden = deepcopy(bundle["hidden_evaluator"])
    treatment = hidden["hidden_event_schedule"]["communication_events"][0]
    treatment["delay_s"] = 0.0
    with pytest.raises(ValueError, match="delayed communication treatment"):
        validate_goal_hidden_evaluator(hidden, task_spec=bundle["public_task"])


def test_member_join_identity_and_treatment_stay_out_of_participant_layers():
    bundle = build_goal_task_package(
        "factory", factor_id="team_continuity", factor_level="member_join")
    participant_text = json.dumps({
        key: bundle[key]
        for key in ("public_task", "participant_api", "baseline_manifest")
    }, ensure_ascii=False, sort_keys=True)
    hidden_text = json.dumps(bundle["hidden_evaluator"], ensure_ascii=False, sort_keys=True)
    assert "factory_hidden_joiner_1" not in participant_text
    assert "after_first_cross_mobility_observation_before_first_business_commit" not in participant_text
    assert "factory_hidden_joiner_1" in hidden_text
    assert "after_first_cross_mobility_observation_before_first_business_commit" in hidden_text


@pytest.mark.parametrize("scene", ["factory", "warehouse_nvd", "modern_house_nvd"])
def test_goal_authoring_brief_bounds_llm_regeneration_without_an_answer(scene):
    brief = build_goal_authoring_brief(scene)
    assert validate_goal_authoring_brief(brief)["validated"]
    assert brief["visibility"] == "author_only"
    assert set(brief["factor_axes"]) == {"communication_quality", "team_continuity"}
    encoded = json.dumps(brief, ensure_ascii=False, sort_keys=True)
    assert '"reference_plan":' not in encoded
    assert '"reference_dag":' not in encoded
    assert '"witness_plans":' not in encoded


def test_generator_writes_author_brief_outside_participant_release(tmp_path):
    output = tmp_path / "goal-packages"
    generate_goal_task_packages(["factory"], output_root=str(output))
    scene_root = output / "factory"
    assert (scene_root / "authoring" / "goal_authoring_brief.json").is_file()
    assert not (scene_root / "release" / "goal_authoring_brief.json").exists()
    review = json.loads((scene_root / "authoring" / "review.json").read_text())
    assert len(review["quantitative_authoring_review"]) == 2
    assert all(not row["execution_validated"] for row in review["quantitative_authoring_review"])
    assert not (scene_root / "release" / "review.json").exists()


def test_api_publishes_fixed_point_route_and_time_contract_and_rejects_tampering():
    bundle = _bundle()
    public, api = bundle["public_task"], deepcopy(bundle["participant_api"])
    assert public["version"] == api["version"] == 2
    assert api["skill_catalog"] == public_skill_catalog()
    assert api["skill_catalog"]["skills"]["pick"]["parameters"]["carrier_bindings"]["required"]
    assert api["plan_contract"]["execution_fields"] == ["start_s", "duration_s"]
    del api["skill_catalog"]["skills"]["pick"]["parameters"]["carrier_bindings"]
    with pytest.raises(ValueError, match="complete current skill catalog"):
        validate_goal_participant_api(api, task_spec=public)


@pytest.mark.parametrize("extra", ["pick", "invented_capability"])
def test_plan_checks_every_declared_required_capability(extra):
    bundle = _bundle()
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    aerial = next(task for task in plan["tasks"]
                  if "warehouse_aerial_1" in task["assigned_agents"])
    aerial["required_capabilities"].append(extra)
    with pytest.raises(ValueError, match="does not declare|unknown required capability"):
        validate_goal_plan(plan, task_spec=bundle["public_task"])


def test_current_payload_and_skill_changes_override_initial_capability_declaration():
    bundle = _bundle()
    public = bundle["public_task"]
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    picker = next(t["assigned_agents"][0] for t in plan["tasks"] if t["skill"] == "pick")
    original = next(row for row in public["candidate_robot_pool"] if row["id"] == picker)
    updated = deepcopy(original)
    updated["public_capabilities"].remove("pick")
    with pytest.raises(ValueError, match="does not declare"):
        validate_goal_plan(plan, task_spec=public, context={"capability_updates": [updated]})
    updated = deepcopy(original)
    updated["physical_profile"]["effective_payload_kg"] = 0.0
    with pytest.raises(ValueError, match="payload|feasible|binding|load"):
        validate_goal_plan(plan, task_spec=public, context={"capability_updates": [updated]})


def test_join_event_requires_complete_physical_declaration_and_matching_identity():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    joined = deepcopy(public["candidate_robot_pool"][1])
    joined["id"] = "arriving_member"
    event = {"schema": "eai.benchmark.goal_event", "version": 1,
             "sequence": 5, "time_s": 6.0, "type": "member_join",
             "payload": {"agent_id": joined["id"], "capability_version": 1,
                         "capability_declaration": joined}}
    assert validate_goal_event(event, api=api, task_spec=public, current_time_s=6)["type"] == "member_join"
    del joined["physical_profile"]
    with pytest.raises(ValueError, match="fields mismatch"):
        validate_goal_event(event, api=api, task_spec=public, current_time_s=6)


def test_runtime_roster_cannot_admit_an_undeclared_joined_agent():
    bundle = _bundle()
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    with pytest.raises(ValueError, match="without complete public declarations"):
        validate_goal_plan(plan, task_spec=bundle["public_task"], context={
            "allow_joined_agents": True,
            "current_agents": plan["assigned_team"] + ["undeclared_member"],
        })


def test_joined_robot_with_complete_profile_can_replace_departed_member():
    bundle = _bundle()
    public = bundle["public_task"]
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    old = next(t["assigned_agents"][0] for t in plan["tasks"] if t["skill"] == "pick")
    joined = deepcopy(next(r for r in public["candidate_robot_pool"] if r["id"] == old))
    joined["id"] = "joined_m20"
    plan["assigned_team"] = [joined["id"] if a == old else a for a in plan["assigned_team"]]
    for task in plan["tasks"]:
        task["assigned_agents"] = [joined["id"] if a == old else a for a in task["assigned_agents"]]
        for binding in task["arguments"].get("carrier_bindings", []):
            if binding["agent_id"] == old:
                binding["agent_id"] = joined["id"]
    context = {"current_agents": plan["assigned_team"], "allow_joined_agents": True,
               "joined_agents": [joined]}
    assert validate_goal_plan(plan, task_spec=public, context=context)["constraint_review"]["validated"]
    joined["physical_profile"]["effective_payload_kg"] = 0.1
    with pytest.raises(ValueError, match="payload|feasible|binding|load"):
        validate_goal_plan(plan, task_spec=public, context=context)


def test_submission_cannot_backdate_plan_or_override_runtime_clock():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    action = {"schema": "eai.benchmark.goal_action", "version": 1,
              "action_id": "submit-1", "submitted_at_s": 6.0, "type": "submit_plan",
              "payload": {"plan": bundle["hidden_evaluator"]["witness_plans"][0]["plan"]}}
    with pytest.raises(ValueError, match="starts before current_time_s"):
        validate_goal_action(action, api=api, task_spec=public,
                             context=_initial_runtime_state(public, action["payload"]["plan"]))
    with pytest.raises(ValueError, match="match current simulation time"):
        validate_goal_action(action, api=api, task_spec=public, context={"current_time_s": 7.0})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, True])
def test_skill_numeric_arguments_are_finite_and_nonnegative(value):
    with pytest.raises(ValueError, match="finite nonnegative"):
        validate_skill_arguments("aerial_navigation", {
            "region_id": "air_region", "route_ids": ["route"], "altitude_m": value})


def test_goal_release_distinguishes_declared_constraints_from_backend_conformance():
    from demo.benchmark.release_manifest import audit_goal_challenge_bundle, build_goal_release_manifest

    bundle = _bundle()
    manifest = build_goal_release_manifest({"warehouse": bundle})
    assert manifest["claims"]["declared_constraint_model_review_complete"] is True
    assert manifest["claims"]["scene_binding_validated"] is False
    assert manifest["claims"]["skill_backend_conformance_validated"] is False
    assert manifest["scoreable"] is False
    assert manifest["packages"][0]["parameter_basis"] == "benchmark_defined"
    invalid = deepcopy(bundle)
    invalid["hidden_evaluator"]["release_gate"]["scoreable"] = True
    with pytest.raises(ValueError, match="readiness|release gate"):
        audit_goal_challenge_bundle("warehouse", invalid)


def test_dispatch_rejects_expired_observation_including_exact_expiry_boundary():
    bundle = _bundle()
    public, api = bundle["public_task"], bundle["participant_api"]
    for now in (20.0, 21.0):
        action = {"schema": "eai.benchmark.goal_action", "version": 1,
                  "action_id": "dispatch-expired", "submitted_at_s": now, "type": "dispatch_skill",
                  "payload": {"task_id": "survey", "evidence_refs": ["msg-rack-1"]}}
        with pytest.raises(ValueError, match="expired"):
            validate_goal_action(action, api=api, task_spec=public, context={
                "current_time_s": now, "known_task_ids": ["survey"],
                "visible_events": [_observation_event()]})
        with pytest.raises(ValueError, match="expired"):
            validate_goal_action(action, api=api, task_spec=public, context={
                "known_task_ids": ["survey"], "visible_events": [_observation_event()]})


def test_replanning_requires_current_positions_and_cannot_reinfer_upper_floor_spawn():
    bundle = _bundle("factory")
    public = bundle["public_task"]
    original = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    observe = deepcopy(next(t for t in original["tasks"] if t["task_id"] == "close_view_upper"))
    observe["prerequisites"] = []
    observe["execution"]["start_s"] = 10.0
    plan = {"schema": "eai.benchmark.goal_plan", "version": 3, "plan_id": "recovery",
            "assigned_team": observe["assigned_agents"], "tasks": [observe]}
    with pytest.raises(ValueError, match="snapshot"):
        validate_goal_plan(plan, task_spec=public, context={"current_time_s": 10})
    snapshot = _initial_runtime_state(public, plan)
    snapshot["robot_regions"][observe["assigned_agents"][0]] = "stair_approach"
    with pytest.raises(ValueError, match="region continuity"):
        validate_goal_plan(plan, task_spec=public, context={"current_time_s": 10, **snapshot})


def test_nested_private_material_is_rejected_from_all_participant_layers():
    bundle = _bundle()
    api = deepcopy(bundle["participant_api"])
    api["plan_contract"]["witness_plans"] = bundle["hidden_evaluator"]["witness_plans"]
    with pytest.raises(ValueError, match="fields mismatch|private"):
        validate_goal_participant_api(api, task_spec=bundle["public_task"])
    baseline = deepcopy(bundle)
    baseline["baseline_manifest"]["solvers"][0]["metadata"] = {"reference_plan": {"answer": 1}}
    with pytest.raises(ValueError, match="baseline leaks private"):
        validate_goal_bundle(baseline)
    event = _observation_event()
    event["payload"]["data"]["hidden_event_schedule"] = {"future": 9}
    with pytest.raises(ValueError, match="private fields"):
        validate_goal_event(event, api=bundle["participant_api"], task_spec=bundle["public_task"])


@pytest.mark.parametrize("location", ["plan", "metadata", "behavior", "extra_baseline_field"])
def test_baseline_cannot_publish_hidden_witness_under_unreserved_keys(tmp_path, location):
    from demo.benchmark.release_manifest import audit_goal_challenge_bundle

    bundle = _bundle("modern_house_nvd")
    plan = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0]["plan"])
    baseline = bundle["baseline_manifest"]
    if location == "extra_baseline_field":
        baseline["example"] = plan
    else:
        baseline["solvers"][0][location] = {"example": plan} if location == "metadata" else plan
    for validate in (validate_goal_bundle, lambda b: audit_goal_challenge_bundle("house", b)):
        with pytest.raises(ValueError, match="baseline"):
            validate(bundle)
    output = tmp_path / "must-not-be-published"
    with pytest.raises(ValueError, match="baseline"):
        write_goal_task_bundle(bundle, output)
    assert not output.exists()


@pytest.mark.parametrize("patch", [
    {"scoreable": True}, {"benchmark_ready": True},
    {"scoreable": 0}, {"benchmark_ready": None},
    {"execution_status": "passed"}, {"authoring_status": "pending"},
    {"blockers": []}, {"blockers": [""]}, {"blockers": "pending"},
    {"scoreable": True, "benchmark_ready": True, "execution_status": "passed", "blockers": []},
])
def test_native_pending_gate_is_enforced_at_every_publication_boundary(tmp_path, patch):
    from demo.benchmark.release_manifest import audit_goal_challenge_bundle

    bundle = _bundle()
    bundle["hidden_evaluator"]["release_gate"].update(patch)
    checks = (
        lambda: validate_goal_hidden_evaluator(bundle["hidden_evaluator"], task_spec=bundle["public_task"]),
        lambda: validate_goal_bundle(bundle),
        lambda: audit_goal_challenge_bundle("warehouse", bundle),
        lambda: write_goal_task_bundle(bundle, tmp_path / "invalid"),
    )
    for check in checks:
        with pytest.raises(ValueError, match="release gate"):
            check()
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("scene", ["factory", "modern_house_nvd", "warehouse_nvd"])
def test_pending_goal_bundle_still_publishes_and_round_trips(tmp_path, scene):
    from demo.benchmark.release_manifest import audit_goal_challenge_bundle

    bundle = _bundle(scene)
    paths = write_goal_task_bundle(bundle, tmp_path / scene)
    restored = {name: json.loads(path.read_text()) for name, path in paths.items()}
    assert restored == bundle
    assert validate_goal_bundle(restored)["validated"] is True
    report = audit_goal_challenge_bundle(scene, restored)
    assert report["constraint_model_status"] == "passed"
    assert report["skill_backend_conformance"] == "external_pending"
    assert report["scoreable"] is False
    assert paths["hidden_evaluator"].parent.name == "evaluator"
    assert paths["baseline_manifest"].parent.name == "release"


def test_duplicate_message_identity_cannot_be_bound_to_different_observations():
    bundle = _bundle()
    event = _observation_event()
    duplicate = deepcopy(event)
    duplicate["sequence"] += 1
    duplicate["payload"]["data"]["public_subject"] = "order_crate_01"
    action = {"schema": "eai.benchmark.goal_action", "version": 1,
              "action_id": "ambiguous", "submitted_at_s": 6.0, "type": "dispatch_skill",
              "payload": {"task_id": "survey", "evidence_refs": ["msg-rack-1"]}}
    with pytest.raises(ValueError, match="message ids must be unique"):
        validate_goal_action(action, api=bundle["participant_api"], task_spec=bundle["public_task"],
                             context={"known_task_ids": ["survey"], "visible_events": [event, duplicate]})


@pytest.mark.parametrize("target", ["event_types", "action_types", "action_envelope", "implementation_status"])
def test_api_nested_declarations_cannot_smuggle_answers_or_redefine_protocol(target):
    bundle = _bundle()
    api = deepcopy(bundle["participant_api"])
    if target in {"event_types", "action_types"}:
        kind = "observation" if target == "event_types" else "submit_plan"
        api[target][kind]["example"] = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    elif target == "action_envelope":
        api[target]["fields"] = ["anything"]
    else:
        api[target]["runtime_execution"] = "validated"
    with pytest.raises(ValueError, match="invalid|implementation_status"):
        validate_goal_participant_api(api, task_spec=bundle["public_task"])
