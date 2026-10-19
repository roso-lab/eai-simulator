"""Quaternion math in Isaac Lab's w,x,y,z convention, using only torch."""
import torch

def wrap_to_pi(x):
    return torch.remainder(x+torch.pi,2*torch.pi)-torch.pi

def euler_xyz_from_quat(q):
    w,x,y,z=q.unbind(-1)
    roll=torch.atan2(2*(w*x+y*z),1-2*(x*x+y*y))
    pitch=torch.asin((2*(w*y-z*x)).clamp(-1.,1.))
    yaw=torch.atan2(2*(w*z+x*y),1-2*(y*y+z*z))
    return roll,pitch,yaw
