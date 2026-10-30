"""Generate the coffee_shop Isaac-free logic matrix."""
import csv,json
from pathlib import Path
from benchmark.scenarios.coffee_shop import CoffeeShopScenarioAdapter
from benchmark.coffee_shop_algorithms import EmosCoffeeShopAlgorithm,TeamWeaverCoffeeShopAlgorithm
ROOT=Path(__file__).resolve().parents[1]
JSON=ROOT/'benchmark/results/coffee_shop_logic_matrix.v1.json'; CSV=ROOT/'benchmark/results/coffee_shop_logic_matrix.v1.csv'
def run_matrix():
 a=CoffeeShopScenarioAdapter(); rows=[]
 for alg_t in (EmosCoffeeShopAlgorithm,TeamWeaverCoffeeShopAlgorithm):
  for v in a.manifest()['roster_variants']:
   for tier in range(1,5):
    for c in a.manifest()['conditions']:
     r=a.run_generic(tier=tier,variant_id=v['variant_id'],condition_id=c['condition_id'],algorithm=alg_t(),seed=17)
     rows.append({'algorithm':alg_t.algorithm_id,'variant_id':v['variant_id'],'tier':tier,'task_id':r['scenario']['task_id'],'condition_id':c['condition_id'],'success':bool(r['success']),'failure_reason':r.get('failure_reason',''),'makespan_s':r.get('makespan_s',0.0),'hard_deadline_s':r.get('hard_deadline_s',0.0),'task_completion_rate':r.get('task_completion_rate',0.0),'resource_wait_s':r.get('resource_wait_ticks',0)*0.02,'resource_contention_requests':r.get('resource_contention_requests',0),'communication_delay_s':r.get('communication_delay_ticks',0)*0.02,'communication_loss_bits':r.get('communication_loss_bits',0),'checkpoint_labels':[x['label'] for x in r['checkpoints']],'checkpoint_replay_equivalent':bool(r['replay_equivalent']),'freshness_window_s':c.get('freshness_window_s'),'freshness_expired':0,'discarded_items':[],'paper_score_eligible':False})
 return rows
def write_results(rows):
 JSON.write_text(json.dumps({'schema':'heterocoord.coffee_shop.logic_matrix.v1','scenario':'coffee_shop','clock_dt_s':0.02,'row_count':len(rows),'success_count':sum(x['success'] for x in rows),'paper_score_eligible':False,'rows':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 with CSV.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
if __name__=='__main__':
 rows=run_matrix();write_results(rows);print(json.dumps({'rows':len(rows),'success':sum(x['success'] for x in rows)}))
