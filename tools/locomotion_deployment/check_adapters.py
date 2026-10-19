"""Portable state/ordering checks for the Go2 deployment adapter."""
from pathlib import Path
import sys,json
from types import SimpleNamespace as S
import torch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'source/EAI_assets/EAI_assets/controller'))
from go2_extreme_parkour import deployment as d

def fixture(n):
    names=list(reversed(d.EXTREME_PARKOUR_JOINT_ORDER))
    zeros=torch.zeros(n,12)
    defaults=d._TRAINING_DEFAULT_POS.flip(0)[None].repeat(n,1)
    robot=S(joint_names=names,data=S(root_ang_vel_b=torch.ones(n,3),root_lin_vel_b=torch.ones(n,3),
        root_quat_w=torch.tensor([[1.,0.,0.,0.]]).repeat(n,1),root_pos_w=torch.tensor([[0.,0.,.3]]).repeat(n,1),
        joint_pos=defaults+torch.arange(12)[None],joint_vel=zeros))
    contact=S(data=S(net_forces_w_history=torch.zeros(n,2,4,3)),
        find_bodies=lambda names,preserve_order:(list(range(4)),list(d.EXTREME_PARKOUR_FOOT_ORDER)))
    scan=S(data=S(ray_hits_w=torch.zeros(n,132,3)))
    commands={'base_velocity':torch.tensor([[.5,0.,0.]]).repeat(n,1),'parkour_waypoint_yaw':torch.tensor([[.2,.4]]).repeat(n,1)}
    env=S(scene=S(articulations={'go2':robot},sensors={'go2_height_scanner':scan,'go2_contact_forces':contact}),
        _controllers_dict={'go2':{}},actions_dict={},common_step_counter=0,get_command=lambda name,key:commands.get(key))
    return env,commands

def main():
    for n in [1,4,32]:
        env,cmd=fixture(n)
        obs=d.compute_go2_extreme_parkour_observations(env,'go2')
        assert obs.shape==(n,753) and (obs[:,223:]==0).all()
        torch.testing.assert_close(obs[:,13:25],torch.arange(12).flip(0).float()[None].repeat(n,1))
        torch.testing.assert_close(obs[:,:3],torch.full((n,3),.25))
        torch.testing.assert_close(obs[:,185:188],torch.full((n,3),2.))
        state=env._controllers_dict['go2']['extreme_parkour_state'];before=state['history'].clone()
        assert torch.equal(obs,d.compute_go2_extreme_parkour_observations(env,'go2'))
        assert torch.equal(before,state['history']) and (state['history'][:,:,6:8]==0).all()
        # A partial reset does not touch another environment's internal history.
        d.reset_controller(env,'go2',torch.tensor([0]));assert (state['history'][0]==0).all()
        if n>1:torch.testing.assert_close(state['history'][1:],before[1:])
        env.common_step_counter+=1
        again=d.compute_go2_extreme_parkour_observations(env,'go2');assert (again[0,223:]==0).all()
        # Missing rays must stop inference, not silently manufacture flat observations.
        env.common_step_counter=5;env.scene.sensors['go2_height_scanner'].data.ray_hits_w[0,0,2]=float('inf')
        try:d.compute_go2_extreme_parkour_observations(env,'go2')
        except ValueError:pass
        else:raise AssertionError('Missing geometry accepted')
    result={'batches':[1,4,32],'checks':['SDK joint permutation','753-D slices/scales','idempotent same-step observation','yaw excluded from history','partial reset isolation','reject missing terrain'],'passed':True}
    Path(__file__).with_name('adapter_verification.json').write_text(json.dumps(result,indent=2));print(result)

if __name__=='__main__':main()
