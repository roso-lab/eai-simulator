"""Typed physical-evidence certificate for the v3 release gate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .dynamic_v3 import DynamicDispositionV3, DynamicEventDispositionV3


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"unknown", "unresolved", "none", "null"}:
        raise ValueError(f"{name} is required and resolved")
    return value.strip()


@dataclass(frozen=True)
class PhysicalEvidenceCertificateV3:
    composition_digest: str
    source_commit: str
    closure_digest: str
    event_disposition: DynamicEventDispositionV3
    operation_receipt_digest: str
    remaining_feasibility_digest: str
    guard_digest: str
    world_preservation_digest: str
    transport_trace_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("composition_digest", "source_commit", "closure_digest", "operation_receipt_digest", "remaining_feasibility_digest", "guard_digest", "world_preservation_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if not isinstance(self.event_disposition, DynamicEventDispositionV3):
            raise TypeError("event_disposition must be DynamicEventDispositionV3")
        if self.event_disposition.closure_digest != self.closure_digest:
            raise ValueError("event disposition and certificate closure digest differ")
        if self.transport_trace_digest is not None:
            object.__setattr__(self, "transport_trace_digest", _required(self.transport_trace_digest, "transport_trace_digest"))
        if self.event_disposition.disposition is DynamicDispositionV3.UNKNOWN:
            raise ValueError("unknown event disposition cannot qualify physical evidence")

    @property
    def qualified(self) -> bool:
        return self.event_disposition.disposition in {
            DynamicDispositionV3.RECOVERED,
            DynamicDispositionV3.SAFE_STOP,
            DynamicDispositionV3.TERMINAL_FAILURE,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "physical-evidence.v3",
            "composition_digest": self.composition_digest,
            "source_commit": self.source_commit,
            "closure_digest": self.closure_digest,
            "event_disposition": self.event_disposition.to_dict(),
            "operation_receipt_digest": self.operation_receipt_digest,
            "remaining_feasibility_digest": self.remaining_feasibility_digest,
            "guard_digest": self.guard_digest,
            "world_preservation_digest": self.world_preservation_digest,
            "transport_trace_digest": self.transport_trace_digest,
            "qualified": self.qualified,
        }

    def digest(self) -> str:
        return _digest(self.to_dict())


__all__ = ["PhysicalEvidenceCertificateV3"]
