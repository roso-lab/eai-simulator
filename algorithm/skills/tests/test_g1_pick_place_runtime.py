"""USD-backed regression of fixed-base motion, independent of Isaac rendering."""
import json
import math
from pathlib import Path
import numpy as np
import pytest
pytest.importorskip('pxr')
from pxr import Gf, Usd, UsdGeom, UsdPhysics
from task.executors.g1_pick_place import (
    G1PickPlaceSkill, G1PickPlaceConfig, G1SkillError, ObjectBinding, ObstacleBox, GraspProfile,
)
from task.executors.g1_manipulation.geometry import world_matrix, set_world_matrix
from algorithm.skills.contracts import SkillRequest, SkillPlan, SkillCommand
from algorithm.skills.g1_pick_place import G1PickPlaceProvider

ROOT = Path(__file__).resolve().parents[3]
ASSET = ROOT / 'usd/robot/g1/g1_29dof_with_inspire_rev_1_0.usd'
PICK = {'bottle':[-.26,24.35,1.17058605], 'glass':[-.26,24.20,1.079]}
DEST = {'bottle':[.16,24.36,1.171294], 'glass':[.24,24.24,1.079]}

@pytest.fixture(scope='module')
def template():
    if not ASSET.exists():pytest.skip('local G1 Inspire asset not installed')
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage,'Z');UsdGeom.SetStageMetersPerUnit(stage,1.)
    root=UsdGeom.Xform.Define(stage,'/World/G1').GetPrim()
    root.GetReferences().AddReference(str(ASSET))
    for p in Usd.PrimRange(root):
        if p.IsInstance():p.SetInstanceable(False)
    for p in Usd.PrimRange(root):
        if p.HasAPI(UsdPhysics.RigidBodyAPI):p.RemoveAPI(UsdPhysics.RigidBodyAPI)
        if p.IsA(UsdPhysics.Joint):UsdPhysics.Joint(p).CreateJointEnabledAttr(False)
    m=np.eye(4);m[:3,:3]=[[0,1,0],[-1,0,0],[0,0,1]];m[:3,3]=[.22,24.6,.7923]
    set_world_matrix(root,m)
    for name,point in PICK.items():
        p=UsdGeom.Xform.Define(stage,'/World/'+name).GetPrim();m=np.eye(4);m[:3,3]=point;set_world_matrix(p,m)
        # Volume proxy: runtime/state test; actual calibrated mesh is checked in native replay.
        profile=GraspProfile.builtin('bottle_neck' if name=='bottle' else 'glass_stem')
        cube=UsdGeom.Cube.Define(stage,'/World/'+name+'/Volume');cube.CreateSizeAttr(1.)
        cube.AddScaleOp().Set(Gf.Vec3d(*profile.object_size_m))
    # Known relaxed arm pose; fingers use the asset's initial state.
    skill=G1PickPlaceSkill(stage,'/World/G1',{n:ObjectBinding('/World/'+n,'bottle_neck' if n=='bottle' else 'glass_stem') for n in PICK})
    q={'waist_yaw_joint':0.}
    for side,roll in [('right',-.16),('left',.16)]:
        q.update({side+'_shoulder_pitch_joint':.35,side+'_shoulder_roll_joint':roll,side+'_shoulder_yaw_joint':0.,side+'_elbow_joint':.87,side+'_wrist_roll_joint':0.,side+'_wrist_pitch_joint':0.,side+'_wrist_yaw_joint':0.})
    skill.robot.set_joint_positions(q)
    return stage.GetRootLayer().ExportToString()


def bind(template, yaw=0., shift=(0,0,0), obstacles=True, config=None):
    stage=Usd.Stage.CreateInMemory();stage.GetRootLayer().ImportFromString(template)
    c,s=math.cos(yaw),math.sin(yaw);delta=np.eye(4);delta[:3,:3]=[[c,-s,0],[s,c,0],[0,0,1]];delta[:3,3]=shift
    for path in ['/World/G1','/World/bottle','/World/glass']:
        prim=stage.GetPrimAtPath(path);set_world_matrix(prim,delta@world_matrix(prim))
    point=lambda p:(delta@np.r_[p,1])[:3]
    boxes=[ObstacleBox(tuple(point([-.5645,23.9345,.5])),(.4005,.9345,.5),yaw,'counter')] if obstacles else []
    sk=G1PickPlaceSkill(stage,'/World/G1',{n:ObjectBinding('/World/'+n,'bottle_neck' if n=='bottle' else 'glass_stem') for n in PICK},obstacles=boxes,config=config)
    return stage,sk,point


@pytest.mark.parametrize('yaw,shift,alternate',[(0.,(0,0,0),False),(.61,(2,-7,.2),True)])
def test_two_objects_world_frames_and_paused_hold(template,yaw,shift,alternate):
    stage,sk,point=bind(template,yaw,shift,obstacles=not alternate)
    for name in PICK:
        before=world_matrix(stage.GetPrimAtPath('/World/'+name))
        dest=np.array(DEST[name]);dest+=np.array([-.025,.015,.025]) if alternate else 0
        req=SkillRequest('run-'+name,'eai.manipulation.g1_pick_place','g1_1',parameters={'pick_position':point(PICK[name]).tolist(),'place_position':point(dest).tolist()})
        original=stage.GetRootLayer().ExportToString()
        previous_angles=dict(sk.robot.angles)
        sk.start_plan(G1PickPlaceProvider().plan(req))
        assert stage.GetRootLayer().ExportToString()==original
        paused=False;relative=None;seen=[]
        for _ in range(1800):
            r=sk.step(1/24);seen.append(r['phase'])
            peak=max(abs(sk.robot.angles[n]-previous_angles[n])*24 for n in sk.robot.angles)
            assert peak<=sk.config.joint_speed_rad_s+1e-6
            previous_angles=dict(sk.robot.angles)
            actual=world_matrix(stage.GetPrimAtPath('/World/'+name))
            if not r['events']:assert np.allclose(actual,before)
            if sk.attached:
                frame=sk.transfer._grip()
                if relative is None:relative=np.linalg.inv(frame)@actual
                assert np.allclose(np.linalg.inv(frame)@actual,relative,atol=1e-8)
                grip=(actual@np.r_[sk.transfer.object_point,1])[:3]
                assert np.linalg.norm(grip-frame[:3,3])<.006
                if not paused:
                    sk.stop();frozen=stage.GetRootLayer().ExportToString();sk.step(.05)
                    assert stage.GetRootLayer().ExportToString()==frozen
                    with pytest.raises(G1SkillError,match='holding_object'):sk.cancel()
                    sk.resume();paused=True
            if r['status']=='completed':break
        assert r['status']=='completed' and paused and not sk.attached
        assert all(p in seen for p in ('prepare','grasp','lift','transfer','release','withdraw','retract'))
        assert np.linalg.norm(actual[:3,3]-point(dest))<.003
        assert sk.transfer.min_clearance>=sk.config.clearance_m
        assert [e['event'] for e in r['events']]==['attached','released']
        assert r['physical_success'] is None


def test_preflight_rejections_do_not_mutate(template):
    stage,sk,point=bind(template,obstacles=False)
    original=stage.GetRootLayer().ExportToString();angles=dict(sk.robot.angles);last=sk.robot.last_receipt
    for pick,dest in [([True,2,3],DEST['bottle']),(PICK['bottle'],[1e20,0,0]),([0,0,0],DEST['bottle'])]:
        with pytest.raises((ValueError,G1SkillError)):sk.start(pick,dest)
        assert stage.GetRootLayer().ExportToString()==original and sk.robot.angles==angles and sk.robot.last_receipt is last
    sk.obstacles=(ObstacleBox((.22,24.6,1.),(2,2,2)),)
    with pytest.raises(G1SkillError,match='infeasible'):sk.start(PICK['bottle'],DEST['bottle'])
    assert stage.GetRootLayer().ExportToString()==original and sk.robot.angles==angles and sk.robot.last_receipt is last


def test_forged_plan_and_ambiguity_rejected(template):
    stage,sk,_=bind(template,obstacles=False)
    for payload in [{'pick_position':PICK['bottle'],'place_position':DEST['bottle'],'frame':'robot'}, {'pick_position':PICK['bottle'],'place_position':DEST['bottle'],'bogus':1}]:
        p=SkillPlan('x','eai.manipulation.g1_pick_place','g1_1',(SkillCommand('g1_pick_place',payload),))
        with pytest.raises(G1SkillError,match='invalid_plan'):sk.start_plan(p)
    set_world_matrix(stage.GetPrimAtPath('/World/glass'),world_matrix(stage.GetPrimAtPath('/World/bottle')))
    with pytest.raises(G1SkillError,match='exactly one'):sk.start(PICK['bottle'],DEST['bottle'])


def test_external_motion_and_live_physics_rejected(template):
    stage,sk,_=bind(template,obstacles=False)
    sk.start(PICK['bottle'],DEST['bottle'])
    m=world_matrix(stage.GetPrimAtPath('/World/G1'));m[0,3]+=.1;set_world_matrix(stage.GetPrimAtPath('/World/G1'),m)
    with pytest.raises(G1SkillError,match='state_changed'):sk.step(.05)
    assert sk.status=='holding'
    sk.cancel()
    body=stage.GetPrimAtPath('/World/bottle');UsdPhysics.RigidBodyAPI.Apply(body).CreateRigidBodyEnabledAttr(True)
    with pytest.raises(G1SkillError,match='dynamic rigid body'):sk.start(PICK['bottle'],DEST['bottle'])


def test_timeout_keeps_attachment(template):
    stage,sk,_=bind(template,obstacles=False,config=G1PickPlaceConfig(timeout_s=15.))
    sk.start(PICK['bottle'],DEST['bottle'])
    with pytest.raises(G1SkillError,match='timed out'):
        while True:sk.step(.05)
    assert sk.attached and sk.status=='holding'
    with pytest.raises(G1SkillError,match='holding_object'):sk.cancel()


def test_mismatched_calibration_rejected(template):
    stage,sk,_=bind(template,obstacles=False)
    cube=UsdGeom.Cube(stage.GetPrimAtPath('/World/bottle/Volume'))
    cube.GetSizeAttr().Set(2.)
    with pytest.raises(G1SkillError,match='object size differs'):sk.start(PICK['bottle'],DEST['bottle'])
    stage.RemovePrim('/World/bottle/Volume')
    with pytest.raises(G1SkillError,match='no visible geometry'):sk.start(PICK['bottle'],DEST['bottle'])
    stage,sk,_=bind(template,obstacles=False)
    p=GraspProfile.builtin('bottle_neck')
    bad=GraspProfile(p.name,p.hand_point,p.object_point,p.closed_joints,'0'*64,p.object_size_m)
    sk.objects['bottle']=ObjectBinding('/World/bottle',bad)
    with pytest.raises(G1SkillError,match='robot asset does not match'):sk.start(PICK['bottle'],DEST['bottle'])
