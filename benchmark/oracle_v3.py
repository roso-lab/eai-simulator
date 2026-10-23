"""Canonical, evidence-aware oracle boundary for the revised protocol.

The existing EAI oracle remains a useful capability/navigation pre-screen. This
adapter makes its limits explicit and prevents a partial certificate from being
treated as a complete timing/physics proof.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
import hashlib
import json
from typing import Any, Mapping


class OracleStatus(str, Enum):
    DEPLOYABLE = "deployable"
    TEAM_STATE_INFEASIBLE = "team_state_infeasible"
    UNVERIFIED = "feasibility_unverified"


@dataclass(frozen=True)
class OracleFinding:
    check_id: str
    status: str
    message: str
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if value["details"] is None:
            value.pop("details")
        return value


@dataclass(frozen=True)
class OracleCertificateV3:
    schema_version: str
    task_id: str
    status: OracleStatus
    findings: tuple[OracleFinding, ...]
    witness_digest: str | None
    proof_digest: str | None
    backend_id: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", OracleStatus(self.status))
        if not self.task_id.strip() or not self.backend_id.strip():
            raise ValueError("task_id and backend_id are required")
        if self.status is OracleStatus.DEPLOYABLE and not self.witness_digest:
            raise ValueError("deployable certificates require an independent witness digest")
        if self.status is OracleStatus.TEAM_STATE_INFEASIBLE and not self.proof_digest:
            raise ValueError("infeasible certificates require a proof digest")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["findings"] = [item.to_dict() for item in self.findings]
        value["limitations"] = list(self.limitations)
        return value

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


REQUIRED_SEMANTIC_CHECKS = ("capability", "role", "time", "joint", "resource", "mutex", "deadline")


def make_oracle_certificate(*, task_id: str, backend_id: str, findings: list[OracleFinding] | tuple[OracleFinding, ...], witness: Mapping[str, Any] | None = None, infeasibility_proof: Mapping[str, Any] | None = None, backend_complete: bool = False, limitations: tuple[str, ...] = ()) -> OracleCertificateV3:
    """Classify a result without claiming more than its evidence supports.

    A witness is only deployable when all seven semantic checks pass and the
    backend declares timing/concurrency/cooperative manipulation coverage.
    A failed explicit team-state proof is infeasible; timeout, disagreement,
    missing proof, or incomplete backend coverage stays unverified.
    """

    rows = tuple(findings)
    by_id = {row.check_id: row for row in rows}
    complete = all(by_id.get(check) and by_id[check].status == "pass" for check in REQUIRED_SEMANTIC_CHECKS)
    witness_digest = None if witness is None else _digest(witness)
    proof_digest = None if infeasibility_proof is None else _digest(infeasibility_proof)
    if proof_digest is not None and not complete:
        status = OracleStatus.TEAM_STATE_INFEASIBLE
    elif witness_digest is not None and complete and backend_complete:
        status = OracleStatus.DEPLOYABLE
    else:
        status = OracleStatus.UNVERIFIED
    return OracleCertificateV3("heterocoord.oracle.v3", task_id, status, rows, witness_digest, proof_digest, backend_id, tuple(limitations))


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["OracleStatus", "OracleFinding", "OracleCertificateV3", "REQUIRED_SEMANTIC_CHECKS", "make_oracle_certificate"]
