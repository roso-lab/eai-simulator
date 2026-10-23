"""Executable pre-execution contracts for paper commit 1f32949.

These validate registrations; a digest or declaration is not a physical proof.
"""
from dataclasses import dataclass
import math
from statistics import median
from typing import Literal, Mapping, Sequence

from .records_v3 import ObligationOutcomeV3, RecordStatus, ResourceTraceV3, canonical_digest


def required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def required_digest(value: str, name: str) -> None:
    required(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


@dataclass(frozen=True)
class PairedBlockRerunV3:
    """Registration-only contract for a paired skill-failure rerun.

    This object admits a retry plan; it does not turn a retry into a valid
    scientific observation.  In particular, a method failure is never
    retryable and a skill postcondition failure must rerun the whole paired
    block on one newly registered controller substream.
    """

    block_id: str
    branch_ids: tuple[str, ...]
    failed_branch_id: str
    failure_scope: Literal["skill_postcondition"]
    checkpoint_digest: str
    random_stream_digest: str
    original_controller_substream_digest: str
    controller_substream_digest: str
    retry_cap: int
    original_record_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("block_id", "failed_branch_id"):
            required(getattr(self, name), name)
        for name in ("checkpoint_digest", "random_stream_digest", "original_controller_substream_digest", "controller_substream_digest"):
            required_digest(getattr(self, name), name)
        if len(self.branch_ids) < 2 or len(set(self.branch_ids)) != len(self.branch_ids):
            raise ValueError("paired block requires distinct branch identities")
        if self.failed_branch_id not in self.branch_ids:
            raise ValueError("failed branch must belong to paired block")
        if len(self.original_record_ids) != len(self.branch_ids) or any(not item for item in self.original_record_ids):
            raise ValueError("one original record is required for every branch")
        if len(set(self.original_record_ids)) != len(self.original_record_ids):
            raise ValueError("original branch records must remain distinct")
        if type(self.retry_cap) is not int or self.retry_cap < 1:
            raise ValueError("retry_cap must permit a registered bounded retry")
        if self.failure_scope != "skill_postcondition":
            raise ValueError("only skill postcondition failure uses paired-block rerun; method failure is not retryable")
        if self.controller_substream_digest == self.original_controller_substream_digest:
            raise ValueError("paired rerun requires one new controller substream")

    @property
    def rerun_branch_ids(self) -> tuple[str, ...]:
        """All branches are rerun; the failed branch is never rerun alone."""
        return tuple(self.branch_ids)

    @property
    def registration_digest(self) -> str:
        """Digest of the frozen retry registration carried by every receipt."""
        return canonical_digest({
            "schema": "heterocoord.paired_block_rerun.v3",
            "block_id": self.block_id,
            "branch_ids": list(self.branch_ids),
            "failed_branch_id": self.failed_branch_id,
            "failure_scope": self.failure_scope,
            "checkpoint_digest": self.checkpoint_digest,
            "random_stream_digest": self.random_stream_digest,
            "original_controller_substream_digest": self.original_controller_substream_digest,
            "controller_substream_digest": self.controller_substream_digest,
            "retry_cap": self.retry_cap,
            "original_record_ids": list(self.original_record_ids),
        })

    def _validate_attempt(self, *, attempt: int, executed_branch_ids: Sequence[str]) -> None:
        if type(attempt) is not int or not 1 <= attempt <= self.retry_cap:
            raise ValueError("retry attempt exceeds the registered retry cap")
        if tuple(executed_branch_ids) != tuple(self.branch_ids):
            raise ValueError("paired retry must execute every branch together in registered order")


@dataclass(frozen=True)
class PairedBlockAttemptReceiptV3:
    block_id: str
    attempt_id: str
    attempt: int
    executed_branch_ids: tuple[str, ...]
    status: Literal["valid-pair", "unknown-record", "pair-invalid"]
    registration_digest: str = ""
    checkpoint_digest: str = ""
    random_stream_digest: str = ""
    controller_substream_digest: str = ""
    original_record_ids: tuple[str, ...] = ()
    branch_result_digests: tuple[str, ...] = ()
    receipt_digest: str = ""

    def unsigned_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "attempt_id": self.attempt_id,
            "attempt": self.attempt,
            "executed_branch_ids": list(self.executed_branch_ids),
            "status": self.status,
            "registration_digest": self.registration_digest,
            "checkpoint_digest": self.checkpoint_digest,
            "random_stream_digest": self.random_stream_digest,
            "controller_substream_digest": self.controller_substream_digest,
            "original_record_ids": list(self.original_record_ids),
            "branch_result_digests": list(self.branch_result_digests),
        }

    def to_dict(self) -> dict:
        return {**self.unsigned_dict(), "receipt_digest": self.receipt_digest}

    @classmethod
    def from_dict(cls, payload: Mapping) -> "PairedBlockAttemptReceiptV3":
        value = dict(payload)
        value["executed_branch_ids"] = tuple(value.get("executed_branch_ids", ()))
        value["original_record_ids"] = tuple(value.get("original_record_ids", ()))
        value["branch_result_digests"] = tuple(value.get("branch_result_digests", ()))
        receipt = cls(**value)
        required(receipt.block_id, "block_id")
        required(receipt.attempt_id, "attempt_id")
        if type(receipt.attempt) is not int or receipt.attempt < 1:
            raise ValueError("paired attempt number must be positive")
        if not receipt.executed_branch_ids or len(set(receipt.executed_branch_ids)) != len(receipt.executed_branch_ids):
            raise ValueError("paired attempt branches must be unique and nonempty")
        if receipt.status not in {"valid-pair", "unknown-record", "pair-invalid"}:
            raise ValueError("unsupported paired attempt status")
        for name in ("registration_digest", "checkpoint_digest", "random_stream_digest"):
            if getattr(receipt, name):
                required_digest(getattr(receipt, name), name)
        if receipt.receipt_digest:
            required_digest(receipt.receipt_digest, "receipt_digest")
        if receipt.original_record_ids and len(set(receipt.original_record_ids)) != len(receipt.original_record_ids):
            raise ValueError("original branch records must remain distinct")
        if receipt.branch_result_digests:
            if len(receipt.branch_result_digests) != len(receipt.executed_branch_ids):
                raise ValueError("paired attempt requires one result digest per branch")
            for value in receipt.branch_result_digests:
                required_digest(value, "branch_result_digest")
        return receipt


class PairedBlockRerunLedgerV3:
    """Consumes each registered paired rerun attempt exactly once in-process."""

    def __init__(self, registration: PairedBlockRerunV3) -> None:
        self.registration = registration
        self._receipts: list[PairedBlockAttemptReceiptV3] = []

    @property
    def receipts(self) -> tuple[PairedBlockAttemptReceiptV3, ...]:
        return tuple(self._receipts)

    def append(self, *, attempt_id: str, executed_branch_ids: Sequence[str],
               branch_result_digests: Mapping[str, str],
               completion: Literal["complete", "infrastructure_unknown", "incomplete_pair"]) -> PairedBlockAttemptReceiptV3:
        required(attempt_id, "attempt_id")
        if any(receipt.attempt_id == attempt_id for receipt in self._receipts):
            raise ValueError("paired rerun attempt_id was already consumed")
        attempt = len(self._receipts) + 1
        self.registration._validate_attempt(attempt=attempt, executed_branch_ids=executed_branch_ids)
        if set(branch_result_digests) != set(self.registration.branch_ids):
            raise ValueError("paired attempt requires one result receipt for every registered branch")
        ordered_result_digests = tuple(branch_result_digests[branch] for branch in self.registration.branch_ids)
        for value in ordered_result_digests:
            required_digest(value, "branch_result_digest")
        status_by_completion = {
            "complete": "valid-pair",
            "infrastructure_unknown": "unknown-record",
            "incomplete_pair": "pair-invalid",
        }
        if completion not in status_by_completion:
            raise ValueError("unsupported paired-block completion status")
        receipt_base = PairedBlockAttemptReceiptV3(
            self.registration.block_id, attempt_id, attempt, tuple(executed_branch_ids), status_by_completion[completion],
            registration_digest=self.registration.registration_digest,
            checkpoint_digest=self.registration.checkpoint_digest,
            random_stream_digest=self.registration.random_stream_digest,
            controller_substream_digest=self.registration.controller_substream_digest,
            original_record_ids=self.registration.original_record_ids,
            branch_result_digests=ordered_result_digests)
        receipt = PairedBlockAttemptReceiptV3(
            **{**receipt_base.to_dict(), "executed_branch_ids": receipt_base.executed_branch_ids,
               "original_record_ids": receipt_base.original_record_ids,
               "branch_result_digests": receipt_base.branch_result_digests,
               "receipt_digest": canonical_digest(receipt_base.unsigned_dict())})
        self._receipts.append(receipt)
        return receipt

    def validate_receipt(self, receipt: PairedBlockAttemptReceiptV3) -> None:
        """Validate a receipt before it can be converted into a scheduled run."""
        if not isinstance(receipt, PairedBlockAttemptReceiptV3):
            raise ValueError("validated paired attempt receipt required")
        if receipt.registration_digest != self.registration.registration_digest:
            raise ValueError("paired receipt registration digest does not match")
        if tuple(receipt.executed_branch_ids) != tuple(self.registration.branch_ids):
            raise ValueError("paired receipt branch order does not match registration")
        if receipt.checkpoint_digest != self.registration.checkpoint_digest:
            raise ValueError("paired receipt checkpoint does not match registration")
        if receipt.random_stream_digest != self.registration.random_stream_digest:
            raise ValueError("paired receipt random stream does not match registration")
        if receipt.controller_substream_digest != self.registration.controller_substream_digest:
            raise ValueError("paired receipt controller substream does not match registration")
        if tuple(receipt.original_record_ids) != tuple(self.registration.original_record_ids):
            raise ValueError("paired receipt original record mapping does not match registration")
        if len(receipt.branch_result_digests) != len(self.registration.branch_ids):
            raise ValueError("paired receipt result set is incomplete")
        for value in receipt.branch_result_digests:
            required_digest(value, "branch_result_digest")
        if receipt.attempt < 1 or receipt.attempt > self.registration.retry_cap:
            raise ValueError("paired receipt attempt exceeds registration retry cap")
        if receipt.receipt_digest != canonical_digest(receipt.unsigned_dict()):
            raise ValueError("paired receipt digest does not match its immutable payload")
        if not any(existing == receipt for existing in self._receipts):
            raise ValueError("paired receipt was not issued by this ledger")


@dataclass(frozen=True)
class ExternalContentionRegistrationV3:
    """Minimal registered semantics for an external holder request queue."""

    condition_id: str
    holder_id: str
    resource_id: str
    frozen_time_tick: int
    queue_policy: Literal["shared_robot_linearization"]
    hold_window_ticks: int
    typed_lock_contract_digest: str
    grant_time_is_outcome: bool = True

    def __post_init__(self) -> None:
        for name in ("condition_id", "holder_id", "resource_id"):
            required(getattr(self, name), name)
        required_digest(self.typed_lock_contract_digest, "typed_lock_contract_digest")
        if type(self.frozen_time_tick) is not int or self.frozen_time_tick < 0:
            raise ValueError("frozen_time_tick must be a nonnegative integer")
        if self.queue_policy != "shared_robot_linearization":
            raise ValueError("external contention requires the same registered linearization as robot requests")
        if type(self.hold_window_ticks) is not int or self.hold_window_ticks <= 0:
            raise ValueError("hold_window_ticks must be positive")
        if self.grant_time_is_outcome is not True:
            raise ValueError("grant time must remain an outcome")

    def queue_request(self, request_id: str, request_tick: int) -> tuple[str, str, str, int]:
        required(request_id, "request_id")
        if request_tick != self.frozen_time_tick:
            raise ValueError("external holder request must be queued at the frozen tick")
        return request_id, self.holder_id, self.resource_id, request_tick


@dataclass(frozen=True)
class ProtectiveStopRegistrationV3:
    """Registration-only protective-stop contract."""

    condition_id: str
    target_ids: tuple[str, ...]
    controller_digest: str
    stop_dynamics_digest: str
    stop_dynamics_verification_digest: str
    load_state_checkpoint_digest: str
    onset_tick: int
    release_tick: int
    reset_digest: str

    def __post_init__(self) -> None:
        required(self.condition_id, "condition_id")
        if not self.target_ids or len(set(self.target_ids)) != len(self.target_ids) or any(not item for item in self.target_ids):
            raise ValueError("protective stop requires distinct target identities")
        for name in ("controller_digest", "stop_dynamics_digest", "stop_dynamics_verification_digest", "load_state_checkpoint_digest", "reset_digest"):
            required_digest(getattr(self, name), name)
        if type(self.onset_tick) is not int or type(self.release_tick) is not int or self.onset_tick < 0 or self.release_tick <= self.onset_tick:
            raise ValueError("protective stop release must follow onset")


@dataclass(frozen=True)
class FixedSystemDisruptionV3:
    """Registration gate for estimating a runtime condition effect."""

    condition_id: str
    nominal_system_digest: str
    treatment_system_digest: str
    nominal_task_family_digest: str
    treatment_task_family_digest: str
    nominal_starting_team_digest: str
    treatment_starting_team_digest: str
    nominal_condition_digest: str
    treatment_condition_digest: str
    condition_kind: Literal["registered_runtime_condition"] = "registered_runtime_condition"
    retraining_applied: bool = False

    def __post_init__(self) -> None:
        required(self.condition_id, "condition_id")
        for name in ("nominal_system_digest", "treatment_system_digest",
                     "nominal_task_family_digest", "treatment_task_family_digest",
                     "nominal_starting_team_digest", "treatment_starting_team_digest",
                     "nominal_condition_digest", "treatment_condition_digest"):
            required_digest(getattr(self, name), name)
        if self.condition_kind != "registered_runtime_condition":
            raise ValueError("observation settings and skill fidelity are not runtime disruption conditions")
        if self.nominal_condition_digest == self.treatment_condition_digest:
            raise ValueError("registered runtime condition must change")
        if self.retraining_applied:
            raise ValueError("fixed-system disruption forbids retraining")
        if self.nominal_system_digest != self.treatment_system_digest:
            raise ValueError("disruption comparison must keep the system fixed")
        if self.nominal_task_family_digest != self.treatment_task_family_digest:
            raise ValueError("disruption comparison must keep the task family fixed")
        if self.nominal_starting_team_digest != self.treatment_starting_team_digest:
            raise ValueError("disruption comparison must keep the starting team fixed")


@dataclass(frozen=True)
class CommonStartV3:
    instance_id: str
    checkpoint_digest: str
    public_history_digest: str
    coordinator_id: str
    replay_validation_digest: str
    closure_digest: str
    loss_feasibility_digest: str
    checkpoint_tick: int
    earliest_notification_tick: int
    event_tick: int
    horizon_ticks: int
    deadline_tick: int

    def __post_init__(self):
        for name in ("instance_id", "checkpoint_digest", "public_history_digest", "coordinator_id", "replay_validation_digest", "closure_digest", "loss_feasibility_digest"):
            required(getattr(self, name), name)
        ticks = (self.checkpoint_tick, self.earliest_notification_tick, self.event_tick, self.horizon_ticks, self.deadline_tick)
        if any(type(t) is not int or t < 0 for t in ticks):
            raise ValueError("ticks must be nonnegative integers")
        if not self.checkpoint_tick < self.earliest_notification_tick <= self.event_tick or not 0 < self.horizon_ticks <= self.deadline_tick - self.event_tick:
            raise ValueError("common checkpoint must precede every notification and fit observation window")

    def validate_branch(self, *, checkpoint_digest: str, history_digest: str, initialization_digest: str, coordinator_id: str, system_id: str) -> None:
        required(system_id, "system")
        required(initialization_digest, "preregistered initialization routine")
        if (checkpoint_digest, history_digest, coordinator_id) != (self.checkpoint_digest, self.public_history_digest, self.coordinator_id):
            raise ValueError("D4 requires the registered common checkpoint and public history")


@dataclass(frozen=True)
class AdaptationPairV3:
    source_reference_digest: str
    target_checkpoint_digest: str
    unit_certificate_digest: str
    initialization_digest: str
    with_reference: tuple[float, ...]
    without_reference: tuple[float, ...]
    repeat_ids: tuple[str, ...]
    target_demonstrations: bool = False
    parameter_updates: bool = False

    def __post_init__(self):
        for name in ("source_reference_digest", "target_checkpoint_digest", "unit_certificate_digest", "initialization_digest"):
            required(getattr(self, name), name)
        if self.target_demonstrations or self.parameter_updates:
            raise ValueError("D5 primary forbids target demonstrations and parameter updates")
        if len(self.repeat_ids) != 3 or len(set(self.repeat_ids)) != 3 or any(not x for x in self.repeat_ids):
            raise ValueError("D5 requires three distinct paired repeat identities")
        if any(len(values) != 3 or any(v not in (0, 1) for v in values) for values in (self.with_reference, self.without_reference)):
            raise ValueError("D5 joint acceptance requires three binary outcomes per branch")

    @property
    def reference_gain(self) -> float:
        return 100 * sum(a - b for a, b in zip(self.with_reference, self.without_reference)) / 3


@dataclass(frozen=True)
class CalibrationBatchV3:
    batch_id: str
    coordinator_id: str
    source_digest: str
    independent_task_digest: str
    run_ids: tuple[str, ...]
    success: tuple[bool, ...]
    transmitted_bits: tuple[int, ...]
    physical_seconds: tuple[float, ...]

    def __post_init__(self):
        for name in ("batch_id", "coordinator_id", "source_digest", "independent_task_digest"):
            required(getattr(self, name), name)
        n = len(self.run_ids)
        if not n or len(set(self.run_ids)) != n or any(not x for x in self.run_ids) or any(len(x) != n for x in (self.success, self.transmitted_bits, self.physical_seconds)):
            raise ValueError("calibration run identities and observations must align")
        if any(type(v) is not bool for v in self.success) or any(type(v) is not int or v < 0 for v in self.transmitted_bits) or any(not math.isfinite(v) or v < 0 for v in self.physical_seconds):
            raise ValueError("invalid calibration observations")

    def references(self, *, candidate_ids: Sequence[str]) -> tuple[float, float]:
        if self.coordinator_id in candidate_ids:
            raise ValueError("calibration coordinator must be independent of candidates")
        selected = [i for i, passed in enumerate(self.success) if passed]
        if not selected:
            raise ValueError("calibration has no successful independent runs")
        result = (median(self.transmitted_bits[i] for i in selected), median(self.physical_seconds[i] for i in selected))
        if min(result) <= 0:
            raise ValueError("zero reference is undefined, not regularized")
        return result


def obligation_components(rows: Sequence[ObligationOutcomeV3], *, unknown: float) -> Mapping[str, float]:
    """Run-level components. Every frozen opportunity stays, unreached = zero.

    Input includes all registered pre-action opportunities. Task display labels
    are deliberately irrelevant. Hierarchical instance/cell averaging follows.
    """
    if unknown not in (0, 1):
        raise ValueError("unknown endpoint must be zero or one")
    groups = {key: [] for key in ("P2", "P3J", "P3S", "P4")}
    seen = set()
    for row in rows:
        if not row.obligation_id or row.obligation_id in seen:
            raise ValueError("obligations must have unique frozen identities")
        seen.add(row.obligation_id)
        key = row.relation + (row.p3_subtype or "")
        if key not in groups:
            raise ValueError("unsupported relation")
        if not row.opportunity:
            raise ValueError("input must be the frozen pre-action opportunity directory")
        if row.status is RecordStatus.VALID:
            value = row.accepted_fraction if row.accepted_fraction is not None else float(row.completed)
            groups[key].append(0. if row.disposition in {"violated", "unreached"} else value)
        elif row.status in {RecordStatus.UNKNOWN, RecordStatus.INCOMPLETE}:
            groups[key].append(unknown)
        else:
            raise ValueError("invalid/NA obligations require explicit coverage adjudication")
    return {key: sum(values) / len(values) for key, values in groups.items() if values}


def registration_digest(payload: Mapping) -> str:
    return canonical_digest(payload)


def d6_observed_value(trace: ResourceTraceV3, *, success: bool | None, horizon_seconds: float, reference_bits: float, reference_time: float) -> float | None:
    """External outcome plus actual transmitted serialized bits and capped
    simulated physical time. Submission/queue counters never replace cost.
    Unknown infrastructure records remain unknown for fixed-weight bounds.
    """
    from .metrics_v3 import d6_score
    if not math.isfinite(horizon_seconds) or horizon_seconds <= 0:
        raise ValueError("registered physical-time horizon must be positive")
    if trace.status in {RecordStatus.UNKNOWN, RecordStatus.INCOMPLETE} or success is None:
        return None
    if trace.status is not RecordStatus.VALID:
        raise ValueError("resource trace needs coverage adjudication")
    if type(trace.transmitted_bits) is not int or trace.transmitted_bits < 0 or not math.isfinite(trace.sim_time_s) or trace.sim_time_s < 0:
        raise ValueError("invalid observed resource counters")
    score = d6_score([{"success": success, "bits": trace.transmitted_bits, "time": min(trace.sim_time_s, horizon_seconds)}], reference_bits=reference_bits, reference_time=reference_time)
    if score.point is None:
        raise ValueError("D6 calibration reference is undefined")
    return score.point/100
