"""Generate and independently verify the versioned five-scene full matrix.

The matrix is an author/evaluator artifact.  It materializes every package in
the Cartesian product below without treating measurement variants as new
business goals::

    5 scenes × (baseline + 4 M0 + 4 M1) × 9 factor conditions = 405

Each canonical template has seven control/variant comparisons, for 315 paired
comparisons.  The existing baseline-only nine-condition suite remains an
identifiable 45-package, 35-pair subset.  No result from this module claims
runtime treatment effects, business-outcome validation, or scoreability.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from .goal_driven_contract import validate_goal_bundle, write_goal_task_bundle
from .goal_driven_tasks import (
    build_goal_authoring_brief,
    build_goal_task_package,
    review_goal_task_design,
    validate_goal_authoring_brief,
    validate_goal_factor_pair,
    write_goal_authoring_brief,
)
from .goal_freeze import (
    build_goal_freeze_manifest,
    validate_goal_freeze_manifest,
    verify_goal_freeze_files,
    write_goal_freeze_manifest,
)
from .goal_scene_registry import registered_goal_scenes
from .goal_suite import FACTOR_LEVELS
from .mechanism_templates import (
    M0_MECHANISMS,
    M1_MECHANISMS,
    SCENES as MECHANISM_SCENES,
    canonical_template_id,
    registered_mechanism_families,
    validate_mechanism_template,
)
from .provenance import canonical_sha256, file_sha256
from .release_manifest import build_goal_release_manifest
from .task_coverage import load_coverage_matrix


FULL_MATRIX_SCHEMA = "eai.benchmark.goal_full_matrix_audit"
FULL_MATRIX_VERSION = 1
SCENE_KEYS = (
    "factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant",
)
MECHANISM_FAMILIES = M0_MECHANISMS + M1_MECHANISMS
EXPECTED_CANONICAL_TEMPLATES = 45
EXPECTED_PACKAGES = 405
EXPECTED_FACTOR_PAIRS = 315
LEGACY_SUBSET_PACKAGES = 45
LEGACY_SUBSET_FACTOR_PAIRS = 35

_BUNDLE_FILES = {
    "public_task": "release/public_task.json",
    "participant_api": "release/participant_api.json",
    "baseline_manifest": "release/baseline_manifest.json",
    "hidden_evaluator": "evaluator/hidden_evaluator.json",
}
_PACKAGE_AUX_FILES = (
    "authoring/goal_authoring_brief.json",
    "authoring/review.json",
)
_SOURCE_FILES = (
    "goal_full_matrix.py", "goal_suite.py", "goal_driven_tasks.py",
    "goal_driven_fixtures.py", "goal_driven_contract.py", "goal_constraints.py",
    "goal_capability_audit.py", "skill_ontology.py", "goal_freeze.py",
    "goal_scene_registry.py", "release_manifest.py", "provenance.py",
    "measurement_contract.py", "mechanism_templates.py", "task_coverage.py",
    "task_coverage_matrix.json", "modality_matrix.json",
)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _safe_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("full-matrix artifact path must be a nonempty relative path")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != relative:
        raise ValueError("full-matrix artifact path must be normalized and relative")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("full-matrix artifacts cannot traverse symlinks")
    if not current.is_file():
        raise ValueError(f"missing full-matrix artifact: {relative}")
    return current


def _assert_safe_root(path: Path, label: str) -> Path:
    absolute = path.absolute()
    if any(item.is_symlink() for item in (absolute, *absolute.parents)):
        raise ValueError(f"{label} cannot traverse symlinks")
    return absolute


def _source_bindings() -> dict[str, dict[str, Any]]:
    files = {
        str(Path(__file__).with_name(name).resolve()): file_sha256(Path(__file__).with_name(name))
        for name in _SOURCE_FILES
    }
    revision = "local-full-matrix-authoring-" + canonical_sha256(files)
    return {scene: {"revision": revision, "files": dict(files)} for scene in SCENE_KEYS}


def _conditions() -> list[tuple[str, str]]:
    return [(factor, level) for factor, levels in FACTOR_LEVELS.items() for level in levels]


def _template_id(scene: str, mechanism: str | None) -> str:
    return f"{scene}.baseline" if mechanism is None else canonical_template_id(scene, mechanism)


def _variant_dir(mechanism: str | None) -> str:
    if mechanism is None:
        return "baseline"
    tier = "m0" if mechanism in M0_MECHANISMS else "m1"
    return f"{tier}.{mechanism}"


def _package_id(scene: str, mechanism: str | None, factor: str, level: str) -> str:
    return f"{_template_id(scene, mechanism)}/{factor}/{level}"


def _package_path(scene: str, mechanism: str | None, factor: str, level: str) -> str:
    return f"packages/{scene}/{_variant_dir(mechanism)}/{factor}/{level}"


def _canonical_templates() -> list[dict[str, Any]]:
    rows = []
    for scene in SCENE_KEYS:
        rows.append({
            "template_id": _template_id(scene, None),
            "scene_key": scene,
            "variant_kind": "baseline_business_goal",
            "mechanism_family": None,
        })
        for mechanism in MECHANISM_FAMILIES:
            rows.append({
                "template_id": _template_id(scene, mechanism),
                "scene_key": scene,
                "variant_kind": "measurement_metadata_variant",
                "mechanism_family": mechanism,
            })
    return rows


def _validate_static_contract() -> dict[str, Any]:
    registrations = tuple(
        row["scene_key"] for row in registered_goal_scenes()
        if row["package_kind"] == "goal_bundle"
    )
    if registrations != SCENE_KEYS or tuple(MECHANISM_SCENES) != SCENE_KEYS:
        raise ValueError("full-matrix scene registries must contain the same five scenes in order")
    if tuple(registered_mechanism_families()) != MECHANISM_FAMILIES:
        raise ValueError("full-matrix mechanisms differ from the shared M0+M1 registry")
    coverage = load_coverage_matrix()
    if tuple(coverage["scene_keys"]) != SCENE_KEYS:
        raise ValueError("full-matrix coverage registry is stale")
    counts = coverage["counting_policy"]
    expected = {
        "current_goal_packages": LEGACY_SUBSET_PACKAGES,
        "current_factor_pairs": LEGACY_SUBSET_FACTOR_PAIRS,
        "full_grid_goal_packages": EXPECTED_PACKAGES,
        "full_grid_factor_pairs": EXPECTED_FACTOR_PAIRS,
        "full_grid_canonical_templates": EXPECTED_CANONICAL_TEMPLATES,
        "conditions_per_canonical_template": len(_conditions()),
    }
    for key, value in expected.items():
        if counts.get(key) != value:
            raise ValueError(f"coverage matrix counting policy is stale: {key}")
    return coverage


def _pair_record(
        packages: Mapping[str, Mapping[str, Any]], *, scene: str,
        mechanism: str | None, factor: str, control: str, variant: str) -> dict[str, Any]:
    control_id = _package_id(scene, mechanism, factor, control)
    variant_id = _package_id(scene, mechanism, factor, variant)
    comparison = validate_goal_factor_pair(
        packages[control_id], packages[variant_id], factor_id=factor)
    return {
        "template_id": _template_id(scene, mechanism),
        "control_package_id": control_id,
        "variant_package_id": variant_id,
        "comparison": comparison,
    }


def _expected_pair_records(packages: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for scene in SCENE_KEYS:
        for mechanism in (None, *MECHANISM_FAMILIES):
            for factor, levels in FACTOR_LEVELS.items():
                for variant in levels[1:]:
                    rows.append(_pair_record(
                        packages, scene=scene, mechanism=mechanism, factor=factor,
                        control=levels[0], variant=variant))
    return rows


def _manifest_path(mechanism: str | None, factor: str, level: str) -> str:
    return f"authoring/manifests/{_variant_dir(mechanism)}/{factor}/{level}.json"


def _expected_manifest(
        packages: Mapping[str, Mapping[str, Any]], mechanism: str | None,
        factor: str, level: str) -> dict[str, Any]:
    condition = {
        scene: packages[_package_id(scene, mechanism, factor, level)]
        for scene in SCENE_KEYS
    }
    return build_goal_release_manifest(condition)


def write_goal_full_matrix(output_root: str | Path) -> dict[str, Any]:
    """Materialize all 405 packages, verify them, then publish a fresh directory."""
    _validate_static_contract()
    destination = _assert_safe_root(Path(output_root), "full-matrix output")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite full matrix: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_bindings = _source_bindings()
    staging = Path(tempfile.mkdtemp(prefix=".goal-full-matrix-", dir=destination.parent))
    reserved_destination = False
    try:
        packages: dict[str, dict[str, Any]] = {}
        records = []
        for scene in SCENE_KEYS:
            brief = build_goal_authoring_brief(scene)
            for mechanism in (None, *MECHANISM_FAMILIES):
                template_id = _template_id(scene, mechanism)
                for factor, level in _conditions():
                    package_id = _package_id(scene, mechanism, factor, level)
                    path = _package_path(scene, mechanism, factor, level)
                    bundle = build_goal_task_package(
                        scene, factor_id=factor, factor_level=level,
                        mechanism_family=mechanism)
                    write_goal_task_bundle(bundle, staging / path)
                    write_goal_authoring_brief(brief, staging / path / "authoring")
                    review = review_goal_task_design(
                        bundle["public_task"], bundle["hidden_evaluator"]["witness_plans"])
                    _write(staging / path / "authoring/review.json", review)
                    bundle_sha256 = canonical_sha256(bundle)
                    packages[package_id] = bundle
                    records.append({
                        "package_id": package_id,
                        "template_id": template_id,
                        "scene_key": scene,
                        "variant_kind": (
                            "baseline_business_goal" if mechanism is None
                            else "measurement_metadata_variant"),
                        "mechanism_family": mechanism,
                        "factor_id": factor,
                        "factor_level": level,
                        "path": path,
                        "bundle_sha256": bundle_sha256,
                        "execution_validated": False,
                        "business_outcomes_validated": False,
                        "scoreable": False,
                    })

        pair_records = _expected_pair_records(packages)
        _write(staging / "authoring/factor_pairs.json", pair_records)
        for mechanism in (None, *MECHANISM_FAMILIES):
            for factor, level in _conditions():
                _write(
                    staging / _manifest_path(mechanism, factor, level),
                    _expected_manifest(packages, mechanism, factor, level),
                )

        freeze = build_goal_freeze_manifest(
            packages,
            split_assignments={key: "reserved" for key in packages},
            source_bindings=source_bindings,
            scope="authoring_candidate",
        )
        freeze_path = "authoring/freeze_manifest.v1.json"
        (staging / "authoring").mkdir(exist_ok=True)
        write_goal_freeze_manifest(freeze, staging / freeze_path)

        legacy_ids = [row["package_id"] for row in records if row["mechanism_family"] is None]
        report = {
            "schema": FULL_MATRIX_SCHEMA,
            "version": FULL_MATRIX_VERSION,
            "visibility": "author_evaluator_only",
            "scene_keys": list(SCENE_KEYS),
            "mechanism_families": list(MECHANISM_FAMILIES),
            "factor_levels": {key: list(value) for key, value in FACTOR_LEVELS.items()},
            "canonical_template_count": len(_canonical_templates()),
            "canonical_templates": _canonical_templates(),
            "package_count": len(records),
            "packages": records,
            "factor_pair_count": len(pair_records),
            "factor_pairs": "authoring/factor_pairs.json",
            "legacy_condition_subset": {
                "identity": "baseline_templates_x_nine_conditions",
                "package_count": len(legacy_ids),
                "factor_pair_count": len(SCENE_KEYS) * sum(len(levels) - 1 for levels in FACTOR_LEVELS.values()),
                "package_ids": legacy_ids,
            },
            "variant_semantics": {
                "kind": "measurement_metadata_over_shared_scene_business_goals",
                "independent_business_goals_claimed": False,
                "package_count_is_not_an_independent_business_goal_count": True,
            },
            "source_binding_basis": "local_authoring_source_files_not_USD_measurements",
            "freeze_manifest": freeze_path,
            "limitations": [
                "Mechanism variants describe measurement metadata over the five scene business goals.",
                "The 405 materialized packages are not 405 independent business goals.",
                "Factor-effect and communication-trigger behavior remain execution-pending.",
                "Restaurant solo witnesses may not activate the inherited cross-mobility communication trigger.",
            ],
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
            "artifact_sha256": {
                path.relative_to(staging).as_posix(): file_sha256(path)
                for path in sorted(staging.rglob("*")) if path.is_file()
            },
        }
        _write(staging / "audit_report.json", report)
        verified = verify_goal_full_matrix(staging, verify_sources=True)
        if _source_bindings() != source_bindings:
            raise ValueError("authoring sources changed during full-matrix generation")
        destination.mkdir()
        reserved_destination = True
        staging.replace(destination)
        return {"output_root": str(destination), **verified}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if reserved_destination and destination.exists() and not (destination / "audit_report.json").exists():
            try:
                destination.rmdir()
            except OSError:
                pass


def verify_goal_full_matrix(
        output_root: str | Path, *, verify_sources: bool = False) -> dict[str, Any]:
    """Reconstruct the exact grid and reject missing, duplicate, extra or changed bytes."""
    if type(verify_sources) is not bool:
        raise ValueError("verify_sources must be a boolean")
    _validate_static_contract()
    root = _assert_safe_root(Path(output_root), "full-matrix root").resolve()
    report = _read(_safe_file(root, "audit_report.json"))
    expected_report_fields = {
        "schema", "version", "visibility", "scene_keys", "mechanism_families",
        "factor_levels", "canonical_template_count", "canonical_templates",
        "package_count", "packages", "factor_pair_count", "factor_pairs",
        "legacy_condition_subset", "variant_semantics", "source_binding_basis",
        "freeze_manifest", "limitations", "execution_validated",
        "business_outcomes_validated", "scoreable", "artifact_sha256",
    }
    if (not isinstance(report, Mapping) or set(report) != expected_report_fields
            or report["schema"] != FULL_MATRIX_SCHEMA
            or report["version"] != FULL_MATRIX_VERSION
            or report["visibility"] != "author_evaluator_only"):
        raise ValueError("unsupported full-matrix audit report")
    if report["scene_keys"] != list(SCENE_KEYS):
        raise ValueError("full-matrix scene order or identity is stale")
    if report["mechanism_families"] != list(MECHANISM_FAMILIES):
        raise ValueError("full-matrix mechanism registry is stale")
    if report["factor_levels"] != {key: list(value) for key, value in FACTOR_LEVELS.items()}:
        raise ValueError("full-matrix factor conditions are stale")
    if any(report[key] is not False for key in (
            "execution_validated", "business_outcomes_validated", "scoreable")):
        raise ValueError("authoring full matrix cannot claim execution, business, or scoring validation")
    semantics = report["variant_semantics"]
    if semantics != {
            "kind": "measurement_metadata_over_shared_scene_business_goals",
            "independent_business_goals_claimed": False,
            "package_count_is_not_an_independent_business_goal_count": True}:
        raise ValueError("full-matrix measurement-variant claim boundary is stale")

    inventory = report["artifact_sha256"]
    if not isinstance(inventory, Mapping) or not inventory:
        raise ValueError("full matrix requires a nonempty artifact inventory")
    actual_files = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("full-matrix artifacts cannot traverse symlinks")
        if path.is_file():
            if path != root / "audit_report.json":
                actual_files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("full-matrix artifacts must be regular files or directories")
    if actual_files != set(inventory):
        raise ValueError("full-matrix artifact inventory differs from disk")
    for relative, expected_sha256 in inventory.items():
        if file_sha256(_safe_file(root, relative)) != expected_sha256:
            raise ValueError(f"full-matrix artifact hash mismatch: {relative}")

    expected_templates = _canonical_templates()
    if (report["canonical_templates"] != expected_templates
            or report["canonical_template_count"] != EXPECTED_CANONICAL_TEMPLATES):
        raise ValueError("full matrix does not contain the exact 45 canonical templates")
    records = report["packages"]
    if not isinstance(records, list) or report["package_count"] != len(records):
        raise ValueError("full-matrix package count differs from its records")
    expected_ids = {
        _package_id(scene, mechanism, factor, level)
        for scene in SCENE_KEYS
        for mechanism in (None, *MECHANISM_FAMILIES)
        for factor, level in _conditions()
    }
    record_ids = [row.get("package_id") for row in records if isinstance(row, Mapping)]
    if len(record_ids) != len(records) or len(record_ids) != len(set(record_ids)):
        raise ValueError("full matrix contains duplicate or malformed package records")
    if set(record_ids) != expected_ids or len(records) != EXPECTED_PACKAGES:
        raise ValueError("full matrix has missing or extra package identities")

    packages: dict[str, dict[str, Any]] = {}
    expected_artifacts = {"authoring/factor_pairs.json", report["freeze_manifest"]}
    bundle_hashes = set()
    for row in records:
        expected_row_fields = {
            "package_id", "template_id", "scene_key", "variant_kind",
            "mechanism_family", "factor_id", "factor_level", "path",
            "bundle_sha256", "execution_validated", "business_outcomes_validated",
            "scoreable",
        }
        if set(row) != expected_row_fields:
            raise ValueError("full-matrix package record fields mismatch")
        scene = row["scene_key"]
        mechanism = row["mechanism_family"]
        factor = row["factor_id"]
        level = row["factor_level"]
        if (scene not in SCENE_KEYS or mechanism not in (None, *MECHANISM_FAMILIES)
                or factor not in FACTOR_LEVELS or level not in FACTOR_LEVELS[factor]):
            raise ValueError("full-matrix package record is outside the registered grid")
        if (row["package_id"] != _package_id(scene, mechanism, factor, level)
                or row["template_id"] != _template_id(scene, mechanism)
                or row["path"] != _package_path(scene, mechanism, factor, level)):
            raise ValueError("full-matrix package identity/path binding mismatch")
        expected_kind = "baseline_business_goal" if mechanism is None else "measurement_metadata_variant"
        if row["variant_kind"] != expected_kind:
            raise ValueError("full-matrix package variant kind is stale")
        if any(row[key] is not False for key in (
                "execution_validated", "business_outcomes_validated", "scoreable")):
            raise ValueError("full-matrix package cannot claim validated outcomes")
        for relative in (*_BUNDLE_FILES.values(), *_PACKAGE_AUX_FILES):
            expected_artifacts.add(f"{row['path']}/{relative}")
        bundle = {
            name: _read(_safe_file(root, f"{row['path']}/{relative}"))
            for name, relative in _BUNDLE_FILES.items()
        }
        validate_goal_bundle(bundle)
        public = bundle["public_task"]
        hidden = bundle["hidden_evaluator"]
        gate = hidden["release_gate"]
        if (public["mission"]["scene_key"] != scene
                or public["experiment_factor"]["id"] != factor
                or public["experiment_factor"]["level"] != level):
            raise ValueError("full-matrix package content differs from its index")
        template = hidden["mechanism_template"]
        if mechanism is None:
            if template is not None or hidden["measurement_status"] != "unclassified":
                raise ValueError("baseline full-matrix package gained mechanism metadata")
        else:
            normalized = validate_mechanism_template(template)["normalized"]
            if (normalized["template_id"] != row["template_id"]
                    or normalized["mechanism_family"] != mechanism
                    or hidden["measurement_status"] != "classified"):
                raise ValueError("full-matrix mechanism template binding is stale")
        if (gate["execution_status"] != "external_pending"
                or gate["scoreable"] is not False
                or hidden["factor_effect"]["effect_status"] != "execution_pending"):
            raise ValueError("full-matrix package release/effect flags are not fail-closed")
        brief = _read(_safe_file(root, f"{row['path']}/authoring/goal_authoring_brief.json"))
        validate_goal_authoring_brief(brief)
        expected_review = review_goal_task_design(public, hidden["witness_plans"])
        if _read(_safe_file(root, f"{row['path']}/authoring/review.json")) != expected_review:
            raise ValueError("full-matrix package authoring review is stale")
        digest = canonical_sha256(bundle)
        if row["bundle_sha256"] != digest:
            raise ValueError("full-matrix package bundle hash is stale")
        bundle_hashes.add(digest)
        packages[row["package_id"]] = bundle
    if len(bundle_hashes) != EXPECTED_PACKAGES:
        raise ValueError("full matrix contains aliased or duplicate package variants")

    pairs_path = report["factor_pairs"]
    if pairs_path != "authoring/factor_pairs.json":
        raise ValueError("full-matrix comparisons must remain author/evaluator-only")
    expected_pairs = _expected_pair_records(packages)
    if (_read(_safe_file(root, pairs_path)) != expected_pairs
            or report["factor_pair_count"] != len(expected_pairs)
            or len(expected_pairs) != EXPECTED_FACTOR_PAIRS):
        raise ValueError("full matrix has missing, duplicate, or stale factor comparisons")

    for mechanism in (None, *MECHANISM_FAMILIES):
        for factor, level in _conditions():
            relative = _manifest_path(mechanism, factor, level)
            expected_artifacts.add(relative)
            if _read(_safe_file(root, relative)) != _expected_manifest(
                    packages, mechanism, factor, level):
                raise ValueError("full-matrix condition manifest is stale")

    legacy_ids = [
        _package_id(scene, None, factor, level)
        for scene in SCENE_KEYS for factor, level in _conditions()
    ]
    if report["legacy_condition_subset"] != {
            "identity": "baseline_templates_x_nine_conditions",
            "package_count": LEGACY_SUBSET_PACKAGES,
            "factor_pair_count": LEGACY_SUBSET_FACTOR_PAIRS,
            "package_ids": legacy_ids}:
        raise ValueError("full matrix no longer identifies the legacy 45-package/35-pair subset")

    freeze_path = report["freeze_manifest"]
    if freeze_path != "authoring/freeze_manifest.v1.json":
        raise ValueError("full-matrix freeze must remain versioned and author-only")
    freeze = _read(_safe_file(root, freeze_path))
    validate_goal_freeze_manifest(freeze, packages)
    if verify_sources and not verify_goal_freeze_files(freeze)["validated"]:
        raise ValueError("full-matrix frozen source files differ from their declared hashes")
    if report["source_binding_basis"] != "local_authoring_source_files_not_USD_measurements":
        raise ValueError("full-matrix source binding must not claim physical-scene validation")
    if expected_artifacts != set(inventory):
        raise ValueError("full matrix contains files outside the protected public/author boundary")
    return {
        "validated": True,
        "canonical_template_count": EXPECTED_CANONICAL_TEMPLATES,
        "package_count": EXPECTED_PACKAGES,
        "factor_pair_count": EXPECTED_FACTOR_PAIRS,
        "legacy_subset_package_count": LEGACY_SUBSET_PACKAGES,
        "legacy_subset_factor_pair_count": LEGACY_SUBSET_FACTOR_PAIRS,
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="generate and verify a fresh 405-package matrix")
    audit.add_argument("--output-root", required=True, type=Path)
    verify = commands.add_parser("verify", help="verify an existing 405-package matrix")
    verify.add_argument("--output-root", required=True, type=Path)
    verify.add_argument("--verify-sources", action="store_true")
    args = parser.parse_args(argv)
    result = (
        write_goal_full_matrix(args.output_root)
        if args.command == "audit"
        else verify_goal_full_matrix(args.output_root, verify_sources=args.verify_sources)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


__all__ = [
    "EXPECTED_CANONICAL_TEMPLATES", "EXPECTED_FACTOR_PAIRS", "EXPECTED_PACKAGES",
    "FULL_MATRIX_SCHEMA", "FULL_MATRIX_VERSION", "LEGACY_SUBSET_FACTOR_PAIRS",
    "LEGACY_SUBSET_PACKAGES", "MECHANISM_FAMILIES", "SCENE_KEYS", "main",
    "verify_goal_full_matrix", "write_goal_full_matrix",
]


if __name__ == "__main__":
    raise SystemExit(main())
