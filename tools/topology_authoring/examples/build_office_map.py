"""Build the editable, evidence-labeled office semantic topology product."""
from __future__ import annotations
import argparse
from pathlib import Path
from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
from tools.topology_authoring.author import build_document
from tools.topology_authoring.product import generate_product, save_draft, validate_document

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "maps" / "office"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output-root", type=Path, required=True)
    args = p.parse_args(); args.output_root.mkdir(parents=True, exist_ok=False)
    adapter = UsdSceneAdapter("office_semantic")
    source, doc = build_document(adapter, ASSET)
    try:
        # Room footprints are editable authoring hypotheses tied to measured support levels.
        nodes = [
            ("l1:open-area", [0.0, -0.8, 0.1095316], "1F", "一层开敞区"),
            ("l1:stair-opening", [0.95, -2.14, 0.1095316], "1F", "一层楼梯洞口"),
            ("l2:meeting-area", [0.0, -1.5, 1.1372218], "2F", "二层会议区"),
            ("l2:stair-opening", [0.95, -2.14, 1.1372218], "2F", "二层楼梯洞口"),
            ("roof:main-deck", [0.0, -1.5, 2.1140029], "3F", "主楼屋面平台"),
            ("roof:tower-stair", [1.05, -2.14, 2.1140029], "3F", "塔楼上屋面楼梯"),
        ]
        for node_id, xyz, level, label in nodes:
            doc.add_node(xyz, node_id=node_id, level_id=level, kind="transition" if "stair" in node_id else "custom", label=label)
        regions = [
            ("region:l1-open-area", "一层开敞活动区", "1F", [[-2.6,-3.3,.1095316],[2.5,-3.3,.1095316],[2.5,2.5,.1095316],[-2.6,2.5,.1095316]], ["l1:open-area"], {"evidence":"first-cutaway.png", "confidence":"medium"}),
            ("region:l2-meeting-area", "二层会议区", "2F", [[-2.5,-3.3,1.1372218],[2.5,-3.3,1.1372218],[2.5,2.5,1.1372218],[-2.5,2.5,1.1372218]], ["l2:meeting-area"], {"evidence":"second-room.png;second-cutaway.png", "confidence":"medium-high"}),
            ("region:roof-main-deck", "主楼屋面平台", "3F", [[-2.5,-3.5,2.1140029],[1.5,-3.5,2.1140029],[1.5,2.5,2.1140029],[-2.5,2.5,2.1140029]], ["roof:main-deck"], {"evidence":"roof.png;roof-cutaway.png", "confidence":"high", "outdoor":True}),
        ]
        for rid,label,level,poly,entrances,props in regions:
            doc.upsert_semantic("regions", {"id":rid,"kind":"room" if level != "3F" else "outdoor_platform","label":label,"aliases":[],"level_id":level,"polygon_xyz":poly,"height_m":2.2,"entrance_node_ids":entrances,"properties":props})
        entities = [
            ("entity:l1:round-table-group", "一层圆桌椅群", "furniture", "region:l1-open-area", "1F", [1.4,1.2,.75], {"confidence":"medium","evidence":"first-cutaway.png","pose_status":"deferred"}),
            ("entity:l1:stair-switchback", "一层双跑开敞楼梯与中间平台", "stair", "region:l1-open-area", "1F", [0.95,-2.5,.55], {"confidence":"high","evidence":"capture-02/first-stairs.png","geometry_status":"measured_route_draft"}),
            ("entity:l1:under-stair-seating", "梯下浅木桌椅区", "furniture", "region:l1-open-area", "1F", [0.2,-1.8,.7], {"confidence":"medium","evidence":"capture-02/first-room.png","pose_status":"deferred"}),
            ("entity:l2:meeting-table", "会议桌椅群", "furniture", "region:l2-meeting-area", "2F", [0.0,-1.5,1.9], {"confidence":"medium-high","evidence":"second-room.png","pose_status":"deferred"}),
            ("entity:l2:bar-table", "二层高脚桌与凳（与会议桌组是否同层待核对）", "furniture", "region:l2-meeting-area", "2F", [0.8,-1.5,1.7], {"confidence":"high","evidence":"capture-02/second-room.png","semantic_status":"same_level_unresolved","pose_status":"deferred"}),
            ("entity:roof:round-units", "屋面圆形设备/罩（用途未定）", "equipment", "region:roof-main-deck", "3F", [0.9,-1.8,2.25], {"confidence":"medium","evidence":"roof.png","semantic_status":"unresolved"}),
            ("entity:roof:panels", "屋面三块架空板/洞口（用途未定）", "opening_or_panel", "region:roof-main-deck", "3F", [-0.4,-1.2,2.2], {"confidence":"medium-high","evidence":"roof-cutaway.png","semantic_status":"unresolved"}),
        ]
        for eid,label,kind,rid,level,pos,props in entities:
            doc.upsert_semantic("entities", {"id":eid,"kind":kind,"label":label,"aliases":[],"region_id":rid,"level_id":level,"position_xyz":pos,"source_ref":None,"properties":props})
        # Measured tread centers from collision triangles; route is authored but not robot-certified.
        lower = [(1.117,-2.135,.236),(1.117,-2.245,.295),(1.117,-2.355,.354),(1.117,-2.465,.413),(1.117,-2.575,.472),(1.117,-2.685,.531),(1.117,-2.795,.590),(1.117,-2.85,.658)]
        landing = [(0.592,-3.10,.658)]
        upper = [(0.592,-2.885,.708),(0.592,-2.775,.767),(0.592,-2.665,.826),(0.592,-2.555,.885),(0.592,-2.445,.944),(0.592,-2.335,1.003),(0.592,-2.225,1.062),(0.592,-2.115,1.121)]
        doc.add_waypoint_route(route_id="route:1F-to-2F-stairs",label="一层至二层楼梯（几何草稿）",start_node_id="l1:stair-opening",end_node_id="l2:stair-opening",intermediate_xyz=lower+landing+upper,traversal_mode="stairs",required_controller_capabilities=["stair_capable"])
        doc.add_waypoint_route(route_id="route:1F-open-to-stairs", label="一层开敞区至楼梯入口（待机器人净空复核）", start_node_id="l1:open-area", end_node_id="l1:stair-opening", traversal_mode="walk")
        doc.add_waypoint_route(route_id="route:2F-stairs-to-meeting", label="二层楼梯入口至会议区（待机器人净空复核）", start_node_id="l2:stair-opening", end_node_id="l2:meeting-area", traversal_mode="walk")
        # The roof/tower stair is visible, but its lower endpoint and continuity are not confirmed.
        doc.upsert_semantic("entities", {"id":"entity:roof:stair-continuity-unverified","kind":"stair","label":"塔楼上屋面楼梯（连接待确认）","aliases":[],"region_id":"region:roof-main-deck","level_id":"3F","position_xyz":[1.05,-2.14,2.1140029],"source_ref":None,"properties":{"status":"unverified","evidence":"roof-cutaway.png","reason":"下端层级与连续性无法由当前视图确认"}})
        errors = validate_document(doc, adapter, source)
        if errors: raise RuntimeError("; ".join(errors))
        save_draft(args.output_root/"topology.draft.json", doc)
        generate_product(args.output_root/"topology.json", doc, adapter, source)
        print({"map":str(args.output_root/"topology.json"),"draft":str(args.output_root/"topology.draft.json"),"errors":errors})
    finally:
        adapter.close()

if __name__ == "__main__": main()
