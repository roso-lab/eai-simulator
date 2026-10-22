"""主工程状态注册表与工具恢复登记之间的边界测试。"""
from __future__ import annotations

import pytest
from copy import deepcopy
from dataclasses import replace

from benchmark.recovery_bridge_v3 import RegisteredRecoveryV3
from benchmark.replay_state_v3 import ReplayStateRegistryV3, SnapshotParticipantV3
from tools.benchmark_branch import BranchExecutor, BranchRunStore
from tools.benchmark_branch.models import BranchPlan, canonical_digest
from tools.benchmark_branch.tests.test_runtime import ContractAdapter, ContractBackend
from tools.benchmark_branch.validation import validate_run
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler


def test_durable_reload_restores_frozen_state_after_original_object_is_discarded(tmp_path):
    frozen = RegisteredRecoveryV3(registry(), registration_id="durable-rule",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    archive = tmp_path / "snapshot"
    digest = frozen.save(archive, source_digest="a"*64)
    del frozen
    loaded = RegisteredRecoveryV3.load(archive, expected_digest=digest, source_digest="a"*64, job=job)
    receipt = loaded.restore_receipt(registry(), job.with_attempt(1))
    assert receipt["restore_passed"] is True
    assert receipt["readback_digest"] == job.checkpoint_digest


def test_durable_reload_rejects_foreign_source_rule_scope_and_process_before_mutation(tmp_path, monkeypatch):
    import benchmark.recovery_bridge_v3 as bridge
    frozen = RegisteredRecoveryV3(registry(), registration_id="durable-rule",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    archive = tmp_path / "snapshot"
    digest = frozen.save(archive, source_digest="a"*64)
    with pytest.raises(ValueError):
        RegisteredRecoveryV3.load(archive, expected_digest=digest, source_digest="b"*64, job=job)
    changed_rule = deepcopy(job.metadata)
    changed_rule["registered_recovery"]["infrastructure_reasons"] = ["new-unregistered-fault"]
    with pytest.raises(ValueError):
        RegisteredRecoveryV3.load(archive, expected_digest=digest, source_digest="a"*64,
            job=replace(job, metadata=changed_rule))
    with pytest.raises(ValueError, match="diagnostic"):
        RegisteredRecoveryV3.load(archive, expected_digest=digest, source_digest="a"*64,
            job=replace(job, execution_scope="measured"))
    monkeypatch.setattr(bridge, "_PROCESS_NONCE", "different-process")
    with pytest.raises(ValueError):
        RegisteredRecoveryV3.load(archive, expected_digest=digest, source_digest="a"*64, job=job)


@pytest.mark.parametrize("operation", ["load", "save", "restore", "bind", "checkpoint"])
def test_fork_cannot_inherit_native_authority(tmp_path, operation):
    import multiprocessing
    frozen = RegisteredRecoveryV3(registry(), registration_id="fork-rule",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    archive = tmp_path / "snapshot"
    digest = frozen.save(archive, source_digest="a"*64)
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    def child():
        mutations = []
        runtime = ReplayStateRegistryV3(tuple(
            SnapshotParticipantV3(name, "test.v1", lambda: {},
                lambda state: mutations.append(state), lambda: {})
            for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
        try:
            if operation == "load":
                RegisteredRecoveryV3.load(archive, expected_digest=digest, source_digest="a"*64, job=job)
            elif operation == "save":
                frozen.save(tmp_path / "child-snapshot", source_digest="a"*64)
            elif operation == "restore":
                frozen.restore_receipt(runtime, job.with_attempt(1))
            elif operation == "bind":
                frozen.bind(job)
            else:
                frozen.checkpoint
        except ValueError as exc:
            sender.send(("rejected", not mutations, str(exc)))
        else:
            sender.send("unsafe-acceptance")
        finally:
            sender.close()
    process = context.Process(target=child)
    process.start()
    sender.close()
    try:
        assert receiver.poll(10), "child did not return a storage decision"
        result = receiver.recv()
        assert result[0:2] == ("rejected", True)
        assert "process" in result[2] or "metadata" in result[2]
        assert not (tmp_path / "child-snapshot").exists()
        process.join(10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        receiver.close()


def registry(*, bad_readback: str | None = None) -> ReplayStateRegistryV3:
    names = ReplayStateRegistryV3.REQUIRED_PARTICIPANTS
    live = {name: {"value": name, "counter": 0} for name in names}

    def make(name):
        def capture():
            return dict(live[name])

        def restore(state):
            live[name] = dict(state)

        def readback():
            value = dict(live[name])
            if name == bad_readback:
                value["counter"] += 1
            return value

        return SnapshotParticipantV3(name, "test.v1", capture, restore, readback)

    return ReplayStateRegistryV3(tuple(make(name) for name in names))


def test_bind_freezes_all_participants_and_retry_receipt():
    frozen = RegisteredRecoveryV3(
        registry(), registration_id="rule-1",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    assert len(job.metadata["registered_recovery"]["participant_state_digests"]) == 10
    retry = job.with_attempt(1)
    receipt = frozen.restore_receipt(registry(), retry)
    assert receipt["restore_passed"] is True
    assert receipt["random_stream_digest"] == job.metadata["registered_recovery"]["random_stream_digest"]


def test_failed_restore_readback_is_quarantined_and_never_receipted():
    frozen = RegisteredRecoveryV3(
        registry(), registration_id="rule-2",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    runtime = registry(bad_readback="world")
    with pytest.raises(RuntimeError, match="readback"):
        frozen.restore_receipt(runtime, job.with_attempt(1))
    with pytest.raises(RuntimeError, match="quarantined"):
        runtime.capture()


def test_fresh_process_and_nonzero_initial_attempt_are_rejected():
    frozen = RegisteredRecoveryV3(
        registry(), registration_id="rule-3",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    plan = BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic")
    with pytest.raises(ValueError, match="before the initial"):
        frozen.bind(plan.jobs()[0].with_attempt(1))
    fresh = replace(plan.jobs()[0], metadata={"checkpoint_receipt_mode": "fresh_initialization_v1"})
    with pytest.raises(ValueError, match="fresh initialization"):
        frozen.bind(fresh)


def test_actual_registry_receipt_through_collector_and_offline_validation(tmp_path):
    frozen = RegisteredRecoveryV3(registry(), registration_id="collector-rule",
        infrastructure_reasons=("preflight_rejected",), max_attempts=2)
    plan = BranchPlan(run_id="registry-integration", slots=1, arms=("fact",),
                      execution_scope="measured_diagnostic")
    job = frozen.bind(plan.jobs()[0])
    archive = tmp_path / "collector-checkpoint"
    archive_digest = frozen.save(archive, source_digest="a" * 64)
    del frozen
    frozen = RegisteredRecoveryV3.load(archive, expected_digest=archive_digest,
        source_digest="a" * 64, job=job)
    plan = replace(plan, explicit_jobs=(job,))
    store = BranchRunStore.create(tmp_path / "run", plan, execution_scope="measured_diagnostic")
    store.update_manifest(execution_backend="session_backend")
    store.append("branch_manifest", job.identity(), job=job)
    prior = {"status": "infrastructure_invalid", "completed": False,
             "physical_success": None, "infrastructure_preflight": True,
             "infrastructure_reason": "preflight_rejected"}
    store.append("episodes", prior, job=job)
    original = deepcopy(store.read_ledger("episodes"))
    store.finish()

    class Adapter(ContractAdapter):
        def create_checkpoint(self, plan):
            return frozen.checkpoint

    class Backend(ContractBackend):
        def run_branch(self, runtime, job, *, emit):
            emit("checkpoints", frozen.restore_receipt(registry(), job))
            emit("metrics", {"kind": "heartbeat"})
            emit("steps", {"simulator_step": 1})
            return {"status": "method_timeout", "completed": False, "physical_success": None}

    resumed = BranchRunStore.resume(store.root)
    def executor(current):
        return BranchExecutor(plan, Adapter(), current, session_backend=Backend(current.root),
            scheduler=ReplayScheduler(ReplayCapacity(1, 1, 1)), requested_device="cpu")
    assert executor(resumed).run()["failed"] == 1
    outcomes = resumed.read_ledger("episodes")
    assert outcomes[:1] == original  # no replacement of the failed original
    assert len(outcomes) == 2 and outcomes[-1]["status"] == "method_timeout"
    report = validate_run(store.root)
    assert report["status"] == "passed", report["errors"]
    assert report["metrics"]["formal_pairing_eligible"] is False
    assert executor(BranchRunStore.resume(store.root)).run()["results"] == []
    assert len(resumed.read_ledger("episodes")) == 2  # method failure retained


def test_detached_snapshots_and_metadata_cannot_change_frozen_registration():
    frozen = RegisteredRecoveryV3(registry(), registration_id="immutable",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    original = BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0]
    checkpoint = frozen.checkpoint
    checkpoint.state["world"]["state"]["counter"] = 99
    first = frozen.bind(original)
    first.metadata["registered_recovery"]["participant_state_digests"]["rng"] = "tampered"
    second = frozen.bind(original)
    assert second.checkpoint_digest == canonical_digest(frozen.checkpoint.state)
    assert second.metadata["registered_recovery"]["participant_state_digests"]["rng"] != "tampered"
    assert frozen.restore_receipt(registry(), second.with_attempt(1))["restore_passed"]


@pytest.mark.parametrize("change", ["checkpoint", "registration", "version", "extra_participant"])
def test_incompatible_job_or_registry_is_rejected(change):
    runtime = registry()
    frozen = RegisteredRecoveryV3(runtime, registration_id="binding",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    original = BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0]
    if change == "checkpoint":
        with pytest.raises(ValueError, match="differs"):
            frozen.bind(replace(original, checkpoint_digest="different"))
        return
    job = frozen.bind(original).with_attempt(1)
    if change == "registration":
        job.metadata["registered_recovery"]["random_stream_digest"] = "changed"
    elif change == "extra_participant":
        runtime.register(SnapshotParticipantV3("extra", "v1", lambda: {}, lambda s: None, lambda: {}))
    else:
        runtime = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(name, "v2", lambda: {},
            lambda s: None, lambda: {}) for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    with pytest.raises((ValueError, RuntimeError)):
        frozen.restore_receipt(runtime, job)


def test_readback_drifting_after_restore_is_rejected_and_quarantined():
    calls = 0
    def readback():
        nonlocal calls
        calls += 1
        return {"value": 0 if calls == 1 else 1}
    runtime = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(name, "v1", lambda: {"value": 0},
        lambda s: None, readback if name == "rng" else lambda: {"value": 0})
        for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    frozen = RegisteredRecoveryV3(runtime, registration_id="late-drift",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    with pytest.raises(RuntimeError, match="exact participant"):
        frozen.restore_receipt(runtime, job.with_attempt(1))
    with pytest.raises(RuntimeError, match="quarantined"):
        runtime.readback_snapshot()


def test_tolerance_comparator_cannot_turn_inexact_restore_into_exact_receipt():
    runtime = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(name, "v1", lambda: {"x": 0.0},
        lambda s: None, lambda: {"x": 0.001}, lambda a, b: True)
        for name in ReplayStateRegistryV3.REQUIRED_PARTICIPANTS))
    frozen = RegisteredRecoveryV3(runtime, registration_id="exact-only",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    with pytest.raises(RuntimeError, match="exact participant"):
        frozen.restore_receipt(runtime, job.with_attempt(1))


def test_unqualified_bridge_cannot_bind_or_receipt_formal_jobs():
    frozen = RegisteredRecoveryV3(registry(), registration_id="diagnostic-only",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = BranchPlan(slots=1, execution_scope="measured").jobs()[0]
    with pytest.raises(ValueError, match="diagnostic-only"):
        frozen.bind(job)
    diagnostic = frozen.bind(replace(job, execution_scope="measured_diagnostic"))
    with pytest.raises(ValueError, match="does not match"):
        frozen.restore_receipt(registry(), replace(diagnostic.with_attempt(1), execution_scope="measured"))


def test_restore_order_is_canonical_even_with_reversed_plugin_registration():
    writes = []
    names = (*ReplayStateRegistryV3.REQUIRED_PARTICIPANTS, "extra_z", "extra_a")
    runtime = ReplayStateRegistryV3(tuple(SnapshotParticipantV3(name, "v1", lambda: {},
        lambda s, key=name: writes.append(key), lambda: {}) for name in reversed(names)))
    frozen = RegisteredRecoveryV3(runtime, registration_id="ordered",
        infrastructure_reasons=("simulator_error",), max_attempts=2)
    job = frozen.bind(BranchPlan(slots=1, arms=("fact",), execution_scope="measured_diagnostic").jobs()[0])
    frozen.restore_receipt(runtime, job.with_attempt(1))
    assert writes == [*ReplayStateRegistryV3.REQUIRED_PARTICIPANTS, "extra_a", "extra_z"]
    assert len(job.metadata["registered_recovery"]["participant_state_digests"]) == 12
