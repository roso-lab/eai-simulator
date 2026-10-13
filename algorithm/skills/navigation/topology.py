"""Shared, simulator-free contract for authored ground-node routes.

Nodes, directed edges, world coordinates and v2 traversal admission affect execution.
Polyline decoration, profiles and scene collision checks belong to authoring.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

LEVEL_HEIGHT_TOLERANCE_M = 0.20
TRAVERSAL_MODES = frozenset({"walk", "stairs", "ramp"})


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
            ("nodes", "edges", "coordinate_frame", "meters_per_unit", "up_axis", "version", "levels", "domain")
            if hasattr(document, name)}


def edge_capabilities(edge: Mapping[str, Any]) -> frozenset[str]:
    return frozenset((edge.get("traversal_mode", "walk"),
                      *edge.get("required_controller_capabilities", ())))


def _strings(value: Any) -> bool:
    return (isinstance(value, (list, tuple))
            and all(isinstance(item, str) and item.strip() for item in value))


def validate_ground_topology(document: Any) -> list[str]:
    return _validate_topology(document, aerial=False)


def validate_aerial_topology(document: Any) -> list[str]:
    """Validate XYZ node products without applying ground support constraints."""
    return _validate_topology(document, aerial=True)


def validate_topology(document: Any) -> list[str]:
    return _validate_topology(document, aerial=topology_fields(document).get("domain") == "aerial")


def _validate_topology(document: Any, *, aerial: bool) -> list[str]:
    """Validate the shared executable subset; an empty authoring draft is valid.

    Legacy host-supplied node/edge mappings may omit coordinate metadata; their
    historical contract is scene-world metres, Z-up. Explicit metadata must agree.
    """
    data = topology_fields(document)
    errors: list[str] = []
    if data.get("domain", "ground") != ("aerial" if aerial else "ground"):
        errors.append("topology domain differs from navigation domain")
    if "schema" in data and data["schema"] != "eai.navigation.topology":
        errors.append("unsupported topology schema")
    version = data.get("version", 1)
    if type(version) is not int or version not in (1, 2):
        errors.append("unsupported ground topology version")
    levels = {}
    if version == 2:
        rows = data.get("levels")
        if not isinstance(rows, (list, tuple)) or (not rows and not aerial):
            errors.append("multilevel topology requires declared levels")
        else:
            for row in rows:
                if (not isinstance(row, Mapping) or not isinstance(row.get("id"), str)
                        or not row["id"].strip() or row["id"] in levels
                        or not isinstance(row.get("label"), str)
                        or not finite_number(row.get("support_z_m"))):
                    errors.append("invalid or duplicate topology level")
                    continue
                levels[row["id"]] = row["support_z_m"]
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
    nodes, node_ids, node_rows, bound_refs = {}, set(), {}, {}
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
        node_rows[node_id] = row
        if not finite_xyz(row.get("position_xyz")):
            errors.append(f"invalid node position: {node_id}")
        else:
            nodes[node_id] = row["position_xyz"]
        heading = row.get("heading_rad")
        if heading is not None and (version != 2 or not finite_number(heading) or abs(heading) > math.pi):
            errors.append(f"node heading requires v2 normalized radians in [-pi, pi]: {node_id}")
        refs = row.get("target_refs", [])
        if (not isinstance(refs, (list, tuple)) or
                any(not isinstance(ref, str) or not ref.strip() or ":" not in ref for ref in refs)):
            errors.append(f"invalid node target references: {node_id}")
        elif version == 2:
            for ref in refs:
                if ref in bound_refs and bound_refs[ref] != node_id:
                    errors.append(f"ambiguous stable target reference: {ref}")
                bound_refs[ref] = node_id
        if version == 2:
            level_id = row.get("level_id")
            if level_id is None and (aerial or row.get("kind") == "transition"):
                pass
            elif not isinstance(level_id, str) or level_id not in levels:
                errors.append(f"node requires a declared level or transition kind: {node_id}")
            elif (not aerial and node_id in nodes and
                  abs(nodes[node_id][2] - levels[level_id]) > LEVEL_HEIGHT_TOLERANCE_M):
                errors.append(f"node support height differs from declared level: {node_id}")
            if not isinstance(row.get("label", ""), str) or not _strings(row.get("aliases", [])):
                errors.append(f"invalid node landmark metadata: {node_id}")
    if version == 2:
        for ref, owner in bound_refs.items():
            if ref in node_ids and ref != owner:
                errors.append(f"stable target reference conflicts with another node id: {ref}")
        # Arrival uses a bounded support-height tolerance. Ordinary floor bands
        # must remain disjoint under that tolerance; small stair landings belong
        # to transition nodes, not indistinguishable declared building floors.
        bands = {level: [height, height] for level, height in levels.items()}
        for node_id, row in node_rows.items():
            level = row.get("level_id")
            if isinstance(level,str) and level in bands and node_id in nodes:
                bands[level][0] = min(bands[level][0], nodes[node_id][2])
                bands[level][1] = max(bands[level][1], nodes[node_id][2])
        ordered = sorted(bands.values())
        if not aerial and any(b[0]-a[1] <= 2*LEVEL_HEIGHT_TOLERANCE_M for a,b in zip(ordered,ordered[1:])):
            errors.append("declared floor height bands overlap the support tolerance; use transition nodes")
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
        elif first in nodes and last in nodes:
            a, b = nodes[first], nodes[last]
            if math.dist(a if aerial else a[:2], b if aerial else b[:2]) <= 0:
                errors.append(f"{'aerial topology edge must have nonzero XYZ' if aerial else 'ground topology edge must have nonzero XY'} length: {edge_id}")
        if type(edge.get("directed", False)) is not bool:
            errors.append(f"invalid edge direction: {edge_id}")
        if version == 2:
            mode = edge.get("traversal_mode")
            if not isinstance(mode, str) or mode not in ({"fly"} if aerial else TRAVERSAL_MODES):
                errors.append(f"invalid edge traversal_mode: {edge_id}")
            if not _strings(edge.get("required_controller_capabilities", [])):
                errors.append(f"invalid edge controller capabilities: {edge_id}")
            if (not aerial and mode == "walk" and isinstance(first, str) and isinstance(last, str)
                    and first in nodes and last in nodes):
                a, b = node_rows[first], node_rows[last]
                if (a.get("level_id") is None or a.get("level_id") != b.get("level_id")
                        or abs(nodes[first][2] - nodes[last][2]) > LEVEL_HEIGHT_TOLERANCE_M):
                    errors.append(f"walk edge cannot cross levels or transitions: {edge_id}")
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
    if data.get("version", 1) == 2:
        payload["schema"] = "eai.navigation.node_route.v2"
        payload["levels"] = sorted((dict(id=row["id"], label=row["label"],
                                          support_z_m=float(row["support_z_m"]))
                                    for row in data["levels"]), key=lambda row: row["id"])
        by_id = {row["id"]: row for row in data["nodes"]}
        for row in payload["nodes"]:
            original = by_id[row["id"]]
            row.update(level_id=original.get("level_id"), label=original.get("label", ""),
                       aliases=sorted(set(original.get("aliases", []))))
            if original.get("heading_rad") is not None:
                row["heading_rad"] = float(original["heading_rad"])
        by_id = {row["id"]: row for row in data["edges"]}
        for row in payload["edges"]:
            original = by_id[row["id"]]
            row.update(traversal_mode=original["traversal_mode"],
                       required_controller_capabilities=sorted(edge_capabilities(original)))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()
