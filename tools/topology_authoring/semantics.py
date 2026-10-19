"""Versioned, scene-independent annotations over the executable node graph.

Regions/entities are scene facts; interaction poses are optional task additions.
Named routes refer to actual ordered nodes/edges, never decorative polylines.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Mapping

from algorithm.skills.navigation.topology import finite_number, finite_xyz

COLLECTIONS = ("regions", "entities", "interaction_poses", "routes")


def empty_semantics():
    return {"schema": "eai.scene.semantics", "version": 1,
            **{name: [] for name in COLLECTIONS}, "extensions": {}}


def semantic_level(row, records):
    """Resolve the floor inherited from a region or interaction owner."""
    seen = set()
    while row:
        if row.get("level_id") is not None:
            return row["level_id"]
        parent = row.get("owner_id") or row.get("region_id")
        if not isinstance(parent, str) or parent in seen:
            return None
        seen.add(parent)
        row = records.get(parent)
    return None


def _identifier(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _strings(value):
    return (isinstance(value, (list, tuple)) and all(_identifier(item) for item in value)
            and len(value) == len(set(value)))


def _json_object(value):
    if not isinstance(value, Mapping):
        return False
    def valid(item):
        if item is None or type(item) in (bool, str):
            return True
        if type(item) in (int, float):
            return finite_number(item)
        if isinstance(item, Mapping):
            return all(isinstance(key, str) and valid(val) for key, val in item.items())
        return isinstance(item, (list, tuple)) and all(valid(val) for val in item)
    return valid(value)


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _on_segment(point, a, b):
    return (abs(_cross(a, b, point)) <= 1e-9 and
            min(a[0], b[0])-1e-9 <= point[0] <= max(a[0], b[0])+1e-9 and
            min(a[1], b[1])-1e-9 <= point[1] <= max(a[1], b[1])+1e-9)


def _intersects(a, b, c, d):
    first, second, third, fourth = _cross(a,b,c), _cross(a,b,d), _cross(c,d,a), _cross(c,d,b)
    return ((first*second < 0 and third*fourth < 0) or
            any(_on_segment(p, x, y) for p, x, y in ((c,a,b), (d,a,b), (a,c,d), (b,c,d))))


def polygon_error(points):
    if not isinstance(points, (list, tuple)) or len(points) < 3 or not all(finite_xyz(p) for p in points):
        return "region polygon requires at least three finite XYZ vertices"
    if max(p[2] for p in points)-min(p[2] for p in points) > 1e-6:
        return "region footprint must be horizontal; use separate regions for different floors"
    count = len(points)
    if len({tuple(p[:2]) for p in points}) != count:
        return "region polygon vertices must be distinct (do not repeat the closing vertex)"
    area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points, (*points[1:], points[0])))
    if abs(area) <= 1e-9:
        return "region polygon has zero XY area"
    for i in range(count):
        for j in range(i+1, count):
            if j == i+1 or (i == 0 and j == count-1):
                continue
            if _intersects(points[i], points[(i+1)%count], points[j], points[(j+1)%count]):
                return "region polygon must not self-intersect"
    return None


def point_in_region(region, position):
    """Test the annotated prism, including its boundary; not physical access."""
    if not finite_xyz(position):
        raise ValueError("position must contain finite XYZ")
    points = region["polygon_xyz"]
    if not points[0][2]-1e-9 <= position[2] <= points[0][2]+region["height_m"]+1e-9:
        return False
    inside = False
    x, y = position[:2]
    for a, b in zip(points, (*points[1:], points[0])):
        if _on_segment(position, a, b):
            return True
        if (a[1] > y) != (b[1] > y):
            crossing = a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x < crossing:
                inside = not inside
    return inside


def validate_semantics(document, value=None):
    """Validate annotations and all graph/semantic references without Isaac."""
    data = document.semantics if value is None else value
    if data is None:
        return []
    if (not isinstance(data, Mapping) or data.get("schema") != "eai.scene.semantics"
            or type(data.get("version")) is not int or data["version"] != 1):
        return ["unsupported scene semantics"]
    errors = []
    if not _json_object(data):
        errors.append("semantics must contain only finite JSON values")
    if not _json_object(data.get("extensions", {})):
        errors.append("semantic extensions must be a finite JSON object")
    rows, all_ids = {}, set()
    for collection in COLLECTIONS:
        items = data.get(collection)
        rows[collection] = {}
        if not isinstance(items, list):
            errors.append(f"semantic {collection} must be an array")
            continue
        for row in items:
            if not isinstance(row, Mapping) or not _identifier(row.get("id")):
                errors.append(f"invalid {collection} record ID")
                continue
            identifier = row["id"]
            if identifier in all_ids:
                errors.append(f"duplicate semantic ID: {identifier}")
            all_ids.add(identifier)
            rows[collection][identifier] = row
            if not _identifier(row.get("kind")) or not isinstance(row.get("label", ""), str):
                errors.append(f"invalid semantic kind/label: {identifier}")
            if not _strings(row.get("aliases", [])):
                errors.append(f"invalid semantic aliases: {identifier}")
            if not _json_object(row.get("properties", {})):
                errors.append(f"semantic properties must be finite JSON: {identifier}")
    nodes = {row["id"]: row for row in (document.nodes or [])
             if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    edges = {row["id"]: row for row in (document.edges or [])
             if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    levels = {row["id"]: row for row in (document.levels or [])
              if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    def level_check(row, position):
        level = row.get("level_id")
        if level is not None:
            if not isinstance(level, str) or level not in levels:
                errors.append(f"unknown semantic level: {row['id']}")
            elif (finite_xyz(position) and finite_number(levels[level].get("support_z_m"))
                  and abs(position[2]-levels[level]["support_z_m"]) > .20):
                errors.append(f"semantic support height differs from level: {row['id']}")
    def owner_level_check(row, owner):
        owner_level = owner.get("level_id")
        if owner_level is None and isinstance(owner.get("region_id"), str):
            owner_level = rows["regions"].get(owner["region_id"], {}).get("level_id")
        if row.get("level_id") is not None and owner_level is not None and row["level_id"] != owner_level:
            errors.append(f"semantic floor differs from owner/region: {row['id']}")
    for identifier, row in rows["regions"].items():
        problem = polygon_error(row.get("polygon_xyz"))
        if problem:
            errors.append(f"{identifier}: {problem}")
        else:
            level_check(row, row["polygon_xyz"][0])
        if not finite_number(row.get("height_m")) or row["height_m"] <= 0:
            errors.append(f"region height_m must be positive: {identifier}")
        entrances = row.get("entrance_node_ids", [])
        if not _strings(entrances):
            errors.append(f"invalid region entrances: {identifier}")
        else:
            for node_id in entrances:
                if node_id not in nodes:
                    errors.append(f"unknown entrance node {node_id}: {identifier}")
                elif row.get("level_id") is not None and nodes[node_id].get("level_id") != row["level_id"]:
                    errors.append(f"entrance node is on another level: {identifier}")
    for identifier, row in rows["entities"].items():
        level_check(row, None)
        region = row.get("region_id")
        if region is not None and (not isinstance(region, str) or region not in rows["regions"]):
            errors.append(f"unknown entity region: {identifier}")
        elif region is not None:
            owner_level_check(row, rows["regions"][region])
        if row.get("position_xyz") is not None and not finite_xyz(row["position_xyz"]):
            errors.append(f"invalid entity position: {identifier}")
        if row.get("source_ref") is not None and not _identifier(row["source_ref"]):
            errors.append(f"invalid entity source_ref: {identifier}")
    for identifier, row in rows["interaction_poses"].items():
        owner = row.get("owner_id")
        if not isinstance(owner, str) or owner not in {**rows["regions"], **rows["entities"]}:
            errors.append(f"unknown interaction owner: {identifier}")
        else:
            owner_level_check(row, {**rows["regions"], **rows["entities"]}[owner])
        if not finite_xyz(row.get("position_xyz")):
            errors.append(f"invalid interaction XYZ: {identifier}")
        # Interaction Z may be a tabletop/hand height, not the floor height.
        if row.get("level_id") is not None and (not isinstance(row["level_id"], str) or row["level_id"] not in levels):
            errors.append(f"unknown interaction level: {identifier}")
        node_id = row.get("node_id")
        if node_id is not None and (not isinstance(node_id, str) or node_id not in nodes):
            errors.append(f"unknown interaction navigation node: {identifier}")
        elif node_id is not None:
            floor = semantic_level(row, {**rows["regions"], **rows["entities"]})
            if floor is not None and nodes[node_id].get("level_id") != floor:
                errors.append(f"interaction navigation node is on another level: {identifier}")
        heading = row.get("heading_rad")
        if heading is not None and (not finite_number(heading) or abs(heading) > math.pi):
            errors.append(f"invalid interaction heading: {identifier}")
    for identifier, row in rows["routes"].items():
        route_nodes, route_edges = row.get("node_ids"), row.get("edge_ids")
        if not _strings(route_nodes) or len(route_nodes) < 2:
            errors.append(f"named route needs at least two distinct nodes: {identifier}")
            continue
        if not _strings(route_edges) or len(route_edges) != len(route_nodes)-1:
            errors.append(f"named route needs one edge per node segment: {identifier}")
            continue
        for first, last, edge_id in zip(route_nodes, route_nodes[1:], route_edges):
            edge = edges.get(edge_id)
            if first not in nodes or last not in nodes or edge is None:
                errors.append(f"named route has missing nodes/edges: {identifier}")
            elif not ((edge.get("from") == first and edge.get("to") == last) or
                      (not edge.get("directed", False) and edge.get("from") == last and edge.get("to") == first)):
                errors.append(f"named route contradicts edge direction/connectivity: {identifier}")
            elif isinstance(row.get("kind"), str) and row["kind"] in {"walk", "stairs", "ramp"} and edge.get("traversal_mode", "walk") != row["kind"]:
                errors.append(f"named route traversal differs from its kind: {identifier}")
    return sorted(set(errors))


def semantic_inventory(document, collection=None):
    errors = validate_semantics(document)
    if errors:
        raise ValueError("; ".join(errors))
    if collection is not None and collection not in COLLECTIONS:
        raise ValueError("unknown semantic collection")
    data = document.semantics or empty_semantics()
    names = (collection,) if collection else COLLECTIONS
    return [dict(deepcopy(row), collection=name) for name in names
            for row in sorted(data[name], key=lambda item: item["id"])]


def resolve_semantic(document, query, *, collection=None, level_id=None):
    if not _identifier(query):
        raise ValueError("query must be a nonempty ID or label")
    inventory = semantic_inventory(document)
    records = {row["id"]: row for row in inventory}
    rows = [row for row in inventory
            if (collection is None or row["collection"] == collection)
            and (level_id is None or semantic_level(row, records) == level_id)]
    if collection is not None and collection not in COLLECTIONS:
        raise ValueError("unknown semantic collection")
    matches = [row for row in rows if row["id"] == query]
    if not matches:
        key = query.casefold()
        matches = [row for row in rows if key in {name.strip().casefold()
                   for name in (row.get("label", ""), *row.get("aliases", []))}]
    if len(matches) != 1:
        raise ValueError(f"{'ambiguous' if matches else 'unknown'} semantic location: {query}")
    return matches[0]


class SemanticAuthoringMixin:
    def _check_semantic_graph_edit(self, *, nodes=None, edges=None, levels=None):
        """Referenced graph edits must leave an editable map reloadable."""
        if self.semantics is None:
            return
        from copy import copy
        from .product import validate_document
        candidate = copy(self)
        if nodes is not None:
            candidate.nodes = nodes
        if edges is not None:
            candidate.edges = edges
        if levels is not None:
            candidate.version = 2
            candidate.levels = levels
        errors = validate_document(candidate)
        if errors:
            raise ValueError("graph edit invalidates semantic map: " + "; ".join(errors))

    def replace_semantics(self, data):
        if not isinstance(data, Mapping):
            raise ValueError("semantics must be an object")
        candidate = deepcopy(data)
        errors = validate_semantics(self, candidate)
        if errors:
            raise ValueError("; ".join(errors))
        self._record()
        self.semantics = candidate

    def upsert_semantic(self, collection, record):
        if collection not in COLLECTIONS or not isinstance(record, Mapping):
            raise ValueError("expected a semantic collection and record object")
        candidate = deepcopy(self.semantics or empty_semantics())
        rows = candidate[collection]
        replacement = deepcopy(dict(record))
        for index, row in enumerate(rows):
            if row["id"] == replacement.get("id"):
                rows[index] = replacement
                break
        else:
            rows.append(replacement)
        self.replace_semantics(candidate)
        return replacement["id"]

    def remove_semantic(self, collection, identifier):
        if collection not in COLLECTIONS:
            raise ValueError("unknown semantic collection")
        candidate = deepcopy(self.semantics or empty_semantics())
        rows = candidate[collection]
        if not any(row["id"] == identifier for row in rows):
            raise KeyError(identifier)
        candidate[collection] = [row for row in rows if row["id"] != identifier]
        self.replace_semantics(candidate)

    def semantic_references(self, *, node_id=None, edge_id=None):
        """Prevent edits from silently destroying region/pose/route bindings."""
        data = self.semantics or empty_semantics()
        refs = []
        for row in data["regions"]:
            if node_id is not None and node_id in row.get("entrance_node_ids", []):
                refs.append(row["id"])
        for row in data["interaction_poses"]:
            if node_id is not None and node_id == row.get("node_id"):
                refs.append(row["id"])
        for row in data["routes"]:
            if ((node_id is not None and node_id in row["node_ids"]) or
                    (edge_id is not None and edge_id in row["edge_ids"])):
                refs.append(row["id"])
        return sorted(set(refs))

    def add_waypoint_route(self, *, route_id, label, start_node_id, end_node_id,
                           intermediate_xyz=(), traversal_mode="walk", directed=False,
                           required_controller_capabilities=()):
        """Commit a complete waypoint chain as one undoable graph+semantic edit."""
        from .model import TopologyDocument
        from .product import validate_document
        if self.version != 2:
            raise ValueError("waypoint drawing requires declared levels (topology v2)")
        if not _identifier(route_id):
            raise ValueError("route ID must be a nonempty stable identifier")
        if any(row["id"] == route_id for row in (self.semantics or empty_semantics())["routes"]):
            raise ValueError("route ID already exists")
        candidate = TopologyDocument.from_dict(self.to_dict())
        start, end = candidate.node(start_node_id), candidate.node(end_node_id)
        if start_node_id == end_node_id:
            raise ValueError("route endpoints must differ")
        node_ids = [start_node_id]
        level = start.get("level_id") if traversal_mode == "walk" else None
        for index, point in enumerate(intermediate_xyz, 1):
            node_ids.append(candidate.add_node(point, node_id=f"{route_id}:point:{index:04d}",
                kind="custom" if level is not None else "transition", level_id=level))
        node_ids.append(end_node_id)
        edge_ids = []
        for index, (first, last) in enumerate(zip(node_ids, node_ids[1:]), 1):
            edge_ids.append(candidate.add_edge(first, last,
                [candidate.node(first)["position_xyz"], candidate.node(last)["position_xyz"]],
                edge_id=f"{route_id}:edge:{index:04d}", directed=directed,
                traversal_mode=traversal_mode,
                required_controller_capabilities=required_controller_capabilities))
        candidate.upsert_semantic("routes", {"id": route_id, "label": label,
            "kind": traversal_mode, "aliases": [], "node_ids": node_ids,
            "edge_ids": edge_ids, "properties": {}})
        errors = validate_document(candidate)
        if errors:
            raise ValueError("; ".join(errors))
        self._record()
        self._restore_snapshot(candidate._snapshot())
        self.cancel_preview()
        return route_id
