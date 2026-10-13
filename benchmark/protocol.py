"""Versioned, dependency-light benchmark protocol contracts.

These records define experiment identity and branch semantics.  They do not
capture or restore physics and they do not decide whether a task succeeded.
Those responsibilities belong to the replay adapter and evaluator layers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Mapping


class ProtocolError(ValueError):
    """Raised when a benchmark protocol record is malformed."""


class BranchRole(str, Enum):
    FACT = "fact"
    REFERENCE = "reference"
    SHAM = "sham"
    INTERVENTION = "intervention"


class ClaimScope(str, Enum):
    DEBUG = "debug"
    NOMINAL_PHYSICAL = "nominal-physical"
    OBSERVABLE_STATE_REPLAY = "observable-state-replay"
    STRICT_PAIRED = "strict-paired"
    INFERENTIAL_RELEASE = "inferential-release"


def _required(value: object, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ProtocolError(f"{name} is required")
    return result


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProtocolContract:
    """Identity and public contract for one benchmark task family."""

    contract_id: str
    version: str
    domain: str
    primary_label: str
    relation_signature: tuple[str, ...]
    observation_schema: str
    difficulty: str = "unspecified"
    p3_subtype: str | None = None
    milestones: tuple[str, ...] = ()
    horizon_ticks: int | None = None
    initial_state_digest: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("contract_id", "version", "domain", "primary_label", "observation_schema"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        relations = tuple(_required(item, "relation_signature item") for item in self.relation_signature)
        if not relations:
            raise ProtocolError("relation_signature cannot be empty")
        object.__setattr__(self, "relation_signature", relations)
        object.__setattr__(self, "milestones", tuple(_required(item, "milestone") for item in self.milestones))
        if self.horizon_ticks is not None and self.horizon_ticks <= 0:
            raise ProtocolError("horizon_ticks must be positive when provided")
        if self.p3_subtype is not None:
            object.__setattr__(self, "p3_subtype", _required(self.p3_subtype, "p3_subtype"))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["relation_signature"] = list(self.relation_signature)
        result["milestones"] = list(self.milestones)
        return result

    def digest(self) -> str:
        return _canonical_digest(self.to_dict())


@dataclass(frozen=True)
class CheckpointRef:
    """A task-level reference to a checkpoint selected by the protocol."""

    checkpoint_id: str
    kind: str
    step: int
    artifact_uri: str
    digest: str
    source_run: str

    def __post_init__(self) -> None:
        for name in ("checkpoint_id", "kind", "artifact_uri", "digest", "source_run"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.step < 0:
            raise ProtocolError("checkpoint step cannot be negative")


@dataclass(frozen=True)
class RunKey:
    """Stable identity shared by fact/reference/sham/intervention branches."""

    base_instance_id: str
    system_id: str
    condition_id: str
    repeat_id: str
    checkpoint_round: str
    branch_role: BranchRole = BranchRole.FACT
    untreated_history_id: str = ""

    def __post_init__(self) -> None:
        for name in (
            "base_instance_id",
            "system_id",
            "condition_id",
            "repeat_id",
            "checkpoint_round",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(self, "branch_role", BranchRole(self.branch_role))
        object.__setattr__(self, "untreated_history_id", str(self.untreated_history_id).strip())

    def pair_key(self) -> tuple[str, str, str, str, str]:
        """Return the branch-independent identity used for paired risk sets."""

        return (
            self.base_instance_id,
            self.system_id,
            self.condition_id,
            self.repeat_id,
            self.checkpoint_round,
        )

    def as_string(self) -> str:
        return "/".join(
            (
                self.base_instance_id,
                self.system_id,
                self.condition_id,
                self.repeat_id,
                self.checkpoint_round,
                self.branch_role.value,
            )
        )


@dataclass(frozen=True)
class BranchSpec:
    """Immutable branch role and treatment declaration."""

    run_key: RunKey
    checkpoint: CheckpointRef
    treatment_id: str = "none"
    authority: str = "protocol"
    non_target_budget: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.run_key.branch_role is BranchRole.SHAM and self.treatment_id != "none":
            raise ProtocolError("sham branches cannot declare an effective treatment")
        if self.run_key.branch_role is BranchRole.INTERVENTION and self.treatment_id == "none":
            raise ProtocolError("intervention branches require a treatment_id")
        object.__setattr__(self, "treatment_id", _required(self.treatment_id, "treatment_id"))
        object.__setattr__(self, "authority", _required(self.authority, "authority"))
        object.__setattr__(self, "non_target_budget", dict(self.non_target_budget))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_key": {
                **self.run_key.__dict__,
                "branch_role": self.run_key.branch_role.value,
            },
            "checkpoint": self.checkpoint.__dict__,
            "treatment_id": self.treatment_id,
            "authority": self.authority,
            "non_target_budget": dict(self.non_target_budget),
        }
