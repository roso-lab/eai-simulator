from dataclasses import asdict, replace
import json
import pytest
from benchmark.relations_v3 import RelationRunV3, d3_relation_report, relation_run_from_obligations
from benchmark.records_v3 import IdentityV3, ObligationOutcomeV3, RecordStatus


def rows():
    return [RelationRunV3(str(i), "production", "L1", "nominal", str(s), values, "evidence", primary_label="P1") for i,values in enumerate(({"P2": 1, "P3J": 1}, {"P3S": 0, "P4": 1}, {"P2": 0, "P3S": 1})) for s in range(3)]


def registered():
    return {key: [("production", "L1", "nominal")] for key in ("P2", "P3J", "P3S", "P4")}


def test_relation_partition_must_be_registered_and_primary_label_can_be_a_stratum():
    assert d3_relation_report(rows(), resamples=100)["status"] == "incomplete"
    original = d3_relation_report(rows(), partition_mode="domain_difficulty_condition", registered_cells=registered(), minimum_instances=1, resamples=100)
    changed = d3_relation_report([replace(r, primary_label="P4") for r in rows()], partition_mode="domain_difficulty_condition", registered_cells=registered(), minimum_instances=1, resamples=100)
    assert original["identification"] == changed["identification"]
    assert original["identification"][0] == pytest.approx(100*(.5/3+1/6+.5/6+1/3))
    assert original["empty_relation_resample_fraction"] > 0
    assert original["status"] == "incomplete"
    split = d3_relation_report(rows(), partition_mode="domain_primary_difficulty_condition", registered_cells={key: [("production", "P1", "L1", "nominal")] for key in ("P2", "P3J", "P3S", "P4")}, minimum_instances=1, resamples=100)
    assert split["relation_partition"] == "domain_primary_difficulty_condition"
    with pytest.raises(ValueError, match="primary-label"):
        d3_relation_report([replace(r, primary_label="") for r in rows()], partition_mode="domain_primary_difficulty_condition", registered_cells=registered(), minimum_instances=1, resamples=100)


def test_relation_unknown_changes_only_its_fixed_contribution():
    source = rows()
    source[0] = replace(source[0], values={"P2": None, "P3J": 1})
    result = d3_relation_report(source, partition_mode="domain_difficulty_condition", registered_cells=registered(), minimum_instances=1, resamples=100)
    assert result["status"] == "incomplete"
    assert result["reason"] == "empty_relation_support_in_bootstrap"
    with pytest.raises(ValueError, match="membership"):
        d3_relation_report(source[:-1], partition_mode="domain_difficulty_condition", registered_cells=registered(), minimum_instances=1, resamples=100)


def test_obligation_ledger_to_relation_run_preserves_dispositions_and_roundtrip():
    identity = IdentityV3(
        "v3", "score", "catalog", "design", "task", "anchor", "production",
        ("P2", "P4"), "L1", "nominal", "system", "instance", "1", "fact", "checkpoint")
    obligations = (
        ObligationOutcomeV3(identity, obligation_id="handoff", relation="P2", participants=("a", "b"),
                            opportunity=True, violated=True, evidence_digests=("event-ledger",)),
        ObligationOutcomeV3(identity, obligation_id="lock", relation="P4", participants=("b",),
                            opportunity=True),
    )
    run = relation_run_from_obligations(obligations)
    assert run.values == {"P2": 0., "P4": 0.}
    assert run.obligation_dispositions == {"P2": ("violated",), "P4": ("unreached",)}
    restored = RelationRunV3(**json.loads(json.dumps(asdict(run))))
    assert restored.obligation_dispositions == run.obligation_dispositions
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        replace(run, obligation_ledger_digest="not-a-digest")
    with pytest.raises(ValueError, match="canonical records"):
        replace(run, obligation_ledger_digest="0" * 64)
    with pytest.raises(ValueError, match="derived from ledger"):
        replace(run, obligation_dispositions={"P2": ("unreached",), "P4": ("unreached",)})
    with pytest.raises(ValueError, match="values are not derived"):
        replace(run, values={"P2": 1., "P4": 0.})

    unknown = ObligationOutcomeV3(
        identity, status=RecordStatus.UNKNOWN, invalid_reason="missing evaluator event",
        obligation_id="handoff", relation="P2", participants=("a", "b"), opportunity=True)
    unresolved = relation_run_from_obligations((unknown, obligations[1]))
    assert unresolved.values["P2"] is None
    assert unresolved.obligation_dispositions["P2"] == ("unknown",)
