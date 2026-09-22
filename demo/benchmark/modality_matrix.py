"""Offline validation for the four-scene deployment-realism modality matrix.

The matrix audits, per scene and per declared skill, which mobility modality
fulfils each business phase under the current authoring contract.  It is an
authoring-side audit artifact: it never claims execution validation, and its
wording discipline forbids machine-type prohibition statements.  Rows may only
state which modality the current declared contract assigns to a phase, backed
by file:line evidence into the task generator, skill ontology, fixtures and
constraint model.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from .skill_ontology import skill_names
from .task_coverage import SCENES


MODALITY_MATRIX_SCHEMA = "eai.benchmark.modality_matrix"
MODALITY_MATRIX_VERSION = 1
MODALITY_MATRIX_STATUS = "deployment_realism_audit"
MODALITY_STATUSES = ("declared_realistic", "realistic_missing", "excluded_defensible")
MODALITY_VOCABULARY = (
    "aerial", "ground-legged", "ground-wheeled", "ground-legged+ground-wheeled",
)
WORDING_BLACKLIST = ("不该", "不应该", "错误决策", "不适合")
_EVIDENCE_PATTERN = re.compile(r"^([A-Za-z0-9_./-]+):(\d+)(?:-(\d+))?$")
_ROW_REQUIRED_FIELDS = {"skill", "phase", "modality", "status", "evidence", "rationale"}
_ROW_ALLOWED_FIELDS = _ROW_REQUIRED_FIELDS | {"proposal"}
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def load_modality_matrix(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path is not None else Path(__file__).with_name("modality_matrix.json")
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    return validate_modality_matrix(value)["normalized"]


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _check_wording(text: str, label: str, blacklist: tuple[str, ...]) -> None:
    for word in blacklist:
        if word in text:
            raise ValueError(f"{label} contains forbidden wording {word!r}")


def _check_evidence(entries: Any, label: str) -> None:
    if (not isinstance(entries, list) or not entries
            or any(not isinstance(entry, str) for entry in entries)):
        raise ValueError(f"{label}: evidence must be a nonempty list of file:line strings")
    for entry in entries:
        match = _EVIDENCE_PATTERN.match(entry)
        if match is None:
            raise ValueError(f"{label}: evidence entry is not a file:line reference: {entry!r}")
        source = _REPOSITORY_ROOT / match.group(1)
        if not source.is_file():
            raise ValueError(f"{label}: evidence references a missing file: {entry}")
        start = int(match.group(2))
        end = int(match.group(3) or match.group(2))
        line_count = len(source.read_text(encoding="utf-8").splitlines())
        if start < 1 or end < start or end > line_count:
            raise ValueError(
                f"{label}: evidence line range outside {match.group(1)} "
                f"(has {line_count} lines): {entry}")


def _validate_row(row: Any, label: str, *, skills: frozenset[str],
                  blacklist: tuple[str, ...]) -> None:
    if not isinstance(row, Mapping):
        raise ValueError(f"{label}: each matrix row must be an object")
    fields = set(row)
    if not _ROW_REQUIRED_FIELDS <= fields or not fields <= _ROW_ALLOWED_FIELDS:
        raise ValueError(f"{label}: row fields mismatch: {sorted(fields)}")
    skill = _require_nonempty_string(row["skill"], f"{label}.skill")
    if skill not in skills:
        raise ValueError(f"{label}.skill {skill!r} is not in the public skill ontology")
    _require_nonempty_string(row["phase"], f"{label}.phase")
    if row["modality"] not in MODALITY_VOCABULARY:
        raise ValueError(f"{label}.modality must be one of {list(MODALITY_VOCABULARY)}")
    status = row["status"]
    if status not in MODALITY_STATUSES:
        raise ValueError(f"{label}.status must be one of {list(MODALITY_STATUSES)}")
    _check_evidence(row["evidence"], label)
    rationale = _require_nonempty_string(row["rationale"], f"{label}.rationale")
    _check_wording(rationale, f"{label}.rationale", blacklist)
    proposal = row.get("proposal")
    if status == "realistic_missing":
        if not isinstance(proposal, str) or not proposal.strip():
            raise ValueError(f"{label}: realistic_missing rows require a nonempty proposal")
    elif proposal is not None and (not isinstance(proposal, str) or not proposal.strip()):
        raise ValueError(f"{label}: proposal must be a nonempty string when present")
    if proposal is not None:
        _check_wording(proposal, f"{label}.proposal", blacklist)
    if status == "excluded_defensible" and "阶段" not in rationale:
        raise ValueError(
            f"{label}: excluded_defensible rows must decompose the excluded modality "
            "into phases (rationale must mention '阶段')")


def validate_modality_matrix(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate structure, three-state semantics, evidence and wording discipline."""
    if not isinstance(value, Mapping):
        raise ValueError("modality matrix must be an object")
    required = {
        "schema", "version", "status", "claim_boundary", "scene_keys",
        "status_vocabulary", "modality_vocabulary", "wording_blacklist",
        "scene_modality_audit",
    }
    if set(value) != required:
        raise ValueError("modality matrix fields mismatch")
    if value["schema"] != MODALITY_MATRIX_SCHEMA or value["version"] != MODALITY_MATRIX_VERSION \
            or type(value["version"]) is not int:
        raise ValueError("unsupported modality matrix schema or version")
    if value["status"] != MODALITY_MATRIX_STATUS:
        raise ValueError("modality matrix must remain a deployment realism audit")
    boundary = value["claim_boundary"]
    if not isinstance(boundary, Mapping) or not {"allowed", "forbidden", "wording_rule"} <= set(boundary):
        raise ValueError("modality matrix claim boundary is incomplete")
    if "runtime_execution_validation" not in boundary["forbidden"] \
            or "machine_type_prohibition" not in boundary["forbidden"]:
        raise ValueError("modality matrix must fail closed on execution and prohibition claims")
    _require_nonempty_string(boundary["wording_rule"], "claim_boundary.wording_rule")
    if tuple(value["scene_keys"]) != SCENES:
        raise ValueError("modality matrix must list the four scenes in registry order")
    if tuple(value["status_vocabulary"]) != MODALITY_STATUSES:
        raise ValueError("modality matrix status vocabulary mismatch")
    if tuple(value["modality_vocabulary"]) != MODALITY_VOCABULARY:
        raise ValueError("modality matrix modality vocabulary mismatch")
    blacklist = tuple(value["wording_blacklist"])
    if any(word not in blacklist for word in WORDING_BLACKLIST):
        raise ValueError("modality matrix wording blacklist is missing a required term")
    audit = value["scene_modality_audit"]
    if not isinstance(audit, Mapping) or set(audit) != set(SCENES):
        raise ValueError("modality matrix must audit every registered scene")
    skills = frozenset(skill_names())
    for scene in SCENES:
        rows = audit[scene]
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"scene {scene} has no modality audit rows")
        identities: set[tuple[str, str, str]] = set()
        for index, row in enumerate(rows):
            label = f"{scene}[{index}]"
            _validate_row(row, label, skills=skills, blacklist=blacklist)
            identity = (row["skill"], row["phase"], row["modality"])
            if identity in identities:
                raise ValueError(f"{label}: duplicate skill/phase/modality row {identity}")
            identities.add(identity)
    return {"validated": True, "normalized": json.loads(json.dumps(value, sort_keys=True)),
            "statistics": modality_matrix_statistics(value)}


def modality_matrix_statistics(value: Mapping[str, Any]) -> dict[str, Any]:
    """Per-scene and total three-state counts for reporting and release checks."""
    audit = value["scene_modality_audit"]
    scenes: dict[str, dict[str, int]] = {}
    totals = {status: 0 for status in MODALITY_STATUSES}
    for scene in SCENES:
        counts = {status: 0 for status in MODALITY_STATUSES}
        for row in audit[scene]:
            if row["status"] not in counts:
                raise ValueError(f"scene {scene} row has unsupported status {row['status']!r}")
            counts[row["status"]] += 1
        scenes[scene] = counts
        for status in MODALITY_STATUSES:
            totals[status] += counts[status]
    return {"row_count": sum(totals.values()), "by_scene": scenes, "totals": totals}


__all__ = [
    "MODALITY_MATRIX_SCHEMA", "MODALITY_MATRIX_VERSION", "MODALITY_MATRIX_STATUS",
    "MODALITY_STATUSES", "MODALITY_VOCABULARY", "WORDING_BLACKLIST",
    "load_modality_matrix", "modality_matrix_statistics", "validate_modality_matrix",
]
