import json
from pathlib import Path

import pytest

# These integration checks belong to the optional upstream task package.
# The tool-only distribution deliberately does not ship that implementation.
pytest.importorskip("demo.benchmark.branch_plugins", reason="optional upstream scene plugins are not installed")

from demo.benchmark.branch_plugins import (
    ModernHouseMeasuredAdapter,
    ModernHouseTaskAdapter,
    WarehouseTaskAdapter,
)
from tools.benchmark_branch import BranchPlan


ROOT = Path(__file__).resolve().parents[3]


def _plan(adapter, *, slots=1):
    return BranchPlan(
        scene_key=adapter.scene_key,
        task_id=adapter.task_id,
        system_id="test-system",
        task_version=adapter.task_version,
        slots=slots,
        execution_scope="protocol_synthetic",
    )


def test_modern_house_contract_freezes_phase_graph_and_checkpoint():
    adapter = ModernHouseTaskAdapter()
    plan = _plan(adapter)
    report = adapter.validate_contract(plan)
    assert report["passed"] is True
    assert report["phase_count"] == 11
    assert report["task_contract_digest"] == adapter.task_contract.digest
    assert adapter.task_id == "MH-DAG-01"
    checkpoint = adapter.create_checkpoint(plan)
    assert checkpoint.digest == adapter.create_checkpoint(plan).digest
    assert checkpoint.metadata["domain_evidence_status"] == "pending"
    assert checkpoint.state["phase_graph"]["nodes"][3]["id"] == "coordinated_furniture_reposition"


def test_warehouse_contract_preserves_joint_and_dock_semantics():
    adapter = WarehouseTaskAdapter()
    plan = _plan(adapter)
    report = adapter.validate_contract(plan)
    assert report["passed"] is True
    contract = report["task_contract"]
    ids = [node["id"] for node in contract["dag"]]
    assert ids == [node["id"] for node in json.loads(
        (ROOT / "demo/warehouse_benchmark/scenarios/business_logistics_01.json").read_text()
    )["phase_graph"]["nodes"]]
    assert "joint_transport" in contract["capability_requirements"]
    assert "priority_carton_dispatch" in ids
    assert contract["feasibility_oracle"]["status"] == "feasibility_unverified"


def test_plugin_step_keeps_protocol_completion_separate_from_physical_outcome():
    adapter = ModernHouseTaskAdapter(step_delay_s=0)
    plan = _plan(adapter)
    job = plan.jobs()[0]
    checkpoint = adapter.create_checkpoint(plan)
    runtime = adapter.prepare_base_instance(job, checkpoint)
    restored = adapter.restore_checkpoint(runtime, checkpoint)
    assert adapter.validate_restore(runtime, checkpoint, restored)["passed"] is True
    adapter.apply_treatment(runtime, job)
    while True:
        state = adapter.step(runtime, job)
        adapter.collect_events(runtime, job)
        if state["done"]:
            break
    outcome = adapter.finish_episode(runtime, job)
    assert outcome["status"] == "not_evaluable"
    assert outcome["physical_success"] is None
    assert outcome["evidence_refs"] == []
    assert len(outcome["protocol_completed_phase_ids"]) == 11


def test_measured_bridge_freezes_same_contract_without_starting_isaac():
    adapter = ModernHouseMeasuredAdapter(runtime_steps=2, artifact_root="tmp/test-measured-reports")
    plan = BranchPlan(
        scene_key=adapter.scene_key,
        task_id=adapter.task_id,
        system_id="isaac-runtime",
        task_version=adapter.task_version,
        slots=1,
        execution_scope="measured",
    )
    report = adapter.validate_contract(plan)
    assert report["passed"] is True
    assert report["external_evaluator"] == "pending"
