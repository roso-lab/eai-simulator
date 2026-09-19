#!/usr/bin/env python3
"""Gate 5 (visual, optional) of the usd-to-gazebo skill: headless Blender.

Renders the converted Gazebo scene (world.sdf + OBJ meshes, so run the
converter with --obj-out first) and optionally the original USD for
side-by-side comparison. Run inside Blender:

    blender --background --python blender_render_check.py -- --mode conv \
        --gazebo-dir usd/scene/factory_gazebo --obj-dir /tmp/objrender \
        --out-dir /tmp/render
    blender --background --python blender_render_check.py -- --mode usd \
        --usd usd/scene/factory/factory.usd --out-dir /tmp/render

Known Blender-side quirk: the OBJ importer's Y-up->Z-up conversion lives on
the imported object transform; this script discards it by overwriting
matrix_world (never `transform_apply` before that, or the rotation gets
baked into the vertices and the whole scene tilts 90 degrees).
"""

import argparse
import math
import os
import sys
import xml.etree.ElementTree as ET

import bpy
from mathutils import Matrix, Vector

parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("conv", "usd"), required=True)
parser.add_argument("--gazebo-dir", default=None)
parser.add_argument("--obj-dir", default=None, help="converter --obj-out directory (required for --mode conv)")
parser.add_argument("--usd", default=None, help="source USD path (required for --mode usd)")
parser.add_argument("--out-dir", default="/tmp/usd2gz_render")
# Blender passes "--" before our args
argv = sys.argv
args, _unknown = (parser.parse_known_args(argv[argv.index("--") + 1:]) if "--" in argv
                  else parser.parse_args([]))

os.makedirs(args.out_dir, exist_ok=True)


def parse_pose(txt):
    v = [float(x) for x in txt.split()]
    if len(v) != 6:
        raise ValueError(f"expected 6-value RPY pose, got: {txt!r}")
    x, y, z, roll, pitch, yaw = v
    # SDF RPY (extrinsic XYZ) -> matrix, then to Blender column convention via .to_4x4()
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    R = Matrix((
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp, cp * sr, cp * cr),
    ))
    return Vector((x, y, z)), R


def import_obj(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    if not meshes:
        return None
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active  # keep raw local coords


bpy.ops.wm.read_factory_settings(use_empty=True)
total = 0
if args.mode == "conv":
    world = ET.parse(f"{args.gazebo_dir}/world.sdf").getroot()
    includes = [i for i in world.iter("include") if i.find("name") is not None]
    for inc in includes:
        model = inc.find("uri").text.replace("model://", "")
        t, R = parse_pose(inc.find("pose").text)
        msdf = ET.parse(f"{args.gazebo_dir}/models/{model}/model.sdf")
        for vis in msdf.iter("visual"):
            vt, vR = parse_pose(vis.find("pose").text)
            mesh = vis.find("geometry/mesh")
            obj_rel = mesh.find("uri").text.replace(f"model://{model}/meshes/", "").replace(".dae", ".obj")
            scale = Vector((1, 1, 1))
            sc = mesh.find("scale")
            if sc is not None:
                scale = Vector([float(x) for x in sc.text.split()])
            opath = os.path.join(args.obj_dir, model, "meshes", obj_rel)
            if not os.path.isfile(opath):
                continue
            obj = import_obj(opath)
            if obj is None:
                continue
            obj.name = f"{inc.find('name').text}_{vis.get('name')}"
            obj.matrix_world = (Matrix.Translation(t) @ R.to_4x4()
                                @ Matrix.Translation(vt) @ vR.to_4x4()
                                @ Matrix.Diagonal(scale.to_4d()))
            total += 1
    print(f"CONV imported {total} visuals")
else:
    bpy.ops.wm.usd_import(filepath=args.usd)
    print(f"USD imported {len([o for o in bpy.data.objects if o.type == 'MESH'])} mesh objects")

bpy.ops.object.light_add(type="SUN", location=(10, -10, 25))
sun = bpy.context.object
sun.data.energy = 3.5
sun.rotation_euler = (math.radians(45), math.radians(15), math.radians(35))
w = bpy.data.worlds.new("w")
bpy.context.scene.world = w
w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.72, 0.78, 0.88, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 0.7

mins = Vector((1e9,) * 3)
maxs = Vector((-1e9,) * 3)
mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
for o in mesh_objs:
    for c in o.bound_box:
        wc = o.matrix_world @ Vector(c)
        mins = Vector(map(min, mins, wc))
        maxs = Vector(map(max, maxs, wc))
center = (mins + maxs) / 2
size = maxs - mins
print("BBOX min", tuple(round(v, 2) for v in mins), "max", tuple(round(v, 2) for v in maxs))

sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "TEXTURE"
sc.display.shading.show_shadows = False
sc.render.resolution_x = 1600
sc.render.resolution_y = 1200
sc.render.image_settings.file_format = "PNG"

cam_data = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_data)
sc.collection.objects.link(cam)
sc.camera = cam

d = max(size.x, size.y, size.z)
loc = center + Vector((d * 0.85, -d * 0.85, d * 0.6))
cam.location = loc
cam.rotation_euler = (center - loc).to_track_quat("-Z", "Y").to_euler()
cam_data.lens = 30
sc.render.filepath = os.path.join(args.out_dir, f"{args.mode}_overview.png")
bpy.ops.render.render(write_still=True)

cam_data.type = "ORTHO"
cam_data.ortho_scale = max(size.x, size.y) * 1.12
cam.location = (center.x, center.y, maxs.z + 30)
cam.rotation_euler = (0, 0, 0)
sc.render.filepath = os.path.join(args.out_dir, f"{args.mode}_top.png")
bpy.ops.render.render(write_still=True)
print("DONE", args.mode)
