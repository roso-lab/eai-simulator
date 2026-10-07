from algorithm.heterocoord.registry import REGISTRY


def test_registry_has_six_primary_and_seven_additional_conditions():
    assert sum(item["paper_scope"] == "table_3_4_primary" for item in REGISTRY) == 6
    assert sum(item["paper_scope"] == "additional_candidate" for item in REGISTRY) == 7
    assert sum(item["paper_scope"] == "existing_eai_extra" for item in REGISTRY) == 1


def test_registry_preserves_missing_artifact_boundaries():
    by_id = {item["algorithm_id"]: item for item in REGISTRY}
    assert by_id["Qrainbow"]["official_repo"] is None
    assert "N/A" in by_id["HARL-HAPPO"]["checkpoint"]
    assert by_id["LiP-LLM-adapted-to-EAI"]["implementation"] == "paper_derived_adapter_not_reproduction"
    assert by_id["LiP-LLM-adapted-to-EAI"]["status"].startswith("N/A_")
    assert by_id["MRTA-heuristic-adapted-to-EAI"]["source_commit"] == "c69509e8d2370f55824519dda0a13252e1817415"
    assert by_id["Hybrid-SUSD-MRTA-adapted-to-EAI"]["source_commit"] == "52473fe16f606dca6e6b3bcc99f3f164afdbe6ee"
    assert by_id["HeteroMRTA-adapted-to-EAI"]["source_commit"] == "db51e29535e34bcaa8cb75f70be3fd28b0027988"


def test_registry_records_system_layer_contract():
    for item in REGISTRY:
        assert item["layer"] == "system"
        assert item["input_contract"].startswith("EaiTaskPackage")
        assert "AlgorithmDecision" in item["output_contract"]
        assert item["executor_owner"].startswith("task.executors")
