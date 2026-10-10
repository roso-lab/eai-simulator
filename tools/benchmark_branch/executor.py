"""Fresh-process branch execution with bounded parent collection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path
from queue import Empty
import os
import time
import traceback
import uuid
from typing import Any, Mapping, TextIO

from .adapters import SceneAdapter
from .models import BranchJob, BranchPlan, CheckpointArtifact, ResourceSnapshot
from .resources import ResourceProbe, resource_row
from .store import BranchRunStore
from .session import backend_method


@dataclass(frozen=True)
class WorkerResult:
    branch_id: str
    logical_branch_id: str
    status: str
    worker_instance_id: str
    worker_session_id: str
    pid: int
    started_at_ns: int
    heartbeat_first_ns: int | None = None
    first_tick: int | None = None
    last_tick: int | None = None
    ended_at_ns: int | None = None
    error: str = ""


def _call(adapter: Any, name: str, *args: Any, default: Any = None) -> Any:
    fn = getattr(adapter, name, None)
    return fn(*args) if callable(fn) else default


def _supported_execution_scopes(adapter: Any) -> frozenset[str]:
    """Return an explicit adapter scope declaration, failing closed.

    Older adapters remain usable for protocol smoke runs, but they cannot be
    promoted to ``measured`` merely by changing a CLI flag.
    """

    raw = getattr(adapter, "supported_execution_scopes", None)
    if raw is None:
        return frozenset({"protocol_synthetic"})
    if isinstance(raw, str):
        raw = (raw,)
    return frozenset(str(item) for item in raw)


def _worker_main(adapter: Any, job: BranchJob, checkpoint: CheckpointArtifact, branch_dir: str, out: Any) -> None:
    pid = os.getpid()
    instance_id = f"worker-{pid}"
    session_id = str(uuid.uuid4())
    started = time.time_ns()
    common = {**job.identity(), "worker_instance_id": instance_id, "worker_session_id": session_id,
              "worker_pid": pid, "execution_scope": job.execution_scope,
              "scene_build_digest": job.scene_build_digest or str(checkpoint.metadata.get("scene_build_digest", "")),
              "asset_resolution_digest": job.asset_resolution_digest or str(checkpoint.metadata.get("asset_resolution_digest", "")),
              "scene_build_order_digest": job.scene_build_order_digest or str(checkpoint.metadata.get("scene_build_order_digest", "")),
              "skill_adapter_version": job.skill_adapter_version or str(checkpoint.metadata.get("skill_adapter_version", "")),
              "checkpoint_digest": checkpoint.digest, "restore_checkpoint_digest": checkpoint.digest}
    runtime = None
    first_heartbeat: int | None = None
    first_tick: int | None = None
    last_tick: int | None = None
    local_ledger: Path | None = None
    local_stream: TextIO | None = None
    local_event_seq = 0

    def put(kind: str, payload: Mapping[str, Any], *, event: bool = False) -> None:
        nonlocal local_event_seq
        row = {**common, **dict(payload), "event_record": event, "ledger": kind}
        if event:
            row["event_seq"] = local_event_seq
            local_event_seq += 1
        if local_stream is not None:
            local_stream.write(__import__("json").dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            local_stream.flush()
            # Lifecycle boundaries are durable before the worker reports
            # completion.  Step/heartbeat/observation rows are flushed
            # but avoid a syscall per row; the parent ledger remains the
            # authoritative append-only record.
            if event or kind in {"checkpoints", "episodes", "result", "branch_manifest"}:
                os.fsync(local_stream.fileno())
        out.put((kind, row), block=True)

    try:
        Path(branch_dir).mkdir(parents=True, exist_ok=True)
        local_ledger = Path(branch_dir) / "ledger.jsonl"
        local_stream = local_ledger.open("a", encoding="utf-8")
        Path(branch_dir, "worker.json").write_text(
            __import__("json").dumps({"pid": pid, "worker_instance_id": instance_id,
                                      "worker_session_id": session_id, "branch_id": job.branch_id}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        scene = _call(adapter, "create_scene", job, default=None)
        runtime = _call(adapter, "prepare_base_instance", job, checkpoint, default=None)
        if runtime is None:
            runtime = scene if scene is not None else {}
        if isinstance(runtime, dict):
            runtime.setdefault("slot", job.slot)
        put("metrics", {"kind": "worker_started", "started_at_ns": started})
        restored = _call(adapter, "restore_checkpoint", runtime, checkpoint, default=True)
        valid = _call(adapter, "validate_restore", runtime, checkpoint, restored, default=True)
        if isinstance(valid, Mapping):
            restore_ok = bool(valid.get("passed", valid.get("valid", False)))
            restore_row = dict(valid)
        else:
            restore_ok = bool(valid)
            restore_row = {"passed": restore_ok}
        put("checkpoints", {"checkpoint_id": checkpoint.checkpoint_id,
                             "snapshot_digest": checkpoint.digest,
                             "restore_checkpoint_digest": checkpoint.digest,
                             "restore_passed": restore_ok,
                             "restore_status": "passed" if restore_ok else "failed",
                             "restore_error": restore_row.get("error", ""),
                             "restore_error_source": restore_row.get("error_source", ""),
                             "readback_digest": restore_row.get("readback_digest", ""),
                             "max_state_error": restore_row.get("max_state_error"),
                             "error_threshold": restore_row.get("error_threshold"),
                             "verification_steps": restore_row.get("verification_steps", []),
                             "restore": restore_row})
        if not restore_ok:
            raise RuntimeError("restore mismatch")
        _call(adapter, "apply_treatment", runtime, job)
        step = 0
        done = False
        while not done:
            now = time.time_ns()
            first_heartbeat = first_heartbeat or now
            put("metrics", {"kind": "heartbeat", "heartbeat_at_ns": now, "simulator_step": step})
            result = _call(adapter, "step", runtime, job, default={"done": True})
            state = dict(result) if isinstance(result, Mapping) else {"done": bool(result)}
            done = bool(state.get("done", state.get("completed", False)))
            if first_tick is None:
                first_tick = step
            last_tick = step
            put("steps", {"simulator_step": step, "sim_time_s": float(step),
                           "commands": state.get("commands", {}), "post_step_state": state,
                           "phase_state": state.get("phase_state", ""), "task_state": state.get("task_state", ""),
                           "milestone_state": state.get("milestones", {}), "collision_contact": state.get("collision_contact", {}),
                           "safety_state": state.get("safety", ""), "active": not done,
                           "completed": done, "terminated": done})
            external = _call(adapter, "observe_external_state", runtime, job, default={}) or {}
            put("observations", {"simulator_step": step, "observations": dict(external),
                                 "sensor_frames": external.get("sensor_frames", []) if isinstance(external, Mapping) else [],
                                 "observation_consumption_event": external.get("observation_consumption_event", []) if isinstance(external, Mapping) else []})
            progress = _call(adapter, "collect_progress", runtime, job, default={}) or {}
            put("progress", {"simulator_step": step, "external_state": dict(external), "progress": dict(progress)})
            network = _call(adapter, "collect_network", runtime, job, default={}) or {}
            if network:
                put("network", {"simulator_step": step, "network": dict(network), "message_sent": network.get("message_sent", 0),
                                 "message_arrived": network.get("message_arrived", 0), "message_consumed": network.get("message_consumed", 0),
                                 "decision_made": network.get("decision_made", 0)})
            cost = _call(adapter, "collect_cost", runtime, job, default={}) or {}
            if cost:
                put("costs", {"simulator_step": step, "cost": dict(cost)})
            for event in (_call(adapter, "collect_events", runtime, job, default=()) or ()):
                put("events", {"simulator_step": step, **dict(event)}, event=True)
            step += 1
            if step > int(job.metadata.get("max_steps", 100000)):
                raise TimeoutError("method timeout/max_steps")
        outcome = _call(adapter, "finish_episode", runtime, job, default={}) or {}
        put("episodes", {"status": outcome.get("status", "valid_terminal"), "completed": True,
                          "simulator_step": step, "outcome": dict(outcome),
                          "ended_at_ns": time.time_ns()})
        result = WorkerResult(job.branch_id, job.logical_branch_id, "completed", instance_id, session_id,
                              pid, started, first_heartbeat, first_tick, last_tick, time.time_ns())
        put("result", asdict(result))
    except BaseException as exc:
        put("episodes", {"status": "crashed" if isinstance(exc, (SystemExit, KeyboardInterrupt)) else "failed",
                          "completed": False, "error": f"{type(exc).__name__}: {exc}",
                          "traceback": traceback.format_exc(), "ended_at_ns": time.time_ns()})
        result = WorkerResult(job.branch_id, job.logical_branch_id, "failed", instance_id, session_id,
                              pid, started, first_heartbeat, first_tick, last_tick, time.time_ns(),
                              f"{type(exc).__name__}: {exc}")
        put("result", asdict(result))
    finally:
        if local_stream is not None:
            try:
                local_stream.flush()
                os.fsync(local_stream.fileno())
                local_stream.close()
            except OSError:
                pass
        try:
            _call(adapter, "close", runtime, default=None)
        except BaseException:
            pass


class BranchExecutor:
    """Execute logical jobs in an isolated or shared-session backend.

    The historical backend starts one process per branch.  A scene adapter can
    opt into ``execution_mode=shared_session`` and provide
    :meth:`SharedSessionAdapter.run_shared_session`; in that mode this class
    never starts a branch process.  The adapter starts one scene-owned
    simulator session and owns the vectorized environment slots.
    """

    def __init__(self, plan: BranchPlan, adapter: SceneAdapter, store: BranchRunStore,
                 *, max_workers: int = 1, queue_size: int | None = None,
                 timeout_s: float | None = None, resource_limit: Mapping[str, Any] | None = None,
                 start_method: str = "spawn", session_backend: Any = None,
                 scheduler: Any = None, requested_device: str = "cuda:0",
                 scene_reservation: Any = None, branch_reservation: Any = None,
                 warmup_ticks: int = 0):
        # The store owns the durable run id.  Use its frozen plan so a CLI
        # run never emits the transient ``planned`` identity.
        self.plan, self.adapter, self.store = store.plan, adapter, store
        self.requested_max_workers = max(1, int(max_workers))
        self.queue_size = int(queue_size or max(32, self.requested_max_workers * 8))
        self.timeout_s = timeout_s
        self.resource_limit = dict(resource_limit or {})
        self.resource_probe = ResourceProbe()
        self.ctx = get_context(start_method)
        self.session_backend = session_backend
        self.scheduler = scheduler
        self.requested_device = str(requested_device)
        self.scene_reservation = scene_reservation
        self.branch_reservation = branch_reservation
        self.warmup_ticks = max(0, int(warmup_ticks))

    def _run_session_backend(self, jobs: list[BranchJob], checkpoint: CheckpointArtifact) -> dict[str, Any]:
        """Run scene-owned sessions under explicit scheduler leases.

        This path is deliberately opt-in.  It is the integration seam for a
        real ExperimentBatch runner: the scheduler admits a scene, the
        backend opens and warms the simulator, device preflight runs before
        any branch lease, then each branch gets its own lease/runtime.  Scene
        groups execute concurrently so two scenes can be resident at once;
        the scheduler remains the authority for the actual capacity.
        """

        if self.session_backend is None or self.scheduler is None:
            raise ValueError("session_backend and scheduler must be supplied together")
        from tools.physics_replay.execution.scheduler import ReplayRequest, ResourceReservation

        backend = self.session_backend
        scene_reservation = self.scene_reservation or ResourceReservation(0, 0)
        branch_reservation = self.branch_reservation or ResourceReservation(0, 0)
        grouped: dict[str, list[BranchJob]] = {}
        for job in jobs:
            grouped.setdefault(job.scene_key, []).append(job)
        # Select the session backend before any branch row is emitted so the
        # store also creates branch-local mirrors for this path.
        self.store.update_manifest(execution_backend="session_backend",
                                   scheduler_integrated=True)
        records_lock = __import__("threading").RLock()
        scene_results: list[dict[str, Any]] = []
        peak_state = {"value": 0}

        def append_scheduler_audit(row: Mapping[str, Any]) -> None:
            # Resource/audit rows do not carry benchmark identity.  Keep the
            # scheduler evidence separate from evaluator outcomes.
            self.store.append("resources", {"scheduler_audit": dict(row),
                                             "execution_scope": self.plan.execution_scope}, sync=True)

        def run_scene(scene_id: str, scene_jobs: list[BranchJob]) -> dict[str, Any]:
            completed = failed = 0
            scene_lease = None
            session = None
            active_runtime_ids: set[int] = set()
            try:
                scene_lease = self.scheduler.acquire_scene(
                    scene_id, reservation=scene_reservation,
                    requested_device=self.requested_device, wait=True,
                    timeout_s=self.timeout_s,
                )
                session = backend.open_scene(scene_id, scheduler=self.scheduler)
                warmup = backend_method(backend, "warmup")
                if warmup is not None:
                    warmup(session, ticks=self.warmup_ticks)
                request = ReplayRequest(
                    scene_id=scene_id, branch_key=f"{scene_id}/preflight",
                    reservation=branch_reservation, requested_device=self.requested_device,
                    warmup_ticks=self.warmup_ticks,
                )
                evidence = self.scheduler.preflight(request, session)
                if not evidence.valid:
                    reason = "; ".join(evidence.diagnostics) or evidence.device_validation
                    for job in scene_jobs:
                        self.store.append("episodes", {
                            "status": "infrastructure_invalid", "completed": False,
                            "physical_success": None, "error": reason,
                            "infrastructure_preflight": True,
                            "device_evidence": evidence.__dict__,
                        }, job=job)
                        failed += 1
                    return {"scene_id": scene_id, "completed": 0, "failed": len(scene_jobs),
                            "preflight": evidence.__dict__}
                def run_branch(job: BranchJob) -> bool:
                    """Run one isolated branch runtime inside this scene.

                    A scene session may contain many independent branch slots.
                    The scheduler lease, backend runtime and branch-local ledger
                    are the isolation boundary; the backend must not share
                    mutable controller, queue, RNG or checkpoint state between
                    runtimes.  Running these calls in a bounded pool is what
                    makes ``max_branches_per_scene`` observable as real
                    overlap instead of sequential admission.
                    """

                    branch_lease = None
                    branch_runtime = None
                    started = time.time_ns()
                    receipt = {"heartbeat": False, "first_tick": None,
                               "last_tick": None, "restore_passed": None}
                    try:
                        branch_lease = self.scheduler.acquire_branch(
                            scene_lease, branch_key=job.branch_id,
                            reservation=branch_reservation,
                            requested_device=self.requested_device, wait=True,
                            timeout_s=self.timeout_s, warmup_ticks=self.warmup_ticks,
                        )
                        with records_lock:
                            peak_state["value"] = max(
                                peak_state["value"],
                                int(self.scheduler.snapshot().active_branches),
                            )
                        self.store.append("metrics", {
                            "kind": "session_branch_started",
                            "lease_id": branch_lease.lease_id,
                            "scene_id": scene_id,
                            "branch_key": job.branch_id,
                            "requested_device": evidence.requested_device,
                            "actual_physics_device": evidence.actual_physics_device,
                            "controller_device": evidence.controller_device,
                            "gpu_uuid": evidence.gpu_uuid,
                            "warmup_ticks": self.warmup_ticks,
                            "episode_start_time_ns": started,
                        }, job=job, sync=True)
                        branch_runtime = backend.open_branch(session, job, lease=branch_lease)
                        if branch_runtime is None:
                            raise RuntimeError("SessionBackend.open_branch returned no branch runtime")
                        runtime_identity = id(branch_runtime)
                        with records_lock:
                            if runtime_identity in active_runtime_ids:
                                raise RuntimeError(
                                    "SessionBackend returned one mutable branch runtime for multiple active branches"
                                )
                            active_runtime_ids.add(runtime_identity)

                        def emit(ledger: str, payload: Mapping[str, Any], *, event: bool = False) -> None:
                            row = dict(payload)
                            row.setdefault("worker_instance_id", f"branch-worker-{branch_lease.lease_id}")
                            row.setdefault("worker_session_id", f"{scene_lease.lease_id}/{branch_lease.lease_id}")
                            row.setdefault("worker_pid", os.getpid())
                            if ledger == "metrics" and row.get("kind") == "heartbeat":
                                receipt["heartbeat"] = True
                            if ledger == "steps" and row.get("simulator_step") is not None:
                                step = int(row["simulator_step"])
                                receipt["first_tick"] = step if receipt["first_tick"] is None else min(receipt["first_tick"], step)
                                receipt["last_tick"] = step if receipt["last_tick"] is None else max(receipt["last_tick"], step)
                            if ledger == "checkpoints" and row.get("restore_passed") is not None:
                                receipt["restore_passed"] = bool(row["restore_passed"])
                            self.store.append(ledger, row, job=job, event=event,
                                              sync=event or ledger in {"checkpoints", "episodes"})

                        result = backend.run_branch(branch_runtime, job, emit=emit)
                        result = dict(result) if isinstance(result, Mapping) else {}
                        status = str(result.get("status", "missing_response"))
                        branch_completed = bool(result.get("completed", False))
                        if not result or "completed" not in result or "status" not in result:
                            status = "missing_response"
                            branch_completed = False
                        execution_scope = str(self.store.manifest.get("execution_scope") or self.plan.execution_scope)
                        if execution_scope == "measured":
                            heartbeat = bool(result.get("heartbeat", receipt["heartbeat"]))
                            first_tick = result.get("first_tick", receipt["first_tick"])
                            last_tick = result.get("last_tick", receipt["last_tick"])
                            restore_passed = result.get("restore_passed", receipt["restore_passed"])
                            if not heartbeat or first_tick is None or last_tick is None or restore_passed is not True:
                                status = "infrastructure_invalid"
                                branch_completed = False
                                result["physical_success"] = None
                                result["infrastructure_error"] = {
                                    "heartbeat": heartbeat, "first_tick": first_tick,
                                    "last_tick": last_tick, "restore_passed": restore_passed,
                                }
                            else:
                                # A measured run may only expose a physical
                                # outcome when an external evaluator supplies
                                # the evidence envelope.  Process completion,
                                # a horizon tick, or a backend-provided
                                # ``physical_success`` flag is not evidence.
                                required = ("evaluator_version", "evidence_hash",
                                            "evidence_refs", "evidence_source")
                                evaluator_ok = (
                                    all(bool(result.get(field)) for field in required)
                                    and result.get("evidence_source") == "external_evaluator"
                                )
                                terminal_candidate = (
                                    branch_completed
                                    or status in {"completed", "success", "valid_terminal",
                                                   "safe_stop", "team_state_infeasible"}
                                    or result.get("physical_success") is not None
                                )
                                if terminal_candidate and not evaluator_ok:
                                    status = "not_evaluable"
                                    branch_completed = False
                                    result["physical_success"] = None
                                    result["evaluator_error"] = {
                                        "required": required,
                                        "evidence_source": result.get("evidence_source"),
                                    }
                        episode = {
                            "status": status, "completed": branch_completed,
                            "outcome": result.get("outcome", result),
                            "physical_success": result.get("physical_success"),
                            "scheduler_lease_id": branch_lease.lease_id,
                            # Backend supplied identities are preferred, but
                            # the collector must still give every branch a
                            # distinct auditable fallback identity.
                            "worker_instance_id": str(result.get(
                                "worker_instance_id", f"branch-worker-{branch_lease.lease_id}")),
                            "worker_session_id": str(result.get(
                                "worker_session_id", f"{scene_lease.lease_id}/{branch_lease.lease_id}")),
                            "episode_start_time_ns": started,
                            "episode_end_time_ns": time.time_ns(),
                        }
                        for field in ("evaluator_version", "evidence_hash", "evidence_refs",
                                      "evidence_source", "milestones", "terminal_predicate",
                                      "safety_predicate", "feasibility_predicate",
                                      "first_tick", "last_tick", "heartbeat_first_ns",
                                      "heartbeat_last_ns", "restore_passed", "restore_error"):
                            if field in result:
                                episode[field] = result[field]
                        self.store.append("episodes", episode, job=job, sync=True)
                        self.scheduler.release_branch(
                            branch_lease, status="completed" if branch_completed else status,
                        )
                        branch_lease = None
                        return branch_completed
                    except BaseException as exc:
                        self.store.append("episodes", {
                            "status": "crashed", "completed": False,
                            "physical_success": None,
                            "error": f"{type(exc).__name__}: {exc}",
                            "worker_instance_id": (
                                f"branch-worker-{branch_lease.lease_id}" if branch_lease else ""
                            ),
                            "worker_session_id": (
                                f"{scene_lease.lease_id}/{branch_lease.lease_id}"
                                if branch_lease else ""
                            ),
                            "episode_start_time_ns": started,
                            "episode_end_time_ns": time.time_ns(),
                        }, job=job, sync=True)
                        if branch_lease is not None:
                            self.scheduler.release_branch(
                                branch_lease, status="aborted",
                                reason=f"{type(exc).__name__}: {exc}",
                            )
                        return False
                    finally:
                        if branch_runtime is not None:
                            with records_lock:
                                active_runtime_ids.discard(id(branch_runtime))
                            close_branch = backend_method(backend, "close_branch")
                            if close_branch is not None:
                                try:
                                    close_branch(branch_runtime)
                                except BaseException as close_exc:
                                    self.store.append("resources", {
                                        "kind": "branch_close_error", "scene_id": scene_id,
                                        "branch_id": job.branch_id,
                                        "error": f"{type(close_exc).__name__}: {close_exc}",
                                    }, job=job, sync=True)

                # Admit/run branch slots concurrently within one scene-owned
                # session.  The scheduler remains authoritative when global
                # or per-scene capacity is lower than this requested pool.
                branch_pool_workers = max(1, min(
                    self.requested_max_workers, len(scene_jobs),
                    int(self.scheduler.capacity.max_branches_per_scene),
                    int(self.scheduler.capacity.max_parallel_branches),
                ))
                with ThreadPoolExecutor(max_workers=branch_pool_workers) as branch_pool:
                    branch_futures = [branch_pool.submit(run_branch, job) for job in scene_jobs]
                    branch_outcomes = [future.result() for future in as_completed(branch_futures)]
                completed = sum(1 for outcome in branch_outcomes if outcome)
                failed = len(branch_outcomes) - completed
            except BaseException as exc:
                # A scene/session failure must leave one raw receipt per
                # branch so resume and validation can account for it.
                for job in scene_jobs:
                    self.store.append("episodes", {
                        "status": "crashed", "completed": False,
                        "physical_success": None,
                        "error": f"scene session failed: {type(exc).__name__}: {exc}",
                    }, job=job, sync=True)
                completed = 0
                failed = len(scene_jobs)
                return {"scene_id": scene_id, "completed": completed,
                        "failed": failed, "error": str(exc)}
            finally:
                if session is not None:
                    close_scene = backend_method(backend, "close_scene")
                    if close_scene is not None:
                        try:
                            close_scene(session)
                        except BaseException as close_exc:
                            self.store.append("resources", {
                                "kind": "session_close_error", "scene_id": scene_id,
                                "error": f"{type(close_exc).__name__}: {close_exc}",
                            }, sync=True)
                if scene_lease is not None:
                    try:
                        self.scheduler.release_scene(
                            scene_lease,
                            status="completed" if failed == 0 else "aborted",
                            reason="session branch failure" if failed else "",
                        )
                    except BaseException as release_exc:
                        self.store.append("resources", {
                            "kind": "scene_lease_release_error", "scene_id": scene_id,
                            "lease_id": scene_lease.lease_id,
                            "error": f"{type(release_exc).__name__}: {release_exc}",
                        }, sync=True)
            return {"scene_id": scene_id, "completed": completed, "failed": failed}

        with ThreadPoolExecutor(max_workers=max(1, len(grouped))) as pool:
            futures = [pool.submit(run_scene, scene_id, scene_jobs)
                       for scene_id, scene_jobs in grouped.items()]
            for future in as_completed(futures):
                scene_results.append(future.result())

        # Capture the scheduler's immutable audit stream after all scene
        # threads have released their leases.  Each audit row remains resource
        # evidence and is intentionally separate from evaluator outcomes.
        for audit in self.scheduler.audits:
            append_scheduler_audit(audit)
        completed = sum(item["completed"] for item in scene_results)
        failed = sum(item["failed"] for item in scene_results)
        self.store.update_manifest(
            execution_backend="session_backend", scheduler_integrated=True,
            requested=len(jobs), resident=0, active=0, completed=completed,
            failed=failed, queued=0,
            observed_peak_branch_workers=peak_state["value"],
            infrastructure_status="valid" if failed == 0 else "invalid_or_failed",
        )
        self.store.finish()
        from .validation import summarize_run, validate_run
        validate_run(self.store.root)
        summarize_run(self.store.root)
        snapshot = self.scheduler.snapshot()
        return {
            "run_id": self.store.run_id,
            "execution_backend": "session_backend",
            "scheduler_integrated": True,
            "logical_branches": len(jobs), "completed": completed,
            "failed": failed, "queued": 0,
            "observed_peak_branch_workers": peak_state["value"],
            "active": snapshot.active_branches,
            "scene_results": scene_results,
            "scheduler_snapshot": dict(snapshot.__dict__),
        }

    def _checkpoint(self) -> CheckpointArtifact:
        value = _call(self.adapter, "create_checkpoint", self.plan, default=None)
        if isinstance(value, CheckpointArtifact):
            return value
        # Generic plugins may expose only the protocol method
        # ``capture_checkpoint``.  Construct one bootstrap base instance in
        # the parent solely for fan-out; all experiment branches still start
        # in fresh processes from this immutable artifact.
        if callable(getattr(self.adapter, "capture_checkpoint", None)):
            bootstrap = CheckpointArtifact.from_state({"bootstrap": True}, checkpoint_id="bootstrap")
            first_job = self.plan.jobs()[0]
            scene = _call(self.adapter, "create_scene", first_job, default=None)
            runtime = _call(self.adapter, "prepare_base_instance", first_job, bootstrap, default=scene)
            captured = _call(self.adapter, "capture_checkpoint", runtime, first_job, default=None)
            _call(self.adapter, "close", runtime, default=None)
            if isinstance(captured, CheckpointArtifact):
                return captured
            if captured is not None:
                metadata = captured.get("metadata", {}) if isinstance(captured, Mapping) else {}
                state = captured.get("state", captured) if isinstance(captured, Mapping) else captured
                return CheckpointArtifact.from_state(state, metadata=metadata)
        if value is None:
            value = {"scene_key": self.plan.scene_key, "task_id": self.plan.task_id, "simulator_step": 0}
        return CheckpointArtifact.from_state(value, metadata={"task_contract_digest": ""})

    def _jobs_to_run(self) -> list[BranchJob]:
        jobs = list(self.plan.jobs())
        existing_attempts: dict[str, int] = {}
        done: set[str] = set()
        for row in self.store.read_ledger("branch_manifest"):
            logical = str(row.get("logical_branch_id") or str(row.get("branch_id", "")).split("#attempt-", 1)[0])
            existing_attempts[logical] = max(existing_attempts.get(logical, -1), int(row.get("attempt", 0)))
        for row in self.store.read_ledger("episodes"):
            logical = str(row.get("logical_branch_id") or str(row.get("branch_id", "")).split("#attempt-", 1)[0])
            if bool(row.get("completed")) and int(row.get("attempt", 0)) >= existing_attempts.get(logical, 0):
                done.add(logical)
        result = []
        for job in jobs:
            if job.logical_branch_id in done:
                continue
            attempt = existing_attempts.get(job.logical_branch_id, -1) + 1
            result.append(job.with_attempt(attempt) if attempt else job)
        return result

    def _run_shared_session(self, jobs: list[BranchJob], checkpoint: CheckpointArtifact) -> dict[str, Any]:
        """Run all jobs through one adapter-owned scene session.

        The generic collector remains the only writer of the durable ledgers.
        This keeps shared-session rows auditable and makes resume semantics
        identical to the fresh-process backend.  The adapter may emit rows
        synchronously or return a ``rows`` list after collecting them in a
        child process; both forms use the same callback.
        """
        runner = getattr(self.adapter, "run_shared_session", None)
        if not callable(runner):
            raise TypeError(
                "execution_mode=shared_session requires adapter.run_shared_session(...)"
            )
        requested_envs = int(self.store.manifest.get("shared_session_envs") or
                             self.store.manifest.get("requested_max_workers") or
                             self.requested_max_workers)
        requested_envs = max(1, min(requested_envs, self.requested_max_workers, len(jobs)))
        admitted, reason = self.resource_probe.admission(requested_envs, self.resource_limit)
        admitted = max(1, min(requested_envs, int(admitted)))
        if admitted < requested_envs and not reason:
            reason = "resource or vectorized environment admission"
        session_id = f"shared-session-{uuid.uuid4()}"
        jobs_by_branch = {job.branch_id: job for job in jobs}
        emitted_results: dict[str, Mapping[str, Any]] = {}
        started = time.time_ns()
        self.store.update_manifest(
            execution_backend="shared_session",
            shared_session_id=session_id,
            requested_shared_envs=requested_envs,
            admitted_shared_envs=admitted,
            independent_branch_sessions=1,
            resident=0,
            active=0,
            queued=len(jobs),
            observed_peak_branch_workers=0,
            concurrency_reduced=bool(reason),
            reduction_reason=reason,
        )
        self.store.append("metrics", {
            "kind": "shared_session_started",
            "shared_session_id": session_id,
            "shared_session_pid": os.getpid(),
            "num_envs": admitted,
            "branch_jobs": len(jobs),
            "static_asset_policy": "shared_scene_assets",
            "dynamic_state_policy": "slot_owned_branch_state",
            "started_at_ns": started,
        }, sync=True)

        def emit(kind: str, payload: Mapping[str, Any], *, event: bool = False,
                 job: BranchJob | None = None) -> None:
            row = dict(payload)
            branch_id = str(row.get("branch_id", ""))
            target = job or jobs_by_branch.get(branch_id)
            ledger = kind if kind in {
                "branch_manifest", "checkpoints", "steps", "episodes", "events",
                "progress", "network", "costs", "resources", "metrics", "observations",
            } else "metrics"
            self.store.append(ledger, row, job=target, event=event or ledger == "events",
                              sync=event or ledger in {"branch_manifest", "checkpoints", "episodes"})
            if kind == "result" or row.get("kind") == "branch_result":
                if branch_id:
                    emitted_results[branch_id] = row

        try:
            result = runner(jobs, checkpoint, emit, num_envs=admitted,
                            timeout_s=self.timeout_s)
        except BaseException as exc:
            # Preserve a terminal row for every slot that was admitted.  The
            # raw branch manifest remains intact and resume can retry them.
            for job in jobs:
                if job.branch_id in emitted_results:
                    continue
                emit("episodes", {
                    "branch_id": job.branch_id,
                    "status": "crashed",
                    "completed": False,
                    "error": f"shared session failed: {type(exc).__name__}: {exc}",
                    "shared_session_id": session_id,
                    "ended_at_ns": time.time_ns(),
                }, job=job)
            self.store.update_manifest(active=0, resident=0, queued=len(jobs),
                                       completed=0, failed=len(jobs),
                                       shared_session_error=f"{type(exc).__name__}: {exc}")
            self.store.append("metrics", {
                "kind": "shared_session_failed",
                "shared_session_id": session_id,
                "error": f"{type(exc).__name__}: {exc}",
            }, sync=True)
            self.store.finish()
            raise

        # Some adapters use a subprocess and return rows after it exits.  Feed
        # those rows through the same collector callback so no second writer is
        # needed.
        if isinstance(result, Mapping):
            for item in result.get("rows", ()) or ():
                if isinstance(item, Mapping):
                    emit(str(item.get("ledger", "metrics")), item,
                         event=bool(item.get("event_record", False)))
        latest: dict[str, dict[str, Any]] = {}
        for row in self.store.read_ledger("episodes"):
            logical = str(row.get("logical_branch_id") or
                          str(row.get("branch_id", "")).split("#attempt-", 1)[0])
            attempt = int(row.get("attempt", 0))
            if logical not in latest or attempt >= int(latest[logical].get("attempt", 0)):
                latest[logical] = row
        completed = sum(bool(row.get("completed")) and row.get("status") in {
            "completed", "valid_terminal", "not_evaluable", "right_censored",
        } for row in latest.values())
        failed = max(0, len(latest) - completed)
        queued = max(0, len(jobs) - len(latest))
        # Admission is a capacity limit, not evidence that the adapter used it.
        observed_peak = 0
        if isinstance(result, Mapping):
            observed_peak = int(result.get("observed_peak_branch_workers") or 0)
            if not 0 <= observed_peak <= admitted:
                raise ValueError("shared-session observed concurrency exceeds admitted capacity")
        self.store.update_manifest(
            execution_backend="shared_session",
            shared_session_id=session_id,
            resident=int(result.get("resident") or 0) if isinstance(result, Mapping) else 0,
            active=0,
            completed=completed,
            failed=failed,
            queued=queued,
            observed_peak_branch_workers=observed_peak,
            cross_slot_contact_count=int((result or {}).get("cross_slot_contact_count", 0)) if isinstance(result, Mapping) else 0,
            slot_isolation_validated=bool((result or {}).get("slot_isolation_validated", False)) if isinstance(result, Mapping) else False,
            finished_at_ns=time.time_ns(),
        )
        self.store.append("metrics", {
            "kind": "shared_session_finished",
            "shared_session_id": session_id,
            "num_envs": admitted,
            "completed": completed,
            "failed": failed,
            "queued": queued,
            "observed_peak_branch_workers": observed_peak,
            "cross_slot_contact_count": self.store.manifest.get("cross_slot_contact_count", 0),
            "slot_isolation_validated": self.store.manifest.get("slot_isolation_validated", False),
            "finished_at_ns": time.time_ns(),
        }, sync=True)
        self.store.finish()
        from .validation import summarize_run, validate_run
        validate_run(self.store.root)
        summarize_run(self.store.root)
        return {
            "run_id": self.store.run_id,
            "logical_branches": self.plan.logical_branch_count,
            "pair_slots": self.plan.pair_slots,
            "execution_backend": "shared_session",
            "requested_shared_envs": requested_envs,
            "admitted_shared_envs": admitted,
            "requested_max_workers": self.requested_max_workers,
            "independent_branch_sessions": 1,
            "observed_peak_branch_workers": observed_peak,
            "completed": completed,
            "failed": failed,
            "queued": queued,
            "slot_isolation_validated": self.store.manifest.get("slot_isolation_validated", False),
            "cross_slot_contact_count": self.store.manifest.get("cross_slot_contact_count", 0),
            "shared_session_result": dict(result or {}) if isinstance(result, Mapping) else {},
        }

    def run(self) -> dict[str, Any]:
        # Freeze scheduler controls into the manifest before any worker is
        # launched so a later audit can distinguish requested policy from the
        # observed resident/active counts.
        self.store.update_manifest(timeout_s=self.timeout_s, resource_limit=dict(self.resource_limit))
        requested_scope = str(self.store.manifest.get("execution_scope") or self.plan.execution_scope)
        supported_scopes = _supported_execution_scopes(self.adapter)
        if requested_scope not in supported_scopes:
            reason = (
                f"adapter {type(self.adapter).__name__} does not support execution_scope="
                f"{requested_scope!r}; declared={sorted(supported_scopes)!r}"
            )
            self.store.update_manifest(
                status="rejected", collector_status="rejected",
                inference_status="unsupported", rejection_reason=reason,
            )
            self.store.finish()
            raise ValueError(reason)
        if requested_scope == "measured" and (self.session_backend is None or self.scheduler is None):
            reason = "measured execution requires an explicit session_backend and ReplayScheduler"
            self.store.update_manifest(
                status="rejected", collector_status="rejected",
                inference_status="unsupported", rejection_reason=reason,
            )
            self.store.finish()
            raise ValueError(reason)
        contract = _call(self.adapter, "validate_contract", self.plan, default=True)
        if isinstance(contract, Mapping) and not bool(contract.get("passed", True)):
            raise ValueError(f"task contract rejected: {contract}")
        checkpoint = self._checkpoint()
        explicit_checkpoint_digests = {
            str(job.checkpoint_digest) for job in self.plan.jobs() if str(job.checkpoint_digest)
        }
        if explicit_checkpoint_digests and explicit_checkpoint_digests != {checkpoint.digest}:
            requested_scope = str(self.store.manifest.get("execution_scope") or self.plan.execution_scope)
            if requested_scope != "protocol_synthetic":
                reason = ("captured checkpoint digest does not match benchmark-owned explicit job "
                          f"digest(s): expected={sorted(explicit_checkpoint_digests)!r}, got={checkpoint.digest!r}")
                self.store.update_manifest(status="rejected", collector_status="rejected",
                                           inference_status="unsupported", rejection_reason=reason)
                self.store.finish()
                raise ValueError(reason)
            # Synthetic adapters may not deserialize an upstream checkpoint,
            # but they must retain its authoritative identity. Keep the
            # adapter digest as diagnostic metadata and never call this measured.
            checkpoint = CheckpointArtifact(
                checkpoint_id=checkpoint.checkpoint_id,
                digest=next(iter(explicit_checkpoint_digests)),
                state=checkpoint.state, schema=checkpoint.schema,
                metadata={**dict(checkpoint.metadata),
                          "captured_state_digest": checkpoint.digest,
                          "checkpoint_digest_source": "benchmark_identity"},
            )
        metadata = dict(checkpoint.metadata)
        self.store.update_manifest(
            task_contract_digest=str(metadata.get("task_contract_digest") or (contract.get("task_contract_digest", "") if isinstance(contract, Mapping) else "")),
            checkpoint_digest=checkpoint.digest,
            scene_build_digest=str(metadata.get("scene_build_digest", "")),
            asset_resolution_digest=str(metadata.get("asset_resolution_digest", "")),
            scene_build_order_digest=str(metadata.get("scene_build_order_digest", "")),
            skill_adapter_version=str(metadata.get("skill_adapter_version", "")),
        )
        jobs = self._jobs_to_run()
        resolved_jobs: list[BranchJob] = []
        for job in jobs:
            updated = replace(job,
                              checkpoint_digest=job.checkpoint_digest or checkpoint.digest,
                              restore_checkpoint_digest=job.restore_checkpoint_digest or checkpoint.digest,
                              task_contract_digest=job.task_contract_digest or str(metadata.get("task_contract_digest", "")),
                              scene_build_digest=job.scene_build_digest or str(metadata.get("scene_build_digest", "")),
                              asset_resolution_digest=job.asset_resolution_digest or str(metadata.get("asset_resolution_digest", "")),
                              scene_build_order_digest=job.scene_build_order_digest or str(metadata.get("scene_build_order_digest", "")),
                              skill_adapter_version=job.skill_adapter_version or str(metadata.get("skill_adapter_version", "")))
            self.store.append("branch_manifest", updated.identity(), job=updated)
            resolved_jobs.append(updated)
        jobs = resolved_jobs
        existing_checkpoint_digests = {
            str(row.get("snapshot_digest") or row.get("checkpoint_digest"))
            for row in self.store.read_ledger("checkpoints")
        }
        if checkpoint.digest not in existing_checkpoint_digests:
            self.store.append("checkpoints", {"checkpoint_id": checkpoint.checkpoint_id, "simulator_step": 0,
                                               "sim_time_s": 0.0, "snapshot_digest": checkpoint.digest,
                                               "snapshot_schema": checkpoint.schema, "base_checkpoint": True,
                                               "metadata": dict(checkpoint.metadata)})
        if not jobs:
            self.store.update_manifest(active=0, queued=0)
            self.store.finish()
            report = dict(self.store.manifest)
            report.update({"run_id": self.store.run_id, "logical_branches": self.plan.logical_branch_count,
                           "pair_slots": self.plan.pair_slots, "results": []})
            return report

        if self.session_backend is not None or self.scheduler is not None:
            return self._run_session_backend(jobs, checkpoint)

        if str(self.store.manifest.get("execution_mode", "")) == "shared_session":
            return self._run_shared_session(jobs, checkpoint)

        requested_admission = min(self.requested_max_workers, len(jobs))
        admitted, reason = self.resource_probe.admission(requested_admission, self.resource_limit)
        admitted = min(requested_admission, max(1, admitted))
        if admitted < requested_admission and not reason:
            reason = "resource or queue admission"
        self.store.update_manifest(admitted_max_workers=admitted,
                                   concurrency_reduced=bool(reason), reduction_reason=reason,
                                   resident=0, active=0, queued=len(jobs))
        queue = self.ctx.Queue(maxsize=self.queue_size)
        pending = list(jobs)
        running: dict[int, tuple[Any, BranchJob]] = {}
        jobs_by_branch = {job.branch_id: job for job in jobs}
        results: dict[str, WorkerResult] = {}
        parent_started_at: dict[int, int] = {}
        peak = 0
        started_at = time.monotonic()
        timed_out = False

        def drain(block: bool = False) -> None:
            # Wait for at most one message, then drain the currently queued
            # batch without blocking.  A continuously producing worker set
            # must not starve the timeout and process-health checks below.
            first = True
            while True:
                try:
                    kind, row = queue.get(timeout=0.05 if block and first else 0.0)
                except Empty:
                    return
                first = False
                branch_id = str(row.get("branch_id", ""))
                job = jobs_by_branch.get(branch_id)
                event_record = bool(row.pop("event_record", False))
                ledger = kind if kind in {"branch_manifest", "checkpoints", "steps", "episodes", "events", "progress", "network", "costs", "resources", "metrics", "observations"} else "metrics"
                lifecycle = event_record or ledger in {"branch_manifest", "checkpoints", "episodes"} or kind == "result"
                self.store.append(ledger, row, job=job, event=event_record, sync=lifecycle)
                if kind == "result":
                    result = WorkerResult(**{key: value for key, value in row.items() if key in WorkerResult.__dataclass_fields__})
                    results[result.branch_id] = result

        while pending or running:
            while pending and len(running) < admitted:
                job = pending.pop(0)
                branch_dir = self.store.root / "branches" / job.branch_id.replace("/", "_").replace("#", "_")
                process = self.ctx.Process(target=_worker_main, args=(self.adapter, job, checkpoint, str(branch_dir), queue), daemon=False)
                process.start()
                running[process.pid] = (process, job)
                parent_started_at[process.pid] = time.time_ns()
                self.store.manifest["resident"] = max(int(self.store.manifest.get("resident", 0)), len(running))
                self.store.manifest["active"] = len(running)
                self.store.manifest["queued"] = len(pending)
                peak = max(peak, len(running))
            self.store.manifest["observed_peak_branch_workers"] = peak
            self.store.append("resources", resource_row(self.resource_probe, active=len(running), worker_pids=tuple(running)), sync=False)
            drain(block=True)
            if self.timeout_s is not None and time.monotonic() - started_at > self.timeout_s:
                timed_out = True
                # Stop only branches that are actually running.  Preserve
                # pending jobs as queued so resume can launch them later, and
                # record a terminal method_timeout row for each interrupted
                # worker.  Previously this path cleared both collections and
                # the final result calculation overwrote the failure count.
                for pid, (process, job) in list(running.items()):
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=1.0)
                # A worker may have put its final result just before the
                # terminate boundary; collect those rows before synthesizing
                # timeout results for the remaining branches.
                drain(block=True)
                for pid, (process, job) in list(running.items()):
                    if job.branch_id in results:
                        continue
                    timeout_session = f"timeout-{uuid.uuid4()}"
                    result = WorkerResult(
                        job.branch_id, job.logical_branch_id, "failed",
                        f"worker-{pid}", timeout_session, pid,
                        parent_started_at.get(pid, time.time_ns()),
                        ended_at_ns=time.time_ns(),
                        error="executor timeout",
                    )
                    results[job.branch_id] = result
                    self.store.append(
                        "episodes",
                        {"status": "method_timeout", "completed": False,
                         "error": result.error, "ended_at_ns": result.ended_at_ns,
                         "worker_instance_id": result.worker_instance_id,
                         "worker_session_id": result.worker_session_id,
                         "started_at_ns": result.started_at_ns},
                        job=job,
                    )
                running.clear()
                self.store.manifest["active"] = 0
                # Do not restart pending jobs after the absolute timeout.
                break
            for pid, (process, job) in list(running.items()):
                if not process.is_alive():
                    process.join(timeout=0.2)
                    running.pop(pid, None)
                    if job.branch_id not in results:
                        crash_session = f"crash-{uuid.uuid4()}"
                        result = WorkerResult(
                            job.branch_id, job.logical_branch_id, "failed",
                            f"worker-{pid}", crash_session, pid,
                            parent_started_at.get(pid, time.time_ns()),
                            ended_at_ns=time.time_ns(),
                            error=f"worker exited with code {process.exitcode}",
                        )
                        results[job.branch_id] = result
                        self.store.append("episodes", {
                            "status": "crashed", "completed": False,
                            "error": result.error,
                            "worker_instance_id": result.worker_instance_id,
                            "worker_session_id": result.worker_session_id,
                            "started_at_ns": result.started_at_ns,
                            "ended_at_ns": result.ended_at_ns,
                        }, job=job)
            self.store.manifest["active"] = len(running)
            self.store.manifest["queued"] = len(pending)
            self.store.persist_state()
        drain(block=False)
        completed = sum(1 for item in results.values() if item.status == "completed")
        failed = len(results) - completed
        self.store.update_manifest(active=0, completed=completed, failed=failed,
                                   queued=len(pending) if timed_out else 0,
                                   observed_peak_branch_workers=peak,
                                   finished_at=_now(), runtime_s=time.monotonic() - started_at)
        self.store.finish()
        report = dict(self.store.manifest)
        report.update({"run_id": self.store.run_id, "logical_branches": self.plan.logical_branch_count,
                       "pair_slots": self.plan.pair_slots,
                       "results": [asdict(item) for item in results.values()]})
        return report


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class BranchWorker:
    """Compatibility wrapper for running a single isolated branch."""

    def __init__(self, adapter: SceneAdapter, job: BranchJob, checkpoint: CheckpointArtifact,
                 *, start_method: str = "spawn", branch_dir: str | Path | None = None) -> None:
        self.adapter, self.job, self.checkpoint = adapter, job, checkpoint
        self.context = get_context(start_method)
        self.branch_dir = Path(branch_dir) if branch_dir else Path.cwd() / ".branch_worker" / job.branch_id.replace("/", "_")

    def run(self) -> WorkerResult:
        queue = self.context.Queue()
        process = self.context.Process(target=_worker_main, args=(self.adapter, self.job, self.checkpoint, str(self.branch_dir), queue))
        process.start(); result = None
        while process.is_alive() or not queue.empty():
            try:
                kind, payload = queue.get(timeout=0.05)
            except Empty:
                continue
            if kind == "result":
                result = WorkerResult(**{key: value for key, value in payload.items() if key in WorkerResult.__dataclass_fields__})
        process.join()
        if result is None:
            return WorkerResult(self.job.branch_id, self.job.logical_branch_id, "crashed",
                                f"worker-{process.pid}", "", process.pid or -1, 0,
                                error="worker crashed")
        return result


__all__ = ["SceneAdapter", "WorkerResult", "BranchExecutor", "BranchWorker"]
