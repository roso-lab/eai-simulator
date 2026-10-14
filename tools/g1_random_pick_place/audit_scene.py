"""Independent post-execution mesh-vertex audit of demo support geometry.

Scene supports are known only to this auditor; never passed to the skill.
This is a visual geometry check, not continuous physical collision detection.
"""
from pathlib import Path
import json
import sys
import numpy as np
from pxr import Usd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.g1_random_pick_place.scene import RandomTrial, OBJECTS


def signed_cylinder(points, x, y, z, radius, height):
    delta = np.column_stack((np.linalg.norm(points[:, :2] - [x,y], axis=1) - radius,
                             np.abs(points[:, 2] - z) - height/2))
    return np.linalg.norm(np.maximum(delta,0.), axis=1) + np.minimum(np.max(delta,axis=1),0.)


def main():
    output = ROOT / 'artifacts/g1_random_pick_place'
    manifest = json.loads((output / 'audit.json').read_text())
    fps = manifest['fps']; rng = np.random.default_rng(manifest['seed']); results = []
    for index, specification in enumerate(manifest['trials']):
        stage = Usd.Stage.CreateInMemory()
        trial = RandomTrial(stage, index, rng, specification=specification)
        minimum = float('inf'); worst = None
        solids = []
        for endpoint in (specification['pick_position'], specification['place_position']):
            x, y, z = endpoint; top = z - OBJECTS[index][3]/2
            solids.extend(((x,y,(top-.035)/2,.036,top-.035), (x,y,top-.0175,.065,.035)))
        while trial.skill.status == 'running':
            trial.step(1/fps)
            fk = trial.skill.robot.forward()
            for path, vertices in trial.skill.transfer.clearance.parts:
                matrix = fk[path]
                points = vertices @ matrix[:3,:3].T + matrix[:3,3]
                for solid in solids:
                    distance = float(signed_cylinder(points, *solid).min())
                    if distance < minimum:
                        minimum = distance; worst = {'phase':trial.skill.phase, 'body':path.rsplit('/',1)[-1],
                                                     'seconds':trial.frames/fps}
        results.append({'object':trial.kind, 'minimum_arm_mesh_vertex_support_distance_m':minimum,
                        'worst_sample':worst, 'skill_obstacle_inputs':[]})
        print(json.dumps(results[-1]), flush=True)
    report = {'method':'arm/finger visual mesh vertices versus exact procedural support cylinders at 24 Hz',
              'continuous_collision_or_contact_force_verification':False, 'trials':results,
              'no_sampled_arm_vertex_penetration':all(r['minimum_arm_mesh_vertex_support_distance_m']>=0 for r in results)}
    (output / 'scene_clearance.json').write_text(json.dumps(report,indent=2)+'\n')
    assert report['no_sampled_arm_vertex_penetration'], report


if __name__ == '__main__': main()
