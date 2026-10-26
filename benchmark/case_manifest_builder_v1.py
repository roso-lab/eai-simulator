"""Assemble one production case manifest from audited upstream products.

This module is deliberately a narrow seam between authors' semantic-map and
runtime-roster products and :mod:`case_manifest_v1`.  It never invents task
entities, zones, robot identities, contract parameters, or evaluator facts.
The caller supplies an explicit binding specification; the upstream files are
only used for byte/digest verification and membership checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .case_input_audit_v1 import CaseInputAuditError, audit_case_inputs
from .case_manifest_v1 import (OBJECT_BINDINGS_SCHEMA, TASK_OBJECT_BINDINGS_SCHEMA,
                               CaseManifestError, bind_case_manifest, validate_task_object_bindings)
from .robot_resume.runtime_qualification_receipt import RuntimeReceiptError, validate_receipt


BUILDER_SCHEMA = "heterocoord.case-manifest-builder.v1"
_HEX = set("0123456789abcdef")


class CaseManifestBuildError(ValueError):
    """Raised when audited inputs cannot be bound into one real case."""


def _load_json(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseManifestBuildError(f"cannot read {name}: {path}") from exc
    if not isinstance(value, Mapping):
        raise CaseManifestBuildError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseManifestBuildError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(ch not in _HEX for ch in value):
        raise CaseManifestBuildError(f"{name} must be a lowercase SHA-256")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CaseManifestBuildError(f"cannot hash artifact: {path}") from exc


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _explicit_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise CaseManifestBuildError(f"{name} must be {qualifier} of IDs")
    if len(set(value)) != len(value):
        raise CaseManifestBuildError(f"{name} must not contain duplicate IDs")
    return list(value)


def _object_bindings(value: Any, *, scene_digest: str, entity_ids: list[str]) -> dict[str, Any]:
    """Normalize one explicit case-owned entity-to-USD binding book."""

    if not isinstance(value, Mapping):
        raise CaseManifestBuildError("binding.object_bindings must be an object")
    if value.get("schema") == TASK_OBJECT_BINDINGS_SCHEMA:
        try:
            return validate_task_object_bindings(value, scene_digest=scene_digest, entity_ids=entity_ids)
        except CaseManifestError as exc:
            raise CaseManifestBuildError(str(exc)) from exc
    if set(value) != {"schema", "scene_digest", "confirmation", "objects"}:
        raise CaseManifestBuildError("binding.object_bindings schema mismatch")
    if value.get("schema") != OBJECT_BINDINGS_SCHEMA:
        raise CaseManifestBuildError("unsupported binding.object_bindings schema")
    bound_scene_digest = _digest(value.get("scene_digest"), "binding.object_bindings.scene_digest")
    if bound_scene_digest != scene_digest:
        raise CaseManifestBuildError("binding.object_bindings.scene_digest does not match binding.scene_digest")

    confirmation = value.get("confirmation")
    if not isinstance(confirmation, Mapping):
        raise CaseManifestBuildError("binding.object_bindings.confirmation must be an object")
    if set(confirmation) != {"status", "confirmed_by", "confirmed_at", "source"}:
        raise CaseManifestBuildError("binding.object_bindings.confirmation schema mismatch")
    if confirmation.get("status") != "confirmed":
        raise CaseManifestBuildError("binding.object_bindings must be explicitly confirmed")
    normalized_confirmation = {"status": "confirmed"}
    for key in ("confirmed_by", "confirmed_at", "source"):
        normalized_confirmation[key] = _text(
            confirmation.get(key), f"binding.object_bindings.confirmation.{key}"
        )

    rows = value.get("objects")
    if not isinstance(rows, list) or not rows:
        raise CaseManifestBuildError("binding.object_bindings.objects must be a non-empty list")
    normalized_rows: list[dict[str, str]] = []
    seen_entities: set[str] = set()
    seen_paths: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {"entity_id", "prim_path"}:
            raise CaseManifestBuildError(f"binding.object_bindings.objects[{index}] schema mismatch")
        entity_id = _text(row.get("entity_id"), f"binding.object_bindings.objects[{index}].entity_id")
        prim_path = _text(row.get("prim_path"), f"binding.object_bindings.objects[{index}].prim_path")
        if not prim_path.startswith("/") or prim_path == "/" or "//" in prim_path or prim_path.endswith("/"):
            raise CaseManifestBuildError(
                f"binding.object_bindings.objects[{index}].prim_path must be an absolute USD prim path"
            )
        if entity_id in seen_entities:
            raise CaseManifestBuildError("binding.object_bindings contains duplicate entity_id values")
        if prim_path in seen_paths:
            raise CaseManifestBuildError("binding.object_bindings contains duplicate prim_path values")
        seen_entities.add(entity_id)
        seen_paths.add(prim_path)
        normalized_rows.append({"entity_id": entity_id, "prim_path": prim_path})
    if seen_entities != set(entity_ids):
        raise CaseManifestBuildError("binding.object_bindings must exactly cover binding.entity_ids")
    return {
        "schema": OBJECT_BINDINGS_SCHEMA,
        "scene_digest": bound_scene_digest,
        "confirmation": normalized_confirmation,
        "objects": normalized_rows,
    }


def _semantic_ids(root: Path, spec: Mapping[str, Any]) -> tuple[dict[str, Any], set[str], set[str]]:
    """Return scene fields and IDs explicitly present in semantic products."""

    manifest = _load_json(root / "manifest.json", "semantic map manifest")
    footprints = _load_json(root / "semantic-footprints.json", "semantic footprints")
    topology = _load_json(root / "topology.json", "ground topology")
    semantics = footprints.get("semantics")
    if not isinstance(semantics, Mapping):
        raise CaseManifestBuildError("semantic footprints have no explicit semantics object")

    entities: set[str] = set()
    for key in ("entities",):
        rows = semantics.get(key)
        if not isinstance(rows, list):
            raise CaseManifestBuildError(f"semantic semantics.{key} is missing or not a list")
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise CaseManifestBuildError(f"semantic semantics.{key}[{index}] is not an object")
            entities.add(_text(row.get("id"), f"semantic semantics.{key}[{index}].id"))

    regions: set[str] = set()
    rows = semantics.get("regions")
    if not isinstance(rows, list):
        raise CaseManifestBuildError("semantic semantics.regions is missing or not a list")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CaseManifestBuildError(f"semantic semantics.regions[{index}] is not an object")
        regions.add(_text(row.get("id"), f"semantic semantics.regions[{index}].id"))

    scene_id = _text(manifest.get("scene_key"), "manifest.scene_key")
    if footprints.get("scene_id") != scene_id or topology.get("scene_key") != scene_id:
        raise CaseManifestBuildError("semantic products disagree on scene identity")
    frame = _text(footprints.get("coordinate_frame"), "semantic-footprints.coordinate_frame")
    if topology.get("coordinate_frame") != frame:
        raise CaseManifestBuildError("semantic products disagree on coordinate frame")

    # Scene digest is an identity for the complete scene asset and must be
    # authored explicitly; only the map/topology bundle digests are computed
    # here from bytes that the input audit already verified.
    scene_digest = _digest(spec.get("scene_digest"), "binding.scene_digest")
    verified = {"manifest.json": _sha256(root / "manifest.json")}
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise CaseManifestBuildError("semantic map manifest.files is empty")
    for relative, expected in files.items():
        relative = _text(relative, "manifest.files path")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CaseManifestBuildError(f"semantic map artifact escapes root: {relative}") from exc
        actual = _sha256(path)
        if isinstance(expected, str) and len(expected) == 64 and set(expected) <= _HEX and actual != expected:
            raise CaseManifestBuildError(f"semantic map artifact digest mismatch: {relative}")
        verified[relative] = actual
    semantic_map_digest = _canonical_digest({"manifest": verified["manifest.json"], "files": verified})
    topology_digest = verified.get("topology.json") or _sha256(root / "topology.json")
    entity_ids = _explicit_list(spec.get("entity_ids"), "binding.entity_ids")
    zone_ids = _explicit_list(spec.get("zone_ids"), "binding.zone_ids", allow_empty=True)
    object_binding_value = spec.get("object_bindings")
    format_contract_version = spec.get("format_contract_version")
    if format_contract_version is not None:
        format_contract_version = _text(
            format_contract_version, "binding.format_contract_version"
        )
        if format_contract_version != "2.1":
            raise CaseManifestBuildError("unsupported binding.format_contract_version")
    requires_case_objects = format_contract_version == "2.1" or not entities
    object_binding = None
    if object_binding_value is not None:
        object_binding = _object_bindings(
            object_binding_value, scene_digest=scene_digest, entity_ids=entity_ids
        )
    elif requires_case_objects:
        raise CaseManifestBuildError(
            "binding.object_bindings is required for format contract v2.1 or a map without semantic entities"
        )
    unknown_entities = sorted(set(entity_ids) - entities) if object_binding is None else []
    unknown_zones = sorted(set(zone_ids) - regions)
    if unknown_entities:
        raise CaseManifestBuildError(f"binding.entity_ids not present in semantic map: {unknown_entities}")
    if unknown_zones:
        raise CaseManifestBuildError(f"binding.zone_ids not present in semantic map: {unknown_zones}")
    horizon = spec.get("horizon")
    tau_stable = spec.get("tau_stable")
    if type(horizon) is not int or horizon <= 0:
        raise CaseManifestBuildError("binding.horizon must be a positive integer")
    if type(tau_stable) is not int or tau_stable <= 0:
        raise CaseManifestBuildError("binding.tau_stable must be a positive integer")
    units = _text(spec.get("units"), "binding.units")
    scene = {
        "scene_id": scene_id,
        "scene_digest": scene_digest,
        "semantic_map_digest": semantic_map_digest,
        "topology_digest": topology_digest,
        "frame": frame,
        "units": units,
        "entity_ids": entity_ids,
        "zone_ids": zone_ids,
        "horizon": horizon,
        "tau_stable": tau_stable,
    }
    if object_binding is not None:
        scene["object_bindings"] = object_binding
    return scene, entities, regions


def _runtime_robots(pool_path: Path, spec: Mapping[str, Any], pool_digest: str) -> tuple[str, list[dict[str, str]]]:
    pool = _load_json(pool_path, "robot pool")
    # The v3.1 runtime roster is the authoritative deployable shape.  Keep
    # the legacy resume path below for diagnostic/backward compatibility, but
    # never downgrade a roster row into a runtime robot without an explicit
    # qualification receipt and instance-bound digests.
    if pool.get("schema_id") == "heterocoord.robot_runtime_roster":
        rows = pool.get("robots")
        if not isinstance(rows, list) or len(rows) != 27:
            raise CaseManifestBuildError("runtime roster must contain exactly 27 robots")
        requested = _explicit_list(spec.get("resume_ids"), "binding.resume_ids")
        by_id: dict[str, Mapping[str, Any]] = {}
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise CaseManifestBuildError(f"runtime roster robots[{index}] is not an object")
            instance_id = _text(row.get("instance_id"), f"runtime roster robots[{index}].instance_id")
            source_id = _text(row.get("source_resume_id"), f"runtime roster robots[{index}].source_resume_id")
            if instance_id in by_id or source_id in by_id:
                raise CaseManifestBuildError("runtime roster contains duplicate instance/source IDs")
            by_id[instance_id] = row
            by_id[source_id] = row
        result: list[dict[str, str]] = []
        for requested_id in requested:
            row = by_id.get(requested_id)
            if row is None:
                raise CaseManifestBuildError(f"binding.resume_ids not present in runtime roster: {[requested_id]}")
            qualification_receipt = row.get("runtime_qualification_receipt")
            if row.get("runtime_qualified") is not True or qualification_receipt is None:
                raise CaseManifestBuildError(
                    f"runtime roster {row['instance_id']} lacks a valid runtime qualification receipt"
                )
            if not isinstance(qualification_receipt, Mapping):
                raise CaseManifestBuildError(
                    f"runtime roster {row['instance_id']} qualification receipt must be embedded"
                )
            try:
                validate_receipt(qualification_receipt, roster_row=row)
            except (RuntimeReceiptError, TypeError, ValueError) as exc:
                raise CaseManifestBuildError(
                    f"runtime roster {row['instance_id']} qualification receipt is invalid: {exc}"
                ) from exc
            bindings = {
                "asset_digest": row.get("asset_binding", {}).get("asset_digest") if isinstance(row.get("asset_binding"), Mapping) else None,
                "capability_digest": row.get("capability_binding", {}).get("capability_digest") if isinstance(row.get("capability_binding"), Mapping) else None,
                "initial_state_digest": row.get("initial_state_binding", {}).get("initial_state_digest") if isinstance(row.get("initial_state_binding"), Mapping) else None,
            }
            result.append({"robot_id": row["instance_id"], **{key: _digest(value, f"{row['instance_id']}.{key}") for key, value in bindings.items()}})
        if len({row["robot_id"] for row in result}) != len(result):
            raise CaseManifestBuildError("selected runtime robot identities are not unique")
        pool_id = _text(spec.get("pool_id"), "binding.pool_id")
        return pool_id, result
    if pool.get("status") in {"review_draft_not_runtime_input", "review_only"}:
        raise CaseManifestBuildError("robot pool is review-only, not runtime input")
    rows = pool.get("resumes")
    if not isinstance(rows, list) or not rows:
        raise CaseManifestBuildError("robot pool resumes is empty")
    requested = _explicit_list(spec.get("resume_ids"), "binding.resume_ids")
    by_resume = {row.get("resume_id"): row for row in rows if isinstance(row, Mapping)}
    missing = sorted(set(requested) - set(by_resume))
    if missing:
        raise CaseManifestBuildError(f"binding.resume_ids not present in robot pool: {missing}")
    result: list[dict[str, str]] = []
    for resume_id in requested:
        row = by_resume[resume_id]
        identity = row.get("identity")
        deployment = row.get("deployment")
        artifacts = row.get("runtime_artifacts") or row.get("artifacts")
        if not isinstance(identity, Mapping) or not isinstance(deployment, Mapping):
            raise CaseManifestBuildError(f"robot resume {resume_id} lacks identity/deployment")
        if deployment.get("runtime_qualified") is not True:
            raise CaseManifestBuildError(f"robot resume {resume_id} is not runtime qualified")
        robot_id = _text(identity.get("robot_id"), f"{resume_id}.identity.robot_id")
        if not isinstance(artifacts, Mapping):
            raise CaseManifestBuildError(f"robot resume {resume_id} lacks runtime_artifacts")
        result.append({
            "robot_id": robot_id,
            "asset_digest": _digest(artifacts.get("asset_digest"), f"{resume_id}.asset_digest"),
            "capability_digest": _digest(artifacts.get("capability_digest"), f"{resume_id}.capability_digest"),
            "initial_state_digest": _digest(artifacts.get("initial_state_digest"), f"{resume_id}.initial_state_digest"),
        })
    if len({row["robot_id"] for row in result}) != len(result):
        raise CaseManifestBuildError("selected robot identities are not unique")
    pool_id = _text(spec.get("pool_id"), "binding.pool_id")
    return pool_id, result


def build_case_manifest(
    *,
    semantic_root: str | Path,
    robot_pool_path: str | Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Audit inputs and build a validated, digest-bound case manifest."""

    if not isinstance(binding, Mapping):
        raise CaseManifestBuildError("binding must be an object")
    try:
        audit = audit_case_inputs(semantic_root, robot_pool_path,
                                  semantic_requirements=binding.get("semantic_requirements"))
    except CaseInputAuditError as exc:
        raise CaseManifestBuildError(str(exc)) from exc
    if not audit.get("case_ready"):
        raise CaseManifestBuildError("case inputs are not ready: " + "; ".join(audit.get("failures", [])))
    root = Path(semantic_root).resolve()
    pool_path = Path(robot_pool_path).resolve()
    scene, _, _ = _semantic_ids(root, binding)
    pool_digest = _sha256(pool_path)
    pool_id, robots = _runtime_robots(pool_path, binding, pool_digest)
    contract = binding.get("contract")
    if not isinstance(contract, Mapping):
        raise CaseManifestBuildError("binding.contract must be an object")
    contract = dict(contract)
    for key in ("contract_digest", "oracle_id", "contract_revision"):
        if key.endswith("digest"):
            _digest(contract.get(key), f"binding.contract.{key}")
        else:
            _text(contract.get(key), f"binding.contract.{key}")
    replay = binding.get("replay")
    evaluator = binding.get("evaluator")
    if not isinstance(replay, Mapping) or not isinstance(evaluator, Mapping):
        raise CaseManifestBuildError("binding.replay and binding.evaluator must be objects")
    for key in ("serializer_version", "rng_stream_key", "checkpoint_schema"):
        _text(replay.get(key), f"binding.replay.{key}")
    for key in ("evaluator_id", "evaluator_version"):
        _text(evaluator.get(key), f"binding.evaluator.{key}")
    manifest = {
        "schema": "heterocoord.case-manifest.v1",
        "manifest_digest": "0" * 64,
        "case_id": _text(binding.get("case_id"), "binding.case_id"),
        "task_id": _text(binding.get("task_id"), "binding.task_id"),
        "anchor_id": _text(binding.get("anchor_id"), "binding.anchor_id"),
        "domain": _text(binding.get("domain"), "binding.domain"),
        "relation_signature": _explicit_list(binding.get("relation_signature"), "binding.relation_signature"),
        "base_instance_id": _text(binding.get("base_instance_id"), "binding.base_instance_id"),
        "scene": scene,
        "robot_pool": {"pool_id": pool_id, "roster_digest": pool_digest, "robots": robots},
        "contract": contract,
        "catalog_digest": _digest(binding.get("catalog_digest"), "binding.catalog_digest"),
        "plan_digest": _digest(binding.get("plan_digest"), "binding.plan_digest"),
        "replay": dict(replay),
        "evaluator": dict(evaluator),
    }
    try:
        return bind_case_manifest(manifest)
    except CaseManifestError as exc:
        raise CaseManifestBuildError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-root", required=True, type=Path)
    parser.add_argument("--robot-pool", required=True, type=Path)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        binding = _load_json(args.binding, "binding specification")
        result = build_case_manifest(semantic_root=args.semantic_root, robot_pool_path=args.robot_pool, binding=binding)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (CaseManifestBuildError, OSError) as exc:
        parser.error(str(exc))
    return 0


__all__ = ["BUILDER_SCHEMA", "CaseManifestBuildError", "build_case_manifest"]


if __name__ == "__main__":
    raise SystemExit(main())
