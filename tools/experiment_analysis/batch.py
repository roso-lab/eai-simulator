"""Deterministic multi-run binding and registered attempt selection."""

from pathlib import Path

from tools.benchmark_branch.models import IDENTITY_FIELDS

from . import ANALYSIS_VERSION
from .collector import bind_attempts, _snapshot_handoff, make_dataset
from .planning import expected_observations
from .registration import execution_identity, load_registration
from .schema import AnalysisError, Dataset, Design, digest, integer, obj, read_json, record_key, sha, text
from .snapshot import Budget, SnapshotLimits


def read_sources(manifest: dict, *, base_dir: Path, limits: SnapshotLimits = SnapshotLimits()) -> list[dict]:
    """Read live sealed collector sources once, before producing any output."""
    obj(manifest, "schema sources")
    if manifest["schema"] != ANALYSIS_VERSION + ".batch_sources" or not isinstance(manifest["sources"], list):
        raise AnalysisError("invalid batch source manifest")
    sources, budget = [], Budget(limits)
    for entry in manifest["sources"]:
        obj(entry, "run_dir outcomes")
        run_dir = base_dir / text(entry["run_dir"])
        outcomes_path = (base_dir / text(entry["outcomes"])).resolve()
        sources.append({"handoff": _snapshot_handoff(run_dir, budget), "outcomes": read_json(outcomes_path)})
    return sources


def _validate_handoff(handoff: dict) -> None:
    """Check detached snapshot consistency, without asserting source honesty."""
    if not isinstance(handoff, dict) or handoff.get("schema") != ANALYSIS_VERSION + ".collector_handoff":
        raise AnalysisError("unsupported collector handoff")
    if handoff.get("publishable") is not False or handoff.get("qualification") != "external_endpoint_evaluator_required":
        raise AnalysisError("collector handoff qualification mismatch")
    if not isinstance(handoff.get("validation"), dict) or handoff["validation"].get("status") != "passed":
        raise AnalysisError("handoff requires a passed collector validation snapshot")
    run = text(handoff.get("run_id"))
    sha(handoff.get("plan_digest"))
    sha(handoff.get("seal_sha256"))
    scope = handoff.get("execution_scope")
    if scope not in {"protocol_synthetic", "measured", "measured_diagnostic"}:
        raise AnalysisError("invalid handoff execution scope")
    kind = "synthetic" if scope == "protocol_synthetic" else "measured_unverified"
    if handoff.get("provenance") != kind:
        raise AnalysisError("handoff execution scope/provenance mismatch")
    if not isinstance(handoff.get("attempts"), list):
        raise AnalysisError("handoff attempts must be an array")
    seen = set()
    for item in handoff["attempts"]:
        obj(item, "identity episode status identity_digest episode_digest")
        ident = item["identity"]
        if not isinstance(ident, dict) or ident.get("run_id") != run or ident.get("execution_scope") != scope:
            raise AnalysisError("handoff branch run/scope mismatch")
        branch = text(ident.get("branch_id"))
        ordinal = integer(ident.get("attempt"))
        if not branch.endswith(f"#attempt-{ordinal}"):
            raise AnalysisError("collector branch suffix/attempt mismatch")
        if branch in seen or digest(ident) != item["identity_digest"]:
            raise AnalysisError("duplicate or changed handoff identity")
        seen.add(branch)
        if item["episode"] is None:
            if item["episode_digest"] is not None or item["status"] != "missing_terminal_episode":
                raise AnalysisError("handoff terminal state mismatch")
        elif not isinstance(item["episode"], dict) or digest(item["episode"]) != item["episode_digest"] or item["status"] != "observed":
            raise AnalysisError("handoff terminal digest mismatch")
        elif any(item["episode"].get(field) != ident.get(field) for field in IDENTITY_FIELDS
                 if field not in {"worker_instance_id", "worker_session_id"}):
            raise AnalysisError("terminal episode/branch identity mismatch")


def infrastructure_reason(episode: dict) -> str | None:
    """Read an explicit sealed reason, never infer infrastructure from failure."""
    if episode.get("status") != "infrastructure_invalid":
        return None
    if episode.get("infrastructure_reason") is not None:
        return text(episode["infrastructure_reason"])
    if episode.get("infrastructure_preflight") is True:
        return "preflight_rejected"
    outcome = episode.get("outcome", {})
    if not isinstance(outcome, dict):
        return None
    if isinstance(outcome.get("infrastructure_error"), dict):
        return "missing_runtime_receipt"
    code = outcome.get("infrastructure_reason")
    return text(code) if code is not None else None


def assemble(design_data: dict, registration: dict, sources: list[dict]) -> tuple[dict, dict]:
    """Merge detached verified snapshots and apply an outcome-independent policy.

    CLI ingestion verifies live seals. Offline replay calls this same function
    on preserved snapshots and claims content consistency only.
    """
    design = Design.parse(design_data)
    schedule = load_registration(design_data, registration)
    expected = expected_observations(design)
    batches = {b["run_id"]: b["plan_digest"] for b in schedule["batches"]}
    entries = {record_key(o["identity"]): o for o in schedule["observations"]}
    refs = {(a["run_id"], a["branch_id"]): (key, a["attempt"]) for key, e in entries.items() for a in e["attempts"]}
    evaluated, observed, admitted, seen_runs, seen_seals = {}, {}, [], set(), set()
    if not isinstance(sources, list):
        raise AnalysisError("batch sources must be an array")
    for source in sources:
        obj(source, "handoff outcomes")
        handoff, outcomes = source["handoff"], source["outcomes"]
        _validate_handoff(handoff)
        if not isinstance(outcomes, dict):
            raise AnalysisError("evaluated outcomes must be an object")
        run = handoff["run_id"]
        if run not in batches or run in seen_runs:
            raise AnalysisError("unregistered or duplicate source run")
        seen_runs.add(run)
        if handoff["seal_sha256"] in seen_seals:
            raise AnalysisError("duplicate source seal")
        seen_seals.add(handoff["seal_sha256"])
        if batches[run] != handoff["plan_digest"]:
            raise AnalysisError("source differs from frozen batch plan")
        if handoff["execution_scope"] != schedule["execution_scope"]:
            raise AnalysisError("mixed or unexpected execution scopes")
        if outcomes.get("evaluator") != schedule["evaluator"] or outcomes.get("selection_policy_digest") != registration["registration_digest"]:
            raise AnalysisError("evaluator or registered selection policy changed")
        for item in handoff["attempts"]:
            ident = item["identity"]
            ref = (run, ident["branch_id"])
            observed[ref] = item
            if ref in refs:
                key, ordinal = refs[ref]
                expected_identity = entries[key]["identity"]
                for target, src in (("base_instance_id", "base_instance_id"), ("system_id", "system_id"),
                                    ("condition_id", "condition_id"), ("repeat_id", "repeat_id"), ("branch_role", "branch_type")):
                    if ident.get(src) != expected_identity[target]:
                        raise AnalysisError("scheduled attempt/collector identity mismatch")
                if ident["attempt"] != ordinal:
                    raise AnalysisError("scheduled attempt ordinal mismatch")
                if execution_identity(ident) != entries[key]["execution_identity"]:
                    raise AnalysisError("retry execution identity differs from frozen context")
                metadata = ident.get("metadata", {})
                if not isinstance(metadata, dict) or not isinstance(metadata.get("benchmark_run_key", {}), dict):
                    raise AnalysisError("invalid checkpoint metadata")
                if metadata.get("benchmark_run_key", {}).get("checkpoint_round", metadata.get("checkpoint_round")) != expected_identity["checkpoint_round"]:
                    raise AnalysisError("scheduled checkpoint round mismatch")
            elif any(all(ident.get(src) == e["identity"][dst] for dst, src in
                         (("base_instance_id", "base_instance_id"), ("system_id", "system_id"),
                          ("condition_id", "condition_id"), ("repeat_id", "repeat_id"), ("branch_role", "branch_type")))
                     for e in entries.values()):
                raise AnalysisError("unregistered attempt for a scheduled observation")
        for item in bind_attempts(design_data, outcomes, handoff):
            ref, raw = (item["run_id"], item["branch_id"]), item["record"]
            if ref not in refs or refs[ref] != (record_key(raw), item["attempt"]):
                raise AnalysisError("evaluated attempt is not in its registered observation")
            episode = observed[ref]["episode"]
            if raw["status"] == "infrastructure_invalid" and infrastructure_reason(episode) != raw["reason"]:
                raise AnalysisError("infrastructure outcome lacks matching sealed episode reason")
            if episode.get("status") == "infrastructure_invalid" and raw["status"] == "valid":
                raise AnalysisError("infrastructure episode cannot supply a valid outcome")
            evaluated[ref] = raw
        admitted.append({"run_id": run, "seal_sha256": handoff["seal_sha256"], "plan_digest": handoff["plan_digest"],
                         "handoff_digest": digest(handoff), "outcomes_digest": digest(outcomes)})
    policy = schedule["retry_policy"]
    rows, audit = [], []
    for key, entry in sorted(entries.items()):
        attempts, chosen, stop = [], None, None
        for attempt in entry["attempts"]:
            ref = (attempt["run_id"], attempt["branch_id"])
            raw = evaluated.get(ref)
            disposition = "after_terminal" if chosen is not None else "blocked_by_predecessor" if stop else None
            if disposition is None:
                if raw is None:
                    stop = ("missing_source" if ref[0] not in seen_runs else
                            "missing_attempt" if ref not in observed else
                            "missing_terminal_episode" if observed[ref]["episode"] is None else "missing_evaluator_record")
                    disposition = stop
                elif raw["status"] == "infrastructure_invalid" and policy["mode"] == "infrastructure_retry" and raw["reason"] in policy["infrastructure_reasons"]:
                    disposition = "allowed_infrastructure_retry"
                else:
                    chosen = raw
                    rows.append(raw)
                    disposition = "selected"
            attempts.append({**attempt, "disposition": disposition, "observed": ref in observed,
                             "evaluated": raw is not None, "artifact_digest": raw["artifact_digest"] if raw else None,
                             "status": raw["status"] if raw else None, "method_outcome": raw["method_outcome"] if raw else None,
                             "reason": raw.get("reason") if raw else None})
        reason = stop or ("infrastructure_retries_exhausted" if chosen is None else None)
        missing_measures = sorted(set(expected[key]) - set(chosen["values"])) if chosen else list(expected[key])
        audit.append({"identity": entry["identity"], "selected": chosen is not None, "reason": reason,
                      "missing_measures": missing_measures, "attempts": attempts})
    kind = "synthetic" if schedule["execution_scope"] == "protocol_synthetic" else "measured_unverified"
    data = make_dataset(design_data, rows, kind, schedule["evaluator"])
    Dataset.parse(data, design_data)
    complete = all(item["selected"] and not item["missing_measures"] for item in audit)
    return data, {"schema": ANALYSIS_VERSION + ".batch_audit", "registration_digest": registration["registration_digest"],
                  "status": "complete" if complete else "incomplete", "qualification": "structural_collection_only", "publishable": False,
                  "expected_observations": len(entries), "selected_observations": len(rows),
                  "missing_sources": sorted(set(batches)-seen_runs), "sources": sorted(admitted, key=lambda s: s["run_id"]),
                  "unregistered_collector_attempts": [list(ref) for ref in sorted(set(observed)-set(refs))],
                  "observations": audit}
