"""Externally evaluated P2 precedence/handoff milestone semantics.

The evaluator consumes observation snapshots and externally emitted receipts.
It deliberately does not inspect assignments, skill plans or executor
feedback.  A missing physical observation is ``N/A`` at the report level;
missing milestone-specific evidence is ``unknown``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from .evidence import EvaluationReport, EvaluationStatus, EvidenceRecord, EvidenceStatus
from .protocol import BranchSpec, ProtocolContract


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _position(row: Any) -> tuple[float, float, float] | None:
    if isinstance(row, Mapping):
        row = row.get("position", row.get("xyz"))
    if row is None:
        return None
    try:
        values = tuple(_number(item) for item in row)
    except TypeError:
        return None
    if len(values) != 3 or any(item is None for item in values):
        return None
    return tuple(float(item) for item in values)  # type: ignore[arg-type]


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class P2Observation:
    """Normalized one externally sampled physical snapshot."""

    tick: int
    sim_time_s: float
    robots: Mapping[str, Any]
    targets: Mapping[str, Any]
    external_events: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, fallback_tick: int = 0) -> "P2Observation":
        raw_tick = value.get("tick", fallback_tick)
        try:
            tick = int(raw_tick)
        except (TypeError, ValueError) as exc:
            raise ValueError("P2 observation tick must be an integer") from exc
        if tick < 0:
            raise ValueError("P2 observation tick cannot be negative")
        sim_time = _number(value.get("sim_time_s", 0.0))
        if sim_time is None or sim_time < 0:
            raise ValueError("P2 observation sim_time_s must be finite and non-negative")
        robots = value.get("robots", {})
        targets = value.get("targets", {})
        events = value.get("external_events", ())
        if not isinstance(robots, Mapping) or not isinstance(targets, Mapping):
            raise ValueError("P2 observation robots and targets must be mappings")
        if not isinstance(events, (list, tuple)):
            raise ValueError("P2 observation external_events must be a sequence")
        normalized_events = tuple(dict(item) for item in events if isinstance(item, Mapping))
        return cls(tick=tick, sim_time_s=sim_time, robots=dict(robots), targets=dict(targets), external_events=normalized_events)


class P2MilestoneEvaluator:
    """Evaluate P2's precedence → release → handoff → accept ordering."""

    evaluator_id = "benchmark.p2.external_evidence"
    evaluator_version = "p2.v1"

    def __init__(
        self,
        *,
        contract: ProtocolContract | None = None,
        predecessor_id: str | None = None,
        receiver_id: str | None = None,
        release_zone: str = "release_zone",
        handoff_zone: str = "handoff_zone",
        accept_zone: str = "accept_zone",
        tolerance_m: float = 0.25,
        stable_window: int = 1,
        milestone_ids: Sequence[str] = ("precedence", "release", "handoff", "accept"),
    ) -> None:
        if tolerance_m <= 0 or not math.isfinite(float(tolerance_m)):
            raise ValueError("tolerance_m must be positive and finite")
        if stable_window <= 0:
            raise ValueError("stable_window must be positive")
        self.contract = contract
        metadata = dict((contract.metadata if contract is not None else {}) or {})
        p2 = metadata.get("p2", {})
        if isinstance(p2, Mapping):
            predecessor_id = predecessor_id or p2.get("predecessor_id")
            receiver_id = receiver_id or p2.get("receiver_id")
            release_zone = str(p2.get("release_zone", release_zone))
            handoff_zone = str(p2.get("handoff_zone", handoff_zone))
            accept_zone = str(p2.get("accept_zone", accept_zone))
            tolerance_m = float(p2.get("tolerance_m", tolerance_m))
            stable_window = int(p2.get("stable_window", stable_window))
            milestone_ids = tuple(str(item) for item in p2.get("milestone_ids", milestone_ids))
        self.predecessor_id = str(predecessor_id or "").strip()
        self.receiver_id = str(receiver_id or "").strip()
        self.release_zone = release_zone
        self.handoff_zone = handoff_zone
        self.accept_zone = accept_zone
        self.tolerance_m = tolerance_m
        self.stable_window = stable_window
        self.milestone_ids = tuple(str(item).strip() for item in milestone_ids if str(item).strip())
        if not self.milestone_ids:
            raise ValueError("at least one P2 milestone is required")

    def evaluate(
        self,
        *,
        task: Any,
        observations: Sequence[Mapping[str, Any]],
        branch: BranchSpec | None,
    ) -> EvaluationReport:
        del task, branch
        if not observations:
            return EvaluationReport(
                status=EvaluationStatus.NOT_APPLICABLE,
                reason="physical_evidence_required",
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
            )
        try:
            samples = tuple(P2Observation.from_mapping(item, fallback_tick=index) for index, item in enumerate(observations))
        except ValueError as exc:
            return EvaluationReport(
                status=EvaluationStatus.UNKNOWN,
                reason=f"invalid_physical_observation: {exc}",
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
            )
        if not any(sample.robots or sample.targets or sample.external_events for sample in samples):
            return EvaluationReport(
                status=EvaluationStatus.NOT_APPLICABLE,
                reason="physical_evidence_required",
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
            )
        samples = tuple(sorted(samples, key=lambda item: item.tick))
        results: dict[str, EvidenceRecord] = {}
        results["precedence"] = self._precedence(samples)
        results["release"] = self._receipt_milestone(samples, "release_receipt", self.predecessor_id, self.release_zone, "release")
        results["handoff"] = self._handoff(samples, results["release"])
        results["accept"] = self._receipt_milestone(samples, "accept_receipt", self.receiver_id, self.accept_zone, "accept")

        # Preserve contract-declared ordering.  If a later event is observed
        # before its predecessor, that is a physical protocol violation.
        ordered = [name for name in ("precedence", "release", "handoff", "accept") if name in results and name in self.milestone_ids]
        for earlier, later in zip(ordered, ordered[1:]):
            left, right = results[earlier], results[later]
            if left.status is EvidenceStatus.SATISFIED and right.status is EvidenceStatus.SATISFIED and right.tick < left.tick:
                sample = next((item for item in samples if item.tick == right.tick), samples[-1])
                results[later] = self._record(later, EvidenceStatus.VIOLATED, right.tick, "external milestone precedes its prerequisite", sample)

        evidence = tuple(results[name] for name in self.milestone_ids if name in results)
        if any(item.status is EvidenceStatus.VIOLATED for item in evidence):
            status = EvaluationStatus.FAILURE
            reason = "p2_milestone_order_or_predicate_violated"
        elif evidence and all(item.status is EvidenceStatus.SATISFIED for item in evidence):
            status = EvaluationStatus.SUCCESS
            reason = "p2_milestones_satisfied_by_external_evidence"
        else:
            status = EvaluationStatus.UNKNOWN
            reason = "p2_milestone_evidence_incomplete"
        return EvaluationReport(
            status=status,
            reason=reason,
            evidence=evidence,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
        )

    def _precedence(self, samples: Sequence[P2Observation]) -> EvidenceRecord:
        if not self.predecessor_id:
            return self._record("precedence", EvidenceStatus.UNKNOWN, samples[-1].tick, "predecessor_id is not configured", samples[-1])
        for sample in samples:
            robot = _position(sample.robots.get(self.predecessor_id))
            target = _position(sample.targets.get(self.release_zone))
            if robot is not None and target is not None and _distance(robot, target) <= self.tolerance_m:
                return self._record("precedence", EvidenceStatus.SATISFIED, sample.tick, "predecessor reached release opportunity", sample)
        return self._record("precedence", EvidenceStatus.UNKNOWN, samples[-1].tick, "missing predecessor/release opportunity pose", samples[-1])

    def _receipt_milestone(
        self,
        samples: Sequence[P2Observation],
        event_name: str,
        robot_id: str,
        zone_name: str,
        predicate_id: str,
    ) -> EvidenceRecord:
        if not robot_id:
            return self._record(predicate_id, EvidenceStatus.UNKNOWN, samples[-1].tick, "robot_id is not configured", samples[-1])
        event_samples = [sample for sample in samples if self._has_event(sample, event_name, robot_id)]
        if not event_samples:
            return self._record(predicate_id, EvidenceStatus.UNKNOWN, samples[-1].tick, f"missing external {event_name}", samples[-1])
        for sample in event_samples:
            robot = _position(sample.robots.get(robot_id))
            zone = _position(sample.targets.get(zone_name))
            if robot is not None and zone is not None and _distance(robot, zone) <= self.tolerance_m:
                required_window = self.stable_window if predicate_id == "accept" else 1
                return self._stable_record(
                    samples,
                    sample,
                    predicate_id,
                    robot_id,
                    zone_name,
                    f"external {event_name} and pose satisfied",
                    required_window=required_window,
                )
        return self._record(predicate_id, EvidenceStatus.UNKNOWN, event_samples[0].tick, f"{event_name} lacks pose evidence at zone", event_samples[0])

    def _handoff(self, samples: Sequence[P2Observation], release: EvidenceRecord) -> EvidenceRecord:
        if release.status is not EvidenceStatus.SATISFIED:
            return self._record("handoff", EvidenceStatus.UNKNOWN, samples[-1].tick, "release evidence is not satisfied", samples[-1])
        if not self.receiver_id:
            return self._record("handoff", EvidenceStatus.UNKNOWN, samples[-1].tick, "receiver_id is not configured", samples[-1])
        for sample in samples:
            if sample.tick < release.tick:
                continue
            receiver = _position(sample.robots.get(self.receiver_id))
            zone = _position(sample.targets.get(self.handoff_zone))
            if receiver is None or zone is None or _distance(receiver, zone) > self.tolerance_m:
                continue
            if self._has_event(sample, "handoff_receipt", self.receiver_id) or self._has_event(sample, "handoff_receipt", ""):
                return self._record("handoff", EvidenceStatus.SATISFIED, sample.tick, "receiver eligibility and external handoff receipt satisfied", sample)
        return self._record("handoff", EvidenceStatus.UNKNOWN, samples[-1].tick, "missing receiver eligibility or handoff receipt", samples[-1])

    def _stable_record(
        self,
        samples: Sequence[P2Observation],
        event_sample: P2Observation,
        predicate_id: str,
        robot_id: str,
        zone_name: str,
        reason: str,
        required_window: int = 1,
    ) -> EvidenceRecord:
        zone = _position(event_sample.targets.get(zone_name))
        if zone is None:
            return self._record(predicate_id, EvidenceStatus.UNKNOWN, event_sample.tick, "zone pose is missing", event_sample)
        window = [sample for sample in samples if sample.tick >= event_sample.tick]
        stable: list[P2Observation] = []
        previous_tick: int | None = None
        for sample in window:
            robot = _position(sample.robots.get(robot_id))
            if robot is None or _distance(robot, zone) > self.tolerance_m:
                break
            if previous_tick is not None and sample.tick != previous_tick + 1:
                break
            stable.append(sample)
            previous_tick = sample.tick
            if len(stable) >= required_window:
                return self._record(predicate_id, EvidenceStatus.SATISFIED, stable[-1].tick, reason, stable[-1], stability_window=len(stable))
        return self._record(predicate_id, EvidenceStatus.UNKNOWN, event_sample.tick, f"stable window of {required_window} is unavailable", event_sample)

    @staticmethod
    def _has_event(sample: P2Observation, event_name: str, robot_id: str) -> bool:
        for event in sample.external_events:
            if str(event.get("event", "")).strip() != event_name:
                continue
            event_robot = str(event.get("robot_id", "")).strip()
            if not robot_id or event_robot == robot_id:
                return True
        return False

    def _record(
        self,
        predicate_id: str,
        status: EvidenceStatus,
        tick: int,
        reason: str,
        sample: P2Observation,
        *,
        stability_window: int = 1,
    ) -> EvidenceRecord:
        evidence = {
            "predicate_id": predicate_id,
            "predicate_version": self.evaluator_version,
            "tick": tick,
            "sample": {
                "robots": sample.robots,
                "targets": sample.targets,
                "external_events": sample.external_events,
            },
            "reason": reason,
        }
        return EvidenceRecord(
            predicate_id=predicate_id,
            predicate_version=self.evaluator_version,
            status=status,
            tick=tick,
            source=self.evaluator_id,
            evidence_hash=_canonical_hash(evidence),
            stability_window=stability_window,
            reason=reason,
        )


ExternalEvidenceEvaluator = P2MilestoneEvaluator


__all__ = ["ExternalEvidenceEvaluator", "P2MilestoneEvaluator", "P2Observation"]
