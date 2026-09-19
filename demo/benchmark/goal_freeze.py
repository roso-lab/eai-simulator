"""Author-only, immutable candidate freezes for goal-driven task bundles.

Splits are explicit author inputs, never random assignments. Same-scene world
or spatial-layout copies, and instances sharing a task ID, form transitive
leakage groups. Factor labels and package IDs cannot split a group. The spatial
projection excludes prose, load parameters and control/resource policies;
its distinct signatures count declared layouts, not measured USD layouts.
Semantic equivalence after object/region renaming still needs external review.

``source_bindings`` freezes declarations only. Relative file paths are anchored
to the repository root, then stored as absolute paths without reading files or
resolving symlinks. ``verify_goal_freeze_files`` separately reads current bytes;
even a match does not authenticate a provider revision, establish USD geometry,
prove prior non-exposure or validate held-out generalization.

The current single-layout scenes may be development or reserved candidates.
A test designation needs separate development and test groups and at least two
declared layouts in its scene. It remains an unvalidated candidate: no function
here accepts external execution/exposure evidence or sets held_out_validated.

Build/validate bind the full public, participant, baseline and hidden bundle,
including schedules and witnesses. Write each version to a NEW path::

    manifest = build_goal_freeze_manifest(
        packages, split_assignments=splits, source_bindings=bindings)
    validate_goal_freeze_manifest(manifest, packages)
    write_goal_freeze_manifest(manifest, Path("private/freeze-v1.json"))
    file_report = verify_goal_freeze_files(manifest)

The writer uses a private temporary file plus an atomic exclusive hard link;
it never replaces an existing destination (including a symlink). Changed
contents or assignments require a new version/path. It checks manifest
integrity; use validate_goal_freeze_manifest to recheck live bundles first.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from .goal_driven_contract import validate_goal_bundle
from .provenance import REPO_ROOT, canonical_sha256


GOAL_FREEZE_SCHEMA = "eai.benchmark.goal_freeze_manifest"
_SPLITS = {"development", "test", "reserved"}
_LAYERS = {"public_task", "participant_api", "hidden_evaluator", "baseline_manifest"}
_ENTRY_FIELDS = {"task_id", "scene_key", "split", "public_world_sha256",
                 "layout_sha256", "bundle_sha256", "layer_sha256"}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a nonempty string without NUL")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value.lower()


def _sources(bindings: Any, scenes: set[str]) -> dict[str, Any]:
    bindings = _mapping(bindings, "source_bindings")
    if set(bindings) != scenes:
        raise ValueError("source_bindings must cover exactly the package scenes")
    normalized: dict[str, Any] = {}
    file_claims: dict[str, str] = {}
    for scene in sorted(scenes):
        binding = _mapping(bindings[scene], f"source binding {scene}")
        if set(binding) != {"revision", "files"}:
            raise ValueError("source binding requires exactly revision and files")
        revision = _name(binding["revision"], "declared revision")
        files = _mapping(binding["files"], "source files")
        if not files:
            raise ValueError("source files must be nonempty")
        converted = {}
        for raw_path, raw_hash in files.items():
            path = Path(_name(raw_path, "source file path"))
            # Lexical normalization only: build/validate do not inspect bytes.
            absolute = os.path.abspath(path if path.is_absolute() else REPO_ROOT / path)
            if absolute in converted:
                raise ValueError(f"source files contain duplicate normalized path: {absolute}")
            digest = _sha(raw_hash, "source file hash")
            if absolute in file_claims and file_claims[absolute] != digest:
                raise ValueError(f"source files have conflicting hashes: {absolute}")
            converted[absolute] = file_claims[absolute] = digest
        normalized[scene] = {"revision": revision, "files": dict(sorted(converted.items()))}
    return normalized


def _layout_sha(world: Mapping[str, Any]) -> str:
    """Conservative spatial identity, independent of descriptions and factors."""
    def projection(row: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        result = {key: row[key] for key in keys}
        for key in ("length_m", "max_step_height_m"):
            if key in result:
                result[key] = float(result[key])
        if "clearance_m" in result:
            result["clearance_m"] = [float(value) for value in result["clearance_m"]]
        return result

    fields = {
        "regions": ("id",),
        "routes": ("id", "from_region_id", "to_region_id", "length_m", "clearance_m", "max_step_height_m"),
        "vertical_links": ("id", "from_region_id", "to_region_id"),
    }
    layout = {
        group: sorted((projection(row, keys) for row in world[group]),
                      key=canonical_sha256)
        for group, keys in fields.items()
    }
    layout["objects"] = sorted(({
        "id": row["id"],
        "size_m": [float(value) for value in row["physical_properties"]["size_m"]],
        "initial_region_id": row["physical_properties"]["initial_region_id"],
    } for row in world["objects"]), key=canonical_sha256)
    return canonical_sha256(layout)


def _entries(packages: Any, assignments: Any) -> dict[str, Any]:
    packages = _mapping(packages, "packages")
    assignments = _mapping(assignments, "split_assignments")
    if not packages or set(assignments) != set(packages):
        raise ValueError("explicit split_assignments must cover exactly the nonempty packages")
    for package_id in packages:
        _name(package_id, "package_id")
    entries = {}
    for package_id in sorted(packages):
        split = assignments[package_id]
        if not isinstance(split, str) or split not in _SPLITS:
            raise ValueError("split must be development, test or reserved")
        bundle = packages[package_id]
        validate_goal_bundle(bundle)
        public = bundle["public_task"]
        entries[package_id] = {
            "task_id": public["task_id"], "scene_key": public["mission"]["scene_key"],
            "split": split, "public_world_sha256": canonical_sha256(public["public_world"]),
            "layout_sha256": _layout_sha(public["public_world"]),
            "bundle_sha256": canonical_sha256(bundle),
            "layer_sha256": {key: canonical_sha256(bundle[key]) for key in sorted(_LAYERS)},
        }
    return entries


def _groups(entries: Mapping[str, Any]) -> list[dict[str, Any]]:
    parents = {key: key for key in entries}

    def root(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    seen: dict[tuple[str, ...], str] = {}
    for package_id, entry in entries.items():
        identities = [
            ("task_id", entry["task_id"]),
            ("world", entry["scene_key"], entry["public_world_sha256"]),
            ("layout", entry["scene_key"], entry["layout_sha256"]),
        ]
        for identity in identities:
            if identity in seen:
                parents[root(package_id)] = root(seen[identity])
            else:
                seen[identity] = package_id
    members: dict[str, list[str]] = {}
    for package_id in entries:
        members.setdefault(root(package_id), []).append(package_id)
    result = []
    for package_ids in sorted(sorted(value) for value in members.values()):
        splits = {entries[key]["split"] for key in package_ids}
        if len(splits) != 1:
            raise ValueError(f"leakage group must stay in one split: {package_ids}")
        result.append({
            "group_id": canonical_sha256(package_ids), "package_ids": package_ids,
            "scene_keys": sorted({entries[key]["scene_key"] for key in package_ids}),
            "split": next(iter(splits)),
        })
    return result


def _assemble(entries: dict[str, Any], sources: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope != "authoring_candidate":
        raise ValueError("freeze scope must remain authoring_candidate")
    groups = _groups(entries)
    per_scene = {}
    for scene in sorted(sources):
        selected = {key: row for key, row in entries.items() if row["scene_key"] == scene}
        scene_groups = [group for group in groups if scene in group["scene_keys"]]
        layout_count = len({row["layout_sha256"] for row in selected.values()})
        enough = layout_count >= 2 and len(scene_groups) >= 2
        splits = {row["split"] for row in selected.values()}
        if "test" in splits and (not enough or "development" not in splits):
            raise ValueError(f"scene {scene}: test requires independent layout groups and a development group; use reserved")
        per_scene[scene] = {
            "package_count": len(selected),
            "independent_layout_count": layout_count,
            "layout_count_basis": "declared_spatial_projection_not_measured_geometry",
            "leakage_group_count": len(scene_groups),
            "split_group_counts": {split: sum(group["split"] == split for group in scene_groups)
                                   for split in sorted(_SPLITS)},
            "sufficient_independent_groups": enough,
            "holdout_status": ("test_candidate_unvalidated" if "test" in splits else
                               "reserved_candidate" if "reserved" in splits else "development_only"),
            "held_out_validated": False,
        }
    result = {
        "schema": GOAL_FREEZE_SCHEMA, "version": 1, "visibility": "author_only", "scope": scope,
        "assignment_policy": "explicit_caller_assignment_no_secrecy_claim",
        "freeze_policy": "no_overwrite_new_content_requires_new_path",
        "packages": entries, "source_bindings": sources, "leakage_groups": groups,
        "per_scene": per_scene,
        "source_binding_status": "declared_not_file_verified",
        "file_contents_verified": False,
        "provider_revision_verified": False,
        "scene_physical_binding_validated": False,
        "held_out_validated": False,
        "external_evidence_status": "not_supplied",
        "limitations": [
            "File digests and revision strings are declarations until independently checked.",
            "Matching local bytes does not authenticate a provider revision or measured USD geometry.",
            "Grouping uses declared identities and spatial structure; semantic renaming needs external leakage review.",
            "Reserved and test assignments do not establish non-exposure of templates or independent held-out generalization.",
            "Sufficient declared groups do not replace external independence, exposure and execution evidence.",
            "This manifest is author-only and is not a participant release or scoring authorization.",
        ],
    }
    return {**result, "sha256": canonical_sha256(result)}


def build_goal_freeze_manifest(
        packages: Mapping[str, Mapping[str, Any]], *, split_assignments: Mapping[str, str],
        source_bindings: Mapping[str, Mapping[str, Any]], scope: str = "authoring_candidate") -> dict[str, Any]:
    """Validate and freeze complete bundles without reading source files."""
    entries = _entries(packages, split_assignments)
    sources = _sources(source_bindings, {row["scene_key"] for row in entries.values()})
    return _assemble(entries, sources, scope)


def _validate_stored(manifest: Any) -> Mapping[str, Any]:
    manifest = _mapping(manifest, "freeze manifest")
    entries = _mapping(manifest.get("packages"), "freeze packages")
    if not entries:
        raise ValueError("freeze packages must be nonempty")
    for key, raw in entries.items():
        _name(key, "package_id")
        row = _mapping(raw, "freeze entry")
        if set(row) != _ENTRY_FIELDS:
            raise ValueError("freeze entry fields mismatch")
        for name in ("task_id", "scene_key"):
            _name(row[name], name)
        if not isinstance(row["split"], str) or row["split"] not in _SPLITS:
            raise ValueError("invalid freeze split")
        for name in ("public_world_sha256", "layout_sha256", "bundle_sha256"):
            _sha(row[name], name)
        layers = _mapping(row["layer_sha256"], "layer hashes")
        if set(layers) != _LAYERS:
            raise ValueError("freeze must bind all four bundle layers")
        for digest in layers.values():
            _sha(digest, "layer hash")
    sources = _sources(manifest.get("source_bindings"), {row["scene_key"] for row in entries.values()})
    expected = _assemble(dict(sorted(entries.items())), sources, manifest.get("scope"))
    _sha(manifest.get("sha256"), "manifest hash")
    # Canonical comparison also rejects booleans substituted for version integers.
    if canonical_sha256(manifest) != canonical_sha256(expected):
        raise ValueError("freeze manifest integrity or derived metadata mismatch")
    return manifest


def validate_goal_freeze_manifest(
        manifest: Mapping[str, Any], packages: Mapping[str, Mapping[str, Any]], *,
        source_bindings: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Revalidate live bundles and every frozen field; source bytes stay unread."""
    _validate_stored(manifest)
    expected = build_goal_freeze_manifest(
        packages, split_assignments={key: row["split"] for key, row in manifest["packages"].items()},
        source_bindings=manifest["source_bindings"] if source_bindings is None else source_bindings,
        scope=manifest["scope"])
    if canonical_sha256(manifest) != canonical_sha256(expected):
        raise ValueError("packages or declared source_bindings differ from the frozen content")
    return {"validated": True, "manifest_sha256": manifest["sha256"],
            "package_count": len(packages), "bundle_contents_validated": True,
            "file_contents_verified": False, "held_out_validated": False}


def write_goal_freeze_manifest(manifest: Mapping[str, Any], path: Path) -> Path:
    """Atomically create a private JSON version; an existing path always fails."""
    _validate_stored(manifest)
    data = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")
    destination = Path(path).absolute()
    # Requiring an existing parent avoids unrelated directory creation.
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        os.unlink(temporary)
    return destination


def verify_goal_freeze_files(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Read declared local files; return a separate report without changing the freeze.

    Missing, non-regular, unreadable, changing and mismatched files fail the
    report. All declared paths are checked. This verifies local bytes only;
    frozen bundle contents require validate_goal_freeze_manifest(packages).
    """
    _validate_stored(manifest)
    claims = {path: digest for binding in manifest["source_bindings"].values()
              for path, digest in binding["files"].items()}
    results = []
    for path, expected in sorted(claims.items()):
        actual = None
        try:
            # O_NONBLOCK prevents a swapped FIFO/device from hanging a read-only audit.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode):
                    status = "not_regular_file"
                else:
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    after = os.fstat(fd)
                    current = os.stat(path)
                    def signature(st):
                        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                    if signature(before) != signature(after) or signature(after) != signature(current):
                        status = "changed_during_read"
                    else:
                        status = "matched" if actual == expected else "hash_mismatch"
            finally:
                os.close(fd)
        except FileNotFoundError:
            status = "missing"
        except OSError:
            status = "unreadable"
        results.append({"path": path, "expected_sha256": expected, "actual_sha256": actual, "status": status})
    valid = bool(results) and all(row["status"] == "matched" for row in results)
    return {"schema": "eai.benchmark.goal_freeze_file_verification", "version": 1,
            "visibility": "author_only", "manifest_sha256": manifest["sha256"],
            "validated": valid, "file_contents_verified": valid, "files": results,
            "bundle_contents_validated": False, "provider_revision_verified": False,
            "scene_physical_binding_validated": False, "held_out_validated": False}


__all__ = ["GOAL_FREEZE_SCHEMA", "build_goal_freeze_manifest", "validate_goal_freeze_manifest",
           "write_goal_freeze_manifest", "verify_goal_freeze_files"]
