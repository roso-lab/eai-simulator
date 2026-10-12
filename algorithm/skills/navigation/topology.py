"""Shared, simulator-free contract for authored ground-node routes.

Only nodes, directed edges and world coordinates affect route execution.
Polyline decoration, profiles and scene collision checks belong to authoring.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


def finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def finite_xyz(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(finite_number(item) for item in value))


def topology_fields(document: Any) -> Mapping[str, Any]:
    if isinstance(document, Mapping):
        return document
    return {name: getattr(document, name) for name in
            ("nodes", "edges", "coordinate_frame", "meters_per_unit", "up_axis")
            if hasattr(document, name)}


def validate_ground_topology(document: Any) -> list[str]:
    """Validate the shared executable subset; an empty authoring draft is valid.

    Legacy host-supplied node/edge mappings may omit coordinate metadata; their
    historical contract is scene-world metres, Z-up. Explicit metadata must agree.
    """
    data = topology_fields(document)
    errors: list[str] = []
    if data.get("coordinate_frame", "scene_world") != "scene_world":
        errors.append("ground topology coordinate_frame must be scene_world")
    units = data.get("meters_per_unit", 1.0)
    if not finite_number(units) or units != 1:
        errors.append("ground topology meters_per_unit must be 1")
    if data.get("up_axis", "Z") != "Z":
        errors.append("ground topology up_axis must be Z")
    rows, edges = data.get("nodes"), data.get("edges")
    if not isinstance(rows, (list, tuple)) or not isinstance(edges, (list, tuple)):
        return errors + ["ground topology nodes and edges must be arrays"]
    nodes, node_ids = {}, set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append(f"invalid node object: {index}")
            continue
        node_id = row.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append(f"duplicate/invalid node id: {node_id!r}")
            continue
        if node_id in node_ids:
            errors.append(f"duplicate/invalid node id: {node_id!r}")
        node_ids.add(node_id)
        if not finite_xyz(row.get("position_xyz")):
            errors.append(f"invalid node position: {node_id}")
        else:
            nodes[node_id] = row["position_xyz"]
        refs = row.get("target_refs", [])
        if (not isinstance(refs, (list, tuple)) or
                any(not isinstance(ref, str) or not ref.strip() or ":" not in ref for ref in refs)):
            errors.append(f"invalid node target references: {node_id}")
    edge_ids = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            errors.append(f"invalid edge object: {index}")
            continue
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id.strip() or edge_id in edge_ids:
            errors.append(f"duplicate/invalid edge id: {edge_id!r}")
            continue
        edge_ids.add(edge_id)
        first, last = edge.get("from"), edge.get("to")
        if (not isinstance(first, str) or not isinstance(last, str)
                or first not in node_ids or last not in node_ids or first == last):
            errors.append(f"invalid edge endpoints: {edge_id}")
        elif first in nodes and last in nodes and math.dist(nodes[first][:2], nodes[last][:2]) <= 0:
            errors.append(f"ground topology edge must have nonzero XY length: {edge_id}")
        if type(edge.get("directed", False)) is not bool:
            errors.append(f"invalid edge direction: {edge_id}")
    return sorted(set(errors))


def route_graph_digest(document: Any) -> str:
    """Identity of the validated executable graph, independent of row ordering.

    This is not the source-asset/product digest or proof of physical passage.
    """
    errors = validate_ground_topology(document)
    if errors:
        raise ValueError("; ".join(errors))
    data = topology_fields(document)
    payload = {
        "schema": "eai.navigation.node_route.v1", "coordinate_frame": "scene_world",
        "meters_per_unit": 1, "up_axis": "Z",
        "nodes": sorted(({
            "id": row["id"], "position_xyz": list(map(float, row["position_xyz"])),
            "target_refs": sorted(set(row.get("target_refs", []))),
        } for row in data["nodes"]), key=lambda row: row["id"]),
        "edges": sorted(({
            "id": row["id"], "from": row["from"], "to": row["to"],
            "directed": row.get("directed", False),
        } for row in data["edges"]), key=lambda row: row["id"]),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()
