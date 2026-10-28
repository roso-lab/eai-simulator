"""Nonspatial project annotations bound to ground and aerial navigation nodes."""
from copy import deepcopy
import math
import uuid

SCHEMA = "eai.semantic_points"
FIELDS = {"id", "label", "kind", "description", "aliases",
          "properties", "navigation_bindings"}


def migrate_points(points):
    """Drop spatial fields and Isaac bindings only when reading legacy products."""
    if not isinstance(points, list):
        return points
    return [{key: deepcopy(value) for key, value in point.items()
             if key not in {"isaac_binding", "position_xyz", "edit_mode"}} if isinstance(point, dict) else point
            for point in points]


def coordinates(value, size=3):
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError(f"expected {size} finite coordinates")
    return list(value)


def normalize_navigation_bindings(value):
    """Validate and canonicalize project navigation-node bindings."""
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError("navigation_bindings must be an array")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"domain", "node_id"}:
            raise ValueError("navigation binding must contain domain and node_id")
        domain, node_id = item["domain"], item["node_id"]
        if domain not in ("ground", "aerial"):
            raise ValueError("navigation binding domain must be ground or aerial")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("navigation binding node_id must be a nonempty string")
        key = (domain, node_id)
        if key in seen:
            raise ValueError("duplicate navigation binding")
        seen.add(key)
        result.append({"domain": domain, "node_id": node_id})
    return sorted(result, key=lambda row: (row["domain"], row["node_id"]))


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(v) for v in value)
    return isinstance(value, dict) and all(isinstance(k, str) and _json_value(v) for k, v in value.items())


def validate_points(points, source_path=None):
    if not isinstance(points, list):
        raise ValueError("semantic_points must be an array")
    seen = set()
    for point in points:
        if not isinstance(point, dict) or set(point) - FIELDS:
            raise ValueError("invalid semantic point fields")
        identifier = point.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier != identifier.strip() or identifier in seen:
            raise ValueError("invalid or duplicate semantic point ID")
        seen.add(identifier)
        for field in ("label", "kind", "description"):
            if not isinstance(point.get(field, ""), str):
                raise ValueError(f"semantic point {field} must be text")
        if not point.get("kind", "").strip():
            raise ValueError("semantic point kind is required")
        aliases = point.get("aliases", [])
        if (not isinstance(aliases, list) or any(not isinstance(v, str) or not v.strip() for v in aliases)
                or len(set(aliases)) != len(aliases)):
            raise ValueError("semantic aliases must be unique nonempty strings")
        if not isinstance(point.get("properties", {}), dict) or not _json_value(point.get("properties", {})):
            raise ValueError("semantic properties must be a finite JSON object")
        point["navigation_bindings"] = normalize_navigation_bindings(point.get("navigation_bindings", []))
    return points


def edit_points(points, operation, args, source_path=None):
    if set(args) & {"position_xyz", "position_xy", "edit_mode"}:
        raise ValueError("semantic annotations have no spatial coordinates or edit mode")
    if "isaac_binding" in args or "binding" in args:
        raise ValueError("Isaac object bindings are no longer supported")
    result = deepcopy(points)
    if operation == "add_semantic_point":
        record = {"id": args.get("id") or "semantic:" + uuid.uuid4().hex,
                  "label": "", "kind": "custom", "description": "", "aliases": [],
                  "properties": {},
                  "navigation_bindings": []}
        for name in FIELDS - {"id", "navigation_bindings"}:
            if name in args:
                record[name] = deepcopy(args[name])
        result.append(record)
    else:
        record = next((p for p in result if p["id"] == args.get("id")), None)
        if record is None:
            raise ValueError("unknown semantic point")
        if operation == "update_semantic_point":
            for name in FIELDS - {"id", "navigation_bindings"}:
                if name in args:
                    record[name] = deepcopy(args[name])
        elif operation == "remove_semantic_point":
            result.remove(record)
        elif operation == "set_semantic_point_navigation_bindings":
            record["navigation_bindings"] = normalize_navigation_bindings(args.get("bindings", []))
        else:
            raise ValueError("unknown semantic point operation")
    return validate_points(result)
