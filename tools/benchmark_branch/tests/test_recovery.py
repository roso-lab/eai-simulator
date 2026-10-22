"""Measured reruns must preserve the registered risk set and failed outcomes."""
from dataclasses import replace
import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, DummyAdapter
from tools.benchmark_branch.models import CheckpointArtifact
from tools.benchmark_branch.recovery import SCHEMA, decide_recovery, recovery_registration, verify_recovery_readback
from tools.benchmark_branch.validation import validate_run
from tools.benchmark_branch.tests.test_runtime import ContractAdapter, ContractBackend
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler


def setup_run(tmp_path, *, registration=True, prior=None):
    plan = BranchPlan(run_id="recovery-run", slots=1, arms=("fact",), execution_scope="measured_diagnostic")
    checkpoint = ContractAdapter().create_checkpoint(plan)
    rule = {"schema": SCHEMA, "mode": "same_checkpoint_random_stream", "registration_id": "registered-rule",
            "checkpoint_digest": checkpoint.digest, "random_stream_digest": "rng-state-hash", "max_attempts": 2,
            "infrastructure_reasons": ["preflight_rejected", "simulator_failure"],
            "participant_state_digests": {name: f"{name}-hash" for name in (
                "world", "clock", "controller", "skill", "task", "algorithm",
                "transport", "locks", "events", "rng")}}
    job = replace(plan.jobs()[0], checkpoint_digest=checkpoint.digest, restore_checkpoint_digest=checkpoint.digest,
                  metadata={"registered_recovery": rule} if registration else {})
    plan = replace(plan, explicit_jobs=(job,))
    store = BranchRunStore.create(tmp_path / "run", plan, execution_scope="measured_diagnostic")
    store.update_manifest(execution_backend="session_backend")
    store.append("branch_manifest", job.identity(), job=job)
    store.append("episodes", prior or {"status": "infrastructure_invalid", "completed": False,
                                      "physical_success": None, "infrastructure_preflight": True,
                                      "infrastructure_reason": "preflight_rejected"}, job=job)
    store.finish()
    return plan, job, BranchRunStore.resume(store.root)


def executor(store, backend=None):
    return BranchExecutor(store.plan, ContractAdapter(), store,
                          session_backend=backend or ContractBackend(store.root),
                          scheduler=ReplayScheduler(ReplayCapacity(1, 1, 1)), requested_device="cpu")


@pytest.mark.parametrize("status", ["method_timeout", "malformed_action", "budget_overrun", "missing_response",
                                   "safe_stop", "team_state_infeasible", "right_censored", "method_failure"])
def test_method_outcomes_are_not_retried_even_with_registration(tmp_path, status):
    _, job, store = setup_run(tmp_path, prior={"status": status, "completed": False})
    ex = executor(store)
    assert ex._jobs_to_run() == []
    assert store.manifest["resume_decisions"][0]["reason"] == "method_outcome_retained"
    store.finish()


def test_method_failure_survives_later_infrastructure_record(tmp_path):
    _, job, store = setup_run(tmp_path, prior={"status": "method_timeout", "completed": False})
    store.append("episodes", {"status": "infrastructure_invalid", "completed": False}, job=job)
    assert executor(store)._jobs_to_run() == []
    store.finish()


def test_unknown_and_unregistered_reruns_require_upstream_decision(tmp_path):
    _, job, store = setup_run(tmp_path, registration=False)
    assert executor(store)._jobs_to_run() == []
    assert store.manifest["resume_decisions"][0]["reason"] == "registered_recovery_required"
    assert not decide_recovery(job, {"status": "crashed", "completed": False}, next_attempt=1).retry
    assert not decide_recovery(job, None, next_attempt=1).retry
    store.finish()


def test_registered_infrastructure_rerun_preserves_identity_and_respects_limit(tmp_path):
    _, job, store = setup_run(tmp_path)
    selected = executor(store)._jobs_to_run()
    assert len(selected) == 1
    assert selected[0].attempt == 1
    assert selected[0].base_instance_id == job.base_instance_id
    assert selected[0].pair_group_id == job.pair_group_id
    assert selected[0].rng_namespace == job.rng_namespace
    prior = {"status": "infrastructure_invalid", "completed": False,
             "method_status": "infrastructure_invalid", "method_completed": False,
             "infrastructure_reason": "simulator_failure"}
    assert decide_recovery(job, prior, next_attempt=1).retry
    assert not decide_recovery(job, prior, next_attempt=2).retry
    store.finish()


class RecoveryBackend(ContractBackend):
    def __init__(self, root, *, wrong_stream=False, late_receipt=False, later_checkpoint=False):
        super().__init__(root)
        self.wrong_stream, self.late_receipt = wrong_stream, late_receipt
        self.later_checkpoint = later_checkpoint

    def run_branch(self, runtime, job, *, emit):
        if self.late_receipt:
            emit("steps", {"simulator_step": 1})
        rule = job.metadata["registered_recovery"]
        emit("checkpoints", {"restore_passed": True, "readback_digest": job.checkpoint_digest,
                             "recovery_registration_id": rule["registration_id"],
                             "random_stream_digest": "other" if self.wrong_stream else rule["random_stream_digest"],
                             "recovery_mode": rule["mode"],
                             "participant_state_digests": rule["participant_state_digests"]})
        emit("metrics", {"kind": "heartbeat"})
        emit("steps", {"simulator_step": 1})
        if self.later_checkpoint:
            emit("checkpoints", {"kind": "capture_diagnostic", "simulator_step": 1})
        return {"status": "method_timeout", "completed": False, "physical_success": None}


def test_registered_recovery_requires_live_readback_and_preserves_failure(tmp_path):
    _, _, store = setup_run(tmp_path)
    result = executor(store, RecoveryBackend(store.root)).run()
    assert result["failed"] == 1
    episodes = store.read_ledger("episodes")
    assert len(episodes) == 2
    assert episodes[-1]["status"] == "method_timeout"
    assert episodes[-1]["attempt"] == 1
    report = validate_run(store.root)
    assert report["status"] == "passed", report["errors"]
    resumed = BranchRunStore.resume(store.root)
    ex = executor(resumed)
    ex._checkpoint = lambda: (_ for _ in ()).throw(AssertionError("no-op resume captured checkpoint"))
    assert ex.run()["results"] == []
    assert len(store.read_ledger("episodes")) == 2


@pytest.mark.parametrize("options", [{"wrong_stream": True}, {"late_receipt": True}])
def test_bad_recovery_cannot_emit_steps(tmp_path, options):
    _, _, store = setup_run(tmp_path)
    assert executor(store, RecoveryBackend(store.root, **options)).run()["failed"] == 1
    assert store.read_ledger("steps") == []


def test_offline_validation_rejects_retry_after_method_failure(tmp_path):
    _, job, store = setup_run(tmp_path, prior={"status": "method_timeout", "completed": False})
    retry = job.with_attempt(1)
    store.append("branch_manifest", retry.identity(), job=retry)
    store.append("episodes", {"status": "infrastructure_invalid", "completed": False,
                              "infrastructure_preflight": True}, job=retry)
    store.finish()
    report = validate_run(store.root)
    assert report["status"] == "failed"
    assert any("method_outcome_retained" in error for error in report["errors"])


def test_fresh_process_retry_cannot_be_registered_as_old_pair(tmp_path):
    _, job, store = setup_run(tmp_path)
    job = replace(job, metadata={"registered_recovery": {**job.metadata["registered_recovery"], "mode": "fresh_process"}})
    with pytest.raises(ValueError, match="new upstream registration"):
        decide_recovery(job, {"status": "infrastructure_invalid"}, next_attempt=1)
    store.finish()


def test_measured_resume_counts_keep_previously_retained_method_failure(tmp_path):
    plan, job, store = setup_run(tmp_path)
    second = replace(job, branch_id=job.branch_id.replace("/fact#", "/sham#"), branch_type="sham")
    # Build a separate frozen two-job run; the first fixture is only a source
    # of registered checkpoint metadata and is not modified after sealing.
    store.finish()
    plan = replace(plan, explicit_jobs=(job, second))
    both = BranchRunStore.create(tmp_path / "both", plan, execution_scope="measured_diagnostic")
    both.update_manifest(execution_backend="session_backend")
    for item in (job, second):
        both.append("branch_manifest", item.identity(), job=item)
    both.append("episodes", {"status": "method_timeout", "completed": False}, job=job)
    both.append("episodes", {"status": "infrastructure_invalid", "completed": False,
                             "infrastructure_reason": "preflight_rejected", "infrastructure_preflight": True}, job=second)
    both.finish()
    resumed = BranchRunStore.resume(both.root)
    result = executor(resumed, RecoveryBackend(resumed.root)).run()
    assert result["logical_branches"] == 2
    assert result["failed"] == 2
    assert resumed.manifest["requested"] == resumed.manifest["completed"] + resumed.manifest["failed"] == 2
    assert result["last_execution"]["scheduled"] == result["last_execution"]["failed"] == 1
    assert len(resumed.read_ledger("episodes")) == 3


def test_recovery_rejects_unregistered_infrastructure_reason_or_participant(tmp_path):
    _, job, store = setup_run(tmp_path)
    decision = decide_recovery(job, {"status": "infrastructure_invalid", "completed": False,
                                     "infrastructure_reason": "not-registered"}, next_attempt=1)
    assert not decision.retry
    rule = job.metadata["registered_recovery"]
    receipt = {"restore_passed": True, "readback_digest": job.checkpoint_digest,
               "recovery_registration_id": rule["registration_id"], "recovery_mode": rule["mode"],
               "random_stream_digest": rule["random_stream_digest"], "participant_state_digests": {"world": "world-hash"}}
    assert not verify_recovery_readback(job, receipt)
    store.finish()


@pytest.mark.parametrize("missing", ["world", "clock", "controller", "skill", "task", "algorithm",
                                     "transport", "locks", "events", "rng"])
def test_recovery_registration_requires_all_causal_state_participants(tmp_path, missing):
    _, job, store = setup_run(tmp_path)
    rule = job.metadata["registered_recovery"]
    partial = {name: digest for name, digest in rule["participant_state_digests"].items() if name != missing}
    job = replace(job, metadata={"registered_recovery": {**rule, "participant_state_digests": partial}})
    with pytest.raises(ValueError, match="missing participants"):
        recovery_registration(job)
    store.finish()


def test_later_checkpoint_does_not_invalidate_initial_recovery_receipt(tmp_path):
    _, _, store = setup_run(tmp_path)
    executor(store, RecoveryBackend(store.root, later_checkpoint=True)).run()
    report = validate_run(store.root)
    assert report["status"] == "passed", report["errors"]
    # A second recovery receipt is still invalid, even when resealed.
    resumed = BranchRunStore.resume(store.root)
    receipt = next(row for row in resumed.read_ledger("checkpoints") if "recovery_mode" in row)
    resumed.append("checkpoints", receipt)
    resumed.finish()
    report = validate_run(store.root)
    assert report["status"] == "failed"
    assert any("recovery lacks prior" in error for error in report["errors"])
