"""Portable, branch-bound evaluator artifacts; no task success predicates live here.

Hashes establish integrity and identity, not evaluator honesty or causal fidelity.
The caller must supply a trusted evaluator and retain its raw observations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .models import BranchJob, canonical_digest

SCHEMA = "eai.evaluator_evidence.v1"
PREDICATES = ("terminal_predicate", "safety_predicate", "feasibility_predicate")


def _binding(job: BranchJob) -> str:
    return canonical_digest(job.identity())


def write_evaluator_evidence(root: str | Path, job: BranchJob, *,
                             evaluator_version: str, result: Mapping[str, Any],
                             observations: Any) -> dict[str, Any]:
    """Called by an external evaluator after it computes the result.

    Each attempt gets its own immutable artifact, including negative results.
    Observations must be JSON serializable and sufficient for a task-specific
    auditor; this collector does not interpret their domain meaning.
    """
    root = Path(root).resolve()
    payload = {"schema": SCHEMA, "job_digest": _binding(job),
               "evaluator_version": evaluator_version, "result": dict(result),
               "observations": observations}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False,
                     separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    relative = Path("evidence") / f"{digest}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError("evaluator artifact collision")
    envelope = {**dict(result), "evaluator_version": evaluator_version,
                "evidence_source": "external_evaluator", "evidence_hash": digest,
                "evidence_refs": [relative.as_posix()]}
    verify_evaluator_evidence(root, job, envelope)
    return envelope


def verify_evaluator_evidence(root: str | Path, job: BranchJob,
                              result: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read the artifact and reject tampering, wrong branches and stale retries."""
    if result.get("evidence_source") != "external_evaluator":
        raise ValueError("external evaluator evidence required")
    version = result.get("evaluator_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("evaluator_version must be nonempty text")
    refs = result.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str):
        raise ValueError("evidence_refs must contain one relative JSON artifact")
    root = Path(root).resolve()
    ref = Path(refs[0])
    path = (root / ref).resolve()
    if ref.is_absolute() or not path.is_relative_to(root) or ".." in ref.parts:
        raise ValueError("evidence path must remain inside run directory")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != result.get("evidence_hash"):
        raise ValueError("evaluator evidence hash mismatch")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("evaluator artifact must be a JSON object")
    if payload.get("schema") != SCHEMA or payload.get("job_digest") != _binding(job):
        raise ValueError("evaluator evidence schema or branch identity mismatch")
    if payload.get("evaluator_version") != version or not payload.get("observations"):
        raise ValueError("evaluator version or raw observations missing/mismatched")
    verdict = payload.get("result", {})
    for name in ("status", "completed", "physical_success", *PREDICATES):
        if name not in verdict or verdict[name] != result.get(name):
            raise ValueError(f"evaluator verdict mismatch: {name}")
    if type(verdict["completed"]) is not bool:
        raise ValueError("completed must be boolean")
    if verdict["physical_success"] is not None and type(verdict["physical_success"]) is not bool:
        raise ValueError("physical_success must be boolean or null")
    if any(type(verdict[name]) is not bool for name in PREDICATES):
        raise ValueError("evaluator predicates must be explicit booleans")
    if verdict["physical_success"] is True and not (
        verdict["completed"] and all(verdict[name] for name in PREDICATES)
    ):
        raise ValueError("physical success contradicts evaluator predicates")
    return payload
