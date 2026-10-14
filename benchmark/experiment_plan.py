"""Data-driven EMOS experiment matrix.

The plan describes what must be run and which evidence is admissible.  It does
not launch Isaac or silently infer a result from an assignment.  A caller can
map an :class:`ExperimentCell` to ``simulator.py``/``run_session_episode`` and
retain the resulting branch artifact under the existing benchmark stores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Iterator, Mapping

from .metrics import MetricRegistry
from .task_catalog import TaskAnchor, catalog_digest, load_default_catalog


class ExperimentPlanError(ValueError):
    """Raised when a phase or condition violates the paper matrix."""


class BranchMode(str, Enum):
    FACT = "fact"
    PAIRED = "paired"
    EVENT = "event"
    RELEASE = "release"


@dataclass(frozen=True)
class ConditionSpec:
    condition_id: str
    kind: str
    branch_mode: BranchMode
    treatment_id: str
    levels: tuple[float, ...] = ()
    branch_roles: tuple[str, ...] = ("fact",)
    checkpoint_required: bool = True
    external_evidence_required: bool = True
    strict_pair_required: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.condition_id.strip() or not self.kind.strip() or not self.treatment_id.strip():
            raise ExperimentPlanError("condition_id, kind and treatment_id are required")
        if not self.branch_roles:
            raise ExperimentPlanError(f"condition {self.condition_id} must declare branch roles")
        if len(set(self.branch_roles)) != len(self.branch_roles):
            raise ExperimentPlanError(f"condition {self.condition_id} repeats branch roles")
        legal = {"fact", "reference", "sham", "intervention"}
        if any(role not in legal for role in self.branch_roles):
            raise ExperimentPlanError(f"condition {self.condition_id} has an unknown branch role")
        if self.strict_pair_required and set(self.branch_roles) != legal:
            raise ExperimentPlanError(f"strict paired condition {self.condition_id} requires all four branches")
        if any(float(level) < 0 or float(level) > 1 for level in self.levels):
            raise ExperimentPlanError(f"condition {self.condition_id} levels must be normalized to [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "kind": self.kind,
            "branch_mode": self.branch_mode.value,
            "treatment_id": self.treatment_id,
            "levels": list(self.levels),
            "branch_roles": list(self.branch_roles),
            "checkpoint_required": self.checkpoint_required,
            "external_evidence_required": self.external_evidence_required,
            "strict_pair_required": self.strict_pair_required,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class PhaseSpec:
    phase_id: str
    ordinal: int
    dimension: str | None
    title: str
    anchor_selector: str
    conditions: tuple[ConditionSpec, ...]
    primary_metrics: tuple[str, ...]
    required_evidence: tuple[str, ...]
    release_gates: tuple[str, ...]
    notes: str = ""

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.ordinal > 7:
            raise ExperimentPlanError("phase ordinal must be in [0, 7]")
        if self.dimension is not None and self.dimension not in {f"D{i}" for i in range(1, 7)}:
            raise ExperimentPlanError(f"unknown phase dimension: {self.dimension}")
        if not self.conditions and self.ordinal < 7:
            raise ExperimentPlanError(f"phase {self.phase_id} needs at least one condition")
        if not self.release_gates:
            raise ExperimentPlanError(f"phase {self.phase_id} needs release gates")


@dataclass(frozen=True)
class ExperimentCell:
    phase_id: str
    dimension: str | None
    anchor_id: str
    condition_id: str
    repeat_id: str
    checkpoint_round: str
    branch_roles: tuple[str, ...]
    treatment_id: str
    strict_pair_required: bool
    evidence_required: tuple[str, ...]
    generator_version: str = "task-anchor.v1"
    base_seed: int = 0
    slot: int = 0
    initial_state_digest: str = ""
    untreated_history_id: str = ""

    def __post_init__(self) -> None:
        if not self.generator_version.strip():
            raise ExperimentPlanError("generator version required")
        if any(type(v) is not int or v < 0 for v in (self.base_seed, self.slot)):
            raise ExperimentPlanError("base seed and slot must be nonnegative integers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "dimension": self.dimension,
            "anchor_id": self.anchor_id,
            "condition_id": self.condition_id,
            "repeat_id": self.repeat_id,
            "checkpoint_round": self.checkpoint_round,
            "branch_roles": list(self.branch_roles),
            "treatment_id": self.treatment_id,
            "strict_pair_required": self.strict_pair_required,
            "evidence_required": list(self.evidence_required),
            "generator_version": self.generator_version,
            "base_seed": self.base_seed, "slot": self.slot,
            "initial_state_digest": self.initial_state_digest,
            "untreated_history_id": self.untreated_history_id,
        }


@dataclass(frozen=True)
class ExperimentPlan:
    catalog: tuple[TaskAnchor, ...]
    phases: tuple[PhaseSpec, ...]
    metric_registry: MetricRegistry = field(default_factory=MetricRegistry)
    tick_dt_s: float = 0.02
    repeats: int = 3
    system_id: str = "EMOS-adapted-to-EAI"
    checkpoint_round: str = "first-eligible"
    catalog_hash: str = ""

    def __post_init__(self) -> None:
        if self.tick_dt_s != 0.02:
            raise ExperimentPlanError("paper tick boundary is fixed at 0.02 seconds")
        if self.repeats != 3:
            raise ExperimentPlanError("paper preregisters K=3 repeats")
        if not self.catalog:
            raise ExperimentPlanError("experiment plan requires a task catalog")
        ids = [phase.phase_id for phase in self.phases]
        if len(ids) != len(set(ids)) or {phase.ordinal for phase in self.phases} != set(range(8)):
            raise ExperimentPlanError("plan must contain exactly phases 0 through 7")
        for phase in self.phases:
            for metric_id in phase.primary_metrics:
                self.metric_registry.get(metric_id)
        if self.catalog_hash and self.catalog_hash != catalog_digest(self.catalog):
            raise ExperimentPlanError("catalog_hash does not match the supplied catalog")
        object.__setattr__(self, "catalog_hash", catalog_digest(self.catalog))

    @classmethod
    def default(cls, catalog: Iterable[TaskAnchor] | None = None) -> "ExperimentPlan":
        entries = tuple(catalog or load_default_catalog())
        registry = MetricRegistry()
        phases = _default_phases()
        plan = cls(entries, phases, registry)
        plan.validate()
        return plan

    def validate(self) -> None:
        """Validate phase selectors and relation-specific support."""

        by_id = {item.anchor_id: item for item in self.catalog}
        for phase in self.phases:
            if phase.anchor_selector == "all":
                selected = tuple(self.catalog)
            elif phase.anchor_selector == "typical":
                selected = tuple(item for item in self.catalog if item.typical_or_common == "typical")
            elif phase.anchor_selector == "common_p2_source_target":
                selected = tuple(item for item in self.catalog if item.typical_or_common == "common" and item.common_anchor_id and "P2" in item.relation_signature)
            elif phase.anchor_selector == "relation_eligible":
                selected = tuple(self.catalog)
            elif phase.anchor_selector == "none":
                selected = ()
            else:
                raise ExperimentPlanError(f"unknown anchor selector: {phase.anchor_selector}")
            if phase.ordinal != 7 and not selected:
                raise ExperimentPlanError(f"phase {phase.phase_id} selects no anchors")
            if phase.anchor_selector == "common_p2_source_target":
                by_common: dict[str, set[str]] = {}
                for item in selected:
                    assert item.common_anchor_id is not None
                    by_common.setdefault(item.common_anchor_id, set()).add(item.domain)
                if any({"production", "service"} - domains for domains in by_common.values()):
                    raise ExperimentPlanError("D5 source-target selector requires production and service members")
            for condition in phase.conditions:
                if condition.strict_pair_required and condition.branch_roles != ("fact", "reference", "sham", "intervention"):
                    raise ExperimentPlanError(f"phase {phase.phase_id} has an incomplete strict pair")
                if phase.ordinal == 5 and not condition.strict_pair_required:
                    raise ExperimentPlanError("D5 transfer cells must require strict paired replay")
        # Every required relation panel has at least one catalog witness, even
        # when the current planned catalog is not yet physically certified.
        for metric_id in ("D3.p2_handoff_e2e", "D3.p3j_e2e", "D3.p3s_uncovered_service", "D3.p4_mutex_violation"):
            status, reason = self.metric_registry.support_status(metric_id, self.catalog)
            if status.name == "NA" and reason != "relation_support_missing":
                raise ExperimentPlanError(f"unexpected relation support state for {metric_id}: {reason}")

    def anchors_for(self, phase: PhaseSpec) -> tuple[str, ...]:
        if phase.anchor_selector == "all":
            return tuple(item.anchor_id for item in self.catalog)
        if phase.anchor_selector == "typical":
            return tuple(item.anchor_id for item in self.catalog if item.typical_or_common == "typical")
        if phase.anchor_selector == "relation_eligible":
            return tuple(item.anchor_id for item in self.catalog)
        if phase.anchor_selector == "common_p2_source_target":
            groups: dict[str, set[str]] = {}
            for item in self.catalog:
                if item.typical_or_common == "common" and item.common_anchor_id and "P2" in item.relation_signature:
                    groups.setdefault(item.common_anchor_id, set()).add(item.domain)
            return tuple(sorted(common_id for common_id, domains in groups.items() if {"production", "service"} <= domains))
        return ()

    def iter_cells(self) -> Iterator[ExperimentCell]:
        """Expand the plan into repeat-level work items without launching runs."""

        for phase in sorted(self.phases, key=lambda item: item.ordinal):
            for condition in phase.conditions:
                for anchor_id in self.anchors_for(phase):
                    for repeat in range(1, self.repeats + 1):
                        yield ExperimentCell(
                            phase.phase_id,
                            phase.dimension,
                            anchor_id,
                            condition.condition_id,
                            f"r{repeat}",
                            self.checkpoint_round,
                            condition.branch_roles,
                            condition.treatment_id,
                            condition.strict_pair_required,
                            phase.required_evidence + (("causal_replay", "sham_calibration") if condition.strict_pair_required else ()),
                        )

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "emos-experiment-plan.v1",
            "system_id": self.system_id,
            "tick_dt_s": self.tick_dt_s,
            "repeats": self.repeats,
            "checkpoint_round": self.checkpoint_round,
            "catalog_hash": self.catalog_hash,
            "anchor_count": len(self.catalog),
            "phases": [
                {
                    "phase_id": phase.phase_id,
                    "ordinal": phase.ordinal,
                    "dimension": phase.dimension,
                    "title": phase.title,
                    "anchor_selector": phase.anchor_selector,
                    "conditions": [condition.to_dict() for condition in phase.conditions],
                    "primary_metrics": list(phase.primary_metrics),
                    "required_evidence": list(phase.required_evidence),
                    "release_gates": list(phase.release_gates),
                    "notes": phase.notes,
                }
                for phase in self.phases
            ],
        }


def _condition(condition_id: str, kind: str, *, treatment_id: str = "none", levels: tuple[float, ...] = (), branch_mode: BranchMode = BranchMode.FACT, branch_roles: tuple[str, ...] = ("fact",), checkpoint_required: bool = True, external_evidence_required: bool = True, strict_pair_required: bool = False, notes: str = "") -> ConditionSpec:
    return ConditionSpec(condition_id, kind, branch_mode, treatment_id, levels, branch_roles, checkpoint_required, external_evidence_required, strict_pair_required, notes)


def _default_phases() -> tuple[PhaseSpec, ...]:
    paired = ("fact", "reference", "sham", "intervention")
    return (
        PhaseSpec("phase0_qualification", 0, None, "Interface, asset, skill and replay qualification", "all", (_condition("qualification", "qualification", branch_mode=BranchMode.FACT, external_evidence_required=False, notes="Manifest, controller digest, skill predicate and checkpoint readback."),), (), ("asset_manifest", "controller_digest", "skill_predicate", "checkpoint_readback", "sham_calibration"), ("assets_resolve", "skill_contract_pass", "physical_state_only_label_is_preserved"), "No paper endpoint is reported from qualification alone."),
        PhaseSpec("phase1_nominal_organization", 1, "D1", "Nominal task organization", "all", (_condition("nominal", "nominal"),), ("D1.initial_plan_validity", "D1.correct_infeasible_declaration"), ("algorithm_decision", "oracle_certificate", "ledger"), ("oracle_status_registered", "capability_role_milestones_audited", "unsupported_cells_marked_NA"), "D1 separates deployable, infeasible and unverified oracle states."),
        PhaseSpec("phase2_communication", 2, "D2", "Communication adaptation", "all", (_condition("nominal_control", "nominal_control", branch_mode=BranchMode.PAIRED, branch_roles=paired), _condition("delay_lambda_0.5", "delay", treatment_id="communication-delay-v1", levels=(0.5,), branch_mode=BranchMode.PAIRED, branch_roles=paired), _condition("loss_lambda_0.5", "loss", treatment_id="communication-loss-v1", levels=(0.5,), branch_mode=BranchMode.PAIRED, branch_roles=paired), _condition("jitter_lambda_0.5", "jitter", treatment_id="communication-jitter-v1", levels=(0.5,), branch_mode=BranchMode.PAIRED, branch_roles=paired), _condition("burst_lambda_0.5", "burst_loss", treatment_id="communication-burst-v1", levels=(0.5,), branch_mode=BranchMode.PAIRED, branch_roles=paired), _condition("capacity_crossing", "capacity", treatment_id="communication-capacity-crossing-v1", levels=(0.25, 0.5, 0.75, 1.0), branch_mode=BranchMode.PAIRED, branch_roles=paired)), ("D2.success_retention", "D2.jitter_penalty", "D2.burst_penalty"), ("external_milestones", "terminal_predicate", "communication_trace", "checkpoint", "ledger"), ("paired_checkpoint_digest", "sham_calibration", "stress_success_retention_registered"), "Stress retention at normalized lambda=0.5 is the headline; all registered levels remain in the release index."),
        PhaseSpec("phase3_temporal_coordination", 3, "D3", "Temporal coordination and relation endpoints", "relation_eligible", (_condition("nominal_evidence", "nominal_evidence"),), ("D3.p2_handoff_e2e", "D3.p3j_e2e", "D3.p3s_uncovered_service", "D3.p4_mutex_violation"), ("external_milestones", "terminal_predicate", "resource_trace", "coverage_trace", "ledger"), ("relation_signature_preserved", "p3_subtype_preserved", "N/A_for_empty_relation_support"), "P2 is primary; P3-J, P3-S and P4 remain relation-specific endpoints."),
        PhaseSpec("phase4_dynamic_reconfiguration", 4, "D4", "Dynamic reconfiguration and safe-stop", "all", (_condition("task_function_loss", "task_function_loss", treatment_id="task-function-loss-v2", branch_mode=BranchMode.EVENT), _condition("member_join", "member_join", treatment_id="member-join-v1", branch_mode=BranchMode.EVENT), _condition("capability_loss", "capability_loss", treatment_id="capability-loss-v1", branch_mode=BranchMode.EVENT), _condition("demand_expansion", "demand_expansion", treatment_id="demand-expansion-v1", branch_mode=BranchMode.EVENT), _condition("service_failure", "service_failure", treatment_id="service-failure-v1", branch_mode=BranchMode.EVENT), _condition("safe_stop", "safe_stop", treatment_id="safe-stop-v1", branch_mode=BranchMode.EVENT)), ("D4.recovery_cif",), ("reconfiguration_event", "external_milestones", "terminal_predicate", "safe_stop_certificate", "progress_trace", "ledger"), ("event_time_registered", "response_recovery_competing_risks", "safe_stop_counted_as_valid_evidence"), "Primary: persistent task-function loss without advance notice; onset notification follows nominal transport. Resource/notification/restoration axes are explicit event registrations. Other event types are diagnostics."),
        PhaseSpec("phase5_cross_scenario_transfer", 5, "D5", "Certified source-to-target transfer", "common_p2_source_target", (_condition("production_to_service_p2", "cross_scenario_transfer", treatment_id="transfer-p2-v1", branch_mode=BranchMode.PAIRED, branch_roles=paired, strict_pair_required=True, notes="Requires certified common-anchor pair and causal replay fidelity."),), ("D5.p2_transfer_gap",), ("fact_branch", "sham_calibration", "intervention_branch", "external_milestones", "ledger"), ("common_anchor_certified", "causal_replay", "strict_paired_status_valid"), "The current catalog is contract_only/planned, so this phase is expected to remain N/A until certification."),
        PhaseSpec("phase6_coordination_cost", 6, "D6", "Nominal coordination cost", "typical", (_condition("nominal_fact_cost", "nominal_fact_cost"),), ("D6.offered_bits",), ("communication_trace", "decision_trace", "runtime_trace", "progress_trace", "ledger"), ("fact_branch_only", "natural_units_recorded", "startup_and_UWI_retained"), "Offered bits is primary; the complete cost vector remains in every artifact."),
        PhaseSpec("phase7_release_audit", 7, None, "Release audit and inferential gates", "none", (), (), ("artifact_hash", "ledger_chain", "coverage_index", "risk_set_index", "N/A_index"), ("all_required_cells_indexed", "no_N/A_as_zero", "paired_blocks_clustered", "evidence_hashes_present"), "No new episode is launched in this phase."),
    )


def default_experiment_plan() -> ExperimentPlan:
    return ExperimentPlan.default()


__all__ = [
    "BranchMode",
    "ConditionSpec",
    "ExperimentCell",
    "ExperimentPlan",
    "ExperimentPlanError",
    "PhaseSpec",
    "default_experiment_plan",
]
