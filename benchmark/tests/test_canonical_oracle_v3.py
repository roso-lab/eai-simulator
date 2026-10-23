from __future__ import annotations

import dataclasses

import pytest

from benchmark.canonical_oracle_v3 import (
    CanonicalContractV3,
    ObligationSpecV3,
    OracleResultV3,
    OracleStatus,
    PrecedenceV3,
    ResourceSpecV3,
    RobotSpecV3,
    check_infeasibility_proof,
    check_witness,
    solve_canonical,
)


def _contract(**changes):
    value = dict(
        task_id="case",
        horizon_ticks=12,
        robots=(
            RobotSpecV3("r1", ("move",), ("worker",)),
            RobotSpecV3("r2", ("move",), ("worker",)),
        ),
        obligations=(
            ObligationSpecV3("a", "move", "worker", 2, 0, 8),
            ObligationSpecV3("b", "move", "worker", 2, 0, 12),
        ),
    )
    value.update(changes)
    return CanonicalContractV3(**value)


def test_exact_backend_produces_independently_checkable_witness():
    result = solve_canonical(_contract(), node_budget=10000)
    assert result.status is OracleStatus.DEPLOYABLE
    assert result.complete is True
    assert result.witness is not None
    assert check_witness(_contract(), result.witness) == (True, ())
    assert result.witness["contract_digest"] == _contract().digest()
    certificate = result.to_certificate(_contract())
    assert certificate.status.value == "feasibility_unverified"
    assert "paper_canonical_contract_incomplete" in certificate.limitations


def test_witness_checker_rejects_changed_role_and_digest_independently():
    contract = _contract()
    result = solve_canonical(contract)
    witness = dict(result.witness)
    assignments = {key: list(value) for key, value in witness["assignments"].items()}
    assignments["a"] = ["missing"]
    witness["assignments"] = assignments
    assert check_witness(contract, witness)[0] is False
    assert witness["contract_digest"] == contract.digest()


def test_pairwise_joint_sync_window_is_checked():
    contract = _contract(obligations=(ObligationSpecV3("joint", "move", "worker", 2, 0, 8,
                                                        min_participants=2, max_participants=2,
                                                        sync_window_ticks=1),))
    witness = {
        "schema": "heterocoord.canonical_oracle.v3", "contract_digest": contract.digest(),
        "team": ["r1", "r2"], "assignments": {"joint": ["r1", "r2"]},
        "starts": {"joint": 1}, "participant_ticks": {"joint": {"r1": 0, "r2": 2}},
    }
    assert check_witness(contract, witness)[0] is False


def test_resource_and_mutex_constraints_force_serialization():
    contract = _contract(
        resources=(ResourceSpecV3("dock", 1),),
        obligations=(
            ObligationSpecV3("a", "move", "worker", 3, 0, 3, resource_demands={"dock": 1}),
            ObligationSpecV3("b", "move", "worker", 3, 0, 6, resource_demands={"dock": 1}),
        ),
        mutex_pairs=(("a", "b"),),
        horizon_ticks=6,
    )
    result = solve_canonical(contract)
    assert result.status is OracleStatus.DEPLOYABLE
    assert result.witness["starts"]["b"] >= result.witness["starts"]["a"] + 3 or result.witness["starts"]["a"] >= result.witness["starts"]["b"] + 3


def test_joint_coalition_requires_two_role_compatible_members():
    one_robot = _contract(
        robots=(RobotSpecV3("r1", ("move",), ("worker",)),),
        obligations=(ObligationSpecV3("joint", "move", "worker", 2, 0, 6, min_participants=2, max_participants=2),),
        horizon_ticks=6,
    )
    result = solve_canonical(one_robot)
    assert result.status is OracleStatus.TEAM_STATE_INFEASIBLE
    assert result.complete is True
    assert check_infeasibility_proof(one_robot, result.proof)
    assert result.to_certificate(one_robot).status.value == "feasibility_unverified"


def test_precedence_is_integer_tick_and_completion_deadline_is_enforced():
    contract = _contract(
        obligations=(
            ObligationSpecV3("a", "move", "worker", 3, 0, 3),
            ObligationSpecV3("b", "move", "worker", 2, 0, 8),
        ),
        precedences=(PrecedenceV3("a", "b", min_lag_ticks=1, max_lag_ticks=1),),
    )
    result = solve_canonical(contract)
    assert result.status is OracleStatus.DEPLOYABLE
    assert result.witness["starts"]["b"] == result.witness["starts"]["a"] + 4


def test_node_budget_exhaustion_is_unverified_and_has_no_proof():
    result = solve_canonical(_contract(), node_budget=1)
    assert result.status is OracleStatus.UNVERIFIED
    assert result.complete is False
    assert result.proof is None
    assert result.to_certificate(_contract()).status.value == "feasibility_unverified"


def test_tampered_infeasibility_proof_is_rejected():
    contract = _contract(
        robots=(RobotSpecV3("r1", ("move",), ("worker",)),),
        obligations=(ObligationSpecV3("joint", "move", "worker", 2, 0, 6, min_participants=2, max_participants=2),),
        horizon_ticks=6,
    )
    result = solve_canonical(contract)
    proof = dict(result.proof)
    proof["explored_nodes"] += 1
    assert check_infeasibility_proof(contract, proof) is False


def test_infeasibility_proof_checker_does_not_call_producer(monkeypatch):
    contract = _contract(
        robots=(RobotSpecV3("r1", ("move",), ("worker",)),),
        obligations=(ObligationSpecV3("joint", "move", "worker", 2, 0, 6, min_participants=2, max_participants=2),),
        horizon_ticks=6,
    )
    result = solve_canonical(contract)
    monkeypatch.setattr("benchmark.canonical_oracle_v3.solve_canonical", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("producer called")))
    assert check_infeasibility_proof(contract, result.proof)


def test_contract_rejects_non_integer_horizon_and_unknown_resources():
    with pytest.raises(ValueError):
        _contract(horizon_ticks=2.5)
    with pytest.raises(ValueError):
        _contract(obligations=(ObligationSpecV3("a", "move", "worker", 2, 0, 8, resource_demands={"missing": 1}),))


@pytest.mark.parametrize("infeasible", [False, True])
def test_hand_built_result_cannot_enter_certificate_adapter(infeasible):
    contract = _contract()
    if infeasible:
        contract = _contract(obligations=(ObligationSpecV3("a", "fly", "worker", 2, 0, 8),))
    result = solve_canonical(contract)
    forged = OracleResultV3(result.task_id, result.status, result.backend_id,
                           result.contract_digest, result.witness, result.proof,
                           result.explored_nodes, result.complete, result.reasons)
    with pytest.raises(ValueError, match="not produced"):
        forged.to_certificate(contract)


def test_team_size_infeasibility_is_independently_checkable():
    contract = _contract(
        robots=(RobotSpecV3("r1", ("move",), ("worker",)),
                RobotSpecV3("r2", ("fly",), ("worker",))),
        obligations=(ObligationSpecV3("a", "move", "worker", 2, 0, 2),
                     ObligationSpecV3("b", "fly", "worker", 2, 0, 2)),
        max_team_size=1,
    )
    result = solve_canonical(contract)
    assert result.status is OracleStatus.TEAM_STATE_INFEASIBLE
    assert check_infeasibility_proof(contract, result.proof)


def test_robot_inputs_are_detached_and_serializable():
    capabilities, roles = ["move"], ["worker"]
    robot = RobotSpecV3("r1", capabilities, roles)
    capabilities.append("fly")
    roles.clear()
    contract = _contract(robots=(robot,))
    assert contract.to_dict()["robots"] == [
        {"robot_id": "r1", "capabilities": ["move"], "roles": ["worker"]}
    ]


def test_certificate_rechecks_witness_after_mutation():
    contract = _contract()
    result = solve_canonical(contract)
    result.witness["starts"]["a"] = -1
    with pytest.raises(ValueError, match="witness failed"):
        result.to_certificate(contract)
