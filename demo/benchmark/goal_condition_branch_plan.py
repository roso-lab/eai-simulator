"""Map the 405-condition experiment matrix onto 32-branch batch execution.

The fused branch-data tool executes up to 32 branches per batch, where every
branch in a batch shares one frozen, checkpointed base state.  That sharing
is exactly what paired contrasts need — a treatment tier and its baseline
tier evaluated from the *same* initial state — while it is *not* statistical
independence.  This planner therefore separates the two layers explicitly:

- **Contrast layer (within a batch):** for one (scene, task variant) the
  batch hosts all condition tiers as sibling branches, so every
  treatment-vs-baseline pair is co-located on one base instance.
- **Independence layer (across batches):** one batch per
  (scene, variant, base index); the number of base instances per condition,
  not the number of branches, is the independent sample count.

Unfrozen statistical parameters (repeat count, spare-slot policy) are named
explicitly and carry proposal defaults only — implementers must not treat
them as frozen.  Release flags stay false; the plan is execution
infrastructure, never a score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from EAI.keyed_random import keyed_seed

from .mechanism_templates import M0_MECHANISMS, M1_MECHANISMS

PLAN_SCHEMA = "eai.benchmark.goal_condition_branch_plan"
PLAN_VERSION = 1
BRANCHES_PER_BATCH = 32
BASELINE_TIERS = {"communication_quality": "immediate",
                  "team_continuity": "stable"}
FACTOR_LEVELS = {
    "communication_quality": ("immediate", "delayed", "dropped",
                              "bandwidth_limited", "stale"),
    "team_continuity": ("stable", "member_exit", "member_join",
                        "capability_change"),
}
TASK_VARIANTS = ("base", *M0_MECHANISMS, *M1_MECHANISMS)
_CONDITIONS_PER_VARIANT = sum(len(levels) for levels in FACTOR_LEVELS.values())
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _tier_is_baseline(factor: str, level: str) -> bool:
    return BASELINE_TIERS[factor] == level


def build_condition_inventory(scenes: Sequence[str]) -> dict[str, Any]:
    """Enumerate the full condition space (5 scenes x 9 variants x 9 tiers)."""
    if (not isinstance(scenes, (list, tuple)) or not scenes
            or len(set(scenes)) != len(scenes)
            or any(not isinstance(scene, str) or not scene for scene in scenes)):
        raise ValueError("scenes must be distinct nonempty strings")
    conditions: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    for scene in scenes:
        for variant in TASK_VARIANTS:
            for factor, levels in FACTOR_LEVELS.items():
                for level in levels:
                    conditions.append({
                        "condition_id": f"{scene}/{variant}/{factor}/{level}",
                        "scene": scene, "variant": variant,
                        "factor": factor, "level": level,
                        "is_baseline": _tier_is_baseline(factor, level),
                    })
                baseline = BASELINE_TIERS[factor]
                for level in levels:
                    if level == baseline:
                        continue
                    pairs.append({
                        "pair_id": f"{scene}/{variant}/{factor}/{baseline}->{level}",
                        "scene": scene, "variant": variant, "factor": factor,
                        "baseline_level": baseline, "treatment_level": level,
                    })
    return {
        "scenes": list(scenes), "task_variants": list(TASK_VARIANTS),
        "factor_levels": deepcopy(dict(FACTOR_LEVELS)),
        "condition_count": len(conditions),
        "pair_count": len(pairs),
        "conditions": conditions,
        "pairs": pairs,
    }


def plan_contrast_batches(
    scenes: Sequence[str], *, bases_per_pair: int = 10,
    base_seed: int = 0,
) -> dict[str, Any]:
    """Assign every condition to branch batches with pair co-location.

    One batch per (scene, variant, base index) hosts all tiers of that
    (scene, variant) as sibling branches, so every treatment/baseline pair
    shares one base instance.  ``bases_per_pair`` is an **unfrozen** repeat
    policy (proposal: the user's same-task-times-10 principle).
    """
    if type(bases_per_pair) is not int or bases_per_pair < 1:
        raise ValueError("bases_per_pair must be a positive integer")
    inventory = build_condition_inventory(scenes)
    batches: list[dict[str, Any]] = []
    for scene in scenes:
        for variant in TASK_VARIANTS:
            for base_index in range(1, bases_per_pair + 1):
                branches = []
                for factor, levels in FACTOR_LEVELS.items():
                    for level in levels:
                        condition_id = f"{scene}/{variant}/{factor}/{level}"
                        branches.append({
                            "branch_id": f"{scene}/{variant}/base{base_index}/{factor}/{level}",
                            "condition_id": condition_id,
                            "is_baseline": _tier_is_baseline(factor, level),
                            "seed": keyed_seed(
                                base_seed, "condition-branch",
                                {"scene": scene, "variant": variant,
                                 "base_index": base_index,
                                 "factor": factor, "level": level}),
                        })
                batches.append({
                    "batch_id": f"{scene}/{variant}/base{base_index}",
                    "scene": scene, "variant": variant,
                    "base_index": base_index,
                    "base_seed": keyed_seed(base_seed, "condition-base",
                                            {"scene": scene, "variant": variant,
                                             "base_index": base_index}),
                    "branches": branches,
                    "branch_count": len(branches),
                    "slot_capacity": BRANCHES_PER_BATCH,
                })
    plan = {
        "schema": PLAN_SCHEMA, "version": PLAN_VERSION,
        "visibility": "evaluator_only",
        "scenes": list(scenes), "bases_per_pair": bases_per_pair,
        "branches_per_batch": BRANCHES_PER_BATCH,
        "batch_count": len(batches),
        "condition_count": inventory["condition_count"],
        "pair_count": inventory["pair_count"],
        "statistics_policy": {
            "independent_unit": "base_instance",
            "within_base": "paired_contrast_only",
            "branch_count_is_not_sample_count": True,
        },
        "unfrozen_parameters": {
            "bases_per_pair": {
                "proposal": 10,
                "note": "same-task-times-10 principle; not frozen",
            },
            "spare_slot_policy": {
                "proposal": "unused within-batch slots stay idle",
                "alternatives": ["within-base noise probes (non-independent)"],
                "note": "not frozen",
            },
        },
        "batches": batches,
        **_RELEASE,
    }
    _validate_plan(plan, inventory)
    return plan


def _validate_plan(plan: Mapping[str, Any],
                   inventory: Mapping[str, Any]) -> None:
    covered: dict[str, set[str]] = {}
    for batch in plan["batches"]:
        if batch["branch_count"] > plan["branches_per_batch"]:
            raise ValueError("batch exceeds branch capacity")
        if any(not (batch["base_index"] >= 1) for _ in [0]):
            raise ValueError("invalid base index")
        for branch in batch["branches"]:
            covered.setdefault(branch["condition_id"], set()).add(
                batch["batch_id"])
    expected = {row["condition_id"] for row in inventory["conditions"]}
    if set(covered) != expected:
        missing = sorted(expected - set(covered))[:3]
        extra = sorted(set(covered) - expected)[:3]
        raise ValueError(
            f"plan coverage mismatch: missing={missing} extra={extra}")
    if any(len(bases) != plan["bases_per_pair"] for bases in covered.values()):
        raise ValueError("condition base replication inconsistent")
    # Pair co-location: every pair's two tiers live in the same batches.
    pair_index = {row["pair_id"]: row for row in inventory["pairs"]}
    for pair in pair_index.values():
        treatment = f"{pair['scene']}/{pair['variant']}/{pair['factor']}/{pair['treatment_level']}"
        baseline = f"{pair['scene']}/{pair['variant']}/{pair['factor']}/{pair['baseline_level']}"
        if not (covered[treatment] & covered[baseline]):
            raise ValueError(f"pair not co-located: {pair['pair_id']}")
    seeds = [branch["seed"] for batch in plan["batches"]
             for branch in batch["branches"]]
    if len(set(seeds)) != len(seeds):
        raise ValueError("branch seeds collide")


def condition_branch_plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Compact summary for docs and reviews (no branch rows)."""
    return {
        "schema": plan["schema"], "version": plan["version"],
        "scenes": list(plan["scenes"]),
        "condition_count": plan["condition_count"],
        "pair_count": plan["pair_count"],
        "batch_count": plan["batch_count"],
        "branches_per_batch": plan["branches_per_batch"],
        "bases_per_pair": plan["bases_per_pair"],
        "statistics_policy": deepcopy(plan["statistics_policy"]),
        "unfrozen_parameters": deepcopy(plan["unfrozen_parameters"]),
        **_RELEASE,
    }


__all__ = [
    "BASELINE_TIERS", "BRANCHES_PER_BATCH", "FACTOR_LEVELS", "PLAN_SCHEMA",
    "PLAN_VERSION", "TASK_VARIANTS", "build_condition_inventory",
    "condition_branch_plan_summary", "plan_contrast_batches",
]
