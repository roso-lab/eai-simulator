"""Isaac-free bar scenario adapter with roster variants and battery conditions."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math
from pathlib import Path
from typing import Any, Mapping
from benchmark.hetero_diagnostic_runtime_v1 import (CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, GreedyGoalAlgorithm, ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec)
ROOT=Path(__file__).resolve().parents[2]
DEFAULT_MAP=ROOT/"usd/scene/Production and logistics/bar/semantic_map/current"
DEFAULT_MANIFEST=ROOT/"benchmark/tasks/bar/bar_task_manifest.v1.json"
def _load(p:Path): return json.loads(p.read_text(encoding="utf-8"))
def _digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
def _length(poly, a, b):
 pts=[tuple(x) for x in (poly or [a,b])]; return sum(math.dist(pts[i],pts[i+1]) for i in range(len(pts)-1))
@dataclass(frozen=True)
class BarScenarioAdapter:
 map_root: Path=DEFAULT_MAP
 manifest_path: Path=DEFAULT_MANIFEST
 @property
 def scenario_id(self): return "bar"
 def manifest(self): return _load(self.manifest_path)
 def semantic_graph(self):
  nodes=[]; edges=[]; labels={}
  for domain,fn in (("ground","topology.json"),("aerial","topology-aerial.json")):
   d=_load(self.map_root/fn); pos={x["id"]:tuple(float(v) for v in x["position_xyz"]) for x in d.get("nodes",[])}
   for nid,p in sorted(pos.items()): nodes.append(SemanticNode(f"{domain}::{nid}",domain,p))
   for e in d.get("edges",[]):
    edges.append(SemanticEdge(f"{domain}::{e['from']}",f"{domain}::{e['to']}",_length(e.get("polyline_xyz"),pos[e['from']],pos[e['to']])))
  g=SemanticGraph(nodes,edges)
  for p in _load(self.map_root/"semantic-points.json").get("points",[]):
   for b in p.get("navigation_bindings",[]): g.bind(p["label"],f"{b['domain']}::{b['node_id']}" )
  return g
 def _condition(self,c,t):
  m=self.manifest(); cid=c or {x['difficulty_tier']:x for x in m['difficulty_tiers']}[t]['default_condition']; rows={x['condition_id']:x for x in m['conditions']}
  if cid not in rows: raise ValueError(f"unknown bar condition {cid}")
  return rows[cid]
 def _variant(self,variant_id):
  m=self.manifest(); rows={x['variant_id']:x for x in m['roster_variants']}; return rows[variant_id] if variant_id else rows[m['roster_variants'][0]['variant_id']]
 def build_episode(self, *, tier=4, condition_id=None, variant_id=None, communication=None, seed=0):
  m=self.manifest(); c=self._condition(condition_id,tier); v=self._variant(variant_id); graph=self.semantic_graph(); byid={x['robot_id']:x for x in m['robot_roster']}
  starts=("storage","production","reserve"); robots=[]
  for rid,start in zip(v['robots'],starts):
   row=byid[rid]; cap=float(c.get('battery_capacity',0)) if c.get('battery_enabled') else None
   robots.append(RobotSpec(rid,"ground",f"ground::{m['spawn_points'][start]}",speed_mps=float(row['speed_mps']),capabilities=tuple(row['capabilities']),battery_capacity_m=cap,battery_per_meter=float(c.get('battery_per_meter',0.0)),battery_reserve_m=float(c.get('battery_reserve',0.0))))
  tasks=[]; n={1:1,2:2,3:3,4:2}[tier]
  for i in range(1,n+1):
   pre=() if i==1 else (f"order_{i-1}_place",)
   tasks += [TaskSpec(f"order_{i}_prepare","manipulation",("workbench-1",),prerequisites=pre,required_capabilities=("manipulator",),resource_id="workbench_mutex",service_ticks=10,object_id=f"order_{i}",object_action="prepare"), TaskSpec(f"order_{i}_release","handoff",("floor1-bar_counter-1-inner",),prerequisites=(f"order_{i}_prepare",),required_capabilities=("loader",),resource_id="handoff_window",service_ticks=10,object_id=f"order_{i}",object_action="release"), TaskSpec(f"order_{i}_accept","handoff",("floor1-bar_counter-1-external",),prerequisites=(f"order_{i}_release",),required_capabilities=("handoff_receive",),object_id=f"order_{i}",object_action="accept"), TaskSpec(f"order_{i}_place","manipulation",("floor1-dining_table-{min(i,6)}",),prerequisites=(f"order_{i}_accept",),required_capabilities=("manipulator",),object_id=f"order_{i}",object_action="place")]
  if tier==4:
   tasks += [TaskSpec("stair_transport_1","transport",("floor2-dining_table-1",),prerequisites=("order_1_accept",),required_capabilities=("stair_capable",),resource_id="stair_mutex",service_ticks=15),TaskSpec("stair_transport_2","transport",("floor2-dining_table-2",),prerequisites=("stair_transport_1",),required_capabilities=("stair_capable",),resource_id="stair_mutex",service_ticks=15)]
  resources=[]
  for rr in m['resources']:
   service=float(rr.get('service_s',0.02));
   if rr['resource_id']=="charging_point": service=float(c.get('charge_wait_s',0.0) or 0.0) if c.get('battery_enabled') else 0.02
   resources.append(ResourceSpec(rr['resource_id'],f"ground::{graph.resolve(rr['semantic_label'],'ground')[0].split('::')[1]}",capacity=int(rr['capacity']),service_ticks=max(1,round(service/CONTROL_DT_S)),recharges=bool(rr.get('recharges',False) and c.get('battery_enabled'))))
  deadline=int(m['hard_deadline_ticks'][f't{tier}'])
  return EpisodeSpec(graph,tuple(robots),tuple(tasks),resources=tuple(resources),horizon_ticks=deadline+1000,hard_deadline_tick=deadline,dt_s=CONTROL_DT_S,seed=seed,communication_condition=communication or c['communication'],availability_loss_task=c.get('availability_loss_task'))
 def contract(self,*,tier=4,condition_id=None,variant_id=None):
  m=self.manifest(); c=self._condition(condition_id,tier); row={x['difficulty_tier']:x for x in m['difficulty_tiers']}[tier]; v=self._variant(variant_id)
  return {'scenario_id':self.scenario_id,'scene_key':m['scene_key'],'task_family':m['task_family'],'tier':tier,'task_id':row['task_id'],'condition_id':c['condition_id'],'variant_id':v['variant_id'],'relation_signature':row['relation_signature'],'map_revision':m['semantic_map']['revision'],'semantic_binding_digest':m['semantic_map']['semantic_binding_digest'],'checkpoint_policy':m['checkpoint_policy'],'battery':c,'digest':_digest({'manifest':m,'tier':tier,'condition':c['condition_id'],'variant':v['variant_id']})}
 def run_generic(self,*,tier=4,condition_id=None,variant_id=None,algorithm=None,seed=0):
  runtime=DiagnosticRuntime(self.build_episode(tier=tier,condition_id=condition_id,variant_id=variant_id,seed=seed),algorithm=algorithm or GreedyGoalAlgorithm()); cp=runtime.checkpoint(metadata={'scenario':self.scenario_id,'tier':str(tier),'variant':variant_id or 'default'}); result=runtime.run(); result['scenario']=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id); result['checkpoint']=cp; result['replay_equivalent']=bool(runtime.replay(cp).get('replay_equivalent')); result['diagnostic_metrics']=self.diagnostic_metrics(result); return result
 @staticmethod
 def diagnostic_metrics(result):
  return {'success':bool(result.get('success')),'makespan_s':float(result.get('makespan_s',0.0)),'min_battery_remaining':result.get('min_battery_remaining_m'),'battery_remaining':result.get('battery_remaining_m',{}),'charge_services':result.get('recover_services',0),'resource_wait_s':float(result.get('resource_wait_ticks',0))*CONTROL_DT_S,'communication_delay_s':float(result.get('communication_delay_ticks',0))*CONTROL_DT_S,'paper_score_eligible':False,'D1':bool(result.get('D1_eligible',False)),'D2':False,'D3':bool(result.get('D3_eligible',False)),'D4':False,'D5':'NA','D6':False}
__all__=['BarScenarioAdapter']
