import json
from pathlib import Path
from benchmark.scenarios.coffee_shop import CoffeeShopScenarioAdapter

def test_map_and_contract():
    a=CoffeeShopScenarioAdapter(); m=a.manifest(); g=a.semantic_graph()
    assert len(g.nodes)==37 and len(g.edge_requirements)//2==35
    assert m['semantic_map']['robot_validated'] is False
    assert set(['gate-external','ordering_counter-customer','coffee_machine','shaker','rest_area-1','rest_area-2']).issubset(g.bindings)

def test_all_tiers_replay():
    a=CoffeeShopScenarioAdapter()
    for tier in range(1,5):
        r=a.run_generic(tier=tier)
        assert r['success'] and r['replay_equivalent'] and r['paper_score_eligible'] is False

def test_matrix_shape_and_fields():
    rows=json.loads(Path('benchmark/results/coffee_shop_logic_matrix.v1.json').read_text())['rows']
    assert len(rows)==2*4*4*6
    assert all(r['paper_score_eligible'] is False for r in rows)
    assert all('checkpoint_labels' in r and 'freshness_window_s' in r for r in rows)

def test_infeasible_certificates():
    ids={x['case_id'] for x in CoffeeShopScenarioAdapter().infeasible_certificates()}
    assert ids=={'coffee_infeasible_no_manipulator','coffee_infeasible_rest_capacity','coffee_infeasible_recipe_resource','coffee_infeasible_dual_entry_conflict'}

def test_d1_case_registry():
    a=CoffeeShopScenarioAdapter(); cases=a.d1_cases()['cases']
    assert [x['case_id'] for x in cases] == ['coffee_infeasible_no_manipulator','coffee_infeasible_rest_capacity','coffee_infeasible_recipe_resource','coffee_infeasible_dual_entry_conflict']
    assert all(x['proof_type'] and x['certificate'] for x in cases)
