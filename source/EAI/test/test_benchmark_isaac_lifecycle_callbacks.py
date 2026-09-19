"""Pure-Python tests for fail-closed Isaac lifecycle callbacks."""

from copy import deepcopy

import pytest

from demo.benchmark.isaac_lifecycle_callbacks import (
    ArticulationBinding,
    ControllerBinding,
    IsaacLifecycleCallbackError,
    IsaacLifecycleCallbacks,
)
from demo.benchmark.isaac_env_lifecycle import build_env_lifecycle_callbacks
from demo.benchmark.participant_runtime import (
    IsaacRuntimeLifecycleBoundary, RuntimeLifecycleUnsupported,
)


GROUND = {
    "domain": "ground",
    "navigation_interface": True,
    "attachment_types": [],
}
MANIPULATOR = {
    "domain": "ground",
    "navigation_interface": True,
    "attachment_types": ["ur5"],
}


class _Articulation:
    def __init__(self, prim_path, *, active=True, present=True,
                 ignore_activation_readback=False):
        self.prim_path = prim_path
        self.active = active
        self.present = present
        self.ignore_activation_readback = ignore_activation_readback
        self.activation_calls = []


class _Controller:
    def __init__(self, controller_id, declaration, *, version=1,
                 ignore_reconfigure=False):
        self.controller_id = controller_id
        self.declaration = deepcopy(declaration)
        self.version = version
        self.configured = True
        self.ignore_reconfigure = ignore_reconfigure
        self.reconfigure_calls = []


def _set_active(handle, active):
    handle.activation_calls.append(active)
    handle.active = bool(active)


def _read_articulation(handle):
    active = handle.active
    if handle.ignore_activation_readback and handle.activation_calls:
        active = not handle.activation_calls[-1]
    return {
        "present": handle.present,
        "articulation": handle.present,
        "active": active,
        "prim_path": handle.prim_path,
    }


def _reconfigure(handle, declaration, version):
    handle.reconfigure_calls.append((deepcopy(dict(declaration)), version))
    handle.declaration = deepcopy(dict(declaration))
    if not handle.ignore_reconfigure:
        handle.version = version


def _read_controller(handle):
    return {
        "configured": handle.configured,
        "capability_version": handle.version,
        "capability_declaration": deepcopy(handle.declaration),
        "controller_id": handle.controller_id,
    }


def _callbacks(*, reserve_active=False, reserve_present=True,
               reserve_bad_activation_readback=False,
               active_bad_reconfigure=False):
    articulations = {
        "active_1": _Articulation("/World/active_1", active=True),
        "reserve_1": _Articulation(
            "/World/reserve_1", active=reserve_active,
            present=reserve_present,
            ignore_activation_readback=reserve_bad_activation_readback),
    }
    controllers = {
        "active_1": _Controller(
            "controller.active_1", GROUND,
            ignore_reconfigure=active_bad_reconfigure),
        "reserve_1": _Controller("controller.reserve_1", GROUND),
    }
    callback = IsaacLifecycleCallbacks(
        articulations={agent_id: ArticulationBinding(
            handle=handle, prim_path=handle.prim_path,
            set_active=_set_active, read_state=_read_articulation)
            for agent_id, handle in articulations.items()},
        controllers={agent_id: ControllerBinding(
            handle=handle, controller_id=handle.controller_id,
            reconfigure=_reconfigure, read_state=_read_controller)
            for agent_id, handle in controllers.items()},
    )
    return callback, articulations, controllers


def _join_event(**overrides):
    event = {
        "id": "join-1",
        "kind": "member_join",
        "time_s": 5.0,
        "agent": "reserve_1",
        "capability_version": 1,
        "capability_declaration": deepcopy(GROUND),
    }
    event.update(overrides)
    return event


def _capability_event(**overrides):
    event = {
        "id": "capability-1",
        "kind": "capability_change",
        "time_s": 6.0,
        "agent": "active_1",
        "capability_version": 2,
        "capability_declaration": deepcopy(MANIPULATOR),
    }
    event.update(overrides)
    return event


def _expected_versions(**overrides):
    versions = {"active_1": 1, "reserve_1": 1}
    versions.update(overrides)
    return versions


class _Participant:
    """Minimum state surface consumed by IsaacRuntimeLifecycleBoundary."""

    def __init__(self):
        self.task_spec = {
            "initial_fleet": [{
                "id": "active_1",
                "capability_declaration": deepcopy(GROUND),
            }],
            "participant_requirements": [],
        }
        self.events = []
        self.actions = []
        self._pending_events = []
        self._action_ids = set()
        self._sequence = 0
        self._turn = 0
        self._last_event_time_s = 0.0
        self._active_fleet = {
            "active_1": deepcopy(self.task_spec["initial_fleet"][0]),
        }
        self._capability_versions = {"active_1": 1}

    def emit(self, event_type, *, time_s, payload):
        event = {"type": event_type, "time_s": time_s, "payload": deepcopy(dict(payload))}
        self.events.append(event)
        self._pending_events.append(deepcopy(event))
        self._sequence += 1
        self._last_event_time_s = time_s
        if event_type == "member_join":
            agent_id = payload["agent_id"]
            self._active_fleet[agent_id] = {
                "id": agent_id,
                "capability_declaration": deepcopy(payload["capability_declaration"]),
            }
            self._capability_versions[agent_id] = payload["capability_version"]
        elif event_type == "capability_change":
            agent_id = payload["agent_id"]
            self._active_fleet[agent_id]["capability_declaration"] = deepcopy(
                payload["capability_declaration"])
            self._capability_versions[agent_id] = payload["capability_version"]
        return deepcopy(event)


def test_prepare_sets_initial_agents_active_and_reserves_inactive():
    callbacks, articulations, _ = _callbacks(reserve_active=True)

    evidence = callbacks.prepare_preloaded_articulations(
        active_agent_ids={"active_1"},
        expected_actual_agent_ids={"active_1", "reserve_1"},
        expected_capability_versions=_expected_versions())

    assert evidence["actual_agent_ids"] == ["active_1", "reserve_1"]
    assert evidence["active_agent_ids"] == ["active_1"]
    assert articulations["reserve_1"].activation_calls == [False]
    assert evidence["entities"]["reserve_1"]["preloaded"] is True
    assert evidence["entities"]["reserve_1"]["active"] is False


def test_member_join_callback_activates_only_preloaded_reserve_and_matches_boundary():
    callbacks, articulations, _ = _callbacks()
    participant = _Participant()
    boundary = IsaacRuntimeLifecycleBoundary(
        participant,
        available_agent_ids={"active_1", "reserve_1"},
        actual_agent_ids={"active_1", "reserve_1"},
        member_join_callback=callbacks.member_join,
        capability_change_callback=callbacks.capability_change,
    )

    result = boundary.apply_event(_join_event())

    assert result["status"] == "applied"
    assert result["physical_evidence"]["actual_agent_ids"] == [
        "active_1", "reserve_1"]
    assert result["physical_evidence"]["capability_versions"] == _expected_versions()
    assert result["physical_evidence"]["entity"] == {
        "agent_id": "reserve_1",
        "present": True,
        "articulation": True,
        "active": True,
        "preloaded": True,
        "prim_path": "/World/reserve_1",
        "handle_type": "_Articulation",
    }
    assert articulations["reserve_1"].activation_calls == [True]
    assert boundary.active_agent_ids == frozenset({"active_1", "reserve_1"})


def test_member_join_rejects_unloaded_or_missing_articulation_without_mutation():
    callbacks, articulations, _ = _callbacks(reserve_present=False)

    with pytest.raises(IsaacLifecycleCallbackError) as caught:
        callbacks.member_join(
            _join_event(),
            expected_actual_agent_ids=frozenset({"active_1", "reserve_1"}),
            expected_capability_versions=_expected_versions())

    assert "inventory mismatch" in str(caught.value)
    assert caught.value.audit["mutation_attempted"] is False
    assert caught.value.audit["physical_state_after_failure"] == "no_mutation_attempted"
    assert articulations["reserve_1"].activation_calls == []


def test_member_join_failed_readback_does_not_claim_or_attempt_rollback():
    callbacks, articulations, _ = _callbacks(
        reserve_bad_activation_readback=True)

    with pytest.raises(IsaacLifecycleCallbackError) as caught:
        callbacks.member_join(
            _join_event(),
            expected_actual_agent_ids=frozenset({"active_1", "reserve_1"}),
            expected_capability_versions=_expected_versions())

    assert articulations["reserve_1"].active is True
    assert articulations["reserve_1"].activation_calls == [True]
    assert caught.value.audit["mutation_attempted"] is True
    assert caught.value.audit["logical_state_rolled_back"] is False
    assert caught.value.audit["physical_compensation_confirmed"] is False
    assert caught.value.audit["physical_state_after_failure"] == \
        "unknown_requires_external_compensation_audit"


def test_capability_change_reconfigures_and_reads_back_version_and_declaration():
    callbacks, _, controllers = _callbacks()

    evidence = callbacks.capability_change(
        _capability_event(),
        expected_actual_agent_ids=frozenset({"active_1", "reserve_1"}),
        expected_capability_versions=_expected_versions(active_1=2))

    assert evidence["actual_agent_ids"] == ["active_1", "reserve_1"]
    assert evidence["capability_versions"] == _expected_versions(active_1=2)
    assert evidence["controller"]["agent_id"] == "active_1"
    assert evidence["controller"]["configured"] is True
    assert evidence["controller"]["capability_version"] == 2
    assert evidence["controller"]["capability_declaration"] == MANIPULATOR
    assert len(evidence["controller"]["capability_declaration_sha256"]) == 64
    assert controllers["active_1"].reconfigure_calls == [(MANIPULATOR, 2)]


def test_capability_change_callback_matches_boundary_contract():
    callbacks, _, _ = _callbacks()
    participant = _Participant()
    boundary = IsaacRuntimeLifecycleBoundary(
        participant,
        available_agent_ids={"active_1", "reserve_1"},
        actual_agent_ids={"active_1", "reserve_1"},
        member_join_callback=callbacks.member_join,
        capability_change_callback=callbacks.capability_change,
    )

    result = boundary.apply_event(
        _capability_event(), affected_node_ids=["manipulation_stage"])

    assert result["status"] == "applied"
    assert result["physical_evidence"]["capability_versions"] == \
        _expected_versions(active_1=2)
    assert result["physical_evidence"]["controller"]["configured"] is True
    assert result["physical_evidence"]["controller"]["capability_version"] == 2
    assert participant.events[-1]["payload"]["affected_node_ids"] == [
        "manipulation_stage"]


def test_capability_change_wrong_version_readback_fails_without_rollback_claim():
    callbacks, _, controllers = _callbacks(active_bad_reconfigure=True)

    with pytest.raises(IsaacLifecycleCallbackError) as caught:
        callbacks.capability_change(
            _capability_event(),
            expected_actual_agent_ids=frozenset({"active_1", "reserve_1"}),
            expected_capability_versions=_expected_versions(active_1=2))

    assert controllers["active_1"].declaration == MANIPULATOR
    assert controllers["active_1"].version == 1
    assert controllers["active_1"].reconfigure_calls == [(MANIPULATOR, 2)]
    assert caught.value.audit["mutation_attempted"] is True
    assert caught.value.audit["physical_compensation_confirmed"] is False


def test_inventory_mismatch_rejects_attached_or_unbound_agent_before_mutation():
    callbacks, articulations, controllers = _callbacks()

    with pytest.raises(IsaacLifecycleCallbackError) as caught:
        callbacks.member_join(
            _join_event(),
            expected_actual_agent_ids=frozenset({
                "active_1", "reserve_1", "active_1_arm"}),
            expected_capability_versions={
                **_expected_versions(), "active_1_arm": 1})

    assert "bindings must exactly match" in str(caught.value)
    assert articulations["reserve_1"].activation_calls == []
    assert controllers["reserve_1"].reconfigure_calls == []


def test_explicit_deactivation_keeps_articulation_in_actual_inventory():
    callbacks, articulations, _ = _callbacks(reserve_active=True)

    evidence = callbacks.deactivate_preloaded_articulation(
        "reserve_1",
        expected_actual_agent_ids={"active_1", "reserve_1"},
        expected_capability_versions=_expected_versions())

    assert evidence["actual_agent_ids"] == ["active_1", "reserve_1"]
    assert evidence["entity"]["present"] is True
    assert evidence["entity"]["articulation"] is True
    assert evidence["entity"]["active"] is False
    assert articulations["reserve_1"].activation_calls == [False]


def test_bindings_must_cover_exactly_the_same_preloaded_agent_set():
    articulation = _Articulation("/World/active_1")

    with pytest.raises(ValueError, match="exactly match"):
        IsaacLifecycleCallbacks(
            articulations={"active_1": ArticulationBinding(
                handle=articulation, prim_path=articulation.prim_path,
                set_active=_set_active, read_state=_read_articulation)},
            controllers={})


class _BenchmarkEnv:
    def __init__(self):
        self.active = {"active_1": True, "reserve_1": True}
        self.declarations = {}
        self.versions = {}
        self.configure_calls = []
        self.activation_calls = []
        self.reconfigure_calls = []

    def configure_benchmark_lifecycle(
            self, *, active_agent_ids, capability_declarations,
            capability_versions):
        self.configure_calls.append({
            "active_agent_ids": set(active_agent_ids),
            "capability_declarations": deepcopy(capability_declarations),
            "capability_versions": dict(capability_versions),
        })
        self.active = {
            agent_id: agent_id in set(active_agent_ids)
            for agent_id in capability_declarations
        }
        self.declarations = deepcopy(capability_declarations)
        self.versions = dict(capability_versions)

    def set_benchmark_agent_active(self, agent_id, active):
        self.activation_calls.append((agent_id, active))
        self.active[agent_id] = active

    def reconfigure_benchmark_controller(self, agent_id, declaration, version):
        self.reconfigure_calls.append((agent_id, deepcopy(declaration), version))
        self.declarations[agent_id] = deepcopy(declaration)
        self.versions[agent_id] = version

    def read_benchmark_agent_state(self, agent_id):
        return {
            "present": agent_id in self.active,
            "articulation": agent_id in self.active,
            "active": self.active.get(agent_id, False),
            "prim_path": f"/World/envs/env_0/{agent_id}",
        }

    def read_benchmark_controller_state(self, agent_id):
        return {
            "configured": agent_id in self.declarations,
            "capability_version": self.versions.get(agent_id),
            "capability_declaration": deepcopy(self.declarations.get(agent_id)),
            "controller_id": f"{agent_id}:FakeController",
        }


def _lifecycle_scenario():
    def robot(agent_id):
        return {
            "id": agent_id,
            "type": "carter",
            "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
            "attachments": [],
            "spawn_pose": {
                "position": [0.0, 0.0, 0.2],
                "rotation": [1.0, 0.0, 0.0, 0.0],
            },
        }

    return {
        "robots": [robot("active_1"), robot("reserve_1")],
        "runtime_lifecycle": {
            "initial_agent_ids": ["active_1"],
            "preloaded_reserve_agent_ids": ["reserve_1"],
        },
    }


def test_live_env_adapter_parks_reserve_then_exposes_verified_join_and_reconfigure():
    env = _BenchmarkEnv()
    callbacks = build_env_lifecycle_callbacks(env, _lifecycle_scenario())

    assert env.configure_calls[0]["active_agent_ids"] == {"active_1"}
    assert env.active == {"active_1": True, "reserve_1": False}
    join_declaration = deepcopy(env.declarations["reserve_1"])
    join = callbacks.member_join(
        _join_event(capability_declaration=join_declaration),
        expected_actual_agent_ids=frozenset({"active_1", "reserve_1"}),
        expected_capability_versions=_expected_versions())
    assert join["entity"]["active"] is True
    assert env.activation_calls == [("reserve_1", True)]

    changed = deepcopy(env.declarations["active_1"])
    changed["navigation_interface"] = False
    capability = callbacks.capability_change(
        _capability_event(capability_declaration=changed),
        expected_actual_agent_ids=frozenset({"active_1", "reserve_1"}),
        expected_capability_versions=_expected_versions(active_1=2))
    assert capability["controller"]["capability_declaration"] == changed
    assert capability["controller"]["capability_version"] == 2
    assert env.reconfigure_calls == [("active_1", changed, 2)]


def test_live_env_adapter_rejects_inventory_that_does_not_partition_robots():
    scenario = _lifecycle_scenario()
    scenario["runtime_lifecycle"]["preloaded_reserve_agent_ids"] = []

    with pytest.raises(ValueError, match="partition scenario robots"):
        build_env_lifecycle_callbacks(_BenchmarkEnv(), scenario)


def test_member_exit_deactivates_control_before_public_event():
    env = _BenchmarkEnv()
    scenario = _lifecycle_scenario()
    scenario["runtime_lifecycle"] = {
        "initial_agent_ids": ["active_1", "reserve_1"],
        "preloaded_reserve_agent_ids": [],
    }
    callbacks = build_env_lifecycle_callbacks(env, scenario)
    participant = _Participant()
    participant.task_spec["initial_fleet"].append({
        "id": "reserve_1",
        "capability_declaration": deepcopy(env.declarations["reserve_1"]),
    })
    boundary = IsaacRuntimeLifecycleBoundary(
        participant,
        available_agent_ids={"active_1", "reserve_1"},
        actual_agent_ids={"active_1", "reserve_1"},
        member_exit_deactivate_callback=callbacks.deactivate_preloaded_articulation,
    )

    event = {"kind": "member_exit", "agent": "active_1",
             "replacement_agent": "reserve_1", "time_s": 5.0, "id": "exit-1"}
    result = boundary.apply_event(
        event, pose_reader=lambda name: [0., 0., .2])

    assert result["control_deactivation_confirmed"] is True
    assert result["physical_evidence"]["entity"]["active"] is False
    assert env.activation_calls == [("active_1", False)]
    assert boundary.active_agent_ids == frozenset({"reserve_1"})
    assert participant.events[-1]["type"] == "member_exit"


def test_member_exit_rejects_bad_deactivation_readback_without_publication():
    callbacks, articulations, _ = _callbacks(reserve_active=True,
                                              reserve_bad_activation_readback=True)
    participant = _Participant()
    participant.task_spec["initial_fleet"].append({
        "id": "reserve_1", "capability_declaration": deepcopy(GROUND),
    })
    boundary = IsaacRuntimeLifecycleBoundary(
        participant,
        available_agent_ids={"active_1", "reserve_1"},
        actual_agent_ids={"active_1", "reserve_1"},
        member_exit_deactivate_callback=callbacks.deactivate_preloaded_articulation,
    )
    event = {"kind": "member_exit", "agent": "reserve_1",
             "replacement_agent": "active_1", "time_s": 5.0, "id": "exit-1"}

    with pytest.raises(RuntimeLifecycleUnsupported) as caught:
        boundary.apply_event(event, pose_reader=lambda name: [0., 0., .2])

    assert caught.value.result["status"] == "failed"
    assert articulations["reserve_1"].activation_calls == [False]
    assert participant.events == []
    assert boundary.active_agent_ids == frozenset({"active_1", "reserve_1"})
