"""Independent logical members of the room06 task entities.

The batch remains a contract identity. No member has a USD or rigid-body
requirement. Legacy CPU fixtures may omit member definitions; new cases may not.
"""

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import math
import re
from typing import Any

BOOK_BATCH_ID = "book_batch_01"
HEAVY_OBJECT_ID = "heavy_object"
SHELVES = tuple(f"floor2-bookshelf-{i}" for i in range(1, 4))


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def book_members(definition: Mapping[str, Any], *, required: bool = True) -> list[dict[str, Any]]:
    rows = definition.get("books")
    if rows is None and not required and "books" not in definition:
        return []
    if not isinstance(rows, list) or len(rows) != 6 or definition.get("count") != 6:
        raise ValueError("individual book definitions must contain six books")
    result = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "entity_id", "mass_kg", "destination_semantic_point"
        }:
            raise ValueError("individual book definition schema mismatch")
        entity = row["entity_id"]
        if not isinstance(entity, str) or not re.fullmatch(r"book_[0-9]{2}", entity):
            raise ValueError("individual book ID must be a safe book_NN identity")
        target = row["destination_semantic_point"]
        if not isinstance(target, str) or target not in SHELVES:
            raise ValueError("individual book destination is not an upper shelf")
        result.append({
            "entity_id": entity,
            "mass_kg": _positive(row["mass_kg"], "book mass_kg"),
            "destination_semantic_point": target,
        })
    ids = [row["entity_id"] for row in result]
    declared_ids = definition.get("book_ids")
    if not isinstance(declared_ids, list) or any(not isinstance(x, str) for x in declared_ids):
        raise ValueError("book_ids must list the individual books")
    if len(set(ids)) != 6 or sorted(ids) != sorted(declared_ids):
        raise ValueError("individual book IDs must exactly cover the batch without duplicates")
    if Counter(row["destination_semantic_point"] for row in result) != Counter({shelf: 2 for shelf in SHELVES}):
        raise ValueError("each upper shelf must receive exactly two books")
    return sorted(result, key=lambda row: row["entity_id"])


def logical_task_objects(setup: Mapping[str, Any], *, require_individual_books: bool = True) -> list[dict[str, Any]]:
    """Expand member objects without assigning any robot to the task."""
    if setup.get("schema") != "heterocoord.room06-task-setup.v1" or setup.get("task_id") != "liv-t-04":
        raise ValueError("task setup must identify liv-t-04 v1")
    rows = setup.get("objects")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("task setup must contain book_batch_01 and heavy_object")
    by_id = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("definition"), Mapping):
            raise ValueError("task object definition is unavailable")
        entity = row.get("entity_id")
        if entity not in (BOOK_BATCH_ID, HEAVY_OBJECT_ID) or entity in by_id:
            raise ValueError("task object identity is invalid or duplicated")
        by_id[entity] = row["definition"]
    batch = by_id[BOOK_BATCH_ID]
    members = book_members(batch, required=require_individual_books)
    if not members:
        return deepcopy(rows)
    if batch.get("source_semantic_point") != "floor1-bookshelf" or batch.get("destination_semantic_points") != list(SHELVES):
        raise ValueError("book batch semantic points mismatch")
    objects = []
    for row in members:
        definition = {key: value for key, value in row.items() if key != "entity_id"}
        definition.update(kind="book", batch_id=BOOK_BATCH_ID, source_semantic_point=batch["source_semantic_point"])
        objects.append({"entity_id": row["entity_id"], "definition": definition})
    heavy = deepcopy(by_id[HEAVY_OBJECT_ID])
    heavy.pop("size_xyz_m", None)  # Legacy metadata is not execution state.
    objects.append({"entity_id": HEAVY_OBJECT_ID, "definition": heavy})
    return objects


def logical_object_definitions(setup: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for row in logical_task_objects(setup):
        definition = row["definition"]
        result.append({
            "entity_id": row["entity_id"], "category": definition["kind"],
            "metadata": {k: v for k, v in definition.items() if k not in {"kind", "source_semantic_point", "destination_semantic_point"}},
            "initial_semantic_point": definition["source_semantic_point"],
            "target_semantic_point": definition["destination_semantic_point"],
        })
    return result


def public_task_package(package: Mapping[str, Any], *, robots, semantic_points):
    """Publish task facts, never evaluator internals or a prescribed coalition.

    The host supplies complete public RobotState records and map points. This
    projection deliberately constructs metadata by allowlist rather than
    forwarding arbitrary frozen-package dictionaries to a tested system.
    """
    from benchmark.records_v3 import canonical_digest
    from algorithm.common_coordination.contract import CoordinationTask, TaskGraph
    from task.contracts import EaiTaskPackage, RobotState

    body = dict(package)
    if body.pop("package_digest", None) != canonical_digest(body):
        raise ValueError("public task requires an intact frozen package")
    robots = tuple(robots)
    if any(not isinstance(robot, RobotState) for robot in robots):
        raise ValueError("public robots must be RobotState records")
    ids = [robot.capability.robot_id for robot in robots]
    selected = package["plan"]["selected_robot_ids"]
    if len(ids) != len(set(ids)) or set(ids) != set(selected):
        raise ValueError("public roster must contain the complete deployment pool")
    if not isinstance(semantic_points, list):
        raise ValueError("public semantic points must be a list")
    points = []
    labels = set()
    for row in semantic_points:
        label = row.get("label") if isinstance(row, Mapping) else None
        xyz = row.get("position_xyz") if isinstance(row, Mapping) else None
        if not isinstance(label, str) or not label.strip() or label in labels:
            raise ValueError("public semantic point labels must be nonempty and unique")
        if (not isinstance(xyz, (list, tuple)) or len(xyz) != 3
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not math.isfinite(x) for x in xyz)):
            raise ValueError("public semantic point position must be finite XYZ")
        labels.add(label)
        points.append({"label": label, "position_xyz": list(xyz)})
    tasks = []
    for obj in logical_object_definitions(package["task_setup"]):
        source, target = obj["initial_semantic_point"], obj["target_semantic_point"]
        if {source, target} - labels:
            raise ValueError("public map is missing an object operation point")
        metadata = obj["metadata"]
        transport = {
            "object_ref": obj["entity_id"], "object_mass_kg": metadata["mass_kg"],
            "source_ref": source, "target_ref": target,
            "minimum_holders": metadata.get("minimum_holders", 1),
        }
        if "batch_id" in metadata:
            transport["batch_id"] = metadata["batch_id"]
        tasks.append(CoordinationTask(
            task_id=f"transport:{obj['entity_id']}",
            description=f"Move {obj['entity_id']} from {source} to {target}.",
            target_ref=target, metadata={"transport": transport}))
    return EaiTaskPackage(
        task_id="liv-t-04", scene_id="room_06",
        graph=TaskGraph(tasks=tuple(tasks), source="room06-task-setup"),
        robots=deepcopy(robots), metadata={
            "semantic_points": points, "deadline_tick": package["plan"]["horizon_ticks"],
            "task_object_model": "logical", "book_relay_required": True,
        })


def room06_leg_bindings(setup: Mapping[str, Any], *, transition_point: str) -> dict[str, dict[str, Any]]:
    """Host operation templates; robot membership comes only from decisions.

    Heavy pickup produces hold observations, not an invented milestone. The
    transition point must be supplied by the case binding, never inferred as
    a staircase from a node name.
    """
    if not isinstance(transition_point, str) or not transition_point.strip():
        raise ValueError("transition point is required")
    result = {}
    for row in logical_task_objects(setup):
        entity, definition = row["entity_id"], row["definition"]
        book = definition["kind"] == "book"
        phases = [(
            "pickup", "pick", definition["source_semantic_point"],
            "relay_pickup_completed" if book else None)]
        if book:
            phases.append(("handoff", "receive", transition_point, "topology_handoff_completed"))
        phases.append(("delivery", "deliver", definition["destination_semantic_point"],
                       "relay_delivery_completed" if book else "heavy_carry_completed"))
        for phase, operation, point, event in phases:
            result[f"{phase}:{entity}"] = {
                "operation": operation, "object_id": entity,
                "semantic_point": point, "event_type": event,
            }
    return result
