"""Strict identity contract for one real semantic benchmark case.

The manifest is the hand-off boundary between scene/roster authors and the
evaluator.  It contains references and digests only; it does not infer
semantics from a map or a robot asset.  A receipt may be normalized without a
manifest for diagnostic fixtures, but production case receipts should provide
one so provenance is bound into the result package.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping


CASE_MANIFEST_SCHEMA = "heterocoord.case-manifest.v1"
OBJECT_BINDINGS_SCHEMA = "heterocoord.case-object-bindings.v1"
TASK_OBJECT_BINDINGS_SCHEMA = "heterocoord.task-object-bindings.v1"
LOGICAL_TASK_OBJECT_BINDINGS_SCHEMA = "heterocoord.logical-task-object-bindings.v1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PHYSICAL_METADATA_KEYS = {
    "attachment",
    "attachment_ref",
    "attachments",
    "body_path",
    "geometry",
    "geometry_ref",
    "physics_handle",
    "prim",
    "prim_path",
    "prim_paths",
    "readback",
    "rigid_body",
    "rigid_body_path",
    "size_xyz_m",
    "usd",
    "usd_handle",
    "usd_prim",
    "usd_prim_path",
    "usd_path",
}
_PHYSICAL_METADATA_KEY_STEMS = {
    "attachment",
    "bodypath",
    "collider",
    "collisiongeometry",
    "collisionshape",
    "geometry",
    "meshpath",
    "physicshandle",
    "primpath",
    "readback",
    "rigidbody",
    "sizexyzm",
    "usdhandle",
    "usdpath",
    "usdprim",
}
_TOP_KEYS = {
    "schema", "manifest_digest", "case_id", "task_id", "anchor_id", "domain",
    "relation_signature", "base_instance_id", "scene", "robot_pool", "contract",
    "catalog_digest", "plan_digest", "replay", "evaluator",
}


class CaseManifestError(ValueError):
    """Raised when a real case identity contract is incomplete or tampered."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseManifestError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if not _DIGEST_RE.fullmatch(value):
        raise CaseManifestError(f"{name} must be a lowercase SHA-256")
    return value


def _exact(value: Mapping[str, Any], required: set[str], name: str) -> None:
    if set(value) != required:
        raise CaseManifestError(f"{name} schema mismatch")


def _digest_body(manifest: Mapping[str, Any]) -> str:
    body = dict(manifest)
    body.pop("manifest_digest", None)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _validate_logical_metadata(value: Any, name: str, active: set[int]) -> None:
    """Require finite canonical-JSON state and reject physical object handles."""

    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise CaseManifestError(f"{name} must contain only finite JSON values")
        return
    if not isinstance(value, (Mapping, list)):
        raise CaseManifestError(f"{name} must contain only canonical JSON values")
    identity = id(value)
    if identity in active:
        raise CaseManifestError(f"{name} must not be cyclic")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CaseManifestError(f"{name} keys must be strings")
                normalized_key = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip()).lower(),
                ).strip("_")
                compact_key = normalized_key.replace("_", "")
                if normalized_key in _PHYSICAL_METADATA_KEYS or any(
                    compact_key == stem
                    or compact_key.startswith(stem)
                    or compact_key.endswith(stem)
                    for stem in _PHYSICAL_METADATA_KEY_STEMS
                ):
                    raise CaseManifestError(
                        f"{name} must not contain physical dependency key {key!r}"
                    )
                _validate_logical_metadata(item, f"{name}.{key}", active)
        else:
            for index, item in enumerate(value):
                _validate_logical_metadata(item, f"{name}[{index}]", active)
    finally:
        active.remove(identity)


def _validate_object_bindings(scene: Mapping[str, Any]) -> None:
    """Validate case-owned USD identities without claiming asset existence.

    The structure stays inside ``scene`` so its confirmation and provenance
    are covered by ``manifest_digest``.  A syntactically valid prim path is
    only an asserted case binding; prim existence remains runtime evidence.
    """

    value = scene["object_bindings"]
    if not isinstance(value, Mapping):
        raise CaseManifestError("scene.object_bindings must be an object")
    if value.get("schema") == TASK_OBJECT_BINDINGS_SCHEMA:
        validate_task_object_bindings(value, scene_digest=scene["scene_digest"], entity_ids=scene["entity_ids"])
        return
    if value.get("schema") == LOGICAL_TASK_OBJECT_BINDINGS_SCHEMA:
        validate_logical_task_object_bindings(
            value,
            scene_digest=scene["scene_digest"],
            entity_ids=scene["entity_ids"],
        )
        return
    _exact(value, {"schema", "scene_digest", "confirmation", "objects"}, "scene.object_bindings")
    if value["schema"] != OBJECT_BINDINGS_SCHEMA:
        raise CaseManifestError("unsupported scene.object_bindings schema")
    _digest(value["scene_digest"], "scene.object_bindings.scene_digest")
    if value["scene_digest"] != scene["scene_digest"]:
        raise CaseManifestError("scene.object_bindings.scene_digest does not match scene.scene_digest")

    confirmation = value["confirmation"]
    if not isinstance(confirmation, Mapping):
        raise CaseManifestError("scene.object_bindings.confirmation must be an object")
    _exact(
        confirmation,
        {"status", "confirmed_by", "confirmed_at", "source"},
        "scene.object_bindings.confirmation",
    )
    if confirmation["status"] != "confirmed":
        raise CaseManifestError("scene.object_bindings must be explicitly confirmed")
    for key in ("confirmed_by", "confirmed_at", "source"):
        _text(confirmation[key], f"scene.object_bindings.confirmation.{key}")

    objects = value["objects"]
    if not isinstance(objects, list) or not objects:
        raise CaseManifestError("scene.object_bindings.objects must be a non-empty list")
    entity_ids: list[str] = []
    prim_paths: list[str] = []
    for index, row in enumerate(objects):
        if not isinstance(row, Mapping):
            raise CaseManifestError(f"scene.object_bindings.objects[{index}] must be an object")
        _exact(row, {"entity_id", "prim_path"}, f"scene.object_bindings.objects[{index}]")
        entity_id = _text(row["entity_id"], f"scene.object_bindings.objects[{index}].entity_id")
        prim_path = _text(row["prim_path"], f"scene.object_bindings.objects[{index}].prim_path")
        if not prim_path.startswith("/") or prim_path == "/" or "//" in prim_path or prim_path.endswith("/"):
            raise CaseManifestError(
                f"scene.object_bindings.objects[{index}].prim_path must be an absolute USD prim path"
            )
        entity_ids.append(entity_id)
        prim_paths.append(prim_path)
    if len(set(entity_ids)) != len(entity_ids):
        raise CaseManifestError("scene.object_bindings entity_id values must be unique")
    if len(set(prim_paths)) != len(prim_paths):
        raise CaseManifestError("scene.object_bindings prim_path values must be unique")
    if set(entity_ids) != set(scene["entity_ids"]):
        raise CaseManifestError("scene.object_bindings must exactly cover scene.entity_ids")


def validate_task_object_bindings(value: Mapping[str, Any], *, scene_digest: str,
                                  entity_ids: list[str]) -> dict[str, Any]:
    """V3 task-generated identities: no scene-prop prim or human confirmation.

    Definition provenance is frozen here; existence is established by the
    evaluator's task-setup receipt, not by this structural validation.
    Scene-prop reuse continues through the explicit confirmed legacy binding.
    """
    _exact(value, {"schema", "scene_digest", "objects"}, "task object bindings")
    if value["schema"] != TASK_OBJECT_BINDINGS_SCHEMA:
        raise CaseManifestError("unsupported task object bindings schema")
    _digest(value["scene_digest"], "task object bindings.scene_digest")
    if value["scene_digest"] != scene_digest:
        raise CaseManifestError("task object bindings scene digest mismatch")
    rows = value["objects"]
    if not isinstance(rows, list) or not rows:
        raise CaseManifestError("task object bindings.objects must be non-empty")
    ids = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise CaseManifestError("task object binding row must be an object")
        _exact(row, {"entity_id", "origin", "definition_ref", "definition_digest"}, "task object")
        ids.append(_text(row["entity_id"], "task object.entity_id"))
        if row["origin"] != "task_generated":
            raise CaseManifestError("task object origin must be task_generated")
        _text(row["definition_ref"], "task object.definition_ref")
        _digest(row["definition_digest"], "task object.definition_digest")
    if len(set(ids)) != len(ids) or set(ids) != set(entity_ids):
        raise CaseManifestError("task objects must exactly cover entity_ids without duplicates")
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def validate_logical_task_object_bindings(
    value: Mapping[str, Any], *, scene_digest: str, entity_ids: list[str]
) -> dict[str, Any]:
    """Validate task objects represented only by benchmark logical state.

    Logical objects have no USD prim, attachment, physics-readback, or human
    confirmation identity.  The semantic endpoints and positive finite mass
    are authored facts covered by the enclosing case-manifest digest.
    """

    _exact(value, {"schema", "scene_digest", "objects"}, "logical task object bindings")
    if value["schema"] != LOGICAL_TASK_OBJECT_BINDINGS_SCHEMA:
        raise CaseManifestError("unsupported logical task object bindings schema")
    _digest(value["scene_digest"], "logical task object bindings.scene_digest")
    if value["scene_digest"] != scene_digest:
        raise CaseManifestError("logical task object bindings scene digest mismatch")
    rows = value["objects"]
    if not isinstance(rows, list) or not rows:
        raise CaseManifestError("logical task object bindings.objects must be non-empty")
    ids: list[str] = []
    required = {
        "entity_id",
        "category",
        "metadata",
        "initial_semantic_point",
        "target_semantic_point",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CaseManifestError(
                f"logical task object bindings.objects[{index}] must be an object"
            )
        _exact(row, required, f"logical task object bindings.objects[{index}]")
        ids.append(
            _text(
                row["entity_id"],
                f"logical task object bindings.objects[{index}].entity_id",
            )
        )
        _text(row["category"], f"logical task object bindings.objects[{index}].category")
        _text(
            row["initial_semantic_point"],
            f"logical task object bindings.objects[{index}].initial_semantic_point",
        )
        _text(
            row["target_semantic_point"],
            f"logical task object bindings.objects[{index}].target_semantic_point",
        )
        metadata = row["metadata"]
        if not isinstance(metadata, Mapping):
            raise CaseManifestError(
                f"logical task object bindings.objects[{index}].metadata must be an object"
            )
        metadata_name = f"logical task object bindings.objects[{index}].metadata"
        mass_kg = metadata.get("mass_kg")
        if mass_kg is not None and (
            isinstance(mass_kg, bool)
            or not isinstance(mass_kg, (int, float))
            or not math.isfinite(float(mass_kg))
            or mass_kg <= 0
        ):
            raise CaseManifestError(
                f"{metadata_name}.mass_kg "
                "must be a positive finite number"
            )
        minimum_holders = metadata.get("minimum_holders")
        if minimum_holders is not None and (
            type(minimum_holders) is not int or minimum_holders <= 0
        ):
            raise CaseManifestError(
                f"{metadata_name}.minimum_holders must be a positive integer"
            )
        _validate_logical_metadata(metadata, metadata_name, set())
    if len(set(ids)) != len(ids) or set(ids) != set(entity_ids):
        raise CaseManifestError(
            "logical task objects must exactly cover entity_ids without duplicates"
        )
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def validate_case_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached, normalized case manifest mapping."""

    if not isinstance(value, Mapping):
        raise CaseManifestError("case manifest must be an object")
    if set(value) != _TOP_KEYS:
        raise CaseManifestError("case manifest schema mismatch")
    if value["schema"] != CASE_MANIFEST_SCHEMA:
        raise CaseManifestError("unsupported case manifest schema")
    _digest(value["manifest_digest"], "manifest_digest")
    if value["manifest_digest"] != _digest_body(value):
        raise CaseManifestError("manifest_digest does not bind the case manifest")
    for key in ("case_id", "task_id", "anchor_id", "domain", "base_instance_id"):
        _text(value[key], key)
    signature = value["relation_signature"]
    if not isinstance(signature, list) or not signature or any(not isinstance(item, str) or not item.strip() for item in signature):
        raise CaseManifestError("relation_signature must be a non-empty list")
    _digest(value["catalog_digest"], "catalog_digest")
    _digest(value["plan_digest"], "plan_digest")

    scene = value["scene"]
    if not isinstance(scene, Mapping):
        raise CaseManifestError("scene must be an object")
    legacy_scene_keys = {
        "scene_id", "scene_digest", "semantic_map_digest", "topology_digest",
        "frame", "units", "entity_ids", "zone_ids", "horizon", "tau_stable",
    }
    if set(scene) not in (legacy_scene_keys, legacy_scene_keys | {"object_bindings"}):
        raise CaseManifestError("scene schema mismatch")
    _text(scene["scene_id"], "scene.scene_id")
    for key in ("scene_digest", "semantic_map_digest", "topology_digest"):
        _digest(scene[key], f"scene.{key}")
    _text(scene["frame"], "scene.frame")
    _text(scene["units"], "scene.units")
    for key in ("entity_ids", "zone_ids"):
        ids = scene[key]
        if not isinstance(ids, list) or any(not isinstance(item, str) or not item.strip() for item in ids) or len(set(ids)) != len(ids):
            raise CaseManifestError(f"scene.{key} must be a unique list of IDs")
    if "object_bindings" in scene:
        _validate_object_bindings(scene)
    if type(scene["horizon"]) is not int or scene["horizon"] <= 0:
        raise CaseManifestError("scene.horizon must be a positive integer")
    if type(scene["tau_stable"]) is not int or scene["tau_stable"] <= 0:
        raise CaseManifestError("scene.tau_stable must be a positive integer")

    pool = value["robot_pool"]
    if not isinstance(pool, Mapping):
        raise CaseManifestError("robot_pool must be an object")
    _exact(pool, {"pool_id", "roster_digest", "robots"}, "robot_pool")
    _text(pool["pool_id"], "robot_pool.pool_id")
    _digest(pool["roster_digest"], "robot_pool.roster_digest")
    robots = pool["robots"]
    if not isinstance(robots, list) or not robots:
        raise CaseManifestError("robot_pool.robots must be non-empty")
    robot_ids = []
    for index, robot in enumerate(robots):
        if not isinstance(robot, Mapping):
            raise CaseManifestError(f"robot_pool.robots[{index}] must be an object")
        _exact(robot, {"robot_id", "asset_digest", "capability_digest", "initial_state_digest"}, f"robot_pool.robots[{index}]")
        robot_ids.append(_text(robot["robot_id"], f"robot_pool.robots[{index}].robot_id"))
        for key in ("asset_digest", "capability_digest", "initial_state_digest"):
            _digest(robot[key], f"robot_pool.robots[{index}].{key}")
    if len(set(robot_ids)) != len(robot_ids):
        raise CaseManifestError("robot_pool robot_id values must be unique")

    contract = value["contract"]
    if not isinstance(contract, Mapping):
        raise CaseManifestError("contract must be an object")
    _exact(contract, {"contract_digest", "oracle_id", "contract_revision"}, "contract")
    _digest(contract["contract_digest"], "contract.contract_digest")
    for key in ("oracle_id", "contract_revision"):
        _text(contract[key], f"contract.{key}")

    replay = value["replay"]
    if not isinstance(replay, Mapping):
        raise CaseManifestError("replay must be an object")
    _exact(replay, {"serializer_version", "rng_stream_key", "checkpoint_schema"}, "replay")
    for key in replay:
        _text(replay[key], f"replay.{key}")

    evaluator = value["evaluator"]
    if not isinstance(evaluator, Mapping):
        raise CaseManifestError("evaluator must be an object")
    _exact(evaluator, {"evaluator_id", "evaluator_version"}, "evaluator")
    for key in evaluator:
        _text(evaluator[key], f"evaluator.{key}")
    return json.loads(json.dumps(value, ensure_ascii=False))


def bind_case_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached manifest with its canonical digest filled in."""

    body = dict(value)
    body.pop("manifest_digest", None)
    body["manifest_digest"] = _digest_body(body)
    return validate_case_manifest(body)


__all__ = [
    "CASE_MANIFEST_SCHEMA", "LOGICAL_TASK_OBJECT_BINDINGS_SCHEMA",
    "OBJECT_BINDINGS_SCHEMA", "TASK_OBJECT_BINDINGS_SCHEMA", "CaseManifestError",
    "bind_case_manifest", "validate_case_manifest",
    "validate_logical_task_object_bindings", "validate_task_object_bindings",
]
