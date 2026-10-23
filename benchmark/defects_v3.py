"""Planted defects live in the submitted brain, never in fleet/evaluator.

Hooks can be integrated by a registered implementation. This module alone
does not establish that any physical benchmark has the predicted effect.
"""
from dataclasses import dataclass
from copy import deepcopy
import math
from typing import Any, Callable, Mapping, Sequence
from .statistics_v3 import IntervalV3


@dataclass(frozen=True)
class StateMessageV3:
    message_id: str
    sent_tick: int
    valid_until_tick: int
    payload: Mapping[str, Any]

    def __post_init__(self):
        if not self.message_id or type(self.sent_tick) is not int or type(self.valid_until_tick) is not int or not 0 <= self.sent_tick <= self.valid_until_tick:
            raise ValueError("message identity and validity interval required")


class DefectModulesV3:
    """The same policy owns send/decision logic; only a module gate differs.

    Original/sham reject expired received state. stale_message ignores expiry.
    no_reallocation freezes the existing assignment after first loss, but the
    policy still handles observations and actions on the retained assignment.
    Caller must invoke reset for each independent run/checkpoint restore.
    """
    def __init__(self, variant="original"):
        if variant not in {"original", "stale_message", "no_reallocation", "sham"}:
            raise ValueError("unregistered planted defect")
        self.variant = variant
        self.reset()

    def reset(self):
        self.loss_seen = False
        self.frozen_assignment = None
        self.audit = []

    def consume(self, messages: Sequence[StateMessageV3], *, tick: int):
        if type(tick) is not int or tick < 0 or len({m.message_id for m in messages}) != len(messages):
            raise ValueError("valid tick and unique received message identities required")
        if any(m.sent_tick > tick for m in messages):
            raise ValueError("cannot consume messages before they were sent")
        accepted = tuple(m for m in messages if self.variant == "stale_message" or tick <= m.valid_until_tick)
        self.audit.extend({"message_id": m.message_id, "consume_tick": tick, "expired": tick > m.valid_until_tick} for m in accepted)
        return accepted

    def reassign(self, *, function_loss: bool, current_assignment: Mapping, propose: Callable[[], Mapping]):
        if type(function_loss) is not bool:
            raise ValueError("loss notification must be boolean")
        if function_loss and not self.loss_seen:
            self.frozen_assignment = deepcopy(dict(current_assignment))
            self.loss_seen = True
        if self.variant == "no_reallocation" and self.loss_seen:
            self.audit.append({"reallocation_suppressed": True})
            return deepcopy(self.frozen_assignment)
        return propose()


@dataclass(frozen=True)
class AttributionRegistrationV3:
    implementation_digest: str
    development_split_digest: str
    heldout_split_digest: str
    nominal_success_margin: float
    score_margins: Mapping[str, float]
    family_id: str

    def __post_init__(self):
        if not all((self.implementation_digest, self.development_split_digest, self.heldout_split_digest, self.family_id)) or self.development_split_digest == self.heldout_split_digest:
            raise ValueError("registered implementation and disjoint held-out split required")
        if set(self.score_margins) != {f"D{i}" for i in range(1,7)} or any(not math.isfinite(v) or v < 0 for v in self.score_margins.values()) or not math.isfinite(self.nominal_success_margin) or self.nominal_success_margin < 0:
            raise ValueError("nominal and six score equivalence margins must be preregistered")


def attribution_status(registration: AttributionRegistrationV3, *, variant: str, differences: Mapping[str, IntervalV3], nominal_success: IntervalV3, interaction: IntervalV3 | None, restored_target: IntervalV3 | None, external_condition_seen: bool, nominal_unsaturated: bool, nominal_decisions_identical: bool, nominal_sent_messages_identical: bool):
    """Report three separate checks. No system ranking/unique-cause claim.
    Difference = variant minus original. Restoration = restored minus original
    on held-out support. Supplied intervals must come from the registered
    multiplicity family, checked separately before a confirmatory claim.
    """
    target = {"stale_message": "D2", "no_reallocation": "D4", "sham": None}.get(variant)
    if variant not in {"stale_message", "no_reallocation", "sham"} or set(differences) != set(registration.score_margins):
        raise ValueError("all six score differences required for registered variant")
    equivalent = lambda interval, margin: interval.lower >= -margin and interval.upper <= margin
    nominal = equivalent(nominal_success, registration.nominal_success_margin)
    controls = nominal_decisions_identical and nominal_sent_messages_identical
    if variant == "stale_message": controls = controls and nominal_unsaturated
    pattern = {score: interval.upper < 0 if score == target else equivalent(interval, registration.score_margins[score]) for score,interval in differences.items()}
    observed = bool(external_condition_seen) if target else True
    interaction_pass = interaction is not None and interaction.upper < 0 if target else None
    restored = equivalent(restored_target, registration.score_margins[target]) if target and restored_target is not None else None
    return {"variant": variant, "nominal_equivalent": nominal, "nominal_controls_pass": controls, "pattern_checks": pattern, "external_condition_recorded": observed, "factor_interaction_supported": interaction_pass, "heldout_restoration_equivalent": restored, "pattern_supported": nominal and controls and all(pattern.values()), "all_checks_supported": nominal and controls and all(pattern.values()) and observed and (not target or bool(interaction_pass and restored)), "family_id": registration.family_id, "multiplicity_validated": False, "physical_attribution_qualified": False, "ranks_systems": False}


def attribution_holm(pvalues: Mapping[str, float], *, registered_hypotheses: Sequence[str], alpha=.05):
    """Separate preregistered defect family, never mixed into primary six."""
    if not registered_hypotheses or len(set(registered_hypotheses)) != len(registered_hypotheses) or set(pvalues) != set(registered_hypotheses) or not 0 < alpha < 1 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in pvalues.values()):
        raise ValueError("defect p-values must match the frozen attribution family")
    result, previous = {}, 0.
    for i,(key,p) in enumerate(sorted(pvalues.items(), key=lambda row: row[1])):
        previous = max(previous, min(1., (len(pvalues)-i)*p))
        result[key] = {"adjusted_p": previous, "reject": previous <= alpha}
    return result
