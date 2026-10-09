import pytest

from tools.benchmark_branch.contracts import TaskContract, evaluator_result


def _contract():
    return {
        "schema": "task.contract.v1", "task_id": "task", "scene_id": "scene", "domain": "service",
        "task_version": "task.v1", "objectives": ["finish"],
        "dag": [{"id": "start", "prerequisites": []}],
        "milestones": [{"id": "finish", "weight": 1.0, "stable_window": 2, "predicate": "done"}],
        "capability_requirements": {}, "role_constraints": {}, "observation_permissions": {},
        "resource_locks": {}, "safety_constraints": {}, "fixed_timestep": 0.01, "horizon": 20,
        "deadlines": {}, "termination_predicates": ["finish"],
        "treatment": {"fact": {}, "reference": {}, "sham": {}, "intervention": {}},
        "nuisance_factors": {}, "intervention_points": ["tick-5"],
        "validity_thresholds": {"restore_max_error": 1e-6, "sham_max_error": 1e-6, "reference_denominator_floor": 1},
        "feasibility_oracle": {"status": "deployable", "witness_proof_hash": "proof"},
        "skill_adapter_version": "skill.v1", "non_target_budget": {}, "public_exogenous_events": [],
    }


def test_task_contract_is_frozen_and_evaluator_does_not_infer_success():
    contract = TaskContract.freeze(_contract())
    assert len(contract.digest) == 64
    invalid = evaluator_result({"status": "valid_terminal"}, contract, "protocol_synthetic")
    assert invalid["status"] == "not_evaluable"


def test_contract_rejects_cyclic_dag():
    data = _contract(); data["dag"] = [{"id": "a", "prerequisites": ["b"]}, {"id": "b", "prerequisites": ["a"]}]
    with pytest.raises(ValueError, match="cyclic"):
        TaskContract.freeze(data)
