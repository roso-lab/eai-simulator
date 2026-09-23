"""Raw-only cost evidence extracted from a Goal episode archive.

This module deliberately joins only trusted host-side records that already
exist in an episode archive.  It does not estimate physics time, participant
compute, or communication traffic from action counts.  Missing or unverified
sources remain ``None`` so the sidecar cannot turn an evidence gap into a
zero-cost claim.
"""

from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "eai.benchmark.goal_raw_cost_evidence"
VERSION = 1


def _finite_number(value: Any, *, nonnegative: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return not nonnegative or value >= 0


def _read_json(root: Path, name: str) -> Any:
    path = root / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _session_report(root: Path, supplied: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if supplied is not None:
        wrapped = supplied.get("report") if isinstance(supplied, Mapping) else None
        return wrapped if isinstance(wrapped, Mapping) else supplied
    payload = _read_json(root, "goal_session_report.json")
    if not isinstance(payload, Mapping):
        return None
    report = payload.get("report")
    return report if isinstance(report, Mapping) else None


def _accepted_actions(report: Mapping[str, Any] | None) -> tuple[dict[str, int], int | None]:
    if not isinstance(report, Mapping):
        return {}, None
    rows = report.get("actions")
    if not isinstance(rows, list):
        return {}, None
    counts: Counter[str] = Counter()
    for row in rows:
        # GoalSession.actions is the trusted accepted-action ledger.  Keep a
        # defensive check so malformed archives do not become evidence.
        if isinstance(row, Mapping) and isinstance(row.get("type"), str) and row["type"]:
            counts[row["type"]] += 1
    return dict(sorted(counts.items())), sum(counts.values())


def _host_clock(report: Mapping[str, Any] | None) -> dict[str, Any]:
    source = "goal_host_receipt_clock"
    if not isinstance(report, Mapping):
        return {"source": source, "start_s": None, "end_s": None,
                "elapsed_s": None, "valid": False}
    end = report.get("current_time_s")
    if not _finite_number(end, nonnegative=True):
        return {"source": source, "start_s": None, "end_s": None,
                "elapsed_s": None, "valid": False}
    # GoalSession starts its trusted receipt clock at zero.  This is a ledger
    # clock bound, not a claim about wall-clock or physics elapsed time.
    return {"source": source, "start_s": 0.0, "end_s": float(end),
            "elapsed_s": float(end), "valid": True}


def _activation_proxy(root: Path) -> dict[str, Any]:
    usage = _read_json(root, "team_usage.json")
    if not isinstance(usage, Mapping):
        return {"value": None, "source": None, "is_proxy": True,
                "valid": False}
    value = usage.get("activation_cost_proxy")
    source = usage.get("activation_cost_source")
    valid = _finite_number(value, nonnegative=True) and isinstance(source, str) and bool(source)
    return {"value": float(value) if valid else None,
            "source": source if valid else None, "is_proxy": True,
            "valid": valid}


def _explicit_compute_cost(report: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read only explicitly host-recorded planning costs.

    No current GoalSession producer records these fields, so normal episodes
    intentionally report ``None``.  The names are reserved for a future
    trusted host scheduler source and are not populated from participant trace.
    """
    values: dict[str, float | None] = {}
    for key in ("planning_compute_cost_s", "replanning_compute_cost_s"):
        value = report.get(key) if isinstance(report, Mapping) else None
        values[key] = float(value) if _finite_number(value, nonnegative=True) else None
    total = (values["planning_compute_cost_s"] + values["replanning_compute_cost_s"]
             if values["planning_compute_cost_s"] is not None
             and values["replanning_compute_cost_s"] is not None else None)
    recorded = any(value is not None for value in values.values())
    return {"planning_s": values["planning_compute_cost_s"],
            "replanning_s": values["replanning_compute_cost_s"],
            "total_s": total,
            "source": "trusted_host_scheduler" if recorded else None,
            "valid": recorded}


def _communication_bytes(root: Path) -> dict[str, Any]:
    """Sum bytes only from communication reports with replay proof.

    Pose and camera tools each own an archive report.  The replay sidecar is
    the provenance gate; a raw report without a validated replay is not
    treated as reliable traffic evidence.
    """
    sources = (
        ("pose_tools_report.json", "pose_communication_replay.json"),
        ("camera_tools_report.json", "camera_communication_replay.json"),
        ("communication_report.json", "communication_replay.json"),
        ("communication.json", "communication_replay.json"),
    )
    total = 0
    by_pool: Counter[str] = Counter()
    by_direction: Counter[str] = Counter()
    reliable_sources = 0
    invalid_sources = 0
    for report_name, replay_name in sources:
        report_payload = _read_json(root, report_name)
        replay = _read_json(root, replay_name)
        communication = (report_payload.get("communication")
                         if isinstance(report_payload, Mapping) else None)
        if communication is None and isinstance(report_payload, Mapping):
            # A generic archive may already be the communication report.
            communication = report_payload if report_payload.get("schema") == "eai.benchmark.communication" else None
        if not isinstance(communication, Mapping):
            continue
        if not isinstance(replay, Mapping) or replay.get("validated") is not True:
            invalid_sources += 1
            continue
        ledger = communication.get("ledger")
        if not isinstance(ledger, list):
            invalid_sources += 1
            continue
        source_total = 0
        source_valid = True
        for event in ledger:
            if not isinstance(event, Mapping) or event.get("event") != "bytes_served":
                continue
            value = event.get("bytes")
            if not _finite_number(value, nonnegative=True):
                source_valid = False
                break
            source_total += int(value)
            pool = event.get("pool")
            direction = event.get("direction")
            if isinstance(pool, str) and pool:
                by_pool[pool] += int(value)
            if isinstance(direction, str) and direction:
                by_direction[direction] += int(value)
        if not source_valid:
            invalid_sources += 1
            continue
        total += source_total
        reliable_sources += 1
    reliable = reliable_sources > 0 and invalid_sources == 0
    return {
        "total_bytes": total if reliable else None,
        "by_pool_bytes": dict(sorted(by_pool.items())) if reliable else None,
        "by_direction_bytes": dict(sorted(by_direction.items())) if reliable else None,
        "reliable_source_count": reliable_sources,
        "invalid_source_count": invalid_sources,
        "source": "validated_communication_replay" if reliable else None,
        "valid": reliable,
    }


def build_raw_cost_evidence(
    output_root: str | Path,
    *,
    trusted_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable, raw-only declaration/coordination cost archive.

    The function is intentionally sidecar-only: callers may persist the
    returned mapping, but it never changes episode validity or scoreability.
    """
    root = Path(output_root).absolute()
    report = _session_report(root, trusted_report)
    action_counts, action_total = _accepted_actions(report)
    clock = _host_clock(report)
    communication = _communication_bytes(root)
    activation = _activation_proxy(root)
    compute = _explicit_compute_cost(report)
    validity = {
        "raw_only": True,
        "not_protocol_score": True,
        "clock_valid": clock["valid"],
        "accepted_action_ledger_valid": action_total is not None,
        "communication_ledger_reliable": communication["valid"],
        "activation_cost_proxy_valid": activation["valid"],
        "planning_compute_cost_recorded": compute["valid"],
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "raw_only": True,
        "not_protocol_score": True,
        "clock_source": clock["source"],
        "clock_valid": clock["valid"],
        "clock": clock,
        "accepted_actions": {
            "counts_by_type": action_counts,
            "total_count": action_total,
            "source": "goal_session.actions",
        },
        "communication": communication,
        "activation_cost_proxy": activation,
        "planning_compute_cost": compute,
        "validity": validity,
    }


def build_cost_evidence(
    output_root: str | Path,
    *,
    trusted_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable short alias for consumers assembling a scoring sidecar."""
    return build_raw_cost_evidence(output_root, trusted_report=trusted_report)


__all__ = ["SCHEMA", "VERSION", "build_raw_cost_evidence", "build_cost_evidence"]
