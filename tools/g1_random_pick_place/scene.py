"""Procedural scene and seeded endpoint-only trials of the existing G1 skill."""
from pathlib import Path
import json
import math
import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade
from task.executors.g1_pick_place import G1PickPlaceSkill, GraspProfile, ObjectBinding, G1SkillError
from task.executors.g1_manipulation.geometry import world_matrix, set_world_matrix, object_local_size

ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / 'usd/robot/g1/g1_29dof_with_inspire_rev_1_0.usd'
OBJECTS = (
    ('rod', '橙色细杆', (.94, .32, .06), .24),
    ('dumbbell', '蓝色小哑铃', (.04, .62, .80), .28),
    ('spool', '紫色线轴', (.58, .18, .78), .24),
)


def pose(prim, position, yaw=0.):
    c, s = math.cos(yaw), math.sin(yaw)
    matrix = np.eye(4)
    matrix[:3, :3] = ((c, -s, 0), (s, c, 0), (0, 0, 1))
    matrix[:3, 3] = position
    set_world_matrix(prim, matrix)
    return matrix


def material(stage, path, color, metallic=0.):
    mat = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path + '/Surface')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.34)
    shader.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(metallic)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
    return mat


def cylinder(stage, path, radius, height, z, mat):
    mesh = UsdGeom.Cylinder.Define(stage, path)
    mesh.CreateRadiusAttr(radius); mesh.CreateHeightAttr(height); mesh.CreateAxisAttr('Z')
    UsdGeom.Xformable(mesh).AddTranslateOp().Set(Gf.Vec3d(0, 0, z))
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)
    return mesh.GetPrim()


def reference_robot(stage, path, position, yaw):
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    prim.GetReferences().AddReference(str(ASSET))
    for child in Usd.PrimRange(prim):
        if child.IsInstance(): child.SetInstanceable(False)
    for child in Usd.PrimRange(prim):
        for api in (UsdPhysics.RigidBodyAPI, UsdPhysics.CollisionAPI, UsdPhysics.ArticulationRootAPI):
            if child.HasAPI(api): child.RemoveAPI(api)
        if child.IsA(UsdPhysics.Joint):
            UsdPhysics.Joint(child).CreateJointEnabledAttr(False)
    pose(prim, position, yaw)
    return prim


def idle(robot):
    joints = {'waist_yaw_joint': 0.}
    for side, sign in (('right', -1), ('left', 1)):
        for joint, value in (('shoulder_pitch', .35), ('shoulder_roll', sign * .16),
                             ('shoulder_yaw', 0.), ('elbow', .87),
                             ('wrist_roll', 0.), ('wrist_pitch', 0.), ('wrist_yaw', 0.)):
            joints[f'{side}_{joint}_joint'] = value
    robot.set_joint_positions(joints)


class RandomTrial:
    def __init__(self, stage, index, rng, *, specification=None):
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1.)
        self.stage, self.index = stage, index
        self.kind, self.label, color, height = OBJECTS[index]
        self.path = f'/World/RandomDemo/Trial{index}'
        UsdGeom.Xform.Define(stage, self.path)
        root_position = [1.3 + index * 5., -.8, .7923]
        yaw = float(rng.uniform(-.8, .8)) if specification is None else specification['root_yaw_rad']
        self.root_matrix = pose(reference_robot(stage, self.path + '/G1', root_position, yaw), root_position, yaw)
        mat = material(stage, self.path + '/ObjectMaterial', color, .18)
        obj_path = self.path + '/Object'
        obj = UsdGeom.Xform.Define(stage, obj_path).GetPrim()
        cylinder(stage, obj_path + '/GripShaft', .012, height if index == 0 else height - .04, 0., mat)
        if index:
            radius, thickness = (.037, .04) if index == 1 else (.035, .03)
            for side in (-1, 1):
                cylinder(stage, obj_path + ('/Bottom' if side < 0 else '/Top'), radius,
                         thickness, side * (height - thickness) / 2., mat)
        template = GraspProfile.builtin('bottle_neck')
        profile = GraspProfile(self.kind + '_12mm_shaft', template.hand_point, (0., 0., 0.),
                               template.closed_joints, template.robot_asset_sha256, tuple(object_local_size(obj)))
        self.skill = G1PickPlaceSkill(stage, self.path + '/G1', {self.kind: ObjectBinding(obj_path, profile)})
        idle(self.skill.robot)
        rejections = []
        for attempt in range(30):
            if specification is None:
                # Declared reachable sampling box in G1 root coordinates, not global arbitrary reach.
                pick_local = [float(rng.uniform(.24, .32)), float(rng.uniform(-.50, -.41)), float(rng.uniform(.43, .48))]
                place_local = [float(rng.uniform(.24, .34)), float(rng.uniform(-.10, -.02)), float(rng.uniform(.43, .48))]
                pick = (self.root_matrix @ np.r_[pick_local, 1])[:3]
                place = (self.root_matrix @ np.r_[place_local, 1])[:3]
            else:
                pick, place = np.array(specification['pick_position']), np.array(specification['place_position'])
            pose(obj, pick)
            try:
                # Exactly the two public coordinate inputs, no obstacle/support locations.
                self.skill.start(pick.tolist(), place.tolist())
                break
            except G1SkillError as error:
                if error.code != 'infeasible' or specification is not None: raise
                rejections.append({'pick_position': pick.tolist(), 'place_position': place.tolist(), 'reason': str(error)})
        else: raise RuntimeError('30 random samples were infeasible')
        self.specification = {'object': self.kind, 'label': self.label, 'root_position': root_position,
            'root_yaw_rad': yaw, 'pick_position': pick.tolist(), 'place_position': place.tolist(),
            'object_size_m': list(profile.object_size_m), 'shaft_radius_m': .012,
            'sample_rejections': rejections if specification is None else specification['sample_rejections'],
            'skill_obstacle_inputs': []}
        # Supports are authored by the demo host AFTER skill planning. Their coordinates
        # never enter the planner. Separate narrow stands leave the lifting route open.
        stand_mat = material(stage, self.path + '/StandMaterial', (.20, .25, .30), .35)
        for name, endpoint in (('PickStand', pick), ('PlaceStand', place)):
            stand = UsdGeom.Xform.Define(stage, self.path + '/' + name).GetPrim()
            top = float(endpoint[2] - height / 2.)
            pose(stand, [endpoint[0], endpoint[1], 0.])
            cylinder(stage, str(stand.GetPath()) + '/Column', .036, top - .035, (top - .035) / 2., stand_mat)
            cylinder(stage, str(stand.GetPath()) + '/Top', .065, .035, top - .0175, stand_mat)
            marker = material(stage, self.path + '/' + name + 'Marker', color if name == 'PickStand' else (.10, .80, .36))
            cylinder(stage, str(stand.GetPath()) + '/Marker', .051, .001, top + .0005, marker)
        self.previous = dict(self.skill.robot.angles)
        self.peak_speed = self.grip_error = 0.
        self.events = []
        self.last_phase = None
        self.frames = 0

    def step(self, dt):
        result = self.skill.step(dt)
        self.frames += 1
        q = self.skill.robot.angles
        self.peak_speed = max(self.peak_speed, max(abs(q[n] - self.previous[n]) / dt for n in q))
        self.previous = dict(q)
        if self.skill.attached:
            obj = world_matrix(self.stage.GetPrimAtPath(self.skill.object_path))
            point = (obj @ np.r_[self.skill.transfer.object_point, 1])[:3]
            self.grip_error = max(self.grip_error, float(np.linalg.norm(point - self.skill.transfer._grip()[:3, 3])))
        if self.skill.phase != self.last_phase:
            self.events.append({'seconds': self.frames * dt, 'phase': self.skill.phase})
            self.last_phase = self.skill.phase
        return result

    def report(self):
        position = world_matrix(self.stage.GetPrimAtPath(self.skill.object_path))[:3, 3]
        result = {**self.specification, 'status': self.skill.status, 'events': self.events,
            'attachment_events': self.skill.events, 'max_grip_error_m': self.grip_error,
            'place_error_m': float(np.linalg.norm(position - self.skill.place)),
            'final_position': position.tolist(), 'peak_joint_speed_rad_s': self.peak_speed}
        if self.skill.status == 'completed':
            assert result['place_error_m'] < .003
            assert self.grip_error < .006
            assert self.peak_speed <= self.skill.config.joint_speed_rad_s + 1e-6
            assert [e['event'] for e in self.skill.events] == ['attached', 'released']
        return result


def audit(output, seed=20261014, fps=24):
    output.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    results = []
    for index in range(3):
        stage = Usd.Stage.CreateInMemory()
        trial = RandomTrial(stage, index, rng)
        while trial.skill.status == 'running': trial.step(1 / fps)
        results.append(trial.report())
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    report = {'seed': seed, 'fps': fps, 'completed': all(r['status'] == 'completed' for r in results),
        'mode': 'kinematic USD motion with ideal rigid attachment', 'physical_grasp_verified': False,
        'sampling_workspace_base_m': {'pick': [[.24,.32],[-.50,-.41],[.43,.48]],
                                      'place': [[.24,.34],[-.10,-.02],[.43,.48]]},
        'unknown_obstacle_avoidance_claimed': False, 'trials': results}
    (output / 'audit.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/g1_random_pick_place')
    parser.add_argument('--seed', type=int, default=20261014)
    args = parser.parse_args()
    audit(args.output, args.seed)
