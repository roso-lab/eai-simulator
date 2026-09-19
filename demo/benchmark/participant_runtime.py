"""Validated event/action boundary for benchmark participant adapters."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Protocol

from demo.benchmark.challenge_contract import (
    validate_participant_action,
    validate_participant_event,
)
from demo.benchmark.provenance import canonical_sha256


class ParticipantAdapter(Protocol):
    def manifest(self) -> Mapping[str, Any]: ...

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class RuntimeLifecyclePhysicalCallback(Protocol):
    """Transaction callback for one Isaac-side lifecycle mutation.

    The callback owns the simulator operation.  It must either raise without
    leaving a partial mutation, or return physical evidence containing the
    post-operation agent set and capability versions.  The boundary publishes
    the participant event only after validating that evidence.
    """

    def __call__(
        self,
        event: Mapping[str, Any],
        *,
        expected_actual_agent_ids: frozenset[str],
        expected_capability_versions: Mapping[str, int],
    ) -> Mapping[str, Any]: ...


class RuntimeLifecycleUnsupported(ValueError):
    """Raised when an Isaac-side lifecycle event cannot be safely committed.

    The attached result is an auditable ``unknown`` or ``failed`` result.
    Callers may put it in an execution report before stopping the episode;
    they must not turn an uncommitted physical operation into a participant
    membership, capability, or task-completion claim.
    """

    def __init__(self, result: Mapping[str, Any]):
        self.result = deepcopy(dict(result))
        super().__init__(self.result["reason"])


class IsaacRuntimeLifecycleBoundary:
    """Audit lifecycle changes that can be applied inside one Isaac session.

    ``member_exit`` retains the preloaded replacement behavior.  ``member_join``
    and ``capability_change`` may additionally be enabled by explicitly
    injected physical callbacks.  A callback is treated as a transaction: it
    must return post-operation entity/controller evidence and an exact runtime
    membership/version snapshot before the public event is committed.
    """

    def __init__(self, participant_session: "ParticipantSession", *,
                 available_agent_ids: set[str] | frozenset[str],
                 actual_agent_ids: set[str] | frozenset[str],
                 robot_types: Mapping[str, str] | None = None,
                 member_join_callback: RuntimeLifecyclePhysicalCallback | None = None,
                 capability_change_callback: RuntimeLifecyclePhysicalCallback | None = None,
                 member_exit_deactivate_callback=None):
        self.participant = participant_session
        self._available = set(available_agent_ids)
        self._actual = set(actual_agent_ids)
        self._initial_active = {
            robot["id"] for robot in participant_session.task_spec.get("initial_fleet", ())
        }
        self._active = set(self._initial_active)
        self._robot_types = dict(robot_types or {})
        self._member_join_callback = member_join_callback
        self._capability_change_callback = capability_change_callback
        self._member_exit_deactivate_callback = member_exit_deactivate_callback
        self._events: list[dict[str, Any]] = []
        self._last_time_s = 0.0
        self._capability_versions = {
            agent_id: 1 for agent_id in self._actual
        }
        # Keep the pure lifecycle ledger beside the physical boundary.  It is
        # state evidence only; TeamLifecycle never creates an Isaac body.
        from demo.benchmark.team_lifecycle import TeamLifecycle
        initial_members = [{
            "id": robot["id"],
            "capabilities": deepcopy(robot.get("capability_declaration") or {}),
        } for robot in participant_session.task_spec.get("initial_fleet", ())]
        self._team = TeamLifecycle(
            initial_members,
            participant_session.task_spec.get("participant_requirements", ()),
        )

    def _participant_snapshot(self) -> dict[str, Any]:
        """Snapshot mutable session state before a physical transaction."""
        names = (
            "events", "actions", "_pending_events", "_action_ids", "_sequence",
            "_turn", "_last_event_time_s", "_active_fleet", "_capability_versions",
        )
        return {name: deepcopy(getattr(self.participant, name)) for name in names}

    def _restore_participant_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        for name, value in snapshot.items():
            setattr(self.participant, name, deepcopy(value))

    def _boundary_snapshot(self) -> dict[str, Any]:
        return {
            "actual": deepcopy(self._actual),
            "active": deepcopy(self._active),
            "capability_versions": deepcopy(self._capability_versions),
            "team": deepcopy(self._team),
            "events": deepcopy(self._events),
            "last_time_s": self._last_time_s,
        }

    def _restore_boundary_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self._actual = set(snapshot["actual"])
        self._active = set(snapshot["active"])
        self._capability_versions = dict(snapshot["capability_versions"])
        self._team = deepcopy(snapshot["team"])
        self._events = deepcopy(snapshot["events"])
        self._last_time_s = snapshot["last_time_s"]

    @property
    def active_agent_ids(self) -> frozenset[str]:
        return frozenset(self._active)

    @property
    def available_agent_ids(self) -> frozenset[str]:
        return frozenset(self._available)

    def _unsupported(self, event: Mapping[str, Any], *, reason: str,
                     time_s: float | None = None) -> dict[str, Any]:
        result = {
            "event": "runtime_lifecycle_unsupported",
            "status": "unknown",
            "validated": False,
            "kind": event.get("kind"),
            "fault_id": event.get("id"),
            "agent": event.get("agent"),
            "replacement_agent": event.get("replacement_agent"),
            "time_s": time_s if time_s is not None else event.get("time_s"),
            "reason": reason,
            "physical_membership_changed": False,
            "capability_changed": False,
            "task_completion_claimed": False,
        }
        self._events.append(deepcopy(result))
        return result

    def _failed(self, event: Mapping[str, Any], *, reason: str,
                time_s: float | None = None) -> dict[str, Any]:
        result = {
            "event": "runtime_lifecycle_failed",
            "status": "failed",
            "validated": False,
            "kind": event.get("kind"),
            "fault_id": event.get("id"),
            "agent": event.get("agent"),
            "replacement_agent": event.get("replacement_agent"),
            "time_s": time_s if time_s is not None else event.get("time_s"),
            "reason": reason,
            "physical_membership_changed": None,
            "capability_changed": None,
            "logical_state_rolled_back": True,
            "physical_compensation_confirmed": False,
            "physical_state_after_failure": "unknown_requires_callback_compensation_audit",
            "task_completion_claimed": False,
        }
        self._events.append(deepcopy(result))
        return result

    @staticmethod
    def _agent_ids(value: Any, *, label: str) -> set[str]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError(f"{label} must be a sequence of agent ids")
        result = set(value)
        if len(result) != len(value) or any(
                not isinstance(agent_id, str) or not agent_id for agent_id in result):
            raise ValueError(f"{label} must contain unique nonempty strings")
        return result

    @staticmethod
    def _versions(value: Any, *, label: str) -> dict[str, int]:
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} must be an object")
        result = dict(value)
        if any(not isinstance(agent_id, str) or not agent_id for agent_id in result):
            raise ValueError(f"{label} keys must be nonempty strings")
        if any(type(version) is not int or version < 1
               for version in result.values()):
            raise ValueError(f"{label} values must be positive integers")
        return result

    def _validate_physical_evidence(
            self, event: Mapping[str, Any], evidence: Any, *,
            expected_actual: set[str],
            expected_versions: Mapping[str, int]) -> dict[str, Any]:
        if not isinstance(evidence, Mapping):
            raise ValueError("physical lifecycle callback must return an object")
        actual = self._agent_ids(
            evidence.get("actual_agent_ids"), label="physical actual_agent_ids")
        versions = self._versions(
            evidence.get("capability_versions"), label="physical capability_versions")
        if actual != expected_actual:
            raise ValueError(
                f"physical agent set mismatch: expected {sorted(expected_actual)}, "
                f"got {sorted(actual)}")
        if versions != dict(expected_versions):
            raise ValueError(
                f"physical capability versions mismatch: expected {dict(expected_versions)}, "
                f"got {versions}")
        kind = event["kind"]
        if kind == "member_join":
            entity = evidence.get("entity")
            if not isinstance(entity, Mapping):
                raise ValueError("member_join evidence requires an entity object")
            if (entity.get("agent_id") != event["agent"]
                    or entity.get("present") is not True
                    or entity.get("articulation") is not True):
                raise ValueError(
                    "member_join entity evidence must confirm the joined articulation")
            required_key = "entity"
        else:
            controller = evidence.get("controller")
            if not isinstance(controller, Mapping):
                raise ValueError(
                    "capability_change evidence requires a controller object")
            if (controller.get("agent_id") != event["agent"]
                    or controller.get("configured") is not True
                    or controller.get("capability_version") != event["capability_version"]):
                raise ValueError(
                    "capability_change controller evidence does not confirm the version")
            required_key = "controller"
        return {
            "actual_agent_ids": sorted(actual),
            "capability_versions": dict(sorted(versions.items())),
            required_key: deepcopy(dict(evidence[required_key])),
        }

    def _apply_dynamic_event(self, event: Mapping[str, Any], *, time_s: float,
                             affected_node_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        kind = event["kind"]
        agent = event.get("agent")
        declaration = event.get("capability_declaration")
        version = event.get("capability_version")
        if not isinstance(agent, str) or not agent:
            raise ValueError(f"{kind} requires a nonempty agent")
        if agent not in self._available:
            raise ValueError(f"{kind} names an agent outside the declared available set")
        if kind == "member_join":
            if agent in self._active:
                raise ValueError("member_join names an already active agent")
            if agent not in self._actual:
                raise ValueError(
                    "member_join requires an articulation in the preloaded physical inventory")
            if type(version) is not int or version != 1:
                raise ValueError("member_join capability_version must be 1")
            expected_actual = set(self._actual)
            expected_versions = {
                agent_id: version for agent_id, version in self._capability_versions.items()
                if agent_id in expected_actual
            }
            expected_versions[agent] = version
            callback = self._member_join_callback
        else:
            if agent not in self._active or agent not in self._actual:
                raise ValueError("capability_change names an inactive or nonphysical agent")
            if not isinstance(declaration, Mapping) or not declaration:
                raise ValueError("capability_change requires a nonempty capability declaration")
            if type(version) is not int or version <= self._capability_versions.get(agent, 0):
                raise ValueError("capability_change capability_version must increase")
            expected_actual = set(self._actual)
            expected_versions = {
                agent_id: current_version
                for agent_id, current_version in self._capability_versions.items()
                if agent_id in expected_actual
            }
            expected_versions[agent] = version
            callback = self._capability_change_callback
        if not isinstance(declaration, Mapping) or not declaration:
            raise ValueError(f"{kind} requires a nonempty capability declaration")
        if callback is None:
            result = self._unsupported(
                event, time_s=time_s,
                reason=f"Isaac runtime has no injected physical {kind} callback")
            raise RuntimeLifecycleUnsupported(result)
        affected = list(affected_node_ids)
        if any(not isinstance(node_id, str) or not node_id for node_id in affected):
            raise ValueError("affected_node_ids must contain nonempty strings")
        staged_team = deepcopy(self._team)
        if kind == "member_join":
            staged_team.register_reserve(
                {"id": agent, "capabilities": deepcopy(dict(declaration))},
                time_s=time_s)
            team_event = staged_team.join(agent, time_s=time_s)
        else:
            team_event = staged_team.capability_change(
                agent, deepcopy(dict(declaration)), capability_version=version,
                time_s=time_s)
        participant_snapshot = self._participant_snapshot()
        boundary_snapshot = self._boundary_snapshot()
        try:
            evidence = callback(
                deepcopy(dict(event)),
                expected_actual_agent_ids=frozenset(expected_actual),
                expected_capability_versions=deepcopy(expected_versions),
            )
            physical = self._validate_physical_evidence(
                event, evidence, expected_actual=expected_actual,
                expected_versions=expected_versions)
            payload = {
                "agent_id": agent,
                "capability_declaration": deepcopy(dict(declaration)),
                "capability_version": version,
            }
            if kind == "capability_change":
                payload["affected_node_ids"] = affected
            self.participant.emit(kind, time_s=time_s, payload=payload)
        except Exception as error:
            self._restore_participant_snapshot(participant_snapshot)
            self._restore_boundary_snapshot(boundary_snapshot)
            result = self._failed(
                event, time_s=time_s,
                reason=(f"physical {kind} transaction rejected; logical state rolled back, "
                        f"physical compensation is unverified: {error}"))
            raise RuntimeLifecycleUnsupported(result) from error
        self._team = staged_team
        self._active.add(agent)
        self._actual = expected_actual
        self._capability_versions = expected_versions
        self._last_time_s = time_s
        result = {
            "event": kind,
            "status": "applied",
            "validated": True,
            "kind": kind,
            "fault_id": event.get("id"),
            "agent": agent,
            "affected_nodes": affected,
            "time_s": time_s,
            "physical_evidence": physical,
            "physical_membership_changed": False,
            "control_membership_changed": kind == "member_join",
            "articulation_activation_changed": kind == "member_join",
            "capability_changed": kind == "capability_change",
            "task_completion_claimed": False,
            "team_lifecycle_event": team_event,
        }
        self._events.append(deepcopy(result))
        return deepcopy(result)

    @staticmethod
    def _finite_pose(value: Any, *, agent_id: str) -> list[float]:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            raise ValueError(f"{agent_id}: runtime pose must contain at least xyz")
        pose = [float(component) for component in value[:3]]
        if not all(math.isfinite(component) for component in pose):
            raise ValueError(f"{agent_id}: runtime pose contains non-finite xyz")
        return pose

    def apply_event(self, event: Mapping[str, Any], *,
                    affected_node_ids: list[str] | tuple[str, ...] = (),
                    pose_reader=None) -> dict[str, Any]:
        """Apply one event or raise with an auditable fail-closed result.

        Join and capability changes reach ``ParticipantSession.emit`` only
        after an injected physical callback has produced matching evidence.
        With no callback, the legacy unknown/fail-closed result is retained.
        """
        if not isinstance(event, Mapping):
            raise ValueError("runtime lifecycle event must be an object")
        kind = event.get("kind")
        raw_time = event.get("time_s")
        if isinstance(raw_time, bool) or not isinstance(raw_time, (int, float)):
            raise ValueError("runtime lifecycle event time_s must be numeric")
        time_s = float(raw_time)
        if not math.isfinite(time_s) or time_s < self._last_time_s:
            raise ValueError("runtime lifecycle event time_s must be finite and monotonic")
        if kind in {"member_join", "capability_change"}:
            callback = (self._member_join_callback if kind == "member_join"
                        else self._capability_change_callback)
            if callback is None:
                result = self._unsupported(
                    event, time_s=time_s,
                    reason=f"Isaac runtime has no injected physical {kind} callback")
                raise RuntimeLifecycleUnsupported(result)
            try:
                return self._apply_dynamic_event(
                    event, time_s=time_s, affected_node_ids=affected_node_ids)
            except RuntimeLifecycleUnsupported:
                raise
            except Exception as error:
                result = self._failed(
                    event, time_s=time_s,
                    reason=f"physical {kind} transaction rejected: {error}")
                raise RuntimeLifecycleUnsupported(result) from error
        if kind != "member_exit":
            result = self._unsupported(
                event, time_s=time_s,
                reason=("Isaac runtime does not support lifecycle event "
                        f"{kind!r}"))
            raise RuntimeLifecycleUnsupported(result)

        agent = event.get("agent")
        replacement = event.get("replacement_agent")
        if not isinstance(agent, str) or not agent:
            raise ValueError("member_exit requires a nonempty agent")
        if not isinstance(replacement, str) or not replacement:
            result = self._unsupported(
                event, time_s=time_s,
                reason="member_exit requires an already-declared replacement_agent")
            raise RuntimeLifecycleUnsupported(result)
        if agent not in self._active:
            result = self._unsupported(
                event, time_s=time_s,
                reason=f"member_exit names an inactive or unknown agent: {agent}")
            raise RuntimeLifecycleUnsupported(result)
        if replacement == agent or replacement not in self._active:
            result = self._unsupported(
                event, time_s=time_s,
                reason="replacement_agent must be a different active member")
            raise RuntimeLifecycleUnsupported(result)
        missing_bodies = sorted({agent, replacement} - self._actual)
        if missing_bodies:
            result = self._unsupported(
                event, time_s=time_s,
                reason=f"same-session articulation is unavailable: {missing_bodies}")
            raise RuntimeLifecycleUnsupported(result)
        if pose_reader is None:
            result = self._unsupported(
                event, time_s=time_s,
                reason="member_exit requires measured poses at the event boundary")
            raise RuntimeLifecycleUnsupported(result)
        try:
            exited_pose = self._finite_pose(pose_reader(agent), agent_id=agent)
            replacement_pose = self._finite_pose(
                pose_reader(replacement), agent_id=replacement)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            result = self._unsupported(
                event, time_s=time_s,
                reason=f"event-boundary pose evidence is unavailable: {error}")
            raise RuntimeLifecycleUnsupported(result) from error

        affected = list(affected_node_ids)
        if any(not isinstance(node_id, str) or not node_id for node_id in affected):
            raise ValueError("affected_node_ids must contain nonempty strings")
        # Stage the logical ledger before touching the public session.  This
        # keeps a rejected TeamLifecycle transition from leaving a visible
        # event behind with no matching replay record.
        staged_team = deepcopy(self._team)
        team_event = staged_team.exit(agent, time_s=time_s)
        physical_evidence = None
        if self._member_exit_deactivate_callback is not None:
            try:
                evidence = self._member_exit_deactivate_callback(
                    agent,
                    expected_actual_agent_ids=frozenset(self._actual),
                    expected_capability_versions=deepcopy(self._capability_versions),
                )
                if not isinstance(evidence, Mapping):
                    raise ValueError("exit deactivation callback must return an object")
                observed = self._agent_ids(
                    evidence.get("actual_agent_ids"), label="physical actual_agent_ids")
                versions = self._versions(
                    evidence.get("capability_versions"),
                    label="physical capability_versions")
                entity = evidence.get("entity")
                if (observed != self._actual
                        or versions != self._capability_versions
                        or not isinstance(entity, Mapping)
                        or entity.get("agent_id") != agent
                        or entity.get("present") is not True
                        or entity.get("articulation") is not True
                        or entity.get("active") is not False):
                    raise ValueError("exit deactivation readback does not match physical inventory")
                physical_evidence = deepcopy(dict(evidence))
            except Exception as error:
                result = self._failed(
                    event, time_s=time_s,
                    reason=("physical member_exit deactivation rejected; logical state "
                            f"unchanged, physical compensation is unverified: {error}"))
                raise RuntimeLifecycleUnsupported(result) from error
        # Emit only after all physical availability checks pass.  The public
        # event intentionally omits the hidden replacement identity.
        participant_snapshot = self._participant_snapshot()
        try:
            self.participant.emit("member_exit", time_s=time_s, payload={
                "agent_id": agent, "affected_node_ids": affected,
            })
        except Exception as error:
            self._restore_participant_snapshot(participant_snapshot)
            result = self._failed(
                event, time_s=time_s,
                reason=("member_exit publication rejected; logical state rolled back, "
                        f"physical compensation is unverified: {error}"))
            raise RuntimeLifecycleUnsupported(result) from error
        self._team = staged_team
        self._active.remove(agent)
        self._last_time_s = time_s
        result = {
            "event": "member_exit",
            "status": "applied",
            "validated": True,
            "kind": "member_exit",
            "fault_id": event.get("id"),
            "agent": agent,
            "replacement_agent": replacement,
            "affected_nodes": affected,
            "time_s": time_s,
            "same_session_articulations": {
                "exited": True, "replacement": True,
            },
            "event_boundary_pose_xyz": {
                agent: exited_pose, replacement: replacement_pose,
            },
            "physical_evidence": physical_evidence,
            "control_deactivation_confirmed": physical_evidence is not None,
            "replacement_mode": "preloaded_existing_articulation",
            "replacement_spawned": False,
            "capability_changed": False,
            "control_membership_changed": True,
            "physical_membership_changed": False,
            "physical_articulation_removed": False,
            "task_completion_claimed": False,
            "team_lifecycle_event": team_event,
        }
        self._events.append(deepcopy(result))
        return deepcopy(result)

    def mark_unobserved(self, event: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
        """Record a scheduled event that never reached a safe runtime boundary."""
        result = self._unsupported(event, reason=reason)
        return deepcopy(result)

    def report(self) -> dict[str, Any]:
        invalid = [event for event in self._events
                   if event.get("status") in {"unknown", "failed"}]
        unknown_count = sum(event.get("status") == "unknown" for event in invalid)
        failed_count = sum(event.get("status") == "failed" for event in invalid)
        supported_events = ["member_exit_preloaded_replacement"]
        unsupported_events = []
        if self._member_join_callback is not None:
            supported_events.append("member_join_physical_callback")
        else:
            unsupported_events.append("member_join")
        if self._capability_change_callback is not None:
            supported_events.append("capability_change_physical_callback")
        else:
            unsupported_events.append("capability_change")
        return {
            "schema": "eai.benchmark.isaac_runtime_lifecycle",
            "version": 1,
            "supported_events": supported_events,
            "unsupported_events": unsupported_events,
            "available_agent_ids": sorted(self._available),
            "active_agent_ids": sorted(self._active),
            "actual_agent_ids": sorted(self._actual),
            "capability_versions": dict(sorted(self._capability_versions.items())),
            "events": deepcopy(self._events),
            "team_lifecycle": self._team.report(),
            "validated": not invalid,
            "unknown_event_count": unknown_count,
            "failed_event_count": failed_count,
            "claims": {
                "dynamic_spawn": False,
                "preloaded_reserve_activation": any(
                    event.get("kind") == "member_join"
                    and event.get("status") == "applied" for event in self._events),
                "capability_reconfiguration": any(
                    event.get("kind") == "capability_change"
                    and event.get("status") == "applied" for event in self._events),
                "physical_task_completion": False,
            },
        }


def validate_adapter_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ValueError("participant adapter manifest must be an object")
    result = deepcopy(dict(manifest))
    if result.get("kind") not in {"baseline_solver", "participant"}:
        raise ValueError("participant adapter kind must be baseline_solver or participant")
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise ValueError("participant adapter id must be a nonempty string")
    if not isinstance(result.get("version"), int) or isinstance(result["version"], bool):
        raise ValueError("participant adapter version must be an integer")
    if any(key in result for key in ("assignment", "decision", "ground_truth")):
        raise ValueError("participant adapter manifest must not contain answers or evaluator truth")
    canonical_sha256(result)
    return result


class ParticipantSession:
    """Validate and retain exactly what an adapter sees and returns."""

    def __init__(self, *, adapter: ParticipantAdapter, api: Mapping[str, Any],
                 task_spec: Mapping[str, Any]):
        self.adapter = adapter
        self.api = deepcopy(dict(api))
        self.task_spec = deepcopy(dict(task_spec))
        self.adapter_manifest = validate_adapter_manifest(adapter.manifest())
        self.events: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self._pending_events: list[dict[str, Any]] = []
        self._action_ids: set[str] = set()
        self._sequence = 0
        self._turn = 0
        self._last_event_time_s = 0.0
        self._active_fleet = {robot["id"]: deepcopy(dict(robot)) for robot in self.task_spec["initial_fleet"]}
        self._capability_versions = {identifier: 1 for identifier in self._active_fleet}

    def current_task_spec(self) -> dict[str, Any]:
        """Action-validation view after visible member changes; initial hash stays frozen."""
        result = deepcopy(self.task_spec)
        result["initial_fleet"] = deepcopy(list(self._active_fleet.values()))
        return result

    def emit(self, event_type: str, *, time_s: float, payload: Mapping[str, Any]) -> dict[str, Any]:
        if time_s < self._last_event_time_s:
            raise ValueError("participant event time must be monotonic")
        event = {
            "schema": "eai.benchmark.participant_event",
            "version": 1,
            "sequence": self._sequence,
            "time_s": time_s,
            "type": event_type,
            "payload": deepcopy(dict(payload)),
            "turn_id": self._turn,
        }
        validate_participant_event(event, api=self.api)
        if event_type in {"member_join", "member_exit", "capability_change"}:
            agent = payload["agent_id"]
            if event_type == "member_join":
                if agent in self._active_fleet:
                    raise ValueError("joined agent is already active")
                if payload["capability_version"] <= self._capability_versions.get(agent, 0):
                    raise ValueError("rejoining capability version must increase")
                self._active_fleet[agent] = {"id": agent, "capability_declaration": deepcopy(payload["capability_declaration"])}
                self._capability_versions[agent] = payload["capability_version"]
            elif agent not in self._active_fleet:
                raise ValueError("lifecycle event names an inactive agent")
            elif event_type == "member_exit":
                del self._active_fleet[agent]
            else:
                if payload["capability_version"] <= self._capability_versions[agent]:
                    raise ValueError("capability version must increase")
                self._active_fleet[agent]["capability_declaration"] = deepcopy(payload["capability_declaration"])
                self._capability_versions[agent] = payload["capability_version"]
        self._sequence += 1
        self._last_event_time_s = float(time_s)
        self.events.append(event)
        self._pending_events.append(event)
        return deepcopy(event)

    def request_actions(self) -> list[dict[str, Any]]:
        events = deepcopy(self._pending_events)
        self._pending_events.clear()
        raw_actions = self.adapter.act(events)
        if not isinstance(raw_actions, list):
            raise ValueError("participant adapter must return a list of actions")
        accepted = []
        turn_action_ids = set()
        turn_time_s = max((event["time_s"] for event in events), default=None)
        for raw_action in raw_actions:
            action = deepcopy(dict(raw_action)) if isinstance(raw_action, Mapping) else raw_action
            if isinstance(action, dict):
                action.setdefault("turn_id", self._turn)
            validated = validate_participant_action(
                action, api=self.api, task_spec=self.current_task_spec())
            if turn_time_s is None or not math.isclose(
                    validated["submitted_at_s"], turn_time_s, abs_tol=1e-9):
                raise ValueError("participant action time must equal the current turn time")
            action_id = validated["action_id"]
            if action_id in self._action_ids or action_id in turn_action_ids:
                raise ValueError("participant action ids must be unique")
            payload = validated["payload"]
            if payload.get("node_id") is not None:
                for agent in payload.get("agent_ids", [payload.get("agent_id")]):
                    if agent is not None and self._active_fleet[agent].get("capability_declaration") is not None:
                        _validate_public_agent_requirement(self.current_task_spec(), node_id=payload["node_id"], agent_id=agent)
            turn_action_ids.add(action_id)
            accepted.append(deepcopy(validated))
        self._action_ids.update(turn_action_ids)
        self.actions.extend(deepcopy(accepted))
        self._turn += 1
        return accepted

    def report(self) -> dict[str, Any]:
        return {
            "adapter": deepcopy(self.adapter_manifest),
            "public_task_sha256": canonical_sha256(self.task_spec),
            "participant_api_sha256": canonical_sha256(self.api),
            "events": deepcopy(self.events),
            "actions": deepcopy(self.actions),
            "visible_fleet": deepcopy(list(self._active_fleet.values())),
            "capability_versions": deepcopy(self._capability_versions),
        }


class PublicTaskGreedyAdapter:
    """Minimal reference participant for scene-neutral navigation tasks.

    It uses only participant-visible lifecycle, observation and phase events.
    It does not inspect scenario snapshots, scheduler truth, preferred agents,
    or any evaluator-only field. The adapter is a baseline, not a task
    requirement.
    """

    def __init__(self, task_spec: Mapping[str, Any]):
        self._fleet = {
            robot["id"]: deepcopy(dict(robot))
            for robot in task_spec.get("initial_fleet", ())
        }
        self._objectives = {
            item["id"]: deepcopy(dict(item))
            for item in task_spec.get("objectives", ())
        }
        self._targets = {
            item["id"]: deepcopy(dict(item))
            for item in task_spec.get("targets", ())
            if isinstance(item, Mapping) and item.get("id")
        }
        self._requirements = {
            item["id"]: deepcopy(dict(item))
            for item in task_spec.get("participant_requirements", ())
            if isinstance(item, Mapping) and item.get("id")
        }
        self._controlled = set(
            task_spec.get("participant", {}).get("controlled_objectives", ()))
        self._submitted: set[str] = set()
        self._failed_agents: set[str] = set()
        self._committed_agents: set[str] = set()
        self._consumed_messages: set[tuple[str, str]] = set()
        self._turn = 0

    def _order_by_load(self, candidates: list[str], *, target_ref: str | None = None) -> list[str]:
        """Prefer idle agents, then the nearest public initial pose.

        Only the adapter's own submitted allocations feed this ordering; no
        hidden scheduler or preference state is read. Target coordinates and
        initial poses come from the public task contract.
        """
        target = self._targets.get(target_ref or "", {}).get("position")

        def public_distance(agent_id: str) -> float:
            position = self._fleet.get(agent_id, {}).get("initial_pose", {}).get("position")
            if (not isinstance(target, (list, tuple)) or len(target) < 2
                    or not isinstance(position, (list, tuple)) or len(position) < 2):
                return math.inf
            return math.hypot(float(target[0]) - float(position[0]),
                              float(target[1]) - float(position[1]))

        return sorted(candidates, key=lambda agent: (
            agent in self._committed_agents, public_distance(agent), agent))

    def _eligible_agents(self, node_id: str, candidates: list[str]) -> list[str]:
        requirement = self._requirements.get(node_id, {})
        objective = self._objectives.get(node_id, {})
        required_domain = requirement.get("domain")
        if required_domain is None:
            required_domain = "aerial" if objective.get("skill") == "aerial_scan" else "ground"
        eligible = []
        for agent_id in candidates:
            robot = self._fleet.get(agent_id)
            if robot is None or agent_id in self._failed_agents:
                continue
            capabilities = robot.get("capability_declaration") or {}
            if capabilities.get("domain") != required_domain:
                continue
            if requirement.get("navigation") and not capabilities.get("navigation_interface"):
                continue
            if (requirement.get("manipulation_interface")
                    and not set(capabilities.get("attachment_types") or ()) & {"ur5", "z1"}):
                continue
            eligible.append(agent_id)
        return eligible

    def manifest(self) -> Mapping[str, Any]:
        return {
            "kind": "baseline_solver",
            "id": "scene_public_greedy_v2",
            "version": 2,
            "supported_action_types": [
                "allocate", "reassign", "navigate_goal", "wait", "consume_message"],
            "truth_scope": "participant_visible_events_only",
        }

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        actions = []
        for event in events:
            if event.get("type") != "episode_started":
                continue
            for objective in event.get("payload", {}).get("objectives", ()):
                if isinstance(objective, Mapping) and objective.get("id"):
                    self._objectives[objective["id"]] = deepcopy(dict(objective))
        for event in events:
            if event.get("type") == "observation_message":
                payload = event.get("payload", {})
                message_id = payload.get("message_id")
                consumers = (payload.get("estimate") or {}).get("consumer_ids") or ()
                for consumer_id in consumers:
                    key = (message_id, consumer_id)
                    if (not isinstance(message_id, str)
                            or consumer_id not in self._controlled
                            or key in self._consumed_messages):
                        continue
                    actions.append({
                        "schema": "eai.benchmark.participant_action", "version": 1,
                        "action_id": f"scene-greedy-consume-{message_id}-{consumer_id}",
                        "submitted_at_s": event["time_s"], "type": "consume_message",
                        "payload": {"message_id": message_id,
                                    "consumer_id": consumer_id},
                    })
                    self._consumed_messages.add(key)
                continue
            if event.get("type") in {"member_join", "capability_change"}:
                payload = event["payload"]
                self._fleet[payload["agent_id"]] = {
                    "id": payload["agent_id"], "capability_declaration": deepcopy(payload["capability_declaration"])}
                self._failed_agents.discard(payload["agent_id"])
                for node_id in payload.get("affected_node_ids", ()):
                    self._submitted.discard(node_id)
                continue
            if event.get("type") == "member_exit":
                payload = event.get("payload", {})
                failed = payload.get("agent_id")
                if failed:
                    self._failed_agents.add(failed)
                for node_id in payload.get("affected_node_ids", ()):
                    if node_id not in self._controlled:
                        continue
                    objective = self._objectives.get(node_id, {})
                    required = 2 if objective.get("skill") == "joint_transport" else 1
                    candidates = self._order_by_load(
                        self._eligible_agents(node_id, list(self._fleet)),
                        target_ref=objective.get("target_ref"))
                    if node_id in self._submitted or len(candidates) < required:
                        continue
                    assigned = candidates[:required]
                    actions.append({
                        "schema": "eai.benchmark.participant_action", "version": 1,
                        "action_id": f"scene-greedy-reassign-{node_id}",
                        "submitted_at_s": event["time_s"], "type": "reassign",
                        "payload": {"node_id": node_id, "agent_ids": assigned, "reason": "member_exit"},
                    })
                    self._submitted.add(node_id)
                continue
            if event.get("type") != "phase_ready":
                continue
            payload = event["payload"]
            node_id = payload["node_id"]
            if node_id not in self._controlled:
                continue
            objective = self._objectives.get(node_id, {})
            target = objective.get("target_ref")
            eligible = self._order_by_load(
                self._eligible_agents(
                    node_id, list(payload.get("eligible_agent_ids") or [])),
                target_ref=target)
            if not eligible or node_id in self._submitted:
                continue
            if objective.get("skill") == "joint_transport" and len(eligible) < 2:
                continue
            assigned = eligible[:2] if objective.get("skill") == "joint_transport" else eligible[:1]
            actions.append({
                "schema": "eai.benchmark.participant_action", "version": 1,
                "action_id": f"scene-greedy-assign-{node_id}", "submitted_at_s": event["time_s"],
                "type": "allocate",
                "payload": {"node_id": node_id, "agent_ids": assigned},
            })
            self._committed_agents.update(assigned)
            if objective.get("skill") == "navigate" and target:
                actions.append({
                    "schema": "eai.benchmark.participant_action", "version": 1,
                    "action_id": f"scene-greedy-navigate-{node_id}", "submitted_at_s": event["time_s"],
                    "type": "navigate_goal",
                    "payload": {"node_id": node_id, "agent_id": assigned[0], "target_ref": target},
                })
            self._submitted.add(node_id)
        self._turn += 1
        return actions


def replay_adapter_actions(adapter: ParticipantAdapter, events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Replay recorded turn boundaries without access to evaluator-only state."""
    actions = []
    by_turn: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        by_turn.setdefault(event["turn_id"], []).append(deepcopy(dict(event)))
    for turn_id in sorted(by_turn):
        for action in adapter.act(by_turn[turn_id]):
            normalized = deepcopy(dict(action))
            normalized.setdefault("turn_id", turn_id)
            actions.append(normalized)
    return actions


def interpret_dispatch_actions(actions: list[Mapping[str, Any]], *, node_id: str, agent_id: str,
                               visible_message_ids: set[str],
                               consumed_message_ids: set[str]) -> dict[str, Any]:
    """Validate one F-INS dispatch turn without simulator dependencies."""
    navigate_actions = [action for action in actions if action["type"] == "navigate_goal"]
    if len(navigate_actions) > 1:
        raise ValueError("participant submitted multiple navigation goals in one turn")
    if navigate_actions:
        navigate_index = actions.index(navigate_actions[0])
        if any(action["type"] == "consume_message" for action in actions[navigate_index + 1:]):
            raise ValueError("participant must consume a message before using it for navigation")
    consumed = set(consumed_message_ids)
    wait_actions = []
    for action in actions:
        payload = action["payload"]
        if action["type"] == "consume_message":
            message_id = payload["message_id"]
            if message_id not in visible_message_ids:
                raise ValueError("participant consumed an unavailable or expired message")
            if payload["consumer_id"] != node_id:
                raise ValueError("participant message consumer differs from the active objective")
            consumed.add(message_id)
        elif action["type"] == "wait":
            if payload["node_id"] != node_id or payload["agent_id"] != agent_id:
                raise ValueError("participant wait action differs from the active objective")
            wait_actions.append(action)
        elif action["type"] == "navigate_goal":
            if payload["node_id"] != node_id or payload["agent_id"] != agent_id:
                raise ValueError("participant navigation action differs from the active objective")
        else:
            raise ValueError("participant action is unsupported by the F-INS dispatch runtime")
    return {
        "consumed_message_ids": consumed,
        "wait_action": wait_actions[0] if wait_actions else None,
        "navigate_action": navigate_actions[0] if navigate_actions else None,
    }


def _validate_public_agent_requirement(task_spec: Mapping[str, Any], *, node_id: str,
                                       agent_id: str) -> None:
    robot = next((item for item in task_spec.get("initial_fleet", ())
                  if item.get("id") == agent_id), None)
    if robot is None:
        raise ValueError("participant action names an unknown agent")
    requirement = next((item for item in task_spec.get("participant_requirements", ())
                        if item.get("id") == node_id), {})
    capabilities = robot.get("capability_declaration") or {}
    required_domain = requirement.get("domain")
    if required_domain is not None and capabilities.get("domain") != required_domain:
        raise ValueError(
            f"agent {agent_id} does not satisfy {node_id} domain={required_domain}")
    if requirement.get("navigation") and not capabilities.get("navigation_interface"):
        raise ValueError(f"agent {agent_id} lacks the declared navigation interface")
    if (requirement.get("manipulation_interface")
            and not set(capabilities.get("attachment_types") or ()) & {"ur5", "z1"}):
        raise ValueError(f"agent {agent_id} lacks the configured manipulation interface")


def _validate_controlled_objective(task_spec: Mapping[str, Any], node_id: str) -> None:
    controlled = set(task_spec.get("participant", {}).get("controlled_objectives", ()))
    if node_id not in controlled:
        raise ValueError("objective is outside the enabled participant runtime scope")


def eligible_public_agents(task_spec: Mapping[str, Any], *, node_id: str,
                           excluded: set[str] | frozenset[str] = frozenset(),
                           active_agent_ids: set[str] | frozenset[str] | None = None) -> list[str]:
    """Return public fleet candidates satisfying one objective's declared requirements."""
    eligible = []
    for robot in task_spec.get("initial_fleet", ()):
        agent_id = robot["id"]
        if agent_id in excluded or (
                active_agent_ids is not None and agent_id not in active_agent_ids):
            continue
        try:
            _validate_public_agent_requirement(
                task_spec, node_id=node_id, agent_id=agent_id)
        except ValueError:
            continue
        eligible.append(agent_id)
    return eligible


def apply_scene_navigation_action(action: Mapping[str, Any], *, entry: dict[str, Any],
                                  task_spec: Mapping[str, Any],
                                  active_agent_ids: set[str] | frozenset[str] | None = None) -> dict[str, Any]:
    """Apply one public navigation action to a mutable runtime plan entry.

    This is the shared action-to-scheduler boundary. It changes only the
    selected phase's assigned agent and target; it never reads hidden scenario
    snapshots or alters robot physics parameters.
    """
    if action.get("type") != "navigate_goal":
        raise ValueError("scene runtime requires a navigate_goal action")
    if entry.get("skill") in {"transport_object", "joint_transport"}:
        raise ValueError(
            "transport objectives keep their declared delivery target; "
            "navigate_goal is not accepted for them")
    payload = action.get("payload") or {}
    if payload.get("node_id") != entry.get("node"):
        raise ValueError("navigation action targets a different objective")
    _validate_controlled_objective(task_spec, entry["node"])
    agent_id = payload.get("agent_id")
    known_agents = {robot["id"] for robot in task_spec.get("initial_fleet", ())}
    if active_agent_ids is not None:
        known_agents &= set(active_agent_ids)
    if agent_id not in known_agents:
        raise ValueError("navigation action names an inactive or unknown agent")
    _validate_public_agent_requirement(
        task_spec, node_id=entry["node"], agent_id=agent_id)
    target_ref = payload.get("target_ref")
    target = next((item for item in task_spec.get("targets", ()) if item.get("id") == target_ref), None)
    if target is None:
        raise ValueError("navigation action names an unknown target")
    entry["agent"] = agent_id
    entry["goal"] = tuple(target["position"])
    entry["participant_action_id"] = action.get("action_id")
    return entry


def apply_scene_assignment_action(action: Mapping[str, Any], *, entry: dict[str, Any],
                                  task_spec: Mapping[str, Any],
                                  carried_objects: "set[str] | frozenset[str]" = frozenset(),
                                  active_agent_ids: set[str] | frozenset[str] | None = None) -> dict[str, Any]:
    """Apply a public allocate/reassign action to a phase entry.

    ``carried_objects`` is runtime ownership state: an objective whose object
    is currently owned cannot change its assigned agent or team, because
    ownership may only change through runtime pickup/release events.
    """
    if action.get("type") not in {"allocate", "reassign"}:
        raise ValueError("scene runtime requires an allocate or reassign action")
    payload = action.get("payload") or {}
    if payload.get("node_id") != entry.get("node"):
        raise ValueError("assignment action targets a different objective")
    _validate_controlled_objective(task_spec, entry["node"])
    if entry.get("object") and entry["object"] in set(carried_objects):
        raise ValueError(
            f"objective {entry['node']} owns carried object '{entry['object']}'; "
            "reassignment is rejected until the runtime releases it")
    agent_ids = payload.get("agent_ids")
    known_agents = {robot["id"] for robot in task_spec.get("initial_fleet", ())}
    if active_agent_ids is not None:
        known_agents &= set(active_agent_ids)
    if not isinstance(agent_ids, list) or not agent_ids or len(agent_ids) != len(set(agent_ids)):
        raise ValueError("assignment action requires unique agent_ids")
    if any(agent not in known_agents for agent in agent_ids):
        raise ValueError("assignment action names an inactive or unknown agent")
    for agent_id in agent_ids:
        _validate_public_agent_requirement(
            task_spec, node_id=entry["node"], agent_id=agent_id)
    if entry.get("skill") == "joint_transport" and len(agent_ids) != 2:
        raise ValueError("joint_transport requires exactly two assigned agents")
    if entry.get("skill") != "joint_transport" and len(agent_ids) != 1:
        raise ValueError("single-agent objective requires exactly one assigned agent")
    entry["team"] = list(agent_ids)
    entry["agent"] = agent_ids[0]
    entry["participant_action_id"] = action.get("action_id")
    return entry


def replacement_assignment_matches(entry: Mapping[str, Any], *,
                                    agent_ids: list[str] | tuple[str, ...] | set[str],
                                    replacement_agent: str) -> bool:
    """Check that a recovery assignment uses the declared runtime replacement."""
    assigned = set(agent_ids)
    if entry.get("skill") == "joint_transport":
        return replacement_agent in assigned
    return assigned == {replacement_agent}


__all__ = [
    "IsaacRuntimeLifecycleBoundary", "ParticipantAdapter", "ParticipantSession",
    "PublicTaskGreedyAdapter", "RuntimeLifecycleUnsupported", "apply_scene_assignment_action",
    "apply_scene_navigation_action", "eligible_public_agents",
    "interpret_dispatch_actions", "replacement_assignment_matches",
    "replay_adapter_actions", "RuntimeLifecyclePhysicalCallback",
    "validate_adapter_manifest",
]
