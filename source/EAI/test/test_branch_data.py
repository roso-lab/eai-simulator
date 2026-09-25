"""Pure Python coverage for the cross-scene branch data tool."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from demo.benchmark.branch_data import (
    LEDGER_FILES,
    REQUIRED_LEDGER_FIELDS,
    BranchIdentity,
    digest_state,
    open_batch,
    resume_batch,
    validate_run,
)


def _identity(slot: int, *, scope: str = "measured") -> BranchIdentity:
    return BranchIdentity(
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        base_instance_id="warehouse-instance-0",
        condition_id="baseline",
        pair_seed=17,
        pair_group_id="warehouse-instance-0:baseline:17",
        branch_id=f"warehouse-instance-0:baseline:slot-{slot}",
        branch_type="fact",
        slot=slot,
        checkpoint_digest="a" * 64,
        task_version="warehouse.task.v1",
        source_revision="test-revision",
        execution_scope=scope,
    )


def _record_complete_branch(batch, identity: BranchIdentity, *, step: int = 1) -> None:
    batch.register_branch(identity)
    batch.record_step(
        identity.branch_id,
        simulator_step=step,
        sim_time_s=step * 0.02,
        commands={"m20": [0.1, 0.0, 0.0]},
        post_step_state={"position": [1.0, 2.0, 0.0]},
        phase_state={"phase": "closeout"},
        task_state={"completed_nodes": ["dock_confirmation_hold"]},
        active=True,
        completed=False,
        scene_payload={"target_error_m": 0.01},
    )
    batch.record_event(
        identity.branch_id,
        event_type="task_complete",
        simulator_step=step,
        sim_time_s=step * 0.02,
        payload={"node": "aisle_closeout"},
    )
    batch.record_progress(
        identity.branch_id,
        simulator_step=step,
        sim_time_s=step * 0.02,
        progress={"completed": 1, "total": 1, "q_t": 1.0},
    )
    batch.record_network(
        identity.branch_id,
        simulator_step=step,
        sim_time_s=step * 0.02,
        network={"offered_bits": 0, "delivered_bits": 0},
    )
    batch.record_cost(
        identity.branch_id,
        simulator_step=step,
        sim_time_s=step * 0.02,
        cost={"control_steps": step, "wall_s": 0.1},
    )
    batch.record_checkpoint(
        identity.branch_id,
        checkpoint_id="dock_confirmation_complete",
        simulator_step=step,
        snapshot_digest="b" * 64,
        snapshot_schema="warehouse.public_state.v1",
        restore_error=1e-7,
        restore_passed=True,
        restore_status="measured",
        restore_error_source="max_abs_public_tensor_error",
    )
    batch.finish_branch(
        identity.branch_id,
        completed=True,
        independently_verified=True,
        simulator_step=step,
        sim_time_s=step * 0.02,
        outcome={"target_error_m": 0.01},
    )


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_checkpoint_digest_preserves_array_shape_and_dtype() -> None:
    baseline = digest_state({"physics": np.zeros((2, 3), dtype=np.float32)})

    assert baseline == digest_state({"physics": np.zeros((2, 3), dtype=np.float32)})
    assert baseline != digest_state({"physics": np.zeros((3, 2), dtype=np.float32)})
    assert baseline != digest_state({"physics": np.zeros((2, 3), dtype=np.float64)})


def test_open_record_close_and_validate_small_batch(tmp_path: Path) -> None:
    output = tmp_path / "warehouse-small"
    batch = open_batch(
        output,
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        requested=2,
        execution_scope="measured",
        task_version="warehouse.task.v1",
        source_revision_value="test-revision",
    )
    for slot in range(2):
        _record_complete_branch(batch, _identity(slot), step=slot + 1)
    batch.close_batch(status="completed", resident=2, peak_active=2, queued=0)

    result = validate_run(output)

    assert result["status"] == "passed"
    assert result["metrics"]["requested"] == 2
    assert result["metrics"]["completed"] == 2
    assert result["metrics"]["checkpoint_restore_error"] == 1e-7
    assert result["warnings"] == [
        "small-scale run is not evidence of formal 32-branch capacity"]
    for filename in ("manifest.json", "run_state.json", "validation.json", *LEDGER_FILES.values()):
        assert (output / filename).is_file()
    for ledger, filename in LEDGER_FILES.items():
        rows = _rows(output / filename)
        assert rows, ledger
        assert all(set(REQUIRED_LEDGER_FIELDS) <= set(row) for row in rows)


def test_resume_appends_without_changing_identity_or_old_rows(tmp_path: Path) -> None:
    output = tmp_path / "resume"
    batch = open_batch(
        output,
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        requested=2,
        execution_scope="measured",
        task_version="warehouse.task.v1",
        source_revision_value="test-revision",
        autosave_every=1,
    )
    first = _identity(0)
    batch.register_branch(first)
    batch.record_event(
        first.branch_id,
        event_type="interrupted",
        simulator_step=3,
        sim_time_s=0.06,
    )
    before = (output / "events.jsonl").read_bytes()
    batch.close_batch(status="interrupted", resident=1, peak_active=1, queued=1)

    resumed = resume_batch(output, autosave_every=1)
    assert resumed.register_branch(first) == first
    with pytest.raises(ValueError, match="identity mismatch"):
        resumed.register_branch(BranchIdentity(**(first.row() | {"condition_id": "changed"})))
    second = _identity(1)
    resumed.register_branch(second)
    resumed.record_event(
        second.branch_id,
        event_type="resumed",
        simulator_step=4,
        sim_time_s=0.08,
    )
    resumed.close_batch(status="completed", resident=2, peak_active=2, queued=0)

    assert (output / "events.jsonl").read_bytes().startswith(before)
    state = json.loads((output / "run_state.json").read_text(encoding="utf-8"))
    assert state["resume_count"] == 1
    assert state["registered"] == 2
    assert len(_rows(output / "branch_manifest.jsonl")) == 2


def test_checkpoint_requires_honest_restore_measurement(tmp_path: Path) -> None:
    batch = open_batch(
        tmp_path / "checkpoint",
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        requested=1,
        execution_scope="measured",
        task_version="warehouse.task.v1",
        source_revision_value="test-revision",
    )
    identity = batch.register_branch(_identity(0))
    pending = batch.record_checkpoint(
        identity.branch_id,
        checkpoint_id="precondition",
        simulator_step=0,
        snapshot_digest="b" * 64,
        snapshot_schema="warehouse.public_state.v1",
        restore_error=None,
        restore_passed=None,
        restore_status="pending",
        restore_error_source="not_measured",
    )
    assert pending["restore_error"] is None
    assert pending["sim_step"] == pending["simulator_step"] == 0
    with pytest.raises(ValueError, match="cannot pass"):
        batch.record_checkpoint(
            identity.branch_id,
            checkpoint_id="invalid",
            simulator_step=0,
            snapshot_digest="b" * 64,
            snapshot_schema="warehouse.public_state.v1",
            restore_error=None,
            restore_passed=True,
            restore_status="pending",
            restore_error_source="not_measured",
        )


def test_formal_batch_requires_32_and_complete_per_branch_ledgers(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly 32"):
        open_batch(
            tmp_path / "invalid-formal",
            scene_key="warehouse_nvd",
            task_id="W-DAG-01",
            requested=4,
            execution_scope="measured",
            task_version="warehouse.task.v1",
            source_revision_value="test-revision",
            formal=True,
        )

    output = tmp_path / "formal"
    batch = open_batch(
        output,
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        requested=32,
        execution_scope="measured",
        task_version="warehouse.task.v1",
        source_revision_value="test-revision",
        formal=True,
        autosave_every=1024,
    )
    for slot in range(32):
        _record_complete_branch(batch, _identity(slot), step=1)
    batch.close_batch(status="completed", resident=32, peak_active=32, queued=0)

    result = validate_run(output)

    assert result["status"] == "passed"
    assert result["metrics"] == {
        "requested": 32,
        "resident": 32,
        "peak_active": 32,
        "completed": 32,
        "independently_verified": 32,
        "queued": 0,
        "checkpoint_restore_error": 1e-7,
        "episode_count": 32,
        "step_count": 32,
        "event_count": 32,
        "network_count": 32,
        "cost_count": 32,
        "checkpoint_count": 32,
        "validation_status": "passed",
    }


def test_formal_protocol_synthetic_run_cannot_pass(tmp_path: Path) -> None:
    output = tmp_path / "synthetic-formal"
    batch = open_batch(
        output,
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        requested=32,
        execution_scope="protocol_synthetic",
        task_version="warehouse.task.v1",
        source_revision_value="test-revision",
        formal=True,
    )
    batch.close_batch(
        status="completed", resident=32, peak_active=32, queued=0)

    result = validate_run(output)

    assert result["status"] == "failed"
    assert "formal batch execution_scope must be measured" in result["errors"]


def test_fire_adapter_records_measured_restore_and_shared_identity(
    tmp_path: Path,
) -> None:
    from demo.fire_rescue.branch_data_adapter import FireBranchDataAdapter

    assignment = tmp_path / "assignment.json"
    assignment.write_text(
        json.dumps({"assignments": {"m20_2": {"colour": "yellow"}}}),
        encoding="utf-8",
    )
    sim = SimpleNamespace(
        device="cpu",
        base_env=SimpleNamespace(step_dt=0.02),
    )
    adapter = FireBranchDataAdapter.open(
        sim=sim,
        output=tmp_path / "fire-data",
        checkpoint_id="extinguisher_carried",
        assignments=[assignment],
        fingerprint={"seed": 17, "hazard": 2},
        started_monotonic=1.0,
    )
    validation = {
        "passed": True,
        "readback_digest": "c" * 64,
        "arrays": {
            "/physics/root_transforms": {
                "max_abs_error": 2.5e-7,
                "threshold": 1e-5,
                "passed": True,
            }
        },
    }
    audit = SimpleNamespace(
        checkpoint_restore_validation=validation,
        restore_history=[validation, validation],
    )
    scheduler = SimpleNamespace(
        _async_last_logical={
            0: {"checkpoints": {"extinguisher_carried": True}}
        }
    )
    checkpoint = {"schema": 2, "step": 1259, "physics": np.zeros(3)}

    adapter.register_checkpoint({0: audit}, checkpoint, scheduler)
    identity = next(iter(adapter.batch.branches.values()))
    adapter.batch.record_step(
        identity.branch_id,
        simulator_step=1260,
        sim_time_s=25.2,
        commands={"m20_2": [0.1, 0.0, 0.0]},
        post_step_state={"m20_2": [0.0] * 13},
        phase_state={"result": "success"},
        task_state={"checkpoints": {"mission_success": True}},
        active=False,
        completed=True,
        scene_payload={"hazard": {"id": 2}},
    )
    result = adapter.finish({
        "physics_step_count": 1260,
        "batched_physics_slots": 1,
        "public_peak_active_slots": 1,
        "slot_isolation_verified": True,
        "full_task_completed_by_slot": {"0": True},
        "slots": {0: {"result": "success"}},
        "branch_reports": {0: {"branch_steps": 1}},
    }, wall_s=0.5)

    assert result["status"] == "passed"
    checkpoint_row = _rows(adapter.output / "checkpoints.jsonl")[0]
    assert checkpoint_row["restore_error"] == 2.5e-7
    assert checkpoint_row["restore_status"] == "measured"
    assert checkpoint_row["restore_passed"] is True
    assert checkpoint_row["scene_key"] == "factory"
    assert checkpoint_row["task_id"] == "fire_rescue"


def test_fire_adapter_does_not_invent_unmeasured_restore_error() -> None:
    from demo.fire_rescue.branch_data_adapter import FireBranchDataAdapter

    assert FireBranchDataAdapter._restore_measurement({
        "passed": True,
        "arrays": {},
    }) == (None, "unavailable")


def test_warehouse_resume_cli_flags_are_explicit(tmp_path: Path) -> None:
    from demo.warehouse_benchmark.checkpoint_branches import (
        _is_planned_interruption,
        build_parser,
    )

    args = build_parser().parse_args([
        "--static-audit", str(tmp_path / "audit.json"),
        "--output", str(tmp_path / "run"),
        "--slots", "1",
        "--resume-batch",
        "--stop-after-branch-registration",
    ])
    assert args.resume_batch is True
    assert args.stop_after_branch_registration is True
    report = {"stage": "interrupted_at_branch_checkpoint"}
    state = {"status": "interrupted"}
    assert _is_planned_interruption(0, report, state)
    assert _is_planned_interruption(2, report, state)
    assert not _is_planned_interruption(1, report, state)
    assert not _is_planned_interruption(0, {"stage": "failed"}, state)
    assert not _is_planned_interruption(0, report, {"status": "open"})
