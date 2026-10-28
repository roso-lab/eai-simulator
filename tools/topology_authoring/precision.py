"""Millimetre quantization of authored navigation geometry (not source assets)."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import math


def millimetres(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("coordinate must be a finite number")
    number = Decimal(str(value))
    with localcontext() as context:
        context.prec = max(32, number.adjusted() + 8)
        result = float(number.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP))
    return 0.0 if result == 0 else result


def point_mm(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("position must contain three finite coordinates")
    return [millimetres(v) for v in value]


def quantize_geometry(document, previous=None):
    """Return a copy. For edits, quantize only changed geometry; saves pass no previous.

    Reject newly collapsed geometry rather than silently merging IDs. Existing
    coincident nodes (e.g. intentionally separate networks) remain legal.
    """
    result = deepcopy(document)
    old_nodes = {n['id']: n for n in (previous or {}).get('nodes', [])}
    old_edges = {e['id']: e for e in (previous or {}).get('edges', [])}
    originals = {n['id']: n for n in document['nodes']}
    nodes = {}
    occupied = {}
    for node in result['nodes']:
        old = old_nodes.get(node['id'])
        if previous is None or old is None or old['position_xyz'] != node['position_xyz']:
            node['position_xyz'] = point_mm(node['position_xyz'])
        nodes[node['id']] = node
        key = (node.get('level_id'), *node['position_xyz'])
        for other in occupied.get(key, []):
            if originals[other]['position_xyz'] != originals[node['id']]['position_xyz']:
                raise ValueError(f"millimetre rounding collides nodes: {other}, {node['id']}")
        occupied.setdefault(key, []).append(node['id'])
    for edge in result['edges']:
        old = old_edges.get(edge['id'])
        original = edge['polyline_xyz']
        if previous is None or old is None or old['polyline_xyz'] != original:
            edge['polyline_xyz'] = [point_mm(p) for p in original]
        points = edge['polyline_xyz']
        if len(points) < 2:
            raise ValueError(f"invalid polyline: {edge['id']}")
        points[0] = list(nodes[edge['from']]['position_xyz'])
        points[-1] = list(nodes[edge['to']]['position_xyz'])
        if all(p == points[0] for p in points):
            raise ValueError(f"zero length edge after millimetre rounding: {edge['id']}")
        for index, (a, b) in enumerate(zip(points, points[1:])):
            if a == b and original[index] != original[index+1]:
                raise ValueError(f"collapsed polyline segment after millimetre rounding: {edge['id']}")
    return result
