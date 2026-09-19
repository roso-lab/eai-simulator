"""Pure, fail-closed team lifecycle state machine for benchmark adapters.

This module records logical participant state only.  It does not add/remove
robots in Isaac, alter a scheduler, or transfer a carried object's ownership.
All inputs and returned records are deep copied.  A caller may forward the
returned ledger events through ``ParticipantSession`` when its event schema has
the corresponding event type; no future intervention is queued or exposed.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


_MEMBER_KEYS = {"id", "capabilities"}
_REQUIREMENT_KEYS = {
    "id", "domain", "navigation", "aerial_scan", "manipulation_interface",
    "required_capabilities", "same_team_as", "skill", "resources", "prerequisites",
}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a nonempty trimmed string")
    return value


def _time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("event time_s must be a finite nonnegative number")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("event time_s must be a finite nonnegative number")
    return value


def _member(value: Any, label: str = "member") -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) != _MEMBER_KEYS:
        raise ValueError(f"{label} fields must be exactly {sorted(_MEMBER_KEYS)}")
    result["id"] = _id(result["id"], f"{label}.id")
    capabilities = _mapping(result["capabilities"], f"{label}.capabilities")
    if not capabilities:
        raise ValueError(f"{label}.capabilities must not be empty")
    for key, item in capabilities.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label}.capabilities keys must be nonempty strings")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{label}.capabilities contains non-finite value for {key!r}")
        if isinstance(item, (str, int, float, bool)) or item is None:
            continue
        if isinstance(item, (list, tuple)) and all(isinstance(entry, str) and entry for entry in item):
            continue
        raise ValueError(f"{label}.capabilities contains unsupported value for {key!r}")
    result["capabilities"] = capabilities
    return result


def _requirement(value: Any) -> dict[str, Any]:
    result = _mapping(value, "participant requirement")
    unknown = set(result) - _REQUIREMENT_KEYS
    if unknown:
        raise ValueError(f"participant requirement has unknown fields {sorted(unknown)}")
    if "id" in result:
        result["id"] = _id(result["id"], "participant requirement.id")
    if result.get("domain") is not None:
        result["domain"] = _id(result["domain"], "participant requirement.domain")
    for key in ("navigation", "aerial_scan", "manipulation_interface"):
        if key in result and not isinstance(result[key], bool):
            raise ValueError(f"participant requirement.{key} must be boolean")
    required = result.get("required_capabilities", {})
    if not isinstance(required, Mapping):
        raise ValueError("participant requirement.required_capabilities must be an object")
    result["required_capabilities"] = deepcopy(dict(required))
    return result


def _capability_matches(capabilities: Mapping[str, Any], required: Any, label: str) -> bool:
    if isinstance(required, (list, tuple)):
        actual = capabilities.get(label, ())
        return isinstance(actual, (list, tuple, set, frozenset)) and set(required) <= set(actual)
    return capabilities.get(label) == required


class TeamLifecycle:
    """Manage logical members, qualifications, objectives, and release duties."""

    def __init__(self, initial_members: list[Mapping[str, Any]],
                 participant_requirements: list[Mapping[str, Any]] | None = None,
                 *, reserve_pool: list[Mapping[str, Any]] | None = None) -> None:
        if not isinstance(initial_members, list) or not initial_members:
            raise ValueError("initial_members must be a nonempty list")
        if reserve_pool is not None and not isinstance(reserve_pool, list):
            raise ValueError("reserve_pool must be a list")
        self._members: dict[str, dict[str, Any]] = {}
        self._requirements: dict[str, dict[str, Any]] = {}
        self._objectives: dict[str, dict[str, Any]] = {}
        self._carried: dict[str, tuple[str, ...]] = {}
        self._ledger: list[dict[str, Any]] = []
        self._time_s = 0.0
        self._initial_members = deepcopy(initial_members)
        self._initial_requirements = deepcopy(participant_requirements or [])
        for raw in initial_members:
            member = _member(raw, "initial member")
            if member["id"] in self._members:
                raise ValueError(f"duplicate member {member['id']!r}")
            self._members[member["id"]] = {
                "member": member, "status": "active", "capability_version": 1,
            }
        for raw in participant_requirements or []:
            requirement = _requirement(raw)
            identifier = requirement.get("id")
            if identifier is None or identifier in self._requirements:
                raise ValueError("participant requirements need unique ids")
            self._requirements[identifier] = requirement
        self._reserve: dict[str, dict[str, Any]] = {}
        if reserve_pool:
            # Registration is intentionally a state-changing operation, so
            # constructors cannot silently make a reserve member joinable.
            raise ValueError("reserve members require explicit register_reserve event")
        self._append_ledger("initialized", {"active_member_ids": sorted(self._members)})

    def _append_ledger(self, event_type: str, payload: Mapping[str, Any], *, time_s: float | None = None):
        event_time = self._time_s if time_s is None else _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        self._time_s = event_time
        self._ledger.append({"sequence": len(self._ledger), "time_s": event_time,
                             "type": event_type, "payload": deepcopy(dict(payload))})

    def _commit(self, event_type: str, payload: Mapping[str, Any], *, time_s: Any):
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        self._append_ledger(event_type, payload, time_s=event_time)
        return deepcopy(self._ledger[-1])

    def _member_state(self, member_id: Any) -> dict[str, Any]:
        member_id = _id(member_id, "member_id")
        if member_id not in self._members:
            raise ValueError(f"unknown member {member_id!r}")
        return self._members[member_id]

    def _eligible(self, member_id: str, requirement: Mapping[str, Any]) -> bool:
        state = self._member_state(member_id)
        if state["status"] != "active":
            return False
        requirement = _requirement(requirement)
        capabilities = state["member"]["capabilities"]
        if requirement.get("domain") is not None and capabilities.get("domain") != requirement["domain"]:
            return False
        if requirement.get("navigation") and capabilities.get("navigation_interface") is not True:
            return False
        if requirement.get("aerial_scan") and not (
                capabilities.get("aerial_scan") is True
                or (capabilities.get("domain") == "aerial" and capabilities.get("camera") is True)):
            return False
        if requirement.get("manipulation_interface") and not set(
                capabilities.get("attachment_types", ())) & {"ur5", "z1"}:
            return False
        return all(_capability_matches(capabilities, value, key)
                   for key, value in requirement.get("required_capabilities", {}).items())

    def qualified_members(self, requirement: Mapping[str, Any] | str) -> list[str]:
        """Return active eligible members, evaluated against current capabilities."""
        if isinstance(requirement, str):
            if requirement not in self._requirements:
                raise ValueError(f"unknown participant requirement {requirement!r}")
            requirement = self._requirements[requirement]
        requirement = _requirement(requirement)
        return sorted(member_id for member_id in self._members if self._eligible(member_id, requirement))

    def register_reserve(self, member: Mapping[str, Any], *, time_s: Any) -> dict[str, Any]:
        candidate = _member(member, "reserve member")
        identifier = candidate["id"]
        if identifier in self._members or identifier in self._reserve:
            raise ValueError(f"member {identifier!r} is already registered")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        self._reserve[identifier] = {"member": candidate, "status": "reserve",
                                     "capability_version": 1}
        return self._commit("reserve_registered", {"member": deepcopy(candidate),
            "member_id": identifier, "status": "reserve", "capability_version": 1}, time_s=event_time)

    def join(self, member_id: Any, *, time_s: Any) -> dict[str, Any]:
        identifier = _id(member_id, "member_id")
        if identifier not in self._reserve:
            raise ValueError("only an explicitly registered reserve member may join")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        state = self._reserve.pop(identifier)
        state["status"] = "active"
        self._members[identifier] = state
        return self._commit("member_join", {"member_id": identifier,
            "status": "active", "capability_version": state["capability_version"]}, time_s=event_time)

    def _in_flight_for(self, member_id: str) -> list[dict[str, Any]]:
        return [objective for objective in self._objectives.values()
                if objective["status"] in {"assigned", "running", "affected"}
                and member_id in objective["agent_ids"]]

    def exit(self, member_id: Any, *, time_s: Any) -> dict[str, Any]:
        identifier = _id(member_id, "member_id")
        state = self._member_state(identifier)
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        if state["status"] != "active":
            raise ValueError("only an active member may exit")
        affected = self._in_flight_for(identifier)
        release_required = [{"object_id": object_id, "carrier_id": identifier,
                             "status": "release_required",
                             "runtime_action": "release_at_runtime_carrier_pose",
                             "ownership_transferred": False}
                            for object_id, carrier in sorted(self._carried.items())
                            if identifier in carrier]
        state["status"] = "exited"
        for objective in affected:
            objective["status"] = "affected"
            objective["affected_reason"] = "member_exit"
        return self._commit("member_exit", {"member_id": identifier,
            "affected_objective_ids": [item["objective_id"] for item in affected],
            "release_required": release_required,
            "replacement_allowed": False}, time_s=event_time)

    def capability_change(self, member_id: Any, capabilities: Mapping[str, Any], *,
                          capability_version: Any, time_s: Any) -> dict[str, Any]:
        identifier = _id(member_id, "member_id")
        state = self._member_state(identifier)
        if state["status"] != "active":
            raise ValueError("capability_change requires an active member")
        replacement = _member({"id": identifier, "capabilities": capabilities}, "capability change")
        version = capability_version
        if isinstance(version, bool) or not isinstance(version, int) or version <= state["capability_version"]:
            raise ValueError("capability_version must be a strictly increasing integer")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        affected = []
        old = deepcopy(state["member"])
        try:
          for objective in self._in_flight_for(identifier):
            requirement = self._requirements.get(objective["requirement_id"], objective["requirement"])
            # Evaluate replacement against the old state without committing it.
            was_eligible = self._eligible(identifier, requirement)
            state["member"] = replacement
            try:
                remains_eligible = self._eligible(identifier, requirement)
            finally:
                state["member"] = old
            if was_eligible and not remains_eligible:
                affected.append(objective)
        except Exception:
            state["member"] = old
            raise
        state["member"] = replacement
        state["capability_version"] = version
        for objective in affected:
            objective["status"] = "affected"
            objective["affected_reason"] = "capability_change"
        release_required = [{"object_id": object_id, "carrier_id": identifier,
                             "status": "release_required",
                             "runtime_action": "release_at_runtime_carrier_pose",
                             "ownership_transferred": False}
                            for object_id, owners in sorted(self._carried.items())
                            if identifier in owners]
        return self._commit("capability_change", {"member_id": identifier,
            "capabilities": deepcopy(replacement["capabilities"]),
            "capability_version": version,
            "affected_objective_ids": [item["objective_id"] for item in affected],
            "release_required": release_required},
            time_s=event_time)

    def assign(self, objective_id: Any, requirement: Mapping[str, Any] | str,
               agent_ids: list[str], *, time_s: Any, carried_object_id: str | None = None) -> dict[str, Any]:
        objective_id = _id(objective_id, "objective_id")
        if objective_id in self._objectives and self._objectives[objective_id]["status"] in {"running", "affected"}:
            raise ValueError("cannot reassign an active or affected objective without explicit recovery")
        requirement_obj = self._requirements.get(requirement, None) if isinstance(requirement, str) else _requirement(requirement)
        if requirement_obj is None:
            raise ValueError(f"unknown participant requirement {requirement!r}")
        if not isinstance(agent_ids, list) or not agent_ids or len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent_ids must be a nonempty unique list")
        identifiers = [_id(agent, "agent_id") for agent in agent_ids]
        if carried_object_id is not None:
            object_id = _id(carried_object_id, "carried_object_id")
            if object_id not in self._carried:
                raise ValueError("carried_object_id must refer to registered runtime ownership")
            if tuple(identifiers) != self._carried[object_id]:
                raise ValueError("carried object ownership cannot be assigned to another team")
        else:
            object_id = None
        for identifier in identifiers:
            if not self._eligible(identifier, requirement_obj):
                raise ValueError(f"agent {identifier!r} is not currently eligible")
        if requirement_obj.get("skill") == "joint_transport" and len(identifiers) != 2:
            raise ValueError("joint_transport requires exactly two eligible agents")
        if requirement_obj.get("skill") != "joint_transport" and len(identifiers) != 1:
            raise ValueError("single-agent objective requires exactly one eligible agent")
        same_as = requirement_obj.get("same_team_as")
        if same_as is not None:
            prior = self._objectives.get(same_as)
            if prior is None or tuple(prior["agent_ids"]) != tuple(identifiers):
                raise ValueError("same_team_as requires the exact prior active team")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        record = {"objective_id": objective_id, "requirement_id": requirement_obj.get("id", objective_id),
                  "requirement": requirement_obj, "agent_ids": identifiers, "status": "assigned",
                  "carried_object_id": object_id}
        self._objectives[objective_id] = record
        return self._commit("objective_assigned", {"objective_id": objective_id,
            "agent_ids": identifiers, "requirement_id": record["requirement_id"],
            "requirement": deepcopy(requirement_obj),
            "carried_object_id": object_id, "ownership_transferred": False}, time_s=event_time)

    def record_pickup(self, object_id: Any, agent_ids: list[str], *, time_s: Any) -> dict[str, Any]:
        """Record runtime-confirmed ownership; this does not command a pickup."""
        object_id = _id(object_id, "object_id")
        if object_id in self._carried:
            raise ValueError("object ownership is already registered")
        if not isinstance(agent_ids, list) or not agent_ids or len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent_ids must be a nonempty unique list")
        identifiers = [_id(agent, "agent_id") for agent in agent_ids]
        if any(self._member_state(agent)["status"] != "active" for agent in identifiers):
            raise ValueError("ownership requires active members")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        self._carried[object_id] = tuple(identifiers)
        return self._commit("runtime_ownership_registered", {"object_id": object_id,
            "ownership_ids": identifiers, "ownership_transferred": False}, time_s=event_time)

    def start_objective(self, objective_id: Any, *, time_s: Any) -> dict[str, Any]:
        identifier = _id(objective_id, "objective_id")
        if identifier not in self._objectives or self._objectives[identifier]["status"] != "assigned":
            raise ValueError("only an assigned objective may start")
        objective = self._objectives[identifier]
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        if any(not self._eligible(agent, objective["requirement"]) for agent in objective["agent_ids"]):
            objective["status"] = "affected"
            objective["affected_reason"] = "member_no_longer_eligible"
            raise ValueError("objective lost member qualification; explicit recovery is required")
        objective["status"] = "running"
        return self._commit("objective_started", {"objective_id": identifier}, time_s=event_time)

    def finish_objective(self, objective_id: Any, *, time_s: Any, status: str = "completed") -> dict[str, Any]:
        identifier = _id(objective_id, "objective_id")
        if status not in {"completed", "terminated"}:
            raise ValueError("objective status must be completed or terminated")
        objective = self._objectives.get(identifier)
        if objective is None or objective["status"] not in {"assigned", "running", "affected"}:
            raise ValueError("objective is not finishable")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        objective["status"] = status
        return self._commit("objective_finished", {"objective_id": identifier, "status": status}, time_s=event_time)

    def terminate_objective(self, objective_id: Any, *, time_s: Any) -> dict[str, Any]:
        return self.finish_objective(objective_id, time_s=time_s, status="terminated")

    def release_object(self, object_id: Any, *, carrier_id: Any, pose_xyz: Any,
                       time_s: Any, reason: str = "member_lifecycle") -> dict[str, Any]:
        object_id = _id(object_id, "object_id")
        carrier_id = _id(carrier_id, "carrier_id")
        if carrier_id not in self._carried.get(object_id, ()):
            raise ValueError("object is not carried by this member")
        if not isinstance(pose_xyz, (list, tuple)) or len(pose_xyz) != 3:
            raise ValueError("runtime release pose_xyz must contain three numeric coordinates")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in pose_xyz):
            raise ValueError("runtime release pose_xyz must contain three numeric coordinates")
        pose = tuple(float(value) for value in pose_xyz)
        if len(pose) != 3 or not all(math.isfinite(value) for value in pose):
            raise ValueError("runtime release pose_xyz must contain three finite coordinates")
        reason = _id(reason, "release reason")
        event_time = _time(time_s)
        if event_time < self._time_s:
            raise ValueError("event time_s must be monotonic")
        ownership_before = list(self._carried[object_id])
        del self._carried[object_id]
        for objective in self._objectives.values():
            if objective.get("carried_object_id") == object_id:
                objective["release_status"] = "released"
        return self._commit("object_release_required_runtime", {"object_id": object_id,
            "carrier_id": carrier_id, "ownership_ids": ownership_before,
            "pose_xyz": list(pose), "reason": reason,
            "ownership_transferred": False, "runtime_release_recorded": True}, time_s=event_time)

    def apply_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one already-visible event; no future event schedule is accepted."""
        envelope = _mapping(event, "lifecycle event")
        if set(envelope) - {"type", "time_s", "payload", "sequence"} != set() or not {"type", "time_s", "payload"} <= set(envelope):
            raise ValueError("lifecycle event fields must be exactly type, time_s, payload")
        event_type = _id(envelope["type"], "event type")
        payload = _mapping(envelope["payload"], "lifecycle event.payload")
        time_s = _time(envelope["time_s"])
        if event_type == "reserve_registered":
            if "member" not in payload or set(payload) - {"member", "member_id", "status", "capability_version"}:
                raise ValueError("reserve_registered payload requires member")
            return self.register_reserve(payload["member"], time_s=time_s)
        if event_type == "member_join":
            if "member_id" not in payload or set(payload) - {"member_id", "status", "capability_version"}:
                raise ValueError("member_join payload must contain exactly member_id")
            return self.join(payload["member_id"], time_s=time_s)
        if event_type == "member_exit":
            if "member_id" not in payload or set(payload) - {"member_id", "affected_objective_ids", "release_required", "replacement_allowed"}:
                raise ValueError("member_exit payload must contain exactly member_id")
            return self.exit(payload["member_id"], time_s=time_s)
        if event_type == "capability_change":
            required = {"member_id", "capabilities", "capability_version"}
            if not required <= set(payload) or set(payload) - required - {"affected_objective_ids", "release_required"}:
                raise ValueError(f"capability_change payload requires {sorted(required)}")
            return self.capability_change(payload["member_id"], payload["capabilities"],
                                          capability_version=payload["capability_version"], time_s=time_s)
        if event_type == "object_release_required_runtime":
            required = {"object_id", "carrier_id", "pose_xyz"}
            if not required <= set(payload) or set(payload) - required - {"reason"}:
                raise ValueError("object_release_required_runtime payload is malformed")
            return self.release_object(payload["object_id"], carrier_id=payload["carrier_id"],
                                       pose_xyz=payload["pose_xyz"], time_s=time_s,
                                       reason=payload.get("reason", "member_lifecycle"))
        if event_type == "runtime_ownership_registered":
            required = {"object_id", "ownership_ids"}
            if not required <= set(payload) or set(payload) - required - {"ownership_transferred"}:
                raise ValueError("runtime_ownership_registered payload is malformed")
            return self.record_pickup(payload["object_id"], payload["ownership_ids"], time_s=time_s)
        if event_type == "objective_assigned":
            requirement = self._requirements.get(payload["requirement_id"], payload.get("requirement"))
            return self.assign(payload["objective_id"], requirement, payload["agent_ids"],
                               carried_object_id=payload.get("carried_object_id"), time_s=time_s)
        if event_type == "objective_started":
            return self.start_objective(payload["objective_id"], time_s=time_s)
        if event_type == "objective_finished":
            return self.finish_objective(payload["objective_id"], status=payload["status"], time_s=time_s)
        raise ValueError(f"unsupported lifecycle event {event_type!r}")

    def public_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Apply a visible event and expose only current, participant-facing fields."""
        if not isinstance(event, Mapping) or event.get("type") not in {
                "member_join", "capability_change", "member_exit"}:
            raise ValueError("event type is evaluator-only and has no public ParticipantSession form")
        applied = self.apply_event(event)
        payload = applied["payload"]
        if applied["type"] == "member_join":
            member = self._members[payload["member_id"]]
            return {"type": "member_join", "time_s": applied["time_s"],
                    "payload": {"agent_id": payload["member_id"],
                                 "capability_declaration": deepcopy(member["member"]["capabilities"]),
                                 "capability_version": member["capability_version"]}}
        if applied["type"] == "capability_change":
            member = self._members[payload["member_id"]]
            return {"type": "capability_change", "time_s": applied["time_s"],
                    "payload": {"agent_id": payload["member_id"],
                                 "capability_declaration": deepcopy(member["member"]["capabilities"]),
                                 "capability_version": member["capability_version"],
                                 "affected_node_ids": deepcopy(payload["affected_objective_ids"] or [])}}
        if applied["type"] == "member_exit":
            return {"type": "member_exit", "time_s": applied["time_s"],
                    "payload": {"agent_id": payload["member_id"],
                                 "affected_node_ids": deepcopy(payload["affected_objective_ids"])}}
        raise ValueError("event type is evaluator-only and has no public ParticipantSession form")

    @classmethod
    def replay_validate(cls, initial_members, participant_requirements, command_log):
        """Rebuild state from initial members and visible commands, rejecting divergence."""
        replay = cls(deepcopy(initial_members), deepcopy(participant_requirements))
        for command in deepcopy(command_log):
            envelope = {key: command[key] for key in ("type", "time_s", "payload")}
            payload = envelope["payload"]
            if envelope["type"] == "reserve_registered":
                envelope["payload"] = {"member": payload["member"]}
            elif envelope["type"] == "member_join":
                envelope["payload"] = {"member_id": payload["member_id"]}
            elif envelope["type"] == "member_exit":
                envelope["payload"] = {"member_id": payload["member_id"]}
            elif envelope["type"] == "capability_change":
                envelope["payload"] = {key: payload[key] for key in
                                         ("member_id", "capabilities", "capability_version")}
            replay.apply_event(envelope)
        return replay.snapshot()

    def snapshot(self) -> dict[str, Any]:
        """Return evaluator-only state, including the explicit reserve pool."""
        return deepcopy({
            "schema": "eai.benchmark.team_lifecycle",
            "version": 1,
            "time_s": self._time_s,
            "members": {identifier: {"status": state["status"],
                         "capabilities": state["member"]["capabilities"],
                         "capability_version": state["capability_version"]}
                         for identifier, state in sorted(self._members.items())},
            "reserve_pool": {identifier: {"status": state["status"],
                             "capabilities": state["member"]["capabilities"],
                             "capability_version": state["capability_version"]}
                             for identifier, state in sorted(self._reserve.items())},
            "object_ownership": {object_id: list(owner_ids)
                                 for object_id, owner_ids in self._carried.items()},
            "objectives": {identifier: {key: value for key, value in objective.items()
                         if key not in {"requirement"}}
                         for identifier, objective in sorted(self._objectives.items())},
        })

    def public_snapshot(self) -> dict[str, Any]:
        """Return the participant view; reserve membership is evaluator-only."""
        view = self.snapshot()
        view.pop("reserve_pool", None)
        return view

    def ledger(self) -> list[dict[str, Any]]:
        return deepcopy(self._ledger)

    def report(self) -> dict[str, Any]:
        """Return the complete evaluator report and replayable command log."""
        return deepcopy({"initial_members": self._initial_members,
                         "participant_requirements": self._initial_requirements,
                         "command_log": self._ledger[1:],
                         "ledger": self._ledger,
                         "snapshot": self.snapshot()})

    @classmethod
    def validate_lifecycle_report(cls, report: Mapping[str, Any]) -> dict[str, Any]:
        """Replay every command and reject forged ledger or final snapshot data."""
        data = _mapping(report, "lifecycle report")
        required = {"initial_members", "participant_requirements", "command_log", "ledger", "snapshot"}
        if set(data) != required or not isinstance(data["command_log"], list) or not isinstance(data["ledger"], list):
            raise ValueError("lifecycle report fields are malformed")
        replay = cls(data["initial_members"], data["participant_requirements"])
        for command in data["command_log"]:
            replay.apply_event(command)
        if replay.ledger() != data["ledger"] or replay.snapshot() != data["snapshot"]:
            raise ValueError("lifecycle report ledger or snapshot does not match replay")
        return replay.snapshot()


__all__ = ["TeamLifecycle"]
