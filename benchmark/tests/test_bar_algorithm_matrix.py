from benchmark.bar_algorithms import EmosBarAlgorithm, TeamWeaverBarAlgorithm
from benchmark.scenarios.bar import BarScenarioAdapter

def test_bar_map_and_variants():
 a=BarScenarioAdapter(); g=a.semantic_graph(); m=a.manifest()
 assert len(g.nodes)==59 and len(g.adjacency)==59
 assert g.resolve('floor1-charging_point','ground')==('ground::node:0066',)
 assert len(m['roster_variants'])==4
 assert [x['task_id'] for x in m['difficulty_tiers']]==['bar_delivery_t1','bar_delivery_t2','bar_service_t3','bar_crossfloor_t4']

def test_bar_baseline_runs():
 a=BarScenarioAdapter()
 for alg in (EmosBarAlgorithm,TeamWeaverBarAlgorithm):
  for tier in (1,2):
   r=a.run_generic(tier=tier,condition_id='nominal_no_battery',algorithm=alg())
   assert r['paper_score_eligible'] is False
   assert r['scenario']['condition_id']=='nominal_no_battery'

def test_bar_battery_contracts_and_charge_resources():
 a=BarScenarioAdapter()
 hard=a.build_episode(tier=3,condition_id='battery_hard')
 very=a.build_episode(tier=4,condition_id='battery_very_hard')
 assert all(r.battery_capacity_m==100.0 for r in hard.robots)
 assert next(r for r in hard.resources if r.resource_id=='charging_point').service_ticks==1
 assert next(r for r in very.resources if r.resource_id=='charging_point').service_ticks==200
 assert next(r for r in very.resources if r.resource_id=='charging_point').capacity==1

def test_bar_runtime_replay():
 r=BarScenarioAdapter().run_generic(tier=3,condition_id='battery_hard')
 assert 'replay_equivalent' in r
 assert r['scenario']['task_id']=='bar_service_t3'
