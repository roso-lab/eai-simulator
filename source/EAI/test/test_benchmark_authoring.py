from copy import deepcopy

import pytest

from demo.benchmark.authoring import (
    build_authoring_contract, validate_authored_business_scenario,
    validate_authoring_scaffold, build_authoring_witness, validate_authoring_factor_pair,
)
from demo.benchmark.scene_challenge import generate_scene_scenario
from demo.benchmark.scene_challenge import build_scene_runtime_contract


def test_author_packets_keep_scene_goals_distinct_and_do_not_claim_autonomous_planning():
    packets = [build_authoring_contract(s) for s in ("factory", "warehouse_nvd", "modern_house_nvd")]
    assert len({p["task_family"] for p in packets}) == 3
    assert packets[0]["evaluation_track"] == "evidence_based_dispatch"
    assert packets[1]["evaluation_track"] == "fixed_business_dag_allocation"
    assert all(not p["execution_validated"] for p in packets)
    assert "hall_release" in packets[2]["required_objective_roles"]


@pytest.mark.parametrize("mutation", ["primitive", "missing_role", "renamed_skill"])
def test_llm_cannot_substitute_proxy_or_rename_navigation_into_business_success(mutation):
    scenario = generate_scene_scenario(scene_key="warehouse_nvd", task_name="authored", seed=1, origin=(0., 0.))
    if mutation == "primitive":
        scenario["composition"]["entities"][0]["asset_key"] = "primitive_box"
    elif mutation == "missing_role":
        scenario["phase_graph"]["nodes"].pop()
    else:
        scenario["phase_graph"]["nodes"][0]["skill_key"] = "navigate"
    before = deepcopy(scenario)
    with pytest.raises(ValueError, match="unreviewed business scaffold"):
        validate_authored_business_scenario(scenario)
    assert scenario == before


def test_factory_dispatch_cannot_be_certified_as_production_recovery():
    with pytest.raises(ValueError, match="production recovery is not implemented"):
        validate_authored_business_scenario({"scene_key": "factory"})


def test_public_scene_geometry_uses_opaque_object_labels():
    public, _ = build_scene_runtime_contract(generate_scene_scenario(
        scene_key="warehouse_nvd", task_name="opaque", seed=1, origin=(0., 0.)))
    assert all(row["id"].startswith("scene_object_") for row in public["scene_objects"])
    assert all("proxy" not in row["id"] for row in public["scene_objects"])


def scene(key="warehouse_nvd"):
    # Origin is synthetic. These tests exercise declarations, not map admission.
    return generate_scene_scenario(scene_key=key, task_name="author_fixture", seed=1, origin=(0., 0.))


@pytest.mark.parametrize("key", ["warehouse_nvd", "modern_house_nvd"])
def test_scaffold_and_witness_preserve_input_and_bound_the_feasibility_claim(key):
    data = scene(key)
    before = deepcopy(data)
    assert validate_authoring_scaffold(data)["validated"]
    witness = build_authoring_witness(data)
    assert data == before
    assert witness["declaration_feasible"]
    assert not witness["execution_validated"]
    assert witness["physical_team_necessity"] == "unknown"
    assert witness["visibility"] == "author_evaluator_only"
    done = set()
    nodes = {n["id"]: n for n in data["phase_graph"]["nodes"]}
    decisions = {d["node_id"]: d for d in witness["decisions"]}
    for step in witness["serial_witness"]:
        assert set(nodes[step["node_id"]]["prerequisites"]) <= done
        assert step["agents"] in decisions[step["node_id"]]["candidate_teams"]
        done.add(step["node_id"])
    assert done == set(nodes)
    for decision in decisions.values():
        skill = nodes[decision["node_id"]]["skill_key"]
        # Joint transport has one eligible pair, not two independent choices.
        assert decision["candidate_count"] == (2 if skill in {"navigate", "wait"} else 1)
    assert witness["assignment_combination_count"] == (32 if key == "warehouse_nvd" else 64)


@pytest.mark.parametrize("key", ["warehouse_nvd", "modern_house_nvd"])
def test_generation_must_preserve_observation_and_closeout_causal_order(key):
    data = scene(key)
    node_id = "priority_carton_dispatch" if key == "warehouse_nvd" else "room_condition_scan"
    next(n for n in data["phase_graph"]["nodes"] if n["id"] == node_id)["prerequisites"] = []
    with pytest.raises(ValueError, match="causal predecessor"):
        validate_authoring_scaffold(data)
    with pytest.raises(ValueError, match="causal predecessor"):
        validate_authored_business_scenario(data)


def test_graph_cycle_and_unknown_predecessor_are_not_theoretical_witnesses():
    for dependency in ("aisle_closeout", "invented_node"):
        data = scene()
        data["phase_graph"]["nodes"][0]["prerequisites"] = [dependency]
        with pytest.raises(ValueError, match="cycle or unknown"):
            build_authoring_witness(data)


@pytest.mark.parametrize("key,bad_asset", [("warehouse_nvd", "mh_armchair"),
                                           ("modern_house_nvd", "wooden_crate_a")])
def test_registered_but_semantically_wrong_native_prop_is_rejected(key, bad_asset):
    data = scene(key)
    data["composition"]["entities"][0]["asset_key"] = bad_asset
    with pytest.raises(ValueError, match="business prop role"):
        validate_authoring_scaffold(data)


@pytest.mark.parametrize("size", [[.02, .02, .02], [float("nan"), 1., 1.], [True, 1., 1.]])
def test_native_prop_cannot_be_shrunk_to_make_the_problem_easy(size):
    data = scene()
    data["composition"]["entities"][0]["size_m"] = size
    with pytest.raises(ValueError, match="audited declaration"):
        validate_authoring_scaffold(data)


def test_carton_dispatch_cannot_silently_substitute_another_identical_prop():
    data = scene()
    other = deepcopy(data["composition"]["entities"][1])
    other["id"] = "another_carton"
    data["composition"]["entities"].append(other)
    next(n for n in data["phase_graph"]["nodes"]
         if n["id"] == "priority_carton_dispatch")["object_ref"] = other["id"]
    with pytest.raises(ValueError, match="staged carton identity"):
        validate_authoring_scaffold(data)


def test_static_prop_and_invented_mass_cannot_support_a_movement_task():
    for patch in ({"mode": "static"}, {"mass_kg": None}, {"mass_kg": True}):
        data = scene()
        data["composition"]["entities"][0]["physics"].update(patch)
        with pytest.raises(ValueError, match="movable prop"):
            validate_authoring_scaffold(data)


def test_exit_without_a_carrier_and_joining_predeclared_reserve_change_real_counts():
    data = scene()
    reserve = deepcopy(data["robots"][1])
    reserve["id"] = "reserve_m20"
    data["robots"].append(reserve)
    data["runtime_lifecycle"] = {
        "initial_agent_ids": ["iris_1", "m20_1", "carter_1"],
        "preloaded_reserve_agent_ids": ["reserve_m20"],
    }
    original = deepcopy(data)
    public, _ = build_scene_runtime_contract(data)
    assert "reserve_m20" not in {r["id"] for r in public["initial_fleet"]}
    default = build_authoring_witness(data)
    assert default["active_agent_ids"] == sorted(data["runtime_lifecycle"]["initial_agent_ids"])
    assert all("reserve_m20" not in team for d in default["decisions"]
               for team in d["candidate_teams"])
    # Same declared author fleet; the reserve is absent from the active snapshot.
    before = build_authoring_witness(data, active_agent_ids=["iris_1", "m20_1", "carter_1"])
    exited = build_authoring_witness(data, active_agent_ids=["iris_1", "carter_1"])
    joined = build_authoring_witness(data, active_agent_ids=["iris_1", "carter_1", "reserve_m20"])
    assert before["declaration_feasible"] and joined["declaration_feasible"]
    assert not exited["declaration_feasible"] and exited["serial_witness"] is None
    assert {"oversized_crate_transfer", "priority_carton_dispatch"} <= set(exited["empty_candidate_node_ids"])
    assert exited["assignment_combination_count"] == 0
    assert all("reserve_m20" not in team for d in before["decisions"] for team in d["candidate_teams"])
    assert any("reserve_m20" in s["agents"] for s in joined["serial_witness"])
    assert not joined["execution_validated"]
    assert data == original
    assert "reserve_m20" not in {r["id"] for r in public["initial_fleet"]}


@pytest.mark.parametrize("key,node_id", [
    ("warehouse_nvd", "aisle_right_access"),
    ("modern_house_nvd", "doorway_access_check"),
])
def test_preloaded_carter_can_replace_exited_member_only_after_join(key, node_id):
    data = scene(key)
    reserve = deepcopy(data["robots"][2])
    reserve["id"] = "carter_reserve_1"
    data["robots"].append(reserve)
    data["runtime_lifecycle"] = {
        "initial_agent_ids": ["iris_1", "m20_1", "carter_1"],
        "preloaded_reserve_agent_ids": ["carter_reserve_1"],
    }
    original = deepcopy(data)
    public, _ = build_scene_runtime_contract(data)
    before = build_authoring_witness(data, active_agent_ids=["iris_1", "m20_1"])
    after = build_authoring_witness(
        data, active_agent_ids=["iris_1", "m20_1", "carter_reserve_1"])
    prior = next(row for row in before["decisions"] if row["node_id"] == node_id)
    joined = next(row for row in after["decisions"] if row["node_id"] == node_id)
    assert prior["candidate_count"] == 1  # M20 is still a ground candidate.
    assert joined["candidate_count"] == 2
    assert ["carter_reserve_1"] in joined["candidate_teams"]
    assert "carter_reserve_1" not in {r["id"] for r in public["initial_fleet"]}
    assert data == original


def test_capability_loss_is_not_repaired_by_the_robot_name_or_previous_qualification():
    data = scene("modern_house_nvd")
    data["robots"][1]["attachments"] = []
    result = build_authoring_witness(data)
    assert result["empty_candidate_node_ids"] == ["keepsake_relocation"]
    with pytest.raises(ValueError, match="no declared feasible team"):
        validate_authored_business_scenario(data)


def test_remaining_work_can_reuse_members_but_cannot_assume_unfinished_predecessors_done():
    data = scene()
    last = ["aisle_closeout"]
    result = build_authoring_witness(data, active_agent_ids=["carter_1"], remaining_node_ids=last)
    assert result["declaration_feasible"]
    assert result["decisions"][0]["candidate_count"] == 1
    assert result["choice_node_ids"] == []
    with pytest.raises(ValueError, match="successor-closed"):
        build_authoring_witness(data, remaining_node_ids=["rack_overview_scan"])
    with pytest.raises(ValueError, match="unknown member"):
        build_authoring_witness(data, active_agent_ids=["undeclared_reserve"])


def test_fixed_dag_cannot_be_promoted_to_autonomous_decomposition():
    with pytest.raises(ValueError, match="fixed DAG"):
        validate_authored_business_scenario(scene(), evaluation_track="autonomous_business_planning")
    for key in ("factory", "warehouse_nvd", "modern_house_nvd"):
        contract = build_authoring_contract(key)
        assert contract["version"] == 2
        assert contract["autonomous_decomposition_evaluated"] is False
        assert contract["participant_decides"] and contract["fixed_by_author"]
        assert "autonomous task decomposition" in contract["forbidden_claims"]


def experiment_pair(key="warehouse_nvd", factor="communication_quality"):
    packet = build_authoring_contract(key)
    control = {
        "scenario": scene(key) if key != "factory" else {"scene_key": key, "task_name": "frozen_dispatch"},
        "observation_inputs": [{"message_id": "m", "published_at_s": 0., "valid_until_s": 10., "estimate": {"label": "carton"}}],
        "budgets": {"simulation_s": 120., "model_tokens": 4096},
        "scoring": {"required_domain_evidence": True}, "reserve_pool": [],
        "factor_levels": {a["id"]: a["levels"][0] for a in packet["factor_manifest"]["axes"]},
        "interventions": {"communication_quality": [], "team_continuity": []},
    }
    variant = deepcopy(control)
    if factor == "communication_quality":
        variant["factor_levels"][factor] = "delayed"
        variant["interventions"][factor] = [{"time_s": 0., "conditions": {"delay_s": 2.}}]
    else:
        variant["factor_levels"][factor] = "member_exit"
        variant["interventions"][factor] = [{"time_s": 20., "type": "member_exit", "member_id": "m20_1"}]
    return control, variant


@pytest.mark.parametrize("key", ["factory", "warehouse_nvd", "modern_house_nvd"])
@pytest.mark.parametrize("factor", ["communication_quality", "team_continuity"])
def test_factor_pair_checks_inputs_without_claiming_effect_or_live_execution(key, factor):
    control, variant = experiment_pair(key, factor)
    original = deepcopy((control, variant))
    result = validate_authoring_factor_pair(control, variant, factor_id=factor)
    assert result["validated"] and not result["factor_effect_validated"]
    assert not result["execution_validated"]
    assert result["control_sha256"] != result["variant_sha256"]
    assert (control, variant) == original


@pytest.mark.parametrize("mutation", [
    lambda row: row["scenario"]["robots"][0]["spawn_pose"]["position"].__setitem__(0, 99.),
    lambda row: row["scenario"]["robots"].pop(),
    lambda row: row["scenario"]["phase_graph"]["nodes"][-1].__setitem__("timeout_s", 999.),
    lambda row: row["observation_inputs"][0].__setitem__("valid_until_s", 1000.),
    lambda row: row["observation_inputs"][0]["estimate"].__setitem__("label", "answer"),
    lambda row: row["budgets"].__setitem__("model_tokens", 8192),
    lambda row: row["scoring"].__setitem__("required_domain_evidence", False),
    lambda row: row["reserve_pool"].append({"id": "extra_carrier"}),
    lambda row: row["interventions"]["team_continuity"].append({"type": "member_exit", "time_s": 10.}),
])
def test_communication_pair_rejects_changed_non_target_inputs(mutation):
    control, variant = experiment_pair()
    mutation(variant)
    with pytest.raises(ValueError, match="non-target"):
        validate_authoring_factor_pair(control, variant, factor_id="communication_quality")


def test_factor_labels_alone_and_multi_factor_changes_are_not_admissible_pairs():
    control, variant = experiment_pair()
    variant["interventions"] = deepcopy(control["interventions"])
    with pytest.raises(ValueError, match="labels changed"):
        validate_authoring_factor_pair(control, variant, factor_id="communication_quality")
    control, variant = experiment_pair()
    variant["factor_levels"]["team_continuity"] = "member_exit"
    with pytest.raises(ValueError, match="exactly the named factor"):
        validate_authoring_factor_pair(control, variant, factor_id="communication_quality")
    with pytest.raises(ValueError, match="own reviewed comparison"):
        validate_authoring_factor_pair(control, variant, factor_id="aisle_clearance")
