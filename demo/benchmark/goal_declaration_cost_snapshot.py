"""Generic pre-declaration cost snapshots for deployment assessments.

Stage60's R2 exit requires cost facts cut at the *actual declaration
boundary*: every ``deployment_assessment`` decision freezes a snapshot of
what the episode had metered before that declaration.  Ordering uses the
real call sequence — actions by their submission order, events by their
``sequence`` — and same-timestamp entries are only merged when their actual
order is knowable.  Communication-ledger rows that land exactly on the
boundary timestamp cannot be attributed post hoc across two separate
ledgers; they are segregated into ``at_boundary_not_attributed`` instead of
being guessed into either side.

Bytes keep the scheduling-billing semantics of the producing report (v4
``byte_accounting`` pass-through, ``total_physical_progress=false``);
physical-progress and planning-compute costs stay null unless a trusted
host ledger recorded them.  Archives without declarations get an explicit
"unmeasured at declaration granularity" status — never a silent zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

DECLARATION_COST_SCHEMA = "eai.benchmark.goal_declaration_cost_snapshot"
DECLARATION_COST_VERSION = 1
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _unwrap_session_report(report: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(report, Mapping):
        raise ValueError("session report must be a mapping")
    inner = report.get("report")
    if isinstance(inner, Mapping):
        return inner
    return report


def _boundary_event_sequence(events: Sequence[Mapping[str, Any]],
                             decision_action_id: str,
                             declared_at_s: float) -> int | None:
    """Sequence of the declaration's own action_result event, if present."""
    for event in events:
        if (event.get("type") == "action_result"
                and isinstance(event.get("payload"), Mapping)
                and event["payload"].get("action_id") == decision_action_id):
            return event.get("sequence")
    return None


def _events_before(events: Sequence[Mapping[str, Any]], *, declared_at_s: float,
                   boundary_sequence: int | None) -> list[Mapping[str, Any]]:
    result = []
    for event in events:
        time_s = event.get("time_s")
        if type(time_s) not in (int, float):
            continue
        if time_s < declared_at_s:
            result.append(event)
            continue
        if time_s == declared_at_s and boundary_sequence is not None:
            sequence = event.get("sequence")
            if type(sequence) is int and sequence < boundary_sequence:
                result.append(event)
    return result


def _action_counts(actions: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action.get("type", "unknown")] = counts.get(
            action.get("type", "unknown"), 0) + 1
    return counts


def _communication_cut(communication_report: Mapping[str, Any] | None,
                       *, declared_at_s: float) -> dict[str, Any]:
    """Split the transport ledger at the boundary with honest ambiguity."""
    if not isinstance(communication_report, Mapping):
        return {"available": False, "pre_boundary_bytes": None,
                "at_boundary_not_attributed_bytes": None,
                "bytes_by_pool": None, "byte_accounting": None,
                "reason": "communication_report_missing"}
    ledger = communication_report.get("ledger")
    if not isinstance(ledger, list):
        return {"available": False, "pre_boundary_bytes": None,
                "at_boundary_not_attributed_bytes": None,
                "bytes_by_pool": None, "byte_accounting": None,
                "reason": "communication_ledger_missing"}
    pre = 0
    ambiguous = 0
    by_pool: dict[str, int] = {}
    for row in ledger:
        if row.get("event") != "bytes_served":
            continue
        size = row.get("bytes") or 0
        time_s = row.get("time_s")
        if type(time_s) not in (int, float):
            continue
        if time_s < declared_at_s:
            pre += size
            pool = row.get("pool", "unattributed")
            by_pool[pool] = by_pool.get(pool, 0) + size
        elif time_s == declared_at_s:
            ambiguous += size
    return {
        "available": True,
        "pre_boundary_bytes": pre,
        "at_boundary_not_attributed_bytes": ambiguous,
        "bytes_by_pool": dict(sorted(by_pool.items())),
        "byte_accounting": deepcopy(communication_report.get("byte_accounting")),
        "total_physical_progress": False,
        "reason": None,
    }


def _host_recorded_planning_costs(planning: Mapping[str, Any] | None) -> dict[str, Any]:
    """Only explicitly host-recorded planning compute; otherwise null."""
    if not isinstance(planning, Mapping):
        return {"planning_s": None, "replanning_s": None, "recorded": False}
    values = {}
    for key in ("planning_s", "replanning_s"):
        value = planning.get(key)
        values[key] = (float(value)
                       if type(value) in (int, float) and value >= 0 else None)
    return {**values, "recorded": values["planning_s"] is not None
            or values["replanning_s"] is not None}


def build_declaration_cost_snapshots(
    session_report: Mapping[str, Any],
    *,
    episode_id: str | None = None,
    communication_report: Mapping[str, Any] | None = None,
    planning_costs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze one cost snapshot per deployment declaration.

    Snapshots are keyed by (episode, assessment id, epoch, decision action)
    and never back-filled by later outcomes.  Unmeasured quantities stay
    null with an explicit reason; missing declarations produce an explicit
    unmeasured status rather than zero costs.
    """
    report = _unwrap_session_report(session_report)
    resolved_episode = episode_id or report.get("episode_id")
    actions = report.get("actions") or []
    events = report.get("events") or []
    gate = report.get("deployment_assessment") or {}
    history = gate.get("history") or []
    if not isinstance(actions, list) or not isinstance(events, list):
        raise ValueError("session report actions/events must be lists")

    snapshots: list[dict[str, Any]] = []
    for row in history:
        if not isinstance(row, Mapping):
            continue
        decision_action_id = row.get("decision_action_id")
        declared_at = row.get("declared_at_s")
        action_index = next(
            (index for index, action in enumerate(actions)
             if action.get("action_id") == decision_action_id), None)
        if action_index is None or type(declared_at) not in (int, float):
            snapshots.append({
                "assessment_id": row.get("assessment_id"),
                "assessment_epoch": row.get("assessment_epoch"),
                "decision_action_id": decision_action_id,
                "status": "boundary_unresolvable",
                "quality_failures": ["declaration_action_not_in_report"],
                **_RELEASE,
            })
            continue
        boundary_sequence = _boundary_event_sequence(
            events, decision_action_id, float(declared_at))
        pre_events = _events_before(events, declared_at_s=float(declared_at),
                                    boundary_sequence=boundary_sequence)
        pre_actions = actions[:action_index]
        snapshots.append({
            "status": "snapshot",
            "assessment_id": row.get("assessment_id"),
            "assessment_epoch": row.get("assessment_epoch"),
            "decision_action_id": decision_action_id,
            "decision": row.get("decision"),
            "boundary": {
                "declared_at_s": float(declared_at),
                "action_index": action_index,
                "boundary_event_sequence": boundary_sequence,
                "ordering_basis": "actual_call_order",
            },
            "pre_declaration": {
                "action_count": len(pre_actions),
                "action_counts_by_type": _action_counts(pre_actions),
                "event_count": len(pre_events),
                "tick_count": sum(1 for event in pre_events
                                  if event.get("type") == "tick"),
                "communication": _communication_cut(
                    communication_report, declared_at_s=float(declared_at)),
                "planning_compute": _host_recorded_planning_costs(planning_costs),
                "physical_progress_cost": None,
                "physical_progress_reason": "not_measured_at_declaration_boundary",
            },
            "quality_failures": [],
            **_RELEASE,
        })

    if not snapshots:
        return {
            "schema": DECLARATION_COST_SCHEMA, "version": DECLARATION_COST_VERSION,
            "episode_id": resolved_episode,
            "status": "no_declarations",
            "snapshots": [],
            # Legacy archives without declarations cannot prove zero
            # pre-declaration cost; the quantity is unmeasured, not zero.
            "unmeasured_note": "declaration_granularity_unavailable_not_zero",
            **_RELEASE,
        }
    return {
        "schema": DECLARATION_COST_SCHEMA, "version": DECLARATION_COST_VERSION,
        "episode_id": resolved_episode,
        "status": "snapshotted",
        "snapshots": snapshots,
        "snapshot_count": len(snapshots),
        **_RELEASE,
    }


__all__ = [
    "DECLARATION_COST_SCHEMA", "DECLARATION_COST_VERSION",
    "build_declaration_cost_snapshots",
]
