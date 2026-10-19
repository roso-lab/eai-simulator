"""Capture the unchanged office asset for semantic observation (no physics run)."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=False)
    asset_root = Path(__file__).resolve().parents[1] / "maps" / "office"
    from isaacsim import SimulationApp
    app = SimulationApp({"headless": False, "fast_shutdown": False,
                         "width": 1600, "height": 1000})
    adapter = stage = light = None
    try:
        import numpy as np
        import carb.settings
        import omni.usd
        from pxr import UsdLux
        from isaacsim.core.utils.viewports import set_camera_view
        from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
        from tools.topology_authoring.author import build_document
        from tools.topology_authoring.deploy import capture_frame
        adapter = UsdSceneAdapter("office_semantic")
        source, _ = build_document(adapter, asset_root)
        before = adapter.source_digests(source)
        adapter.load_stage(source)
        # Match the viewport's Camera Light mode for dark interior materials.
        carb.settings.get_settings().set_bool("/rtx/useViewLightingMode", True)
        stage = omni.usd.get_context().get_stage()
        light = UsdLux.DomeLight.Define(stage, "/World/SemanticObservationLight")
        light.CreateIntensityAttr().Set(250)
        for _ in range(35):
            app.update()
        views = [
            ("overview", None, (7,-10,7), (0,0,1.2)),
            ("first-stairs", None, (-.1,-1.8,.96), (.8,-2.8,.55)),
            ("first-room", None, (1.2,-3,.9), (-.5,-1,.45)),
            ("second-stairs", None, (1.1,-.6,1.95), (2.05,.6,1.55)),
            ("second-room", None, (1.3,-3,1.9), (-.5,-1,1.5)),
            ("roof", None, (4,-6,5), (0,0,2.2)),
            ("first-cutaway", "1F", (4,-6,7), (0,0,.11)),
            ("second-cutaway", "2F", (4,-6,8), (0,0,1.14)),
            ("roof-cutaway", "3F", (4,-6,9), (0,0,2.12)),
        ]
        for name, level, eye, target in views:
            adapter.set_level_view(level, source)
            set_camera_view(eye=eye, target=target)
            capture_frame(app, args.output_root / (name + ".png"))
            print("CAPTURE", name, flush=True)
        surface = adapter._geometry(source)
        triangles = surface.points[surface.triangles]
        normals = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
        area = np.linalg.norm(normals, axis=1)/2
        centers = triangles.mean(axis=1)
        flat = (abs(normals[:,2]) > 1.99*area) & (area > 1e-4)
        # Candidate tread surfaces only; names/route decisions remain human authored.
        mask = flat & (centers[:,0]>.3) & (centers[:,0]<1.4) & (centers[:,1]<-2.1)
        mask &= (centers[:,2]>.12) & (centers[:,2]<1.14)
        treads = []
        for z in sorted(set(np.round(centers[mask,2],3))):
            selected = mask & (abs(centers[:,2]-z)<.0005)
            points = triangles[selected].reshape(-1,3)
            treads.append({"z":float(z), "min":points.min(axis=0).tolist(),
                           "max":points.max(axis=0).tolist(), "area_m2":float(area[selected].sum())})
        # Lightweight arrays for a coordinate-aligned floor evidence drawing.
        for level in source.payload["levels"]:
            z = level["support_z_m"]
            subset = flat & (centers[:,2]>=z-.02) & (centers[:,2]<z+.7)
            np.savez_compressed(args.output_root / (level["id"]+"-surfaces.npz"),
                                triangles=triangles[subset])
        original = Path(source.payload["usd_path"]).parent
        report = {"source_digests":before, "source_unchanged":adapter.source_digests(source)==before,
                  "units":"metres", "up_axis":"Z", "bounds_min":surface.points.min(axis=0).tolist(),
                  "bounds_max":surface.points.max(axis=0).tolist(), "triangles":len(surface.triangles),
                  "views":[{"name":n,"level":l,"eye":e,"target":t} for n,l,e,t in views],
                  "candidate_treads":treads,
                  "asset_sha256":{str(p.relative_to(original)):hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(original.rglob("*")) if p.is_file()}}
        (args.output_root/"capture.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
        print("OFFICE_CAPTURE_COMPLETE", flush=True)
    finally:
        try:
            import carb.settings
            carb.settings.get_settings().set_bool("/rtx/useViewLightingMode", False)
        except Exception:
            pass
        light = stage = None
        if adapter:
            adapter.close()
        adapter = None
        gc.collect()
        from simulator import _prepare_gui_for_app_close
        _prepare_gui_for_app_close(app)
        app.close()


if __name__ == "__main__":
    main()
