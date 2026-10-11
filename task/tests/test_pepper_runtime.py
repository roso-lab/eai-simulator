"""Protocol tests of the adapter; physical acceptance is a separate tmp probe."""
from types import SimpleNamespace

import pytest

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.pepper_transport.provider import CARRY_MODE, SKILL_ID, PepperTransportProvider
from algorithm.skills.pepper_transport import runtime as module
from task import DependencyOutcome


def setup(monkeypatch):
    config = SimpleNamespace(cube_prim_name="box",box_position=(.2,0,.63),box_size=(.26,.28,.30))
    state = SimpleNamespace(config=config, phase=SimpleNamespace(value="idle"), attached=False)
    transport = SimpleNamespace(phase=SimpleNamespace(value="wait_for_grasp"),
        config=SimpleNamespace(source_table_name="source", destination_table_name="destination",
            source_table_position=(.2,0,.43),destination_table_position=(.2,.75,.43),table_size=(.42,.44,.08)))
    controller = {"runtime": SimpleNamespace(state=state, last_error=None), "transport": transport,
                  "execution_enabled": False, "auto_start": True}
    box = SimpleNamespace(data=SimpleNamespace(root_pos_w=[[.2,0,.63]]),cfg=SimpleNamespace(spawn=SimpleNamespace(size=config.box_size)))
    supports = {name:SimpleNamespace(get_world_poses=lambda p=pos:([p],[[1,0,0,0]]))
                for name,pos in [('source',(.2,0,.43)),('destination',(.2,.75,.43))]}
    support_cfg = SimpleNamespace(spawn=SimpleNamespace(size=transport.config.table_size))
    env = SimpleNamespace(scene=SimpleNamespace(rigid_objects={"box": box}, extras=supports),
                          cfg=SimpleNamespace(scene=SimpleNamespace(source=support_cfg, destination=support_cfg)))
    monkeypatch.setattr(module, "pepper_controller_state", lambda env, robot: controller)
    request = SkillRequest("p1", SKILL_ID, "pepper_1", parameters={
        "object_ref": "box", "source_ref": "source", "destination_ref": "destination",
        "carry_mode": CARRY_MODE, "horizon_ticks": 10,
    })
    plan = PepperTransportProvider().plan(request)
    return env, controller, request, plan


def test_wrong_target_rejected_before_controller_enable(monkeypatch):
    env, controller, request, plan = setup(monkeypatch)
    request.parameters["destination_ref"] = "other"
    plan = PepperTransportProvider().plan(request)
    with pytest.raises(ValueError, match="targets"):
        module.PepperTransportHandle(plan, scene=env)
    assert controller["execution_enabled"] is False


def test_completion_requires_release_stop_and_stable_ticks(monkeypatch):
    env, controller, request, plan = setup(monkeypatch)
    handle = module.PepperTransportHandle(plan, scene=env, neutral_action=lambda *a: "zero",
                                         stopped_probe=lambda *a: True, settle_ticks=2)
    assert controller["execution_enabled"] is True
    assert handle.command() == "zero"
    controller["runtime"].state.phase.value = controller["transport"].phase.value = "done"
    controller["runtime"].state.attached = True
    assert handle.after_step() is None
    controller["runtime"].state.attached = False
    assert handle.after_step() is None
    result = handle.after_step()
    assert result.outcome is DependencyOutcome.COMPLETED
    assert controller["execution_enabled"] is False


def test_cancel_does_not_drop_attached_box_or_report_success(monkeypatch):
    env, controller, request, plan = setup(monkeypatch)
    handle = module.PepperTransportHandle(plan, scene=env, neutral_action=lambda *a: "zero",
                                         stopped_probe=lambda *a: True, settle_ticks=1)
    controller["runtime"].state.attached = True
    handle.cancel("cancelled by caller")
    assert controller["runtime"].state.attached is True
    assert controller["execution_enabled"] is True
    assert handle.after_step() is None
    controller["runtime"].state.phase.value = controller["transport"].phase.value = "done"
    controller["runtime"].state.attached = False
    result = handle.after_step()
    assert result.outcome is DependencyOutcome.TERMINAL_FAILURE and result.quiescent


def test_unknown_controller_error_does_not_claim_stopped(monkeypatch):
    env, controller, request, plan = setup(monkeypatch)
    handle = module.PepperTransportHandle(plan, scene=env)
    controller["runtime"].last_error = "IK failed while holding"
    with pytest.raises(RuntimeError, match="IK failed"):
        handle.after_step()
    assert controller["execution_enabled"] is True


def test_moved_box_or_support_rejected_before_start(monkeypatch):
    env, controller, request, plan = setup(monkeypatch)
    env.scene.rigid_objects['box'].data.root_pos_w = [[2,0,.63]]
    with pytest.raises(ValueError,match='pickup'):
        module.PepperTransportHandle(plan,scene=env)
    env.scene.rigid_objects['box'].data.root_pos_w = [[.2,0,.63]]
    env.scene.extras['destination'].get_world_poses = lambda:([[.2,2,.43]],[[1,0,0,0]])
    with pytest.raises(ValueError,match='support pose'):
        module.PepperTransportHandle(plan,scene=env)
    assert controller['execution_enabled'] is False


@pytest.mark.parametrize('moving', ['arm', 'box_linear', 'box_angular', 'none'])
def test_default_stop_probe_checks_arm_and_box_motion(monkeypatch, moving):
    env, controller, _, _ = setup(monkeypatch)
    class Vector:
        def __init__(self, values):self.values=values
        def detach(self):return self
        def cpu(self):return self
        def tolist(self):return self.values
    class Joints:
        def __getitem__(self, key):
            return Vector([.2 if moving=='arm' and key[1]==[3] else 0.] * len(key[1]))
    class BoxVelocity:
        def __getitem__(self, key):
            return Vector([.2 if moving=='box_linear' else 0.,0.,0.,
                           .2 if moving=='box_angular' else 0.,0.,0.])
    robot=SimpleNamespace(find_joints=lambda *a,**kw:([0,1,2],[]),
                          data=SimpleNamespace(joint_vel=Joints()))
    env.scene.articulations={'pepper_1':robot}
    controller['runtime']._resolve=lambda *a:([3],1)
    env.scene.rigid_objects['box'].data.root_vel_w=BoxVelocity()
    assert module.measured_transport_stopped(env,'pepper_1') is (moving=='none')
