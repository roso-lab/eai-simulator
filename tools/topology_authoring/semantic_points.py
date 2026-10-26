"""Project annotations in world metres, independent of both navigation graphs."""
from copy import deepcopy
import math
import uuid

SCHEMA = "eai.semantic_points"
FIELDS = {"id", "position_xyz", "label", "kind", "description", "aliases",
          "properties", "edit_mode", "navigation_bindings"}


def migrate_points(points):
    """Drop retired Isaac object bindings while loading legacy products."""
    if not isinstance(points, list):
        return points
    return [{key: deepcopy(value) for key, value in point.items()
             if key != "isaac_binding"} if isinstance(point, dict) else point
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
        coordinates(point.get("position_xyz"))
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
        if point.get("edit_mode", "3d") not in ("2.5d", "3d"):
            raise ValueError("semantic edit_mode must be 2.5d or 3d")
        point["navigation_bindings"] = normalize_navigation_bindings(point.get("navigation_bindings", []))
    return points


def edit_points(points, operation, args, source_path=None):
    if "isaac_binding" in args or "binding" in args:
        raise ValueError("Isaac object bindings are no longer supported")
    result = deepcopy(points)
    if operation == "add_semantic_point":
        record = {"id": args.get("id") or "semantic:" + uuid.uuid4().hex,
                  "position_xyz": coordinates(args.get("position_xyz")),
                  "label": "", "kind": "custom", "description": "", "aliases": [],
                  "properties": {}, "edit_mode": "3d",
                  "navigation_bindings": []}
        for name in FIELDS - {"id", "position_xyz", "navigation_bindings"}:
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
        elif operation == "move_semantic_point_xy":
            record["position_xyz"] = [*coordinates(args.get("position_xy"), 2), record["position_xyz"][2]]
        elif operation == "move_semantic_point":
            record["position_xyz"] = coordinates(args.get("position_xyz"))
        elif operation == "remove_semantic_point":
            result.remove(record)
        elif operation == "set_semantic_point_navigation_bindings":
            record["navigation_bindings"] = normalize_navigation_bindings(args.get("bindings", []))
        else:
            raise ValueError("unknown semantic point operation")
    return validate_points(result)
