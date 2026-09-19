"""Audit NVIDIA SimReady prop libraries for the scenario asset registry.

Runs inside a headless Isaac Sim Kit process (offline pxr cannot open USD on
this setup). For every prop it extracts size, mass, collision, and semantic
anchor tags, and writes one registry-metadata JSON. The output feeds a
half-automatic review before props enter the scenario asset registry.

Usage:
  python tools/scenario_generation/audit_nvd_props.py \
    --roots /home/pupil/下载/NVD_assets/SimReady_Warehouse_01_NVD@10010 \
    --output tmp/nvd_audit/warehouse_01.json [--limit 5]
"""

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    import common
else:
    from . import common


def audit_prop(prop_dir: Path) -> dict | None:
    from pxr import Usd, UsdGeom, UsdPhysics

    mains = sorted(p for p in prop_dir.glob("*.usd")
                   if not p.stem.endswith(("_inst", "_base", "_inst_base", "_tagged")))
    if not mains:
        return None
    stage = Usd.Stage.Open(str(mains[0]))
    root = stage.GetDefaultPrim() or next(iter(stage.Traverse()), None)
    if root is None:
        return None

    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_,
                                                            UsdGeom.Tokens.proxyPrim])
    size = bbox_cache.ComputeWorldBound(root).ComputeAlignedRange().GetSize()

    mass = None
    n_collision = 0
    n_rigid = 0
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            n_rigid += 1
            mass_api = UsdPhysics.MassAPI(prim)
            value = mass_api.GetMassAttr().Get()
            if value:
                mass = float(value)
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            n_collision += 1

    tags = set()
    for prim in stage.Traverse():
        for api in prim.GetAppliedSchemas():
            if "SemanticsAPI" in api or "AnchorTags" in api:
                for prop_name in prim.GetPropertyNames():
                    name = str(prop_name)
                    if "labels" in name.lower() or "tag" in name.lower():
                        attr = prim.GetAttribute(prop_name)
                        value = attr.Get()
                        if isinstance(value, str):
                            tags.update(t for t in value.split(",") if t)
                        elif isinstance(value, list):
                            tags.update(str(v) for v in value)

    return {
        "prop": prop_dir.name,
        "package": prop_dir.parents[2].name,
        "usd": str(mains[0]),
        "size_m": [round(float(v), 3) for v in size],
        "mass_kg": mass,
        "collision_prims": n_collision,
        "rigid_prims": n_rigid,
        "tags": sorted(tags),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", nargs="+", required=True, help="SimReady package root dirs")
    parser.add_argument("--output", required=True, help="Audit JSON output path")
    parser.add_argument("--limit", type=int, default=0, help="Audit at most N props per root (0 = all)")
    args = parser.parse_args(argv)

    from isaaclab.app import AppLauncher

    launcher = AppLauncher({"headless": True, "enable_cameras": True})
    app = launcher.app
    try:
        props = []
        for root in args.roots:
            root_path = Path(root)
            prop_dirs = sorted((root_path / "Assets/simready_content/common_assets/props").iterdir())
            prop_dirs = [d for d in prop_dirs if d.is_dir()]
            if args.limit:
                prop_dirs = prop_dirs[:args.limit]
            print(f"[audit] {root_path.name}: {len(prop_dirs)} props", flush=True)
            for index, prop_dir in enumerate(prop_dirs):
                try:
                    entry = audit_prop(prop_dir)
                except Exception as exc:
                    entry = {"prop": prop_dir.name, "package": root_path.name,
                             "error": f"{type(exc).__name__}: {exc}"}
                if entry is not None:
                    props.append(entry)
                if (index + 1) % 25 == 0:
                    print(f"[audit] {index + 1}/{len(prop_dirs)}", flush=True)

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        audited = sum(1 for p in props if "error" not in p)
        with_collision = sum(1 for p in props if p.get("collision_prims", 0) > 0)
        summary = {"audited": audited, "errors": len(props) - audited,
                   "with_collision": with_collision, "props": props}
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[audit] wrote {output} ({audited} ok, {len(props) - audited} errors, "
              f"{with_collision} with collision)", flush=True)
        return 0
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())
