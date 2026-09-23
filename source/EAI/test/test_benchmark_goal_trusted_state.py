from copy import deepcopy

from demo.benchmark.goal_remaining_feasibility_oracle import freeze_oracle_registration
from demo.benchmark.goal_trusted_state import (
    GroundedRemainingTaskOracle, freeze_state_contract, ground_state,
    validate_state_contract,
)
from demo.benchmark.provenance import canonical_sha256


def _registration():
    model = {
        "fact_ids": ["at_rack", "at_dock", "receipt"],
        "terminal_all": ["receipt"],
        "resource_bounds": {"energy": 3},
        "transitions": [
            {"id": "carry", "requires_all": ["at_rack"], "requires_absent": [],
             "adds": ["at_dock"], "deletes": ["at_rack"],
             "capability_slots": [{"requires_all": ["carry"], "min_payload_kg": 1}],
             "resource_costs": {"energy": 1}},
            {"id": "receive", "requires_all": ["at_dock"], "requires_absent": [],
             "adds": ["receipt"], "deletes": [],
             "capability_slots": [{"requires_all": ["handoff"], "min_payload_kg": 0}],
             "resource_costs": {"energy": 1}},
        ],
    }
    return freeze_oracle_registration(
        model, task_sha256=canonical_sha256(_task()), measurement_points=[
            {"id": "initial", "trigger": "initial_deployment",
             "required_true": ["at_rack"], "required_false": ["at_dock", "receipt"],
             "varying_facts": []},
            {"id": "dock_before_receipt", "trigger": "milestone_state_change",
             "required_true": ["at_dock"], "required_false": ["at_rack", "receipt"],
             "varying_facts": []},
        ], validation={"status": "verified", "scope": "native_remaining_task",
                       "evidence_refs": ["model-review"]})


def _task():
    return {"task_id": "task", "mission": {"scene_key": "warehouse_nvd"},
            "required_outcomes": [{"id": "receipt"}]}


def _contract(reg):
    return freeze_state_contract(
        _task(), reg,
        predicate_bindings={
            "at_rack": {"source_id": "physx", "evidence_kind": "physical_completion", "entity_ids": ["crate"]},
            "at_dock": {"source_id": "physx", "evidence_kind": "physical_completion", "entity_ids": ["crate"]},
            "receipt": {"source_id": "receipt", "evidence_kind": "business_receipt", "entity_ids": ["crate"]},
        },
        outcome_predicates={"receipt": ["receipt"]},
        source_versions={"physx": "physx-v1", "receipt": "receipt-v1"},
        entity_ids=["crate"], asset_manifest_sha256="a" * 64,
        skill_contract_sha256="b" * 64)


def _state(facts, *, time_s=2):
    return {"time_s": time_s, "true_facts": facts,
            "entity_versions": {"crate": 1}}


def _readbacks(state, measurement_id, facts):
    common = {"measurement_id": measurement_id, "state_sha256": canonical_sha256(state),
              "measured_at_s": state["time_s"], "entity_versions": {"crate": 1},
              "evidence_refs": ["trusted-readback"]}
    return {
        "physx": {**common, "source_id": "physx", "source_version": "physx-v1",
                   "predicates": {"at_rack": "at_rack" in facts, "at_dock": "at_dock" in facts}},
        "receipt": {**common, "source_id": "receipt", "source_version": "receipt-v1",
                     "predicates": {"receipt": "receipt" in facts}},
    }


def test_state_contract_binds_all_facts_and_never_promotes_release_flags():
    reg = _registration()
    contract = _contract(reg)
    validate_state_contract(contract, reg)
    assert contract["scoreable"] is False
    assert contract["registration_sha256"] == reg["registration_sha256"]


def test_grounding_accepts_explicit_intermediate_state():
    reg = _registration(); contract = _contract(reg); state = _state(["at_dock"])
    result = ground_state(contract, reg, measurement_id="dock_before_receipt",
                          state=state, readbacks=_readbacks(state, "dock_before_receipt", ["at_dock"]))
    assert result["status"] == "verified"


def test_grounding_rejects_missing_or_tampered_source_without_label():
    reg = _registration(); contract = _contract(reg); state = _state(["at_dock"])
    packets = _readbacks(state, "dock_before_receipt", ["at_dock"])
    packets["receipt"]["predicates"] = {}
    result = ground_state(contract, reg, measurement_id="dock_before_receipt",
                          state=state, readbacks=packets)
    assert result["status"] == "failed"
    assert result["scoreable"] is False


def test_contract_tamper_fails_closed():
    reg = _registration(); contract = _contract(reg)
    changed = deepcopy(contract); changed["entity_ids"] = ["other"]
    try:
        validate_state_contract(changed, reg)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered state contract was accepted")


def test_grounded_oracle_preserves_solver_as_only_label_source():
    reg = _registration(); contract = _contract(reg)
    state = _state(["at_dock"])
    def provider(snapshot):
        return {"measurement_id": "dock_before_receipt", "state": state}
    def reader(snapshot, observation):
        return _readbacks(state, "dock_before_receipt", ["at_dock"])[snapshot.get("source_id", "physx")]
    # Deliberately make readers fail closed: construction succeeds, but no
    # participant-facing binary result can be inferred from empty readbacks.
    oracle = GroundedRemainingTaskOracle(reg, contract, provider,
                                         {"physx": lambda *_: {}, "receipt": lambda *_: {}})
    report = oracle.report()
    assert report["scoreable"] is False
    assert report["results"] == []
