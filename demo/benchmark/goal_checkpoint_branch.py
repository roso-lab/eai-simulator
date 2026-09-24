"""Checkpoint-gated branch replay contract for protocol comparisons.

Protocol branch comparisons are only meaningful when every compared change
happens on top of a checkpoint that was taken *before* the change.  This
module freezes that discipline as data:

- ``build_branch_plan``/``validate_branch_plan`` require each change point to
  reference a pre-change checkpoint with strictly earlier time and sequence;
  violations make the plan invalid, not "partially valid".
- ``branch_rng_digest`` derives a replay-stable random-stream digest through
  ``EAI.keyed_random``: the same seed and branch key reproduce the same
  stream, a different branch key deliberately diverges.
- ``build_branch_replay_report`` separates real branches from sham controls:
  sham evidence is always archived as diagnostics and strict comparison
  eligibility additionally requires equal seeds and matching stream digests
  on both sides.

Nothing here performs a native PhysX restore; native restore verification
remains the SCORING_CHECKPOINT_DESIGN gate.  Release flags stay false.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from EAI.keyed_random import KeyedRandom

from .development_track_marker import build_development_marker, stamp_evidence_record
from .provenance import canonical_sha256

BRANCH_SCHEMA = "eai.benchmark.goal_checkpoint_branch"
BRANCH_VERSION = 1
SHAM_ISOLATION_BLOCKER = "native_restore_verification_pending"
BRANCH_KINDS = ("treatment", "control", "sham_control")


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} requires a nonempty string")
    return value


def build_branch_plan(branch_id: str, *, seed: int, changes: Sequence[Mapping[str, Any]],
                      checkpoints: Sequence[Mapping[str, Any]],
                      rng_stream_key: str | None = None) -> dict[str, Any]:
    """Freeze one branch's changes on top of before-change checkpoints.

    ``rng_stream_key`` names the shared comparison stream (usually the change
    point id): treatment and control of the same compared change derive their
    random streams from the same key so seed identity is meaningful.
    """
    _nonempty_text(branch_id, "branch_id")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    stream_key = rng_stream_key or branch_id
    _nonempty_text(stream_key, "rng_stream_key")
    changes = deepcopy(list(changes))
    checkpoints = deepcopy(list(checkpoints))
    if not changes:
        raise ValueError("a branch plan requires at least one change")
    for change in changes:
        _nonempty_text(change.get("change_id"), "change_id")
        checkpoint_id = _nonempty_text(change.get("pre_change_checkpoint_id"),
                                       "pre_change_checkpoint_id")
        if (type(change.get("time_s")) not in (int, float)
                or type(change.get("sequence")) is not int
                or change["sequence"] < 0 or change["time_s"] < 0):
            raise ValueError("change points require nonnegative time and sequence")
        referenced = next((row for row in checkpoints
                           if row.get("checkpoint_id") == checkpoint_id), None)
        if referenced is None:
            raise ValueError(f"change references unknown checkpoint {checkpoint_id!r}")
        if (referenced["time_s"] >= change["time_s"]
                or referenced["sequence"] >= change["sequence"]):
            raise ValueError(
                f"checkpoint {checkpoint_id!r} is not before its change")
    seen: set[str] = set()
    for checkpoint in checkpoints:
        checkpoint_id = _nonempty_text(checkpoint.get("checkpoint_id"), "checkpoint_id")
        if checkpoint_id in seen:
            raise ValueError("checkpoint ids must be unique")
        seen.add(checkpoint_id)
        if (type(checkpoint.get("time_s")) not in (int, float)
                or type(checkpoint.get("sequence")) is not int
                or checkpoint["sequence"] < 0 or checkpoint["time_s"] < 0):
            raise ValueError("checkpoints require nonnegative time and sequence")
    plan = {
        "schema": BRANCH_SCHEMA, "version": BRANCH_VERSION,
        "visibility": "evaluator_only", "branch_id": branch_id, "seed": seed,
        "rng_stream_key": stream_key,
        "changes": changes, "checkpoints": checkpoints,
        "before_change_snapshot_discipline": "verified_at_build_time",
        "native_restore_verified": False,
        "execution_validated": False, "business_outcomes_validated": False,
        "scoreable": False,
    }
    plan["plan_sha256"] = canonical_sha256(
        {key: value for key, value in plan.items()})
    return plan


def validate_branch_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive the plan; tampered plans raise instead of passing."""
    if not isinstance(plan, Mapping):
        raise ValueError("plan must be a mapping")
    rebuilt = build_branch_plan(
        plan.get("branch_id", ""),
        seed=plan.get("seed", -1) if type(plan.get("seed")) is int else -1,
        changes=plan.get("changes", []),
        checkpoints=plan.get("checkpoints", []),
        rng_stream_key=plan.get("rng_stream_key"))
    if (plan.get("schema") != BRANCH_SCHEMA
            or plan.get("version") != BRANCH_VERSION
            or plan.get("visibility") != "evaluator_only"
            or plan.get("plan_sha256") != rebuilt["plan_sha256"]
            or plan.get("native_restore_verified") is not False
            or plan.get("scoreable") is not False):
        raise ValueError("branch plan identity, digest, or flags are invalid")
    return rebuilt


def branch_rng_digest(seed: int, branch_key: str, *, draws: int = 64) -> str:
    """Replay-stable digest of one branch's keyed random stream."""
    _nonempty_text(branch_key, "branch_key")
    if type(seed) is not int or type(draws) is not int or draws < 1:
        raise ValueError("seed must be an int and draws must be positive")
    random = KeyedRandom(seed, f"branch:{branch_key}")
    samples = [float(random.uniform(f"stream", index, 0.0, 1.0))
               for index in range(draws)]
    return canonical_sha256({"seed": seed, "branch_key": branch_key,
                             "draws": samples})


def build_branch_replay_report(
    *, treatment: Mapping[str, Any], control: Mapping[str, Any],
    sham_control: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare branches under before-change and RNG-identity gates.

    Sham controls never merge into the strict comparison: they are archived
    separately as diagnostic evidence even when every gate passes.
    """
    treatment = validate_branch_plan(treatment)
    control = validate_branch_plan(control)
    sham = validate_branch_plan(sham_control) if sham_control is not None else None
    treatment_digest = branch_rng_digest(treatment["seed"], treatment["rng_stream_key"])
    control_digest = branch_rng_digest(control["seed"], control["rng_stream_key"])
    replay_identical = (treatment["seed"] == control["seed"]
                        and treatment["rng_stream_key"] == control["rng_stream_key"]
                        and treatment_digest == control_digest)
    report: dict[str, Any] = {
        "schema": BRANCH_SCHEMA, "version": BRANCH_VERSION,
        "visibility": "evaluator_only",
        "treatment": {"branch_id": treatment["branch_id"], "seed": treatment["seed"],
                      "rng_stream_key": treatment["rng_stream_key"],
                      "rng_stream_sha256": treatment_digest,
                      "kind": "treatment"},
        "control": {"branch_id": control["branch_id"], "seed": control["seed"],
                    "rng_stream_key": control["rng_stream_key"],
                    "rng_stream_sha256": control_digest, "kind": "control"},
        "rng_replay_identical": replay_identical,
        "before_change_snapshots_present": True,
        "strict_branch_comparison_eligible": replay_identical,
        "sham_isolation": {
            "present": sham is not None,
            "branch_id": sham["branch_id"] if sham else None,
            "evidence_class": "diagnostic",
            "merged_into_strict_comparison": False,
        },
        "native_restore_verified": False,
        "infrastructure_blockers": [SHAM_ISOLATION_BLOCKER],
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    return stamp_evidence_record(
        report, build_development_marker(
            "city", asset_status="development_unverified",
            evidence_class="contract",
            infrastructure_blockers=[SHAM_ISOLATION_BLOCKER]))


def merge_sham_into_comparison(report: Mapping[str, Any]) -> dict[str, Any]:
    """Explicitly refuse sham merging instead of silently allowing it."""
    raise ValueError(
        "sham control evidence is diagnostic-only and cannot merge into the "
        "strict branch comparison")


__all__ = [
    "BRANCH_KINDS", "BRANCH_SCHEMA", "BRANCH_VERSION", "SHAM_ISOLATION_BLOCKER",
    "branch_rng_digest", "build_branch_plan", "build_branch_replay_report",
    "merge_sham_into_comparison", "validate_branch_plan",
]
