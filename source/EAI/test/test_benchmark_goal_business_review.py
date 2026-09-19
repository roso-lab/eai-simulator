"""Business authoring checks reject feasible motion plans with fake outcomes."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_contract import validate_goal_hidden_evaluator, validate_goal_plan
from demo.benchmark.goal_driven_tasks import (
    build_goal_task_package,
    review_goal_business_witness,
)


def _case(scene):
    bundle = build_goal_task_package(scene)
    return bundle["public_task"], deepcopy(bundle["hidden_evaluator"]["witness_plans"][0])


def _remove_task(witness, task_id):
    plan = witness["plan"]
    removed = next(row for row in plan["tasks"] if row["task_id"] == task_id)
    plan["tasks"] = [row for row in plan["tasks"] if row["task_id"] != task_id]
    for row in plan["tasks"]:
        if task_id in row["prerequisites"]:
            row["prerequisites"] = sorted(
                (set(row["prerequisites"]) - {task_id}) | set(removed["prerequisites"]))
    for outcome, references in witness["outcome_support"].items():
        if task_id in references:
            witness["outcome_support"][outcome] = [
                ref for ref in references if ref != task_id] or list(removed["prerequisites"])


@pytest.mark.parametrize("scene", ["factory", "warehouse_nvd", "modern_house_nvd"])
@pytest.mark.parametrize("witness_index", [0, 1])
def test_business_review_is_independent_of_task_ids_and_list_order(scene, witness_index):
    bundle = build_goal_task_package(scene)
    public = bundle["public_task"]
    witness = deepcopy(bundle["hidden_evaluator"]["witness_plans"][witness_index])
    plan = witness["plan"]
    mapping = {row["task_id"]: f"participant_step_{index}" for index, row in enumerate(plan["tasks"])}
    for row in plan["tasks"]:
        row["task_id"] = mapping[row["task_id"]]
        row["prerequisites"] = [mapping[ref] for ref in row["prerequisites"]]
    for outcome, refs in witness["outcome_support"].items():
        witness["outcome_support"][outcome] = [mapping[ref] for ref in refs]
    plan["tasks"].reverse()
    report = review_goal_business_witness(public, witness)
    assert report["validated"]
    assert report["scope"] == "symbolic_business_necessary_conditions"
    assert report["runtime_outcomes_validated"] is False
    assert report["observation_visibility_validated"] is False
    assert report["receipt_authenticity_validated"] is False


def test_observation_only_plan_cannot_claim_warehouse_order_completion():
    public, witness = _case("warehouse_nvd")
    tasks = [
        {"task_id": "aerial_inventory", "skill": "observe",
         "arguments": {"region_id": "rack_overview", "subject_ref": "order_crate_01",
                       "observation_mode": "rack_inventory"},
         "assigned_agents": ["warehouse_aerial_1"], "prerequisites": [],
         "required_capabilities": ["observe"], "evidence_refs": [],
         "execution": {"start_s": 0.0, "duration_s": 4.0}},
        {"task_id": "ground_confirmation", "skill": "observe",
         "arguments": {"region_id": "consolidation_area", "subject_ref": "consolidation_area",
                       "observation_mode": "close_view"},
         "assigned_agents": ["warehouse_ground_1"], "prerequisites": ["aerial_inventory"],
         "required_capabilities": ["observe"], "evidence_refs": [],
         "execution": {"start_s": 4.0, "duration_s": 4.0}},
    ]
    witness["plan"]["tasks"] = tasks
    witness["plan"]["assigned_team"] = ["warehouse_aerial_1", "warehouse_ground_1"]
    witness["outcome_support"] = {outcome["id"]: ["ground_confirmation"]
                                  for outcome in public["required_outcomes"]}
    assert validate_goal_plan(witness["plan"], task_spec=public)["validated"]
    with pytest.raises(ValueError, match="business necessary condition.*dock_receipt"):
        review_goal_business_witness(public, witness)


def test_crate_delivery_does_not_replace_dock_receipt():
    public, witness = _case("warehouse_nvd")
    receipt = next(row for row in witness["plan"]["tasks"]
                   if row["arguments"].get("observation_mode") == "dock_receiver_receipt")
    _remove_task(witness, receipt["task_id"])
    assert validate_goal_plan(witness["plan"], task_spec=public)["validated"]
    with pytest.raises(ValueError, match="order_crate_01:inventory_then_consolidation_then_dock_receipt"):
        review_goal_business_witness(public, witness)


def test_factory_arrival_does_not_replace_service_receipt():
    public, witness = _case("factory")
    receipt = next(row for row in witness["plan"]["tasks"]
                   if row["arguments"].get("observation_mode") == "maintenance_service_receipt")
    _remove_task(witness, receipt["task_id"])
    assert validate_goal_plan(witness["plan"], task_spec=public)["validated"]
    with pytest.raises(ValueError, match="service_receipt_after_field_checks_and_actual_return"):
        review_goal_business_witness(public, witness)


def test_house_clearance_scan_before_withdrawal_is_not_final_verification():
    public, witness = _case("modern_house_nvd")
    rows = witness["plan"]["tasks"]
    final = next(row for row in rows if row["arguments"].get("observation_mode") == "final_clearance")
    last_work = next(row for row in rows if row["skill"] == "place")
    final["prerequisites"] = [last_work["task_id"]]
    final["execution"]["start_s"] = last_work["execution"]["start_s"] + last_work["execution"]["duration_s"]
    assert validate_goal_plan(witness["plan"], task_spec=public)["validated"]
    with pytest.raises(ValueError, match="all_work_and_ground_withdrawals_before_scan"):
        review_goal_business_witness(public, witness)


def test_outcome_cannot_be_bound_to_an_unrelated_early_observation():
    public, witness = _case("modern_house_nvd")
    initial_scan = next(row for row in witness["plan"]["tasks"] if row["skill"] == "observe")
    witness["outcome_support"]["belonging_staged"] = [initial_scan["task_id"]]
    assert validate_goal_plan(witness["plan"], task_spec=public)["validated"]
    with pytest.raises(ValueError, match="resident_bookstack_01:observed_then_relocated"):
        review_goal_business_witness(public, witness)


def test_legal_alternative_factory_return_route_is_accepted():
    public, witness = _case("factory")
    return_leg = next(row for row in witness["plan"]["tasks"]
                      if row["skill"] == "ground_navigation"
                      and row["arguments"]["region_id"] == "response_staging")
    return_leg["arguments"]["route_ids"] = ["stair_to_lower", "lower_to_staging"]
    assert review_goal_business_witness(public, witness)["validated"]


def test_legal_alternative_carrier_team_is_accepted():
    public, witness = _case("warehouse_nvd")
    original, replacement = "warehouse_ground_2", "warehouse_ground_3"
    plan = witness["plan"]
    plan["assigned_team"] = [replacement if agent == original else agent for agent in plan["assigned_team"]]
    for row in plan["tasks"]:
        row["assigned_agents"] = [replacement if agent == original else agent for agent in row["assigned_agents"]]
        for binding in row["arguments"].get("carrier_bindings", []):
            if binding["agent_id"] == original:
                binding["agent_id"] = replacement
    assert review_goal_business_witness(public, witness)["validated"]


def test_direct_hidden_validator_also_rejects_missing_business_receipt():
    bundle = build_goal_task_package("warehouse_nvd")
    hidden = deepcopy(bundle["hidden_evaluator"])
    witness = hidden["witness_plans"][0]
    receipt = next(row for row in witness["plan"]["tasks"]
                   if row["arguments"].get("observation_mode") == "dock_receiver_receipt")
    _remove_task(witness, receipt["task_id"])
    assert validate_goal_plan(witness["plan"], task_spec=bundle["public_task"])["validated"]
    with pytest.raises(ValueError, match="business necessary condition.*dock_receipt"):
        validate_goal_hidden_evaluator(hidden, task_spec=bundle["public_task"])


def test_new_business_outcome_cannot_receive_an_unreviewed_pass():
    from demo.benchmark.goal_driven_tasks import review_goal_task_design
    from demo.benchmark.provenance import canonical_sha256

    bundle = deepcopy(build_goal_task_package("warehouse_nvd"))
    public, hidden = bundle["public_task"], bundle["hidden_evaluator"]
    public["required_outcomes"].append({
        "id": "crate_returned_to_rack", "description": "Crate must return to its rack.",
        "acceptance": "Crate ends in rack_aisle_a.", "evidence_expectations": ["crate_pose"],
    })
    hidden["outcome_predicates"].append({
        "id": "crate_returned_to_rack", "predicate": "returned_to_rack",
        "evidence_types": ["crate_pose"], "failure_codes": ["wrong_region"],
    })
    hidden["public_task_sha256"] = canonical_sha256(public)
    for witness in hidden["witness_plans"]:
        witness["satisfies_outcomes"].append("crate_returned_to_rack")
        witness["outcome_support"]["crate_returned_to_rack"] = [witness["plan"]["tasks"][-1]["task_id"]]
    with pytest.raises(ValueError, match="every_outcome_requires_registered_business_review"):
        review_goal_task_design(public, hidden["witness_plans"])
    with pytest.raises(ValueError, match="every_outcome_requires_registered_business_review"):
        validate_goal_hidden_evaluator(hidden, task_spec=public)
