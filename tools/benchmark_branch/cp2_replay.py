"""Deterministic C-P2-02 contract replay ledger.

This is an offline audit harness: it binds the V2 catalog digest to the
evaluator-owned predicate implementation and recomputes the registered
difficulty vectors.  It intentionally does not mint a physical deployment
certificate; a deployment witness still requires a runtime receipt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from benchmark.task_catalog import catalog_digest, load_catalog
from .oracle_predicates import MilestoneEvent, PredicateStreams, VerdictStatus, ZoneInterval, judge_anchor

ANCHORS = ("prod-p2-02", "svc-p2-02", "liv-p2-02")
COMMON_ORACLE_ANCHOR = "svc-p2-02"
ORACLE_SOURCE_COMMITS = ("cf839ec8", "0b6890c0")


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _default_witness() -> dict[str, Any]:
    """Deterministic abstract witness for the shared C-P2-02 predicate set.

    This is an executable oracle-input replay, not a physical deployment
    receipt.  The three common-anchor members intentionally share the same
    event semantics while retaining their own catalog identities.
    """
    return {
        "schema_version": "c-p2-02-oracle-witness.v1",
        "integrity_confirmed": True,
        "milestones": [
            {"label": "inspection_complete", "tick": 10},
            {"label": "quarantine_marked", "tick": 12},
            {"label": "released", "tick": 20},
            {"label": "accepted", "tick": 25},
        ],
        "zone_intervals": [
            {"zone_id": "quarantine_bay", "holder_id": "package_a", "enter_tick": 12, "exit_tick": 20},
        ],
        "hold_intervals": [],
        "task_events": [],
    }


def _load_witness(witness_path: str | Path | None) -> tuple[PredicateStreams, str, dict[str, Any]]:
    raw = _default_witness() if witness_path is None else json.loads(Path(witness_path).read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "c-p2-02-oracle-witness.v1":
        raise ValueError("unsupported C-P2-02 oracle witness schema")
    integrity_confirmed = raw.get("integrity_confirmed")
    if type(integrity_confirmed) is not bool:
        raise ValueError("oracle witness integrity_confirmed must be boolean")
    rows = {
        "schema_version": raw["schema_version"],
        "integrity_confirmed": integrity_confirmed,
        "milestones": list(raw.get("milestones", [])),
        "zone_intervals": list(raw.get("zone_intervals", [])),
        "hold_intervals": list(raw.get("hold_intervals", [])),
        "task_events": list(raw.get("task_events", [])),
    }
    supplied_digest = raw.get("integrity_digest")
    if witness_path is not None and integrity_confirmed:
        expected_digest = _sha(rows)
        if supplied_digest != expected_digest:
            raise ValueError("oracle witness integrity digest mismatch")
    streams = PredicateStreams(
        milestones=[MilestoneEvent(**row) for row in rows["milestones"]],
        zone_intervals=[ZoneInterval(**row) for row in rows["zone_intervals"]],
        integrity_confirmed=integrity_confirmed,
    )
    return streams, _sha(rows), rows


def replay_cp2_catalog(catalog_path: str | Path, witness_path: str | Path | None = None) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    entries = {entry.anchor_id: entry for entry in catalog}
    missing = [anchor for anchor in ANCHORS if anchor not in entries]
    if missing:
        raise ValueError(f"C-P2-02 anchors missing: {missing}")
    digest = catalog_digest(catalog)
    streams, witness_digest, witness_rows = _load_witness(witness_path)
    rows = []
    for anchor_id in ANCHORS:
        entry = entries[anchor_id]
        # V2 binds the shared C-P2-02 predicate set through the service
        # representative.  The replay executes the same evaluator-owned
        # oracle for all three catalog members and records the common witness.
        verdicts = judge_anchor(COMMON_ORACLE_ANCHOR, streams)
        rows.append({
            "anchor_id": anchor_id,
            "common_anchor_id": entry.common_anchor_id,
            "milestones": list(entry.milestones),
            "difficulty_vector": entry.difficulty.to_dict(),
            "difficulty_tier": entry.difficulty_tier,
            "predicate_statuses": [verdict.status.value for verdict in verdicts],
            "predicate_causes": [verdict.cause.value if verdict.cause else None for verdict in verdicts],
            "predicate_evidence": [verdict.evidence for verdict in verdicts],
            "oracle_anchor_id": COMMON_ORACLE_ANCHOR,
            "oracle_witness_digest": witness_digest,
            "oracle_replay_status": "executed_offline",
            "deployment_witness": "pending_runtime_receipt",
            "skill_gate": "pending_skill_receipt",
        })
    difficulty_digest = _sha({row["anchor_id"]: row["difficulty_vector"] for row in rows})
    oracle_replay_digest = _sha({
        "witness_digest": witness_digest,
        "anchors": {row["anchor_id"]: row["predicate_statuses"] for row in rows},
    })
    all_verdicts_sufficient = all(
        status != VerdictStatus.DATA_INSUFFICIENT.value
        for row in rows for status in row["predicate_statuses"]
    )
    return {
        "schema_version": "c-p2-02-replay-ledger.v1",
        "contract_version": "C-P2-02-quarantine-marked-v2",
        "catalog_digest": digest,
        "common_anchor_id": "C-P2-02",
        "oracle_source_commits": list(ORACLE_SOURCE_COMMITS),
        "oracle_witness_schema": witness_rows["schema_version"],
        "oracle_witness_digest": witness_digest,
        "oracle_replay_digest": oracle_replay_digest,
        "anchors": rows,
        "difficulty_recomputed": True,
        "difficulty_digest": difficulty_digest,
        "oracle_witness_replayed": True,
        "oracle_verdicts_sufficient": all_verdicts_sufficient,
        "deployment_oracle_replayed": False,
        "skill_gate_passed": False,
        "certification_status": "planned",
        "formal_gates": {"physical": False, "oracle": False, "skill": False, "paper_score_eligible": False},
        "blocking_reason": "offline oracle replay is executable, but deployment witness and skill receipts still require runtime evidence",
    }


def write_replay_ledger(catalog_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    ledger = replay_cp2_catalog(catalog_path)
    Path(output_path).write_text(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return ledger


__all__ = ["replay_cp2_catalog", "write_replay_ledger"]
