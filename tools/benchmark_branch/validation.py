"""Fail-closed validation and raw-ledger aggregation."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

from .models import BRANCH_ROLES, IDENTITY_FIELDS, SCHEMA_VERSION
from .benchmark_bridge import benchmark_job_errors
from .store import LEDGERS


STATUS_RISK = {
    "right_censored", "not_evaluable", "invalid_instance", "feasibility_unverified",
    "pair_invalid", "sham_invalid", "method_timeout",
    "malformed_action", "crashed", "budget_overrun", "missing_response", "undefined_zero_progress",
    "infrastructure_invalid",
}


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"non-object JSONL row at {path}:{number}")
        rows.append(value)
    return rows


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _latest(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest = {}
    for row in rows:
        logical = str(row.get("logical_branch_id") or str(row.get("branch_id", "")).split("#attempt-", 1)[0])
        attempt = int(row.get("attempt", 0))
        old = latest.get(logical)
        if old is None or attempt >= int(old.get("attempt", 0)):
            latest[logical] = row
    return latest


def validate_run(run_dir: str | Path, *, write: bool = True) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = root / "manifest.json"
    plan_path = root / "plan.json"
    if not manifest_path.exists():
        result = {"schema": f"{SCHEMA_VERSION}.validation.v1", "status": "failed", "errors": ["missing manifest.json"], "warnings": []}
        if write: (root / "validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    except Exception as exc:
        errors.append(f"invalid manifest/plan: {exc}"); manifest, plan = {}, {}
    rows = {}
    for name in LEDGERS:
        path = root / f"{name}.jsonl"
        if not path.exists():
            errors.append(f"missing ledger: {name}.jsonl")
            rows[name] = []
        else:
            try: rows[name] = _read(path)
            except Exception as exc: errors.append(f"invalid ledger {name}: {exc}"); rows[name] = []
    checksum_path = root / "checksums.sha256"
    if not checksum_path.exists():
        errors.append("missing checksums.sha256")
    else:
        for line in checksum_path.read_text(encoding="ascii").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].endswith(".jsonl") and (root / parts[1]).exists() and _sha(root / parts[1]) != parts[0]:
                errors.append(f"checksum mismatch: {parts[1]}")
    branch_rows = rows["branch_manifest"]
    for row in branch_rows:
        missing = sorted(field for field in IDENTITY_FIELDS if field not in row)
        if missing:
            errors.append(f"branch identity missing fields {row.get('branch_id', '')}: {missing}")
    ids = [str(row.get("branch_id", "")) for row in branch_rows]
    duplicates = sorted({item for item in ids if item and ids.count(item) > 1})
    if duplicates: errors.append(f"duplicate branch_id: {duplicates[:5]}")
    latest_branches = _latest(branch_rows)
    if str(manifest.get("execution_scope")) == "measured":
        for row in latest_branches.values():
            try:
                from .models import BranchJob
                errors.extend(f"{row.get('branch_id')}: {item}" for item in
                              benchmark_job_errors(BranchJob.from_mapping(row)))
            except Exception as exc:
                errors.append(f"measured benchmark identity invalid {row.get('branch_id', '')}: {exc}")
    expected = int(manifest.get("logical_branches", plan.get("logical_branches", 0)) or 0)
    if expected and len(latest_branches) != expected:
        errors.append(f"logical branch count {len(latest_branches)} != expected {expected}")
    required = set(IDENTITY_FIELDS)
    branch_identity = {str(row.get("branch_id")): row for row in branch_rows}
    for name, ledger_rows in rows.items():
        if name == "branch_manifest": continue
        for row in ledger_rows:
            branch_id = str(row.get("branch_id", ""))
            if not branch_id: continue
            source = branch_identity.get(branch_id)
            if source is None:
                errors.append(f"{name}: unknown branch_id {branch_id}"); continue
            missing = sorted(field for field in required if field not in row)
            if missing:
                errors.append(f"{name}: identity missing fields {branch_id}: {missing}")
            for field in required:
                # Worker ids are unknown at manifest creation and are filled
                # by the isolated process; all other identity fields are
                # immutable across ledgers.
                if field in {"worker_instance_id", "worker_session_id"} and not source.get(field):
                    continue
                if field in source and field in row and row[field] != source[field]:
                    errors.append(f"identity drift {branch_id}: {field}")
    checkpoint_digests = {str(row.get("snapshot_digest") or row.get("checkpoint_digest")) for row in rows["checkpoints"] if row.get("snapshot_digest") or row.get("checkpoint_digest")}
    base_digests = {str(row.get("checkpoint_digest")) for row in branch_rows if row.get("checkpoint_digest")}
    if len(base_digests) > 1: errors.append("branch checkpoint digests disagree")
    if checkpoint_digests and base_digests and not checkpoint_digests.issubset(base_digests):
        errors.append("checkpoint digest mismatch")
    event_sequences: dict[str, list[int]] = defaultdict(list)
    for row in rows["events"]:
        if "event_seq" in row: event_sequences[str(row.get("branch_id"))].append(int(row["event_seq"]))
    for branch_id, sequence in event_sequences.items():
        if sequence != list(range(len(sequence))): errors.append(f"event_seq is not continuous: {branch_id}")
    started_at: dict[str, int] = {}
    ended_at: dict[str, int] = {}
    heartbeat_branches: set[str] = set()
    first_tick_branches: set[str] = set()
    last_tick_branches: set[str] = set()
    for row in rows["metrics"]:
        branch_id = str(row.get("branch_id", ""))
        if not branch_id: continue
        if row.get("kind") == "worker_started" and row.get("started_at_ns") is not None:
            started_at[branch_id] = int(row["started_at_ns"])
        if row.get("kind") == "heartbeat" or row.get("heartbeat_at_ns") is not None:
            heartbeat_branches.add(branch_id)
        if row.get("first_tick") is not None: first_tick_branches.add(branch_id)
        if row.get("last_tick") is not None: last_tick_branches.add(branch_id)
        if row.get("ended_at_ns") is not None:
            ended_at[branch_id] = max(ended_at.get(branch_id, 0), int(row["ended_at_ns"]))
    intervals = sorted((start, ended_at[branch_id]) for branch_id, start in started_at.items() if branch_id in ended_at)
    overlap_detected = False
    latest_end = -1
    for start, end in intervals:
        if start < latest_end:
            overlap_detected = True
            break
        latest_end = max(latest_end, end)
    restore_by_branch = {}
    for row in rows["checkpoints"]:
        if row.get("branch_id"): restore_by_branch[str(row["branch_id"])] = row
    restore_failures = sorted(branch_id for branch_id, row in restore_by_branch.items() if row.get("restore_passed") is False or row.get("restore_status") == "failed")
    episodes = _latest(rows["episodes"])
    # A branch is only independently auditable when its runtime rows carry a
    # non-empty worker instance and session identity.  The pre-launch
    # manifest intentionally has empty values, so use the latest episode (or
    # another runtime row) as the authoritative execution receipt.
    runtime_identity: dict[str, tuple[str, str]] = {}
    executed_logicals: set[str] = set()
    for name in ("episodes", "metrics", "steps", "events"):
        for row in rows[name]:
            logical = str(row.get("logical_branch_id") or str(row.get("branch_id", "")).split("#attempt-", 1)[0])
            # A scene-level device preflight can reject every branch before a
            # worker/session exists.  Preserve those raw infrastructure rows,
            # but do not require runtime worker identity for a branch that was
            # never admitted.
            if logical and row.get("branch_id") and not row.get("infrastructure_preflight"):
                executed_logicals.add(logical)
            worker = str(row.get("worker_instance_id") or "")
            session = str(row.get("worker_session_id") or "")
            if logical and worker and session:
                runtime_identity[logical] = (worker, session)
    # Queued branches have only a pre-launch manifest and therefore do not
    # need a worker identity yet.  Any branch that emitted runtime rows must,
    # however, have a stable worker/session receipt.
    missing_worker_identity = sorted(
        logical for logical in latest_branches
        if logical in executed_logicals and logical not in runtime_identity
    )
    latest_runtime_identities = [runtime_identity[logical] for logical in latest_branches if logical in runtime_identity]
    worker_instances = {item[0] for item in latest_runtime_identities}
    worker_sessions = {item[1] for item in latest_runtime_identities}
    if missing_worker_identity:
        errors.append(f"missing worker identity: {missing_worker_identity[:5]}")
    execution_backend = str(manifest.get("execution_backend") or "")
    shared_backend = str(manifest.get("execution_mode") or "") == "shared_session"
    session_backend = execution_backend == "session_backend"
    if not shared_backend and len(worker_sessions) != len(latest_runtime_identities):
        errors.append("duplicate worker_session_id across latest branches")
    if not shared_backend and len(worker_instances) != len(latest_runtime_identities):
        # A PID-derived worker instance can repeat across sequential retries;
        # report it as a warning when sessions remain unique, but fail when
        # it aliases concurrently auditable latest branches.
        warnings.append("worker_instance_id is reused across latest branches")
    shared_sessions = {
        str(row.get("shared_session_id"))
        for name in ("episodes", "metrics", "steps", "events")
        for row in rows[name] if row.get("branch_id") and row.get("shared_session_id")
    }
    if shared_backend or session_backend:
        if session_backend and manifest.get("scheduler_integrated") is not True:
            errors.append("session backend did not record scheduler integration")
        if len(shared_sessions) != 1:
            if shared_backend:
                errors.append("shared session identity is missing or split across branches")
        if shared_backend:
            slot_workers: dict[int, str] = {}
            for name in ("episodes", "steps", "events"):
                for row in rows[name]:
                    # ``slot`` is the experimental pair slot.  Shared-session
                    # adapters may reuse it across four arms; ownership is
                    # keyed by the physical environment slot instead.
                    slot_value = row.get("environment_slot", row.get("slot"))
                    if not row.get("branch_id") or slot_value is None:
                        continue
                    slot = int(slot_value)
                    worker = str(row.get("worker_instance_id") or "")
                    old = slot_workers.setdefault(slot, worker)
                    if old and worker and old != worker:
                        errors.append(f"slot {slot} changed worker ownership")
        if shared_backend and manifest.get("slot_isolation_validated") is not True:
            errors.append("shared-session slot isolation was not validated")
        # Shared-session collection has one parent writer, but every logical
        # branch must still retain an independently inspectable append-only
        # mirror.  Check the mirror against the latest branch identity so a
        # missing or accidentally empty slot ledger cannot pass validation.
        branch_root = root / "branches"
        for branch_id, manifest_row in branch_identity.items():
            safe = branch_id.replace("/", "_").replace("#", "_")
            local_path = branch_root / safe / "ledger.jsonl"
            if not local_path.exists():
                errors.append(f"missing shared-session branch ledger: {branch_id}")
                continue
            try:
                local_rows = _read(local_path)
            except Exception as exc:
                errors.append(f"invalid shared-session branch ledger {branch_id}: {exc}")
                continue
            if not local_rows:
                errors.append(f"empty shared-session branch ledger: {branch_id}")
                continue
            local_ids = {str(row.get("branch_id", "")) for row in local_rows}
            if local_ids != {branch_id}:
                errors.append(f"shared-session branch ledger identity mismatch: {branch_id}")
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in latest_branches.values(): pairs[str(row.get("pair_group_id"))].append(row)
    eligible: list[str] = []; excluded: dict[str, str] = {}
    for group, members in sorted(pairs.items()):
        roles = {str(row.get("branch_type")) for row in members}
        reason = ""
        if roles != set(BRANCH_ROLES): reason = "missing branch role"
        elif any(str(row.get("logical_branch_id")) not in episodes for row in members):
            reason = "missing episode/runtime receipt"
        else:
            pair_fields = (
                "scene_key", "task_id", "system_id", "condition_id", "base_instance_id",
                "pair_seed", "checkpoint_digest", "restore_checkpoint_digest", "task_version",
                "system_version", "source_revision", "task_contract_digest", "scene_build_digest",
                "asset_resolution_digest", "scene_build_order_digest", "skill_adapter_version",
                "communication_rng_namespace", "execution_scope",
            )
            fingerprints = {tuple(str(row.get(field, "")) for field in pair_fields) for row in members}
            if len(fingerprints) != 1: reason = "paired identity mismatch"
        if not reason and any(str(row.get("branch_id")) in restore_failures for row in members): reason = "restore mismatch"
        if not reason:
            sham = next((episodes.get(str(row.get("logical_branch_id"))) for row in members if row.get("branch_type") == "sham"), None)
            outcome = sham.get("outcome", {}) if isinstance(sham, Mapping) else {}
            if sham and (sham.get("status") == "sham_invalid" or (isinstance(outcome, Mapping) and outcome.get("sham_valid") is False)): reason = "sham_invalid"
        if reason: excluded[group] = reason
        else: eligible.append(group)
    latest_episode_values = list(episodes.values())
    if str(manifest.get("execution_scope")) == "measured":
        for row in latest_episode_values:
            if not bool(row.get("completed")):
                continue
            status = str(row.get("status", ""))
            if status in {"valid_terminal", "safe_stop", "team_state_infeasible"}:
                required = ("evaluator_version", "evidence_hash", "evidence_refs", "evidence_source")
                missing = [field for field in required if not row.get(field)]
                if missing or row.get("evidence_source") != "external_evaluator":
                    errors.append(f"measured episode lacks external evaluator evidence: {row.get('branch_id', '')}")
    completed = sum(bool(row.get("completed")) and str(row.get("status")) not in STATUS_RISK for row in latest_episode_values)
    failed = sum(str(row.get("status")) in STATUS_RISK or (row.get("completed") is False) for row in latest_episode_values)
    runtime_completed = sum(bool(row.get("completed")) for row in latest_episode_values)
    runtime_failed = sum(not bool(row.get("completed")) for row in latest_episode_values)
    outcome_status_counts: dict[str, int] = defaultdict(int)
    for row in latest_episode_values:
        outcome_status_counts[str(row.get("status", "missing"))] += 1
    collector_pairing_complete = bool(eligible) and not bool(excluded)
    # The collector can establish structural completeness and runtime
    # receipts, but it does not run causal replay fidelity or sham
    # calibration.  Never expose this diagnostic as a paper-valid paired
    # claim; benchmark.PairedBranchCoordinator remains the authority.
    result = {
        "schema": f"{SCHEMA_VERSION}.validation.v1", "run_id": manifest.get("run_id"),
        "status": "passed" if not errors else "failed", "errors": errors, "warnings": warnings,
        "metrics": {
            "pair_slots": manifest.get("pair_slots"), "logical_branches": len(latest_branches),
            "expected_logical_branches": expected, "requested": manifest.get("requested"),
            "resident": manifest.get("resident"), "active": manifest.get("active"),
            "completed": completed, "queued": manifest.get("queued"), "failed": failed,
            "requested_max_workers": manifest.get("requested_max_workers"),
            "admitted_max_workers": manifest.get("admitted_max_workers"),
            "observed_peak_branch_workers": manifest.get("observed_peak_branch_workers"),
            "execution_scope": manifest.get("execution_scope"),
            "branches_with_heartbeat": len(heartbeat_branches),
            "branches_with_first_and_last_tick": len(first_tick_branches & last_tick_branches),
            "worker_intervals": len(intervals), "overlap_detected": overlap_detected,
            "independent_branch_outputs": sum(1 for item in (root / "branches").iterdir() if (item / "ledger.jsonl").exists()) if (root / "branches").is_dir() else 0,
            "independent_worker_instances": len(worker_instances),
            "independent_worker_sessions": 1 if shared_backend and shared_sessions else len(worker_sessions),
            "shared_session": shared_backend,
            "session_backend": session_backend,
            "shared_session_ids": sorted(shared_sessions),
            "missing_worker_identity": missing_worker_identity,
            "runtime_completed": runtime_completed,
            "runtime_failed": runtime_failed,
            "outcome_status_counts": dict(sorted(outcome_status_counts.items())),
            "restore_failures": restore_failures,
            "pairing_eligible_groups": eligible, "pairing_excluded_groups": excluded,
            "collector_pairing_complete": collector_pairing_complete,
            "strict_pair_claim_status": "unsupported",
            "strict_pair_claim_reason": "benchmark_paired_gate_required",
            # Deprecated compatibility field.  It is deliberately false even
            # when all four collector roles have complete runtime receipts.
            "formal_pairing_eligible": False,
        },
        "validated_at_ns": time.time_ns(),
    }
    if write:
        (root / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def summarize_run(run_dir: str | Path, *, write: bool = True) -> dict[str, Any]:
    root = Path(run_dir).resolve()
    validation = validate_run(root, write=write)
    episodes = _latest(_read(root / "episodes.jsonl"))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in episodes.values(): grouped[str(row.get("pair_group_id"))].append(row)
    outcomes = []
    for group, members in sorted(grouped.items()):
        fact = next((row for row in members if row.get("branch_type") == "fact"), None)
        valid = [row for row in members if bool(row.get("completed")) and row.get("status") not in STATUS_RISK]
        outcome = fact.get("outcome", {}) if fact else {}
        complete = bool(members) and len(valid) == len(members)
        outcomes.append({"pair_group_id": group,
                         "status": "collector_complete" if complete else "collector_incomplete",
                         "inference_status": "unsupported",
                         "completed_branches": len(valid), "fact_status": fact.get("status") if fact else "missing",
                         "fact_outcome": outcome if isinstance(outcome, Mapping) else None})
    validation_metrics = validation.get("metrics", {})
    collector_status = (
        "complete"
        if validation.get("status") == "passed"
        and int(validation_metrics.get("runtime_failed", 0)) == 0
        and int(validation_metrics.get("runtime_completed", 0)) == int(validation_metrics.get("logical_branches", 0))
        else "incomplete"
    )
    summary = {"schema": f"{SCHEMA_VERSION}.summary.v1", "run_id": validation.get("run_id"),
               "validation_status": validation.get("status"), "metrics": validation.get("metrics", {}),
               "collector_status": collector_status, "inference_status": "unsupported",
               "inference_status_reason": "benchmark_metric_reducer_and_release_index_required",
               "outcomes": outcomes, "derived_from": {"raw_ledgers": [f"{name}.jsonl" for name in LEDGERS], "audit": "validation.json"}}
    if write: (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


class BranchValidator:
    def validate(self, run_dir: str | Path, *, write: bool = True) -> dict[str, Any]: return validate_run(run_dir, write=write)
    @staticmethod
    def validate_run(run_dir: str | Path, *, write: bool = True) -> dict[str, Any]: return validate_run(run_dir, write=write)


class BranchAggregator:
    def aggregate(self, run_dir: str | Path, *, write: bool = True) -> dict[str, Any]: return summarize_run(run_dir, write=write)


__all__ = ["STATUS_RISK", "validate_run", "summarize_run", "BranchValidator", "BranchAggregator"]
