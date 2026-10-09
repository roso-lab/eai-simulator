"""Isaac-independent scheduler smoke used by CI and handoff validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
import time
from typing import Any

from .scheduler import ReplayRequest, ReplayScheduler, ResourceReservation


@dataclass(frozen=True)
class FakeWorkerResult:
    scene_id: str
    branch_key: str
    session_identity: int
    warmup_ticks: int
    episode_ticks: int
    completed: bool


def run_fake_parallel(
    scheduler: ReplayScheduler,
    *,
    scene_id: str = "fake-scene",
    branch_count: int = 4,
    warmup_ticks: int = 2,
    episode_ticks: int = 4,
    reservation: ResourceReservation = ResourceReservation(1, 1),
) -> tuple[FakeWorkerResult, ...]:
    """Run independent fake sessions concurrently through the real scheduler.

    This is a lifecycle/admission smoke only.  It produces no physical-success,
    task-evaluator, or replay-fidelity claim.
    """
    if branch_count < 1 or warmup_ticks < 0 or episode_ticks < 1:
        raise ValueError("branch_count must be positive; ticks must be valid")
    scene = scheduler.acquire_scene(scene_id, reservation=reservation, requested_device="cuda:0")
    scheduler.preflight(
        ReplayRequest(scene_id, "fake-preflight", reservation, "cuda:0", warmup_ticks),
        session=type("FakeSceneSession", (), {
            "actual_physics_device": "cuda:0", "controller_device": "cuda:0"
        })(),
    )
    barrier = (
        threading.Barrier(branch_count)
        if branch_count <= scheduler.capacity.max_parallel_branches
        and branch_count <= scheduler.capacity.max_branches_per_scene
        else None
    )

    def worker(index: int) -> FakeWorkerResult:
        branch_key = f"fake-branch-{index:04d}"
        with scheduler.branch_slot(scene, branch_key=branch_key, reservation=reservation,
                                   requested_device="cuda:0", warmup_ticks=warmup_ticks) as lease:
            # Every worker owns a distinct mutable object.  The barrier gives
            # the smoke a real overlapping active interval.
            session = {"branch_key": branch_key, "ticks": 0}
            identity = id(session)
            if barrier is not None:
                barrier.wait(timeout=10)
            for _ in range(warmup_ticks):
                time.sleep(0)
            for _ in range(episode_ticks):
                session["ticks"] += 1
                time.sleep(0.001)
            return FakeWorkerResult(scene_id, branch_key, identity, warmup_ticks,
                                    session["ticks"], session["ticks"] == episode_ticks)

    try:
        with ThreadPoolExecutor(max_workers=branch_count) as pool:
            results = tuple(pool.map(worker, range(branch_count)))
    finally:
        scheduler.release_scene(scene)
    return results


__all__ = ["FakeWorkerResult", "run_fake_parallel"]
