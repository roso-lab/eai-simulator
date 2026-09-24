"""Episode-level communication analysis archive for the scoring sidecar.

Formal goal episodes already archive per-tool replay-validated communication
reports (``pose_communication_replay.json`` /
``camera_communication_replay.json``).  This module folds those ledgers into
one episode-level ``communication_analysis.json`` the sidecar can observe, so
the P1-COMM formal episode hookup stops depending on hand-carried files.

Discipline (STAGE22 / capacity experiments): totals survive without
direction metadata, but the direction breakdown fails closed to ``null``
instead of guessing from channel names; missing evidence is never zero cost;
release flags stay false and every report carries a development marker until
the formal episode provenance gate passes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .communication_capacity_experiments import bytes_by_direction
from .communication_analysis import SCHEMA, VERSION
from .development_track_marker import build_development_marker, stamp_evidence_record

COMMUNICATION_FORMAL_BLOCKER = "communication_formal_episode_pending"
_EVENT_COUNTERS = ("published", "delivered", "dropped", "expired", "consumed",
                   "bytes_served")


def _event_counts(ledger: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {event: 0 for event in _EVENT_COUNTERS}
    for row in ledger:
        event = row.get("event")
        if event in counts:
            counts[event] += 1
    return counts


def _tool_analysis(communication_report: Mapping[str, Any]) -> dict[str, Any]:
    ledger = communication_report.get("ledger")
    if not isinstance(ledger, list):
        raise ValueError("communication report has no ledger")
    attribution = bytes_by_direction(ledger)
    return {
        "event_counts": _event_counts(ledger),
        "total_bytes_served": attribution["total_bytes"],
        "bytes_by_pool": attribution["bytes_by_pool"],
        "bytes_by_direction": attribution["bytes_by_direction"],
        "direction_complete": attribution["direction_complete"],
        "has_aoi": isinstance(communication_report.get("aoi"), Mapping),
        "time_s": communication_report.get("time_s"),
    }


def build_communication_analysis(
    tool_reports: Mapping[str, Mapping[str, Any]], *, episode_id: str,
    scene_key: str = "city", evidence_class: str = "diagnostic",
) -> dict[str, Any]:
    """Fold per-tool communication reports into one sidecar-visible archive.

    ``tool_reports`` maps a tool name (for example ``pose_tools``) to its
    raw communication report.  A malformed report fails closed: the analysis
    records the quality failure for that tool instead of guessing or zeroing.
    """
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError("episode_id must be a nonempty string")
    if not isinstance(tool_reports, Mapping) or not tool_reports:
        raise ValueError("tool_reports must be a nonempty mapping")
    tools: dict[str, Any] = {}
    quality_failures: list[str] = []
    for name, report in tool_reports.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool names must be nonempty strings")
        if not isinstance(report, Mapping):
            tools[name] = {"status": "invalid", "quality_failure": "report_not_mapping"}
            quality_failures.append(f"{name}:report_not_mapping")
            continue
        try:
            analysis = _tool_analysis(report)
        except (ValueError, TypeError, KeyError) as exc:
            tools[name] = {"status": "invalid", "quality_failure": str(exc)}
            quality_failures.append(f"{name}:{exc}")
            continue
        tools[name] = {"status": "observed", **analysis}
    totals = {
        "published": sum(row.get("event_counts", {}).get("published", 0)
                         for row in tools.values() if isinstance(row, Mapping)),
        "delivered": sum(row.get("event_counts", {}).get("delivered", 0)
                         for row in tools.values() if isinstance(row, Mapping)),
        "dropped": sum(row.get("event_counts", {}).get("dropped", 0)
                       for row in tools.values() if isinstance(row, Mapping)),
        "expired": sum(row.get("event_counts", {}).get("expired", 0)
                       for row in tools.values() if isinstance(row, Mapping)),
    }
    direction_complete = all(
        row.get("direction_complete") is True
        for row in tools.values()
        if isinstance(row, Mapping) and row.get("status") == "observed")
    report = {
        "schema": SCHEMA, "version": VERSION, "status": "ok",
        "episode_id": episode_id,
        "tools": tools,
        "event_totals": totals,
        # Any direction gap keeps the whole episode breakdown fail-closed.
        "direction_breakdown": "complete" if direction_complete else "fail_closed_null",
        "quality_failures": quality_failures,
        "raw_only": True, "not_protocol_score": True,
        "execution_validated": False, "business_outcomes_validated": False,
        "scoreable": False,
    }
    return stamp_evidence_record(
        report, build_development_marker(
            scene_key, asset_status="development_unverified",
            evidence_class=evidence_class,
            infrastructure_blockers=[COMMUNICATION_FORMAL_BLOCKER]))


__all__ = [
    "COMMUNICATION_FORMAL_BLOCKER", "build_communication_analysis",
]
