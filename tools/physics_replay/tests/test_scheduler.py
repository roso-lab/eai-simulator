import threading
import time

import pytest

from tools.physics_replay.execution import (
    CapacityDenied,
    DevicePreflightError,
    ReplayCapacity,
    ReplayRequest,
    ReplayScheduler,
    ResourceReservation,
    run_fake_parallel,
)


class DeviceProbe:
    def __init__(self, physics="cuda:0", controller="cuda:0"):
        self.physics = physics
        self.controller = controller

    def probe(self, session, request):
        return {
            "actual_physics_device": self.physics,
            "controller_device": self.controller,
            "gpu_uuid": "GPU-test",
        }


class ResourceProbe:
    def __init__(self):
        self.sample_count = 0

    def sample(self):
        self.sample_count += 1
        return {"gpu_free_memory_mb": 1000, "host_available_memory_mb": 1000,
                "gpu_uuid": "GPU-test"}


def scheduler(capacity=None, probe=None):
    return ReplayScheduler(
        capacity or ReplayCapacity(2, 4, 2, gpu_memory_budget_mb=1000, host_memory_budget_mb=1000),
        resource_probe=probe or ResourceProbe(), device_probe=DeviceProbe(),
    )


def admit_scene(s, scene="scene-a"):
    lease = s.acquire_scene(scene, reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    evidence = s.preflight(
        ReplayRequest(scene, "probe", ResourceReservation(100, 100), "cuda:0", warmup_ticks=3),
        session=object(),
    )
    assert evidence.valid
    return lease


def test_scene_and_global_branch_capacity_are_enforced():
    s = scheduler(ReplayCapacity(1, 2, 1, 1000, 1000))
    scene = admit_scene(s)
    first = s.acquire_branch(scene, branch_key="one", reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    with pytest.raises(CapacityDenied):
        s.acquire_branch(scene, branch_key="two", reservation=ResourceReservation(100, 100), requested_device="cuda:0", wait=False)
    s.release_branch(first)
    second = s.acquire_branch(scene, branch_key="two", reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    s.release_branch(second)
    s.release_scene(scene)


def test_different_scenes_are_limited_and_queue_then_admit():
    s = scheduler(ReplayCapacity(1, 4, 2, 1000, 1000))
    first = admit_scene(s, "first")
    result = []

    def acquire():
        lease = s.acquire_scene("second", reservation=ResourceReservation(100, 100), requested_device="cuda:0", timeout_s=1)
        result.append(lease)

    thread = threading.Thread(target=acquire)
    thread.start()
    time.sleep(0.03)
    assert s.snapshot().queued_requests == 1
    s.release_scene(first)
    thread.join(timeout=1)
    assert len(result) == 1
    s.release_scene(result[0])


def test_global_branch_limit_counts_branches_across_scenes():
    s = scheduler(ReplayCapacity(2, 1, 1, 1000, 1000))
    first_scene = admit_scene(s, "first")
    second_scene = admit_scene(s, "second")
    first = s.acquire_branch(first_scene, branch_key="first-branch", reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    with pytest.raises(CapacityDenied):
        s.acquire_branch(second_scene, branch_key="second-branch", reservation=ResourceReservation(100, 100), requested_device="cuda:0", wait=False)
    s.release_branch(first)
    s.release_scene(first_scene)
    s.release_scene(second_scene)


def test_preflight_fails_for_cpu_physics_even_when_cuda_requested():
    s = ReplayScheduler(ReplayCapacity(1, 2, 2), device_probe=DeviceProbe(physics="cpu", controller="cuda:0"))
    scene = s.acquire_scene("scene", reservation=ResourceReservation(0, 0), requested_device="cuda:0")
    evidence = s.preflight(ReplayRequest("scene", "branch", ResourceReservation(0, 0), "cuda:0"), object())
    assert evidence.device_validation == "failed"
    with pytest.raises(DevicePreflightError):
        s.acquire_branch(scene, branch_key="branch", reservation=ResourceReservation(0, 0), requested_device="cuda:0")
    s.release_scene(scene)


def test_warmup_is_recorded_and_not_episode_time():
    s = scheduler()
    scene = admit_scene(s)
    branch = s.acquire_branch(scene, branch_key="branch", reservation=ResourceReservation(100, 100), requested_device="cuda:0", warmup_ticks=7)
    s.release_branch(branch)
    events = s.audits
    acquired = next(row for row in events if row["event"] == "branch_acquired")
    released = next(row for row in events if row["event"] == "branch_released")
    assert acquired["warmup_ticks"] == 7
    assert released["episode_time_s"] >= 0
    assert "physical_success" not in released
    s.release_scene(scene)


def test_context_manager_releases_after_worker_exception():
    s = scheduler()
    with pytest.raises(RuntimeError):
        with s.scene_slot("scene", reservation=ResourceReservation(100, 100), requested_device="cuda:0") as scene:
            s.preflight(ReplayRequest("scene", "branch", ResourceReservation(100, 100), "cuda:0"), object())
            with s.branch_slot(scene, branch_key="branch", reservation=ResourceReservation(100, 100), requested_device="cuda:0"):
                raise RuntimeError("worker crash")
    assert s.snapshot().active_scenes == 0
    assert s.snapshot().active_branches == 0
    assert any(row["event"] == "branch_abort" for row in s.audits)
    assert any(row["event"] == "scene_released" for row in s.audits)


def test_branch_leases_have_independent_session_owners():
    s = scheduler()
    scene = admit_scene(s)
    first = s.acquire_branch(scene, branch_key="a", reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    second = s.acquire_branch(scene, branch_key="b", reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    assert first.lease_id != second.lease_id
    assert first.branch_key != second.branch_key
    assert first.owner_pid == second.owner_pid
    s.release_branch(first)
    s.release_branch(first)  # idempotent
    s.release_branch(second)
    s.release_scene(scene)


def test_stale_recovery_only_reclaims_dead_owner():
    s = scheduler()
    scene = admit_scene(s)
    branch = s.acquire_branch(scene, branch_key="alive", reservation=ResourceReservation(100, 100), requested_device="cuda:0", owner_pid=123)
    assert s.recover_stale(live_pid_check=lambda pid: pid == 123) == ()
    assert s.snapshot().active_branches == 1
    recovered = s.recover_stale(live_pid_check=lambda pid: False)
    assert recovered[0] == branch.lease_id
    assert len(recovered) == 2  # branch and then its now-unused scene lease
    assert s.snapshot().active_branches == 0
    assert s.snapshot().active_scenes == 0


def test_gpu_budget_rejects_without_implicit_oversubscription():
    s = scheduler(ReplayCapacity(2, 4, 2, gpu_memory_budget_mb=100, host_memory_budget_mb=1000))
    scene = s.acquire_scene("scene", reservation=ResourceReservation(100, 0), requested_device="cuda:0")
    with pytest.raises(CapacityDenied):
        s.acquire_scene("second", reservation=ResourceReservation(1, 0), requested_device="cuda:0", wait=False)
    s.release_scene(scene)


def test_scene_and_branch_reservations_share_one_memory_budget():
    s = scheduler(ReplayCapacity(
        1, 2, 2, gpu_memory_budget_mb=100, host_memory_budget_mb=100,
    ))
    scene = s.acquire_scene(
        "scene", reservation=ResourceReservation(70, 60),
        requested_device="cuda:0",
    )
    s.preflight(
        ReplayRequest(
            "scene", "branch", ResourceReservation(20, 20), "cuda:0",
        ),
        object(),
    )
    with pytest.raises(CapacityDenied):
        s.acquire_branch(
            scene, branch_key="branch",
            reservation=ResourceReservation(31, 20),
            requested_device="cuda:0", wait=False,
        )
    s.release_scene(scene)


def test_active_slots_remain_unique_after_out_of_order_release():
    s = scheduler(ReplayCapacity(3, 4, 3, 1000, 1000))
    scene_a = admit_scene(s, "a")
    scene_b = admit_scene(s, "b")
    s.release_scene(scene_a)
    scene_c = admit_scene(s, "c")
    assert scene_b.slot != scene_c.slot

    first = s.acquire_branch(
        scene_b, branch_key="first", reservation=ResourceReservation(100, 100),
        requested_device="cuda:0",
    )
    second = s.acquire_branch(
        scene_b, branch_key="second", reservation=ResourceReservation(100, 100),
        requested_device="cuda:0",
    )
    s.release_branch(first)
    third = s.acquire_branch(
        scene_b, branch_key="third", reservation=ResourceReservation(100, 100),
        requested_device="cuda:0",
    )
    assert second.slot != third.slot
    s.release_branch(second)
    s.release_branch(third)
    s.release_scene(scene_b)
    s.release_scene(scene_c)


def test_branch_device_request_must_match_preflight_request():
    s = scheduler()
    scene = admit_scene(s)
    with pytest.raises(DevicePreflightError, match="preflight was for"):
        s.acquire_branch(
            scene, branch_key="branch",
            reservation=ResourceReservation(100, 100),
            requested_device="cuda:1",
        )
    s.release_scene(scene)


def test_cuda_preflight_requires_gpu_uuid():
    class MissingUuidProbe(DeviceProbe):
        def probe(self, session, request):
            return {
                "actual_physics_device": self.physics,
                "controller_device": self.controller,
            }

    s = ReplayScheduler(
        ReplayCapacity(1, 1, 1), device_probe=MissingUuidProbe(),
    )
    scene = s.acquire_scene(
        "scene", reservation=ResourceReservation(0, 0),
        requested_device="cuda:0",
    )
    evidence = s.preflight(
        ReplayRequest("scene", "branch", ResourceReservation(0, 0), "cuda:0"),
        object(),
    )
    assert evidence.device_validation == "unavailable"
    assert "GPU UUID" in evidence.diagnostics[-1]
    s.release_scene(scene)


def test_audit_contains_gpu_uuid_capacity_and_abort_reason():
    s = scheduler()
    scene = admit_scene(s)
    branch = s.acquire_branch(scene, branch_key="branch", reservation=ResourceReservation(100, 100), requested_device="cuda:0")
    s.release_branch(branch, status="crashed", reason="SIGABRT")
    row = next(row for row in s.audits if row["event"] == "branch_abort")
    assert row["gpu_uuid"] == "GPU-test"
    assert row["max_parallel_scenes"] == 2
    assert row["crash_or_abort_reason"] == "SIGABRT"
    s.release_scene(scene, status="aborted", reason="child crashed")


def test_fake_worker_smoke_has_overlapping_independent_sessions():
    s = scheduler(ReplayCapacity(1, 4, 4, 1000, 1000))
    results = run_fake_parallel(s, branch_count=4, warmup_ticks=2, episode_ticks=3)
    assert len(results) == 4
    assert all(item.completed and item.episode_ticks == 3 for item in results)
    assert len({item.session_identity for item in results}) == 4
    assert s.snapshot().active_scenes == 0
    assert s.snapshot().active_branches == 0
    assert sum(row["event"] == "branch_started" for row in s.audits) == 4


def test_halt_admission_wakes_waiter_and_keeps_existing_lease():
    s = scheduler(ReplayCapacity(1, 1, 1, 1000, 1000))
    scene = admit_scene(s)
    lease = s.acquire_branch(scene, branch_key="first", reservation=ResourceReservation(10), requested_device="cuda:0")
    failures = []
    started = threading.Event()

    def waiting():
        started.set()
        try:
            s.acquire_branch(scene, branch_key="next", reservation=ResourceReservation(10), requested_device="cuda:0")
        except CapacityDenied as exc:
            failures.append(str(exc))

    thread = threading.Thread(target=waiting, daemon=True)
    thread.start()
    assert started.wait(1)
    s.halt_admission("unconfirmed cleanup")
    thread.join(1)
    assert not thread.is_alive()
    assert failures and "unconfirmed cleanup" in failures[0]
    assert s.snapshot().active_branches == 1
    s.release_branch(lease)
    s.release_scene(scene)
    with pytest.raises(CapacityDenied, match="halted"):
        admit_scene(s)
