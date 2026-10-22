from __future__ import annotations
import copy
import pytest

from benchmark.canonical_deployment_v3 import Status, from_mapping, solve
from benchmark.canonical_deployment_proof_v3 import CheckerLimits, verify_infeasibility_proof, verify_witness
from benchmark.tests.test_canonical_deployment_v3 import package


def impossible_package():
    p = package(); first = {**p["obligations"][0], "deadline_tick": 2}; second = {**first, "id": "o2"}
    return package(robots=[{"id": "r1", "capabilities": ["carry"], "roles": ["carrier"]}],
                   team_bounds={"min": 1, "max": 1}, obligations=[first, second], mutex_pairs=[["o1", "o2"]])


def test_nonempty_mutex_proof_is_replayed_independently():
    contract = from_mapping(impossible_package()); result = solve(contract)
    assert result.status is Status.INFEASIBLE
    assert verify_infeasibility_proof(contract, result.proof)


def test_tampered_source_count_is_metadata_only_but_digest_tamper_rejected():
    contract = from_mapping(impossible_package()); proof = dict(solve(contract).proof)
    proof["candidate_count"] += 100
    # candidate_count is producer-source metadata and is not used as checker coverage.
    proof["proof_digest"] = __import__("hashlib").sha256(__import__("json").dumps({k: v for k, v in proof.items() if k != "proof_digest"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert verify_infeasibility_proof(contract, proof)
    proof["contract_digest"] = "tampered"
    assert not verify_infeasibility_proof(contract, proof)


def test_fake_empty_domain_proof_for_feasible_contract_is_rejected():
    contract = from_mapping(package()); result = solve(contract)
    assert result.status is Status.DEPLOYABLE
    fake = {"schema": result.witness["schema"], "kind": "exhaustive_no_witness", "contract_digest": contract.digest(),
            "backend_id": "bounded-explicit-contract-v3", "checker_backend_id": "bounded-independent-flat-checker-v1",
            "checker_policy": "flat-team-role-participant-tick-v1", "candidate_count": 0,
            "max_candidates": 1, "max_seconds": 1.0, "search_exhausted": True, "complete": True}
    import hashlib, json
    fake["proof_digest"] = hashlib.sha256(json.dumps(fake, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert not verify_infeasibility_proof(contract, fake)


def test_checker_does_not_call_producer_helpers(monkeypatch):
    import benchmark.canonical_deployment_v3 as producer
    contract = from_mapping(impossible_package()); result = solve(contract)
    for name in ("_domain", "_witness", "_check", "_eligible", "_role_maps", "solve"):
        if hasattr(producer, name):
            monkeypatch.setattr(producer, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError(name)))
    assert verify_infeasibility_proof(contract, result.proof)


def test_witness_replay_survives_producer_fault_injection(monkeypatch):
    import benchmark.canonical_deployment_v3 as producer
    contract = from_mapping(package()); witness = solve(contract).witness
    monkeypatch.setattr(producer, "_check", lambda *a, **k: (_ for _ in ()).throw(AssertionError("producer check")))
    monkeypatch.setattr(producer, "_witness", lambda *a, **k: (_ for _ in ()).throw(AssertionError("producer witness")))
    assert verify_witness(contract, witness)[0]


def test_internal_domain_and_candidate_budget_fail_closed():
    contract = from_mapping(impossible_package()); proof = solve(contract).proof
    assert not verify_infeasibility_proof(contract, proof, CheckerLimits(max_candidates=1, max_seconds=5.0, max_domain_items=1))
    assert not verify_infeasibility_proof(contract, proof, CheckerLimits(max_candidates=1000, max_seconds=1e-12, max_domain_items=1000))


@pytest.mark.parametrize("seconds", [True, float("nan"), float("inf"), -1])
def test_invalid_checker_budget_never_certifies(seconds):
    contract = from_mapping(impossible_package())
    assert not verify_infeasibility_proof(contract, solve(contract).proof, CheckerLimits(max_seconds=seconds))


def test_producer_false_empty_domain_cannot_certify_a_feasible_contract(monkeypatch):
    import benchmark.canonical_deployment_v3 as producer
    contract = from_mapping(package())
    monkeypatch.setattr(producer, "_domain", lambda _: iter(()))
    wrong_result = producer.solve(contract)
    assert wrong_result.status is Status.INFEASIBLE
    assert not verify_infeasibility_proof(contract, wrong_result.proof)


def test_checker_enumerates_incompatible_roles_before_rejecting():
    from benchmark.canonical_deployment_proof_v3 import _Budget, _flat_candidates
    raw = package(robots=[{"id": "r1", "capabilities": ["other"], "roles": ["other"]}],
                  team_bounds={"min": 1, "max": 1})
    contract = from_mapping(raw)
    # The producer prunes all candidates; the independent domain must not do so.
    assert solve(contract).explored == 0
    budget = _Budget(CheckerLimits())
    assert next(_flat_candidates(contract, budget))
    assert verify_infeasibility_proof(contract, solve(contract).proof)


@pytest.mark.parametrize("field", ["u", "tau_rv", "role_links"])
def test_malformed_witness_rows_are_rejected(field):
    contract = from_mapping(package())
    witness = copy.deepcopy(solve(contract).witness)
    witness[field]["o1"] = None
    assert verify_witness(contract, witness)[0] is False


def test_half_open_resource_sweep_accepts_adjacent_and_rejects_overlap():
    raw = impossible_package()
    raw["mutex_pairs"] = []
    raw["resources"] = [{"id": "station", "capacity": 1}]
    for obligation in raw["obligations"]:
        obligation["deadline_tick"] = 4
        obligation["resource_demands"] = {"station": 1}
    contract = from_mapping(raw)
    witness = copy.deepcopy(solve(contract).witness)
    assert verify_witness(contract, witness)[0]
    witness["tau_o"]["o2"] = witness["tau_o"]["o1"]
    witness["tau_rv"]["o2"] = dict(witness["tau_rv"]["o1"])
    assert not verify_witness(contract, witness)[0]
