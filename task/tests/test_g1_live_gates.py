"""Measured gates must control live attachment independently of shadow phases."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
pytest.importorskip('pxr')
import torch
from task.executors.g1_live import G1LiveHandle, G1LiveRuntime


def handle():
    h = G1LiveHandle.__new__(G1LiveHandle)
    h.receipt = h.error = h.cancel_reason = None
    h.elapsed = 0.
    h.attached = False
    h._target = None
    h.base_id, h.hand_id = 0, 1
    h.root = np.eye(4)
    palm = np.diag([1.,-1.,-1.,1.])
    palm[2,3] = 1.
    h._body_pose = lambda i: np.eye(4) if i == 0 else palm
    h.robot = NS(data=NS(joint_pos=torch.zeros(1,1)), set_joint_position_target=lambda *a,**k:None)
    h.joint_ids = {'finger':0}
    h.view = NS(device='cpu')
    h.object = NS(data=NS(root_pos_w=torch.tensor([[0.,0.,1.]]), root_quat_w=torch.tensor([[1.,0.,0.,0.]])))
    ack=[]
    h.skill = NS(config=NS(timeout_s=90.), place=np.array([0.,0.,1.]), pick=np.array([0.,0.,1.]),
        robot=NS(base_local={'base':np.eye(4)},base_paths={'base'}),
        transfer=NS(names=[], closed={'finger':.7},pocket=np.zeros(3),object_point=np.zeros(3),
            step=lambda *a:{'joint_angles_rad':{'finger':.7},'request_attach':True,'request_release':False},
            acknowledge_attachment=ack.append))
    h._follow=lambda:None
    return h, ack


def test_shadow_attachment_request_waits_for_measured_closed_fingers():
    h,ack=handle()
    h.advance(.005)
    assert not h.attached and ack==[]
    h.robot.data.joint_pos[0,0]=.7
    h.advance(.005)
    assert h.attached and ack==[True]


def test_measured_palm_distance_blocks_attachment():
    h,ack=handle()
    h.robot.data.joint_pos[0,0]=.7
    h.object.data.root_pos_w[0,0]=.03
    h.advance(.005)
    assert not h.attached and ack==[]


def test_release_waits_for_measured_placement_and_cancel_does_not_drop():
    h,ack=handle()
    h.robot.data.joint_pos[0,0]=.7
    h.advance(.005)
    h.cancel('stop task')
    assert h.attached
    h.skill.transfer.step=lambda *a:{'joint_angles_rad':{'finger':.7},'request_attach':False,'request_release':True}
    h.skill.place=np.array([.04,0.,1.])
    h.advance(.005)
    assert h.attached and ack==[True]
    h.skill.place=np.array([0.,0.,1.])
    h.advance(.005)
    assert not h.attached and ack==[True,False]


def test_timeout_retains_attachment_and_does_not_acknowledge_stop():
    h,ack=handle()
    h.attached=True
    h.elapsed=91.
    h.advance(.005)
    assert h.attached and h.error and not ack
    with pytest.raises(RuntimeError,match='timeout'):
        h.after_step()


def test_invalid_ownership_blocks_trajectory_progress():
    h,ack=handle()
    def rejected():raise ValueError('foreign or released lease')
    h._ownership_guard=rejected
    h.advance(.005)
    assert h.error=='foreign or released lease' and h._target is None and not ack


def test_revoked_ownership_never_writes_existing_targets_or_attached_objects():
    h, ack = handle()
    h.attached = True
    h._target = {'finger': .7}
    writes = []
    h._follow = lambda: writes.append('object')
    h._write_target = lambda: writes.append('joint')
    def rejected():
        raise ValueError('released lease')
    h._ownership_guard = rejected
    h.advance(.005)
    h.advance(.005)
    assert writes == [] and ack == [] and h.attached


def test_selective_reset_invalidates_old_owner_and_allows_restored_row_retry():
    runtime = G1LiveRuntime(NS(num_envs=2))
    first = NS(receipt=None, attached=True, _target={'finger': .7}, error=None)
    other = NS(receipt=None, attached=True, _target={'finger': .7}, error=None)
    runtime.add(0, 'g1', first)
    runtime.add(1, 'g1', other)
    runtime.reset([0])
    assert first.error and not first.attached and first._target is None
    assert other.error is None and other.attached
    assert (0, 'g1') not in runtime.handles
    retry = NS(receipt=None)
    assert runtime.add(0, 'g1', retry) is retry
    assert runtime.handles[(1, 'g1')] is other
