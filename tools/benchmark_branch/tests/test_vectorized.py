from copy import deepcopy
from dataclasses import replace
from multiprocessing import get_context

import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore
from tools.benchmark_branch.examples.vectorized_reference import NumpyVectorBackend, NumpyVectorAdapter as DummyAdapter
from tools.benchmark_branch.models import canonical_digest
from tools.benchmark_branch.vectorized import IsolationError, VectorizedSession
from tools.benchmark_branch import vectorized_executor
from tools.benchmark_branch.vectorized_qualification import compare_isolation_traces, qualify_trace_files
from tools.benchmark_branch.validation import validate_run
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler


def setup(count=2, backend=None):
    plan = BranchPlan(slots=1)
    checkpoint = DummyAdapter().create_checkpoint(plan)
    backend = backend or NumpyVectorBackend()
    session = backend.open_scene("test", num_envs=count, scheduler=None)
    manager = VectorizedSession(backend, session, session_id="test", capacity=count)
    bindings = [manager.bind(job, slot, checkpoint)[0] for slot, job in enumerate(plan.jobs()[:count])]
    return manager, backend, session, bindings, checkpoint


def trace(jobs, capacity, reverse=False, backend=None):
    backend = backend or NumpyVectorBackend()
    session = backend.open_scene("test", num_envs=capacity, scheduler=None)
    manager = VectorizedSession(backend, session, session_id="test", capacity=capacity)
    checkpoint = DummyAdapter().create_checkpoint(BranchPlan(slots=1))
    output = {job.branch_id: [] for job in jobs}
    for offset in range(0, len(jobs), capacity):
        active = [manager.bind(job, slot, checkpoint)[0] for slot, job in enumerate(jobs[offset:offset + capacity])]
        wave = tuple(active)
        while active:
            rows = manager.step(tuple(reversed(active)) if reverse else tuple(active))
            for binding, row in rows.items():
                output[binding.branch_id].append({"row": row, "state": manager.snapshot(binding).digest})
                if row.get("result"):
                    active.remove(binding)
        for binding in wave:
            manager.close(binding)
    backend.close_scene(session)
    return output, backend.batch_calls


def _process_trace(job):
    return trace([job], 1)[0]


def test_vector_batches_match_fresh_process_reference_and_slot_permutation():
    jobs = BranchPlan(slots=1).jobs()
    with get_context("spawn").Pool(2, maxtasksperchild=1) as pool:
        isolated = {}
        for result in pool.map(_process_trace, jobs, chunksize=1):
            isolated.update(result)
    vector, calls = trace(jobs, 4)
    permuted, _ = trace(tuple(reversed(jobs)), 4, reverse=True)
    assert canonical_digest(isolated) == canonical_digest(vector) == canonical_digest(permuted)
    assert calls == 4  # Four vector steps instead of sixteen singleton steps.


def test_partial_restore_preserves_peer_and_rewinds_private_rng_messages():
    manager, _, session, (a, b), _ = setup()
    manager.channels[a].send(a, "robot", {"value": [1]})
    saved = manager.snapshot(a)
    first = manager.step((a, b))[a]
    peer = manager.snapshot(b).digest
    manager.channels[a].receive(a, "robot")
    manager.restore(a, saved)
    assert manager.snapshot(b).digest == peer
    assert manager.channels[a].receive(a, "robot") == [{"value": [1]}]
    assert manager.step((a,))[a] == first


def test_generation_fences_stale_actions_channels_and_snapshots():
    manager, backend, session, (a, b), checkpoint = setup()
    saved, channel = manager.snapshot(a), manager.channels[a]
    manager.close(a)
    next_job = BranchPlan(slots=1).jobs()[2]
    new, _ = manager.bind(next_job, 0, checkpoint)
    assert new.generation == a.generation + 1
    with pytest.raises(IsolationError, match="stale"):
        manager.step((a, b))
    with pytest.raises(IsolationError, match="another slot"):
        manager.restore(new, saved)
    with pytest.raises(IsolationError, match="stale"):
        channel.send(a, "robot", "late callback")
    with pytest.raises(IsolationError, match="foreign"):
        manager.channels[b].send(new, "robot", "cross branch")


@pytest.mark.parametrize("operation", ["open", "restore", "close", "inactive_step"])
def test_peer_mutation_quarantines_scene(operation):
    class Bad(NumpyVectorBackend):
        corrupt = False
        def damage(self, session):
            if self.corrupt:
                session["world"][1, 0] += 1
        def open_slot(self, session, *args, **kwargs):
            result = super().open_slot(session, *args, **kwargs)
            if operation == "open": self.damage(session)
            return result
        def restore_slot(self, session, *args):
            super().restore_slot(session, *args)
            if operation == "restore": self.damage(session)
        def close_slot(self, session, *args):
            super().close_slot(session, *args)
            if operation == "close": self.damage(session)
        def step_batch(self, session, actions):
            result = super().step_batch(session, actions)
            if operation == "inactive_step": self.damage(session)
            return result
    manager, backend, session, (a, b), cp = setup(backend=Bad())
    if operation == "open": manager.close(a)
    backend.corrupt = True
    with pytest.raises(IsolationError):
        if operation == "open": manager.bind(BranchPlan(slots=1).jobs()[2], 0, cp)
        if operation == "restore": manager.restore(a, manager.snapshot(a))
        if operation == "close": manager.close(a)
        if operation == "inactive_step": manager.step((a,))
    assert manager.quarantined
    with pytest.raises(IsolationError, match="quarantined"):
        manager.step((b,))


def test_aliasing_python_state_and_tensor_views_rejected():
    manager, backend, session, (a, b), _ = setup()
    session["slots"][1]["states"]["controller"] = session["slots"][0]["states"]["controller"]
    with pytest.raises(IsolationError, match="overlap"):
        manager.step((a, b))
    manager, backend, session, (a, b), _ = setup()
    original = backend.mutable_buffers
    def overlapping(session, binding):
        value = original(session, binding)
        value["world"] = session["world"][0]
        return value
    backend.mutable_buffers = overlapping
    with pytest.raises(IsolationError, match="overlap"):
        manager.step((a, b))


def test_actions_and_outputs_must_match_exact_live_bindings():
    manager, backend, session, (a, b), _ = setup()
    original = backend.step_batch
    def stale(session, actions):
        rows = original(session, actions)
        rows[replace(a, generation=0)] = rows.pop(a)
        return rows
    backend.step_batch = stale
    with pytest.raises(IsolationError, match="exactly"):
        manager.step((a, b))


def test_active_peer_corruption_requires_differential_qualification():
    class CrossWrite(NumpyVectorBackend):
        def step_batch(self, session, actions):
            result = super().step_batch(session, actions)
            bindings = tuple(actions)
            if len(bindings) > 1:
                other = bindings[1].environment_slot
                session["world"][other, 0] += 99
            return result

    clean, _ = trace(BranchPlan(slots=1).jobs()[:2], 2)
    observed, _ = trace(BranchPlan(slots=1).jobs()[:2], 2, backend=CrossWrite())
    report = compare_isolation_traces(clean, observed)
    assert report["passed"] is False
    assert report["differences"] == [{"branch_id": BranchPlan(slots=1).jobs()[1].branch_id,
                                       "frame": 0, "reason": "trace_mismatch"}]
    assert report["process_failure_isolation"] is False


def test_keyed_event_randomness_does_not_shift_when_an_event_is_absent():
    manager, _, _, (a, b), _ = setup()
    assert manager.channels[a].event_random(a, "weather", "event-2") == manager.channels[b].event_random(b, "weather", "event-2")
    manager.channels[a].event_random(a, "weather", "event-1")
    assert manager.channels[a].event_random(a, "weather", "event-2") == manager.channels[b].event_random(b, "weather", "event-2")


def test_differential_qualification_writes_explicit_scope(tmp_path):
    import json
    traces = {"b": [{"row": {"simulator_step": 1, "action": [0], "applied_action": [0], "observation": [0]}, "state": "s"}]}
    (tmp_path / "reference.json").write_text(json.dumps(traces))
    (tmp_path / "candidate.json").write_text(json.dumps(traces))
    report = qualify_trace_files(tmp_path / "reference.json", tmp_path / "candidate.json",
                                 tmp_path / "qualification.json")
    assert report["qualification_status"] == "trace_comparison_passed"
    assert report["provenance_verified"] is False
    assert report["process_failure_isolation"] is False


def test_nonzero_initial_tick_is_preserved():
    from tools.benchmark_branch.models import CheckpointArtifact
    backend = NumpyVectorBackend(horizon=20)
    session = backend.open_scene("test", num_envs=1, scheduler=None)
    manager = VectorizedSession(backend, session, session_id="test", capacity=1)
    cp = DummyAdapter().create_checkpoint(BranchPlan(slots=1))
    cp.state["participants"]["clock"]["state"]["next_step"] = 7
    cp.state["next_step"] = 7
    cp = CheckpointArtifact.from_state(cp.state)
    binding, _ = manager.bind(BranchPlan(slots=1).jobs()[0], 0, cp)
    assert manager.step((binding,))[binding]["simulator_step"] == 7


def execute(tmp_path, *, backend=None, scope="protocol_synthetic", max_workers=4, adapter=None):
    plan = BranchPlan(slots=2, execution_scope=scope)
    store = BranchRunStore.create(tmp_path, plan, execution_mode="shared_session", requested_max_workers=max_workers)
    backend = backend or NumpyVectorBackend(horizon=3)
    scheduler = ReplayScheduler(ReplayCapacity(1, max_workers, max_workers), device_probe=lambda session, request: session)
    result = BranchExecutor(plan, adapter or DummyAdapter(), store, max_workers=max_workers,
                            session_backend=backend, scheduler=scheduler, requested_device="cpu").run()
    return result, store, backend, scheduler


def test_collector_runs_batched_shared_session_and_offline_rechecks(tmp_path):
    result, store, backend, scheduler = execute(tmp_path)
    assert result["completed"] == 8
    assert result["validation_status"] == "passed", validate_run(tmp_path)["errors"]
    assert result["slot_isolation_validated"] is True
    assert backend.batch_calls == 6
    assert backend.scene_opens == 1
    assert scheduler.snapshot().active_branches == 0
    assert len({row["worker_session_id"] for row in store.read_ledger("episodes")}) == 8
    assert len(list((tmp_path / "branches").glob("*/ledger.jsonl"))) == 8
    assert all("action" in row and "applied_action" in row for row in store.read_ledger("steps"))


def test_vectorized_method_failure_keeps_other_slots_running(tmp_path):
    class Failure(NumpyVectorBackend):
        def step_batch(self, session, actions):
            rows = super().step_batch(session, actions)
            for binding, row in rows.items():
                if session["slots"][binding.environment_slot]["job"].branch_type == "fact":
                    row["result"] = {"status": "method_timeout", "completed": False}
            return rows
    result, store, _, _ = execute(tmp_path, backend=Failure(horizon=3))
    assert result["completed"] == 6 and result["failed"] == 2
    assert result["validation_status"] == "passed", validate_run(tmp_path)["errors"]


def test_shared_native_failure_retains_completed_methods_and_blocks_reuse(tmp_path):
    class Failure(NumpyVectorBackend):
        def step_batch(self, session, actions):
            if self.batch_calls == 1:
                raise RuntimeError("native batch failed")
            rows = super().step_batch(session, actions)
            for binding, row in rows.items():
                if binding.environment_slot == 0:
                    row["result"] = {"status": "method_timeout", "completed": False}
            return rows
    result, store, _, scheduler = execute(tmp_path, backend=Failure())
    # Only the first active wave was in flight when the shared session died;
    # later queued branches remain unrun and have no terminal receipt.
    assert result["failed"] == 4
    assert result["validation_status"] == "failed"
    assert len(store.read_ledger("episodes")) == 4
    assert store.read_ledger("episodes")[0]["status"] == "method_timeout"
    assert scheduler.snapshot().active_scenes == 0
    queued = [row for row in store.read_ledger("resources")
              if row.get("kind") == "vector_branch_queued"]
    assert len(queued) == 4
    assert all(row["execution_phase"] == "queued" and row["binding_present"] is False
               for row in queued)
    assert sum(row.get("execution_phase") == "in_flight"
               for row in store.read_ledger("episodes")) == 3
    assert sum(row.get("status") == "method_timeout"
               for row in store.read_ledger("episodes")) == 1


def test_serial_backend_cannot_silently_ignore_shared_mode(tmp_path):
    with pytest.raises(ValueError, match="serial session fallback"):
        execute(tmp_path, backend=object())


@pytest.mark.parametrize("mutation", ["missing_step", "missing_observation", "duplicate_sequence", "forged_token", "strip_sequence"])
def test_offline_validator_detects_incomplete_or_cross_slot_evidence(tmp_path, mutation):
    from tools.benchmark_branch.store import LEDGERS
    from tools.benchmark_branch.vectorized_validation import validate_vectorized
    _, store, _, _ = execute(tmp_path)
    rows = {name: [{**row, "_vector_ledger": name} for row in store.read_ledger(name)] for name in LEDGERS}
    if mutation == "missing_step": rows["steps"].pop()
    if mutation == "missing_observation": rows["observations"].pop()
    if mutation == "duplicate_sequence": rows["steps"][0]["vector_seq"] = rows["steps"][1]["vector_seq"]
    if mutation == "forged_token": rows["steps"][0]["slot_token"] = "forged"
    if mutation == "strip_sequence": rows["steps"][0].pop("vector_seq")
    # Renumber after deletion so the coverage checks, not merely sequence gaps,
    # must discover the missing native output.
    if mutation.startswith("missing"):
        ordered = sorted((row for values in rows.values() for row in values if "vector_seq" in row),
                         key=lambda row: row["vector_seq"])
        for number, row in enumerate(ordered): row["vector_seq"] = number
    assert validate_vectorized(store.manifest, rows)


@pytest.mark.parametrize("scene_close_fails", [False, True])
def test_partial_slot_open_retains_lease_until_confirmed_scene_close(tmp_path, scene_close_fails):
    class Partial(NumpyVectorBackend):
        def open_slot(self, session, *args, **kwargs):
            super().open_slot(session, *args, **kwargs)
            raise RuntimeError("partial allocation")
        def close_scene(self, session):
            if scene_close_fails:
                raise RuntimeError("native close failed")
            super().close_scene(session)
    result, store, _, scheduler = execute(tmp_path, backend=Partial())
    assert result["failed"] == 1
    assert result["validation_status"] == "failed"
    assert scheduler.snapshot().active_branches == int(scene_close_fails)
    assert scheduler.snapshot().active_scenes == int(scene_close_fails)
    shutdown_rows = [row for row in store.read_ledger("resources")
                     if row.get("kind") == "shared_session_shutdown"]
    assert len(shutdown_rows) == 1
    assert shutdown_rows[0]["shutdown_status"] == "unknown-record"


def test_second_slot_bind_failure_distinguishes_admitted_from_queued(tmp_path):
    class SecondBindFails(NumpyVectorBackend):
        def open_slot(self, session, binding, *args, **kwargs):
            value = super().open_slot(session, binding, *args, **kwargs)
            if binding.environment_slot == 1:
                raise RuntimeError("second admitted slot failed during bind")
            return value

    result, store, _, scheduler = execute(tmp_path, backend=SecondBindFails())
    episodes = store.read_ledger("episodes")
    queued = [row for row in store.read_ledger("resources")
              if row.get("kind") == "vector_branch_queued"]
    assert len(episodes) == 2
    assert len(queued) == 6
    assert {row["branch_id"] for row in episodes}.isdisjoint(
        {row["branch_id"] for row in queued}
    )
    assert all(row["execution_phase"] == "in_flight" for row in episodes)
    assert all(row["binding_present"] is True for row in episodes)
    assert all(row["execution_phase"] == "queued" for row in queued)
    assert result["queued"] == 6
    assert scheduler.snapshot().active_scenes == 0


def test_partial_scene_open_retains_scene_capacity(tmp_path):
    class Partial(NumpyVectorBackend):
        def open_scene(self, *args, **kwargs):
            raise RuntimeError("partial native allocation without handle")
    result, _, _, scheduler = execute(tmp_path, backend=Partial())
    assert result["active_scenes"] == scheduler.snapshot().active_scenes == 1
    assert result["validation_status"] == "failed"


def test_snapshot_clock_tampering_and_version_drift_are_rejected():
    manager, _, session, (a, b), _ = setup()
    saved = manager.snapshot(a)
    with pytest.raises(IsolationError, match="modified"):
        manager.restore(a, replace(saved, tick=99))
    session["slots"][a.environment_slot]["states"]["controller"]["version"] = "v2"
    with pytest.raises(IsolationError, match="version mismatch"):
        manager.restore(a, saved)
    assert manager.quarantined


def test_initial_restore_must_bind_native_readback_to_captured_state():
    class LyingRestore(NumpyVectorBackend):
        def open_slot(self, session, binding, job, checkpoint, *, channel):
            receipt = super().open_slot(session, binding, job, checkpoint, channel=channel)
            session["world"][binding.environment_slot, 0] = 12345
            receipt["state_digest"] = canonical_digest(self.capture_slot(session, binding))
            return receipt
    backend = LyingRestore()
    session = backend.open_scene("test", num_envs=1, scheduler=None)
    manager = VectorizedSession(backend, session, session_id="test", capacity=1)
    cp = DummyAdapter().create_checkpoint(BranchPlan(slots=1))
    with pytest.raises(IsolationError, match="frozen checkpoint"):
        manager.bind(BranchPlan(slots=1).jobs()[0], 0, cp)


def test_formal_vectorized_scope_requires_native_and_paper_qualification(tmp_path):
    with pytest.raises(ValueError, match="native backend differential qualification"):
        execute(tmp_path, scope="measured")


def test_measured_diagnostic_does_not_promote_missing_evaluator_evidence(tmp_path):
    class Adapter(DummyAdapter):
        supported_execution_scopes = {"measured_diagnostic"}
    result, store, _, _ = execute(tmp_path, scope="measured_diagnostic", adapter=Adapter())
    assert result["completed"] == 0
    assert all(row["status"] == "not_evaluable" and row["physical_success"] is None
               and row.get("evaluator_error") for row in store.read_ledger("episodes"))


def test_paired_native_batch_crash_is_unknown_and_excluded_without_retry(tmp_path):
    adapter = DummyAdapter()
    base_plan = BranchPlan(slots=1, run_id="run-r7-paired-crash")
    checkpoint = adapter.create_checkpoint(base_plan)
    participant_digests = {
        name: canonical_digest(value)
        for name, value in checkpoint.state["participants"].items()
    }
    base_jobs = base_plan.jobs()
    registration = {
        "block_id": "paired-block-1",
        "pair_group_id": base_jobs[0].pair_group_id,
        "branch_to_original": {
            job.logical_branch_id: f"original/{job.logical_branch_id}" for job in base_jobs
        },
        "checkpoint_digest": checkpoint.digest,
        "random_stream_digest": "random-stream-1",
        "controller_substream_digest": "controller-stream-1",
        "participant_digests": participant_digests,
    }
    jobs = tuple(replace(
        job,
        checkpoint_digest=checkpoint.digest,
        restore_checkpoint_digest=checkpoint.digest,
        metadata={"paired_block_rerun": registration},
    ) for job in base_jobs)
    plan = replace(base_plan, explicit_jobs=jobs)
    store = BranchRunStore.create(
        tmp_path, plan, execution_mode="shared_session", requested_max_workers=4,
    )

    class NativeCrash(NumpyVectorBackend):
        def step_batch(self, session, actions):
            self.batch_calls += 1
            raise RuntimeError("native shared batch crashed")

    backend = NativeCrash()
    scheduler = ReplayScheduler(
        ReplayCapacity(1, 4, 4), device_probe=lambda session, request: session,
    )
    result = BranchExecutor(
        plan, adapter, store, max_workers=4,
        session_backend=backend, scheduler=scheduler, requested_device="cpu",
    ).run()
    report = validate_run(tmp_path)
    episodes = store.read_ledger("episodes")

    assert backend.batch_calls == 1
    assert len(episodes) == 4
    assert {row["attempt"] for row in episodes} == {0}
    assert {row["status"] for row in episodes} == {"unknown-record"}
    assert {row["failure_class"] for row in episodes} == {"infrastructure"}
    assert base_jobs[0].pair_group_id not in report["metrics"]["pairing_eligible_groups"]
    assert report["metrics"]["pairing_excluded_groups"][base_jobs[0].pair_group_id] == "unknown-record"
    assert result["completed"] == 0


def test_shared_crash_does_not_terminalize_pending_paired_block(tmp_path):
    """Only the in-flight paired block is invalidated; the next block stays queued."""
    adapter = DummyAdapter()
    first_plan = BranchPlan(slots=1, run_id="run-r7-active-block")
    second_plan = BranchPlan(slots=1, run_id="run-r7-pending-block")
    checkpoint = adapter.create_checkpoint(first_plan)
    participant_digests = {
        name: canonical_digest(value)
        for name, value in checkpoint.state["participants"].items()
    }

    def register(plan, block_id):
        jobs = plan.jobs()
        registration = {
            "block_id": block_id,
            "pair_group_id": jobs[0].pair_group_id,
            "branch_to_original": {
                job.logical_branch_id: f"original/{job.logical_branch_id}" for job in jobs
            },
            "checkpoint_digest": checkpoint.digest,
            "random_stream_digest": f"random-{block_id}",
            "controller_substream_digest": f"controller-{block_id}",
            "participant_digests": participant_digests,
        }
        return tuple(replace(
            job,
            checkpoint_digest=checkpoint.digest,
            restore_checkpoint_digest=checkpoint.digest,
            metadata={"paired_block_rerun": registration},
        ) for job in jobs)

    jobs = tuple(replace(job, run_id=first_plan.run_id) for job in (
        register(first_plan, "active-block") + register(second_plan, "pending-block")
    ))
    plan = replace(first_plan, explicit_jobs=jobs)
    store = BranchRunStore.create(
        tmp_path, plan, execution_mode="shared_session", requested_max_workers=4,
    )

    class NativeCrash(NumpyVectorBackend):
        def step_batch(self, session, actions):
            self.batch_calls += 1
            raise RuntimeError("native shared batch crashed")

    scheduler = ReplayScheduler(
        ReplayCapacity(1, 4, 4), device_probe=lambda session, request: session,
    )
    result = BranchExecutor(
        plan, adapter, store, max_workers=4,
        session_backend=NativeCrash(), scheduler=scheduler, requested_device="cpu",
    ).run()
    episodes = store.read_ledger("episodes")
    assert len(episodes) == 4
    assert all(row["status"] == "unknown-record" for row in episodes)
    assert all("pending-block" not in row["branch_id"] for row in episodes)
    assert result["queued"] == 4


def test_paired_equivalence_failure_is_pair_invalid_and_excluded(tmp_path, monkeypatch):
    adapter = DummyAdapter()
    base_plan = BranchPlan(slots=1, run_id="run-r7-paired-equivalence")
    checkpoint = adapter.create_checkpoint(base_plan)
    participant_digests = {
        name: canonical_digest(value)
        for name, value in checkpoint.state["participants"].items()
    }
    base_jobs = base_plan.jobs()
    registration = {
        "block_id": "paired-block-equivalence",
        "pair_group_id": base_jobs[0].pair_group_id,
        "branch_to_original": {
            job.logical_branch_id: f"original/{job.logical_branch_id}" for job in base_jobs
        },
        "checkpoint_digest": checkpoint.digest,
        "random_stream_digest": "random-stream-1",
        "controller_substream_digest": "controller-stream-1",
        "participant_digests": participant_digests,
    }
    jobs = tuple(replace(
        job,
        checkpoint_digest=checkpoint.digest,
        restore_checkpoint_digest=checkpoint.digest,
        metadata={"paired_block_rerun": registration},
    ) for job in base_jobs)
    plan = replace(base_plan, explicit_jobs=jobs)
    store = BranchRunStore.create(
        tmp_path, plan, execution_mode="shared_session", requested_max_workers=4,
    )
    backend = NumpyVectorBackend(horizon=2)
    scheduler = ReplayScheduler(
        ReplayCapacity(1, 4, 4), device_probe=lambda session, request: session,
    )
    def reject(receipt):
        raise ValueError("equivalence mismatch")

    monkeypatch.setattr(vectorized_executor, "validate_shared_session_receipt", reject)
    result = BranchExecutor(
        plan, adapter, store, max_workers=4,
        session_backend=backend, scheduler=scheduler, requested_device="cpu",
    ).run()
    report = validate_run(tmp_path)
    episodes = store.read_ledger("episodes")

    assert backend.batch_calls == 0
    assert {row["status"] for row in episodes} == {"pair-invalid"}
    assert base_jobs[0].pair_group_id not in report["metrics"]["pairing_eligible_groups"]
    # Only the bound branch receives pair-invalid; the other roles stayed
    # queued, so the aggregate remains excluded for missing runtime receipts.
    assert report["metrics"]["pairing_excluded_groups"][base_jobs[0].pair_group_id] == "missing episode/runtime receipt"
    assert result["completed"] == 0
