"""Scene-independent benchmark-owned logical task objects.

Only host controller observations and completed skill sessions mutate this
ledger. Algorithms receive decisions/skill APIs, never this mutable object.
No USD, geometry, attachment, contact or rigid-body state belongs here.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
import math
from typing import Any
from .contracts import CHECKPOINT, MODEL, ControllerObservation, SkillSessionResult, digest
ACQUIRE = {"pick", "load", "receive"}
RELEASE = {"place", "deliver", "store"}


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def tick(value):
    if type(value) is not int or value < 0:
        raise ValueError("logical object clock must be a nonnegative integer tick")
    return value


def _evidence(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("controller/session evidence must have a SHA-256 digest")


class LogicalObjectRuntime:
    """Host-injected executor and evaluator ledger, shared by all scenes.

    ``observe`` reads actual controller arrival; ``run_session`` executes the
    host skill and returns its completion. ``monitor_session`` samples ongoing
    joint membership at *every* transport tick. None are accepted from tested
    system command parameters. ``admission`` consumes frozen metadata and a
    host-defined per-holder load share, never an algorithm-provided payload.
    """

    name = "task.runtime.logical_objects"
    version = 1
    priority = 80

    @classmethod
    def from_case_manifest(cls, manifest, **host_callbacks):
        """Install the same logical execution model for any case/scene."""
        from benchmark.case_manifest_v1 import validate_case_manifest
        value = validate_case_manifest(manifest)
        binding = value["scene"].get("object_bindings", {})
        if binding.get("schema") != "heterocoord.logical-task-object-bindings.v1":
            raise ValueError("production logical runtime requires independent manifest object definitions")
        robots = [row["robot_id"] for row in value["robot_pool"]["robots"]]
        if "robot_ids" in host_callbacks:
            raise ValueError("deployed robot IDs come only from the frozen case manifest")
        return cls(scene_id=value["scene"]["scene_id"], objects=binding["objects"], robot_ids=robots, **host_callbacks)

    def __init__(self, *, scene_id, objects, robot_ids, clock, observe, run_session,
                 admission, monitor_session=None):
        self.scene_id = text(scene_id, "scene_id")
        if not isinstance(objects, list) or not objects:
            raise ValueError("logical objects must be a nonempty list")
        self.definitions = {}
        for row in objects:
            if not isinstance(row, Mapping) or set(row) != {
                "entity_id", "category", "metadata", "initial_semantic_point", "target_semantic_point"
            }:
                raise ValueError("logical object definition schema mismatch")
            entity = text(row["entity_id"], "entity_id")
            if entity in self.definitions:
                raise ValueError("duplicate logical object ID")
            text(row["category"], "category")
            text(row["initial_semantic_point"], "initial_semantic_point")
            text(row["target_semantic_point"], "target_semantic_point")
            metadata = row["metadata"]
            if not isinstance(metadata, Mapping):
                raise ValueError("object metadata must be a mapping")
            if any(k in metadata for k in ("prim_path", "attachment", "rigid_body", "size_xyz_m", "geometry")):
                raise ValueError("task object metadata must not require physical object state")
            if "mass_kg" in metadata and (isinstance(metadata["mass_kg"], bool) or not isinstance(metadata["mass_kg"], (int, float)) or not math.isfinite(metadata["mass_kg"]) or metadata["mass_kg"] <= 0):
                raise ValueError("object mass must be finite and positive")
            minimum = metadata.get("minimum_holders", 1)
            if type(minimum) is not int or minimum < 1:
                raise ValueError("minimum_holders must be a positive integer")
            self.definitions[entity] = deepcopy(dict(row))
        if isinstance(robot_ids, (str, bytes)) or not isinstance(robot_ids, Sequence) or not robot_ids:
            raise ValueError("explicit deployed robot IDs are required")
        self.robot_ids = tuple(sorted(text(r, "robot ID") for r in robot_ids))
        if len(set(self.robot_ids)) != len(self.robot_ids):
            raise ValueError("duplicate deployed robot ID")
        for callback in (clock, observe, run_session, admission):
            if not callable(callback):
                raise TypeError("host clock, controller, session and admission callbacks are required")
        self._clock, self._observe, self._run_session = clock, observe, run_session
        self._admission, self._monitor = admission, monitor_session
        self.definition_digest = digest({"scene_id": scene_id, "objects": self.definitions, "robot_ids": self.robot_ids})
        self._states = {key: {"state": "at_point", "semantic_point": row["initial_semantic_point"], "holders": [], "hold_sessions": {}, "last_joint_tick": None} for key, row in self.definitions.items()}
        self._sessions, self._requests = {}, {}
        self._inflight = set()
        self._events = []
        self._last_tick = -1

    def _now(self):
        now = tick(self._clock())
        if now < self._last_tick:
            raise ValueError("logical object clock moved backwards without checkpoint restore")
        self._last_tick = now
        return now

    def _robots(self, ids):
        if isinstance(ids, (str, bytes)) or not isinstance(ids, Sequence) or not ids:
            raise ValueError("skill robot IDs must be a nonempty sequence")
        ids = tuple(sorted(text(r, "robot ID") for r in ids))
        if len(set(ids)) != len(ids) or not set(ids).issubset(self.robot_ids):
            raise ValueError("skill robots must be a unique deployed-pool subset")
        return ids

    def _arrivals(self, ids, point, now):
        records = []
        for robot in ids:
            value = self._observe(robot, point)
            if not isinstance(value, ControllerObservation) or type(value.tick) is not int or value.arrived is not True or (value.robot_id, value.semantic_point, value.tick) != (robot, point, now):
                raise ValueError("controller has not reached the registered operation point at the current tick")
            _evidence(value.evidence_digest)
            records.append(dict(value.__dict__))
        return records

    def execute(self, *, request_id, operation, object_id, robot_ids, semantic_point):
        """Execute a semantic skill, committing state only after host evidence.

        One robot may hold one task object. Joint pickup is atomic: acquiring
        fewer than minimum_holders never produces a transient valid lift.
        """
        request_id = text(request_id, "request_id")
        operation = text(operation, "operation")
        ids = self._robots(robot_ids)
        if object_id not in self.definitions:
            raise ValueError("unknown logical object ID")
        if operation not in ACQUIRE | RELEASE | {"join", "leave"}:
            raise ValueError("unsupported logical object skill")
        point = text(semantic_point, "semantic_point")
        command = {"request_id": request_id, "operation": operation, "object_id": object_id, "robot_ids": list(ids), "semantic_point": point}
        if request_id in self._requests:
            old = self._requests[request_id]
            if old["command"] != command:
                raise ValueError("request ID reused with changed command")
            return deepcopy(old["result"])
        if self._inflight:
            raise ValueError("unresolved host session quarantines execution; do not silently retry")
        now = self._now()
        state, definition = self._states[object_id], self.definitions[object_id]
        if state["state"] == "interrupted":
            raise ValueError("interrupted logical holding cannot be promoted to task success")
        minimum = definition["metadata"].get("minimum_holders", 1)
        holders = set(state["holders"])
        transferring = operation == "receive" and bool(holders)
        if transferring:
            if holders & set(ids) or len(ids) < minimum:
                raise ValueError("receive requires distinct replacement holders")
            next_holders = set(ids)
        elif operation in ACQUIRE:
            if holders or state["state"] != "at_point" or point != state["semantic_point"] or len(ids) < minimum:
                raise ValueError("pickup requires the object at its point and enough simultaneous holders")
            next_holders = set(ids)
        elif operation in RELEASE:
            if set(ids) != holders or not holders or point != definition["target_semantic_point"]:
                raise ValueError("delivery requires all legal holders at the registered target")
            next_holders = set()
        elif operation == "join":
            if not holders or holders & set(ids):
                raise ValueError("joint replacement requires new distinct holders")
            next_holders = holders | set(ids)
        else:
            if not set(ids).issubset(holders) or not holders:
                raise ValueError("release requires legal holders")
            next_holders = holders - set(ids)
            if len(next_holders) < minimum:
                raise ValueError("replacement must join before a holder leaves")
        if operation in ACQUIRE | {"join"}:
            for robot in ids:
                if any(robot in s["holders"] for s in self._states.values()):
                    raise ValueError("robot is already holding a task object")
        # All current holders must co-locate for transfer/replacement. In
        # transport their ongoing session is checked by sample_holds instead.
        participants = tuple(sorted(holders | set(ids)))
        arrivals = self._arrivals(participants, point, now)
        load_members = next_holders or holders
        for robot in load_members:
            if self._admission(robot, deepcopy(definition), len(load_members)) is not True:
                raise ValueError("logical object skill capability/load admission rejected")
        if holders and minimum > 1:
            self.sample_holds()  # Includes continuity, before committing a release.
            if state["state"] == "interrupted":
                raise ValueError("joint holding interrupted before skill execution")
        self._inflight.add(request_id)
        # Keep a consumed marker if the host raises or returns malformed
        # evidence: its side effects cannot safely be assumed absent.
        result = self._run_session(deepcopy(command))
        end = self._now()
        if not isinstance(result, SkillSessionResult) or type(result.tick) is not int or (
            result.request_id, tuple(result.robot_ids), result.object_id, result.operation,
            result.semantic_point, result.tick
        ) != (request_id, ids, object_id, operation, point, end):
            raise ValueError("host skill-session result identity does not match the command")
        text(result.session_id, "session_id")
        _evidence(result.evidence_digest)
        if result.session_id in self._sessions:
            raise ValueError("host reused a skill session ID")
        if result.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("host skill session is not terminal")
        # Re-read at completion, not just at request time.
        if result.status == "completed":
            arrivals = self._arrivals(participants, point, end)
            if holders and minimum > 1:
                self.sample_holds()
                if state["state"] == "interrupted":
                    raise ValueError("joint holding interrupted during skill execution")
        record = {**result.__dict__, "robot_ids": list(ids), "arrivals": arrivals}
        self._sessions[result.session_id] = record
        self._requests[request_id] = {"command": command, "result": record}
        self._inflight.remove(request_id)
        self._events.append({"tick": end, "kind": "skill_session", "session": deepcopy(record)})
        if result.status != "completed":
            return deepcopy(record)
        if operation in ACQUIRE | {"join"}:
            if transferring:
                state["hold_sessions"].clear()
            state["hold_sessions"].update({robot: result.session_id for robot in ids})
        else:
            for robot in ids:
                state["hold_sessions"].pop(robot)
        state["holders"] = sorted(next_holders)
        state["state"] = "held_by" if next_holders else "delivered"
        state["semantic_point"] = point if not next_holders else None
        state["last_joint_tick"] = end if len(next_holders) >= minimum and minimum > 1 else None
        return deepcopy(record)

    def sample_holds(self):
        """Sample exact-roster hold state; joint membership needs live sessions."""
        now = self._now()
        for entity, state in self._states.items():
            minimum = self.definitions[entity]["metadata"].get("minimum_holders", 1)
            if not state["holders"] or minimum == 1:
                continue
            if state["last_joint_tick"] is None or now - state["last_joint_tick"] > 1:
                state["state"] = "interrupted"
                self._events.append({"tick": now, "kind": "joint_observation_missing", "object_id": entity})
                raise ValueError("joint holding observation gap; missing transport samples")
            if not callable(self._monitor):
                state["state"] = "interrupted"
                raise ValueError("joint holding requires a host session monitor")
            live, observations = [], []
            try:
                for robot in state["holders"]:
                    session_id = state["hold_sessions"][robot]
                    observation = self._monitor(session_id, robot, entity, now)
                    if not isinstance(observation, Mapping) or set(observation) != {"session_id", "robot_id", "object_id", "tick", "active", "evidence_digest"}:
                        raise ValueError("joint skill membership observation is missing")
                    if type(observation["tick"]) is not int or (observation["session_id"], observation["robot_id"], observation["object_id"], observation["tick"]) != (session_id, robot, entity, now) or type(observation["active"]) is not bool:
                        raise ValueError("joint skill membership identity/tick mismatch")
                    _evidence(observation["evidence_digest"])
                    if observation["active"]:
                        live.append(robot)
                    observations.append({"tick": now, "kind": "joint_membership", "observation": dict(observation)})
            except Exception:
                state["state"] = "interrupted"
                self._events.append({"tick": now, "kind": "joint_observation_invalid", "object_id": entity})
                raise
            self._events.extend(observations)
            state["hold_sessions"] = {robot: state["hold_sessions"][robot] for robot in live}
            state["holders"] = live
            state["last_joint_tick"] = now
            if len(live) < minimum:
                state["state"] = "interrupted"
                self._events.append({"tick": now, "kind": "joint_hold_below_minimum", "object_id": entity, "holders": live})
        result = {robot: None for robot in self.robot_ids}
        for entity, state in self._states.items():
            for robot in state["holders"]:
                result[robot] = entity
        return result

    def object_state(self, entity):
        return deepcopy(self._states[entity])

    def session_record(self, session_id):
        return deepcopy(self._sessions[session_id])

    def capture_state(self):
        if self._inflight:
            raise ValueError("checkpoint cannot capture unresolved host skill sessions")
        body = {"schema": CHECKPOINT, "scene_id": self.scene_id, "definition_digest": self.definition_digest,
                "clock_tick": self._now(), "objects": deepcopy(self._states), "sessions": deepcopy(self._sessions),
                "requests": deepcopy(self._requests), "events": deepcopy(self._events)}
        return {**body, "state_digest": digest(body)}

    def restore_state(self, state, *, expected_digest):
        """Restore only from an externally pinned evaluator checkpoint.

        The outer replay coordinator restores robot physics, queues, locks and
        RNG using their own components; no task-object geometry is recreated.
        """
        if not isinstance(state, Mapping) or set(state) != {"schema", "scene_id", "definition_digest", "clock_tick", "objects", "sessions", "requests", "events", "state_digest"}:
            raise ValueError("logical object checkpoint schema mismatch")
        body = {k: v for k, v in state.items() if k != "state_digest"}
        if state["state_digest"] != expected_digest or digest(body) != expected_digest:
            raise ValueError("logical object checkpoint differs from external registration")
        if (state["schema"], state["scene_id"], state["definition_digest"]) != (CHECKPOINT, self.scene_id, self.definition_digest):
            raise ValueError("logical object checkpoint case/roster binding changed")
        if tick(self._clock()) != tick(state["clock_tick"]):
            raise ValueError("restore the registered simulation clock before logical objects")
        if set(state["objects"]) != set(self.definitions):
            raise ValueError("logical object checkpoint identities changed")
        held = set()
        for entity, obj in state["objects"].items():
            if set(obj) != {"state", "semantic_point", "holders", "hold_sessions", "last_joint_tick"}:
                raise ValueError("logical object checkpoint state fields mismatch")
            holders = obj["holders"]
            last_joint_tick = obj["last_joint_tick"]
            if last_joint_tick is not None and tick(last_joint_tick) > state["clock_tick"]:
                raise ValueError("restored joint observation tick is in the future")
            if not isinstance(holders, list) or len(set(holders)) != len(holders) or not set(holders).issubset(self.robot_ids) or held & set(holders):
                raise ValueError("invalid restored holder ownership")
            held.update(holders)
            if set(obj["hold_sessions"]) != set(holders):
                raise ValueError("restored holder session coverage mismatch")
            for robot, session in obj["hold_sessions"].items():
                record = state["sessions"].get(session, {})
                if record.get("object_id") != entity or robot not in record.get("robot_ids", []) or record.get("status") != "completed":
                    raise ValueError("restored holder lacks a successful bound skill session")
        self._states, self._sessions = deepcopy(state["objects"]), deepcopy(state["sessions"])
        self._requests, self._events = deepcopy(state["requests"]), deepcopy(state["events"])
        self._inflight.clear()
        self._last_tick = state["clock_tick"]


__all__ = ["MODEL", "ControllerObservation", "SkillSessionResult", "LogicalObjectRuntime"]
