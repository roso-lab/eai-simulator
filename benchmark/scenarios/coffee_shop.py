"""Isaac-free coffee_shop guest-service scenario adapter."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math
from pathlib import Path
from typing import Any, Mapping
from benchmark.hetero_diagnostic_runtime_v1 import CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, RobotSpec, ResourceSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_MAP=ROOT/'usd/scene/Production and logistics/coffee_shop/semantic_map/current'
DEFAULT_MANIFEST=ROOT/'benchmark/tasks/coffee_shop/coffee_shop_task_manifest.v1.json'
DEFAULT_D1_CASES=ROOT/'benchmark/tasks/coffee_shop/coffee_shop_d1_cases.v1.json'

def _load(p:Path): return json.loads(p.read_text(encoding='utf-8'))
def _digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def _length(points): return sum(math.dist(a,b) for a,b in zip(points,points[1:]))

@dataclass(frozen=True)
class CoffeeShopScenarioAdapter:
    map_root: Path=DEFAULT_MAP
    manifest_path: Path=DEFAULT_MANIFEST
    @property
    def scenario_id(self): return 'coffee_shop'
    def manifest(self): return _load(self.manifest_path)
    def semantic_graph(self):
        m=self.manifest(); actual=_load(self.map_root/'manifest.json')
        for k in ('scene_key','revision','semantic_binding_digest'):
            expected=m['scene_key'] if k=='scene_key' else m['semantic_map'][k]
            if actual[k]!=expected: raise ValueError(f'coffee_shop map {k} mismatch')
        topo=_load(self.map_root/'topology.json'); pos={n['id']:tuple(n['position_xyz']) for n in topo['nodes']}
        g=SemanticGraph([SemanticNode('ground::'+i,'ground',p) for i,p in pos.items()])
        for e in topo['edges']:
            pts=[tuple(x) for x in (e.get('polyline_xyz') or [pos[e['from']],pos[e['to']]])]
            g.add_edge(SemanticEdge('ground::'+e['from'],'ground::'+e['to'],_length(pts),not e.get('directed',False),e.get('traversal_mode','walk'),tuple(e.get('required_controller_capabilities',()))))
        points=_load(self.map_root/'semantic-points.json')
        for p in points['points']:
            for b in p.get('navigation_bindings',[]): g.bind(p['label'],b['domain']+'::'+b['node_id'])
        return g
    def _condition(self,cid,tier):
        m=self.manifest(); default=next(x['default_condition'] for x in m['difficulty_tiers'] if x['difficulty_tier']==tier)
        return dict(next(x for x in m['conditions'] if x['condition_id']==(cid or default)))
    def _variant(self,vid):
        rows=self.manifest()['roster_variants']; return next((x for x in rows if x['variant_id']==vid),rows[0])
    def build_episode(self,*,tier=4,condition_id=None,variant_id=None,communication=None,seed=17):
        if tier not in range(1,5): raise ValueError('coffee_shop tier must be 1..4')
        m=self.manifest(); c=self._condition(condition_id,tier); v=self._variant(variant_id); g=self.semantic_graph(); pool={x['robot_id']:x for x in m['robot_pool']}
        starts=('welcome','ordering','production'); robots=[]
        for rid,start in zip(v['robots'],starts):
            row=pool[rid]; caps=tuple(row.get('capabilities',()))
            robots.append(RobotSpec(rid,'ground','ground::'+m['spawn_points'][start],speed_mps=1.0,capabilities=caps))
        tasks=[]; objects=[]
        def add(tid,kind,target,pre=(),caps=('navigation',),resource=None,service=0,obj=None,action=None,cont=None,**kw):
            tasks.append(TaskSpec(tid,kind,(target,),prerequisites=tuple(pre),required_domain='ground',required_capabilities=tuple(caps),resource_id=resource,service_ticks=max(0,round(service/CONTROL_DT_S)),object_id=obj,object_action=action,continuation_group=None,report_required=True,announce_after=tuple(pre),**kw))
        add('gate_open','gate','gate-external',caps=('navigation',),resource='gate_mutex',service=.4)
        add('side_door_open','gate','side_door-external',caps=('navigation',),resource='side_door_mutex',service=.4)
        n={1:2,2:3,3:4,4:5}[tier]
        for i in range(1,n+1):
            p=f'order_{i}'; entrance='gate_open' if i%2 else 'side_door_open'; pre=(entrance,) if i==1 else (f'order_{i-1}_bound',)
            objects.append({'object_id':p,'customer_id':f'customer_{i}','menu_item':f'menu_item_{(i%3)+1}','source':'cup_holder','destination':'rest_area-1' if i%2 else 'rest_area-2','quantity':1})
            add(p+'_arrive','customer_arrival','ordering_counter-customer',pre=pre,caps=('navigation',),service=.1)
            add(p+'_bound','order_bind','ordering_counter-customer',pre=(p+'_arrive',),caps=('navigation',),service=.1,cont=p)
            add(p+'_cup','pick_cup','cup_holder',pre=(p+'_bound',),caps=('navigation',),service=.1,cont=p)
            add(p+'_recipe_acquire','recipe_acquire','spice_rack',pre=(p+'_cup',),caps=('navigation',),service=.1,cont=p)
            proc='shaker' if i%2 else 'coffee_machine'
            add(p+'_process','recipe_process',proc,pre=(p+'_recipe_acquire',),caps=('navigation',),resource='shaker_mutex' if proc=='shaker' else None,service=max(0.5,c['fixed_process_wait_s']),cont=p)
            add(p+'_verify','recipe_verify','ordering_counter-worker',pre=(p+'_process',),caps=('navigation',),service=.1,cont=p)
            area='rest_area-1' if i%2 else 'rest_area-2'
            add(p+'_rest_assign','rest_assign',area,pre=(p+'_verify',),caps=('navigation',),resource='rest_area_1' if area.endswith('1') else 'rest_area_2',service=.1,cont=p)
            add(p+'_guide','customer_guide',area,pre=(p+'_rest_assign',),caps=('navigation',),service=.1,cont=p)
            add(p+'_delivery','delivery',area,pre=(p+'_guide',),caps=('navigation',),service=.1,cont=p)
            add(p+'_departure','customer_departure',area,pre=(p+'_delivery',),caps=('navigation',),service=.1,cont=p)
        add('closeout','closeout','ordering_counter-worker',pre=tuple(f'order_{i}_departure' for i in range(1,n+1)),caps=('navigation',),service=.1)
        resources=[]
        for r in m['resources']:
            resources.append(ResourceSpec(r['resource_id'],g.resolve(r['semantic_label'],'ground')[0],int(r['capacity']),max(1,round(float(r['service_s'])*50))))
        deadline=m['hard_deadline_ticks'][f't{tier}']
        # C3 is a shared FIFO medium.  A retry interval shorter than one
        # serialized report creates a retry burst that can starve the queue
        # and turn a deliverable report into a deterministic timeout.
        policy=m.get('communication_policy', {})
        return EpisodeSpec(g,tuple(robots),tuple(tasks),tuple(resources),deadline+1000,deadline,seed=seed,communication_condition=communication or c['communication'],objects=tuple(objects),report_retry_limit=int(policy.get('report_retry_limit', 8)),report_retry_interval_ticks=int(policy.get('report_retry_interval_ticks', 30)))
    def contract(self,*,tier=4,condition_id=None,variant_id=None):
        m=self.manifest(); c=self._condition(condition_id,tier); v=self._variant(variant_id); row=next(x for x in m['difficulty_tiers'] if x['difficulty_tier']==tier)
        return {**row,'scenario_id':'coffee_shop','condition_id':c['condition_id'],'variant_id':v['variant_id'],'map_revision':m['semantic_map']['revision'],'semantic_binding_digest':m['semantic_map']['semantic_binding_digest'],'freshness_window_s':c['freshness_window_s'],'digest':_digest({'m':m,'tier':tier,'condition':c,'variant':v}),'paper_score_eligible':False}
    def run_generic(self,*,tier=4,condition_id=None,variant_id=None,communication=None,algorithm=None,seed=17):
        if algorithm is None:
            from benchmark.coffee_shop_algorithms import CoffeeShopGoalAlgorithm
            algorithm=CoffeeShopGoalAlgorithm()
        rt=DiagnosticRuntime(self.build_episode(tier=tier,condition_id=condition_id,variant_id=variant_id,communication=communication,seed=seed),algorithm=algorithm)
        cp=rt.checkpoint(); result=rt.run()
        freshness=self._condition(condition_id,tier).get('freshness_window_s')
        expired=[]
        if freshness is not None:
            completed={e.get('task_id'):int(e.get('tick',0)) for e in result.get('events',()) if e.get('kind')=='task_completed'}
            for i in range(1, {1:2,2:3,3:4,4:5}[tier]+1):
                if completed.get(f'order_{i}_delivery', 0)-completed.get(f'order_{i}_process', 0) > round(float(freshness)/CONTROL_DT_S):
                    expired.append(f'order_{i}')
            if expired:
                result['success']=False; result['failure_reason']='freshness_window_expired'; result['freshness_expired']=expired
        result['scenario']=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id); result['checkpoint']=cp; result['replay_equivalent']=rt.replay(cp).get('replay_equivalent',False); result['checkpoints']=self._checkpoints(result); result['diagnostic_metrics']=self.diagnostic_metrics(result); result['diagnostic_metrics']['freshness_expired']=len(expired); result['diagnostic_metrics']['discarded_items']=expired; result['paper_score_eligible']=False
        return result
    @staticmethod
    def _checkpoints(result):
        events=result.get('events',[]); done={e.get('task_id'):e.get('tick') for e in events if e.get('kind')=='task_completed'}
        labels=['cp_00_spawn_valid','cp_01_customer_arrived','cp_02_order_bound','cp_03_recipe_released','cp_04_recipe_complete','cp_05_rest_assigned','cp_06_customer_guided','cp_07_delivery_complete','cp_08_customer_departure','cp_09_closeout']
        return [{'label':x,'tick':0 if i==0 else None,'applicable':i==0} for i,x in enumerate(labels)]
    @staticmethod
    def diagnostic_metrics(result):
        events=result.get('events',[]); return {'success':bool(result.get('success')),'makespan_s':result.get('makespan_s',0.0),'recipe_actions_completed':sum(1 for e in events if e.get('kind')=='task_completed' and 'recipe' in str(e.get('task_id'))),'rest_capacity_wait_s':float(result.get('resource_wait_ticks',0))*CONTROL_DT_S,'resource_contention_requests':result.get('resource_contention_requests',0),'communication_delay_s':float(result.get('communication_delay_ticks',0))*CONTROL_DT_S,'freshness_expired':0,'discarded_items':[],'checkpoint_replay_equivalent':bool(result.get('replay_equivalent')),'paper_score_eligible':False}
    def d1_cases(self):
        return _load(DEFAULT_D1_CASES)
    def infeasible_certificates(self):
        return ({'case_id':'coffee_infeasible_no_manipulator','status':'team-state-infeasible','certificate':'selected roster has no manipulator capability','not_deployable':True,'safe_stop':'do_not_dispatch'}, {'case_id':'coffee_infeasible_rest_capacity','status':'team-state-infeasible','certificate':'customer count exceeds registered rest capacity with no admissible wait','not_deployable':True,'safe_stop':'hold_queue'}, {'case_id':'coffee_infeasible_recipe_resource','status':'team-state-infeasible','certificate':'recipe requires unavailable device/resource','not_deployable':True,'safe_stop':'do_not_dispatch'}, {'case_id':'coffee_infeasible_dual_entry_conflict','status':'team-state-infeasible','certificate':'both entry obligations have no feasible gate schedule','not_deployable':True,'safe_stop':'safe_stop'})

__all__=['CoffeeShopScenarioAdapter']
