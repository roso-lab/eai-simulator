"""D6 natural-unit saving claims, separate from the success-gated score.

This evaluates an already computed, registered simultaneous interval family.
It does not estimate intervals or certify their physical provenance.
Differences are candidate minus reference: lower cost is better, while higher
success and fixed-horizon progress are better.
"""
from dataclasses import dataclass, asdict
import math
from typing import Mapping

from .records_v3 import canonical_digest
from .scorebook_v3 import PAPER_COMMIT
from .statistics_v3 import IntervalV3


COST_UNITS = {
    "offered_bits": "bit", "link_bits": "bit",
    "interactions": "count", "commands": "count",
    "reallocations": "count", "replans": "count",
    "execution_time": "second",
}


@dataclass(frozen=True)
class ResourceClaimRegistrationV3:
    candidate: str
    reference: str
    support_digest: str
    costs: tuple[str, ...]
    success_margin: float
    progress_margin: float
    alpha: float = .05

    def __post_init__(self):
        object.__setattr__(self, "costs", tuple(self.costs))
        if not all(isinstance(v, str) and v for v in (self.candidate, self.reference, self.support_digest)) or self.candidate == self.reference:
            raise ValueError("distinct systems and frozen identical support required")
        if not self.costs or len(set(self.costs)) != len(self.costs) or not set(self.costs) <= COST_UNITS.keys():
            raise ValueError("unique registered natural-unit cost components required")
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (self.success_margin, self.progress_margin)) or not 0 < self.alpha < 1:
            raise ValueError("normalized outcome margins and valid alpha required")

    def digest(self):
        return canonical_digest({"paper_commit": PAPER_COMMIT, **asdict(self)})


def assess_resource_claim(registration: ResourceClaimRegistrationV3, *,
                          costs: Mapping[str, IntervalV3],
                          success: IntervalV3 | None,
                          progress: IntervalV3 | None,
                          support_digest: str,
                          registration_digest: str,
                          evidence_digest: str):
    """Fail closed on missing/mixed support or non-simultaneous intervals.

    Bonferroni allocation covers every selected cost plus both outcomes.
    Each interval must already have alpha <= registered alpha / family size.
    A passed numerical gate still needs artifact/physical release verification.
    """
    provenance = {"paper_commit": PAPER_COMMIT, "registration_digest": registration.digest(),
                  "support_digest": registration.support_digest, "evidence_digest": evidence_digest,
                  "physical_qualified": False}
    def result(status, reason, **details):
        return {**provenance, "status": status, "numerical_gate_passed": status == "pass",
                "publication_ready": False,
                "evidence_status": "caller_supplied_intervals_not_artifact_verified",
                "reason": reason, **details}
    if support_digest != registration.support_digest or registration_digest != registration.digest() or not evidence_digest:
        return result("na", "claim_identity_missing_or_mismatched")
    if set(costs) != set(registration.costs) or success is None or progress is None:
        return result("na", "complete_cost_success_progress_family_required")
    intervals = (*costs.values(), success, progress)
    if any(not isinstance(i, IntervalV3) for i in intervals):
        raise ValueError("computed intervals required")
    allocated_alpha = registration.alpha / len(intervals)
    if any(i.alpha > allocated_alpha + 1e-15 for i in intervals):
        return result("na", "simultaneous_interval_family_not_established")
    if any(not -1 <= i.lower <= i.point <= i.upper <= 1 for i in (success, progress)):
        raise ValueError("success and progress differences use normalized natural units")
    if any(not i.lower <= i.point <= i.upper for i in costs.values()):
        raise ValueError("cost difference point must lie inside its interval")
    ni = {"success": success.lower >= -registration.success_margin,
          "progress": progress.lower >= -registration.progress_margin}
    no_worse = {name: interval.upper <= 0 for name, interval in costs.items()}
    better = [name for name, interval in costs.items() if interval.upper < 0]
    details = {"noninferiority": ni, "cost_no_worse": no_worse,
               "strictly_better_costs": better, "allocated_alpha": allocated_alpha,
               "units": {name: COST_UNITS[name] for name in registration.costs}}
    if not all(ni.values()):
        return result("fail", "outcome_noninferiority_not_established", **details)
    if not all(no_worse.values()) or not better:
        return result("fail", "pareto_saving_not_established", **details)
    return result("pass", "pareto_saving_with_outcome_noninferiority", **details)
