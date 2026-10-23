"""Bounded, caller-supplied D4 value evaluation for the current paper.

The evaluator consumes a frozen value rule and an external evaluator timeline.
It does not inspect a simulator, infer physical success, or grant formal
qualification.  State-change logs are authoritative only through their
declared observation horizon; missing coverage remains incomplete.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping, Sequence

from .records_v3 import RecordStatus, canonical_digest


_ITEM_TYPES = {"deadline", "time_valued", "continuous_supply"}
_TERMINAL_KINDS = {"fail", "infeas", "safe"}
_EVENT_PRIORITY = {"fail": 0, "infeas": 1, "safe": 2, "resume": 3}


@dataclass(frozen=True)
class OutcomeValueV3:
    outcome_id: str
    weight: float
    persists_after_terminal: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.outcome_id, str) or not self.outcome_id.strip():
            raise ValueError("outcome_id is required")
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise ValueError("outcome weight must be finite and positive")
        if type(self.persists_after_terminal) is not bool:
            raise ValueError("outcome persistence must be boolean")


@dataclass(frozen=True)
class DensitySegmentV3:
    """Piecewise-constant density over relative elapsed ticks [start, end)."""

    start: int
    end: int
    density: float

    def __post_init__(self) -> None:
        if type(self.start) is not int or type(self.end) is not int or self.start < 0 or self.end <= self.start:
            raise ValueError("density segment needs ordered nonnegative integer ticks")
        if not math.isfinite(self.density) or self.density < 0:
            raise ValueError("density must be finite and nonnegative")


@dataclass(frozen=True)
class DynamicValueRegistrationV3:
    item_id: str
    item_type: str
    event_tick: int
    horizon_tick: int
    outcomes: tuple[OutcomeValueV3, ...]
    deadline_tick: int | None = None
    density: tuple[DensitySegmentV3, ...] = ()
    density_purpose: str = ""
    demand_start_tick: int | None = None
    demand_end_tick: int | None = None
    stability_ticks: int | None = None
    beta: float = .5

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        object.__setattr__(self, "density", tuple(self.density))
        if not isinstance(self.item_id, str) or not self.item_id.strip() or self.item_type not in _ITEM_TYPES:
            raise ValueError("registered D4 item identity/type is invalid")
        if type(self.event_tick) is not int or type(self.horizon_tick) is not int or self.event_tick < 0 or self.horizon_tick <= self.event_tick:
            raise ValueError("D4 event and common horizon must be ordered absolute ticks")
        if not self.outcomes or len({item.outcome_id for item in self.outcomes}) != len(self.outcomes):
            raise ValueError("D4 needs a frozen nonempty unique outcome set")
        if not math.isclose(sum(item.weight for item in self.outcomes), 1., abs_tol=1e-12):
            raise ValueError("D4 outcome weights must sum to one")
        if self.beta != .5:
            raise ValueError("current paper freezes D4 beta=1/2; sensitivity is separate")
        if self.item_type == "deadline":
            if type(self.deadline_tick) is not int or not self.event_tick <= self.deadline_tick <= self.horizon_tick:
                raise ValueError("deadline work needs a frozen absolute deadline in the common window")
            if self.density or any(value is not None for value in (self.demand_start_tick, self.demand_end_tick, self.stability_ticks)):
                raise ValueError("deadline work cannot register time-value/service fields")
        elif self.item_type == "time_valued":
            if type(self.deadline_tick) is not int or not self.event_tick <= self.deadline_tick <= self.horizon_tick:
                raise ValueError("time-valued work needs an absolute achievement deadline")
            if not self.density or any(value is not None for value in (self.demand_start_tick, self.demand_end_tick, self.stability_ticks)):
                raise ValueError("time-valued work needs only its registered density")
            duration = self.horizon_tick - self.event_tick
            ordered = tuple(sorted(self.density, key=lambda item: (item.start, item.end)))
            if ordered != self.density or ordered[0].start != 0 or ordered[-1].end != duration or any(left.end != right.start for left, right in zip(ordered, ordered[1:])):
                raise ValueError("density segments must exactly partition [0,H_c]")
            if not math.isclose(sum(item.density * (item.end - item.start) for item in ordered), 1., abs_tol=1e-12):
                raise ValueError("registered time-value density must integrate to one")
            positive = {item.density for item in ordered}
            if len(positive) > 1 and not self.density_purpose.strip():
                raise ValueError("non-uniform time value requires a documented task purpose")
        else:
            if self.deadline_tick is not None or self.density:
                raise ValueError("continuous supply uses its fixed demand interval, not a deadline/density")
            if any(type(value) is not int for value in (self.demand_start_tick, self.demand_end_tick, self.stability_ticks)):
                raise ValueError("continuous supply needs integer demand/stability ticks")
            assert self.demand_start_tick is not None and self.demand_end_tick is not None and self.stability_ticks is not None
            if not self.event_tick <= self.demand_start_tick < self.demand_end_tick <= self.horizon_tick:
                raise ValueError("continuous demand interval must lie in the common window")
            if not 0 < self.stability_ticks <= self.demand_end_tick - self.demand_start_tick:
                raise ValueError("continuous stability window must fit the demand interval")

    def digest(self) -> str:
        return canonical_digest(asdict(self))


@dataclass(frozen=True)
class OutcomeStateV3:
    outcome_id: str
    tick: int
    valid: bool | None
    evidence_digest: str
    legal: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.outcome_id, str) or not self.outcome_id.strip() or type(self.tick) is not int or self.tick < 0:
            raise ValueError("outcome state identity/tick is invalid")
        if self.valid is not None and type(self.valid) is not bool:
            raise ValueError("outcome validity must be bool or explicit unknown")
        if type(self.legal) is not bool or not isinstance(self.evidence_digest, str) or not self.evidence_digest.strip():
            raise ValueError("outcome state needs legality and external evidence")


@dataclass(frozen=True)
class ServiceStateV3:
    tick: int
    valid: bool | None
    evidence_digest: str

    def __post_init__(self) -> None:
        if type(self.tick) is not int or self.tick < 0 or (self.valid is not None and type(self.valid) is not bool):
            raise ValueError("service state tick/value is invalid")
        if not isinstance(self.evidence_digest, str) or not self.evidence_digest.strip():
            raise ValueError("service state needs external evidence")


@dataclass(frozen=True)
class TerminalEventV3:
    tick: int
    kind: str
    evidence_digest: str
    certified: bool = False
    policy_exhaustion: bool = False

    def __post_init__(self) -> None:
        if type(self.tick) is not int or self.tick < 0 or self.kind not in _TERMINAL_KINDS:
            raise ValueError("terminal event tick/kind is invalid")
        if not isinstance(self.evidence_digest, str) or not self.evidence_digest.strip():
            raise ValueError("terminal event needs external evidence")
        if type(self.certified) is not bool or type(self.policy_exhaustion) is not bool:
            raise ValueError("terminal certification flags must be boolean")


@dataclass(frozen=True)
class DynamicValueTimelineV3:
    item_id: str
    observed_through_tick: int
    outcome_states: tuple[OutcomeStateV3, ...] = ()
    service_states: tuple[ServiceStateV3, ...] = ()
    terminal_events: tuple[TerminalEventV3, ...] = ()
    operation_evidence_hash: str = ""
    remaining_feasibility_hash: str = ""
    progress_evidence_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome_states", tuple(self.outcome_states))
        object.__setattr__(self, "service_states", tuple(self.service_states))
        object.__setattr__(self, "terminal_events", tuple(self.terminal_events))
        if not isinstance(self.item_id, str) or not self.item_id.strip() or type(self.observed_through_tick) is not int or self.observed_through_tick < 0:
            raise ValueError("timeline identity/coverage is invalid")
        if any(not isinstance(value, str) or not value.strip() for value in (self.operation_evidence_hash, self.remaining_feasibility_hash, self.progress_evidence_hash)):
            raise ValueError("timeline must bind operation, feasibility and progress evidence")
        outcome_keys = [(item.outcome_id, item.tick) for item in self.outcome_states]
        if len(outcome_keys) != len(set(outcome_keys)):
            raise ValueError("duplicate outcome/tick states are ambiguous")
        service_ticks = [item.tick for item in self.service_states]
        if len(service_ticks) != len(set(service_ticks)):
            raise ValueError("duplicate service/tick states are ambiguous")
        if any(item.tick > self.observed_through_tick for item in (*self.outcome_states, *self.service_states, *self.terminal_events)):
            raise ValueError("timeline event exceeds declared observation coverage")

    def digest(self) -> str:
        return canonical_digest(asdict(self))


@dataclass(frozen=True)
class DynamicValueEvaluationV3:
    item_id: str
    status: RecordStatus
    legal_completion_fraction: float | None
    service_fraction: float | None
    confirmation_tick: int | None
    first_event_cause: str | None
    first_event_tick: int | None
    tied_first_causes: tuple[str, ...]
    event_records: tuple[Mapping[str, Any], ...]
    evidence_digests: tuple[str, ...]
    registration_digest: str
    timeline_digest: str
    reason: str | None = None
    physical_qualified: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", RecordStatus(self.status))
        if self.physical_qualified is not False:
            raise ValueError("timeline evaluation cannot grant physical qualification")

    def to_dynamic_observation(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "invalid_reason": self.reason,
            "operation_evidence_hash": self.evidence_digests[0],
            "remaining_feasibility_hash": self.evidence_digests[1],
            "progress_evidence_hash": self.evidence_digests[2],
            "legal_completion_fraction": self.legal_completion_fraction,
            "service_fraction": self.service_fraction,
            "extra_metadata": {
                "value_registration_digest": self.registration_digest,
                "value_timeline_digest": self.timeline_digest,
                "confirmation_tick": self.confirmation_tick,
                "first_event_cause": self.first_event_cause,
                "first_event_tick": self.first_event_tick,
                "tied_first_causes": list(self.tied_first_causes),
                "event_records": [dict(item) for item in self.event_records],
                "physical_qualified": False,
                "evaluator_scope": "caller_supplied_external_timeline",
            },
        }


def _state_at(states: Sequence[Any], tick: int) -> bool | None:
    current: bool | None = None
    found = False
    for state in states:
        if state.tick > tick:
            break
        current = state.valid if not hasattr(state, "legal") or state.legal else False
        found = True
    return current if found else None


def _classified_events(events: Sequence[TerminalEventV3]) -> tuple[list[dict[str, Any]], str | None]:
    records: list[dict[str, Any]] = []
    error = None
    for event in sorted(events, key=lambda item: (item.tick, _EVENT_PRIORITY[item.kind], item.evidence_digest)):
        if event.kind == "infeas" and event.policy_exhaustion:
            cause, note = "fail", "policy_exhaustion_cannot_be_exogenous"
        elif event.kind == "infeas" and not event.certified:
            cause, note = "unknown", "terminal_infeasibility_requires_exogenous_certificate"
            error = error or note
        elif event.kind == "safe" and not event.certified:
            cause, note = "unknown", "terminal_safe_stop_requires_valid_certificate"
            error = error or note
        else:
            cause, note = event.kind, None
        records.append({"tick": event.tick, "declared_cause": event.kind, "cause": cause,
                        "evidence_digest": event.evidence_digest, "note": note})
    return records, error


def _first_event(records: list[dict[str, Any]], confirmation_tick: int | None) -> tuple[str | None, int | None, tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    all_records = list(records)
    if confirmation_tick is not None:
        all_records.append({"tick": confirmation_tick, "declared_cause": "resume", "cause": "resume",
                            "evidence_digest": "derived_from_registered_stability_or_milestone", "note": None})
    all_records.sort(key=lambda item: (item["tick"], _EVENT_PRIORITY.get(item["cause"], -1), item["evidence_digest"]))
    if not all_records:
        return None, None, (), ()
    first_tick = all_records[0]["tick"]
    tied = tuple(sorted({item["cause"] for item in all_records if item["tick"] == first_tick}, key=lambda c: _EVENT_PRIORITY.get(c, -1)))
    return tied[0], first_tick, tied, tuple(all_records)


def _incomplete(registration: DynamicValueRegistrationV3, timeline: DynamicValueTimelineV3,
                reason: str, records: list[dict[str, Any]] | None = None) -> DynamicValueEvaluationV3:
    first, tick, tied, all_records = _first_event(records or [], None)
    return DynamicValueEvaluationV3(
        registration.item_id, RecordStatus.INCOMPLETE, None, None, None,
        first, tick, tied, all_records,
        (timeline.operation_evidence_hash, timeline.remaining_feasibility_hash, timeline.progress_evidence_hash),
        registration.digest(), timeline.digest(), reason,
    )


def evaluate_dynamic_value_v3(registration: DynamicValueRegistrationV3,
                              timeline: DynamicValueTimelineV3) -> DynamicValueEvaluationV3:
    """Evaluate one registered D4 item from a complete external state log."""

    if timeline.item_id != registration.item_id:
        raise ValueError("D4 value registration and timeline item identity differ")
    if any(e.tick < registration.event_tick or e.tick > registration.horizon_tick for e in timeline.terminal_events):
        raise ValueError("D4 terminal event outside the registered active window")
    records, terminal_error = _classified_events(timeline.terminal_events)
    if terminal_error:
        return _incomplete(registration, timeline, terminal_error, records)
    terminal_tick = min((item["tick"] for item in records), default=None)
    required_coverage = registration.horizon_tick if terminal_tick is None else min(registration.horizon_tick, terminal_tick)
    if timeline.observed_through_tick < required_coverage:
        return _incomplete(registration, timeline, "timeline_does_not_cover_required_window", records)

    if registration.item_type == "continuous_supply":
        assert registration.demand_start_tick is not None and registration.demand_end_tick is not None and registration.stability_ticks is not None
        states = tuple(sorted(timeline.service_states, key=lambda item: item.tick))
        if not states or states[0].tick > registration.demand_start_tick:
            return _incomplete(registration, timeline, "continuous_service_initial_state_missing", records)
        start, end = registration.demand_start_tick, registration.demand_end_tick
        breakpoints = {start, end, *(item.tick for item in states if start < item.tick < end)}
        if terminal_tick is not None and start < terminal_tick < end:
            breakpoints.add(terminal_tick)
        ordered = sorted(breakpoints)
        valid_duration = 0
        unknown = False
        for left, right in zip(ordered, ordered[1:]):
            state = False if terminal_tick is not None and left >= terminal_tick else _state_at(states, left)
            if state is None:
                unknown = True
            elif state:
                valid_duration += right - left
        if unknown:
            return _incomplete(registration, timeline, "continuous_service_observation_unknown", records)
        phi = valid_duration / (end - start)
        final_start = end - registration.stability_ticks
        final_breaks = {final_start, end, *(item.tick for item in states if final_start < item.tick <= end)}
        if terminal_tick is not None and final_start <= terminal_tick <= end:
            final_breaks.add(terminal_tick)
        final_ok = True
        for left, right in zip(sorted(final_breaks), sorted(final_breaks)[1:]):
            state = False if terminal_tick is not None and left >= terminal_tick else _state_at(states, left)
            final_ok = final_ok and state is True
        endpoint_state = False if terminal_tick is not None and terminal_tick <= end else _state_at(states, end)
        if endpoint_state is None:
            return _incomplete(registration, timeline, "continuous_final_state_unknown", records)
        final_ok = final_ok and endpoint_state is True
        if terminal_tick is not None and terminal_tick == end:
            final_ok = False
        confirmation_tick = None
        streak_start = None
        confirmation_start = max(registration.event_tick, start)
        confirmation_breaks = sorted({confirmation_start, end, *(item.tick for item in states if confirmation_start <= item.tick <= end), *([terminal_tick] if terminal_tick is not None and confirmation_start <= terminal_tick <= end else [])})
        for left, right in zip(confirmation_breaks, confirmation_breaks[1:]):
            state = False if terminal_tick is not None and left >= terminal_tick else _state_at(states, left)
            if state is True:
                if streak_start is None:
                    streak_start = left
                if right - streak_start >= registration.stability_ticks:
                    candidate = streak_start + registration.stability_ticks
                    # A genuine service interruption at the closed-window
                    # endpoint prevents confirmation. A terminal event itself
                    # is kept separately for conservative exact-tick ties.
                    if _state_at(states, candidate) is True:
                        confirmation_tick = candidate
                        break
            else:
                streak_start = None
        first, first_tick, tied, all_records = _first_event(records, confirmation_tick)
        return DynamicValueEvaluationV3(
            registration.item_id, RecordStatus.VALID, float(final_ok), phi, confirmation_tick,
            first, first_tick, tied, all_records,
            (timeline.operation_evidence_hash, timeline.remaining_feasibility_hash, timeline.progress_evidence_hash),
            registration.digest(), timeline.digest(), None,
        )

    by_outcome: dict[str, tuple[OutcomeStateV3, ...]] = {}
    for state in timeline.outcome_states:
        by_outcome.setdefault(state.outcome_id, tuple())
        by_outcome[state.outcome_id] = (*by_outcome[state.outcome_id], state)
    expected = {item.outcome_id for item in registration.outcomes}
    if set(by_outcome) != expected:
        return _incomplete(registration, timeline, "registered_outcome_state_directory_mismatch", records)
    by_outcome = {key: tuple(sorted(value, key=lambda item: item.tick)) for key, value in by_outcome.items()}
    if any(not states or states[0].tick > registration.event_tick for states in by_outcome.values()):
        return _incomplete(registration, timeline, "outcome_initial_state_missing", records)

    rules = {item.outcome_id: item for item in registration.outcomes}
    terminal_pre_state = {
        outcome_id: (_state_at(states, terminal_tick - 1) if terminal_tick is not None else None)
        for outcome_id, states in by_outcome.items()
    }

    def outcome_state(outcome_id: str, tick: int) -> bool | None:
        if terminal_tick is not None and tick >= terminal_tick:
            return terminal_pre_state[outcome_id] if rules[outcome_id].persists_after_terminal else False
        return _state_at(by_outcome[outcome_id], tick)

    assert registration.deadline_tick is not None
    endpoint_states = {key: outcome_state(key, registration.deadline_tick) for key in expected}
    if any(value is None for value in endpoint_states.values()):
        return _incomplete(registration, timeline, "outcome_achievement_state_unknown", records)
    achievement = sum(rules[key].weight for key, value in endpoint_states.items() if value)
    completion_ticks: list[int] = []
    for outcome_id, states in by_outcome.items():
        previous = _state_at(states, registration.event_tick)
        for state in states:
            if state.tick <= registration.event_tick or state.tick > registration.horizon_tick:
                continue
            if terminal_tick is not None and state.tick > terminal_tick:
                continue
            current = state.valid if state.legal else False
            if current is True and previous is not True:
                completion_ticks.append(state.tick)
            previous = current
    confirmation_tick = min(completion_ticks, default=None)

    phi: float | None = None
    if registration.item_type == "time_valued":
        value = 0.
        for segment in registration.density:
            absolute_start = registration.event_tick + segment.start
            absolute_end = registration.event_tick + segment.end
            breaks = {absolute_start, absolute_end}
            for states in by_outcome.values():
                breaks.update(item.tick for item in states if absolute_start < item.tick < absolute_end)
            if terminal_tick is not None and absolute_start < terminal_tick < absolute_end:
                breaks.add(terminal_tick)
            ordered = sorted(breaks)
            for left, right in zip(ordered, ordered[1:]):
                states = {key: outcome_state(key, left) for key in expected}
                if any(item is None for item in states.values()):
                    return _incomplete(registration, timeline, "time_value_outcome_state_unknown", records)
                fraction = sum(rules[key].weight for key, state in states.items() if state)
                value += segment.density * fraction * (right - left)
        phi = value
    first, first_tick, tied, all_records = _first_event(records, confirmation_tick)
    return DynamicValueEvaluationV3(
        registration.item_id, RecordStatus.VALID, achievement, phi, confirmation_tick,
        first, first_tick, tied, all_records,
        (timeline.operation_evidence_hash, timeline.remaining_feasibility_hash, timeline.progress_evidence_hash),
        registration.digest(), timeline.digest(), None,
    )


__all__ = [
    "OutcomeValueV3", "DensitySegmentV3", "DynamicValueRegistrationV3",
    "OutcomeStateV3", "ServiceStateV3", "TerminalEventV3",
    "DynamicValueTimelineV3", "DynamicValueEvaluationV3",
    "evaluate_dynamic_value_v3",
]
