"""Generate the complete subway algorithm × tier × roster × condition matrix."""
import argparse, csv, json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from benchmark.subway_algorithms import EmosSubwayAlgorithm, TeamWeaverSubwayAlgorithm
from benchmark.scenarios.subway import SubwayScenarioAdapter
ROOT=Path(__file__).resolve().parents[1]
def _job(job):
    algorithm_cls, variant, tier, condition, seed = job
    adapter=SubwayScenarioAdapter(); result=adapter.run_generic(tier=tier,variant_id=variant,condition_id=condition,algorithm=algorithm_cls(),seed=seed,replay=True)
    metrics=result.get("diagnostic_metrics",{})
    return {"algorithm":algorithm_cls.algorithm_id,"algorithm_scope":result.get("algorithm_scope","deterministic_contract_witness"),"native_algorithm":False,"adapter_provenance":result.get("adapter_provenance","shared_allocation_witness"),"variant_id":variant,"tier":tier,"task_id":result["scenario"]["task_id"],"condition_id":condition,"seed":seed,"status":result.get("status","completed"),"success":bool(result.get("success",False)),"failure_reason":result.get("failure_reason"),"makespan_s":result.get("makespan_s"),"hard_deadline_s":result.get("hard_deadline_s"),"deadline_met":result.get("deadline_met",False),"task_completion_rate":result.get("task_completion_rate",0.0),"search_coverage":metrics.get("search_coverage",0.0),"ground_contact_count":metrics.get("ground_contact_count",0),"generator_start_success":metrics.get("generator_start_success",0),"deadline_suspended_count":metrics.get("deadline_suspended_count",0),"deadline_extension_s":metrics.get("deadline_extension_s",0.0),"assistance_window_timeout_count":metrics.get("assistance_window_timeout_count",0),"assembly_arrival_rate":sum(x.get('state')=='assembly_complete' for x in metrics.get('people_state',{}).values())/len(metrics.get('people_state',{})) if metrics.get('people_state') else 0.0,"communication_delay_s":result.get("communication_delay_ticks",0)*0.02,"communication_loss_bits":result.get("communication_loss_bits",0),"resource_wait_s":result.get("resource_wait_ticks",0)*0.02,"checkpoint_labels":[x["label"] for x in result.get("checkpoints",[])],"checkpoint_ticks":[x["tick"] for x in result.get("checkpoints",[])],"checkpoint_replay_equivalent":result.get("replay_equivalent",False),"replay_digest":result.get('replay_digest'),"paper_score_eligible":False}

def run_matrix(workers=1):
    adapter=SubwayScenarioAdapter(); jobs=[]; seeds=tuple(adapter.manifest().get("registered_seeds",[17]))
    for algorithm in (EmosSubwayAlgorithm,TeamWeaverSubwayAlgorithm):
      for variant in adapter.manifest()["roster_variants"]:
       for tier in range(1,5):
        for condition in adapter.manifest()["conditions"]:
         for seed in seeds: jobs.append((algorithm,variant["variant_id"],tier,condition["condition_id"],seed))
    if workers<=1: return [_job(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool: return list(pool.map(_job,jobs))
def write_results(rows, json_path=ROOT/'benchmark/results/subway_logic_matrix.v1.json', csv_path=ROOT/'benchmark/results/subway_logic_matrix.v1.csv'):
    json_path.parent.mkdir(parents=True,exist_ok=True); payload={"schema":"heterocoord.subway.logic_matrix.v1","scenario":"subway","clock_dt_s":0.02,"row_count":len(rows),"success_count":sum(bool(x["success"]) for x in rows),"paper_score_eligible":False,"rows":rows}; json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    with csv_path.open('w',newline='') as f:
      w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n'); w.writeheader()
      for row in rows: w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in row.items()})
if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--workers',type=int,default=1); args=parser.parse_args(); rows=run_matrix(args.workers); write_results(rows); print(json.dumps({"rows":len(rows),"success":sum(bool(x["success"]) for x in rows)}))
