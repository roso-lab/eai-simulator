"""Episode-owned city evidence cache; never pass this writer to participants.

The simulator host supplies observations, measured role intervals, service
acknowledgements and independent world readbacks. This adapter stores their
provenance and exposes only delivered evidence to CityServiceBackend. It does
not sample Isaac, decide physical safety, or synthesize successful outcomes.
"""
from __future__ import annotations

from copy import deepcopy
import math
from threading import RLock
from typing import Any, Callable, Mapping

from .service_backend import CityRuntimeReadbackAdapter, _time


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


class CityEpisodeEvidenceHost:
    """Trusted writer/readback pair with no participant-facing write surface.

    ``admission`` belongs to the scene host and must inspect current routes,
    capacity, hazard, service authority and selected-team state. No admission
    is inferred from a map candidate or a request alone.
    """

    def __init__(self, *, admission: Callable[[str, tuple[str, ...], str,
                                               str | None, str | None, float], bool],
                 episode_id: str | None = None, source_id: str | None = None,
                 require_episode_rosters: bool = False):
        self._admission = admission
        # Legacy offline hosts have no live identity.  Keep those hosts
        # unbound; CityEpisodeServiceWitness always supplies both values.
        if episode_id is not None:
            self.episode_id = _id(episode_id, "episode id")
        if source_id is not None:
            self.source_id = _id(source_id, "episode source id")
        self._now = 0.0
        self._observations: dict[str, dict[str, Any]] = {}
        self._roles: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._readbacks: dict[str, dict[str, Any]] = {}
        self._require_episode_rosters = bool(require_episode_rosters)
        self._rosters: dict[str, dict[str, dict[str, Any]]] = {
            "person": {}, "vehicle": {}}

    def advance(self, time_s: float) -> None:
        now = _time(time_s)
        if now < self._now:
            raise ValueError("episode clock cannot move backwards")
        self._now = now

    def now_s(self) -> float:
        return self._now

    def _bind_roster(self, kind: str, rows: Mapping[str, Mapping[str, Any]]) -> None:
        if kind not in self._rosters:
            raise ValueError("unsupported episode roster kind")
        if not isinstance(rows, Mapping) or not rows:
            raise ValueError(f"episode-owned {kind} roster must be nonempty")
        bound: dict[str, dict[str, Any]] = {}
        for entity_id, row in rows.items():
            entity_id = _id(entity_id, f"{kind} roster id")
            if not isinstance(row, Mapping):
                raise ValueError(f"{kind} roster rows must be mappings")
            if row.get("id", entity_id) != entity_id or row.get("kind", kind) != kind:
                raise ValueError(f"{kind} roster identity mismatch")
            expected_episode = getattr(self, "episode_id", None)
            expected_source = getattr(self, "source_id", None)
            if expected_episode is not None and row.get("episode_id") != expected_episode:
                raise ValueError(f"{kind} roster episode provenance mismatch")
            if expected_source is not None and row.get("source_id") != expected_source:
                raise ValueError(f"{kind} roster source provenance mismatch")
            bound[entity_id] = deepcopy({**dict(row), "id": entity_id, "kind": kind,
                                         "episode_id": expected_episode,
                                         "source_id": expected_source})
        self._rosters[kind] = bound

    def bind_affected_person_roster(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
        """Bind the hidden affected-person set supplied by the episode host."""
        self._bind_roster("person", rows)

    def bind_following_vehicle_roster(self, rows: Mapping[str, Mapping[str, Any]]) -> None:
        """Bind the hidden following-vehicle set supplied by the episode host."""
        self._bind_roster("vehicle", rows)

    def roster_ids(self, kind: str) -> tuple[str, ...]:
        return tuple(self._rosters.get(kind, {}))

    def deliver_observation(self, row: Mapping[str, Any]) -> None:
        """Import a captured observation only after the channel delivered it."""
        row = dict(row)
        episode_id = getattr(self, "episode_id", None)
        source_id = getattr(self, "source_id", None)
        if episode_id is not None:
            if row.get("episode_id") != episode_id or row.get("source_id") != source_id:
                raise ValueError("observation is not bound to this episode and source")
        ref = _id(row.get("id"), "observation id")
        captured, delivered = _time(row.get("captured_at_s")), _time(row.get("delivered_at_s"))
        expires = _time(row.get("valid_until_s"))
        if captured > delivered or delivered > self._now or expires <= delivered:
            raise ValueError("observation is future, undelivered or invalid")
        _id(row.get("producer_agent_id"), "observation producer")
        _id(row.get("region_id"), "observation region")
        entities = row.get("entities")
        if not isinstance(entities, list):
            raise ValueError("observation entities must be a list")
        ids = []
        for entity in entities:
            if not isinstance(entity, Mapping):
                raise ValueError("invalid observation entity")
            ids.append(_id(entity.get("id"), "entity id"))
            _id(entity.get("kind"), "entity kind")
        if len(ids) != len(set(ids)) or ref in self._observations:
            raise ValueError("duplicate entity or observation identity")
        self._observations[ref] = deepcopy(dict(row))

    def observation(self, ref: str) -> dict[str, Any] | None:
        row = self._observations.get(ref)
        return deepcopy(row) if row is not None and row["delivered_at_s"] <= self._now else None

    def latest_delivered_capture_s(self, entity_id: str, now_s: float) -> float:
        as_of = _time(now_s)
        if as_of > self._now:
            raise ValueError("cannot query future observation delivery")
        captures = [float(row["captured_at_s"]) for row in self._observations.values()
                    if row["delivered_at_s"] <= as_of
                    and any(entity["id"] == entity_id for entity in row["entities"])]
        return max(captures, default=0.0)

    def record_role_occupancy(self, agent_id: str, role: str, region_id: str, *,
                              start_time_s: float, end_time_s: float) -> None:
        key = (_id(agent_id, "role agent"), _id(role, "field role"),
               _id(region_id, "role region"))
        start, end = _time(start_time_s), _time(end_time_s)
        if not start < end <= self._now:
            raise ValueError("role occupancy requires a measured, elapsed interval")
        spans = self._roles.setdefault(key, [])
        if spans and start < spans[-1][0]:
            raise ValueError("role occupancy cannot be inserted out of order")
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))

    def role_interval(self, agent_id: str, role: str, region_id: str,
                      as_of_s: float) -> dict[str, float] | None:
        as_of = _time(as_of_s)
        if as_of > self._now:
            raise ValueError("cannot query future field role")
        spans = self._roles.get((agent_id, role, region_id), ())
        candidates = [(start, min(end, as_of)) for start, end in spans if start < as_of]
        if not candidates:
            return None
        start, end = candidates[-1]
        return {"start_time_s": start, "end_time_s": end} if start < end else None

    def role_active(self, agent_id: str, role: str, region_id: str, now_s: float) -> bool:
        interval = self.role_interval(agent_id, role, region_id, now_s)
        return interval is not None and interval["end_time_s"] == _time(now_s)

    def admit(self, service: str, ids: tuple[str, ...], region_id: str,
              target_region_id: str | None, mode: str | None, now_s: float) -> bool:
        if _time(now_s) != self._now:
            raise ValueError("admission must use the current episode clock")
        if service == "guide_people":
            requested = set(ids)
            claimed: set[str] = set()
            for request in self._requests.values():
                if request["service"] != "guide_people":
                    continue
                failure = self._readbacks[request["request_id"]].get("partial_failure")
                if failure and all(stop["status"] == "stopped" for stop in failure["stops"]):
                    continue
                people = set(request["entity_ids"])
                if request["target_region_id"] != target_region_id:
                    if requested & people:
                        return False
                    continue
                claimed.update(people)
            ids = tuple(dict.fromkeys((*ids, *sorted(claimed))))
        return self._admission(service, ids, region_id, target_region_id, mode, now_s) is True

    def register_request(self, request: Mapping[str, Any], *, evidence_class: str) -> None:
        """Called by the host after CityServiceBackend accepts a request."""
        request_id = _id(request.get("request_id"), "service request id")
        service = request.get("service")
        if service not in {"guide_people", "control_traffic", "report_incident"}:
            raise ValueError("unsupported city service")
        if evidence_class not in {"reference", "runtime"}:
            raise ValueError("evidence class must be explicitly selected by the host")
        if request.get("status") != "pending" or _time(request.get("submitted_time_s")) != self._now:
            raise ValueError("register only a newly accepted request at the episode clock")
        if request_id in self._requests:
            raise ValueError("duplicate service request identity")
        ids = request.get("entity_ids")
        if not isinstance(ids, list) or len(ids) != len(set(ids)):
            raise ValueError("invalid service request entity ids")
        roster_kind = {"guide_people": "person", "control_traffic": "vehicle"}.get(service)
        if roster_kind is not None:
            roster = self._rosters[roster_kind]
            if self._require_episode_rosters and not roster:
                raise ValueError(f"missing episode-owned {roster_kind} roster")
            if roster and any(entity_id not in roster for entity_id in ids):
                raise ValueError(f"service request entity is outside episode-owned {roster_kind} roster")
        accepted = deepcopy(dict(request))
        episode_id = getattr(self, "episode_id", None)
        source_id = getattr(self, "source_id", None)
        if episode_id is not None:
            accepted.update(episode_id=episode_id, source_id=source_id)
        self._requests[request_id] = accepted
        self._readbacks[request_id] = {
            "request_id": request_id, "service": service,
            "evidence_class": evidence_class,
            "episode_id": episode_id,
            "source_id": source_id,
        }

    def request_ids(self) -> tuple[str, ...]:
        """Return accepted request identities to a trusted runtime collector."""
        return tuple(self._requests)

    def _request(self, request_id: str, service: str) -> tuple[dict[str, Any], dict[str, Any]]:
        request = self._requests[request_id]
        if request["service"] != service:
            raise ValueError("service readback has wrong request kind")
        return request, self._readbacks[request_id]

    def record_actor_ack(self, request_id: str, actor_id: str, *, time_s: float,
                         applied_mode: str | None = None, evidence_ref: str) -> None:
        request = self._requests[request_id]
        if request["service"] not in {"guide_people", "control_traffic"}:
            raise ValueError("actor acknowledgement requires guidance or traffic request")
        if actor_id not in request["entity_ids"]:
            raise ValueError("actor not in service request")
        timestamp = _time(time_s)
        if not request["submitted_time_s"] <= timestamp <= self._now:
            raise ValueError("actor acknowledgement outside causal interval")
        _id(evidence_ref, "external acknowledgement reference")
        if request["service"] == "control_traffic" and applied_mode != request["mode"]:
            raise ValueError("traffic acknowledgement mode mismatch")
        update = self._readbacks[request_id]
        actors = update.setdefault("actors", {})
        if actor_id in actors:
            raise ValueError("duplicate actor acknowledgement")
        actors[actor_id] = {"id": actor_id, "ack_time_s": timestamp,
                            "ack_evidence_ref": evidence_ref}
        if applied_mode is not None:
            actors[actor_id]["applied_mode"] = applied_mode

    def record_partial_failure(self, request_id: str, *, attempted_ids: tuple[str, ...],
                               failed_actor_id: str, failure_stage: str,
                               stops: tuple[Mapping[str, Any], ...]) -> None:
        request = self._requests[request_id]
        update = self._readbacks[request_id]
        if (request["service"] not in {"guide_people", "control_traffic"}
                or "partial_failure" in update or failure_stage not in {"controller", "ack"}
                or not attempted_ids or attempted_ids[-1] != failed_actor_id
                or tuple(request["entity_ids"][:len(attempted_ids)]) != attempted_ids
                or len(stops) != len(attempted_ids)):
            raise ValueError("invalid partial service failure identity")
        seen_refs = set()
        for actor_id, stop in zip(reversed(attempted_ids), stops):
            if stop.get("id") != actor_id or stop.get("status") not in {"stopped", "stop_unconfirmed"}:
                raise ValueError("partial failure stop identity mismatch")
            if stop["status"] == "stopped":
                ref = _id(stop.get("evidence_ref"), "native stop evidence")
                position = stop.get("position_xyz")
                if (not isinstance(position, list) or len(position) != 3
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in position)):
                    raise ValueError("native stop position readback is invalid")
                if ref in seen_refs or not request["submitted_time_s"] <= _time(stop.get("time_s")) <= self._now:
                    raise ValueError("native stop evidence is duplicate or outside causal interval")
                seen_refs.add(ref)
            elif "evidence_ref" in stop:
                raise ValueError("unconfirmed stop cannot claim evidence")
        update["partial_failure"] = {
            "failed_actor_id": failed_actor_id, "failure_stage": failure_stage,
            "attempted_ids": list(attempted_ids), "stops": deepcopy(list(stops)),
            "all_stopped": all(stop["status"] == "stopped" for stop in stops),
        }

    def record_person_readback(self, request_id: str, person_id: str, *, arrival_time_s: float,
                               assembly_region_id: str, measured_trajectory: bool,
                               safe_route_and_assembly: bool, trajectory_ref: str,
                               safety_ref: str) -> None:
        request, update = self._request(request_id, "guide_people")
        actor = update.get("actors", {}).get(person_id)
        if actor is None:
            raise ValueError("arrival requires an external per-person acknowledgement")
        arrived = _time(arrival_time_s)
        if not actor["ack_time_s"] <= arrived <= self._now:
            raise ValueError("person arrival outside causal interval")
        if "arrival_time_s" in actor:
            raise ValueError("duplicate person arrival readback")
        _id(trajectory_ref, "person trajectory evidence")
        _id(safety_ref, "assembly safety evidence")
        if not isinstance(measured_trajectory, bool) or not isinstance(safe_route_and_assembly, bool):
            raise ValueError("person readback results must be explicit booleans")
        actor.update(arrival_time_s=arrived, assembly_region_id=_id(assembly_region_id, "assembly region"),
                     measured_trajectory=measured_trajectory,
                     safe_route_and_assembly=safe_route_and_assembly,
                     trajectory_evidence_ref=trajectory_ref, safety_evidence_ref=safety_ref)

    def record_traffic_readback(self, request_id: str, vehicle_id: str, *,
                                trajectory_check_time_s: float, measured_trajectory: bool,
                                no_hazard_ingress: bool, rescue_access_preserved: bool,
                                trajectory_ref: str, hazard_ref: str, access_ref: str) -> None:
        _, update = self._request(request_id, "control_traffic")
        actor = update.get("actors", {}).get(vehicle_id)
        if actor is None:
            raise ValueError("traffic trajectory requires a service acknowledgement")
        checked = _time(trajectory_check_time_s)
        if not actor["ack_time_s"] <= checked <= self._now:
            raise ValueError("traffic check outside causal interval")
        if "trajectory_check_time_s" in actor:
            raise ValueError("duplicate traffic trajectory readback")
        for ref in (trajectory_ref, hazard_ref, access_ref):
            _id(ref, "traffic world evidence reference")
        if not all(isinstance(flag, bool) for flag in
                   (measured_trajectory, no_hazard_ingress, rescue_access_preserved)):
            raise ValueError("traffic readback results must be explicit booleans")
        actor.update(trajectory_check_time_s=checked, measured_trajectory=measured_trajectory,
                     no_hazard_ingress=no_hazard_ingress,
                     rescue_access_preserved=rescue_access_preserved,
                     trajectory_evidence_ref=trajectory_ref, hazard_evidence_ref=hazard_ref,
                     access_evidence_ref=access_ref)

    def record_receipt(self, request_id: str, *, recipient_service_id: str,
                       observation_refs: list[str], received_time_s: float,
                       receipt_ref: str) -> None:
        request, update = self._request(request_id, "report_incident")
        received = _time(received_time_s)
        if (recipient_service_id != request["recipient_service_id"]
                or observation_refs != request["observation_refs"]
                or not request["submitted_time_s"] <= received <= self._now):
            raise ValueError("response receipt must bind exact request and causal time")
        if "receipt" in update:
            raise ValueError("duplicate service receipt")
        update["receipt"] = {"recipient_service_id": recipient_service_id,
                             "observation_refs": list(observation_refs),
                             "received_time_s": received,
                             "receipt_evidence_ref": _id(receipt_ref, "response endpoint receipt")}

    def record_access_readback(self, request_id: str, *, start_time_s: float,
                               end_time_s: float, measured_clear_throughout: bool,
                               rescue_and_evacuation_routes_admitted: bool,
                               evidence_ref: str) -> None:
        _, update = self._request(request_id, "report_incident")
        start, end = _time(start_time_s), _time(end_time_s)
        if not start < end <= self._now:
            raise ValueError("continuous access requires an elapsed interval")
        if "continuous_access" in update:
            raise ValueError("duplicate continuous access readback")
        if (not isinstance(measured_clear_throughout, bool)
                or not isinstance(rescue_and_evacuation_routes_admitted, bool)):
            raise ValueError("access readback results must be explicit booleans")
        update["continuous_access"] = {
            "start_time_s": start, "end_time_s": end,
            "measured_clear_throughout": measured_clear_throughout,
            "rescue_and_evacuation_routes_admitted": rescue_and_evacuation_routes_admitted,
            "access_evidence_ref": _id(evidence_ref, "continuous access evidence")}

    def service_update(self, request_id: str) -> dict[str, Any] | None:
        row = self._readbacks.get(request_id)
        if row is None:
            return None
        result = deepcopy(row)
        if "partial_failure" in result:
            result["actors"] = list(result.get("actors", {}).values())
            return result
        if result["service"] != "report_incident":
            if len(result.get("actors", {})) != len(self._requests[request_id]["entity_ids"]):
                return None
            result["actors"] = list(result["actors"].values())
        return result

    def as_source(self) -> CityRuntimeReadbackAdapter:
        return CityRuntimeReadbackAdapter(
            clock=self.now_s, observation_lookup=self.observation,
            latest_capture=self.latest_delivered_capture_s, field_role=self.role_active,
            field_role_interval=self.role_interval, admission=self.admit,
            readback=self.service_update)


class CityLiveWitnessBlocked(RuntimeError):
    """Raised when a City runtime cannot provide the live witness contract."""


class CityEpisodeServiceWitness:
    """Bind an explicit episode-owned runtime source to the service backend.

    The source contract is deliberately separate from scene objects.  A
    runtime must expose ``city_service_witness_source()`` returning runtime
    evidence, an authoritative clock, service assignment/admission callbacks,
    and a trusted collector callback.  Scene pose lists, ACKs, and reference
    models are never inferred as a live source.
    """

    CONTRACT_VERSION = 1
    REQUIRED_CAPABILITIES = (
        "affected_person_source", "following_vehicle_source", "service_ack_source",
        "measured_person_trajectory", "measured_vehicle_trajectory",
        "dynamic_hazard_and_assembly_access", "external_response_receipt",
        "episode_owned_affected_person_roster", "episode_owned_following_vehicle_roster",
        "continuous_access_readback",
    )

    def __init__(self, runtime: Any, *, episode_id: str):
        self.runtime = runtime
        self.episode_id = _id(episode_id, "episode id")
        self._host: CityEpisodeEvidenceHost | None = None
        self._backend = None
        self._requests = None
        self._pull = None
        self._clock = None
        self._last_sync: float | None = None
        self._submit_lock = RLock()
        self._report = self._probe()

    @classmethod
    def from_city_runtime(cls, runtime: Any, *, episode_id: str) -> "CityEpisodeServiceWitness":
        return cls(runtime, episode_id=episode_id)

    @staticmethod
    def _source_value(source: Any, key: str) -> Any:
        return source.get(key) if isinstance(source, Mapping) else getattr(source, key, None)

    def _report_for(self, blockers: list[str], *, source_id: str | None = None,
                    evidence_class: str | None = None) -> dict[str, Any]:
        return {
            "schema": "eai.city.live_service_witness", "version": self.CONTRACT_VERSION,
            "episode_id": self.episode_id, "source_id": source_id,
            "evidence_class": evidence_class, "status": "blocked" if blockers else "ready",
            "real_witness_ready": not blockers, "execution_validated": False,
            "scoreable": False, "request_ack_is_business_outcome": False,
            "blockers": list(dict.fromkeys(blockers)),
        }

    def _probe(self) -> dict[str, Any]:
        factory = getattr(self.runtime, "city_service_witness_source", None)
        if not callable(factory):
            return self._report_for([
                "missing_episode_owned_service_witness_source",
                "missing_episode_owned_affected_person_roster_and_observations",
                "missing_episode_owned_following_vehicle_roster_and_observations",
                "missing_episode_owned_service_ack_and_external_receipt_source",
                "missing_measured_person_and_vehicle_trajectory_readback",
                "missing_dynamic_hazard_assembly_capacity_and_rescue_access_readback",
                "demo_city_runtime_entities_are_scripted_or_background_only",
            ])
        try:
            source = factory()
        except Exception as exc:
            return self._report_for([f"city_service_witness_source_error:{type(exc).__name__}"])
        source_id = self._source_value(source, "source_id")
        evidence_class = self._source_value(source, "evidence_class")
        blockers: list[str] = []
        if not isinstance(source_id, str) or not source_id.strip():
            blockers.append("missing_live_witness_source_id")
            source_id = None
        if evidence_class != "runtime":
            blockers.append("live_witness_source_must_declare_runtime_evidence")
        if self._source_value(source, "episode_id") != self.episode_id:
            blockers.append("live_witness_episode_id_mismatch")
        for key in ("clock", "admit", "assignment", "pull_episode_witness"):
            if not callable(self._source_value(source, key)):
                blockers.append(f"missing_live_witness_callback:{key}")
        capabilities = self._source_value(source, "capabilities")
        if not isinstance(capabilities, Mapping):
            blockers.extend(f"missing_live_capability:{key}" for key in self.REQUIRED_CAPABILITIES)
        else:
            blockers.extend(f"missing_live_capability:{key}" for key in self.REQUIRED_CAPABILITIES
                            if capabilities.get(key) is not True)
        if blockers:
            return self._report_for(blockers, source_id=source_id, evidence_class=evidence_class)
        self._clock = self._source_value(source, "clock")
        self._pull = self._source_value(source, "pull_episode_witness")
        self._host = CityEpisodeEvidenceHost(
            admission=self._source_value(source, "admit"),
            episode_id=self.episode_id, source_id=source_id,
            require_episode_rosters=True)
        from .service_backend import CityServiceBackend, CityServiceRequestAdapter
        self._backend = CityServiceBackend(self._host.as_source())
        self._requests = CityServiceRequestAdapter(
            self._backend, self._source_value(source, "assignment"))
        return self._report_for([], source_id=source_id, evidence_class="runtime")

    @property
    def readiness(self) -> dict[str, Any]:
        return deepcopy(self._report)

    @property
    def blocked(self) -> bool:
        return self._report["status"] != "ready"

    def require_ready(self) -> None:
        if self.blocked:
            raise CityLiveWitnessBlocked(
                "City live service witness is blocked: " + ", ".join(self._report["blockers"]))

    @property
    def request_adapter(self):
        self.require_ready()
        return self._requests

    def sync(self) -> None:
        self.require_ready()
        now = _time(self._clock())
        self._host.advance(now)
        if self._last_sync == now:
            return
        self._pull(self._host)
        self._last_sync = now

    def submit(self, service: str, **kwargs: Any) -> dict[str, Any]:
        with self._submit_lock:
            self.sync()
            if service not in {"guide_people", "control_following_traffic", "report_incident"}:
                raise ValueError(f"unsupported city service operation: {service}")
            request = getattr(self._requests, service)(**kwargs)
            try:
                self._host.register_request(request, evidence_class="runtime")
            except Exception:
                self._backend.discard_unregistered(request)
                raise
            return deepcopy(request)

    def review(self, request_id: str) -> dict[str, Any]:
        self.sync()
        return self._backend.review(request_id)

    def advance(self, time_s: float) -> None:
        self.require_ready()
        advance = getattr(self.runtime, "advance", None)
        if not callable(advance):
            raise CityLiveWitnessBlocked("live runtime owns its clock; advance() is unavailable")
        advance(_time(time_s))
        self.sync()


__all__ = ["CityEpisodeEvidenceHost", "CityEpisodeServiceWitness", "CityLiveWitnessBlocked"]
