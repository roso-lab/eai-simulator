import threading
import time
import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, DummyAdapter
from tools.physics_replay.execution.scheduler import (
    ReplayCapacity, ReplayScheduler, ResourceReservation,
)
from tools.benchmark_branch.validation import validate_run


class _SessionBackend:
    def __init__(self, barrier_size=None):
        self.lock = threading.Lock()
        self.open_sessions = 0
        self.peak_sessions = 0
        self.active_branches = 0
        self.peak_branches = 0
        self.branch_barrier = threading.Barrier(barrier_size) if barrier_size else None
        self.closed = []

    def open_scene(self, scene_id, *, scheduler):
        with self.lock:
            self.open_sessions += 1
            self.peak_sessions = max(self.peak_sessions, self.open_sessions)
        time.sleep(0.02)
        return {"scene_id": scene_id}

    def warmup(self, session, *, ticks):
        session["warmup_ticks"] = ticks

    def open_branch(self, session, job, *, lease):
        return {"scene_id": session["scene_id"], "branch_id": job.branch_id,
                "lease_id": lease.lease_id}

    def run_branch(self, runtime, job, *, emit):
        with self.lock:
            self.active_branches += 1
            self.peak_branches = max(self.peak_branches, self.active_branches)
        if self.branch_barrier is not None:
            self.branch_barrier.wait(timeout=5)
        emit("steps", {"simulator_step": 0, "sim_time_s": 0.0,
                        "active": False, "completed": True, "terminated": True})
        try:
            time.sleep(0.02)
            return {"status": "not_evaluable", "completed": True,
                    "physical_success": None, "evidence_refs": []}
        finally:
            with self.lock:
                self.active_branches -= 1

    def close_branch(self, runtime):
        return None

    def close_scene(self, session):
        with self.lock:
            self.open_sessions -= 1
            self.closed.append(session["scene_id"])


class _MeasuredAdapter(DummyAdapter):
    supported_execution_scopes = frozenset({"protocol_synthetic", "measured", "measured_diagnostic"})


class _MeasuredEvidenceBackend(_SessionBackend):
    def run_branch(self, runtime, job, *, emit):
        emit("metrics", {"kind": "heartbeat", "heartbeat_at_ns": time.time_ns()})
        emit("steps", {"simulator_step": 0, "sim_time_s": 0.0,
                        "active": False, "completed": True, "terminated": True})
        emit("checkpoints", {"restore_passed": True,
                              "restore_status": "passed",
                              "readback_digest": job.checkpoint_digest})
        return {"status": "completed", "completed": True,
                "physical_success": True}


def _scheduler():
    return ReplayScheduler(
        ReplayCapacity(max_parallel_scenes=2, max_parallel_branches=8,
                       max_branches_per_scene=4),
        device_probe=lambda session, request: {
            "actual_physics_device": "cuda:0",
            "controller_device": "cuda:0",
            "gpu_uuid": "fake-gpu-0",
        },
    )


def test_session_backend_connects_scene_and_branch_leases(tmp_path):
    plan = BranchPlan(scenes=("scene-a", "scene-b"), slots=1)
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=8,
                                  execution_scope="protocol_synthetic")
    backend = _SessionBackend()
    result = BranchExecutor(
        plan, DummyAdapter(steps=1), store, max_workers=8,
        session_backend=backend, scheduler=_scheduler(), requested_device="cuda:0",
        scene_reservation=ResourceReservation(0, 0),
        branch_reservation=ResourceReservation(0, 0), warmup_ticks=2,
    ).run()
    assert result["scheduler_integrated"] is True
    assert result["completed"] == 8
    assert result["failed"] == 0
    assert result["observed_peak_branch_workers"] == 8
    assert backend.peak_branches == 8
    assert backend.peak_sessions == 2
    assert sorted(backend.closed) == ["scene-a", "scene-b"]
    assert validate_run(tmp_path / "run")["status"] == "passed"


def test_session_backend_cpu_preflight_is_fail_closed(tmp_path):
    scheduler = ReplayScheduler(
        ReplayCapacity(max_parallel_scenes=1, max_parallel_branches=4),
        device_probe=lambda session, request: {
            "actual_physics_device": "cpu",
            "controller_device": "cpu",
            "gpu_uuid": "",
        },
    )
    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=4,
                                  execution_scope="protocol_synthetic")
    result = BranchExecutor(
        plan, DummyAdapter(steps=1), store, max_workers=4,
        session_backend=_SessionBackend(), scheduler=scheduler,
        requested_device="cuda:0",
    ).run()
    assert result["completed"] == 0
    assert result["failed"] == 4
    episodes = store.read_ledger("episodes")
    assert {row["status"] for row in episodes} == {"infrastructure_invalid"}
    assert all(row["physical_success"] is None for row in episodes)


def test_measured_backend_cannot_promote_completion_to_physical_success(tmp_path):
    plan = BranchPlan(slots=1, execution_scope="measured_diagnostic")
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=4,
                                  execution_scope="measured_diagnostic")
    result = BranchExecutor(
        plan, _MeasuredAdapter(steps=1), store, max_workers=4,
        session_backend=_MeasuredEvidenceBackend(), scheduler=_scheduler(),
        requested_device="cuda:0",
    ).run()
    assert result["completed"] == 0
    assert result["failed"] == 4
    episodes = store.read_ledger("episodes")
    assert {row["status"] for row in episodes} == {"not_evaluable"}
    assert all(row["physical_success"] is None for row in episodes)


def test_missing_receipt_reason_is_sealed_even_with_nested_outcome(tmp_path):
    class NestedBackend(_SessionBackend):
        def run_branch(self, runtime, job, *, emit):
            return {"status": "completed", "completed": True, "outcome": {"value": 1}}

    plan = BranchPlan(slots=1, execution_scope="measured_diagnostic")
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=4,
                                  execution_scope="measured_diagnostic")
    BranchExecutor(plan, _MeasuredAdapter(steps=1), store, max_workers=4,
                   session_backend=NestedBackend(), scheduler=_scheduler(), requested_device="cuda:0").run()
    episodes = store.read_ledger("episodes")
    assert len(episodes) == 4
    for row in episodes:
        assert row["status"] == "infrastructure_invalid"
        assert row["infrastructure_reason"] == "missing_runtime_receipt"
        assert row["infrastructure_error"]["heartbeat"] is False
        assert row["outcome"] == {"value": 1}


def test_session_backend_can_overlap_32_branch_slots_across_two_scenes(tmp_path):
    plan = BranchPlan(
        scenes=("scene-a", "scene-b"), slots=4,
    )
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=32,
                                  execution_scope="protocol_synthetic")
    backend = _SessionBackend(barrier_size=32)
    scheduler = ReplayScheduler(
        ReplayCapacity(max_parallel_scenes=2, max_parallel_branches=32,
                       max_branches_per_scene=16),
        device_probe=lambda session, request: {
            "actual_physics_device": "cuda:0",
            "controller_device": "cuda:0",
            "gpu_uuid": "fake-gpu-0",
        },
    )
    result = BranchExecutor(
        plan, DummyAdapter(steps=1), store, max_workers=32,
        session_backend=backend, scheduler=scheduler, requested_device="cuda:0",
    ).run()

    assert result["completed"] == 32
    assert result["failed"] == 0
    assert result["observed_peak_branch_workers"] == 32
    assert backend.peak_branches == 32
    assert backend.peak_sessions == 2
    assert scheduler.snapshot().active_scenes == 0
    assert scheduler.snapshot().active_branches == 0
    episodes = store.read_ledger("episodes")
    assert len({row["worker_instance_id"] for row in episodes}) == 32
    assert validate_run(tmp_path / "run")["status"] == "passed"


def test_exception_cleanup_keeps_lease_until_runtime_closed(tmp_path):
    scheduler = _scheduler()

    class Backend(_SessionBackend):
        execution_affinity = "caller_thread"

        def run_branch(self, runtime, job, *, emit):
            raise RuntimeError("execution failed")

        def close_branch(self, runtime):
            assert scheduler.snapshot().active_branches == 1
            self.closed.append(runtime["branch_id"])

    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    backend = Backend()
    result = BranchExecutor(plan, DummyAdapter(), store, session_backend=backend,
                            scheduler=scheduler).run()
    assert result["failed"] == 4
    assert scheduler.snapshot().active_branches == 0
    assert len(backend.closed) == 5


def test_cleanup_failure_stops_queued_work_and_only_scene_shutdown_releases(tmp_path):
    scheduler = ReplayScheduler(ReplayCapacity(1, 1, 1), device_probe=lambda session, request: {
        "actual_physics_device": "cuda:0", "controller_device": "cuda:0", "gpu_uuid": "test"})

    class Backend(_SessionBackend):
        def __init__(self):
            super().__init__()
            self.opens = self.closes = 0

        def open_branch(self, session, job, *, lease):
            self.opens += 1
            return super().open_branch(session, job, lease=lease)

        def close_branch(self, runtime):
            self.closes += 1
            raise RuntimeError("native cleanup failed")

        def close_scene(self, session):
            assert scheduler.snapshot().active_branches == 1
            super().close_scene(session)

    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    backend = Backend()
    result = BranchExecutor(plan, DummyAdapter(), store, max_workers=4,
                            session_backend=backend, scheduler=scheduler).run()
    assert result["failed"] == 4
    assert backend.opens == backend.closes == 1
    assert scheduler.snapshot().active_branches == 0
    assert scheduler.snapshot().active_scenes == 0
    assert validate_run(tmp_path)["status"] == "failed"


def test_failed_scene_shutdown_retains_capacity_in_manifest(tmp_path):
    class Backend(_SessionBackend):
        execution_affinity = "caller_thread"

        def close_branch(self, runtime):
            raise RuntimeError("branch close failed")

        def close_scene(self, session):
            raise RuntimeError("scene close failed")

    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    scheduler = _scheduler()
    result = BranchExecutor(plan, DummyAdapter(), store, session_backend=Backend(), scheduler=scheduler).run()
    assert result["failed"] == 4
    assert result["active"] == store.manifest["active"] == 1
    assert scheduler.snapshot().active_scenes == 1


@pytest.mark.parametrize("missing_handle", [False, True])
@pytest.mark.parametrize("scene_close_fails", [False, True])
def test_partial_branch_open_quarantines_until_scene_shutdown(tmp_path, missing_handle, scene_close_fails):
    scheduler = _scheduler()

    class Backend(_SessionBackend):
        def __init__(self):
            super().__init__()
            self.opens = 0
            self.native_active = False

        def open_branch(self, session, job, *, lease):
            assert not self.native_active
            self.opens += 1
            self.native_active = True
            if missing_handle:
                return None
            raise RuntimeError("partial branch allocation")

        def close_branch(self, runtime):
            pytest.fail("no handle returned; individual close is impossible")

        def close_scene(self, session):
            assert scheduler.snapshot().active_branches == 1
            if scene_close_fails:
                raise RuntimeError("scene shutdown failed")
            self.native_active = False
            super().close_scene(session)

    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    backend = Backend()
    result = BranchExecutor(plan, DummyAdapter(), store, max_workers=1,
                            session_backend=backend, scheduler=scheduler).run()
    assert result["failed"] == 4
    assert backend.opens == 1
    assert backend.native_active == scene_close_fails
    assert scheduler.snapshot().active_branches == int(scene_close_fails)
    assert scheduler.snapshot().active_scenes == int(scene_close_fails)
    assert store.manifest["active"] == int(scene_close_fails)
    assert any(row.get("kind") == "branch_open_error" for row in store.read_ledger("resources"))


@pytest.mark.parametrize("missing_handle", [False, True])
def test_partial_scene_open_retains_scene_capacity_and_stops_admission(tmp_path, missing_handle):
    class Backend(_SessionBackend):
        def open_scene(self, scene_id, *, scheduler):
            self.open_sessions += 1
            if missing_handle:
                return None
            raise RuntimeError("partial scene allocation")

        def close_scene(self, session):
            pytest.fail("no session handle returned")

    plan = BranchPlan(scenes=("a", "b"), slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    scheduler = _scheduler()
    backend = Backend()
    result = BranchExecutor(plan, DummyAdapter(), store, max_workers=1,
                            session_backend=backend, scheduler=scheduler).run()
    assert result["failed"] == 8
    assert backend.open_sessions == 1
    assert scheduler.snapshot().active_scenes == store.manifest["active_scenes"] == 1
    assert scheduler.snapshot().active_branches == 0
    assert any(row.get("kind") == "session_open_error" for row in store.read_ledger("resources"))


def test_max_workers_is_global_across_resident_scenes(tmp_path):
    plan = BranchPlan(scenes=("a", "b"), slots=2)
    store = BranchRunStore.create(tmp_path, plan)
    backend = _SessionBackend()
    result = BranchExecutor(plan, DummyAdapter(), store, max_workers=3,
                            session_backend=backend, scheduler=_scheduler()).run()
    assert result["completed"] == 16
    assert backend.peak_sessions == 2
    assert 1 < backend.peak_branches <= 3
    assert result["observed_peak_branch_workers"] <= 3


def test_method_failure_fields_survive_cleanup_failure(tmp_path):
    class Backend(_SessionBackend):
        execution_affinity = "caller_thread"

        def run_branch(self, runtime, job, *, emit):
            return {"status": "method_timeout", "completed": False,
                    "method_status": "method_timeout", "method_completed": False,
                    "method_outcome": "timeout"}

        def close_branch(self, runtime):
            raise RuntimeError("cleanup error after method timeout")

    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path, plan)
    BranchExecutor(plan, DummyAdapter(), store, session_backend=Backend(), scheduler=_scheduler()).run()
    first = store.read_ledger("episodes")[0]
    assert first["method_status"] == "method_timeout"
    assert first["method_completed"] is False
    assert first["method_outcome"] == "timeout"
    assert "cleanup error" in first["error"]


def test_duplicate_runtime_across_scenes_is_not_closed_by_rejected_branch(tmp_path):
    from dataclasses import replace

    class Backend(_SessionBackend):
        def __init__(self):
            super().__init__()
            self.shared = {}
            self.open_barrier = threading.Barrier(2)
            self.runtime_closes = 0

        def open_branch(self, session, job, *, lease):
            self.open_barrier.wait(timeout=2)
            return self.shared

        def run_branch(self, runtime, job, *, emit):
            time.sleep(0.1)
            return {"status": "completed", "completed": True}

        def close_branch(self, runtime):
            self.runtime_closes += 1

    plan = BranchPlan(run_id="shared-object", scenes=("a", "b"), slots=1)
    jobs = plan.jobs()
    plan = replace(plan, explicit_jobs=(jobs[0], jobs[4]))
    store = BranchRunStore.create(tmp_path, plan)
    backend = Backend()
    result = BranchExecutor(plan, DummyAdapter(), store, max_workers=2,
                            session_backend=backend, scheduler=_scheduler()).run()
    assert result["completed"] == result["failed"] == 1
    assert backend.runtime_closes == 1
    assert any("multiple active branches" in row.get("error", "") for row in store.read_ledger("episodes"))
