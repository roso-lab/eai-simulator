import json
from benchmark.scenarios.subway import SubwayScenarioAdapter
from benchmark.subway_algorithms import EmosSubwayAlgorithm, TeamWeaverSubwayAlgorithm

def test_subway_manifest_and_map_contract():
    a=SubwayScenarioAdapter(); m=a.manifest(); g=a.semantic_graph()
    assert m['semantic_map']['ground_nodes']==67 and m['semantic_map']['ground_edges']==69
    assert m['semantic_map']['aerial_nodes']==68 and m['semantic_map']['aerial_edges']==72
    assert g.resolve('assembly_point','ground')==('ground::node:0052',)
    assert g.resolve('assembly_point','aerial')==('aerial::node:0081',)
    assert len(g.resolve('generator-1'))>=2 and len(g.resolve('generator-2'))>=2
    assert len(g.resolve('trapped_people-13'))>=1

def test_subway_variants_and_tier_contracts():
    a=SubwayScenarioAdapter(); m=a.manifest()
    assert len(m['robot_roster'])==9 and len(m['roster_variants'])==6
    cf2x=next(r for r in m['robot_roster'] if r['robot_id']=='cf2x')
    assert 'observation_only' in cf2x['capabilities'] and 'pickplace' not in cf2x['capabilities']
    assert [x['task_id'] for x in m['difficulty_tiers']]==['subway_search_t1','subway_guide_t2','subway_generator_t3','subway_rescue_t4']
    for t in range(1,5):
        e=a.build_episode(tier=t,variant_id='subway_roster_pegasus_iris_m20d1_b2z1')
        assert all(r.start_node in {'aerial::node:0081','ground::node:0052'} for r in e.robots)
        if t>=3: assert sum(x.task_id.endswith('_place') for x in e.tasks)==2

def test_subway_matrix_adapter_replay_is_logically_available():
    a=SubwayScenarioAdapter()
    e=a.build_episode(tier=1,variant_id='subway_roster_pegasus_m20_lite3')
    assert e.communication_condition=='C0'
    result=a.run_generic(tier=1,variant_id='subway_roster_pegasus_m20_lite3',algorithm=EmosSubwayAlgorithm(),seed=17)
    assert result['paper_score_eligible'] is False
    assert result['replay_equivalent'] is True
    assert [x['label'] for x in result['checkpoints']]==['cp_00_spawn_valid','cp_01_search_started','cp_02_target_observed','cp_03_ground_handoff_complete','cp_04_target_confirmed','cp_05_generators_started','cp_06_assistance_or_guide_started','cp_07_assembly_arrival','cp_08_closeout']

def test_subway_algorithm_adapters_have_distinct_provenance():
    assert EmosSubwayAlgorithm.algorithm_id != TeamWeaverSubwayAlgorithm.algorithm_id
