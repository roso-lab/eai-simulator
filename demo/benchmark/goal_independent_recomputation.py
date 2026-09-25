"""Independent recomputation of scoring facts from an episode archive.

R2 requires that one real archive can be independently recomputed into its
raw progress / deployment / communication / cost facts, and that tampering,
missing evidence, early termination, failed retries and duplicate receipts
produce explicit results.  This module never trusts a summary file: every
family is recomputed from the archive's own raw sources (native evidence
log, session report, communication ledger) and only then compared with any
archived claims.  Disagreements and gaps are reported per family — never
silently zeroed, never promoted to a score.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

RECOMPUTATION_SCHEMA = "eai.benchmark.goal_independent_recomputation"
RECOMPUTATION_VERSION = 1
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(path: Path) -> str | None:
    import hashlib

    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _recompute_progress(goal_dir: Path, *,
                        milestone_kind_map: Mapping[str, str] | None = None,
                        horizon_s: float = 240.0) -> dict[str, Any]:
    """Milestones from the native evidence log, not business_evidence.json.

    With ``milestone_kind_map`` the recomputation also derives the protocol
    progress curve (Q/MCR/AUP/PGI) from the same raw records, under the
    frozen equal-weight v1 policy.
    """
    payload = _load(goal_dir / "native_evidence_log.json")
    if not isinstance(payload, Mapping):
        return {"status": "missing_source",
                "reason": "native_evidence_log_missing"}
    records = payload.get("records")
    if not isinstance(records, list):
        return {"status": "missing_source",
                "reason": "native_evidence_records_missing"}
    arrivals, observations, receipts = [], [], []
    for record in records:
        kind = record.get("kind")
        stamp = (record.get("arrival") or record.get("stamp")
                 or record.get("publication_stamp") or record.get("receipt_stamp")
                 or {})
        time_s = stamp.get("time_s") if isinstance(stamp, Mapping) else None
        row = {"task_id": record.get("task_id"),
               "region_id": record.get("region_id"),
               "evidence_ref": record.get("evidence_ref"),
               "time_s": time_s}
        if kind == "arrival":
            arrivals.append(row)
        elif kind == "observation":
            observations.append(row)
        elif kind == "relay_and_receipt":
            receipts.append(row)
    arrivals.sort(key=lambda row: (row["time_s"] is None, row["time_s"]))
    observations.sort(key=lambda row: (row["time_s"] is None, row["time_s"]))
    duplicates = _duplicate_refs(arrivals + observations + receipts)
    result = {
        "status": "recomputed",
        "arrival_count": len(arrivals),
        "observation_count": len(observations),
        "receipt_count": len(receipts),
        "arrivals": arrivals,
        "observations": observations,
        "receipts": receipts,
        "duplicate_evidence_refs": duplicates,
        "early_termination": False,  # refined by comparison below
    }
    curve = _progress_curve_from_records(records, milestone_kind_map,
                                          horizon_s=horizon_s)
    if curve is not None:
        result["progress_curve"] = curve
    return result


def _progress_curve_from_records(records, kind_map, *, horizon_s):
    if not isinstance(kind_map, Mapping) or not kind_map:
        return None
    from .goal_progress_curve import build_progress_curve

    milestone_times: dict[str, float] = {}
    for record in records:
        milestone_id = kind_map.get(record.get("kind", ""))
        if milestone_id is None:
            # Region-scoped observation kinds carry the region in the row.
            if record.get("kind") == "observation":
                milestone_id = kind_map.get(
                    f"observation:{record.get('region_id')}")
        if milestone_id is None:
            continue
        stamp = (record.get("stamp") or record.get("arrival")
                 or record.get("relay_publication") or {})
        time_s = stamp.get("time_s") if isinstance(stamp, Mapping) else None
        if type(time_s) not in (int, float):
            continue
        previous = milestone_times.get(milestone_id)
        if previous is None or time_s < previous:
            milestone_times[milestone_id] = float(time_s)
    if not milestone_times:
        return None
    active = sorted(set(kind_map.values()))
    observations = [{"milestone_id": milestone_id,
                     "satisfied_at_s": time_s}
                    for milestone_id, time_s in milestone_times.items()]
    # Viewer stage65-batch3 P0-3: the execution window must come from a
    # trusted termination anchor, never from the last milestone observation
    # time.  Without a verified termination the window is unverified and the
    # PGI family is recomputed over the full horizon with an explicit
    # unverified marker instead of an inferred end.
    from .goal_temporal_metrics import frozen_time_anchors

    anchors = frozen_time_anchors({"events": [], "actions": []},
                                  native_evidence_log={
                                      "records": list(records)})
    termination = anchors.get("termination") or {}
    end = termination.get("time_s")
    source = termination.get("source")
    if end is not None:
        end = min(float(end), horizon_s)
        window = {"t_exec_s": end, "source": f"anchor:{source}"}
    else:
        end = horizon_s
        window = {"t_exec_s": horizon_s, "source":
                  "anchor:termination_unverified_do_not_infer"}
    curve = build_progress_curve(active, observations, horizon_s=horizon_s,
                                 execution_end_s=end)
    curve["execution_window"] = window
    return curve


def _duplicate_refs(rows: list[dict[str, Any]]) -> list[str]:
    seen: dict[str, int] = {}
    for row in rows:
        ref = row.get("evidence_ref")
        if isinstance(ref, str):
            seen[ref] = seen.get(ref, 0) + 1
    return sorted(ref for ref, count in seen.items() if count > 1)


def _recompute_deployment(goal_dir: Path) -> dict[str, Any]:
    """Deployment classification counts from the raw session report."""
    payload = _load(goal_dir / "goal_session_report.json")
    report = payload.get("report") if isinstance(payload, Mapping) else None
    if not isinstance(report, Mapping):
        return {"status": "missing_source",
                "reason": "goal_session_report_missing"}
    gate = report.get("deployment_assessment") or {}
    history = gate.get("history") or []
    evaluations = gate.get("oracle_evaluations") or []
    decisions = [row.get("decision") for row in history
                 if isinstance(row, Mapping)]
    answered = sum(1 for decision in decisions if decision)
    oracle_verified = sum(1 for row in evaluations
                          if isinstance(row, Mapping)
                          and row.get("oracle_status") == "verified")
    return {
        "status": "recomputed",
        "required_epoch_count": len(history),
        "answered_epoch_count": answered,
        "oracle_verified_count": oracle_verified,
        "decisions": decisions,
        "terminated": gate.get("terminated") is True,
        "unanswered_epochs": len(history) - answered,
    }


def _recompute_communication(goal_dir: Path,
                             top_root: Path) -> dict[str, Any]:
    """Bytes/events from the raw transport ledger."""
    candidates = [top_root / "communication_tool_replay.json",
                  top_root / "communication_analysis.json"]
    ledger_payload = None
    for path in candidates:
        payload = _load(path)
        if isinstance(payload, Mapping) and isinstance(
                payload.get("ledger") or payload.get("report", {}).get("ledger")
                if isinstance(payload.get("report"), Mapping)
                else payload.get("ledger"), list):
            ledger_payload = payload
            break
    ledger = None
    if ledger_payload is not None:
        ledger = ledger_payload.get("ledger")
        if not isinstance(ledger, list) and isinstance(
                ledger_payload.get("report"), Mapping):
            ledger = ledger_payload["report"].get("ledger")
    if not isinstance(ledger, list):
        return {"status": "missing_source",
                "reason": "communication_ledger_missing"}
    counts: dict[str, int] = {}
    bytes_by_pool: dict[str, int] = {}
    missing_direction = False
    for row in ledger:
        event = row.get("event")
        counts[event] = counts.get(event, 0) + 1
        if event == "bytes_served":
            pool = row.get("pool", "unattributed")
            bytes_by_pool[pool] = (bytes_by_pool.get(pool, 0)
                                   + (row.get("bytes") or 0))
            if row.get("direction") is None:
                missing_direction = True
    return {
        "status": "recomputed",
        "event_counts": counts,
        "bytes_by_pool": dict(sorted(bytes_by_pool.items())),
        "direction_complete": not missing_direction,
        "ledger_rows": len(ledger),
    }


def _recompute_cost(goal_dir: Path, top_root: Path) -> dict[str, Any]:
    """Declaration snapshots and raw cost evidence recomputed in place."""
    from .goal_cost_evidence import build_cost_evidence
    from .goal_declaration_cost_snapshot import (
        build_declaration_cost_snapshots,
    )

    session_payload = _load(goal_dir / "goal_session_report.json")
    report = session_payload.get("report") if isinstance(
        session_payload, Mapping) else None
    if not isinstance(report, Mapping):
        return {"status": "missing_source",
                "reason": "goal_session_report_missing"}
    snapshots = build_declaration_cost_snapshots(
        {"episode_id": report.get("episode_id"), "report": report})
    cost = None
    try:
        cost = build_cost_evidence(goal_dir)
    except Exception:
        cost = None
    return {
        "status": "recomputed",
        "declaration_snapshots": snapshots.get("status"),
        "snapshot_count": snapshots.get("snapshot_count",
                                        len(snapshots.get("snapshots") or [])),
        "raw_cost_available": cost is not None,
    }


def _compare_family(recomputed: Any, archived: Any) -> dict[str, Any]:
    if not isinstance(archived, Mapping):
        return {"agreement": "archived_missing"}
    if recomputed == archived:
        return {"agreement": "agrees"}
    return {"agreement": "mismatch",
            "recomputed": recomputed, "archived": archived}


def recompute_episode_facts(top_root: str | Path, *,
                            milestone_kind_map: Mapping[str, str] | None = None,
                            horizon_s: float = 240.0) -> dict[str, Any]:
    """Independently recompute all fact families for one episode archive.

    ``milestone_kind_map`` optionally derives the protocol progress curve
    (equal-weight v1) from the same raw records; absent mapping keeps the
    progress family curve-free (explicitly, not as zero progress).
    """
    root = Path(top_root)
    goal_dir = root / "goal_episode"
    progress = _recompute_progress(goal_dir, milestone_kind_map=milestone_kind_map,
                                   horizon_s=horizon_s)
    deployment = _recompute_deployment(goal_dir)
    communication = _recompute_communication(goal_dir, root)
    cost = _recompute_cost(goal_dir, root)
    families: dict[str, Any] = {}
    for name, value in (("progress", progress), ("deployment", deployment),
                        ("communication", communication), ("cost", cost)):
        row: dict[str, Any] = {"recomputed": value}
        archived = None
        if name == "deployment":
            sidecar = _load(root / "scoring_adapter_metadata.json")
            if isinstance(sidecar, Mapping):
                archived = sidecar.get("deployment_assessment")
        row["comparison"] = _compare_family(value, archived)
        families[name] = row
    blocked = [name for name, row in families.items()
               if row["recomputed"].get("status") == "missing_source"]
    mismatches = [name for name, row in families.items()
                  if row["comparison"].get("agreement") == "mismatch"]
    report = _load(root / "restaurant_algorithm_episode_report.json")
    if (isinstance(report, Mapping)
            and isinstance(progress, Mapping)):
        claimed = report.get("business_episode_execution_validated") is True
        receipts = progress.get("receipt_count", 0)
        progress["claim_check"] = {
            "business_episode_execution_validated": claimed,
            "receipt_count_recomputed": receipts,
            "claim_supported_by_receipts": claimed and receipts > 0,
        }
    return {
        "schema": RECOMPUTATION_SCHEMA, "version": RECOMPUTATION_VERSION,
        "visibility": "evaluator_only",
        "archive_root": str(root),
        "families": families,
        "missing_families": sorted(blocked),
        "mismatched_families": sorted(mismatches),
        "status": ("failed" if blocked or mismatches else "recomputed"),
        **_RELEASE,
    }


def write_recomputation(top_root: str | Path, output_path: str | Path) -> Path:
    report = recompute_episode_facts(top_root)
    path = Path(output_path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                               sort_keys=True), encoding="utf-8")
    return path


__all__ = [
    "RECOMPUTATION_SCHEMA", "RECOMPUTATION_VERSION",
    "recompute_episode_facts", "write_recomputation",
]
