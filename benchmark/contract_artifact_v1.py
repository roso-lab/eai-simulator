"""Load and validate the single catalog contract projection used by production.

The catalog intentionally has one extension (``liv-t-04``) in addition to the
30 baseline anchors.  This small manifest keeps that distinction explicit and
prevents a stale pilot-only projection from being selected by accident.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "heterocoord.canonical_contract_artifact.v1"


class ContractArtifactError(ValueError):
    """Raised when the canonical contract pointer or its source is stale."""


@dataclass(frozen=True)
class CanonicalContractArtifact:
    manifest_path: Path
    artifact_path: Path
    artifact_sha256: str
    catalog_path: Path
    catalog_sha256: str
    paper_sha: str
    anchor_count: int
    baseline_anchor_count: int
    extension_anchor_ids: tuple[str, ...]
    payload: dict[str, Any]


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ContractArtifactError(f"cannot read bound source: {path}") from exc


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(f"invalid JSON source: {path}") from exc
    if not isinstance(value, dict):
        raise ContractArtifactError(f"JSON source must be an object: {path}")
    return value


def load_canonical_contract_artifact(
    manifest_path: str | Path = "artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json",
    *,
    project_root: str | Path = ".",
) -> CanonicalContractArtifact:
    """Resolve and verify the canonical contract projection by content hash."""

    root = Path(project_root).resolve(strict=True)
    manifest = Path(manifest_path)
    if not manifest.is_absolute():
        manifest = root / manifest
    manifest = manifest.resolve(strict=True)
    if not manifest.is_relative_to(root):
        raise ContractArtifactError("canonical manifest must be project-local")
    data = _json(manifest)
    required = {
        "schema", "status", "paper_sha", "source_catalog", "source_catalog_sha256",
        "contract_artifact", "contract_artifact_sha256", "anchor_count",
        "baseline_anchor_count", "extension_anchor_ids", "scope", "certification",
        "supersedes_for_catalog_indexing",
    }
    if set(data) != required or data["schema"] != SCHEMA:
        raise ContractArtifactError("canonical manifest schema or fields are invalid")
    if data["status"] != "bound_pending_replay":
        raise ContractArtifactError("canonical contract artifact must remain pending replay")
    if data["certification"] != {
        "case_certified": False,
        "paper_score_eligible": False,
        "formal_qualification_gates": False,
    }:
        raise ContractArtifactError("canonical contract artifact contains premature certification")
    artifact = (root / data["contract_artifact"]).resolve()
    catalog = (root / data["source_catalog"]).resolve()
    if not artifact.is_relative_to(root) or not catalog.is_relative_to(root):
        raise ContractArtifactError("canonical sources must be project-local")
    if _sha256(artifact) != data["contract_artifact_sha256"]:
        raise ContractArtifactError("contract artifact digest mismatch")
    if _sha256(catalog) != data["source_catalog_sha256"]:
        raise ContractArtifactError("source catalog digest mismatch")
    artifact_data = _json(artifact)
    contracts = artifact_data.get("contracts")
    if not isinstance(contracts, list) or len(contracts) != data["anchor_count"]:
        raise ContractArtifactError("canonical contract count mismatch")
    ids = [row.get("anchor_id") for row in contracts if isinstance(row, dict)]
    if len(ids) != len(contracts) or len(set(ids)) != len(ids):
        raise ContractArtifactError("canonical contract IDs are invalid")
    catalog_data = _json(catalog)
    anchors = catalog_data.get("anchors")
    if not isinstance(anchors, list) or len(anchors) != data["anchor_count"]:
        raise ContractArtifactError("canonical catalog count mismatch")
    catalog_ids = {row.get("anchor_id") for row in anchors if isinstance(row, dict)}
    if set(ids) != catalog_ids:
        raise ContractArtifactError("contract IDs differ from catalog IDs")
    extensions = tuple(data["extension_anchor_ids"])
    if len(extensions) != data["anchor_count"] - data["baseline_anchor_count"]:
        raise ContractArtifactError("baseline/extension count mismatch")
    if not set(extensions).issubset(catalog_ids):
        raise ContractArtifactError("extension anchor is absent from catalog")
    return CanonicalContractArtifact(
        manifest_path=manifest,
        artifact_path=artifact,
        artifact_sha256=data["contract_artifact_sha256"],
        catalog_path=catalog,
        catalog_sha256=data["source_catalog_sha256"],
        paper_sha=data["paper_sha"],
        anchor_count=data["anchor_count"],
        baseline_anchor_count=data["baseline_anchor_count"],
        extension_anchor_ids=extensions,
        payload=data,
    )


__all__ = ["CanonicalContractArtifact", "ContractArtifactError", "SCHEMA", "load_canonical_contract_artifact"]
