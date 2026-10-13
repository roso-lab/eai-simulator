"""Executor contract fixtures; no claim of native physics from these tests."""
from types import SimpleNamespace
import pytest
from algorithm.skills import SkillContractError
from algorithm.skills.aerial_transport import RigidPose
from algorithm.skills.tests.test_aerial_transport import request, topology
from task.executors.aerial_pick_place import AerialPickPlaceExecutor
from task.resources import (InvalidResourceLeaseError, ResourceBusyError,
    ResourceManager, ResourceQuarantinedError)


class Bridge:
    def __init__(self, env):
        self.env, self.bound, self.held, self.collision, self.support = env, False, False, True, False
        self.calls, self.fail_operation, self.fail_unbind = [], None, False

    def bind(self, *args):
        self.bound = True

    def unbind(self, key):
        if self.fail_unbind:
            raise RuntimeError("unbind failed")
        if self.held or self.support or not self.collision:
            raise RuntimeError("unclean physical state")
        self.bound = False

    def attached(self, key):
        return self.held

    def measure(self, key):
        return tuple(self.env.root), tuple(self.env.root) if self.held else (0., 0., .2)

    def evidence(self, key):
        return {"collision_disabled": not self.collision, "joint_path": "/private/joint" if self.held else None}

    def apply(self, key, operation, confirmation_id):
        self.calls.append(operation)
        if self.fail_operation == operation:
            raise RuntimeError("mutation error")
        if operation == "create_support":
            self.support = True
        elif operation == "disable_collision":
            assert self.support
            self.collision = False
        elif operation == "attach":
            self.held = True
        elif operation == "remove_support":
            self.support = False
        elif operation in {"release", "rollback"}:
            self.held, self.support, self.collision = False, False, True
        return {"operation": operation, "confirmation_id": confirmation_id, "applied": True}


class Env:
    num_envs = 1
    def __init__(self):
        self.root, self.goal, self.t = [0., 0., 1.], None, 0.
        self.quiescent = True
        self._controller_configs = {"iris_1": SimpleNamespace(
            command_name="goal_position", control_mode="position",
            robot_type="PegasusIris")}
        self.applied, self.fail_step = [], False

    def set_command(self, robot, name, tensor):
        assert name == "goal_position" and len(tensor) == 1 and len(tensor[0]) == 3
        self.goal = tensor[0]

    def step(self, actions):
        assert actions == {"iris_1": [[0., 0., 0.]]}
        if self.fail_step:
            raise RuntimeError("controller rejected")
        self.applied.append(self.goal)
        self.t += .1


def executor(**options):
    env = Env()
    bridge = Bridge(env)
    ex = AerialPickPlaceExecutor(env, topology=topology(), bridge=bridge,
        world_state_validator=lambda seq, motion: None,
        reference_resolver=lambda kind, ref: "/robot" if kind == "robot" else "/box",
        pose_reader=lambda robot: RigidPose(tuple(env.root)), simulation_time_reader=lambda: env.t,
        action_factory=lambda env, robot, goal: [list(goal)],
        quiescence_reader=lambda key, robot: env.quiescent, **options)
    return env, bridge, ex


def attach(env, bridge, ex):
    ex.dispatch(request())
    ex.control(request())
    ex.control(request("continue"))
    ex.control(request("continue"))
    env.root = [0., 0., .205]
    env.t += 10.
    ex.control(request("continue"))
    assert bridge.held


def test_applies_neutral_external_actions_and_acknowledges_only_current_mutation():
    env, bridge, ex = executor()
    queued = ex.dispatch(request())
    assert queued.private_evidence["command_applied"] is True
    supporting = ex.control(request())
    assert supporting.state["phase"] == "supporting"
    assert bridge.calls == ["create_support"]
    ex.control(request("continue"))
    assert bridge.calls == ["create_support", "disable_collision"]
    ex.control(request("hold"))
    assert bridge.calls == ["create_support", "disable_collision"]
    stopped = ex.control(request("stop"))
    assert bridge.calls[-1] == "rollback" and bridge.collision and not bridge.support
    assert ex.resources.snapshot()["leases"] == []
    assert stopped.state["physical_outcome"] == "unknown"


@pytest.mark.parametrize("cause", ["stop", "timeout", "step_failure", "mutation_failure"])
def test_retains_attached_payload_and_lease_until_explicit_host_release(cause):
    env, bridge, ex = executor(timeout_s=15.)
    attach(env, bridge, ex)
    if cause == "timeout":
        env.t = 16.
    elif cause == "step_failure":
        env.fail_step = True
    elif cause == "mutation_failure":
        bridge.fail_operation = "remove_support"
    stopped = ex.control(request("stop" if cause == "stop" else "continue"))
    assert stopped.state["status"] in {"cancelled", "failed"}
    assert stopped.state["attached"] and bridge.held and not bridge.collision
    assert "release" not in bridge.calls and "rollback" not in bridge.calls
    assert ex.resources.snapshot()["leases"][0]["state"] == "quarantined"
    with pytest.raises(ResourceQuarantinedError, match="scene"):
        ex.control(request("continue"))
    with pytest.raises(SkillContractError, match="retained quarantined"):
        ex.host_release("carry", quiescent=False)
    env.fail_step, bridge.fail_operation = False, None
    released = ex.host_release("carry", quiescent=True)
    assert not bridge.held and bridge.collision and not bridge.support
    assert ex.resources.snapshot()["leases"] == []
    assert not released.state["attached"]
    assert "joint_path" not in str(stopped.to_mapping())


def test_controller_requirement_conflicting_identity_and_shared_resources():
    resources = ResourceManager()
    env, bridge, ex = executor(resource_manager=resources)
    env._controller_configs["iris_1"].control_mode = "rotor_velocity"
    with pytest.raises(SkillContractError, match="goal_position"):
        ex.dispatch(request())
    env._controller_configs["iris_1"].control_mode = "position"
    ex.dispatch(request())
    with pytest.raises(SkillContractError, match="immutable"):
        ex.control(request("stop", object_ref="other"))
    assert bridge.calls == []
    _, _, other = executor(resource_manager=resources)
    with pytest.raises(ResourceBusyError):
        other.dispatch(request(key="other"))


def test_pregrab_mutation_failure_rolls_back_before_step_and_releases_lease():
    env, bridge, ex = executor()
    ex.dispatch(request())
    ex.control(request())
    bridge.fail_operation = "disable_collision"
    failed = ex.control(request("continue"))
    assert failed.state["status"] == "failed"
    assert bridge.calls[-1] == "rollback" and not bridge.support and bridge.collision
    assert not failed.private_evidence["resources_retained"]


def test_queue_only_hook_cannot_confirm_running_execution():
    env, bridge, ex = executor()
    ex._advance = lambda actions: None
    failed = ex.dispatch(request())
    assert failed.state["status"] == "failed"
    assert not failed.private_evidence["command_applied"]
    assert ex.resources.snapshot()["leases"][0]["state"] == "quarantined"


def test_whole_scene_quarantine_blocks_disjoint_dispatch_before_step():
    resources = ResourceManager()
    blocked = resources.acquire("other", ["robot:iris_2", "object:/other"])
    resources.quarantine(blocked, "uncertain retained body")
    env, _, ex = executor(resource_manager=resources)
    with pytest.raises(ResourceQuarantinedError, match="scene"):
        ex.dispatch(request())
    assert env.applied == []


def test_queue_only_terminal_step_cannot_release_clean_pregrab_state():
    env, bridge, ex = executor()
    ex.dispatch(request())
    ex._advance = lambda actions: None
    stopped = ex.control(request("stop"))
    assert stopped.state["status"] == "failed"
    assert stopped.private_evidence["command_applied"] is False
    assert bridge.calls == ["rollback"]
    assert ex.resources.snapshot()["leases"][0]["state"] == "quarantined"


def test_keyboard_interrupt_after_mutation_quarantines_uncertain_state():
    env, bridge, ex = executor()
    ex.dispatch(request())

    def interrupt(actions):
        raise KeyboardInterrupt()

    ex._advance = interrupt
    with pytest.raises(KeyboardInterrupt):
        ex.control(request())
    assert bridge.support
    assert "carry" in ex._quarantined
    assert ex.resources.snapshot()["leases"][0]["state"] == "quarantined"


def test_shared_tick_prepare_requires_one_advancing_host_step_and_single_outstanding():
    env, _, ex = executor()
    prepared = ex.prepare_dispatch(request())
    assert env.t == 0. and env.applied == []
    with pytest.raises(SkillContractError, match="already prepared"):
        ex.prepare_control(request())
    env.step({prepared.robot_id: prepared.action})
    response = ex.after_step(prepared)
    assert response.private_evidence["command_applied"] is True
    with pytest.raises(SkillContractError, match="stale"):
        ex.after_step(prepared)


def test_external_lease_is_authenticated_covers_resources_and_is_not_released():
    resources = ResourceManager()
    lease = resources.acquire(
        "cooperative:batch", ["robot:iris_1", "object:/box", "extra"])
    env, _, ex = executor(resource_manager=resources)
    prepared = ex.prepare_dispatch(request(), lease=lease)
    env.step({prepared.robot_id: prepared.action})
    ex.after_step(prepared)
    stopped = ex.prepare_control(request("stop"))
    env.step({stopped.robot_id: stopped.action})
    response = ex.after_step(stopped)
    assert response.private_evidence["external_lease"] is True
    assert response.private_evidence["resources_retained"] is True
    assert response.private_evidence["ready_to_release"] is True
    assert resources.snapshot()["leases"][0]["state"] == "active"

    foreign = ResourceManager().acquire(
        "foreign", ["robot:iris_1", "object:/box"])
    _, _, other = executor(resource_manager=resources)
    with pytest.raises(InvalidResourceLeaseError, match="this manager"):
        other.prepare_dispatch(request(key="other"), lease=foreign)

    incomplete_resources = ResourceManager()
    incomplete = incomplete_resources.acquire(
        "cooperative:incomplete", ["robot:iris_1"])
    _, _, incomplete_executor = executor(resource_manager=incomplete_resources)
    with pytest.raises(InvalidResourceLeaseError, match="cover"):
        incomplete_executor.prepare_dispatch(
            request(key="incomplete"), lease=incomplete)


def test_external_lease_cleanup_failure_never_signals_ready_to_release():
    resources = ResourceManager()
    lease = resources.acquire(
        "cooperative:batch", ["robot:iris_1", "object:/box"])
    env, bridge, ex = executor(resource_manager=resources)
    prepared = ex.prepare_dispatch(request(), lease=lease)
    env.step({prepared.robot_id: prepared.action})
    ex.after_step(prepared)
    bridge.fail_unbind = True
    stopped = ex.prepare_control(request("stop"))
    env.step({stopped.robot_id: stopped.action})
    response = ex.after_step(stopped)
    assert response.private_evidence["quiescent"] is True
    assert response.private_evidence["ready_to_release"] is False
    assert response.private_evidence["cleanup_error_type"] == "RuntimeError"
    assert resources.snapshot()["leases"][0]["state"] == "quarantined"


def test_terminal_settling_repeats_hover_without_mutation_then_releases():
    env, bridge, ex = executor()
    ex.dispatch(request())
    env.quiescent = False
    prepared = ex.prepare_control(request("stop"))
    env.step({prepared.robot_id: prepared.action})
    first = ex.after_step(prepared)
    assert first.private_evidence["settling"] is True
    assert ex.settling_task_ids == ("carry",)
    assert bridge.calls == ["rollback"]
    env.quiescent = True
    settle = ex.prepare_settle("carry")
    env.step({settle.robot_id: settle.action})
    final = ex.after_step(settle)
    assert final.private_evidence["quiescent"] is True
    assert final.private_evidence["resources_retained"] is False
    assert bridge.calls == ["rollback"]
    assert all(command["command_type"] != "transport_mutation"
               for command in final.private_evidence["execution_samples"][-1]["commands"])
    assert ex.resources.snapshot()["leases"] == []


def test_terminal_settling_timeout_quarantines_and_host_release_remeasures():
    env, bridge, ex = executor(terminal_settle_timeout_s=.15)
    ex.dispatch(request())
    env.quiescent = False
    prepared = ex.prepare_control(request("stop"))
    env.step({prepared.robot_id: prepared.action})
    ex.after_step(prepared)
    settle = ex.prepare_settle("carry")
    env.step({settle.robot_id: settle.action})
    final = ex.after_step(settle)
    assert final.private_evidence["settling"] is False
    assert ex.resources.snapshot()["leases"][0]["state"] == "quarantined"
    with pytest.raises(SkillContractError, match="measured quiescence"):
        ex.host_release("carry", quiescent=True)
    env.quiescent = True
    ex.host_release("carry", quiescent=True)
    assert not bridge.bound and ex.resources.snapshot()["leases"] == []
