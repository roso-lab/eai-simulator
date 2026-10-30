from benchmark.subway_d1 import subway_d1_certificates

def test_subway_d1_registry_is_fail_closed():
    rows=subway_d1_certificates()
    assert len(rows)==4
    assert all(row['physical_qualified'] is False and row['paper_score_eligible'] is False for row in rows)
    assert rows[0]['status']=='team-state-infeasible'
    assert rows[1]['status']=='team-state-infeasible'
