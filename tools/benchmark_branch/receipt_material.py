"""Strict evaluator hook for assembling terminal receipt material.

The branch executor records lifecycle, step and observation ledgers, but those
rows are not themselves paper facts.  A task-specific evaluator must
explicitly expose the three objects consumed by ``benchmark.receipt_bridge``:

``episode``
    terminal identity/status metadata;
``streams``
    evaluator-owned milestone, zone, logical-hold and identity observations;
``facts``
    explicit D1--D6 inputs and obligation outcomes.

This module is deliberately a narrow adapter boundary.  It never derives
success, physical predicates, milestones, resource use or score values from
process exit, ``completed`` flags, progress rows or caller-supplied scores.
Missing material is an error, not an empty/default receipt.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .evidence import write_evaluator_evidence
from .models import BranchJob
from .oracle_predicates import (
    CauseCode, VerdictStatus, judge_anchor, predicate_scoring_role,
)



RECEIPT_MATERIAL_KEYS = frozenset({"episode", "streams", "facts"})
TERMINAL_STATUSES = frozenset({
    "valid_terminal",
    "safe_stop",
    "team_state_infeasible",
    "method_timeout",
    "malformed_action",
    "crashed",
    "infrastructure_invalid",
})


class ReceiptMaterialError(ValueError):
    """Raised when an evaluator does not provide explicit receipt material."""


def assemble_oracle_receipt_material(
    *, episode: Mapping[str, Any], streams: Mapping[str, Any], base_facts: Mapping[str, Any],
    anchor_id: str, obligation_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Reduce terminal ledgers into receipt facts before evidence is sealed.

    The evaluator owns ``base_task_success`` and non-oracle measurements.  The
    registered oracle owns D3 obligations plus safety/deadline success guards.
    This function is the only pre-seal convenience path; downstream adapters
    recompute the same facts and reject drift.
    """
    if not isinstance(base_facts, Mapping):
        raise ReceiptMaterialError("base_facts must be an object")
    required = {"d1", "base_task_success", "d4_items", "d6"}
    optional = {
        "d5_attempt", "safety_predicate", "safe_stop_certificate_digest",
        "infeasibility_proof_hash",
    }
    if set(base_facts) - (required | optional) or not required.issubset(base_facts):
        raise ReceiptMaterialError("base_facts schema mismatch")
    if type(base_facts["base_task_success"]) is not bool:
        raise ReceiptMaterialError("base_facts.base_task_success must be boolean")
    status = episode.get("status")
    if status == "safe_stop":
        if base_facts.get("safety_predicate") is not True:
            raise ReceiptMaterialError("safe_stop requires certified safety_predicate=true")
        if not _is_sha256(base_facts.get("safe_stop_certificate_digest")):
            raise ReceiptMaterialError("safe_stop requires a SHA-256 certificate digest")
    if status == "team_state_infeasible":
        if not _is_sha256(base_facts.get("infeasibility_proof_hash")):
            raise ReceiptMaterialError("team_state_infeasible requires a SHA-256 infeasibility proof")
    # Lazy imports avoid making the tools package initializer depend on the
    # production adapter while keeping one canonical stream parser.
    from benchmark.oracle_obligation_v1 import ObligationReductionError, reduce_obligations
    from benchmark.receipt_adapter_v3 import ReceiptAdapterError, _stream
    try:
        parsed = _stream(streams)
        obligations = reduce_obligations(anchor_id, parsed, obligation_binding)
    except (ReceiptAdapterError, ObligationReductionError, KeyError, TypeError, ValueError) as exc:
        raise ReceiptMaterialError(f"oracle reduction failed: {exc}") from exc

    success = bool(base_facts["base_task_success"])
    if status in {"safe_stop", "team_state_infeasible"}:
        success = False
    for verdict in judge_anchor(anchor_id, parsed):
        role = predicate_scoring_role(anchor_id, verdict.predicate_id)
        if role not in {"safety_success", "success_condition"}:
            continue
        if verdict.status is VerdictStatus.DATA_INSUFFICIENT:
            if verdict.cause is CauseCode.UNREACHED:
                success = False
                continue
            raise ReceiptMaterialError(
                f"oracle success evidence unresolved: {anchor_id}/{verdict.predicate_id}"
            )
        if verdict.status is VerdictStatus.VIOLATED:
            success = False

    d6 = dict(_mapping(base_facts["d6"], "base_facts.d6"))
    if set(d6) != {"bits", "time"}:
        raise ReceiptMaterialError("base_facts.d6 must contain exactly bits and time")
    facts = {
        "d1": base_facts["d1"], "success": success,
        "obligations": obligations, "d4_items": base_facts["d4_items"],
        "d6": {"success": success, **d6},
    }
    if "d5_attempt" in base_facts:
        facts["d5_attempt"] = base_facts["d5_attempt"]
    for key in ("safety_predicate", "safe_stop_certificate_digest", "infeasibility_proof_hash"):
        if key in base_facts:
            facts[key] = base_facts[key]
    return validate_receipt_material({"episode": episode, "streams": streams, "facts": facts})


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptMaterialError(f"{name} must be an object")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_receipt_material(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach one evaluator-owned ``receipt_material`` object.

    The validator checks shape and terminal identity only.  It intentionally
    does not interpret task semantics; those remain in the oracle and receipt
    producer layers.
    """

    material = _mapping(value, "receipt_material")
    if set(material) != RECEIPT_MATERIAL_KEYS:
        raise ReceiptMaterialError(
            "receipt_material must contain exactly episode, streams and facts"
        )

    episode = dict(_mapping(material["episode"], "receipt_material.episode"))
    status = episode.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ReceiptMaterialError("receipt_material.episode.status is required")
    if status not in TERMINAL_STATUSES:
        raise ReceiptMaterialError(
            f"receipt_material.episode.status is not terminal: {status}"
        )
    for key in ("condition_id", "branch", "repeat_id", "checkpoint_id"):
        if not isinstance(episode.get(key), str) or not episode[key].strip():
            raise ReceiptMaterialError(
                f"receipt_material.episode.{key} must be non-empty text"
            )

    streams = dict(_mapping(material["streams"], "receipt_material.streams"))
    required_streams = {
        "zone_intervals", "hold_intervals", "milestones", "task_events",
        "identity_records", "integrity_confirmed",
    }
    if set(streams) != required_streams:
        raise ReceiptMaterialError(
            "receipt_material.streams schema must match raw receipt streams"
        )
    for key in ("zone_intervals", "hold_intervals", "milestones", "task_events"):
        if not isinstance(streams[key], list):
            raise ReceiptMaterialError(f"receipt_material.streams.{key} must be a list")
    if not isinstance(streams["identity_records"], Mapping):
        raise ReceiptMaterialError("receipt_material.streams.identity_records must be an object")
    if type(streams["integrity_confirmed"]) is not bool:
        raise ReceiptMaterialError(
            "receipt_material.streams.integrity_confirmed must be boolean"
        )

    facts = dict(_mapping(material["facts"], "receipt_material.facts"))
    required_facts = {"d1", "success", "obligations", "d4_items", "d6"}
    missing = sorted(required_facts - set(facts))
    if missing:
        raise ReceiptMaterialError(f"receipt_material.facts missing: {missing}")
    if type(facts["success"]) is not bool:
        raise ReceiptMaterialError("receipt_material.facts.success must be boolean")
    if episode.get("status") == "safe_stop":
        if facts.get("safety_predicate") is not True or not _is_sha256(facts.get("safe_stop_certificate_digest")):
            raise ReceiptMaterialError("safe_stop receipt material lacks certified safety proof")
        if facts["success"] is not False:
            raise ReceiptMaterialError("safe_stop receipt material cannot claim task success")
    if episode.get("status") == "team_state_infeasible":
        if not _is_sha256(facts.get("infeasibility_proof_hash")):
            raise ReceiptMaterialError("infeasible receipt material lacks proof hash")
        if facts["success"] is not False:
            raise ReceiptMaterialError("infeasible receipt material cannot claim task success")
    if not isinstance(facts["obligations"], list):
        raise ReceiptMaterialError("receipt_material.facts.obligations must be a list")
    if not isinstance(facts["d4_items"], list):
        raise ReceiptMaterialError("receipt_material.facts.d4_items must be a list")
    if not isinstance(facts["d6"], Mapping):
        raise ReceiptMaterialError("receipt_material.facts.d6 must be an object")

    return {
        "episode": episode,
        "streams": streams,
        "facts": facts,
    }


def collect_receipt_material(adapter: Any, runtime: Any, job: BranchJob) -> dict[str, Any]:
    """Call the evaluator's explicit terminal-material hook.

    Adapters that do not implement ``collect_receipt_material`` cannot enter
    the production receipt path.  There is intentionally no fallback to
    ``finish_episode``, process status, progress rows or score fields.
    """

    hook = getattr(adapter, "collect_receipt_material", None)
    if not callable(hook):
        raise ReceiptMaterialError(
            "evaluator adapter must implement collect_receipt_material"
        )
    return validate_receipt_material(hook(runtime, job))


def write_terminal_receipt_evidence(
    root: str | Path,
    job: BranchJob,
    *,
    evaluator_version: str,
    result: Mapping[str, Any],
    material: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a content-addressed evaluator artifact with explicit material."""

    validated = validate_receipt_material(material)
    return write_evaluator_evidence(
        root,
        job,
        evaluator_version=evaluator_version,
        result=result,
        observations={"receipt_material": validated},
    )


__all__ = [
    "RECEIPT_MATERIAL_KEYS",
    "TERMINAL_STATUSES",
    "ReceiptMaterialError",
    "assemble_oracle_receipt_material",
    "collect_receipt_material",
    "validate_receipt_material",
    "write_terminal_receipt_evidence",
]
