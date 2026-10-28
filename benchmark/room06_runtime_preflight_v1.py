"""Static fail-closed preflight for the real ``room_06 × liv-t-04`` runtime.

This check runs before Isaac starts.  It binds the exact scene bytes, semantic
map revision, task targets, selected roster rows and task-generated
object definitions.  Passing it only means that the declared runtime inputs are
internally consistent enough for a scene smoke; it is never evidence of task
success, room-specific robot feasibility, physical qualification or a paper
score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .case_input_audit_v1 import CaseInputAuditError, audit_semantic_map
from .case_manifest_v1 import CaseManifestError, validate_task_object_bindings


PREFLIGHT_SCHEMA = "heterocoord.room06-runtime-preflight.v1"
INPUT_SCHEMA = "heterocoord.room06-runtime-preflight-input.v1"
TASK_SETUP_SCHEMA = "heterocoord.room06-task-setup.v1"
SCENE_KEY = "living_room_06"
TASK_ID = "liv-t-04"
REQUIRED_POINT_LABELS = (
    "floor1-bookshelf",
    "floor2-bookshelf-1",
    "floor2-bookshelf-2",
    "floor2-bookshelf-3",
)
REQUIRED_OBJECT_IDS = ("book_batch_01", "heavy_object")
REQUIRED_SKILL_IDS = frozenset(
    {"eai.navigation.node_route", "eai.manipulation.pick_place"}
)
CROSS_FLOOR_CONTROLLERS = frozenset(
    {"B2_MIXED_STAIRS_CFG", "GO2_MIXED_STAIRS_CFG"}
)
CONFLICTING_RESTRICTIONS = frozenset(
    {
        "GO2_OFFROAD_MOTION_UNSTABLE",
        "G1_WALK_GRASP_MODE_EXCLUSIVE_AND_STAIRS_UNSTABLE",
        "CF2X_NO_PICK_PLACE_ADAPTER",
        "LITE3_STAIRS_UNSUPPORTED",
    }
)
FORMAL_GATES = {
    "runtime_qualified": False,
    "physical_qualified": False,
    "rankable": False,
    "paper_score_eligible": False,
}
_HEX = frozenset("0123456789abcdef")
_INPUT_KEYS = {
    "schema",
    "scene_key",
    "scene_asset_digest",
    "semantic_map_identity",
    "transition_handoff_node_id",
    "selected_robot_ids",
    "cross_floor_robot_ids",
    "robot_roster_digest",
    "task_object_bindings",
    "formal_gates",
}
_MAP_IDENTITY_KEYS = {"save_id", "semantic_binding_digest", "topology_digest"}


class Room06RuntimePreflightError(ValueError):
    """Raised by the strict wrapper when static runtime inputs are not ready."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Room06RuntimePreflightError("value is not canonical JSON") from exc


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path, name: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise Room06RuntimePreflightError(f"{name} is not a regular file: {path}")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Room06RuntimePreflightError(f"cannot hash {name}: {path}") from exc


def _load_json(path: Path, name: str) -> Mapping[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise Room06RuntimePreflightError(f"{name} is not a regular JSON file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Room06RuntimePreflightError(f"cannot read {name}: {path}") from exc
    if not isinstance(value, Mapping):
        raise Room06RuntimePreflightError(f"{name} must be an object")
    return value


def _object(value: Mapping[str, Any] | str | Path, name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return _load_json(Path(value).resolve(), name)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06RuntimePreflightError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise Room06RuntimePreflightError(f"{name} must be a lowercase SHA-256")
    return value


def _unique_text_list(value: Any, name: str, *, minimum: int = 1) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise Room06RuntimePreflightError(
            f"{name} must contain at least {minimum} unique non-empty IDs"
        )
    return list(value)


def _project_path(root: Path) -> Path:
    candidates = [root.parent / "project.json"]
    if root.parent.name == "revisions":
        candidates.insert(0, root.parent.parent / "project.json")
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise Room06RuntimePreflightError(
        "semantic map revision has no owning project.json"
    )


def _edge_endpoints(edge: Mapping[str, Any], index: int) -> tuple[str, str]:
    legacy = (edge.get("source"), edge.get("target"))
    current = (edge.get("from"), edge.get("to"))
    legacy_present = any(value is not None for value in legacy)
    current_present = any(value is not None for value in current)
    if legacy_present and current_present and legacy != current:
        raise Room06RuntimePreflightError(
            f"topology.edges[{index}] has conflicting endpoint fields"
        )
    endpoints = current if current_present else legacy
    if any(not isinstance(value, str) or not value.strip() for value in endpoints):
        raise Room06RuntimePreflightError(
            f"topology.edges[{index}] has invalid endpoints"
        )
    return endpoints[0], endpoints[1]


def _point_labels(spec: Mapping[str, Any]) -> dict[str, str]:
    """Bind contract roles to exact map labels; never infer names or routes."""
    labels = spec.get("semantic_point_labels")
    if labels is None and "semantic_point_labels" not in spec:
        return {label: label for label in REQUIRED_POINT_LABELS}
    if not isinstance(labels, Mapping) or set(labels) != set(REQUIRED_POINT_LABELS):
        raise Room06RuntimePreflightError("semantic_point_labels must cover all task target roles")
    result = {
        role: _text(labels[role], f"semantic_point_labels.{role}")
        for role in REQUIRED_POINT_LABELS
    }
    if len(set(result.values())) != len(result):
        raise Room06RuntimePreflightError("semantic_point_labels must identify distinct targets")
    return result


def validate_semantic_target_summary(value: Mapping[str, Any], handoff_node_id: str) -> None:
    """Check the target contract consumed by the task ledger, without routing."""
    required = {
        "save_id", "semantic_binding_digest", "topology_digest",
        "transition_handoff_node_id", "required_semantic_points",
        "semantic_point_labels", "target_bindings", "ground_node_ids",
        "handoff_binding_verified", "route_precomputed",
        "navigation_authority", "room_specific_feasibility",
    }
    if set(value) != required:
        raise Room06RuntimePreflightError("semantic target summary schema mismatch")
    _text(value["save_id"], "semantic target save_id")
    for field in ("semantic_binding_digest", "topology_digest"):
        _digest(value[field], f"semantic target {field}")
    labels = _point_labels(value)
    nodes = set(_unique_text_list(value["ground_node_ids"], "ground_node_ids"))
    targets = value["target_bindings"]
    if not isinstance(targets, Mapping) or set(targets) != set(REQUIRED_POINT_LABELS):
        raise Room06RuntimePreflightError("semantic target bindings must cover every task role")
    for role, bindings in targets.items():
        if not set(_unique_text_list(bindings, f"target_bindings.{role}")).issubset(nodes):
            raise Room06RuntimePreflightError("semantic target binding references an unknown node")
    if (
        value["required_semantic_points"] != list(labels.values())
        or value["transition_handoff_node_id"] != handoff_node_id
        or handoff_node_id not in nodes
        or value["navigation_authority"] != "system_runtime"
        or value["route_precomputed"] is not False
        or value["handoff_binding_verified"] is not True
        or value["room_specific_feasibility"] is not False
    ):
        raise Room06RuntimePreflightError("semantic target binding is inconsistent")


def _task_setup(
    value: Mapping[str, Any] | str | Path,
    bindings: Mapping[str, Any],
    scene_digest: str,
) -> dict[str, Any]:
    setup = _object(value, "task setup")
    if set(setup) != {"schema", "task_id", "objects"}:
        raise Room06RuntimePreflightError("task setup schema mismatch")
    if setup.get("schema") != TASK_SETUP_SCHEMA or setup.get("task_id") != TASK_ID:
        raise Room06RuntimePreflightError("task setup must identify liv-t-04 v1")
    rows = setup.get("objects")
    if not isinstance(rows, list) or len(rows) != len(REQUIRED_OBJECT_IDS):
        raise Room06RuntimePreflightError(
            "task setup must contain exactly book_batch_01 and heavy_object"
        )
    definitions: dict[str, Any] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {"entity_id", "definition"}:
            raise Room06RuntimePreflightError(
                f"task setup objects[{index}] schema mismatch"
            )
        entity_id = _text(row["entity_id"], f"task setup objects[{index}].entity_id")
        definition = row["definition"]
        if not isinstance(definition, Mapping) or not definition:
            raise Room06RuntimePreflightError(
                f"task setup definition is empty: {entity_id}"
            )
        if entity_id in definitions:
            raise Room06RuntimePreflightError(
                f"task setup entity is duplicated: {entity_id}"
            )
        _canonical_bytes(definition)
        definitions[entity_id] = definition
    if set(definitions) != set(REQUIRED_OBJECT_IDS):
        raise Room06RuntimePreflightError(
            "task setup must exactly cover book_batch_01 and heavy_object"
        )
    try:
        normalized = validate_task_object_bindings(
            bindings,
            scene_digest=scene_digest,
            entity_ids=list(REQUIRED_OBJECT_IDS),
        )
    except (CaseManifestError, TypeError, ValueError) as exc:
        raise Room06RuntimePreflightError(str(exc)) from exc
    by_id = {row["entity_id"]: row for row in normalized["objects"]}
    definition_digests: dict[str, str] = {}
    for entity_id, definition in definitions.items():
        expected_ref = f"task-setup:{TASK_ID}/{entity_id}"
        binding = by_id[entity_id]
        if binding["definition_ref"] != expected_ref:
            raise Room06RuntimePreflightError(
                f"task object definition_ref mismatch: {entity_id}"
            )
        actual = _canonical_digest(definition)
        if binding["definition_digest"] != actual:
            raise Room06RuntimePreflightError(
                f"task object definition digest mismatch: {entity_id}"
            )
        if actual == "0" * 64:
            raise Room06RuntimePreflightError(
                f"task object definition digest is unresolved: {entity_id}"
            )
        definition_digests[entity_id] = actual
    return {
        "task_setup_digest": _canonical_digest(setup),
        "definition_digests": definition_digests,
    }


def _robot_rows(
    roster_path: Path,
    *,
    expected_digest: str,
    selected_ids: Sequence[str],
    cross_floor_ids: Sequence[str],
) -> list[dict[str, Any]]:
    actual_digest = _sha256_file(roster_path, "robot roster")
    if actual_digest != expected_digest:
        raise Room06RuntimePreflightError("robot roster digest mismatch")
    roster = _load_json(roster_path, "robot roster")
    if roster.get("schema_id") != "heterocoord.robot_runtime_roster":
        raise Room06RuntimePreflightError("robot roster schema mismatch")
    if roster.get("formal_gates") != FORMAL_GATES:
        raise Room06RuntimePreflightError("robot roster formal gates must remain closed")
    rows = roster.get("robots")
    if not isinstance(rows, list) or not rows:
        raise Room06RuntimePreflightError("robot roster robots is empty")
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise Room06RuntimePreflightError(f"robot roster robots[{index}] is invalid")
        instance_id = _text(row.get("instance_id"), f"robots[{index}].instance_id")
        if instance_id in by_id:
            raise Room06RuntimePreflightError(
                f"robot roster instance is duplicated: {instance_id}"
            )
        by_id[instance_id] = row
    missing = sorted(set(selected_ids) - set(by_id))
    if missing:
        raise Room06RuntimePreflightError(
            f"selected robots are absent from roster: {missing}"
        )
    results: list[dict[str, Any]] = []
    for instance_id in selected_ids:
        row = by_id[instance_id]
        if row.get("runtime_qualified") is not True or not isinstance(
            row.get("runtime_qualification_receipt"), Mapping
        ):
            raise Room06RuntimePreflightError(
                f"selected robot lacks historical runtime receipt: {instance_id}"
            )
        restrictions = row.get("restrictions")
        if (
            not isinstance(restrictions, list)
            or any(not isinstance(item, str) for item in restrictions)
        ):
            raise Room06RuntimePreflightError(
                f"selected robot restrictions are malformed: {instance_id}"
            )
        conflicts = sorted(set(restrictions) & CONFLICTING_RESTRICTIONS)
        if conflicts:
            raise Room06RuntimePreflightError(
                f"selected robot restrictions conflict with liv-t-04: "
                f"{instance_id} {conflicts}"
            )
        capability = row.get("capability_binding")
        if not isinstance(capability, Mapping):
            raise Room06RuntimePreflightError(
                f"selected robot capability binding is missing: {instance_id}"
            )
        source_input = capability.get("source_input")
        if not isinstance(source_input, Mapping):
            raise Room06RuntimePreflightError(
                f"selected robot capability source is missing: {instance_id}"
            )
        expected_capability_digest = _digest(
            capability.get("capability_digest"),
            f"{instance_id}.capability_digest",
        )
        if _canonical_digest(source_input) != expected_capability_digest:
            raise Room06RuntimePreflightError(
                f"selected robot capability digest mismatch: {instance_id}"
            )
        skill_rows = capability.get("skill_bindings")
        if not isinstance(skill_rows, list):
            raise Room06RuntimePreflightError(
                f"selected robot skill bindings are malformed: {instance_id}"
            )
        skills = {
            item.get("skill_id")
            for item in skill_rows
            if isinstance(item, Mapping) and isinstance(item.get("skill_id"), str)
        }
        missing_skills = sorted(REQUIRED_SKILL_IDS - skills)
        if missing_skills:
            raise Room06RuntimePreflightError(
                f"selected robot lacks liv-t-04 skills: {instance_id} {missing_skills}"
            )
        controller = source_input.get("motion_controller")
        if instance_id in cross_floor_ids and controller not in CROSS_FLOOR_CONTROLLERS:
            raise Room06RuntimePreflightError(
                f"cross-floor robot has no frozen stair controller: {instance_id}"
            )
        results.append(
            {
                "instance_id": instance_id,
                "required_skills": sorted(REQUIRED_SKILL_IDS),
                "restrictions": list(restrictions),
                "cross_floor_role": instance_id in cross_floor_ids,
                "motion_controller": controller,
                "historical_runtime_qualified": True,
                "room_specific_feasibility": False,
            }
        )
    return results


def _map_and_targets(
    root: Path,
    *,
    scene_digest: str,
    identity: Mapping[str, Any],
    transition_handoff_node_id: str,
    point_labels: Mapping[str, str],
) -> dict[str, Any]:
    if set(identity) != _MAP_IDENTITY_KEYS:
        raise Room06RuntimePreflightError("semantic_map_identity schema mismatch")
    expected_save_id = _text(identity.get("save_id"), "semantic_map_identity.save_id")
    expected_binding = _digest(
        identity.get("semantic_binding_digest"),
        "semantic_map_identity.semantic_binding_digest",
    )
    expected_topology = _digest(
        identity.get("topology_digest"), "semantic_map_identity.topology_digest"
    )
    project = _load_json(_project_path(root), "semantic map project")
    manifest = _load_json(root / "manifest.json", "semantic map manifest")
    topology_path = root / "topology.json"
    topology = _load_json(topology_path, "ground topology")
    points = _load_json(root / "semantic-points.json", "semantic points")
    if (
        project.get("scene_key") != SCENE_KEY
        or manifest.get("scene_key") != SCENE_KEY
        or topology.get("scene_key") != SCENE_KEY
        or points.get("scene_key") != SCENE_KEY
    ):
        raise Room06RuntimePreflightError("room06 semantic products have wrong scene identity")
    if project.get("save_id") != expected_save_id or manifest.get("revision") != expected_save_id:
        raise Room06RuntimePreflightError("semantic map save_id identity mismatch")
    if (
        project.get("semantic_binding_digest") != expected_binding
        or manifest.get("semantic_binding_digest") != expected_binding
    ):
        raise Room06RuntimePreflightError("semantic map binding identity mismatch")
    actual_topology = _sha256_file(topology_path, "ground topology")
    if actual_topology != expected_topology:
        raise Room06RuntimePreflightError("ground topology digest mismatch")
    source = topology.get("source")
    if not isinstance(source, Mapping) or scene_digest not in source.values():
        raise Room06RuntimePreflightError(
            "ground topology source digest does not bind the scene USD"
        )

    node_rows = topology.get("nodes")
    edge_rows = topology.get("edges")
    if not isinstance(node_rows, list) or not node_rows:
        raise Room06RuntimePreflightError("ground topology nodes is empty")
    if not isinstance(edge_rows, list):
        raise Room06RuntimePreflightError("ground topology edges must be a list")
    nodes: dict[str, Mapping[str, Any]] = {}
    for index, node in enumerate(node_rows):
        if not isinstance(node, Mapping):
            raise Room06RuntimePreflightError(f"topology.nodes[{index}] is invalid")
        node_id = _text(node.get("id"), f"topology.nodes[{index}].id")
        _text(node.get("level_id"), f"topology.nodes[{index}].level_id")
        if node_id in nodes:
            raise Room06RuntimePreflightError(f"topology node is duplicated: {node_id}")
        nodes[node_id] = node
    handoff = nodes.get(transition_handoff_node_id)
    if handoff is None:
        raise Room06RuntimePreflightError(
            "transition_handoff_node_id is absent from ground topology: "
            f"{transition_handoff_node_id}"
        )

    # Edges remain authored data. Existence/reachability of a cross-floor
    # route is a navigation/execution outcome, never a map admission condition.
    for index, edge in enumerate(edge_rows):
        if not isinstance(edge, Mapping):
            raise Room06RuntimePreflightError(f"topology.edges[{index}] is invalid")
        source_id, target_id = _edge_endpoints(edge, index)
        if source_id not in nodes or target_id not in nodes:
            raise Room06RuntimePreflightError(
                f"topology.edges[{index}] references an unknown node"
            )

    point_rows = points.get("points")
    if not isinstance(point_rows, list):
        raise Room06RuntimePreflightError("semantic points list is missing")
    ground_bindings: dict[str, set[str]] = {}
    for point in point_rows:
        if not isinstance(point, Mapping) or point.get("label") not in point_labels.values():
            continue
        bindings = point.get("navigation_bindings")
        if not isinstance(bindings, list):
            continue
        ground_bindings[point["label"]] = {
            item.get("node_id")
            for item in bindings
            if isinstance(item, Mapping)
            and item.get("domain") == "ground"
            and isinstance(item.get("node_id"), str)
        }
    for label in point_labels.values():
        if not ground_bindings.get(label) or not ground_bindings[label].issubset(nodes):
            raise Room06RuntimePreflightError(
                f"required semantic point has no ground binding: {label}"
            )
    return {
        "save_id": expected_save_id,
        "semantic_binding_digest": expected_binding,
        "topology_digest": actual_topology,
        # Legacy field name: this is a task exchange observation location,
        # not a claim that the node is a stair entrance/exit.
        "transition_handoff_node_id": transition_handoff_node_id,
        "required_semantic_points": list(point_labels.values()),
        "semantic_point_labels": dict(point_labels),
        "target_bindings": {
            role: sorted(ground_bindings[label]) for role, label in point_labels.items()
        },
        "ground_node_ids": sorted(nodes),
        "handoff_binding_verified": True,
        "route_precomputed": False,
        "navigation_authority": "system_runtime",
        "room_specific_feasibility": False,
    }


def audit_room06_runtime_preflight(
    *,
    semantic_root: str | Path,
    scene_usd: str | Path,
    robot_roster: str | Path,
    binding: Mapping[str, Any] | str | Path,
    task_setup: Mapping[str, Any] | str | Path,
) -> dict[str, Any]:
    """Return a non-authorizing static preflight report.

    All checks are attempted in dependency order and any malformed or missing
    input yields ``preflight_passed=false``.  Formal gates are always closed.
    """

    failures: list[str] = []
    result: dict[str, Any] = {
        "schema": PREFLIGHT_SCHEMA,
        "scope": "static_cpu_preflight_only",
        "task_id": TASK_ID,
        "scene_key": SCENE_KEY,
        "preflight_passed": False,
        "scene_smoke_may_start": False,
        "room_specific_feasibility": False,
        "formal_gates": dict(FORMAL_GATES),
        "failures": failures,
    }
    try:
        spec = _object(binding, "runtime preflight binding")
        if set(spec) not in (_INPUT_KEYS, _INPUT_KEYS | {"semantic_point_labels"}) or spec.get("schema") != INPUT_SCHEMA:
            raise Room06RuntimePreflightError("runtime preflight binding schema mismatch")
        if spec.get("scene_key") != SCENE_KEY:
            raise Room06RuntimePreflightError("runtime preflight scene_key mismatch")
        if spec.get("formal_gates") != FORMAL_GATES:
            raise Room06RuntimePreflightError("runtime preflight formal gates must remain closed")
        result["runtime_binding_digest"] = _canonical_digest(spec)
        scene_digest = _digest(spec.get("scene_asset_digest"), "scene_asset_digest")
        usd_path = Path(scene_usd).resolve()
        actual_scene_digest = _sha256_file(usd_path, "scene USD")
        if actual_scene_digest != scene_digest:
            raise Room06RuntimePreflightError("scene USD digest mismatch")
        result["scene"] = {
            "usd_path": str(usd_path),
            "scene_asset_digest": actual_scene_digest,
        }

        # Map acceptance and case executability are different results. Keep
        # the accepted map visible even when a later case binding is missing.
        point_labels = _point_labels(spec)
        result["failure_scope"] = "semantic_input"
        result["semantic_input_audit"] = audit_semantic_map(
            semantic_root,
            semantic_requirements={"semantic_points": list(point_labels.values())},
        )
        if result["semantic_input_audit"].get("case_ready") is not True:
            raise Room06RuntimePreflightError(
                "semantic map input audit failed: "
                + "; ".join(result["semantic_input_audit"].get("failures", []))
            )
        result["failure_scope"] = "case_runtime_binding"

        selected = _unique_text_list(
            spec.get("selected_robot_ids"), "selected_robot_ids", minimum=2
        )
        cross_floor = _unique_text_list(
            spec.get("cross_floor_robot_ids"), "cross_floor_robot_ids"
        )
        if not set(cross_floor).issubset(selected):
            raise Room06RuntimePreflightError(
                "cross_floor_robot_ids must be a subset of selected_robot_ids"
            )
        roster_digest = _digest(
            spec.get("robot_roster_digest"), "robot_roster_digest"
        )
        result["robots"] = _robot_rows(
            Path(robot_roster).resolve(),
            expected_digest=roster_digest,
            selected_ids=selected,
            cross_floor_ids=cross_floor,
        )
        result["robot_roster_digest"] = roster_digest

        transition_handoff_node_id = _text(
            spec.get("transition_handoff_node_id"),
            "transition_handoff_node_id",
        )
        identity = spec.get("semantic_map_identity")
        if not isinstance(identity, Mapping):
            raise Room06RuntimePreflightError("semantic_map_identity must be an object")
        semantic_map = _map_and_targets(
            Path(semantic_root).resolve(),
            scene_digest=scene_digest,
            identity=identity,
            transition_handoff_node_id=transition_handoff_node_id,
            point_labels=point_labels,
        )
        result["semantic_map"] = semantic_map
        result["transition_handoff_node_id"] = semantic_map[
            "transition_handoff_node_id"
        ]
        bindings = spec.get("task_object_bindings")
        if not isinstance(bindings, Mapping):
            raise Room06RuntimePreflightError("task_object_bindings must be an object")
        result["task_setup"] = _task_setup(
            task_setup, bindings=bindings, scene_digest=scene_digest
        )
    except (CaseInputAuditError, Room06RuntimePreflightError, OSError, TypeError, ValueError) as exc:
        failures.append(str(exc))
        return result

    result["preflight_passed"] = True
    result["scene_smoke_may_start"] = True
    result.pop("failure_scope", None)
    return result


def require_room06_runtime_preflight(**kwargs: Any) -> dict[str, Any]:
    """Return a passing report or raise with its exact fail-closed reason."""

    report = audit_room06_runtime_preflight(**kwargs)
    if not report["preflight_passed"]:
        raise Room06RuntimePreflightError("; ".join(report["failures"]))
    return report


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise Room06RuntimePreflightError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-root", required=True, type=Path)
    parser.add_argument("--scene-usd", required=True, type=Path)
    parser.add_argument("--robot-roster", required=True, type=Path)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--task-setup", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = audit_room06_runtime_preflight(
        semantic_root=args.semantic_root,
        scene_usd=args.scene_usd,
        robot_roster=args.robot_roster,
        binding=args.binding,
        task_setup=args.task_setup,
    )
    try:
        _write_new_json(args.output, report)
    except Room06RuntimePreflightError as exc:
        parser.error(str(exc))
    if not report["preflight_passed"]:
        for failure in report["failures"]:
            print(f"BLOCKED: {failure}")
        return 2
    print("PASS: static room06 runtime inputs are ready for a scene smoke")
    return 0


__all__ = [
    "CONFLICTING_RESTRICTIONS",
    "CROSS_FLOOR_CONTROLLERS",
    "FORMAL_GATES",
    "INPUT_SCHEMA",
    "PREFLIGHT_SCHEMA",
    "REQUIRED_OBJECT_IDS",
    "REQUIRED_POINT_LABELS",
    "REQUIRED_SKILL_IDS",
    "Room06RuntimePreflightError",
    "TASK_SETUP_SCHEMA",
    "audit_room06_runtime_preflight",
    "require_room06_runtime_preflight",
]


if __name__ == "__main__":
    raise SystemExit(main())
