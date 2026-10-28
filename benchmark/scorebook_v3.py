"""Versioned scorebook for the revised HeteroCoord paper protocol.

This module is deliberately independent from :mod:`benchmark.metrics` and
``MetricReducer``.  Those APIs describe legacy diagnostic endpoints and must
not silently change meaning when the paper's composite D1--D6 estimands are
implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Any, Mapping


SCOREBOOK_VERSION = "heterocoord.scorebook.v3"
# The scorebook is bound to the current Overleaf export.  A stale commit here
# makes every registration artifact look internally consistent while actually
# describing an older estimand, so this identity is intentionally explicit.
PAPER_COMMIT = "0ffd470fecdf5b4bdad82dbd9c23c439198b43d3"


class ScorebookError(ValueError):
    """Raised when a scorebook entry is malformed."""


@dataclass(frozen=True)
class ScoreSpecV3:
    score_id: str
    dimension: str
    title: str
    formula: str
    unit: str
    direction: str
    support_selector: str
    aggregation: str
    condition_rule: str
    branch_rule: str
    checkpoint_rule: str
    required_evidence: tuple[str, ...]
    missingness: tuple[str, ...]
    estimator_family: str
    dependencies: tuple[str, ...]
    ranking_gate: str
    release_gate: str
    golden_cases: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.score_id not in {"D1", "D2", "D3", "D4", "D5", "D6"}:
            raise ScorebookError(f"unsupported score_id: {self.score_id}")
        if self.dimension != self.score_id:
            raise ScorebookError("dimension must equal score_id for a composite score")
        for name in ("title", "formula", "unit", "direction", "support_selector", "aggregation", "condition_rule", "branch_rule", "checkpoint_rule", "estimator_family", "ranking_gate", "release_gate"):
            if not str(getattr(self, name)).strip():
                raise ScorebookError(f"{name} is required for {self.score_id}")
        if self.direction not in {"up", "down", "bounded"}:
            raise ScorebookError(f"invalid direction for {self.score_id}")
        if not self.required_evidence or not self.golden_cases:
            raise ScorebookError(f"{self.score_id} needs evidence and golden cases")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("required_evidence", "missingness", "dependencies", "golden_cases"):
            value[key] = list(value[key])
        return value


@dataclass(frozen=True)
class ScorebookV3:
    version: str
    paper_commit: str
    specs: tuple[ScoreSpecV3, ...]
    unresolved_parameters: tuple[str, ...]
    legacy_endpoint_map: Mapping[str, tuple[str, ...]]
    clause_matrix: tuple[Mapping[str, str], ...]

    def __post_init__(self) -> None:
        if self.version != SCOREBOOK_VERSION:
            raise ScorebookError(f"unexpected scorebook version: {self.version}")
        ids = tuple(item.score_id for item in self.specs)
        if ids != ("D1", "D2", "D3", "D4", "D5", "D6"):
            raise ScorebookError("scorebook must contain exactly D1 through D6 in order")
        if not self.unresolved_parameters:
            raise ScorebookError("unresolved parameters must remain explicit until frozen")
        if len(self.clause_matrix) < 6:
            raise ScorebookError("clause matrix must cover all six dimensions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "paper_commit": self.paper_commit,
            "specs": [item.to_dict() for item in self.specs],
            "unresolved_parameters": list(self.unresolved_parameters),
            "legacy_endpoint_map": {key: list(value) for key, value in sorted(self.legacy_endpoint_map.items())},
            "clause_matrix": [dict(item) for item in self.clause_matrix],
        }

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, score_id: str) -> ScoreSpecV3:
        for spec in self.specs:
            if spec.score_id == score_id:
                return spec
        raise ScorebookError(f"unknown composite score: {score_id}")


def _spec(score_id: str, title: str, formula: str, *, support: str, aggregation: str, condition: str, branch: str, checkpoint: str, evidence: tuple[str, ...], estimator: str, dependencies: tuple[str, ...], golden: tuple[str, ...], direction: str = "up") -> ScoreSpecV3:
    return ScoreSpecV3(
        score_id=score_id,
        dimension=score_id,
        title=title,
        formula=formula,
        unit="0--100 points",
        direction=direction,
        support_selector=support,
        aggregation=aggregation,
        condition_rule=condition,
        branch_rule=branch,
        checkpoint_rule=checkpoint,
        required_evidence=evidence,
        missingness=("method_failure_is_observed", "infrastructure_unknown_is_not_method_failure", "fixed_directory_required", "do_not_silently_drop_weight",
                    "unresolved_fixed_catalog_bounds" if score_id in {"D5", "D6"}
                    else "retain_three_valid_repeats_subject_to_frozen_class_cell_coverage" if score_id == "D1"
                    else "residual_unknown_fixed_weight_identification_range"),
        estimator_family=estimator,
        dependencies=dependencies,
        ranking_gate="score_is_computable_and_support_is_complete",
        release_gate="protocol_identity_and_evidence_hashes_verified",
        golden_cases=golden,
    )


def default_scorebook_v3() -> ScorebookV3:
    """Return the frozen structural scorebook with explicit open parameters."""

    specs = (
        _spec("D1", "Initial task organization", "100*(0.50*V + 0.30*Qorg + 0.20*I)", support="same 48 cells for deployable and certified-infeasible tracks", aggregation="category equal-weight; base-instance cluster bootstrap", condition="nominal first-plan submission", branch="fact plus certified infeasible declaration track", checkpoint="task-start; first plan immutable", evidence=("initial_plan", "semantic_checks", "oracle_certificate", "safe_stop"), estimator="cluster_bootstrap", dependencies=("canonical_oracle",), golden=("D1_all_valid_100", "D1_all_safe_reject_20_component_bound", "D1_no_complete_plan_at_most_50_component_bound")),
        _spec("D2", "Communication adaptation", "(100/3)*sum_l=1..3 min(1,p_l/p_0)", support="fixed directory with C0,C1,C2,C3 and three repeats per instance", aggregation="repeat -> instance -> fixed cell, then capped retention", condition="C0 nominal; C1--C3 registered shared-capacity conditions", branch="same task-start checkpoint; registered fact/reference controls", checkpoint="identical task-start checkpoint per capacity", evidence=("physical_outcome", "shared_channel_trace", "delivery_trace", "checkpoint"), estimator="d2_finite_sample_paired_retention", dependencies=("shared_channel", "serialization_contract"), golden=("D2_retention_75", "D2_zero_nominal_na")),
        _spec("D3", "Temporal and relation coordination", "(100/3)*(mu2 + (muJ+muS)/2 + mu4)", support="complete relation signatures across domain-difficulty-condition cells; relation cells exclude primary label", aggregation="obligation -> repeat -> instance -> fixed cell; nominal primary", condition="nominal primary; stress/availability separate panels", branch="fact evidence with infrastructure unknown preserved", checkpoint="relation-specific registered opportunity", evidence=("external_milestones", "resource_trace", "coverage_trace", "ledger"), estimator="base_instance_cluster_bootstrap", dependencies=("obligation_evaluator",), golden=("D3_relation_weights", "D3_unreached_zero")),
        _spec("D4", "Work preservation after function loss", "100*HierMean(q_j)", support="frozen affected-work closure J; verified method failures retained", aggregation="equal item -> repeat -> instance -> fixed cell", condition="unannounced persistent registered function loss", branch="loss and healthy branches for every system from shared calibration checkpoint", checkpoint="validated common pre-event checkpoint plus public history and registered initialization", evidence=("event_trace", "remaining_feasibility", "physical_outcome", "progress_trace", "common_checkpoint", "public_history", "initialization_routine"), estimator="base_instance_cluster_bootstrap", dependencies=("physical_operation_adapter", "causal_checkpoint"), golden=("D4_deadline_time_value", "D4_competing_work_in_J")),
        _spec("D5", "Cross-scenario adaptation", "100*mean_family(mean_case(mean_unit(mean_3_repeat(Y))))", support="certified families with at least 30 cases and target adaptation units", aggregation="repeat -> unit -> case -> family equal-weight", condition="shared certified source reference; no target demonstrations or parameter updates", branch="with/without source-reference paired gain; source success does not gate", checkpoint="unit-specific target checkpoint", evidence=("source_reference_hash", "target_checkpoint", "joint_acceptance", "causal_replay", "sham"), estimator="fixed_directory_weighted_hoeffding", dependencies=("common_anchor_certificate", "causal_replay", "sham_calibration"), golden=("D5_joint_unit_acceptance", "D5_source_not_gate")),
        _spec("D6", "Success-gated coordination efficiency", "100*HierMean(Y*sqrt(B0/max(B0,B)*T0/max(T0,T)))", support="fixed nominal directory with independent calibration reference table", aggregation="repeat -> instance -> fixed cell; fixed reference medians", condition="nominal physical execution", branch="fact candidate; calibration is independent", checkpoint="registered task-start and terminal evidence", evidence=("success", "actual_transmitted_bits", "capped_sim_time", "calibration_record"), estimator="fixed_directory_weighted_hoeffding", dependencies=("shared_channel", "calibration_coordinator"), golden=("D6_failure_zero", "D6_double_cost_50")),
    )
    legacy = {
        "D1": ("D1.initial_plan_validity", "D1.correct_infeasible_declaration", "D1.capability_validity", "D1.role_validity", "D1.milestone_validity"),
        "D2": ("D2.nominal_success", "D2.stress_success", "D2.success_retention", "D2.capacity_penalty"),
        "D3": ("D3.p2_handoff_e2e", "D3.p3j_e2e", "D3.p3s_uncovered_service", "D3.p4_mutex_violation"),
        "D4": ("D4.response_cif", "D4.recovery_cif", "D4.failure_cif", "D4.affected_work_fact"),
        "D5": ("D5.source_success", "D5.target_success", "D5.p2_transfer_gap"),
        "D6": ("D6.offered_bits", "D6.link_bits", "D6.execution_time", "D6.uwi"),
    }
    # Explicit current-paper clauses are part of the digest, not prose added
    # beside an otherwise stale registration identity.
    clauses = {
        "D1": "first_committed_timely_parseable_plan;seven_applicable_check_categories;both_feasibility_supports;three_valid_repeats;coverage_floor_no_reweighting",
        "D2": "cluster_percentile_headline;zero_reference_fraction_above_alpha_over_two_unstable;simultaneous_retention_bands_only_for_operating_boundary",
        "D3": "registered_relation_support;relation_cells_exclude_primary_label;empty_required_support_not_endpoint_fill",
        "D4": "untreated_frozen_closure;healthy_loss_pair;same_checkpoint_random_stream_retry_only;fresh_process_new_instance;registered_value_density_and_stability_window",
        "D5": "fixed_catalog;exclude_and_disclose_below_floor_families;equal_eligible_family_case_unit_repeat;source_gain_reported_not_release_gate",
        "D6": "independent_calibration_medians;fixed_catalog_hoeffding;resource_claim_separate_pareto_and_success_progress_noninferiority_gate",
    }
    from dataclasses import replace
    specs = tuple(replace(spec,
        estimator_family="base_instance_cluster_percentile" if spec.score_id == "D2" else spec.estimator_family,
        support_selector="explicit registered relation partition; relation cells exclude primary label" if spec.score_id == "D3" else spec.support_selector,
        branch_rule="shared source reference; paired gain reported separately, not a primary-score release gate" if spec.score_id == "D5" else spec.branch_rule,
    ) for spec in specs)
    matrix = tuple(
        {"score_id": spec.score_id, "formula": spec.formula, "fields": ",".join(spec.required_evidence), "tests": ",".join(spec.golden_cases), "status": "structural_contract", "current_paper_clauses": clauses[spec.score_id]}
        for spec in specs
    )
    return ScorebookV3(
        version=SCOREBOOK_VERSION,
        paper_commit=PAPER_COMMIT,
        specs=specs,
        unresolved_parameters=("C0-C3 numeric rates and queue limits", "D1 certified support directory", "D3 service sampling and event ordering", "D4 common checkpoints/public histories and time-value parameters", "D5 family/case/unit directory and replay provider", "D6 independent calibration batches and coordinator"),
        legacy_endpoint_map=legacy,
        clause_matrix=matrix,
    )


__all__ = ["PAPER_COMMIT", "SCOREBOOK_VERSION", "ScoreSpecV3", "ScorebookError", "ScorebookV3", "default_scorebook_v3"]
