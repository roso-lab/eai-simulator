"""Project-local, diagnostic-only import boundary for tool trace evidence.

This adapter validates the tool envelope and binds it to the project ledger
without making it a D1--D6 result.  Observable/numeric trace agreement never
promotes native full-state or physical qualification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .evidence import BranchLedger, LedgerEvent

DIAGNOSTIC_SCHEMA = "heterocoord.diagnostic_trace_evidence.v1"
QUALIFICATION_FLAGS = (
    "formal_measured_qualified",
    "native_full_state_qualified",
    "process_isolation_qualified",
)
REQUIRED_COMPARISON_FIELDS = (
    "missing_branch_ids", "extra_branch_ids", "structural_mismatches",
    "max_numeric_error", "tolerance", "numeric_equivalence", "exact_equivalence",
)
SHUTDOWN_STATUSES = frozenset({"clean", "pending", "pending_timeout", "failed", "unknown"})
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOTS = (PROJECT_ROOT / "artifacts",)
PROVENANCE_FIELDS = (
    "command", "cwd", "interpreter", "exit_code", "output_path", "output_digest",
    "shutdown_status", "diagnostic_body_completed", "process_failure_scope",
)
ALLOWED_ENVELOPE_FIELDS = frozenset({
    "schema", "evidence_scope", "rankable", "experiment_identity",
    "experiment_identity_digest", "reference_paths", "candidate_path",
    "process_failure_scope", "shutdown_status", "missing_branch_ids",
    "extra_branch_ids", "structural_mismatches", "field_max_errors",
    "max_numeric_error", "tolerance", "numeric_equivalence",
    "exact_equivalence", "qualification_flags", "qualification_status",
    "recorded_at_ns", "provenance", "provenance_digest", "evidence_digest",
})


class DiagnosticEvidenceError(ValueError):
    """Raised when a diagnostic envelope is malformed or overclaims."""


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiagnosticEvidenceError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DiagnosticEvidenceError(f"{name} must be a sequence")
    if not allow_empty and not value:
        raise DiagnosticEvidenceError(f"{name} must be non-empty")
    return tuple(_string(item, f"{name} item") for item in value)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiagnosticEvidenceError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise DiagnosticEvidenceError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")
    # Preserve the JSON number representation supplied by the tool.  The v1
    # digest distinguishes 0 from 0.0, so validation must not silently
    # normalize a legal integer before the original envelope is audited.
    return value


def _envelope_digest(value: Any) -> str:
    """Canonical digest used by the tool's v1 envelope schema."""
    payload = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    import hashlib
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class DiagnosticEvidenceV3:
    experiment_identity: Mapping[str, Any]
    reference_paths: tuple[str, ...]
    candidate_path: str
    shutdown_status: str
    missing_branch_ids: tuple[str, ...]
    extra_branch_ids: tuple[str, ...]
    structural_mismatches: Mapping[str, Any]
    field_max_errors: Mapping[str, Any]
    max_numeric_error: float
    tolerance: float
    numeric_equivalence: bool
    exact_equivalence: bool
    process_failure_scope: str = "shared_session"
    recorded_at_ns: int | None = None
    provenance: Mapping[str, Any] | None = None
    source_schema: str = DIAGNOSTIC_SCHEMA
    _verified_ingress: bool = field(default=False, init=False, repr=False, compare=False)
    _source_evidence_digest: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_identity, Mapping) or not self.experiment_identity:
            raise DiagnosticEvidenceError("experiment_identity is required")
        object.__setattr__(self, "experiment_identity", _freeze(dict(self.experiment_identity)))
        object.__setattr__(self, "reference_paths", _strings(self.reference_paths, "reference_paths"))
        object.__setattr__(self, "candidate_path", _string(self.candidate_path, "candidate_path"))
        object.__setattr__(self, "shutdown_status", _string(self.shutdown_status, "shutdown_status"))
        if self.shutdown_status not in SHUTDOWN_STATUSES:
            raise DiagnosticEvidenceError(f"unsupported shutdown_status: {self.shutdown_status}")
        object.__setattr__(self, "missing_branch_ids", _strings(self.missing_branch_ids, "missing_branch_ids", allow_empty=True))
        object.__setattr__(self, "extra_branch_ids", _strings(self.extra_branch_ids, "extra_branch_ids", allow_empty=True))
        if not isinstance(self.structural_mismatches, Mapping) or not isinstance(self.field_max_errors, Mapping):
            raise DiagnosticEvidenceError("comparison mismatch fields must be mappings")
        object.__setattr__(self, "structural_mismatches", _freeze(dict(self.structural_mismatches)))
        object.__setattr__(self, "field_max_errors", _freeze(dict(self.field_max_errors)))
        object.__setattr__(self, "max_numeric_error", _number(self.max_numeric_error, "max_numeric_error"))
        object.__setattr__(self, "tolerance", _number(self.tolerance, "tolerance", positive=True))
        if type(self.numeric_equivalence) is not bool or type(self.exact_equivalence) is not bool:
            raise DiagnosticEvidenceError("equivalence flags must be bool")
        if self.process_failure_scope != "shared_session":
            raise DiagnosticEvidenceError("diagnostic evidence must use process_failure_scope=shared_session")
        if self.recorded_at_ns is not None and (type(self.recorded_at_ns) is not int or self.recorded_at_ns < 0):
            raise DiagnosticEvidenceError("recorded_at_ns must be a non-negative integer")
        if self.provenance is not None:
            if not isinstance(self.provenance, Mapping):
                raise DiagnosticEvidenceError("provenance must be a mapping")
            object.__setattr__(self, "provenance", _freeze(dict(self.provenance)))
            if self.provenance.get("shutdown_status") not in (None, self.shutdown_status):
                raise DiagnosticEvidenceError("provenance shutdown_status disagrees with evidence shutdown_status")
        if self.source_schema != DIAGNOSTIC_SCHEMA:
            raise DiagnosticEvidenceError("unsupported diagnostic evidence schema")

    @classmethod
    def from_tool_mapping(cls, payload: Mapping[str, Any]) -> "DiagnosticEvidenceV3":
        if not isinstance(payload, Mapping) or payload.get("schema") != DIAGNOSTIC_SCHEMA:
            raise DiagnosticEvidenceError("unsupported diagnostic evidence schema")
        unknown = set(payload) - ALLOWED_ENVELOPE_FIELDS
        if unknown:
            raise DiagnosticEvidenceError(f"diagnostic evidence has unknown fields: {sorted(unknown)}")
        if payload.get("evidence_scope") != "diagnostic_only" or payload.get("rankable") is not False:
            raise DiagnosticEvidenceError("tool evidence must be diagnostic_only and rankable=false")
        flags = payload.get("qualification_flags")
        if not isinstance(flags, Mapping) or any(flags.get(name) is not False for name in QUALIFICATION_FLAGS):
            raise DiagnosticEvidenceError("diagnostic evidence cannot contain a positive qualification flag")
        missing = [name for name in REQUIRED_COMPARISON_FIELDS if name not in payload]
        if missing:
            raise DiagnosticEvidenceError(f"diagnostic evidence missing fields: {missing}")
        if "evidence_digest" not in payload or "experiment_identity_digest" not in payload:
            raise DiagnosticEvidenceError("diagnostic evidence digests are required")
        identity = payload.get("experiment_identity", {})
        if payload.get("experiment_identity_digest") != _envelope_digest(identity):
            raise DiagnosticEvidenceError("experiment identity digest mismatch")
        if "provenance" in payload:
            if payload.get("provenance_digest") != _envelope_digest(payload.get("provenance")):
                raise DiagnosticEvidenceError("provenance digest mismatch")
        core = {key: value for key, value in payload.items()
                if key not in {"provenance", "provenance_digest", "evidence_digest"}}
        if payload.get("evidence_digest") != _envelope_digest(core):
            raise DiagnosticEvidenceError("diagnostic evidence digest mismatch")
        evidence = cls(
            experiment_identity=payload.get("experiment_identity", {}),
            reference_paths=tuple(payload.get("reference_paths", ())),
            candidate_path=payload.get("candidate_path", ""),
            shutdown_status=payload.get("shutdown_status", ""),
            missing_branch_ids=tuple(payload.get("missing_branch_ids", ())),
            extra_branch_ids=tuple(payload.get("extra_branch_ids", ())),
            structural_mismatches=payload.get("structural_mismatches", {}),
            field_max_errors=payload.get("field_max_errors", {}),
            max_numeric_error=payload.get("max_numeric_error"),
            tolerance=payload.get("tolerance"),
            numeric_equivalence=payload.get("numeric_equivalence"),
            exact_equivalence=payload.get("exact_equivalence"),
            process_failure_scope=payload.get("process_failure_scope", ""),
            recorded_at_ns=payload.get("recorded_at_ns"),
            provenance=payload.get("provenance"),
        )
        if payload.get("evidence_digest") != evidence.digest():
            raise DiagnosticEvidenceError("diagnostic evidence digest mismatch")
        object.__setattr__(evidence, "_verified_ingress", True)
        object.__setattr__(evidence, "_source_evidence_digest", str(payload["evidence_digest"]))
        return evidence

    @property
    def experiment_identity_digest(self) -> str:
        return _envelope_digest(_thaw(self.experiment_identity))

    @property
    def evidence_scope(self) -> str:
        return "diagnostic_only"

    @property
    def rankable(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": DIAGNOSTIC_SCHEMA, "evidence_scope": "diagnostic_only", "rankable": False,
            "experiment_identity": _thaw(self.experiment_identity),
            "experiment_identity_digest": self.experiment_identity_digest,
            "reference_paths": list(self.reference_paths), "candidate_path": self.candidate_path,
            "process_failure_scope": self.process_failure_scope, "shutdown_status": self.shutdown_status,
            "missing_branch_ids": list(self.missing_branch_ids), "extra_branch_ids": list(self.extra_branch_ids),
            "structural_mismatches": _thaw(self.structural_mismatches), "field_max_errors": _thaw(self.field_max_errors),
            "max_numeric_error": self.max_numeric_error, "tolerance": self.tolerance,
            "numeric_equivalence": self.numeric_equivalence, "exact_equivalence": self.exact_equivalence,
            "qualification_flags": {name: False for name in QUALIFICATION_FLAGS},
            "qualification_status": "diagnostic_only",
        }
        if self.provenance is not None:
            result["provenance"] = _thaw(self.provenance)
            result["provenance_digest"] = _envelope_digest(result["provenance"])
        if self.recorded_at_ns is not None:
            result["recorded_at_ns"] = self.recorded_at_ns
        digest_input = {key: value for key, value in result.items()
                        if key not in {"provenance", "provenance_digest", "evidence_digest"}}
        result["evidence_digest"] = _envelope_digest(digest_input)
        return result

    def digest(self) -> str:
        return str(self.to_dict()["evidence_digest"])

    def append_to_ledger(
        self,
        ledger: BranchLedger,
        *,
        tick: int,
        ledger_identity: Mapping[str, Any],
        source: str = "tool.diagnostic_trace",
    ) -> LedgerEvent:
        """Append only after explicit project branch identity binding."""
        if not isinstance(ledger, BranchLedger):
            raise DiagnosticEvidenceError("ledger must be a BranchLedger")
        if not self._verified_ingress or self.digest() != self._source_evidence_digest:
            raise DiagnosticEvidenceError("diagnostic evidence was not produced by a verified immutable ingress")
        if not isinstance(ledger_identity, Mapping):
            raise DiagnosticEvidenceError("ledger_identity is required for diagnostic binding")
        expected = dict(ledger.run_key.__dict__)
        expected["branch_role"] = ledger.run_key.branch_role.value
        if dict(ledger_identity) != expected:
            raise DiagnosticEvidenceError("diagnostic evidence does not match project ledger identity")
        identity = dict(self.experiment_identity)
        branch_role = identity.get("branch_role")
        branch_type = identity.get("branch_type")
        if branch_role is not None and branch_type is not None and branch_role != branch_type:
            raise DiagnosticEvidenceError("diagnostic envelope has conflicting branch_role and branch_type")
        role = branch_role if branch_role is not None else branch_type
        bound = {
            "base_instance_id": identity.get("base_instance_id"),
            "system_id": identity.get("system_id"),
            "condition_id": identity.get("condition_id"),
            "repeat_id": identity.get("repeat_id"),
            "checkpoint_round": identity.get("checkpoint_round"),
            "branch_role": role,
        }
        if any(value is None or (isinstance(value, str) and not value.strip()) for value in bound.values()):
            raise DiagnosticEvidenceError("diagnostic envelope experiment_identity lacks RunKey fields")
        for field, value in bound.items():
            if value != expected[field]:
                raise DiagnosticEvidenceError(
                    f"diagnostic envelope identity field {field} does not match project ledger identity")
        if "untreated_history_id" not in identity:
            raise DiagnosticEvidenceError("diagnostic envelope experiment_identity lacks untreated_history_id")
        if identity["untreated_history_id"] != expected["untreated_history_id"]:
            raise DiagnosticEvidenceError(
                "diagnostic envelope identity field untreated_history_id does not match project ledger identity")
        return ledger.append("diagnostic_trace_comparison", tick=tick, source=source, payload=self.to_dict())

    def write_json(self, path: str | Path) -> Path:
        if not self._verified_ingress or self.digest() != self._source_evidence_digest:
            raise DiagnosticEvidenceError("diagnostic evidence was not produced by a verified immutable ingress")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"diagnostic evidence already exists: {destination}")
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(destination)
        return destination


def _artifact_roots(roots: Sequence[str | Path] | None) -> tuple[Path, ...]:
    selected = DEFAULT_ARTIFACT_ROOTS if roots is None else tuple(Path(item) for item in roots)
    if not selected:
        raise DiagnosticEvidenceError("at least one registered artifact root is required")
    resolved = []
    for root in selected:
        if root.is_symlink():
            raise DiagnosticEvidenceError(f"artifact root cannot be a symlink: {root}")
        value = root.resolve(strict=False)
        if not value.is_dir():
            raise DiagnosticEvidenceError(f"artifact root must be an existing directory: {root}")
        resolved.append(value)
    return tuple(resolved)


def _contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _validate_artifact_path(value: Any, name: str, roots: Sequence[Path]) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DiagnosticEvidenceError(f"{name} must be a non-empty path")
    raw = Path(value)
    if not raw.is_absolute():
        raise DiagnosticEvidenceError(f"{name} must be an absolute registered artifact path")
    if _contains_symlink(raw):
        raise DiagnosticEvidenceError(f"{name} cannot contain a symlink")
    resolved = raw.resolve(strict=False)
    if not any(resolved.is_relative_to(root) for root in roots):
        raise DiagnosticEvidenceError(f"{name} escapes registered artifact roots")
    if not resolved.is_file() or resolved.is_symlink():
        raise DiagnosticEvidenceError(f"{name} must point to a regular file")
    return resolved


def _validate_provenance(provenance: Mapping[str, Any], *, shutdown_status: str,
                         process_failure_scope: str, roots: Sequence[Path]) -> None:
    missing = [name for name in PROVENANCE_FIELDS if name not in provenance]
    if missing:
        raise DiagnosticEvidenceError(f"provenance missing required fields: {missing}")
    for name in ("command", "cwd", "interpreter", "output_digest", "shutdown_status", "process_failure_scope"):
        _string(provenance[name], f"provenance {name}")
    if type(provenance["exit_code"]) is not int:
        raise DiagnosticEvidenceError("provenance exit_code must be an integer")
    if type(provenance["diagnostic_body_completed"]) is not bool:
        raise DiagnosticEvidenceError("provenance diagnostic_body_completed must be a bool")
    output = _validate_artifact_path(provenance["output_path"], "provenance output_path", roots)
    if not isinstance(provenance["output_digest"], str) or not provenance["output_digest"].startswith("sha256:"):
        raise DiagnosticEvidenceError("provenance output_digest must use sha256:<hex>")
    import hashlib
    actual = "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()
    if actual != provenance["output_digest"]:
        raise DiagnosticEvidenceError("provenance output_digest does not match output_path bytes")
    if "output_size" in provenance:
        if type(provenance["output_size"]) is not int or provenance["output_size"] < 0:
            raise DiagnosticEvidenceError("provenance output_size must be a non-negative integer")
        if provenance["output_size"] != output.stat().st_size:
            raise DiagnosticEvidenceError("provenance output_size does not match output_path bytes")
    if provenance["shutdown_status"] != shutdown_status:
        raise DiagnosticEvidenceError("provenance shutdown_status disagrees with evidence shutdown_status")
    if provenance["process_failure_scope"] != process_failure_scope:
        raise DiagnosticEvidenceError("provenance process_failure_scope disagrees with evidence scope")
    if shutdown_status == "clean" and (provenance["exit_code"] != 0 or not provenance["diagnostic_body_completed"]):
        raise DiagnosticEvidenceError("clean shutdown requires exit_code=0 and completed diagnostic body")
    if shutdown_status in {"pending_timeout", "shutdown_timeout"} and not provenance["diagnostic_body_completed"]:
        raise DiagnosticEvidenceError("timeout shutdown requires a completed diagnostic body")
    if provenance["exit_code"] == 124 and shutdown_status not in {"pending_timeout", "shutdown_timeout"}:
        raise DiagnosticEvidenceError("exit_code=124 requires timeout shutdown status")

def read_diagnostic_evidence_v3(
    path: str | Path, *, artifact_roots: Sequence[str | Path] | None = None,
) -> DiagnosticEvidenceV3:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticEvidenceError(f"cannot read diagnostic evidence: {path}") from exc
    evidence = DiagnosticEvidenceV3.from_tool_mapping(payload)
    if payload.get("evidence_digest") != evidence.digest():
        raise DiagnosticEvidenceError("diagnostic evidence digest mismatch")
    roots = _artifact_roots(artifact_roots)
    for index, reference in enumerate(evidence.reference_paths):
        _validate_artifact_path(reference, f"reference_paths[{index}]", roots)
    _validate_artifact_path(evidence.candidate_path, "candidate_path", roots)
    if evidence.provenance is not None:
        _validate_provenance(
            evidence.provenance,
            shutdown_status=evidence.shutdown_status,
            process_failure_scope=evidence.process_failure_scope,
            roots=roots,
        )
    return evidence


__all__ = ["DIAGNOSTIC_SCHEMA", "QUALIFICATION_FLAGS", "DiagnosticEvidenceError", "DiagnosticEvidenceV3", "read_diagnostic_evidence_v3"]
