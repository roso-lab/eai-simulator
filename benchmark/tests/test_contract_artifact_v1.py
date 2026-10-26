from pathlib import Path

from benchmark.contract_artifact_v1 import load_canonical_contract_artifact


def test_canonical_contract_artifact_is_single_31_anchor_projection():
    root = Path(__file__).resolve().parents[2]
    result = load_canonical_contract_artifact(project_root=root)
    assert result.anchor_count == 31
    assert result.baseline_anchor_count == 30
    assert result.extension_anchor_ids == ("liv-t-04",)
    assert result.artifact_path.name == "pilot_contracts-v3.json"
    assert result.payload["certification"] == {
        "case_certified": False,
        "paper_score_eligible": False,
        "formal_qualification_gates": False,
    }
