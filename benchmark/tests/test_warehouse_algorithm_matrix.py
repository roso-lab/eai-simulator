import json
from benchmark.scenarios.warehouse import WarehouseScenarioAdapter
from benchmark.warehouse_algorithms import EmosWarehouseAlgorithm, TeamWeaverWarehouseAlgorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime

def test_map_identity_and_binding_domains():
 a=WarehouseScenarioAdapter();g=a.semantic_graph()
 assert len(g.nodes)==474
 assert set(g.resolve('floor2-shelf1'))=={'ground::node:0405','aerial::node:0047'}
 assert set(g.resolve('floor3-east','ground'))=={'ground::node:0417'}
 assert len(g.resolve('workspace','ground'))==6 and len(g.resolve('workspace','aerial'))==6

def test_tiers_and_domain_rosters():
 a=WarehouseScenarioAdapter();m=a.manifest()
 assert [x['task_id'] for x in m['difficulty_tiers']]==['warehouse_known_pick_t1','warehouse_search_pick_t2','warehouse_joint_batch_t3','warehouse_peak_multifloor_t4']
 assert all(2<=len(x['profiles'])<=4 for x in m['roster_variants'])


def test_runtime_builds_all_tiers():
 a=WarehouseScenarioAdapter()
 for alg in (EmosWarehouseAlgorithm,TeamWeaverWarehouseAlgorithm):
  for tier in range(1,5):
   r=DiagnosticRuntime(a.build_episode(tier=tier,variant_id='warehouse_mixed_four'),algorithm=alg()).run()
   assert r['success'] is True
   assert r['paper_score_eligible'] is False
   assert 'task_completion_rate' in r

def test_required_charging_is_public_and_time_budget_is_diagnostic():
 a=WarehouseScenarioAdapter()
 for tier, deadline in ((3, 10000), (4, 12000)):
  episode=a.build_episode(tier=tier,variant_id='warehouse_mixed_four')
  assert episode.hard_deadline_tick == deadline
  assert all(task.public for task in episode.tasks if task.kind == 'charge')

def test_checkpoint_schema_and_replay():
 a=WarehouseScenarioAdapter(); result=a.run_generic(tier=1,variant_id='warehouse_go2_pegasus')
 assert result['replay_equivalent'] is True
 assert [x['label'] for x in result['checkpoints']]==['cp_00_spawn_valid','cp_01_order_bound','cp_02_region_observed','cp_03_shelf_resolved','cp_04_items_verified','cp_05_joint_carry_ready','cp_06_workspace_delivery','cp_07_charge_complete','cp_08_order_closeout']

def test_missing_logical_route_stops_before_dispatch_without_d1_certification():
 result=WarehouseScenarioAdapter().run_generic(
     tier=2,variant_id='warehouse_aerial_pair',condition_id='nominal_no_battery')
 assert result['status']=='not-deployable'
 assert result['diagnostic_class']=='feasibility-unverified'
 assert result['failure_reason']=='logical_route_unavailable:order_2_acquire'
 assert result['paper_score_eligible'] is False

def test_matrix_covers_all_registered_conditions_and_records_real_replay():
 from pathlib import Path
 matrix=json.loads(Path('benchmark/results/warehouse_logic_matrix.v1.json').read_text())
 manifest=WarehouseScenarioAdapter().manifest()
 keys={(row['algorithm'],row['tier'],row['variant_id'],row['condition_id'])
       for row in matrix['rows']}
 assert len(keys)==len(matrix['rows'])==2*4*len(manifest['roster_variants'])*len(manifest['conditions'])
 assert {row['condition_id'] for row in matrix['rows']}=={c['condition_id'] for c in manifest['conditions']}
 assert all(row['failure_reason']!='logic_matrix_pending_runtime_execution' for row in matrix['rows'])
 assert all(row['checkpoint_replay_equivalent'] for row in matrix['rows'] if row['status']=='completed')
 assert matrix['paper_score_eligible'] is False
