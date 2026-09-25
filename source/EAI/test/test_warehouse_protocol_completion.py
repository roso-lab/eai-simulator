"""Offline checks for the Warehouse six-dimension protocol pilot."""

from __future__ import annotations

import json
from pathlib import Path

from demo.warehouse_benchmark.protocol_completion import (
    DEFAULT_INFRA_REPORT,
    DEFAULT_SCENARIO,
    DIMENSIONS,
    PROTOCOL_SHA256,
    _condition_matrix,
    file_sha256,
    freeze_task_contract,
    load_scenario,
    run_pilot,
    validate_infrastructure_report,
)
from demo.benchmark.protocol_runner import GenericJsonSceneAdapter


REQUIRED_KEYS = {
    "system_id", "base_instance_id", "condition_id", "pair_seed", "branch_id",
    "branch_type", "round_index", "checkpoint_digest", "task_version",
    "source_revision",
}


def _rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_contract_freezes_protocol_and_unique_milestones() -> None:
    scenario = load_scenario(DEFAULT_SCENARIO)
    contract = freeze_task_contract(scenario, scenario_sha256=file_sha256(DEFAULT_SCENARIO))
    assert contract["protocol_sha256"] == PROTOCOL_SHA256
    assert contract["scenario_sha256"] == file_sha256(DEFAULT_SCENARIO)
    milestones = contract["progress_milestones"]
    assert len({row["id"] for row in milestones}) == len(milestones)
    assert {row["kind"] for row in contract["phase_graph"]["relations"]} >= {
        "precedence", "synchronization", "mutual_exclusion", "handoff",
    }
    assert contract["success_predicate"]["external_evidence_status"] == "pending"


def test_condition_matrix_keeps_communication_families_separate() -> None:
    conditions = _condition_matrix()
    communication = [row for row in conditions if row["dimension"] == "communication_adaptation"]
    assert {row["family"] for row in communication} == {
        "perfect", "zero", "fixed_delay", "iid_loss", "capacity", "jitter", "burst_loss",
    }
    assert not any("delay_steps" in row and "loss" in row for row in communication)
    assert [row["delay_steps"] for row in communication if row["family"] == "fixed_delay"] == [0, 1, 2, 4, 8]


def test_pilot_writes_all_ledgers_and_scorecard_boundary(tmp_path: Path) -> None:
    output = run_pilot(
        output_root=tmp_path / "protocol_completion",
        channel_seeds=1,
        learning_rounds=2,
    )
    for relative in (
        "contracts/warehouse_task_contract.json", "instances.jsonl", "episodes.jsonl",
        "events.jsonl", "progress.jsonl", "network.jsonl", "costs.jsonl",
        "checkpoints.jsonl", "metrics.json", "diagnostics.json", "scorecard.json",
        "run_state.json", "experiment_manifest.json", "protocol_gap_table.json",
        "pilot_report.json",
    ):
        assert (output / relative).is_file(), relative
    for relative in ("episodes.jsonl", "events.jsonl", "progress.jsonl", "network.jsonl", "costs.jsonl", "checkpoints.jsonl", "assignment.jsonl"):
        rows = _rows(output / relative)
        assert rows
        assert REQUIRED_KEYS <= set(rows[0]), relative
        assert {row["branch_type"] for row in rows} <= {"fact", "reference", "sham", "intervention"}
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert set(DIMENSIONS) <= set(metrics["metrics"])
    assert metrics["metrics"]["task_allocation"]["VAR"]["raw"] == 1.0
    scorecard = json.loads((output / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["score_model"]["total_score"] is None
    assert scorecard["physical_gate"]["scoreable"] is False
    pilot_report = json.loads((output / "pilot_report.json").read_text(encoding="utf-8"))
    assert pilot_report["execution_scope"] == "protocol_synthetic"
    learning = metrics["metrics"]["cross_episode_learning"]
    assert learning["experience_mechanism"] == "unsupported_public_greedy_no_writable_memory"
    assert learning["DeltaRec"]["status"] == "no_gain_unsupported_experience"


def test_learning_first_round_is_shared_and_infrastructure_is_separate(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verified_infrastructure.json"
    report_path.write_text(json.dumps({
        "requested": 32,
        "resident": 32,
        "peak_active": 32,
        "completed": 32,
        "independently_verified": 32,
        "queued": 0,
        "restore": {"max_abs_error": 1e-7, "passed": True},
        "transport_scope": "fixture",
        "sensor_scope": "fixture",
        "full_task_completed": True,
    }), encoding="utf-8")

    report = validate_infrastructure_report(report_path)
    assert report["status"] == "verified"
    assert report["fields"]["requested"] == 32
    assert report["fields"]["resident"] == 32
    assert report["fields"]["queued"] == 0
    assert report["checkpoint_restore_error"] <= 7.63e-6


def test_missing_restore_measurement_stays_null(tmp_path: Path) -> None:
    report_path = tmp_path / "infrastructure.json"
    report_path.write_text(json.dumps({
        "requested": 32,
        "resident": 32,
        "peak_active": 32,
        "completed": 32,
        "independently_verified": 32,
        "queued": 0,
        "restore": {"passed": True},
    }), encoding="utf-8")

    report = validate_infrastructure_report(report_path)

    assert report["status"] == "failed"
    assert report["checkpoint_restore_error"] is None


def test_generic_protocol_missing_restore_measurement_is_not_infinite(tmp_path: Path) -> None:
    report_path = tmp_path / "infrastructure.json"
    report_path.write_text(json.dumps({
        "requested": 32,
        "resident": 32,
        "peak_active": 32,
        "completed": 32,
        "independently_verified": 32,
        "queued": 0,
        "restore": {},
    }), encoding="utf-8")

    evidence = GenericJsonSceneAdapter(
        infrastructure_report=report_path,
    ).infrastructure_evidence()

    assert evidence["status"] == "failed"
    assert evidence["checkpoint_restore_error"] is None
    assert evidence["checkpoint_restore_status"] == "unavailable"
    assert evidence["restore_error_source"] == "infrastructure_restore_not_measured"
