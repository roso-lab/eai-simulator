"""Scheduler-backed shared-session runner with a collector-owned batch loop."""
from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import time
import uuid

from .evidence import verify_evaluator_evidence
from .models import MEASURED_SCOPES, canonical_digest
from .recovery import METHOD_TERMINALS, verify_recovery_readback
from .vectorized import IsolationError, PARTICIPANTS, SCHEMA, VectorizedSession
from .shutdown_watchdog import (
    FORMAL_QUALIFICATION_GATES,
    SHARED_EXECUTION_MODEL,
    _issue_collector_observation,
    _DeferredCollectorObservationCapability,
    shutdown_with_watchdog,
    validate_shared_session_receipt,
)


def _unqualified_fields():
    return {key: False for key in FORMAL_QUALIFICATION_GATES}


class SharedEquivalenceRejected(IsolationError):
    """Collector observed a terminal slot, but its equivalence chain is invalid."""


_SHUTDOWN_IDENTITY_FIELDS = (
    "shared_session_id", "block_id", "pair_group_id", "checkpoint_digest",
    "environment_slot", "slot_generation", "slot_token", "run_id",
    "base_instance_id",
)


def _shutdown_report(equivalence, *, job=None, binding=None):
    """Build the watchdog report from frozen collector identity only."""

    report = {
        "diagnostic_body_completed": True,
        "execution_model": SHARED_EXECUTION_MODEL,
        "process_failure_scope": "shared_session",
        "shutdown_status": "pending",
    }
    equivalence = equivalence if isinstance(equivalence, Mapping) else {}
    experiment = equivalence.get("experiment_identity")
    if not isinstance(experiment, Mapping):
        experiment = {}
    report.update({
        key: equivalence[key] for key in _SHUTDOWN_IDENTITY_FIELDS
        if key in equivalence
    })
    for key in ("run_id", "base_instance_id"):
        if key not in report and key in experiment:
            report[key] = experiment[key]
    if job is not None:
        report.setdefault("branch_id", job.branch_id)
        report.setdefault("run_id", job.run_id)
        report.setdefault("base_instance_id", job.base_instance_id)
        report.setdefault("pair_group_id", job.pair_group_id)
        report.setdefault("checkpoint_digest", job.checkpoint_digest)
    if binding is not None:
        report.update({
            key: binding.row()[key]
            for key in ("shared_session_id", "environment_slot", "slot_generation", "slot_token")
        })
    return report


def _finalize_shared_shutdown(
    *, store, backend, session, session_id, candidates, scene_closed,
    shutdown_fn, cleanup_state, scene_close_error=None, timeout_s=20.0,
):
    """Persist one collector-owned shutdown receipt per bound branch.

    When a trusted observer exists, the watchdog invokes ``shutdown_fn`` so
    the native close is inside the timeout/exception boundary.  If the
    observer is missing, the watchdog deliberately refuses to invoke the
    shutdown function; this helper then performs the conservative cleanup
    once and records ``unknown-record`` for every candidate.
    """

    observer_factory = getattr(backend, "collect_shutdown_observation", None)
    receipts = []
    output_root = store.root / "shutdown"
    output_root.mkdir(parents=True, exist_ok=True)
    shutdown_attempted = False
    for candidate in candidates:
        if len(candidate) == 2:
            job, equivalence = candidate
            binding = None
        else:
            job, equivalence, binding = candidate
        report = _shutdown_report(equivalence, job=job, binding=binding)
        capability = None
        observer_error = ""
        if callable(observer_factory):
            capability = _DeferredCollectorObservationCapability(
                lambda identity: observer_factory(session, dict(identity))
            )
        elif not scene_closed:
            observer_error = (
                "scene close did not complete"
                + (f": {scene_close_error}" if scene_close_error else "")
            )
        else:
            observer_error = "backend lacks collector-owned shutdown observer"

        safe_branch = job.branch_id.replace("/", "_").replace("#", "_")
        output = output_root / f"{safe_branch}.json"
        try:
            shutdown_attempted = shutdown_attempted or capability is not None
            status = shutdown_with_watchdog(
                report,
                output,
                shutdown_fn,
                timeout_s=timeout_s,
                process_observer=capability,
            )
        except BaseException as exc:
            # The watchdog may have durably recorded a stricter terminal state
            # (notably shutdown_timeout) before propagating the close error.
            # Re-read that receipt instead of replacing it with a caller-side
            # generic unknown status, keeping the ledger and branch receipt
            # consistent.
            status = "unknown-record"
            try:
                persisted_status = json.loads(output.read_text(encoding="utf-8")).get("shutdown_status")
            except (OSError, ValueError, TypeError):
                persisted_status = None
            if isinstance(persisted_status, str) and persisted_status.strip():
                status = persisted_status
            observer_error = (f"{observer_error}; " if observer_error else "") + \
                f"{type(exc).__name__}: {exc}"
        persisted = json.loads(output.read_text(encoding="utf-8"))
        if observer_error:
            persisted["collector_observer_error"] = observer_error
            output.write_text(json.dumps(persisted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        receipt = {
            "kind": "shared_session_shutdown",
            "shared_session_id": session_id,
            "shutdown_status": status,
            "shutdown_receipt_path": str(output.relative_to(store.root)),
            "collector_observer_present": callable(observer_factory),
            "collector_observer_verified": status == "clean",
        }
        if observer_error:
            receipt["collector_observer_error"] = observer_error
        store.append("resources", receipt, job=job, sync=True)
        receipts.append({"branch_id": job.branch_id, **receipt})
    if not cleanup_state.get("closed") and not shutdown_attempted:
        try:
            shutdown_fn()
        except BaseException as exc:
            cleanup_state["error"] = f"{type(exc).__name__}: {exc}"
        scene_closed = bool(cleanup_state.get("closed"))
    if not receipts:
        aggregate = "unknown-record" if not scene_closed else "clean"
    elif any(item["shutdown_status"] == "pair-invalid" for item in receipts):
        aggregate = "pair-invalid"
    elif any(item["shutdown_status"] != "clean" for item in receipts):
        aggregate = "unknown-record"
    else:
        aggregate = "clean"
    store.update_manifest(
        shutdown_status=aggregate,
        shutdown_receipts=receipts,
        shutdown_observer_present=callable(observer_factory),
        **_unqualified_fields(),
    )
    return aggregate, receipts


def _paired_result_digest(row):
    return canonical_digest({key: value for key, value in row.items()
                             if key not in {"result_digest", "schema", "recorded_at"}})


def _paired_terminal_receipt(job, payload):
    """Bind an opted-in paired terminal row to its frozen registration.

    The main project does not infer or schedule paired retries here.  When an
    upstream plan supplies a paired-block registration, the vectorized path
    either emits the same complete terminal identity contract or fails closed.
    """

    row = dict(payload)
    registration = job.metadata.get("paired_block_rerun")
    if not isinstance(registration, Mapping):
        return row
    original_map = registration.get("branch_to_original")
    participants = registration.get("participant_digests")
    if not isinstance(original_map, Mapping) or job.logical_branch_id not in original_map:
        raise IsolationError("paired terminal receipt lacks original-record registration")
    if not isinstance(participants, Mapping) or not participants:
        raise IsolationError("paired terminal receipt lacks participant registration")
    frozen = {
        "block_id": str(registration.get("block_id") or ""),
        "logical_branch_id": job.logical_branch_id,
        "branch_type": job.branch_type,
        "attempt": job.attempt,
        "checkpoint_digest": str(registration.get("checkpoint_digest") or ""),
        "random_stream_digest": str(registration.get("random_stream_digest") or ""),
        "controller_substream_digest": str(
            job.metadata.get("controller_substream_digest")
            or registration.get("controller_substream_digest") or ""
        ),
        "original_record_id": str(original_map[job.logical_branch_id]),
        "participant_digests": dict(participants),
    }
    if any(value in (None, "") for key, value in frozen.items() if key != "participant_digests"):
        raise IsolationError("paired terminal receipt registration is incomplete")
    for key, value in frozen.items():
        if key in row and row[key] != value:
            raise IsolationError(f"paired terminal receipt {key} conflicts with frozen identity")
        row[key] = value
    for key, value in job.identity().items():
        if key in {"worker_instance_id", "worker_session_id"}:
            continue
        if key in row and row[key] != value:
            raise IsolationError(f"paired terminal receipt {key} conflicts with branch identity")
        row[key] = value
    status = str(row.get("status") or "")
    if status in METHOD_TERMINALS:
        failure_class = "method"
    elif status in {"infrastructure_invalid", "simulator_error", "evaluator_error", "crashed"}:
        failure_class = "infrastructure"
    elif status == "skill_postcondition":
        failure_class = "skill_postcondition"
    elif row.get("completed") is True:
        failure_class = ""
    else:
        failure_class = str(row.get("failure_class") or "")
    if "failure_class" in row and str(row["failure_class"]) != failure_class:
        raise IsolationError("paired terminal receipt failure class conflicts with collector status")
    row["failure_class"] = failure_class
    row.pop("result_digest", None)
    row["result_digest"] = _paired_result_digest(row)
    return row


def _shared_equivalence_receipt(job, checkpoint, binding, restore_receipt):
    """Construct collector-owned equivalence evidence after ``manager.bind``.

    ``VectorizedSession.bind`` independently captures native state and enriches
    the backend receipt with observed and expected participant digests.  This
    function binds those observations to immutable job, slot, checkpoint,
    stream, and manifest-tolerance identity before the first shared step.
    """

    registration = job.metadata.get("paired_block_rerun")
    registration = dict(registration) if isinstance(registration, Mapping) else {}
    if job.checkpoint_digest != checkpoint.digest:
        raise SharedEquivalenceRejected("shared-session job checkpoint differs from frozen archive")
    registered_checkpoint = str(registration.get("checkpoint_digest") or checkpoint.digest)
    if registered_checkpoint != checkpoint.digest:
        raise SharedEquivalenceRejected("shared-session paired checkpoint identity mismatch")
    registered_pair = str(registration.get("pair_group_id") or job.pair_group_id)
    if registered_pair != job.pair_group_id:
        raise SharedEquivalenceRejected("shared-session paired experiment identity mismatch")
    new_identity = registration.get("new_experiment_identity")
    if isinstance(new_identity, Mapping):
        expected_identity = {
            "run_id": job.run_id,
            "base_instance_id": job.base_instance_id,
            "pair_group_id": job.pair_group_id,
        }
        if any(str(new_identity.get(key) or "") != value for key, value in expected_identity.items()):
            raise SharedEquivalenceRejected("shared-session reconstructed experiment identity mismatch")

    participant_digests = {
        name: canonical_digest(value)
        for name, value in checkpoint.state["participants"].items()
    }
    registered_participants = registration.get("participant_digests")
    if isinstance(registered_participants, Mapping) and dict(registered_participants) != participant_digests:
        raise SharedEquivalenceRejected("shared-session participant digest registry mismatch")

    expected_archive = str(checkpoint.metadata.get("archive_digest") or checkpoint.digest)
    manifest_archive = str(job.metadata.get("archive_digest") or expected_archive)
    if manifest_archive != expected_archive:
        raise SharedEquivalenceRejected("shared-session archive digest differs from frozen manifest")

    expected_tolerance = checkpoint.metadata.get("restore_deviation_tolerance")
    manifest_tolerance = job.metadata.get(
        "restore_deviation_tolerance",
        0.0 if expected_tolerance is None else expected_tolerance,
    )
    if expected_tolerance is not None and manifest_tolerance != expected_tolerance:
        raise SharedEquivalenceRejected("shared-session restore tolerance differs from frozen manifest")

    observed_digest = str(restore_receipt.get("participant_state_digest") or "")
    expected_digest = str(restore_receipt.get("expected_participant_state_digest") or "")
    observer_verified = bool(
        observed_digest
        and expected_digest
        and observed_digest == expected_digest
        and restore_receipt.get("state_digest") == observed_digest
        and restore_receipt.get("readback_digest") == checkpoint.digest
    )
    normalized_deviation = restore_receipt.get(
        "normalized_deviation",
        0.0 if observer_verified else float("inf"),
    )
    random_stream_digest = str(
        registration.get("random_stream_digest")
        or canonical_digest({
            "pair_seed": job.pair_seed,
            "rng_namespace": job.rng_namespace,
            "communication_rng_namespace": job.communication_rng_namespace,
        })
    )
    controller_substream_digest = str(
        job.metadata.get("controller_substream_digest")
        or registration.get("controller_substream_digest")
        or canonical_digest({
            "skill_adapter_version": job.skill_adapter_version,
            "pair_group_id": job.pair_group_id,
        })
    )
    receipt = {
        "execution_model": SHARED_EXECUTION_MODEL,
        "shared_session_id": binding.session_id,
        "environment_slot": binding.environment_slot,
        "slot_generation": binding.generation,
        "expected_slot_generation": binding.generation,
        "slot_token": binding.token,
        "branch_id": job.branch_id,
        "block_id": str(registration.get("block_id") or job.pair_group_id),
        "pair_group_id": job.pair_group_id,
        "experiment_identity": {
            "run_id": job.run_id,
            "base_instance_id": job.base_instance_id,
            "pair_group_id": job.pair_group_id,
        },
        "checkpoint_digest": checkpoint.digest,
        "archive_digest": expected_archive,
        "random_stream_digest": random_stream_digest,
        "controller_substream_digest": controller_substream_digest,
        "process_failure_scope": "shared_session",
        "shutdown_status": "pending",
        "participant_digests": participant_digests,
        "snapshot_digest": expected_digest,
        "snapshot_readback_digest": observed_digest,
        "normalized_deviation": normalized_deviation,
        "deviation_tolerance": manifest_tolerance,
        "restore_receipt": {
            "observer_verified": observer_verified,
            "restored": restore_receipt.get("restore_passed") is True and observer_verified,
            "observer_source": "VectorizedSession.bind.capture_readback",
        },
    }
    try:
        return validate_shared_session_receipt(receipt)
    except (TypeError, ValueError) as exc:
        raise SharedEquivalenceRejected(f"shared-session restore equivalence rejected: {exc}") from exc


def run_vectorized(executor, jobs, checkpoint):
    from tools.physics_replay.execution.scheduler import ReplayRequest, ResourceReservation, CapacityDenied

    backend, scheduler, store = executor.session_backend, executor.scheduler, executor.store
    if backend is None or scheduler is None or getattr(backend, "vectorized_contract", None) != SCHEMA:
        raise ValueError("managed shared_session requires a vectorized backend and scheduler")
    requested = min(executor.requested_max_workers, int(store.manifest.get("shared_session_envs")
                                                        or executor.requested_max_workers))
    if requested < 1:
        raise ValueError("shared_session_envs must be positive")
    capacity = min(requested, scheduler.capacity.max_parallel_branches,
                   scheduler.capacity.max_branches_per_scene)
    scene_reservation = executor.scene_reservation or ResourceReservation(0, 0)
    branch_reservation = executor.branch_reservation or ResourceReservation(0, 0)
    measured = executor.plan.execution_scope in MEASURED_SCOPES
    results = {}
    peak = 0
    sequence = max((row.get("vector_seq", -1) for name in (
        "resources", "metrics", "checkpoints", "steps", "observations", "events", "episodes",
        "network", "costs", "progress") for row in store.read_ledger(name)), default=-1) + 1
    session_ids = []
    receipts = []
    jobs_by_id = {job.branch_id: job for job in jobs}
    store.update_manifest(execution_backend="vectorized_session", scheduler_integrated=True,
                          execution_model=SHARED_EXECUTION_MODEL,
                          vectorized_contract=SCHEMA, process_failure_scope="shared_session",
                          isolation_evidence_scope="registered_state_and_io_checks",
                          causal_equivalence_status="requires_backend_differential_qualification",
                          requested_shared_envs=requested, admitted_shared_envs=capacity,
                          slot_isolation_validated=False, requested=executor.plan.logical_branch_count,
                          **_unqualified_fields())

    def emit(ledger, payload, *, job=None, binding=None):
        nonlocal sequence
        row = dict(payload)
        job = job or jobs_by_id.get(row.get("branch_id"))
        if binding is not None:
            for key, value in binding.row().items():
                if key in row and row[key] != value:
                    raise IsolationError(f"slot output cannot override {key}")
                row[key] = value
        row["vector_seq"] = sequence
        sequence += 1
        if ledger == "episodes" and job is not None:
            row = _paired_terminal_receipt(job, row)
        store.append(ledger, row, job=job, event=ledger == "events",
                     sync=ledger in {"resources", "episodes", "checkpoints", "events"})

    def terminal(job, binding, value):
        row = dict(value)
        if type(row.get("completed")) is not bool or not isinstance(row.get("status"), str):
            row = {"status": "missing_response", "completed": False,
                   "method_status": "missing_response", "method_completed": False}
        row.update({
            "execution_model": SHARED_EXECUTION_MODEL,
            "process_failure_scope": "shared_session",
            "shared_session_id": binding.session_id if binding is not None else "",
            "environment_slot": binding.environment_slot if binding is not None else -1,
            "slot_generation": binding.generation if binding is not None else 0,
            "slot_token": binding.token if binding is not None else "",
            "shutdown_status": "pending",
            **_unqualified_fields(),
        })
        if row["status"] in METHOD_TERMINALS:
            row.update(method_status=row["status"], method_completed=False, completed=False)
        if measured and (row["completed"] or row.get("physical_success") is not None
                         or row["status"] in {"safe_stop", "team_state_infeasible"}):
            try:
                verify_evaluator_evidence(store.root, job, row)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                row.update(status="not_evaluable", completed=False, physical_success=None,
                           evaluator_error=str(exc))
        emit("episodes", row, job=job, binding=binding)
        results[job.branch_id] = row

    grouped = {}
    for job in jobs:
        grouped.setdefault(job.scene_key, []).append(job)
    for scene_id, scene_jobs in grouped.items():
        scene_lease = session = manager = None
        shutdown_candidates = []
        leases = {}
        bound_jobs = {}
        # ``active`` is the only authoritative set of branches that were in
        # flight when a shared-session step failed.  It is initialized outside
        # the wave loop so failures during bind/open still have a defined
        # empty set; pending jobs must never be inferred from ``scene_jobs``.
        active = {}
        pending = list(scene_jobs)
        current_job = None
        opening = False
        session_id = f"vector-session-{uuid.uuid4()}"
        session_ids.append(session_id)
        try:
            scene_lease = scheduler.acquire_scene(scene_id, reservation=scene_reservation,
                                                 requested_device=executor.requested_device, wait=False)
            opening = True
            session = backend.open_scene(scene_id, num_envs=capacity, scheduler=scheduler)
            if session is None:
                raise IsolationError("vectorized scene open returned no handle")
            backend.warmup(session, ticks=executor.warmup_ticks)
            evidence = scheduler.preflight(ReplayRequest(
                scene_id=scene_id, branch_key=f"{scene_id}/preflight", reservation=branch_reservation,
                requested_device=executor.requested_device, warmup_ticks=executor.warmup_ticks), session)
            if not evidence.valid:
                raise IsolationError("vectorized device preflight failed")
            config = backend.isolation_config(session)
            if (not isinstance(config, Mapping) or not config.get("static_asset_digest")
                    or any(config.get(key) is not True for key in (
                        "collision_filtering", "sensor_filtering", "independent_reset", "inactive_slot_freeze"))):
                raise IsolationError("backend lacks vectorized native isolation configuration")
            emit("resources", {"kind": "vector_scene_opened", "shared_session_id": session_id,
                               "capacity": capacity, "config": dict(config), "schema": SCHEMA,
                               "process_failure_scope": "shared_session"})
            manager = VectorizedSession(backend, session, session_id=session_id, capacity=capacity,
                                        audit=lambda row: emit("resources", {"shared_session_id": session_id, **row}))
            while pending:
                active = {}
                # A wave is fixed until every slot terminates. Reset/close never
                # overlaps an in-flight batch. Slots reuse with a new generation.
                for slot in range(capacity):
                    if not pending:
                        break
                    job = pending[0]
                    current_job = job
                    try:
                        lease = scheduler.acquire_branch(scene_lease, branch_key=job.branch_id,
                                                         reservation=branch_reservation,
                                                         requested_device=executor.requested_device, wait=False)
                    except CapacityDenied:
                        if active:
                            break
                        raise
                    leases[job.branch_id] = lease
                    pending.pop(0)
                    binding, receipt = manager.bind(job, slot, checkpoint)
                    bound_jobs[binding] = job
                    # This must happen after the independent slot restore and
                    # readback performed by bind, and before the binding can be
                    # admitted to the first shared physics step.
                    equivalence = _shared_equivalence_receipt(
                        job, checkpoint, binding, receipt,
                    )
                    receipt = {**receipt, "shared_session_equivalence": equivalence}
                    shutdown_candidates.append((job, equivalence))
                    emit("checkpoints", receipt, job=job, binding=binding)
                    if (receipt.get("restore_passed") is not True
                            or receipt.get("readback_digest") != job.checkpoint_digest
                            or (measured and job.attempt > 0 and not verify_recovery_readback(job, receipt))):
                        raise IsolationError("vectorized restore/recovery receipt mismatch")
                    active[binding] = job
                peak = max(peak, len(active))
                started = time.monotonic()
                while active:
                    if executor.timeout_s is not None and time.monotonic() - started > executor.timeout_s:
                        for binding, job in active.items():
                            terminal(job, binding, {"status": "method_timeout", "completed": False})
                        active.clear()
                        break
                    for binding, job in active.items():
                        emit("metrics", {"kind": "heartbeat", "heartbeat_at_ns": time.time_ns()},
                             job=job, binding=binding)
                    outputs = manager.step(tuple(active))
                    for binding, row in outputs.items():
                        job = active[binding]
                        tick = row["simulator_step"]
                        emit("steps", {"simulator_step": tick, "action": row["action"],
                                       "applied_action": row["applied_action"],
                                       "commands": row.get("commands", {}),
                                       "post_step_state": row.get("post_step_state", {})}, job=job, binding=binding)
                        emit("observations", {"simulator_step": tick, "observations": row["observation"]},
                             job=job, binding=binding)
                        for name in ("network", "costs", "progress"):
                            if name in row:
                                emit(name, {"simulator_step": tick, name: row[name]}, job=job, binding=binding)
                        for event in row.get("events", ()):
                            emit("events", {**event, "simulator_step": tick}, job=job, binding=binding)
                        if row.get("result") is not None:
                            terminal(job, binding, row["result"])
                            del active[binding]
                        elif tick + 1 >= int(job.metadata.get("max_steps", 100000)):
                            terminal(job, binding, {"status": "method_timeout", "completed": False})
                            del active[binding]
                for binding in tuple(manager.bindings.values()):
                    job = bound_jobs[binding]
                    manager.close(binding)
                    scheduler.release_branch(leases.pop(job.branch_id), status=results[job.branch_id]["status"])
            receipts.append(manager.isolation_receipt())
        except BaseException as exc:
            # A native batch exception or isolation violation has session scope.
            # Keep completed method rows and retain ownership until scene close.
            scheduler.halt_admission(f"vectorized session failed: {exc}")
            # A shared-session failure invalidates only branches which were
            # actually admitted to the current wave.  ``scene_jobs`` also
            # contains queued jobs (including later paired blocks); assigning
            # them an unknown terminal record would falsely claim they were
            # in flight and would destroy the registered retry boundary.
            affected_jobs = list(active.values())
            if not affected_jobs:
                # Restore/equivalence failures can happen immediately after
                # bind, before the binding is added to ``active``.  Such a
                # bound job is still an admitted infrastructure failure, but
                # jobs remaining in ``pending`` are not.
                affected_jobs = [
                    job for binding, job in bound_jobs.items()
                    if getattr(binding, "environment_slot", None) in getattr(manager, "bindings", {})
                    and job.branch_id not in results
                ]
            if not affected_jobs and current_job is not None and manager is not None:
                # ``VectorizedSession.bind`` can leave a native slot allocated
                # before raising.  The returned binding is therefore absent
                # from ``bound_jobs``; recover only this concrete current job
                # from the manager's owned bindings, never from queued jobs.
                if any(item.branch_id == current_job.branch_id
                       for item in getattr(manager, "bindings", {}).values()):
                    affected_jobs = [current_job]
            paired_crash_groups = {
                str(job.metadata.get("paired_block_rerun", {}).get("block_id") or job.pair_group_id)
                for job in affected_jobs
                if isinstance(job.metadata.get("paired_block_rerun"), Mapping)
            }
            candidate_ids = {job.branch_id for job, *_ in shutdown_candidates}
            for job in affected_jobs:
                if job.branch_id in candidate_ids:
                    continue
                binding = next(
                    (key for key, value in bound_jobs.items() if value.branch_id == job.branch_id),
                    None,
                )
                if binding is None and manager is not None:
                    binding = next(
                        (item for item in getattr(manager, "bindings", {}).values()
                         if item.branch_id == job.branch_id),
                        None,
                    )
                # A partial bind may not have produced any trusted
                # equivalence receipt. Keep an explicit unknown shutdown
                # record with whatever immutable identity is available.
                shutdown_candidates.append((job, None, binding))
                candidate_ids.add(job.branch_id)
            paired_status = "pair-invalid" if isinstance(exc, SharedEquivalenceRejected) else "unknown-record"
            for block_id in sorted(paired_crash_groups):
                emit("metrics", {
                    "kind": "paired_block_disposition",
                    "block_id": block_id,
                    "disposition": paired_status,
                    "reason": "shared_session_equivalence_invalid" if paired_status == "pair-invalid" else "shared_session_crash",
                    "retry": False,
                })
            emit("resources", {"kind": "vector_session_error", "shared_session_id": session_id,
                               "error": f"{type(exc).__name__}: {exc}"})
            # Preserve the scheduler boundary explicitly: branches still in
            # ``pending`` never entered this shared session and therefore do
            # not receive an execution terminal.  A resource ledger marker is
            # the only durable evidence for their queued/not-run disposition.
            affected_ids = {job.branch_id for job in affected_jobs}
            for job in pending:
                if job.branch_id in results or job.branch_id in affected_ids:
                    continue
                emit("resources", {
                    "kind": "vector_branch_queued",
                    "shared_session_id": session_id,
                    "branch_id": job.branch_id,
                    "logical_branch_id": job.logical_branch_id,
                    "execution_phase": "queued",
                    "execution_status": "not-run",
                    "binding_present": False,
                    "reason": "shared_session_failure_before_admission",
                    "process_failure_scope": "shared_session",
                }, job=job)
            for job in affected_jobs:
                if job.branch_id not in results:
                    binding = next((key for key, value in bound_jobs.items() if value.branch_id == job.branch_id), None)
                    paired = isinstance(job.metadata.get("paired_block_rerun"), Mapping)
                    terminal(job, binding, {"status": paired_status if paired else "infrastructure_invalid", "completed": False,
                                           "physical_success": None, "infrastructure_reason": "vectorized_session_failure",
                                           "failure_class": "infrastructure", "error": str(exc),
                                           "execution_phase": "in_flight",
                                           "binding_present": binding is not None})
        finally:
            scene_closed = False
            scene_close_error = None
            cleanup_state = {"closed": False, "error": None}

            def close_scene_once():
                if cleanup_state["closed"]:
                    return "clean"
                if session is None:
                    raise RuntimeError("vectorized scene handle is missing")
                native_close_ok = False
                channel_close_ok = True
                try:
                    backend.close_scene(session)
                    native_close_ok = True
                finally:
                    # Channel invalidation is safe even when native close
                    # fails, and prevents stale Python handles from being
                    # reused while the scheduler lease remains quarantined.
                    if manager is not None:
                        try:
                            manager.shutdown_channels()
                        except BaseException:
                            channel_close_ok = False
                if not native_close_ok or not channel_close_ok:
                    raise RuntimeError("vectorized scene cleanup failed")
                for lease in leases.values():
                    scheduler.release_branch(lease, status="aborted", reason="whole scene closed")
                leases.clear()
                scheduler.release_scene(scene_lease)
                emit("resources", {"kind": "vector_scene_closed", "shared_session_id": session_id})
                cleanup_state["closed"] = True
                return "clean"

            if shutdown_candidates:
                try:
                    _finalize_shared_shutdown(
                        store=store,
                        backend=backend,
                        session=session,
                        session_id=session_id,
                        candidates=shutdown_candidates,
                        scene_closed=False,
                        shutdown_fn=close_scene_once,
                        cleanup_state=cleanup_state,
                        scene_close_error=scene_close_error,
                        timeout_s=executor.timeout_s or 20.0,
                    )
                except BaseException as exc:
                    cleanup_state["error"] = f"{type(exc).__name__}: {exc}"
            elif session is not None:
                try:
                    close_scene_once()
                except BaseException as exc:
                    cleanup_state["error"] = f"{type(exc).__name__}: {exc}"
            elif scene_lease is not None and not opening:
                scheduler.release_scene(scene_lease)

            scene_closed = bool(cleanup_state["closed"])
            scene_close_error = cleanup_state.get("error")
            if not scene_closed and scene_close_error:
                scheduler.halt_admission(f"vectorized scene cleanup failed: {scene_close_error}")
                emit("resources", {"kind": "vector_session_error", "shared_session_id": session_id,
                                   "error": f"scene cleanup failed: {scene_close_error}"})
    for audit in scheduler.audits:
        store.append("resources", {"scheduler_audit": dict(audit)})
    latest = {}
    for row in store.read_ledger("episodes"):
        logical = row.get("logical_branch_id")
        if logical and (logical not in latest or int(row.get("attempt", 0)) >= int(latest[logical].get("attempt", 0))):
            latest[logical] = row
    completed = sum(row.get("completed") is True for row in latest.values())
    snapshot = scheduler.snapshot()
    store.update_manifest(completed=completed, failed=len(latest) - completed,
                          active=snapshot.active_branches, resident=snapshot.active_branches,
                          active_scenes=snapshot.active_scenes,
                          queued=sum(job.branch_id not in results for job in jobs),
                          observed_peak_branch_workers=peak, shared_session_ids=session_ids,
                          vectorized_isolation_receipts=receipts,
                          last_execution={"scheduled": len(jobs), "completed": sum(
                              row.get("completed") is True for row in results.values()),
                              "failed": sum(row.get("completed") is not True for row in results.values())},
                          **_unqualified_fields())
    store.finish()
    from .validation import validate_run, summarize_run
    report = validate_run(store.root)
    # Isolation is derived from ordered receipts, never a backend success flag.
    store.update_manifest(
        slot_isolation_validated=report.get("vectorized_isolation_validated") is True,
        **_unqualified_fields(),
    )
    store.finish()
    report = validate_run(store.root)
    summarize_run(store.root)
    return {**store.manifest, **_unqualified_fields(),
            "logical_branches": executor.plan.logical_branch_count,
            "validation_status": report["status"]}
