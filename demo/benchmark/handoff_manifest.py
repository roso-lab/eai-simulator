"""Read-only handoff status for benchmark task generation.

This report is the boundary handed to the developer who integrates external
multi-robot systems.  It deliberately separates authoring/API readiness from
Isaac execution and scoreability: all admitted scenes must have the same contract
shape before handoff, while scene-specific live blockers remain explicit.  The
default handoff covers five scenes; the named four-scene functions remain as
compatibility wrappers for archived version-2 handoffs.
"""

from __future__ import annotations

from copy import deepcopy
import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .algorithm_adapter import DEFAULT_CONTROL_STACK
from .goal_driven_contract import validate_goal_bundle, write_goal_task_bundle
from .goal_driven_tasks import build_goal_task_package, review_goal_task_design
from .goal_driven_fixtures import execution_contract
from .provenance import canonical_sha256


SCENE_KEYS = ("factory", "warehouse_nvd", "modern_house_nvd", "city")
FIVE_SCENE_KEYS = (*SCENE_KEYS, "restaurant")
SCHEMA = "eai.benchmark.four_scene_handoff"
HANDOFF_SCHEMA = "eai.benchmark.goal_scene_handoff"

_DIAGNOSTIC_BOUNDARIES = {
    "factory": "ParticipantSession + factory main(participant_adapter=...)",
    "warehouse_nvd": "ParticipantSession + warehouse main(participant_adapter=...)",
    "modern_house_nvd": "ParticipantSession + modern_house main(participant_adapter=...)",
    "city": "City service probes; no fixed business DAG",
    "restaurant": "GoalSession + restaurant route/readback adapter; treatment effects pending",
}

_BUNDLE_PATHS = {
    "public_task": "release/public_task.json",
    "participant_api": "release/participant_api.json",
    "baseline_manifest": "release/baseline_manifest.json",
    "hidden_evaluator": "evaluator/hidden_evaluator.json",
}


def _scene_handoff(scene_key: str, bundle: Mapping[str, Any]) -> dict[str, Any]:
    if scene_key not in FIVE_SCENE_KEYS:
        raise ValueError(f"unsupported handoff scene: {scene_key!r}")
    validate_goal_bundle(bundle)
    if bundle["public_task"]["mission"]["scene_key"] != scene_key:
        raise ValueError("handoff bundle belongs to a different scene")
    if bundle["public_task"]["execution_contract"] != execution_contract():
        raise ValueError("handoff execution contract differs from fixed reliable-skill defaults")
    review = review_goal_task_design(
        bundle["public_task"], bundle["hidden_evaluator"]["witness_plans"])
    if review.get("validated") is not True or review.get("authoring_status") != "passed":
        raise ValueError(f"{scene_key}: goal task authoring review did not pass")
    hashes = {name: canonical_sha256(bundle[name]) for name in (
        "public_task", "participant_api", "baseline_manifest", "hidden_evaluator")}
    task = bundle["public_task"]
    gate = bundle["hidden_evaluator"]["release_gate"]
    if gate["execution_status"] != "external_pending" or gate["scoreable"] is not False:
        raise ValueError("authoring handoff cannot claim execution or scoreability")
    return {
        "scene_key": scene_key,
        "task_id": task["task_id"],
        "business_family": task["mission"]["goal"],
        "package_shape": ["public_task", "participant_api", "baseline_manifest", "hidden_evaluator"],
        "artifact_sha256": hashes,
        "task_generation": "passed",
        "author_review": "passed",
        "participant_api": "passed",
        "hidden_boundary": "passed",
        "control_stack": deepcopy(DEFAULT_CONTROL_STACK),
        "control_stack_status": "required_policy_not_runtime_attestation",
        "execution_contract_sha256": canonical_sha256(task["execution_contract"]),
        "runtime_boundary": "demo.benchmark.goal_session.GoalSession",
        "diagnostic_boundary": _DIAGNOSTIC_BOUNDARIES[scene_key],
        "execution_status": gate["execution_status"],
        "scoreable": gate["scoreable"],
        "handoff_ready": True,
        "execution_blockers": list(gate["blockers"]),
    }


def _bundles(bundles=None):
    if bundles is None:
        return {key: build_goal_task_package(key) for key in SCENE_KEYS}
    if not isinstance(bundles, Mapping) or set(bundles) != set(SCENE_KEYS):
        raise ValueError("handoff requires exactly four scene bundles")
    return bundles


def build_four_scene_handoff(bundles=None) -> dict[str, Any]:
    """Build a deterministic handoff matrix without starting Isaac."""
    bundles = _bundles(bundles)
    scenes = [_scene_handoff(scene_key, bundles[scene_key]) for scene_key in SCENE_KEYS]
    report = {
        "schema": SCHEMA,
        "version": 2,
        "scope": "task_generation_and_review_handoff",
        "low_level_control_policy": deepcopy(DEFAULT_CONTROL_STACK),
        "scene_keys": list(SCENE_KEYS),
        "scenes": scenes,
        "all_handoff_ready": all(row["handoff_ready"] for row in scenes),
        "all_authoring_passed": all(row["author_review"] == "passed" for row in scenes),
        "all_execution_pending": all(row["execution_status"] == "external_pending" for row in scenes),
        "scoreable": False,
        "isaac_started": False,
    }
    return report


def validate_four_scene_handoff(report: Mapping[str, Any], *, bundles=None) -> dict[str, Any]:
    """Re-run bundle/API/witness audits and compare every report field.

    Callers verifying archived reports must provide the archived bundles;
    without them, this validates against the currently generated four tasks.
    Hash syntax and pre-existing pass flags alone are never sufficient.
    """
    if not isinstance(report, Mapping) or report.get("schema") != SCHEMA or report.get("version") != 2:
        raise ValueError("unsupported four-scene handoff report")
    if tuple(report.get("scene_keys", ())) != SCENE_KEYS:
        raise ValueError("handoff must contain the four canonical scenes in order")
    if report.get("low_level_control_policy") != DEFAULT_CONTROL_STACK:
        raise ValueError("handoff control policy drifted from benchmark defaults")
    scenes = report.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != len(SCENE_KEYS):
        raise ValueError("handoff must contain one row per canonical scene")
    for index, row in enumerate(scenes):
        if not isinstance(row, Mapping):
            raise ValueError("handoff scene must be an object")
        if row.get("scene_key") != SCENE_KEYS[index]:
            raise ValueError("handoff scene order or identity mismatch")
        if row.get("task_generation") != "passed" or row.get("author_review") != "passed":
            raise ValueError("all scenes must pass task generation and author review before handoff")
        if row.get("control_stack") != DEFAULT_CONTROL_STACK:
            raise ValueError("scene control stack differs from the fixed benchmark stack")
        if row.get("execution_status") != "external_pending" or row.get("scoreable") is not False:
            raise ValueError("handoff cannot claim execution or scoreability")
    if report.get("all_handoff_ready") is not True or report.get("all_authoring_passed") is not True:
        raise ValueError("handoff aggregate flags are inconsistent")
    if report.get("all_execution_pending") is not True or report.get("scoreable") is not False:
        raise ValueError("handoff aggregate execution flags are inconsistent")
    expected = build_four_scene_handoff(bundles)
    if canonical_sha256(dict(report)) != canonical_sha256(expected):
        raise ValueError("handoff report does not match re-audited bundle content")
    return deepcopy(dict(report))


def write_four_scene_handoff(output_root):
    """Export identical four-layer layouts; never overwrite existing output."""
    root = Path(output_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("handoff root cannot traverse symlinks")
    bundles = _bundles()
    report = build_four_scene_handoff(bundles)
    root.mkdir(parents=True, exist_ok=False)
    for key, bundle in bundles.items():
        write_goal_task_bundle(bundle, root / key)
    (root / "handoff.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    return verify_four_scene_handoff(root)


def verify_four_scene_handoff(output_root):
    """Read actual artifacts, recompute content hashes and author audits."""
    root = Path(output_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("handoff root cannot traverse symlinks")

    def read(relative):
        path = root
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("handoff artifacts cannot traverse symlinks")
        return json.loads(path.read_text(encoding="utf-8"))

    if root.is_symlink():
        raise ValueError("handoff root cannot be a symlink")
    expected_files = {"handoff.json"} | {
        f"{scene}/{relative}" for scene in SCENE_KEYS for relative in _BUNDLE_PATHS.values()}
    actual_files = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("handoff artifacts cannot traverse symlinks")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("handoff artifacts must be regular files or directories")
    if actual_files != expected_files:
        raise ValueError("handoff file inventory differs from the reviewed public/private boundary")
    bundles = {scene: {key: read(f"{scene}/{path}")
                       for key, path in _BUNDLE_PATHS.items()} for scene in SCENE_KEYS}
    return validate_four_scene_handoff(read("handoff.json"), bundles=bundles)


def _selected_bundles(scene_keys, bundles=None):
    keys = tuple(scene_keys)
    if (not keys or len(keys) != len(set(keys))
            or any(key not in FIVE_SCENE_KEYS for key in keys)):
        raise ValueError("handoff scene_keys must be distinct registered handoff scenes")
    if bundles is None:
        return keys, {key: build_goal_task_package(key) for key in keys}
    if not isinstance(bundles, Mapping) or set(bundles) != set(keys):
        raise ValueError("handoff bundles must cover exactly the selected scenes")
    return keys, bundles


def build_goal_handoff(bundles=None, *, scene_keys=FIVE_SCENE_KEYS) -> dict[str, Any]:
    """Build the default five-scene handoff without starting Isaac."""
    keys, selected = _selected_bundles(scene_keys, bundles)
    scenes = [_scene_handoff(key, selected[key]) for key in keys]
    report = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "scope": "task_generation_and_review_handoff",
        "low_level_control_policy": deepcopy(DEFAULT_CONTROL_STACK),
        "scene_keys": list(keys),
        "scenes": scenes,
        "all_handoff_ready": all(row["handoff_ready"] for row in scenes),
        "all_authoring_passed": all(row["author_review"] == "passed" for row in scenes),
        "all_execution_pending": all(row["execution_status"] == "external_pending" for row in scenes),
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
        "isaac_started": False,
    }
    return report


def validate_goal_handoff(
        report: Mapping[str, Any], *, bundles=None, scene_keys=FIVE_SCENE_KEYS) -> dict[str, Any]:
    """Re-audit a selected-scene handoff against actual or current bundles."""
    keys = tuple(scene_keys)
    if (not isinstance(report, Mapping) or report.get("schema") != HANDOFF_SCHEMA
            or report.get("version") != 1):
        raise ValueError("unsupported goal-scene handoff report")
    if tuple(report.get("scene_keys", ())) != keys:
        raise ValueError("handoff scene order or identity differs from the selected scenes")
    if report.get("low_level_control_policy") != DEFAULT_CONTROL_STACK:
        raise ValueError("handoff control policy drifted from benchmark defaults")
    if any(report.get(key) is not False for key in (
            "execution_validated", "business_outcomes_validated", "scoreable")):
        raise ValueError("handoff cannot claim execution, business, or scoring validation")
    _, selected = _selected_bundles(keys, bundles)
    expected = build_goal_handoff(selected, scene_keys=keys)
    if canonical_sha256(dict(report)) != canonical_sha256(expected):
        raise ValueError("handoff report does not match re-audited bundle content")
    return deepcopy(dict(report))


def write_goal_handoff(output_root, *, scene_keys=FIVE_SCENE_KEYS):
    """Export the selected handoff to a fresh, protected directory."""
    root = Path(output_root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("handoff root cannot traverse symlinks")
    keys, bundles = _selected_bundles(scene_keys)
    report = build_goal_handoff(bundles, scene_keys=keys)
    root.mkdir(parents=True, exist_ok=False)
    try:
        for key, bundle in bundles.items():
            write_goal_task_bundle(bundle, root / key)
        (root / "handoff.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8")
        return verify_goal_handoff(root, scene_keys=keys)
    except Exception:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        raise


def verify_goal_handoff(output_root, *, scene_keys=FIVE_SCENE_KEYS):
    """Read actual selected-scene artifacts and enforce the public/private boundary."""
    root = Path(output_root).absolute()
    keys = tuple(scene_keys)
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("handoff root cannot traverse symlinks")

    def read(relative):
        path = root
        for part in Path(relative).parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("handoff artifacts cannot traverse symlinks")
        return json.loads(path.read_text(encoding="utf-8"))

    expected_files = {"handoff.json"} | {
        f"{scene}/{relative}" for scene in keys for relative in _BUNDLE_PATHS.values()}
    actual_files = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("handoff artifacts cannot traverse symlinks")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError("handoff artifacts must be regular files or directories")
    if actual_files != expected_files:
        raise ValueError("handoff file inventory differs from the reviewed public/private boundary")
    bundles = {scene: {key: read(f"{scene}/{path}")
                       for key, path in _BUNDLE_PATHS.items()} for scene in keys}
    return validate_goal_handoff(read("handoff.json"), bundles=bundles, scene_keys=keys)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--output-root", help="Export a fresh five-scene authoring handoff")
    mode.add_argument("--verify", help="Read back and independently audit an exported handoff")
    parser.add_argument("--legacy-four-scene", action="store_true",
                        help="Use the archived version-2 four-scene compatibility API")
    args = parser.parse_args(argv)
    if args.legacy_four_scene:
        report = (write_four_scene_handoff(args.output_root) if args.output_root else
                  verify_four_scene_handoff(args.verify) if args.verify else build_four_scene_handoff())
    else:
        report = (write_goal_handoff(args.output_root) if args.output_root else
                  verify_goal_handoff(args.verify) if args.verify else build_goal_handoff())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


__all__ = [
    "FIVE_SCENE_KEYS", "HANDOFF_SCHEMA", "SCENE_KEYS", "SCHEMA",
    "build_four_scene_handoff", "build_goal_handoff", "main",
    "validate_four_scene_handoff", "validate_goal_handoff",
    "verify_four_scene_handoff", "verify_goal_handoff",
    "write_four_scene_handoff", "write_goal_handoff",
]


if __name__ == "__main__":
    raise SystemExit(main())
