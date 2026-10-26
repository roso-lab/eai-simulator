"""Bridge verified evaluator artifacts into production raw receipts.

The branch runtime deliberately stores evaluator material as an opaque,
content-addressed artifact.  This module is the narrow production seam that
allows a caller to opt in to the receipt producer without teaching the branch
executor any paper semantics.  It accepts only the explicit
``observations.receipt_material`` object and never derives scientific facts
from process status, completion flags, physical-success booleans, or scores.
"""

from __future__ import annotations

from typing import Any, Mapping

from tools.benchmark_branch.evidence import verify_evaluator_evidence
from tools.benchmark_branch.models import BranchJob

from .receipt_producer_v1 import ReceiptProducerError, build_raw_receipt


BRIDGE_SCHEMA = "heterocoord.receipt-bridge.v1"
BRIDGE_VERSION = "receipt-bridge-v1"


class ReceiptBridgeError(ValueError):
    """Raised when a verified evaluator artifact lacks explicit receipt data."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptBridgeError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptBridgeError(f"{name} must be non-empty text")
    return value


def _bound_episode(
    material_episode: Mapping[str, Any],
    *,
    evaluator_version: str,
    evidence_hash: str,
    evidence_refs: list[str],
    artifact_result: Mapping[str, Any],
    artifact_manifest_digest: str | None = None,
) -> dict[str, Any]:
    """Bind artifact identity metadata while preserving evaluator facts.

    ``status`` is terminal evaluator metadata already checked by
    ``verify_evaluator_evidence``.  Duplicated values in the explicit episode
    are accepted only when identical; conflicting values are rejected rather
    than silently overwritten.
    """

    episode = dict(_mapping(material_episode, "receipt_material.episode"))
    expected = {
        "status": _text(artifact_result.get("status"), "artifact.result.status"),
        "evaluator_version": evaluator_version,
        "evidence_hash": evidence_hash,
        "evidence_refs": list(evidence_refs),
    }
    for key, value in expected.items():
        if key in episode and episode[key] != value:
            raise ReceiptBridgeError(f"receipt_material.episode.{key} does not match verified artifact")
        episode[key] = value
    if artifact_manifest_digest is not None:
        if episode.get("artifact_manifest_digest") not in (None, artifact_manifest_digest):
            raise ReceiptBridgeError(
                "receipt_material.episode.artifact_manifest_digest does not match supplied manifest"
            )
        episode["artifact_manifest_digest"] = artifact_manifest_digest
    return episode


def build_raw_receipt_from_artifact(
    *,
    artifact_root: str,
    job: BranchJob,
    evaluator_envelope: Mapping[str, Any],
    case_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    plan: Mapping[str, Any],
    parameters: Mapping[str, Any],
    receipt_id: str | None = None,
    artifact_manifest_digest: str | None = None,
) -> dict[str, Any]:
    """Build one raw receipt from a verified branch evaluator artifact.

    The artifact must contain exactly one explicit material object:

    ``observations.receipt_material = {episode, streams, facts}``

    ``verify_evaluator_evidence`` is called before any material is consumed.
    Its returned payload is the source of the artifact hash and evidence
    references.  The branch-level ``completed``, ``physical_success`` and
    predicate booleans remain metadata and are intentionally ignored.
    """

    try:
        payload = verify_evaluator_evidence(artifact_root, job, evaluator_envelope)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ReceiptBridgeError(f"evaluator artifact verification failed: {exc}") from exc

    observations = _mapping(payload.get("observations"), "artifact.observations")
    material = _mapping(observations.get("receipt_material"), "observations.receipt_material")
    if set(material) != {"episode", "streams", "facts"}:
        raise ReceiptBridgeError("observations.receipt_material schema mismatch")

    artifact_result = _mapping(payload.get("result"), "artifact.result")
    evaluator_version = _text(payload.get("evaluator_version"), "artifact.evaluator_version")
    # ``verify_evaluator_evidence`` accepts both the historical top-level
    # envelope and the executor's nested ``evaluator_verdict`` shape.  Use the
    # exact object that was verified as the provenance source in either case.
    verified_envelope = evaluator_envelope.get("evaluator_verdict", evaluator_envelope)
    verified_envelope = _mapping(verified_envelope, "evaluator_envelope")
    evidence_hash = _text(verified_envelope.get("evidence_hash"), "evaluator_envelope.evidence_hash")
    evidence_refs = verified_envelope.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs or any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
        raise ReceiptBridgeError("evaluator_envelope.evidence_refs must be a non-empty list")

    episode = _bound_episode(
        material["episode"],
        evaluator_version=evaluator_version,
        evidence_hash=evidence_hash,
        evidence_refs=evidence_refs,
        artifact_result=artifact_result,
        artifact_manifest_digest=artifact_manifest_digest,
    )
    try:
        return build_raw_receipt(
            case_manifest=case_manifest,
            catalog=catalog,
            plan=plan,
            parameters=parameters,
            episode=episode,
            streams=_mapping(material["streams"], "receipt_material.streams"),
            facts=_mapping(material["facts"], "receipt_material.facts"),
            receipt_id=receipt_id,
        )
    except ReceiptProducerError as exc:
        raise ReceiptBridgeError(f"receipt producer rejected verified material: {exc}") from exc


__all__ = [
    "BRIDGE_SCHEMA",
    "BRIDGE_VERSION",
    "ReceiptBridgeError",
    "build_raw_receipt_from_artifact",
]
