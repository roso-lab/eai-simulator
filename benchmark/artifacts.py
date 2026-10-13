"""Portable branch-local episode artifacts.

The runtime keeps its ledger in memory while an episode is executing.  This
module provides the small persistence seam used by experiment runners: one
JSON artifact per branch, written atomically and verified before it is read.
It stores protocol metadata, evaluator evidence and the hash-chained ledger;
it does not store or imply a physical checkpoint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .evidence import BranchLedger
from .protocol import BranchRole, BranchSpec, RunKey
from .runtime import EpisodeResult, RuntimeTaskBinding


ARTIFACT_FORMAT_VERSION = 1


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "tolist"):
        try:
            return _json_safe(value.tolist())
        except Exception:  # pragma: no cover - defensive for device tensors
            pass
    if hasattr(value, "__dict__"):
        fields = {
            str(key): _json_safe(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
        if fields:
            return {"type": f"{type(value).__module__}.{type(value).__name__}", "fields": fields}
    return str(value)


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run_key_payload(run_key: RunKey) -> dict[str, Any]:
    return {**run_key.__dict__, "branch_role": run_key.branch_role.value}


def _run_key_from_payload(payload: Mapping[str, Any]) -> RunKey:
    values = dict(payload)
    try:
        values["branch_role"] = BranchRole(values["branch_role"])
        return RunKey(**values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid artifact run_key") from exc


@dataclass(frozen=True)
class StoredBranchArtifact:
    """Validated artifact loaded from disk."""

    path: Path
    payload: Mapping[str, Any]

    @property
    def run_key(self) -> RunKey:
        return _run_key_from_payload(self.payload["run_key"])

    @property
    def ledger_digest(self) -> str:
        return str(self.payload["ledger"]["digest"])


class BranchArtifactStore:
    """Persist and validate one immutable JSON artifact per branch run."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_key: RunKey) -> Path:
        slug = run_key.as_string().replace("/", "__")
        suffix = hashlib.sha256(run_key.as_string().encode("utf-8")).hexdigest()[:12]
        return self.root / f"{slug}--{suffix}.json"

    def write(
        self,
        result: EpisodeResult,
        *,
        binding: RuntimeTaskBinding,
        branch: BranchSpec | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Path:
        """Write a sealed result, refusing to overwrite an existing branch."""

        run_key = branch.run_key if branch is not None else self._run_key_from_result(result, binding)
        destination = self.path_for(run_key)
        if destination.exists():
            raise FileExistsError(f"branch artifact already exists: {destination}")
        events = tuple(result.ledger_events)
        event_payloads = tuple(_json_safe(event.to_dict() if hasattr(event, "to_dict") else event) for event in events)
        digest = BranchLedger.verify_serialized_events(
            run_key,
            event_payloads,
            schema_version=result.ledger_schema_version,
            expected_digest=result.ledger_digest,
        )
        payload: dict[str, Any] = {
            "artifact_format_version": ARTIFACT_FORMAT_VERSION,
            "run_key": _run_key_payload(run_key),
            "branch": _json_safe(branch.to_dict()) if branch is not None else None,
            "binding": _json_safe(binding),
            "decision": _json_safe(result.decision),
            "executions": _json_safe(result.executions),
            "evaluation": _json_safe(result.evaluation),
            "observations": _json_safe(result.observations),
            "fleet_audit": _json_safe(result.fleet_audit),
            "ledger": {
                "schema_version": result.ledger_schema_version,
                "digest": digest,
                "events": list(event_payloads),
            },
            "metadata": _json_safe(metadata or {}),
        }
        payload["artifact_digest"] = _canonical_digest(payload)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(destination)
        return destination

    def read(self, run_key: RunKey) -> StoredBranchArtifact:
        path = self.path_for(run_key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read branch artifact: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"branch artifact is not valid JSON: {path}") from exc
        if int(payload.get("artifact_format_version", 0)) != ARTIFACT_FORMAT_VERSION:
            raise ValueError("unsupported branch artifact format version")
        stored_key = _run_key_from_payload(payload.get("run_key", {}))
        if stored_key != run_key:
            raise ValueError("branch artifact run_key mismatch")
        artifact_digest = str(payload.pop("artifact_digest", ""))
        if not artifact_digest or artifact_digest != _canonical_digest(payload):
            raise ValueError("branch artifact digest mismatch")
        ledger = payload.get("ledger")
        if not isinstance(ledger, Mapping):
            raise ValueError("branch artifact ledger is missing")
        events = ledger.get("events", ())
        if not isinstance(events, list):
            raise ValueError("branch artifact ledger events must be a list")
        BranchLedger.verify_serialized_events(
            run_key,
            events,
            schema_version=str(ledger.get("schema_version", "")),
            expected_digest=str(ledger.get("digest", "")),
        )
        payload["artifact_digest"] = artifact_digest
        return StoredBranchArtifact(path=path, payload=payload)

    @staticmethod
    def _run_key_from_result(result: EpisodeResult, binding: RuntimeTaskBinding) -> RunKey:
        del binding
        if result.run_key is None:
            raise ValueError("EpisodeResult.run_key is required when branch is omitted")
        return result.run_key


__all__ = ["ARTIFACT_FORMAT_VERSION", "BranchArtifactStore", "StoredBranchArtifact"]
