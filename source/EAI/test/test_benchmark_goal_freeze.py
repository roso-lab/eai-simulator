"""Immutable author freezes, leakage groups and independent local-byte checks."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from demo.benchmark.goal_driven_contract import build_goal_heterogeneity_certificate
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_freeze import (
    build_goal_freeze_manifest, validate_goal_freeze_manifest,
    verify_goal_freeze_files, write_goal_freeze_manifest,
)
from demo.benchmark.provenance import REPO_ROOT, canonical_sha256


@pytest.fixture(scope="module")
def bundles():
    return {scene: build_goal_task_package(scene)
            for scene in ("factory", "warehouse_nvd", "modern_house_nvd")}


def _sources(packages):
    return {bundle["public_task"]["mission"]["scene_key"]: {
        "revision": "local-authoring-candidate-v1",
        "files": {f"tmp/declared-freeze-{bundle['public_task']['mission']['scene_key']}.usd": "a" * 64},
    } for bundle in packages.values()}


def _build(packages, assignments=None, sources=None):
    return build_goal_freeze_manifest(
        packages, split_assignments=assignments or {key: "reserved" for key in packages},
        source_bindings=_sources(packages) if sources is None else sources)


def _variant(bundle, *, new_id=False, new_layout=False, prose=False, payload=False):
    bundle = deepcopy(bundle)
    public = bundle["public_task"]
    if new_id:
        public["task_id"] += "-variant"
    if new_layout:
        public["public_world"]["routes"][0]["length_m"] += 0.5
    if prose:
        public["public_world"]["public_notes"] += " Reworded author note."
    if payload:
        public["candidate_robot_pool"][1]["physical_profile"]["effective_payload_kg"] += 0.5
    public_hash = canonical_sha256(public)
    for name in ("participant_api", "hidden_evaluator", "baseline_manifest"):
        bundle[name]["task_id"] = public["task_id"]
        bundle[name]["public_task_sha256"] = public_hash
    bundle["hidden_evaluator"]["heterogeneity_certificate"] = build_goal_heterogeneity_certificate(
        public, bundle["hidden_evaluator"]["witness_plans"])
    return bundle


def _reseal(manifest):
    manifest["sha256"] = canonical_sha256({key: value for key, value in manifest.items() if key != "sha256"})


def test_three_single_layout_scenes_are_reserved_candidates_not_heldout(bundles):
    manifest = _build(bundles)
    assert validate_goal_freeze_manifest(manifest, bundles)["validated"]
    assert manifest["visibility"] == "author_only"
    assert manifest["scope"] == "authoring_candidate"
    assert manifest["held_out_validated"] is False
    assert manifest["source_binding_status"] == "declared_not_file_verified"
    for scene in bundles:
        report = manifest["per_scene"][scene]
        assert report["independent_layout_count"] == report["leakage_group_count"] == 1
        assert report["sufficient_independent_groups"] is False
        assert report["holdout_status"] == "reserved_candidate"
        assert report["held_out_validated"] is False
    for key, bundle in bundles.items():
        entry = manifest["packages"][key]
        assert entry["bundle_sha256"] == canonical_sha256(bundle)
        assert set(entry["layer_sha256"]) == set(bundle)
        assert all(entry["layer_sha256"][layer] == canonical_sha256(content)
                   for layer, content in bundle.items())


@pytest.mark.parametrize("assignments", [{}, {"x": "reserved"}, {"factory": "random"},
                                          {"factory": None}, {"factory": "reserved", "extra": "test"}])
def test_split_assignments_are_explicit_and_complete(bundles, assignments):
    packages = {"factory": bundles["factory"]}
    with pytest.raises(ValueError, match="split"):
        build_goal_freeze_manifest(packages, split_assignments=assignments, source_bindings=_sources(packages))


def test_factor_variants_must_share_split_and_count_as_one_layout(bundles):
    packages = {"immediate": bundles["warehouse_nvd"],
                "delayed": build_goal_task_package("warehouse_nvd", factor_level="delayed")}
    with pytest.raises(ValueError, match="leakage group"):
        _build(packages, {"immediate": "development", "delayed": "reserved"})
    manifest = _build(packages)
    assert len(manifest["leakage_groups"]) == 1
    assert manifest["per_scene"]["warehouse_nvd"]["independent_layout_count"] == 1


@pytest.mark.parametrize("changes", [dict(new_id=True), dict(new_id=True, prose=True),
                                      dict(new_id=True, payload=True), dict(new_layout=True)])
def test_same_layout_or_same_task_cannot_be_relabelled_across_splits(bundles, changes):
    original = bundles["warehouse_nvd"]
    packages = {"a": original, "b": _variant(original, **changes)}
    with pytest.raises(ValueError, match="leakage group"):
        _build(packages, {"a": "development", "b": "reserved"})


def test_grouping_uses_transitive_closure_of_world_and_task_identity(bundles):
    original = bundles["warehouse_nvd"]
    renamed = _variant(original, new_id=True)
    packages = {"a": original, "b": renamed, "c": _variant(renamed, new_layout=True)}
    manifest = _build(packages)
    assert manifest["leakage_groups"][0]["package_ids"] == ["a", "b", "c"]
    report = manifest["per_scene"]["warehouse_nvd"]
    assert report["independent_layout_count"] == 2
    assert report["leakage_group_count"] == 1
    assert report["sufficient_independent_groups"] is False
    with pytest.raises(ValueError, match="leakage group"):
        _build(packages, {"a": "development", "b": "development", "c": "reserved"})


def test_reordering_or_equivalent_numeric_encoding_does_not_create_new_layout(bundles):
    original = bundles["warehouse_nvd"]
    edited = deepcopy(original)
    world = edited["public_task"]["public_world"]
    for rows in (world["routes"], world["regions"], world["objects"]):
        rows.reverse()
    for route in world["routes"]:
        if route["length_m"] == int(route["length_m"]):
            route["length_m"] = int(route["length_m"])
    renamed = _variant(edited, new_id=True)
    manifest = _build({"a": original, "b": renamed})
    assert manifest["packages"]["a"]["public_world_sha256"] != manifest["packages"]["b"]["public_world_sha256"]
    assert manifest["per_scene"]["warehouse_nvd"]["independent_layout_count"] == 1
    with pytest.raises(ValueError, match="leakage group"):
        _build({"a": original, "b": renamed}, {"a": "development", "b": "reserved"})


def test_single_layout_cannot_be_promoted_to_test(bundles):
    with pytest.raises(ValueError, match="use reserved"):
        _build(bundles, {key: "test" for key in bundles})


def test_city_full_bundle_freezes_as_a_reserved_candidate():
    packages = {"city": build_goal_task_package("city")}
    manifest = _build(packages)
    assert validate_goal_freeze_manifest(manifest, packages)["validated"]
    report = manifest["per_scene"]["city"]
    assert report["independent_layout_count"] == report["leakage_group_count"] == 1
    assert report["sufficient_independent_groups"] is False
    assert report["holdout_status"] == "reserved_candidate"
    assert manifest["held_out_validated"] is False
    with pytest.raises(ValueError, match="use reserved"):
        _build(packages, {key: "test" for key in packages})


def test_distinct_declared_groups_still_do_not_validate_heldout_generalization(bundles):
    original = bundles["warehouse_nvd"]
    packages = {"dev": original, "test": _variant(original, new_id=True, new_layout=True)}
    manifest = _build(packages, {"dev": "development", "test": "test"})
    report = manifest["per_scene"]["warehouse_nvd"]
    assert report["independent_layout_count"] == report["leakage_group_count"] == 2
    assert report["sufficient_independent_groups"] is True
    assert report["holdout_status"] == "test_candidate_unvalidated"
    assert report["held_out_validated"] is False
    assert manifest["external_evidence_status"] == "not_supplied"


def test_build_and_validate_do_not_read_source_bytes(bundles, monkeypatch):
    packages = {"factory": bundles["factory"]}
    def forbidden(*args, **kwargs):
        raise AssertionError("declaration validation must not inspect the filesystem")
    monkeypatch.setattr(os, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    manifest = _build(packages)
    report = validate_goal_freeze_manifest(manifest, packages)
    assert report["file_contents_verified"] is False
    assert manifest["provider_revision_verified"] is False


def test_valid_hidden_schedule_change_breaks_full_bundle_freeze(bundles):
    packages = {"warehouse": deepcopy(bundles["warehouse_nvd"])}
    manifest = _build(packages)
    packages["warehouse"]["hidden_evaluator"]["hidden_event_schedule"]["communication_events"][0]["id"] += "_new"
    with pytest.raises(ValueError, match="frozen content"):
        validate_goal_freeze_manifest(manifest, packages)


@pytest.mark.parametrize("layer", ["public_task", "baseline_manifest"])
def test_other_valid_bundle_edits_also_break_freeze(bundles, layer):
    packages = {"warehouse": deepcopy(bundles["warehouse_nvd"])}
    manifest = _build(packages)
    if layer == "public_task":
        packages["warehouse"] = _variant(packages["warehouse"], prose=True)
    else:
        packages["warehouse"][layer]["limitations"].append("Additional author limitation.")
    with pytest.raises(ValueError, match="frozen content"):
        validate_goal_freeze_manifest(manifest, packages)


def test_invalid_bundle_rejected_even_if_freeze_would_hash_it(bundles):
    packages = {"warehouse": deepcopy(bundles["warehouse_nvd"])}
    packages["warehouse"]["participant_api"]["plan_contract"]["witness_plans"] = []
    with pytest.raises(ValueError):
        _build(packages)


def test_external_source_declaration_must_match_frozen_revision_and_hashes(bundles):
    packages = {"factory": bundles["factory"]}
    sources = _sources(packages)
    manifest = _build(packages, sources=sources)
    assert validate_goal_freeze_manifest(manifest, packages, source_bindings=sources)["validated"]
    sources["factory"]["revision"] = "different-revision"
    with pytest.raises(ValueError, match="source_bindings"):
        validate_goal_freeze_manifest(manifest, packages, source_bindings=sources)


@pytest.mark.parametrize("digest", ["", "0" * 63, "g" * 64, "sha256:" + "0" * 64, 1, None])
def test_illegal_source_hashes_are_rejected(bundles, digest):
    packages = {"factory": bundles["factory"]}
    source = {"factory": {"revision": "declared", "files": {"tmp/x.usd": digest}}}
    with pytest.raises(ValueError, match="SHA-256"):
        _build(packages, sources=source)


def test_source_paths_are_stable_and_uppercase_hashes_normalized(bundles):
    packages = {"factory": bundles["factory"]}
    source = {"factory": {"revision": "main-is-only-a-declaration", "files": {"tmp/x.usd": "A" * 64}}}
    manifest = _build(packages, sources=source)
    assert manifest["source_bindings"]["factory"]["files"] == {str(REPO_ROOT / "tmp/x.usd"): "a" * 64}
    assert manifest["provider_revision_verified"] is False


@pytest.mark.parametrize("source", [
    {}, {"factory": {"revision": "", "files": {"tmp/a": "a" * 64}}},
    {"factory": {"revision": "v1", "files": {}}},
    {"factory": {"revision": "v1", "files": {"tmp/a": "a" * 64}, "measured": True}},
    {"factory": {"revision": "v1", "files": {"tmp/a": "a" * 64, "tmp/x/../a": "a" * 64}}},
])
def test_incomplete_ambiguous_or_overclaiming_sources_fail(bundles, source):
    with pytest.raises(ValueError):
        _build({"factory": bundles["factory"]}, sources=source)


def test_same_source_path_cannot_have_conflicting_cross_scene_hashes(bundles):
    sources = _sources(bundles)
    for index, binding in enumerate(sources.values()):
        binding["files"] = {"tmp/shared.usd": str(index) * 64}
    with pytest.raises(ValueError, match="conflicting hashes"):
        _build(bundles, sources=sources)


@pytest.mark.parametrize("field", ["held_out_validated", "file_contents_verified", "provider_revision_verified"])
def test_resealed_false_claims_do_not_pass_manifest_validation(bundles, field):
    manifest = _build(bundles)
    manifest[field] = True
    _reseal(manifest)
    with pytest.raises(ValueError, match="integrity|metadata"):
        validate_goal_freeze_manifest(manifest, bundles)


def test_resealed_counts_or_package_hashes_cannot_replace_original_content(bundles):
    manifest = _build(bundles)
    forged = deepcopy(manifest)
    forged["per_scene"]["factory"]["independent_layout_count"] = 100
    _reseal(forged)
    with pytest.raises(ValueError, match="metadata"):
        validate_goal_freeze_manifest(forged, bundles)
    forged = deepcopy(manifest)
    forged["packages"]["factory"]["bundle_sha256"] = "0" * 64
    _reseal(forged)
    with pytest.raises(ValueError, match="frozen content"):
        validate_goal_freeze_manifest(forged, bundles)


def test_scope_cannot_be_promoted_to_measured_or_scoreable(bundles):
    with pytest.raises(ValueError, match="authoring_candidate"):
        build_goal_freeze_manifest(bundles, split_assignments={key: "reserved" for key in bundles},
                                   source_bindings=_sources(bundles), scope="measured_release")


def test_verify_source_files_is_separate_read_only_and_detects_drift(bundles, tmp_path):
    path = tmp_path / "scene.usd"
    path.write_bytes(b"declared source content")
    source = {"factory": {"revision": "declared-not-authenticated",
                           "files": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}}}
    manifest = _build({"factory": bundles["factory"]}, sources=source)
    original = deepcopy(manifest)
    verified = verify_goal_freeze_files(manifest)
    assert verified["validated"] and verified["file_contents_verified"]
    assert verified["files"][0]["status"] == "matched"
    assert verified["provider_revision_verified"] is False
    assert verified["scene_physical_binding_validated"] is False
    assert verified["held_out_validated"] is False
    assert verified["bundle_contents_validated"] is False
    path.write_bytes(b"changed source content")
    assert verify_goal_freeze_files(manifest)["files"][0]["status"] == "hash_mismatch"
    assert manifest == original


def test_verify_missing_and_nonregular_files_fails_without_waiting(bundles, tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    sources = {"factory": {"revision": "v1", "files": {
        str(tmp_path / "missing"): "a" * 64, str(directory): "a" * 64, str(fifo): "a" * 64,
    }}}
    report = verify_goal_freeze_files(_build({"factory": bundles["factory"]}, sources=sources))
    assert report["validated"] is False
    assert {row["status"] for row in report["files"]} == {"missing", "not_regular_file"}


def test_write_readback_is_private_and_refuses_existing_version(bundles, tmp_path):
    manifest = _build(bundles)
    path = tmp_path / "freeze-v1.json"
    assert write_goal_freeze_manifest(manifest, path) == path
    assert validate_goal_freeze_manifest(json.loads(path.read_text()), bundles)["validated"]
    assert path.stat().st_mode & 0o077 == 0
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_goal_freeze_manifest(manifest, path)
    assert path.read_bytes() == original
    assert sorted(child.name for child in tmp_path.iterdir()) == ["freeze-v1.json"]


def test_write_refuses_even_dangling_destination_symlink(bundles, tmp_path):
    path = tmp_path / "freeze.json"
    target = tmp_path / "missing.json"
    path.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_goal_freeze_manifest(_build(bundles), path)
    assert path.is_symlink() and not target.exists()
    assert sorted(child.name for child in tmp_path.iterdir()) == ["freeze.json"]


def test_concurrent_writers_cannot_replace_each_other(bundles, tmp_path):
    path = tmp_path / "freeze.json"
    manifest = _build(bundles)
    def write():
        try:
            write_goal_freeze_manifest(manifest, path)
            return "created"
        except FileExistsError:
            return "exists"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write) for _ in range(2)]
        assert sorted(future.result() for future in futures) == ["created", "exists"]
    assert json.loads(path.read_text()) == manifest
    assert list(tmp_path.iterdir()) == [path]


def test_manifest_is_deterministic_and_inputs_are_not_mutated(bundles):
    original = deepcopy(bundles)
    sources = _sources(bundles)
    before_sources = deepcopy(sources)
    first = _build(bundles, sources=sources)
    second = _build(dict(reversed(list(bundles.items()))), sources=sources)
    assert first == second
    assert bundles == original and sources == before_sources
    json.dumps(first, allow_nan=False)
