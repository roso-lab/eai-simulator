from __future__ import annotations

from dataclasses import asdict

import pytest

from benchmark.evaluator.room06_livt04 import build_diagnostic_runtime_fixture
from benchmark.room06_gpu_receipt_seam_v1 import (
    FORMAL_GATES,
    Room06GpuReceiptSeamError,
    build_terminal_payload,
)
from tools.benchmark_branch.models import BranchJob


def _job() -> BranchJob:
    return BranchJob(
        run_id="room06-gpu-run",
        system_id="emos-zero",
        scene_key="room_06",
        task_id="liv-t-04",
        condition_id="C0",
        slot=0,
        branch_type="fact",
        branch_id="room06-gpu-r1",
        pair_group_id="room06-gpu-r1",
        repeat_id="r1",
        execution_scope="measured_diagnostic",
    )


def _report() -> dict:
    runtime = build_diagnostic_runtime_fixture()
    payload = {
        "episode": runtime["episode"],
        "streams": runtime["streams"],
        "base_facts": runtime["base_facts"],
        "obligation_binding": runtime["obligation_binding"],
        "runtime_binding": runtime["runtime_binding"],
        "runtime_evidence_digest": "a" * 64,
        "runtime_state": "complete",
        "assembler_schema": "heterocoord.room06-terminal-material.v1",
        "formal_gates": dict(FORMAL_GATES),
    }
    return {
        "status": "terminal_observation_captured",
        "terminal_payload": payload,
        "formal_gates": dict(FORMAL_GATES),
    }


def test_wraps_explicit_payload_for_map_ready_case() -> None:
    job = _job()
    result = build_terminal_payload(
        gpu_report=_report(),
        job=job,
        evaluator_result={"status": "valid_terminal", "success": True},
        parameters={"alpha": 0.05},
    )

    assert list(result) == ["terminals", "parameters"]
    assert result["terminals"][0]["job"] == asdict(job)
    assert result["terminals"][0]["runtime"]["room06_livt04"]["formal_gates"] == FORMAL_GATES
    assert result["terminals"][0]["evaluator_result"] == {
        "status": "valid_terminal",
        "success": True,
    }


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda report: report.update(status="diagnostic_only"), "terminal_observation_captured"),
        (lambda report: report["terminal_payload"].pop("base_facts"), "schema mismatch"),
        (lambda report: report.update(formal_gates={**FORMAL_GATES, "rankable": True}), "formal gates"),
    ],
)
def test_rejects_missing_or_non_terminal_gpu_evidence(mutate, message: str) -> None:
    report = _report()
    mutate(report)
    with pytest.raises(Room06GpuReceiptSeamError, match=message):
        build_terminal_payload(
            gpu_report=report,
            job=_job(),
            evaluator_result={"status": "valid_terminal"},
            parameters={},
        )


def test_rejects_wrong_case_job() -> None:
    job = asdict(_job())
    job["task_id"] = "other-task"
    with pytest.raises(Room06GpuReceiptSeamError, match="room_06/liv-t-04"):
        build_terminal_payload(
            gpu_report=_report(),
            job=job,
            evaluator_result={"status": "valid_terminal"},
            parameters={},
        )


def test_does_not_infer_evaluator_result_from_gpu_report() -> None:
    result = build_terminal_payload(
        gpu_report=_report(),
        job=_job(),
        evaluator_result={"status": "explicit-verdict", "success": False},
        parameters={},
    )
    assert result["terminals"][0]["evaluator_result"] == {
        "status": "explicit-verdict",
        "success": False,
    }
