from __future__ import annotations
import importlib.util, sys, types
from pathlib import Path
from types import SimpleNamespace

MODULE = Path("source/EAI/EAI/hmrs_ros/orsus_odometry.py")
SPEC = importlib.util.spec_from_file_location("orsus_odometry_test_module", MODULE)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
OrsusOdometryManager = MOD.OrsusOdometryManager

class Tensor:
 def __init__(self,value): self.value=value
 def __getitem__(self,_): return self
 def detach(self): return self
 def cpu(self): return self
 def tolist(self): return self.value
class Message:
 def __init__(self):
  vector=lambda: SimpleNamespace(x=0.,y=0.,z=0.)
  self.header=SimpleNamespace(stamp=SimpleNamespace(sec=0,nanosec=0),frame_id='')
  self.child_frame_id=''; self.pose=SimpleNamespace(pose=SimpleNamespace(position=vector(),orientation=SimpleNamespace(w=0.,x=0.,y=0.,z=0.)))
  self.twist=SimpleNamespace(twist=SimpleNamespace(linear=vector(),angular=vector()))
class Publisher:
 def __init__(self): self.messages=[]
 def publish(self,msg): self.messages.append(msg)
class Node:
 def __init__(self): self.publisher=Publisher(); self.destroyed=False
 def create_publisher(self,*_): return self.publisher
 def destroy_node(self): self.destroyed=True

def install_ros(monkeypatch):
 node=Node(); state={'ok':False,'shutdown':0}
 rclpy=types.ModuleType('rclpy'); rclpy.ok=lambda:state['ok']
 def init(): state['ok']=True
 def shutdown(): state['ok']=False;state['shutdown']+=1
 rclpy.init=init;rclpy.shutdown=shutdown;rclpy.create_node=lambda _:node
 nav=types.ModuleType('nav_msgs.msg');nav.Odometry=Message
 monkeypatch.setitem(sys.modules,'rclpy',rclpy);monkeypatch.setitem(sys.modules,'nav_msgs',types.ModuleType('nav_msgs'));monkeypatch.setitem(sys.modules,'nav_msgs.msg',nav)
 return node,state

def test_manager_publishes_root_pose_velocity_and_closes(monkeypatch):
 node,state=install_ros(monkeypatch)
 data=SimpleNamespace(root_pos_w=Tensor([1,2,3]),root_quat_w=Tensor([1,0,0,0]),root_lin_vel_b=Tensor([.5,.2,0]),root_ang_vel_b=Tensor([0,0,.1]))
 env=SimpleNamespace(scene=SimpleNamespace(articulations={'carter_1':SimpleNamespace(data=data)}),step_dt=.02)
 manager=OrsusOdometryManager(env,{'carter_1':'/carter_1'});env._orsus_odometry_manager=manager
 manager.update(.02);manager.update(.02)
 assert len(node.publisher.messages)==2
 msg=node.publisher.messages[-1];assert (msg.header.stamp.sec,msg.header.stamp.nanosec)==(0,40_000_000)
 assert msg.header.frame_id=='mapping_init';assert msg.child_frame_id=='carter_1/base_link';assert msg.pose.pose.position.x==1.;assert msg.twist.twist.linear.x==.5
 manager.close();assert node.destroyed;assert state['shutdown']==1;assert not hasattr(env,'_orsus_odometry_manager')

def test_manager_without_instances_does_not_import_ros():
 manager=OrsusOdometryManager(SimpleNamespace(),{});manager.update(.1);assert manager.registered_instances==();manager.close()
