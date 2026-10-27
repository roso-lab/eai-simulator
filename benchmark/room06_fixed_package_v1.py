"""Content-addressed fixed package for the room_06 × liv-t-04 first run.

The package is the CPU boundary immediately before an Isaac run.  It reruns
the task-specific preflight, derives transition identity from that report, and
binds every source byte needed to rebuild the case.  It never opens Isaac and
never turns a diagnostic package into a paper-eligible result.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .case_manifest_builder_v1 import CaseManifestBuildError, build_case_manifest
from .records_v3 import canonical_digest
from .room06_runtime_preflight_v1 import (
    FORMAL_GATES,
    Room06RuntimePreflightError,
    require_room06_runtime_preflight,
)


PACKAGE_SCHEMA = "heterocoord.room06-fixed-package.v1"
PLAN_SCHEMA = "heterocoord.room06-fixed-plan.v1"
DEPLOYMENT_SCHEMA = "heterocoord.room06-deployment-state.v1"
TASK_SETUP_SCHEMA = "heterocoord.room06-task-setup.v1"
PAPER_SHA = "dac7b94207a2683d494bdb388fc48533c9219bdf"
TASK_ID = "liv-t-04"
SCENE_KEY = "room_06"
HORIZON_TICKS = 15_000
TAU_STABLE = 25
ENTITY_IDS = ("book_batch_01", "heavy_object")
REQUIRED_POINTS = (
    "floor1-bookshelf",
    "floor2-bookshelf-1",
    "floor2-bookshelf-2",
    "floor2-bookshelf-3",
)
EVALUATOR = {
    "evaluator_id": "heterocoord.evaluator.room06-livt04",
    "evaluator_version": "room06-livt04-evaluator-v2",
}
REPLAY = {
    "serializer_version": "heterocoord.room06-replay-v1",
    "rng_stream_key": "room06/liv-t-04/c0/r1",
    "checkpoint_schema": "heterocoord.room06-checkpoint-v1",
}
ORACLE_OBLIGATION_BINDING = {
    "schema": "heterocoord.oracle-obligation-binding.v1",
    "anchor_id": TASK_ID,
    "obligations": [{
        "obligation_id": "relay",
        "relation": "P2",
        "predicate_ids": ["upper_shelf_delivery_after_transition_handoff"],
    }],
}


class Room06FixedPackageError(ValueError):
    """Raised when a fixed package cannot be created or verified."""


def _load(value: Mapping[str, Any] | str | Path, name: str) -> tuple[dict[str, Any], str, str]:
    if isinstance(value, Mapping):
        result = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        return result, canonical_digest(result), "canonical_json_sha256"
    path = Path(value).resolve()
    if not path.is_file() or path.is_symlink():
        raise Room06FixedPackageError(f"{name} is not a regular JSON file: {path}")
    try:
        raw = path.read_bytes()
        result = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise Room06FixedPackageError(f"cannot read {name}: {path}") from exc
    if not isinstance(result, Mapping):
        raise Room06FixedPackageError(f"{name} must be an object")
    return dict(result), hashlib.sha256(raw).hexdigest(), "file_sha256"


def _sha(path: str | Path, name: str) -> str:
    resolved = Path(path).resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise Room06FixedPackageError(f"{name} is not a regular file: {resolved}")
    try:
        return hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError as exc:
        raise Room06FixedPackageError(f"cannot hash {name}: {resolved}") from exc


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06FixedPackageError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise Room06FixedPackageError(f"{name} must be a lowercase SHA-256")
    return value


def _finite_vector(value: Any, name: str, length: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise Room06FixedPackageError(f"{name} must contain {length} numeric values")
    result = [float(item) for item in value]
    if not all(math.isfinite(item) for item in result):
        raise Room06FixedPackageError(f"{name} must contain finite values")
    return result


def _deployment(value: Mapping[str, Any] | str | Path, selected: Sequence[str]) -> tuple[dict[str, Any], str, str]:
    deployment, source_digest, source_kind = _load(value, "scene deployment state")
    if deployment.get("schema") != DEPLOYMENT_SCHEMA:
        raise Room06FixedPackageError("scene deployment state schema mismatch")
    rows = deployment.get("robots")
    if not isinstance(rows, list) or len(rows) != len(selected):
        raise Room06FixedPackageError("scene deployment must exactly cover selected robots")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Room06FixedPackageError(f"deployment.robots[{index}] must be an object")
        if set(row) != {"instance_id", "initial_state_digest", "position_m", "orientation_wxyz"}:
            raise Room06FixedPackageError(f"deployment.robots[{index}] schema mismatch")
        instance = _text(row.get("instance_id"), f"deployment.robots[{index}].instance_id")
        if instance in seen:
            raise Room06FixedPackageError("deployment robot identities must be unique")
        seen.add(instance)
        _digest(row.get("initial_state_digest"), f"deployment.{instance}.initial_state_digest")
        normalized.append({
            "instance_id": instance,
            "initial_state_digest": row["initial_state_digest"],
            "position_m": _finite_vector(row["position_m"], f"deployment.{instance}.position_m", 3),
            "orientation_wxyz": _finite_vector(row["orientation_wxyz"], f"deployment.{instance}.orientation_wxyz", 4),
        })
    if set(seen) != set(selected):
        raise Room06FixedPackageError("deployment robot IDs do not match selected_robot_ids")
    normalized.sort(key=lambda row: row["instance_id"])
    result = {"schema": DEPLOYMENT_SCHEMA, "robots": normalized}
    return result, source_digest, source_kind


def _roster_initial_states(value: str | Path, selected: Sequence[str]) -> tuple[dict[str, str], str]:
    """Bind scene spawn rows to the exact promoted roster instance identities.

    The roster's ``initial_state_digest`` describes the instance's default
    configuration.  The deployment file is the scene-specific pose/state
    record, but it must still reference the same digest for every selected
    instance; otherwise a package could silently combine a pose with a
    different robot configuration.
    """

    roster, source_digest, _ = _load(value, "robot roster")
    if roster.get("schema_id") != "heterocoord.robot_runtime_roster":
        raise Room06FixedPackageError("robot roster schema mismatch")
    rows = roster.get("robots")
    if not isinstance(rows, list):
        raise Room06FixedPackageError("robot roster robots must be a list")
    by_id: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Room06FixedPackageError(f"robot roster robots[{index}] must be an object")
        instance = _text(row.get("instance_id"), f"robot roster robots[{index}].instance_id")
        if instance in by_id:
            raise Room06FixedPackageError("robot roster instance IDs must be unique")
        initial = row.get("initial_state_binding")
        if not isinstance(initial, Mapping):
            raise Room06FixedPackageError(f"robot roster {instance} has no initial_state_binding")
        by_id[instance] = _digest(
            initial.get("initial_state_digest"),
            f"robot roster {instance}.initial_state_digest",
        )
    if set(selected) - set(by_id):
        raise Room06FixedPackageError(
            f"selected robots missing from robot roster: {sorted(set(selected) - set(by_id))}"
        )
    return {instance: by_id[instance] for instance in selected}, source_digest


def _d6_reference(value: Mapping[str, Any] | None, selected: Sequence[str]) -> dict[str, Any] | None:
    """Normalize an independent per-instance D6 calibration table.

    ``calibration_digest`` remains an externally issued authority token (as
    in ``receipt_adapter_v3``); the package additionally binds the complete
    table bytes through its own package digest and stores the selected
    instance set explicitly.  No global reference is accepted here.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"calibration", "calibration_digest"}:
        raise Room06FixedPackageError(
            "d6_reference must contain exactly calibration and calibration_digest"
        )
    calibration_digest = _digest(value.get("calibration_digest"), "d6_reference.calibration_digest")
    rows = value.get("calibration")
    if not isinstance(rows, list) or not rows:
        raise Room06FixedPackageError("d6_reference.calibration must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {
            "instance_id", "reference_bits", "reference_time"
        }:
            raise Room06FixedPackageError(f"d6_reference.calibration[{index}] schema mismatch")
        instance = _text(row.get("instance_id"), f"d6_reference.calibration[{index}].instance_id")
        if instance in seen:
            raise Room06FixedPackageError(f"duplicate D6 calibration instance: {instance}")
        seen.add(instance)
        bits = float(row["reference_bits"])
        time = float(row["reference_time"])
        if not math.isfinite(bits) or not math.isfinite(time) or bits <= 0.0 or time <= 0.0:
            raise Room06FixedPackageError(
                f"d6_reference.calibration[{index}] references must be finite and positive"
            )
        normalized.append({
            "instance_id": instance,
            "reference_bits": bits,
            "reference_time": time,
        })
    if seen != set(selected):
        raise Room06FixedPackageError(
            "D6 calibration instances must exactly match selected robots "
            f"(missing={sorted(set(selected) - seen)}, unknown={sorted(seen - set(selected))})"
        )
    normalized.sort(key=lambda row: row["instance_id"])
    return {"calibration": normalized, "calibration_digest": calibration_digest}


def _validate_catalog(value: Mapping[str, Any]) -> None:
    """Run the complete authoring-catalog validator before selecting a row."""

    if value.get("schema_version") != "paper-task-catalog.v2":
        raise Room06FixedPackageError("task catalog schema_version is unsupported")
    if value.get("contract_version") != "C-P2-02-quarantine-marked-v2":
        raise Room06FixedPackageError("task catalog contract_version is unsupported")
    anchors = value.get("anchors")
    if not isinstance(anchors, list):
        raise Room06FixedPackageError("task catalog anchors must be a list")
    try:
        from .task_catalog import TaskAnchor, validate_catalog
        enriched = []
        for raw in anchors:
            if not isinstance(raw, Mapping):
                raise ValueError("catalog anchor must be an object")
            item = dict(raw)
            metadata = dict(item.get("metadata", {}))
            metadata.update({
                "catalog_schema_version": value["schema_version"],
                "contract_version": value["contract_version"],
            })
            item["metadata"] = metadata
            enriched.append(TaskAnchor.from_mapping(item))
        validate_catalog(enriched)
    except (ImportError, TypeError, ValueError, KeyError) as exc:
        raise Room06FixedPackageError(f"task catalog failed validation: {exc}") from exc


def _resolve_contract_artifact(
    value: Mapping[str, Any] | str | Path,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None, str | None]:
    """Resolve a canonical-pointer file to its content-addressed contract set."""

    if artifact.get("schema") != "heterocoord.canonical_contract_artifact.v1":
        return dict(artifact), None, None
    if artifact.get("status") != "bound_pending_replay" or artifact.get("paper_sha") != PAPER_SHA:
        raise Room06FixedPackageError("canonical contract pointer identity/status is stale")
    nested_ref = _text(artifact.get("contract_artifact"), "canonical contract pointer.contract_artifact")
    nested_path = Path(nested_ref)
    if not nested_path.is_absolute():
        candidates = [Path.cwd() / nested_path]
        if not isinstance(value, Mapping):
            pointer_path = Path(value).resolve()
            candidates.extend([
                pointer_path.parent / nested_path,
                pointer_path.parent.parent / nested_path,
                pointer_path.parent.parent.parent / nested_path,
            ])
        nested_path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    nested, nested_digest, nested_kind = _load(nested_path, "contract artifact")
    expected_nested = _digest(artifact.get("contract_artifact_sha256"), "canonical contract pointer.contract_artifact_sha256")
    if nested_digest != expected_nested:
        raise Room06FixedPackageError("canonical contract pointer does not match contract artifact bytes")
    return nested, str(nested_path.resolve()), nested_digest


def _task_bindings(setup: Mapping[str, Any], scene_digest: str) -> dict[str, Any]:
    if setup.get("schema") != TASK_SETUP_SCHEMA or setup.get("task_id") != TASK_ID:
        raise Room06FixedPackageError("task setup must identify liv-t-04 v1")
    rows = setup.get("objects")
    if (
        not isinstance(rows, list)
        or len(rows) != len(ENTITY_IDS)
        or {row.get("entity_id") for row in rows if isinstance(row, Mapping)} != set(ENTITY_IDS)
    ):
        raise Room06FixedPackageError("task setup must exactly cover book_batch_01 and heavy_object")
    definitions = {row["entity_id"]: row["definition"] for row in rows}
    if any(not isinstance(value, Mapping) or not value for value in definitions.values()):
        raise Room06FixedPackageError("task setup definitions must be non-empty objects")
    objects = [{
        "entity_id": entity_id,
        "origin": "task_generated",
        "definition_ref": f"task-setup:{TASK_ID}/{entity_id}",
        "definition_digest": canonical_digest(definitions[entity_id]),
    } for entity_id in ENTITY_IDS]
    return {
        "schema": "heterocoord.task-object-bindings.v1",
        "scene_digest": scene_digest,
        "objects": objects,
    }


def _select_contract(artifact: Mapping[str, Any], catalog: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if artifact.get("status") != "candidate_contract_only" or artifact.get("paper_sha") != PAPER_SHA:
        raise Room06FixedPackageError("canonical contract artifact identity/status is stale")
    rows = artifact.get("contracts")
    if not isinstance(rows, list):
        raise Room06FixedPackageError("contract artifact must contain contracts")
    try:
        from tools.benchmark_branch.pilot_contract import validate_pilot_contract, validate_pilot_contract_set
        validate_pilot_contract_set(artifact)
    except (ImportError, TypeError, ValueError) as exc:
        raise Room06FixedPackageError(f"contract artifact failed validation: {exc}") from exc
    selected = [row for row in rows if isinstance(row, Mapping) and row.get("anchor_id") == TASK_ID]
    if len(selected) != 1:
        raise Room06FixedPackageError("contract artifact must contain exactly one liv-t-04 contract")
    row = dict(selected[0])
    if row.get("paper_sha") != PAPER_SHA or row.get("status") != "candidate_contract_only":
        raise Room06FixedPackageError("selected liv-t-04 contract is stale or certified")
    catalog_rows = [row for row in catalog.get("anchors", []) if isinstance(row, Mapping) and row.get("anchor_id") == TASK_ID]
    if len(catalog_rows) != 1:
        raise Room06FixedPackageError("catalog must contain exactly one liv-t-04 anchor")
    catalog_row = catalog_rows[0]
    identity = row.get("identity")
    capability = row.get("capability_contract")
    obligations = row.get("obligation_contract")
    milestones = row.get("milestone_contract")
    if not isinstance(identity, Mapping) or not isinstance(capability, Mapping):
        raise Room06FixedPackageError("selected contract identity/capability binding is missing")
    expected_identity = {
        "domain": catalog_row.get("domain"),
        "scene_key": catalog_row.get("scene_key"),
        "scene_anchor": catalog_row.get("scene_anchor"),
        "p3_subtype": catalog_row.get("p3_subtype"),
        "relation_signature": catalog_row.get("relation_signature"),
    }
    actual_identity = {key: identity.get(key) for key in expected_identity}
    if actual_identity != expected_identity:
        raise Room06FixedPackageError("selected contract identity disagrees with task catalog")
    if capability.get("required_capabilities") != catalog_row.get("required_capabilities"):
        raise Room06FixedPackageError("selected contract capabilities disagree with task catalog")
    if not isinstance(obligations, Mapping) or obligations.get("obligations") != catalog_row.get("obligations"):
        raise Room06FixedPackageError("selected contract obligations disagree with task catalog")
    if obligations.get("roles") != catalog_row.get("roles"):
        raise Room06FixedPackageError("selected contract roles disagree with task catalog")
    if not isinstance(milestones, Mapping) or milestones.get("ordered_labels") != catalog_row.get("milestones"):
        raise Room06FixedPackageError("selected contract milestones disagree with task catalog")
    return row, canonical_digest(row)


def _source(path: str | Path, value_digest: str, kind: str, role: str) -> dict[str, str]:
    return {
        "role": role,
        "path": str(Path(path).resolve()),
        "sha256": value_digest,
        "digest_kind": kind,
    }


def _source_value(value: Mapping[str, Any] | str | Path, parsed: Mapping[str, Any],
                 source_digest: str, source_kind: str, role: str) -> dict[str, str]:
    """Describe either a file-backed or in-memory JSON input."""

    if isinstance(value, Mapping):
        return {
            "role": role,
            "path": f"<in-memory:{role}>",
            "sha256": canonical_digest(parsed),
            "digest_kind": "canonical_json_sha256",
        }
    return _source(value, source_digest, source_kind, role)


def _tree_sources(root: str | Path) -> list[dict[str, str]]:
    """Return every regular map byte covered by the package provenance."""

    resolved = Path(root).resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise Room06FixedPackageError(f"semantic_root is not a regular directory: {resolved}")
    files = [path for path in resolved.rglob("*") if path.is_file() and not path.is_symlink()]
    return [_source(path, _sha(path, f"semantic map file {path}"), "file_sha256", "semantic_map")
            for path in sorted(files)]


def build_room06_fixed_package(
    *,
    semantic_root: str | Path,
    scene_usd: str | Path,
    robot_roster: str | Path,
    preflight_binding: Mapping[str, Any] | str | Path,
    task_setup: Mapping[str, Any] | str | Path,
    deployment_state: Mapping[str, Any] | str | Path,
    catalog: Mapping[str, Any] | str | Path,
    contract_artifact: Mapping[str, Any] | str | Path,
    case_id: str = "room06-livt04-c0-r1",
    d6_reference: Mapping[str, Any] | None = None,
    diagnostic_inputs: bool = False,
) -> dict[str, Any]:
    """Create one fresh, diagnostic-only fixed package."""

    case_id = _text(case_id, "case_id")
    binding, binding_source, binding_kind = _load(preflight_binding, "preflight binding")
    setup, setup_source, setup_kind = _load(task_setup, "task setup")
    catalog_value, catalog_source, catalog_kind = _load(catalog, "task catalog")
    artifact_input, artifact_source, artifact_kind = _load(contract_artifact, "contract artifact")
    if (isinstance(catalog, Mapping) or isinstance(contract_artifact, Mapping)) and not diagnostic_inputs:
        raise Room06FixedPackageError(
            "production fixed packages require file-backed canonical catalog/contract inputs"
        )
    if not diagnostic_inputs and artifact_input.get("schema") != "heterocoord.canonical_contract_artifact.v1":
        raise Room06FixedPackageError(
            "production fixed packages require the canonical contract pointer"
        )
    artifact, nested_contract_path, nested_contract_digest = _resolve_contract_artifact(
        contract_artifact, artifact_input
    )
    _validate_catalog(catalog_value)
    if artifact.get("schema_version") == "heterocoord-pilot-contract-set.v1":
        if artifact.get("source_catalog") != "benchmark/task_catalog.json":
            raise Room06FixedPackageError("contract artifact source_catalog is not canonical")
        if isinstance(catalog, Mapping):
            raise Room06FixedPackageError(
                "canonical contract artifact requires file-backed catalog input"
            )
        if artifact.get("source_catalog_sha256") != catalog_source:
            raise Room06FixedPackageError("contract artifact source catalog bytes do not match catalog input")
    pointer_catalog_digest = artifact_input.get("source_catalog_sha256")
    if pointer_catalog_digest is not None and not isinstance(catalog, Mapping):
        if _digest(pointer_catalog_digest, "contract source_catalog_sha256") != catalog_source:
            raise Room06FixedPackageError("canonical contract source catalog bytes do not match catalog input")
    if nested_contract_path and artifact_input.get("anchor_count") != len(artifact.get("contracts", ())):
        raise Room06FixedPackageError("canonical contract pointer anchor_count does not match contract artifact")
    selected = binding.get("selected_robot_ids")
    if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected):
        raise Room06FixedPackageError("preflight binding selected_robot_ids is invalid")
    deployment, deployment_source, deployment_kind = _deployment(deployment_state, selected)
    roster_initial_states, roster_source = _roster_initial_states(robot_roster, selected)
    deployment_by_id = {row["instance_id"]: row for row in deployment["robots"]}
    for instance_id, roster_digest in roster_initial_states.items():
        if deployment_by_id[instance_id]["initial_state_digest"] != roster_digest:
            raise Room06FixedPackageError(
                f"deployment {instance_id}.initial_state_digest does not match robot roster"
            )
    scene_digest = _digest(binding.get("scene_asset_digest"), "preflight scene_asset_digest")
    scene_source = _sha(scene_usd, "scene USD")
    if scene_source != scene_digest:
        raise Room06FixedPackageError("scene USD digest does not match preflight binding")

    try:
        preflight = require_room06_runtime_preflight(
            semantic_root=semantic_root,
            scene_usd=scene_usd,
            robot_roster=robot_roster,
            binding=binding,
            task_setup=setup,
        )
    except (Room06RuntimePreflightError, OSError, TypeError, ValueError) as exc:
        raise Room06FixedPackageError(f"fresh room06 preflight failed: {exc}") from exc

    contract, contract_digest = _select_contract(artifact, catalog_value)
    task_objects = _task_bindings(setup, scene_digest)
    normalized_d6_reference = _d6_reference(d6_reference, selected)
    d6_ready = normalized_d6_reference is not None

    runtime_binding = {
        "schema": "heterocoord.room06-runtime-binding.v1",
        "anchor_id": TASK_ID,
        "scene_id": SCENE_KEY,
        "transition_handoff_node_id": preflight["transition_handoff_node_id"],
        "runtime_binding_digest": preflight["runtime_binding_digest"],
    }
    plan = {
        "schema": PLAN_SCHEMA,
        "paper_sha": PAPER_SHA,
        "case_id": case_id,
        "task_id": TASK_ID,
        "anchor_id": TASK_ID,
        "horizon_ticks": HORIZON_TICKS,
        "tau_stable_ticks": TAU_STABLE,
        "room06_runtime_binding": runtime_binding,
        "oracle_obligation_binding": ORACLE_OBLIGATION_BINDING,
        "task_setup_digest": canonical_digest(setup),
        "task_object_binding_digest": canonical_digest(task_objects),
        "scene_deployment_digest": canonical_digest(deployment),
        "robot_roster_digest": _sha(robot_roster, "robot roster"),
        "selected_robot_ids": list(selected),
        "replay": dict(REPLAY),
        "evaluator": dict(EVALUATOR),
        "d6_reference_digest": canonical_digest(normalized_d6_reference) if normalized_d6_reference else None,
        "d6_reference_ready": d6_ready,
        "formal_gates": dict(FORMAL_GATES),
    }
    plan_digest = canonical_digest(plan)
    domain = "living_public"
    relation_signature = ["P2", "P3-J"]
    manifest_binding = {
        "case_id": case_id,
        "task_id": TASK_ID,
        "anchor_id": TASK_ID,
        "domain": domain,
        "relation_signature": relation_signature,
        "base_instance_id": f"{case_id}/base",
        "scene_digest": scene_digest,
        "entity_ids": list(ENTITY_IDS),
        "zone_ids": [],
        "horizon": HORIZON_TICKS,
        "tau_stable": TAU_STABLE,
        "units": "meters+ticks",
        "format_contract_version": "2.1",
        "object_bindings": task_objects,
        "pool_id": "promoted-27-v3.1",
        "resume_ids": list(selected),
        "contract": {
            "contract_digest": contract_digest,
            "oracle_id": contract["oracle_contract"]["oracle_ref"],
            "contract_revision": f"paper-{PAPER_SHA}",
        },
        "catalog_digest": canonical_digest(catalog_value),
        "plan_digest": plan_digest,
        "replay": dict(REPLAY),
        "evaluator": dict(EVALUATOR),
        "semantic_requirements": {"semantic_points": list(REQUIRED_POINTS)},
    }
    try:
        manifest = build_case_manifest(
            semantic_root=semantic_root,
            robot_pool_path=robot_roster,
            binding=manifest_binding,
        )
    except CaseManifestBuildError as exc:
        raise Room06FixedPackageError(f"case manifest build failed: {exc}") from exc

    sources = _tree_sources(semantic_root) + [
        _source(scene_usd, scene_source, "file_sha256", "scene_usd"),
        _source(robot_roster, _sha(robot_roster, "robot roster"), "file_sha256", "robot_roster"),
        _source_value(preflight_binding, binding, binding_source, binding_kind, "preflight_binding"),
        _source_value(task_setup, setup, setup_source, setup_kind, "task_setup"),
        _source_value(catalog, catalog_value, catalog_source, catalog_kind, "catalog"),
        _source_value(contract_artifact, artifact_input, artifact_source, artifact_kind, "contract_pointer" if nested_contract_path else "contract_artifact"),
        _source_value(deployment_state, deployment, deployment_source, deployment_kind, "deployment_state"),
    ]
    if nested_contract_path:
        sources.append(_source(nested_contract_path, nested_contract_digest or "", "file_sha256", "contract_artifact"))
    package = {
        "schema": PACKAGE_SCHEMA,
        "package_digest": "0" * 64,
        "paper_sha": PAPER_SHA,
        "case_id": case_id,
        "preflight": preflight,
        "plan": plan,
        "manifest": manifest,
        "task_setup": setup,
        "task_object_bindings": task_objects,
        "scene_deployment": deployment,
        "source_inputs": {
            "preflight_binding": binding,
            "task_setup": setup,
            "catalog": catalog_value,
            "contract_pointer": artifact_input,
            "contract_artifact": artifact,
            "deployment_state": deployment,
        },
        "selected_contract": contract,
        "d6_reference": normalized_d6_reference,
        "robot_roster_digest": roster_source,
        "selected_contract_digest": contract_digest,
        "source_provenance": sources,
        "readiness": {
            "case_receipt_ready": False,
            "paper_support_ready": False,
            "d6_reference_ready": d6_ready,
            "export_mode": "production" if d6_ready else "measured_unqualified",
            "formal_gates": dict(FORMAL_GATES),
        },
    }
    body = dict(package)
    body.pop("package_digest")
    package["package_digest"] = canonical_digest(body)
    return package


def verify_room06_fixed_package(package: Mapping[str, Any]) -> dict[str, Any]:
    """Verify package self-integrity and source provenance without rebuilding it."""

    if not isinstance(package, Mapping) or package.get("schema") != PACKAGE_SCHEMA:
        raise Room06FixedPackageError("fixed package schema mismatch")
    expected = package.get("package_digest")
    _digest(expected, "package_digest")
    body = dict(package)
    body.pop("package_digest", None)
    if expected != canonical_digest(body):
        raise Room06FixedPackageError("package_digest does not bind package bytes")
    if package.get("paper_sha") != PAPER_SHA:
        raise Room06FixedPackageError("fixed package paper identity is stale")
    if package.get("readiness", {}).get("formal_gates") != FORMAL_GATES:
        raise Room06FixedPackageError("fixed package formal gates must remain closed")
    plan = package.get("plan")
    if not isinstance(plan, Mapping):
        raise Room06FixedPackageError("fixed package plan is missing")
    source_inputs = package.get("source_inputs")
    if not isinstance(source_inputs, Mapping):
        raise Room06FixedPackageError("fixed package source_inputs are missing")
    required_inputs = {
        "preflight_binding", "task_setup", "catalog", "contract_pointer",
        "contract_artifact", "deployment_state",
    }
    if set(source_inputs) != required_inputs:
        raise Room06FixedPackageError("fixed package source_inputs schema is incomplete")
    _validate_catalog(source_inputs["catalog"])
    for gate_source_name in ("preflight_binding", "contract_pointer", "contract_artifact"):
        gate_source = source_inputs[gate_source_name]
        if isinstance(gate_source, Mapping) and "formal_gates" in gate_source and gate_source["formal_gates"] != FORMAL_GATES:
            raise Room06FixedPackageError(f"{gate_source_name} formal gates must remain closed")
    if not isinstance(package.get("preflight"), Mapping) or package["preflight"].get("formal_gates") != FORMAL_GATES:
        raise Room06FixedPackageError("package preflight formal gates must remain closed")
    if plan.get("formal_gates") != FORMAL_GATES:
        raise Room06FixedPackageError("plan formal gates must remain closed")
    readiness = package.get("readiness")
    if not isinstance(readiness, Mapping):
        raise Room06FixedPackageError("package readiness is missing")
    if readiness.get("case_receipt_ready") is not False or readiness.get("paper_support_ready") is not False:
        raise Room06FixedPackageError("package receipt/paper readiness must remain false")
    selected = plan.get("selected_robot_ids")
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
        raise Room06FixedPackageError("fixed package selected_robot_ids are invalid")
    binding = source_inputs["preflight_binding"]
    if not isinstance(binding, Mapping) or binding.get("selected_robot_ids") != selected:
        raise Room06FixedPackageError("plan selected_robot_ids disagree with preflight binding")
    deployment = package.get("scene_deployment")
    if plan.get("scene_deployment_digest") != canonical_digest(deployment):
        raise Room06FixedPackageError("plan scene deployment digest is not bound")
    if source_inputs["deployment_state"] != deployment:
        raise Room06FixedPackageError("deployment source input disagrees with normalized deployment")
    task_setup = package.get("task_setup")
    if source_inputs["task_setup"] != task_setup or plan.get("task_setup_digest") != canonical_digest(task_setup):
        raise Room06FixedPackageError("task setup digest/input is not bound")
    task_objects = package.get("task_object_bindings")
    if plan.get("task_object_binding_digest") != canonical_digest(task_objects):
        raise Room06FixedPackageError("task object binding digest is not bound")
    if package.get("robot_roster_digest") != plan.get("robot_roster_digest"):
        raise Room06FixedPackageError("robot roster digest is not shared by package and plan")
    if package.get("selected_contract_digest") != canonical_digest(package.get("selected_contract")):
        raise Room06FixedPackageError("selected contract digest is not bound")
    try:
        selected_contract, selected_contract_digest = _select_contract(
            source_inputs["contract_artifact"], source_inputs["catalog"]
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise Room06FixedPackageError(f"source contract failed validation: {exc}") from exc
    if selected_contract != package.get("selected_contract") or selected_contract_digest != package.get("selected_contract_digest"):
        raise Room06FixedPackageError("selected contract is not bound to source contract artifact")
    manifest = package.get("manifest")
    if not isinstance(manifest, Mapping):
        raise Room06FixedPackageError("fixed package manifest is missing")
    if manifest.get("plan_digest") != canonical_digest(plan):
        raise Room06FixedPackageError("manifest plan digest is not bound")
    if manifest.get("catalog_digest") != canonical_digest(source_inputs["catalog"]):
        raise Room06FixedPackageError("manifest catalog digest is not bound")
    if manifest.get("contract", {}).get("contract_digest") != package.get("selected_contract_digest"):
        raise Room06FixedPackageError("manifest contract digest is not bound")
    sources = package.get("source_provenance")
    if not isinstance(sources, list) or not sources:
        raise Room06FixedPackageError("fixed package source provenance is missing")
    roles = [source.get("role") for source in sources if isinstance(source, Mapping)]
    if len(roles) != len(sources) or len(set((source.get("role"), source.get("path")) for source in sources if isinstance(source, Mapping))) != len(sources):
        raise Room06FixedPackageError("source provenance contains duplicate or malformed role/path entries")
    singular_roles = {
        "scene_usd", "robot_roster", "preflight_binding", "task_setup", "catalog",
        "contract_artifact", "deployment_state",
    }
    for role in singular_roles:
        if roles.count(role) != 1:
            raise Room06FixedPackageError(f"source provenance must contain exactly one {role} entry")
    if not any(role == "semantic_map" for role in roles):
        raise Room06FixedPackageError("source provenance must contain semantic map bytes")
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise Room06FixedPackageError(f"source_provenance[{index}] is malformed")
        path = source.get("path")
        expected_source = source.get("sha256")
        if not isinstance(path, str) or not path.strip():
            raise Room06FixedPackageError(f"source_provenance[{index}].path is missing")
        kind = source.get("digest_kind")
        if kind == "canonical_json_sha256" and path.startswith("<in-memory:"):
            role = source.get("role")
            input_by_role = {
                "preflight_binding": source_inputs["preflight_binding"],
                "task_setup": source_inputs["task_setup"],
                "catalog": source_inputs["catalog"],
                "contract_pointer": source_inputs["contract_pointer"],
                "contract_artifact": source_inputs["contract_artifact"],
                "deployment_state": source_inputs["deployment_state"],
            }
            if role not in input_by_role or expected_source != canonical_digest(input_by_role[role]):
                raise Room06FixedPackageError(f"in-memory source bytes changed: {role}")
        elif _sha(path, f"source_provenance[{index}]") != expected_source:
            raise Room06FixedPackageError(f"source bytes changed: {path}")
    pointer = source_inputs["contract_pointer"]
    if isinstance(pointer, Mapping) and pointer.get("schema") == "heterocoord.canonical_contract_artifact.v1":
        provenance_by_role = {source["role"]: source for source in sources if source["role"] != "semantic_map"}
        if pointer.get("status") != "bound_pending_replay" or pointer.get("paper_sha") != PAPER_SHA:
            raise Room06FixedPackageError("canonical contract pointer identity/status is stale")
        if pointer.get("contract_artifact_sha256") != provenance_by_role["contract_artifact"]["sha256"]:
            raise Room06FixedPackageError("canonical pointer contract artifact provenance mismatch")
        if pointer.get("source_catalog_sha256") != provenance_by_role["catalog"]["sha256"]:
            raise Room06FixedPackageError("canonical pointer source catalog provenance mismatch")
        if pointer.get("anchor_count") != len(source_inputs["contract_artifact"].get("contracts", ())):
            raise Room06FixedPackageError("canonical pointer anchor_count mismatch")
    if plan.get("oracle_obligation_binding") != ORACLE_OBLIGATION_BINDING:
        raise Room06FixedPackageError("fixed package D3 obligation binding is not exact")
    d6_reference = package.get("d6_reference")
    selected = plan.get("selected_robot_ids")
    if not isinstance(selected, list) or not selected:
        raise Room06FixedPackageError("fixed package selected_robot_ids are missing")
    normalized_d6 = _d6_reference(d6_reference, selected)
    expected_d6_digest = canonical_digest(normalized_d6) if normalized_d6 else None
    if plan.get("d6_reference_digest") != expected_d6_digest:
        raise Room06FixedPackageError("fixed package D6 reference digest is not bound")
    if plan.get("d6_reference_ready") is not (normalized_d6 is not None):
        raise Room06FixedPackageError("fixed package D6 readiness does not match reference")
    if package.get("readiness", {}).get("d6_reference_ready") is not (normalized_d6 is not None):
        raise Room06FixedPackageError("fixed package readiness D6 flag does not match reference")
    if plan.get("d6_reference_ready") is False and package["readiness"].get("export_mode") != "measured_unqualified":
        raise Room06FixedPackageError("missing D6 reference must remain diagnostic-only")
    if plan.get("d6_reference_ready") is True and package["readiness"].get("export_mode") != "production":
        raise Room06FixedPackageError("present D6 reference cannot be marked non-production")
    return json.loads(json.dumps(package, ensure_ascii=False))


__all__ = [
    "DEPLOYMENT_SCHEMA", "EVALUATOR", "FORMAL_GATES", "ORACLE_OBLIGATION_BINDING",
    "PACKAGE_SCHEMA", "PAPER_SHA", "Room06FixedPackageError",
    "build_room06_fixed_package", "verify_room06_fixed_package",
]
