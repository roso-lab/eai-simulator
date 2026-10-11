"""Read-only sealed collector handoff, without task-outcome interpretation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import tempfile

from tools.benchmark_branch.store import verify_seal, LEDGERS
from tools.benchmark_branch.validation import validate_run

from . import ANALYSIS_VERSION
from .collector_snapshot import parse_seal, snapshot_run, strict_run_json
from .schema import AnalysisError, Dataset, Design, digest, read_json, _pairs, obj, text, sha
from .snapshot import Budget, SnapshotLimits


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


def collector_handoff(run_dir: str | Path, *, limits: SnapshotLimits = SnapshotLimits()) -> dict:
    """Export every attempt; never select an outcome-dependent retry winner.

    Copies bounded sealed bytes once, then validates only the private snapshot.
    Hashes bind content, not a scientific authenticity claim.
    The external evaluator must consume the ledgers and return registered raw
    scalar/event outcomes. No milestone or success is inferred here.
    """
    return _snapshot_handoff(run_dir, Budget(limits))


def _snapshot_handoff(run_dir: str | Path, budget: Budget) -> dict:
    # Do not resolve input paths: the copier rejects symlink ancestors as well
    # as leaf members. Preserve the historical location in the exported handoff.
    with tempfile.TemporaryDirectory(prefix="eai-collector-handoff-") as temp:
        try:
            seal, _ = snapshot_run(run_dir, Path(temp), None, budget)
        except AnalysisError as exc:
            raise AnalysisError(f"collector snapshot/seal failed: {exc}") from exc
        return _handoff_from_snapshot(Path(temp) / "runs" / seal, collector_path=os.path.abspath(run_dir))


def _handoff_from_snapshot(root: Path, *, collector_path: str | None = None) -> dict:
    """Internal only: root must be a private, checksum-verified snapshot."""
    before = (root / "checksums.sha256").read_bytes()
    strict_run_json(root, parse_seal(before))
    errors = verify_seal(root)
    if errors:
        raise AnalysisError("collector seal failed: " + "; ".join(errors))
    validation = validate_run(root, write=False)
    if validation["status"] != "passed":
        raise AnalysisError("collector validation failed: " + "; ".join(validation["errors"]))
    manifest = read_json(root / "manifest.json")
    plan = read_json(root / "plan.json")
    if manifest.get("plan_digest") != digest(plan):
        raise AnalysisError("collector plan digest mismatch")
    if manifest.get("run_id") != plan.get("run_id") or manifest.get("execution_scope") != plan.get("execution_scope"):
        raise AnalysisError("collector manifest/plan run or scope mismatch")
    ledgers = {name: _rows(root / f"{name}.jsonl") for name in LEDGERS}
    branches, episodes = ledgers["branch_manifest"], ledgers["episodes"]
    counts = Counter(row["branch_id"] for row in episodes)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise AnalysisError("multiple terminal episodes for one attempt: " + ", ".join(duplicates))
    identities = {row["branch_id"]: row for row in branches}
    if len(identities) != len(branches):
        raise AnalysisError("duplicate branch attempt identity")
    if any(row.get("run_id") != manifest["run_id"] for row in branches):
        raise AnalysisError("collector branch/manifest run mismatch")
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
    return {"schema": ANALYSIS_VERSION + ".collector_handoff", "collector_path": collector_path or str(root),
            "run_id": manifest["run_id"], "plan_digest": digest(plan),
            "seal_sha256": hashlib.sha256(before).hexdigest(), "execution_scope": scope,
            "provenance": "synthetic" if scope == "protocol_synthetic" else "measured_unverified",
            "publishable": False, "qualification": "external_endpoint_evaluator_required",
            "validation": {k: v for k, v in validation.items() if k != "validated_at_ns"},
            "ledger_counts": {name: len(rows) for name, rows in ledgers.items()},
            "attempts": attempts, "attempt_selection": "none_external_preregistered_policy_required",
            "raw_endpoint_records": None}


def bind_attempts(design_data: dict, outcomes: dict, handoff: dict) -> list[dict]:
    """Bind all evaluated attempts to an already verified handoff snapshot.

    Offline callers must not treat the snapshot as an authenticated collector.
    Repeated scientific observations are retained for registered selection.
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
    if outcomes["collector_seal_sha256"] != handoff["seal_sha256"]:
        raise AnalysisError("evaluated outcomes name a different collector seal")
    by_id = {item["identity"]["branch_id"]: item for item in handoff["attempts"]}
    selected, records = set(), []
    if not isinstance(outcomes["records"], list):
        raise AnalysisError("evaluated records must be an array")
    for item in outcomes["records"]:
        obj(item, "branch_id identity_digest outcome")
        branch_id = text(item["branch_id"])
        attempt = by_id.get(branch_id)
        if attempt is None or attempt["identity_digest"] != item["identity_digest"]:
            raise AnalysisError("unknown or misbound branch attempt")
        if digest(attempt["identity"]) != attempt["identity_digest"] or digest(attempt["episode"]) != attempt["episode_digest"]:
            raise AnalysisError("handoff identity/episode content digest mismatch")
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
        candidate = make_dataset(design, [raw], handoff["provenance"], evaluator)
        Dataset.parse(candidate, design)
        records.append({"run_id": identity["run_id"], "branch_id": branch_id,
                        "attempt": identity["attempt"], "record": raw})
    return records


def make_dataset(design_data: dict | Design, records: list[dict], provenance: str, evaluator: dict) -> dict:
    data = {"schema": ANALYSIS_VERSION + ".dataset", "design_digest": design_data.digest if isinstance(design_data, Design) else digest(design_data),
            "provenance": {"kind": provenance, "producer": f"{evaluator['id']}@{evaluator['version']}",
                           "source_revision": evaluator["source_digest"],
                           "evidence_digests": sorted({r["artifact_digest"] for r in records})},
            "records": records}
    return data


def bind_outcomes(design_data: dict, outcomes: dict, run_dir: str | Path, *,
                  limits: SnapshotLimits = SnapshotLimits()) -> dict:
    """Single-run compatibility path; scientific qualification is external."""
    handoff = collector_handoff(run_dir, limits=limits)
    return bind_handoff(design_data, outcomes, handoff)


def bind_handoff(design_data: dict, outcomes: dict, handoff: dict) -> dict:
    """Bind and export from the same already acquired handoff; no source reread."""
    attempts = bind_attempts(design_data, outcomes, handoff)
    data = make_dataset(design_data, [a["record"] for a in attempts], handoff["provenance"], outcomes["evaluator"])
    Dataset.parse(data, design_data)
    return data
