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
   assert r['paper_score_eligible'] is False
   assert 'task_completion_rate' in r

def test_checkpoint_schema_and_replay():
 a=WarehouseScenarioAdapter(); result=a.run_generic(tier=1,variant_id='warehouse_go2_pegasus')
 assert result['replay_equivalent'] is True
 assert [x['label'] for x in result['checkpoints']]==['cp_00_spawn_valid','cp_01_order_bound','cp_02_region_observed','cp_03_shelf_resolved','cp_04_items_verified','cp_05_joint_carry_ready','cp_06_workspace_delivery','cp_07_charge_complete','cp_08_order_closeout']
