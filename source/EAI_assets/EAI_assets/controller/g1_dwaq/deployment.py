"""dwaq_named_body_v1 observations and named-joint application, without training imports."""
from pathlib import Path
import json
import torch

CONTRACT = json.loads(Path(__file__).with_name('deployment_contract.json').read_text())
JOINT_NAMES = tuple(CONTRACT['contract']['joint_names'])
FRAME_FIELDS = {'angular_velocity_b':(0,3),'projected_gravity_b':(3,6),'velocity_command':(6,9),
    'joint_position_error':(9,38),'joint_velocity_error':(38,67),'previous_raw_action':(67,96),
    'sin_left_right_phase':(96,98),'cos_left_right_phase':(98,100)}


def locomotion_frame(data, joint_ids, previous_action, command, episode_steps):
    """Raw frame; checkpoint scales are all 1.0 (including angular/joint velocity).

    Body frame command = [vx m/s, vy m/s, yaw_rate rad/s]. Phase advances at
    50 Hz, period .8 s, right-leg offset .5. No velocity normalization or noise.
    History is deliberately UNCLIPPED; only actor's current frame is clipped.
    """
    phase = torch.remainder(episode_steps.to(command.dtype)*.02,.8)/.8
    legs = torch.stack((phase,torch.remainder(phase+.5,1.)),dim=-1)
    frame = torch.cat((data.root_ang_vel_b,data.projected_gravity_b,command,
        (data.joint_pos-data.default_joint_pos)[:,joint_ids],
        (data.joint_vel-data.default_joint_vel)[:,joint_ids],previous_action,
        torch.sin(2*torch.pi*legs),torch.cos(2*torch.pi*legs)),dim=-1)
    if frame.shape[-1]!=100 or not torch.isfinite(frame).all():
        raise ValueError('Expected finite dwaq_named_body_v1 frame [N,100]')
    return frame


def critic_height_scan(scanner_position_w, ray_hits_w):
    """Training-only height scan: 17x11 torso yaw grid at .1 m resolution.

    NEVER append this to the 100-D deployed actor frame or 500-D history.
    Uses actual collision hits; missing geometry is an error, not flat terrain.
    """
    if ray_hits_w.shape[-2]!=187 or not torch.isfinite(ray_hits_w).all():
        raise ValueError('Expected 187 finite actual-terrain height hits')
    return (scanner_position_w[:,2:3]-ray_hits_w[...,2]-.5).clamp(-100.,100.)


class G1DwaqController:
    def __init__(self,robot,policy_path=None):
        self.robot=robot
        ids,names=robot.find_joints(list(JOINT_NAMES),preserve_order=True)
        if tuple(names)!=JOINT_NAMES or len(set(ids))!=29:
            raise ValueError('G1 body joint names/order mismatch')
        other=[n for n in robot.joint_names if n not in JOINT_NAMES]
        if len(other)!=24 or any(not n.startswith(('L_','R_')) for n in other):
            raise ValueError('Expected repository 29 body + 24 Inspire finger joints')
        self.joint_ids=ids
        self.hand_ids=[i for i,n in enumerate(robot.joint_names) if n not in JOINT_NAMES]
        like=robot.data.joint_pos
        self.policy=torch.jit.load(str(policy_path or Path(__file__).with_name('policy.pt')),map_location=like.device).eval()
        self.history=like.new_zeros((like.shape[0],500))
        self.previous_action=like.new_zeros((like.shape[0],29))
        self.steps=torch.zeros(like.shape[0],dtype=torch.long,device=like.device)
        self.reset_mask=torch.ones_like(self.steps,dtype=torch.bool)

    def reset(self,env_ids=None):
        ids=slice(None) if env_ids is None else env_ids
        self.history[ids]=0.;self.previous_action[ids]=0.;self.steps[ids]=0;self.reset_mask[ids]=True

    @torch.no_grad()
    def compute(self,command):
        if command.shape!=(self.history.shape[0],3):
            raise ValueError('Expected base velocity command [N,3]')
        frame=locomotion_frame(self.robot.data,self.joint_ids,self.previous_action,command,self.steps)
        raw,self.history=self.policy(frame,self.history,self.reset_mask)
        if not torch.isfinite(raw).all():raise ValueError('Nonfinite G1 action')
        self.previous_action.copy_(raw)  # training buffer stores raw before clip
        self.reset_mask.fill_(False);self.steps+=1
        return raw.clamp(-5.,5.)

    def apply(self,actions):
        targets=self.robot.data.default_joint_pos.clone()
        limits=self.robot.data.soft_joint_pos_limits[:,self.joint_ids]
        body=targets[:,self.joint_ids]+.25*actions.clamp(-5.,5.)
        targets[:,self.joint_ids]=body.clamp(limits[...,0],limits[...,1])
        targets[:,self.hand_ids]=0.
        self.robot.set_joint_position_target(targets)
