"""Episode-owned Restaurant readiness service boundary.

``relay`` only publishes an event-bus message.  This backend owns the
business receipt and will issue it only after both zone observations belong to
the same episode and have been recorded on native simulation steps.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping


SCHEMA = "eai.restaurant.readiness_service.v1"
REQUIRED_REGIONS = ("kitchen_service", "dining_service")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _step(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _time(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{label} must be a non-negative number")
    return float(value)


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique_refs(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    refs = [_text(item, f"{label} item") for item in value]
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} must not contain duplicates")
    return refs


class RestaurantReadinessBackend:
    """Collect native observation facts and issue one episode-bound receipt."""

    def __init__(self, *, episode_id: str):
        self.episode_id = _text(episode_id, "episode_id")
        self._observations: dict[str, dict[str, Any]] = {}
        self._last_step = -1
        self._last_time_s = -1.0
        self._relay_publications: list[dict[str, Any]] = []
        self._receipt: dict[str, Any] | None = None

    def _check_step(self, simulation_step: int, time_s: float) -> tuple[int, float]:
        step = _step(simulation_step, "simulation_step")
        timestamp = _time(time_s, "time_s")
        if step <= self._last_step or timestamp <= self._last_time_s:
            raise ValueError("native step and time must increase monotonically")
        self._last_step = step
        self._last_time_s = timestamp
        return step, timestamp

    def record_observation(self, *, region_id: str, observation_id: str,
                           simulation_step: int, time_s: float,
                           producer_id: str, evidence_digest: str) -> dict[str, Any]:
        region = _text(region_id, "region_id")
        if region not in REQUIRED_REGIONS:
            raise ValueError("Restaurant readiness observation must target a required service region")
        observation = _text(observation_id, "observation_id")
        producer = _text(producer_id, "producer_id")
        digest = _text(evidence_digest, "evidence_digest")
        step, timestamp = self._check_step(simulation_step, time_s)
        if region in self._observations:
            raise ValueError(f"observation already recorded for {region}")
        row = {
            "schema": SCHEMA,
            "episode_id": self.episode_id,
            "kind": "native_observation",
            "region_id": region,
            "observation_id": observation,
            "producer_id": producer,
            "simulation_step": step,
            "time_s": timestamp,
            "evidence_digest": digest,
        }
        self._observations[region] = row
        return deepcopy(row)

    def publish_relay(self, *, message_id: str, simulation_step: int,
                      time_s: float, observation_refs: list[str]) -> dict[str, Any]:
        message = _text(message_id, "message_id")
        refs = sorted(_unique_refs(observation_refs, "observation_refs"))
        observed = {row["observation_id"] for row in self._observations.values()}
        if set(refs) != observed or len(refs) != len(observed) or len(observed) != len(REQUIRED_REGIONS):
            raise ValueError("relay publication requires both recorded observations")
        step, timestamp = self._check_step(simulation_step, time_s)
        publication = {
            "schema": SCHEMA,
            "episode_id": self.episode_id,
            "kind": "relay_publication",
            "message_id": message,
            "observation_refs": refs,
            "simulation_step": step,
            "time_s": timestamp,
        }
        self._relay_publications.append(publication)
        return deepcopy(publication)

    def commit_readiness_receipt(self, *, receipt_id: str, simulation_step: int,
                                 time_s: float, observation_refs: list[str]) -> dict[str, Any]:
        if self._receipt is not None:
            raise ValueError("Restaurant readiness receipt already committed")
        receipt = _text(receipt_id, "receipt_id")
        refs = _unique_refs(observation_refs, "observation_refs")
        step = _step(simulation_step, "simulation_step")
        timestamp = _time(time_s, "time_s")
        if step <= self._last_step or timestamp <= self._last_time_s:
            raise ValueError("native step and time must increase monotonically")
        observations = list(self._observations.values())
        by_id = {row["observation_id"]: row for row in observations}
        if set(by_id) != set(refs):
            raise ValueError("readiness receipt requires both kitchen and dining observations")
        if set(row["region_id"] for row in observations) != set(REQUIRED_REGIONS):
            raise ValueError("readiness receipt requires both kitchen and dining observations")
        if any(row["simulation_step"] >= step or row["time_s"] >= timestamp for row in observations):
            raise ValueError("readiness receipt must be committed after both observations")
        if not any(
                set(publication["observation_refs"]) == set(refs)
                and publication["simulation_step"] < step
                and publication["time_s"] < timestamp
                for publication in self._relay_publications):
            raise ValueError("readiness receipt requires a prior relay publication")
        self._last_step = step
        self._last_time_s = timestamp
        self._receipt = {
            "schema": SCHEMA,
            "episode_id": self.episode_id,
            "kind": "readiness_receipt",
            "receipt_id": receipt,
            "observation_refs": sorted(refs),
            "simulation_step": step,
            "time_s": timestamp,
            "receipt_digest": _digest({"episode_id": self.episode_id, "receipt_id": receipt,
                                        "observation_refs": sorted(refs), "simulation_step": step}),
        }
        return deepcopy(self._receipt)

    def snapshot(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "observations": deepcopy(self._observations),
            "relay_publications": deepcopy(self._relay_publications),
            "receipt": deepcopy(self._receipt),
            "last_step": self._last_step,
            "last_time_s": self._last_time_s,
        }


def evaluate_readiness_receipt(*, episode_id: str, receipt: Mapping[str, Any],
                               observations: Mapping[str, Mapping[str, Any]],
                               relay_publications: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate the complete business causal chain from archived evidence."""
    if receipt.get("schema") != SCHEMA or receipt.get("kind") != "readiness_receipt":
        raise ValueError("invalid Restaurant readiness receipt")
    if receipt.get("episode_id") != episode_id:
        raise ValueError("readiness receipt episode_id mismatch")
    _text(receipt.get("receipt_id"), "receipt_id")
    receipt_refs = _unique_refs(receipt.get("observation_refs"), "receipt observation_refs")
    refs = set(receipt_refs)
    if len(refs) != len(REQUIRED_REGIONS) or set(observations) != refs:
        raise ValueError("readiness receipt must bind both required observations")
    receipt_step = _step(receipt.get("simulation_step"), "receipt simulation_step")
    receipt_time = _time(receipt.get("time_s"), "receipt time_s")
    expected_digest = _digest({"episode_id": episode_id,
                               "receipt_id": receipt.get("receipt_id"),
                               "observation_refs": sorted(refs),
                               "simulation_step": receipt_step})
    if receipt.get("receipt_digest") != expected_digest:
        raise ValueError("readiness receipt digest mismatch")
    observation_steps: list[int] = []
    observation_times: list[float] = []
    for observation_id, row in observations.items():
        if row.get("schema") != SCHEMA or row.get("kind") != "native_observation":
            raise ValueError("invalid Restaurant readiness observation")
        if row.get("episode_id") != episode_id:
            raise ValueError("observation episode_id mismatch")
        if row.get("observation_id") != observation_id:
            raise ValueError("observation archive key does not match observation_id")
        _text(row.get("producer_id"), "observation producer_id")
        _text(row.get("evidence_digest"), "observation evidence_digest")
        step = _step(row.get("simulation_step"), "observation simulation_step")
        timestamp = _time(row.get("time_s"), "observation time_s")
        observation_steps.append(step)
        observation_times.append(timestamp)
        if step >= receipt_step or timestamp >= receipt_time:
            raise ValueError("receipt is not causally after every observation")
    if {row.get("region_id") for row in observations.values()} != set(REQUIRED_REGIONS):
        raise ValueError("readiness receipt must bind both required service regions")
    if not isinstance(relay_publications, list) or len(relay_publications) != 1:
        raise ValueError("exactly one relay publication must be archived")
    publication = relay_publications[0]
    if not isinstance(publication, Mapping):
        raise ValueError("invalid Restaurant relay publication")
    if (publication.get("schema") != SCHEMA
            or publication.get("kind") != "relay_publication"
            or publication.get("episode_id") != episode_id):
        raise ValueError("invalid Restaurant relay publication")
    _text(publication.get("message_id"), "relay message_id")
    relay_refs = _unique_refs(publication.get("observation_refs"),
                              "relay observation_refs")
    if relay_refs != receipt_refs:
        raise ValueError("relay publication must bind the exact receipt observations")
    relay_step = _step(publication.get("simulation_step"), "relay simulation_step")
    relay_time = _time(publication.get("time_s"), "relay time_s")
    if (any(step >= relay_step for step in observation_steps)
            or any(timestamp >= relay_time for timestamp in observation_times)
            or relay_step >= receipt_step or relay_time >= receipt_time):
        raise ValueError("relay publication must be after observations and before receipt")
    return {"validated": True, "episode_id": episode_id, "outcome": "readiness_receipt_committed",
            "observation_count": len(observations), "relay_publication_count": 1,
            "receipt_id": receipt["receipt_id"]}


__all__ = ["RestaurantReadinessBackend", "evaluate_readiness_receipt", "REQUIRED_REGIONS", "SCHEMA"]
