#!/usr/bin/env python3
"""Launch the reusable topology authoring window in Isaac Sim.

The pure model/product modules can be used without Isaac.  This entry point
imports Kit only after parsing the scene adapter and is therefore also useful
for checking adapter wiring with ``--help``.
"""
from __future__ import annotations
import argparse, importlib, inspect, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import generate_product, validate_document
from tools.topology_authoring.adapter import SceneSource

def _load(spec, scene_key=None):
    module, name = spec.split(":", 1)
    factory = getattr(importlib.import_module(module), name)
    if scene_key is not None:
        try:
            signature = inspect.signature(factory)
        except (TypeError, ValueError):
            return factory(scene_key=scene_key)
        try:
            signature.bind(scene_key=scene_key)
        except TypeError:
            return factory()
        return factory(scene_key=scene_key)
    return factory()

def _with_semantic_map(source, semantic_map):
    if semantic_map is None:
        return source
    payload = dict(source.payload)
    payload["semantic"] = semantic_map
    return SceneSource(source.scene_key, Path(source.asset_root), payload)

def _read_semantic_map(path):
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))

def build_document(adapter, asset_root, semantic_map=None, *, requested_scene_key=None):
    source = _with_semantic_map(adapter.load_source(Path(asset_root)), semantic_map)
    if requested_scene_key is not None and source.scene_key != requested_scene_key:
        raise ValueError(
            f"adapter source scene {source.scene_key!r} differs from requested "
            f"scene {requested_scene_key!r}"
        )
    document = TopologyDocument(source.scene_key, adapter.source_digests(source))
    if "levels" in source.payload:
        document.set_levels(source.payload["levels"])
        # Source-declared levels are the initial document state, not an author
        # edit that can be undone back into a mismatched v1 product.
        document._undo.clear()
        document._redo.clear()
    return source, document

def _headless(args):
    semantic_map = _read_semantic_map(args.semantic_map)
    adapter=_load(args.adapter, args.scene_key)
    source, doc=build_document(
        adapter,
        args.asset_root,
        semantic_map,
        requested_scene_key=args.scene_key,
    )
    errors=validate_document(doc,adapter,source)
    print(json.dumps({"scene_key":source.scene_key,"nodes":len(doc.nodes),"edges":len(doc.edges),"validation_errors":errors},ensure_ascii=False,indent=2))
    if args.generate:
        generate_product(Path(args.generate),doc,adapter,source)
    return 1 if errors else 0

def _gui(args):
    # Isaac imports are deliberately lazy: adapters and model remain usable on
    # CI hosts without Kit.
    from isaacsim import SimulationApp
    app=SimulationApp({"headless":False, "fast_shutdown":False})
    window = None
    adapter = None
    try:
        # Use an absolute import so the documented direct-script entry point
        # (``python tools/topology_authoring/author.py``) works as well as
        # ``python -m tools.topology_authoring.author``.  A relative import has no
        # package context when this file is executed by path.
        from tools.topology_authoring.ui import TopologyAuthoringWindow
        semantic_map = _read_semantic_map(args.semantic_map)
        adapter=_load(args.adapter, args.scene_key)
        source, doc=build_document(
            adapter,
            args.asset_root,
            semantic_map,
            requested_scene_key=args.scene_key,
        )
        errors = validate_document(doc, adapter, source)
        if errors:
            raise ValueError(
                "source validation failed: " + "; ".join(errors)
            )
        adapter.load_stage(source)
        window=TopologyAuthoringWindow(
            doc,
            adapter,
            source,
            Path(args.output_root),
            semantic_map=semantic_map,
        )
        while app.is_running():
            app.update()
        return 0
    finally:
        try:
            if window is not None:
                window.destroy()
                window = None
            close = getattr(adapter, "close", None)
            if callable(close):
                close()
        finally:
            try:
                from simulator import _prepare_gui_for_app_close

                _prepare_gui_for_app_close(app)
            finally:
                app.close()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scene-key", required=True); p.add_argument("--adapter", required=True)
    p.add_argument("--asset-root", type=Path, required=True); p.add_argument("--output-root", type=Path, required=True)
    p.add_argument("--semantic-map", type=Path, help="optional participant semantic map for target binding")
    p.add_argument("--headless", action="store_true"); p.add_argument("--generate", type=Path)
    args=p.parse_args(argv)
    if args.scene_key and not args.asset_root.is_dir(): p.error(f"asset root does not exist: {args.asset_root}")
    return _headless(args) if args.headless else _gui(args)

if __name__ == "__main__": raise SystemExit(main())
