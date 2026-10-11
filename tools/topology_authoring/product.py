"""Serialization and fail-closed validation for topology products."""
from __future__ import annotations
import hashlib, json, math
from pathlib import Path
from typing import Any, Mapping, Sequence
from .model import PROFILES, TopologyDocument

def _sha(path: Path) -> str:
    h = hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()

def validate_document(doc: TopologyDocument, adapter=None, source=None, *, require_targets=False,
                      node_only=False) -> list[str]:
    errors=[]; ids=set()
    if doc.scene_key.strip() != doc.scene_key or not doc.scene_key or "/" in doc.scene_key or "\\" in doc.scene_key:
        errors.append("scene_key is invalid")
    if doc.coordinate_frame.startswith("/") or doc.up_axis != "Z":
        errors.append("topology coordinate system is invalid")
    if not isinstance(doc.source, Mapping):
        errors.append("source digest inventory is invalid")
    else:
        for name, digest in doc.source.items():
            if not isinstance(name, str) or not name or name.startswith(("/", "\\")) \
                    or ".." in name.split("/"):
                errors.append("source digest key is invalid")
            if not isinstance(digest, str) or len(digest) != 64 \
                    or any(char not in "0123456789abcdef" for char in digest):
                errors.append(f"invalid source digest: {name}")
    if adapter is not None and source is not None:
        try:
            expected = dict(adapter.source_digests(source))
        except (AttributeError, TypeError, ValueError) as exc:
            errors.append(f"cannot read source digests: {exc}")
        else:
            if dict(doc.source) != expected:
                errors.append("source digest differs from current scene input")
    for n in doc.nodes:
        nid=n.get("id")
        if not isinstance(nid,str) or nid in ids: errors.append(f"duplicate/invalid node id: {nid}")
        ids.add(nid)
        try:
            p=n["position_xyz"]
            if len(p)!=3 or any(not math.isfinite(float(v)) for v in p): raise ValueError
        except (KeyError,TypeError,ValueError): errors.append(f"invalid node position: {nid}")
        refs = n.get("target_refs", ())
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref or ":" not in ref
                                             for ref in refs):
            errors.append(f"invalid node target references: {nid}")
    target_refs=set()
    if adapter is not None and source is not None:
        target_refs={t.ref for t in adapter.semantic_targets(source)}
    if require_targets:
        bound={r for n in doc.nodes for r in n.get("target_refs",())}
        errors.extend(f"target has no entrance: {r}" for r in sorted(target_refs-bound))
    edge_ids=set()
    for e in doc.edges:
        eid=e.get("id")
        if not isinstance(eid,str) or eid in edge_ids: errors.append(f"duplicate/invalid edge id: {eid}")
        edge_ids.add(eid)
        if e.get("from") not in ids or e.get("to") not in ids or e.get("from")==e.get("to"): errors.append(f"invalid edge endpoints: {eid}")
        if type(e.get("directed", False)) is not bool:
            errors.append(f"invalid edge direction: {eid}")
        if node_only:
            continue
        points=e.get("polyline_xyz")
        if not isinstance(points,list) or len(points)<2: errors.append(f"edge has too few points: {eid}"); continue
        for p in points:
            try:
                if len(p)!=3 or any(not math.isfinite(float(v)) for v in p): raise ValueError
            except (TypeError,ValueError): errors.append(f"invalid edge point: {eid}"); break
        if all(math.dist(points[index - 1], points[index]) <= 1e-9
               for index in range(1, len(points))):
            errors.append(f"zero length edge: {eid}")
        try:
            start_node = next(row for row in doc.nodes if row.get("id") == e.get("from"))
            end_node = next(row for row in doc.nodes if row.get("id") == e.get("to"))
            if math.dist(points[0], start_node["position_xyz"]) > 1e-5 \
                    or math.dist(points[-1], end_node["position_xyz"]) > 1e-5:
                errors.append(f"edge endpoints do not match polyline: {eid}")
        except (StopIteration, KeyError, TypeError, ValueError):
            pass
        for profile, cfg in (e.get("profiles") or {}).items():
            try:
                clearance = float(cfg.get("clearance_m", 0)) if isinstance(cfg, Mapping) else 0
            except (TypeError, ValueError):
                clearance = 0
            if profile not in PROFILES or not isinstance(cfg,Mapping) or not math.isfinite(clearance) or clearance<=0:
                errors.append(f"invalid profile {profile}: {eid}")
        if adapter is not None and source is not None:
            for profile in (e.get("profiles") or {}): errors.extend(f"{eid}: {x}" for x in adapter.validate_polyline(points, profile, source))
    if target_refs:
        for n in doc.nodes:
            for ref in n.get("target_refs",()):
                if ref not in target_refs: errors.append(f"unknown target reference: {ref}")
    return sorted(set(errors))

def save_draft(path: Path, doc: TopologyDocument) -> None:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(doc.to_dict(),indent=2,ensure_ascii=False)+"\n",encoding="utf-8")

def load_product(path: Path, adapter=None, source=None) -> TopologyDocument:
    document = TopologyDocument.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    if adapter is not None and source is not None:
        errors = validate_document(document, adapter, source)
        if errors:
            raise ValueError("topology source validation failed: " + "; ".join(errors))
    return document

def generate_product(path: Path, doc: TopologyDocument, adapter=None, source=None, *, require_targets=False) -> Path:
    errors=validate_document(doc,adapter,source,require_targets=require_targets)
    if not doc.nodes:
        errors.append("topology is empty; add at least two nodes before generating")
    elif len(doc.nodes) < 2 or not doc.edges:
        errors.append("topology is incomplete; add connected nodes and edges before generating")
    if errors: raise ValueError("topology validation failed: " + "; ".join(errors))
    save_draft(path,doc); return Path(path)
