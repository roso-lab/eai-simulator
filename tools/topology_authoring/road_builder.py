"""Geometry planning for navigation-road construction."""
from __future__ import annotations

from copy import deepcopy
import math
from .precision import point_mm, quantize_geometry

Z_TOLERANCE_M = 0.20
AERIAL_Z_TOLERANCE_M = 0.10
EPSILON_M = 1e-6


def _dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _project(point, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    t = 0.0 if denom <= EPSILON_M ** 2 else max(0.0, min(1.0, ((point[0]-a[0])*dx + (point[1]-a[1])*dy)/denom))
    return t, [a[0] + t*dx, a[1] + t*dy, a[2] + t*(b[2]-a[2])]


def _eligible_edge(edge, nodes, level_id, domain):
    if domain == "aerial":
        return True
    levels = {nodes[edge["from"]].get("level_id"), nodes[edge["to"]].get("level_id")}
    return level_id in levels or None in levels


def _snap(point, nodes, edges, level_id, radius, *, domain, z_tolerance_m, disabled=False, axis=None):
    if disabled:
        return {"position_xyz": list(point), "node_id": None, "edge_id": None}
    candidates = []
    for node in nodes.values():
        if domain == "ground" and node.get("level_id") not in (None, level_id):
            continue
        p = node["position_xyz"]
        if axis is not None and point_mm(p)[axis] != point_mm(point)[axis]:
            continue
        if abs(p[2] - point[2]) <= z_tolerance_m + EPSILON_M and _dist2(p, point) <= radius * radius:
            candidates.append((_dist2(p, point), 0, node["id"], list(p), None))
    if candidates:
        _, _, node_id, position, _ = min(candidates)
        return {"position_xyz": position, "node_id": node_id, "edge_id": None}
    for edge in edges:
        if not _eligible_edge(edge, nodes, level_id, domain):
            continue
        points = edge["polyline_xyz"]
        for index, (a, b) in enumerate(zip(points, points[1:])):
            t, projected = _project(point, a, b)
            if axis is not None:
                delta = b[axis] - a[axis]
                if abs(delta) <= EPSILON_M:
                    if point_mm(a)[axis] != point_mm(point)[axis]:
                        continue
                else:
                    t = (point[axis] - a[axis]) / delta
                    if not 0 <= t <= 1:
                        continue
                    projected = [a[i] + t*(b[i]-a[i]) for i in range(3)]
                projected[axis] = point[axis]
            if abs(projected[2] - point[2]) <= z_tolerance_m + EPSILON_M and _dist2(projected, point) <= radius * radius:
                candidates.append((_dist2(projected, point), 1, edge["id"], projected, (index, t)))
    if candidates:
        _, _, edge_id, position, segment = min(candidates)
        return {"position_xyz": position, "node_id": None, "edge_id": edge_id, "segment": segment}
    return {"position_xyz": list(point), "node_id": None, "edge_id": None}


def _intersection(a, b, c, d):
    rx, ry = b[0]-a[0], b[1]-a[1]
    sx, sy = d[0]-c[0], d[1]-c[1]
    denom = rx*sy-ry*sx
    qx, qy = c[0]-a[0], c[1]-a[1]
    if abs(denom) <= EPSILON_M:
        if abs(qx*ry-qy*rx) <= EPSILON_M:
            length2 = rx*rx + ry*ry
            if length2 > EPSILON_M**2:
                first = (qx*rx + qy*ry) / length2
                second = first + (sx*rx + sy*ry) / length2
                if max(min(0.0, 1.0), min(first, second)) < min(max(0.0, 1.0), max(first, second))-EPSILON_M:
                    raise ValueError("道路与已有边共线重叠")
        return None
    t, u = (qx*sy-qy*sx)/denom, (qx*ry-qy*rx)/denom
    if -EPSILON_M <= t <= 1+EPSILON_M and -EPSILON_M <= u <= 1+EPSILON_M:
        return max(0., min(1., t)), max(0., min(1., u))
    return None


def snap_road_start(document, point, *, level_id=None, snap_radius_m=0.0, disable_snap=False):
    source = document.to_dict() if hasattr(document, "to_dict") else document
    radius = float(snap_radius_m)
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("snap radius must be finite and non-negative")
    domain = source.get("domain")
    if domain not in {"ground", "aerial"}:
        raise ValueError("invalid road domain")
    if domain == "ground" and level_id not in {r['id'] for r in source.get('levels', [])}:
        raise ValueError("unknown active ground level")
    # Quantized geometry is used for snapping so the persisted axis matches preview.
    geometry = quantize_geometry(source)
    snap = _snap(point_mm(point), {n['id']: n for n in geometry['nodes']}, geometry['edges'],
                 level_id, radius, domain=domain,
                 z_tolerance_m=AERIAL_Z_TOLERANCE_M if domain == "aerial" else Z_TOLERANCE_M,
                 disabled=disable_snap)
    snap['position_xyz'] = point_mm(snap['position_xyz'])
    return snap


def plan_navigation_road(document, start_xyz, end_xyz, *, level_id=None, snap_radius_m=0.0,
                         disable_snap=False, connect_intersections=True, z_tolerance_m=None,
                         axis_snap=False, fixed_start=None):
    """Return a preview plus a candidate document; never mutates the input."""
    source = document.to_dict() if hasattr(document, "to_dict") else deepcopy(document)
    domain = source.get("domain")
    if domain not in {"ground", "aerial"}:
        raise ValueError("road construction requires a navigation topology")
    source = quantize_geometry(source)
    nodes = {row["id"]: row for row in source["nodes"]}
    edges = source["edges"]
    levels = {row["id"] for row in source.get("levels", [])}
    if domain == "ground" and level_id not in levels:
        raise ValueError("unknown active ground level")
    tolerance = (AERIAL_Z_TOLERANCE_M if domain == "aerial" else Z_TOLERANCE_M) if z_tolerance_m is None else float(z_tolerance_m)
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("height tolerance must be a finite non-negative value")
    start = [float(x) for x in start_xyz]
    end = [float(x) for x in end_xyz]
    if len(start) != 3 or len(end) != 3 or not all(math.isfinite(x) for x in start + end):
        raise ValueError("road endpoints must contain finite XYZ coordinates")
    if _dist2(start, end) <= EPSILON_M ** 2:
        raise ValueError("道路长度必须大于零")
    radius = float(snap_radius_m)
    if not math.isfinite(radius) or radius < 0:
        raise ValueError("snap radius must be finite and non-negative")
    start_snap = snap_road_start(source, start, level_id=level_id,
                                snap_radius_m=radius, disable_snap=disable_snap)
    if fixed_start is not None:
        if not isinstance(fixed_start, dict):
            raise ValueError("invalid fixed road start")
        position = point_mm(fixed_start.get("position_xyz"))
        if fixed_start.get("node_id"):
            node = nodes.get(fixed_start['node_id'])
            if node is None or node['position_xyz'] != position:
                raise ValueError("fixed start node changed")
            if domain == "ground" and node.get('level_id') not in (None, level_id):
                raise ValueError("fixed start node outside active floor")
        elif fixed_start.get('edge_id'):
            edge = next((e for e in edges if e['id'] == fixed_start['edge_id']), None)
            if edge is None or not _eligible_edge(edge, nodes, level_id, domain):
                raise ValueError("fixed start edge changed")
            segment = fixed_start.get('segment')
            if not isinstance(segment, (list, tuple)) or len(segment) != 2:
                raise ValueError("invalid fixed start segment")
            index, u = segment
            if type(index) is not int or not 0 <= index < len(edge['polyline_xyz'])-1 or not 0 <= u <= 1:
                raise ValueError("invalid fixed start segment")
            a, b = edge['polyline_xyz'][index:index+2]
            if point_mm([a[i]+u*(b[i]-a[i]) for i in range(3)]) != position:
                raise ValueError("fixed start edge position changed")
        start_snap = deepcopy(fixed_start)
        start_snap['position_xyz'] = position
    start = point_mm(start_snap['position_xyz'])
    axis = None
    if axis_snap and not disable_snap:
        angle = math.atan2(abs(end[1]-start[1]), abs(end[0]-start[0]))
        if angle <= math.pi/18:
            axis = 1
        elif math.pi/2-angle <= math.pi/18:
            axis = 0
        if axis is not None:
            end[axis] = start[axis]
    end_snap = _snap(end, nodes, edges, level_id, radius, domain=domain,
                     z_tolerance_m=tolerance, disabled=disable_snap, axis=axis)
    end = point_mm(end_snap['position_xyz'])
    if axis is not None:
        end[axis] = start[axis]
    start_snap['position_xyz'], end_snap['position_xyz'] = start, end
    if _dist2(start, end) <= EPSILON_M ** 2:
        raise ValueError("道路长度必须大于零")

    crossings = []
    for edge in edges:
        edge_points = edge["polyline_xyz"]
        if not _eligible_edge(edge, nodes, level_id, domain):
            continue
        for index, (a, b) in enumerate(zip(edge_points, edge_points[1:])):
            hit = _intersection(start, end, a, b)
            if hit is None or not connect_intersections:
                continue
            t, u = hit
            if t <= EPSILON_M or t >= 1-EPSILON_M:
                continue
            road_z = start[2] + t*(end[2]-start[2])
            edge_z = a[2] + u*(b[2]-a[2])
            if abs(road_z-edge_z) > tolerance + EPSILON_M:
                continue
            if edge.get("locked_geometry"):
                raise ValueError("交点需要拆分锁定边，不能建设")
            p = [start[0]+t*(end[0]-start[0]), start[1]+t*(end[1]-start[1]), (road_z+edge_z)/2]
            crossings.append({"t": t, "edge_id": edge["id"], "segment": index, "u": u, "point": p})
    endpoint_cuts = []
    for road_t, snap in ((0.0, start_snap), (1.0, end_snap)):
        if snap.get("edge_id"):
            if next(edge for edge in edges if edge["id"] == snap["edge_id"]).get("locked_geometry"):
                raise ValueError("不能连接到需要修改的锁定边")
            index, u = snap["segment"]
            endpoint_cuts.append({"t": road_t, "edge_id": snap["edge_id"], "segment": index,
                                  "u": u, "point": list(snap["position_xyz"]), "endpoint": road_t})
    if not connect_intersections:
        crossings = []
    crossings.sort(key=lambda row: row["t"])

    candidate = deepcopy(source)
    new_nodes, new_edges = candidate["nodes"], candidate["edges"]
    used_edge_ids = {edge["id"] for edge in edges}
    next_edge_number = max((int(identifier.split(":")[-1]) for identifier in used_edge_ids
                           if identifier.startswith("edge:") and identifier.split(":")[-1].isdigit()),
                          default=0) + 1

    def allocate_edge_id():
        nonlocal next_edge_number
        while True:
            identifier = f"edge:{next_edge_number:04d}"
            next_edge_number += 1
            if identifier not in used_edge_ids:
                used_edge_ids.add(identifier)
                return identifier

    def next_id(rows, prefix):
        numbers = [int(row["id"].split(":")[-1]) for row in rows if row["id"].startswith(prefix) and row["id"].split(":")[-1].isdigit()]
        return f"{prefix}{max(numbers, default=0)+1:04d}"
    def node_for(point, existing_id=None):
        if existing_id:
            return existing_id
        for row in new_nodes:
            if (domain == "aerial" or row.get("level_id") in (None, level_id)) and abs(row["position_xyz"][2]-point[2]) <= tolerance + EPSILON_M and _dist2(row["position_xyz"], point) <= EPSILON_M**2:
                return row["id"]
        identifier = next_id(new_nodes, "node:")
        row = {"id": identifier, "kind": "custom", "position_xyz": list(point),
               "target_refs": [], "source_ref": None, "locked": False}
        if domain == "ground" and int(candidate.get("version", 1)) >= 2:
            row.update(level_id=level_id, label="", aliases=[])
        new_nodes.append(row)
        return identifier

    start_id = node_for(start, start_snap.get("node_id"))
    end_id = node_for(end, end_snap.get("node_id"))
    for cut in endpoint_cuts:
        cut["node_id"] = node_for(cut["point"])
        if cut["endpoint"] == 0.0:
            start_id = cut["node_id"]
        else:
            end_id = cut["node_id"]
    # Group duplicate geometric intersections so each crossing has one shared node.
    grouped = []
    for crossing in crossings:
        match = next((group for group in grouped if abs(group["point"][2]-crossing["point"][2]) <= tolerance + EPSILON_M and _dist2(group["point"], crossing["point"]) <= EPSILON_M**2), None)
        if match is None:
            crossing["node_id"] = node_for(crossing["point"])
            grouped.append(crossing)
        else:
            crossing["node_id"] = match["node_id"]

    # Split each touched polyline while retaining the original ID on its first segment.
    by_edge = {}
    for crossing in [*crossings, *endpoint_cuts]:
        by_edge.setdefault(crossing["edge_id"], []).append(crossing)
    route_expansions = {}
    replacements = []
    for edge in new_edges:
        cuts = by_edge.get(edge["id"], [])
        if not cuts:
            replacements.append(edge)
            continue
        points = edge["polyline_xyz"]
        segments, current_node, current_line = [], edge["from"], [list(points[0])]
        cuts_by_segment = {}
        for cut in cuts:
            cuts_by_segment.setdefault(cut["segment"], []).append(cut)
        for index, (a, b) in enumerate(zip(points, points[1:])):
            for cut in sorted(cuts_by_segment.get(index, []), key=lambda row: row["u"]):
                current_line.append(list(cut["point"]))
                next_node = cut["node_id"]
                if next_node != current_node:
                    identifier = edge["id"] if not segments else allocate_edge_id()
                    seg = deepcopy(edge); seg.update(id=identifier, **{"from": current_node, "to": next_node})
                    seg["polyline_xyz"] = deepcopy(current_line); segments.append(seg)
                    current_node, current_line = next_node, [list(cut["point"])]
            current_line.append(list(b))
        if current_node != edge["to"]:
            identifier = edge["id"] if not segments else allocate_edge_id()
            seg = deepcopy(edge); seg.update(id=identifier, **{"from": current_node, "to": edge["to"]})
            seg["polyline_xyz"] = deepcopy(current_line); segments.append(seg)
        replacements.extend(segments)
        split_nodes = [row["node_id"] for row in sorted(cuts, key=lambda row: (row["segment"], row["u"]))]
        route_expansions[edge["id"]] = ([edge["from"]] + split_nodes + [edge["to"]], [x["id"] for x in segments])
    candidate["edges"] = replacements
    road_ids = [start_id] + [row["node_id"] for row in grouped] + [end_id]
    road_points = [start] + [row["point"] for row in grouped] + [end]
    road_edge_ids = []
    for first, last, a, b in zip(road_ids, road_ids[1:], road_points, road_points[1:]):
        if first == last or _dist2(a, b) <= EPSILON_M**2:
            continue
        identifier = allocate_edge_id()
        profile_defaults = (source["edges"][0].get("profiles") if source["edges"] else
                            {key: {"clearance_m": value} for key, value in source.get("profiles", {}).items()})
        candidate["edges"].append({"id": identifier, "kind": "custom_connection", "from": first, "to": last,
            "directed": False, "polyline_xyz": [list(a), list(b)], "profiles": deepcopy(profile_defaults or {}),
            "source_ref": None, "locked_geometry": False, "traversal_mode": "walk", "required_controller_capabilities": []})
        road_edge_ids.append(identifier)
    semantics = candidate.get("semantics") or {}
    for route in semantics.get("routes", []):
        route_edges, route_nodes = [], []
        for index, edge_id in enumerate(route["edge_ids"]):
            if index == 0:
                route_nodes.extend([route["node_ids"][0]])
            if edge_id in route_expansions:
                ns, es = route_expansions[edge_id]
                route_nodes.extend(ns[1:]); route_edges.extend(es)
            else:
                route_nodes.append(route["node_ids"][index+1]); route_edges.append(edge_id)
        route["edge_ids"], route["node_ids"] = route_edges, route_nodes
    candidate["semantics"] = semantics
    candidate = quantize_geometry(candidate)
    return {"document": candidate, "start": start, "end": end, "start_node_id": start_id,
            "end_node_id": end_id, "crossings": [point_mm(row["point"]) for row in grouped], "edge_ids": road_edge_ids,
            "axis": "x" if axis == 1 else "y" if axis == 0 else None,
            "snap": {"start": start_snap, "end": end_snap}}


def plan_ground_road(document, start_xyz, end_xyz, *, level_id, snap_radius_m=0.0,
                     disable_snap=False, connect_intersections=True):
    """Compatibility entry point for callers that explicitly build ground roads."""
    source = document.to_dict() if hasattr(document, "to_dict") else document
    if source.get("domain") != "ground":
        raise ValueError("road construction is only available for ground topology")
    return plan_navigation_road(source, start_xyz, end_xyz, level_id=level_id,
        snap_radius_m=snap_radius_m, disable_snap=disable_snap,
        connect_intersections=connect_intersections, z_tolerance_m=Z_TOLERANCE_M)
