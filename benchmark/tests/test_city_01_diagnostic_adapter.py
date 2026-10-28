from benchmark.scenarios.city_01 import City01ScenarioAdapter


def test_city01_adapter_exposes_contract_and_diagnostic_metrics():
    adapter = City01ScenarioAdapter()
    contract = adapter.contract(tier=4)
    assert contract["scenario_id"] == "city_01"
    assert contract["relation_signature"]["P2"] is True
    result = adapter.run_compatibility(tier=1)
    assert result["scenario"]["map_revision"] == contract["map_revision"]
    assert result["diagnostic_metrics"]["coverage_fraction"] == 1.0
    assert result["diagnostic_metrics"]["D5"]["status"] == "na"
    assert adapter.validate_result(result)["valid"] is True
