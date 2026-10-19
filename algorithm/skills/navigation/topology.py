"""Canonical, controller independent node topology contract.

Ground and aerial navigation products use the same JSON representation.  A
product is still owned by one map (``domain`` is authoring metadata), while the
navigation profile chooses whether distances are measured in XY or XYZ.
Traversal labels and controller capabilities are executable admission
constraints when a controller profile declares its capabilities; the motion
adapter still decides how to execute each accepted node-to-node command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Mapping


LEVEL_HEIGHT_TOLERANCE_M = 0.20
TRAVERSAL_MODES = frozenset({"walk", "stairs", "ramp"})


def finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def finite_xyz(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(
        finite_number(item) for item in value
    )


def topology_fields(document: Any) -> Mapping[str, Any]:
    if isinstance(document, Mapping):
        return document
    names = (
        "schema", "scene_key", "source", "nodes", "edges", "coordinate_frame",
        "meters_per_unit", "up_axis", "version", "levels", "domain", "semantics",
    )
    return {name: getattr(document, name) for name in names if hasattr(document, name)}


def _strings(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def _semantic_navigation_errors(data: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]],
                               edges: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Check the semantic fields that can change executable navigation.

    Full semantic record validation lives in the authoring package.  Runtime
    navigation still needs a small, dependency-free guard so a malformed map
    cannot silently fall back to a raw node target.
    """
    semantics = data.get("semantics")
    if semantics is None:
        return []
    if (not isinstance(semantics, Mapping)
            or semantics.get("schema") != "eai.scene.semantics"
            or type(semantics.get("version")) is not int
            or semantics.get("version") != 1):
        return ["unsupported scene semantics"]
    errors: list[str] = []
    seen: set[str] = set()
    collections = ("regions", "entities", "interaction_poses", "routes")
    rows: dict[str, dict[str, Mapping[str, Any]]] = {}
    for collection in collections:
        values = semantics.get(collection)
        if not isinstance(values, list):
            errors.append(f"semantic {collection} must be an array")
            rows[collection] = {}
            continue
        current: dict[str, Mapping[str, Any]] = {}
        for row in values:
            identifier = row.get("id") if isinstance(row, Mapping) else None
            if not isinstance(identifier, str) or not identifier.strip():
                errors.append(f"invalid {collection} record ID")
                continue
            if identifier in seen:
                errors.append(f"duplicate semantic ID: {identifier}")
            seen.add(identifier)
            current[identifier] = row
        rows[collection] = current
    for identifier, row in rows.get("regions", {}).items():
        entrances = row.get("entrance_node_ids", [])
        if (not isinstance(entrances, (list, tuple))
                or any(not isinstance(node_id, str) or node_id not in nodes for node_id in entrances)):
            errors.append(f"invalid semantic region entrances: {identifier}")
    for identifier, row in rows.get("interaction_poses", {}).items():
        node_id = row.get("node_id")
        if node_id is not None and (not isinstance(node_id, str) or node_id not in nodes):
            errors.append(f"unknown interaction navigation node: {identifier}")
    for identifier, row in rows.get("routes", {}).items():
        node_ids, edge_ids = row.get("node_ids"), row.get("edge_ids")
        if (not isinstance(node_ids, (list, tuple)) or len(node_ids) < 2
                or any(node_id not in nodes for node_id in node_ids)
                or not isinstance(edge_ids, (list, tuple))
                or len(edge_ids) != len(node_ids) - 1
                or any(edge_id not in edges for edge_id in edge_ids)):
            errors.append(f"invalid semantic route references: {identifier}")
            continue
        for first, last, edge_id in zip(node_ids, node_ids[1:], edge_ids):
            edge = edges[edge_id]
            if not ((edge.get("from") == first and edge.get("to") == last)
                    or (not edge.get("directed", False)
                        and edge.get("from") == last and edge.get("to") == first)):
                errors.append(f"semantic route contradicts edge direction/connectivity: {identifier}")
    return sorted(set(errors))


def validate_topology(document: Any, *, metric: str | None = None) -> list[str]:
    """Validate the shared structural product format."""
    try:
        if metric is not None and metric not in {"xy", "xyz"}:
            raise ValueError("topology metric must be xy or xyz")
        return _validate_topology(document)
    except (AttributeError, TypeError, ValueError):
        return ["topology product must be an object"]


def _validate_topology(document: Any) -> list[str]:
    data = topology_fields(document)
    errors: list[str] = []
    if data.get("schema", "eai.navigation.topology") != "eai.navigation.topology":
        errors.append("unsupported topology schema")
    version = data.get("version", 1)
    if type(version) is not int or version not in {1, 2}:
        errors.append("unsupported topology version")
        version = 1
    domain = data.get("domain")
    if domain is not None and domain not in {"ground", "aerial"}:
        errors.append("domain must be ground or aerial")
    if data.get("coordinate_frame", "scene_world") != "scene_world":
        errors.append("topology coordinate_frame must be scene_world")
    units = data.get("meters_per_unit", 1.0)
    if not finite_number(units) or float(units) != 1.0:
        errors.append("topology meters_per_unit must be 1")
    if data.get("up_axis", "Z") != "Z":
        errors.append("topology up_axis must be Z")

    levels: dict[str, float] = {}
    level_rows = data.get("levels", [])
    if level_rows is None:
        level_rows = []
    if not isinstance(level_rows, (list, tuple)):
        errors.append("topology levels must be an array")
        level_rows = []
    for row in level_rows:
        valid = isinstance(row, Mapping)
        identifier = row.get("id") if valid else None
        height = row.get("support_z_m") if valid else None
        if (not isinstance(identifier, str) or not identifier.strip()
                or identifier in levels or not isinstance(row.get("label", ""), str)
                or not finite_number(height)):
            errors.append("invalid or duplicate topology level")
            continue
        levels[identifier] = float(height)

    rows = data.get("nodes")
    edges = data.get("edges")
    if not isinstance(rows, (list, tuple)) or not isinstance(edges, (list, tuple)):
        return errors + ["topology nodes and edges must be arrays"]

    nodes: dict[str, tuple[float, float, float]] = {}
    node_rows: dict[str, Mapping[str, Any]] = {}
    bound_refs: dict[str, set[str]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append(f"invalid node object: {index}")
            continue
        node_id = row.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append(f"duplicate/invalid node id: {node_id!r}")
            continue
        if node_id in nodes:
            errors.append(f"duplicate/invalid node id: {node_id!r}")
        point = row.get("position_xyz")
        if not finite_xyz(point):
            errors.append(f"invalid node position: {node_id}")
            continue
        refs = row.get("target_refs", [])
        if not isinstance(refs, (list, tuple)) or any(
            not isinstance(ref, str) or not ref.strip() for ref in refs
        ):
            errors.append(f"invalid node target references: {node_id}")
        else:
            # A reference can be present on more than one node.  The provider
            # reports that condition as a target_ambiguous admission error;
            # rejecting it here would make that public error unreachable.
            for ref in set(refs):
                bound_refs.setdefault(ref, set()).add(node_id)
        heading = row.get("heading_rad")
        if heading is not None and (version != 2 or not finite_number(heading)
                                    or abs(float(heading)) > math.pi + 1e-9):
            errors.append(f"node heading requires v2 normalized radians: {node_id}")
        level_id = row.get("level_id")
        if level_id is not None and (not isinstance(level_id, str) or level_id not in levels):
            errors.append(f"unknown node level: {node_id}")
        elif (level_id is not None and finite_number(point[2])
              and abs(float(point[2]) - levels[level_id]) > LEVEL_HEIGHT_TOLERANCE_M):
            errors.append(f"node support height differs from declared level: {node_id}")
        if version == 2 and (not isinstance(row.get("label", ""), str)
                             or not _strings(row.get("aliases", []))):
            errors.append(f"invalid node landmark metadata: {node_id}")
        nodes[node_id] = tuple(float(item) for item in point)
        node_rows[node_id] = row

    node_ids = set(nodes)
    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            errors.append(f"invalid edge object: {index}")
            continue
        edge_id = edge.get("id")
        first, last = edge.get("from"), edge.get("to")
        if not isinstance(edge_id, str) or not edge_id.strip() or edge_id in edge_ids:
            errors.append(f"duplicate/invalid edge id: {edge_id!r}")
            continue
        edge_ids.add(edge_id)
        if (not isinstance(first, str) or not isinstance(last, str)
                or first not in node_ids or last not in node_ids or first == last):
            errors.append(f"invalid edge endpoints: {edge_id}")
        if type(edge.get("directed", False)) is not bool:
            errors.append(f"invalid edge direction: {edge_id}")
        polyline = edge.get("polyline_xyz")
        if polyline is not None and (not isinstance(polyline, (list, tuple))
                                     or any(not finite_xyz(point) for point in polyline)):
            errors.append(f"invalid edge polyline: {edge_id}")
        traversal_mode = edge.get("traversal_mode", "walk")
        capabilities = edge.get("required_controller_capabilities", [])
        if traversal_mode not in TRAVERSAL_MODES:
            errors.append(f"invalid edge traversal_mode: {edge_id}")
        if (not isinstance(capabilities, (list, tuple))
                or any(not isinstance(item, str) or not item.strip() for item in capabilities)
                or len(set(capabilities)) != len(capabilities)):
            errors.append(f"invalid edge controller capabilities: {edge_id}")
        if version != 2 and (traversal_mode != "walk" or capabilities):
            errors.append(f"edge traversal metadata requires topology version 2: {edge_id}")
        if (data.get("domain", "ground") == "ground" and first in nodes and last in nodes
                and math.dist(nodes[first][:2], nodes[last][:2]) <= 1e-12):
            errors.append(f"ground topology edge must have nonzero XY length: {edge_id}")
        mode = edge.get("traversal_mode", "walk")
        # Floor-crossing checks describe ground traversal.  Aerial v2 maps
        # are also explicit XYZ graphs, but they do not need declared building
        # levels for every waypoint and may use the default walk label for a
        # free-flight edge.
        if (data.get("domain", "ground") == "ground" and version == 2
                and mode == "walk" and first in node_rows and last in node_rows):
            first_level, last_level = node_rows[first].get("level_id"), node_rows[last].get("level_id")
            if (first_level is None or last_level is None or first_level != last_level
                    or abs(nodes[first][2] - nodes[last][2]) > LEVEL_HEIGHT_TOLERANCE_M):
                errors.append(f"walk edge cannot cross levels or transitions: {edge_id}")
    edge_rows = {edge["id"]: edge for edge in edges
                 if isinstance(edge, Mapping) and isinstance(edge.get("id"), str)}
    errors.extend(_semantic_navigation_errors(data, node_rows, edge_rows))
    return sorted(set(errors))


def _canonical_json(value: Any) -> Any:
    """Return a deterministic JSON-safe semantic value.

    Authoring order is irrelevant for route identity.  Decorative geometry is
    removed by :func:`_canonical_graph`; remaining arrays are sorted whenever
    their members are JSON objects or scalar strings.
    """
    if isinstance(value, Mapping):
        return {str(key): _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        values = [_canonical_json(item) for item in value]
        try:
            return sorted(values, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        except TypeError:
            return values
    return value


def _canonical_graph(document: Any) -> dict[str, Any]:
    data = topology_fields(document)
    version = data.get("version", 1)
    nodes = []
    for row in data.get("nodes", ()):
        item = {
            "id": row["id"],
            "position_xyz": [float(value) for value in row["position_xyz"]],
            "target_refs": sorted(set(row.get("target_refs", ()))),
        }
        if version == 2:
            item.update({
                "kind": row.get("kind", "custom"),
                "level_id": row.get("level_id"),
                "label": row.get("label", ""),
                "aliases": sorted(set(row.get("aliases", ()))),
            })
            if row.get("heading_rad") is not None:
                item["heading_rad"] = float(row["heading_rad"])
        nodes.append(item)
    edges = []
    for row in data.get("edges", ()):
        # Keep route-affecting/authoring semantic metadata in the frozen graph
        # identity while dropping the editable polyline and scene-only fields.
        item = {
            "id": row["id"], "from": row["from"], "to": row["to"],
            "directed": bool(row.get("directed", False)),
            "kind": row.get("kind", "custom_connection"),
        }
        for key in ("profiles", "traversal_mode", "required_controller_capabilities", "metadata"):
            if key in row:
                item[key] = _canonical_json(row[key])
        edges.append(item)
    payload = {
        "schema": f"eai.navigation.node_route.v{2 if version == 2 else 1}",
        "domain": data.get("domain", "ground"),
        "coordinate_frame": data.get("coordinate_frame", "scene_world"),
        "meters_per_unit": 1.0, "up_axis": "Z",
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: item["id"]),
    }
    if version == 2:
        payload["levels"] = sorted(({
            "id": row["id"], "label": row.get("label", ""),
            "support_z_m": float(row["support_z_m"]),
        } for row in data.get("levels", ())), key=lambda item: item["id"])
    semantics = data.get("semantics")
    if isinstance(semantics, Mapping):
        # Region entrances and authored named routes are navigation bindings.
        # Entity/interaction geometry is task data and intentionally does not
        # invalidate an executable graph when it is edited later.
        semantic_payload = {}
        regions = semantics.get("regions", ())
        if isinstance(regions, (list, tuple)):
            semantic_payload["regions"] = sorted(({
                "id": row.get("id"),
                "level_id": row.get("level_id"),
                "entrance_node_ids": sorted(set(row.get("entrance_node_ids", ()))),
            } for row in regions if isinstance(row, Mapping)), key=lambda item: item["id"])
        routes = semantics.get("routes", ())
        if isinstance(routes, (list, tuple)):
            semantic_payload["routes"] = sorted(({
                "id": row.get("id"), "kind": row.get("kind"),
                "node_ids": list(row.get("node_ids", ())),
                "edge_ids": list(row.get("edge_ids", ())),
            } for row in routes if isinstance(row, Mapping)), key=lambda item: item["id"])
        if semantic_payload:
            payload["semantic_navigation_bindings"] = semantic_payload
    return payload


def route_graph_digest(document: Any) -> str:
    errors = validate_topology(document)
    if errors:
        raise ValueError("; ".join(errors))
    payload = _canonical_graph(document)
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FrozenNavigationTopology:
    """Immutable scene-bound topology product."""

    scene_key: str
    product_digest: str
    route_graph_digest: str
    _json: str = field(repr=False)

    def __init__(self, document: Any, *, scene_key: str,
                 source_digests: Mapping[str, str]):
        data = dict(topology_fields(document))
        errors = validate_topology(data)
        if errors:
            raise ValueError("; ".join(errors))
        if len(data.get("nodes", ())) < 2 or not data.get("edges"):
            raise ValueError("frozen navigation topology must contain nodes and edges")
        if not isinstance(scene_key, str) or not scene_key.strip():
            raise ValueError("scene_key is required")
        if data.get("scene_key") not in (None, scene_key):
            raise ValueError("navigation topology belongs to another scene")
        if data.get("source", {}) != dict(source_digests):
            raise ValueError("navigation topology source inventory differs from scene")
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        graph = route_graph_digest(data)
        product = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        object.__setattr__(self, "scene_key", scene_key)
        object.__setattr__(self, "product_digest", product)
        object.__setattr__(self, "route_graph_digest", graph)
        object.__setattr__(self, "_json", payload)

    def snapshot(self) -> dict[str, Any]:
        return json.loads(self._json)


__all__ = [
    "FrozenNavigationTopology", "LEVEL_HEIGHT_TOLERANCE_M",
    "finite_number", "finite_xyz", "route_graph_digest", "topology_fields",
    "validate_topology", "TRAVERSAL_MODES",
]
