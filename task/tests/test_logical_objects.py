"""Shared logical model: no Isaac/USD dependencies, real-host callback boundary."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from task.logical_objects import LogicalObjectRuntime, ControllerObservation, SkillSessionResult


def make_runtime(*, scene_id="room_06", minimum=1, objects=None, robots=("r1", "r2", "r3")):
    host = SimpleNamespace(tick=0, arrived=True, admitted=True, status="completed", active=set(robots), calls=[])
    if objects is None:
        objects = [{"entity_id": "object_01", "category": "book", "metadata": {"mass_kg": .2, "minimum_holders": minimum}, "initial_semantic_point": "source", "target_semantic_point": "target"}]
    def observe(robot, point):
        return ControllerObservation(robot, point, host.tick, host.arrived, "a" * 64)
    def run(command):
        host.calls.append(command)
        return SkillSessionResult("session:" + command["request_id"], command["request_id"], tuple(command["robot_ids"]), command["object_id"], command["operation"], command["semantic_point"], host.tick, host.status, "b" * 64)
    def monitor(session, robot, obj, at):
        return {"session_id": session, "robot_id": robot, "object_id": obj, "tick": at, "active": robot in host.active, "evidence_digest": "c" * 64}
    runtime = LogicalObjectRuntime(scene_id=scene_id, objects=objects, robot_ids=robots, clock=lambda: host.tick, observe=observe, run_session=run, admission=lambda *_: host.admitted, monitor_session=monitor)
    return runtime, host


def execute(runtime, req="pick-1", op="pick", robots=("r1",), point="source"):
    return runtime.execute(request_id=req, operation=op, object_id="object_01", robot_ids=robots, semantic_point=point)


@pytest.mark.parametrize("scene_id", ["room_06", "warehouse", "factory", "restaurant", "fire_rescue", "city", "modern_house_nvd", "house", "warehouse_nvd", "restaurant_svc", "prod"])
def test_same_semantic_session_contract_across_scenes_without_prim(scene_id):
    runtime, host = make_runtime(scene_id=scene_id)
    assert execute(runtime)["status"] == "completed"
    assert runtime.sample_holds()["r1"] == "object_01"
    host.tick = 1
    execute(runtime, "deliver-1", "deliver", point="target")
    assert runtime.object_state("object_01")["state"] == "delivered"
    assert runtime.sample_holds() == {"r1": None, "r2": None, "r3": None}
    assert len(host.calls) == 2


@pytest.mark.parametrize("failure", ["arrival", "admission", "failed_session", "forged_session", "foreign_robot", "wrong_point"])
def test_no_state_transition_without_host_evidence(failure):
    runtime, host = make_runtime()
    if failure == "arrival": host.arrived = False
    if failure == "admission": host.admitted = False
    if failure == "failed_session": host.status = "failed"
    if failure == "forged_session":
        callback = runtime._run_session
        runtime._run_session = lambda cmd: replace(callback(cmd), object_id="forged")
    kwargs = {}
    if failure == "foreign_robot": kwargs["robots"] = ("outside",)
    if failure == "wrong_point": kwargs["point"] = "target"
    if failure == "failed_session":
        assert execute(runtime, **kwargs)["status"] == "failed"
    else:
        with pytest.raises(ValueError): execute(runtime, **kwargs)
    assert runtime.object_state("object_01")["state"] == "at_point"
    assert not runtime.object_state("object_01")["holders"]


def test_idempotence_and_changed_request_rejection():
    runtime, host = make_runtime()
    result = execute(runtime)
    assert execute(runtime) == result
    assert len(host.calls) == 1
    with pytest.raises(ValueError, match="reused"):
        execute(runtime, robots=("r2",))


@pytest.mark.parametrize("field", ["arrival_tick", "arrived", "session_tick", "membership_tick"])
def test_bool_int_aliases_do_not_authenticate_runtime_evidence(field):
    runtime, host = make_runtime(minimum=2)
    host.tick = 1
    if field.startswith("arrival") or field == "arrived":
        observe = runtime._observe
        runtime._observe = lambda r, p: replace(observe(r, p), **({"tick": True} if field == "arrival_tick" else {"arrived": 1}))
    elif field == "session_tick":
        run = runtime._run_session
        runtime._run_session = lambda cmd: replace(run(cmd), tick=True)
    else:
        execute(runtime, robots=("r1", "r2"))
        monitor = runtime._monitor
        runtime._monitor = lambda *args: {**monitor(*args), "tick": True}
        with pytest.raises(ValueError, match="identity/tick"):
            runtime.sample_holds()
        assert runtime.object_state("object_01")["state"] == "interrupted"
        return
    with pytest.raises(ValueError):
        execute(runtime, robots=("r1", "r2"))
    assert runtime.object_state("object_01")["state"] == "at_point"


def test_transfer_requires_current_holder_arrival_and_records_new_owner():
    runtime, host = make_runtime()
    execute(runtime)
    host.tick = 1
    execute(runtime, "receive-1", "receive", robots=("r2",), point="transfer_point")
    assert runtime.sample_holds() == {"r1": None, "r2": "object_01", "r3": None}
    with pytest.raises(ValueError, match="legal holders"):
        execute(runtime, "illegal-delivery", "deliver", point="target")


def test_joint_pick_is_atomic_and_replacement_preserves_minimum():
    runtime, host = make_runtime(minimum=2)
    with pytest.raises(ValueError, match="enough simultaneous"):
        execute(runtime)
    execute(runtime, robots=("r1", "r2"))
    assert runtime.sample_holds()["r2"] == "object_01"
    host.tick = 1
    execute(runtime, "join", "join", robots=("r3",), point="handoff")
    execute(runtime, "leave", "leave", robots=("r1",), point="handoff")
    assert runtime.sample_holds() == {"r1": None, "r2": "object_01", "r3": "object_01"}
    with pytest.raises(ValueError, match="join before"):
        execute(runtime, "unsafe-leave", "leave", robots=("r2",), point="handoff")
    host.tick = 2
    execute(runtime, "deliver", "deliver", robots=("r2", "r3"), point="target")
    assert runtime.object_state("object_01")["state"] == "delivered"


@pytest.mark.parametrize("failure", ["gap", "inactive", "missing_monitor"])
def test_joint_interruptions_never_become_completion(failure):
    runtime, host = make_runtime(minimum=2)
    execute(runtime, robots=("r1", "r2"))
    if failure == "gap": host.tick = 2
    elif failure == "inactive": host.active.remove("r2")
    else: runtime._monitor = None
    if failure == "inactive":
        assert runtime.sample_holds()["r2"] is None
    else:
        with pytest.raises(ValueError): runtime.sample_holds()
    with pytest.raises(ValueError, match="interrupted"):
        execute(runtime, "deliver", "deliver", robots=("r1", "r2"), point="target")


def test_checkpoint_roundtrip_in_isolated_ledger_restores_only_logical_state():
    runtime, host = make_runtime(minimum=2)
    execute(runtime, robots=("r1", "r2"))
    snapshot = runtime.capture_state()
    restored, other = make_runtime(minimum=2)
    restored.restore_state(snapshot, expected_digest=snapshot["state_digest"])
    assert restored.capture_state() == snapshot
    assert restored.sample_holds() == runtime.sample_holds()
    tampered = deepcopy(snapshot)
    tampered["objects"]["object_01"]["holders"] = ["r3"]
    with pytest.raises(ValueError, match="external registration"):
        restored.restore_state(tampered, expected_digest=snapshot["state_digest"])
    other.tick = 2
    with pytest.raises(ValueError, match="clock"):
        restored.restore_state(snapshot, expected_digest=snapshot["state_digest"])


def test_external_digest_cannot_authorize_future_joint_observation():
    from task.logical_objects import digest
    runtime, _ = make_runtime(minimum=2)
    execute(runtime, robots=("r1", "r2"))
    snapshot = runtime.capture_state()
    snapshot["objects"]["object_01"]["last_joint_tick"] = 1000
    snapshot["state_digest"] = digest({k: v for k, v in snapshot.items() if k != "state_digest"})
    restored, _ = make_runtime(minimum=2)
    with pytest.raises(ValueError, match="future"):
        restored.restore_state(snapshot, expected_digest=snapshot["state_digest"])


def test_pick_place_default_uses_logical_runtime_and_never_native_adapter():
    from algorithm.skills.contracts import SkillRequest
    from task.executors.pick_place import PickPlaceExecutor
    runtime, host = make_runtime()
    executor = PickPlaceExecutor(advance=lambda: None, simulation_time=lambda: 0.0, logical_runtime=runtime)
    req = SkillRequest("pick-1", "eai.manipulation.pick_place", "r1", parameters={"operation": "pick", "object_ref": "object_01", "semantic_point": "source"})
    assert executor.execute(req) is True
    assert runtime.sample_holds()["r1"] == "object_01"
    assert PickPlaceExecutor(advance=lambda: None, simulation_time=lambda: 0).execute(req) is False


def test_public_provider_and_executor_preserve_algorithm_selected_joint_members():
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.pick_place import PickPlaceProvider
    from algorithm.skills.simulator_schema import validate_simulator_skill_request
    from task.executors.pick_place import PickPlaceExecutor
    runtime, host = make_runtime(minimum=2)
    executor = PickPlaceExecutor(advance=lambda: None, simulation_time=lambda: host.tick,
                                 logical_runtime=runtime)
    provider = PickPlaceProvider()
    for req_id, operation, point in (("joint-pick", "pick", "source"), ("joint-deliver", "deliver", "target")):
        request = SkillRequest(req_id, provider.skill_id, "r2", parameters={
            "operation": operation, "object_ref": "object_01",
            "semantic_point": point, "robot_ids": ["r2", "r3"],
        })
        validate_simulator_skill_request(request)
        plan = provider.plan(request)
        assert plan.status == "planned"
        assert plan.commands[0].payload["robot_ids"] == ["r2", "r3"]
        assert executor.execute(request) is True
        host.tick += 1
    assert runtime.object_state("object_01")["state"] == "delivered"
    assert all(cmd["robot_ids"] == ["r2", "r3"] for cmd in host.calls)


@pytest.mark.parametrize("extra", [
    {"robot_ids": []}, {"robot_ids": ["r1", "r1"]}, {"robot_ids": ["r2", "r3"]},
    {"mass_kg": 0.01}, {"completed": True}, {"physical_object_experiment_authorization": "self-authorized"},
    {"base_position_world_m": [0, 0, 0]},
])
def test_logical_public_boundary_rejects_forged_or_ambiguous_command_fields(extra):
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.pick_place import PickPlaceProvider
    from task.executors.pick_place import PickPlaceExecutor
    runtime, host = make_runtime(minimum=2)
    request = SkillRequest("invalid", "eai.manipulation.pick_place", "r1", parameters={
        "operation": "pick", "object_ref": "object_01", "semantic_point": "source", **extra})
    assert PickPlaceProvider().plan(request).status == "invalid_request"
    assert PickPlaceExecutor(advance=lambda: None, simulation_time=lambda: 0,
        logical_runtime=runtime).execute(request) is False
    assert host.calls == []
    assert runtime.object_state("object_01")["state"] == "at_point"


def test_malformed_second_joint_sample_does_not_half_mutate_ownership():
    runtime, host = make_runtime(minimum=2)
    execute(runtime, robots=("r1", "r2"))
    before = runtime.object_state("object_01")
    host.active.remove("r1")
    monitor = runtime._monitor
    runtime._monitor = lambda session, robot, obj, at: {} if robot == "r2" else monitor(session, robot, obj, at)
    with pytest.raises(ValueError, match="missing"):
        runtime.sample_holds()
    after = runtime.object_state("object_01")
    assert after["state"] == "interrupted"
    assert after["holders"] == before["holders"]
    assert after["hold_sessions"] == before["hold_sessions"]
    with pytest.raises(ValueError, match="missing"):
        runtime.sample_holds()


def test_unknown_host_failure_cannot_be_silently_retried_or_checkpointed():
    runtime, host = make_runtime()
    def crash(command):
        host.calls.append(command)
        raise RuntimeError("controller response lost")
    runtime._run_session = crash
    with pytest.raises(RuntimeError): execute(runtime)
    with pytest.raises(ValueError, match="silently retry"): execute(runtime)
    with pytest.raises(ValueError, match="quarantines"): execute(runtime, req="different-request")
    with pytest.raises(ValueError, match="unresolved"): runtime.capture_state()
    assert len(host.calls) == 1


def test_registered_clean_checkpoint_clears_unknown_host_session_quarantine():
    runtime, _ = make_runtime()
    checkpoint = runtime.capture_state()
    runtime._run_session = lambda _: (_ for _ in ()).throw(RuntimeError("lost"))
    with pytest.raises(RuntimeError): execute(runtime)
    runtime.restore_state(checkpoint, expected_digest=checkpoint["state_digest"])
    assert runtime.capture_state() == checkpoint
