from __future__ import annotations
import csv,json
from pathlib import Path
from benchmark.scenarios.warehouse import WarehouseScenarioAdapter
from benchmark.warehouse_algorithms import EmosWarehouseAlgorithm,TeamWeaverWarehouseAlgorithm
ROOT=Path(__file__).resolve().parents[1]
def run_matrix():
 a=WarehouseScenarioAdapter(); rows=[]
 for alg in (EmosWarehouseAlgorithm,TeamWeaverWarehouseAlgorithm):
  for tier in range(1,5):
   for variant in a.manifest()['roster_variants']:
    for c in a.manifest()['conditions'][:2]:
     try: 
      from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
      rt=DiagnosticRuntime(a.build_episode(tier=tier,condition_id=c['condition_id'],variant_id=variant['variant_id'],seed=17),algorithm=alg())
      r=rt.run(); r['checkpoints']=[]; r['replay_equivalent']=False
     except Exception as e: r={'success':False,'failure_reason':'adapter_error:'+str(e),'makespan_s':0,'hard_deadline_s':0,'deadline_met':False,'task_completion_rate':0,'resource_wait_ticks':0,'resource_contention_requests':0,'communication_delay_ticks':0,'communication_loss_bits':0,'lost_robots':[],'replay_equivalent':False,'paper_score_eligible':False,'checkpoints':[]}
     rows.append({'algorithm':alg.algorithm_id,'variant_id':variant['variant_id'],'tier':tier,'task_id':a._tier(tier)['task_id'],'condition_id':c['condition_id'],'success':bool(r.get('success')),'failure_reason':r.get('failure_reason'),'makespan_s':r.get('makespan_s',0),'hard_deadline_s':r.get('hard_deadline_s',0),'deadline_met':r.get('deadline_met',False),'task_completion_rate':r.get('task_completion_rate',0),'resource_wait_s':r.get('resource_wait_ticks',0)*.02,'resource_contention_requests':r.get('resource_contention_requests',0),'communication_delay_s':r.get('communication_delay_ticks',0)*.02,'communication_loss_bits':r.get('communication_loss_bits',0),'lost_robots':r.get('lost_robots',[]),'checkpoint_labels':[x['label'] for x in r.get('checkpoints',[])],'checkpoint_replay_equivalent':r.get('replay_equivalent',False),'paper_score_eligible':False})
 return rows
def write_results(rows):
 out=ROOT/'benchmark/results'; out.mkdir(exist_ok=True)
 (out/'warehouse_logic_matrix.v1.json').write_text(json.dumps({'schema':'heterocoord.warehouse.logic_matrix.v1','scenario':'warehouse','clock_dt_s':.02,'row_count':len(rows),'success_count':sum(x['success'] for x in rows),'rows':rows},ensure_ascii=False,indent=2)+'\n')
 with (out/'warehouse_logic_matrix.v1.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows({k:(json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v) for k,v in x.items()} for x in rows)
if __name__=='__main__':
 rows=run_matrix();write_results(rows);print(len(rows),sum(x['success'] for x in rows))
