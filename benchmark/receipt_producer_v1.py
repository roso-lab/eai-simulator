"""Assemble evaluator-owned ledgers into a raw evaluator receipt.

This module is deliberately narrower than the normalizer.  It is the seam
between a terminal evaluator episode and ``receipt_adapter_v3``: identity is
copied from a bound case manifest, while streams and D1--D6 facts must be
provided explicitly by the evaluator.  No process exit, completion flag, or
caller score is converted into a scientific fact here.

The evaluator artifact hash and the receipt-envelope hash are different
integrity layers.  The former is retained in ``facts.source_evidence_hash``;
the latter is written to ``evaluator.evidence_hash`` so the adapter can bind
the complete receipt envelope before any result calculation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .case_manifest_v1 import CaseManifestError, validate_case_manifest
from .receipt_adapter_v3 import RAW_RECEIPT_SCHEMA, ReceiptAdapterError, _validate_receipt
from .records_v3 import canonical_digest


PRODUCER_SCHEMA = "heterocoord.receipt-producer.v1"
PRODUCER_VERSION = "receipt-producer-v1"
_ALLOWED_STATUSES = {
    "valid_terminal", "safe_stop", "team_state_infeasible", "method_timeout",
    "malformed_action", "crashed", "infrastructure_invalid",
}
_BRANCHES = {"fact", "reference", "sham", "intervention"}


class ReceiptProducerError(ValueError):
    """Raised when terminal evaluator material cannot form a raw receipt."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptProducerError(f"{name} must be non-empty text")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptProducerError(f"{name} must be an object")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ReceiptProducerError(f"{name} must be a lowercase SHA-256")
    return value


def _receipt_envelope_digest(receipt: Mapping[str, Any]) -> str:
    envelope = dict(receipt)
    evaluator = dict(_mapping(envelope["evaluator"], "evaluator"))
    evaluator.pop("evidence_hash", None)
    envelope["evaluator"] = evaluator
    return hashlib.sha256(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_raw_receipt(
    *,
    case_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    plan: Mapping[str, Any],
    parameters: Mapping[str, Any],
    episode: Mapping[str, Any],
    streams: Mapping[str, Any],
    facts: Mapping[str, Any],
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """Build and validate one raw receipt from explicit evaluator material.

    ``streams`` and ``facts`` are not derived from the episode row.  They are
    evaluator-owned observations and must already use the receipt schema.  The
    function only binds identity, copies terminal evaluator metadata, records
    the source artifact hash, and computes the receipt-envelope hash.
    """

    try:
        case = validate_case_manifest(case_manifest)
    except CaseManifestError as exc:
        raise ReceiptProducerError(f"invalid case manifest: {exc}") from exc
    catalog = dict(_mapping(catalog, "catalog"))
    plan = dict(_mapping(plan, "plan"))
    parameters = dict(_mapping(parameters, "parameters"))
    episode = _mapping(episode, "episode")
    streams = dict(_mapping(streams, "streams"))
    facts = dict(_mapping(facts, "facts"))
    # These are the evaluator-owned minimums needed by the downstream
    # normalizer.  Their numerical and relation-specific validation remains in
    # ``receipt_adapter_v3``; the producer must nevertheless reject an
    # envelope that cannot possibly become a receipt.
    required_fact_keys = {"d1", "success", "obligations", "d4_items", "d6"}
    missing_facts = sorted(required_fact_keys - set(facts))
    if missing_facts:
        raise ReceiptProducerError(f"facts missing required fields: {missing_facts}")
    if type(facts["success"]) is not bool or not isinstance(facts["obligations"], list) or not isinstance(facts["d4_items"], list):
        raise ReceiptProducerError("facts success/obligations/d4_items have invalid types")
    if not isinstance(facts["d6"], Mapping):
        raise ReceiptProducerError("facts.d6 must be an object")
    if not isinstance(catalog.get("anchors"), list) or not catalog["anchors"]:
        raise ReceiptProducerError("catalog.anchors is required")
    if type(plan.get("seed")) is not int:
        raise ReceiptProducerError("plan.seed must be an integer")
    for key, expected in (("task_id", case["task_id"]), ("anchor_id", case["anchor_id"]),
                          ("domain", case["domain"]), ("base_instance_id", case["base_instance_id"])):
        if episode.get(key, expected) != expected:
            raise ReceiptProducerError(f"episode.{key} does not match case manifest")
    if canonical_digest(catalog) != case["catalog_digest"]:
        raise ReceiptProducerError("catalog digest does not match case manifest")
    if canonical_digest(plan) != case["plan_digest"]:
        raise ReceiptProducerError("plan digest does not match case manifest")

    condition_id = _text(episode.get("condition_id"), "episode.condition_id")
    branch = _text(episode.get("branch", episode.get("branch_type")), "episode.branch")
    if branch not in _BRANCHES:
        raise ReceiptProducerError(f"unsupported episode branch: {branch}")
    repeat_id = _text(episode.get("repeat_id", "r1"), "episode.repeat_id")
    checkpoint_id = _text(episode.get("checkpoint_id", "checkpoint-unregistered"), "episode.checkpoint_id")
    difficulty = _text(episode.get("difficulty", "nominal"), "episode.difficulty")

    source_hash = episode.get("evidence_hash")
    if source_hash is not None:
        source_hash = _digest(source_hash, "episode.evidence_hash")
    facts_out = dict(facts)
    if source_hash is not None:
        facts_out["source_evidence_hash"] = source_hash
    if "source_evidence_hash" in facts_out:
        _digest(facts_out["source_evidence_hash"], "facts.source_evidence_hash")

    status = _text(episode.get("status"), "episode.status")
    if status not in _ALLOWED_STATUSES:
        raise ReceiptProducerError(f"episode.status is not a terminal evaluator status: {status}")
    evaluator_version = _text(
        episode.get("evaluator_version", case["evaluator"]["evaluator_version"]),
        "episode.evaluator_version",
    )
    if evaluator_version != case["evaluator"]["evaluator_version"]:
        raise ReceiptProducerError("episode evaluator version does not match case manifest")
    refs = episode.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ReceiptProducerError("episode.evidence_refs must be a non-empty list")

    identity = {
        "task_id": case["task_id"], "anchor_id": case["anchor_id"], "domain": case["domain"],
        "relation_signature": list(case["relation_signature"]), "difficulty": difficulty,
        "condition_id": condition_id, "system_id": case.get("case_id", ""),
        "base_instance_id": case["base_instance_id"], "repeat_id": repeat_id,
        "branch": branch, "checkpoint_id": checkpoint_id,
    }
    # ``system_id`` is an evaluator identity, not a semantic-map guess.  A
    # bound case may provide it explicitly in the episode; otherwise case_id
    # is the deterministic fallback used only for receipt identity.
    identity["system_id"] = _text(episode.get("system_id", identity["system_id"]), "episode.system_id")
    receipt: dict[str, Any] = {
        "schema": RAW_RECEIPT_SCHEMA,
        "receipt_id": _text(receipt_id or episode.get("receipt_id") or episode.get("branch_id"), "receipt_id"),
        "identity": identity,
        "catalog": catalog,
        "plan": plan,
        "parameters": parameters,
        "evaluator": {
            "status": status,
            "evaluator_version": evaluator_version,
            "evidence_hash": "0" * 64,
            "evidence_refs": list(refs),
        },
        "streams": streams,
        "facts": facts_out,
    }
    if episode.get("artifact_manifest_digest") is not None:
        receipt["evaluator"]["artifact_manifest_digest"] = _digest(
            episode["artifact_manifest_digest"], "episode.artifact_manifest_digest"
        )
    receipt["evaluator"]["evidence_hash"] = _receipt_envelope_digest(receipt)
    try:
        return _validate_receipt(receipt)
    except ReceiptAdapterError as exc:
        raise ReceiptProducerError(f"raw receipt validation failed: {exc}") from exc


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptProducerError(f"cannot read JSON: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-manifest", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--episode", required=True, type=Path)
    parser.add_argument("--streams", required=True, type=Path)
    parser.add_argument("--facts", required=True, type=Path)
    parser.add_argument("--receipt-id")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build_raw_receipt(
            case_manifest=_load_json(args.case_manifest), catalog=_load_json(args.catalog),
            plan=_load_json(args.plan), parameters=_load_json(args.parameters),
            episode=_load_json(args.episode), streams=_load_json(args.streams),
            facts=_load_json(args.facts), receipt_id=args.receipt_id,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ReceiptProducerError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    return 0


__all__ = ["PRODUCER_SCHEMA", "PRODUCER_VERSION", "ReceiptProducerError", "build_raw_receipt"]


if __name__ == "__main__":
    raise SystemExit(main())
