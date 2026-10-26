"""Evaluator-owned oracle predicates for the catalog anchors.

The predicate semantics, thresholds and event sources implemented here are the
binding decisions authored by the case author on 2026-10-23, recorded in
``benchmark/docs/progress/PREDICATE_BINDING_20261023.zh-CN.md`` (including the
V2 revision that adopts the paper review response MILESTONE_PREDICATE_REVIEW_
RESPONSE_20261023_zh.md in full).  This module is the executable form of that
record; if the record changes, this module and its tests must change with it.

V2 revisions applied here (record section "修订 V2"):

- prod-p1-01 parallel-overlap predicate is DIAGNOSTIC ONLY: P1 necessity is
  deadline-driven (a registered deadline shorter than any serial execution,
  certified by the deployment oracle), not overlap-based.
- liv-p3-01 sync window measures arrival-in-position events, not lift onset
  (one-sided load bearing within the window would be unsafe).
- release-before-re-entry follows the paper's half-open convention
  (Eq. p4-mutual-exclusion, tau_o + d_o <= tau_o'): touching intervals are a
  lawful back-to-back handover; only enter < exit is a violation.
- carrier support is checked every tick (no sampling), substitutes allowed.
- data-insufficient verdicts carry a cause code: ``unreached`` scores the
  obligation zero, ``evaluator_record_missing`` triggers a rerun/identification
  range, ``invariant_insufficient`` is an infrastructure failure.

Design boundaries (case-author decisions):

- The oracle consumes abstract event streams only (zone enter/exit intervals,
  hold intervals, milestone events, task events).  It never touches scene
  coordinates, so pending scene-entity identification does not block it; a
  zone tracker that needs the semantic-map coordinates feeds it later.
- The oracle is a pure offline judge over recorded streams: reproducible,
  recomputable, no experiment-time side effects.  Violations are recorded with
  tick evidence; penalty weighting belongs to the scoring layer (D3 obligation
  scores), not here.
- Judgment is fail-closed: a predicate whose required events are missing is
  reported ``data_insufficient`` with a cause code and never counts as
  satisfied.

Ticks are simulator ticks (``tick_dt_s = 0.02``); all intervals are half-open
``[start_tick, end_tick)`` so touching intervals (exit == enter) never overlap.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

TICK_DT_S = 0.02
PARALLEL_MIN_OVERLAP_TICKS = 1
RECONCILE_DEADLINE_TICKS = 500  # 10 s, prod-p1-01 only
SYNC_WINDOW_TICKS = 250  # 5 s, liv-p3-01 only
COVERAGE_MAX_GAP_TICKS = 50  # 1 s, prod-t-02 only
CARRIER_MIN_CONTACTS = 2  # liv-p3-01 pallet support

# Pilot safety predicates predate the expanded draft registry.  Keep this
# explicit map as the single compatibility authority for all receipt seams.
PILOT_SAFETY_PREDICATES = frozenset({
    ("liv-p3-01", "pallet_requires_two_carriers"),
})


class VerdictStatus(str, Enum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    DATA_INSUFFICIENT = "data_insufficient"


class CauseCode(str, Enum):
    """Why a verdict is data-insufficient (binding record V2 item 8).

    ``unreached``: the system never produced the required event within the
    horizon -- an unmet obligation scoring zero (time-to-event ends are
    right-censored).  ``evaluator_record_missing``: the evaluator's own record
    is incomplete while the task package is valid -- rerun from the same
    checkpoint and exogenous stream, then an identification range if still
    unknown.  ``invariant_insufficient``: postcondition attribution cannot be
    decided -- infrastructure failure with a new controller substream.
    """

    UNREACHED = "unreached"
    EVALUATOR_RECORD_MISSING = "evaluator_record_missing"
    INVARIANT_INSUFFICIENT = "invariant_insufficient"


@dataclass(frozen=True)
class ZoneInterval:
    """One occupancy ``[enter_tick, exit_tick)`` of ``zone_id`` by ``holder_id``.

    ``holder_id`` is the task-level occupant (lot, request, load, robot or
    package id) produced by the evaluator-internal zone tracker.  The tested
    system never sees this ledger.
    """

    zone_id: str
    holder_id: str
    enter_tick: int
    exit_tick: int


@dataclass(frozen=True)
class HoldInterval:
    """Evaluator-observed logical hold ``[start_tick, end_tick)``.

    V3: derived from held_object state or joint skill-session membership,
    never from physical contact. object_id is a task-level identity.
    """

    object_id: str
    robot_id: str
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class MilestoneEvent:
    label: str
    tick: int


@dataclass(frozen=True)
class TaskEvent:
    """Evaluator-observed task event, e.g. first-item pickup of a lot."""

    name: str
    subject: str
    tick: int


@dataclass
class PredicateStreams:
    zone_intervals: Sequence[ZoneInterval] = field(default_factory=list)
    hold_intervals: Sequence[HoldInterval] = field(default_factory=list)
    milestones: Sequence[MilestoneEvent] = field(default_factory=list)
    task_events: Sequence[TaskEvent] = field(default_factory=list)
    #: Evaluator-owned identity observations.  A value may be a two-tuple
    #: ``(actual, expected)``, or a mapping with ``actual``/``expected``
    #: keys.  ``expected`` may be a collection for set equality or membership
    #: checks.  This stream is deliberately separate from system messages.
    identity_records: Mapping[str, Any] = field(default_factory=dict)
    #: Evaluator integrity metadata (zone-tracker heartbeat / stream coverage
    #: records).  When True, the streams provably covered the horizon, so a
    #: missing event means the system never produced it (``unreached``).  When
    #: False/absent the oracle cannot tell "never happened" from "we did not
    #: see it" and reports ``evaluator_record_missing`` (rerun path).
    integrity_confirmed: bool = False

    def milestone_tick(self, label: str) -> int | None:
        ticks = [m.tick for m in self.milestones if m.label == label]
        return min(ticks) if ticks else None

    def task_tick(self, name: str, subject: str | None = None) -> int | None:
        ticks = [
            e.tick
            for e in self.task_events
            if e.name == name and (subject is None or e.subject == subject)
        ]
        return min(ticks) if ticks else None

    def zones(self, zone_id: str) -> list[ZoneInterval]:
        return [z for z in self.zone_intervals if z.zone_id == zone_id]

    def holds(self, object_id: str) -> list[HoldInterval]:
        return [c for c in self.hold_intervals if c.object_id == object_id]


@dataclass(frozen=True)
class Violation:
    tick: int
    detail: str


@dataclass(frozen=True)
class PredicateVerdict:
    anchor_id: str
    predicate_id: str
    status: VerdictStatus
    violations: tuple[Violation, ...] = ()
    cause: CauseCode | None = None
    evidence: dict = field(default_factory=dict)


def _verdict_priority(status: VerdictStatus) -> int:
    """Worst-first ordering for duplicate source consumers."""
    return {
        VerdictStatus.SATISFIED: 0,
        VerdictStatus.VIOLATED: 1,
        VerdictStatus.DATA_INSUFFICIENT: 2,
    }[status]


def dedupe_predicate_verdicts(
    verdicts: Sequence[PredicateVerdict],
) -> tuple[PredicateVerdict, ...]:
    """Consume each evaluator-owned source group once for scoring.

    Temporal and resource views can intentionally expose the same private
    occupancy ledger.  Contracts attach a stable ``dedupe_group`` to those
    views; this reducer retains one worst-case verdict per group and records
    all collapsed predicate IDs in evidence.  Predicates with no group remain
    independent.  A group is scoped by anchor, so identical resource names in
    different task instances never collapse across anchors.
    """

    retained: list[PredicateVerdict] = []
    positions: dict[str, int] = {}
    for verdict in verdicts:
        group = verdict.evidence.get("dedupe_group")
        if not group:
            retained.append(verdict)
            continue
        position = positions.get(group)
        if position is None:
            positions[group] = len(retained)
            evidence = dict(verdict.evidence)
            evidence["deduped_predicate_ids"] = [verdict.predicate_id]
            retained.append(replace(verdict, evidence=evidence))
            continue
        current = retained[position]
        ids = list(current.evidence.get("deduped_predicate_ids", [current.predicate_id]))
        if verdict.predicate_id not in ids:
            ids.append(verdict.predicate_id)
        chosen = current
        if _verdict_priority(verdict.status) > _verdict_priority(current.status):
            chosen = verdict
        evidence = dict(chosen.evidence)
        evidence["deduped_predicate_ids"] = ids
        evidence["dedupe_group"] = group
        retained[position] = replace(chosen, evidence=evidence)
    return tuple(retained)


# --------------------------------------------------------------------------
# Judgment primitives (pure functions, reused across predicates and anchors)
# --------------------------------------------------------------------------


def overlap_ticks(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def check_min_overlap(
    a: tuple[int, int], b: tuple[int, int], min_overlap: int
) -> tuple[bool, int]:
    got = overlap_ticks(a, b)
    return got >= min_overlap, got


def check_precedence(
    before_tick: int, after_tick: int, max_gap_ticks: int | None = None
) -> tuple[bool, str | None]:
    if after_tick <= before_tick:
        return False, f"after_tick={after_tick} not strictly after before_tick={before_tick}"
    if max_gap_ticks is not None and after_tick - before_tick > max_gap_ticks:
        return False, (
            f"gap {after_tick - before_tick} ticks exceeds deadline {max_gap_ticks}"
        )
    return True, None


def check_within_window(tick_a: int, tick_b: int, window_ticks: int) -> tuple[bool, int]:
    diff = abs(tick_a - tick_b)
    return diff <= window_ticks, diff


def check_coverage_no_gap(
    horizon: tuple[int, int],
    covered: Iterable[tuple[int, int]],
    max_gap_ticks: int,
) -> list[Violation]:
    """Gaps (per horizon) longer than ``max_gap_ticks`` between unioned intervals."""

    start, end = horizon
    merged: list[list[int]] = []
    for s, e in sorted(covered):
        if e <= start or s >= end:
            continue
        s, e = max(s, start), min(e, end)
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    violations: list[Violation] = []
    cursor = start
    for s, e in merged:
        if s - cursor > max_gap_ticks:
            violations.append(
                Violation(
                    cursor,
                    f"uncovered window of {s - cursor} ticks before covered start {s}",
                )
            )
        cursor = e
    if end - cursor > max_gap_ticks:
        violations.append(
            Violation(cursor, f"uncovered window of {end - cursor} ticks until horizon end")
        )
    return violations


def check_zone_mutex(zone: Sequence[ZoneInterval]) -> list[Violation]:
    """Different holders of one zone must never overlap, even by a single tick."""

    ordered = sorted(zone, key=lambda z: (z.enter_tick, z.exit_tick, z.holder_id))
    violations: list[Violation] = []
    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            if second.enter_tick >= first.exit_tick:
                break
            if second.holder_id == first.holder_id:
                continue
            got = overlap_ticks(
                (first.enter_tick, first.exit_tick), (second.enter_tick, second.exit_tick)
            )
            if got > 0:
                violations.append(
                    Violation(
                        max(first.enter_tick, second.enter_tick),
                        (
                            f"holders '{first.holder_id}' and '{second.holder_id}' "
                            f"overlap for {got} ticks"
                        ),
                    )
                )
    return violations


def check_release_before_enter(
    zone: Sequence[ZoneInterval], before_holder: str, after_holder: str
) -> tuple[bool, str | None]:
    """Paper half-open convention (Eq. p4-mutual-exclusion, tau_o + d_o <= tau_o').

    ``exit_tick`` is the first non-occupying tick, so touching intervals
    (enter == exit of the previous holder) are a lawful back-to-back handover;
    only an enter strictly earlier than the previous exit is a violation.
    """

    exits = [z.exit_tick for z in zone if z.holder_id == before_holder]
    enters = [z.enter_tick for z in zone if z.holder_id == after_holder]
    if not exits or not enters:
        return False, "missing release or re-entry interval"
    # A later re-entry by the releasing holder remains part of its registered
    # execution history.  Comparing against the first exit would incorrectly
    # allow B to enter between two A intervals, so use A's final exit and B's
    # first entry.
    exit_tick, enter_tick = max(exits), min(enters)
    if enter_tick < exit_tick:
        return False, f"enter_tick={enter_tick} precedes exit_tick={exit_tick}"
    return True, None


def check_contact_count(
    window: tuple[int, int],
    contacts: Sequence[HoldInterval],
    min_count: int,
) -> list[Violation]:
    """Every sampled tick must have at least ``min_count`` distinct holders.

    Substitution is allowed: any robot holding the object counts toward the minimum
    (case-author decision), so only the concurrent count matters.
    """

    start, end = window
    violations: list[Violation] = []
    run_start: int | None = None
    run_count = 0
    for tick in range(start, end):
        count = len({c.robot_id for c in contacts if c.start_tick <= tick < c.end_tick})
        if count < min_count:
            if run_start is None:
                run_start, run_count = tick, count
        elif run_start is not None:
            violations.append(
                Violation(
                    run_start,
                    f"only {run_count} joint holder(s) for {tick - run_start} ticks",
                )
            )
            run_start = None
    if run_start is not None:
        violations.append(
            Violation(
                run_start,
                f"only {run_count} joint holder(s) until window end",
            )
        )
    return violations


def check_deadline(final_tick: int | None, deadline_tick: int | None) -> tuple[bool, str | None]:
    """Check an inclusive terminal-milestone deadline.

    The endpoint is inclusive: a terminal event at exactly ``deadline_tick``
    succeeds, while an event one tick later violates.  Missing endpoints are
    not a successful result; callers turn them into a fail-closed verdict with
    the appropriate evaluator cause code.
    """

    if final_tick is None or deadline_tick is None:
        return False, "missing terminal milestone or registered deadline"
    if type(final_tick) is not int or type(deadline_tick) is not int:
        return False, "deadline and terminal tick must be integer ticks"
    if final_tick <= deadline_tick:
        return True, None
    return False, f"terminal tick {final_tick} exceeds deadline {deadline_tick}"


def check_identity_consistency(
    actual: Any,
    expected: Any,
    *,
    relation: str = "equal",
) -> tuple[bool, str | None]:
    """Compare evaluator-recorded identities without leaking hidden mappings.

    ``equal`` compares scalar identities, ``member`` checks that one identity
    belongs to a registered authorization collection, and ``set_equal``
    compares two batch/identity collections as mathematical sets.  ``None``
    denotes a missing observation and always fails closed.  Empty collections
    remain valid observations and therefore compare normally.
    """

    if actual is None or expected is None:
        return False, "missing identity observation"
    if relation == "equal":
        ok = actual == expected
    elif relation == "member":
        if isinstance(expected, (str, bytes, Mapping)):
            return False, "expected identity collection is not a collection"
        try:
            ok = actual in expected
        except TypeError:
            return False, "expected identity collection is not iterable"
    elif relation == "set_equal":
        if isinstance(actual, (str, bytes, Mapping)) or isinstance(expected, (str, bytes, Mapping)):
            return False, "identity collections are not set-compatible"
        try:
            ok = set(actual) == set(expected)
        except (TypeError, ValueError):
            return False, "identity collections are not set-compatible"
    else:
        raise ValueError(f"unsupported identity relation: {relation}")
    if ok:
        return True, None
    return False, f"identity mismatch under relation={relation}"


# --------------------------------------------------------------------------
# Predicate judges: one per bound predicate, faithful to the binding record
# --------------------------------------------------------------------------


def _verdict(
    anchor_id: str,
    predicate_id: str,
    status: VerdictStatus,
    violations: Sequence[Violation] = (),
    cause: CauseCode | None = None,
    **evidence,
) -> PredicateVerdict:
    return PredicateVerdict(
        anchor_id=anchor_id,
        predicate_id=predicate_id,
        status=status,
        violations=tuple(violations),
        cause=cause,
        evidence=dict(evidence),
    )


def _insufficient(
    anchor_id: str,
    predicate_id: str,
    streams: PredicateStreams,
    required: str,
) -> PredicateVerdict:
    """Fail-closed data-insufficient verdict with the V2 cause split."""

    cause = (
        CauseCode.UNREACHED
        if streams.integrity_confirmed
        else CauseCode.EVALUATOR_RECORD_MISSING
    )
    return _verdict(
        anchor_id, predicate_id, VerdictStatus.DATA_INSUFFICIENT,
        cause=cause, required=required,
    )


def _tick_invariant_error(
    streams: PredicateStreams,
    binding: Mapping[str, Any] | None = None,
) -> str | None:
    """Return the first malformed evaluator tick, or ``None``.

    Dataclass annotations do not enforce runtime types.  In particular,
    ``bool`` is an ``int`` subclass and ``int(1.5)`` silently truncates.  The
    evaluator therefore validates the complete received ledger before any
    ordering, arithmetic, ``range`` or evidence conversion.  Intervals are
    half-open and must have positive duration.
    """

    def tick_error(value: Any, path: str) -> str | None:
        if type(value) is not int:
            return f"{path} must be an integer tick (bool/float rejected)"
        if value < 0:
            return f"{path} must be a non-negative integer tick"
        return None

    for index, event in enumerate(streams.milestones):
        if not isinstance(event, MilestoneEvent):
            return f"milestones[{index}] must be MilestoneEvent"
        error = tick_error(event.tick, f"milestones[{index}].tick")
        if error:
            return error
    for index, event in enumerate(streams.task_events):
        if not isinstance(event, TaskEvent):
            return f"task_events[{index}] must be TaskEvent"
        error = tick_error(event.tick, f"task_events[{index}].tick")
        if error:
            return error
    for index, interval in enumerate(streams.zone_intervals):
        if not isinstance(interval, ZoneInterval):
            return f"zone_intervals[{index}] must be ZoneInterval"
        for field_name, value in (
            ("enter_tick", interval.enter_tick),
            ("exit_tick", interval.exit_tick),
        ):
            error = tick_error(value, f"zone_intervals[{index}].{field_name}")
            if error:
                return error
        if interval.exit_tick <= interval.enter_tick:
            return f"zone_intervals[{index}] must satisfy enter_tick < exit_tick"
    for index, interval in enumerate(streams.hold_intervals):
        if not isinstance(interval, HoldInterval):
            return f"hold_intervals[{index}] must be HoldInterval"
        for field_name, value in (
            ("start_tick", interval.start_tick),
            ("end_tick", interval.end_tick),
        ):
            error = tick_error(value, f"hold_intervals[{index}].{field_name}")
            if error:
                return error
        if interval.end_tick <= interval.start_tick:
            return f"hold_intervals[{index}] must satisfy start_tick < end_tick"

    tick_binding_fields = {
        "fixed_tick",
        "deadline_ticks",
        "max_gap_ticks",
        "window_ticks",
        "min_overlap_ticks",
    }

    def binding_error(value: Any, path: str) -> str | None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key in tick_binding_fields:
                    error = tick_error(child, child_path)
                else:
                    error = binding_error(child, child_path)
                if error:
                    return error
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                error = binding_error(child, f"{path}[{index}]")
                if error:
                    return error
        return None

    return binding_error(binding, "binding") if binding is not None else None


def _invariant_insufficient(
    anchor_id: str,
    predicate_id: str,
    reason: str,
    **evidence: Any,
) -> PredicateVerdict:
    """Fail closed on malformed evaluator/binding tick data."""

    return _verdict(
        anchor_id,
        predicate_id,
        VerdictStatus.DATA_INSUFFICIENT,
        cause=CauseCode.INVARIANT_INSUFFICIENT,
        required="well-formed integer tick ledger",
        invariant_error=reason,
        **evidence,
    )


def _lot_execution_interval(streams: PredicateStreams, lot: str) -> tuple[int, int] | None:
    first = streams.task_tick("lot_first_pickup", lot)
    last = streams.task_tick("lot_last_scan", lot)
    if first is None or last is None:
        return None
    return first, last


def _judge_prod_p1_01(predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    if predicate_id == "lot_a_and_lot_b_may_run_in_parallel":
        # V2: diagnostic only.  P1 necessity is deadline-driven (registered
        # deadline shorter than any serial execution, certified by the
        # deployment oracle); the overlap ratio is reported, not scored.
        a, b = _lot_execution_interval(s, "lot_a"), _lot_execution_interval(s, "lot_b")
        if a is None or b is None:
            return _insufficient(
                "prod-p1-01", predicate_id, s,
                "lot_first_pickup/lot_last_scan for lot_a and lot_b",
            )
        ok, got = check_min_overlap(a, b, PARALLEL_MIN_OVERLAP_TICKS)
        horizon = max(a[1], b[1]) - min(a[0], b[0])
        return _verdict(
            "prod-p1-01", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            interval_lot_a=a, interval_lot_b=b, overlap_ticks=got,
            overlap_ratio=round(got / horizon, 4) if horizon else 0.0,
            scoring_role="diagnostic",
        )
    if predicate_id == "reconcile_after_both_picks":
        before = s.milestone_tick("both_lots_picked")
        after = s.task_tick("count_reconcile_start")
        if before is None or after is None:
            return _insufficient(
                "prod-p1-01", predicate_id, s,
                "milestone both_lots_picked and task count_reconcile_start",
            )
        ok, reason = check_precedence(before, after, RECONCILE_DEADLINE_TICKS)
        return _verdict(
            "prod-p1-01", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            (Violation(after, reason),) if reason else (),
            both_lots_picked_tick=before,
            count_reconcile_start_tick=after,
            deadline_ticks=RECONCILE_DEADLINE_TICKS,
            deadline_rationale="count results void after 10 s (V2 item 4)",
        )
    if predicate_id == "one_scan_station_per_lot":
        zone = s.zones("scan_station")
        if not zone:
            return _insufficient(
                "prod-p1-01", predicate_id, s, "zone intervals for scan_station",
            )
        v = check_zone_mutex(zone)
        if not v and not s.integrity_confirmed:
            return _insufficient(
                "prod-p1-01", predicate_id, s,
                "complete zone interval ledger for scan_station",
            )
        return _verdict(
            "prod-p1-01", predicate_id,
            VerdictStatus.VIOLATED if v else VerdictStatus.SATISFIED,
            v, zone_id="scan_station", interval_count=len(zone),
        )
    raise KeyError(predicate_id)


def _judge_svc_p2_02(predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    pairs = {
        "mark_after_inspection": ("inspection_complete", "quarantine_marked", None),
        "accept_after_release": ("released", "accepted", None),
    }
    if predicate_id in pairs:
        before_label, after_label, deadline = pairs[predicate_id]
        before, after = s.milestone_tick(before_label), s.milestone_tick(after_label)
        if before is None or after is None:
            return _insufficient(
                "svc-p2-02", predicate_id, s,
                f"milestones {before_label} and {after_label}",
            )
        ok, reason = check_precedence(before, after, deadline)
        return _verdict(
            "svc-p2-02", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            (Violation(after, reason),) if reason else (),
            before_milestone=before_label, after_milestone=after_label,
        )
    if predicate_id == "one_quarantine_bay":
        zone = s.zones("quarantine_bay")
        if not zone:
            return _insufficient(
                "svc-p2-02", predicate_id, s, "zone intervals for quarantine_bay",
            )
        v = check_zone_mutex(zone)
        if not v and not s.integrity_confirmed:
            return _insufficient(
                "svc-p2-02", predicate_id, s,
                "complete zone interval ledger for quarantine_bay",
            )
        return _verdict(
            "svc-p2-02", predicate_id,
            VerdictStatus.VIOLATED if v else VerdictStatus.SATISFIED,
            v, zone_id="quarantine_bay", interval_count=len(zone),
        )
    raise KeyError(predicate_id)


def _judge_liv_p3_01(predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    if predicate_id == "carriers_start_within_sync_window":
        # V2: the window measures arrival-in-position (grasp/support posture
        # reached), not load-bearing lift onset.
        left = s.task_tick("arrive_in_position", "left_carrier")
        right = s.task_tick("arrive_in_position", "right_carrier")
        if left is None or right is None:
            return _insufficient(
                "liv-p3-01", predicate_id, s,
                "arrive_in_position for left_carrier and right_carrier",
            )
        ok, diff = check_within_window(left, right, SYNC_WINDOW_TICKS)
        return _verdict(
            "liv-p3-01", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            (Violation(max(left, right), f"arrival differs by {diff} ticks"),) if not ok else (),
            left_carrier_arrival=left, right_carrier_arrival=right,
            window_ticks=SYNC_WINDOW_TICKS,
            event="arrive_in_position",
        )
    if predicate_id in ("release_after_joint_carry", "accept_after_release"):
        before_label = (
            "joint_carry_complete"
            if predicate_id == "release_after_joint_carry"
            else "released"
        )
        after_label = "released" if predicate_id == "release_after_joint_carry" else "accepted"
        before, after = s.milestone_tick(before_label), s.milestone_tick(after_label)
        if before is None or after is None:
            return _insufficient(
                "liv-p3-01", predicate_id, s,
                f"milestones {before_label} and {after_label}",
            )
        ok, reason = check_precedence(before, after)
        return _verdict(
            "liv-p3-01", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            (Violation(after, reason),) if reason else (),
            before_milestone=before_label, after_milestone=after_label,
        )
    if predicate_id == "pallet_requires_two_carriers":
        left = s.task_tick("arrive_in_position", "left_carrier")
        right = s.task_tick("arrive_in_position", "right_carrier")
        end = s.milestone_tick("joint_carry_complete")
        if left is None or right is None or end is None:
            return _insufficient(
                "liv-p3-01", predicate_id, s,
                "arrive_in_position events and milestone joint_carry_complete",
            )
        # Carry window starts when both carriers are in position; logical
        # holders are checked on every tick of the window (V3).
        window = (max(left, right), end)
        if window[1] <= window[0]:
            return _verdict(
                "liv-p3-01",
                predicate_id,
                VerdictStatus.VIOLATED,
                (Violation(end, "carry window must have positive duration"),),
                carry_window=window,
                object_id="pallet",
                safety_class=True,
                scoring_role="safety_success",
            )
        holds = s.holds("pallet")
        violations = check_contact_count(window, holds, CARRIER_MIN_CONTACTS)
        if violations and not s.integrity_confirmed:
            return _insufficient(
                "liv-p3-01", predicate_id, s,
                "complete logical hold interval ledger for pallet",
            )
        return _verdict(
            "liv-p3-01", predicate_id,
            VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
            violations,
            carry_window=window, min_contacts=CARRIER_MIN_CONTACTS,
            contact_count=len(holds), object_id="pallet", safety_class=True,
            scoring_role="safety_success",
        )
    raise KeyError(predicate_id)


def _judge_prod_t_02(predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    if predicate_id in ("scan_point_use_is_mutex", "scan_point_capacity_one"):
        zone = s.zones("scan_point")
        if not zone:
            return _insufficient(
                "prod-t-02", predicate_id, s, "zone intervals for scan_point",
            )
        v = check_zone_mutex(zone)
        if not v and not s.integrity_confirmed:
            return _insufficient(
                "prod-t-02", predicate_id, s,
                "complete zone interval ledger for scan_point",
            )
        return _verdict(
            "prod-t-02", predicate_id,
            VerdictStatus.VIOLATED if v else VerdictStatus.SATISFIED,
            v, zone_id="scan_point", interval_count=len(zone),
            note="temporal and resource views share one occupancy source",
        )
    if predicate_id == "coverage_has_no_uncovered_window":
        start = s.milestone_tick("coverage_started")
        end = s.milestone_tick("accepted")
        if start is None or end is None:
            return _insufficient(
                "prod-t-02", predicate_id, s,
                "milestones coverage_started and accepted",
            )
        covered = [(z.enter_tick, z.exit_tick) for z in s.zones("aisle")]
        violations = check_coverage_no_gap((start, end), covered, COVERAGE_MAX_GAP_TICKS)
        if violations and not s.integrity_confirmed:
            return _insufficient(
                "prod-t-02", predicate_id, s,
                "complete coverage interval ledger for aisle",
            )
        return _verdict(
            "prod-t-02", predicate_id,
            VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
            violations,
            horizon=(start, end), max_gap_ticks=COVERAGE_MAX_GAP_TICKS,
            covered_interval_count=len(covered),
        )
    raise KeyError(predicate_id)


def _judge_svc_t_01(predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    if predicate_id in ("requests_cannot_overlap_counter", "counter_capacity_one"):
        zone = s.zones("service_counter")
        if not zone:
            return _insufficient(
                "svc-t-01", predicate_id, s, "zone intervals for service_counter",
            )
        v = check_zone_mutex(zone)
        if not v and not s.integrity_confirmed:
            return _insufficient(
                "svc-t-01", predicate_id, s,
                "complete zone interval ledger for service_counter",
            )
        return _verdict(
            "svc-t-01", predicate_id,
            VerdictStatus.VIOLATED if v else VerdictStatus.SATISFIED,
            v, zone_id="service_counter", interval_count=len(zone),
            note="temporal and resource views share one occupancy source",
        )
    if predicate_id == "release_between_requests":
        zone = s.zones("service_counter")
        ok, reason = (
            check_release_before_enter(zone, "request_a", "request_b")
            if zone
            else (False, "missing service_counter intervals")
        )
        if reason == "missing release or re-entry interval" or not zone:
            return _insufficient(
                "svc-t-01", predicate_id, s,
                "exit of request_a and enter of request_b at service_counter",
            )
        if ok and not s.integrity_confirmed:
            return _insufficient(
                "svc-t-01", predicate_id, s,
                "complete release/entry ledger for service_counter",
            )
        return _verdict(
            "svc-t-01", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            (Violation(0, reason),) if reason else (),
            released_holder="request_a", entering_holder="request_b",
        )
    raise KeyError(predicate_id)


def _judge_liv_p4_01(predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    if predicate_id in ("load_a_and_load_b_cannot_overlap_dock", "dock_capacity_one"):
        zone = s.zones("dock")
        if not zone:
            return _insufficient(
                "liv-p4-01", predicate_id, s, "zone intervals for dock",
            )
        v = check_zone_mutex(zone)
        if not v and not s.integrity_confirmed:
            return _insufficient(
                "liv-p4-01", predicate_id, s,
                "complete zone interval ledger for dock",
            )
        return _verdict(
            "liv-p4-01", predicate_id,
            VerdictStatus.VIOLATED if v else VerdictStatus.SATISFIED,
            v, zone_id="dock", interval_count=len(zone),
            note="temporal and resource views share one occupancy source",
        )
    if predicate_id == "release_before_load_b":
        zone = s.zones("dock")
        ok, reason = (
            check_release_before_enter(zone, "loader_a", "loader_b")
            if zone
            else (False, "missing dock intervals")
        )
        if reason == "missing release or re-entry interval" or not zone:
            return _insufficient(
                "liv-p4-01", predicate_id, s,
                "exit of loader_a and enter of loader_b at dock",
            )
        if ok and not s.integrity_confirmed:
            return _insufficient(
                "liv-p4-01", predicate_id, s,
                "complete release/entry ledger for dock",
            )
        return _verdict(
            "liv-p4-01", predicate_id,
            VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
            (Violation(0, reason),) if reason else (),
            released_holder="loader_a", entering_holder="loader_b",
        )
    raise KeyError(predicate_id)


# --------------------------------------------------------------------------
# Full 24-anchor draft projection
# --------------------------------------------------------------------------

# The six pilot bindings above remain byte-for-byte semantic authorities for
# their original anchors.  The following table is the mechanical projection
# of the 24 author-approved draft files.  It intentionally does not invent
# scene coordinates or identity mappings: those are supplied later by the
# semantic-map/evaluator receipt.
_DRAFT_PREDICATES: dict[str, tuple[str, ...]] = {
    "liv-t-04": ("upper_shelf_delivery_after_transition_handoff", "heavy_carry_joint_hold", "relay_and_carry_within_horizon"),
    "liv-p2-01": ("move_after_assembly", "receive_after_release", "dock_slot_capacity_one", "accept_after_receipt", "delivery_handoff_within_horizon"),
    "liv-p2-02": ("mark_after_inspection", "accept_after_release", "quarantine_bay_capacity_one", "medication_custody_identity_matches", "medication_handoff_complete_within_horizon"),
    "liv-p2-03": ("stage_before_release", "release_before_accept", "one_dispatch_lane", "accept_before_deadline", "dispatch_identity_matches_order"),
    "liv-p3-02": ("coverage_has_no_uncovered_window", "scan_point_use_is_mutex", "scan_complete_after_scan_point_reservation", "coverage_and_supply_complete_within_horizon"),
    "liv-p4-02": ("scan_a_and_scan_b_cannot_overlap", "scan_station_capacity_one", "release_between_scans", "reserve_before_scan_a", "both_scans_complete_within_horizon"),
    "liv-t-01": ("visits_may_run_in_parallel", "reconcile_after_both_visits", "one_access_token_per_room", "room_assistance_complete_within_horizon"),
    "liv-t-02": ("segments_may_run_in_parallel", "handoff_after_both_clear", "accept_after_release", "one_safety_lead", "route_handoff_within_horizon"),
    "liv-t-03": ("deliveries_may_run_in_parallel", "room_entry_is_mutex", "reserve_before_room_entry", "release_after_room_use", "supplies_and_privacy_complete_within_horizon"),
    "prod-p1-02": ("shelf_operations_may_run_in_parallel", "verification_after_replenishment", "one_stock_cart_per_shelf", "both_shelves_complete_within_horizon"),
    "prod-p2-01": ("move_after_assembly", "receive_after_release", "dock_slot_capacity_one", "accept_after_receipt", "handoff_complete_within_horizon"),
    "prod-p2-02": ("mark_after_inspection", "accept_after_release", "one_quarantine_bay", "custody_closed_within_horizon"),
    "prod-p2-03": ("release_before_accept", "one_dispatch_lane", "accept_before_deadline", "staged_before_release"),
    "prod-p4-01": ("load_a_and_load_b_cannot_overlap_dock", "dock_capacity_one", "release_before_load_b", "both_loads_complete_within_horizon"),
    "prod-p4-02": ("scan_a_and_scan_b_cannot_overlap", "scan_station_capacity_one", "release_between_scans", "both_scans_complete_within_horizon"),
    "prod-t-01": ("carriers_start_within_sync_window", "pallet_requires_two_carriers", "release_after_joint_carry", "accept_after_release"),
    "prod-t-03": ("parallel_moves_before_handoff", "safe_zone_clear_before_entry", "staging_zone_capacity_one", "handoff_before_accept", "gate_use_is_mutex"),
    "svc-p1-01": ("lot_a_and_lot_b_may_run_in_parallel", "reconcile_after_both_picks", "one_scan_station_per_lot", "patrol_and_reconcile_within_horizon"),
    "svc-p1-02": ("shelf_operations_may_run_in_parallel", "verification_after_replenishment", "one_stock_cart_per_shelf", "reset_and_audit_within_horizon"),
    "svc-p2-01": ("move_after_assembly", "receive_after_release", "accept_after_receipt", "handoff_complete_within_horizon"),
    "svc-p2-03": ("release_before_accept", "one_dispatch_lane", "accept_before_deadline", "staged_before_release"),
    "svc-p3-01": ("carriers_start_within_sync_window", "pallet_requires_two_carriers", "release_after_joint_carry", "accept_after_release"),
    "svc-p3-02": ("coverage_has_no_uncovered_window", "scan_point_use_is_mutex", "scan_complete_after_scan_point_reservation", "coverage_and_replenishment_complete_within_horizon"),
    "svc-t-02": ("access_mutex", "accept_after_release", "release_access_after_case_accept", "case_handoff_within_horizon"),
    "svc-t-03": ("coverage_has_no_uncovered_window", "inspections_may_run_in_parallel", "inspection_records_after_point_arrival", "coverage_and_inspections_within_horizon"),
}

_DRAFT_HORIZON_TICKS = {
    "liv-t-04": 15000,
    "liv-p2-01": 7500, "liv-p2-02": 8500, "liv-p2-03": 9000,
    "liv-p3-02": 11500, "liv-p4-02": 10500, "liv-t-01": 6000,
    "liv-t-02": 9000, "liv-t-03": 12000, "prod-p1-02": 7000,
    "prod-p2-01": 7500, "prod-p2-02": 8500, "prod-p2-03": 9000,
    "prod-p4-01": 9500, "prod-p4-02": 10500, "prod-t-01": 11000,
    "prod-t-03": 12000, "svc-p1-01": 6000, "svc-p1-02": 7000,
    "svc-p2-01": 7500, "svc-p2-03": 9000, "svc-p3-01": 11000,
    "svc-p3-02": 11500, "svc-t-02": 9000, "svc-t-03": 12000,
}


def _event(name: str, subject: str | None = None, *, source: str = "any") -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "source": source}
    if subject is not None:
        result["subject"] = subject
    return result


def _precedence(
    before: Sequence[dict[str, Any]],
    after: dict[str, Any],
    *,
    allow_equal: bool = False,
    max_gap_ticks: int | None = None,
    required_zone_id: str | None = None,
    required_holder: str | None = None,
    require_zone_entry_after_before: bool = False,
    scoring_role: str = "obligation",
    diagnostic_interval_pair: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "operator": "precedence", "before_events": list(before),
        "after_event": dict(after), "allow_equal": allow_equal,
        "scoring_role": scoring_role,
        "source_ledger": "evaluator_event_ledger",
        "required_fields": ["before_events", "after_event"],
    }
    if max_gap_ticks is not None:
        spec["max_gap_ticks"] = max_gap_ticks
    if required_zone_id is not None:
        spec["required_zone_id"] = required_zone_id
        spec["required_fields"].extend(["zone_id", "holder_id", "enter_tick", "exit_tick"])
    if required_holder is not None:
        spec["required_holder"] = required_holder
    if require_zone_entry_after_before:
        spec["require_zone_entry_after_before"] = True
    if diagnostic_interval_pair is not None:
        spec["diagnostic_interval_pair"] = [dict(item) for item in diagnostic_interval_pair]
        spec["diagnostic_min_overlap_ticks"] = PARALLEL_MIN_OVERLAP_TICKS
    return spec


def _release_enter(zone_id: str, before_holder: str, after_holder: str, *, dedupe_group: str) -> dict[str, Any]:
    return {
        "operator": "precedence", "mode": "release_enter", "zone_id": zone_id,
        "before_holder": before_holder, "after_holder": after_holder,
        "allow_equal": True, "scoring_role": "obligation",
        "source_ledger": "evaluator_private_zone_intervals",
        "required_fields": ["zone_id", "holder_id", "enter_tick", "exit_tick"],
        "dedupe_group": dedupe_group,
    }


def _release_enter_with_events(
    zone_id: str,
    before_holder: str,
    after_holder: str,
    before_event: dict[str, Any],
    after_event: dict[str, Any],
) -> dict[str, Any]:
    spec = _release_enter(
        zone_id, before_holder, after_holder,
        dedupe_group=f"svc-t-02:occupancy:{zone_id}",
    )
    spec.update({
        "before_event": before_event,
        "after_event": after_event,
        "event_boundary_alignment": "exact",
        "required_fields": [
            *spec["required_fields"], "before_event", "after_event",
        ],
    })
    return spec


def _mutex(
    *zone_ids: str,
    unified: bool = False,
    safety_class: bool = False,
    dedupe_group: str | None = None,
) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "operator": "non_overlapping", "zone_ids": list(zone_ids), "capacity": 1,
        "interval_convention": "half_open", "zone_scope": "unified" if unified else "independent",
        "scoring_role": "safety_success" if safety_class else "obligation",
        "safety_class": safety_class, "source_ledger": "evaluator_private_zone_intervals",
        "required_fields": ["zone_id", "holder_id", "enter_tick", "exit_tick"],
    }
    if dedupe_group is not None:
        spec["dedupe_group"] = dedupe_group
    return spec


def _diagnostic(zone_a: str, zone_b: str) -> dict[str, Any]:
    return {
        "operator": "may_overlap",
        "interval_pair": [{"zone_id": zone_a}, {"zone_id": zone_b}],
        "min_overlap_ticks": PARALLEL_MIN_OVERLAP_TICKS,
        "scoring_role": "diagnostic", "source_ledger": "evaluator_private_zone_intervals",
        "required_fields": ["zone_id", "holder_id", "enter_tick", "exit_tick"],
    }


def _deadline(anchor_id: str, terminal: str) -> dict[str, Any]:
    return {
        "operator": "deadline", "terminal_milestone": terminal,
        "deadline_ticks": _DRAFT_HORIZON_TICKS[anchor_id],
        "inclusive": True, "scoring_role": "success_condition",
        "source_ledger": "milestone_ledger",
        "required_fields": ["terminal_milestone", "terminal_tick", "deadline_ticks"],
    }


def _coverage(zone_id: str, start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    return {
        "operator": "continuous_coverage", "zone_id": zone_id,
        "window": {"start": start, "end": end},
        "max_gap_ticks": COVERAGE_MAX_GAP_TICKS,
        "include_head_tail_gaps": True, "scoring_role": "obligation",
        "source_ledger": "evaluator_private_zone_intervals",
        "required_fields": ["window_start", "window_end", "zone_id", "enter_tick", "exit_tick"],
    }


def _within_window() -> dict[str, Any]:
    return {
        "operator": "within_window",
        "event_pair": {
            "left": _event("arrive_in_position", "left_carrier", source="task"),
            "right": _event("arrive_in_position", "right_carrier", source="task"),
        },
        "window_ticks": SYNC_WINDOW_TICKS, "scoring_role": "obligation",
        "source_ledger": "evaluator_event_ledger",
        "required_fields": ["left_event_tick", "right_event_tick"],
    }


def _contact(object_id: str) -> dict[str, Any]:
    return {
        "operator": "contact_count",
        "window": {
            "start": _event("joint_carry_start"),
            "end": _event("joint_carry_complete", source="milestone"),
        },
        "object_id": object_id, "minimum_contacts": CARRIER_MIN_CONTACTS,
        "distinct_by": "robot_id", "allow_substitution": True,
        "scoring_role": "safety_success", "safety_class": True,
        "source_ledger": "evaluator_private_hold_intervals",
        "observation_quantity": "distinct_joint_holders",
        "observation_source": "held_object_or_joint_skill_session",
        "required_fields": ["window_start", "window_end", "object_id", "robot_id", "hold_interval"],
    }


def _identity(identity_key: str, checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "operator": "identity_consistency", "identity_key": identity_key,
        "checks": [dict(item) for item in checks], "scoring_role": "obligation",
        "source_ledger": "evaluator_private_identity_ledger",
        "required_fields": ["accepted_identity_fields", "assigned_identity_fields"],
    }


# Author-approved executable registry.  Pair keys are intentional: repeated
# predicate IDs have different events, objects and zones across anchors.
DRAFT_BINDING_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {
    ("liv-t-04", "upper_shelf_delivery_after_transition_handoff"): {
        "operator": "precedence",
        "event_pairs": [
            {"before": _event("relay_started", source="milestone"),
             "after": _event("transition_handoff_observed")},
            {"before": _event("transition_handoff_observed"),
             "after": _event("relay_complete", source="milestone")},
        ],
        "allow_equal": False, "scoring_role": "obligation",
        "source_ledger": "evaluator_event_ledger",
        "handoff_observation_location": "topology_transition_node",
        "required_fields": ["pickup_tick", "transition_handoff_tick", "upper_shelf_delivery_tick"],
    },
    ("liv-t-04", "heavy_carry_joint_hold"): {
        **_contact("heavy_object"),
        "window": {"start": _event("bilateral_support_established"),
                   "end": _event("heavy_carry_complete", source="milestone")},
    },
    ("liv-t-04", "relay_and_carry_within_horizon"): _deadline("liv-t-04", "accepted"),
    ("liv-p2-01", "move_after_assembly"): _precedence([_event("assemble_pallet")], _event("ready_for_handoff", source="milestone")),
    ("liv-p2-01", "receive_after_release"): _precedence([_event("released", source="milestone")], _event("received", source="milestone")),
    ("liv-p2-01", "dock_slot_capacity_one"): _mutex("home_entry_handoff_dock"),
    ("liv-p2-01", "accept_after_receipt"): _precedence([_event("received", source="milestone")], _event("accepted", source="milestone")),
    ("liv-p2-01", "delivery_handoff_within_horizon"): _deadline("liv-p2-01", "accepted"),

    ("liv-p2-02", "mark_after_inspection"): _precedence([_event("inspection_complete", source="milestone")], _event("quarantine_marked", source="milestone")),
    ("liv-p2-02", "accept_after_release"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("liv-p2-02", "quarantine_bay_capacity_one"): _mutex("quarantine_bay_1"),
    ("liv-p2-02", "medication_custody_identity_matches"): _identity("medication_custody", [
        {"actual_path": "accepted.package_id", "expected_path": "assigned.package_id", "relation": "equal"},
        {"actual_path": "accepted.receiver_id", "expected_path": "assigned.authorized_receivers", "relation": "member"},
    ]),
    ("liv-p2-02", "medication_handoff_complete_within_horizon"): _deadline("liv-p2-02", "closed"),

    ("liv-p2-03", "stage_before_release"): _precedence([_event("staged", source="milestone")], _event("released", source="milestone")),
    ("liv-p2-03", "release_before_accept"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("liv-p2-03", "one_dispatch_lane"): _mutex("event_dispatch_lane"),
    ("liv-p2-03", "accept_before_deadline"): _deadline("liv-p2-03", "accepted"),
    ("liv-p2-03", "dispatch_identity_matches_order"): _identity("dispatch_order", [
        {"actual_path": "accepted.batch_ids", "expected_path": "assigned.batch_ids", "relation": "set_equal"},
        {"actual_path": "accepted.dispatcher_id", "expected_path": "assigned.dispatcher_id", "relation": "equal"},
        {"actual_path": "accepted.stager_id", "expected_path": "assigned.stager_id", "relation": "equal"},
    ]),

    ("liv-p3-02", "coverage_has_no_uncovered_window"): _coverage("accessible_route_coverage_zone", {"fixed_tick": 250, "name": "service_window_open"}, {"fixed_tick": 11250, "name": "service_window_close"}),
    ("liv-p3-02", "scan_point_use_is_mutex"): _mutex("scan_point_capacity_one"),
    ("liv-p3-02", "scan_complete_after_scan_point_reservation"): _precedence([_event("reserve_scan_point")], _event("scan_complete", source="milestone"), required_zone_id="scan_point_capacity_one"),
    ("liv-p3-02", "coverage_and_supply_complete_within_horizon"): _deadline("liv-p3-02", "accepted"),

    ("liv-p4-02", "scan_a_and_scan_b_cannot_overlap"): _mutex("shared_room_scan_station", dedupe_group="liv-p4-02:occupancy:shared_room_scan_station"),
    ("liv-p4-02", "scan_station_capacity_one"): _mutex("shared_room_scan_station", dedupe_group="liv-p4-02:occupancy:shared_room_scan_station"),
    ("liv-p4-02", "release_between_scans"): _release_enter("shared_room_scan_station", "scan_a", "scan_b", dedupe_group="liv-p4-02:occupancy:shared_room_scan_station"),
    ("liv-p4-02", "reserve_before_scan_a"): _precedence([_event("reserved", source="milestone")], _event("scan_a_complete", source="milestone"), required_zone_id="shared_room_scan_station", required_holder="scan_a", require_zone_entry_after_before=True),
    ("liv-p4-02", "both_scans_complete_within_horizon"): _deadline("liv-p4-02", "accepted"),

    ("liv-t-01", "visits_may_run_in_parallel"): _diagnostic("resident_room_a_access", "resident_room_b_access"),
    ("liv-t-01", "reconcile_after_both_visits"): _precedence([_event("visit_complete", "room_a", source="task"), _event("visit_complete", "room_b", source="task")], _event("acknowledgements_reconciled", source="milestone")),
    ("liv-t-01", "one_access_token_per_room"): _mutex("resident_room_a_access", "resident_room_b_access"),
    ("liv-t-01", "room_assistance_complete_within_horizon"): _deadline("liv-t-01", "accepted"),

    ("liv-t-02", "segments_may_run_in_parallel"): _diagnostic("evacuation_segment_a", "evacuation_segment_b"),
    ("liv-t-02", "handoff_after_both_clear"): _precedence([_event("clear_segment_complete", "segment_a", source="task"), _event("clear_segment_complete", "segment_b", source="task")], _event("handoff_route")),
    ("liv-t-02", "accept_after_release"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("liv-t-02", "one_safety_lead"): _mutex("safety_lead_handoff_point"),
    ("liv-t-02", "route_handoff_within_horizon"): _deadline("liv-t-02", "closed"),

    ("liv-t-03", "deliveries_may_run_in_parallel"): _diagnostic("restaurant_supply_batch_a", "restaurant_supply_batch_b"),
    ("liv-t-03", "room_entry_is_mutex"): _mutex("privacy_sensitive_room", "privacy_room_gate", unified=True, safety_class=True),
    ("liv-t-03", "reserve_before_room_entry"): _precedence([_event("room_reserved", source="milestone")], _event("first_room_entry"), required_zone_id="privacy_sensitive_room"),
    ("liv-t-03", "release_after_room_use"): _precedence([_event("room_use_complete"), _event("privacy_guard_check_complete"), _event("physical_room_exit")], _event("room_released", source="milestone")),
    ("liv-t-03", "supplies_and_privacy_complete_within_horizon"): _deadline("liv-t-03", "accepted"),

    ("prod-p1-02", "shelf_operations_may_run_in_parallel"): _diagnostic("shelf_a", "shelf_b"),
    ("prod-p1-02", "verification_after_replenishment"): _precedence([_event("both_shelves_replenished", source="milestone")], _event("labels_verified", source="milestone")),
    ("prod-p1-02", "one_stock_cart_per_shelf"): _mutex("stock_cart_a", "stock_cart_b"),
    ("prod-p1-02", "both_shelves_complete_within_horizon"): _deadline("prod-p1-02", "accepted"),

    ("prod-p2-01", "move_after_assembly"): _precedence([_event("assemble_pallet")], _event("move_to_dock_first_pickup")),
    ("prod-p2-01", "receive_after_release"): _precedence([_event("released", source="milestone")], _event("received", source="milestone")),
    ("prod-p2-01", "dock_slot_capacity_one"): _mutex("handoff_dock_slot"),
    ("prod-p2-01", "accept_after_receipt"): _precedence([_event("received", source="milestone")], _event("accepted", source="milestone")),
    ("prod-p2-01", "handoff_complete_within_horizon"): _deadline("prod-p2-01", "accepted"),

    ("prod-p2-02", "mark_after_inspection"): _precedence([_event("inspection_complete", source="milestone")], _event("quarantine_marked", source="milestone")),
    ("prod-p2-02", "accept_after_release"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("prod-p2-02", "one_quarantine_bay"): _mutex("quarantine_bay"),
    ("prod-p2-02", "custody_closed_within_horizon"): _deadline("prod-p2-02", "closed"),

    ("prod-p2-03", "release_before_accept"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("prod-p2-03", "one_dispatch_lane"): _mutex("dispatch_lane"),
    ("prod-p2-03", "accept_before_deadline"): _deadline("prod-p2-03", "accepted"),
    ("prod-p2-03", "staged_before_release"): _precedence([_event("staged", source="milestone")], _event("released", source="milestone")),

    ("prod-p4-01", "load_a_and_load_b_cannot_overlap_dock"): _mutex("dock_slot_1", dedupe_group="prod-p4-01:occupancy:dock_slot_1"),
    ("prod-p4-01", "dock_capacity_one"): _mutex("dock_slot_1", dedupe_group="prod-p4-01:occupancy:dock_slot_1"),
    ("prod-p4-01", "release_before_load_b"): _release_enter("dock_slot_1", "loader_a", "loader_b", dedupe_group="prod-p4-01:occupancy:dock_slot_1"),
    ("prod-p4-01", "both_loads_complete_within_horizon"): _deadline("prod-p4-01", "accepted"),

    ("prod-p4-02", "scan_a_and_scan_b_cannot_overlap"): _mutex("shared_scan_station", dedupe_group="prod-p4-02:occupancy:shared_scan_station"),
    ("prod-p4-02", "scan_station_capacity_one"): _mutex("shared_scan_station", dedupe_group="prod-p4-02:occupancy:shared_scan_station"),
    ("prod-p4-02", "release_between_scans"): _release_enter("shared_scan_station", "scan_a", "scan_b", dedupe_group="prod-p4-02:occupancy:shared_scan_station"),
    ("prod-p4-02", "both_scans_complete_within_horizon"): _deadline("prod-p4-02", "accepted"),

    ("prod-t-01", "carriers_start_within_sync_window"): _within_window(),
    ("prod-t-01", "pallet_requires_two_carriers"): _contact("long_pallet"),
    ("prod-t-01", "release_after_joint_carry"): _precedence([_event("joint_carry_complete", source="milestone")], _event("released", source="milestone")),
    ("prod-t-01", "accept_after_release"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),

    ("prod-t-03", "parallel_moves_before_handoff"): _precedence([_event("move_lot_a_complete"), _event("move_lot_b_complete")], _event("handoff"), diagnostic_interval_pair=[{"zone_id": "move_lot_a"}, {"zone_id": "move_lot_b"}]),
    ("prod-t-03", "safe_zone_clear_before_entry"): _precedence([_event("safe_state_validated")], _event("lot_enter_safe_zone"), max_gap_ticks=25),
    ("prod-t-03", "staging_zone_capacity_one"): _mutex("hazard_safe_staging", safety_class=True),
    ("prod-t-03", "handoff_before_accept"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("prod-t-03", "gate_use_is_mutex"): _mutex("hazard_gate"),

    ("svc-p1-01", "lot_a_and_lot_b_may_run_in_parallel"): _diagnostic("facility_zone_a", "facility_zone_b"),
    ("svc-p1-01", "reconcile_after_both_picks"): _precedence([_event("both_lots_picked", source="milestone")], _event("count_reconciled", source="milestone")),
    ("svc-p1-01", "one_scan_station_per_lot"): _mutex("scan_station"),
    ("svc-p1-01", "patrol_and_reconcile_within_horizon"): _deadline("svc-p1-01", "accepted"),

    ("svc-p1-02", "shelf_operations_may_run_in_parallel"): _diagnostic("dining_area_a", "dining_area_b"),
    ("svc-p1-02", "verification_after_replenishment"): _precedence([_event("both_shelves_replenished", source="milestone")], _event("labels_verified", source="milestone")),
    ("svc-p1-02", "one_stock_cart_per_shelf"): _mutex("stock_cart"),
    ("svc-p1-02", "reset_and_audit_within_horizon"): _deadline("svc-p1-02", "accepted"),

    ("svc-p2-01", "move_after_assembly"): _precedence([_event("ready_for_handoff", source="milestone")], _event("released", source="milestone")),
    ("svc-p2-01", "receive_after_release"): _precedence([_event("released", source="milestone")], _event("received", source="milestone")),
    ("svc-p2-01", "accept_after_receipt"): _precedence([_event("received", source="milestone")], _event("accepted", source="milestone")),
    ("svc-p2-01", "handoff_complete_within_horizon"): _deadline("svc-p2-01", "accepted"),

    ("svc-p2-03", "release_before_accept"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),
    ("svc-p2-03", "one_dispatch_lane"): _mutex("service_dispatch_lane"),
    ("svc-p2-03", "accept_before_deadline"): _deadline("svc-p2-03", "accepted"),
    ("svc-p2-03", "staged_before_release"): _precedence([_event("staged", source="milestone")], _event("released", source="milestone")),

    ("svc-p3-01", "carriers_start_within_sync_window"): _within_window(),
    ("svc-p3-01", "pallet_requires_two_carriers"): _contact("visitor_target"),
    ("svc-p3-01", "release_after_joint_carry"): _precedence([_event("joint_carry_complete", source="milestone")], _event("released", source="milestone")),
    ("svc-p3-01", "accept_after_release"): _precedence([_event("released", source="milestone")], _event("accepted", source="milestone")),

    ("svc-p3-02", "coverage_has_no_uncovered_window"): _coverage("public_waiting_area", _event("coverage_started", source="milestone"), _event("accepted", source="milestone")),
    ("svc-p3-02", "scan_point_use_is_mutex"): _mutex("replenishment_scan_point"),
    ("svc-p3-02", "scan_complete_after_scan_point_reservation"): _precedence([_event("reserve_scan_point")], _event("scan_complete", source="milestone"), required_zone_id="replenishment_scan_point"),
    ("svc-p3-02", "coverage_and_replenishment_complete_within_horizon"): _deadline("svc-p3-02", "accepted"),

    ("svc-t-02", "access_mutex"): _mutex("access_point"),
    ("svc-t-02", "accept_after_release"): _release_enter_with_events("access_point", "case_owner", "responder", _event("released", source="milestone"), _event("accepted", source="milestone")),
    ("svc-t-02", "release_access_after_case_accept"): _precedence([_event("accepted", source="milestone")], _event("access_released", source="milestone")),
    ("svc-t-02", "case_handoff_within_horizon"): _deadline("svc-t-02", "access_released"),

    ("svc-t-03", "coverage_has_no_uncovered_window"): _coverage("warehouse_public_aisle", _event("service_window_start"), _event("service_window_end")),
    ("svc-t-03", "inspections_may_run_in_parallel"): _diagnostic("power_check_a", "power_check_b"),
    ("svc-t-03", "inspection_records_after_point_arrival"): {
        "operator": "precedence", "event_pairs": [
            {"before": _event("arrive_power_point", "power_check_a", source="task"), "after": _event("inspection_complete", "power_check_a", source="task")},
            {"before": _event("arrive_power_point", "power_check_b", source="task"), "after": _event("inspection_complete", "power_check_b", source="task")},
        ], "allow_equal": False, "scoring_role": "obligation", "source_ledger": "evaluator_event_ledger",
        "required_fields": ["arrive_power_point", "inspection_complete", "subject"],
    },
    ("svc-t-03", "coverage_and_inspections_within_horizon"): _deadline("svc-t-03", "accepted"),
}


def _outcome_semantics(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Map an oracle verdict to the registered downstream consumer boundary."""
    if spec["operator"] == "deadline":
        return {
            "on_violation": "task_incomplete",
            "experiment_interrupt": False,
            "d3_obligation_penalty": False,
            "consumers": [],
        }
    if spec.get("safety_class"):
        return {
            "on_violation": "task_unsuccessful",
            "success_variable": "Y_omega",
            "experiment_interrupt": False,
            "d3_obligation_penalty": False,
            "consumers": ["D2", "D6"],
        }
    if spec.get("scoring_role") == "diagnostic":
        return {
            "on_violation": "diagnostic_only",
            "experiment_interrupt": False,
            "d3_obligation_penalty": False,
            "consumers": [],
        }
    return {
        "on_violation": "obligation_violation",
        "experiment_interrupt": False,
        "d3_obligation_penalty": True,
        "consumers": ["D3"],
    }


for _binding_spec in DRAFT_BINDING_REGISTRY.values():
    _binding_spec["outcome_semantics"] = _outcome_semantics(_binding_spec)


def predicate_scoring_role(anchor_id: str, predicate_id: str) -> str:
    """Return the registered downstream role for pilot and draft predicates."""

    if (anchor_id, predicate_id) in PILOT_SAFETY_PREDICATES:
        return "safety_success"
    if (anchor_id, predicate_id) == (
        "prod-p1-01", "lot_a_and_lot_b_may_run_in_parallel"
    ):
        return "diagnostic"
    return DRAFT_BINDING_REGISTRY.get((anchor_id, predicate_id), {}).get(
        "scoring_role", "obligation"
    )


def _draft_spec(anchor_id: str, predicate_id: str) -> dict[str, Any]:
    try:
        return DRAFT_BINDING_REGISTRY[(anchor_id, predicate_id)]
    except KeyError as exc:
        raise KeyError(f"unregistered draft predicate: {(anchor_id, predicate_id)!r}") from exc


# Compatibility views used by contract generation and external audits.  These
# are derived from the pair registry; none is a judgment authority.
_DRAFT_DEADLINE_IDS = {pid for (anchor, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "deadline"}
_DRAFT_DEADLINE_TERMINALS = {pid: spec["terminal_milestone"] for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "deadline"}
_DRAFT_IDENTITY_SPECS = {pid: spec for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "identity_consistency"}
_DRAFT_ZONE_IDS = {pid: (spec.get("zone_id") or (spec.get("zone_ids") or [None])[0]) for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "non_overlapping"}
_DRAFT_RESOURCE_IDS = {pid for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "non_overlapping"}
_DRAFT_DIAGNOSTIC_IDS = {pid for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "may_overlap"}
_DRAFT_CONTACT_IDS = {pid for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "contact_count"}
_DRAFT_WINDOW_IDS = {pid for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "within_window"}
_DRAFT_PRECEDENCE_IDS = {pid for (_, pid), spec in DRAFT_BINDING_REGISTRY.items() if spec["operator"] == "precedence"}


def _event_tick(streams: PredicateStreams, ref: Mapping[str, Any]) -> int | None:
    if "fixed_tick" in ref:
        tick = ref["fixed_tick"]
        return tick if type(tick) is int else None
    name = str(ref["name"])
    subject = ref.get("subject")
    source = ref.get("source", "any")
    if source in {"milestone", "any"} and subject is None:
        tick = streams.milestone_tick(name)
        if tick is not None:
            return tick
    if source in {"task", "any"}:
        return streams.task_tick(name, str(subject) if subject is not None else None)
    return None


def _path_value(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for component in path.split("."):
        if not isinstance(value, Mapping) or component not in value:
            return None
        value = value[component]
    return value


def _zone_execution_interval(streams: PredicateStreams, zone_id: str) -> tuple[int, int] | None:
    intervals = streams.zones(zone_id)
    if not intervals:
        return None
    return min(item.enter_tick for item in intervals), max(item.exit_tick for item in intervals)


def _maximum_interval_overlap(
    left: Sequence[ZoneInterval],
    right: Sequence[ZoneInterval],
) -> int:
    """Maximum overlap of actual intervals; never bridges an unoccupied gap."""
    return max(
        (overlap_ticks((a.enter_tick, a.exit_tick), (b.enter_tick, b.exit_tick))
         for a in left for b in right),
        default=0,
    )


def _judge_draft_predicate(anchor_id: str, predicate_id: str, s: PredicateStreams) -> PredicateVerdict:
    spec = _draft_spec(anchor_id, predicate_id)
    operator = spec["operator"]
    base_evidence = {
        "operator": operator,
        "scoring_role": spec.get("scoring_role", "obligation"),
    }
    if spec.get("safety_class"):
        base_evidence["safety_class"] = True

    invariant_error = _tick_invariant_error(s, spec)
    if invariant_error:
        return _invariant_insufficient(
            anchor_id, predicate_id, invariant_error, **base_evidence
        )

    if operator == "deadline":
        label = spec["terminal_milestone"]
        tick = s.milestone_tick(label)
        if tick is None:
            return _insufficient(anchor_id, predicate_id, s, f"terminal milestone {label}")
        if type(tick) is not int:
            return _verdict(anchor_id, predicate_id, VerdictStatus.DATA_INSUFFICIENT,
                            cause=CauseCode.INVARIANT_INSUFFICIENT,
                            required=f"integer terminal milestone {label}", terminal_tick=tick, **base_evidence)
        ok, reason = check_deadline(tick, spec["deadline_ticks"])
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
                        (Violation(tick, reason),) if reason else (),
                        terminal_milestone=label, terminal_tick=tick,
                        deadline_ticks=spec["deadline_ticks"], success_condition=True,
                        **base_evidence)

    if operator == "identity_consistency":
        record = s.identity_records.get(spec["identity_key"])
        if not isinstance(record, Mapping):
            return _insufficient(anchor_id, predicate_id, s, f"identity record {spec['identity_key']}")
        checks: list[dict[str, Any]] = []
        violations: list[Violation] = []
        for check in spec["checks"]:
            actual = _path_value(record, check["actual_path"])
            expected = _path_value(record, check["expected_path"])
            if actual is None or expected is None:
                return _insufficient(anchor_id, predicate_id, s, f"identity fields {check['actual_path']} and {check['expected_path']}")
            ok, reason = check_identity_consistency(actual, expected, relation=check["relation"])
            checks.append({"actual_path": check["actual_path"], "expected_path": check["expected_path"], "relation": check["relation"], "matched": ok})
            if not ok:
                violations.append(Violation(0, f"{check['actual_path']}: {reason}"))
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
                        violations, identity_key=spec["identity_key"], checks=checks,
                        **base_evidence)

    if operator == "within_window":
        left = _event_tick(s, spec["event_pair"]["left"])
        right = _event_tick(s, spec["event_pair"]["right"])
        if left is None or right is None:
            return _insufficient(anchor_id, predicate_id, s, "both registered synchronization events")
        ok, diff = check_within_window(left, right, spec["window_ticks"])
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
                        (Violation(max(left, right), f"arrival differs by {diff} ticks"),) if not ok else (),
                        left_tick=left, right_tick=right, window_ticks=spec["window_ticks"],
                        **base_evidence)

    if operator == "contact_count":
        start = _event_tick(s, spec["window"]["start"])
        end = _event_tick(s, spec["window"]["end"])
        if start is None or end is None:
            return _insufficient(anchor_id, predicate_id, s, "registered carry start and completion events")
        if end <= start:
            return _verdict(anchor_id, predicate_id, VerdictStatus.VIOLATED,
                            (Violation(end, "carry window must have positive duration"),),
                            carry_window=(start, end), object_id=spec["object_id"], **base_evidence)
        contacts = s.holds(spec["object_id"])
        violations = check_contact_count((start, end), contacts, spec["minimum_contacts"])
        if violations and not s.integrity_confirmed:
            return _insufficient(
                anchor_id, predicate_id, s,
                f"complete logical hold interval ledger for {spec['object_id']}",
            )
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
                        violations, carry_window=(start, end), min_contacts=spec["minimum_contacts"],
                        contact_count=len(contacts), object_id=spec["object_id"], **base_evidence)

    if operator == "non_overlapping":
        zone_ids = list(spec["zone_ids"])
        ledgers = {zone_id: s.zones(zone_id) for zone_id in zone_ids}
        missing = [zone_id for zone_id, intervals in ledgers.items() if not intervals]
        violations: list[Violation] = []
        if spec.get("zone_scope") == "unified":
            unified = [replace(item, zone_id="|".join(zone_ids)) for values in ledgers.values() for item in values]
            violations.extend(check_zone_mutex(unified))
        else:
            for intervals in ledgers.values():
                violations.extend(check_zone_mutex(intervals))
        if not violations and missing:
            return _insufficient(anchor_id, predicate_id, s, f"zone intervals for {', '.join(missing)}")
        if not violations and not s.integrity_confirmed:
            return _insufficient(
                anchor_id, predicate_id, s,
                f"complete zone interval ledger for {', '.join(zone_ids)}",
            )
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
                        violations, zone_ids=zone_ids,
                        interval_count=sum(len(items) for items in ledgers.values()), **base_evidence)

    if operator == "may_overlap":
        pair = spec["interval_pair"]
        intervals_a = s.zones(pair[0]["zone_id"])
        intervals_b = s.zones(pair[1]["zone_id"])
        if not intervals_a or not intervals_b:
            return _insufficient(anchor_id, predicate_id, s, "both registered diagnostic execution intervals")
        got = _maximum_interval_overlap(intervals_a, intervals_b)
        ok = got >= spec["min_overlap_ticks"]
        if not ok and not s.integrity_confirmed:
            return _insufficient(
                anchor_id, predicate_id, s,
                "complete diagnostic interval ledgers",
            )
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.SATISFIED if ok else VerdictStatus.VIOLATED,
                        intervals_a=[(item.enter_tick, item.exit_tick) for item in intervals_a],
                        intervals_b=[(item.enter_tick, item.exit_tick) for item in intervals_b],
                        overlap_ticks=got,
                        min_overlap_ticks=spec["min_overlap_ticks"], **base_evidence)

    if operator == "continuous_coverage":
        start = _event_tick(s, spec["window"]["start"])
        end = _event_tick(s, spec["window"]["end"])
        if start is None or end is None:
            return _insufficient(anchor_id, predicate_id, s, "registered coverage window endpoints")
        if end <= start:
            return _verdict(anchor_id, predicate_id, VerdictStatus.VIOLATED,
                            (Violation(end, "coverage window must have positive duration"),),
                            horizon=(start, end), zone_id=spec["zone_id"], **base_evidence)
        covered = [(item.enter_tick, item.exit_tick) for item in s.zones(spec["zone_id"])]
        violations = check_coverage_no_gap((start, end), covered, spec["max_gap_ticks"])
        if violations and not s.integrity_confirmed:
            return _insufficient(
                anchor_id, predicate_id, s,
                f"complete coverage interval ledger for {spec['zone_id']}",
            )
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
                        violations, horizon=(start, end), zone_id=spec["zone_id"],
                        max_gap_ticks=spec["max_gap_ticks"], covered_interval_count=len(covered),
                        **base_evidence)

    if operator == "precedence" and spec.get("mode") == "release_enter":
        zone = s.zones(spec["zone_id"])
        if not zone:
            return _insufficient(anchor_id, predicate_id, s, f"zone intervals for {spec['zone_id']}")
        ok, reason = check_release_before_enter(zone, spec["before_holder"], spec["after_holder"])
        if reason == "missing release or re-entry interval":
            return _insufficient(anchor_id, predicate_id, s, f"holders {spec['before_holder']} and {spec['after_holder']} in {spec['zone_id']}")
        positive_violations = [Violation(0, reason)] if reason else []
        absence_dependent_violations: list[Violation] = []
        before_intervals = [item for item in zone if item.holder_id == spec["before_holder"]]
        after_intervals = [item for item in zone if item.holder_id == spec["after_holder"]]
        final_exit = max(item.exit_tick for item in before_intervals)
        first_enter = min(item.enter_tick for item in after_intervals)
        if "before_event" in spec:
            before_event_tick = _event_tick(s, spec["before_event"])
            after_event_tick = _event_tick(s, spec["after_event"])
            if before_event_tick is None or after_event_tick is None:
                return _insufficient(anchor_id, predicate_id, s, "registered release and acceptance events")
            if after_event_tick < before_event_tick:
                positive_violations.append(Violation(after_event_tick, "acceptance event precedes release event"))
            if spec.get("event_boundary_alignment") == "exact":
                if before_event_tick < final_exit:
                    positive_violations.append(Violation(before_event_tick, "release event precedes source holder final exit"))
                elif before_event_tick > final_exit:
                    absence_dependent_violations.append(Violation(before_event_tick, "release event follows observed source holder final exit"))
                if after_event_tick > first_enter:
                    positive_violations.append(Violation(after_event_tick, "acceptance event follows responder first entry"))
                elif after_event_tick < first_enter:
                    absence_dependent_violations.append(Violation(after_event_tick, "acceptance event precedes observed responder first entry"))
        if absence_dependent_violations and not positive_violations and not s.integrity_confirmed:
            return _insufficient(
                anchor_id, predicate_id, s,
                f"complete boundary-alignment ledger for {spec['zone_id']}",
            )
        violations = positive_violations + absence_dependent_violations
        if not violations and not s.integrity_confirmed:
            return _insufficient(
                anchor_id, predicate_id, s,
                f"complete release/entry ledger for {spec['zone_id']}",
            )
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
                        violations, zone_id=spec["zone_id"],
                        released_holder=spec["before_holder"], entering_holder=spec["after_holder"],
                        final_exit_tick=final_exit, first_enter_tick=first_enter,
                        **base_evidence)

    if operator == "precedence":
        positive_violations: list[Violation] = []
        absence_dependent_violations: list[Violation] = []
        observed_pairs: list[dict[str, int]] = []
        if "event_pairs" in spec:
            pairs = spec["event_pairs"]
        else:
            pairs = [{"before_many": spec["before_events"], "after": spec["after_event"]}]
        for pair in pairs:
            before_refs = pair.get("before_many") or [pair["before"]]
            before_ticks = [_event_tick(s, ref) for ref in before_refs]
            after_tick = _event_tick(s, pair["after"])
            if any(tick is None for tick in before_ticks) or after_tick is None:
                return _insufficient(anchor_id, predicate_id, s, "all registered precedence events")
            before_tick = max(tick for tick in before_ticks if tick is not None)
            if spec.get("allow_equal"):
                ok = after_tick >= before_tick
                reason = None if ok else f"after_tick={after_tick} precedes before_tick={before_tick}"
            else:
                ok, reason = check_precedence(before_tick, after_tick, spec.get("max_gap_ticks"))
            if not ok:
                positive_violations.append(Violation(after_tick, reason or "precedence violated"))
            observed_pairs.append({"before_tick": before_tick, "after_tick": after_tick})
        after_tick = observed_pairs[-1]["after_tick"]
        required_zone_id = spec.get("required_zone_id")
        if required_zone_id is not None:
            intervals = s.zones(required_zone_id)
            if not intervals:
                if positive_violations:
                    return _verdict(
                        anchor_id, predicate_id, VerdictStatus.VIOLATED,
                        positive_violations,
                        observed_pairs=observed_pairs,
                        **base_evidence,
                    )
                return _insufficient(anchor_id, predicate_id, s, f"zone intervals for {required_zone_id}")
            holder = spec.get("required_holder")
            relevant = [item for item in intervals if holder is None or item.holder_id == holder]
            if not relevant:
                if positive_violations:
                    return _verdict(
                        anchor_id, predicate_id, VerdictStatus.VIOLATED,
                        positive_violations,
                        observed_pairs=observed_pairs,
                        **base_evidence,
                    )
                return _insufficient(anchor_id, predicate_id, s, f"holder {holder} in {required_zone_id}")
            containing = [item for item in relevant if item.enter_tick <= after_tick < item.exit_tick]
            if not containing:
                absence_dependent_violations.append(Violation(after_tick, f"after event is outside physical occupancy of {required_zone_id}"))
            elif spec.get("require_zone_entry_after_before"):
                before_tick = observed_pairs[-1]["before_tick"]
                first_relevant_entry = min(item.enter_tick for item in containing)
                if first_relevant_entry <= before_tick:
                    positive_violations.append(Violation(first_relevant_entry, f"physical entry into {required_zone_id} is not strictly after reservation"))
            if (
                absence_dependent_violations
                and not positive_violations
                and not s.integrity_confirmed
            ):
                return _insufficient(
                    anchor_id, predicate_id, s,
                    f"complete event-containment ledger for {required_zone_id}",
                )
            if (
                not positive_violations
                and not absence_dependent_violations
                and spec.get("require_zone_entry_after_before")
                and not s.integrity_confirmed
            ):
                return _insufficient(
                    anchor_id, predicate_id, s,
                    f"complete reservation-entry ledger for {required_zone_id}",
                )
        violations = positive_violations + absence_dependent_violations
        evidence = dict(base_evidence, observed_pairs=observed_pairs)
        diagnostic_pair = spec.get("diagnostic_interval_pair")
        if diagnostic_pair:
            intervals_a = s.zones(diagnostic_pair[0]["zone_id"])
            intervals_b = s.zones(diagnostic_pair[1]["zone_id"])
            evidence["diagnostic_overlap_ticks"] = (
                _maximum_interval_overlap(intervals_a, intervals_b)
                if intervals_a and intervals_b else None
            )
            evidence["diagnostic_scoring_role"] = "diagnostic"
        return _verdict(anchor_id, predicate_id,
                        VerdictStatus.VIOLATED if violations else VerdictStatus.SATISFIED,
                        violations, **evidence)

    raise AssertionError(f"unsupported draft operator: {operator}")


ANCHOR_PREDICATES: dict[str, tuple[Callable[[str, PredicateStreams], PredicateVerdict], ...]] = {
    "prod-p1-01": (_judge_prod_p1_01,) * 3,
    "svc-p2-02": (_judge_svc_p2_02,) * 3,
    "liv-p3-01": (_judge_liv_p3_01,) * 4,
    "prod-t-02": (_judge_prod_t_02,) * 3,
    "svc-t-01": (_judge_svc_t_01,) * 3,
    "liv-p4-01": (_judge_liv_p4_01,) * 3,
}

BOUND_PREDICATES: dict[str, tuple[str, ...]] = {
    "prod-p1-01": (
        "lot_a_and_lot_b_may_run_in_parallel",
        "reconcile_after_both_picks",
        "one_scan_station_per_lot",
    ),
    "svc-p2-02": (
        "mark_after_inspection",
        "accept_after_release",
        "one_quarantine_bay",
    ),
    "liv-p3-01": (
        "carriers_start_within_sync_window",
        "release_after_joint_carry",
        "accept_after_release",
        "pallet_requires_two_carriers",
    ),
    "prod-t-02": (
        "coverage_has_no_uncovered_window",
        "scan_point_use_is_mutex",
        "scan_point_capacity_one",
    ),
    "svc-t-01": (
        "requests_cannot_overlap_counter",
        "release_between_requests",
        "counter_capacity_one",
    ),
    "liv-p4-01": (
        "load_a_and_load_b_cannot_overlap_dock",
        "release_before_load_b",
        "dock_capacity_one",
    ),
}

# Keep the historical six-pilot registry stable for callers that use it as a
# release-scope certificate.  ``ALL_BOUND_PREDICATES`` is the explicit
# full-catalog registry used by the 24-anchor contract projection.
ALL_BOUND_PREDICATES: dict[str, tuple[str, ...]] = {
    **BOUND_PREDICATES,
    **_DRAFT_PREDICATES,
}
ALL_ANCHOR_PREDICATES: dict[str, tuple[Callable[[str, PredicateStreams], PredicateVerdict], ...]] = {
    **ANCHOR_PREDICATES,
    **{anchor: (_judge_draft_predicate,) * len(predicates)
       for anchor, predicates in _DRAFT_PREDICATES.items()},
}

_PILOT_ZONE_SOURCES = {
    "one_scan_station_per_lot": "scan_station",
    "one_quarantine_bay": "quarantine_bay",
    "scan_point_use_is_mutex": "scan_point",
    "scan_point_capacity_one": "scan_point",
    "requests_cannot_overlap_counter": "service_counter",
    "release_between_requests": "service_counter",
    "counter_capacity_one": "service_counter",
    "load_a_and_load_b_cannot_overlap_dock": "dock",
    "release_before_load_b": "dock",
    "dock_capacity_one": "dock",
}


def judge_anchor(anchor_id: str, streams: PredicateStreams) -> list[PredicateVerdict]:
    judges = ALL_ANCHOR_PREDICATES.get(anchor_id)
    if judges is None:
        raise KeyError(f"unknown anchor: {anchor_id}")
    invariant_error = _tick_invariant_error(streams)
    if invariant_error and anchor_id not in _DRAFT_PREDICATES:
        verdicts = [
            _invariant_insufficient(anchor_id, predicate_id, invariant_error)
            for predicate_id in ALL_BOUND_PREDICATES[anchor_id]
        ]
    elif anchor_id in _DRAFT_PREDICATES:
        verdicts = [
            _judge_draft_predicate(anchor_id, predicate_id, streams)
            for predicate_id in ALL_BOUND_PREDICATES[anchor_id]
        ]
    else:
        verdicts = [
            judge(predicate_id, streams)
            for judge, predicate_id in zip(judges, ALL_BOUND_PREDICATES[anchor_id])
        ]
    annotated: list[PredicateVerdict] = []
    for verdict in verdicts:
        evidence = dict(verdict.evidence)
        if anchor_id in _DRAFT_PREDICATES:
            spec = _draft_spec(anchor_id, verdict.predicate_id)
            source = spec.get("source_ledger")
            zone_ids = list(spec.get("zone_ids", []))
            resource = spec.get("zone_id") or (zone_ids[0] if zone_ids else None) or spec.get("object_id")
            if source in {"evaluator_private_zone_intervals", "evaluator_private_hold_intervals"}:
                group = spec.get("dedupe_group") or (
                    f"{anchor_id}:occupancy:{resource}"
                    if source == "evaluator_private_zone_intervals"
                    else f"{anchor_id}:contact:{resource}"
                )
            else:
                group = None
        else:
            resource = evidence.get("zone_id") or _PILOT_ZONE_SOURCES.get(verdict.predicate_id)
            if resource:
                source = "evaluator_private_zone_intervals"
                group = f"{anchor_id}:occupancy:{resource}"
            elif evidence.get("object_id"):
                resource = evidence["object_id"]
                source = "evaluator_private_hold_intervals"
                group = f"{anchor_id}:contact:{resource}"
            else:
                source = None
                group = None
        if group:
            evidence.update({
                "source_ledger": source,
                "dedupe_group": group,
                "dedupe_key": f"{group}:{verdict.predicate_id}",
                "counting_policy": "one_consumer_per_source_group",
            })
        annotated.append(replace(verdict, evidence=evidence))
    return annotated


def judge_all(streams: PredicateStreams) -> list[PredicateVerdict]:
    """Judge the historical pilot scope (19 predicates).

    Use :func:`judge_all_catalog` for the full 30-anchor catalog.  Keeping the
    default pilot scope avoids silently changing the release certificate of
    existing callers while making the expanded scope explicit.
    """
    verdicts: list[PredicateVerdict] = []
    for anchor_id in BOUND_PREDICATES:
        verdicts.extend(judge_anchor(anchor_id, streams))
    return verdicts


def judge_all_catalog(streams: PredicateStreams) -> list[PredicateVerdict]:
    """Judge all 30 catalog anchors (six pilots plus 24 draft projections)."""
    verdicts: list[PredicateVerdict] = []
    for anchor_id in ALL_BOUND_PREDICATES:
        verdicts.extend(judge_anchor(anchor_id, streams))
    return verdicts
