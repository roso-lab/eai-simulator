"""Branch-local evidence records and append-only event ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from .protocol import RunKey


class EvidenceStatus(str, Enum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"


class EvaluationStatus(str, Enum):
    """Terminal status owned by the external evaluator."""

    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "N/A"


@dataclass(frozen=True)
class EvidenceRecord:
    predicate_id: str
    predicate_version: str
    status: EvidenceStatus
    tick: int
    source: str
    evidence_hash: str = ""
    stability_window: int = 1
    reason: str = ""

    def __post_init__(self) -> None:
        for name in ("predicate_id", "predicate_version", "source"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "status", EvidenceStatus(self.status))
        if self.tick < 0:
            raise ValueError("evidence tick cannot be negative")
        if self.stability_window <= 0:
            raise ValueError("stability_window must be positive")
        object.__setattr__(self, "reason", str(self.reason))


@dataclass(frozen=True)
class EvaluationReport:
    """Evaluator-owned terminal report plus the evidence supporting it.

    The runtime records this report but never derives a terminal success from a
    skill plan, execution feedback, or replay status.
    """

    status: EvaluationStatus
    reason: str
    evidence: tuple[EvidenceRecord, ...] = ()
    evaluator_id: str = ""
    evaluator_version: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", EvaluationStatus(self.status))
        if not str(self.reason).strip():
            raise ValueError("evaluation reason is required")
        if not str(self.evaluator_id).strip():
            raise ValueError("evaluator_id is required")
        if not str(self.evaluator_version).strip():
            raise ValueError("evaluator_version is required")
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "evidence": [
                {
                    **asdict(item),
                    "status": item.status.value,
                }
                for item in self.evidence
            ],
        }


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    kind: str
    tick: int
    source: str
    run_key: RunKey
    payload: Mapping[str, Any]
    previous_hash: str
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["run_key"]["branch_role"] = self.run_key.branch_role.value
        result["payload"] = dict(self.payload)
        return result


class BranchLedger:
    """Small hash-chained ledger scoped to exactly one branch run."""

    def __init__(self, run_key: RunKey, *, schema_version: str = "benchmark.ledger.v1") -> None:
        self.run_key = run_key
        self.schema_version = str(schema_version)
        self._events: list[LedgerEvent] = []
        self._sealed = False

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    @property
    def sealed(self) -> bool:
        return self._sealed

    def append(self, kind: str, *, tick: int, source: str, payload: Mapping[str, Any] | None = None) -> LedgerEvent:
        if self._sealed:
            raise RuntimeError("cannot append to a sealed branch ledger")
        if not str(kind).strip() or not str(source).strip():
            raise ValueError("event kind and source are required")
        if tick < 0:
            raise ValueError("event tick cannot be negative")
        event_payload = dict(payload or {})
        previous_hash = self._events[-1].event_hash if self._events else "GENESIS"
        body = {
            "schema_version": self.schema_version,
            "sequence": len(self._events),
            "kind": str(kind),
            "tick": tick,
            "source": str(source),
            "run_key": {
                **self.run_key.__dict__,
                "branch_role": self.run_key.branch_role.value,
            },
            "payload": event_payload,
            "previous_hash": previous_hash,
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        event = LedgerEvent(
            sequence=len(self._events),
            kind=str(kind),
            tick=tick,
            source=str(source),
            run_key=self.run_key,
            payload=event_payload,
            previous_hash=previous_hash,
            event_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )
        self._events.append(event)
        return event

    def seal(self) -> str:
        self._sealed = True
        return self.digest()

    def digest(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "run_key": self.run_key.as_string(),
            "events": [event.event_hash for event in self._events],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def verify_serialized_events(
        run_key: RunKey,
        events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
        *,
        schema_version: str = "benchmark.ledger.v1",
        expected_digest: str | None = None,
    ) -> str:
        """Verify a persisted branch event chain without reopening it for writes.

        Artifact readers use this fail-closed check before exposing a stored
        branch result.  It intentionally accepts JSON-shaped mappings rather
        than constructing mutable :class:`LedgerEvent` instances.
        """

        previous_hash = "GENESIS"
        hashes: list[str] = []
        for sequence, raw in enumerate(events):
            event = dict(raw)
            if int(event.get("sequence", -1)) != sequence:
                raise ValueError("ledger event sequence is not contiguous")
            if str(event.get("previous_hash", "")) != previous_hash:
                raise ValueError("ledger event previous_hash does not match the chain")
            event_run_key = event.get("run_key")
            if not isinstance(event_run_key, Mapping):
                raise ValueError("ledger event run_key is missing")
            normalized_run_key = dict(event_run_key)
            normalized_run_key["branch_role"] = str(normalized_run_key.get("branch_role", ""))
            expected_run_key = {**run_key.__dict__, "branch_role": run_key.branch_role.value}
            if normalized_run_key != expected_run_key:
                raise ValueError("ledger event belongs to a different run key")
            body = {
                "schema_version": str(schema_version),
                "sequence": sequence,
                "kind": str(event.get("kind", "")),
                "tick": int(event.get("tick", -1)),
                "source": str(event.get("source", "")),
                "run_key": normalized_run_key,
                "payload": dict(event.get("payload", {})),
                "previous_hash": previous_hash,
            }
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            event_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            if str(event.get("event_hash", "")) != event_hash:
                raise ValueError("ledger event hash mismatch")
            hashes.append(event_hash)
            previous_hash = event_hash
        digest_payload = {
            "schema_version": str(schema_version),
            "run_key": run_key.as_string(),
            "events": hashes,
        }
        digest = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if expected_digest is not None and str(expected_digest) != digest:
            raise ValueError("ledger digest mismatch")
        return digest
