"""Typed evidence records for the revised D1--D6 protocol."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping


RECORDS_SCHEMA_VERSION = "heterocoord.records.v3"


class RecordStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"
    INCOMPLETE = "incomplete"
    NA = "na"


class Branch(str, Enum):
    FACT = "fact"
    REFERENCE = "reference"
    SHAM = "sham"
    INTERVENTION = "intervention"


@dataclass(frozen=True)
class IdentityV3:
    protocol_version: str
    scorebook_digest: str
    catalog_digest: str
    design_digest: str
    task_id: str
    anchor_id: str
    domain: str
    relation_signature: tuple[str, ...]
    difficulty: str
    condition_id: str
    system_id: str
    base_instance_id: str
    repeat_id: str
    branch: Branch
    checkpoint_id: str
    untreated_history_id: str = ""
    family_id: str | None = None
    case_id: str | None = None
    adaptation_unit_id: str | None = None
    source_reference_hash: str | None = None
    target_checkpoint_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("protocol_version", "scorebook_digest", "catalog_digest", "design_digest", "task_id", "anchor_id", "domain", "difficulty", "condition_id", "system_id", "base_instance_id", "repeat_id", "checkpoint_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not self.relation_signature:
            raise ValueError("relation_signature cannot be empty")
        object.__setattr__(self, "relation_signature", tuple(str(item) for item in self.relation_signature))
        object.__setattr__(self, "branch", Branch(self.branch))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["relation_signature"] = list(self.relation_signature)
        value["branch"] = self.branch.value
        return value

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IdentityV3":
        value = dict(payload)
        value["relation_signature"] = tuple(value.get("relation_signature", ()))
        return cls(**value)


@dataclass(frozen=True)
class EvidenceRecordV3:
    identity: IdentityV3
    status: RecordStatus = RecordStatus.VALID
    evidence_digests: tuple[str, ...] = ()
    invalid_reason: str | None = None
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, IdentityV3):
            raise ValueError("record identity must be a validated IdentityV3")
        object.__setattr__(self, "status", RecordStatus(self.status))
        object.__setattr__(self, "evidence_digests", tuple(str(item) for item in self.evidence_digests))
        object.__setattr__(self, "extra_metadata", dict(self.extra_metadata))
        if self.status in {RecordStatus.INVALID, RecordStatus.NA, RecordStatus.INCOMPLETE} and not self.invalid_reason:
            raise ValueError(f"{self.status.value} records require invalid_reason")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["identity"] = self.identity.to_dict()
        value["status"] = self.status.value
        value["evidence_digests"] = list(self.evidence_digests)
        return value


@dataclass(frozen=True)
class ContractRecordV3(EvidenceRecordV3):
    contract_revision: str = ""
    oracle_status: str = "unverified"
    witness_digest: str | None = None


@dataclass(frozen=True)
class CheckRecordV3(EvidenceRecordV3):
    check_id: str = ""
    category: str = ""
    passed: bool | None = None


@dataclass(frozen=True)
class ObligationOutcomeV3(EvidenceRecordV3):
    obligation_id: str = ""
    relation: str = ""
    participants: tuple[str, ...] = ()
    opportunity: bool = False
    completed: bool = False
    violated: bool = False
    disposition: str | None = None
    start_tick: int | None = None
    end_tick: int | None = None
    p3_subtype: str | None = None
    accepted_fraction: float | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.obligation_id, str) or not self.obligation_id.strip():
            raise ValueError("obligation_id is required")
        if self.relation not in {"P2", "P3", "P4"}:
            raise ValueError("obligation relation must be P2, P3, or P4")
        participants = tuple(str(item) for item in self.participants)
        if not participants or any(not item.strip() for item in participants) or len(set(participants)) != len(participants):
            raise ValueError("obligation participants must be unique nonempty identities")
        object.__setattr__(self, "participants", participants)
        if self.opportunity is not True:
            raise ValueError("obligation outcome must bind a frozen pre-action opportunity")
        for name in ("start_tick", "end_tick"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.start_tick is not None and self.end_tick is not None and self.end_tick < self.start_tick:
            raise ValueError("obligation end_tick cannot precede start_tick")
        if self.accepted_fraction is not None and (not math.isfinite(self.accepted_fraction) or not 0 <= self.accepted_fraction <= 1):
            raise ValueError("accepted_fraction must lie in [0,1]")
        completed_evidence = self.completed or self.accepted_fraction is not None
        if completed_evidence and self.violated:
            raise ValueError("obligation cannot be both completed and violated")
        if self.status is RecordStatus.VALID:
            inferred = "violated" if self.violated else ("completed" if completed_evidence else "unreached")
            if self.violated and not self.evidence_digests:
                raise ValueError("violated obligation requires external evidence")
        elif self.status in {RecordStatus.UNKNOWN, RecordStatus.INCOMPLETE}:
            if completed_evidence or self.violated:
                raise ValueError("unknown/incomplete obligation cannot claim an outcome")
            if not self.invalid_reason:
                raise ValueError("unknown/incomplete obligation requires a reason")
            inferred = self.status.value
        else:
            if completed_evidence or self.violated:
                raise ValueError("invalid/NA obligation cannot claim an outcome")
            inferred = self.status.value
        if self.disposition is not None and self.disposition != inferred:
            raise ValueError("obligation disposition contradicts completion/violation evidence")
        object.__setattr__(self, "disposition", inferred)
        if self.relation == "P3" and self.p3_subtype not in {"J", "S"}:
            raise ValueError("P3 subtype J/S belongs to each obligation")
        if self.relation != "P3" and self.p3_subtype is not None:
            raise ValueError("only P3 obligations have a J/S subtype")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ObligationOutcomeV3":
        value = dict(payload)
        identity = value.get("identity")
        if isinstance(identity, Mapping):
            value["identity"] = IdentityV3.from_dict(identity)
        value["participants"] = tuple(value.get("participants", ()))
        value["evidence_digests"] = tuple(value.get("evidence_digests", ()))
        return cls(**value)


@dataclass(frozen=True)
class DynamicItemOutcomeV3(EvidenceRecordV3):
    item_id: str = ""
    item_type: str = ""
    work_weight: float = 1.0
    legal_completion_fraction: float | None = None
    service_fraction: float | None = None
    deadline_tick: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not str(self.item_id).strip() or not str(self.item_type).strip():
            raise ValueError("dynamic item identity is required")
        if not math.isfinite(float(self.work_weight)) or float(self.work_weight) <= 0:
            raise ValueError("dynamic work_weight must be finite and positive")
        for name, value in (("legal_completion_fraction", self.legal_completion_fraction), ("service_fraction", self.service_fraction)):
            if value is not None and (not math.isfinite(float(value)) or not 0 <= float(value) <= 1):
                raise ValueError(f"{name} must be in [0,1]")
        if self.deadline_tick is not None and (type(self.deadline_tick) is not int or self.deadline_tick < 0):
            raise ValueError("deadline_tick must be a nonnegative integer")


@dataclass(frozen=True)
class AdaptationAttemptV3(EvidenceRecordV3):
    family_id: str = ""
    case_id: str = ""
    adaptation_unit_id: str = ""
    source_reference_hash: str = ""
    target_checkpoint_id: str = ""
    joint_acceptance: bool | None = None
    memory_reset: bool = False


@dataclass(frozen=True)
class ResourceTraceV3(EvidenceRecordV3):
    resource_id: str = ""
    submitted_bits: int = 0
    queued_bits: int = 0
    transmitted_bits: int = 0
    delivered_bits: int = 0
    expired_bits: int = 0
    sim_time_s: float = 0.0


@dataclass(frozen=True)
class CalibrationRecordV3(EvidenceRecordV3):
    calibration_id: str = ""
    coordinator_id: str = ""
    successful_run_ids: tuple[str, ...] = ()
    reference_bits_median: float | None = None
    reference_time_median: float | None = None


@dataclass(frozen=True)
class MetricResultV3(EvidenceRecordV3):
    score_id: str = ""
    point: float | None = None
    lower: float | None = None
    upper: float | None = None
    support_weight: float = 0.0
    component_contributions: Mapping[str, float] = field(default_factory=dict)
    estimator_family: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "component_contributions", dict(self.component_contributions))
        if self.point is None and (self.lower is None or self.upper is None) and self.status is RecordStatus.VALID:
            raise ValueError("valid MetricResultV3 needs a point or an explicit range")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("metric range is reversed")


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["RECORDS_SCHEMA_VERSION", "RecordStatus", "Branch", "IdentityV3", "EvidenceRecordV3", "ContractRecordV3", "CheckRecordV3", "ObligationOutcomeV3", "DynamicItemOutcomeV3", "AdaptationAttemptV3", "ResourceTraceV3", "CalibrationRecordV3", "MetricResultV3", "canonical_digest"]
