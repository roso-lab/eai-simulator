"""Generate an explicitly synthetic two-floor house authoring example.

Run in the prepared Isaac/OpenUSD environment; no Kit app is needed.
This demonstrates editable semantic maps, not certified robot traversal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
from tools.topology_authoring.author import build_document
from tools.topology_authoring.product import generate_product


def build_example(root):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    def slab(name, xmin, xmax, ymin, ymax, zmin, zmax, color):
        points = [(xmin,ymin,zmin), (xmax,ymin,zmin), (xmax,ymax,zmin), (xmin,ymax,zmin),
                  (xmin,ymin,zmax), (xmax,ymin,zmax), (xmax,ymax,zmax), (xmin,ymax,zmax)]
        mesh = UsdGeom.Mesh.Define(stage, "/World/"+name)
        mesh.CreatePointsAttr().Set([Gf.Vec3f(*p) for p in points])
        faces = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
        mesh.CreateFaceVertexCountsAttr().Set([3]*12)
        mesh.CreateFaceVertexIndicesAttr().Set([v for a,b,c,d in faces for v in (a,b,c,a,c,d)])
        mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(*color)])
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())

    slab("Ground", -4, 0, -2, 2, -.15, 0, (.52,.62,.68))
    slab("Upper", 3, 7, -2, 2, 1.85, 2, (.65,.62,.52))
    for index in range(1,21):
        slab(f"Steps/S{index:02d}", (index-1)*.15, index*.15, -.6, .6,
             -.15, index*.1, (.7,.7,.72))
    stage.GetRootLayer().Export(str(root / "house.usda"))
    descriptor = {"schema":"eai.topology.usd_scene", "version":1, "scene_key":"synthetic_house",
        "usd_path":"house.usda", "levels":[
            {"id":"ground", "label":"Ground floor", "support_z_m":0.0},
            {"id":"upper", "label":"Upper floor", "support_z_m":2.0}],
        "camera":{"eye":[9,-10,9], "target":[1.5,0,1]}}
    (root / "scene.json").write_text(json.dumps(descriptor, indent=2)+"\n", encoding="utf-8")
    adapter = UsdSceneAdapter("synthetic_house")
    try:
        source, document = build_document(adapter, root)
        for identifier, point, level, label in (
            ("living", [-2,0,0], "ground", "Living room"),
            ("stair_bottom", [-.2,0,0], "ground", "Stair bottom"),
            ("stair_top", [3.2,0,2], "upper", "Stair top"),
            ("bedroom", [5,0,2], "upper", "Bedroom")):
            document.add_node(point, node_id=identifier, level_id=level, label=label)
        for identifier, label, level, polygon, entrance in (
            ("room:living", "Living room", "ground", [[-3,-1,0],[-1,-1,0],[-1,1,0],[-3,1,0]], "living"),
            ("room:bedroom", "Bedroom", "upper", [[4,-1,2],[6,-1,2],[6,1,2],[4,1,2]], "bedroom")):
            document.upsert_semantic("regions", {"id":identifier, "label":label, "kind":"room",
                "level_id":level, "polygon_xyz":polygon, "height_m":2.5,
                "entrance_node_ids":[entrance], "aliases":[], "properties":{}})
        document.add_waypoint_route(route_id="route:ground", label="Living room to stairs",
            start_node_id="living", end_node_id="stair_bottom")
        document.add_waypoint_route(route_id="route:stairs", label="Ground to upper floor",
            start_node_id="stair_bottom", end_node_id="stair_top", traversal_mode="stairs",
            intermediate_xyz=[[i*.15-.075,0,i*.1] for i in range(1,21)])
        document.add_waypoint_route(route_id="route:upper", label="Stairs to bedroom",
            start_node_id="stair_top", end_node_id="bedroom")
        document.upsert_semantic("entities", {"id":"object:table", "label":"Living room table",
            "kind":"table", "region_id":"room:living", "position_xyz":[-2,.5,.75],
            "source_ref":None, "properties":{"note":"Annotation only; task grasp poses can be added later"}})
        generate_product(root / "topology.json", document, adapter, source)
        return document
    finally:
        adapter.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True,
                        help="new example directory (existing files are refused)")
    args = parser.parse_args(argv)
    if any((args.output_root/name).exists() for name in ("scene.json", "house.usda", "topology.json")):
        parser.error("example files already exist; choose a new directory")
    document = build_example(args.output_root)
    print(json.dumps({"map":str((args.output_root/"topology.json").resolve()),
                      "nodes":len(document.nodes), "edges":len(document.edges)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
