import threading
import time

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
