"""Evaluator-only, boundary-sampled progress facts; never a weighted score.

The reader is installed by the trusted episode host, not supplied by a model.
Replay verifies archive consistency, not the authenticity of a physical sensor.
Unobserved predicates stay unknown and observations may be revoked.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping

from .provenance import canonical_sha256


SCHEMA = "eai.benchmark.goal_progress_evidence"
BOUNDARIES = frozenset({"initial", "skill_completed", "idle",
                        "execution_stopped", "business_evaluated", "terminal"})
FLAGS = {"scoreable": False, "execution_validated": False,
         "business_outcomes_validated": False}


def _number(value: Any, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


class GoalProgressRecorder:
    def __init__(self, public_task: Mapping[str, Any], episode_id: str):
        self.episode_id = _text(episode_id, "episode_id")
        self.task_sha256 = canonical_sha256(public_task)
        self.predicate_ids = sorted(row["id"] for row in public_task["required_outcomes"])
        if not self.predicate_ids or len(set(self.predicate_ids)) != len(self.predicate_ids):
            raise ValueError("required outcome identities must be nonempty and unique")
        self.horizon_s = _number(public_task["budgets"]["simulation_s"], "horizon_s")
        self.samples: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []

    def append(self, payload: Mapping[str, Any], *, boundary: str,
               host_time_s: float) -> None:
        if boundary not in BOUNDARIES:
            raise ValueError("unknown progress boundary")
        host_time_s = _number(host_time_s, "host_time_s")
        if not isinstance(payload, Mapping) or set(payload) != {
                "episode_id", "time_s", "clock_source", "evaluator_ref", "predicates"}:
            raise ValueError("progress reader must return the exact sample fields")
        if payload["episode_id"] != self.episode_id:
            raise ValueError("progress sample belongs to another episode")
        time_s = _number(payload["time_s"], "time_s")
        clock = payload["clock_source"]
        if clock not in {"native_simulation", "contract_clock"}:
            raise ValueError("unsupported progress clock source")
        evaluator = _text(payload["evaluator_ref"], "evaluator_ref")
        if self.samples:
            last = self.samples[-1]
            if boundary == "initial":
                raise ValueError("initial progress sample cannot follow another sample")
            if time_s < last["time_s"] or host_time_s < last["host_time_s"]:
                raise ValueError("progress clocks must be monotonic")
            if clock != last["clock_source"] or evaluator != last["evaluator_ref"]:
                raise ValueError("progress clock/evaluator cannot change within an episode")
            if last["boundary"] == "terminal":
                raise ValueError("progress sample after terminal boundary")
        predicates = payload["predicates"]
        if not isinstance(predicates, Mapping) or set(predicates) != set(self.predicate_ids):
            raise ValueError("progress sample must cover every required outcome exactly")
        for row in predicates.values():
            if not isinstance(row, Mapping) or set(row) != {"satisfied", "evidence_refs"}:
                raise ValueError("invalid predicate fields")
            value = row["satisfied"]
            if value is not None and type(value) is not bool:
                raise ValueError("predicate must be true, false, or unknown")
            refs = row["evidence_refs"]
            if not isinstance(refs, list) or any(
                    not isinstance(ref, str) or not ref.strip() for ref in refs):
                raise ValueError("predicate evidence refs must be strings")
            if len(set(refs)) != len(refs) or (value is not None and not refs):
                raise ValueError("measured predicates require unique nonempty evidence refs")
        previous = self.samples[-1]["predicates"] if self.samples else {}
        changes = [key for key in self.predicate_ids
                   if key not in previous
                   or previous[key]["satisfied"] != predicates[key]["satisfied"]]
        self.samples.append({"sequence": len(self.samples), "boundary": boundary,
                             "host_time_s": host_time_s, **deepcopy(dict(payload)),
                             "changed_predicate_ids": changes})

    def record_error(self, *, boundary: str, host_time_s: float, error: str) -> None:
        if boundary not in BOUNDARIES:
            raise ValueError("unknown progress boundary")
        self.errors.append({"boundary": boundary,
                            "host_time_s": _number(host_time_s, "host_time_s"),
                            "category": "infrastructure_evidence_failure",
                            "error": _text(error, "error")})

    def report(self, *, termination_reason: str, host_cutoff_s: float) -> dict[str, Any]:
        final = self.samples[-1] if self.samples else None
        cutoff = _number(host_cutoff_s, "host_cutoff_s")
        if final is not None and cutoff < final["host_time_s"]:
            raise ValueError("host cutoff precedes last progress sample")
        report = {
            "schema": SCHEMA, "version": 1, "episode_id": self.episode_id,
            "public_task_sha256": self.task_sha256,
            "predicate_ids": list(self.predicate_ids), "horizon_s": self.horizon_s,
            "sampling_scope": "runner_boundaries_not_continuous_physical_transitions",
            "integrity_scope": "internal_consistency_not_physical_authentication",
            "raw_only": True, "not_protocol_score": True, **FLAGS,
            "status": "observed" if final and self.samples[0]["boundary"] == "initial"
                      and final["boundary"] == "terminal"
                      and not self.errors else "failed",
            "samples": deepcopy(self.samples), "quality_errors": deepcopy(self.errors),
            "termination": {
                "reason": _text(termination_reason, "termination_reason"),
                "host_time_s": cutoff,
                "measurement_time_s": final["time_s"] if final and
                    final["boundary"] == "terminal" else None},
            "weighted_progress": None,
            "missing_inputs": ["frozen_milestone_weights", "continuous_transition_coverage",
                               "physical_source_authentication"],
        }
        report["report_sha256"] = canonical_sha256(report)
        return report


def replay_progress_evidence(report: Mapping[str, Any], public_task: Mapping[str, Any],
                             *, episode_id: str) -> dict[str, Any]:
    """Recompute all derived fields; neither a stored digest nor a flag is proof."""
    if not isinstance(report, Mapping):
        raise ValueError("progress evidence must be an object")
    recorder = GoalProgressRecorder(public_task, episode_id)
    samples, errors = report.get("samples"), report.get("quality_errors")
    if not isinstance(samples, list) or not isinstance(errors, list):
        raise ValueError("progress samples/errors must be lists")
    for row in samples:
        if not isinstance(row, Mapping) or set(row) != {
                "sequence", "boundary", "host_time_s", "episode_id", "time_s",
                "clock_source", "evaluator_ref", "predicates", "changed_predicate_ids"}:
            raise ValueError("invalid archived progress sample")
        recorder.append({key: row[key] for key in (
            "episode_id", "time_s", "clock_source", "evaluator_ref", "predicates")},
            boundary=row["boundary"], host_time_s=row["host_time_s"])
    for row in errors:
        if not isinstance(row, Mapping) or set(row) != {
                "boundary", "host_time_s", "category", "error"}:
            raise ValueError("invalid archived progress error")
        recorder.record_error(boundary=row["boundary"], host_time_s=row["host_time_s"],
                              error=row["error"])
    termination = report.get("termination")
    if not isinstance(termination, Mapping):
        raise ValueError("progress termination must be an object")
    expected = recorder.report(termination_reason=termination.get("reason"),
                               host_cutoff_s=termination.get("host_time_s"))
    if canonical_sha256(report) != canonical_sha256(expected):
        raise ValueError("progress archive differs from independently recomputed facts")
    return {"validated": True, "status": expected["status"],
            "sample_count": len(samples), "report_sha256": expected["report_sha256"],
            "episode_id": episode_id, "integrity_scope": expected["integrity_scope"],
            **FLAGS}
