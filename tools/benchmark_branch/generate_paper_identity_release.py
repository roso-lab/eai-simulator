"""Regenerate the checkout-portable artifacts bound to the current paper.

This is intentionally a narrow release step.  It updates paper/source identity
and explicit pending difficulty status, but it never promotes replay,
qualification, or paper-score eligibility.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmark.canonical_deployment_v3 import from_mapping
from benchmark.catalog_mapping_v3 import PROFILE, SCHEMA, verify_catalog_mapping
from benchmark.scorebook_v3 import PAPER_COMMIT
from benchmark.task_catalog import (
    CATALOG_SCHEMA_VERSION,
    CONTRACT_VERSION,
    TaskAnchor,
    validate_catalog,
)

from .generate_anchor_contracts import main as generate_anchor_contracts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG = Path("benchmark/task_catalog.json")
CONTRACT_ARTIFACT = Path(
    "artifacts/catalog/contracts/room06-extension-20261026/pilot_contracts-v3.json"
)
CONTRACT_MANIFEST = Path("artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json")
DECISION_SHEET = Path(
    "benchmark/docs/anchor_drafts/BINDING_DECISION_SHEET_20261024.zh-CN.md"
)
ROOM06_BINDING = Path("benchmark/docs/anchor_drafts/room_06.draft.zh-CN.md")
PREDICATE_BINDING = Path(
    "benchmark/docs/progress/PREDICATE_BINDING_20261023.zh-CN.md"
)
PILOT_CANDIDATES = Path(
    "tools/benchmark_branch/tests/fixtures/pilot-candidates-20261022.json"
)
P4_FIXTURE = Path("benchmark/fixtures/p4_candidate_factory_20261021")
ROBOT_RESUME = Path("benchmark/robot_resume/robot_pool_resume.v1.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _pretty_bytes(value: Any, *, sort_keys: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=sort_keys,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _input(root: Path, relative: Path) -> Path:
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"release input does not exist: {relative.as_posix()}") from exc
    if candidate.is_symlink() or not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError(f"release input must be a regular project file: {relative.as_posix()}")
    return resolved


def _output(root: Path, relative: Path) -> Path:
    candidate = root / relative
    parent = candidate.parent.resolve(strict=True)
    if not parent.is_relative_to(root) or candidate.is_symlink():
        raise ValueError(f"release output must remain inside project: {relative.as_posix()}")
    if candidate.exists() and not candidate.is_file():
        raise ValueError(f"release output must be a regular file: {relative.as_posix()}")
    return candidate


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON release input: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON release input must be an object: {path}")
    return value


def _normalize_catalog(root: Path) -> Path:
    path = _input(root, CATALOG)
    raw = path.read_text(encoding="utf-8")
    catalog = _json(path)
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("task catalog schema does not match engineering contract")
    if catalog.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("task catalog contract version does not match engineering contract")
    anchors = catalog.get("anchors")
    if not isinstance(anchors, list):
        raise ValueError("task catalog anchors must be a list")
    normalized: list[dict[str, Any]] = []
    explicit = []
    for item in anchors:
        if not isinstance(item, dict):
            raise ValueError("task catalog anchor must be an object")
        row = dict(item)
        current = row.get(
            "difficulty_certification_status", "difficulty_certification_pending"
        )
        if current != "difficulty_certification_pending":
            raise ValueError("difficulty certification cannot be promoted by identity release")
        explicit.append("difficulty_certification_status" in row)
        row["difficulty_certification_status"] = "difficulty_certification_pending"
        normalized.append(row)
    validate_catalog(TaskAnchor.from_mapping(item) for item in normalized)
    output = _output(root, CATALOG)
    if all(explicit):
        return output
    if any(explicit):
        raise ValueError("task catalog has a partial difficulty-status migration")

    lines = raw.splitlines(keepends=True)
    migrated: list[str] = []
    insertion_count = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('"oracle_status":'):
            newline = "\n" if line.endswith("\n") else ""
            body = line[:-1] if newline else line
            has_comma = body.endswith(",")
            if not has_comma:
                body += ","
            migrated.append(body + newline)
            suffix = "," if has_comma else ""
            migrated.append(
                '      "difficulty_certification_status": '
                f'"difficulty_certification_pending"{suffix}{newline}'
            )
            insertion_count += 1
        else:
            migrated.append(line)
    if insertion_count != len(anchors):
        raise ValueError("task catalog formatting has no exact per-anchor oracle status seam")
    migrated_raw = "".join(migrated)
    migrated_value = json.loads(migrated_raw)
    if migrated_value.get("anchors") != normalized:
        raise ValueError("format-preserving task catalog migration changed anchor semantics")
    output.write_text(migrated_raw, encoding="utf-8")
    return output


def _generate_contract_release(root: Path, catalog: Path) -> tuple[Path, Path]:
    artifact = _output(root, CONTRACT_ARTIFACT)
    argv = [
        "--project-root", str(root),
        "--catalog", str(catalog),
        "--decision-sheet", str(_input(root, DECISION_SHEET)),
        "--additional-binding-source", str(_input(root, ROOM06_BINDING)),
        "--additional-binding-source", str(_input(root, PREDICATE_BINDING)),
        "--pilot-candidates", str(_input(root, PILOT_CANDIDATES)),
        "--paper-sha", PAPER_COMMIT,
        "--output", str(artifact),
    ]
    if generate_anchor_contracts(argv) != 0:
        raise ValueError("anchor contract generation failed")
    payload = _json(artifact)
    contracts = payload.get("contracts")
    if payload.get("paper_sha") != PAPER_COMMIT or not isinstance(contracts, list):
        raise ValueError("generated contract artifact has stale identity or inventory")
    if len(contracts) != 31 or any(
        not isinstance(item, dict)
        or item.get("oracle_contract", {}).get("binding_status")
        != "bound_pending_replay"
        for item in contracts
    ):
        raise ValueError("generated contracts must remain 31 pending-replay bindings")

    manifest = {
        "schema": "heterocoord.canonical_contract_artifact.v1",
        "status": "bound_pending_replay",
        "paper_sha": PAPER_COMMIT,
        "source_catalog": CATALOG.as_posix(),
        "source_catalog_sha256": _sha256(catalog),
        "contract_artifact": CONTRACT_ARTIFACT.as_posix(),
        "contract_artifact_sha256": _sha256(artifact),
        "anchor_count": 31,
        "baseline_anchor_count": 30,
        "extension_anchor_ids": ["liv-t-04"],
        "scope": (
            "30 baseline anchors plus the author-approved liv-t-04 room_06 "
            "extension; six historical pilot contracts are included in this projection"
        ),
        "certification": {
            "case_certified": False,
            "paper_score_eligible": False,
            "formal_qualification_gates": False,
        },
        "supersedes_for_catalog_indexing": [
            "artifacts/catalog/anchor-contracts-20261027.json"
        ],
    }
    manifest_path = _output(root, CONTRACT_MANIFEST)
    manifest_path.write_bytes(_pretty_bytes(manifest))
    return artifact, manifest_path


def _generate_p4_release(root: Path) -> tuple[Path, Path, Path]:
    fixture = root / P4_FIXTURE
    catalog = _input(root, P4_FIXTURE / "catalog.json")
    scenario = _input(root, P4_FIXTURE / "scenario.json")
    contract = _input(root, P4_FIXTURE / "contract.json")
    mapping_path = _input(root, P4_FIXTURE / "catalog_contract_mapping.json")
    robot_resume = _input(root, ROBOT_RESUME)
    contract_value = _json(contract)
    scenario_value = _json(scenario)
    mapping = _json(mapping_path)
    if mapping.get("schema") != SCHEMA or mapping.get("mapping_profile") != PROFILE:
        raise ValueError("P4 mapping template has unsupported schema or profile")
    claims = mapping.get("claims")
    if not isinstance(claims, list):
        raise ValueError("P4 mapping template claims must be a list")
    identity = {
        "instance_id": mapping.get("instance_id"),
        "anchor_id": mapping.get("anchor_id"),
        "scene_key": mapping.get("scene_key"),
    }
    if identity != {
        "instance_id": "prod-p4-01-instance-candidate-20261021",
        "anchor_id": "prod-p4-01",
        "scene_key": "factory",
    }:
        raise ValueError("P4 mapping template identity changed")

    package = {
        "schema": "heterocoord.canonical_package.v3",
        **identity,
        "paper_commit": PAPER_COMMIT,
        "catalog_sha256": _sha256(catalog),
        "scenario_sha256": _sha256(scenario),
        "contract": contract_value,
    }
    package_path = _output(root, P4_FIXTURE / "package.json")
    package_path.write_bytes(_canonical_bytes(package))

    mapping = {
        "schema": SCHEMA,
        "mapping_profile": PROFILE,
        "paper_commit": PAPER_COMMIT,
        **identity,
        "source_digests": {
            "catalog_sha256": _sha256(catalog),
            "scenario_sha256": _sha256(scenario),
            "contract_sha256": _sha256(contract),
            "package_sha256": _sha256(package_path),
            "canonical_contract_digest": from_mapping(contract_value).digest(),
            "robot_resume_sha256": _sha256(robot_resume),
        },
        "claims": claims,
    }
    mapping_output = _output(root, P4_FIXTURE / "catalog_contract_mapping.json")
    mapping_output.write_bytes(_pretty_bytes(mapping))
    verification = verify_catalog_mapping(
        mapping_output,
        expected_mapping_sha256=_sha256(mapping_output),
        catalog_path=catalog,
        scenario_path=scenario,
        contract_path=contract,
        package_path=package_path,
        robot_resume_path=robot_resume,
        project_root=root,
    )
    receipt_path = _output(
        root, P4_FIXTURE / "catalog_contract_mapping_receipt.json"
    )
    receipt_path.write_bytes(verification.receipt_bytes())
    return package_path, mapping_output, receipt_path


def generate_release(project_root: str | Path = PROJECT_ROOT) -> tuple[Path, ...]:
    root = Path(project_root).resolve(strict=True)
    catalog = _normalize_catalog(root)
    artifact, manifest = _generate_contract_release(root, catalog)
    package, mapping, receipt = _generate_p4_release(root)
    return catalog, artifact, manifest, package, mapping, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)
    generate_release(args.project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
