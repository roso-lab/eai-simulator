"""Opt-in readiness evidence for participant-authored Restaurant plans.

Unlike the frozen v1 diagnostic, this service archives repeated observations
and relay attempts. A receipt selects one observation per required region and
one causally subsequent relay; other valid evidence does not invalidate it.
This is a trusted service, not an API for participant self-attested success.
Digest consistency alone does not prove native execution: native adapters must
independently bind these records to physical evidence before scoring.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from .service_backend import REQUIRED_REGIONS

SCHEMA = "eai.restaurant.readiness_service.v2"
_COMMON_FIELDS = frozenset({"schema", "episode_id", "kind", "simulation_step", "time_s"})
_FIELDS = {
    "native_observation": _COMMON_FIELDS | {
        "region_id", "observation_id", "producer_id", "evidence_digest"},
    "relay_publication": _COMMON_FIELDS | {"message_id", "observation_refs"},
    "readiness_receipt": _COMMON_FIELDS | {
        "receipt_id", "observation_refs", "relay_message_id", "relay_digest",
        "observation_digests", "receipt_digest"},
}


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _stamp(row: Mapping[str, Any]) -> tuple[int, float]:
    step, time = row.get("simulation_step"), row.get("time_s")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise ValueError("simulation_step must be a nonnegative integer")
    if (isinstance(time, bool) or not isinstance(time, (int, float))
            or not math.isfinite(time) or time < 0):
        raise ValueError("time_s must be finite and nonnegative")
    return step, float(time)


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()


def _refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("observation_refs must be a list")
    refs = [_text(item, "observation ref") for item in value]
    if len(refs) != len(set(refs)):
        raise ValueError("duplicate observation references")
    return sorted(refs)


def _record(row: Any, *, episode_id: str, kind: str) -> None:
    if (not isinstance(row, Mapping) or row.get("schema") != SCHEMA
            or row.get("kind") != kind or row.get("episode_id") != episode_id):
        raise ValueError(f"invalid {kind} schema, kind or episode_id")
    if set(row) != _FIELDS[kind]:
        raise ValueError(f"invalid {kind} fields: unknown or missing keys")
    _stamp(row)


def _before(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> bool:
    step_a, time_a = _stamp(earlier)
    step_b, time_b = _stamp(later)
    return step_a < step_b and time_a < time_b


def _not_after(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> bool:
    step_a, time_a = _stamp(earlier)
    step_b, time_b = _stamp(later)
    return step_a <= step_b and time_a <= time_b


def _selection(refs: list[str], observations: Mapping[str, Mapping[str, Any]]) -> None:
    if (len(refs) != len(REQUIRED_REGIONS)
            or any(ref not in observations for ref in refs)
            or {observations[ref]["region_id"] for ref in refs} != set(REQUIRED_REGIONS)):
        raise ValueError("selection must bind one recorded observation per required region")


def evaluate_readiness_receipt_v2(
    *, episode_id: str, receipt: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Any]],
    relay_publications: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Independently verify the selected chain and the surrounding v2 archive.

    Valid extra observations and publications are permitted, but foreign,
    misfiled, duplicate-ID, nonfinite or causally invalid records are rejected.
    No arrival count, producer allocation or reference route is prescribed.
    """
    _text(episode_id, "episode_id")
    _record(receipt, episode_id=episode_id, kind="readiness_receipt")
    _text(receipt.get("receipt_id"), "receipt_id")
    if not isinstance(observations, Mapping):
        raise ValueError("observations must be keyed by observation_id")
    for key, row in observations.items():
        _record(row, episode_id=episode_id, kind="native_observation")
        if _text(row.get("observation_id"), "observation_id") != key:
            raise ValueError("observation archive key mismatch")
        if row.get("region_id") not in REQUIRED_REGIONS:
            raise ValueError("unknown observation region")
        _text(row.get("producer_id"), "producer_id")
        _text(row.get("evidence_digest"), "evidence_digest")
        if not _not_after(row, receipt):
            raise ValueError("observation cannot follow terminal receipt")
    refs = _refs(receipt.get("observation_refs"))
    _selection(refs, observations)
    if not isinstance(relay_publications, list):
        raise ValueError("relay_publications must be a list")
    publications = {}
    for row in relay_publications:
        _record(row, episode_id=episode_id, kind="relay_publication")
        message = _text(row.get("message_id"), "message_id")
        if message in publications:
            raise ValueError("duplicate relay message_id")
        relay_refs = _refs(row.get("observation_refs"))
        _selection(relay_refs, observations)
        if (not all(_before(observations[ref], row) for ref in relay_refs)
                or not _not_after(row, receipt)):
            raise ValueError("relay must follow its observations and cannot follow terminal receipt")
        publications[message] = row
    relay_id = _text(receipt.get("relay_message_id"), "relay_message_id")
    publication = publications.get(relay_id)
    if publication is None or _refs(publication["observation_refs"]) != refs:
        raise ValueError("receipt must select a matching relay publication")
    if not _before(publication, receipt):
        raise ValueError("selected relay must precede terminal receipt")
    if receipt.get("observation_digests") != {ref: _digest(observations[ref]) for ref in refs}:
        raise ValueError("selected observation digest mismatch")
    if receipt.get("relay_digest") != _digest(publication):
        raise ValueError("selected relay digest mismatch")
    # This invariant applies to unused evidence too: one native clock cannot
    # report conflicting timestamps for a step or move backwards across steps.
    timeline = sorted(set(_stamp(row) for row in (
        *observations.values(), *publications.values(), receipt)))
    if any(step_b <= step_a or time_b <= time_a
           for (step_a, time_a), (step_b, time_b) in zip(timeline, timeline[1:])):
        raise ValueError("archive native step/time mapping is inconsistent")
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt.get("receipt_digest") != _digest(body):
        raise ValueError("receipt digest mismatch")
    return {
        "validated": True, "schema": SCHEMA, "episode_id": episode_id,
        "outcome": "readiness_receipt_committed", "receipt_id": receipt["receipt_id"],
        "selected_observation_refs": refs, "selected_relay_message_id": relay_id,
        "observation_count": len(observations),
        "relay_publication_count": len(publications),
        "native_execution_verified": False,
    }


class RestaurantReadinessBackendV2:
    """Append trusted evidence until one terminal receipt is committed.

    Independent observations may share a physics step. Dependency edges
    (observation→relay→receipt) must each advance native time and step.
    Rejected calls are atomic and never advance the service clock.
    """

    def __init__(self, *, episode_id: str):
        self.episode_id = _text(episode_id, "episode_id")
        self._observations: dict[str, dict[str, Any]] = {}
        self._publications: dict[str, dict[str, Any]] = {}
        self._receipt: dict[str, Any] | None = None
        self._last_step = -1
        self._last_time_s = -1.0

    def _new(self, kind: str, simulation_step: int, time_s: float, **data: Any) -> dict[str, Any]:
        if self._receipt is not None:
            raise ValueError("readiness service already terminated")
        row = {"schema": SCHEMA, "episode_id": self.episode_id, "kind": kind,
               "simulation_step": simulation_step, "time_s": time_s, **data}
        step, timestamp = _stamp(row)
        if step < self._last_step or timestamp < self._last_time_s:
            raise ValueError("native evidence clock must not regress")
        # Two records for the same physical tick must agree on both clocks.
        if (step == self._last_step) != (timestamp == self._last_time_s):
            raise ValueError("same native step must have the same time")
        row["time_s"] = timestamp
        return row

    def _accept(self, row: Mapping[str, Any]) -> dict[str, Any]:
        self._last_step, self._last_time_s = _stamp(row)
        return deepcopy(dict(row))

    def record_observation(self, *, region_id: str, observation_id: str,
                           simulation_step: int, time_s: float,
                           producer_id: str, evidence_digest: str) -> dict[str, Any]:
        if region_id not in REQUIRED_REGIONS:
            raise ValueError("unknown observation region")
        observation = _text(observation_id, "observation_id")
        if observation in self._observations:
            raise ValueError("duplicate observation_id")
        row = self._new(
            "native_observation", simulation_step, time_s,
            region_id=region_id, observation_id=observation,
            producer_id=_text(producer_id, "producer_id"),
            evidence_digest=_text(evidence_digest, "evidence_digest"))
        self._observations[observation] = row
        return self._accept(row)

    def publish_relay(self, *, message_id: str, simulation_step: int,
                      time_s: float, observation_refs: list[str]) -> dict[str, Any]:
        message = _text(message_id, "message_id")
        if message in self._publications:
            raise ValueError("duplicate relay message_id")
        refs = _refs(observation_refs)
        _selection(refs, self._observations)
        row = self._new("relay_publication", simulation_step, time_s,
                        message_id=message, observation_refs=refs)
        if not all(_before(self._observations[ref], row) for ref in refs):
            raise ValueError("relay must follow its observations")
        self._publications[message] = row
        return self._accept(row)

    def commit_readiness_receipt(self, *, receipt_id: str, simulation_step: int,
                                 time_s: float, observation_refs: list[str],
                                 relay_message_id: str) -> dict[str, Any]:
        refs = _refs(observation_refs)
        _selection(refs, self._observations)
        relay_id = _text(relay_message_id, "relay_message_id")
        publication = self._publications.get(relay_id)
        if publication is None:
            raise ValueError("receipt requires a recorded relay")
        row = self._new(
            "readiness_receipt", simulation_step, time_s,
            receipt_id=_text(receipt_id, "receipt_id"), observation_refs=refs,
            relay_message_id=relay_id, relay_digest=_digest(publication),
            observation_digests={ref: _digest(self._observations[ref]) for ref in refs})
        row["receipt_digest"] = _digest(row)
        evaluate_readiness_receipt_v2(
            episode_id=self.episode_id, receipt=row, observations=self._observations,
            relay_publications=list(self._publications.values()))
        self._receipt = row
        return self._accept(row)

    def snapshot(self) -> dict[str, Any]:
        return deepcopy({
            "schema": SCHEMA, "episode_id": self.episode_id,
            "observations": self._observations,
            "relay_publications": list(self._publications.values()),
            "receipt": self._receipt, "last_step": self._last_step,
            "last_time_s": self._last_time_s,
        })
