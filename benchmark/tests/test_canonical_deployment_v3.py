from __future__ import annotations

import copy

import pytest

from benchmark.canonical_deployment_v3 import (
    BACKEND_ID, SCHEMA, ContractError, Limits, Status,
    check_infeasibility_proof, check_witness, from_mapping, solve,
)


def package(**changes):
    value = {
        "schema": SCHEMA, "task_id": "instance-a", "horizon_ticks": 10, "tick_dt_s": 0.02,
        "team_bounds": {"min": 1, "max": 2},
        "robots": [
            {"id": "r1", "capabilities": ["carry"], "roles": ["carrier", "sender"]},
            {"id": "r2", "capabilities": ["carry"], "roles": ["receiver"]},
        ],
        "obligations": [{"id": "o1", "required_capabilities": ["carry"],
                          "role_requirements": {"carrier": [1, 1]},
                          "participant_bounds": [1, 1],
                          "duration_ticks": 2, "release_tick": 0, "deadline_tick": 8,
                          "participant_tick_offsets": [0, 0], "sync_window_ticks": 0,
                          "resource_demands": {}}],
        "resources": [], "temporal_edges": [], "mutex_pairs": [],
    }
    value.update(changes)
    return value


def test_explicit_contract_produces_checkable_witness_and_nonzero_participant_search():
    c = from_mapping(package())
    result = solve(c)
    assert result.status is Status.DEPLOYABLE
    assert check_witness(c, result.witness) == (True, ())
    assert result.witness["tau_rv"]["o1"]["r1"] == result.witness["tau_o"]["o1"]
    assert result.witness["z"]["r1"] == 1


def test_participant_tick_offset_is_searched_and_checked():
    c = from_mapping(package(obligations=[{**package()["obligations"][0], "participant_tick_offsets": [1, 1]}]))
    result = solve(c)
    assert result.status is Status.DEPLOYABLE
    w = result.witness
    assert w["tau_rv"]["o1"]["r1"] == w["tau_o"]["o1"] + 1
    bad = copy.deepcopy(w); bad["tau_rv"]["o1"]["r1"] -= 1
    assert check_witness(c, bad)[0] is False


def test_coalition_bounds_are_not_replaced_by_last_role_bounds():
    raw = package()
    raw["obligations"][0]["participant_bounds"] = [2, 2]
    raw["obligations"][0]["role_requirements"] = {"sender": [1, 1], "receiver": [1, 1]}
    contract = from_mapping(raw)
    assert (contract.obligations[0].min_participants, contract.obligations[0].max_participants) == (2, 2)
    result = solve(contract)
    assert result.status is Status.DEPLOYABLE
    assert sum(result.witness["u"]["o1"].values()) == 2
    assert check_witness(contract, result.witness) == (True, ())


def test_coalition_upper_bound_can_make_individually_valid_roles_infeasible():
    raw = package()
    raw["obligations"][0]["participant_bounds"] = [1, 1]
    raw["obligations"][0]["role_requirements"] = {"sender": [1, 1], "receiver": [1, 1]}
    assert solve(from_mapping(raw)).status is Status.INFEASIBLE


def test_role_and_capability_mutations_fail_closed():
    c = from_mapping(package())
    w = solve(c).witness
    bad = copy.deepcopy(w); bad["v"]["r1"]["carrier"] = 0
    assert check_witness(c, bad)[0] is False
    bad = copy.deepcopy(w); bad["u"]["o1"]["r1"] = 0
    assert check_witness(c, bad)[0] is False


def test_p2_handoff_requires_explicit_links_and_windows():
    p = package(
        obligations=[
            {**package()["obligations"][0], "id": "send", "role_requirements": {"sender": [1, 1]}},
            {**package()["obligations"][0], "id": "recv", "role_requirements": {"receiver": [1, 1]}},
        ],
        temporal_edges=[{"id": "h", "before": "send", "after": "recv", "kind": "handoff",
                         "min_lag": 0, "max_lag": 4, "release_min": 0, "release_max": 1,
                         "acceptance_min": 0, "acceptance_max": 1, "ownership_change": True}],
    )
    c = from_mapping(p); result = solve(c)
    assert result.status is Status.DEPLOYABLE
    bad = copy.deepcopy(result.witness); bad["handoffs"]["h"]["to_robot"] = "r1"
    assert check_witness(c, bad)[0] is False


def test_p4_ordering_literals_are_required_and_verified():
    base = package()
    second = {**base["obligations"][0], "id": "o2", "role_requirements": {"receiver": [1, 1]}}
    p = package(obligations=[base["obligations"][0], second], mutex_pairs=[["o1", "o2"]])
    c = from_mapping(p); result = solve(c, Limits(max_candidates=10000))
    assert result.status is Status.DEPLOYABLE
    bad = copy.deepcopy(result.witness); bad["b"]["o1|o2"] = {"forward": 0, "reverse": 0}
    assert check_witness(c, bad)[0] is False


def test_budget_is_unverified_and_tampered_proof_rejected():
    first = package()["obligations"][0]
    second = {**first, "id": "o2"}
    c = from_mapping(package(obligations=[first, second], temporal_edges=[
        {"id": "impossible-lag", "before": "o1", "after": "o2", "kind": "precedence",
         "min_lag": 3, "max_lag": 3}]))
    result = solve(c, Limits(max_candidates=1))
    assert result.status is Status.UNVERIFIED and result.proof is None
    impossible = from_mapping(package(team_bounds={"min": 1, "max": 1}, robots=[{"id": "r1", "capabilities": ["other"], "roles": ["carrier"]}]))
    result = solve(impossible)
    assert result.status is Status.INFEASIBLE
    assert check_infeasibility_proof(impossible, result.proof)
    proof = dict(result.proof); proof["candidate_count"] += 1
    assert not check_infeasibility_proof(impossible, proof)


def test_infeasibility_replay_is_independent_of_producer_helpers(monkeypatch):
    import benchmark.canonical_deployment_v3 as producer

    deployable = from_mapping(package())
    deployable_witness = solve(deployable).witness
    impossible = from_mapping(package(
        team_bounds={"min": 1, "max": 1},
        robots=[{"id": "r1", "capabilities": ["other"], "roles": ["carrier"]}],
    ))
    result = solve(impossible)
    assert result.status is Status.INFEASIBLE
    for name in ("_domain", "_witness", "_check", "_eligible", "_role_maps"):
        monkeypatch.setattr(producer, name, lambda *args, _name=name, **kwargs: pytest.fail(_name))
    assert check_infeasibility_proof(impossible, result.proof)
    assert check_witness(deployable, deployable_witness) == (True, ())


def test_infeasibility_replay_rejects_wall_clock_budget_exhaustion():
    base = package()
    obligation = {**base["obligations"][0], "resource_demands": {"station": 2}}
    contract = from_mapping(package(
        obligations=[obligation], resources=[{"id": "station", "capacity": 1}],
    ))
    result = solve(contract, Limits(max_candidates=100_000, max_seconds=5.0))
    assert result.status is Status.INFEASIBLE
    assert check_infeasibility_proof(contract, result.proof)
    assert not check_infeasibility_proof(
        contract, result.proof, Limits(max_candidates=100_000, max_seconds=1e-12)
    )


@pytest.mark.parametrize("bad", [
    {"schema": SCHEMA, "task_id": "x"},
    {"schema": SCHEMA, "task_id": "x", "tick_dt_s": 0.01},
])
def test_missing_or_unsupported_contract_is_rejected(bad):
    with pytest.raises(ContractError):
        from_mapping(bad)
