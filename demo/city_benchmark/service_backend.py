"""City service request boundary for a trusted episode runtime.

The source belongs to the simulator, never to a participant. Its readbacks
must come from service endpoints and measured world state; this module only
checks identity, visibility, causal ordering and deadlines. It cannot certify
the source or turn reference animation into physical evidence.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Callable, Iterable, Mapping, Protocol

from .task_logic import default_deadline_policy, review_deadline_policy


def _time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("service time must be finite and nonnegative")
    return float(value)


class CityServiceSource(Protocol):
    """Trusted, episode-owned read API; never expose this object to agents."""

    def now_s(self) -> float: ...
    def observation(self, ref: str) -> Mapping[str, Any] | None: ...
    def latest_delivered_capture_s(self, entity_id: str, now_s: float) -> float: ...
    def role_active(self, agent_id: str, role: str, region_id: str, now_s: float) -> bool: ...
    def role_interval(self, agent_id: str, role: str, region_id: str,
                      as_of_s: float) -> Mapping[str, Any] | None: ...
    def admit(self, service: str, ids: tuple[str, ...], region_id: str,
              target_region_id: str | None, mode: str | None, now_s: float) -> bool: ...
    def service_update(self, request_id: str) -> Mapping[str, Any] | None: ...


class CityRuntimeReadbackAdapter:
    """Wire episode-owned registries/collectors to the city service boundary.

    The caller must supply authoritative callbacks; this adapter intentionally
    has no participant-facing registration or receipt-injection method.
    """

    def __init__(self, *, clock: Callable[[], float],
                 observation_lookup: Callable[[str], Mapping[str, Any] | None],
                 latest_capture: Callable[[str, float], float],
                 field_role: Callable[[str, str, str, float], bool],
                 field_role_interval: Callable[[str, str, str, float], Mapping[str, Any] | None],
                 admission: Callable[[str, tuple[str, ...], str, str | None, str | None, float], bool],
                 readback: Callable[[str], Mapping[str, Any] | None]):
        self._clock = clock
        self._observation_lookup = observation_lookup
        self._latest_capture = latest_capture
        self._field_role = field_role
        self._field_role_interval = field_role_interval
        self._admission = admission
        self._readback = readback

    def now_s(self) -> float:
        return self._clock()

    def observation(self, ref: str) -> Mapping[str, Any] | None:
        return self._observation_lookup(ref)

    def latest_delivered_capture_s(self, entity_id: str, now_s: float) -> float:
        return self._latest_capture(entity_id, now_s)

    def role_active(self, agent_id: str, role: str, region_id: str, now_s: float) -> bool:
        return self._field_role(agent_id, role, region_id, now_s)

    def role_interval(self, agent_id: str, role: str, region_id: str,
                      as_of_s: float) -> Mapping[str, Any] | None:
        return self._field_role_interval(agent_id, role, region_id, as_of_s)

    def admit(self, service: str, ids: tuple[str, ...], region_id: str,
              target_region_id: str | None, mode: str | None, now_s: float) -> bool:
        return self._admission(service, ids, region_id, target_region_id, mode, now_s)

    def service_update(self, request_id: str) -> Mapping[str, Any] | None:
        return self._readback(request_id)


class CityServiceBackend:
    """Submit and review one service request without revealing private rosters.

    ``service_update`` is a trusted readback, not participant-supplied data.
    Result status is per request; whole-mission success requires the separate
    hidden roster and continuous access acceptance review.
    """

    _ROLES = {"guide_people": "guidance_contact", "control_traffic": "traffic_control_point",
              "report_incident": "incident_observer"}

    def __init__(self, source: CityServiceSource, *, policy: Mapping[str, Any] | None = None):
        self.source = source
        self.policy = dict(policy or default_deadline_policy())
        review_deadline_policy(self.policy)
        self._requests: dict[str, dict[str, Any]] = {}
        self._keys: set[tuple[Any, ...]] = set()

    def _role_covers(self, row: Mapping[str, Any], start: float, end: float,
                     as_of: float) -> bool:
        interval = self.source.role_interval(row["agent_id"], self._ROLES[row["service"]],
                                             row["region_id"], as_of)
        if interval is None:
            return False
        begin = _time(interval.get("start_time_s"))
        finish = _time(interval.get("end_time_s"))
        if begin >= finish or finish > as_of:
            raise ValueError("invalid historical field-role interval")
        return begin <= start <= end <= finish

    def _observations(self, refs: tuple[str, ...], now: float) -> dict[str, Mapping[str, Any]]:
        if not refs or len(set(refs)) != len(refs) or any(not ref for ref in refs):
            raise ValueError("unique nonempty observation references required")
        entities: dict[str, Mapping[str, Any]] = {}
        for ref in refs:
            row = self.source.observation(ref)
            if row is None or row.get("id") != ref:
                raise ValueError("observation not delivered")
            captured = _time(row.get("captured_at_s"))
            delivered = _time(row.get("delivered_at_s"))
            expires = _time(row.get("valid_until_s"))
            if captured > delivered or delivered > now or not now < expires:
                raise ValueError("observation future, undelivered or expired")
            if not row.get("producer_agent_id") or not row.get("region_id"):
                raise ValueError("observation provenance missing")
            for entity in row.get("entities", ()):
                if not isinstance(entity, Mapping) or not entity.get("id") or not entity.get("kind"):
                    raise ValueError("invalid observation entity")
                entity_id = entity["id"]
                latest = _time(self.source.latest_delivered_capture_s(entity_id, now))
                if captured < latest:
                    raise ValueError("observation superseded by a delivered capture")
                if entity_id in entities and dict(entities[entity_id]) != dict(entity):
                    raise ValueError("conflicting observed entity identity")
                entities[entity_id] = entity
        return entities

    def _submit(self, service: str, *, agent_id: str, region_id: str,
                observation_refs: tuple[str, ...], ids: tuple[str, ...] = (),
                mode: str | None = None, recipient: str | None = None,
                target_region_id: str | None = None) -> dict[str, Any]:
        now = _time(self.source.now_s())
        if now > self.policy["response_budget_s"]:
            raise ValueError("response budget exceeded")
        if not agent_id or not region_id or not self.source.role_active(
                agent_id, self._ROLES[service], region_id, now):
            raise ValueError("active field role evidence missing")
        if not self._role_covers({"agent_id": agent_id, "region_id": region_id,
                                  "service": service}, now, now, now):
            raise ValueError("time-bounded field role evidence missing")
        entities = self._observations(observation_refs, now)
        if service == "guide_people":
            if not target_region_id or not ids or any(entities.get(i, {}).get("kind") != "person" for i in ids):
                raise ValueError("person not observed")
        elif service == "control_traffic":
            if mode not in {"hold", "divert", "release"}:
                raise ValueError("invalid traffic mode")
            if not ids or any(entities.get(i, {}).get("kind") != "vehicle"
                              or entities[i].get("following_incident") is not True
                              or entities[i].get("is_accident_vehicle") is not False for i in ids):
                raise ValueError("only observed following traffic may be controlled")
        elif not recipient:
            raise ValueError("report requires a recipient")
        if ids and (len(set(ids)) != len(ids) or any(not i for i in ids)):
            raise ValueError("duplicate or empty entity identity")
        if not self.source.admit(service, ids, region_id, target_region_id, mode, now):
            raise ValueError("service route, authority or capacity not admitted")
        key = (service, agent_id, region_id, tuple(sorted(ids)), mode, recipient,
               target_region_id, observation_refs)
        if key in self._keys:
            raise ValueError("duplicate service request")
        self._keys.add(key)
        request_id = f"city_service_{len(self._requests) + 1:04d}"
        row = {"request_id": request_id, "service": service, "agent_id": agent_id,
               "region_id": region_id, "entity_ids": list(ids), "observation_refs": list(observation_refs),
               "mode": mode, "recipient_service_id": recipient, "target_region_id": target_region_id,
               "submitted_time_s": now,
               "status": "pending", "evidence_class": "request_only", "scoreable": False}
        self._requests[request_id] = row
        return deepcopy(row)

    def guide_people(self, *, agent_id: str, region_id: str, assembly_region_id: str,
                     person_ids: tuple[str, ...],
                     observation_refs: tuple[str, ...]) -> dict[str, Any]:
        return self._submit("guide_people", agent_id=agent_id, region_id=region_id,
                            ids=tuple(person_ids), target_region_id=assembly_region_id,
                            observation_refs=tuple(observation_refs))

    def control_following_traffic(self, *, agent_id: str, region_id: str,
                                  vehicle_ids: tuple[str, ...], mode: str,
                                  observation_refs: tuple[str, ...]) -> dict[str, Any]:
        return self._submit("control_traffic", agent_id=agent_id, region_id=region_id,
                            ids=tuple(vehicle_ids), mode=mode, observation_refs=tuple(observation_refs))

    def report_incident(self, *, agent_id: str, region_id: str, observation_refs: tuple[str, ...],
                        recipient_service_id: str) -> dict[str, Any]:
        return self._submit("report_incident", agent_id=agent_id, region_id=region_id,
                            observation_refs=tuple(observation_refs), recipient=recipient_service_id)

    def review(self, request_id: str) -> dict[str, Any]:
        """Read trusted service and physical evidence; fail closed on missing readback."""
        row = self._requests[request_id]
        if row["status"] != "pending":
            return deepcopy(row)
        now = _time(self.source.now_s())
        start = row["submitted_time_s"]
        if now < start:
            raise ValueError("episode clock moved backwards")
        # The public response budget is a hard episode boundary.  We still
        # inspect a supplied update so malformed timestamps fail with their
        # precise causal error, but a valid historical update cannot be
        # promoted after the boundary; otherwise the outcome would depend on
        # when the reviewer happened to poll the endpoint.
        within_response_budget = now <= self.policy["response_budget_s"]
        update = self.source.service_update(request_id)
        if update is not None:
            if update.get("request_id") != request_id or update.get("service") != row["service"]:
                raise ValueError("service readback identity mismatch")
            evidence_class = update.get("evidence_class")
            if evidence_class not in {"reference", "runtime"}:
                raise ValueError("service evidence provenance missing")
            if row["service"] == "report_incident":
                complete = self._review_report(row, update, now)
            else:
                complete = self._review_actors(row, update, now)
            if complete and within_response_budget:
                row["status"] = "evidence_complete" if evidence_class == "runtime" else "reference_complete"
                row["evidence_class"] = evidence_class
                row["evidence"] = deepcopy(dict(update))
        # The deadlines are public relative windows, never undisclosed timeouts.
        limit = {"guide_people": self.policy["people_guidance_ack_s"],
                 "control_traffic": self.policy["traffic_control_ack_s"],
                 "report_incident": self.policy["report_receipt_s"]}[row["service"]]
        timely_ack = False
        if update is not None and row["service"] != "report_incident":
            actors = update.get("actors")
            timely_ack = (isinstance(actors, list) and len(actors) == len(row["entity_ids"])
                          and all(isinstance(actor, Mapping) and actor.get("ack_time_s") is not None
                                  and start <= _time(actor["ack_time_s"]) <= start + limit
                                  for actor in actors))
        elif update is not None:
            receipt = update.get("receipt")
            timely_ack = (isinstance(receipt, Mapping) and receipt.get("received_time_s") is not None
                          and start <= _time(receipt["received_time_s"]) <= start + limit)
        arrival_deadline = start + limit + self.policy["people_assembly_arrival_s"]
        if timely_ack and row["service"] == "guide_people":
            arrival_deadline = max(_time(actor["ack_time_s"]) for actor in update["actors"]) + \
                self.policy["people_assembly_arrival_s"]
        if row["status"] == "pending" and (now > self.policy["response_budget_s"]
                                           or (now > start + limit and not timely_ack)
                                           or (timely_ack and row["service"] == "guide_people"
                                               and now > arrival_deadline)):
            row["status"] = "timed_out"
        return deepcopy(row)

    def _review_actors(self, request: Mapping[str, Any], update: Mapping[str, Any], now: float) -> bool:
        rows = update.get("actors")
        if not isinstance(rows, list):
            return False
        ids = [r.get("id") for r in rows if isinstance(r, Mapping)]
        if len(ids) != len(set(ids)) or set(ids) != set(request["entity_ids"]):
            raise ValueError("service evidence must bind each requested actor exactly once")
        start = request["submitted_time_s"]
        for actor in rows:
            if actor.get("ack_time_s") is None:
                return False
            ack = _time(actor.get("ack_time_s"))
            if not start <= ack <= now:
                raise ValueError("acknowledgement outside causal interval")
            deadline = (self.policy["people_guidance_ack_s"] if request["service"] == "guide_people"
                        else self.policy["traffic_control_ack_s"])
            if ack - start > deadline:
                return False
            if not self._role_covers(request, start, ack, now):
                raise ValueError("field role lost before service acknowledgement; new assignment must resubmit")
            if request["service"] == "guide_people":
                if actor.get("arrival_time_s") is None:
                    return False
                arrival = _time(actor.get("arrival_time_s"))
                if not ack <= arrival <= min(now, self.policy["response_budget_s"]):
                    raise ValueError("arrival outside causal interval")
                if arrival - ack > self.policy["people_assembly_arrival_s"]:
                    return False
                if (actor.get("assembly_region_id") != request["target_region_id"]
                        or actor.get("measured_trajectory") is not True
                        or actor.get("safe_route_and_assembly") is not True):
                    return False
            else:
                if actor.get("trajectory_check_time_s") is None:
                    return False
                check = _time(actor.get("trajectory_check_time_s"))
                if not ack <= check <= min(now, self.policy["response_budget_s"]):
                    raise ValueError("trajectory readback outside causal interval")
                if (check - ack < self.policy["traffic_reaction_window_s"]
                        or actor.get("applied_mode") != request["mode"]
                        or actor.get("measured_trajectory") is not True
                        or actor.get("no_hazard_ingress") is not True
                        or actor.get("rescue_access_preserved") is not True):
                    return False
        return True

    def _review_report(self, request: Mapping[str, Any], update: Mapping[str, Any], now: float) -> bool:
        receipt = update.get("receipt")
        if not isinstance(receipt, Mapping):
            return False
        if (receipt.get("recipient_service_id") != request["recipient_service_id"]
                or receipt.get("observation_refs") != request["observation_refs"]):
            raise ValueError("receipt must bind exact recipient and observation references")
        received = _time(receipt.get("received_time_s"))
        if not request["submitted_time_s"] <= received <= min(now, self.policy["response_budget_s"]):
            raise ValueError("receipt outside causal interval")
        if received - request["submitted_time_s"] > self.policy["report_receipt_s"]:
            return False
        access = update.get("continuous_access")
        if not isinstance(access, Mapping):
            return False
        begin, end = _time(access.get("start_time_s")), _time(access.get("end_time_s"))
        return (begin < end <= min(now, self.policy["response_budget_s"])
                and begin <= received <= end
                and access.get("measured_clear_throughout") is True
                and access.get("rescue_and_evacuation_routes_admitted") is True)


class CityServiceRequestAdapter:
    """Stable city operation names with deployment-owned assignment binding."""

    def __init__(self, backend: CityServiceBackend,
                 assignment: Callable[[str], Mapping[str, str]]):
        self.backend = backend
        self._assignment = assignment

    def _field(self, service: str) -> tuple[str, str]:
        row = self._assignment(service)
        agent_id, region_id = row.get("agent_id"), row.get("region_id")
        if not agent_id or not region_id:
            raise ValueError("service assignment requires agent_id and region_id")
        return agent_id, region_id

    def guide_people(self, *, person_ids: Iterable[str], observation_refs: Iterable[str],
                     assembly_region_id: str, now_s: float | None = None) -> dict[str, Any]:
        if now_s is not None and _time(now_s) != _time(self.backend.source.now_s()):
            raise ValueError("caller time does not match the trusted episode clock")
        agent_id, region_id = self._field("guide_people")
        return self.backend.guide_people(
            agent_id=agent_id, region_id=region_id, assembly_region_id=assembly_region_id,
            person_ids=tuple(person_ids), observation_refs=tuple(observation_refs))

    def control_following_traffic(self, vehicle_ids: Iterable[str], mode: str, *,
                                  observation_refs: Iterable[str]) -> dict[str, Any]:
        agent_id, region_id = self._field("control_traffic")
        return self.backend.control_following_traffic(
            agent_id=agent_id, region_id=region_id, vehicle_ids=tuple(vehicle_ids),
            mode=mode, observation_refs=tuple(observation_refs))

    def report_incident(self, observation_refs: Iterable[str], recipient_service_id: str, *,
                        time_s: float | None = None) -> dict[str, Any]:
        if time_s is not None and _time(time_s) != _time(self.backend.source.now_s()):
            raise ValueError("caller time does not match the trusted episode clock")
        agent_id, region_id = self._field("report_incident")
        return self.backend.report_incident(
            agent_id=agent_id, region_id=region_id, observation_refs=tuple(observation_refs),
            recipient_service_id=recipient_service_id)


__all__ = ["CityRuntimeReadbackAdapter", "CityServiceBackend", "CityServiceRequestAdapter",
           "CityServiceSource"]
