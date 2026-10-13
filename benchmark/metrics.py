"""Registered D1--D6 endpoints for the EMOS paper experiment.

This module freezes endpoint identity and missingness semantics.  It does not
estimate confidence intervals or turn an algorithm assignment into physical
success; those operations belong to a later result-reduction layer once the
external evidence is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Iterable, Mapping

from .task_catalog import TaskAnchor


class MetricError(ValueError):
    """Raised when an endpoint declaration or observation is malformed."""


class MetricStatus(str, Enum):
    VALID = "valid"
    NA = "N/A"
    INVALID = "invalid"
    PENDING = "pending"


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    dimension: str
    label: str
    direction: str
    unit: str
    primary: bool
    support: str
    numerator: str
    denominator: str
    risk_set: str
    required_evidence: tuple[str, ...]
    na_reasons: tuple[str, ...]
    relation: str | None = None
    condition: str = "nominal"
    factors: tuple[str, ...] = ()
    denominator_floor: int = 1

    def __post_init__(self) -> None:
        if self.dimension not in {f"D{i}" for i in range(1, 7)}:
            raise MetricError(f"unknown metric dimension: {self.dimension}")
        if self.direction not in {"up", "down"}:
            raise MetricError("direction must be up or down")
        if not self.metric_id.startswith(self.dimension + "."):
            raise MetricError(f"metric_id {self.metric_id} must be prefixed by {self.dimension}.")
        if not self.required_evidence or not self.na_reasons:
            raise MetricError(f"{self.metric_id} must declare evidence and N/A reasons")
        if self.denominator_floor < 1:
            raise MetricError("denominator_floor must be at least one")


@dataclass(frozen=True)
class MetricObservation:
    """One reduction-ready endpoint record with explicit support status."""

    metric_id: str
    status: MetricStatus
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    support_count: int = 0
    invalid_count: int = 0
    na_reason: str | None = None
    evidence_hash: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.support_count < 0 or self.invalid_count < 0:
            raise MetricError("support_count and invalid_count cannot be negative")
        if self.status is MetricStatus.VALID:
            if self.value is None or not math.isfinite(float(self.value)):
                raise MetricError("valid observations require a finite value")
            if self.denominator is None or float(self.denominator) <= 0:
                raise MetricError("valid observations require a positive denominator")
            if self.na_reason is not None:
                raise MetricError("valid observations cannot carry na_reason")
        elif self.status is MetricStatus.NA:
            if not self.na_reason:
                raise MetricError("N/A observations require a reason")
            if self.value is not None:
                raise MetricError("N/A observations cannot carry a numeric value")
        elif self.value is not None and not math.isfinite(float(self.value)):
            raise MetricError("numeric metric values must be finite")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "status": self.status.value,
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "support_count": self.support_count,
            "invalid_count": self.invalid_count,
            "na_reason": self.na_reason,
            "evidence_hash": self.evidence_hash,
            "metadata": dict(self.metadata),
        }


def _spec(
    metric_id: str,
    dimension: str,
    label: str,
    *,
    direction: str,
    unit: str,
    primary: bool,
    support: str,
    numerator: str,
    denominator: str,
    risk_set: str,
    required_evidence: tuple[str, ...],
    na_reasons: tuple[str, ...],
    relation: str | None = None,
    condition: str = "nominal",
    factors: tuple[str, ...] = (),
    denominator_floor: int = 1,
) -> MetricSpec:
    declared_na_reasons = tuple(dict.fromkeys((*na_reasons, "denominator_below_floor")))
    return MetricSpec(
        metric_id,
        dimension,
        label,
        direction,
        unit,
        primary,
        support,
        numerator,
        denominator,
        risk_set,
        required_evidence,
        declared_na_reasons,
        relation,
        condition,
        factors,
        denominator_floor,
    )


_EVIDENCE_PLAN = ("algorithm_decision", "oracle_certificate", "ledger")
_EVIDENCE_PHYSICAL = ("external_milestones", "terminal_predicate", "ledger")
_EVIDENCE_PAIRED = ("fact_branch", "sham_calibration", "intervention_branch", "ledger")
_NA_BASE = ("empty_support", "infrastructure_invalid", "evidence_missing")


def metric_registry() -> tuple[MetricSpec, ...]:
    """Return the preregistered endpoint universe in paper table order."""

    specs: list[MetricSpec] = [
        _spec("D1.initial_plan_validity", "D1", "Initial plan validity", direction="up", unit="proportion", primary=True, support="typical oracle-deployable", numerator="initially valid plans", denominator="oracle-deployable typical repeats", risk_set="infrastructure-valid typical repeats", required_evidence=_EVIDENCE_PLAN, na_reasons=_NA_BASE + ("oracle_unverified",)),
        _spec("D1.correct_infeasible_declaration", "D1", "Correct infeasible declaration", direction="up", unit="proportion", primary=False, support="track-level oracle-infeasible", numerator="correct infeasible declarations", denominator="certified infeasible repeats", risk_set="oracle-infeasible repeats", required_evidence=_EVIDENCE_PLAN, na_reasons=_NA_BASE + ("oracle_unverified",)),
        _spec("D1.capability_validity", "D1", "Capability validity", direction="up", unit="proportion", primary=False, support="initially valid plans", numerator="capability-valid plans", denominator="initially valid plans", risk_set="valid-plan repeats", required_evidence=_EVIDENCE_PLAN, na_reasons=_NA_BASE),
        _spec("D1.role_validity", "D1", "Role validity", direction="up", unit="proportion", primary=False, support="initially valid plans", numerator="role-valid plans", denominator="initially valid plans", risk_set="valid-plan repeats", required_evidence=_EVIDENCE_PLAN, na_reasons=_NA_BASE),
        _spec("D1.milestone_validity", "D1", "Milestone validity", direction="up", unit="proportion", primary=False, support="initially valid plans", numerator="milestone-valid plans", denominator="initially valid plans", risk_set="valid-plan repeats", required_evidence=_EVIDENCE_PLAN, na_reasons=_NA_BASE),
        _spec("D2.nominal_success", "D2", "Nominal success", direction="up", unit="proportion", primary=False, support="paired nominal controls", numerator="nominal mission successes", denominator="paired nominal repeats", risk_set="fact/reference paired repeats", required_evidence=_EVIDENCE_PHYSICAL + ("checkpoint",), na_reasons=_NA_BASE + ("paired_support_missing",), condition="nominal"),
        _spec("D2.stress_success", "D2", "Stress success", direction="up", unit="proportion", primary=False, support="registered communication stress", numerator="stress mission successes", denominator="stress repeats", risk_set="infrastructure-valid stress repeats", required_evidence=_EVIDENCE_PHYSICAL + ("communication_trace",), na_reasons=_NA_BASE, condition="stress"),
        _spec("D2.success_retention", "D2", "Success retention at delay lambda=0.5", direction="up", unit="ratio", primary=True, support="paired nominal/stress controls", numerator="stress success rate", denominator="nominal success rate", risk_set="matched base-instance stress pairs", required_evidence=_EVIDENCE_PHYSICAL + ("communication_trace", "checkpoint"), na_reasons=_NA_BASE + ("paired_support_missing",), condition="delay_lambda_0.5", factors=("delay",)),
        _spec("D2.jitter_penalty", "D2", "Jitter penalty lambda=0.5", direction="down", unit="percentage_points", primary=False, support="paired jitter bracket", numerator="nominal success minus jitter success", denominator="paired jitter support", risk_set="matched jitter pairs", required_evidence=_EVIDENCE_PHYSICAL + ("communication_trace",), na_reasons=_NA_BASE + ("paired_support_missing",), condition="jitter_lambda_0.5", factors=("jitter",)),
        _spec("D2.burst_penalty", "D2", "Burst loss penalty lambda=0.5", direction="down", unit="percentage_points", primary=False, support="paired burst bracket", numerator="nominal success minus burst success", denominator="paired burst support", risk_set="matched burst pairs", required_evidence=_EVIDENCE_PHYSICAL + ("communication_trace",), na_reasons=_NA_BASE + ("paired_support_missing",), condition="burst_lambda_0.5", factors=("burst_loss",)),
        _spec("D2.capacity_penalty", "D2", "Capacity crossing penalty", direction="down", unit="percentage_points", primary=False, support="paired capacity bracket", numerator="nominal success minus capacity-stress success", denominator="paired capacity support", risk_set="matched capacity pairs", required_evidence=_EVIDENCE_PHYSICAL + ("communication_trace",), na_reasons=_NA_BASE + ("paired_support_missing",), condition="capacity_crossing", factors=("capacity",)),
        _spec("D3.p2_handoff_e2e", "D3", "P2 handoff end-to-end satisfaction", direction="up", unit="proportion", primary=True, support="relation-eligible P2 repeats", numerator="P2 precedence/release/handoff/accept complete", denominator="P2 relation-eligible repeats", risk_set="P2 opportunities with valid external evidence", required_evidence=_EVIDENCE_PHYSICAL, na_reasons=_NA_BASE + ("relation_support_missing",), relation="P2"),
        _spec("D3.p2_handoff_opportunity", "D3", "P2 handoff opportunity", direction="up", unit="proportion", primary=False, support="relation-eligible P2 repeats", numerator="P2 opportunities observed", denominator="P2 relation-eligible repeats", risk_set="P2 relation-eligible repeats", required_evidence=("external_milestones", "ledger"), na_reasons=_NA_BASE + ("relation_support_missing",), relation="P2"),
        _spec("D3.p2_handoff_conditional", "D3", "P2 conditional handoff satisfaction", direction="up", unit="proportion", primary=False, support="P2 opportunities", numerator="completed P2 handoffs", denominator="P2 opportunities", risk_set="P2 opportunities", required_evidence=_EVIDENCE_PHYSICAL, na_reasons=_NA_BASE + ("relation_support_missing",), relation="P2"),
        _spec("D3.p2_handoff_gap", "D3", "P2 handoff gap", direction="down", unit="seconds", primary=False, support="complete P2 handoffs", numerator="accept tick minus release tick", denominator="complete P2 handoffs", risk_set="complete P2 handoffs", required_evidence=("external_milestones", "ledger"), na_reasons=_NA_BASE + ("relation_support_missing",), relation="P2"),
        _spec("D3.p3j_e2e", "D3", "P3-J coupled-action end-to-end satisfaction", direction="up", unit="proportion", primary=False, support="relation-eligible P3-J repeats", numerator="P3-J synchronized endpoints complete", denominator="P3-J relation-eligible repeats", risk_set="P3-J opportunities with external evidence", required_evidence=_EVIDENCE_PHYSICAL, na_reasons=_NA_BASE + ("relation_support_missing",), relation="P3-J"),
        _spec("D3.p3s_uncovered_service", "D3", "P3-S uncovered service", direction="down", unit="fraction_time", primary=False, support="relation-eligible P3-S repeats", numerator="uncovered service time", denominator="required service time", risk_set="P3-S service intervals with valid clocks", required_evidence=("external_milestones", "coverage_trace", "ledger"), na_reasons=_NA_BASE + ("relation_support_missing",), relation="P3-S"),
        _spec("D3.p4_mutex_violation", "D3", "P4 mutex violation", direction="down", unit="proportion", primary=False, support="relation-eligible P4 repeats", numerator="shared-resource overlap events", denominator="P4 resource-opportunity events", risk_set="P4 repeats with resource trace", required_evidence=("external_milestones", "resource_trace", "ledger"), na_reasons=_NA_BASE + ("relation_support_missing",), relation="P4"),
        _spec("D3.p4_certified_stall", "D3", "P4 certified stall", direction="down", unit="proportion", primary=False, support="relation-eligible P4 repeats", numerator="certified P4 stalls", denominator="P4 resource-opportunity events", risk_set="P4 repeats with wait-for evidence", required_evidence=("external_milestones", "resource_trace", "ledger"), na_reasons=_NA_BASE + ("relation_support_missing",), relation="P4"),
        _spec("D4.response_cif", "D4", "Response CIF at member exit", direction="up", unit="cumulative_incidence", primary=False, support="member-exit event risk set", numerator="response events by exit horizon", denominator="affected member-exit repeats", risk_set="event-eligible exit repeats", required_evidence=("reconfiguration_event", "external_milestones", "ledger"), na_reasons=_NA_BASE + ("event_support_missing",), condition="member_exit"),
        _spec("D4.recovery_cif", "D4", "Recovery CIF at member exit", direction="up", unit="cumulative_incidence", primary=True, support="member-exit event risk set", numerator="resume events by exit horizon", denominator="affected member-exit repeats", risk_set="event-eligible exit repeats", required_evidence=("reconfiguration_event", "external_milestones", "ledger"), na_reasons=_NA_BASE + ("event_support_missing",), condition="member_exit"),
        _spec("D4.failure_cif", "D4", "Recovery failure CIF", direction="down", unit="cumulative_incidence", primary=False, support="member-exit event risk set", numerator="terminal failures by exit horizon", denominator="affected member-exit repeats", risk_set="event-eligible exit repeats", required_evidence=("reconfiguration_event", "external_milestones", "terminal_predicate", "ledger"), na_reasons=_NA_BASE + ("event_support_missing",), condition="member_exit"),
        _spec("D4.safe_stop_cif", "D4", "Certified safe-stop CIF", direction="down", unit="cumulative_incidence", primary=False, support="member-exit event risk set", numerator="certified safe-stops by exit horizon", denominator="affected member-exit repeats", risk_set="event-eligible exit repeats", required_evidence=("reconfiguration_event", "safe_stop_certificate", "ledger"), na_reasons=_NA_BASE + ("event_support_missing",), condition="member_exit"),
        _spec("D4.affected_work_fact", "D4", "Affected work fraction, fact branch", direction="down", unit="proportion", primary=False, support="member-exit fact branch", numerator="affected fact work", denominator="fact work at event", risk_set="event-eligible fact repeats", required_evidence=("reconfiguration_event", "progress_trace", "ledger"), na_reasons=_NA_BASE + ("event_support_missing",), condition="member_exit"),
        _spec("D4.affected_work_healthy", "D4", "Affected work fraction, healthy branch", direction="down", unit="proportion", primary=False, support="member-exit healthy branch", numerator="affected healthy work", denominator="healthy work at event", risk_set="event-eligible healthy repeats", required_evidence=("reconfiguration_event", "progress_trace", "ledger"), na_reasons=_NA_BASE + ("event_support_missing",), condition="member_exit"),
        _spec("D5.source_success", "D5", "Certified source success", direction="up", unit="proportion", primary=False, support="certified common source pairs", numerator="source successes", denominator="certified source pair repeats", risk_set="strict paired source repeats", required_evidence=_EVIDENCE_PAIRED, na_reasons=_NA_BASE + ("common_anchor_uncertified", "strict_pair_unsupported"), condition="production_to_service"),
        _spec("D5.target_success", "D5", "Certified target success", direction="up", unit="proportion", primary=False, support="certified common target pairs", numerator="target successes", denominator="certified target pair repeats", risk_set="strict paired target repeats", required_evidence=_EVIDENCE_PAIRED, na_reasons=_NA_BASE + ("common_anchor_uncertified", "strict_pair_unsupported"), condition="production_to_service"),
        _spec("D5.p2_transfer_gap", "D5", "Production to service P2 success gap", direction="up", unit="percentage_points", primary=True, support="certified common P2 pairs", numerator="target success minus source success", denominator="certified P2 pair repeats", risk_set="strict paired production-to-service P2 repeats", required_evidence=_EVIDENCE_PAIRED, na_reasons=_NA_BASE + ("common_anchor_uncertified", "strict_pair_unsupported"), relation="P2", condition="production_to_service"),
    ]
    for relation in ("P1", "P2", "P3-J", "P3-S", "P4"):
        specs.append(_spec(
            f"D5.transfer_gap.{relation}", "D5", f"Certified {relation} transfer gap", direction="up", unit="percentage_points", primary=False, support=f"certified common {relation} pairs", numerator="target success minus source success", denominator=f"certified {relation} pair repeats", risk_set=f"strict paired {relation} source-target repeats", required_evidence=_EVIDENCE_PAIRED, na_reasons=_NA_BASE + ("common_anchor_uncertified", "strict_pair_unsupported"), relation=relation, condition="certified_source_to_target",
        ))
    specs.extend([
        _spec("D6.offered_bits", "D6", "Offered communication volume", direction="down", unit="kbit_per_episode", primary=True, support="typical nominal fact branch", numerator="offered bytes", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("communication_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.link_bits", "D6", "Link communication volume", direction="down", unit="kbit_per_episode", primary=False, support="typical nominal fact branch", numerator="link bytes", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("communication_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.interactions", "D6", "Interaction count", direction="down", unit="per_episode", primary=False, support="typical nominal fact branch", numerator="typed interactions", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("communication_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.commands", "D6", "Command count", direction="down", unit="per_episode", primary=False, support="typical nominal fact branch", numerator="typed commands", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("communication_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.reallocations", "D6", "Reallocation count", direction="down", unit="per_episode", primary=False, support="typical nominal fact branch", numerator="reallocation events", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("decision_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.replans", "D6", "Replan count", direction="down", unit="per_episode", primary=False, support="typical nominal fact branch", numerator="replan events", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("decision_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.execution_time", "D6", "Execution time", direction="down", unit="seconds_per_episode", primary=False, support="typical nominal fact branch", numerator="episode execution seconds", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("runtime_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.startup_time", "D6", "Launch-to-readiness time", direction="down", unit="seconds_per_episode", primary=False, support="typical nominal fact branch", numerator="startup seconds", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("runtime_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
        _spec("D6.uwi", "D6", "Uncompleted-work integral", direction="down", unit="work_seconds", primary=False, support="typical nominal fact branch", numerator="uncompleted work over time", denominator="fact episodes", risk_set="infrastructure-valid nominal fact episodes", required_evidence=("progress_trace", "ledger"), na_reasons=_NA_BASE + ("cost_trace_missing",)),
    ])
    return tuple(specs)


class MetricRegistry:
    """Lookup and support checks for the fixed endpoint universe."""

    def __init__(self, specs: Iterable[MetricSpec] | None = None):
        self._specs = tuple(specs or metric_registry())
        ids = [item.metric_id for item in self._specs]
        if len(ids) != len(set(ids)):
            raise MetricError("metric_id values must be unique")
        self._by_id = {item.metric_id: item for item in self._specs}

    @property
    def specs(self) -> tuple[MetricSpec, ...]:
        return self._specs

    def get(self, metric_id: str) -> MetricSpec:
        try:
            return self._by_id[metric_id]
        except KeyError as exc:
            raise MetricError(f"unknown metric_id: {metric_id}") from exc

    def for_dimension(self, dimension: str) -> tuple[MetricSpec, ...]:
        return tuple(item for item in self._specs if item.dimension == dimension)

    def relation_support(self, spec_or_id: MetricSpec | str, catalog: Iterable[TaskAnchor]) -> int:
        spec = self.get(spec_or_id) if isinstance(spec_or_id, str) else spec_or_id
        if spec.relation is None:
            return sum(1 for _ in catalog)
        count = 0
        for anchor in catalog:
            relation = spec.relation
            if relation in {"P3-J", "P3-S"}:
                if "P3" in anchor.relation_signature and anchor.p3_subtype == relation:
                    count += 1
            elif relation in anchor.relation_signature:
                count += 1
        return count

    def certified_common_support(self, relation: str, catalog: Iterable[TaskAnchor]) -> int:
        return sum(
            1
            for anchor in catalog
            if anchor.typical_or_common == "common"
            and anchor.certification_status == "certified"
            and ((relation in {"P3-J", "P3-S"} and anchor.p3_subtype == relation) or (relation in anchor.relation_signature))
        )

    def support_status(self, spec_or_id: MetricSpec | str, catalog: Iterable[TaskAnchor]) -> tuple[MetricStatus, str]:
        spec = self.get(spec_or_id) if isinstance(spec_or_id, str) else spec_or_id
        anchors = tuple(catalog)
        if spec.dimension == "D5":
            if spec.relation is None:
                return MetricStatus.NA, "relation_support_missing"
            if self.certified_common_support(spec.relation, anchors) == 0:
                return MetricStatus.NA, "common_anchor_uncertified"
            return MetricStatus.PENDING, "strict_pair_replay_and_sham_calibration_required"
        if spec.relation is not None and self.relation_support(spec, anchors) == 0:
            return MetricStatus.NA, "relation_support_missing"
        if not anchors:
            return MetricStatus.NA, "empty_support"
        return MetricStatus.PENDING, "runtime_evidence_required"

    def make_observation(self, metric_id: str, *, status: MetricStatus, value: float | None = None, numerator: float | None = None, denominator: float | None = None, support_count: int = 0, invalid_count: int = 0, na_reason: str | None = None, evidence_hash: str = "", metadata: Mapping[str, Any] | None = None) -> MetricObservation:
        spec = self.get(metric_id)
        if status is MetricStatus.NA and na_reason not in spec.na_reasons:
            raise MetricError(f"{na_reason!r} is not a registered N/A reason for {metric_id}")
        if status is MetricStatus.VALID and (denominator is None or float(denominator) < spec.denominator_floor):
            raise MetricError(f"{metric_id} denominator is below the registered floor {spec.denominator_floor}")
        return MetricObservation(metric_id, status, value, numerator, denominator, support_count, invalid_count, na_reason, evidence_hash, metadata or {})


__all__ = ["MetricError", "MetricObservation", "MetricRegistry", "MetricSpec", "MetricStatus", "metric_registry"]
