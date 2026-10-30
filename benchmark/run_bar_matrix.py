"""Reproducible CPU diagnostic matrix. Native algorithm results are separate."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import gzip
import hashlib
import json
from pathlib import Path

from benchmark.bar_algorithms import EmosBarAlgorithm, TeamWeaverBarAlgorithm
from benchmark.scenarios.bar import BarScenarioAdapter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = ROOT / 'benchmark/results/bar_logic_matrix.v1.json'
DEFAULT_CSV = ROOT / 'benchmark/results/bar_logic_matrix.v1.csv'


def _run(job):
    alg_id, variant, tier, condition, communication, seed = job
    alg = (EmosBarAlgorithm, TeamWeaverBarAlgorithm)[alg_id]()
    result = BarScenarioAdapter().run_generic(tier=tier, condition_id=condition,
        variant_id=variant, communication=communication, algorithm=alg, seed=seed)
    row = dict(algorithm=alg.algorithm_id, algorithm_scope='deterministic_contract_witness',
        native_algorithm=False, variant_id=variant, tier=tier, condition_id=result['scenario']['condition_id'],
        communication=communication or BarScenarioAdapter()._condition(condition, tier)['communication'], seed=seed,
        task_id=result['scenario']['task_id'], contract_digest=result['scenario']['digest'], paired_state_digest=result['paired_state_digest'],
        communication_capacity_bits_per_tick=result['communication_capacity_bits_per_tick'],
        **{key: result[key] for key in ('success', 'failure_reason', 'makespan_s', 'hard_deadline_s', 'deadline_met',
            'task_completion_rate', 'charge_services', 'service_coverage', 'max_service_gap_s', 'energy_used',
            'distance_m', 'resource_wait_ticks', 'resource_contention_requests', 'communication_bits',
            'communication_delay_ticks', 'communication_loss_bits', 'notification_delivered', 'reallocation_count')},
        checkpoint_replay_equivalent=result['replay_equivalent'],
        availability_events=result['availability_events'], obligations=result['evaluation']['records'], paper_score_eligible=False)
    evidence = dict(row, events=result['events'], object_state=result['object_state'],
        logical_objects=result['logical_objects'], service_trace=result['service_trace'], joint_evidence=result['joint_evidence'], resource_state=result['resource_state'], resource_peak_occupancy=result['resource_peak_occupancy'], evaluation=result['evaluation'],
        checkpoints=[{k:v for k,v in cp.items() if k != 'snapshot'} for cp in result['checkpoints']])
    return row, evidence


def jobs(seeds):
    manifest = BarScenarioAdapter().manifest()
    result = []
    for seed in seeds:
        for alg in (0, 1):
            for v in manifest['roster_variants']:
                for tier in range(1, 5):
                    if tier == 4 and any(r in {'g1', 'lite3'} for r in v['robots']):
                        continue  # Excluded by the authored T4 pool, not an algorithm failure.
                    for c in manifest['conditions']:
                        result.append((alg, v['variant_id'], tier, c['condition_id'], None, seed))
                    for comm in ('C1', 'C2'):
                        result.append((alg, v['variant_id'], tier, None, comm, seed))
    return result


def run_matrix(seeds=(17, 18, 19), workers=1):
    selected = jobs(seeds)
    if workers == 1:
        return list(map(_run, selected))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run, selected))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seeds', default='17,18,19')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output', type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()
    seeds = tuple(int(s) for s in args.seeds.split(','))
    results = run_matrix(seeds, args.workers)
    rows = [r for r, _ in results]
    payload = {'schema':'heterocoord.bar.logic-matrix.v2', 'seeds':seeds, 'clock_dt_s':0.02,
        'row_count':len(rows), 'success_count':sum(r['success'] for r in rows),
        'algorithm_scope':'deterministic_contract_witness', 'native_algorithm':False,
        'paper_score_eligible':False, 'source_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [ROOT/'benchmark/bar_runtime_v1.py', ROOT/'benchmark/scenarios/bar.py', ROOT/'benchmark/bar_algorithms.py', ROOT/'benchmark/bar_evaluator_v1.py',
             ROOT/'benchmark/tasks/bar/bar_task_manifest.v1.json', ROOT/'task/runtime/logical_objects/ledger.py']}, 'rows':rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
    with args.output.with_suffix('.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n'); w.writeheader()
        for row in rows:
            w.writerow({k:json.dumps(v, ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    with gzip.GzipFile(str(args.output.with_suffix('.evidence.jsonl.gz')), 'wb', mtime=0) as f:
        for _, evidence in results:
            f.write((json.dumps(evidence, ensure_ascii=False, separators=(',',':'))+'\n').encode())
    print(json.dumps({'rows':len(rows),'success':payload['success_count'],
                      'replay_equivalent':sum(r['checkpoint_replay_equivalent'] for r in rows)}), flush=True)


if __name__ == '__main__':
    main()
