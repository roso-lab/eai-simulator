#!/usr/bin/env python3
"""Launch the reusable topology authoring window in Isaac Sim.

The pure model/product modules can be used without Isaac.  This entry point
imports Kit only after parsing the scene adapter and is therefore also useful
for checking adapter wiring with ``--help``.
"""
from __future__ import annotations
import argparse, importlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import generate_product, validate_document

def _load(spec, scene_key=None):
    module, name = spec.split(":", 1)
    factory = getattr(importlib.import_module(module), name)
    if scene_key is not None:
        try:
            return factory(scene_key=scene_key)
        except TypeError:
            pass
    return factory()

def build_document(adapter, asset_root):
    source=adapter.load_source(Path(asset_root))
    return source, TopologyDocument(source.scene_key, adapter.source_digests(source))

def _headless(args):
    adapter=_load(args.adapter, args.scene_key); source, doc=build_document(adapter,args.asset_root)
    if args.semantic_map:
        source.payload["semantic"] = json.loads(args.semantic_map.read_text(encoding="utf-8"))
        doc = TopologyDocument(source.scene_key, adapter.source_digests(source))
    errors=validate_document(doc,adapter,source)
    print(json.dumps({"scene_key":source.scene_key,"nodes":len(doc.nodes),"edges":len(doc.edges),"validation_errors":errors},ensure_ascii=False,indent=2))
    if args.generate:
        generate_product(Path(args.generate),doc,adapter,source)
    return 0

def _gui(args):
    # Isaac imports are deliberately lazy: adapters and model remain usable on
    # CI hosts without Kit.
    from isaacsim import SimulationApp
    app=SimulationApp({"headless":False})
    import omni.kit.app
    # Use an absolute import so the documented direct-script entry point
    # (``python tools/topology_authoring/author.py``) works as well as
    # ``python -m tools.topology_authoring.author``.  A relative import has no
    # package context when this file is executed by path.
    from tools.topology_authoring.ui import TopologyAuthoringWindow
    adapter=_load(args.adapter, args.scene_key); source, doc=build_document(adapter,args.asset_root)
    if args.semantic_map:
        source.payload["semantic"] = json.loads(args.semantic_map.read_text(encoding="utf-8"))
        doc = TopologyDocument(source.scene_key, adapter.source_digests(source))
    adapter.load_stage(source)
    window=TopologyAuthoringWindow(doc,adapter,source,Path(args.output_root))
    while app.is_running():
        app.update()
    window.destroy(); app.close(); return 0

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
