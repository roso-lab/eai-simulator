"""Isaac-free warehouse order picking adapter.

Task eligibility is derived from semantic navigation bindings. A source bound to
both ground and aerial domains is intentionally executable by either domain.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math
from pathlib import Path
from typing import Any, Mapping
from benchmark.hetero_diagnostic_runtime_v1 import CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = ROOT / "usd/scene/Production and logistics/warehouse/semantic_map/current"
DEFAULT_MANIFEST = ROOT / "benchmark/tasks/warehouse/warehouse_task_manifest.v1.json"

def _load(p: Path): return json.loads(p.read_text(encoding="utf-8"))
def _digest(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",",":"), ensure_ascii=False).encode()).hexdigest()
def _length(e, pos):
    pts=[tuple(map(float,x)) for x in (e.get("polyline_xyz") or [pos[e["from"]],pos[e["to"]]])]
    return sum(math.dist(a,b) for a,b in zip(pts,pts[1:]))

@dataclass(frozen=True)
class WarehouseScenarioAdapter:
    map_root: Path = DEFAULT_MAP
    manifest_path: Path = DEFAULT_MANIFEST
    @property
    def scenario_id(self): return "warehouse"
    def manifest(self): return _load(self.manifest_path)
    def semantic_graph(self):
        m=self.manifest(); actual=_load(self.map_root/"manifest.json")
        if actual["scene_key"] != m["scene_key"] or actual["revision"] != m["semantic_map"]["revision"] or actual["semantic_binding_digest"] != m["semantic_map"]["semantic_binding_digest"]:
            raise ValueError("warehouse semantic map identity mismatch")
        nodes=[]; edges=[]
        for domain,fn in (("ground","topology.json"),("aerial","topology-aerial.json")):
            d=_load(self.map_root/fn); pos={n["id"]:tuple(n["position_xyz"]) for n in d["nodes"]}
            for nid,p in pos.items(): nodes.append(SemanticNode(f"{domain}::{nid}",domain,p))
            for e in d["edges"]:
                edges.append(SemanticEdge(f"{domain}::{e['from']}",f"{domain}::{e['to']}",_length(e,pos),bidirectional=not e.get("directed",False),traversal_mode=e.get("traversal_mode","walk"),required_controller_capabilities=tuple(e.get("required_controller_capabilities",()))))
        g=SemanticGraph(nodes,edges)
        for p in _load(self.map_root/"semantic-points.json")["points"]:
            for b in p.get("navigation_bindings",[]): g.bind(p["label"],f"{b['domain']}::{b['node_id']}" )
        return g
    def _tier(self,t):
        return next(x for x in self.manifest()["difficulty_tiers"] if x["difficulty_tier"]==t)
    def _condition(self,t,cid):
        cid=cid or self._tier(t)["default_condition"]
        return next(x for x in self.manifest()["conditions"] if x["condition_id"]==cid)
    def _profiles(self, variant_id, tier):
        m=self.manifest(); v=next(x for x in m["roster_variants"] if x["variant_id"]==variant_id) if variant_id else m["roster_variants"][-1]
        rows={x["profile"]:x for x in m["robot_pool"]}
        profiles=list(v["profiles"])
        if tier>=3 and len(profiles)<4:
            # Expand a two-robot candidate deterministically when the contract needs cooperation.
            profiles += [p for p in ["go2_d1","carter_d1","pegasus","iris"] if p not in profiles][:max(0,4-len(profiles))]
        return v, [rows[p] for p in profiles]
    def build_episode(self, *, tier=4, condition_id=None, variant_id=None, communication=None, seed=0):
        if tier not in range(1,5): raise ValueError("warehouse tier must be 1..4")
        m=self.manifest(); t=self._tier(tier); c=self._condition(tier,condition_id); graph=self.semantic_graph(); variant, profiles=self._profiles(variant_id,tier)
        starts=["ground_workspace_1","ground_workspace_2","aerial_workspace_1","aerial_workspace_2"]
        robots=[]
        domain_counts={"ground":0,"aerial":0}
        domain_starts={"ground":["ground_workspace_1","ground_workspace_2"],"aerial":["aerial_workspace_1","aerial_workspace_2"]}
        for i,p in enumerate(profiles):
            domain=p["domain"]; idx=domain_counts[domain]; domain_counts[domain]+=1
            start=m["spawn_points"][domain_starts[domain][min(idx,len(domain_starts[domain])-1)]]
            battery=100.0 if c.get("battery_enabled") else None
            speed=4.0 if domain=="aerial" else 1.0
            robots.append(RobotSpec(f"{p['profile']}_{i+1}",domain,f"{domain}::{start['node_id']}",speed_mps=speed,capabilities=tuple(p["capabilities"]),battery_capacity_m=battery,battery_per_meter=(float(c.get("drain_per_s",0.0))/max(0.1,speed) if battery is not None else 0.0)))
        source_labels=("floor1-south-goods-1","floor1-southeast-shelf6-low","floor1-southeast-shelf1-high","floor2-shelf1")
        region_labels=("floor2-shelf1","floor3-east")
        tasks=[]; objects=[]; deliveries=[]
        if t["search_required"]:
            for i,label in enumerate(region_labels[:2 if tier>=3 else 1],1):
                tasks.append(TaskSpec(f"region_observe_{i}","inspection",(label,),required_capabilities=("navigation",),report_required=True,public=True))
        search_pre=tuple(x.task_id for x in tasks)
        n=int(t["orders"])
        for i in range(1,n+1):
            src=source_labels[(i-1)%len(source_labels)]; oid=f"material_{i}"; pre=search_pre if t["search_required"] else ()
            objects.append({"object_id":oid,"source":src,"destination":"workspace","quantity":2 if (t["heavy_required"] and i==n) else 1,"material_id":f"sku-{i}","weight_class":"heavy" if (t["heavy_required"] and i==n) else "light","workspace_version":"workspace-v1"})
            pick=f"order_{i}_acquire"; verify=f"order_{i}_verify"; place=f"order_{i}_place"; deliveries.append(place)
            tasks.append(TaskSpec(pick,"material_acquire",(src,),prerequisites=pre,required_capabilities=("payload_transport",),object_id=oid,report_required=True,public=True,announce_after=pre))
            tasks.append(TaskSpec(verify,"weight_verify",(src,),prerequisites=(pick,),required_capabilities=("navigation",),report_required=True,public=True))
            if t["heavy_required"] and i==n:
                for role in ("left","right"):
                    tasks.append(TaskSpec(f"order_{i}_joint_{role}","cooperative_carry",("workspace",),prerequisites=(verify,),required_capabilities=("joint_carry","payload_transport"),cooperative_group=f"order_{i}_joint",resource_id="joint_carry_mutex",service_ticks=50,object_id=oid,report_required=True,public=True))
                tasks.append(TaskSpec(place,"workspace_place",("workspace",),prerequisites=(f"order_{i}_joint_left",f"order_{i}_joint_right"),required_capabilities=("payload_transport",),object_id=oid,report_required=True,public=True))
            else:
                tasks.append(TaskSpec(place,"workspace_place",("workspace",),prerequisites=(verify,),required_capabilities=("payload_transport",),object_id=oid,report_required=True,public=True))
        if tier>=3:
            for i,r in enumerate(robots):
                tasks.append(TaskSpec(f"charge_{i+1}","charge",("workspace",),prerequisites=tuple(deliveries),required_capabilities=("battery_charge",),resource_id="workspace_charger",service_ticks=max(1,round(float(c.get("charge_wait_s",0.02))/CONTROL_DT_S)),required_for_success=True,public=False))
        tasks.append(TaskSpec("episode_closeout","closeout",("workspace",),prerequisites=tuple(x.task_id for x in tasks if x.required_for_success),required_for_success=False,public=False))
        resources=[]
        for r in m["resources"]:
            if r["resource_id"]=="workspace_charger": service=float(c.get("charge_wait_s",r["nominal_service_s"]))
            else: service=float(r["nominal_service_s"])
            node=graph.resolve(r["semantic_label"],r["domain"])[0]
            resources.append(ResourceSpec(r["resource_id"],node,int(r["capacity"]),max(1,round(service/CONTROL_DT_S)),bool(r.get("recharges"))))
        deadline=4000+1000*tier
        return EpisodeSpec(graph,tuple(robots),tuple(tasks),tuple(resources),horizon_ticks=deadline+1000,hard_deadline_tick=deadline,dt_s=CONTROL_DT_S,seed=seed,communication_condition=communication or c["communication"],availability_loss_task=(c.get("availability_loss_task") if c.get("availability_loss") else None),objects=tuple(objects))
    def contract(self, *, tier=4, condition_id=None, variant_id=None):
        m=self.manifest(); t=self._tier(tier); c=self._condition(tier,condition_id)
        return {"scenario_id":self.scenario_id,"scene_key":m["scene_key"],"task_family":m["task_family"],"tier":tier,"task_id":t["task_id"],"condition_id":c["condition_id"],"relation_signature":t["relation_signature"],"variant_id":variant_id or m["roster_variants"][-1]["variant_id"],"map_revision":m["semantic_map"]["revision"],"semantic_binding_digest":m["semantic_map"]["semantic_binding_digest"],"paper_score_eligible":False,"digest":_digest({"m":m,"t":tier,"c":c["condition_id"],"v":variant_id})}
    def run_generic(self, *, tier=4, condition_id=None, variant_id=None, communication=None, algorithm=None, seed=0):
        if algorithm is None:
            from benchmark.warehouse_algorithms import EmosWarehouseAlgorithm
            algorithm=EmosWarehouseAlgorithm()
        rt=DiagnosticRuntime(self.build_episode(tier=tier,condition_id=condition_id,variant_id=variant_id,communication=communication,seed=seed),algorithm=algorithm)
        cp=rt.checkpoint(metadata={"scenario":self.scenario_id,"tier":str(tier),"variant":variant_id or "default"}); result=rt.run(); replay=rt.replay(cp)
        result["checkpoint"]=cp; result["replay_equivalent"]=bool(replay.get("replay_equivalent")); result["scenario"]=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id); result["checkpoints"]=self._checkpoints(result); result["diagnostic_metrics"]=self.diagnostic_metrics(result); return result
    @staticmethod
    def _checkpoints(result):
        done={x.get("task_id"):x.get("tick") for x in result.get("events",[]) if x.get("kind")=="task_completed"}
        rows=[{"label":"cp_00_spawn_valid","tick":0}]
        for label,prefix in (("cp_01_order_bound","order_"),("cp_02_region_observed","region_observe_"),("cp_03_shelf_resolved","order_1_acquire"),("cp_04_items_verified","order_1_verify"),("cp_05_joint_carry_ready","order_"),("cp_06_workspace_delivery","order_1_place"),("cp_07_charge_complete","charge_1"),("cp_08_order_closeout","episode_closeout")):
            ticks=[v for k,v in done.items() if v is not None and (k==prefix or str(k).startswith(prefix))]
            rows.append({"label":label,"tick":min(ticks) if ticks else None,"applicable":bool(ticks)})
        return rows
    @staticmethod
    def diagnostic_metrics(result):
        events=result.get("events",[])
        return {"success":bool(result.get("success")),"makespan_s":result.get("makespan_s",0.0),"object_place_success":sum(e.get("kind")=="object_placed" for e in events),"joint_action_count":sum(e.get("kind")=="cooperative_service_started" for e in events),"search_observations":sum(e.get("kind")=="task_completed" and str(e.get("task_id","")).startswith("region_observe_") for e in events),"charge_events":sum(e.get("kind")=="battery_full" for e in events),"resource_contention_requests":result.get("resource_contention_requests",0),"replay_equivalent":bool(result.get("replay_equivalent")),"paper_score_eligible":False,"D1":"diagnostic_only","D2":"diagnostic_only","D3":"diagnostic_only","D4":"diagnostic_only","D5":"NA","D6":"diagnostic_only"}
