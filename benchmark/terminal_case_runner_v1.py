"""Seal evaluator terminal material and hand it to the production pipeline.

This module is the map-ready orchestration seam for a measured case.  It does
not execute a simulator and it does not derive scientific facts from process
status.  The task-specific evaluator supplies explicit terminal material and
the runtime supplies an explicit evaluator verdict; this module only seals,
indexes, verifies and forwards those objects to the already versioned receipt
and results layers.

The intended flow after a semantic map is frozen is::

    evaluator runtime payload
      -> collect_receipt_material
      -> content-addressed evidence artifact
      -> artifact manifest
      -> raw receipt/readiness
      -> run_production_case_from_artifacts

The returned report remains ``measured_unqualified`` until the formal release
matrix opens the gates.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .case_readiness_v1 import CaseReadinessError, audit_case_receipts
from tools.benchmark_branch.models import BranchJob, canonical_digest
from .production_results_v1 import ProductionRunError, run_production_case_from_artifacts
from .receipt_bridge_v1 import ReceiptBridgeError, build_raw_receipt_from_artifact
from .receipt_producer_v1 import ReceiptProducerError
from tools.benchmark_branch.receipt_material import (
    ReceiptMaterialError,
    collect_receipt_material,
    write_terminal_receipt_evidence,
)


RUNNER_SCHEMA = "heterocoord.terminal-case-runner.v1"
RUNNER_VERSION = "terminal-case-runner-v1"


class TerminalCaseRunnerError(ValueError):
    """Raised when terminal material cannot be sealed without inference."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TerminalCaseRunnerError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TerminalCaseRunnerError(f"{name} must be non-empty text")
    return value


def _artifact_entry(root: Path, envelope: Mapping[str, Any], label: str) -> dict[str, Any]:
    evidence_hash = _text(envelope.get("evidence_hash"), f"{label}.evidence_hash")
    refs = envelope.get("evidence_refs")
    if not isinstance(refs, list) or len(refs) != 1:
        raise TerminalCaseRunnerError(f"{label}.evidence_refs must contain one artifact")
    relative = _text(refs[0], f"{label}.evidence_refs[0]")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
        stat = path.stat()
    except (OSError, ValueError) as exc:
        raise TerminalCaseRunnerError(f"{label} evidence artifact is unavailable") from exc
    if not path.is_file():
        raise TerminalCaseRunnerError(f"{label} evidence artifact is not a regular file")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != evidence_hash:
        raise TerminalCaseRunnerError(f"{label} evidence hash changed after sealing")
    return {"path": relative, "sha256": evidence_hash, "size_bytes": stat.st_size}


def seal_terminal_artifacts(
    *,
    terminals: Sequence[Mapping[str, Any]],
    artifact_root: str | Path,
    case_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    plan: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal evaluator-owned terminal payloads and build verified raw receipts.

    Each terminal item must contain exactly ``job``, ``evaluator``,
    ``runtime`` and ``evaluator_result``.  The result is deliberately passed
    through as an explicit evaluator verdict; this function never derives its
    booleans from ``runtime`` or from process/lifecycle fields.
    """

    if not isinstance(terminals, Sequence) or isinstance(terminals, (str, bytes)) or not terminals:
        raise TerminalCaseRunnerError("terminals must be a non-empty sequence")
    root = Path(artifact_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    artifact_manifest: dict[str, dict[str, Any]] = {}
    artifact_inputs: list[dict[str, Any]] = []
    receipt_ids: list[str] = []
    seen_jobs: set[str] = set()
    envelopes: list[dict[str, Any]] = []
    try:
        for index, item in enumerate(terminals):
            if not isinstance(item, Mapping):
                raise TerminalCaseRunnerError(f"terminals[{index}] must be an object")
            required = {"job", "evaluator", "runtime", "evaluator_result"}
            if set(item) - (required | {"label", "receipt_id"}) != set() or not required.issubset(item):
                raise TerminalCaseRunnerError(
                    f"terminals[{index}] must contain job/evaluator/runtime/evaluator_result"
                )
            job = item["job"]
            if not isinstance(job, BranchJob):
                raise TerminalCaseRunnerError(f"terminals[{index}].job must be a BranchJob")
            identity_key = canonical_digest(job.identity())
            if identity_key in seen_jobs:
                raise TerminalCaseRunnerError(f"duplicate terminal job at index {index}")
            seen_jobs.add(identity_key)
            evaluator_result = _mapping(item["evaluator_result"], f"terminals[{index}].evaluator_result")
            label = _text(item.get("label", f"evaluator-{job.branch_id}"), f"terminals[{index}].label")
            if label in artifact_manifest:
                raise TerminalCaseRunnerError(f"duplicate artifact label: {label}")
            material = collect_receipt_material(item["evaluator"], item["runtime"], job)
            envelope = write_terminal_receipt_evidence(
                root,
                job,
                evaluator_version=_text(material["episode"].get("evaluator_version"), "episode.evaluator_version"),
                result=evaluator_result,
                material=material,
            )
            artifact_manifest[label] = _artifact_entry(root, envelope, label)
            receipt_id = _text(item.get("receipt_id", job.branch_id), f"terminals[{index}].receipt_id")
            artifact_inputs.append({"job": job, "evaluator_envelope": envelope})
            receipt_ids.append(receipt_id)
            envelopes.append(dict(envelope))
    except (ReceiptMaterialError, OSError, TypeError, ValueError, KeyError) as exc:
        if isinstance(exc, TerminalCaseRunnerError):
            raise
        raise TerminalCaseRunnerError(f"terminal evidence sealing failed: {exc}") from exc

    manifest_digest = canonical_digest(artifact_manifest)
    receipts = []
    for index, item in enumerate(artifact_inputs):
        try:
            receipts.append(
                build_raw_receipt_from_artifact(
                    artifact_root=str(root),
                    job=item["job"],
                    evaluator_envelope=item["evaluator_envelope"],
                    case_manifest=case_manifest,
                    catalog=catalog,
                    plan=plan,
                    parameters=parameters,
                    receipt_id=receipt_ids[index],
                    artifact_manifest_digest=manifest_digest,
                )
            )
        except (ReceiptBridgeError, ReceiptProducerError, TypeError, ValueError, KeyError) as exc:
            raise TerminalCaseRunnerError(f"terminal receipt bridge failed at index {index}: {exc}") from exc

    try:
        readiness = audit_case_receipts(
            receipts,
            case_manifest=case_manifest,
            artifact_manifest=artifact_manifest,
            artifact_root=root,
        )
    except (CaseReadinessError, TypeError, ValueError, KeyError) as exc:
        raise TerminalCaseRunnerError(f"case receipt readiness failed: {exc}") from exc

    return {
        "schema": RUNNER_SCHEMA,
        "runner_version": RUNNER_VERSION,
        "artifact_root": str(root),
        "artifact_manifest": artifact_manifest,
        "artifact_manifest_digest": manifest_digest,
        "artifact_inputs": artifact_inputs,
        "evaluator_envelopes": envelopes,
        "receipts": receipts,
        "readiness": readiness,
        "formal_gates": {
            "rankable": False,
            "paper_score_eligible": False,
            "physical_qualified": False,
        },
    }


def run_production_from_terminals(
    *,
    terminals: Sequence[Mapping[str, Any]],
    artifact_root: str | Path,
    case_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    plan: Mapping[str, Any],
    parameters: Mapping[str, Any],
    calibration: Mapping[str, Any],
    output: str | Path,
    panel_bindings: Mapping[str, Mapping[str, Any]],
    panel_projection_binding: Mapping[str, Any],
    panel_endpoint_receipts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    panel_bootstrap_resamples: int = 0,
    panel_bootstrap_seed: int = 0,
    panel_structured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal terminal artifacts, then invoke the fixed-panel production seam."""

    sealed = seal_terminal_artifacts(
        terminals=terminals,
        artifact_root=artifact_root,
        case_manifest=case_manifest,
        catalog=catalog,
        plan=plan,
        parameters=parameters,
    )
    try:
        report = run_production_case_from_artifacts(
            artifact_inputs=sealed["artifact_inputs"],
            case_manifest=case_manifest,
            catalog=catalog,
            plan=plan,
            parameters=parameters,
            calibration=calibration,
            artifact_manifest=sealed["artifact_manifest"],
            artifact_root=sealed["artifact_root"],
            output=output,
            panel_bindings=panel_bindings,
            panel_endpoint_receipts=panel_endpoint_receipts,
            panel_bootstrap_resamples=panel_bootstrap_resamples,
            panel_bootstrap_seed=panel_bootstrap_seed,
            panel_projection_binding=panel_projection_binding,
            panel_structured=panel_structured,
        )
    except (ProductionRunError, TypeError, ValueError, KeyError) as exc:
        raise TerminalCaseRunnerError(f"production results run failed: {exc}") from exc
    return {"sealed": sealed, "production": report}


__all__ = [
    "RUNNER_SCHEMA",
    "RUNNER_VERSION",
    "TerminalCaseRunnerError",
    "seal_terminal_artifacts",
    "run_production_from_terminals",
]
