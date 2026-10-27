"""Explicit bridge from a room-06 GPU report to map-ready terminal input.

The GPU probe owns observation capture and already assembles an evaluator
payload.  This module only wraps that payload with caller-supplied benchmark
identity and evaluator output so :mod:`benchmark.map_ready_case_v1` can
consume it.  It never derives a job, evaluator verdict, success, or
qualification state from process status or the GPU report envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from tools.benchmark_branch.models import BranchJob


FORMAL_GATES = {
    "rankable": False,
    "paper_score_eligible": False,
    "physical_qualified": False,
}
SEAM_SCHEMA = "heterocoord.room06-gpu-receipt-seam.v1"
REQUIRED_PAYLOAD_KEYS = {
    "episode",
    "streams",
    "base_facts",
    "obligation_binding",
    "runtime_binding",
    "runtime_evidence_digest",
    "runtime_state",
    "assembler_schema",
    "formal_gates",
}


class Room06GpuReceiptSeamError(ValueError):
    """Raised when a GPU report cannot be handed off without inference."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Room06GpuReceiptSeamError(f"{name} must be an object")
    return value


def _job_mapping(value: BranchJob | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, BranchJob):
        job = value
    else:
        raw = _mapping(value, "job")
        try:
            job = BranchJob.from_mapping(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise Room06GpuReceiptSeamError(f"job is invalid: {exc}") from exc
    if job.scene_key != "room_06" or job.task_id != "liv-t-04":
        raise Room06GpuReceiptSeamError(
            "job must identify the room_06/liv-t-04 case"
        )
    return asdict(job)


def build_terminal_payload(
    *,
    gpu_report: Mapping[str, Any],
    job: BranchJob | Mapping[str, Any],
    evaluator_result: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Wrap explicit terminal material for ``map_ready_case_v1``.

    ``gpu_report`` must contain the probe's already assembled
    ``terminal_payload``.  No field is reconstructed from ``status``, process
    exit, or ``scene_readback``.  The evaluator result remains an explicit
    caller-owned verdict and is passed through unchanged apart from a JSON
    detach.
    """

    report = _mapping(gpu_report, "gpu_report")
    if report.get("status") != "terminal_observation_captured":
        raise Room06GpuReceiptSeamError(
            "gpu_report must be a terminal_observation_captured report"
        )
    if report.get("formal_gates") != FORMAL_GATES:
        raise Room06GpuReceiptSeamError("gpu_report formal gates must remain closed")

    payload = _mapping(report.get("terminal_payload"), "gpu_report.terminal_payload")
    if set(payload) != REQUIRED_PAYLOAD_KEYS:
        missing = sorted(REQUIRED_PAYLOAD_KEYS - set(payload))
        extra = sorted(set(payload) - REQUIRED_PAYLOAD_KEYS)
        raise Room06GpuReceiptSeamError(
            f"terminal_payload schema mismatch (missing={missing}, extra={extra})"
        )
    if payload.get("formal_gates") != FORMAL_GATES:
        raise Room06GpuReceiptSeamError(
            "terminal_payload formal gates must remain closed"
        )
    evaluator = _mapping(evaluator_result, "evaluator_result")
    if not evaluator:
        raise Room06GpuReceiptSeamError(
            "evaluator_result must contain an explicit evaluator verdict"
        )
    params = _mapping(parameters, "parameters")

    terminal_job = _job_mapping(job)
    runtime = {"room06_livt04": deepcopy(dict(payload))}
    # Canonical JSON detach catches accidental non-serialisable live objects
    # before the downstream artifact writer is invoked.
    try:
        detached = deepcopy(
            {
                "terminals": [{
                    "job": terminal_job,
                    "runtime": runtime,
                    "evaluator_result": dict(evaluator),
                }],
                "parameters": dict(params),
            }
        )
    except (TypeError, ValueError) as exc:
        raise Room06GpuReceiptSeamError(
            "terminal handoff contains non-detachable values"
        ) from exc
    return detached


__all__ = [
    "FORMAL_GATES",
    "REQUIRED_PAYLOAD_KEYS",
    "Room06GpuReceiptSeamError",
    "SEAM_SCHEMA",
    "build_terminal_payload",
]
