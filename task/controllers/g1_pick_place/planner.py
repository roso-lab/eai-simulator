"""Calibrated finger-pocket transfer with scene-independent Cartesian paths."""
import math
import numpy as np
from pxr import UsdGeom
from .geometry import ArmClearance


class TransferPlanner:
    """Preflight and execute one fixed-base, upright calibrated object transfer."""
    def __init__(self, robot, profile, config, obstacles):
        self.robot, self.profile, self.config = robot, profile, config
        self.pocket = np.array(profile.hand_point)
        self.object_point = np.array(profile.object_point)
        self.closed = dict(profile.closed_joints)
        self.phase = 'prepare'
        self.elapsed = 0.
        self._paths = None
        self.clearance = ArmClearance(robot, obstacles)
        self.min_clearance = math.inf
        self.names = ['waist_yaw_joint'] + robot.arm_joints('right', waist=False)
        self._next = {'prepare':'approach','approach':'grasp','lift':'transfer','transfer':'lower',
                      'lower':'release','withdraw':'return_ready','return_ready':'retract','retract':'done'}

    def prepare(self, pickup, place):
        saved = dict(self.robot.angles)
        saved_receipt = self.robot.last_receipt
        try:
            self._plan(pickup, place)
        finally:
            self.robot.angles = saved
            self.robot.last_receipt = saved_receipt

    def prepare_pick(self, pickup):
        """Plan only approach, closure and lift; no destination is required."""
        saved, receipt = dict(self.robot.angles), self.robot.last_receipt
        try:
            self._paths, self._targets = {}, {}
            root = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(self.robot.root)).T
            pickup = np.asarray(pickup) + self.object_point
            ready = (root @ np.r_[self.config.ready_position_base, 1])[:3]
            ready[2] = max(ready[2], pickup[2] + .04)
            raised = pickup + np.array([0., 0., self.config.lift_height_m])
            self.robot.angles.update(self.config.ik_seed)
            result = self.robot.solve_ik('right', ready, offset=self.pocket,
                target_axis=[0,0,-1], body_axis=2, names=self.names, iterations=200,
                multistart=True, tolerance=self.config.position_tolerance_m,
                orientation_tolerance_rad=.018, joint_bounds={'waist_yaw_joint':(-.35,.35)}, write=False)
            if not result['reached']:
                raise RuntimeError('Clearance-ready pose unreachable')
            self._paths['prepare'] = [saved, dict(self.robot.angles)]
            self._cartesian_paths(ready, [('approach', raised), ('grasp', pickup), ('lift', raised)])
            self._compile_paths(saved)
            self._next = {'prepare':'approach', 'approach':'grasp',
                          'grasp':'lift', 'lift':'holding'}
        finally:
            self.robot.angles, self.robot.last_receipt = saved, receipt

    def prepare_place(self, target_grip, *, home):
        """Start from the measured carry pose and finish with open fingers."""
        saved, receipt = dict(self.robot.angles), self.robot.last_receipt
        try:
            self._paths, self._targets = {}, {}
            target = np.asarray(target_grip)
            current = self._grip()[:3, 3]
            over = target.copy()
            over[2] = max(current[2], target[2] + self.config.lift_height_m)
            root = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(self.robot.root)).T
            ready = (root @ np.r_[self.config.ready_position_base, 1])[:3]
            ready[2] = max(ready[2], target[2] + .04)
            self._cartesian_paths(current, [('transfer', over), ('lower', target),
                                           ('withdraw', over), ('return_ready', ready)])
            self._paths['release'] = [self._paths['lower'][-1]] * 2
            self._paths['retract'] = [dict(self.robot.angles), dict(home)]
            self._compile_paths(saved, home=home, closing=False)
            self._next = {'transfer':'lower', 'lower':'release', 'release':'withdraw',
                          'withdraw':'return_ready',
                          'return_ready':'retract', 'retract':'done'}
            self.phase, self.elapsed = 'transfer', 0.
        finally:
            self.robot.angles, self.robot.last_receipt = saved, receipt

    def _cartesian_paths(self, previous, targets):
        previous = np.asarray(previous)
        for phase, target in targets:
            target = np.asarray(target)
            points = [dict(self.robot.angles)]
            count = max(2, int(np.ceil(np.linalg.norm(target-previous)/self.config.waypoint_spacing_m))+1)
            for u in np.linspace(0, 1, count)[1:]:
                points.append(self._solve(previous*(1-u) + target*u))
            self._paths[phase], self._targets[phase] = points, target
            previous = target.copy()

    def _advance(self,phase):
        self.phase=phase;self.elapsed=self._phase_time=0.

    def _grip(self,q=None):
        hand=self.robot.forward(q)[self.robot.body_paths[self.robot.hand_body('right')]]
        frame=hand.copy();frame[:3,3]=(hand@np.r_[self.pocket,1])[:3]
        return frame

    def _solve(self,point):
        r=self.robot.solve_ik('right',point,offset=self.pocket,target_axis=[0,0,-1.],body_axis=2,
            names=self.names,iterations=180,multistart=False,tolerance=self.config.position_tolerance_m,
            orientation_tolerance_rad=.018,joint_bounds={'waist_yaw_joint':(-.35,.35)},write=False)
        if not r['reached']:
            raise RuntimeError('Finger-pocket path unreachable '+str(point)+' '+str(r['residual_m']))
        return dict(self.robot.angles)

    def _plan(self,pickup,place):
        saved=dict(self.robot.angles);self._paths={};self._targets={}
        pickup=np.asarray(pickup)+self.object_point;place=np.asarray(place)+self.object_point
        root=np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(self.robot.root)).T
        ready=(root@np.r_[self.config.ready_position_base,1])[:3]
        ready[2]=max(ready[2],pickup[2]+.04)
        safe_z=max(pickup[2],place[2])+self.config.lift_height_m
        raised=pickup.copy();raised[2]=safe_z
        over_place=place.copy();over_place[2]=safe_z
        self.robot.angles.update(self.config.ik_seed)
        result=self.robot.solve_ik('right',ready,offset=self.pocket,target_axis=[0,0,-1],body_axis=2,
            names=self.names,iterations=200,multistart=True,tolerance=self.config.position_tolerance_m,
            orientation_tolerance_rad=.018,joint_bounds={'waist_yaw_joint':(-.35,.35)},write=False)
        if not result['reached']:raise RuntimeError('Clearance-ready pose unreachable')
        ready_q=dict(self.robot.angles)
        self._paths['prepare']=[saved,ready_q]
        prev=ready.copy()
        for phase,target in [('approach',raised),('grasp',pickup),('lift',raised),('transfer',over_place),('lower',place),('withdraw',over_place),('return_ready',ready)]:
            start=dict(self.robot.angles);points=[start]
            for u in np.linspace(0,1,max(2,int(np.ceil(np.linalg.norm(target-prev)/self.config.waypoint_spacing_m))+1))[1:]:
                points.append(self._solve(prev*(1-u)+target*u))
            self._paths[phase]=points;self._targets[phase]=target
            prev=target.copy()
        self._paths['release']=[self._paths['lower'][-1]]*2
        self._paths['retract']=[dict(self.robot.angles),saved]
        self._compile_paths(saved)
        self.robot.angles=dict(saved)

    def _compile_paths(self, saved, *, home=None, closing=True):
        self._saved, self._home = dict(saved), dict(saved if home is None else home)
        self._durations={}
        self._arc={}
        self._close_duration=max(.7, 1.5*max(abs(v) for v in self.closed.values())/self.config.joint_speed_rad_s)
        for phase,qs in self._paths.items():
            lengths=[max(abs(b[n]-a[n]) for n in self.names) for a,b in zip(qs,qs[1:])]
            travel=sum(lengths)
            self._arc[phase]=np.r_[0.,np.cumsum(lengths)]
            rest = self._home if phase == 'retract' else saved
            finger_travel=max(abs(rest[n]) for n in self.closed) if phase in ('prepare','retract') else 0.
            self._durations[phase]=max(.7,1.875*max(travel,finger_travel)/self.config.joint_speed_rad_s)
            for a,b in zip(qs,qs[1:]):
                for u in np.linspace(0,1,max(8,int(np.ceil(max(abs(b[n]-a[n]) for n in self.names)/.025))+1)):
                    q={n:a[n]*(1-u)+b[n]*u for n in a}
                    q.update(self._fingers(phase, u, rest))
                    margin,bad=self.clearance.measure(q)
                    self.min_clearance=min(self.min_clearance,margin)
                    if margin<self.config.clearance_m:raise RuntimeError('Arm path obstructed '+phase+' '+str(bad)+' clearance '+str(margin))
        # Closing fingers is a separate stationary sweep after descent.
        for u in (np.linspace(0, 1, 25) if closing else ()):
            q = dict(self._paths['grasp'][-1])
            q.update({n: v*u for n,v in self.closed.items()})
            margin, bad = self.clearance.measure(q)
            self.min_clearance = min(self.min_clearance, margin)
            if margin < self.config.clearance_m:
                raise RuntimeError('Finger closure obstructed '+str(bad))
        if 'release' in self._paths:
            self._durations['release']=max(.7,2.8125*max(abs(v) for v in self.closed.values())/self.config.joint_speed_rad_s)

    def _fingers(self, phase, u, saved):
        if phase in ('lift', 'transfer', 'lower'):
            return self.closed
        if phase == 'release':
            return {n:v*(1-u)**2*(1+2*u) for n,v in self.closed.items()}
        if phase == 'prepare':
            return {n:saved[n]*(1-u) for n in self.closed}
        if phase == 'retract':
            return {n:saved[n]*u for n in self.closed}
        return {n:0. for n in self.closed}

    def step(self,dt,pickup_world,place_world):
        if not math.isfinite(dt) or dt<=0:raise ValueError('dt must be finite and positive')
        if self._paths is None:self.prepare(pickup_world,place_world)
        if self.phase in ('done', 'holding'):
            return {**self.robot.last_receipt, 'phase': self.phase,
                    'next_phase': self.phase, 'action_complete': True}
        phase=self.phase;self.elapsed+=dt;duration=self._durations[phase];self._duration=duration
        motion=min(self.elapsed/duration,1.);u=motion**3*(10+motion*(-15+6*motion))
        qs=self._paths[phase];arc=self._arc[phase];distance=u*arc[-1]
        i=min(int(np.searchsorted(arc,distance,side='right'))-1,len(qs)-2)
        i=max(0,i);a=(distance-arc[i])/(arc[i+1]-arc[i]) if arc[i+1]>arc[i] else u
        command={n:qs[i][n]*(1-a)+qs[i+1][n]*a for n in self.names}
        closed=1. if phase in ('lift','transfer','lower') else 0.
        if phase=='grasp':closed=float(np.clip((self.elapsed-duration)/self._close_duration,0,1))
        elif phase=='release':closed=1-u
        closure=closed*closed*(3-2*closed)
        command.update({n:v*closure for n,v in self.closed.items()})
        if phase in ('prepare', 'retract'):
            command.update(self._fingers(phase, u, self._home if phase == 'retract' else self._saved))
        self.robot.set_joint_positions(command)
        grip=self._grip();axis=float(math.acos(np.clip(-grip[2,2],-1,1)))
        target=self._targets.get(phase,grip[:3,3]);residual=float(np.linalg.norm(grip[:3,3]-target))
        # Finger closure and release are timed action phases.  Their completion
        # never depends on a payload observation or attachment callback.
        phase_done = self.elapsed >= (duration + self._close_duration if phase == 'grasp' else duration)
        if phase_done and phase in self._next:
            self._advance(self._next[phase])
        body=self.robot.hand_body('right');hand=self.robot.body_world(body)
        receipt={'phase':phase,'next_phase':self.phase,'action_complete':phase_done,
            'blocked':False,'palm_world_transform':hand.tolist(),'grip_world_transform':grip.tolist(),
            'hand_world':grip[:3,3].tolist(),'hand_rotation_world':grip[:3,:3].tolist(),
            'hand_body':body,'grip_calibration_frame':'calibrated_finger_pocket',
            'grip_offset_hand_m':self.pocket.tolist(),'grip_point_object_m':self.object_point.tolist(),
            'finger_closure_fraction':closure,'residual_m':residual,'orientation_residual_rad':axis,
            'joint_angles_rad':dict(self.robot.angles),'min_obstacle_clearance_m':self.min_clearance if math.isfinite(self.min_clearance) else None,
            'kinematic_only':True}
        self.robot.last_receipt=receipt
        return receipt
