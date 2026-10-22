"""D1 first-commit evaluation and frozen feasibility-class support.

This is a deterministic consumer of independent evaluator observations, not a
planner validator or a physical oracle. Hashes bind caller-supplied inputs;
they do not certify their truth. No entry point grants publication eligibility.
"""
from dataclasses import asdict, dataclass
import math
from typing import Mapping, Sequence

from .oracle_v3 import REQUIRED_SEMANTIC_CHECKS
from .records_v3 import canonical_digest
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3
from .statistics_v3 import ClusterDesignV3, InstanceV3, quantile


CATEGORIES = REQUIRED_SEMANTIC_CHECKS
CLASSES = ("deployable", "team_state_infeasible")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty")


def _tick(value):
    if type(value) is not int or value < 0:
        raise ValueError("evaluator ticks and subevent orders must be nonnegative integers")


@dataclass(frozen=True)
class OrganizationCheckV3:
    check_id: str
    category: str
    alternatives: tuple[str, ...]

    def __post_init__(self):
        _text(self.check_id, "check_id")
        object.__setattr__(self, "alternatives", tuple(self.alternatives))
        if self.category not in CATEGORIES or not self.alternatives or len(set(self.alternatives)) != len(self.alternatives):
            raise ValueError("one registered category and unique nonempty alternatives required")
        for item in self.alternatives:
            _text(item, "alternative")


@dataclass(frozen=True)
class OrganizationInstanceV3:
    instance_id: str
    cell: str
    feasibility: str
    certificate_digest: str
    contract_digest: str
    physical_model_digest: str
    submission_deadline_tick: int
    assessment_deadline_tick: int
    checks: tuple[OrganizationCheckV3, ...] = ()
    repeat_ids: tuple[str, ...] = ("1", "2", "3")
    stream_id: str = ""

    def __post_init__(self):
        for name in ("instance_id", "cell", "certificate_digest", "contract_digest", "physical_model_digest"):
            _text(getattr(self, name), name)
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "repeat_ids", tuple(self.repeat_ids))
        if self.feasibility not in (*CLASSES, "feasibility_unverified"):
            raise ValueError("unknown oracle feasibility class")
        if len(self.repeat_ids) != 3 or len(set(self.repeat_ids)) != 3:
            raise ValueError("exactly three frozen repeat keys required")
        for repeat in self.repeat_ids:
            _text(repeat, "repeat_id")
        _tick(self.submission_deadline_tick)
        _tick(self.assessment_deadline_tick)
        if self.assessment_deadline_tick < self.submission_deadline_tick:
            raise ValueError("assessment cannot precede submission deadline")
        if len({c.check_id for c in self.checks}) != len(self.checks):
            raise ValueError("semantic check IDs cannot be duplicated")
        if self.feasibility == "deployable" and not self.checks:
            raise ValueError("deployable instances need applicable semantic checks")

    def digest(self):
        return canonical_digest(asdict(self))


@dataclass(frozen=True)
class OrganizationEventV3:
    event_id: str
    tick: int
    order: int
    kind: str
    evidence_digest: str
    plan_digest: str = ""
    parsable: bool = False
    declaration: str = "malformed"
    safe_stop_verified: bool = False

    def __post_init__(self):
        _text(self.event_id, "event_id")
        _text(self.evidence_digest, "evidence_digest")
        _tick(self.tick)
        _tick(self.order)
        if self.kind not in {"candidate", "commit", "declaration", "dispatch", "safe_stop"}:
            raise ValueError("unknown organization event")
        if type(self.parsable) is not bool or type(self.safe_stop_verified) is not bool:
            raise ValueError("event predicates must be booleans")
        if self.kind == "commit":
            _text(self.plan_digest, "first plan payload digest, even when unparsable")
        if self.declaration not in {"deployable", "not_deployable", "malformed"}:
            raise ValueError("unknown declaration")


@dataclass(frozen=True)
class OrganizationCheckObservationV3:
    check_id: str
    alternative_id: str
    passed: bool | None
    evidence_digest: str

    def __post_init__(self):
        for name in ("check_id", "alternative_id", "evidence_digest"):
            _text(getattr(self, name), name)
        if self.passed is not None and type(self.passed) is not bool:
            raise ValueError("semantic check outcome must be bool or unknown")


@dataclass(frozen=True)
class OrganizationRunV3:
    instance_id: str
    repeat_id: str
    system_id: str
    instance_digest: str
    evidence_digest: str
    infrastructure_valid: bool
    journal_complete_through_tick: int
    events: tuple[OrganizationEventV3, ...] = ()
    checks: tuple[OrganizationCheckObservationV3, ...] = ()
    checked_plan_digest: str = ""
    checked_physical_model_digest: str = ""

    def __post_init__(self):
        for name in ("instance_id", "repeat_id", "system_id", "instance_digest", "evidence_digest"):
            _text(getattr(self, name), name)
        if type(self.infrastructure_valid) is not bool:
            raise ValueError("infrastructure validity is an independent boolean")
        _tick(self.journal_complete_through_tick)
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "checks", tuple(self.checks))
        if len({e.event_id for e in self.events}) != len(self.events) or len({(e.tick, e.order) for e in self.events}) != len(self.events):
            raise ValueError("event IDs and evaluator subevent positions must be unique")
        if any(e.tick > self.journal_complete_through_tick for e in self.events):
            raise ValueError("journal event extends beyond observed prefix")
        if len({(c.check_id, c.alternative_id) for c in self.checks}) != len(self.checks):
            raise ValueError("duplicated semantic checks cannot increase score")


def evaluate_organization_run(spec: OrganizationInstanceV3, run: OrganizationRunV3):
    """Evaluate first submission and declaration in force from ordered journal.

    Missing facts in a parsable plan are UNKNOWN. An omitted assignment must be
    explicitly evaluated false; absence of an evaluator log is not a method error.
    """
    if run.instance_id != spec.instance_id or run.instance_digest != spec.digest() or run.repeat_id not in spec.repeat_ids:
        raise ValueError("run does not bind the frozen instance and repeat")
    base = {"instance_id": spec.instance_id, "repeat_id": run.repeat_id,
            "feasibility": spec.feasibility, "V": None, "Qorg": None, "I": None,
            "category_shares": {}, "first_commit_id": None}
    if spec.feasibility == "feasibility_unverified":
        return {**base, "status": "excluded", "reason": "feasibility_unverified"}
    if not run.infrastructure_valid:
        return {**base, "status": "unknown", "reason": "infrastructure_unknown"}
    if run.journal_complete_through_tick < spec.assessment_deadline_tick:
        return {**base, "status": "unknown", "reason": "incomplete_evaluator_journal"}
    events = sorted(run.events, key=lambda e: (e.tick, e.order))
    if spec.feasibility == "team_state_infeasible":
        relevant = [e for e in events if e.tick <= spec.assessment_deadline_tick]
        declarations = [e for e in relevant if e.kind == "declaration"]
        declaration = declarations[-1].declaration if declarations else None
        dispatched = any(e.kind == "dispatch" for e in relevant)
        stopped = any(e.kind == "safe_stop" and e.safe_stop_verified for e in relevant)
        return {**base, "status": "observed", "reason": None,
                "I": float(declaration == "not_deployable" and stopped and not dispatched),
                "declaration_in_force": declaration, "dispatched": dispatched}
    commits = [e for e in events if e.kind == "commit" and e.tick <= spec.submission_deadline_tick]
    first = commits[0] if commits else None
    base["first_commit_id"] = first.event_id if first else None
    shares = {g: 0. for g in CATEGORIES}
    if first is None or not first.parsable:
        return {**base, "status": "observed", "reason": "missing_or_unparsable_first_plan",
                "V": 0., "Qorg": 0., "category_shares": shares}
    if run.checked_plan_digest != first.plan_digest or run.checked_physical_model_digest != spec.physical_model_digest:
        raise ValueError("checks must bind FIRST committed plan and public physical model")
    allowed = {(c.check_id, alt) for c in spec.checks for alt in c.alternatives}
    facts = {(c.check_id, c.alternative_id): c.passed for c in run.checks}
    if not set(facts) <= allowed:
        raise ValueError("unregistered checks or legal alternatives cannot add credit")
    categories = {}
    for check in spec.checks:
        alternatives = [facts.get((check.check_id, a)) for a in check.alternatives]
        passed = True if any(v is True for v in alternatives) else None if any(v is None for v in alternatives) else False
        if passed is None:
            return {**base, "status": "unknown", "reason": "missing_independent_check_observation"}
        categories.setdefault(check.category, []).append(float(passed))
    for category, outcomes in categories.items():
        shares[category] = sum(outcomes) / len(outcomes) / len(categories)
    return {**base, "status": "observed", "reason": None,
            "V": float(all(v for outcomes in categories.values() for v in outcomes)),
            "Qorg": sum(shares.values()), "category_shares": shares}


def organization_report(instances: Sequence[OrganizationInstanceV3], runs: Sequence[OrganizationRunV3], *,
                        registered_cells: Sequence[str], minimum_instances: int = 30,
                        minimum_coverage: float = .80, alpha: float = .05,
                        seed: int = 0, resamples: int = 2000):
    """Separate dep/inf support, repeats -> instances -> equal frozen cells.

    Both infrastructure coverage and usable three-repeat support are disclosed.
    Metric missingness cannot alter the infrastructure denominator. Instances
    with residual unknowns may be excluded ONLY subject to these D1 floors.
    """
    instances, runs, cells = tuple(instances), tuple(runs), tuple(registered_cells)
    if not cells or len(set(cells)) != len(cells) or any(not isinstance(c, str) or not c for c in cells):
        raise ValueError("unique frozen nonempty cells required")
    if type(minimum_instances) is not int or minimum_instances < 30 or not math.isfinite(minimum_coverage) or not .8 <= minimum_coverage <= 1:
        raise ValueError("D1 primary support requires at least 30 instances and 80% coverage")
    if not 0 < alpha < 1 or type(resamples) is not int or resamples < 100:
        raise ValueError("valid alpha and at least 100 bootstrap resamples required")
    by_id = {s.instance_id: s for s in instances}
    if not instances or len(by_id) != len(instances) or not {s.cell for s in instances} <= set(cells):
        raise ValueError("unique frozen instances within registered cells required")
    expected = {(s.instance_id, r) for s in instances for r in s.repeat_ids}
    actual = {(r.instance_id, r.repeat_id) for r in runs}
    if actual != expected or len(actual) != len(runs) or len({r.system_id for r in runs}) != 1:
        raise ValueError("complete three-repeat roster for one system required; retain unknown rows")
    evaluated = [evaluate_organization_run(by_id[r.instance_id], r) for r in runs]
    grouped = {iid: [] for iid in by_id}
    raw_grouped = {iid: [] for iid in by_id}
    for raw, result in zip(runs, evaluated):
        raw_grouped[raw.instance_id].append(raw)
        grouped[result["instance_id"]].append(result)
    coverage, observations, measured, exclusions = {}, [], {}, []
    all_covered = True
    for feasibility in CLASSES:
        coverage[feasibility], measured[feasibility] = {}, {}
        for cell in cells:
            planned = [s for s in instances if s.cell == cell and s.feasibility == feasibility]
            infra = [s for s in planned if all(r.infrastructure_valid for r in raw_grouped[s.instance_id])]
            eligible = [s for s in planned if all(r["status"] == "observed" for r in grouped[s.instance_id])]
            fraction = len(eligible)/len(planned) if planned else 0.
            infra_fraction = len(infra)/len(planned) if planned else 0.
            passed = len(infra) >= minimum_instances and infra_fraction >= minimum_coverage and len(eligible) >= minimum_instances and fraction >= minimum_coverage
            all_covered &= passed
            coverage[feasibility][cell] = {"planned": len(planned), "infrastructure_valid": len(infra),
                "infrastructure_fraction": infra_fraction, "retained_instances": len(eligible),
                "retained_fraction": fraction, "reportable": passed}
            vectors = []
            for spec in eligible:
                rows = grouped[spec.instance_id]
                vector = tuple(sum((r[k] or 0.) for r in rows)/3 for k in ("V", "Qorg", "I"))
                vector += tuple(sum(r["category_shares"].get(g, 0.) for r in rows)/3 for g in CATEGORIES)
                vectors.append(vector)
                stratum = canonical_digest([feasibility, cell])
                observations.append(InstanceV3(spec.instance_id, stratum, vector, spec.stream_id))
            measured[feasibility][cell] = (dict(zip(("V", "Qorg", "I", *CATEGORIES),
                (sum(v[j] for v in vectors)/len(vectors) for j in range(3+len(CATEGORIES))))) if vectors else None)
    for spec in instances:
        rows = grouped[spec.instance_id]
        if any(r["status"] != "observed" for r in rows):
            exclusions.append({"instance_id": spec.instance_id, "feasibility": spec.feasibility,
                "reasons": sorted({r["reason"] for r in rows if r["status"] != "observed"})})
    components = {}
    for feasibility, keys in ((CLASSES[0], ("V", "Qorg", *CATEGORIES)), (CLASSES[1], ("I",))):
        for key in keys:
            components[key] = (sum(measured[feasibility][c][key] for c in cells)/len(cells)
                if all(measured[feasibility][c] is not None for c in cells) else None)
    result = {"score_id": "D1", "evaluation_protocol": "first_commit_frozen_checks_v3", "status": "na", "point": None,
        "reason": "coverage_insufficient", "coverage": coverage, "components": components,
        "measured_cells": measured, "exclusion_funnel": exclusions,
        "evaluated_runs": evaluated, "registered_cells": cells,
        "minimum_instances": minimum_instances, "minimum_coverage": minimum_coverage,
        "scheduled_runs": len(runs), "system_id": runs[0].system_id,
        "paper_commit": PAPER_COMMIT, "scorebook_digest": default_scorebook_v3().digest(),
        "input_digest": canonical_digest({"instances": [asdict(s) for s in instances],
            "runs": [asdict(r) for r in runs], "cells": cells,
            "minimum_instances": minimum_instances, "minimum_coverage": minimum_coverage}),
        "physical_qualified": False, "publication_ready": False,
        "evidence_status": "caller_supplied_evaluator_observations_not_artifact_verified",
        "inference": {"alpha": alpha, "seed": seed, "resamples": resamples,
            "quantile_rule": "empirical_inverse_cdf", "strata": "cell_and_feasibility_class"}}
    result["category_points"] = {g: None if components[g] is None else 30*components[g] for g in CATEGORIES}
    if not all_covered:
        return result
    design = ClusterDesignV3(observations, {canonical_digest([f, c]): 1/(2*len(cells)) for f in CLASSES for c in cells})
    def score(v):
        # Half of design weight belongs to each feasibility class. Undo that
        # normalization, preserving fixed 80/20 score coefficients.
        return 2*(50*v[0]+30*v[1]+20*v[2])
    point = score(design.means())
    samples = [score(v) for v in design.draws(seed=seed, resamples=resamples)]
    result.update(point=point, identification=[point, point])
    if min(samples) == max(samples):
        return {**result, "status": "unstable", "reason": "degenerate_cluster_distribution", "interval": None}
    interval = {"point": point, "lower": quantile(samples, alpha/2),
        "upper": quantile(samples, 1-alpha/2), "method": "stratified_cluster_percentile",
        "alpha": alpha, "seed": seed, "resamples": resamples}
    return {**result, "status": "valid", "reason": None,
            "interval": [interval["lower"], interval["upper"]], "endpoints": [interval, interval]}


def organization_report_from_payload(payload: Mapping):
    """JSON round-trip boundary used by the shared analysis CLI."""
    instances = [OrganizationInstanceV3(**{**s, "checks": tuple(OrganizationCheckV3(**c) for c in s.get("checks", ()))}) for s in payload["instances"]]
    runs = [OrganizationRunV3(**{**r,
        "events": tuple(OrganizationEventV3(**e) for e in r.get("events", ())),
        "checks": tuple(OrganizationCheckObservationV3(**c) for c in r.get("checks", ()))}) for r in payload["runs"]]
    return organization_report(instances, runs, registered_cells=payload["registered_cells"], **payload.get("inference", {}))


def organization_support_errors(report: Mapping, *, minimum_instances=30, minimum_coverage=.8):
    """Formal v1 support gate. This checks support, not artifact authenticity."""
    errors = []
    if report.get("evaluation_protocol") != "first_commit_frozen_checks_v3":
        return ["d1_first_plan_evaluator_report_required"]
    cells = report.get("registered_cells", ())
    if len(cells) != 48 or len(set(cells)) != 48:
        errors.append("d1_v1_requires_48_frozen_cells")
    if report.get("status") != "valid":
        errors.append("d1_interval_not_valid")
    floor, fraction = report.get("minimum_instances"), report.get("minimum_coverage")
    if (type(floor) is not int or floor < max(30, minimum_instances)
            or not isinstance(fraction, (int, float)) or not math.isfinite(fraction)
            or not max(.8, minimum_coverage) <= fraction <= 1):
        return errors + ["d1_support_policy_below_registration"]
    coverage = report.get("coverage", {})
    if set(coverage) != set(CLASSES) or any(set(coverage[f]) != set(cells) for f in CLASSES):
        return errors + ["d1_class_cell_support_missing"]
    for feasibility in CLASSES:
        for cell in cells:
            row = coverage[feasibility][cell]
            planned, infra, retained = (row.get(k) for k in ("planned", "infrastructure_valid", "retained_instances"))
            if (any(type(n) is not int for n in (planned, infra, retained))
                    or not 0 < retained <= infra <= planned or retained < floor
                    or infra/planned < fraction or retained/planned < fraction):
                errors.append("d1_class_cell_coverage_insufficient")
                return errors
    return errors
