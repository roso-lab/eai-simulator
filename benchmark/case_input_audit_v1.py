"""Fail-closed audit for semantic-map and robot-pool inputs before case binding.

The existing ``case_manifest_v1`` binds digests for a case.  This module audits
the referenced material before that binding: a digest is not treated as proof
that the semantic content or runtime roster is usable.  It deliberately does
not infer missing task facts, robot identities, or qualification from catalog
metadata.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


AUDIT_SCHEMA = "heterocoord.case-input-audit.v1"
SEMANTIC_FORMAT_CONTRACT = "2.1"
_DIGEST_RE = set("0123456789abcdef")
_SEMANTIC_COLLECTIONS = ("regions", "entities", "interaction_poses", "routes")
_SEMANTIC_REQUIREMENT_KEYS = frozenset((*_SEMANTIC_COLLECTIONS, "semantic_points"))


class CaseInputAuditError(ValueError):
    """Raised for malformed input material or an unsafe artifact reference."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseInputAuditError(f"{name} must be non-empty text")
    return value


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise CaseInputAuditError(f"artifact is not a regular file: {path}")
    return _sha256_bytes(path.read_bytes())


def _load_json(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseInputAuditError(f"cannot read {name}: {path}") from exc
    if not isinstance(value, Mapping):
        raise CaseInputAuditError(f"{name} must be an object")
    return value


def _relative_file(root: Path, relative: Any, name: str) -> Path:
    rel = _text(relative, name)
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CaseInputAuditError(f"{name} escapes artifact root") from exc
    return candidate


def _digest(value: Any, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(ch not in _DIGEST_RE for ch in text):
        raise CaseInputAuditError(f"{name} must be a lowercase SHA-256")
    return text


def _project_path_for_revision(root: Path) -> Path:
    """Resolve the project ledger that owns a current/revision bundle."""

    candidates = [root.parent / "project.json"]
    if root.parent.name == "revisions":
        candidates.insert(0, root.parent.parent / "project.json")
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise CaseInputAuditError(
        "semantic map revision has no owning project.json for save_id chain"
    )


def _required_names(
    requirements: Mapping[str, Iterable[str]] | None,
) -> dict[str, tuple[str, ...]]:
    """Validate an anchor's explicit semantic requirements without inferring any."""

    if requirements is None:
        return {key: () for key in _SEMANTIC_REQUIREMENT_KEYS}
    if not isinstance(requirements, Mapping):
        raise CaseInputAuditError("semantic_requirements must be an object")
    unknown = sorted(set(requirements) - _SEMANTIC_REQUIREMENT_KEYS)
    if unknown:
        raise CaseInputAuditError(
            f"semantic_requirements has unsupported collections: {unknown}"
        )
    normalized: dict[str, tuple[str, ...]] = {}
    for key in _SEMANTIC_REQUIREMENT_KEYS:
        value = requirements.get(key, ())
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise CaseInputAuditError(
                f"semantic_requirements.{key} must be a list of names"
            )
        names = tuple(value)
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise CaseInputAuditError(
                f"semantic_requirements.{key} must contain non-empty text"
            )
        if len(set(names)) != len(names):
            raise CaseInputAuditError(
                f"semantic_requirements.{key} must not contain duplicates"
            )
        normalized[key] = names
    return normalized


def _record_names(rows: list[Any], collection: str, failures: list[str]) -> set[str]:
    """Collect authored IDs/labels for exact anchor-requirement matching."""

    names: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            failures.append(f"semantic semantics.{collection}[{index}] is not an object")
            continue
        for field in ("id", "label"):
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                names.add(value)
    return names


def _navigation_nodes(
    root: Path,
    manifest: Mapping[str, Any],
    scene_key: str,
    frame: Any,
    failures: list[str],
) -> dict[str, set[str]]:
    """Load every declared navigation product and return its authored node IDs."""

    products = manifest.get("navigation_products")
    if not isinstance(products, Mapping) or not products:
        failures.append("manifest.navigation_products is empty")
        return {}
    result: dict[str, set[str]] = {}
    for domain, relative in products.items():
        if not isinstance(domain, str) or not domain.strip():
            failures.append("manifest.navigation_products has an invalid domain")
            continue
        try:
            path = _relative_file(root, relative, f"navigation_products[{domain}]")
            topology = _load_json(path, f"{domain} topology")
        except CaseInputAuditError as exc:
            failures.append(str(exc))
            continue
        if topology.get("schema") != "eai.navigation.topology":
            failures.append(f"{domain} topology schema mismatch")
        if topology.get("scene_key") != scene_key:
            failures.append(f"{domain} topology scene identity mismatch")
        if topology.get("coordinate_frame") != frame:
            failures.append(f"{domain} topology coordinate frame mismatch")
        if topology.get("domain") != domain:
            failures.append(f"{domain} topology domain mismatch")
        nodes = topology.get("nodes")
        edges = topology.get("edges")
        if not isinstance(nodes, list) or not nodes:
            failures.append(f"{domain} topology.nodes is empty")
            nodes = []
        if not isinstance(edges, list) or not edges:
            failures.append(f"{domain} topology.edges is empty")
        node_ids: set[str] = set()
        for index, node in enumerate(nodes):
            node_id = node.get("id") if isinstance(node, Mapping) else None
            if not isinstance(node_id, str) or not node_id.strip():
                failures.append(f"{domain} topology.nodes[{index}].id is invalid")
            elif node_id in node_ids:
                failures.append(f"{domain} topology node id is duplicated: {node_id}")
            else:
                node_ids.add(node_id)
        result[domain] = node_ids
    return result


def audit_semantic_map(
    root: str | Path,
    *,
    semantic_requirements: Mapping[str, Iterable[str]] | None = None,
    require_task_entities: bool = False,
    require_interaction_poses: bool = False,
) -> dict[str, Any]:
    """Audit one ``eai.semantic_map.revision`` bundle without mutating it.

    The returned ``case_ready`` flag is false whenever required semantic
    products are absent, even if all listed bytes have valid hashes.
    """

    root_path = Path(root).resolve()
    manifest_path = root_path / "manifest.json"
    manifest = _load_json(manifest_path, "semantic map manifest")
    project_path = _project_path_for_revision(root_path)
    project = _load_json(project_path, "semantic map project")
    if manifest.get("schema") != "eai.semantic_map.revision":
        raise CaseInputAuditError("unsupported semantic map manifest schema")
    if manifest.get("version") != 2:
        raise CaseInputAuditError("unsupported semantic map manifest version")
    if project.get("schema") != "eai.semantic_map.project" or project.get("version") != 2:
        raise CaseInputAuditError("unsupported semantic map project schema/version")
    scene_key = _text(manifest.get("scene_key"), "manifest.scene_key")
    manifest_revision = _text(manifest.get("revision"), "manifest.revision")
    project_save_id = _text(project.get("save_id"), "project.save_id")
    requirements = _required_names(semantic_requirements)
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise CaseInputAuditError("manifest.files must be a non-empty mapping")

    verified_files: dict[str, str] = {}
    failures: list[str] = []
    for label, expected in files.items():
        path = _relative_file(root_path, expected if isinstance(expected, str) and "/" in expected else label, f"files[{label}]")
        # The manifest convention stores digest values under labels; paths are
        # the labels themselves in the current revision format.
        if isinstance(expected, str) and len(expected) == 64 and set(expected) <= _DIGEST_RE:
            path = _relative_file(root_path, label, f"files[{label}]")
            actual = _sha256_file(path)
            if actual != expected:
                failures.append(f"file digest mismatch: {label}")
            verified_files[label] = actual
        else:
            verified_files[label] = _sha256_file(path)

    footprints_path = root_path / "semantic-footprints.json"
    topology_path = root_path / "topology.json"
    points_path = root_path / "semantic-points.json"
    footprints = _load_json(footprints_path, "semantic footprints")
    topology = _load_json(topology_path, "ground topology")
    points = _load_json(points_path, "semantic points")
    if footprints.get("schema") != "eai.semantic_footprints":
        failures.append("semantic-footprints schema mismatch")
    if topology.get("schema") != "eai.navigation.topology":
        failures.append("topology schema mismatch")
    if points.get("schema") != "eai.semantic_points":
        failures.append("semantic-points schema mismatch")
    if (
        project.get("scene_key") != scene_key
        or footprints.get("scene_id") != scene_key
        or topology.get("scene_key") != scene_key
        or points.get("scene_key") != scene_key
    ):
        failures.append("semantic products disagree on scene identity")
    frame = footprints.get("coordinate_frame")
    if not isinstance(frame, str) or not frame.strip():
        failures.append("semantic footprints have no coordinate frame")
    if topology.get("coordinate_frame") != frame:
        failures.append("topology coordinate frame mismatch")
    if points.get("coordinate_frame") != frame or project.get("coordinate_frame") != frame:
        failures.append("semantic point/project coordinate frame mismatch")

    footprint_revision = footprints.get("project_revision")
    if project_save_id != manifest_revision or footprint_revision != project_save_id:
        failures.append(
            "save_id chain mismatch: project.save_id, manifest.revision, and "
            "semantic-footprints.project_revision must match"
        )
    current_revision = project.get("current_revision")
    try:
        current_path = _relative_file(
            project_path.parent, current_revision, "project.current_revision"
        )
    except CaseInputAuditError as exc:
        failures.append(str(exc))
        current_path = None
    if current_path is not None and current_path != root_path:
        failures.append("project.current_revision does not select audited revision")
    project_binding_digest = project.get("semantic_binding_digest")
    manifest_binding_digest = manifest.get("semantic_binding_digest")
    if (
        not isinstance(project_binding_digest, str)
        or not project_binding_digest.strip()
        or project_binding_digest != manifest_binding_digest
    ):
        failures.append("semantic binding digest chain mismatch")

    navigation_nodes = _navigation_nodes(root_path, manifest, scene_key, frame, failures)
    semantics = footprints.get("semantics")
    if not isinstance(semantics, Mapping):
        failures.append("semantic footprints have no semantics object")
        semantics = {}
    authored_names: dict[str, set[str]] = {}
    for key in _SEMANTIC_COLLECTIONS:
        value = semantics.get(key)
        if not isinstance(value, list):
            failures.append(f"semantic semantics.{key} is missing or not a list")
            value = []
        authored_names[key] = _record_names(value, key, failures)
        missing = sorted(set(requirements[key]) - authored_names[key])
        if missing:
            failures.append(f"required semantic {key} are missing: {missing}")
    # Backward-compatible explicit demands remain available, but an empty
    # footprints collection is not itself a v2.1 failure.  The defaults no
    # longer infer that every anchor needs all four collections.
    if require_task_entities:
        for key in ("regions", "entities", "routes"):
            if not authored_names[key]:
                failures.append(f"semantic semantics.{key} is empty but explicitly required")
    if require_interaction_poses and not authored_names["interaction_poses"]:
        failures.append("semantic semantics.interaction_poses is empty but explicitly required")

    point_rows = points.get("points")
    if not isinstance(point_rows, list):
        failures.append("semantic-points.points is missing or not a list")
        point_rows = []
    elif not point_rows:
        failures.append("semantic-points.points is empty")
    point_ids: set[str] = set()
    point_labels: set[str] = set()
    for index, point in enumerate(point_rows):
        if not isinstance(point, Mapping):
            failures.append(f"semantic-points.points[{index}] is not an object")
            continue
        point_id = point.get("id")
        label = point.get("label")
        if not isinstance(point_id, str) or not point_id.strip():
            failures.append(f"semantic-points.points[{index}].id is empty")
        elif point_id in point_ids:
            failures.append(f"semantic point id is duplicated: {point_id}")
        else:
            point_ids.add(point_id)
        if not isinstance(label, str) or not label.strip():
            failures.append(f"semantic-points.points[{index}].label is empty")
        elif label in point_labels:
            failures.append(f"semantic point label is duplicated: {label}")
        else:
            point_labels.add(label)
        bindings = point.get("navigation_bindings")
        if not isinstance(bindings, list) or not bindings:
            failures.append(
                f"semantic-points.points[{index}].navigation_bindings is empty"
            )
            continue
        for binding_index, binding in enumerate(bindings):
            domain = binding.get("domain") if isinstance(binding, Mapping) else None
            node_id = binding.get("node_id") if isinstance(binding, Mapping) else None
            if not isinstance(domain, str) or not domain.strip() or not isinstance(node_id, str) or not node_id.strip():
                failures.append(
                    f"semantic-points.points[{index}].navigation_bindings[{binding_index}] is invalid"
                )
            elif domain not in navigation_nodes or node_id not in navigation_nodes[domain]:
                failures.append(
                    f"semantic point navigation binding is unresolved: {domain}/{node_id}"
                )
    missing_points = sorted(set(requirements["semantic_points"]) - point_labels)
    if missing_points:
        failures.append(f"required semantic points are missing: {missing_points}")

    # This summary is descriptive only.  It exposes the material needed by a
    # later case-manifest assembler without inferring task semantics or
    # inventing IDs when the upstream products are incomplete.
    semantic_summary = {
        "scene_key": scene_key,
        "coordinate_frame": frame if isinstance(frame, str) else None,
        "meters_per_unit": topology.get("meters_per_unit"),
        "up_axis": topology.get("up_axis"),
        "counts": {
            key: len(semantics.get(key, [])) if isinstance(semantics.get(key), list) else None
            for key in ("regions", "entities", "interaction_poses", "routes")
        },
        "semantic_point_count": len(point_rows),
        "required_semantics": {
            key: list(requirements[key]) for key in sorted(requirements)
        },
        "topology": {
            "node_count": len(topology.get("nodes", [])) if isinstance(topology.get("nodes"), list) else None,
            "edge_count": len(topology.get("edges", [])) if isinstance(topology.get("edges"), list) else None,
        },
    }

    return {
        "schema": AUDIT_SCHEMA,
        "kind": "semantic_map",
        "format_contract_version": SEMANTIC_FORMAT_CONTRACT,
        "scene_key": scene_key,
        "manifest_revision": manifest_revision,
        "manifest_path": str(manifest_path),
        "project_path": str(project_path),
        "version_chain": {
            "project_current_revision": current_revision,
            "project_save_id": project_save_id,
            "manifest_revision": manifest_revision,
            "footprints_project_revision": footprint_revision,
        },
        "verified_files": verified_files,
        "semantic_summary": semantic_summary,
        "case_ready": not failures,
        "failures": failures,
    }


def audit_robot_pool(path: str | Path) -> dict[str, Any]:
    """Audit a runtime roster source, rejecting review-only pool drafts."""

    pool_path = Path(path).resolve()
    pool = _load_json(pool_path, "robot pool")
    failures: list[str] = []
    if pool.get("schema_id") == "heterocoord.robot_runtime_roster":
        rows = pool.get("robots")
        if not isinstance(rows, list) or len(rows) != 27:
            failures.append("runtime roster must contain exactly 27 robots")
            rows = rows if isinstance(rows, list) else []
        bound_count = 0
        qualified_count = 0
        from .robot_resume.runtime_qualification_receipt import RuntimeReceiptError, validate_receipt
        seen_instances: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                failures.append(f"robots[{index}] is not an object")
                continue
            instance_id = row.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id.strip() or instance_id in seen_instances:
                failures.append(f"robots[{index}].instance_id is missing or duplicated")
            else:
                seen_instances.add(instance_id)
                bound_count += 1
            if row.get("runtime_qualified") is not True:
                failures.append(f"robots[{index}] is not runtime qualified")
                continue
            try:
                validate_receipt(row.get("runtime_qualification_receipt"), roster_row=row)
            except (RuntimeReceiptError, TypeError, ValueError) as exc:
                failures.append(f"robots[{index}] qualification receipt invalid: {exc}")
                continue
            qualified_count += 1
        return {
            "schema": AUDIT_SCHEMA,
            "kind": "robot_pool",
            "pool_path": str(pool_path),
            "pool_digest": _sha256_file(pool_path),
            "resume_count": len(rows),
            "roster_summary": {"status": "runtime_roster", "bound_identity_count": bound_count, "runtime_qualified_count": qualified_count},
            "case_ready": not failures,
            "failures": failures,
        }
    if pool.get("schema_id") != "heterocoord.robot_pool_resume_review":
        failures.append("robot pool schema mismatch")
    if pool.get("status") == "review_draft_not_runtime_input":
        failures.append("robot pool is review-only, not runtime input")
    resumes = pool.get("resumes")
    if not isinstance(resumes, list) or not resumes:
        failures.append("robot pool resumes is empty")
        resumes = []
    for index, row in enumerate(resumes):
        identity = row.get("identity") if isinstance(row, Mapping) else None
        deployment = row.get("deployment") if isinstance(row, Mapping) else None
        if not isinstance(identity, Mapping):
            failures.append(f"resumes[{index}].identity missing")
            continue
        for key in ("robot_id", "configuration_id", "generation"):
            if not isinstance(identity.get(key), str) or not identity[key].strip():
                failures.append(f"resumes[{index}].identity.{key} is unbound")
        if not isinstance(deployment, Mapping) or deployment.get("runtime_qualified") is not True:
            failures.append(f"resumes[{index}] is not runtime qualified")
    bound_count = 0
    qualified_count = 0
    for row in resumes:
        identity = row.get("identity") if isinstance(row, Mapping) else None
        deployment = row.get("deployment") if isinstance(row, Mapping) else None
        if isinstance(identity, Mapping) and all(isinstance(identity.get(key), str) and identity[key].strip() for key in ("robot_id", "configuration_id", "generation")):
            bound_count += 1
        if isinstance(deployment, Mapping) and deployment.get("runtime_qualified") is True:
            qualified_count += 1
    return {
        "schema": AUDIT_SCHEMA,
        "kind": "robot_pool",
        "pool_path": str(pool_path),
        "pool_digest": _sha256_file(pool_path),
        "resume_count": len(resumes),
        "roster_summary": {
            "status": pool.get("status"),
            "bound_identity_count": bound_count,
            "runtime_qualified_count": qualified_count,
        },
        "case_ready": not failures,
        "failures": failures,
    }


def audit_case_inputs(
    semantic_root: str | Path,
    robot_pool_path: str | Path,
    *,
    semantic_requirements: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Return a combined, non-authorizing audit for one case's upstream inputs."""

    semantic = audit_semantic_map(
        semantic_root, semantic_requirements=semantic_requirements
    )
    roster = audit_robot_pool(robot_pool_path)
    failures = list(semantic["failures"]) + list(roster["failures"])
    return {
        "schema": AUDIT_SCHEMA,
        "case_ready": not failures,
        "failures": failures,
        "semantic_map": semantic,
        "robot_pool": roster,
    }


__all__ = [
    "AUDIT_SCHEMA",
    "SEMANTIC_FORMAT_CONTRACT",
    "CaseInputAuditError",
    "audit_case_inputs",
    "audit_robot_pool",
    "audit_semantic_map",
]
