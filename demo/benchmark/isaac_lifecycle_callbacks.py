"""Fail-closed Isaac lifecycle callbacks for preloaded benchmark agents.

The runtime boundary deliberately does not know how a particular Isaac scene
enables an articulation or reconfigures a controller.  This module supplies
the transaction-shaped callbacks expected by
``IsaacRuntimeLifecycleBoundary`` while keeping those scene-specific
operations explicit through bindings.

No code here spawns an entity.  Every agent must have a preloaded
``ArticulationBinding`` and a ``ControllerBinding``.  Mutations are followed by
readback and exact inventory/version checks.  A failed readback raises with an
``unknown`` physical-state audit; it never claims that an external Isaac
mutation was rolled back.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Mapping


EntityStateReader = Callable[[Any], Mapping[str, Any]]
EntityActivationWriter = Callable[[Any, bool], None]
ControllerStateReader = Callable[[Any], Mapping[str, Any]]
ControllerReconfigure = Callable[[Any, Mapping[str, Any], int], None]


class IsaacLifecycleCallbackError(RuntimeError):
    """A lifecycle callback could not prove its requested physical state.

    ``audit`` describes whether a mutation was attempted.  It intentionally
    never reports physical compensation: this module does not know whether a
    failed Isaac operation can be reversed safely.
    """

    def __init__(self, operation: str, agent_id: str, reason: str, *,
                 mutation_attempted: bool):
        self.audit = {
            "operation": operation,
            "agent_id": agent_id,
            "status": "failed",
            "validated": False,
            "reason": reason,
            "mutation_attempted": mutation_attempted,
            "logical_state_rolled_back": False,
            "physical_compensation_confirmed": False,
            "physical_state_after_failure": (
                "unknown_requires_external_compensation_audit"
                if mutation_attempted else "no_mutation_attempted"
            ),
        }
        super().__init__(
            f"{operation} rejected for {agent_id}: {reason}; "
            f"physical compensation is not confirmed"
            if mutation_attempted else f"{operation} rejected for {agent_id}: {reason}"
        )


@dataclass(frozen=True)
class ArticulationBinding:
    """Operations and identity for one already-loaded Isaac articulation.

    ``read_state`` must return ``present``, ``articulation``, ``active`` and
    ``prim_path``.  ``set_active`` changes only the supplied handle; it must
    not create or replace an articulation.
    """

    handle: Any
    prim_path: str
    set_active: EntityActivationWriter
    read_state: EntityStateReader

    def __post_init__(self) -> None:
        if self.handle is None:
            raise ValueError("articulation binding requires a preloaded handle")
        if not isinstance(self.prim_path, str) or not self.prim_path.startswith("/"):
            raise ValueError("articulation binding requires an absolute prim_path")
        if not callable(self.set_active) or not callable(self.read_state):
            raise ValueError("articulation binding operations must be callable")


@dataclass(frozen=True)
class ControllerBinding:
    """Versioned controller operations for one preloaded agent.

    ``read_state`` must return ``configured``, ``capability_version``,
    ``capability_declaration`` and ``controller_id``.  Reconfiguration must be
    made visible through that same readback before evidence can be emitted.
    """

    handle: Any
    controller_id: str
    reconfigure: ControllerReconfigure
    read_state: ControllerStateReader

    def __post_init__(self) -> None:
        if self.handle is None:
            raise ValueError("controller binding requires a loaded handle")
        if not isinstance(self.controller_id, str) or not self.controller_id:
            raise ValueError("controller binding requires a nonempty controller_id")
        if not callable(self.reconfigure) or not callable(self.read_state):
            raise ValueError("controller binding operations must be callable")


def _canonical_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("capability declaration must be canonical JSON") from error
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _agent_ids(value: Any, *, label: str) -> set[str]:
    if not isinstance(value, (set, frozenset, list, tuple)):
        raise ValueError(f"{label} must be a sequence of agent ids")
    result = set(value)
    if len(result) != len(value) or any(
            not isinstance(agent_id, str) or not agent_id for agent_id in result):
        raise ValueError(f"{label} must contain unique nonempty strings")
    return result


def _versions(value: Any, *, label: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    result = dict(value)
    if any(not isinstance(agent_id, str) or not agent_id for agent_id in result):
        raise ValueError(f"{label} keys must be nonempty strings")
    if any(type(version) is not int or version < 1 for version in result.values()):
        raise ValueError(f"{label} values must be positive integers")
    return result


class IsaacLifecycleCallbacks:
    """Create strict join and capability callbacks over preloaded resources.

    The mapping keys are the only physical agents this object can report.
    Attached arm articulations or unrelated scene bodies must not be included
    unless they are independently declared benchmark agents.
    """

    def __init__(self, *,
                 articulations: Mapping[str, ArticulationBinding],
                 controllers: Mapping[str, ControllerBinding]):
        self._articulations = dict(articulations)
        self._controllers = dict(controllers)
        if not self._articulations:
            raise ValueError("lifecycle callbacks require preloaded articulations")
        if any(not isinstance(agent_id, str) or not agent_id
               for agent_id in self._articulations):
            raise ValueError("articulation ids must be nonempty strings")
        if set(self._controllers) != set(self._articulations):
            raise ValueError(
                "controller bindings must exactly match preloaded articulation ids")
        if any(not isinstance(binding, ArticulationBinding)
               for binding in self._articulations.values()):
            raise ValueError("articulations must contain ArticulationBinding values")
        if any(not isinstance(binding, ControllerBinding)
               for binding in self._controllers.values()):
            raise ValueError("controllers must contain ControllerBinding values")

    @property
    def preloaded_agent_ids(self) -> frozenset[str]:
        return frozenset(self._articulations)

    def _entity(self, agent_id: str) -> dict[str, Any]:
        binding = self._articulations[agent_id]
        state = binding.read_state(binding.handle)
        if not isinstance(state, Mapping):
            raise ValueError(f"{agent_id}: articulation readback must be an object")
        for name in ("present", "articulation", "active"):
            if type(state.get(name)) is not bool:
                raise ValueError(f"{agent_id}: articulation readback requires boolean {name}")
        if state.get("prim_path") != binding.prim_path:
            raise ValueError(f"{agent_id}: articulation prim_path readback mismatch")
        return {
            "agent_id": agent_id,
            "present": state["present"],
            "articulation": state["articulation"],
            "active": state["active"],
            "preloaded": True,
            "prim_path": binding.prim_path,
            "handle_type": type(binding.handle).__name__,
        }

    def _controller(self, agent_id: str) -> dict[str, Any]:
        binding = self._controllers[agent_id]
        state = binding.read_state(binding.handle)
        if not isinstance(state, Mapping):
            raise ValueError(f"{agent_id}: controller readback must be an object")
        if type(state.get("configured")) is not bool:
            raise ValueError(f"{agent_id}: controller readback requires boolean configured")
        version = state.get("capability_version")
        if type(version) is not int or version < 1:
            raise ValueError(
                f"{agent_id}: controller capability_version must be a positive integer")
        declaration = state.get("capability_declaration")
        if not isinstance(declaration, Mapping) or not declaration:
            raise ValueError(
                f"{agent_id}: controller readback requires a capability declaration")
        if state.get("controller_id") != binding.controller_id:
            raise ValueError(f"{agent_id}: controller_id readback mismatch")
        declaration = deepcopy(dict(declaration))
        return {
            "agent_id": agent_id,
            "configured": state["configured"],
            "capability_version": version,
            "capability_declaration": declaration,
            "capability_declaration_sha256": _canonical_sha256(declaration),
            "controller_id": binding.controller_id,
            "handle_type": type(binding.handle).__name__,
        }

    def _snapshot(self) -> tuple[dict[str, dict[str, Any]],
                                 dict[str, dict[str, Any]]]:
        entities = {
            agent_id: self._entity(agent_id)
            for agent_id in sorted(self._articulations)
        }
        controllers = {
            agent_id: self._controller(agent_id)
            for agent_id in sorted(self._controllers)
        }
        return entities, controllers

    @staticmethod
    def _verified_snapshot(
            entities: Mapping[str, Mapping[str, Any]],
            controllers: Mapping[str, Mapping[str, Any]], *,
            expected_actual_agent_ids: Any,
            expected_capability_versions: Any) -> tuple[set[str], dict[str, int]]:
        expected_actual = _agent_ids(
            expected_actual_agent_ids, label="expected_actual_agent_ids")
        expected_versions = _versions(
            expected_capability_versions, label="expected_capability_versions")
        if set(expected_versions) != expected_actual:
            raise ValueError(
                "expected capability version keys must equal expected actual agent ids")
        if set(entities) != expected_actual or set(controllers) != expected_actual:
            raise ValueError(
                "callback bindings must exactly match expected actual agent ids")
        actual = {
            agent_id for agent_id, state in entities.items()
            if state["present"] is True and state["articulation"] is True
        }
        if actual != expected_actual:
            raise ValueError(
                f"preloaded articulation inventory mismatch: expected "
                f"{sorted(expected_actual)}, got {sorted(actual)}")
        observed_versions = {
            agent_id: controllers[agent_id]["capability_version"]
            for agent_id in sorted(actual)
        }
        if observed_versions != expected_versions:
            raise ValueError(
                f"controller capability versions mismatch: expected "
                f"{expected_versions}, got {observed_versions}")
        if any(controllers[agent_id]["configured"] is not True for agent_id in actual):
            raise ValueError("every physical agent must have a configured controller")
        return actual, observed_versions

    @staticmethod
    def _evidence(actual: set[str], versions: Mapping[str, int], *,
                  key: str, value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "actual_agent_ids": sorted(actual),
            "capability_versions": dict(sorted(versions.items())),
            key: deepcopy(dict(value)),
        }

    def prepare_preloaded_articulations(
            self, *, active_agent_ids: Any,
            expected_actual_agent_ids: Any,
            expected_capability_versions: Any) -> dict[str, Any]:
        """Set initial active/reserve state and verify the complete inventory.

        This is an explicit startup operation.  A partial failure is reported
        as physically unknown; already-applied activation changes are not
        automatically reversed.
        """
        operation = "prepare_preloaded_articulations"
        agent_label = "<inventory>"
        mutation_attempted = False
        try:
            active = _agent_ids(active_agent_ids, label="active_agent_ids")
            expected = _agent_ids(
                expected_actual_agent_ids, label="expected_actual_agent_ids")
            if active - expected:
                raise ValueError("active agents must be part of the expected inventory")
            if expected != set(self._articulations):
                raise ValueError(
                    "expected agent inventory must exactly match callback bindings")
            entities, controllers = self._snapshot()
            self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected,
                expected_capability_versions=expected_capability_versions)
            for agent_id in sorted(expected):
                desired = agent_id in active
                if entities[agent_id]["active"] is desired:
                    continue
                mutation_attempted = True
                binding = self._articulations[agent_id]
                binding.set_active(binding.handle, desired)
            entities, controllers = self._snapshot()
            actual, versions = self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected,
                expected_capability_versions=expected_capability_versions)
            observed_active = {
                agent_id for agent_id, state in entities.items() if state["active"]
            }
            if observed_active != active:
                raise ValueError(
                    f"articulation active set mismatch: expected {sorted(active)}, "
                    f"got {sorted(observed_active)}")
            return {
                "actual_agent_ids": sorted(actual),
                "capability_versions": dict(sorted(versions.items())),
                "active_agent_ids": sorted(observed_active),
                "entities": deepcopy(entities),
                "controllers": deepcopy(controllers),
            }
        except IsaacLifecycleCallbackError:
            raise
        except Exception as error:
            raise IsaacLifecycleCallbackError(
                operation, agent_label, str(error),
                mutation_attempted=mutation_attempted) from error

    def member_join(self, event: Mapping[str, Any], *,
                    expected_actual_agent_ids: frozenset[str],
                    expected_capability_versions: Mapping[str, int]) -> dict[str, Any]:
        """Activate an already-loaded reserve and return boundary evidence."""
        operation = "member_join"
        agent_id = event.get("agent") if isinstance(event, Mapping) else None
        mutation_attempted = False
        try:
            if not isinstance(event, Mapping) or event.get("kind") != operation:
                raise ValueError("member_join callback requires a member_join event")
            if not isinstance(agent_id, str) or not agent_id:
                raise ValueError("member_join requires a nonempty agent")
            if agent_id not in self._articulations:
                raise ValueError("agent has no preloaded articulation binding")
            declaration = event.get("capability_declaration")
            if not isinstance(declaration, Mapping) or not declaration:
                raise ValueError("member_join requires a capability declaration")
            version = event.get("capability_version")
            if type(version) is not int or version < 1:
                raise ValueError("member_join requires a positive capability_version")
            entities, controllers = self._snapshot()
            actual, versions = self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected_actual_agent_ids,
                expected_capability_versions=expected_capability_versions)
            target = entities[agent_id]
            if target["active"] is not False:
                raise ValueError("joined articulation must be an inactive preloaded reserve")
            if controllers[agent_id]["capability_declaration"] != dict(declaration):
                raise ValueError(
                    "preloaded reserve controller declaration differs from the join event")
            if versions[agent_id] != version:
                raise ValueError(
                    "preloaded reserve controller version differs from the join event")
            mutation_attempted = True
            binding = self._articulations[agent_id]
            binding.set_active(binding.handle, True)
            entities, controllers = self._snapshot()
            actual, versions = self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected_actual_agent_ids,
                expected_capability_versions=expected_capability_versions)
            target = entities[agent_id]
            if target["active"] is not True:
                raise ValueError("joined articulation did not read back as active")
            return self._evidence(actual, versions, key="entity", value=target)
        except IsaacLifecycleCallbackError:
            raise
        except Exception as error:
            raise IsaacLifecycleCallbackError(
                operation, agent_id if isinstance(agent_id, str) else "<invalid>",
                str(error), mutation_attempted=mutation_attempted) from error

    def capability_change(self, event: Mapping[str, Any], *,
                          expected_actual_agent_ids: frozenset[str],
                          expected_capability_versions: Mapping[str, int]) -> dict[str, Any]:
        """Reconfigure one active controller and prove the version by readback."""
        operation = "capability_change"
        agent_id = event.get("agent") if isinstance(event, Mapping) else None
        mutation_attempted = False
        try:
            if not isinstance(event, Mapping) or event.get("kind") != operation:
                raise ValueError(
                    "capability_change callback requires a capability_change event")
            if not isinstance(agent_id, str) or not agent_id:
                raise ValueError("capability_change requires a nonempty agent")
            if agent_id not in self._articulations:
                raise ValueError("agent has no preloaded articulation binding")
            declaration = event.get("capability_declaration")
            if not isinstance(declaration, Mapping) or not declaration:
                raise ValueError("capability_change requires a capability declaration")
            expected = _agent_ids(
                expected_actual_agent_ids, label="expected_actual_agent_ids")
            expected_versions = _versions(
                expected_capability_versions, label="expected_capability_versions")
            if set(expected_versions) != expected:
                raise ValueError(
                    "expected capability version keys must equal expected actual agent ids")
            version = event.get("capability_version")
            if type(version) is not int or expected_versions.get(agent_id) != version:
                raise ValueError(
                    "event capability_version must match the expected post-change version")

            entities, controllers = self._snapshot()
            actual = {
                name for name, state in entities.items()
                if state["present"] and state["articulation"]
            }
            if actual != expected:
                raise ValueError(
                    f"preloaded articulation inventory mismatch: expected "
                    f"{sorted(expected)}, got {sorted(actual)}")
            if set(entities) != expected or set(controllers) != expected:
                raise ValueError(
                    "callback bindings must exactly match expected actual agent ids")
            if any(controllers[name]["configured"] is not True for name in expected):
                raise ValueError("every physical agent must have a configured controller")
            if entities[agent_id]["active"] is not True:
                raise ValueError("capability_change requires an active articulation")
            for name in sorted(expected - {agent_id}):
                if controllers[name]["capability_version"] != expected_versions[name]:
                    raise ValueError(
                        f"unaffected controller version mismatch for {name}")
            current_version = controllers[agent_id]["capability_version"]
            if current_version >= version:
                raise ValueError(
                    "capability_change version must increase from physical readback")

            mutation_attempted = True
            binding = self._controllers[agent_id]
            binding.reconfigure(binding.handle, deepcopy(dict(declaration)), version)
            entities, controllers = self._snapshot()
            actual, versions = self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected,
                expected_capability_versions=expected_versions)
            target = controllers[agent_id]
            if target["configured"] is not True:
                raise ValueError("controller did not read back as configured")
            if target["capability_declaration"] != dict(declaration):
                raise ValueError(
                    "controller capability declaration readback mismatch")
            return self._evidence(actual, versions, key="controller", value=target)
        except IsaacLifecycleCallbackError:
            raise
        except Exception as error:
            raise IsaacLifecycleCallbackError(
                operation, agent_id if isinstance(agent_id, str) else "<invalid>",
                str(error), mutation_attempted=mutation_attempted) from error

    def deactivate_preloaded_articulation(
            self, agent_id: str, *, expected_actual_agent_ids: Any,
            expected_capability_versions: Any) -> dict[str, Any]:
        """Deactivate one preloaded entity without removing it from inventory.

        The runtime boundary invokes this before publishing member_exit when
        the callback is provided. Calling it alone does not commit a public
        lifecycle event.
        """
        operation = "deactivate_preloaded_articulation"
        mutation_attempted = False
        try:
            if not isinstance(agent_id, str) or not agent_id:
                raise ValueError("deactivation requires a nonempty agent id")
            if agent_id not in self._articulations:
                raise ValueError("agent has no preloaded articulation binding")
            entities, controllers = self._snapshot()
            self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected_actual_agent_ids,
                expected_capability_versions=expected_capability_versions)
            if entities[agent_id]["active"] is not True:
                raise ValueError("articulation is already inactive")
            mutation_attempted = True
            binding = self._articulations[agent_id]
            binding.set_active(binding.handle, False)
            entities, controllers = self._snapshot()
            actual, versions = self._verified_snapshot(
                entities, controllers,
                expected_actual_agent_ids=expected_actual_agent_ids,
                expected_capability_versions=expected_capability_versions)
            target = entities[agent_id]
            if target["active"] is not False:
                raise ValueError("articulation did not read back as inactive")
            return self._evidence(actual, versions, key="entity", value=target)
        except IsaacLifecycleCallbackError:
            raise
        except Exception as error:
            raise IsaacLifecycleCallbackError(
                operation, agent_id if isinstance(agent_id, str) else "<invalid>",
                str(error), mutation_attempted=mutation_attempted) from error


__all__ = [
    "ArticulationBinding",
    "ControllerBinding",
    "IsaacLifecycleCallbackError",
    "IsaacLifecycleCallbacks",
]
