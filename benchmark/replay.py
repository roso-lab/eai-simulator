"""Protocol-level checkpoint, sham and paired-branch controls.

This module does not capture or restore PhysX state.  It validates that branch
requests share one immutable checkpoint and interprets the backend's typed
restore/fidelity report.  A branch artifact remains useful even when strict
paired claims are unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Mapping, Sequence

from .protocol import BranchRole, BranchSpec, CheckpointRef, RunKey


class PairStatus(str, Enum):
    PENDING = "pending"
    VALID = "valid"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


@dataclass(frozen=True)
class RestoreDecision:
    """Backend restore outcome as consumed by the paper protocol."""

    status: str
    fidelity: str
    step: int
    unrestored_channels: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    strict_paired_eligible: bool = False

    @classmethod
    def from_backend(cls, value: Any, *, checkpoint: CheckpointRef) -> "RestoreDecision":
        if isinstance(value, Mapping):
            status = str(value.get("status", "unsupported"))
            fidelity = str(value.get("replay_fidelity", value.get("fidelity", "")))
            step = int(value.get("step_index", checkpoint.step))
            unrestored = tuple(str(item) for item in value.get("unrestored_channels", ()))
            diagnostics = tuple(str(item) for item in value.get("diagnostics", ()))
        else:
            status = str(getattr(value, "status", "unsupported"))
            fidelity = str(getattr(value, "replay_fidelity", getattr(value, "fidelity", "")))
            step = int(getattr(value, "step_index", checkpoint.step))
            unrestored = tuple(str(item) for item in getattr(value, "unrestored_channels", ()))
            diagnostics = tuple(str(item) for item in getattr(value, "diagnostics", ()))
        if step != checkpoint.step:
            diagnostics = diagnostics + (f"restore_step_mismatch:{step}!={checkpoint.step}",)
        eligible = status == "restored" and fidelity == "causal_replay" and step == checkpoint.step and not unrestored
        return cls(status, fidelity, step, unrestored, diagnostics, eligible)


@dataclass(frozen=True)
class BranchExecutionStatus:
    branch: BranchSpec
    restore: RestoreDecision
    artifact_retained: bool = True
    pair_status: PairStatus = PairStatus.PENDING
    reason: str = ""


@dataclass(frozen=True)
class ShamCalibrationReport:
    """Comparison of untreated fact and sham observables at a paired boundary."""

    status: PairStatus
    max_abs_error: float | None
    tolerance: float
    compared_keys: tuple[str, ...]
    reason: str

    @property
    def matched(self) -> bool:
        return self.status is PairStatus.VALID

    @classmethod
    def compare(
        cls,
        fact: Mapping[str, Any] | None,
        sham: Mapping[str, Any] | None,
        *,
        tolerance: float = 1e-6,
    ) -> "ShamCalibrationReport":
        if tolerance < 0 or not math.isfinite(float(tolerance)):
            raise ValueError("tolerance must be finite and non-negative")
        if fact is None or sham is None:
            return cls(PairStatus.UNSUPPORTED, None, tolerance, (), "fact_or_sham_observation_missing")
        keys = tuple(sorted(set(fact) | set(sham)))
        if set(fact) != set(sham):
            return cls(PairStatus.INVALID, None, tolerance, keys, "fact_and_sham_observation_schema_mismatch")
        errors: list[float] = []
        for key in keys:
            left, right = fact[key], sham[key]
            try:
                left_values = _flatten(left)
                right_values = _flatten(right)
                if len(left_values) != len(right_values):
                    return cls(PairStatus.INVALID, None, tolerance, keys, f"shape_mismatch:{key}")
                errors.extend(abs(a - b) for a, b in zip(left_values, right_values))
            except (TypeError, ValueError):
                if left != right:
                    return cls(PairStatus.INVALID, None, tolerance, keys, f"non_numeric_value_mismatch:{key}")
        maximum = max(errors, default=0.0)
        if maximum <= tolerance:
            return cls(PairStatus.VALID, maximum, tolerance, keys, "sham_matches_fact_within_tolerance")
        return cls(PairStatus.INVALID, maximum, tolerance, keys, "sham_mismatch_invalidates_strict_pair")


@dataclass(frozen=True)
class PairedBranchPlan:
    """The four paper branches, all rooted at one checkpoint digest."""

    checkpoint: CheckpointRef
    branches: tuple[BranchSpec, ...]

    def __post_init__(self) -> None:
        required = {BranchRole.FACT, BranchRole.REFERENCE, BranchRole.SHAM, BranchRole.INTERVENTION}
        if len(self.branches) != len(required):
            raise ValueError("paired plan must contain exactly one branch for each role")
        roles = {branch.run_key.branch_role for branch in self.branches}
        if roles != required:
            raise ValueError("paired plan must contain exactly one branch for each role: fact, reference, sham and intervention")
        if any(branch.checkpoint.digest != self.checkpoint.digest for branch in self.branches):
            raise ValueError("all paired branches must share one checkpoint digest")
        if any(branch.run_key.pair_key() != self.branches[0].run_key.pair_key() for branch in self.branches):
            raise ValueError("all paired branches must share one pair identity")
        if any(branch.run_key.branch_role is BranchRole.INTERVENTION and branch.treatment_id == "none" for branch in self.branches):
            raise ValueError("intervention branch requires an explicit treatment")

    @classmethod
    def create(
        cls,
        *,
        base_key: RunKey,
        checkpoint: CheckpointRef,
        intervention_id: str,
        reference_treatment: str = "none",
        non_target_budget: Mapping[str, Any] | None = None,
    ) -> "PairedBranchPlan":
        if base_key.branch_role is not BranchRole.FACT:
            base_key = RunKey(
                base_key.base_instance_id,
                base_key.system_id,
                base_key.condition_id,
                base_key.repeat_id,
                base_key.checkpoint_round,
                BranchRole.FACT,
                base_key.untreated_history_id,
            )
        branches = []
        for role, treatment in (
            (BranchRole.FACT, "none"),
            (BranchRole.REFERENCE, reference_treatment),
            (BranchRole.SHAM, "none"),
            (BranchRole.INTERVENTION, intervention_id),
        ):
            key = RunKey(
                base_key.base_instance_id,
                base_key.system_id,
                base_key.condition_id,
                base_key.repeat_id,
                base_key.checkpoint_round,
                role,
                base_key.untreated_history_id,
            )
            branches.append(BranchSpec(key, checkpoint, treatment_id=treatment, non_target_budget=non_target_budget or {}))
        return cls(checkpoint, tuple(branches))


class PairedBranchCoordinator:
    """Prepare independent branches and gate strict paired claims."""

    def __init__(self, plan: PairedBranchPlan, backend: Any) -> None:
        self.plan = plan
        self.backend = backend
        self._statuses: dict[BranchRole, BranchExecutionStatus] = {}
        self._sham_report: ShamCalibrationReport | None = None

    @property
    def statuses(self) -> tuple[BranchExecutionStatus, ...]:
        return tuple(self._statuses[role] for role in sorted(self._statuses, key=lambda item: item.value))

    @property
    def sham_report(self) -> ShamCalibrationReport | None:
        return self._sham_report

    def restore_branch(self, branch: BranchSpec) -> BranchExecutionStatus:
        self._validate_branch(branch)
        # The backend receives the original immutable checkpoint each time;
        # no branch is ever derived from a treated branch.
        try:
            raw = self.backend.fork(self.plan.checkpoint, branch)
            restore = RestoreDecision.from_backend(raw, checkpoint=self.plan.checkpoint)
        except Exception as exc:
            restore = RestoreDecision("unsupported", "", self.plan.checkpoint.step, diagnostics=(str(exc),))
        if restore.strict_paired_eligible:
            pair_status = PairStatus.PENDING
            reason = "causal_replay_restore_admitted_pending_sham_calibration"
        elif restore.status in {"physical_state_only", "unsupported"}:
            pair_status = PairStatus.UNSUPPORTED
            reason = "strict_paired_requires_causal_replay_restore"
        else:
            pair_status = PairStatus.INVALID
            reason = "checkpoint_restore_did_not_pass_fidelity_gate"
        status = BranchExecutionStatus(branch, restore, artifact_retained=True, pair_status=pair_status, reason=reason)
        self._statuses[branch.run_key.branch_role] = status
        return status

    def calibrate_sham(self, fact: Mapping[str, Any] | None, sham: Mapping[str, Any] | None, *, tolerance: float = 1e-6) -> ShamCalibrationReport:
        report = ShamCalibrationReport.compare(fact, sham, tolerance=tolerance)
        self._sham_report = report
        for role, status in tuple(self._statuses.items()):
            if role in {BranchRole.FACT, BranchRole.REFERENCE, BranchRole.SHAM, BranchRole.INTERVENTION}:
                if report.status is PairStatus.VALID and status.pair_status is PairStatus.PENDING:
                    new_status = PairStatus.VALID
                    reason = "causal_replay_and_sham_calibration_admitted"
                elif report.status is not PairStatus.VALID:
                    new_status = report.status
                    reason = report.reason
                else:
                    continue
                self._statuses[role] = BranchExecutionStatus(status.branch, status.restore, status.artifact_retained, new_status, reason)
        return report

    def strict_paired_status(self) -> tuple[PairStatus, str]:
        if set(self._statuses) != {BranchRole.FACT, BranchRole.REFERENCE, BranchRole.SHAM, BranchRole.INTERVENTION}:
            return PairStatus.UNSUPPORTED, "not_all_four_branches_restored"
        if self._sham_report is None:
            return PairStatus.UNSUPPORTED, "sham_calibration_required"
        if self._sham_report.status is not PairStatus.VALID:
            return self._sham_report.status, self._sham_report.reason
        if any(item.pair_status is not PairStatus.VALID for item in self._statuses.values()):
            return PairStatus.UNSUPPORTED, "one_or_more_branch_restore_fidelity_gates_failed: causal_replay_required"
        return PairStatus.VALID, "strict_paired_claim_admitted"

    def _validate_branch(self, branch: BranchSpec) -> None:
        if branch not in self.plan.branches:
            raise ValueError("branch is not a member of the paired plan")
        if branch.checkpoint.digest != self.plan.checkpoint.digest:
            raise ValueError("branch checkpoint digest does not match paired plan")
        if branch.run_key.pair_key() != self.plan.branches[0].run_key.pair_key():
            raise ValueError("branch pair identity does not match paired plan")
        if branch.run_key.branch_role is BranchRole.SHAM and branch.treatment_id != "none":
            raise ValueError("sham treatment must remain none")


def _flatten(value: Any) -> list[float]:
    if isinstance(value, Mapping):
        result: list[float] = []
        for key in sorted(value):
            result.extend(_flatten(value[key]))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_flatten(item))
        return result
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite value")
    return [number]


__all__ = [
    "BranchExecutionStatus",
    "PairStatus",
    "PairedBranchCoordinator",
    "PairedBranchPlan",
    "RestoreDecision",
    "ShamCalibrationReport",
]
