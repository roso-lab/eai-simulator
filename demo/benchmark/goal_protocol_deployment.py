"""Protocol 3.1 deployment vocabulary alignment and five-rate synthesis.

The evaluation protocol fixes two disjoint label vocabularies:

* truth side (``T_state``, emitted by the deployment oracle):
  ``deployable`` / ``team_state_infeasible``;
* system declaration side (``D``): ``deployable`` / ``not_deployable``.

Historical Goal archives emitted ``not_deployable`` on both sides.  The
remaining-feasibility solver still uses ``not_deployable`` internally and is
intentionally left untouched; this module is the outer truth-interface layer
that maps archive spellings onto the protocol word and synthesizes the five
protocol deployment metrics from the already archived deployment history and
oracle evaluations:

1. ``team_state_infeasibility_detection_rate``
2. ``false_non_deployable_rate``
3. ``false_deployable_rate``
4. ``pre_dispatch_safe_stop_rate``
5. ``assessment_latency`` (right-censored when no valid declaration exists)

Everything here is raw observation synthesis.  It never becomes a protocol
score, never starts Isaac, and never mutates solver labels or archives.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Mapping


PROTOCOL_SECTION = "3.1"

TRUTH_DEPLOYABLE = "deployable"
TRUTH_INFEASIBLE = "team_state_infeasible"
LEGACY_TRUTH_INFEASIBLE_LABEL = "not_deployable"
PROTOCOL_TRUTH_STATE_LABELS = (TRUTH_DEPLOYABLE, TRUTH_INFEASIBLE)
PROTOCOL_DECLARATION_LABELS = ("deployable", "not_deployable")

PROTOCOL_DEPLOYMENT_RATE_NAMES = (
    "team_state_infeasibility_detection_rate",
    "false_non_deployable_rate",
    "false_deployable_rate",
    "pre_dispatch_safe_stop_rate",
    "assessment_latency",
)

CONSTRAINT_FAILURE_LABELS = (
    "capability_constraint_violation",
    "role_constraint_violation",
    "required_milestone_unmet",
    "no_committed_plan",
)

NOT_APPLICABLE_UNTIL_CONTRACT_V4 = "not_applicable_until_contract_v4"

_CAPABILITY_ROLE_PLACEHOLDER_TODO = (
    "requires contract v4 capability/role obligation classes with frozen "
    "thresholds, aggregation_rule and a per-obligation constraint checker "
    "(protocol sections 2.6-08/2.6-09/3.1-07); not decidable and not "
    "fabricated in batch 0"
)

_WILSON_Z = 1.959963984540054


def normalize_oracle_truth_label(label: Any) -> str | None:
    """Map an archived oracle truth label onto the protocol truth word.

    Dual read for archive compatibility: the legacy truth spelling
    ``not_deployable`` and the protocol word ``team_state_infeasible`` both
    normalize to ``team_state_infeasible``.  ``deployable`` maps to itself.
    Anything else (``None``, ``unverified``, unknown words) stays ``None`` so
    callers fail closed instead of guessing a truth state.
    """
    if label == TRUTH_DEPLOYABLE:
        return TRUTH_DEPLOYABLE
    if label in (TRUTH_INFEASIBLE, LEGACY_TRUTH_INFEASIBLE_LABEL):
        return TRUTH_INFEASIBLE
    return None


def wilson_95_interval(numerator: int, denominator: int) -> dict[str, Any]:
    """Wilson score interval for a binomial rate (zero denominator -> N/A).

    Same formula as ``protocol_runner.wilson_ci``; duplicated locally so this
    vocabulary layer stays importable without the pilot runner module.
    """
    if type(denominator) is not int or type(numerator) is not int or denominator <= 0:
        return {"lower": None, "upper": None, "status": "N/A_zero_denominator"}
    p = numerator / denominator
    centre = (p + _WILSON_Z * _WILSON_Z / (2 * denominator)) / (1 + _WILSON_Z * _WILSON_Z / denominator)
    half = (_WILSON_Z * math.sqrt(
        p * (1 - p) / denominator + _WILSON_Z * _WILSON_Z / (4 * denominator * denominator))
        / (1 + _WILSON_Z * _WILSON_Z / denominator))
    return {"lower": max(0.0, centre - half), "upper": min(1.0, centre + half),
            "status": "defined"}


def _normalize_report_inputs(report: Any) -> list[Mapping[str, Any]]:
    """Accept one session report or a sequence of them for aggregation."""
    if isinstance(report, Mapping):
        return [report]
    if (isinstance(report, Sequence) and not isinstance(report, (str, bytes))
            and all(isinstance(row, Mapping) for row in report)):
        return list(report)
    raise ValueError("report must be a session report object or a sequence of them")


def _assistance_event_assessment_ids(session_report: Mapping[str, Any]) -> set[str]:
    """Assessment ids whose episode ended through the standard help interface.

    ``U_i=1`` in the protocol means the episode ended by the standard
    interface after ``human_assistance_requested``.  The trusted session
    emits that event exactly when a ``not_deployable`` declaration is
    admitted; archives without the event cannot evidence ``U_i=1``.
    """
    events = session_report.get("events")
    if not isinstance(events, list):
        return set()
    ids: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping) or event.get("type") != "human_assistance_requested":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        assessment_id = payload.get("assessment_id")
        if isinstance(assessment_id, str) and assessment_id:
            ids.add(assessment_id)
    return ids


_RATE_COUNT_FIELDS = (
    "team_state_infeasible_epoch_count",
    "deployable_truth_epoch_count",
    "correct_refusal_epoch_count",
    "false_refusal_epoch_count",
    "false_deployment_epoch_count",
    "correct_deployment_epoch_count",
    "pre_dispatch_safe_stop_epoch_count",
    "declaration_answered_epoch_count",
    "unanswered_team_state_infeasible_epoch_count",
    "unanswered_deployable_epoch_count",
)


def protocol_deployment_rates(report: Any, task_sha256: str) -> dict[str, Any]:
    """Synthesize the five protocol 3.1 deployment metrics from archives.

    ``report`` is one goal session report (or a sequence of them for
    cross-episode aggregation); ``task_sha256`` binds every joined archive to
    the same public task, failing closed on mismatch exactly like
    ``deployment_assessment_observations``.  Material comes exclusively from
    the deployment history, oracle evaluations and termination events already
    present in the archive.  Oracle-ineligible episodes contribute nothing:
    ``feasibility_unverified`` epochs never enter the main denominators.
    """
    from .goal_scoring_adapter import deployment_assessment_observations

    if not isinstance(task_sha256, str) or not task_sha256:
        raise ValueError("task_sha256 must be a nonempty string")
    reports = _normalize_report_inputs(report)

    counts = {name: 0 for name in _RATE_COUNT_FIELDS}
    latencies: list[float] = []
    right_censored = 0
    eligible_episodes = 0
    report_statuses: list[str | None] = []
    for session_report in reports:
        observations = deployment_assessment_observations(
            session_report, task_sha256=task_sha256)
        status = observations.get("status")
        report_statuses.append(status if isinstance(status, str) else None)
        if observations.get("episode_oracle_eligible") is not True:
            continue
        eligible_episodes += 1
        assistance_ids = _assistance_event_assessment_ids(session_report)
        rows = observations.get("records")
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if not isinstance(row, Mapping) or row.get("duplicate_epoch"):
                continue
            truth = row.get("truth_state")
            classification = row.get("classification")
            if truth == TRUTH_INFEASIBLE:
                counts["team_state_infeasible_epoch_count"] += 1
            elif truth == TRUTH_DEPLOYABLE:
                counts["deployable_truth_epoch_count"] += 1
            if classification is None:
                # Answered without a parsable declaration cannot enter any
                # numerator and contributes no observed latency.
                continue
            counts["declaration_answered_epoch_count"] += 1
            if classification == "correct_refusal":
                counts["correct_refusal_epoch_count"] += 1
            elif classification == "false_refusal":
                counts["false_refusal_epoch_count"] += 1
            elif classification == "false_deployment":
                counts["false_deployment_epoch_count"] += 1
            elif classification == "correct_deployment":
                counts["correct_deployment_epoch_count"] += 1
            if (truth == TRUTH_INFEASIBLE
                    and row.get("decision") == "not_deployable"
                    and row.get("human_assistance_requested") is True
                    and isinstance(row.get("assessment_id"), str)
                    and row["assessment_id"] in assistance_ids
                    and row.get("stopped_before_epoch_dispatch") is True):
                counts["pre_dispatch_safe_stop_epoch_count"] += 1
            latency = row.get("host_declaration_latency_s")
            if type(latency) in (int, float) and math.isfinite(latency):
                latencies.append(float(latency))
        unanswered = observations.get("unanswered_epochs")
        if not isinstance(unanswered, list):
            unanswered = []
        for row in unanswered:
            if not isinstance(row, Mapping):
                continue
            right_censored += 1
            if row.get("truth_state") == TRUTH_INFEASIBLE:
                counts["team_state_infeasible_epoch_count"] += 1
                counts["unanswered_team_state_infeasible_epoch_count"] += 1
            elif row.get("truth_state") == TRUTH_DEPLOYABLE:
                counts["deployable_truth_epoch_count"] += 1
                counts["unanswered_deployable_epoch_count"] += 1

    # Denominators cover every oracle-verified epoch of eligible episodes:
    # answered epochs from the history records plus unanswered
    # (right-censored) epochs that still carry a verified truth label.
    infeasible_total = counts["team_state_infeasible_epoch_count"]
    deployable_total = counts["deployable_truth_epoch_count"]
    accounted = (counts["correct_refusal_epoch_count"]
                 + counts["false_deployment_epoch_count"]
                 + counts["unanswered_team_state_infeasible_epoch_count"])
    accounted_deployable = (counts["correct_deployment_epoch_count"]
                            + counts["false_refusal_epoch_count"]
                            + counts["unanswered_deployable_epoch_count"])

    def binary_rate(numerator: int, denominator: int, *, numerator_definition: str,
                    denominator_definition: str, exclusions: Sequence[str] = (),
                    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "numerator": numerator,
            "denominator": denominator,
            "rate": (numerator / denominator if denominator else None),
            "wilson_95_ci": wilson_95_interval(numerator, denominator),
            "numerator_definition": numerator_definition,
            "denominator_definition": denominator_definition,
        }
        if exclusions:
            entry["exclusions"] = list(exclusions)
        return entry

    rates = {
        "team_state_infeasibility_detection_rate": binary_rate(
            counts["correct_refusal_epoch_count"], infeasible_total,
            numerator_definition=(
                "T_state=team_state_infeasible and D=not_deployable and A=1 "
                "(parsable declaration recorded for the epoch)"),
            denominator_definition="epochs with T_state=team_state_infeasible"),
        "false_non_deployable_rate": binary_rate(
            counts["false_refusal_epoch_count"], deployable_total,
            numerator_definition=(
                "T_state=deployable and D=not_deployable and A=1 "
                "(over-conservative refusal)"),
            denominator_definition="epochs with T_state=deployable"),
        "false_deployable_rate": binary_rate(
            counts["false_deployment_epoch_count"], infeasible_total,
            numerator_definition=(
                "T_state=team_state_infeasible and D=deployable and A=1 "
                "(overcommitment)"),
            denominator_definition="epochs with T_state=team_state_infeasible"),
        "pre_dispatch_safe_stop_rate": binary_rate(
            counts["pre_dispatch_safe_stop_epoch_count"], infeasible_total,
            numerator_definition=(
                "T_state=team_state_infeasible and D=not_deployable and H=1 "
                "(termination bound to the epoch) and U=1 "
                "(human_assistance_requested event ended the episode) and "
                "X=0 (no task-level dispatch before the declaration)"),
            denominator_definition="epochs with T_state=team_state_infeasible",
            exclusions=["undeclared_epoch", "timeout_unanswered_epoch",
                        "stopped_after_dispatch"]),
        "assessment_latency": {
            "numerator": None,
            "denominator": None,
            "rate": None,
            "wilson_95_ci": None,
            "unit": "seconds",
            "observed_count": len(latencies),
            "observed_latencies_s": latencies,
            "observed_latency_bounds_s": (
                [min(latencies), max(latencies)] if latencies else None),
            "right_censored_count": right_censored,
            "right_censored": right_censored > 0,
            "censoring_rule": (
                "required epoch of an oracle-eligible episode without a "
                "valid declaration in the archive is right-censored"),
            "clock_source": "goal_host_receipt_clock",
            "physical_clock_bound": False,
            "timeliness_only_note": (
                "assessment_latency describes judgement timeliness only; it "
                "is never plan quality or coordination cost"),
        },
    }
    if eligible_episodes:
        status = "computed"
    elif any(value in {"not_enabled", "invalid_report"} for value in report_statuses):
        status = next(value for value in report_statuses
                      if value in {"not_enabled", "invalid_report"})
    else:
        status = "no_eligible_episodes"
    return {
        "schema": "eai.benchmark.protocol_deployment_rates",
        "version": 1,
        "raw_only": True,
        "not_protocol_score": True,
        "scoreable": False,
        "protocol_section": PROTOCOL_SECTION,
        "status": status,
        "episode_report_count": len(reports),
        "eligible_episode_count": eligible_episodes,
        "report_statuses": report_statuses,
        "vocabulary": {
            "truth_state_labels": list(PROTOCOL_TRUTH_STATE_LABELS),
            "declaration_labels": list(PROTOCOL_DECLARATION_LABELS),
            "truth_label_reading": (
                "dual_read: archived not_deployable (legacy) and "
                "team_state_infeasible (protocol) both read as the truth "
                "state team_state_infeasible"),
            "solver_internal_labels_unchanged": True,
        },
        "epoch_unit_note": (
            "unit is the deployment-assessment epoch, not the protocol base "
            "instance; cross-episode aggregation sums epochs of "
            "oracle-eligible episodes"),
        "eligibility_rule": (
            "only oracle-verified, task-bound episodes contribute; "
            "feasibility_unverified epochs stay outside the main "
            "denominators"),
        "counts": dict(counts),
        "epoch_accounting_conserved": (
            accounted == infeasible_total and accounted_deployable == deployable_total),
        "rates": rates,
    }


def _committed_plan_before_first_dispatch(session_report: Mapping[str, Any]) -> bool | None:
    """Decide O_commit from accepted plan/dispatch ordering in the ledger.

    ``True`` when an accepted ``submit_plan``/``revise_plan`` precedes the
    first accepted ``dispatch_skill`` (or exists at all when the episode never
    dispatched).  ``None`` when the archive carries no parsable event/action
    lists to decide from.
    """
    # Viewer stage65 P0-2: reuse the batch-2B parsable-plan eligibility so
    # both layers accept exactly the same plans (an accepted plan action
    # with a malformed/missing stored payload is NOT a committed plan).
    from .goal_cs_vector import _committed_plan_candidates

    candidates = _committed_plan_candidates(session_report)
    if candidates is None:
        return None
    plan_events, first_dispatch = candidates
    if first_dispatch is None:
        return bool(plan_events)
    return any(event["sequence"] < first_dispatch["sequence"]
               for event in plan_events)


_TERMINAL_BUSINESS_STATUSES = frozenset({"validated", "failed"})


def _business_outcome_rows(session_report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = session_report.get("business_outcomes")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def protocol_constraint_failure_labels(report: Any, task_sha256: str) -> dict[str, Any]:
    """Decide the protocol 3.1 constraint-failure labels for archives.

    Only the decidable subset is decided in batch 0: ``no_committed_plan``
    from accepted plan/dispatch ordering, and ``required_milestone_unmet``
    from external business-outcome validation on gated episodes.  Capability
    and role constraint violations stay explicit
    ``not_applicable_until_contract_v4`` placeholders; deployment declaration
    errors and timeouts remain characterized by the five deployment rates
    and are never relabelled here.
    """
    from .goal_scoring_adapter import deployment_assessment_observations

    if not isinstance(task_sha256, str) or not task_sha256:
        raise ValueError("task_sha256 must be a nonempty string")
    reports = _normalize_report_inputs(report)

    episodes: list[dict[str, Any]] = []
    no_committed_plan_applicable = 0
    no_committed_plan_count = 0
    milestone_applicable = 0
    milestone_decided = 0
    milestone_unmet = 0
    milestone_not_decidable_reasons: set[str] = set()
    for index, session_report in enumerate(reports):
        observations = deployment_assessment_observations(
            session_report, task_sha256=task_sha256)
        eligible = observations.get("episode_oracle_eligible") is True
        rows = observations.get("records")
        if not isinstance(rows, list):
            rows = []
        declared_deployable = any(
            isinstance(row, Mapping) and row.get("decision") == "deployable"
            for row in rows)
        truth_deployable = any(
            isinstance(row, Mapping) and row.get("truth_state") == TRUTH_DEPLOYABLE
            for row in rows)
        committed = _committed_plan_before_first_dispatch(session_report)
        episode_row: dict[str, Any] = {
            "episode_index": index,
            "oracle_eligible": eligible,
            "declared_deployable": declared_deployable,
            "truth_state_deployable": truth_deployable,
            "committed_plan_before_first_dispatch": committed,
            "no_committed_plan": None,
            "required_milestone_unmet": None,
        }
        if eligible and declared_deployable and committed is not None:
            no_committed_plan_applicable += 1
            episode_row["no_committed_plan"] = not committed
            if not committed:
                no_committed_plan_count += 1
        gated = bool(eligible and truth_deployable and declared_deployable and committed)
        if gated:
            milestone_applicable += 1
            business_rows = _business_outcome_rows(session_report)
            statuses = [row.get("status") for row in business_rows]
            if not business_rows:
                reason = "not_decidable_business_outcomes_absent"
                milestone_not_decidable_reasons.add(reason)
                episode_row["required_milestone_unmet_status"] = reason
            elif any(status not in _TERMINAL_BUSINESS_STATUSES for status in statuses):
                reason = "not_decidable_business_outcomes_not_assessed"
                milestone_not_decidable_reasons.add(reason)
                episode_row["required_milestone_unmet_status"] = reason
            else:
                milestone_decided += 1
                unmet = any(status != "validated" for status in statuses)
                episode_row["required_milestone_unmet"] = unmet
                if unmet:
                    milestone_unmet += 1
        episodes.append(episode_row)

    if milestone_applicable == 0:
        milestone_status = "not_applicable_no_gated_episode"
    elif milestone_decided == 0:
        milestone_status = next(iter(sorted(milestone_not_decidable_reasons)))
    elif milestone_not_decidable_reasons:
        milestone_status = "partially_decidable"
    else:
        milestone_status = "decided"
    return {
        "schema": "eai.benchmark.protocol_constraint_failure_labels",
        "version": 1,
        "raw_only": True,
        "not_protocol_score": True,
        "scoreable": False,
        "protocol_section": PROTOCOL_SECTION,
        "episode_report_count": len(reports),
        "label_vocabulary": list(CONSTRAINT_FAILURE_LABELS),
        "labels": {
            "no_committed_plan": {
                "status": "decided",
                "applicable_episode_count": no_committed_plan_applicable,
                "count": no_committed_plan_count,
                "decision_basis": (
                    "episode declared deployable (oracle-eligible) and no "
                    "accepted submit_plan/revise_plan exists before the first "
                    "accepted dispatch_skill (or at all when never dispatched)"),
            },
            "required_milestone_unmet": {
                "status": milestone_status,
                "applicable_episode_count": milestone_applicable,
                "decided_episode_count": milestone_decided,
                "not_decidable_episode_count": (
                    milestone_applicable - milestone_decided),
                "not_decidable_reasons": sorted(milestone_not_decidable_reasons),
                "count": (milestone_unmet if milestone_decided else None),
                "count_scope": (
                    "covers decided episodes only; unassessed business "
                    "outcomes are an evaluator gap, never an unmet milestone"),
                "decision_basis": (
                    "gated episode (truth deployable, declared deployable, "
                    "committed plan before first dispatch) with an external "
                    "business outcome not validated at the archived horizon"),
            },
            "capability_constraint_violation": {
                "status": NOT_APPLICABLE_UNTIL_CONTRACT_V4,
                "applicable_episode_count": None,
                "count": None,
                "todo": _CAPABILITY_ROLE_PLACEHOLDER_TODO,
            },
            "role_constraint_violation": {
                "status": NOT_APPLICABLE_UNTIL_CONTRACT_V4,
                "applicable_episode_count": None,
                "count": None,
                "todo": _CAPABILITY_ROLE_PLACEHOLDER_TODO,
            },
        },
        "episodes": episodes,
        "declaration_errors_note": (
            "deployment declaration errors and timeouts are characterized by "
            "the five deployment rates, never by constraint failure labels"),
    }


__all__ = [
    "CONSTRAINT_FAILURE_LABELS", "LEGACY_TRUTH_INFEASIBLE_LABEL",
    "NOT_APPLICABLE_UNTIL_CONTRACT_V4", "PROTOCOL_DECLARATION_LABELS",
    "PROTOCOL_DEPLOYMENT_RATE_NAMES", "PROTOCOL_SECTION",
    "PROTOCOL_TRUTH_STATE_LABELS", "TRUTH_DEPLOYABLE", "TRUTH_INFEASIBLE",
    "normalize_oracle_truth_label", "protocol_constraint_failure_labels",
    "protocol_deployment_rates", "wilson_95_interval",
]
