"""Episode-level communication analysis archive for the scoring sidecar.

Formal goal episodes already archive per-tool replay-validated communication
reports (``pose_communication_replay.json`` /
``camera_communication_replay.json``).  This module folds those ledgers into
one episode-level ``communication_analysis.json`` the sidecar can observe, so
the P1-COMM formal episode hookup stops depending on hand-carried files.

Shared-transport mode (route decision 2026-09-24, batch-one ruling 1): the
episode runner holds one restricted ``CommunicationRuntime`` reused by the
pose, camera, and communication tools.  Callers then pass that runtime's
report as ``transport_report``; the episode totals, byte totals, and
direction breakdown are counted from that single ledger exactly once, while
the per-tool entries keep only their service-audit summaries.  Tool ledgers
that are duplicate views of the shared runtime are never summed into the
totals, so multiple reports cannot simply add up.  A transport report that
fails replay validation is refused outright instead of being replaced by a
guessed per-tool fallback sum.

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

from .communication import validate_communication_report
from .communication_capacity_experiments import bytes_by_direction
from .communication_analysis import SCHEMA, VERSION
from .development_track_marker import build_development_marker, stamp_evidence_record

COMMUNICATION_FORMAL_BLOCKER = "communication_formal_episode_pending"
_EVENT_COUNTERS = ("published", "delivered", "dropped", "expired", "consumed",
                   "bytes_served")
_TOTAL_EVENTS = ("published", "delivered", "dropped", "expired")
AGGREGATION_SHARED_TRANSPORT = "shared_transport_counted_once"
AGGREGATION_PER_TOOL_SUM = "per_tool_sum"


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


def _validated_transport_report(transport_report: Any) -> None:
    """Refuse a transport archive that cannot replay, before using it once."""
    if not isinstance(transport_report, Mapping):
        raise ValueError("transport report must be a mapping")
    replay = validate_communication_report(transport_report)
    if replay.get("validated") is not True:
        mismatches = "; ".join(replay.get("mismatches") or ["unknown"])
        raise ValueError(f"transport report failed replay validation: {mismatches}")


def build_communication_analysis(
    tool_reports: Mapping[str, Mapping[str, Any]], *, episode_id: str,
    scene_key: str = "city", evidence_class: str = "diagnostic",
    transport_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold per-tool communication reports into one sidecar-visible archive.

    ``tool_reports`` maps a tool name (for example ``pose_tools``) to its
    raw communication report.  A malformed report fails closed: the analysis
    records the quality failure for that tool instead of guessing or zeroing.

    ``transport_report`` is the single authoritative transport archive of the
    runner-owned shared ``CommunicationRuntime``.  When provided it must pass
    ``validate_communication_report``; the episode ``event_totals``, byte
    totals, and direction breakdown are then derived from that one ledger
    exactly once.  The per-tool entries survive only as service-audit
    summaries whose counts never re-enter the totals, because with a shared
    runtime they are duplicate views of the same transport events.  Without
    ``transport_report`` the historical per-tool sum is preserved for tools
    that own independent runtimes.
    """
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError("episode_id must be a nonempty string")
    if not isinstance(tool_reports, Mapping):
        raise ValueError("tool_reports must be a nonempty mapping")
    if not tool_reports and transport_report is None:
        raise ValueError("tool_reports must be a nonempty mapping")
    transport: dict[str, Any] | None = None
    if transport_report is not None:
        _validated_transport_report(transport_report)
        transport = {"status": "observed", **_tool_analysis(transport_report)}
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
    if transport is not None:
        # Shared runtime: one sealed transport ledger is the only authority
        # for the episode totals; tool summaries stay out of the sum.
        totals = {event: transport["event_counts"][event]
                  for event in _TOTAL_EVENTS}
        direction_complete = transport["direction_complete"] is True
        aggregation = AGGREGATION_SHARED_TRANSPORT
    else:
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
        aggregation = AGGREGATION_PER_TOOL_SUM
    report = {
        "schema": SCHEMA, "version": VERSION, "status": "ok",
        "episode_id": episode_id,
        "aggregation": aggregation,
        "tools": tools,
        **({"transport": transport} if transport is not None else {}),
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
    "AGGREGATION_PER_TOOL_SUM", "AGGREGATION_SHARED_TRANSPORT",
    "COMMUNICATION_FORMAL_BLOCKER", "build_communication_analysis",
]
