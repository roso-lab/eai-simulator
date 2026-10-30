"""Subway Isaac-free scene adapter and evaluator-owned rescue state machine."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import hashlib, json, math
from pathlib import Path
from typing import Any
from benchmark.hetero_diagnostic_runtime_v1 import CONTROL_DT_S, DiagnosticRuntime, EpisodeSpec, ResourceSpec, RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec
from benchmark.replay_state_v3 import SnapshotParticipantV3
ROOT=Path(__file__).resolve().parents[2]
DEFAULT_MAP=ROOT/'usd/scene/Service and operations/subway/semantic_map/current'
DEFAULT_MANIFEST=ROOT/'benchmark/tasks/subway/subway_task_manifest.v1.json'
def _load(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def _digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

class SubwayRuntime(DiagnosticRuntime):
    def __init__(self, episode, algorithm=None, *, config):
        self.config=deepcopy(config); self.checkpoints=[]
        self.people={p:{'state':'hidden','discovered_tick':None,'ground_reached_tick':None,'wait_deadline':config.get('base_wait_deadline_ticks') or None,'deadline_suspended':False,'assistance_deadline':None,'assembly_tick':None} for p in config.get('people',())}
        self.generators={g:{'state':'hidden','discovered_tick':None,'pick_tick':None,'place_tick':None,'started_tick':None} for g in ('generator-1','generator-2')}
        self.discovery_events=[]; self.ground_contact_events=[]; self.assistance_window_timeouts=[]; self.deadline_suspension_tick=None; self.deadline_extension_applied=0.0; self.deadline_extension_events=[]
        super().__init__(episode,algorithm); self.mark('cp_00_spawn_valid')
    def _build_registry(self):
        registry=super()._build_registry(); registry.register(SnapshotParticipantV3('subway','v1',self._capture_subway,self._restore_subway,self._capture_subway)); return registry
    def _capture_subway(self):
        return {'people':deepcopy(self.people),'generators':deepcopy(self.generators),'discovery_events':deepcopy(self.discovery_events),'ground_contact_events':deepcopy(self.ground_contact_events),'assistance_window_timeouts':deepcopy(self.assistance_window_timeouts),'deadline_suspension_tick':self.deadline_suspension_tick,'deadline_extension_applied':self.deadline_extension_applied,'deadline_extension_events':deepcopy(self.deadline_extension_events),'checkpoints':deepcopy(self.checkpoints)}
    def _restore_subway(self,s):
        self.people=deepcopy(s['people']); self.generators=deepcopy(s['generators']); self.discovery_events=deepcopy(s['discovery_events']); self.ground_contact_events=deepcopy(s['ground_contact_events']); self.assistance_window_timeouts=deepcopy(s['assistance_window_timeouts']); self.deadline_suspension_tick=s['deadline_suspension_tick']; self.deadline_extension_applied=float(s['deadline_extension_applied']); self.deadline_extension_events=deepcopy(s.get('deadline_extension_events',[])); self.checkpoints=deepcopy(s['checkpoints'])
    def checkpoint(self,*args,**kwargs):
        r=super().checkpoint(*args,**kwargs); r.update(contract_digest=self.config['contract_digest'],semantic_map_digest=self.config['semantic_binding_digest'],evidence_scope='logical_subway_evaluator'); return r
    def mark(self,label):
        if any(x['label']==label for x in self.checkpoints): return
        receipt=self.checkpoint(metadata={'scenario':'subway','phase':label}); self.checkpoints.append({'label':label,'tick':self.tick,'digest':receipt['digest']})
    def _discover(self,label,robot):
        if label in self.people:
            state=self.people[label]
            if state['state']=='hidden':
                state.update(state='discovered',discovered_tick=self.tick); event={'tick':self.tick,'kind':'discovery_event','robot_id':robot.spec.robot_id,'domain':robot.spec.domain,'semantic_label':label,'node':robot.node_id}; self.discovery_events.append(event); self.events.append(event); self.mark('cp_02_target_observed')
        elif label in self.generators:
            state=self.generators[label]
            if state['state']=='hidden': state.update(state='discovered',discovered_tick=self.tick); self.events.append({'tick':self.tick,'kind':'generator_discovered','robot_id':robot.spec.robot_id,'semantic_label':label,'node':robot.node_id})
    def _complete_target(self,robot):
        task=self.tasks.specs.get(robot.task_id) if robot.task_id else None
        if task and task.kind=='inspection':
            if task.targets[0] in self.people: self.mark('cp_01_search_started')
            self._discover(task.targets[0],robot)
        super()._complete_target(robot)
        if task and task.kind=='pick' and task.targets[0] in self.generators:
            self.generators[task.targets[0]]['pick_tick']=self.tick
            self.generators[task.targets[0]]['state']='picked'
            self.events.append({'tick':self.tick,'kind':'generator_pick','generator':task.targets[0],'robot_id':robot.spec.robot_id})
    def _complete_report(self,robot):
        task=self.tasks.specs.get(robot.task_id) if robot.task_id else None; label=task.targets[0] if task and task.targets else None; confirm=bool(task and task.kind=='confirmation'); guide=bool(task and task.kind=='guide'); place=bool(task and task.kind=='place')
        super()._complete_report(robot)
        if confirm and label in self.people:
            s=self.people[label]; s.update(state='assisted',ground_reached_tick=self.tick,wait_deadline=None)
            self.ground_contact_events.append({'tick':self.tick,'person':label,'robot_id':robot.spec.robot_id}); self.events.append({'tick':self.tick,'kind':'ground_handoff_complete','person':label,'robot_id':robot.spec.robot_id}); self.mark('cp_03_ground_handoff_complete'); self.events.append({'tick':self.tick,'kind':'ground_contact','person':label,'robot_id':robot.spec.robot_id}); self.mark('cp_04_target_confirmed')
            if self.config['tier']>=2: s['assistance_deadline']=self.tick+self.config['assistance_window_ticks'] if self.config['tier']==4 else None; self.mark('cp_06_assistance_or_guide_started')
        if guide and label=='assembly_point':
            person=next((p for p in self.people if task.continuation_group=='people_'+p.rsplit('-',1)[-1]),None)
            if person:
                s=self.people[person]
                if s['assistance_deadline'] is not None and self.tick>s['assistance_deadline']: self._fail('rescue_failed_after_ground_contact:'+person); return
                s.update(state='assembly_complete',assembly_tick=self.tick); self.events.append({'tick':self.tick,'kind':'assembly_arrival','person':person,'robot_id':robot.spec.robot_id}); self.mark('cp_07_assembly_arrival')
        if place and label in self.generators:
            s=self.generators[label]
            if s.get('state')!='picked' or s.get('pick_tick') is None:
                self._fail('generator_place_without_pick:'+label); return
            s.update(state='started',place_tick=self.tick,started_tick=self.tick); self.events.append({'tick':self.tick,'kind':'generator_place','generator':label,'robot_id':robot.spec.robot_id}); self.events.append({'tick':self.tick,'kind':'generator_started','generator':label,'robot_id':robot.spec.robot_id})
            if all(x['state']=='started' for x in self.generators.values()):
                self.mark('cp_05_generators_started')
                for p,x in self.people.items():
                    if x['ground_reached_tick'] is not None: continue
                    if self.config['tier']==3: x.update(deadline_suspended=True,wait_deadline=None); self.deadline_suspension_tick=self.tick
                    elif self.config['tier']==4 and x['wait_deadline'] is not None:
                        x['wait_deadline']+=self.config['extension_ticks']; self.deadline_extension_applied+=self.config['extension_s']
                        self.deadline_extension_events.append({'tick':self.tick,'person':p,'extension_s':self.config['extension_s']})
    def _check_deadlines(self):
        for p,s in self.people.items():
            if s['wait_deadline'] is not None and not s['deadline_suspended'] and self.tick>s['wait_deadline']: self._fail('person_wait_deadline_exceeded:'+p); return True
            if s['assistance_deadline'] is not None and s['state']!='assembly_complete' and self.tick>s['assistance_deadline']: self.assistance_window_timeouts.append({'tick':self.tick,'person':p}); self._fail('rescue_failed_after_ground_contact:'+p); return True
        return False
    def record_discovery(self, person, robot_id, domain):
        """Evaluator hook for a binding-node observation."""
        robot=self.robot_states[robot_id]; self._discover(person,robot)
    def record_ground_contact(self, person, robot_id):
        """Evaluator hook; contact is valid only for a ground robot."""
        robot=self.robot_states[robot_id]
        if robot.spec.domain!='ground': raise ValueError('ground contact requires a ground robot')
        if person not in self.people or self.people[person]['state']=='hidden': raise ValueError('person must be discovered before ground contact')
        s=self.people[person]; s.update(state='assisted',ground_reached_tick=self.tick,wait_deadline=None)
        self.ground_contact_events.append({'tick':self.tick,'person':person,'robot_id':robot_id}); self.events.append({'tick':self.tick,'kind':'ground_contact','person':person,'robot_id':robot_id}); self.mark('cp_04_target_confirmed')
    def record_assembly_arrival(self, person, robot_id):
        if person not in self.people or self.people[person]['ground_reached_tick'] is None: raise ValueError('ground contact is required')
        self.people[person].update(state='assembly_complete',assembly_tick=self.tick); self.events.append({'tick':self.tick,'kind':'assembly_arrival','person':person,'robot_id':robot_id}); self.mark('cp_07_assembly_arrival')
    def step(self):
        if self.terminal or self._check_deadlines(): return
        super().step()
        if self.success: self.mark('cp_08_closeout')
    def fork(self,checkpoint=None):
        clone=SubwayRuntime(self.episode,algorithm=deepcopy(self.algorithm),config=self.config); clone.restore((checkpoint or self.snapshot()).get('snapshot',checkpoint or self.snapshot())); return clone
    def metrics(self):
        r=super().metrics(); r.update({'people_state':deepcopy(self.people),'generator_state':deepcopy(self.generators),'discovery_events':deepcopy(self.discovery_events),'ground_contact_events':deepcopy(self.ground_contact_events),'assistance_window_timeouts':deepcopy(self.assistance_window_timeouts),'deadline_suspension_tick':self.deadline_suspension_tick,'deadline_extension_s':self.deadline_extension_applied,'deadline_extension_events':deepcopy(self.deadline_extension_events),'subway_checkpoints':deepcopy(self.checkpoints),'paper_score_eligible':False}); return r

@dataclass(frozen=True)
class SubwayScenarioAdapter:
    map_root: Path=DEFAULT_MAP
    manifest_path: Path=DEFAULT_MANIFEST
    @property
    def scenario_id(self): return 'subway'
    def manifest(self): return _load(self.manifest_path)
    def semantic_graph(self):
        m=self.manifest(); actual=_load(self.map_root/'manifest.json')
        if actual['scene_key']!=m['scene_key']: raise ValueError('subway scene mismatch')
        for key in ('revision','semantic_binding_digest'):
            if actual[key]!=m['semantic_map'][key]: raise ValueError(f'subway map {key} mismatch')
        nodes=[]; edges=[]
        for domain,fn in (('ground','topology.json'),('aerial','topology-aerial.json')):
            topo=_load(self.map_root/fn); pos={x['id']:tuple(x['position_xyz']) for x in topo['nodes']}; nodes.extend(SemanticNode(f'{domain}::{n}',domain,p) for n,p in pos.items())
            for e in topo['edges']:
                pts=e.get('polyline_xyz') or [pos[e['from']],pos[e['to']]]; dist=sum(math.dist(a,b) for a,b in zip(pts,pts[1:])); edges.append(SemanticEdge(f'{domain}::{e["from"]}',f'{domain}::{e["to"]}',dist,bidirectional=not e.get('directed',False),traversal_mode=e.get('traversal_mode','walk'),required_controller_capabilities=tuple(e.get('required_controller_capabilities',()))))
        graph=SemanticGraph(nodes,edges); shortest=graph.shortest
        @lru_cache(maxsize=None)
        def cached(source,target,caps): return shortest(source,target,caps)
        graph.shortest=lambda source,target,capabilities=None: cached(source,target,tuple(sorted(capabilities)) if capabilities is not None else None)
        for p in _load(self.map_root/'semantic-points.json')['points']:
            for b in p.get('navigation_bindings',()): graph.bind(p['label'],f'{b["domain"]}::{b["node_id"]}')
        return graph
    def _tier(self,tier):
        if tier not in range(1,5): raise ValueError('subway tier must be 1..4')
        return next(x for x in self.manifest()['difficulty_tiers'] if x['difficulty_tier']==tier)
    def _condition(self,cid,tier): return next(x for x in self.manifest()['conditions'] if x['condition_id']==(cid or self._tier(tier)['default_condition']))
    def _variant(self,variant_id): return next(x for x in self.manifest()['roster_variants'] if x['variant_id']==variant_id) if variant_id else self.manifest()['roster_variants'][0]
    def deployment_diagnostic(self, *, tier=4, variant_id=None):
        """Return a fail-closed contract result for an under-capable roster.

        A missing runtime witness is deliberately reported as
        ``feasibility-unverified``; only a capability set that is proven empty
        is ``team-state-infeasible``.
        """
        m=self.manifest(); v=self._variant(variant_id); profiles={x['robot_id']:x for x in m['robot_roster']}
        rows=[profiles[r] for r in v['robots']]
        aerial=sum(r['domain']=='aerial' for r in rows); ground=[r for r in rows if r['domain']=='ground']
        missing=[]
        req={1:(1,1,False),2:(1,1,False),3:(1,1,True),4:(1,2,True)}[tier]
        if aerial<req[0]: missing.append('aerial_search')
        if len(ground)<req[1]: missing.append('ground_confirmation')
        if req[2] and not any('pickplace' in r['capabilities'] for r in ground): missing.append('generator_pickplace')
        if tier==4 and not v.get('stair_witness',False): missing.append('stair_capability_witness')
        if missing:
            return {'status':'not-deployable','failure_reason':'capability_contract_empty:'+','.join(missing),'diagnostic_class':'team-state-infeasible','safe_stop':'stop_before_dispatch','feasibility':'team-state-infeasible','paper_score_eligible':False}
        return None
    @staticmethod
    def _task(tid,kind,target,*,pre=(),domain=None,caps=(),continuation=None,resource=None,service=0,obj=None,action=None): return TaskSpec(tid,kind,(target,),prerequisites=tuple(pre),required_domain=domain,required_capabilities=tuple(caps),continuation_group=continuation,resource_id=resource,service_ticks=service,report_required=True,announce_after=tuple(pre),object_id=obj,object_action=action)
    def build_episode(self,*,tier=4,condition_id=None,variant_id=None,communication=None,seed=0):
        m,c,v=self.manifest(),self._condition(condition_id,tier),self._variant(variant_id); graph=self.semantic_graph(); profiles={x['robot_id']:x for x in m['robot_roster']}
        robot_rows=[]
        for rid in v['robots']:
            profile=profiles[rid]; caps=list(profile['capabilities'])
            if profile['domain']=='ground' and v.get('stair_witness',False): caps.append('stair_capable')
            robot_rows.append(RobotSpec(rid, profile['domain'], f"{profile['domain']}::{profile['initial_node']}", speed_mps=profile['speed_mps'], capabilities=tuple(sorted(set(caps)))))
        robots=tuple(robot_rows)
        count={1:1,2:3,3:5,4:6}[tier]; people=tuple(f'trapped_people-{i}' for i in range(1,count+1)); tasks=[]; objects=[]
        for i,label in enumerate(people,1):
            search=f'people_search_{i}'; confirm=f'people_confirm_{i}'; tasks.append(self._task(search,'inspection',label,caps=('search',),service=2)); tasks.append(self._task(confirm,'confirmation',label,pre=(search,),domain='ground',caps=('target_confirm',),service=2))
            if tier>=2: tasks.append(self._task(f'people_guide_{i}','guide','assembly_point',pre=(confirm,),domain='ground',caps=('guide',),continuation=f'people_{i}',service=3))
        if tier>=3:
            for g in ('generator-1','generator-2'):
                b=g.replace('-','_'); objects.append({'object_id':b,'source':g,'destination':g}); tasks += [self._task(b+'_discover','inspection',g,domain='ground',caps=('navigation',),service=2),self._task(b+'_pick','pick',g,pre=(b+'_discover',),domain='ground',caps=('manipulator','pickplace'),continuation=b,resource='generator_interaction',service=2,obj=b,action='pick'),self._task(b+'_place','place',g,pre=(b+'_pick',),domain='ground',caps=('manipulator','pickplace'),continuation=b,resource='generator_interaction',service=2,obj=b,action='place')]
        resources=tuple(ResourceSpec(x['resource_id'],graph.resolve(x['semantic_label'],'ground')[0],capacity=x['capacity'],service_ticks=max(1,round(x['service_s']/CONTROL_DT_S))) for x in m['resources']); deadline=m['hard_deadline_ticks'][f't{tier}']; return EpisodeSpec(graph,robots,tuple(tasks),resources=resources,objects=tuple(objects),horizon_ticks=deadline+1,hard_deadline_tick=deadline,seed=seed,communication_condition=communication or c['communication'],availability_loss_task=c.get('availability_loss_task'),report_retry_interval_ticks=100)
    def contract(self,*,tier=4,condition_id=None,variant_id=None):
        m=self.manifest(); c=self._condition(condition_id,tier); v=self._variant(variant_id); row=self._tier(tier); return dict(row,scenario_id='subway',task_family=m['task_family'],tier=tier,condition_id=c['condition_id'],variant_id=v['variant_id'],map_revision=m['semantic_map']['revision'],semantic_binding_digest=m['semantic_map']['semantic_binding_digest'],digest=_digest({'manifest':m,'tier':tier,'condition':c,'variant':v}),paper_score_eligible=False)
    def runtime(self,*,tier=4,condition_id=None,variant_id=None,communication=None,algorithm=None,seed=0):
        from benchmark.subway_algorithms import EmosSubwayAlgorithm
        c=self._condition(condition_id,tier); contract=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id); people=tuple(f'trapped_people-{i}' for i in range(1,{1:2,2:4,3:6,4:7}[tier])); config={'tier':tier,'people':people,'condition_id':c['condition_id'],'contract_digest':contract['digest'],'semantic_binding_digest':contract['semantic_binding_digest'],'base_wait_deadline_ticks':round((c.get('base_wait_deadline_s') or 0)/CONTROL_DT_S),'extension_s':float(c.get('extension_s') or 0),'extension_ticks':round(float(c.get('extension_s') or 0)/CONTROL_DT_S),'assistance_window_ticks':round(float(c.get('assistance_window_s') or 0)/CONTROL_DT_S)}; return SubwayRuntime(self.build_episode(tier=tier,condition_id=condition_id,variant_id=variant_id,communication=communication,seed=seed),algorithm=algorithm or EmosSubwayAlgorithm(),config=config)
    def run_generic(self,*,tier=4,condition_id=None,variant_id=None,communication=None,algorithm=None,seed=0,replay=True,max_ticks=None):
        diagnostic=self.deployment_diagnostic(tier=tier,variant_id=variant_id)
        if diagnostic:
            contract=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id)
            return {'scenario':contract,'success':False,'failure_reason':diagnostic['failure_reason'],'status':'not-deployable','diagnostic_class':diagnostic['diagnostic_class'],'safe_stop':diagnostic['safe_stop'],'paper_score_eligible':False,'replay_equivalent':False,'checkpoints':[],'diagnostic_metrics':{'paper_score_eligible':False}}
        runtime=self.runtime(tier=tier,condition_id=condition_id,variant_id=variant_id,communication=communication,algorithm=algorithm,seed=seed); initial_checkpoint=runtime.checkpoint(metadata={'scenario':'subway','phase':'pre_run'}); result=runtime.run(max_ticks=max_ticks); result['scenario']=self.contract(tier=tier,condition_id=condition_id,variant_id=variant_id); result['checkpoints']=runtime.checkpoints; result['replay_equivalent']=False
        result.update({'algorithm_scope':'deterministic_contract_witness','native_algorithm':False,'adapter_provenance':'shared_allocation_witness','paper_score_eligible':False})
        if replay and result['success']: result['replay_equivalent']=bool(runtime.replay(initial_checkpoint).get('replay_equivalent'))
        result['diagnostic_metrics']={'search_coverage':sum(x['state']!='hidden' for x in runtime.people.values())/len(runtime.people) if runtime.people else 1.0,'generator_start_success':sum(x['state']=='started' for x in runtime.generators.values()),'ground_contact_count':len(runtime.ground_contact_events),'deadline_suspended_count':sum(x['deadline_suspended'] for x in runtime.people.values()),'assistance_window_timeout_count':len(runtime.assistance_window_timeouts),'paper_score_eligible':False}; return result
__all__=['SubwayScenarioAdapter','SubwayRuntime']
