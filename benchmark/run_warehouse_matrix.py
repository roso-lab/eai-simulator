"""Execute every registered warehouse logical diagnostic, including replay."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
from pathlib import Path

from benchmark.scenarios.warehouse import WarehouseScenarioAdapter
from benchmark.warehouse_algorithms import EmosWarehouseAlgorithm, TeamWeaverWarehouseAlgorithm

ROOT = Path(__file__).resolve().parents[1]
ALGORITHMS = (EmosWarehouseAlgorithm, TeamWeaverWarehouseAlgorithm)


def _run_case(case):
    algorithm_index, tier, variant_id, condition_id = case
    adapter = WarehouseScenarioAdapter()
    algorithm = ALGORITHMS[algorithm_index]
    result = adapter.run_generic(tier=tier, condition_id=condition_id,
                                 variant_id=variant_id, algorithm=algorithm(), seed=17)
    return {
        'algorithm': algorithm.algorithm_id,
        'algorithm_scope': 'deterministic_contract_witness',
        'native_algorithm': False,
        'variant_id': variant_id, 'tier': tier,
        'task_id': adapter._tier(tier)['task_id'], 'condition_id': condition_id,
        'seed': 17, 'status': result.get('status', 'completed'),
        'diagnostic_class': result.get('diagnostic_class', 'logical_execution'),
        'success': bool(result['success']), 'failure_reason': result.get('failure_reason', ''),
        'makespan_s': result.get('makespan_s'), 'hard_deadline_s': result.get('hard_deadline_s'),
        'deadline_met': result.get('deadline_met', False),
        'task_completion_rate': result.get('task_completion_rate', 0.0),
        'resource_wait_s': result.get('resource_wait_ticks', 0) * .02,
        'resource_contention_requests': result.get('resource_contention_requests', 0),
        'communication_delay_s': result.get('communication_delay_ticks', 0) * .02,
        'communication_loss_bits': result.get('communication_loss_bits', 0),
        'lost_robots': result.get('lost_robots', []),
        'checkpoint_labels': [item['label'] for item in result.get('checkpoints', [])],
        'checkpoint_replay_equivalent': result.get('replay_equivalent', False),
        'paper_score_eligible': False,
    }


def run_matrix(*, workers=1):
    manifest = WarehouseScenarioAdapter().manifest()
    cases = [(index, tier, variant['variant_id'], condition['condition_id'])
             for index in range(len(ALGORITHMS)) for tier in range(1, 5)
             for variant in manifest['roster_variants'] for condition in manifest['conditions']]
    if workers == 1:
        return [_run_case(case) for case in cases]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run_case, cases))


def write_results(rows):
    out = ROOT / 'benchmark/results'
    out.mkdir(exist_ok=True)
    sources = ['benchmark/scenarios/warehouse.py', 'benchmark/warehouse_algorithms.py',
               'benchmark/hetero_diagnostic_runtime_v1.py', 'benchmark/transport_v3.py',
               'benchmark/run_warehouse_matrix.py',
               'benchmark/tasks/warehouse/warehouse_task_manifest.v1.json']
    manifest = WarehouseScenarioAdapter().manifest()
    map_root = manifest['semantic_map']['path']
    sources += [f'{map_root}/{name}' for name in
                ('manifest.json', 'semantic-points.json', 'topology.json', 'topology-aerial.json')]
    payload = {
        'schema': 'heterocoord.warehouse.logic_matrix.v1', 'scenario': 'warehouse',
        'clock_dt_s': .02, 'row_count': len(rows),
        'success_count': sum(row['success'] for row in rows),
        'not_deployable_count': sum(row['status'] == 'not-deployable' for row in rows),
        'scope': 'logical_diagnostic_only', 'paper_score_eligible': False,
        'release_gates': manifest['release_gates'], 'timing_policy': manifest['timing_policy'],
        'source_hashes': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in sources}, 'rows': rows,
    }
    (out / 'warehouse_logic_matrix.v1.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (out / 'warehouse_logic_matrix.v1.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                          for key, value in row.items()} for row in rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    rows = run_matrix(workers=args.workers)
    write_results(rows)
    print(json.dumps({'rows': len(rows), 'success': sum(row['success'] for row in rows),
                      'not_deployable': sum(row['status'] == 'not-deployable' for row in rows)}))
