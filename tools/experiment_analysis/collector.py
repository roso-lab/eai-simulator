"""Read-only sealed collector handoff, without task-outcome interpretation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from tools.benchmark_branch.store import verify_seal, LEDGERS
from tools.benchmark_branch.validation import validate_run

from . import ANALYSIS_VERSION
from .schema import AnalysisError, Dataset, Design, digest, read_json, _pairs, obj, text, sha


def _rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line, object_pairs_hook=_pairs)
            if not isinstance(row, dict):
                raise AnalysisError(f"non-object ledger row: {path.name}")
            # Also rejects non-finite JSON numbers.
            digest(row)
            rows.append(row)
    return rows


def collector_handoff(run_dir: str | Path) -> dict:
    """Export every attempt; never select an outcome-dependent retry winner.

    Hashes bind a local immutable snapshot, not a scientific authenticity claim.
    The external evaluator must consume the ledgers and return registered raw
    scalar/event outcomes. No milestone or success is inferred here.
    """
    root = Path(run_dir).resolve()
    before = (root / "checksums.sha256").read_bytes()
    errors = verify_seal(root)
    if errors:
        raise AnalysisError("collector seal failed: " + "; ".join(errors))
    validation = validate_run(root, write=False)
    if validation["status"] != "passed":
        raise AnalysisError("collector validation failed: " + "; ".join(validation["errors"]))
    manifest = read_json(root / "manifest.json")
    ledgers = {name: _rows(root / f"{name}.jsonl") for name in LEDGERS}
    branches, episodes = ledgers["branch_manifest"], ledgers["episodes"]
    counts = Counter(row["branch_id"] for row in episodes)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise AnalysisError("multiple terminal episodes for one attempt: " + ", ".join(duplicates))
    identities = {row["branch_id"]: row for row in branches}
    if len(identities) != len(branches):
        raise AnalysisError("duplicate branch attempt identity")
    scopes = {row["execution_scope"] for row in branches}
    scope = manifest.get("execution_scope")
    if not scopes or scopes != {scope}:
        raise AnalysisError("mixed or empty collector execution scopes")
    if scope not in {"protocol_synthetic", "measured", "measured_diagnostic"}:
        raise AnalysisError("collector execution scope is not an outcome source")
    by_branch = {row["branch_id"]: row for row in episodes}
    attempts = [{"identity": row, "episode": by_branch.get(row["branch_id"]),
                 "status": "observed" if row["branch_id"] in by_branch else "missing_terminal_episode",
                 "identity_digest": digest(row),
                 "episode_digest": digest(by_branch[row["branch_id"]]) if row["branch_id"] in by_branch else None}
                for row in sorted(branches, key=lambda x: x["branch_id"])]
    # Detect writers during the read. Re-verify the actual files, not only the
    # checksum file: live/unsealed mutation cannot become an admitted snapshot.
    if before != (root / "checksums.sha256").read_bytes() or verify_seal(root):
        raise AnalysisError("collector changed during handoff")
    return {"schema": ANALYSIS_VERSION + ".collector_handoff", "collector_path": str(root),
            "seal_sha256": hashlib.sha256(before).hexdigest(), "execution_scope": scope,
            "provenance": "synthetic" if scope == "protocol_synthetic" else "measured_unverified",
            "publishable": False, "qualification": "external_endpoint_evaluator_required",
            "validation": {k: v for k, v in validation.items() if k != "validated_at_ns"},
            "ledger_counts": {name: len(rows) for name, rows in ledgers.items()},
            "attempts": attempts, "attempt_selection": "none_external_preregistered_policy_required",
            "raw_endpoint_records": None}


def bind_outcomes(design_data: dict, outcomes: dict, run_dir: str | Path) -> dict:
    """Join caller-evaluated outcomes to exact sealed branch attempts.

    The supplied evaluator and pairing digests remain unverified scientific
    attestations. Structural identity verification never promotes them. This
    adapter owns neither attempt selection nor metric extraction from events.
    """
    design = Design.parse(design_data)
    obj(outcomes, "schema design_digest collector_seal_sha256 evaluator selection_policy_digest records")
    if outcomes["schema"] != ANALYSIS_VERSION + ".evaluated_outcomes" or outcomes["design_digest"] != design.digest:
        raise AnalysisError("outcome schema/design mismatch")
    evaluator = obj(outcomes["evaluator"], "id version source_digest")
    text(evaluator["id"])
    text(evaluator["version"])
    sha(evaluator["source_digest"])
    sha(outcomes["selection_policy_digest"])
    handoff = collector_handoff(run_dir)
    if outcomes["collector_seal_sha256"] != handoff["seal_sha256"]:
        raise AnalysisError("evaluated outcomes name a different collector seal")
    by_id = {item["identity"]["branch_id"]: item for item in handoff["attempts"]}
    selected, records, evidence = set(), [], []
    for item in outcomes["records"]:
        obj(item, "branch_id identity_digest outcome")
        branch_id = text(item["branch_id"])
        attempt = by_id.get(branch_id)
        if attempt is None or attempt["identity_digest"] != item["identity_digest"]:
            raise AnalysisError("unknown or misbound branch attempt")
        if branch_id in selected:
            raise AnalysisError("duplicate selected branch attempt")
        selected.add(branch_id)
        if attempt["episode"] is None:
            raise AnalysisError("selected attempt has no terminal episode")
        raw = dict(item["outcome"])
        if "artifact_digest" in raw:
            raise AnalysisError("artifact digest is assigned by the evidence join")
        identity = attempt["identity"]
        for name, source in (("base_instance_id", "base_instance_id"), ("system_id", "system_id"),
                             ("condition_id", "condition_id"), ("branch_role", "branch_type"), ("repeat_id", "repeat_id")):
            if raw.get(name) != identity.get(source):
                raise AnalysisError(f"outcome/collector identity mismatch: {name}")
        metadata = identity.get("metadata", {})
        if not isinstance(metadata, dict):
            raise AnalysisError("collector metadata must be an object")
        run_key = metadata.get("benchmark_run_key", {})
        if not isinstance(run_key, dict):
            raise AnalysisError("collector benchmark_run_key must be an object")
        checkpoint_round = run_key.get("checkpoint_round", metadata.get("checkpoint_round"))
        if not checkpoint_round or raw.get("checkpoint_round") != checkpoint_round:
            raise AnalysisError("missing or mismatched authoritative checkpoint_round")
        artifact = digest({"seal": handoff["seal_sha256"], "identity": attempt["identity_digest"],
                           "episode": attempt["episode_digest"], "evaluator": evaluator,
                           "selection_policy": outcomes["selection_policy_digest"], "outcome": raw})
        raw["artifact_digest"] = artifact
        records.append(raw)
        evidence.append(artifact)
    data = {"schema": ANALYSIS_VERSION + ".dataset", "design_digest": design.digest,
            "provenance": {"kind": handoff["provenance"], "producer": f"{evaluator['id']}@{evaluator['version']}",
                           "source_revision": evaluator["source_digest"], "evidence_digests": sorted(evidence)},
            "records": records}
    Dataset.parse(data, design_data)
    return data
