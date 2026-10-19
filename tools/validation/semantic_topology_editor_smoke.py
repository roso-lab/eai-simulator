"""Native Kit callback/render check; no OS mouse or robot traversal claim."""
from __future__ import annotations
import argparse
import gc
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_root.mkdir(parents=True, exist_ok=False)
    from isaacsim import SimulationApp
    app = SimulationApp({"headless":False, "fast_shutdown":False})
    window = adapter = stage = evidence_window = light = builder = None
    try:
        from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
        from tools.topology_authoring.author import build_document, restore_document
        from tools.topology_authoring.ui import TopologyAuthoringWindow
        from tools.topology_authoring.viewport import OVERLAY_ROOT
        from tools.topology_authoring.product import product_digest, load_product
        from algorithm.skills.navigation.topology import route_graph_digest
        from tools.topology_authoring.deploy import capture_frame
        from isaacsim.core.utils.viewports import set_camera_view
        from pxr import UsdLux
        import omni.usd
        adapter = UsdSceneAdapter("synthetic_house")
        source, doc = build_document(adapter,args.asset_root)
        doc = restore_document(doc,args.asset_root/"topology.json",adapter,source)
        before_source = adapter.source_digests(source)
        adapter.load_stage(source)
        for _ in range(20):
            app.update()
        stage = omni.usd.get_context().get_stage()
        light = UsdLux.DomeLight.Define(stage,"/World/AuthoringSmokeLight")
        light.CreateIntensityAttr().Set(250)
        window = TopologyAuthoringWindow(doc,adapter,source,args.output_root)
        window.window.position_x = 850
        window.window.position_y = 28
        window.window.width = 575
        window.window.height = 820
        for _ in range(8):
            app.update()
        window.set_active_level("ground")
        window._semantic_id_model.set_value("room:study")
        window._semantic_label_model.set_value("Study")
        window._region_entrances_model.set_value("living")
        window.begin_region_drawing()
        for point in ((-3,-1,0),(-1,-1,0),(-1,1,0),(-3,1,0)):
            window.on_viewport_click(point)
        assert len(window._semantic_drawing["points"]) == 4
        window.confirm_region_drawing()
        assert window._semantic_drawing is None, window.status.text
        assert len(doc.semantics["regions"]) == 3
        window._semantic_id_model.set_value("route:stairs_ui")
        window._draw_route_start_model.set_value("stair_bottom")
        window._draw_route_end_model.set_value("stair_top")
        window.begin_route_drawing("stairs")
        for index in range(1,21):
            window.on_viewport_click((index*.15-.075,0,index*.1))
        assert len(window._semantic_drawing["points"]) == 20
        window.confirm_route_drawing()
        assert window._semantic_drawing is None, window.status.text
        assert len(doc.semantics["routes"]) == 4
        graph = route_graph_digest(doc)
        window.new_semantic_record("interaction_poses")
        record = json.loads(window._semantic_record_model.as_string)
        record.update(id="pose:table_grasp", owner_id="object:table", level_id="ground",
                      node_id="living", position_xyz=[-2,.5,.85], heading_rad=1.57)
        window._semantic_record_model.set_value(json.dumps(record))
        window.apply_semantic_record()
        assert len(doc.semantics["interaction_poses"]) == 1, window.status.text
        assert route_graph_digest(doc) == graph
        window.save_draft()
        window.generate()
        digest = product_digest(doc)
        window.load_draft()
        assert product_digest(doc) == digest
        assert load_product(args.output_root/"authoring/navigation/ground/topology.json",adapter,source).to_dict() == doc.to_dict()
        window.set_active_level("upper")
        assert not stage.GetPrimAtPath(OVERLAY_ROOT+"/interaction_poses/P00000")
        window.set_active_level(None)
        assert stage.GetPrimAtPath(OVERLAY_ROOT+"/interaction_poses/P00000")
        assert stage.GetPrimAtPath(OVERLAY_ROOT+"/Regions/R00000/Floor")
        window.load_source()
        assert product_digest(doc) == digest, window.status.text
        stage = omni.usd.get_context().get_stage()
        light = UsdLux.DomeLight.Define(stage,"/World/AuthoringSmokeLight")
        light.CreateIntensityAttr().Set(250)
        window.set_active_level(None)
        set_camera_view(eye=(6,-9,5),target=(1,0,1))
        capture_frame(app,args.output_root/"overview.png")
        # Render the actual panel builders in isolated native windows so the
        # screenshot does not depend on offscreen scrolling/raster caching.
        import omni.ui as ui
        window.window.visible = False
        evidence_window = ui.Window("Semantic editor panel inspection", width=575, height=820)
        evidence_window.position_x, evidence_window.position_y = 850, 28
        for builder, name in ((window._build_semantic_controls,"drawing-controls.png"),
                              (window._build_semantic_records,"semantic-records.png")):
            with evidence_window.frame:
                with ui.ScrollingFrame():
                    with ui.VStack(height=0):
                        builder()
            evidence_window.frame.invalidate_raster()
            for _ in range(20):
                app.update()
            capture_frame(app,args.output_root/name)
        window.select_semantic_record("interaction_poses", "pose:table_grasp")
        capture_frame(app,args.output_root/"semantic-record-selected.png")
        evidence_window.destroy()
        evidence_window = builder = None
        assert adapter.source_digests(source) == before_source
        report = {"passed":True,"native_callbacks":True,"os_mouse_delivery":False,
                  "physical_traversal_tested":False,"source_unchanged":True,
                  "isolated_native_panel_capture":True,
                  "product_digest":digest,"nodes":len(doc.nodes),"edges":len(doc.edges),
                  "semantic_counts":{name:len(doc.semantics[name]) for name in
                                     ("regions","routes","entities","interaction_poses")}}
        (args.output_root/"result.json").write_text(json.dumps(report,indent=2)+"\n")
        print("SEMANTIC_TOPOLOGY_EDITOR_COMPLETE",json.dumps(report),flush=True)
    finally:
        # Release native UI objects and bound builders before Kit unloads them.
        if evidence_window:
            evidence_window.destroy()
        evidence_window = builder = light = None
        if window:
            window.destroy()
        window = None
        stage = None
        if adapter:
            adapter.close()
        adapter = None
        gc.collect()
        from simulator import _prepare_gui_for_app_close
        _prepare_gui_for_app_close(app)
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
