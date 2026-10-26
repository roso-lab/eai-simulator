#!/usr/bin/env python3
"""Historical integration evidence receipts for the runtime roster.

These receipts are deliberately a different contract from a live RP-C
qualification receipt.  Historical records may predate GPU UUID, telemetry
cleanup and command-environment fields; those fields are not invented here.
Instead, every claim is bound to an existing file, its bytes, a source commit,
an exact record locator and explicit content assertions.  A missing/unknown
check can never promote a roster row.

The same contract also carries receipts issued under the user's 2026-10-26
final ruling for instances without an exact historical record: the ruling
record itself is then the sole source, and the authority decision names the
ruling instead of a historical integration acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "heterocoord.historical_runtime_qualification_receipt.v1"
# Both decisions are explicit user acceptances; the ruling variant grants
# runtime qualification from the ruling record alone, never a platform run.
HISTORICAL_DECISION = "user_historical_integration_accepted"
RULING_DECISION = "user_runtime_unknown_ruling_accepted"
DECISIONS = frozenset({HISTORICAL_DECISION, RULING_DECISION})
STATUS = frozenset({"qualified", "failed", "blocked_missing_evidence", "rejected"})
CHECK_NAMES = ("integration_record", "functional_record", "configuration_binding")
HEX = frozenset("0123456789abcdef")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


class HistoricalReceiptError(ValueError):
    pass


def canonical_sha256(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise HistoricalReceiptError("historical receipt is not JSON-stable") from exc
    return hashlib.sha256(raw).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalReceiptError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(char not in HEX for char in value):
        raise HistoricalReceiptError(f"{name} must be lowercase sha256")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HistoricalReceiptError(f"{name} must be an object")
    return value


def _source_row(value: Any, name: str, *, root: Path | None) -> dict[str, Any]:
    row = dict(_mapping(value, name))
    required = {"path", "sha256", "source_commit", "record_locator", "content_assertions"}
    if set(row) != required:
        raise HistoricalReceiptError(f"{name} fields mismatch")
    path_text = _text(row["path"], f"{name}.path")
    digest = _digest(row["sha256"], f"{name}.sha256")
    commit = _text(row["source_commit"], f"{name}.source_commit").lower()
    if not COMMIT_RE.fullmatch(commit):
        raise HistoricalReceiptError(f"{name}.source_commit must be a git commit hash")
    _text(row["record_locator"], f"{name}.record_locator")
    assertions = row["content_assertions"]
    if not isinstance(assertions, list) or not assertions or any(
            not isinstance(item, str) or not item.strip() for item in assertions):
        raise HistoricalReceiptError(f"{name}.content_assertions must be non-empty text list")
    if root is not None:
        root_path = root.resolve()
        path = (root_path / path_text).resolve() if not Path(path_text).is_absolute() else Path(path_text).resolve()
        try:
            path.relative_to(root_path)
        except ValueError as exc:
            raise HistoricalReceiptError(f"{name}.path escapes evidence root") from exc
        if not path.is_file() or path.is_symlink():
            raise HistoricalReceiptError(f"{name}.path is not a regular file: {path_text}")
        content = path.read_text(encoding="utf-8", errors="replace")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise HistoricalReceiptError(f"{name}.sha256 does not match file bytes")
        # A commit string is provenance, not decoration: require that the
        # declared commit contains this path with the same blob bytes.  This
        # prevents a receipt from combining a current/untracked file with an
        # unrelated historical commit that merely mentions the configuration.
        relative = path.relative_to(root_path).as_posix()
        try:
            commit_bytes = subprocess.run(
                ["git", "show", f"{commit}:{relative}"], cwd=root_path,
                check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise HistoricalReceiptError(
                f"{name}.source_commit does not contain path: {commit}:{relative}") from exc
        if hashlib.sha256(commit_bytes).hexdigest() != digest:
            raise HistoricalReceiptError(
                f"{name}.source_commit blob does not match sha256 for {relative}")
        lowered = content.casefold()
        missing = [item for item in assertions if item.casefold() not in lowered]
        if missing:
            raise HistoricalReceiptError(f"{name}.content_assertions not found: {missing!r}")
    return row


def _check_row(value: Any, name: str, source_ids: set[str]) -> dict[str, Any]:
    row = dict(_mapping(value, name))
    required = {"status", "source_refs", "details"}
    if set(row) != required:
        raise HistoricalReceiptError(f"{name} fields mismatch")
    if row["status"] not in {"passed", "unknown", "failed"}:
        raise HistoricalReceiptError(f"{name}.status is invalid")
    refs = row["source_refs"]
    if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
        raise HistoricalReceiptError(f"{name}.source_refs must be a list")
    if row["status"] == "passed" and not refs:
        raise HistoricalReceiptError(f"{name} passed without source_refs")
    if any(item not in source_ids for item in refs):
        raise HistoricalReceiptError(f"{name}.source_refs contains unknown source")
    if not isinstance(row["details"], Mapping):
        raise HistoricalReceiptError(f"{name}.details must be an object")
    return row


def validate_historical_receipt(
    receipt: Mapping[str, Any], *, roster_row: Mapping[str, Any] | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise HistoricalReceiptError("historical receipt must be an object")
    row = dict(receipt)
    required = {"schema", "receipt_id", "instance_id", "source_resume_id", "profile_id",
                "admission_class", "platform_type", "configuration_role", "asset_digest",
                "capability_digest", "initial_state_digest", "authority", "platform_scope",
                "sources", "checks", "status", "runtime_qualified", "failure",
                "formal_gates", "receipt_digest"}
    if set(row) != required:
        raise HistoricalReceiptError(
            f"historical receipt fields mismatch; missing={sorted(required-set(row))}, "
            f"extra={sorted(set(row)-required)}")
    if row["schema"] != SCHEMA:
        raise HistoricalReceiptError("unsupported historical receipt schema")
    for key in ("receipt_id", "instance_id", "source_resume_id", "profile_id",
                "admission_class", "platform_type", "configuration_role"):
        _text(row[key], key)
    if row["admission_class"] not in {"pool", "independent_arm_immediate"}:
        raise HistoricalReceiptError("unsupported admission_class")
    for key in ("asset_digest", "capability_digest", "initial_state_digest", "receipt_digest"):
        _digest(row[key], key)
    authority = _mapping(row["authority"], "authority")
    if authority.get("decision") not in DECISIONS or not _text(authority.get("decision_date"), "authority.decision_date"):
        raise HistoricalReceiptError("historical receipt lacks explicit user decision authority")
    scope = _mapping(row["platform_scope"], "platform_scope")
    if scope.get("platform_type") != row["platform_type"] or scope.get("configuration_role") != row["configuration_role"]:
        raise HistoricalReceiptError("platform_scope does not match receipt identity")
    sources_value = row["sources"]
    if not isinstance(sources_value, list) or not sources_value:
        raise HistoricalReceiptError("historical receipt sources must be non-empty")
    root_path = None if root is None else Path(root).resolve()
    sources = [_source_row(item, f"sources[{index}]", root=root_path) for index, item in enumerate(sources_value)]
    source_ids = {str(index) for index in range(len(sources))}
    checks_value = _mapping(row["checks"], "checks")
    if set(checks_value) != set(CHECK_NAMES):
        raise HistoricalReceiptError(
            "historical checks must contain integration_record/functional_record/configuration_binding exactly")
    checks = {name: _check_row(checks_value[name], f"checks.{name}", source_ids) for name in CHECK_NAMES}
    if row["status"] not in STATUS:
        raise HistoricalReceiptError("unsupported historical status")
    if type(row["runtime_qualified"]) is not bool:
        raise HistoricalReceiptError("runtime_qualified must be boolean")
    if row["status"] == "qualified":
        if row["runtime_qualified"] is not True or any(checks[name]["status"] != "passed" for name in CHECK_NAMES):
            raise HistoricalReceiptError("qualified historical receipt requires all checks passed")
        if row["failure"] is not None:
            raise HistoricalReceiptError("qualified historical receipt cannot contain failure")
    else:
        if row["runtime_qualified"] is not False:
            raise HistoricalReceiptError("non-qualified historical receipt cannot set runtime_qualified=true")
    if row["formal_gates"] != {"physical_qualified": False, "rankable": False, "paper_score_eligible": False}:
        raise HistoricalReceiptError("historical receipt cannot open formal gates")
    if roster_row is not None:
        if row["instance_id"] != roster_row.get("instance_id") or row["source_resume_id"] != roster_row.get("source_resume_id"):
            raise HistoricalReceiptError("historical receipt does not match roster identity")
        expected = {
            "asset_digest": roster_row.get("asset_binding", {}).get("asset_digest"),
            "capability_digest": roster_row.get("capability_binding", {}).get("capability_digest"),
            "initial_state_digest": roster_row.get("initial_state_binding", {}).get("initial_state_digest"),
        }
        for key, value in expected.items():
            if row[key] != value:
                raise HistoricalReceiptError(f"{key} does not match roster")
    if canonical_sha256({key: value for key, value in row.items() if key != "receipt_digest"}) != row["receipt_digest"]:
        raise HistoricalReceiptError("historical receipt_digest seal mismatch")
    row["sources"] = sources
    row["checks"] = checks
    return row


def make_historical_receipt(*, roster_row: Mapping[str, Any], receipt_id: str,
                            authority: Mapping[str, Any], platform_scope: Mapping[str, Any],
                            sources: list[Mapping[str, Any]], checks: Mapping[str, Mapping[str, Any]],
                            status: str = "qualified", failure: Mapping[str, Any] | None = None,
                            root: str | Path | None = None) -> dict[str, Any]:
    row = {
        "schema": SCHEMA, "receipt_id": _text(receipt_id, "receipt_id"),
        "instance_id": roster_row["instance_id"], "source_resume_id": roster_row["source_resume_id"],
        "profile_id": roster_row["profile_id"], "admission_class": roster_row["admission_class"],
        "platform_type": roster_row["platform_type"], "configuration_role": roster_row["configuration_role"],
        "asset_digest": roster_row["asset_binding"]["asset_digest"],
        "capability_digest": roster_row["capability_binding"]["capability_digest"],
        "initial_state_digest": roster_row["initial_state_binding"]["initial_state_digest"],
        "authority": dict(authority), "platform_scope": dict(platform_scope),
        "sources": [dict(item) for item in sources], "checks": {key: dict(value) for key, value in checks.items()},
        "status": status, "runtime_qualified": status == "qualified", "failure": None if failure is None else dict(failure),
        "formal_gates": {"physical_qualified": False, "rankable": False, "paper_score_eligible": False},
    }
    row["receipt_digest"] = canonical_sha256(row)
    return validate_historical_receipt(row, roster_row=roster_row, root=root)


__all__ = ["CHECK_NAMES", "HistoricalReceiptError", "SCHEMA", "canonical_sha256",
           "make_historical_receipt", "validate_historical_receipt"]
