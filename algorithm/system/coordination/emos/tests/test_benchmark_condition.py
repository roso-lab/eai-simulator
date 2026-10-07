from algorithm.system.coordination.emos.benchmark_condition import (
    condition_manifest,
    condition_record,
)


def test_emos_condition_is_adapted_and_weight_free():
    record = condition_record(model_or_api="test-provider")
    assert record.algorithm_id == "EMOS-adapted-to-EAI"
    assert record.checkpoint == "not_applicable_no_learned_weights"
    assert "D4_member_exit_recovery" in record.unsupported_dimensions
    assert condition_manifest(model_or_api="test-provider")["model_or_api"] == "test-provider"
