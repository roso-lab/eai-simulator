"""Auditable fresh-scene receipts for fact-only physical diagnostics.

The frozen checkpoint digest identifies an initialization specification, not a
captured simulator state. Readback compares named scalar observations against
that specification. This does not certify hidden solver state or causal replay.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from .models import BranchJob, canonical_digest


FRESH = "fresh_initialization_v1"
SPEC_SCHEMA = "heterocoord.fresh_initialization.v1"


def receipt_mode(job: BranchJob) -> str:
    mode = job.metadata.get("checkpoint_receipt_mode", "restore")
    if mode not in ("restore", FRESH):
        raise ValueError("unknown checkpoint_receipt_mode")
    return mode


def _numbers(value, label):
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{label} must be a nonempty scalar mapping")
    for key, number in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{label} keys must be nonempty strings")
        if type(number) not in (int, float) or not math.isfinite(number):
            raise ValueError(f"{label}.{key} must be a finite number")
    return value


def validate_initialization_job(job: BranchJob, *, session_backend: bool) -> None:
    if receipt_mode(job) != FRESH:
        return
    if job.execution_scope != "measured_diagnostic" or job.branch_type != "fact" or not session_backend:
        raise ValueError("fresh initialization requires a fact-only measured_diagnostic session backend")
    if job.restore_checkpoint_digest:
        raise ValueError("fresh initialization cannot claim a restore checkpoint digest")
    spec = job.metadata.get("initialization_spec")
    if not isinstance(spec, Mapping) or set(spec) != {"schema", "expected", "absolute_tolerances"}:
        raise ValueError("invalid initialization specification")
    if spec["schema"] != SPEC_SCHEMA:
        raise ValueError("invalid initialization specification schema")
    expected = _numbers(spec["expected"], "expected")
    tolerances = _numbers(spec["absolute_tolerances"], "absolute_tolerances")
    if set(expected) != set(tolerances) or any(value < 0 for value in tolerances.values()):
        raise ValueError("initialization tolerances must match observations and be nonnegative")
    if job.checkpoint_digest != canonical_digest(spec):
        raise ValueError("initialization specification digest differs from frozen job")


def initialization_receipt(job: BranchJob, readback: Mapping) -> dict:
    """Make a receipt from actual readback; the caller supplies domain semantics."""
    validate_initialization_job(job, session_backend=True)
    if receipt_mode(job) != FRESH:
        raise ValueError("job does not use fresh initialization")
    observed = dict(_numbers(readback, "readback"))
    spec = job.metadata["initialization_spec"]
    if set(observed) != set(spec["expected"]):
        raise ValueError("initialization readback keys differ from frozen specification")
    checks = {key: abs(observed[key] - expected) <= spec["absolute_tolerances"][key]
              for key, expected in spec["expected"].items()}
    return {"checkpoint_receipt_mode": FRESH, "initialization_spec_digest": job.checkpoint_digest,
            "readback": observed, "readback_digest": canonical_digest(observed),
            "initialization_checks": checks, "initialization_passed": all(checks.values())}


def verify_initialization_receipt(job: BranchJob, row: Mapping) -> bool:
    """Recompute checks and hashes, including failed (well-formed) receipts."""
    if "restore_passed" in row or "restore_status" in row:
        raise ValueError("fresh initialization receipt cannot claim checkpoint restoration")
    expected = initialization_receipt(job, row.get("readback", {}))
    for key, value in expected.items():
        # Canonical comparison distinguishes True/1 and rejects nonfinite data.
        if key not in row or canonical_digest(row[key]) != canonical_digest(value):
            raise ValueError(f"initialization receipt mismatch: {key}")
    return expected["initialization_passed"]
