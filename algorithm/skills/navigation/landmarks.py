"""Resolve authored navigation landmarks and semantic-map locations.

The executable graph remains the source of route geometry.  Semantic records
provide stable names and bindings to that graph: regions resolve to entrance
nodes, interaction poses resolve to their approach node (or their owner's
region entrance), entities resolve through an approach pose or region, and a
named route resolves to its ordered endpoint/waypoint chain.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ..contracts import SkillContractError
from .node_route import (
    NavigationPose,
    NavigationProfile,
    aerial_navigation_profile,
    ground_navigation_profile,
    select_node_route,
)
from .topology import topology_fields, validate_topology


_SEMANTIC_COLLECTIONS = ("regions", "entities", "interaction_poses", "routes")


def _data(document: Any) -> Mapping[str, Any]:
    errors = validate_topology(document)
    if errors:
        raise SkillContractError("invalid topology: " + "; ".join(errors))
    return topology_fields(document)


def _semantic_records(data: Mapping[str, Any]) -> tuple[dict[str, tuple[str, Mapping[str, Any]]], bool]:
    semantics = data.get("semantics")
    if semantics is None:
        return {}, False
    if (not isinstance(semantics, Mapping)
            or semantics.get("schema") != "eai.scene.semantics"
            or type(semantics.get("version")) is not int
            or semantics.get("version") != 1):
        raise SkillContractError("invalid scene semantics")
    records: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for collection in _SEMANTIC_COLLECTIONS:
        rows = semantics.get(collection, [])
        if not isinstance(rows, list):
            raise SkillContractError(f"semantic {collection} must be an array")
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
                raise SkillContractError(f"invalid semantic {collection} record")
            identifier = row["id"]
            if identifier in records:
                raise SkillContractError(f"duplicate semantic ID: {identifier}")
            records[identifier] = (collection, row)
    return records, True


def _semantic_node_ids(records: Mapping[str, tuple[str, Mapping[str, Any]]],
                       identifier: str, *, role: str = "target",
                       seen: set[str] | None = None) -> tuple[str, ...]:
    """Resolve a semantic record recursively to executable approach nodes."""
    if identifier not in records:
        return ()
    seen = set() if seen is None else seen
    if identifier in seen:
        return ()
    seen.add(identifier)
    collection, row = records[identifier]
    if collection == "regions":
        values = row.get("entrance_node_ids", ())
        return tuple(str(value) for value in values if isinstance(value, str))
    if collection == "routes":
        values = row.get("node_ids", ())
        if not isinstance(values, (list, tuple)):
            return ()
        if role == "origin":
            values = values[:1]
        elif role == "target":
            values = values[-1:]
        return tuple(str(value) for value in values if isinstance(value, str))
    if collection == "interaction_poses":
        node_id = row.get("node_id")
        if isinstance(node_id, str) and node_id:
            return (node_id,)
        owner = row.get("owner_id")
        return (_semantic_node_ids(records, owner, role=role, seen=seen)
                if isinstance(owner, str) else ())
    if collection == "entities":
        # A task-specific approach pose is preferred over a generic room
        # entrance.  Stable order keeps equal-cost selection deterministic.
        poses = []
        for pose_id, (kind, pose) in sorted(records.items()):
            if kind == "interaction_poses" and pose.get("owner_id") == identifier:
                poses.extend(_semantic_node_ids(records, pose_id, role=role, seen=set(seen)))
        if poses:
            return tuple(dict.fromkeys(poses))
        region_id = row.get("region_id")
        return (_semantic_node_ids(records, region_id, role=role, seen=seen)
                if isinstance(region_id, str) else ())
    return ()


def _semantic_level(records: Mapping[str, tuple[str, Mapping[str, Any]]],
                    identifier: str, *, nodes: Mapping[str, Mapping[str, Any]] | None = None,
                    seen: set[str] | None = None) -> str | None:
    """Resolve a semantic record's inherited floor without importing authoring."""
    if identifier not in records:
        return None
    seen = set() if seen is None else seen
    if identifier in seen:
        return None
    seen.add(identifier)
    _collection, row = records[identifier]
    level = row.get("level_id")
    if isinstance(level, str) and level:
        return level
    if nodes is not None and _collection in {"regions", "routes", "interaction_poses"}:
        node_keys = (row.get("entrance_node_ids", ()) if _collection == "regions"
                     else row.get("node_ids", ()) if _collection == "routes"
                     else (row.get("node_id"),))
        route_levels = {nodes[node_id].get("level_id") for node_id in node_keys
                        if isinstance(node_id, str) and node_id in nodes
                        and nodes[node_id].get("level_id") is not None}
        if len(route_levels) == 1:
            return next(iter(route_levels))
    parent = row.get("owner_id") or row.get("region_id")
    if isinstance(parent, str):
        return _semantic_level(records, parent, nodes=nodes, seen=seen)
    return None


def _semantic_heading(records: Mapping[str, tuple[str, Mapping[str, Any]]],
                      identifier: str, *, node_id: str | None = None,
                      seen: set[str] | None = None) -> Any:
    """Return the most specific authored approach heading for a location."""
    if identifier not in records:
        return None
    seen = set() if seen is None else seen
    if identifier in seen:
        return None
    seen.add(identifier)
    _collection, row = records[identifier]
    if row.get("heading_rad") is not None:
        return row.get("heading_rad")
    if _collection == "entities":
        headings = []
        for pose_id, (kind, pose) in sorted(records.items()):
            if (kind == "interaction_poses" and pose.get("owner_id") == identifier
                    and (node_id is None or pose.get("node_id") == node_id)):
                heading = _semantic_heading(records, pose_id, seen=set(seen))
                if heading is not None:
                    headings.append(heading)
        if node_id is not None:
            unique_headings = set(headings)
            return next(iter(unique_headings)) if len(unique_headings) == 1 else None
        if headings:
            unique_headings = set(headings)
            return next(iter(unique_headings)) if len(unique_headings) == 1 else None
    parent = row.get("owner_id") or row.get("region_id")
    return (_semantic_heading(records, parent, seen=seen)
            if isinstance(parent, str) else None)


def resolve_landmark_binding(document: Any, query: str, *, level_id: str | None = None,
                             role: str = "target", semantic_points: Sequence[Mapping[str, Any]] | None = None,
                             domain: str | None = None) -> dict[str, Any]:
    """Return a normalized node binding for a node or semantic location."""
    if role not in {"origin", "target"}:
        raise SkillContractError("landmark role must be origin or target")
    data = _data(document)
    if not isinstance(query, str) or not query.strip():
        raise SkillContractError("landmark query must be a nonempty identifier or label")
    query = query.strip()
    if semantic_points is not None:
        matches = [row for row in semantic_points if isinstance(row, Mapping) and row.get("id") == query]
        if len(matches) > 1:
            raise SkillContractError(f"ambiguous landmark: {query!r}")
        if matches:
            if any(isinstance(row, Mapping) and row.get("id") == query
                   for row in data.get("nodes", ())):
                raise SkillContractError(f"ambiguous landmark: {query!r}")
            point = matches[0]
            if domain not in (None, "ground", "aerial"):
                raise SkillContractError("domain must be ground or aerial")
            bindings = [item for item in point.get("navigation_bindings", [])
                        if domain is None or item.get("domain") == domain]
            return {"id": query, "collection": "semantic_points", "record": deepcopy(dict(point)),
                    "node_ids": tuple(item["node_id"] for item in bindings),
                    "navigation_bindings": deepcopy(bindings), "label": point.get("label", ""),
                    "aliases": tuple(point.get("aliases", ())), "query": query}
    all_node_rows = {row.get("id"): row for row in data.get("nodes", ())
                     if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
    rows = list(all_node_rows.values())
    if level_id is not None:
        rows = [row for row in rows if row.get("level_id") == level_id]
    node_rows = {row.get("id"): row for row in rows if isinstance(row, Mapping)}
    records, has_semantics = _semantic_records(data)

    # Exact executable node IDs retain their long-standing precedence.
    if query in node_rows:
        row = deepcopy(dict(node_rows[query]))
        return {"id": query, "collection": "nodes", "record": row,
                "node_ids": (query,), "label": row.get("label", ""),
                "aliases": tuple(row.get("aliases", ())), "query": query}

    semantic_matches: list[tuple[str, Mapping[str, Any]]] = []
    if has_semantics:
        semantic_matches = [(identifier, row) for identifier, (_collection, row) in records.items()
                            if identifier == query and
                            (level_id is None or _semantic_level(records, identifier,
                                                                  nodes=all_node_rows) == level_id)]
        if not semantic_matches:
            key = query.casefold()
            semantic_matches = [(identifier, row) for identifier, (_collection, row) in records.items()
                                if (level_id is None or _semantic_level(records, identifier,
                                                                         nodes=all_node_rows) == level_id)
                                and key in {str(name).strip().casefold()
                                            for name in (row.get("label", ""), *row.get("aliases", ()))
                                            if isinstance(name, str) and name.strip()}]
        if len(semantic_matches) > 1:
            raise SkillContractError(f"ambiguous landmark: {query!r}")
        if semantic_matches:
            identifier, row = semantic_matches[0]
            collection = records[identifier][0]
            ids = _semantic_node_ids(records, identifier, role=role)
            if level_id is not None:
                ids = tuple(node_id for node_id in ids if node_id in node_rows)
            binding = {"id": identifier, "collection": collection, "record": deepcopy(dict(row)),
                       "node_ids": ids, "label": row.get("label", ""),
                       "aliases": tuple(row.get("aliases", ())), "query": query}
            heading = _semantic_heading(records, identifier)
            if heading is not None:
                binding["heading_rad"] = heading
            return binding

    # Legacy external target bindings and node labels remain valid when no
    # semantic record owns the query.
    matches = [row for row in rows if query in row.get("target_refs", ())]
    if not matches:
        key = query.casefold()
        matches = [row for row in rows if key in {
            name.strip().casefold() for name in (row.get("label", ""), *row.get("aliases", ()))
            if isinstance(name, str) and name.strip()
        }]
    if len(matches) != 1:
        raise SkillContractError(f"{'ambiguous' if len(matches) > 1 else 'unknown'} landmark: {query!r}")
    row = deepcopy(dict(matches[0]))
    return {"id": row["id"], "collection": "nodes", "record": row,
            "node_ids": (row["id"],), "label": row.get("label", ""),
            "aliases": tuple(row.get("aliases", ())), "query": query}


def landmark_inventory(document: Any) -> list[dict[str, Any]]:
    """Public navigation names and semantic locations."""
    data = _data(document)
    result = [{"node_id": row["id"], "refs": list(row.get("target_refs", [])),
               "label": row.get("label", ""), "aliases": list(row.get("aliases", [])),
               "level_id": row.get("level_id"), "position_xyz": list(row["position_xyz"]),
               **({"heading_rad": row.get("heading_rad")} if data.get("version") == 2 else {})}
              for row in sorted(data.get("nodes", ()), key=lambda row: row["id"])
              if row.get("target_refs") or row.get("label") or row.get("aliases")]
    records, has_semantics = _semantic_records(data)
    if not has_semantics:
        return result
    nodes = {row["id"]: row for row in data.get("nodes", ())}
    for identifier, (collection, row) in sorted(records.items()):
        ids = _semantic_node_ids(records, identifier)
        level = row.get("level_id")
        if level is None and ids:
            level = nodes.get(ids[0], {}).get("level_id")
        result.append({"node_id": ids[0] if len(ids) == 1 else None,
                      "node_ids": list(ids), "refs": [identifier],
                      "label": row.get("label", ""), "aliases": list(row.get("aliases", ())),
                      "level_id": level, "collection": collection,
                      "semantic_id": identifier})
    return result


def resolve_landmark(document: Any, query: str, *, level_id: str | None = None,
                     semantic_points: Sequence[Mapping[str, Any]] | None = None,
                     domain: str | None = None) -> Mapping[str, Any]:
    """Resolve a node, external target, or semantic map record."""
    binding = resolve_landmark_binding(document, query, level_id=level_id,
                                       semantic_points=semantic_points, domain=domain)
    row = dict(binding["record"])
    if binding["collection"] != "nodes":
        row.update({"collection": binding["collection"],
                    "node_ids": list(binding["node_ids"]),
                    "semantic_id": binding["id"]})
        if binding.get("heading_rad") is not None and row.get("heading_rad") is None:
            row["heading_rad"] = binding["heading_rad"]
    return row


def _default_capabilities(document: Any) -> tuple[str, ...]:
    """Preserve raw-graph compatibility while enforcing semantic routes."""
    data = topology_fields(document)
    semantics = data.get("semantics")
    if isinstance(semantics, Mapping) and any(
            isinstance(row, Mapping) and row.get("kind") in {"stairs", "ramp"}
            for row in semantics.get("routes", ())):
        return ("walk",)
    return ("walk", "stairs", "ramp")


def resolve_landmark_route(
    document: Any, *, origin_ref: str, destination_ref: str,
    origin_level_id: str | None = None, destination_level_id: str | None = None,
    profile: NavigationProfile | None = None,
    controller_capabilities: Sequence[str] | None = None,
    semantic_points: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve semantic locations and run the execution router."""
    data = _data(document)
    navigation_domain = data.get("domain", "ground")
    origin_binding = resolve_landmark_binding(document, origin_ref,
                                               level_id=origin_level_id, role="origin",
                                               semantic_points=semantic_points,
                                               domain=navigation_domain)
    destination_binding = resolve_landmark_binding(
        document, destination_ref, level_id=destination_level_id, role="target",
        semantic_points=semantic_points, domain=navigation_domain,
    )
    origin_nodes = tuple(origin_binding.get("node_ids", ()))
    destination_nodes = tuple(destination_binding.get("node_ids", ()))
    if not origin_nodes:
        raise SkillContractError(f"origin has no navigation entrance: {origin_ref!r}")
    if not destination_nodes and destination_binding.get("collection") != "semantic_points":
        raise SkillContractError(f"destination has no navigation entrance: {destination_ref!r}")
    nodes = {row["id"]: row for row in data.get("nodes", ())}
    origin_point = nodes[origin_nodes[0]]["position_xyz"]
    selected_profile = profile or (
        aerial_navigation_profile()
        if data.get("domain", "ground") == "aerial"
        else ground_navigation_profile()
    )
    capabilities = (tuple(controller_capabilities)
                    if controller_capabilities is not None
                    else tuple(getattr(selected_profile, "controller_capabilities", ())
                               or _default_capabilities(document)))
    result = select_node_route(
        document, NavigationPose(tuple(origin_point)), profile=selected_profile,
        origin_ref=origin_ref, target_ref=destination_ref,
        origin_node_ids=origin_nodes, target_node_ids=(destination_nodes or None),
        allowed_edge_ids=(destination_binding.get("record", {}).get("edge_ids")
                          if destination_binding.get("collection") == "routes" else None),
        controller_capabilities=capabilities,
        target_heading_rad=(
            None if destination_binding.get("collection") == "entities"
            else destination_binding.get(
                "heading_rad", destination_binding.get("record", {}).get("heading_rad"))),
        target_is_semantic=destination_binding.get("collection") != "nodes",
        origin_is_semantic=origin_binding.get("collection") != "nodes",
        semantic_points=semantic_points,
    )
    if semantic_points is not None:
        from .node_route import semantic_binding_digest
        result["semantic_binding_digest"] = semantic_binding_digest(semantic_points)
    selected_semantic_heading = None
    if result.get("admitted") and destination_binding.get("collection") == "entities":
        records, _ = _semantic_records(data)
        selected_node = result["topology_node_ids"][-1]
        selected_semantic_heading = _semantic_heading(
            records, destination_binding["id"], node_id=selected_node)
        if selected_semantic_heading is not None:
            result["target_heading_rad"] = selected_semantic_heading
    result["domain"] = data.get("domain", "ground")
    result["semantic_origin"] = {"id": origin_binding["id"], "collection": origin_binding["collection"]}
    result["semantic_destination"] = {"id": destination_binding["id"], "collection": destination_binding["collection"]}
    destination_record = destination_binding.get("record", {})
    result["authored_heading_rad"] = (selected_semantic_heading
                                        if selected_semantic_heading is not None
                                        else destination_record.get("heading_rad"))
    result.update(schema="eai.navigation.landmark_route", version=1,
                  origin_ref=origin_ref, destination_ref=destination_ref,
                  coordinate_frame="scene_world", meters_per_unit=1, up_axis="Z")
    if result.get("admitted"):
        rows = {row["id"]: row for row in data.get("nodes", ())}
        result["route_level_ids"] = [rows[node_id].get("level_id")
                                      for node_id in result["topology_node_ids"]]
        result["route_headings_rad"] = [rows[node_id].get("heading_rad")
                                         for node_id in result["topology_node_ids"]]
        result["already_at_target"] = False
        result["skill_parameters"] = {
            "origin_ref": origin_ref,
            "route_graph_digest": result["route_graph_digest"],
        }
        if semantic_points is not None:
            result["skill_parameters"]["semantic_binding_digest"] = result["semantic_binding_digest"]
        target_heading = result.get("target_heading_rad")
        if target_heading is not None and selected_profile.supports_final_heading:
            result["skill_parameters"]["target_yaw"] = target_heading
        result["target_ref"] = destination_ref
        result["controller_capabilities"] = sorted(set(capabilities))
    return result


__all__ = [
    "landmark_inventory", "resolve_landmark", "resolve_landmark_binding",
    "resolve_landmark_route",
]
