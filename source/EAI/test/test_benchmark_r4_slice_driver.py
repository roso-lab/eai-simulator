"""Lightweight checks for the pre-registered R4 minimal slice driver.

These tests exercise only the simulator-independent layers of
``demo.benchmark.goal_r4_slice_driver``: the frozen arm plan, the
pre-registered threshold table, the criteria evaluator, the RNG/buffer
comparison helpers, and the branch-data ledger encoding conventions
(reference arm vs. untreated sham arms).  They do not start Isaac Sim.
"""

from __future__ import annotations

import numpy as np
import pytest

from demo.benchmark.branch_data import open_batch, validate_run
from demo.benchmark.goal_r4_slice_driver import (
    R4_CONDITION_ID,
    build_arm_plan,
    evaluate_criteria,
    preregistered_thresholds,
    r4_branch_identity,
    record_real_restore_row,
    record_sham_row,
    record_snapshot_row,
)
from demo.benchmark.goal_r4_slice_driver import (
    _numeric_drift,
    _rng_streams_equal,
)


SCENARIO = {
    "scene_key": "warehouse_nvd",
    "benchmark_task_id": "W-DAG-01",
    "version": 2,
    "seed": 17,
}


def _arm_results(
    *,
    restore_error=1e-9,
    sham_drifts=(0.12, 0.11),
    displacements=(0.15, 0.12, 0.11),
    rng_streams=None,
    buffers_error=0.0,
):
    if rng_streams is None:
        rng_streams = {"python": True, "numpy": True, "torch": True, "cuda": True}
    return {
        "R": {
            "branch_type": "reference",
            "restore_error": restore_error,
            "displacement_m": displacements[0],
            "rng_streams_equal": rng_streams,
            "buffers_max_abs_error": buffers_error,
        },
        "S1": {
            "branch_type": "sham",
            "readback_max_abs_error": sham_drifts[0],
            "displacement_m": displacements[1],
        },
        "S2": {
            "branch_type": "sham",
            "readback_max_abs_error": sham_drifts[1],
            "displacement_m": displacements[2],
        },
    }


def test_arm_plan_is_three_sequential_treatment_arms():
    plan = build_arm_plan()
    assert [arm.arm_id for arm in plan] == ["R", "S1", "S2"]
    assert [arm.slot for arm in plan] == [0, 1, 2]
    assert [arm.branch_type for arm in plan] == ["reference", "sham", "sham"]
    assert [arm.restore_treatment for arm in plan] == [True, False, False]
    assert plan[2].label.startswith(
        "change window + no restore + immediate readback, after an "
        "infrastructure restore")


def test_preregistered_thresholds_are_frozen_and_ordered():
    thresholds = preregistered_thresholds()
    assert thresholds["restore_tolerance"] == 1e-5
    assert thresholds["sham_min_drift"] == 1e-3
    assert thresholds["sham_ratio"] == 100.0
    assert thresholds["min_displacement_m"] == 0.01
    assert thresholds["sham_min_drift"] > thresholds["restore_tolerance"]
    with pytest.raises(ValueError):
        preregistered_thresholds(sham_min_drift=1e-6)
    with pytest.raises(ValueError):
        preregistered_thresholds(restore_tolerance=0.0)


def test_criteria_pass_on_a_well_separated_slice():
    thresholds = preregistered_thresholds()
    verdict = evaluate_criteria(_arm_results(), thresholds)
    assert verdict["passed"] is True
    for name, criterion in verdict["criteria"].items():
        assert criterion["passed"] is True, name
    sham_arms = verdict["criteria"]["CR-SHAM"]["arms"]
    assert [item["observed"] for item in sham_arms] == [0.12, 0.11]
    assert all(item["ratio_to_restore_error"] > 1e6 for item in sham_arms)


@pytest.mark.parametrize(
    "results",
    [
        _arm_results(restore_error=1e-4),
        _arm_results(sham_drifts=(9e-4, 0.11)),
        _arm_results(sham_drifts=(0.12, 9e-4)),
        _arm_results(displacements=(0.15, 0.001, 0.11)),
        _arm_results(rng_streams={"python": True, "numpy": False, "torch": True, "cuda": True}),
        _arm_results(buffers_error=1e-3),
    ],
)
def test_criteria_fail_on_each_violation(results):
    verdict = evaluate_criteria(results, preregistered_thresholds())
    assert verdict["passed"] is False


def test_criteria_require_every_arm_result():
    results = _arm_results()
    del results["S2"]
    with pytest.raises(ValueError):
        evaluate_criteria(results, preregistered_thresholds())


def test_sham_ratio_gate_uses_the_measured_restore_error():
    # Drift clears sham_min_drift but is only 10x the restore error.
    results = _arm_results(restore_error=1e-3, sham_drifts=(1e-2, 1e-2))
    verdict = evaluate_criteria(results, preregistered_thresholds())
    assert verdict["criteria"]["CR-SHAM"]["passed"] is False
    assert all(
        item["ratio_to_restore_error"] == pytest.approx(10.0)
        for item in verdict["criteria"]["CR-SHAM"]["arms"])


def test_numeric_drift_and_rng_comparison_helpers():
    current = {
        "counter": 7,
        "tensor_like": np.array([1.0, 2.0, 3.0]),
        "nested": {"inner": np.array([[0.5, 0.25]])},
        "text": "unchanged",
    }
    snapshot = {
        "counter": 5,
        "tensor_like": np.array([1.0, 2.0, 3.0]),
        "nested": {"inner": np.array([[0.5, 0.5]])},
        "text": "unchanged",
    }
    error, compared, skipped = _numeric_drift(current, snapshot)
    assert error == pytest.approx(2.0)  # counter drift dominates
    assert compared == 3
    assert skipped == 1
    assert _numeric_drift(snapshot, snapshot)[0] == 0.0

    rng_current = {
        "python": (3, (1, 2, 3), None),
        "numpy": ("MT19937", np.arange(624, dtype=np.uint32), 0, 0, 0.0),
        "torch": np.arange(8, dtype=np.uint8),
        "cuda": [np.arange(4, dtype=np.uint8)],
    }
    assert _rng_streams_equal(rng_current, rng_current) == {
        "python": True, "numpy": True, "torch": True, "cuda": True}
    drifted = dict(rng_current)
    drifted["numpy"] = ("MT19937", np.arange(624, dtype=np.uint32) + 1, 0, 0, 0.0)
    assert _rng_streams_equal(drifted, rng_current)["numpy"] is False


def test_ledger_encoding_of_restore_and_sham_arms(tmp_path):
    batch = open_batch(
        tmp_path / "branch_data",
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        requested=3,
        execution_scope="measured",
        task_version="warehouse.task.v2",
        source_revision_value="test-revision",
        formal=False,
        metadata={"condition": R4_CONDITION_ID},
        autosave_every=4,
    )
    snapshot_digest = "0" * 64
    identities = {}
    for arm in build_arm_plan():
        identity = r4_branch_identity(
            arm,
            SCENARIO,
            checkpoint_digest=snapshot_digest,
            source_revision_value="test-revision",
        )
        batch.register_branch(identity)
        identities[arm.arm_id] = identity
    assert len({identity.branch_id for identity in identities.values()}) == 3
    assert {identity.branch_type for identity in identities.values()} == {
        "reference", "sham"}
    assert all(identity.condition_id == R4_CONDITION_ID
               for identity in identities.values())

    real_branch = identities["R"].branch_id
    record_snapshot_row(
        batch, real_branch, simulator_step=40, snapshot_digest=snapshot_digest,
        payload={"checkpoint_store_step": 40})
    restore_row = record_real_restore_row(
        batch, real_branch, simulator_step=60, snapshot_digest=snapshot_digest,
        restore_error=2e-9, restore_tolerance=1e-5,
        payload={"buffers_max_abs_error": 0.0})
    assert restore_row["restore_status"] == "measured"
    assert restore_row["restore_passed"] is True
    assert restore_row["restore_error_source"] == "max_abs_public_physics_tensor_error"

    for arm_id, drift in (("S1", 0.13), ("S2", 0.12)):
        sham_row = record_sham_row(
            batch, identities[arm_id].branch_id, simulator_step=80,
            snapshot_digest=snapshot_digest, readback_error=drift,
            displacement_m=0.11, payload={})
        assert sham_row["restore_status"] == "unavailable"
        assert sham_row["restore_error"] is None
        assert sham_row["restore_passed"] is None
        assert sham_row["payload"]["readback_max_abs_error"] == drift
        assert sham_row["payload"]["change_window_displacement_m"] == 0.11

    for arm in build_arm_plan():
        batch.record_step(
            identities[arm.arm_id].branch_id,
            simulator_step=41,
            sim_time_s=0.82,
            commands={"carter_1": [0.25, 0.0, 0.0]},
            post_step_state={"carter_1_local_root": [0.0] * 13},
            phase_state={"phase": R4_CONDITION_ID, "arm_id": arm.arm_id},
            task_state={"arm_id": arm.arm_id},
            active=True,
            completed=False,
            scene_payload={"carter_world_xy": [0.0, 0.0]},
        )
        batch.record_event(
            identities[arm.arm_id].branch_id,
            event_type="sham_readback" if arm.branch_type == "sham" else "restore_applied",
            simulator_step=60,
            sim_time_s=1.2,
            payload={},
        )
        batch.finish_branch(
            identities[arm.arm_id].branch_id,
            completed=True,
            independently_verified=True,
            simulator_step=80,
            sim_time_s=1.6,
            outcome={"arm_id": arm.arm_id},
        )
    batch.close_batch(
        status="completed", resident=3, peak_active=1, queued=0, details={})

    result = validate_run(batch.output)
    assert result["status"] == "passed", result["errors"]
    assert result["counts"]["branches"] == 3
    assert result["counts"]["checkpoints"] == 4
    assert result["metrics"]["checkpoint_restore_error"] == pytest.approx(2e-9)
    assert any(
        "small-scale run is not evidence of formal 32-branch capacity"
        in warning for warning in result["warnings"])
