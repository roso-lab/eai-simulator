"""Cross-scene contract tests for the portable protocol runner."""

from __future__ import annotations

import json
from pathlib import Path

from demo.benchmark.protocol_runner import (
    DIMENSIONS,
    GenericJsonSceneAdapter,
    ProtocolParticipant,
    PublicGreedyParticipant,
    REQUIRED_RECORD_KEYS,
    run_pilot,
)


SCENARIO = Path("demo/modern_house_benchmark/scenarios/home_recovery_01.json")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_generic_adapter_loads_modern_house_without_warehouse_contract() -> None:
    adapter = GenericJsonSceneAdapter()
    scenario = adapter.load_scenario(SCENARIO)
    contract = adapter.build_contract(
        scenario,
        scenario_sha256="scenario",
        protocol_sha256="protocol",
        horizon_s=120.0,
    )

    assert adapter.scene_key == "modern_house_nvd"
    serialized = json.dumps(contract, sort_keys=True)
    assert "warehouse_nvd" not in serialized
    assert "business_logistics" not in serialized
    assert {node["id"] for node in contract["phase_graph"]["nodes"]} == {
        node["id"] for node in scenario["phase_graph"]["nodes"]
    }


def test_generic_runner_writes_portable_ledgers_and_pending_score(tmp_path: Path) -> None:
    output = run_pilot(
        adapter=GenericJsonSceneAdapter(),
        scenario_path=SCENARIO,
        output_root=tmp_path / "modern_house_protocol",
        instance_count=1,
        channel_seeds=1,
        learning_rounds=2,
    )

    expected_files = {
        "contracts/task_contract.json",
        "contracts/contract_manifest.json",
        "instances.jsonl",
        "episodes.jsonl",
        "events.jsonl",
        "progress.jsonl",
        "network.jsonl",
        "costs.jsonl",
        "checkpoints.jsonl",
        "assignment.jsonl",
        "metrics.json",
        "diagnostics.json",
        "experiment_manifest.json",
        "scorecard.json",
        "protocol_gap_table.json",
        "run_state.json",
        "pilot_report.json",
    }
    for relative in expected_files:
        assert (output / relative).is_file(), relative

    for name in ("episodes", "events", "progress", "network", "costs", "checkpoints", "assignment"):
        rows = _rows(output / f"{name}.jsonl")
        assert rows
        assert all(REQUIRED_RECORD_KEYS <= set(row) for row in rows)
        assert {row["branch_type"] for row in rows} <= {"fact", "reference", "sham", "intervention"}

    scorecard = json.loads((output / "scorecard.json").read_text(encoding="utf-8"))
    assert scorecard["scene_key"] == "modern_house_nvd"
    assert scorecard["score_model"]["total_score"] is None
    assert scorecard["physical_gate"]["scoreable"] is False
    assert set(scorecard["dimensions"]) == set(DIMENSIONS)

    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))["metrics"]
    assert metrics["cross_episode_learning"]["experience_mechanism"] == "unsupported_public_greedy_no_writable_memory"
    assert metrics["cross_episode_learning"]["DeltaRec"]["status"] == "no_gain_unsupported_experience"


class _TestParticipant:
    participant_id = "test_participant"
    experience_support = "test_writable_memory"

    def assign(self, scenario, instance, nodes):
        if instance["instance_label"] != "feasible":
            return {"output": "infeasible", "assignments": {}}
        return {
            "output": "complete",
            "assignments": {node["id"]: [node.get("preferred_agent")] for node in nodes},
        }


def test_participant_boundary_is_replaceable(tmp_path: Path) -> None:
    participant: ProtocolParticipant = _TestParticipant()
    output = run_pilot(
        adapter=GenericJsonSceneAdapter(),
        scenario_path=SCENARIO,
        output_root=tmp_path / "custom_participant",
        participant=participant,
        instance_count=1,
        channel_seeds=1,
        learning_rounds=2,
    )
    manifest = json.loads((output / "experiment_manifest.json").read_text(encoding="utf-8"))
    assert manifest["participant"] == "test_participant"
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))["metrics"]
    assert metrics["cross_episode_learning"]["experience_mechanism"] == "test_writable_memory"


def test_public_greedy_declares_no_cross_episode_memory() -> None:
    assert PublicGreedyParticipant.experience_support == "unsupported_public_greedy_no_writable_memory"
