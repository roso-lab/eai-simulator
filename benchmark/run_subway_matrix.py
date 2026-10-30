"""Generate the complete subway algorithm × tier × roster × condition matrix."""
import csv, json
from pathlib import Path
from benchmark.subway_algorithms import EmosSubwayAlgorithm, TeamWeaverSubwayAlgorithm
from benchmark.scenarios.subway import SubwayScenarioAdapter
ROOT=Path(__file__).resolve().parents[1]
def run_matrix():
    a=SubwayScenarioAdapter(); rows=[]
    for alg_cls in (EmosSubwayAlgorithm,TeamWeaverSubwayAlgorithm):
      for variant in a.manifest()["roster_variants"]:
       for tier in range(1,5):
        for cond in a.manifest()["conditions"]:
         r=a.run_generic(tier=tier,variant_id=variant["variant_id"],condition_id=cond["condition_id"],algorithm=alg_cls(),seed=17,replay=False,max_ticks=100)
         rows.append({"algorithm":alg_cls.algorithm_id,"variant_id":variant["variant_id"],"tier":tier,"task_id":r["scenario"]["task_id"],"condition_id":cond["condition_id"],"success":bool(r["success"]),"failure_reason":r["failure_reason"] or "bounded_matrix_probe","makespan_s":r["makespan_s"],"hard_deadline_s":r["hard_deadline_s"],"deadline_met":r["deadline_met"],"task_completion_rate":r["task_completion_rate"],"generator_start_success":r["diagnostic_metrics"]["generator_start_success"],"checkpoint_labels":[x["label"] for x in r["checkpoints"]],"checkpoint_replay_equivalent":r["replay_equivalent"],"execution_max_ticks":100,"paper_score_eligible":False})
    return rows
def write_results(rows, json_path=ROOT/'benchmark/results/subway_logic_matrix.v1.json', csv_path=ROOT/'benchmark/results/subway_logic_matrix.v1.csv'):
    json_path.parent.mkdir(parents=True,exist_ok=True); payload={"schema":"heterocoord.subway.logic_matrix.v1","scenario":"subway","clock_dt_s":0.02,"row_count":len(rows),"success_count":sum(bool(x["success"]) for x in rows),"paper_score_eligible":False,"rows":rows}; json_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    with csv_path.open('w',newline='') as f:
      w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n'); w.writeheader()
      for row in rows: w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,list) else v for k,v in row.items()})
if __name__=='__main__':
    rows=run_matrix(); write_results(rows); print(json.dumps({"rows":len(rows),"success":sum(bool(x["success"]) for x in rows)}))
