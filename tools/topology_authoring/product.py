"""Serialization and validation of scene-bound ground and aerial topologies."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Mapping

from algorithm.skills.navigation.topology import finite_number, finite_xyz, validate_topology
from .model import PROFILES, TRAVERSAL_MODES, TopologyDocument
from .semantics import validate_semantics


def validate_document(doc: TopologyDocument, adapter=None, source=None, *, require_targets=False,
                      node_only=False) -> list[str]:
    # Navigation remains the executable contract; semantic annotations add
    # cross references and floor/route constraints on top of that graph.
    errors = validate_topology(doc) + validate_semantics(doc)
    if (not isinstance(doc.scene_key, str) or not doc.scene_key
            or doc.scene_key.strip() != doc.scene_key or doc.scene_key in {".", ".."}
            or "/" in doc.scene_key or "\\" in doc.scene_key):
        errors.append("scene_key is invalid")
    if not isinstance(doc.source, Mapping):
        errors.append("source digest inventory is invalid")
    else:
        for name, digest in doc.source.items():
            if (not isinstance(name, str) or not name or name.startswith("/")
                    or "\\" in name or ".." in name.split("/")):
                errors.append("source digest key is invalid")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                errors.append(f"invalid source digest: {name}")
    if (adapter is None) != (source is None):
        errors.append("adapter and source must be supplied together")
    bound_source = adapter is not None and source is not None
    target_refs = set()
    if bound_source:
        if doc.scene_key != source.scene_key or getattr(adapter, "scene_key", source.scene_key) != source.scene_key:
            errors.append("topology scene differs from current scene input")
        try:
            expected = dict(adapter.source_digests(source))
            if doc.source != expected:
                errors.append("source digest differs from current scene input")
            scene_refs = [target.ref for target in adapter.semantic_targets(source)]
            if any(not isinstance(ref, str) or not ref or ":" not in ref for ref in scene_refs):
                raise ValueError("invalid scene target reference")
            target_refs = set(scene_refs)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            errors.append(f"cannot read scene facts: {exc}")
    elif require_targets:
        errors.append("target validation requires a scene adapter and source")

    # Do not perform geometry operations on malformed structural inputs.
    nodes = {row["id"]: row for row in doc.nodes
             if isinstance(row, Mapping) and isinstance(row.get("id"), str)
             and finite_xyz(row.get("position_xyz"))} if isinstance(doc.nodes, (list, tuple)) else {}
    bound = set()
    for node in doc.nodes if isinstance(doc.nodes, (list, tuple)) else ():
        if not isinstance(node, Mapping):
            continue
        refs = node.get("target_refs", [])
        if not isinstance(refs, (list, tuple)):
            continue
        for ref in refs:
            if isinstance(ref, str):
                bound.add(ref)
                if bound_source and ref not in target_refs:
                    errors.append(f"unknown target reference: {ref}")
    if require_targets:
        errors.extend(f"target has no entrance: {ref}" for ref in sorted(target_refs - bound))

    for edge in doc.edges if isinstance(doc.edges, (list, tuple)) else ():
        if not isinstance(edge, Mapping):
            continue
        edge_id = edge.get("id")
        start, end = edge.get("from"), edge.get("to")
        endpoints = (isinstance(start, str) and isinstance(end, str)
                     and start in nodes and end in nodes and start != end)
        points = edge.get("polyline_xyz")
        if not node_only:
            if points is None:
                # Canonical execution products may omit decorative geometry.
                points = None
            elif not isinstance(points, list) or len(points) < 2:
                errors.append(f"edge has too few points: {edge_id}")
            elif not all(finite_xyz(point) for point in points):
                errors.append(f"invalid edge point: {edge_id}")
            else:
                if all(math.dist(a, b) <= 1e-9 for a, b in zip(points, points[1:])):
                    errors.append(f"zero length edge: {edge_id}")
                if endpoints and (math.dist(points[0], nodes[start]["position_xyz"]) > 1e-5
                                  or math.dist(points[-1], nodes[end]["position_xyz"]) > 1e-5):
                    errors.append(f"edge endpoints do not match polyline: {edge_id}")
        profiles = edge.get("profiles", {})
        if profiles is None:
            profiles = {}
        if not isinstance(profiles, Mapping):
            if not node_only:
                errors.append(f"edge profiles must be an object: {edge_id}")
            continue
        traversal_mode = edge.get("traversal_mode", "walk")
        capabilities = edge.get("required_controller_capabilities", [])
        if doc.version == 2:
            if traversal_mode not in TRAVERSAL_MODES:
                errors.append(f"invalid edge traversal_mode: {edge_id}")
            if (not isinstance(capabilities, (list, tuple))
                    or any(not isinstance(item, str) or not item.strip() for item in capabilities)
                    or len(set(capabilities)) != len(capabilities)):
                errors.append(f"invalid edge controller capabilities: {edge_id}")
        elif traversal_mode != "walk" or capabilities:
            errors.append(f"edge traversal metadata requires topology version 2: {edge_id}")
        for profile, config in profiles.items():
            clearance = config.get("clearance_m") if isinstance(config, Mapping) else None
            if (profile not in PROFILES or not finite_number(clearance)
                    or clearance <= 0):
                if not node_only:
                    errors.append(
                        f"invalid profile {profile} (invalid edge metadata profile): {edge_id}")
                continue
            if bound_source and endpoints:
                # Polyline checks are authoring/scene checks only.  Runtime
                # routing still uses the straight node endpoints.
                segment = [nodes[start]["position_xyz"], nodes[end]["position_xyz"]]
                try:
                    # Domain is an authoring hint for scene geometry checks,
                    # never a runtime admission constraint.
                    aerial_validator = getattr(adapter, "validate_aerial_polyline", None)
                    if doc.domain == "aerial" and aerial_validator is not None:
                        problems = aerial_validator(segment, clearance, source)
                    else:
                        validator = getattr(adapter, "validate_polyline", None)
                        if validator is None:
                            continue
                        problems = validator(segment, profile, source)
                    if not isinstance(problems, list) or any(not isinstance(item, str) for item in problems):
                        raise ValueError("adapter validation must return a list of errors")
                    errors.extend(f"{edge_id}: {item}" for item in problems)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    errors.append(f"{edge_id}: scene validation failed: {exc}")
    return sorted(set(errors))


def product_digest(doc: TopologyDocument) -> str:
    """Hash the complete serialized product; the caller freezes it before runs."""
    return hashlib.sha256(json.dumps(doc.to_dict(), sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def save_draft(path: Path, doc: TopologyDocument) -> None:
    # Serialize first, then replace atomically so failed writes preserve drafts.
    payload = json.dumps(doc.to_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"nonfinite JSON constant: {value}")


def load_product(path: Path, adapter=None, source=None, *, expected_digest=None) -> TopologyDocument:
    document = TopologyDocument.from_dict(json.loads(
        Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
        parse_constant=_reject_constant))
    errors = validate_document(document, adapter, source)
    if errors:
        raise ValueError("topology validation failed: " + "; ".join(errors))
    if expected_digest is not None and product_digest(document) != expected_digest:
        raise ValueError("topology product differs from frozen digest")
    return document


def generate_product(path: Path, doc: TopologyDocument, adapter=None, source=None, *, require_targets=False) -> Path:
    errors = validate_document(doc, adapter, source, require_targets=require_targets)
    if errors:
        raise ValueError("topology validation failed: " + "; ".join(errors))
    if not doc.nodes:
        errors.append("topology is empty; add at least two nodes before generating")
    elif len(doc.nodes) < 2 or not doc.edges:
        errors.append("topology is incomplete; add connected nodes and edges before generating")
    if errors:
        raise ValueError("topology validation failed: " + "; ".join(errors))
    save_draft(path, doc)
    return Path(path)
