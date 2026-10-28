from pathlib import Path

from tools.benchmark_branch.cp2_replay import replay_cp2_catalog


ROOT = Path(__file__).resolve().parents[3]


def test_cp2_replay_recomputes_v2_difficulty_without_certifying_runtime():
    ledger = replay_cp2_catalog(ROOT / "benchmark/task_catalog.json")
    assert ledger["contract_version"] == "C-P2-02-quarantine-marked-v2"
    assert ledger["common_anchor_id"] == "C-P2-02"
    assert ledger["oracle_source_commits"] == ["cf839ec8", "0b6890c0"]
    assert {row["anchor_id"] for row in ledger["anchors"]} == {"prod-p2-02", "svc-p2-02", "liv-p2-02"}
    assert all("quarantine_marked" in row["milestones"] for row in ledger["anchors"])
    assert ledger["difficulty_recomputed"] is True
    assert ledger["oracle_witness_replayed"] is True
    assert ledger["oracle_verdicts_sufficient"] is True
    assert ledger["oracle_replay_digest"]
    assert all(row["oracle_replay_status"] == "executed_offline" for row in ledger["anchors"])
    assert all(all(status == "satisfied" for status in row["predicate_statuses"])
               for row in ledger["anchors"])
    assert ledger["deployment_oracle_replayed"] is False
    assert ledger["skill_gate_passed"] is False


def test_cp2_replay_remains_fail_closed_for_an_incomplete_witness(tmp_path):
    witness = tmp_path / "missing-witness.json"
    witness.write_text(
        '{"schema_version":"c-p2-02-oracle-witness.v1",'
        '"integrity_confirmed":false,"milestones":[],"zone_intervals":[],'
        '"hold_intervals":[],"task_events":[]}'
    )
    ledger = replay_cp2_catalog(ROOT / "benchmark/task_catalog.json", witness)
    assert ledger["oracle_witness_replayed"] is True
    assert ledger["oracle_verdicts_sufficient"] is False
    assert all(status == "data_insufficient"
               for row in ledger["anchors"] for status in row["predicate_statuses"])

def test_cp2_replay_rejects_tampered_confirmed_witness(tmp_path):
    witness = tmp_path / "tampered.json"
    witness.write_text('''{"schema_version":"c-p2-02-oracle-witness.v1","integrity_confirmed":true,
      "milestones":[{"label":"inspection_complete","tick":10},{"label":"quarantine_marked","tick":12},{"label":"released","tick":0},{"label":"accepted","tick":25}],
      "zone_intervals":[{"zone_id":"quarantine_bay","holder_id":"package_a","enter_tick":12,"exit_tick":20}],"hold_intervals":[],"task_events":[]}''')
    import pytest
    with pytest.raises(ValueError, match="integrity digest"):
        replay_cp2_catalog(ROOT / "benchmark/task_catalog.json", witness)
