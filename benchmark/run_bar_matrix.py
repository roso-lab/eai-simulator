"""Run the complete Isaac-free bar algorithm/variant/condition matrix."""
from __future__ import annotations
import csv,json
from pathlib import Path
from benchmark.bar_algorithms import EmosBarAlgorithm,TeamWeaverBarAlgorithm
from benchmark.scenarios.bar import BarScenarioAdapter
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_JSON=ROOT/"benchmark/results/bar_logic_matrix.v1.json"; DEFAULT_CSV=ROOT/"benchmark/results/bar_logic_matrix.v1.csv"
def run_matrix():
 a=BarScenarioAdapter(); m=a.manifest(); rows=[]
 for alg in (EmosBarAlgorithm,TeamWeaverBarAlgorithm):
  for v in m['roster_variants']:
   for tier in range(1,5):
    for c in m['conditions']:
     result=a.run_generic(tier=tier,condition_id=c['condition_id'],variant_id=v['variant_id'],algorithm=alg(),seed=17); d=result.get('diagnostic_metrics',{})
     rows.append({'algorithm':alg.algorithm_id,'variant_id':v['variant_id'],'tier':tier,'task_id':result['scenario']['task_id'],'condition_id':c['condition_id'],'success':bool(result.get('success')),'failure_reason':result.get('failure_reason'),'makespan_s':result.get('makespan_s'),'hard_deadline_s':result.get('hard_deadline_s'),'deadline_met':result.get('deadline_met'),'task_completion_rate':result.get('task_completion_rate'),'min_battery_remaining':d.get('min_battery_remaining'),'battery_remaining':d.get('battery_remaining'),'charge_services':d.get('charge_services'),'resource_wait_s':d.get('resource_wait_s'),'resource_contention_requests':result.get('resource_contention_requests'),'communication_delay_s':d.get('communication_delay_s'),'checkpoint_replay_equivalent':result.get('replay_equivalent'),'paper_score_eligible':False})
 return rows
def main():
 rows=run_matrix(); DEFAULT_JSON.write_text(json.dumps({'schema':'heterocoord.bar.logic-matrix.v1','rows':rows,'paper_score_eligible':False},ensure_ascii=False,indent=2)+"\n",encoding='utf-8');
 with DEFAULT_CSV.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
 print(f"wrote {len(rows)} rows")
if __name__=='__main__': main()
