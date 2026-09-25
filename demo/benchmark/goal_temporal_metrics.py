"""Offline temporal-coordination metrics with frozen trusted time anchors.

Protocol batch 3 (protocol section 3.3 "时序协同"): the branch-independent
temporal quantities that one archived episode can already support offline:

* :func:`frozen_time_anchors` freezes the P0-3 trusted time anchors
  (episode start and *real* termination) before any temporal metric is
  computed.  A missing host termination record is right-censored with the
  label ``termination_unverified_do_not_infer``; the last milestone
  observation, the last ``action_result``, derived action acceptance and
  participant self-reports are documented forbidden sources and are never
  substituted (viewer P0-3, stage65 stack review section 2.6/3).
* :func:`precedence_metrics` — ``PrecE2E``/``PrecOpportunity``/
  ``PrecGivenOpportunity`` triples with ``not_reached_system`` kept in the
  end-to-end denominator, evaluator-fault relations excluded as
  ``not_evaluable_external`` with disclosure, and successor-before-
  predecessor recorded as ``violated``.
* :func:`synchronization_metrics` — the same triple for synchronization
  groups with :math:`\\delta_g=\\max-\\min` over fully observed groups and
  the ``violated`` semantics for groups whose opportunity formed but a
  required event is missing at the deadline/horizon.
* :func:`handoff_gap_metrics` — signed handoff gaps
  :math:`G_h=t_{start,b}-t_{end,a}` (positive = service gap, negative =
  overlap) plus the continuity rate at ``G_h <= G_h^max``.
* :func:`mutex_metrics` — ``MutexAny`` (episode-level any-violation
  indicator) and ``MutexOverlap`` (occupied-time overlap ratio) computed
  from the execution intervals of a
  :mod:`demo.benchmark.goal_concurrency_evidence` report; no occupancy is
  an explicit N/A.
* :func:`unnecessary_wait_metrics` — unnecessary waits
  :math:`W_o=max(0,t_{start,o}-t_{ready,o})` with total/mean/median/P90
  over started opportunities, unstarted opportunities reported separately
  and never deleted from the denominator.
* :func:`coordination_stall_metrics` — the offline state machine of the
  coordination-stall protocol: candidate stall = ready opportunity AND no
  eligible action AND :math:`Q(t_k)-Q(s)\\le\\epsilon_Q`; confirmation
  requires :math:`\\ge\\delta_{stall}`; recovery predicates are supplied by
  the caller (external progress beyond :math:`\\epsilon_Q` or service
  restoration) and otherwise the recovery observation is right-censored.

Frozen offline conventions (protocol text left open, pinned by tests):

* event matching is by ``type`` plus payload key/value subset on the first
  matching event of the immutable session ledger (session events are
  append-only and time-ordered, so first match is the earliest occurrence);
* the observation end used by every absence judgment (deadlines, missing
  required events, stall horizon) is the trusted termination anchor; when
  termination is unverified the observation end is the last ledger event
  time *explicitly labelled as right-censored context*, never as a
  termination;
* an action enters :math:`\\mathcal A^{eligible}` only through an accepted
  ``dispatch_skill`` whose ``task_id`` belongs to a stored plan task, stays
  eligible until its trusted terminal receipt or ``T_a^max``, and an
  action type without a ``T_a^max`` entry is conservatively treated as
  unbounded (it can never manufacture a stall) and disclosed.

Offline boundaries that remain out of scope by design (no branch
dependency, per the shelved sham/I-timing probes): wait-for-graph logical
deadlock certificates, sham/I-timing paired branches, continuous-service
uncovered ratio :math:`U_s` (needs declared provider coverage windows),
precondition validity beyond ledger admission, and cross-episode rate
aggregation (:math:`N`-episode ``StallRate``/``MutexAny`` averages remain
caller-side reductions over per-episode outputs).

Every returned mapping carries ``release=False`` plus the development-stage
flags; nothing here is a protocol score.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

TEMPORAL_METRICS_SCHEMA = "eai.benchmark.goal_temporal_metrics"
TEMPORAL_METRICS_VERSION = 1

TERMINATION_UNVERIFIED = "termination_unverified_do_not_infer"
START_UNVERIFIED = "start_unverified_do_not_infer"

#: Anchor provenance vocabulary (P0-3): ``host_event`` beats
#: ``native_receipt``; anything else is ``unverified`` and never inferred.
ANCHOR_SOURCE_HOST_EVENT = "host_event"
ANCHOR_SOURCE_NATIVE_RECEIPT = "native_receipt"
ANCHOR_SOURCE_UNVERIFIED = "unverified"

#: Terminal record kinds accepted as a native episode termination receipt.
#: Ordinary evidence kinds (arrivals, observations, relay receipts) are
#: deliberately absent: observing them says nothing about episode end.
NATIVE_TERMINATION_KINDS = frozenset({"episode_terminated", "episode_terminal"})

#: Time sources that must never become termination anchors (disclosed in
#: every anchor freeze so a reader can audit the discipline).
FORBIDDEN_TERMINATION_SOURCES = (
    "last_milestone_observation",
    "last_action_result",
    "derived_action_acceptance",
    "participant_self_report",
)

_QUANTILE_METHOD = "nearest_rank_ceiling"

_RELEASE = {"release": False, "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}

_ACTION_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _nonnegative(value: Any, label: str) -> float:
    number = _finite(value, label)
    if number < 0:
        raise ValueError(f"{label} must be nonnegative")
    return number


def _positive(value: Any, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _session_report_mapping(session_report: Mapping[str, Any],
                            label: str = "session_report") -> Mapping[str, Any]:
    if not isinstance(session_report, Mapping):
        raise ValueError(f"{label} must be a GoalSession report mapping")
    inner = session_report.get("report")
    if not isinstance(session_report.get("events"), list) and isinstance(inner, Mapping):
        return inner  # archive wrapper {"episode_id": ..., "report": {...}}
    return session_report


def _events_of(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    events = report.get("events")
    if not isinstance(events, list):
        raise ValueError("session report carries no event list")
    return [row for row in events if isinstance(row, Mapping)]


def _event_time(event: Mapping[str, Any]) -> float:
    return _finite(event.get("time_s"), "event time_s")


def _match_event(events: Sequence[Mapping[str, Any]], spec: Mapping[str, Any],
                 label: str) -> Mapping[str, Any] | None:
    """First event matching ``{type, payload}`` (payload subset equality)."""
    if not isinstance(spec, Mapping):
        raise ValueError(f"{label} must be a mapping with 'type' and 'payload'")
    wanted_type = spec.get("type")
    if wanted_type is None:
        raise ValueError(f"{label} must declare an event 'type'")
    wanted_type = _text(wanted_type, f"{label} type")
    wanted_payload = spec.get("payload") or {}
    if not isinstance(wanted_payload, Mapping):
        raise ValueError(f"{label} payload must be a mapping")
    for event in events:
        if event.get("type") != wanted_type:
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if all(payload.get(key) == value for key, value in wanted_payload.items()):
            return event
    return None


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank (ceiling) quantile on an ascending-sorted sample."""
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


# ---------------------------------------------------------------------------
# P0-3: frozen trusted time anchors
# ---------------------------------------------------------------------------

def _native_termination_time(native_evidence_log: Mapping[str, Any] | None,
                             ) -> tuple[float, int] | None:
    if not isinstance(native_evidence_log, Mapping):
        return None
    records = native_evidence_log.get("records")
    if not isinstance(records, list):
        return None
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or record.get("kind") not in NATIVE_TERMINATION_KINDS:
            continue
        for holder in ("arrival", "stamp", "receipt_stamp", "publication_stamp"):
            stamp = record.get(holder)
            if isinstance(stamp, Mapping) and stamp.get("time_s") is not None:
                return _finite(stamp["time_s"], "native termination time_s"), index
        if record.get("time_s") is not None:
            return _finite(record["time_s"], "native termination time_s"), index
    return None


def frozen_time_anchors(session_report: Mapping[str, Any] | None,
                        native_evidence_log: Mapping[str, Any] | None = None,
                        ) -> dict[str, Any]:
    """Freeze the trusted episode start/termination anchors for one episode.

    Anchor policy (viewer P0-3, blocking batch 3):

    * **start** comes from the host ``episode_started`` event; a report
      without it gets ``start_unverified_do_not_infer`` — no fallback.
    * **termination** comes first from a host termination record (the
      ``human_assistance_requested`` host event, or the
      ``deployment_assessment.termination`` host bookkeeping entry), then
      from an explicit terminal record in the native evidence log
      (``episode_terminated``/``episode_terminal``).  With neither, the
      anchor is right-censored as ``termination_unverified_do_not_infer``;
      the last milestone observation / last ``action_result`` time is
      returned as *context only* and is never promoted to an anchor.

    Every anchor carries a ``source`` field from
    ``host_event``/``native_receipt``/``unverified``.
    """
    report = (None if session_report is None
              else _session_report_mapping(session_report))
    events = _events_of(report) if report is not None else []

    # --- start anchor -----------------------------------------------------
    start_event = _match_event(events, {"type": "episode_started"},
                               "episode_started")
    if start_event is not None:
        start_anchor: dict[str, Any] = {
            "time_s": _event_time(start_event),
            "source": ANCHOR_SOURCE_HOST_EVENT,
            "event_sequence": start_event.get("sequence"),
        }
    else:
        start_anchor = {"time_s": None, "source": ANCHOR_SOURCE_UNVERIFIED,
                        "label": START_UNVERIFIED}

    # --- termination anchor (host event > native receipt > unverified) ----
    termination_anchor: dict[str, Any] | None = None
    human_assistance = _match_event(events, {"type": "human_assistance_requested"},
                                    "human_assistance_requested")
    if human_assistance is not None:
        termination_anchor = {
            "time_s": _event_time(human_assistance),
            "source": ANCHOR_SOURCE_HOST_EVENT,
            "event_sequence": human_assistance.get("sequence"),
            "record": "human_assistance_requested_event",
        }
    if termination_anchor is None and report is not None:
        assessment = report.get("deployment_assessment")
        if isinstance(assessment, Mapping):
            termination = assessment.get("termination")
            if isinstance(termination, Mapping) and termination.get("time_s") is not None:
                termination_anchor = {
                    "time_s": _finite(termination["time_s"],
                                      "deployment termination time_s"),
                    "source": ANCHOR_SOURCE_HOST_EVENT,
                    "event_sequence": None,
                    "record": "deployment_assessment_termination",
                }
    if termination_anchor is None:
        native = _native_termination_time(native_evidence_log)
        if native is not None:
            termination_anchor = {
                "time_s": native[0],
                "source": ANCHOR_SOURCE_NATIVE_RECEIPT,
                "record_index": native[1],
                "record_kind": "explicit_native_terminal_record",
            }
    if termination_anchor is None:
        termination_anchor = {"time_s": None, "source": ANCHOR_SOURCE_UNVERIFIED,
                              "label": TERMINATION_UNVERIFIED}

    if (start_anchor["time_s"] is not None and termination_anchor["time_s"] is not None
            and termination_anchor["time_s"] < start_anchor["time_s"]):
        raise ValueError("termination anchor precedes the start anchor")

    last_event_time = max((_event_time(event) for event in events), default=None)
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": ("frozen" if start_anchor["time_s"] is not None
                   and termination_anchor["time_s"] is not None
                   else "partially_frozen_right_censored"),
        "episode_start": start_anchor,
        "termination": termination_anchor,
        "censoring": {
            "termination_right_censored": termination_anchor["time_s"] is None,
            "start_right_censored": start_anchor["time_s"] is None,
            "rule": ("absence of a host/native termination record is "
                     "right-censoring; the observation end is never inferred "
                     "from the last milestone observation or last action"),
        },
        "forbidden_termination_sources": list(FORBIDDEN_TERMINATION_SOURCES),
        "last_event_context": {
            "time_s": last_event_time,
            "usage": "context_only_not_a_time_anchor",
            "note": ("the last ledger event time bounds observation extent "
                     "only; it is not an episode termination anchor"),
        },
        **_RELEASE,
    }
    return result


def _observation_end(anchors: Mapping[str, Any]) -> dict[str, Any]:
    """Trusted observation end derived from frozen anchors, never invented."""
    termination = anchors.get("termination") or {}
    if termination.get("time_s") is not None:
        return {"time_s": float(termination["time_s"]),
                "source": f"termination_anchor:{termination.get('source')}",
                "right_censored": False}
    last = ((anchors.get("last_event_context") or {}).get("time_s"))
    return {"time_s": None if last is None else float(last),
            "source": "last_event_context_right_censored",
            "right_censored": True,
            "label": TERMINATION_UNVERIFIED}


def _anchors_for(session_report: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    anchors = frozen_time_anchors(session_report)
    return anchors, _observation_end(anchors)


# ---------------------------------------------------------------------------
# Precedence relations (protocol 3.3 E2E triple)
# ---------------------------------------------------------------------------

def precedence_metrics(session_report: Mapping[str, Any] | None,
                       relations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """PrecE2E / PrecOpportunity / PrecGivenOpportunity for one episode.

    ``relations`` items carry ``relation_id``, ``predecessor_event`` and
    ``successor_event`` specs (matched by event ``type`` plus payload
    key/value subset), the allowed gap ``[min_gap_s, max_gap_s]`` and
    optionally ``not_evaluable_external_reason`` (evaluator-injected fault;
    excluded from the valid set with disclosure).
    """
    report = (None if session_report is None
              else _session_report_mapping(session_report))
    events = _events_of(report) if report is not None else []
    _, observation = _anchors_for(session_report)
    observation_end_s = observation["time_s"]

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    n_opportunity = 0
    n_opportunity_ok = 0
    n_not_reached = 0

    for relation in relations or []:
        if not isinstance(relation, Mapping):
            raise ValueError("relation entries must be mappings")
        relation_id = _text(relation.get("relation_id"), "relation_id")
        predecessor = _match_event(events, relation.get("predecessor_event"),
                                    f"{relation_id} predecessor_event")
        successor = _match_event(events, relation.get("successor_event"),
                                 f"{relation_id} successor_event")
        reason = relation.get("not_evaluable_external_reason")
        if reason is not None:
            reason = _text(reason, f"{relation_id} not_evaluable_external_reason")
            excluded.append({"relation_id": relation_id,
                             "status": "not_evaluable_external",
                             "reason": reason})
            continue

        min_gap = _nonnegative(relation.get("min_gap_s", 0.0),
                               f"{relation_id} min_gap_s")
        max_gap = _finite(relation.get("max_gap_s"), f"{relation_id} max_gap_s")
        if max_gap < min_gap:
            raise ValueError(f"{relation_id} max_gap_s must be >= min_gap_s")

        row: dict[str, Any] = {"relation_id": relation_id}
        if predecessor is None and successor is None:
            # The system itself never reached the predecessor: opportunity
            # never formed; the relation STAYS in the end-to-end denominator.
            row.update({"status": "not_reached_system", "opportunity": 0,
                        "precedence_ok": 0,
                        "predecessor_time_s": None, "successor_time_s": None})
            n_not_reached += 1
        elif predecessor is None:
            # Successor appeared while no predecessor ever did: since the
            # ledger is an append-only prefix, the successor factually
            # precedes the predecessor -> immediate violation (O=1, OK=0).
            row.update({"status": "violated_successor_before_predecessor",
                        "opportunity": 1, "precedence_ok": 0,
                        "predecessor_time_s": None,
                        "successor_time_s": _event_time(successor)})
            n_opportunity += 1
        else:
            predecessor_time = _event_time(predecessor)
            row["predecessor_time_s"] = predecessor_time
            n_opportunity += 1
            if successor is None:
                deadline = predecessor_time + max_gap
                row["successor_time_s"] = None
                row["deadline_s"] = deadline
                if observation_end_s is not None and observation_end_s >= deadline:
                    # Opportunity formed and the allowed deadline passed
                    # inside the trusted observation window.
                    row.update({"status": "violated_successor_missing_at_deadline",
                                "opportunity": 1, "precedence_ok": 0})
                elif observation_end_s is not None:
                    # Episode ended (right-censored observation end) before
                    # the deadline: fate unresolved, kept unmet in the E2E
                    # denominator with an explicit censoring note.
                    row.update({"status": "right_censored_deadline_after_observation_end",
                                "opportunity": 1, "precedence_ok": 0,
                                "right_censored": True})
                else:
                    row.update({"status": "right_censored_termination_unverified",
                                "opportunity": 1, "precedence_ok": 0,
                                "right_censored": True,
                                "label": TERMINATION_UNVERIFIED})
            else:
                successor_time = _event_time(successor)
                row["successor_time_s"] = successor_time
                if successor_time < predecessor_time:
                    row.update({"status": "violated_successor_before_predecessor",
                                "opportunity": 1, "precedence_ok": 0,
                                "gap_s": successor_time - predecessor_time})
                else:
                    gap = successor_time - predecessor_time
                    row["gap_s"] = gap
                    if min_gap <= gap <= max_gap:
                        row.update({"status": "satisfied", "opportunity": 1,
                                    "precedence_ok": 1})
                        n_opportunity_ok += 1
                    else:
                        row.update({"status": "violated_gap_out_of_bounds",
                                    "opportunity": 1, "precedence_ok": 0})
        rows.append(row)

    n_evaluable = len(rows)
    prec_e2e = _ratio(n_opportunity_ok, n_evaluable)
    prec_opportunity = _ratio(n_opportunity, n_evaluable)
    given = _ratio(n_opportunity_ok, n_opportunity)
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": "computed" if n_evaluable else "na_no_evaluable_relations",
        "n_relations_total": n_evaluable + len(excluded),
        "n_evaluable": n_evaluable,
        "n_not_evaluable_external": len(excluded),
        "not_evaluable_external": excluded,
        "PrecE2E": prec_e2e,
        "PrecOpportunity": prec_opportunity,
        "PrecGivenOpportunity": given,
        "PrecGivenOpportunity_note": (
            None if given is not None else "na_zero_opportunities"),
        "not_reached_system_count": n_not_reached,
        "not_reached_system_rate": _ratio(n_not_reached, n_evaluable),
        "denominator_rule": ("not_reached_system stays in the PrecE2E "
                             "denominator; only not_evaluable_external is "
                             "excluded, with reason disclosure"),
        "relations": rows,
        **_RELEASE,
    }
    return result


# ---------------------------------------------------------------------------
# Synchronization groups
# ---------------------------------------------------------------------------

def synchronization_metrics(session_report: Mapping[str, Any] | None,
                            groups: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """SyncE2E / SyncOpportunity / SyncGivenOpportunity for one episode.

    ``groups`` items carry ``group_id``, ``events`` (member event specs,
    matched by ``type`` plus payload subset), ``window_s`` (:math:`W_g`)
    and optionally ``not_evaluable_external_reason``.  Offline group
    opportunity convention: :math:`O_g=1` once at least one required member
    event is observed (the group was reached); a group with no member event
    is ``not_reached_system`` and stays in the denominator.
    """
    report = (None if session_report is None
              else _session_report_mapping(session_report))
    events = _events_of(report) if report is not None else []
    _, observation = _anchors_for(session_report)
    observation_end_s = observation["time_s"]

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    n_opportunity = 0
    n_ok = 0
    n_not_reached = 0

    for group in groups or []:
        if not isinstance(group, Mapping):
            raise ValueError("group entries must be mappings")
        group_id = _text(group.get("group_id"), "group_id")
        reason = group.get("not_evaluable_external_reason")
        if reason is not None:
            reason = _text(reason, f"{group_id} not_evaluable_external_reason")
            excluded.append({"group_id": group_id,
                             "status": "not_evaluable_external",
                             "reason": reason})
            continue
        window = _nonnegative(group.get("window_s"), f"{group_id} window_s")
        specs = group.get("events")
        if not isinstance(specs, Sequence) or isinstance(specs, (str, bytes)) or not specs:
            raise ValueError(f"{group_id} events must be a nonempty list of event specs")
        member_times: list[float] = []
        for index, spec in enumerate(specs):
            match = _match_event(events, spec, f"{group_id} member {index}")
            member_times.append(None if match is None else _event_time(match))

        row: dict[str, Any] = {"group_id": group_id, "window_s": window,
                               "member_time_s": list(member_times)}
        observed = [t for t in member_times if t is not None]
        if not observed:
            row.update({"status": "not_reached_system", "opportunity": 0, "sync_ok": 0})
            n_not_reached += 1
            rows.append(row)
            continue
        n_opportunity += 1
        row["opportunity"] = 1
        if len(observed) == len(member_times):
            delta_g = max(observed) - min(observed)
            row["delta_g_s"] = delta_g
            if delta_g <= window:
                row.update({"status": "satisfied", "sync_ok": 1})
                n_ok += 1
            else:
                row.update({"status": "violated_window_exceeded", "sync_ok": 0})
        else:
            first = min(observed)
            deadline = first + window
            row["deadline_s"] = deadline
            row["missing_member_count"] = len(member_times) - len(observed)
            if observation_end_s is not None and observation_end_s >= deadline:
                row.update({"status": "violated_required_event_missing", "sync_ok": 0})
            elif observation_end_s is not None:
                row.update({"status": "right_censored_window_open_at_observation_end",
                            "sync_ok": 0, "right_censored": True})
            else:
                row.update({"status": "right_censored_termination_unverified",
                            "sync_ok": 0, "right_censored": True,
                            "label": TERMINATION_UNVERIFIED})
        rows.append(row)

    n_evaluable = len(rows)
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": "computed" if n_evaluable else "na_no_evaluable_groups",
        "n_groups_total": n_evaluable + len(excluded),
        "n_evaluable": n_evaluable,
        "n_not_evaluable_external": len(excluded),
        "not_evaluable_external": excluded,
        "SyncE2E": _ratio(n_ok, n_evaluable),
        "SyncOpportunity": _ratio(n_opportunity, n_evaluable),
        "SyncGivenOpportunity": _ratio(n_ok, n_opportunity),
        "SyncGivenOpportunity_note": (
            None if n_opportunity else "na_zero_opportunities"),
        "not_reached_system_count": n_not_reached,
        "not_reached_system_rate": _ratio(n_not_reached, n_evaluable),
        "opportunity_rule": ("O_g=1 once at least one required member event "
                             "is observed; groups never reached stay in the "
                             "SyncE2E denominator"),
        "groups": rows,
        **_RELEASE,
    }
    return result


# ---------------------------------------------------------------------------
# Handoff gaps
# ---------------------------------------------------------------------------

def handoff_gap_metrics(session_report: Mapping[str, Any] | None,
                        handoffs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Signed handoff gaps and the continuity rate for one episode.

    ``handoffs`` items carry ``handoff_id``, ``start_event`` (new provider
    :math:`b` start spec), ``end_event`` (old provider :math:`a` end spec)
    and ``max_gap_s`` (:math:`G_h^{max}`).  :math:`G_h=t_{start,b}-t_{end,a}`
    is signed: positive means a service gap, negative means overlap.
    Handoffs with a missing endpoint are excluded from the continuity ratio
    and disclosed; the ratio is N/A when nothing is evaluable.  The
    continuous-service uncovered ratio :math:`U_s` needs declared coverage
    windows and stays a documented offline gap.
    """
    report = (None if session_report is None
              else _session_report_mapping(session_report))
    events = _events_of(report) if report is not None else []

    rows: list[dict[str, Any]] = []
    n_continuous = 0
    n_evaluable = 0
    n_missing = 0
    gaps: list[float] = []

    for handoff in handoffs or []:
        if not isinstance(handoff, Mapping):
            raise ValueError("handoff entries must be mappings")
        handoff_id = _text(handoff.get("handoff_id"), "handoff_id")
        max_gap = _finite(handoff.get("max_gap_s"), f"{handoff_id} max_gap_s")
        start = _match_event(events, handoff.get("start_event"),
                             f"{handoff_id} start_event")
        end = _match_event(events, handoff.get("end_event"),
                           f"{handoff_id} end_event")
        row: dict[str, Any] = {"handoff_id": handoff_id, "max_gap_s": max_gap,
                               "start_time_s": None, "end_time_s": None,
                               "G_h_s": None, "continuous": None}
        if end is None:
            row["status"] = "not_evaluable_missing_end_event"
            n_missing += 1
        elif start is None:
            row["status"] = "not_evaluable_missing_start_event"
            n_missing += 1
        else:
            start_time = _event_time(start)
            end_time = _event_time(end)
            gap = start_time - end_time
            row.update({"status": "evaluated", "start_time_s": start_time,
                        "end_time_s": end_time, "G_h_s": gap,
                        "semantics": "gap" if gap > 0 else (
                            "overlap" if gap < 0 else "seamless"),
                        "continuous": bool(gap <= max_gap)})
            gaps.append(gap)
            n_evaluable += 1
            if gap <= max_gap:
                n_continuous += 1
        rows.append(row)

    sorted_gaps = sorted(gaps)
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": "computed" if n_evaluable else "na_no_evaluable_handoffs",
        "n_handoffs_total": len(rows),
        "n_evaluable": n_evaluable,
        "n_missing_endpoint": n_missing,
        "continuity_rate": _ratio(n_continuous, n_evaluable),
        "G_h_mean_s": (sum(gaps) / len(gaps) if gaps else None),
        "G_h_median_s": ((sorted_gaps[len(sorted_gaps) // 2]
                          if len(sorted_gaps) % 2 == 1
                          else (sorted_gaps[len(sorted_gaps) // 2 - 1]
                                + sorted_gaps[len(sorted_gaps) // 2]) / 2)
                         if gaps else None),
        "handoffs": rows,
        "deferred": ("continuous-service uncovered ratio U_s requires declared "
                     "provider coverage windows and is not computed offline"),
        **_RELEASE,
    }
    return result


# ---------------------------------------------------------------------------
# Mutex violations
# ---------------------------------------------------------------------------

def mutex_metrics(concurrency_evidence: Mapping[str, Any]) -> dict[str, Any]:
    """MutexAny (episode indicator) and MutexOverlap from execution intervals.

    Execution intervals come from the ``resource_intervals`` block of a
    :mod:`demo.benchmark.goal_concurrency_evidence` report (closed actual
    executions with their resource keys).  ``MutexOverlap`` is the ratio of
    doubly-held time to singly-held time over all resources; with no
    occupancy at all it is an explicit N/A.
    """
    if not isinstance(concurrency_evidence, Mapping):
        raise ValueError("concurrency_evidence must be a mapping")
    intervals = concurrency_evidence.get("resource_intervals")
    if not isinstance(intervals, Mapping):
        raise ValueError("concurrency_evidence carries no resource_intervals mapping")

    total_occupied = 0.0
    total_overlap = 0.0
    per_resource: dict[str, dict[str, Any]] = {}
    any_violation = False
    for key in sorted(intervals):
        rows = intervals[key]
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise ValueError(f"resource {key} intervals must be a list")
        boundary: list[tuple[float, int]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"resource {key} interval must be a mapping")
            start = _finite(row.get("start_s"), f"resource {key} start_s")
            end = _finite(row.get("end_s"), f"resource {key} end_s")
            if end < start:
                raise ValueError(f"resource {key} interval ends before it starts")
            boundary.append((start, 1))
            boundary.append((end, -1))
        # Ends sort before starts at equal timestamps: touching intervals
        # contribute neither occupancy nor overlap duration.
        boundary.sort(key=lambda item: (item[0], item[1]))
        occupied = 0.0
        overlap = 0.0
        count = 0
        prev: float | None = None
        for time, delta in boundary:
            if prev is not None and count >= 1:
                span = time - prev
                occupied += span
                if count > 1 and span > 0:
                    overlap += span
                    any_violation = True
            count += delta
            prev = time
        total_occupied += occupied
        total_overlap += overlap
        per_resource[key] = {"occupied_s": occupied, "overlap_s": overlap,
                             "interval_count": len(rows)}

    overlap_ratio = _ratio(total_overlap, total_occupied)
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": "computed" if total_occupied > 0 else "na_no_resource_occupancy",
        "MutexAny_episode": bool(any_violation),
        "MutexOverlap": overlap_ratio,
        "overlap_time_s": total_overlap,
        "occupied_time_s": total_occupied,
        "resource_count": len(intervals),
        "per_resource": per_resource,
        "scope_note": ("single-episode indicator; the N-episode MutexAny rate "
                       "is the caller's aggregation over episodes"),
        **_RELEASE,
    }
    return result


# ---------------------------------------------------------------------------
# Unnecessary waits
# ---------------------------------------------------------------------------

def unnecessary_wait_metrics(session_report: Mapping[str, Any] | None,
                             opportunities: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Unnecessary-wait statistics with unstarted opportunities preserved.

    ``opportunities`` items carry ``opportunity_id``, ``ready_at_s`` and
    ``start_at_s`` (or ``None`` when the planned action never started).
    Started opportunities contribute :math:`W_o=max(0, t_{start}-t_{ready})`
    to the total/mean/median/P90; unstarted ones are marked
    ``not_started``, never deleted from the reporting set, and reported as
    a separate fraction (protocol: startup failures must stay visible).
    """
    _, observation = _anchors_for(session_report)
    observation_end_s = observation["time_s"]

    rows: list[dict[str, Any]] = []
    waits: list[float] = []
    n_unstarted = 0
    n_negative_raw = 0
    for opportunity in opportunities or []:
        if not isinstance(opportunity, Mapping):
            raise ValueError("opportunity entries must be mappings")
        opportunity_id = _text(opportunity.get("opportunity_id"), "opportunity_id")
        ready_at = _nonnegative(opportunity.get("ready_at_s"),
                                f"{opportunity_id} ready_at_s")
        start_at = opportunity.get("start_at_s")
        row: dict[str, Any] = {"opportunity_id": opportunity_id,
                               "ready_at_s": ready_at, "start_at_s": None,
                               "wait_s": None, "status": None}
        if start_at is None:
            row["status"] = "not_started"
            if observation_end_s is not None and ready_at > observation_end_s:
                row["status"] = "not_started_beyond_observation_end"
            n_unstarted += 1
        else:
            start_at = _nonnegative(start_at, f"{opportunity_id} start_at_s")
            raw = start_at - ready_at
            if raw < 0:
                n_negative_raw += 1
            wait = max(0.0, raw)
            row.update({"start_at_s": start_at, "wait_s": wait,
                        "status": "started", "raw_gap_s": raw})
            waits.append(wait)
        rows.append(row)

    total = len(rows)
    if total == 0:
        return {
            "schema": TEMPORAL_METRICS_SCHEMA, "version": TEMPORAL_METRICS_VERSION,
            "status": "na_no_opportunities",
            **_RELEASE,
        }
    sorted_waits = sorted(waits)
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": "computed",
        "n_opportunities": total,
        "n_started": len(waits),
        "n_not_started": n_unstarted,
        "not_started_fraction": n_unstarted / total,
        "started_fraction": len(waits) / total,
        "total_unnecessary_wait_s": sum(waits) if waits else 0.0,
        "mean_wait_s": (sum(waits) / len(waits) if waits else None),
        "median_wait_s": ((sorted_waits[len(sorted_waits) // 2]
                           if len(sorted_waits) % 2 == 1
                           else (sorted_waits[len(sorted_waits) // 2 - 1]
                                 + sorted_waits[len(sorted_waits) // 2]) / 2)
                          if waits else None),
        "p90_wait_s": (_percentile(sorted_waits, 0.9) if waits else None),
        "quantile_method": _QUANTILE_METHOD,
        "n_negative_raw_gap": n_negative_raw,
        "reporting_rule": ("statistics cover started opportunities only; "
                           "unstarted opportunities are marked, never deleted "
                           "from the denominator set"),
        "opportunities": rows,
        **_RELEASE,
    }
    return result


# ---------------------------------------------------------------------------
# Coordination stall (offline state machine)
# ---------------------------------------------------------------------------

class _QFunction:
    """Step-function Q(t) with hold-last semantics (0 before first sample)."""

    def __init__(self, q_curve: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> None:
        samples: Any = q_curve
        if isinstance(q_curve, Mapping):
            samples = q_curve.get("Q_samples")
        if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)):
            raise ValueError("q_curve must be a progress-curve result or a "
                             "list of {t_s, Q} samples")
        parsed: list[tuple[float, float]] = []
        for sample in samples:
            if not isinstance(sample, Mapping):
                raise ValueError("Q samples must be mappings")
            t = _nonnegative(sample.get("t_s"), "Q sample t_s")
            value = _finite(sample.get("Q"), "Q sample value")
            if not 0.0 <= value <= 1.0:
                raise ValueError("Q sample value must lie in [0, 1]")
            parsed.append((t, value))
        if not parsed:
            raise ValueError("q_curve carries no samples")
        parsed.sort(key=lambda item: item[0])
        self.times = [t for t, _ in parsed]
        self.values = [v for _, v in parsed]

    def at(self, t: float) -> float:
        index = 0
        values = self.values
        times = self.times
        while index < len(times) and times[index] <= t:
            index += 1
        return 0.0 if index == 0 else values[index - 1]

    def change_times(self) -> list[float]:
        changes: list[float] = []
        previous: float | None = None
        for t, value in zip(self.times, self.values):
            if previous is None or value != previous:
                changes.append(t)
            previous = value
        return changes


def _merge_intervals(intervals: Iterable[Sequence[float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for start, end in sorted((float(a), float(b)) for a, b in intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _eligible_actions(report: Mapping[str, Any],
                      action_timeout_table: Mapping[str, float],
                      ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build the eligible-action timeline from trusted ledger facts.

    An action enters :math:`\\mathcal A^{eligible}` at its accepted
    ``dispatch_skill`` receipt, provided its ``task_id`` belongs to a stored
    plan task (explicit binding to a declared plan action).  It leaves at
    the trusted terminal receipt or, failing that, at
    ``start + T_a^max[type]`` (an ``action_timeout``).  Action types without
    a timeout-table entry are conservatively unbounded and disclosed: they
    can never manufacture a stall.
    """
    actions = report.get("actions")
    actions = actions if isinstance(actions, list) else []
    tasks = report.get("tasks")
    known_task_ids = set(tasks) if isinstance(tasks, Mapping) else set()
    type_by_id = {row.get("action_id"): row.get("type")
                  for row in actions if isinstance(row, Mapping)}

    started: dict[str, dict[str, Any]] = {}
    terminal: dict[str, float] = {}
    unresolvable = 0
    for event in report.get("events") or []:
        if not isinstance(event, Mapping) or event.get("type") != "action_result":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        action_id = payload.get("action_id")
        status = payload.get("status")
        if action_id is None:
            continue
        if status == "accepted" and type_by_id.get(action_id) == "dispatch_skill":
            started[action_id] = {"action_id": action_id,
                                  "started_at_s": _event_time(event)}
        elif status in _ACTION_TERMINAL_STATUSES and action_id not in terminal:
            terminal[action_id] = _event_time(event)

    timeline: list[dict[str, Any]] = []
    n_not_bound = 0
    for action_id, row in sorted(started.items(), key=lambda item: item[1]["started_at_s"]):
        payload = None
        for candidate in actions:
            if isinstance(candidate, Mapping) and candidate.get("action_id") == action_id:
                payload = candidate.get("payload")
                break
        task_id = payload.get("task_id") if isinstance(payload, Mapping) else None
        if task_id not in known_task_ids:
            # Not explicitly bound to a declared plan action: ineligible.
            n_not_bound += 1
            continue
        action_type = type_by_id.get(action_id)
        start = row["started_at_s"]
        entry: dict[str, Any] = {"action_id": action_id, "type": action_type,
                                 "task_id": task_id,
                                 "started_at_s": start}
        # Viewer stage65-batch3 P0-2: eligibility ends at the earlier of the
        # terminal receipt and the T_a^max boundary; a late receipt past the
        # timeout never extends eligibility past the timeout (the timeout
        # fact is preserved when it fires first).
        timeout_cap: float | None = None
        if action_type in action_timeout_table:
            timeout_cap = start + _nonnegative(
                action_timeout_table[action_type], "T_a_max entry")
        if action_id in terminal:
            end_time = terminal[action_id]
            if timeout_cap is not None and end_time > timeout_cap:
                entry.update({"eligible_until_s": timeout_cap,
                              "end_status": "action_timeout_before_late_receipt",
                              "terminal_receipt_at_s": end_time})
            else:
                entry.update({"eligible_until_s": end_time,
                              "end_status": "terminal_receipt"})
        elif timeout_cap is not None:
            entry.update({"eligible_until_s": timeout_cap,
                          "end_status": "action_timeout"})
        else:
            entry.update({"eligible_until_s": None,
                          "end_status": "missing_timeout_entry_unbounded"})
            unresolvable += 1
        timeline.append(entry)
    diagnostics = {"n_dispatch_not_bound_to_plan": n_not_bound,
                   "n_missing_timeout_entries": unresolvable}
    return timeline, diagnostics


def coordination_stall_metrics(
    session_report: Mapping[str, Any] | None,
    *,
    delta_stall_s: float,
    delta_clear_s: float,
    epsilon_q: float,
    q_curve: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    action_timeout_table: Mapping[str, float],
    opportunity_windows: Iterable[Mapping[str, Any]] | None = None,
    blocked_intervals: Iterable[Sequence[float]] | None = None,
    recovery_windows: Iterable[Sequence[float]] | None = None,
    horizon_s: float | None = None,
) -> dict[str, Any]:
    """Offline coordination-stall state machine for one episode.

    Candidate stall at time :math:`t`: :math:`t\\in\\Omega` (not blocked),
    at least one ready-and-unfinished opportunity, no eligible action, and
    :math:`Q(t)-Q(s)\\le\\epsilon_Q` since the candidate start :math:`s`.
    A candidate confirms when the maximal interval satisfies
    :math:`e-s\\ge\\delta_{stall}` with :math:`Q(e)-Q(s)\\le\\epsilon_Q`;
    :math:`T^{det}=s+\\delta_{stall}`.  Recovery requires the caller's
    pre-registered predicate (external progress beyond :math:`\\epsilon_Q`
    or service restoration) to hold for :math:`\\delta_{clear}`; without a
    predicate the recovery observation is right-censored, never guessed.
    ``horizon_s`` overrides the observation end; otherwise the trusted
    termination anchor is used and an unverified termination right-censors
    every horizon-dependent quantity at the last ledger event.

    ``opportunity_windows`` items carry ``opportunity_id``, ``ready_at_s``
    and optional ``satisfied_at_s`` (missing = still unfinished).  ``None``
    (no opportunity source at all) and ``[]`` (no ready opportunity) are
    both explicit N/A conditions: the stall indicator cannot be computed
    without the opportunity set.
    """
    delta_stall = _positive(delta_stall_s, "delta_stall_s")
    delta_clear = _nonnegative(delta_clear_s, "delta_clear_s")
    epsilon = _nonnegative(epsilon_q, "epsilon_q")
    if not isinstance(action_timeout_table, Mapping):
        raise ValueError("action_timeout_table must be a mapping")
    q = _QFunction(q_curve)

    report = (None if session_report is None
              else _session_report_mapping(session_report))
    if report is None:
        raise ValueError("coordination_stall_metrics requires a session report")
    _, observation = _anchors_for(session_report)
    if horizon_s is not None:
        horizon = _nonnegative(horizon_s, "horizon_s")
        horizon_source = "declared_horizon"
    elif observation["time_s"] is not None:
        horizon = observation["time_s"]
        horizon_source = ("trusted_termination_anchor"
                          if not observation["right_censored"]
                          else "right_censored_observation_end")
    else:
        horizon = 0.0
        horizon_source = "no_observation_no_events"
    horizon_censored = horizon_s is None and observation["right_censored"]

    if opportunity_windows is None:
        return {
            "schema": TEMPORAL_METRICS_SCHEMA, "version": TEMPORAL_METRICS_VERSION,
            "status": "na_no_opportunity_source",
            "na_reason": ("ready-opportunity set is required input; the stall "
                          "indicator is not computed without it"),
            **_RELEASE,
        }
    opportunities: list[dict[str, Any]] = []
    for window in opportunity_windows:
        if not isinstance(window, Mapping):
            raise ValueError("opportunity window entries must be mappings")
        opportunity_id = _text(window.get("opportunity_id"), "opportunity_id")
        ready_at = _nonnegative(window.get("ready_at_s"),
                                f"{opportunity_id} ready_at_s")
        satisfied_at = window.get("satisfied_at_s")
        if satisfied_at is not None:
            satisfied_at = _nonnegative(satisfied_at,
                                        f"{opportunity_id} satisfied_at_s")
        opportunities.append({"opportunity_id": opportunity_id,
                              "ready_at_s": ready_at,
                              "satisfied_at_s": satisfied_at})
    if not opportunities:
        return {
            "schema": TEMPORAL_METRICS_SCHEMA, "version": TEMPORAL_METRICS_VERSION,
            "status": "na_no_opportunities",
            "na_reason": "no ready opportunities were supplied",
            **_RELEASE,
        }

    blocked = _merge_intervals(blocked_intervals or []) if blocked_intervals else []
    for start, end in blocked:
        if end < start:
            raise ValueError("blocked intervals must have end >= start")

    timeline, diagnostics = _eligible_actions(report, action_timeout_table)

    def blocked_between(start: float, end: float) -> float:
        return sum(max(0.0, min(end, b_end) - max(start, b_start))
                   for b_start, b_end in blocked)

    def evaluable_time(t: float) -> float:
        """G_i(t): cumulative non-blocked time on [0, t]."""
        return max(0.0, t - blocked_between(0.0, t))

    def in_omega(t: float) -> bool:
        return not any(b_start <= t < b_end for b_start, b_end in blocked)

    def ready_unfinished(t: float) -> int:
        return sum(1 for row in opportunities
                   if row["ready_at_s"] <= t
                   and (row["satisfied_at_s"] is None or t < row["satisfied_at_s"]))

    def eligible_count(t: float) -> int:
        count = 0
        for entry in timeline:
            start = entry["started_at_s"]
            until = entry["eligible_until_s"]
            if start <= t and (until is None or t < until):
                count += 1
        return count

    breakpoints: set[float] = {0.0, horizon}
    for row in opportunities:
        breakpoints.add(row["ready_at_s"])
        if row["satisfied_at_s"] is not None:
            breakpoints.add(row["satisfied_at_s"])
    for entry in timeline:
        breakpoints.add(entry["started_at_s"])
        if entry["eligible_until_s"] is not None:
            breakpoints.add(min(entry["eligible_until_s"], horizon))
    breakpoints.update(t for t in q.change_times() if t <= horizon)
    for b_start, b_end in blocked:
        breakpoints.add(min(b_start, horizon))
        breakpoints.add(min(b_end, horizon))
    ordered = sorted(t for t in breakpoints if 0.0 <= t <= horizon)

    candidates: list[dict[str, Any]] = []
    current_start: float | None = None
    for point in ordered:
        conditions = (in_omega(point) and ready_unfinished(point) > 0
                      and eligible_count(point) == 0)
        if current_start is None:
            if conditions:
                current_start = point
            continue
        # A candidate is running; every continuation condition is checked at
        # the current breakpoint.  Any failure ends the maximal candidate
        # interval here, and the protocol's "end and restart timing" rule
        # immediately re-arms a fresh candidate at this breakpoint with the
        # new Q baseline Q(point) whenever the base conditions still hold
        # (e.g. after a Q jump beyond epsilon; an eligible action start or a
        # blocked-interval start keeps the base conditions false here).
        if (not conditions
                or q.at(point) - q.at(current_start) > epsilon):
            candidates.append({"start_s": current_start, "end_s": point})
            current_start = point if conditions else None
    if current_start is not None:
        candidates.append({"start_s": current_start, "end_s": horizon})

    # Viewer stage65-batch3 P0-1: a stall is confirmed the moment its
    # duration crosses delta_stall with Q held within epsilon up to that
    # crossing; later Q progress ends the candidate but never retroactively
    # cancels an already-confirmed detection.  The candidate interval itself
    # is still cut at the breakpoint for observation bookkeeping.
    confirmed: list[dict[str, Any]] = []
    for candidate in candidates:
        start, end = candidate["start_s"], candidate["end_s"]
        if end - start < delta_stall:
            continue
        crossing = start + delta_stall
        if q.at(crossing) - q.at(start) <= epsilon:
            confirmed.append({**candidate, "T_det_s": crossing})

    recovery = _merge_intervals(recovery_windows or []) if recovery_windows else []

    def first_blocked_after(t: float) -> float | None:
        for b_start, _ in blocked:
            if b_start > t:
                return b_start
        return None

    stall_rows: list[dict[str, Any]] = []
    total_stall_observed = 0.0
    union_segments: list[list[float]] = []
    n_recovered = 0
    for index, stall in enumerate(confirmed):
        t_on = stall["start_s"]
        t_det = stall["T_det_s"]
        censor = first_blocked_after(t_det)
        censor_at = horizon if censor is None else min(horizon, censor)
        t_rec: float | None = None
        for r_start, r_end in recovery:
            earliest = max(t_det, r_start + delta_clear)
            if earliest <= r_end and (t_rec is None or earliest < t_rec):
                t_rec = earliest
        row: dict[str, Any] = {
            "stall_index": index, "T_on_s": t_on, "T_det_s": t_det,
            "candidate_end_s": stall["end_s"],
            "censor_at_s": censor_at,
        }
        if t_rec is not None and t_rec < censor_at:
            row.update({"T_rec_s": t_rec, "recovery_observed": True,
                        "L_obs_s": t_rec - t_on})
            n_recovered += 1
            union_segments.append([t_on, t_rec])
        else:
            reason = ("right_censored_at_next_external_fault"
                      if censor is not None and censor <= horizon and t_rec is None
                      else "right_censored_no_recovery_predicate"
                      if t_rec is None
                      else "right_censored_fault_started_before_recovery")
            if horizon_censored and (t_rec is None or t_rec >= censor_at):
                reason = f"{reason}+{TERMINATION_UNVERIFIED}"
            row.update({"T_rec_s": None, "recovery_observed": False,
                        "L_obs_s": censor_at - t_on, "censor_reason": reason})
            union_segments.append([t_on, censor_at])
        stall_rows.append(row)
    for start, end in _merge_intervals(union_segments):
        total_stall_observed += max(0.0, end - start)

    first = confirmed[0] if confirmed else None
    if first is not None:
        tau_det = evaluable_time(first["T_det_s"])
        pair = {"tilde_tau_det_s": min(tau_det, evaluable_time(horizon)),
                "delta_i": 1}
    else:
        pair = {"tilde_tau_det_s": evaluable_time(horizon), "delta_i": 0}
    restricted = bool(stall_rows) and stall_rows[-1]["T_rec_s"] is None
    result: dict[str, Any] = {
        "schema": TEMPORAL_METRICS_SCHEMA,
        "version": TEMPORAL_METRICS_VERSION,
        "status": "computed",
        "parameters": {"delta_stall_s": delta_stall, "delta_clear_s": delta_clear,
                       "epsilon_q": epsilon},
        "horizon": {"time_s": horizon, "source": horizon_source,
                    "right_censored": horizon_censored},
        "S_i_stall_indicator": 1 if confirmed else 0,
        "StallRate": (1.0 if confirmed else 0.0),
        "StallRate_scope": "single_episode_indicator",
        "M_stall": len(confirmed),
        "StallRecoveryObserved": (_ratio(n_recovered, len(confirmed))
                                  if confirmed else None),
        "StallRecoveryObserved_note": (
            None if confirmed else "na_zero_confirmed_stalls"),
        "first_stall_right_censored_pair": pair,
        "P_T_det_le_H": (1.0 if confirmed else 0.0),
        "L_stall_obs_s": total_stall_observed,
        "L_stall_obs_restricted_at_horizon": restricted,
        "action_diagnostics": diagnostics,
        "action_timeouts": [
            {"action_id": row["action_id"], "type": row["type"],
             "started_at_s": row["started_at_s"],
             "timeout_at_s": row["eligible_until_s"]}
            for row in timeline if row["end_status"] == "action_timeout"],
        "candidate_intervals": candidates,
        "stalls": stall_rows,
        "offline_boundaries": [
            "precondition validity beyond ledger admission is not re-derived",
            "recovery predicates are caller-supplied; without them recovery "
            "is right-censored, never guessed",
            "wait-for-graph logical deadlock certificates and sham/I-timing "
            "paired branches are out of scope (shelved)",
        ],
        **_RELEASE,
    }
    return result


__all__ = [
    "ANCHOR_SOURCE_HOST_EVENT", "ANCHOR_SOURCE_NATIVE_RECEIPT",
    "ANCHOR_SOURCE_UNVERIFIED", "FORBIDDEN_TERMINATION_SOURCES",
    "NATIVE_TERMINATION_KINDS", "START_UNVERIFIED", "TERMINATION_UNVERIFIED",
    "TEMPORAL_METRICS_SCHEMA", "TEMPORAL_METRICS_VERSION",
    "coordination_stall_metrics", "frozen_time_anchors",
    "handoff_gap_metrics", "mutex_metrics", "precedence_metrics",
    "synchronization_metrics", "unnecessary_wait_metrics",
]
