"""Fresh native diagnostics cannot silently weaken restored-state evidence."""
from dataclasses import replace
import json

import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, CheckpointArtifact
from tools.benchmark_branch.evidence import write_evaluator_evidence
from tools.benchmark_branch.initialization import (
    FRESH, SPEC_SCHEMA, initialization_receipt, validate_initialization_job,
    verify_initialization_receipt,
)
from tools.benchmark_branch.models import canonical_digest
from tools.benchmark_branch.tests.test_session_backend import _MeasuredAdapter, _SessionBackend, _scheduler
from tools.benchmark_branch.validation import validate_run


def job():
    spec = {"schema": SPEC_SCHEMA, "expected": {"box.z": .8, "mass": 8},
            "absolute_tolerances": {"box.z": .01, "mass": 0}}
    seed = BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0]
    return replace(seed, checkpoint_digest=canonical_digest(spec), metadata={
        "checkpoint_receipt_mode": FRESH, "initialization_spec": spec})


def test_observed_values_are_recomputed_including_failure():
    good = initialization_receipt(job(), {"box.z": .799, "mass": 8.})
    assert verify_initialization_receipt(job(), good) is True
    bad = initialization_receipt(job(), {"box.z": .5, "mass": 8.})
    assert verify_initialization_receipt(job(), bad) is False
    assert "restore_passed" not in good


@pytest.mark.parametrize("patch", [
    {"initialization_passed": 1}, {"initialization_passed": False},
    {"readback_digest": "forged"}, {"initialization_spec_digest": "forged"},
    {"readback": {"box.z": .5, "mass": 8}}, {"initialization_checks": {"box.z": 1, "mass": True}},
    {"restore_passed": True}, {"restore_passed": False}, {"restore_status": "passed"},
])
def test_receipt_tampering_rejected(patch):
    receipt = initialization_receipt(job(), {"box.z": .8, "mass": 8})
    with pytest.raises(ValueError):
        verify_initialization_receipt(job(), {**receipt, **patch})


@pytest.mark.parametrize("patch", [
    {"execution_scope": "measured"}, {"execution_scope": "protocol_synthetic"},
    {"branch_type": "reference"}, {"restore_checkpoint_digest": "fake"},
    {"checkpoint_digest": "fake"}, {"metadata": {"checkpoint_receipt_mode": "unknown"}},
])
def test_fresh_mode_is_limited_to_explicit_fact_diagnostics(patch):
    with pytest.raises(ValueError):
        validate_initialization_job(replace(job(), **patch), session_backend=True)


def test_fresh_mode_requires_session_backend():
    with pytest.raises(ValueError):
        validate_initialization_job(job(), session_backend=False)


@pytest.mark.parametrize("readback", [{}, {"box.z": .8}, {"box.z": float("nan"), "mass": 8},
                                      {"box.z": .8, "mass": True}])
def test_readback_is_complete_finite_scalar_data(readback):
    with pytest.raises(ValueError):
        initialization_receipt(job(), readback)


def collect(tmp_path, *, observed_z=.8, omit=False, late=False):
    initial = job()
    plan = BranchPlan(run_id=initial.run_id, slots=1, arms=("fact",),
                      execution_scope="measured_diagnostic", explicit_jobs=(initial,))
    store = BranchRunStore.create(tmp_path / "run", plan, execution_scope="measured_diagnostic")

    class Adapter(_MeasuredAdapter):
        def create_checkpoint(self, plan):
            return CheckpointArtifact.from_state(initial.metadata["initialization_spec"], schema=SPEC_SCHEMA)

    class Backend(_SessionBackend):
        def run_branch(self, runtime, current, *, emit):
            receipt = initialization_receipt(current, {"box.z": observed_z, "mass": 8})
            if not omit and not late:
                emit("checkpoints", receipt)
            emit("metrics", {"kind": "heartbeat"})
            emit("steps", {"simulator_step": 1, "first_tick": 1, "last_tick": 1})
            if late:
                emit("checkpoints", receipt)
            result = {"status": "valid_terminal", "completed": True, "physical_success": True,
                      "terminal_predicate": True, "safety_predicate": True, "feasibility_predicate": True}
            return {**result, **write_evaluator_evidence(store.root, current, evaluator_version="test-v1",
                                                        result=result, observations={"native": False})}

    BranchExecutor(plan, Adapter(), store, session_backend=Backend(), scheduler=_scheduler()).run()
    return store


def test_fresh_fact_diagnostic_preserves_no_restore_and_seals(tmp_path):
    store = collect(tmp_path)
    episode, = store.read_ledger("episodes")
    assert episode["status"] == "valid_terminal"
    assert episode["physical_success"] is True
    assert episode["restore_checkpoint_digest"] == ""
    assert "restore_passed" not in episode
    assert validate_run(store.root)["status"] == "passed"


@pytest.mark.parametrize("options", [{"observed_z": .4}, {"omit": True}])
def test_missing_or_failed_readback_never_admits_physical_success(tmp_path, options):
    store = collect(tmp_path, **options)
    episode, = store.read_ledger("episodes")
    assert episode["status"] == "infrastructure_invalid"
    assert episode["physical_success"] is None
    assert episode["infrastructure_reason"] == "missing_runtime_receipt"
    assert not store.read_ledger("steps")
    assert validate_run(store.root)["status"] == "passed"


def test_late_initialization_cannot_retroactively_qualify_execution(tmp_path):
    store = collect(tmp_path, late=True)
    episode, = store.read_ledger("episodes")
    assert episode["completed"] is False
    assert episode["physical_success"] is None


def test_offline_validation_recomputes_initialization(tmp_path):
    store = collect(tmp_path)
    path = store.root / "checkpoints.jsonl"
    rows = store.read_ledger("checkpoints")
    next(row for row in rows if row.get("branch_id"))["readback"]["box.z"] = 7
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert any("invalid initialization" in e for e in validate_run(store.root)["errors"])


def test_frozen_fresh_identity_is_explicit_and_legacy_restore_stays_required():
    from tools.experiment_analysis.registration import execution_identity
    original = job()
    original = replace(original, task_contract_digest="diagnostic", scene_build_digest="scene",
                       asset_resolution_digest="assets", scene_build_order_digest="order", skill_adapter_version="v1")
    frozen = execution_identity(original.identity())
    assert frozen["checkpoint_receipt_mode"] == FRESH
    assert frozen["initialization_spec_digest"] == original.checkpoint_digest
    assert execution_identity(frozen) == frozen
    for fields in ({"checkpoint_receipt_mode": "restore"}, {"initialization_spec_digest": "wrong"},
                   {"restore_checkpoint_digest": "made-up-restore"}):
        with pytest.raises(ValueError):
            execution_identity({**frozen, **fields})
    del frozen["checkpoint_receipt_mode"], frozen["initialization_spec_digest"]
    with pytest.raises(ValueError):
        execution_identity(frozen)


@pytest.mark.parametrize("mode", ["absent", "failed"])
def test_offline_missing_initialization_cannot_be_relabelled_method_crash(tmp_path, mode):
    store = collect(tmp_path, omit=mode == "absent", observed_z=.4 if mode == "failed" else .8)
    path = store.root / "episodes.jsonl"
    episode, = store.read_ledger("episodes")
    episode["status"] = "crashed"
    path.write_text(json.dumps(episode) + "\n")
    assert any("invalid initialization" in e for e in validate_run(store.root)["errors"])
