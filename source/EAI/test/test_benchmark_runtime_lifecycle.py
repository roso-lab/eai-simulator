"""Isaac runtime lifecycle boundary tests; no simulator is started."""

from copy import deepcopy

import pytest

from demo.benchmark.participant_runtime import (
    IsaacRuntimeLifecycleBoundary,
    RuntimeLifecycleUnsupported,
    replacement_assignment_matches,
)
from demo.benchmark.runtime import (
    execution_contract,
    normalize_communication_schedule,
    runtime_lifecycle_preflight,
    validate_runtime_fault_events,
)
from demo.benchmark.scene_challenge import build_scene_runtime_contract, generate_scene_scenario


class _NoopAdapter:
    def manifest(self):
        return {"kind": "participant", "id": "runtime-boundary-test", "version": 1}

    def act(self, events):
        return []


def _participant():
    scenario = generate_scene_scenario(
        scene_key="warehouse_nvd", task_name="runtime_lifecycle_test", seed=9,
        origin=(0.0, 0.0))
    public, api = build_scene_runtime_contract(scenario)
    from demo.benchmark.participant_runtime import ParticipantSession
    session = ParticipantSession(adapter=_NoopAdapter(), api=api, task_spec=public)
    session.emit("episode_started", time_s=0.0, payload={
        "fleet": [robot["id"] for robot in public["initial_fleet"]],
        "objectives": public["objectives"], "resources": public["resources"],
    })
    return session


def _boundary(session, *, actual=None):
    initial = {robot["id"] for robot in session.task_spec["initial_fleet"]}
    return IsaacRuntimeLifecycleBoundary(
        session, available_agent_ids=initial,
        actual_agent_ids=initial if actual is None else set(actual))


def _exit_event(**overrides):
    event = {
        "id": "exit-1", "kind": "member_exit", "time_s": 5.0,
        "agent": "m20_1", "replacement_agent": "carter_1",
    }
    event.update(overrides)
    return event


def _join_event(**overrides):
    event = {
        "id": "join-1", "kind": "member_join", "time_s": 5.0,
        "agent": "reserve_1", "capability_version": 1,
        "capability_declaration": {
            "domain": "ground", "navigation_interface": True,
            "attachment_types": [],
        },
    }
    event.update(overrides)
    return event


def _capability_change_event(**overrides):
    event = {
        "id": "capability-1", "kind": "capability_change", "time_s": 5.0,
        "agent": "m20_1", "capability_version": 2,
        "capability_declaration": {
            "domain": "ground", "navigation_interface": True,
            "attachment_types": ["ur5"],
        },
    }
    event.update(overrides)
    return event


def test_scenario_inventory_hides_preloaded_reserve_until_join_event():
    from EAI.scenario_spec.validation import validate_scenario

    scenario = generate_scene_scenario(
        scene_key="warehouse_nvd", task_name="reserve_inventory_test", seed=11,
        origin=(0.0, 0.0))
    reserve = deepcopy(next(robot for robot in scenario["robots"]
                            if robot["id"] == "carter_1"))
    reserve["id"] = "carter_2"
    reserve["spawn_pose"]["position"][1] += 1.5
    scenario["robots"].append(reserve)
    scenario["runtime_lifecycle"] = {
        "initial_agent_ids": ["iris_1", "m20_1", "carter_1"],
        "preloaded_reserve_agent_ids": ["carter_2"],
    }
    declaration = {
        "domain": "ground", "mobility": "wheeled",
        "navigation_interface": True, "attachment_types": [],
        "rated_payload_kg": None,
        "physical_manipulation_evidence": "not_established",
    }
    scenario["fault_events"] = [{
        "id": "join_reserve", "kind": "member_join", "time_s": 5.0,
        "agent": "carter_2", "capability_version": 1,
        "capability_declaration": declaration,
    }]

    validated = validate_scenario(scenario)
    public, _ = build_scene_runtime_contract(validated)
    assert {robot["id"] for robot in public["initial_fleet"]} == {
        "iris_1", "m20_1", "carter_1"}
    assert "carter_2" not in repr(public)
    assert validated["runtime_lifecycle"]["preloaded_reserve_agent_ids"] == ["carter_2"]


def test_scenario_inventory_must_partition_every_preloaded_robot():
    from EAI.scenario_spec.validation import ScenarioError, validate_scenario

    scenario = generate_scene_scenario(
        scene_key="warehouse_nvd", task_name="bad_reserve_inventory", seed=12,
        origin=(0.0, 0.0))
    scenario["runtime_lifecycle"] = {
        "initial_agent_ids": ["iris_1", "m20_1"],
        "preloaded_reserve_agent_ids": [],
    }
    with pytest.raises(ScenarioError, match="partition every configured robot"):
        validate_scenario(scenario)


def _evidence(event, *, actual_agent_ids, capability_versions):
    result = {
        "actual_agent_ids": sorted(actual_agent_ids),
        "capability_versions": dict(capability_versions),
    }
    if event["kind"] == "member_join":
        result["entity"] = {
            "agent_id": event["agent"], "present": True, "articulation": True,
        }
    else:
        result["controller"] = {
            "agent_id": event["agent"], "configured": True,
            "capability_version": event["capability_version"],
        }
    return result


def _dynamic_boundary(session, *, member_join_callback=None,
                      capability_change_callback=None):
    initial = {robot["id"] for robot in session.task_spec["initial_fleet"]}
    return IsaacRuntimeLifecycleBoundary(
        session, available_agent_ids=initial | {"reserve_1"},
        actual_agent_ids=initial | {"reserve_1"},
        member_join_callback=member_join_callback,
        capability_change_callback=capability_change_callback)


def test_existing_articulations_exit_and_replace_without_join_or_completion_claim():
    session = _participant()
    boundary = _boundary(session)
    poses = {"m20_1": [1.0, 2.0, 0.2], "carter_1": [3.0, 4.0, 0.2]}
    result = boundary.apply_event(
        _exit_event(), affected_node_ids=["priority_carton_stage"],
        pose_reader=lambda agent: poses[agent])

    assert result["status"] == "applied"
    assert result["replacement_mode"] == "preloaded_existing_articulation"
    assert result["replacement_spawned"] is False
    assert result["task_completion_claimed"] is False
    assert boundary.active_agent_ids == frozenset(
        {robot["id"] for robot in session.task_spec["initial_fleet"]} - {"m20_1"})
    assert [event["type"] for event in session.events].count("member_exit") == 1
    report = boundary.report()
    assert report["validated"] is True
    from demo.benchmark.team_lifecycle import TeamLifecycle
    replay = TeamLifecycle.validate_lifecycle_report(report["team_lifecycle"])
    assert replay["members"]["m20_1"]["status"] == "exited"


@pytest.mark.parametrize("kind", ["member_join", "capability_change"])
def test_unimplemented_lifecycle_events_are_unknown_and_do_not_mutate_public_fleet(kind):
    session = _participant()
    boundary = _boundary(session)
    before = deepcopy(session.report())
    event = {"id": "future-1", "kind": kind, "time_s": 3.0,
             "agent": "carter_1", "replacement_agent": "m20_1"}
    with pytest.raises(RuntimeLifecycleUnsupported) as raised:
        boundary.apply_event(event, pose_reader=lambda agent: [0.0, 0.0, 0.2])
    assert raised.value.result["status"] == "unknown"
    assert raised.value.result["validated"] is False
    assert boundary.active_agent_ids == frozenset(
        robot["id"] for robot in session.task_spec["initial_fleet"])
    assert session.report() == before
    assert boundary.report()["unknown_event_count"] == 1


def test_replacement_without_a_same_session_articulation_fails_closed():
    session = _participant()
    initial = {robot["id"] for robot in session.task_spec["initial_fleet"]}
    boundary = _boundary(session, actual=initial - {"carter_1"})
    with pytest.raises(RuntimeLifecycleUnsupported, match="same-session articulation"):
        boundary.apply_event(_exit_event(), pose_reader=lambda agent: [0.0, 0.0, 0.2])
    assert session.report()["events"][-1]["type"] != "member_exit"
    assert boundary.report()["validated"] is False


def test_member_join_requires_physical_entity_evidence_before_public_commit():
    session = _participant()
    calls = []

    def join_callback(event, *, expected_actual_agent_ids,
                      expected_capability_versions):
        calls.append((event, expected_actual_agent_ids,
                      expected_capability_versions))
        return _evidence(
            event, actual_agent_ids=expected_actual_agent_ids,
            capability_versions=expected_capability_versions)

    boundary = _dynamic_boundary(session, member_join_callback=join_callback)
    result = boundary.apply_event(_join_event())

    assert result["status"] == "applied"
    assert result["physical_membership_changed"] is False
    assert result["control_membership_changed"] is True
    assert result["articulation_activation_changed"] is True
    assert result["task_completion_claimed"] is False
    assert calls[0][1] == frozenset(
        {robot["id"] for robot in session.task_spec["initial_fleet"]} | {"reserve_1"})
    assert session.events[-1]["type"] == "member_join"
    assert "reserve_1" in boundary.active_agent_ids
    assert boundary.report()["capability_versions"]["reserve_1"] == 1
    assert boundary.report()["claims"]["dynamic_spawn"] is False
    assert boundary.report()["claims"]["preloaded_reserve_activation"] is True


def test_capability_change_requires_controller_evidence_and_matching_versions():
    session = _participant()

    def capability_callback(event, *, expected_actual_agent_ids,
                            expected_capability_versions):
        return _evidence(
            event, actual_agent_ids=expected_actual_agent_ids,
            capability_versions=expected_capability_versions)

    boundary = _dynamic_boundary(
        session, capability_change_callback=capability_callback)
    result = boundary.apply_event(
        _capability_change_event(), affected_node_ids=["priority_carton_stage"])

    assert result["status"] == "applied"
    assert result["capability_changed"] is True
    assert result["physical_membership_changed"] is False
    assert session.events[-1]["type"] == "capability_change"
    assert session.events[-1]["payload"]["capability_version"] == 2
    assert boundary.report()["capability_versions"]["m20_1"] == 2


def test_physical_callback_exception_rolls_back_without_public_event():
    session = _participant()
    boundary = _dynamic_boundary(
        session, capability_change_callback=lambda *args, **kwargs:
        (_ for _ in ()).throw(RuntimeError("controller reconfigure failed")))
    before_session = deepcopy(session.report())
    before_team = deepcopy(boundary.report()["team_lifecycle"])

    with pytest.raises(RuntimeLifecycleUnsupported) as raised:
        boundary.apply_event(_capability_change_event())

    assert raised.value.result["status"] == "failed"
    assert "logical state rolled back" in raised.value.result["reason"]
    assert raised.value.result["physical_state_after_failure"] == \
        "unknown_requires_callback_compensation_audit"
    assert raised.value.result["physical_compensation_confirmed"] is False
    assert raised.value.result["physical_membership_changed"] is None
    assert session.report() == before_session
    assert boundary.report()["team_lifecycle"] == before_team
    assert boundary.report()["active_agent_ids"] == sorted(
        robot["id"] for robot in session.task_spec["initial_fleet"])
    assert boundary.report()["events"][-1]["status"] == "failed"
    assert boundary.report()["claims"]["capability_reconfiguration"] is False


def test_mismatched_physical_snapshot_is_failed_and_does_not_commit():
    session = _participant()

    def bad_callback(event, *, expected_actual_agent_ids,
                     expected_capability_versions):
        evidence = _evidence(
            event, actual_agent_ids=expected_actual_agent_ids,
            capability_versions=expected_capability_versions)
        evidence["capability_versions"][event["agent"]] -= 1
        return evidence

    boundary = _dynamic_boundary(
        session, capability_change_callback=bad_callback)
    before = deepcopy(session.report())
    with pytest.raises(RuntimeLifecycleUnsupported) as raised:
        boundary.apply_event(_capability_change_event())

    assert raised.value.result["status"] == "failed"
    assert "versions mismatch" in raised.value.result["reason"]
    assert session.report() == before
    assert not any(event["type"] == "capability_change" for event in session.events)


def test_mismatched_physical_agent_set_is_failed_and_does_not_commit():
    session = _participant()

    def bad_callback(event, *, expected_actual_agent_ids,
                     expected_capability_versions):
        evidence = _evidence(
            event, actual_agent_ids=expected_actual_agent_ids,
            capability_versions=expected_capability_versions)
        evidence["actual_agent_ids"].remove(event["agent"])
        return evidence

    boundary = _dynamic_boundary(
        session, member_join_callback=bad_callback)
    before = deepcopy(session.report())
    with pytest.raises(RuntimeLifecycleUnsupported) as raised:
        boundary.apply_event(_join_event())

    assert raised.value.result["status"] == "failed"
    assert "agent set mismatch" in raised.value.result["reason"]
    assert session.report() == before
    assert "reserve_1" not in boundary.active_agent_ids


def _contract_data(event):
    return {
        "scene_key": "warehouse_nvd",
        "robots": [
            {"id": "m20_1", "type": "m20"},
            {"id": "carter_1", "type": "carter"},
        ],
        "phase_graph": {"nodes": [{
            "id": "move", "skill_key": "navigate", "preferred_agent": "m20_1",
            "target_ref": "target", "prerequisites": [],
        }]},
        "fault_events": [event],
    }


def test_live_contract_requires_participant_boundary_and_existing_replacement():
    valid = _contract_data(_exit_event())
    with pytest.raises(ValueError, match="public participant runtime boundary"):
        execution_contract(valid, transport_mode="none", participant_enabled=False)
    assert validate_runtime_fault_events(
        valid, participant_enabled=True)["supported"][0]["implementation"] == \
        "preloaded_existing_articulation_replacement"


def test_live_warehouse_contract_keeps_aerial_baseline_out_of_ground_replacement():
    valid = _contract_data(_exit_event())
    valid["robots"].append({"id": "cf2x_1", "type": "cf2x"})
    result = validate_runtime_fault_events(valid, participant_enabled=True)
    assert result["supported"][0]["replacement_agent"] == "carter_1"
    assert result["supported"][0]["implementation"] == \
        "preloaded_existing_articulation_replacement"


def test_recovery_assignment_must_name_the_declared_replacement():
    assert replacement_assignment_matches(
        {"skill": "transport_object"}, agent_ids=["carter_1"],
        replacement_agent="carter_1")
    assert not replacement_assignment_matches(
        {"skill": "transport_object"}, agent_ids=["m20_1"],
        replacement_agent="carter_1")
    assert replacement_assignment_matches(
        {"skill": "joint_transport"}, agent_ids=["carter_1", "m20_2"],
        replacement_agent="m20_2")


@pytest.mark.parametrize("kind", ["member_join", "capability_change"])
def test_live_contract_rejects_join_and_capability_change_before_isaac(kind):
    event = {"id": "unsupported-1", "kind": kind, "time_s": 2.0,
             "agent": "m20_1", "replacement_agent": "carter_1"}
    with pytest.raises(ValueError, match="unsupported by the Isaac single-session runtime"):
        validate_runtime_fault_events(_contract_data(event), participant_enabled=True)


@pytest.mark.parametrize("kind", ["member_join", "capability_change"])
def test_preflight_emits_unknown_record_for_schema_unsupported_runtime_event(kind):
    report = runtime_lifecycle_preflight({
        "scene_key": "modern_house_nvd",
        "fault_events": [{"id": "f", "kind": kind, "agent": "m20_1", "time_s": 4.0}],
    }, participant_enabled=True)
    assert report["validated"] is False
    assert report["events"][0]["status"] == "unknown"
    assert report["events"][0]["task_completion_claimed"] is False


def test_runtime_contract_admits_only_callback_backed_preloaded_join():
    event = _join_event(agent="carter_2")
    data = _contract_data(event)
    data["robots"].append({"id": "carter_2", "type": "carter"})
    data["runtime_lifecycle"] = {
        "initial_agent_ids": ["m20_1", "carter_1"],
        "preloaded_reserve_agent_ids": ["carter_2"],
    }
    hooks = {"prepare_preloaded_articulations", "member_join"}
    validated = validate_runtime_fault_events(
        data, participant_enabled=True, lifecycle_supported_events=hooks)
    assert validated["supported"][0]["implementation"] == \
        "preloaded_articulation_activation_with_readback"
    preflight = runtime_lifecycle_preflight(
        data, participant_enabled=True, lifecycle_supported_events=hooks)
    assert preflight["validated"]
    assert preflight["events"] == []
    contract = execution_contract(
        data, transport_mode="none", participant_enabled=True,
        lifecycle_supported_events=hooks)
    assert "member_join_physical_callback" in contract["runtime_lifecycle"]["supported"]
    assert "capability_change" in contract["runtime_lifecycle"]["unsupported"]


def test_preloaded_reserve_without_startup_readback_fails_before_isaac():
    data = _contract_data(_exit_event())
    data["runtime_lifecycle"] = {
        "initial_agent_ids": ["m20_1"],
        "preloaded_reserve_agent_ids": ["carter_1"],
    }
    report = runtime_lifecycle_preflight(data, participant_enabled=True)
    assert report["events"][0]["kind"] == "prepare_preloaded_articulations"
    assert report["events"][0]["status"] == "unknown"
    with pytest.raises(ValueError, match="preparation/readback callback"):
        validate_runtime_fault_events(data, participant_enabled=True)


def test_runtime_contract_admits_versioned_capability_callback_without_reserve():
    event = _capability_change_event()
    data = _contract_data(event)
    validated = validate_runtime_fault_events(
        data, participant_enabled=True,
        lifecycle_supported_events={"capability_change"})
    assert validated["supported"][0]["implementation"] == \
        "controller_reconfiguration_with_versioned_readback"


def test_hidden_communication_schedule_is_monotonic_and_detached():
    source = [{"time_s": 2.0, "conditions": {"delay_s": 3.0},
               "channel": "observation"}]
    normalized = normalize_communication_schedule(source)
    source[0]["conditions"]["delay_s"] = 99.0
    assert normalized == [{
        "index": 0, "time_s": 2.0, "conditions": {"delay_s": 3.0},
        "channel": "observation",
    }]
    with pytest.raises(ValueError, match="monotonic"):
        normalize_communication_schedule([
            {"time_s": 2.0, "conditions": {"drop": True}},
            {"time_s": 1.0, "conditions": {"drop": False}},
        ])
    with pytest.raises(ValueError, match="conditions"):
        normalize_communication_schedule([
            {"time_s": 1.0, "conditions": {"future_truth": True}},
        ])
