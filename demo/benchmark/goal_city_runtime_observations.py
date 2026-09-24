"""Opt-in public City state feed over the episode's metered uplink.

This is trusted scene telemetry, not a robot camera/FOV observation. Only an
allowlisted projection crosses the participant boundary. Private assignments,
person-to-assembly bindings, controller paths and author plans never cross it.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .communication import CommunicationRuntime
from .goal_communication_tool import communication_message_observation
from .provenance import canonical_sha256

SCHEMA = "eai.benchmark.city_runtime_observation"
VERSION = 1


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _time(value: Any, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def project_city_observation(row: Mapping[str, Any], public_task: Mapping[str, Any], *,
                             native_episode_id: str, native_source_id: str,
                             clock_epoch_s: float, recipient_service_id: str) -> dict[str, Any]:
    """Explicit public fields only; never copy arbitrary nested source objects."""
    epoch = _time(clock_epoch_s, "clock_epoch_s")
    if row.get("episode_id") != native_episode_id or row.get("source_id") != native_source_id:
        raise ValueError("City observation source/episode binding mismatch")
    captured = _time(row.get("captured_at_s"), "captured_at_s")
    delivered = _time(row.get("delivered_at_s"), "delivered_at_s")
    expires = _time(row.get("valid_until_s"), "valid_until_s")
    if not epoch <= captured <= delivered < expires:
        raise ValueError("City observation has invalid episode-relative timestamps")
    entities = row.get("entities")
    if not isinstance(entities, list):
        raise ValueError("City observation entities must be a list")
    projected, seen = [], set()
    for entity in entities:
        if not isinstance(entity, Mapping):
            raise ValueError("City observation entity must be an object")
        identity = _text(entity.get("id"), "entity.id")
        kind = entity.get("kind")
        if identity in seen or kind not in {"person", "vehicle"}:
            raise ValueError("City observation has duplicate or unsupported entity")
        seen.add(identity)
        if (entity.get("episode_id") != native_episode_id
                or entity.get("source_id") != native_source_id):
            raise ValueError("City observation entity source/episode mismatch")
        point = entity.get("position_xyz")
        if (not isinstance(point, (list, tuple)) or len(point) != 3
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) for v in point)):
            raise ValueError("City entity position must contain three finite meters")
        item = {"id": identity, "kind": kind, "position_m": [float(v) for v in point]}
        if kind == "vehicle":
            for key in ("following_incident", "is_accident_vehicle"):
                if type(entity.get(key)) is not bool:
                    raise ValueError(f"City entity {key} must be a bool")
                item[key] = entity[key]
        projected.append(item)
    result = {
        "schema": SCHEMA, "version": VERSION, "kind": "city_runtime_observation",
        "observation_ref": _text(row.get("id"), "observation.id"),
        "captured_at_s": captured - epoch,
        "source_delivered_at_s": delivered - epoch,
        "valid_until_s": expires - epoch,
        "measurement_scope": "trusted_scene_state_not_robot_field_of_view",
        "entities": sorted(projected, key=lambda item: (item["kind"], item["id"])),
        "assembly_candidates": [{"region_id": region["id"],
                                 "safety_status": "unverified",
                                 "capacity_status": "unverified"}
                                for region in sorted(public_task["public_world"]["regions"],
                                                     key=lambda region: region["id"])
                                if region["kind"] == "assembly_candidate"],
        "recipient_service_id": _text(recipient_service_id, "recipient_service_id"),
    }
    if "assembly_affordance" in row:
        from .goal_city_assembly import project_assembly_affordances

        result["assembly_affordance"] = project_assembly_affordances(
            row["assembly_affordance"], public_task,
            native_episode_id=native_episode_id, native_source_id=native_source_id,
            clock_epoch_s=epoch, captured_at_s=captured,
            observed_person_ids={item["id"] for item in projected if item["kind"] == "person"})
        result["version"] = 2
        result["valid_until_s"] = min(
            result["valid_until_s"], result["assembly_affordance"]["valid_until_s"])
    return result


class CityRuntimeObservationReader:
    """Use a native delivered snapshot, reusing same-tick capture identities."""

    def __init__(self, ctx: Mapping[str, Any], *, assembly_reader: Any = None,
                 assembly_profile: str | None = None):
        self.ctx = ctx
        if assembly_reader is not None and not callable(assembly_reader):
            raise ValueError("assembly reader must be a trusted callable")
        if assembly_reader is None and assembly_profile is not None:
            raise ValueError("assembly profile requires a trusted assembly reader")
        if assembly_reader is not None:
            from demo.city_benchmark.guide_admission import (
                REQUESTED_TARGET_PROFILE,
                REQUESTED_TARGET_PROFILES,
            )

            assembly_profile = assembly_profile or REQUESTED_TARGET_PROFILE
            if assembly_profile not in REQUESTED_TARGET_PROFILES:
                raise ValueError("unsupported City assembly execution profile")
        self.assembly_reader = assembly_reader
        self.assembly_profile = assembly_profile

    def __call__(self) -> Mapping[str, Any]:
        runner, city = self.ctx["runner"], self.ctx["city"]
        ref = getattr(runner, "_observation_ref", None) or self.ctx["guidance_observation"]
        row = runner.host.observation(ref)
        if row is None or row.get("captured_at_s") != float(city.elapsed):
            ref = runner.observe()
            row = runner.host.observation(ref)
        if not isinstance(row, Mapping):
            raise ValueError("native City observation was not delivered to its source host")
        if float(row["delivered_at_s"]) > float(city.elapsed):
            raise ValueError("native City observation is from the future")
        result = deepcopy(dict(row))
        if self.assembly_reader is not None:
            result["assembly_affordance"] = self.assembly_reader()
        return result


class GoalCityRuntimeIntake:
    """Initial and post-skill snapshots, delivered only through shared transport.

    The host opts into this feed by supplying its CommunicationRuntime. A
    participant may ignore it; this is not a new mandatory action or plan rail.
    Source freshness never extends on publication or delivery. No automatic
    refresh occurs when congestion makes a snapshot expire.
    """

    def __init__(self, public_task: Mapping[str, Any], *, episode_id: str,
                 runtime: CommunicationRuntime, reader: Any, native_episode_id: str,
                 native_source_id: str, clock_epoch_s: float,
                 recipient_service_id: str):
        if not isinstance(runtime, CommunicationRuntime) or not callable(reader):
            raise ValueError("City intake requires a trusted reader and CommunicationRuntime")
        if "backhaul_uplink" not in runtime.report()["contract"].get("pools", {}):
            raise ValueError("City intake requires a declared shared backhaul_uplink pool")
        self.episode_id = _text(episode_id, "episode_id")
        self.public_task_sha256 = canonical_sha256(public_task)
        self.runtime = runtime
        self._public = deepcopy(dict(public_task))
        if self._public.get("mission", {}).get("scene_key") != "city":
            raise ValueError("City intake requires a City public task")
        self._horizon_s = _time(self._public["budgets"]["simulation_s"], "simulation budget")
        self._reader = reader
        self._binding = dict(native_episode_id=_text(native_episode_id, "native_episode_id"),
                             native_source_id=_text(native_source_id, "native_source_id"),
                             clock_epoch_s=_time(clock_epoch_s, "clock_epoch_s"),
                             recipient_service_id=recipient_service_id)
        self._started = False
        self._samples: list[dict[str, Any]] = []
        self._emitted: set[str] = set()
        self._source_refs: set[str] = set()
        self._consumed: dict[str, tuple[str, dict[str, Any]]] = {}

    def profile(self) -> dict[str, Any]:
        return {"schema": SCHEMA + "_profile", "version": VERSION,
                "public_task_sha256": self.public_task_sha256,
                "capture_boundaries": ["initial", "skill_completed"],
                "measurement_scope": "trusted_scene_state_not_robot_field_of_view",
                "pool": "backhaul_uplink", "direction": "uplink",
                "source_kind": "scene_telemetry", "robot_access_attribution": None,
                "assembly_profile": getattr(self._reader, "assembly_profile", None)}

    def _now(self, host: Any) -> float:
        report = host.trusted_report()["session"]
        if report["episode_id"] != self.episode_id:
            raise ValueError("City intake host episode mismatch")
        return _time(report["current_time_s"], "host time")

    def _capture(self, host: Any, boundary: str, *, outcome: Any = None) -> None:
        now = self._now(host)
        native = self._reader()
        projected = project_city_observation(native, self._public, **self._binding)
        if projected["source_delivered_at_s"] > now + 1e-8:
            raise ValueError("City source observation postdates the host clock")
        ref = projected["observation_ref"]
        if outcome is not None:
            from .goal_city_assembly import _reasons

            if outcome.get("status") != "failed":
                raise ValueError("City admission outcome must be a failed execution")
            projected["skill_outcome"] = {
                "task_id": _text(outcome.get("task_id"), "outcome.task_id"),
                "action_id": _text(outcome.get("action_id"), "outcome.action_id"),
                "status": "failed", "reason_codes": _reasons(outcome),
                "evidence_ref": _text(outcome.get("evidence_ref"), "outcome.evidence_ref")}
            projected["version"] = 2
            ref = f"{ref}:outcome:{outcome['action_id']}"
        if ref in self._source_refs:
            return
        message_id = f"{self.episode_id}:city-runtime:{len(self._samples)}"
        publication = self.runtime.publish(
            message_id=message_id, producer_id="city_runtime", recipients=["cloud"],
            channel="observation", estimate=projected,
            published_at_s=now, valid_until_s=min(projected["valid_until_s"], self._horizon_s),
            pool="backhaul_uplink", direction="uplink",
            information_id="city:runtime_entities")
        self._samples.append({"boundary": boundary, "message_id": message_id,
                              "native_observation": deepcopy(dict(native)),
                              "public_projection": projected,
                              "published_at_s": now,
                              "effective_valid_until_s": publication["recipients"]["cloud"].get(
                                  "transport_valid_until_s",
                                  min(projected["valid_until_s"], self._horizon_s))})
        self._source_refs.add(ref)

    def advance(self, *, host: Any) -> None:
        now = self._now(host)
        if not self._started:
            self._capture(host, "initial")
            self._started = True
        self.runtime.advance(now)
        own = {sample["message_id"]: sample for sample in self._samples}
        for payload in self.runtime.public_messages(recipient="cloud"):
            identity = payload["message_id"]
            if identity not in own or identity in self._emitted:
                continue
            host.emit("observation", now, communication_message_observation(
                payload, recipient_id="cloud",
                effective_valid_until_s=own[identity]["effective_valid_until_s"]))
            self._emitted.add(identity)

    def after_skill(self, task: Mapping[str, Any], result: Mapping[str, Any], *, host: Any) -> None:
        del task
        self._capture(host, "skill_completed", outcome=result.get("city_admission_outcome"))
        self.advance(host=host)

    def has_pending_work(self) -> bool:
        messages = self.runtime.report()["messages"]
        return any(messages[sample["message_id"]]["recipients"]["cloud"]["status"]
                   in {"queued", "in_transit"} for sample in self._samples)

    def next_wakeup_s(self) -> float | None:
        return self.runtime.next_event_time_s() if self.has_pending_work() else None

    def owns_message(self, message_id: Any) -> bool:
        return any(sample["message_id"] == message_id for sample in self._samples)

    def can_consume(self, message_id: str, *, time_s: float) -> bool:
        if message_id not in self._emitted or time_s != self.runtime.time_s:
            return False
        return any(row["message_id"] == message_id
                   for row in self.runtime.public_messages(recipient="cloud"))

    def consume(self, message_id: str, *, action_id: str, time_s: float) -> dict[str, Any]:
        previous = self._consumed.get(action_id)
        if previous is not None:
            if previous[0] != message_id:
                raise ValueError("City observation action identity collision")
            return deepcopy(previous[1])
        if not self.can_consume(message_id, time_s=time_s):
            raise ValueError("City observation is not delivered and fresh")
        receipt = self.runtime.consume(message_id=message_id, recipient="cloud",
                                       consumer_id=action_id, time_s=time_s)
        self._consumed[action_id] = (message_id, deepcopy(receipt))
        return receipt

    def report(self) -> dict[str, Any]:
        return {"schema": SCHEMA + "_report", "version": VERSION,
                "episode_id": self.episode_id, "public_task_sha256": self.public_task_sha256,
                "profile": self.profile(),
                "source_binding": deepcopy(self._binding),
                "samples": deepcopy(self._samples), "emitted_message_ids": sorted(self._emitted),
                "consumed_action_ids": sorted(self._consumed),
                "physical_source_authenticated": False,
                "execution_validated": False, "business_outcomes_validated": False,
                "scoreable": False}
