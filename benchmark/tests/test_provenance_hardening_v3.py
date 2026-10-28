import pytest

from benchmark.metrics_v3 import MetricV3Error, ScoreResultV3
from benchmark.release_v3 import EvidenceBundleV3, ReleaseV3Error
from benchmark.scorebook_v3 import default_scorebook_v3
from benchmark.statistical_evidence_v3 import StatisticalEvidenceV3


def test_score_result_rejects_nonfinite_and_out_of_range_points():
    with pytest.raises(MetricV3Error, match="finite"):
        ScoreResultV3("D1", "valid", float("nan"))
    with pytest.raises(MetricV3Error, match=r"\[0,100\]"):
        ScoreResultV3("D1", "valid", 100.01)


def test_release_bundle_rejects_stale_scorebook_before_gate_assessment():
    results = tuple(ScoreResultV3(item, "valid", 50.0) for item in ("D1", "D2", "D3", "D4", "D5", "D6"))
    with pytest.raises(ReleaseV3Error, match="scorebook is stale"):
        EvidenceBundleV3("bundle.v3", "protocol.v3", "stale", "composition", (), results, {}, "commit")


def test_statistical_evidence_rejects_stale_scorebook():
    reports = tuple({"score_id": item, "paper_commit": "0b006d4efcd25d5160d4b0a6e75cddd9e5265e44", "scorebook_digest": "stale", "input_digest": "input", "scheduled_runs": 1, "status": "na"} for item in ("D1", "D2", "D3", "D4", "D5", "D6"))
    with pytest.raises(ValueError, match="scorebook is stale"):
        StatisticalEvidenceV3("commit", "stale", reports)
    assert default_scorebook_v3().digest() != "stale"
