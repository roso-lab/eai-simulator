from tools.algorithm_adapter_release_gate import run


def test_release_gate_passes_only_with_explicit_pending_boundaries():
    result = run()
    assert result["status"] == "pass_with_pending_boundaries"
    assert result["cross_algorithm_comparison"] == "pending_common_condition_and_receipt"
    assert result["errors"] == []
