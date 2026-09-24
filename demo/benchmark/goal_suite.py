"""Generate, freeze and read back the goal-task authoring matrix.

This author-only command does not launch a simulator, execute a participant or
score an episode. All admitted scenes produce the same nine-condition goal
matrix.  The separate ``full-audit`` command materializes the versioned
baseline + M0 + M1 grid while preserving this baseline-only compatibility API.
Optional City preparation drafts are copied and re-intaken separately;
including a draft never promotes it or claims scoring readiness.
Existing public templates are reserved for further review, not called
secret held-out evaluation data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from .goal_driven_contract import validate_goal_bundle, write_goal_task_bundle
from .goal_driven_tasks import (
    build_goal_authoring_brief, build_goal_task_package, generate_goal_mechanism_templates,
    review_goal_task_design, validate_goal_authoring_brief, validate_goal_factor_pair,
    write_goal_authoring_brief,
)
from .mechanism_templates import M0_MECHANISMS, canonical_template_id, validate_mechanism_template
from .goal_scene_registry import goal_scene_registration, registered_goal_scenes
from .provenance import canonical_sha256, file_sha256
from .release_manifest import build_goal_release_manifest
from .task_coverage import load_coverage_matrix


FACTOR_LEVELS = {
    "communication_quality": ("immediate", "delayed", "dropped", "bandwidth_limited", "stale"),
    "team_continuity": ("stable", "member_exit", "member_join", "capability_change"),
}
_BUNDLE_FILES = {
    "public_task": "release/public_task.json", "participant_api": "release/participant_api.json",
    "baseline_manifest": "release/baseline_manifest.json", "hidden_evaluator": "evaluator/hidden_evaluator.json",
}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _relative_file(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ValueError("suite artifact must be a normalized relative path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise ValueError("suite artifact must be a normalized relative path")
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("suite artifact cannot traverse a symlink")
    if not current.is_file():
        raise ValueError(f"missing suite artifact: {relative}")
    return current


def _source_bindings(scene_keys):
    # Bind the authoring implementation, explicitly not an unverified USD
    # revision. Source fingerprints do not turn experimental geometry into
    # measured scene data.
    sources = ("goal_driven_tasks.py", "goal_driven_fixtures.py", "goal_driven_contract.py",
               "goal_constraints.py", "goal_capability_audit.py", "skill_ontology.py",
               "goal_freeze.py", "goal_suite.py", "goal_scene_registry.py", "goal_city_intake.py",
               "goal_service_catalog.py", "release_manifest.py", "provenance.py",
               "measurement_contract.py", "mechanism_templates.py", "task_coverage.py",
               "task_coverage_matrix.json", "modality_matrix.json")
    files = {str(Path(__file__).with_name(name).resolve()): file_sha256(Path(__file__).with_name(name))
             for name in sources}
    return {scene: {"revision": "local-authoring-" + canonical_sha256(files), "files": dict(files)}
            for scene in scene_keys}


def _city_intake_summary(candidate):
    """Keep content bindings, not the temporary staging directory or old pass flags."""
    source = {key: value for key, value in candidate["source_binding"].items()
              if key not in {"verification_requested", "status", "checked_file_count"}}
    return {"schema": "eai.benchmark.city_suite_intake", "version": 1,
            "visibility": "author_only", "scene_key": "city", "kind": candidate["kind"],
            "task_id": candidate["task_id"], "source_binding": source,
            "intake_review": candidate["intake_review"], "service_review": candidate["service_review"],
            "factor_candidate_inputs": candidate["factor_candidate_inputs"]}


def _select_scenes(scene_keys, city_package_dir):
    if scene_keys is None:
        return [row["scene_key"] for row in registered_goal_scenes()
                if row["package_kind"] == "goal_bundle" or city_package_dir is not None]
    if (not isinstance(scene_keys, (list, tuple)) or not scene_keys
            or any(not isinstance(key, str) for key in scene_keys)
            or len(scene_keys) != len(set(scene_keys))):
        raise ValueError("scene_keys must be a nonempty list of distinct registered scenes")
    for key in scene_keys:
        goal_scene_registration(key)
    authoring_only = [key for key in scene_keys
                      if goal_scene_registration(key)["package_kind"] != "goal_bundle"]
    if authoring_only:
        raise ValueError(
            "goal_suite condition matrices admit only goal_bundle scenes; "
            f"authoring-only scenes require separate admission: {authoring_only}"
        )
    if city_package_dir is not None and "city" not in scene_keys:
        raise ValueError("--city-package requires city in the selected scene matrix")
    return list(scene_keys)


def _validate_coverage_contract():
    """Validate the authoring matrix before generating or reading a suite."""
    matrix = load_coverage_matrix()
    # The coverage matrix is the formal factor-matrix admission list.  The
    # shared task-generation registry also exposes authoring-only families,
    # which must not silently change the admitted-scene coverage counts.
    registered = tuple(row["scene_key"] for row in registered_goal_scenes()
                       if row["package_kind"] == "goal_bundle")
    if tuple(matrix["scene_keys"]) != registered:
        raise ValueError("coverage matrix scene registry is stale")
    return matrix


def _validate_default_coverage_counts(scene_keys, package_count, factor_pair_count):
    """Keep the published count vocabulary tied to the same source of truth."""
    matrix = _validate_coverage_contract()
    if tuple(scene_keys) != tuple(matrix["scene_keys"]):
        return
    counts = matrix["counting_policy"]
    if package_count != counts["current_goal_packages"]:
        raise ValueError("default suite package count differs from coverage matrix")
    if factor_pair_count != counts["current_factor_pairs"]:
        raise ValueError("default suite factor-pair count differs from coverage matrix")


def write_goal_suite(output_root, *, scene_keys=None, city_package_dir=None, verify_sources=False):
    """Write a fresh matrix, verify its readback, and publish without overwrite."""
    from .goal_freeze import build_goal_freeze_manifest, write_goal_freeze_manifest

    if type(verify_sources) is not bool:
        raise ValueError("verify_sources must be a boolean")
    _validate_coverage_contract()

    destination = Path(output_root).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite suite: {destination}")
    keys = _select_scenes(scene_keys, city_package_dir)
    if city_package_dir is not None:
        city_root = Path(city_package_dir).resolve()
        if destination.resolve() == city_root or city_root in destination.resolve().parents:
            raise ValueError("suite output must not modify the source city preparation package")
    bundle_keys = [key for key in keys if goal_scene_registration(key)["package_kind"] == "goal_bundle"]
    source_bindings = _source_bindings(bundle_keys)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".goal-suite-", dir=destination.parent))
    reserved_destination = False
    try:
        packages, records, factor_pairs = {}, [], []
        for factor, levels in FACTOR_LEVELS.items():
            for level in levels:
                condition = {}
                for scene in bundle_keys:
                    identifier = f"{factor}/{level}/{scene}"
                    bundle = build_goal_task_package(scene, factor_id=factor, factor_level=level)
                    package_dir = staging / "packages" / identifier
                    write_goal_task_bundle(bundle, package_dir)
                    write_goal_authoring_brief(build_goal_authoring_brief(scene), package_dir / "authoring")
                    review = review_goal_task_design(bundle["public_task"], bundle["hidden_evaluator"]["witness_plans"])
                    _write(package_dir / "authoring/review.json", review)
                    packages[identifier] = bundle
                    condition[scene] = bundle
                    records.append({"package_id": identifier, "scene_key": scene,
                                    "factor_id": factor, "factor_level": level,
                                    "path": f"packages/{identifier}"})
                if condition:
                    _write(staging / "manifests" / factor / f"{level}.json", build_goal_release_manifest(condition))
            for scene in bundle_keys:
                for level in levels[1:]:
                    factor_pairs.append(validate_goal_factor_pair(
                        packages[f"{factor}/{levels[0]}/{scene}"], packages[f"{factor}/{level}/{scene}"],
                        factor_id=factor))
        candidates = []
        if "city" in keys and city_package_dir is not None:
            from .goal_city_intake import load_city_goal_candidate
            from .goal_service_catalog import public_service_proposals, review_service_requirements

            load_city_goal_candidate(city_package_dir, verify_sources=verify_sources)
            original_root = Path(city_package_dir).resolve()
            # The intake validates the complete artifact inventory before copy.
            manifest = _read(original_root / "integration_manifest.json")
            for relative in [*manifest["artifact_sha256"], "integration_manifest.json"]:
                source = _relative_file(original_root, relative)
                target = staging / "candidates/city" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            copied = load_city_goal_candidate(staging / "candidates/city", verify_sources=verify_sources)
            if (canonical_sha256(_read(original_root / "integration_manifest.json"))
                    != canonical_sha256(_read(staging / "candidates/city/integration_manifest.json"))):
                raise ValueError("city manifest changed during suite creation")
            private = _read(staging / "candidates/city/authoring/private.json")
            public = _read(staging / "candidates/city/release/public_task.json")
            service_review = review_service_requirements(private["required_services"], public)
            _write(staging / "authoring/city_service_proposals.json", public_service_proposals())
            _write(staging / "authoring/city_service_review.json", service_review)
            _write(staging / "authoring/city_intake.json", _city_intake_summary(copied))
            # The goal matrix above already freezes the full City bundles.
            # Keep the optional preparation intake's compatibility reports
            # separate from those bundles and from formal execution gates.
            city_bundle = build_goal_task_package("city")
            city_review = review_goal_task_design(city_bundle["public_task"],
                                                  city_bundle["hidden_evaluator"]["witness_plans"])
            city_pair = validate_goal_factor_pair(
                city_bundle, build_goal_task_package("city", factor_level="delayed"),
                factor_id="communication_quality")
            _write(staging / "authoring/city_goal_bundle_review.json", city_review)
            _write(staging / "authoring/city_factor_pair.json", city_pair)
            candidates.append({"scene_key": "city", "path": "candidates/city",
                               "package_kind": "preparation_draft_not_goal_bundle",
                               "authoring_status": "blocked", "execution_status": "external_pending",
                               "full_bundle_authoring_status": city_review["authoring_status"],
                               "factor_pair_status": "concrete_treatments_and_witnesses_available_execution_pending",
                               "scoreable": False,
                               "blockers": _read(staging / "candidates/city/authoring/review.json")["blockers"]})
        freeze_path = None
        if packages:
            freeze = build_goal_freeze_manifest(
                packages, split_assignments={key: "reserved" for key in packages},
                source_bindings=source_bindings, scope="authoring_candidate")
            freeze_path = "authoring/freeze_manifest.json"
            (staging / "authoring").mkdir(exist_ok=True)
            write_goal_freeze_manifest(freeze, staging / freeze_path)
        report = {
            "schema": "eai.benchmark.goal_suite_audit", "version": 2, "visibility": "author_only",
            "city_preparation_included": bool(candidates),
            "registered_scenes": registered_goal_scenes(), "included_scene_keys": keys,
            "package_count": len(records), "candidate_count": len(candidates),
            "factor_pair_count": len(factor_pairs), "packages": records, "candidates": candidates,
            "factor_pairs": factor_pairs, "freeze_manifest": freeze_path,
            "source_binding_basis": "local_authoring_source_files_not_USD_measurements",
            "held_out_validated": False, "execution_validated": False, "scoreable": False,
            "artifact_sha256": {path.relative_to(staging).as_posix(): file_sha256(path)
                                for path in sorted(staging.rglob("*")) if path.is_file()},
        }
        _write(staging / "audit_report.json", report)
        verified = verify_goal_suite(staging, verify_sources=verify_sources)
        if _source_bindings(bundle_keys) != source_bindings:
            raise ValueError("authoring sources changed during suite generation; regenerate from stable inputs")
        # mkdir reserves the destination exclusively. Replacing our own empty
        # directory publishes the staged suite; any pre-existing result fails.
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


def verify_goal_suite(output_root, *, verify_sources=False):
    """Verify bytes and semantics; no saved pass flag is treated as evidence."""
    root = Path(output_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("suite root cannot traverse a symlink")
    root = root.resolve()
    if type(verify_sources) is not bool:
        raise ValueError("verify_sources must be a boolean")
    _validate_coverage_contract()
    report = _read(_relative_file(root, "audit_report.json"))
    if (report.get("schema") != "eai.benchmark.goal_suite_audit" or report.get("version") != 2
            or report.get("visibility") != "author_only"):
        raise ValueError("unsupported goal suite audit report")
    if report.get("registered_scenes") != registered_goal_scenes():
        raise ValueError("suite scene registry is stale")
    if type(report.get("city_preparation_included")) is not bool:
        raise ValueError("suite must declare whether city preparation is included")
    _select_scenes(report.get("included_scene_keys"),
                   root / "candidates/city" if report["city_preparation_included"] else None)
    if any(report.get(key) is not False for key in ("held_out_validated", "execution_validated", "scoreable")):
        raise ValueError("authoring suite cannot claim held-out, execution or scoring validation")
    inventory = report.get("artifact_sha256")
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("goal suite requires an artifact inventory")
    actual = set()
    for path in root.rglob("*"):
        # Directory and dangling links do not appear in a file-only inventory,
        # but a recursive release consumer could follow them to private data.
        if path.is_symlink():
            raise ValueError("suite artifact cannot traverse a symlink")
        if path.is_file():
            if path != root / "audit_report.json":
                actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("suite artifacts must be regular files or directories")
    if actual != set(inventory):
        raise ValueError("goal suite artifact inventory differs from disk")
    for relative, digest in inventory.items():
        if file_sha256(_relative_file(root, relative)) != digest:
            raise ValueError(f"goal suite artifact hash mismatch: {relative}")
    packages, expected_artifacts = {}, set()
    for record in report["packages"]:
        identifier = record["package_id"]
        if identifier in packages:
            raise ValueError("duplicate goal suite package id")
        expected_id = f"{record['factor_id']}/{record['factor_level']}/{record['scene_key']}"
        if identifier != expected_id or record["path"] != f"packages/{identifier}":
            raise ValueError("suite package identity/path mismatch")
        if (record["scene_key"] not in report["included_scene_keys"]
                or goal_scene_registration(record["scene_key"])["package_kind"] != "goal_bundle"
                or record["factor_id"] not in FACTOR_LEVELS
                or record["factor_level"] not in FACTOR_LEVELS[record["factor_id"]]):
            raise ValueError("suite package is outside the registered condition matrix")
        expected_artifacts.update(record["path"] + "/" + relative for relative in _BUNDLE_FILES.values())
        package = {key: _read(_relative_file(root, record["path"] + "/" + relative))
                   for key, relative in _BUNDLE_FILES.items()}
        validate_goal_bundle(package)
        if (package["public_task"]["mission"]["scene_key"] != record["scene_key"]
                or package["public_task"]["experiment_factor"]["id"] != record["factor_id"]
                or package["public_task"]["experiment_factor"]["level"] != record["factor_level"]):
            raise ValueError("suite package public identity differs from index")
        author = record["path"] + "/authoring/"
        expected_artifacts.update({author + "goal_authoring_brief.json", author + "review.json"})
        validate_goal_authoring_brief(_read(_relative_file(root, author + "goal_authoring_brief.json")))
        expected_review = review_goal_task_design(package["public_task"], package["hidden_evaluator"]["witness_plans"])
        if _read(_relative_file(root, author + "review.json")) != expected_review:
            raise ValueError("suite authoring review is stale")
        packages[identifier] = package
    scenes = [key for key in report["included_scene_keys"] if goal_scene_registration(key)["package_kind"] == "goal_bundle"]
    expected_pairs = []
    for factor, levels in FACTOR_LEVELS.items():
        for scene in scenes:
            for level in levels[1:]:
                expected_pairs.append(validate_goal_factor_pair(
                    packages[f"{factor}/{levels[0]}/{scene}"], packages[f"{factor}/{level}/{scene}"], factor_id=factor))
        for level in levels:
            condition = {scene: packages[f"{factor}/{level}/{scene}"] for scene in scenes}
            if condition:
                expected_artifacts.add(f"manifests/{factor}/{level}.json")
                if _read(_relative_file(root, f"manifests/{factor}/{level}.json")) != build_goal_release_manifest(condition):
                    raise ValueError("suite release manifest is stale")
    if report["factor_pairs"] != expected_pairs or report["factor_pair_count"] != len(expected_pairs):
        raise ValueError("suite factor pair report is incomplete or stale")
    if report["package_count"] != len(packages) or len(packages) != len(scenes) * 9:
        raise ValueError("suite condition coverage is incomplete")
    _validate_default_coverage_counts(report["included_scene_keys"], report["package_count"],
                                      report["factor_pair_count"])
    if packages:
        from .goal_freeze import validate_goal_freeze_manifest, verify_goal_freeze_files
        if report["freeze_manifest"] != "authoring/freeze_manifest.json":
            raise ValueError("suite freeze must stay in the author-only directory")
        expected_artifacts.add(report["freeze_manifest"])
        freeze = _read(_relative_file(root, report["freeze_manifest"]))
        validate_goal_freeze_manifest(freeze, packages)
        if verify_sources:
            if not verify_goal_freeze_files(freeze)["validated"]:
                raise ValueError("suite frozen source files no longer match their declared hashes")
    elif report["freeze_manifest"] is not None:
        raise ValueError("suite without admitted bundles cannot have a goal freeze manifest")
    if report["candidate_count"] != len(report["candidates"]):
        raise ValueError("suite candidate count mismatch")
    if bool(report["candidates"]) != report["city_preparation_included"]:
        raise ValueError("suite city registration and candidate coverage differ")
    if len(report["candidates"]) > 1:
        raise ValueError("suite may include only one prepared city candidate")
    for candidate in report["candidates"]:
        if (candidate["scene_key"] != "city" or candidate["path"] != "candidates/city"
                or candidate["package_kind"] != "preparation_draft_not_goal_bundle"
                or candidate["authoring_status"] != "blocked" or candidate["scoreable"] is not False):
            raise ValueError("city draft cannot be promoted by suite records; only the registered "
                             "builder and its release gates admit the full bundle")
        from .goal_city_intake import load_city_goal_candidate
        current_intake = load_city_goal_candidate(root / candidate["path"], verify_sources=verify_sources)
        if _read(root / "authoring/city_intake.json") != _city_intake_summary(current_intake):
            raise ValueError("suite city intake report is stale")
        city_manifest = _read(root / "candidates/city/integration_manifest.json")
        expected_artifacts.update("candidates/city/" + relative for relative in city_manifest["artifact_sha256"])
        expected_artifacts.add("candidates/city/integration_manifest.json")
        expected_artifacts.update({"authoring/city_intake.json", "authoring/city_service_proposals.json",
                                   "authoring/city_service_review.json"})
        from .goal_service_catalog import public_service_proposals, review_service_requirements
        city_private = _read(root / "candidates/city/authoring/private.json")
        city_public = _read(root / "candidates/city/release/public_task.json")
        if (_read(root / "authoring/city_service_proposals.json") != public_service_proposals()
                or _read(root / "authoring/city_service_review.json") != review_service_requirements(
                    city_private["required_services"], city_public)):
            raise ValueError("suite city service proposal or review is stale")
        source_review = _read(root / "candidates/city/authoring/review.json")
        if (candidate["blockers"] != source_review["blockers"]
                or candidate["execution_status"] != "external_pending"
                or candidate["factor_pair_status"] != "concrete_treatments_and_witnesses_available_execution_pending"):
            raise ValueError("suite city pending conditions differ from source review")
        # Re-derive the audited full bundle: a stale city builder invalidates
        # the suite exactly like a stale shared contract.
        city_bundle = build_goal_task_package("city")
        expected_review = review_goal_task_design(city_bundle["public_task"],
                                                  city_bundle["hidden_evaluator"]["witness_plans"])
        if candidate["full_bundle_authoring_status"] != expected_review["authoring_status"]:
            raise ValueError("suite city full-bundle authoring status is stale")
        if _read(root / "authoring/city_goal_bundle_review.json") != expected_review:
            raise ValueError("suite city goal bundle review is stale")
        expected_pair = validate_goal_factor_pair(
            city_bundle, build_goal_task_package("city", factor_level="delayed"),
            factor_id="communication_quality")
        if _read(root / "authoring/city_factor_pair.json") != expected_pair:
            raise ValueError("suite city factor pair review is stale")
        expected_artifacts.update({"authoring/city_goal_bundle_review.json",
                                   "authoring/city_factor_pair.json"})
    if expected_artifacts != set(inventory):
        raise ValueError("suite contains artifacts outside the reviewed public/author boundary")
    return {"validated": True, "package_count": len(packages), "candidate_count": len(report["candidates"]),
            "factor_pair_count": len(expected_pairs), "scoreable": False, "held_out_validated": False}


def write_goal_mechanism_suite(output_root, *, scene_keys=None, mechanisms=None,
                               factor_id="communication_quality", factor_level=None):
    """Publish and verify a fresh author-only M0 mechanism matrix.

    This is intentionally separate from :func:`write_goal_suite`: the latter
    preserves the historical nine-condition compatibility matrix, while this
    command publishes the admitted-scene M0 canonical bundles (currently 20).
    """
    from .task_coverage import validate_m0_template_registry

    destination = Path(output_root).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite mechanism suite: {destination}")
    selected_scenes = list(scene_keys) if scene_keys is not None else list(
        row["scene_key"] for row in registered_goal_scenes()
        if row["package_kind"] == "goal_bundle")
    selected_mechanisms = list(mechanisms) if mechanisms is not None else list(M0_MECHANISMS)
    if not selected_scenes or len(selected_scenes) != len(set(selected_scenes)):
        raise ValueError("mechanism suite requires distinct scenes")
    authoring_only = [scene for scene in selected_scenes
                      if goal_scene_registration(scene)["package_kind"] != "goal_bundle"]
    if authoring_only:
        raise ValueError(
            "goal_suite mechanism matrices admit only goal_bundle scenes; "
            f"authoring-only scenes require separate admission: {authoring_only}"
        )
    if not selected_mechanisms or len(selected_mechanisms) != len(set(selected_mechanisms)):
        raise ValueError("mechanism suite requires distinct mechanisms")
    if scene_keys is None and mechanisms is None:
        validate_m0_template_registry()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".goal-mechanism-suite-", dir=destination.parent))
    try:
        packages = generate_goal_mechanism_templates(
            selected_scenes, mechanisms=selected_mechanisms,
            factor_id=factor_id, factor_level=factor_level,
            output_root=str(staging / "templates"))
        records = []
        for template_id, bundle in packages.items():
            template = bundle["hidden_evaluator"]["mechanism_template"]
            validate_mechanism_template(template)
            records.append({
                "template_id": template_id,
                "scene_key": template["scene_key"],
                "mechanism_family": template["mechanism_family"],
                "path": f"templates/{template_id}",
                "measurement_status": bundle["hidden_evaluator"]["measurement_status"],
                "execution_status": bundle["hidden_evaluator"]["release_gate"]["execution_status"],
                "scoreable": False,
            })
        report = {
            "schema": "eai.benchmark.goal_mechanism_suite_audit",
            "version": 1,
            "visibility": "author_only",
            "scene_keys": selected_scenes,
            "mechanism_families": selected_mechanisms,
            "canonical_template_count": len(records),
            "templates": sorted(records, key=lambda row: row["template_id"]),
            "execution_validated": False,
            "scoreable": False,
            "artifact_sha256": {
                path.relative_to(staging).as_posix(): file_sha256(path)
                for path in sorted(staging.rglob("*")) if path.is_file()
            },
        }
        _write(staging / "audit_report.json", report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir()
        staging.replace(destination)
        return verify_goal_mechanism_suite(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_goal_mechanism_suite(output_root):
    """Verify M0 template identities, bytes and fail-closed release states."""
    root = Path(output_root).absolute()
    if root.is_symlink():
        raise ValueError("mechanism suite root cannot be a symlink")
    report = _read(root / "audit_report.json")
    expected = {"schema", "version", "visibility", "scene_keys", "mechanism_families",
                "canonical_template_count", "templates", "execution_validated", "scoreable",
                "artifact_sha256"}
    if set(report) != expected or report["schema"] != "eai.benchmark.goal_mechanism_suite_audit" \
            or report["version"] != 1 or report["visibility"] != "author_only":
        raise ValueError("unsupported mechanism suite audit report")
    if report["execution_validated"] is not False or report["scoreable"] is not False:
        raise ValueError("mechanism suite cannot claim execution or scoring validation")
    records = report["templates"]
    if report["canonical_template_count"] != len(records):
        raise ValueError("mechanism suite template count mismatch")
    expected_ids = {
        canonical_template_id(scene, mechanism)
        for scene in report["scene_keys"] for mechanism in report["mechanism_families"]
    }
    if {row.get("template_id") for row in records} != expected_ids:
        raise ValueError("mechanism suite does not cover the selected scene/mechanism matrix")
    expected_files = set()
    for row in records:
        template_id = row["template_id"]
        package_root = root / row["path"]
        if row["path"] != f"templates/{template_id}":
            raise ValueError("mechanism suite package path is not canonical")
        for relative in ("release/public_task.json", "release/participant_api.json",
                         "release/baseline_manifest.json", "evaluator/hidden_evaluator.json",
                         "authoring/goal_authoring_brief.json", "authoring/review.json"):
            path = package_root / relative
            if path.is_symlink() or not path.is_file():
                raise ValueError("mechanism suite package contains a missing or symlinked artifact")
            expected_files.add(f"{row['path']}/{relative}")
        public = _read(package_root / "release/public_task.json")
        hidden = _read(package_root / "evaluator/hidden_evaluator.json")
        bundle = {
            "public_task": public,
            "participant_api": _read(package_root / "release/participant_api.json"),
            "baseline_manifest": _read(package_root / "release/baseline_manifest.json"),
            "hidden_evaluator": hidden,
        }
        from .goal_driven_contract import validate_goal_bundle
        validate_goal_bundle(bundle)
        template = validate_mechanism_template(hidden["mechanism_template"])["normalized"]
        if template["template_id"] != template_id or hidden["measurement_status"] != "classified":
            raise ValueError("mechanism suite template binding or measurement status is stale")
        if row["scene_key"] != template["scene_key"] or row["mechanism_family"] != template["mechanism_family"]:
            raise ValueError("mechanism suite record disagrees with its template")
    inventory = report["artifact_sha256"]
    if not isinstance(inventory, dict) or set(inventory) != expected_files:
        raise ValueError("mechanism suite artifact inventory is incomplete")
    for relative in expected_files:
        if file_sha256(root / relative) != inventory[relative]:
            raise ValueError("mechanism suite artifact hash mismatch")
    return {"validated": True, "canonical_template_count": len(records),
            "execution_validated": False, "scoreable": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list task-generation registrations, including blocked city preparation")
    audit = commands.add_parser("audit", help="generate and verify a fresh author-only matrix")
    audit.add_argument("--output-root", required=True, type=Path)
    audit.add_argument("--scene", action="append", choices=[row["scene_key"] for row in registered_goal_scenes()])
    audit.add_argument("--city-package", type=Path)
    audit.add_argument("--verify-sources", action="store_true")
    verify = commands.add_parser("verify", help="re-read an existing suite and reject stale bytes or semantics")
    verify.add_argument("--output-root", required=True, type=Path)
    verify.add_argument("--verify-sources", action="store_true")
    mechanism_audit = commands.add_parser(
        "mechanism-audit", help="generate and verify the admitted-scene M0 mechanism matrix")
    mechanism_audit.add_argument("--output-root", required=True, type=Path)
    mechanism_verify = commands.add_parser(
        "mechanism-verify", help="re-read an M0 mechanism matrix and reject stale bytes")
    mechanism_verify.add_argument("--output-root", required=True, type=Path)
    full_audit = commands.add_parser(
        "full-audit", help="generate and verify the five-scene 405-package full matrix")
    full_audit.add_argument("--output-root", required=True, type=Path)
    full_verify = commands.add_parser(
        "full-verify", help="re-read the full matrix and reject missing, duplicate or stale artifacts")
    full_verify.add_argument("--output-root", required=True, type=Path)
    full_verify.add_argument("--verify-sources", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "list":
        result = {"scope": "task_generation_not_simulator_activation", "scenes": registered_goal_scenes()}
    elif args.command == "audit":
        result = write_goal_suite(args.output_root, scene_keys=args.scene, city_package_dir=args.city_package,
                                  verify_sources=args.verify_sources)
    elif args.command == "verify":
        result = verify_goal_suite(args.output_root, verify_sources=args.verify_sources)
    elif args.command == "mechanism-audit":
        result = write_goal_mechanism_suite(args.output_root)
    elif args.command == "mechanism-verify":
        result = verify_goal_mechanism_suite(args.output_root)
    else:
        from .goal_full_matrix import verify_goal_full_matrix, write_goal_full_matrix
        result = (write_goal_full_matrix(args.output_root) if args.command == "full-audit"
                  else verify_goal_full_matrix(
                      args.output_root, verify_sources=args.verify_sources))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
