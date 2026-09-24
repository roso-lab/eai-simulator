"""Fail-closed live service witness entry point for the City benchmark.

The existing ``demo.city_benchmark.scene`` runtime exposes scene animation and USD pose
updates, but it does not expose an episode-owned service witness.  This module
keeps that boundary explicit: a live session can only bind to a runtime that
publishes a trusted ``city_service_witness`` host.  Reference models and
participant supplied acknowledgements are deliberately not accepted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import inspect
import math
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

from .episode_adapter import CityEpisodeEvidenceHost
from .service_backend import CityServiceBackend, _time


_HOST_METHODS = (
    "as_source", "advance", "deliver_observation", "register_request",
    "record_actor_ack", "record_person_readback", "record_traffic_readback",
    "record_receipt", "record_access_readback", "record_partial_failure",
)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _entities(values: Iterable[str], label: str) -> tuple[str, ...]:
    result = tuple(_text(value, label) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must contain unique nonempty identities")
    return result


@dataclass(frozen=True)
class LiveEvidence:
    """Immutable provenance envelope for one live observation or readback."""

    episode_id: str
    evidence_ref: str
    kind: str
    source_id: str
    entity_ids: tuple[str, ...]
    observed_time_s: float
    status_before: Mapping[str, Any]
    status_after: Mapping[str, Any]

    def __post_init__(self) -> None:
        _text(self.episode_id, "evidence episode id")
        _text(self.evidence_ref, "evidence reference")
        _text(self.kind, "evidence kind")
        source = _text(self.source_id, "evidence source")
        if source.lower() in {"reference", "backend", "participant", "ack"}:
            raise ValueError("live evidence source cannot be a reference, backend or participant")
        object.__setattr__(self, "entity_ids", _entities(self.entity_ids, "evidence entity ids"))
        timestamp = _time(self.observed_time_s)
        object.__setattr__(self, "observed_time_s", timestamp)
        if not isinstance(self.status_before, Mapping) or not isinstance(self.status_after, Mapping):
            raise ValueError("live evidence requires status mappings")
        if not self.status_before or not self.status_after or dict(self.status_before) == dict(self.status_after):
            raise ValueError("live evidence requires an observed status change")

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "evidence_ref": self.evidence_ref,
            "kind": self.kind,
            "source_id": self.source_id,
            "entity_ids": list(self.entity_ids),
            "observed_time_s": self.observed_time_s,
            "status_before": dict(self.status_before),
            "status_after": dict(self.status_after),
        }


@dataclass(frozen=True)
class CityRuntimeBindingAssessment:
    """Machine-readable result of checking a City runtime's live boundary."""

    runtime_type: str
    episode_id: str | None
    ready: bool
    blockers: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_type": self.runtime_type,
            "episode_id": self.episode_id,
            "ready": self.ready,
            "blockers": [dict(blocker) for blocker in self.blockers],
            "evidence_class": "runtime" if self.ready else "unavailable",
            "scoreable": False,
        }


class CityRuntimeWitnessBlocked(RuntimeError):
    """Raised when a simulator runtime cannot provide live witness inputs."""

    def __init__(self, assessment: CityRuntimeBindingAssessment | str):
        self.assessment = assessment if isinstance(assessment, CityRuntimeBindingAssessment) else None
        if isinstance(assessment, CityRuntimeBindingAssessment):
            message = "City live service witness is blocked: " + "; ".join(
                blocker["code"] for blocker in assessment.blockers)
        else:
            message = f"City live service witness is blocked: {assessment}"
        super().__init__(message)


class CityIncidentReceiver:
    """Episode-owned incident endpoint with an independent receipt state."""

    def __init__(self, *, episode_id: str, service_id: str):
        self.episode_id = _text(episode_id, "receiver episode id")
        self.service_id = _text(service_id, "receiver service id")
        self._receipts: dict[str, dict[str, Any]] = {}
        self._payloads: dict[str, dict[str, Any]] = {}

    def receive(self, payload: Mapping[str, Any], *, now_s: float) -> dict[str, Any]:
        request_id = _text(payload.get("request_id"), "incident request id")
        if payload.get("episode_id") != self.episode_id:
            raise ValueError("incident payload belongs to another episode")
        if payload.get("recipient_service_id") != self.service_id:
            raise ValueError("incident payload recipient does not match endpoint")
        refs = payload.get("observation_refs")
        if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)) \
                or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError("incident payload requires unique observation references")
        submitted = _time(payload.get("submitted_time_s"))
        received = _time(now_s)
        if received < submitted:
            raise ValueError("incident receipt cannot predate submission")
        existing = self._receipts.get(request_id)
        if existing is not None:
            if self._payloads[request_id] != dict(payload):
                raise ValueError("incident request identity was reused with a different payload")
            return deepcopy(existing)
        receipt = {
            "request_id": request_id,
            "episode_id": self.episode_id,
            "recipient_service_id": self.service_id,
            "observation_refs": list(refs),
            "received_time_s": received,
            "status": "received",
            "receipt_evidence_ref": f"{self.service_id}:{self.episode_id}:{request_id}:receipt",
            "source_id": f"{self.service_id}.receiver",
        }
        self._receipts[request_id] = receipt
        self._payloads[request_id] = deepcopy(dict(payload))
        return deepcopy(receipt)

    def receipt(self, request_id: str) -> dict[str, Any] | None:
        row = self._receipts.get(str(request_id))
        return deepcopy(row) if row is not None else None


class CityDemoRuntimeWitnessAdapter:
    """Benchmark-owned adapter around the existing ``demo.city_benchmark.scene`` runtime.

    It reads actual poses from the runtime and can drive an actual human
    action when the caller supplies an affected-person binding.  It never
    treats the City's scripted traffic list as a control service: a separate
    vehicle controller callback is required before that capability is marked
    available.  The adapter can therefore be used to produce a concrete
    readiness report while the scene is running without changing ``demo.city_benchmark.scene``.
    """

    def __init__(self, runtime: Any, *, episode_id: str, source_id: str = "isaac.city.runtime",
                 affected_people: Mapping[str, Mapping[str, Any]] | None = None,
                 affected_person_roster: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
                 following_vehicle_roster: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
                 person_controller: Callable[..., Mapping[str, Any]] | None = None,
                 person_readback: Callable[[str], Mapping[str, Any]] | None = None,
                 person_stop: Callable[[str], Mapping[str, Any]] | None = None,
                 vehicle_controller: Any = None, admission: Any = None,
                 vehicle_stop: Callable[[str], Mapping[str, Any]] | None = None,
                 vehicle_pose_reader: Callable[[str], Iterable[float]] | None = None,
                 hazard_readback: Callable[[str, float], Mapping[str, Any]] | None = None,
                 access_readback: Callable[[Mapping[str, Any], float, float], Mapping[str, Any]] | None = None,
                 assignment: Any = None, incident_receiver: CityIncidentReceiver | None = None,
                 scene_query_support: bool | None = None,
                 clearance_query_provenance: str | None = None,
                 physx_readback_suppressed: bool | None = None,
                 current_gpu_tensor_clearance: bool = False):
        self.runtime = runtime
        self.episode_id = _text(episode_id, "episode id")
        self.source_id = _text(source_id, "runtime source id")
        self.affected_people = {str(key): dict(value) for key, value in (affected_people or {}).items()}
        self.affected_person_roster = affected_person_roster
        self.following_vehicle_roster = following_vehicle_roster
        self.person_controller = person_controller
        self.person_readback = person_readback
        self.person_stop = person_stop
        self.vehicle_controller = vehicle_controller
        self.vehicle_stop = vehicle_stop
        self.vehicle_pose_reader = vehicle_pose_reader
        self.hazard_readback = hazard_readback
        self.access_readback = access_readback
        self.admission = admission
        self.assignment = assignment
        self.incident_receiver = incident_receiver
        self.scene_query_support = scene_query_support
        self.clearance_query_provenance = clearance_query_provenance
        self.physx_readback_suppressed = physx_readback_suppressed
        self.current_gpu_tensor_clearance = bool(current_gpu_tensor_clearance)

    @staticmethod
    def scene_query_requirement() -> dict[str, Any]:
        """Configuration contract required before any native clearance query."""
        return {
            "env_cfg_path": "cfg.sim.enable_scene_query_support",
            "required_value": True,
            "required_provenance": "scene_query_result_with_episode_and_timestamp",
            "physx_readback": "suppressed=False or independent_current_gpu_tensor_clearance=True",
            "failure": "empty or disabled scene queries are not clearance evidence",
        }

    def now_s(self) -> float:
        value = getattr(self.runtime, "elapsed", None)
        return _time(value)

    def _people_runtime(self) -> Any:
        people = getattr(self.runtime, "people", None)
        if people is None:
            raise CityRuntimeWitnessBlocked("City runtime has no human pose runtime")
        return people

    @staticmethod
    def _roster(source: Callable[[], Mapping[str, Mapping[str, Any]]] | None,
                *, kind: str, episode_id: str, source_id: str) -> dict[str, dict[str, Any]]:
        if not callable(source):
            raise CityRuntimeWitnessBlocked(f"episode-owned {kind} roster source is missing")
        rows = source()
        if not isinstance(rows, Mapping) or not rows:
            raise CityRuntimeWitnessBlocked(f"episode-owned {kind} roster is empty or invalid")
        result: dict[str, dict[str, Any]] = {}
        for entity_id, row in rows.items():
            entity_id = _text(entity_id, f"{kind} roster id")
            if not isinstance(row, Mapping) or row.get("id", entity_id) != entity_id:
                raise CityRuntimeWitnessBlocked(f"{kind} roster identity mismatch")
            if row.get("kind", kind) != kind:
                raise CityRuntimeWitnessBlocked(f"{kind} roster kind mismatch")
            if row.get("episode_id") != episode_id or row.get("source_id") != source_id:
                raise CityRuntimeWitnessBlocked(f"{kind} roster provenance mismatch")
            result[entity_id] = dict(row)
        return result

    def episode_rosters(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        people = self._roster(self.affected_person_roster, kind="person",
                              episode_id=self.episode_id, source_id=self.source_id)
        vehicles = self._roster(self.following_vehicle_roster, kind="vehicle",
                                episode_id=self.episode_id, source_id=self.source_id)
        actor_ids = set(str(actor_id) for actor_id in getattr(self._people_runtime(), "actor_ids", ()))
        if not set(people) <= actor_ids:
            raise CityRuntimeWitnessBlocked("affected-person roster contains an unknown runtime actor")
        return people, vehicles

    def bind_episode_rosters(self, host: CityEpisodeEvidenceHost) -> None:
        people, vehicles = self.episode_rosters()
        host.bind_affected_person_roster(people)
        host.bind_following_vehicle_roster(vehicles)

    def observe_people(self) -> list[dict[str, Any]]:
        people = self._people_runtime()
        actor_ids = tuple(str(actor_id) for actor_id in getattr(people, "actor_ids", ()))
        roster = None
        if callable(self.affected_person_roster):
            roster, _ = self.episode_rosters()
            actor_ids = tuple(roster)
        rows = []
        for actor_id in actor_ids:
            position = tuple(float(value) for value in people.position(actor_id))
            rows.append({
                "id": actor_id, "kind": "person", "position_xyz": list(position),
                "mode": ((roster or {}).get(actor_id, self.affected_people.get(actor_id, {}))
                         .get("mode", "affected_episode_roster" if roster else "background_path")),
                "source_id": self.source_id, "episode_id": self.episode_id,
            })
        return rows

    def observe_vehicles(self) -> list[dict[str, Any]]:
        if callable(self.following_vehicle_roster):
            _, roster = self.episode_rosters()
            if not callable(self.vehicle_pose_reader):
                raise CityRuntimeWitnessBlocked("following-vehicle pose reader is missing")
            rows = []
            for vehicle_id, metadata in roster.items():
                point = tuple(float(component) for component in self.vehicle_pose_reader(vehicle_id))
                if len(point) != 3 or not all(math.isfinite(component) for component in point):
                    raise ValueError("vehicle pose readback must contain three finite values")
                rows.append({"id": vehicle_id, "kind": "vehicle", "position_xyz": list(point),
                             "following_incident": metadata.get("following_incident") is True,
                             "is_accident_vehicle": metadata.get("is_accident_vehicle") is True,
                             "source_id": self.source_id, "episode_id": self.episode_id})
            return rows
        rows = []
        for index, entry in enumerate(tuple(getattr(self.runtime, "traffic", ()) )):
            if not entry:
                continue
            position_attr = entry[0]
            value = position_attr.Get() if callable(getattr(position_attr, "Get", None)) else position_attr
            point = tuple(float(component) for component in value)
            rows.append({
                "id": f"following_vehicle_{index:02d}", "kind": "vehicle",
                "position_xyz": list(point), "following_incident": True,
                "is_accident_vehicle": False, "source_id": self.source_id,
                "episode_id": self.episode_id,
            })
        return rows

    def _requested_target_profile_enabled(self) -> bool:
        runtime = getattr(self, "runtime", None)
        service = getattr(runtime, "_episode_service", None)
        profile = getattr(service, "guidance_profile", None)
        if profile is None:
            binding_factory = getattr(runtime, "city_service_native_binding", None)
            if callable(binding_factory):
                binding = binding_factory()
                profile = binding.get("guidance_profile") if isinstance(binding, Mapping) else None
        return profile == "requested_target_v1"

    def _call_with_optional_target(self, callback: Callable[..., Any], person_id: str,
                                   assembly_region_id: str | None) -> Any:
        if assembly_region_id is None:
            return callback(person_id)
        try:
            inspect.signature(callback).bind(person_id, target_region_id=assembly_region_id)
        except (TypeError, ValueError):
            if self._requested_target_profile_enabled():
                from demo.city_benchmark.guide_admission import CityGuideAdmissionError

                raise CityGuideAdmissionError(
                    ["route_proposal_missing"], infrastructure_failure=True,
                    report={"profile": "requested_target_v1", "person_id": person_id,
                            "target_region_id": assembly_region_id,
                            "controller_target_unsupported": True})
            return callback(person_id)
        return callback(person_id, target_region_id=assembly_region_id)

    def guide_person(self, person_id: str, *, target_region_id: str | None = None) -> dict[str, Any]:
        person_id = _text(person_id, "affected person id")
        people = self._people_runtime()
        roster = None
        if callable(self.affected_person_roster):
            roster, _ = self.episode_rosters()
        specs = roster if roster is not None else self.affected_people
        if person_id not in specs:
            raise CityRuntimeWitnessBlocked(
                "affected-person binding is required; demo.city_benchmark.scene background actors are not service targets")
        for method in ("play_action", "set_path_speed"):
            if not callable(getattr(people, method, None)):
                raise CityRuntimeWitnessBlocked(f"human runtime lacks {method} service action")
        spec = specs[person_id]
        if callable(self.person_controller):
            result = self._call_with_optional_target(
                self.person_controller, person_id, target_region_id)
            if not isinstance(result, Mapping):
                raise CityRuntimeWitnessBlocked("person controller returned no endpoint acknowledgement")
            return dict(result)
        motion = _text(spec.get("motion", "jog"), "human service motion")
        people.play_action(person_id, motion, loop=True, playback_speed=1.0)
        people.set_path_speed(person_id, float(spec["speed_mps"]))
        return {
            "entity_id": person_id, "status_before": "awaiting_guidance",
            "status_after": "guidance_applied", "position_xyz": list(people.position(person_id)),
            "source_id": self.source_id, "episode_id": self.episode_id,
        }

    def submit_guidance(self, witness: CityLiveEpisodeWitness, *, agent_id: str,
                        region_id: str, assembly_region_id: str,
                        person_ids: tuple[str, ...],
                        observation_refs: tuple[str, ...]) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        """Submit through the service boundary, then drive actual human actors.

        The endpoint acknowledgement is recorded only after the USD human
        runtime accepts the action.  Arrival still requires a later pose
        capture and a separate ``record_person_readback`` call.
        """
        request = witness.guide_people(
            agent_id=agent_id, region_id=region_id, assembly_region_id=assembly_region_id,
            person_ids=person_ids, observation_refs=observation_refs)
        actions = []
        attempted = []
        for person_id in person_ids:
            attempted.append(person_id)
            stage = "controller"
            try:
                action = self._call_with_optional_target(
                    self.guide_person, person_id, assembly_region_id)
                stage = "ack"
                if (not isinstance(action, Mapping) or action.get("entity_id") != person_id
                        or action.get("episode_id") != self.episode_id
                        or action.get("source_id") != self.source_id
                        or action.get("status_after") != "guidance_applied"
                        or action.get("assembly_region_id", assembly_region_id) != assembly_region_id):
                    raise CityRuntimeWitnessBlocked("person controller acknowledgement identity mismatch")
                ack = LiveEvidence(
                    episode_id=self.episode_id,
                    evidence_ref=f"{self.source_id}:{request['request_id']}:{person_id}:ack",
                    kind="service_ack", source_id=self.source_id,
                    entity_ids=(str(person_id),), observed_time_s=self.now_s(),
                    status_before={"status": action["status_before"]},
                    status_after={"status": action["status_after"]},
                )
                witness.record_actor_ack(request["request_id"], str(person_id),
                                         time_s=self.now_s(), evidence=ack)
                actions.append({"action": action, "evidence": ack.as_dict()})
            except Exception as exc:
                self._fail_batch(witness, request["request_id"], tuple(attempted), stage,
                                 stop=self.person_stop, identity="entity_id", stopped="paused")
                from demo.city_benchmark.guide_admission import CityGuideAdmissionError
                if isinstance(exc, CityGuideAdmissionError):
                    raise
                raise CityRuntimeWitnessBlocked(
                    f"guidance request {request['request_id']} partially failed at {stage}") from exc
        return request, tuple(actions)

    def submit_traffic(self, witness: CityLiveEpisodeWitness, *, agent_id: str,
                       region_id: str, vehicle_ids: tuple[str, ...], mode: str,
                       observation_refs: tuple[str, ...]) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        request = witness.control_following_traffic(
            agent_id=agent_id, region_id=region_id, vehicle_ids=vehicle_ids,
            mode=mode, observation_refs=observation_refs)
        actions = []
        attempted = []
        for vehicle_id in vehicle_ids:
            attempted.append(vehicle_id)
            stage = "controller"
            try:
                result = self.control_vehicle(vehicle_id, mode)
                stage = "ack"
                if (not isinstance(result, Mapping) or result.get("vehicle_id") != vehicle_id
                        or result.get("episode_id") != self.episode_id
                        or result.get("source_id") != self.source_id
                        or result.get("status_after") != mode):
                    raise CityRuntimeWitnessBlocked("vehicle controller acknowledgement identity mismatch")
                before = _text(result.get("status_before", "awaiting_control"), "vehicle status before")
                after = _text(result.get("status_after"), "vehicle status after")
                observed = _time(result.get("observed_time_s", self.now_s()))
                ack = LiveEvidence(
                    episode_id=self.episode_id,
                    evidence_ref=_text(result.get("evidence_ref"), "vehicle acknowledgement reference"),
                    kind="service_ack", source_id=self.source_id,
                    entity_ids=(str(vehicle_id),), observed_time_s=observed,
                    status_before={"status": before}, status_after={"status": after})
                witness.record_actor_ack(request["request_id"], str(vehicle_id), time_s=observed,
                                         applied_mode=mode, evidence=ack)
                actions.append({"vehicle_id": vehicle_id, "action": dict(result), "evidence": ack.as_dict()})
            except Exception as exc:
                self._fail_batch(witness, request["request_id"], tuple(attempted), stage,
                                 stop=self.vehicle_stop, identity="vehicle_id", stopped="hold")
                raise CityRuntimeWitnessBlocked(
                    f"traffic request {request['request_id']} partially failed at {stage}") from exc
        return request, tuple(actions)

    def _fail_batch(self, witness: CityLiveEpisodeWitness, request_id: str,
                    attempted: tuple[str, ...], stage: str, *, stop: Any,
                    identity: str, stopped: str) -> None:
        stops = []
        for actor_id in reversed(attempted):
            row = {"id": actor_id, "status": "stop_unconfirmed"}
            try:
                if not callable(stop):
                    raise CityRuntimeWitnessBlocked("native stop endpoint unavailable")
                receipt = stop(actor_id)
                if (not isinstance(receipt, Mapping) or receipt.get(identity) != actor_id
                        or receipt.get("episode_id") != self.episode_id
                        or receipt.get("source_id") != self.source_id
                        or receipt.get("status_after") != stopped):
                    raise CityRuntimeWitnessBlocked("native stop acknowledgement identity mismatch")
                position = tuple(float(value) for value in receipt.get("position_xyz", ()))
                if len(position) != 3 or not all(math.isfinite(value) for value in position):
                    raise CityRuntimeWitnessBlocked("native stop position readback is missing")
                observed = _time(receipt.get("observed_time_s"))
                evidence = LiveEvidence(
                    episode_id=self.episode_id,
                    evidence_ref=_text(receipt.get("evidence_ref"), "native stop evidence"),
                    kind="service_stop", source_id=self.source_id,
                    entity_ids=(actor_id,), observed_time_s=observed,
                    status_before={"status": str(receipt.get("status_before", "unknown")),
                                   "batch_status": "stop_unconfirmed"},
                    status_after={"status": stopped, "batch_status": "stopped"})
                witness._accept(evidence, "service_stop", (actor_id,))
                row = {"id": actor_id, "status": "stopped",
                       "time_s": observed, "evidence_ref": evidence.evidence_ref,
                       "position_xyz": list(position)}
            except Exception:
                pass
            stops.append(row)
        witness.record_partial_failure(request_id, attempted_ids=attempted,
                                       failed_actor_id=attempted[-1],
                                       failure_stage=stage, stops=tuple(stops))

    def capture_person_readback(self, person_id: str, *, previous_position: Iterable[float],
                                status_before: str, arrived: bool,
                                safe_route_and_assembly: bool = False) -> dict[str, Any]:
        """Capture a pose-backed readback without certifying arrival by itself."""
        person_id = _text(person_id, "person id")
        current = tuple(float(value) for value in self._people_runtime().position(person_id))
        native_result = None
        if callable(self.person_readback):
            native_result = self.person_readback(person_id)
            if not isinstance(native_result, Mapping):
                raise CityRuntimeWitnessBlocked("person world source returned no readback")
            if type(native_result.get("arrived")) is not bool or \
                    type(native_result.get("safe_route_and_assembly")) is not bool:
                raise ValueError("person world readback requires explicit host booleans")
            arrived = native_result["arrived"]
            safe_route_and_assembly = native_result["safe_route_and_assembly"]
        if type(arrived) is not bool or type(safe_route_and_assembly) is not bool:
            raise ValueError("arrival and route safety require explicit host booleans")
        if len(current) != 3 or not all(math.isfinite(value) for value in current):
            raise ValueError("current person pose must contain three finite values")
        previous = tuple(float(value) for value in previous_position)
        if len(previous) != 3 or not all(math.isfinite(value) for value in previous):
            raise ValueError("previous person pose must contain three finite values")
        trajectory_evidence = LiveEvidence(
            episode_id=self.episode_id,
            evidence_ref=_text(native_result.get("trajectory_evidence_ref"),
                               "person trajectory evidence reference") if native_result is not None else
            f"{self.source_id}:{person_id}:trajectory:{int(self.now_s() * 1000):09d}",
            kind="person_world_readback", source_id=self.source_id,
            entity_ids=(person_id,), observed_time_s=self.now_s(),
            status_before={"status": _text(status_before, "person status before")},
            status_after={"status": "arrived" if arrived else "en_route"},
        )
        safety_evidence = LiveEvidence(
            episode_id=self.episode_id,
            evidence_ref=_text(native_result.get("safety_evidence_ref"),
                               "person safety evidence reference") if native_result is not None else
            f"{self.source_id}:{person_id}:safety:{int(self.now_s() * 1000):09d}",
            kind="person_world_readback", source_id=self.source_id,
            entity_ids=(person_id,), observed_time_s=self.now_s(),
            status_before={"status": "unsafe"},
            status_after={"status": "safe" if safe_route_and_assembly else "unknown"},
        )
        return {
            "person_id": person_id, "position_before": list(previous),
            "position_after": list(current), "measured_trajectory": current != previous,
            "arrived_at_assembly": bool(arrived),
            "safe_route_and_assembly": bool(safe_route_and_assembly),
            "native_readback": dict(native_result) if native_result is not None else None,
            "evidence": trajectory_evidence, "trajectory_evidence": trajectory_evidence,
            "safety_evidence": safety_evidence,
        }

    def bounded_person_trace(self, person_id: str, *, duration_s: float,
                             dt_s: float = 0.1) -> tuple[dict[str, Any], ...]:
        """Step the already-running City runtime for a bounded pose trace."""
        duration = _time(duration_s)
        dt = _time(dt_s)
        if duration <= 0 or dt <= 0:
            raise ValueError("bounded person trace requires positive duration and dt")
        step = getattr(self.runtime, "step", None)
        if not callable(step):
            raise CityRuntimeWitnessBlocked("City runtime does not expose a step(dt=...) hook")
        person_id = _text(person_id, "person id")
        self._people_runtime().position(person_id)
        trace: list[dict[str, Any]] = []
        elapsed = 0.0
        while elapsed < duration:
            delta = min(dt, duration - elapsed)
            step(dt=delta)
            elapsed += delta
            trace.append({
                "episode_id": self.episode_id, "source_id": self.source_id,
                "person_id": person_id, "time_s": self.now_s(),
                "position_xyz": list(self._people_runtime().position(person_id)),
            })
        return tuple(trace)

    def capture_vehicle_readback(self, vehicle_id: str, *, previous_position: Iterable[float],
                                 status_before: str, trajectory_check_time_s: float | None = None,
                                 no_hazard_ingress: bool = False,
                                 rescue_access_preserved: bool = False) -> dict[str, Any]:
        if not callable(self.vehicle_pose_reader):
            raise CityRuntimeWitnessBlocked("following-vehicle pose reader is missing")
        current = tuple(float(value) for value in self.vehicle_pose_reader(_text(vehicle_id, "vehicle id")))
        previous = tuple(float(value) for value in previous_position)
        if len(current) != 3 or len(previous) != 3 or not all(math.isfinite(v) for v in (*current, *previous)):
            raise ValueError("vehicle readback positions must contain finite triples")
        now = self.now_s() if trajectory_check_time_s is None else _time(trajectory_check_time_s)
        hazard_result = None
        if callable(self.hazard_readback):
            hazard_result = self.hazard_readback(str(vehicle_id), now)
            if not isinstance(hazard_result, Mapping):
                raise CityRuntimeWitnessBlocked("vehicle hazard source returned no readback")
            if type(hazard_result.get("no_hazard_ingress")) is not bool or \
                    type(hazard_result.get("rescue_access_preserved")) is not bool:
                raise ValueError("vehicle hazard readback requires explicit host booleans")
            no_hazard_ingress = hazard_result["no_hazard_ingress"]
            rescue_access_preserved = hazard_result["rescue_access_preserved"]
        trajectory_evidence = LiveEvidence(
            episode_id=self.episode_id,
            evidence_ref=f"{self.source_id}:{vehicle_id}:trajectory:{int(now * 1000):09d}",
            kind="traffic_world_readback", source_id=self.source_id,
            entity_ids=(str(vehicle_id),), observed_time_s=now,
            status_before={"status": _text(status_before, "vehicle status before")},
            status_after={"status": "trajectory_checked"})
        hazard_evidence = LiveEvidence(
            episode_id=self.episode_id,
            evidence_ref=_text(hazard_result.get("hazard_evidence_ref"), "hazard evidence reference")
            if hazard_result is not None else
            f"{self.source_id}:{vehicle_id}:hazard:{int(now * 1000):09d}",
            kind="traffic_world_readback", source_id=self.source_id,
            entity_ids=(str(vehicle_id),), observed_time_s=now,
            status_before={"status": "hazard_unknown"},
            status_after={"status": "hazard_clear" if no_hazard_ingress else "hazard_detected"})
        access_evidence = LiveEvidence(
            episode_id=self.episode_id,
            evidence_ref=_text(hazard_result.get("access_evidence_ref"), "access evidence reference")
            if hazard_result is not None else
            f"{self.source_id}:{vehicle_id}:access:{int(now * 1000):09d}",
            kind="traffic_world_readback", source_id=self.source_id,
            entity_ids=(str(vehicle_id),), observed_time_s=now,
            status_before={"status": "access_unknown"},
            status_after={"status": "access_preserved" if rescue_access_preserved else "access_lost"})
        return {"vehicle_id": str(vehicle_id), "position_before": list(previous),
                "position_after": list(current),
                "native_readback": dict(hazard_result) if hazard_result is not None else None,
                "measured_trajectory": hazard_result is not None or current != previous,
                "no_hazard_ingress": bool(no_hazard_ingress),
                "rescue_access_preserved": bool(rescue_access_preserved),
                "trajectory_check_time_s": now, "evidence": trajectory_evidence,
                "trajectory_evidence": trajectory_evidence,
                "hazard_evidence": hazard_evidence, "access_evidence": access_evidence}

    def record_access_readback(self, witness: CityLiveEpisodeWitness, request: Mapping[str, Any],
                               *, start_time_s: float, end_time_s: float) -> dict[str, Any]:
        if not callable(self.access_readback):
            raise CityRuntimeWitnessBlocked("continuous-access readback source is missing")
        result = self.access_readback(request, _time(start_time_s), _time(end_time_s))
        if not isinstance(result, Mapping):
            raise CityRuntimeWitnessBlocked("continuous-access source returned no readback")
        evidence_ref = _text(result.get("evidence_ref"), "continuous access evidence reference")
        evidence = LiveEvidence(
            episode_id=self.episode_id, evidence_ref=evidence_ref,
            kind="access_readback", source_id=self.source_id, entity_ids=(),
            observed_time_s=self.now_s(), status_before={"status": "blocked"},
            status_after={"status": "clear" if result.get("measured_clear_throughout") is True else "unknown"})
        witness.record_access_readback(
            request["request_id"], start_time_s=start_time_s, end_time_s=end_time_s,
            evidence=evidence,
            measured_clear_throughout=result.get("measured_clear_throughout") is True,
            rescue_and_evacuation_routes_admitted=result.get("rescue_and_evacuation_routes_admitted") is True)
        return {**dict(result), "evidence": evidence.as_dict()}

    def deliver_incident_receipt(self, witness: CityLiveEpisodeWitness,
                                 request: Mapping[str, Any]) -> dict[str, Any]:
        """Deliver an independent endpoint receipt for a submitted incident."""
        if self.incident_receiver is None:
            raise CityRuntimeWitnessBlocked("incident receiver endpoint is not configured")
        payload = dict(request)
        payload["episode_id"] = self.episode_id
        receipt = self.incident_receiver.receive(payload, now_s=self.now_s())
        evidence = LiveEvidence(
            episode_id=self.episode_id,
            evidence_ref=receipt["receipt_evidence_ref"], kind="service_receipt",
            source_id=receipt["source_id"], entity_ids=(), observed_time_s=self.now_s(),
            status_before={"status": "queued"}, status_after={"status": "received"},
        )
        witness.record_receipt(
            request["request_id"], recipient_service_id=request["recipient_service_id"],
            observation_refs=list(request["observation_refs"]),
            received_time_s=receipt["received_time_s"], evidence=evidence)
        return receipt

    def control_vehicle(self, vehicle_id: str, mode: str) -> Mapping[str, Any]:
        if not callable(self.vehicle_controller):
            raise CityRuntimeWitnessBlocked(
                "demo.city_benchmark.scene runtime has scripted vehicle poses but no per-vehicle service controller")
        return self.vehicle_controller(_text(vehicle_id, "vehicle id"), _text(mode, "vehicle mode"))

    def city_service_witness_source(self) -> dict[str, Any]:
        """Return the exact source contract consumed by CityEpisodeServiceWitness."""
        people = getattr(self.runtime, "people", None)
        people_ids = set(getattr(people, "actor_ids", ())) if people is not None else set()
        affected_ready = callable(self.affected_person_roster)
        vehicle_roster_ready = callable(self.following_vehicle_roster)
        if affected_ready and vehicle_roster_ready:
            try:
                people_roster, vehicle_roster = self.episode_rosters()
                affected_ready = bool(people_roster)
                vehicle_roster_ready = bool(vehicle_roster)
            except (CityRuntimeWitnessBlocked, ValueError):
                affected_ready = False
                vehicle_roster_ready = False
        else:
            affected_ready = False
            vehicle_roster_ready = False
        pose_ready = people is not None and callable(getattr(people, "position", None))
        vehicle_ready = (vehicle_roster_ready
                         and callable(self.vehicle_controller)
                         and callable(self.vehicle_pose_reader))
        readback_ready = (self.physx_readback_suppressed is False
                          or self.current_gpu_tensor_clearance)
        query_ready = (self.scene_query_support is True
                       and isinstance(self.clearance_query_provenance, str)
                       and bool(self.clearance_query_provenance.strip())
                       and readback_ready)
        capabilities = {
            "affected_person_source": affected_ready,
            "following_vehicle_source": vehicle_ready,
            "service_ack_source": (affected_ready and vehicle_ready
                                   and (callable(self.person_controller) or bool(self.affected_people))),
            "measured_person_trajectory": pose_ready and affected_ready,
            "measured_vehicle_trajectory": vehicle_ready,
            "dynamic_hazard_and_assembly_access": (callable(self.admission) and query_ready
                                                   and callable(self.hazard_readback)
                                                   and callable(self.access_readback)),
            "external_response_receipt": self.incident_receiver is not None,
            "episode_owned_affected_person_roster": affected_ready,
            "episode_owned_following_vehicle_roster": vehicle_ready,
            "continuous_access_readback": callable(self.access_readback),
        }
        blockers = []
        for key, ready in capabilities.items():
            if not ready:
                blockers.append(f"missing_live_capability:{key}")
        if not callable(self.person_stop):
            blockers.append("missing_native_stop_endpoint:person")
        if not callable(self.vehicle_stop):
            blockers.append("missing_native_stop_endpoint:vehicle")
        if not query_ready:
            blockers.extend(("scene_query_support_disabled_or_not_verified",
                             "clearance_query_provenance_missing"))
        if self.physx_readback_suppressed is not False and not self.current_gpu_tensor_clearance:
            blockers.extend(("physx_scene_query_readback_suppressed_or_unknown",
                             "dynamic_clearance_unknown_under_direct_gpu"))
        return {
            "episode_id": self.episode_id, "source_id": self.source_id,
            "evidence_class": "runtime", "clock": self.now_s,
            "admit": self.admission, "assignment": self.assignment,
            "pull_episode_witness": self._pull_episode_witness,
            "capabilities": capabilities, "blockers": blockers,
        }

    def _pull_episode_witness(self, host: CityEpisodeEvidenceHost) -> None:
        """Deliver current real poses; service readbacks remain endpoint-owned."""
        now = self.now_s()
        self.bind_episode_rosters(host)
        rows = self.observe_people() + self.observe_vehicles()
        if not rows:
            return
        host.deliver_observation({
            "id": f"city_runtime_observation_{int(now * 1000):09d}",
            "episode_id": self.episode_id, "source_id": self.source_id,
            "producer_agent_id": "city_runtime", "region_id": "city_runtime",
            "captured_at_s": now, "delivered_at_s": now,
            "valid_until_s": now + 1.0, "entities": rows,
        })

    def readiness(self) -> dict[str, Any]:
        source = self.city_service_witness_source()
        return {
            "schema": "eai.city.demo_runtime_witness",
            "version": 1, "episode_id": self.episode_id, "source_id": self.source_id,
            "evidence_class": "runtime", "status": "ready" if not source["blockers"] else "blocked",
            "real_witness_ready": not source["blockers"], "scoreable": False,
            "blockers": list(source["blockers"]),
        }


def assess_city_runtime(runtime: Any) -> CityRuntimeBindingAssessment:
    """Check for an explicit episode-owned witness host without guessing actors.

    In particular, ``people`` and ``traffic`` attributes are not accepted as
    evidence sources.  They are scene state until a runtime publishes a
    dedicated witness host with service and world readback semantics.
    """
    runtime_type = type(runtime).__name__
    episode_id = getattr(runtime, "episode_id", None)
    if not isinstance(episode_id, str) or not episode_id.strip():
        episode_id = None
    blockers: list[dict[str, str]] = []

    if episode_id is None:
        blockers.append({
            "code": "episode_identity_missing",
            "detail": "runtime must publish a stable episode_id for every service witness",
        })
    host = getattr(runtime, "city_service_witness", None)
    if host is None:
        blockers.append({
            "code": "service_witness_host_missing",
            "detail": "runtime exposes no episode-owned city_service_witness host",
        })
        if getattr(runtime, "people", None) is not None:
            blockers.append({
                "code": "people_source_is_background_only",
                "detail": "runtime.people is a background path runtime without affected-person identity, service acknowledgement, or measured arrival readback",
            })
        if isinstance(getattr(runtime, "traffic", None), list):
            blockers.append({
                "code": "vehicle_source_is_scene_pose_only",
                "detail": "runtime.traffic is a list of scripted USD pose updates without per-vehicle control endpoint, hazard readback, or post-ack trajectory evidence",
            })
        blockers.append({
            "code": "incident_receipt_endpoint_missing",
            "detail": "runtime exposes no external recipient receipt and continuous-access readback",
        })
    else:
        missing = [name for name in _HOST_METHODS if not callable(getattr(host, name, None))]
        for name in missing:
            blockers.append({
                "code": "witness_host_capability_missing",
                "detail": f"city_service_witness.{name} is required by the live evidence contract",
            })
        if not isinstance(getattr(host, "episode_id", episode_id), str):
            blockers.append({
                "code": "witness_episode_identity_missing",
                "detail": "city_service_witness must carry the same stable episode identity",
            })
        if episode_id is not None and getattr(host, "episode_id", episode_id) != episode_id:
            blockers.append({
                "code": "episode_identity_mismatch",
                "detail": "runtime and city_service_witness episode identities differ",
            })
    return CityRuntimeBindingAssessment(runtime_type, episode_id, not blockers, tuple(blockers))


class CityLiveEpisodeWitness:
    """Episode-owned request/readback facade for a genuinely live City run."""

    def __init__(self, host: CityEpisodeEvidenceHost, *, episode_id: str):
        if not isinstance(host, CityEpisodeEvidenceHost):
            raise TypeError("live witness requires a CityEpisodeEvidenceHost")
        self.host = host
        self.episode_id = _text(episode_id, "episode id")
        self.backend = CityServiceBackend(host.as_source())
        self._evidence: dict[str, LiveEvidence] = {}
        self._submit_lock = RLock()

    @classmethod
    def from_runtime(cls, runtime: Any) -> "CityLiveEpisodeWitness":
        assessment = assess_city_runtime(runtime)
        if not assessment.ready:
            raise CityRuntimeWitnessBlocked(assessment)
        return cls(runtime.city_service_witness, episode_id=assessment.episode_id)  # type: ignore[arg-type]

    def advance(self, time_s: float) -> None:
        self.host.advance(time_s)

    def deliver_observation(self, row: Mapping[str, Any], evidence: LiveEvidence) -> None:
        self._accept(evidence, "observation", row.get("entities", ()), row.get("id"))
        if evidence.episode_id != self.episode_id:
            raise ValueError("observation evidence belongs to another episode")
        payload = dict(row)
        payload["episode_id"] = self.episode_id
        payload["source_id"] = evidence.source_id
        self.host.deliver_observation(payload)

    def guide_people(self, *, agent_id: str, region_id: str, assembly_region_id: str,
                     person_ids: tuple[str, ...], observation_refs: tuple[str, ...]) -> dict[str, Any]:
        with self._submit_lock:
            return self._submit(self.backend.guide_people(
                agent_id=agent_id, region_id=region_id, assembly_region_id=assembly_region_id,
                person_ids=person_ids, observation_refs=observation_refs))

    def control_following_traffic(self, *, agent_id: str, region_id: str,
                                  vehicle_ids: tuple[str, ...], mode: str,
                                  observation_refs: tuple[str, ...]) -> dict[str, Any]:
        with self._submit_lock:
            return self._submit(self.backend.control_following_traffic(
                agent_id=agent_id, region_id=region_id, vehicle_ids=vehicle_ids,
                mode=mode, observation_refs=observation_refs))

    def report_incident(self, *, agent_id: str, region_id: str,
                        observation_refs: tuple[str, ...], recipient_service_id: str) -> dict[str, Any]:
        with self._submit_lock:
            return self._submit(self.backend.report_incident(
                agent_id=agent_id, region_id=region_id, observation_refs=observation_refs,
                recipient_service_id=recipient_service_id))

    def record_actor_ack(self, request_id: str, actor_id: str, *, time_s: float,
                         evidence: LiveEvidence, applied_mode: str | None = None) -> None:
        self._accept(evidence, "service_ack", (actor_id,))
        self._causal(evidence, time_s)
        self.host.record_actor_ack(request_id, actor_id, time_s=time_s,
                                   applied_mode=applied_mode, evidence_ref=evidence.evidence_ref)

    def record_person_readback(self, request_id: str, person_id: str, *, arrival_time_s: float,
                               assembly_region_id: str, trajectory_evidence: LiveEvidence,
                               safety_evidence: LiveEvidence,
                               measured_trajectory: bool = True,
                               safe_route_and_assembly: bool = True) -> None:
        self._accept_batch((
            (trajectory_evidence, "person_world_readback", (person_id,), None),
            (safety_evidence, "person_world_readback", (person_id,), None),
        ))
        self._causal(trajectory_evidence, arrival_time_s)
        self._causal(safety_evidence, arrival_time_s)
        self.host.record_person_readback(
            request_id, person_id, arrival_time_s=arrival_time_s,
            assembly_region_id=assembly_region_id, measured_trajectory=measured_trajectory,
            safe_route_and_assembly=safe_route_and_assembly,
            trajectory_ref=trajectory_evidence.evidence_ref,
            safety_ref=safety_evidence.evidence_ref)

    def record_traffic_readback(self, request_id: str, vehicle_id: str, *,
                                trajectory_check_time_s: float,
                                trajectory_evidence: LiveEvidence, hazard_evidence: LiveEvidence,
                                access_evidence: LiveEvidence,
                                measured_trajectory: bool = True,
                                no_hazard_ingress: bool = True,
                                rescue_access_preserved: bool = True) -> None:
        evidence_rows = tuple(
            (evidence, "traffic_world_readback", (vehicle_id,), None)
            for evidence in (trajectory_evidence, hazard_evidence, access_evidence))
        self._accept_batch(evidence_rows)
        for evidence in (trajectory_evidence, hazard_evidence, access_evidence):
            self._causal(evidence, trajectory_check_time_s)
        self.host.record_traffic_readback(
            request_id, vehicle_id, trajectory_check_time_s=trajectory_check_time_s,
            measured_trajectory=measured_trajectory, no_hazard_ingress=no_hazard_ingress,
            rescue_access_preserved=rescue_access_preserved,
            trajectory_ref=trajectory_evidence.evidence_ref,
            hazard_ref=hazard_evidence.evidence_ref, access_ref=access_evidence.evidence_ref)

    def record_receipt(self, request_id: str, *, recipient_service_id: str,
                       observation_refs: list[str], received_time_s: float,
                       evidence: LiveEvidence) -> None:
        self._accept(evidence, "service_receipt", ())
        self._causal(evidence, received_time_s)
        self.host.record_receipt(request_id, recipient_service_id=recipient_service_id,
                                 observation_refs=observation_refs, received_time_s=received_time_s,
                                 receipt_ref=evidence.evidence_ref)

    def record_access_readback(self, request_id: str, *, start_time_s: float, end_time_s: float,
                               evidence: LiveEvidence, measured_clear_throughout: bool = True,
                               rescue_and_evacuation_routes_admitted: bool = True) -> None:
        self._accept(evidence, "access_readback", ())
        self._causal(evidence, end_time_s)
        self.host.record_access_readback(
            request_id, start_time_s=start_time_s, end_time_s=end_time_s,
            measured_clear_throughout=measured_clear_throughout,
            rescue_and_evacuation_routes_admitted=rescue_and_evacuation_routes_admitted,
            evidence_ref=evidence.evidence_ref)

    def review(self, request_id: str) -> dict[str, Any]:
        result = self.backend.review(request_id)
        result["live_witness"] = result.get("status") == "evidence_complete" and \
            result.get("evidence_class") == "runtime"
        result["scoreable"] = False
        return result

    def record_partial_failure(self, request_id: str, *, attempted_ids: tuple[str, ...],
                               failed_actor_id: str, failure_stage: str,
                               stops: tuple[Mapping[str, Any], ...]) -> None:
        self.host.record_partial_failure(request_id, attempted_ids=attempted_ids,
                                         failed_actor_id=failed_actor_id,
                                         failure_stage=failure_stage, stops=stops)
        self.backend.mark_partial_failed(request_id)

    def _submit(self, request: Mapping[str, Any]) -> dict[str, Any]:
        try:
            self.host.register_request(request, evidence_class="runtime")
        except Exception:
            self.backend.discard_unregistered(request)
            raise
        return dict(request)

    def _accept(self, evidence: LiveEvidence, kind: str, entities: Iterable[Any], expected_ref: Any = None) -> None:
        self._accept_batch(((evidence, kind, entities, expected_ref),))

    def _accept_batch(self, rows: Iterable[tuple[LiveEvidence, str, Iterable[Any], Any]]) -> None:
        pending = tuple(rows)
        refs = [row[0].evidence_ref for row in pending if isinstance(row[0], LiveEvidence)]
        if len(refs) != len(set(refs)):
            raise ValueError("live readback requires distinct evidence references")
        for evidence, kind, entities, expected_ref in pending:
            self._validate_evidence(evidence, kind, entities, expected_ref)
        for evidence, _, _, _ in pending:
            self._evidence[evidence.evidence_ref] = evidence

    def _validate_evidence(self, evidence: LiveEvidence, kind: str,
                           entities: Iterable[Any], expected_ref: Any = None) -> None:
        if not isinstance(evidence, LiveEvidence) or evidence.episode_id != self.episode_id:
            raise ValueError("live evidence must belong to this episode")
        if evidence.kind != kind:
            raise ValueError(f"expected {kind} evidence")
        if expected_ref is not None and evidence.evidence_ref != expected_ref:
            raise ValueError("evidence reference does not bind the observed object")
        ids = tuple(str(item.get("id")) if isinstance(item, Mapping) else str(item)
                    for item in entities)
        if kind in {"observation", "service_ack", "person_world_readback", "traffic_world_readback"} \
                and not evidence.entity_ids:
            raise ValueError("live evidence must bind an entity")
        if ids and not set(ids) <= set(evidence.entity_ids):
            raise ValueError("evidence does not bind every observed entity")
        if evidence.evidence_ref in self._evidence:
            raise ValueError("duplicate live evidence reference")
        if evidence.observed_time_s > self.host.now_s():
            raise ValueError("live evidence is from the future")

    @staticmethod
    def _causal(evidence: LiveEvidence, event_time_s: float) -> None:
        if evidence.observed_time_s > _time(event_time_s):
            raise ValueError("live evidence is later than the event it supports")


def bind_city_runtime(runtime: Any) -> CityLiveEpisodeWitness:
    """Executable fail-closed binding entry point used by a future City runtime."""
    return CityLiveEpisodeWitness.from_runtime(runtime)


__all__ = [
    "CityDemoRuntimeWitnessAdapter", "CityIncidentReceiver", "CityLiveEpisodeWitness",
    "CityRuntimeBindingAssessment", "CityRuntimeWitnessBlocked", "LiveEvidence",
    "assess_city_runtime", "bind_city_runtime",
]
