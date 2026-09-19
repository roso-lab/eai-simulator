"""Audit the three-scene benchmark candidate release without promoting it.

The manifest is a review artifact.  It verifies the four-layer package
boundary and records the remaining deployment gates; it does not convert an
offline-valid contract into execution or physical evidence.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from demo.benchmark.challenge_contract import (
    validate_baseline_manifest,
    validate_hidden_evaluator,
    validate_participant_api,
    validate_public_task_spec,
)
from demo.benchmark.provenance import canonical_sha256


REQUIRED_LAYERS = (
    "public_task", "participant_api", "baseline_manifest", "hidden_evaluator",
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def audit_challenge_bundle(package_id: str, bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one four-layer bundle and return a non-promotional summary."""
    if not isinstance(package_id, str) or not package_id:
        raise ValueError("package_id must be a nonempty string")
    bundle = _mapping(bundle, "challenge bundle")
    if set(bundle) != set(REQUIRED_LAYERS):
        raise ValueError("challenge bundle must contain exactly the four release layers")

    public = _mapping(bundle["public_task"], "public task")
    api = _mapping(bundle["participant_api"], "participant API")
    baseline = _mapping(bundle["baseline_manifest"], "baseline manifest")
    hidden = _mapping(bundle["hidden_evaluator"], "hidden evaluator")
    validate_public_task_spec(public)
    validate_participant_api(api, task_spec=public)
    validate_baseline_manifest(baseline, task_spec=public)
    validate_hidden_evaluator(hidden, task_spec=public)

    status = public.get("release_status")
    execution_validated = public.get("execution_validated")
    gate = _mapping(public.get("release_gate"), "public release gate")
    required_gate_keys = {
        "contract_publishable", "benchmark_ready", "scoreable", "completed",
        "blockers", "missing_evidence_result",
    }
    if not required_gate_keys <= set(gate):
        raise ValueError("public release gate has incomplete fields")
    blockers = gate.get("blockers")
    if (not isinstance(blockers, list)
            or any(not isinstance(item, str) or not item for item in blockers)
            or len(blockers) != len(set(blockers))):
        raise ValueError("public release blockers must be distinct nonempty strings")
    if gate.get("contract_publishable") is not True:
        raise ValueError("candidate contract must be explicitly publishable for review")
    if status == "ready":
        if (execution_validated is not True or gate.get("benchmark_ready") is not True
                or gate.get("scoreable") is not True or blockers):
            raise ValueError("ready release requires execution, scoring and zero blockers")
    elif status == "pending":
        if (execution_validated is not False or gate.get("benchmark_ready") is not False
                or gate.get("scoreable") is not False or not blockers
                or gate.get("missing_evidence_result") != "unknown"):
            raise ValueError("pending release must preserve explicit blockers and unknown evidence")
    else:
        raise ValueError("release_status must be pending or ready")

    public_hash = canonical_sha256(public)
    for name, layer in (("participant_api", api), ("baseline_manifest", baseline),
                        ("hidden_evaluator", hidden)):
        if layer.get("release_status") != status:
            raise ValueError(f"{name} release status differs from public task")
        if layer.get("execution_validated") is not execution_validated:
            raise ValueError(f"{name} execution status differs from public task")
        if layer.get("public_task_sha256") != public_hash:
            raise ValueError(f"{name} is not hash-bound to public task")

    scene = _mapping(public.get("scene"), "public scene")
    return {
        "package_id": package_id,
        "task_id": public["task_id"],
        "scene_key": scene["scene_key"],
        "release_status": status,
        "execution_validated": execution_validated,
        "authoring_status": gate.get("authoring_status", "unknown"),
        "execution_status": gate.get("execution_status", "unknown"),
        "benchmark_ready": gate["benchmark_ready"],
        "scoreable": gate["scoreable"],
        "contract_publishable": gate["contract_publishable"],
        "blockers": deepcopy(blockers),
        "hashes": {
            name: canonical_sha256(bundle[name]) for name in REQUIRED_LAYERS
        },
    }


def build_release_manifest(packages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build the suite-level candidate manifest from already compiled bundles."""
    packages = _mapping(packages, "release packages")
    if not packages:
        raise ValueError("release manifest needs at least one package")
    entries = [audit_challenge_bundle(name, bundle)
               for name, bundle in sorted(packages.items())]
    package_ids = [entry["package_id"] for entry in entries]
    task_ids = [entry["task_id"] for entry in entries]
    if len(package_ids) != len(set(package_ids)) or len(task_ids) != len(set(task_ids)):
        raise ValueError("release package and task IDs must be unique")

    ready = all(entry["benchmark_ready"] and entry["scoreable"]
                and entry["execution_validated"] for entry in entries)
    return {
        "schema": "eai.benchmark.release_manifest",
        "version": 1,
        "release_status": "ready" if ready else "pending",
        "benchmark_ready": ready,
        "packages": entries,
        "participant_delivery": [
            "release/public_task.json",
            "release/participant_api.json",
            "release/baseline_manifest.json",
            "runtime events delivered up to the current simulation time",
        ],
        "evaluator_only": [
            "evaluator/hidden_evaluator.json",
            "future lifecycle and communication schedules",
            "private identity bindings, service truth and reference evidence",
        ],
        "stages": [
            {"id": "task_design", "status": "passed",
             "evidence": "scene-specific semantic contracts and regeneration skeletons"},
            {"id": "package_integrity", "status": "passed",
             "evidence": "four-layer validation and content-hash binding"},
            {"id": "immutable_assets", "status": "pending",
             "evidence": "provider revisions and generated navigation products require release binding"},
            {"id": "live_domain_and_factor_adapters", "status": "pending",
             "evidence": "identity/service/access collectors and Isaac lifecycle effects remain incomplete"},
            {"id": "reference_execution", "status": "pending",
             "evidence": "full physical/reference episodes, including stair support, remain incomplete"},
        ],
        "claims": {
            "contract_design_complete": True,
            "full_benchmark_deployed": ready,
            "physical_heterogeneous_capability_validated": False,
        },
    }


def audit_goal_challenge_bundle(package_id: str, bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Audit a goal-driven bundle without treating author witnesses as answers."""
    from demo.benchmark.goal_driven_contract import validate_goal_bundle

    if not isinstance(package_id, str) or not package_id:
        raise ValueError("package_id must be a nonempty string")
    validate_goal_bundle(bundle)
    public = bundle["public_task"]
    gate = bundle["hidden_evaluator"]["release_gate"]
    required = {"authoring_status", "execution_status", "benchmark_ready", "scoreable", "blockers"}
    if not required <= set(gate):
        raise ValueError("goal release gate has incomplete fields")
    blockers = gate["blockers"]
    if (not isinstance(blockers, list) or not blockers
            or any(not isinstance(item, str) or not item for item in blockers)):
        raise ValueError("goal release gate requires explicit blockers")
    if gate["authoring_status"] != "passed":
        raise ValueError("goal bundle cannot publish before authoring review")
    if gate["execution_status"] != "external_pending":
        raise ValueError("goal bundle execution status must remain explicit until external evidence arrives")
    if gate["benchmark_ready"] is not False or gate["scoreable"] is not False:
        raise ValueError("goal bundle cannot claim benchmark readiness before execution evidence")
    return {
        "package_id": package_id,
        "task_id": public["task_id"],
        "scene_key": public["mission"]["scene_key"],
        "evaluation_track": public["evaluation_track"],
        "release_status": "pending",
        "authoring_status": gate["authoring_status"],
        "execution_status": gate["execution_status"],
        "constraint_model_status": "passed",
        "parameter_basis": public["execution_contract"]["parameter_basis"],
        "scene_binding_status": public["execution_contract"]["scene_binding_status"],
        "skill_backend_conformance": "external_pending",
        "benchmark_ready": False,
        "scoreable": False,
        "blockers": list(blockers),
        "hashes": {name: canonical_sha256(bundle[name]) for name in REQUIRED_LAYERS},
    }


def build_goal_release_manifest(packages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build a candidate manifest for the independent goal-driven track."""
    packages = _mapping(packages, "goal release packages")
    if not packages:
        raise ValueError("goal release manifest needs at least one package")
    entries = [audit_goal_challenge_bundle(name, bundle)
               for name, bundle in sorted(packages.items())]
    if len({entry["task_id"] for entry in entries}) != len(entries):
        raise ValueError("goal release task IDs must be unique")
    return {
        "schema": "eai.benchmark.goal_release_manifest",
        "version": 1,
        "evaluation_track": "goal_driven_team_planning_v1",
        "release_status": "pending",
        "benchmark_ready": False,
        "scoreable": False,
        "packages": entries,
        "participant_delivery": [
            "release/public_task.json",
            "release/participant_api.json",
            "release/baseline_manifest.json",
            "runtime events delivered up to the current simulation time",
        ],
        "evaluator_only": [
            "evaluator/hidden_evaluator.json",
            "author witness plans",
            "future lifecycle and communication schedules",
            "private domain truth and reference evidence",
        ],
        "stages": [
            {"id": "goal_contract", "status": "passed",
             "evidence": "outcome-only public task, skill ontology and v2 participant API"},
            {"id": "authoring_review", "status": "passed",
             "evidence": "scene-native declarations and multiple plans legal under the declared constraint model"},
            {"id": "reliable_skill_constraints", "status": "passed",
             "evidence": "per-member payload, fixed-point interfaces, route envelopes and timed resource reservations"},
            {"id": "scene_binding_and_skill_backend", "status": "pending",
             "evidence": "scene-bound geometry/visibility and reliable skill backend conformance; autonomous grasp algorithms are outside this track"},
            {"id": "live_execution", "status": "pending",
             "evidence": "constraint-preserving execution, dynamic events, communication effects and domain evidence"},
            {"id": "formal_scoring_release", "status": "pending",
             "evidence": "hidden outcome predicates and held-out reference episodes"},
        ],
        "claims": {
            "goal_contract_complete": True,
            "authoring_review_complete": True,
            "declared_constraint_model_review_complete": True,
            "scene_binding_validated": False,
            "skill_backend_conformance_validated": False,
            "full_benchmark_deployed": False,
            "physical_heterogeneous_capability_validated": False,
        },
    }


def compile_goal_release_manifest() -> dict[str, Any]:
    from demo.benchmark.goal_driven_tasks import generate_goal_task_packages

    return build_goal_release_manifest(generate_goal_task_packages())


def compile_default_release_manifest(*, stair_measurement_path: Path | None = None
                                     ) -> dict[str, Any]:
    """Compile the reviewed business packages plus the Factory stair probe."""
    from demo.factory_benchmark.challenge import compile_challenge as factory
    from demo.factory_benchmark.vertical_challenge import compile_challenge as vertical
    from demo.modern_house_benchmark.challenge import compile_challenge as house
    from demo.warehouse_benchmark.challenge import compile_challenge as warehouse

    manifest = build_release_manifest({
        "factory_business": factory(),
        "factory_vertical_diagnostic": vertical(
            stair_measurement_path=stair_measurement_path),
        "modern_house_business": house(),
        "warehouse_business": warehouse(),
    })
    # Lifecycle plans are author/evaluator evidence. Compile them against the
    # same current maps used by the maintained scenarios so stale parking
    # points cannot survive a map or collision-product rebuild.
    from demo.benchmark.lifecycle_plan import build_maintained_lifecycle_plan
    lifecycle_plans = {
        scene: build_maintained_lifecycle_plan(scene)
        for scene in ("factory", "warehouse_nvd", "modern_house_nvd")
    }
    manifest["lifecycle_plans"] = {
        scene: {
            "schema": plan["schema"],
            "version": plan["version"],
            "reserve_agent_id": plan["reserve_agent_id"],
            "parking_pose": plan["parking_pose"],
            "join_node_id": plan["join_node_id"],
            "events": plan["events"],
            "map_sha256": plan["map_sha256"],
            "route": plan["route"],
            "physical_execution_validated": plan["physical_execution_validated"],
            "visibility": plan["visibility"],
        }
        for scene, plan in lifecycle_plans.items()
    }
    manifest["stages"] = [
        ({**stage, "evidence": (
            "three map-hash-bound private lifecycle plans compile; physical "
            "activation and reference episodes remain pending"
            if stage["id"] == "live_domain_and_factor_adapters" else stage["evidence"]
        )} if stage["id"] == "live_domain_and_factor_adapters" else stage)
        for stage in manifest["stages"]
    ]
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stair-measurement", type=Path,
                        help="private hash-bound Factory stair measurement")
    args = parser.parse_args(argv)
    manifest = compile_default_release_manifest(
        stair_measurement_path=args.stair_measurement)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise FileExistsError(f"refusing to overwrite release manifest: {args.output}")
        args.output.write_text(encoded, encoding="utf-8")
        print(f"release_manifest: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "audit_challenge_bundle", "audit_goal_challenge_bundle", "build_goal_release_manifest",
    "build_release_manifest", "compile_default_release_manifest", "compile_goal_release_manifest",
    "main",
]
