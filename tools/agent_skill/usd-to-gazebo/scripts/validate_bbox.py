#!/usr/bin/env python3
"""Gate 3 of the usd-to-gazebo skill: vertex-level world-bbox comparison.

For every instance in the manifest, computes the USD point-cloud world AABB
(ground truth) and the SDF-side AABB (DAE points x visual pose/scale x
include pose parsed exactly as sdformat does: 6-value RPY), and reports
mismatches. A failure here means transforms were baked wrongly.

Usage:
    python validate_bbox.py --gazebo-dir usd/scene/factory_gazebo \
        --source usd/scene/factory --scene factory
"""

import argparse
import json
import math
import xml.etree.ElementTree as ET

import numpy as np
from pxr import Usd, UsdGeom

NS = "{http://www.collada.org/2005/11/COLLADASchema}"


def parse_pose(txt):
    """SDF pose -> (translation, column-convention rotation matrix).

    Accepts the 6-value RPY form (the only form gazebo11 parses correctly).
    7-value quaternions are rejected because sdformat9 would misparse them.
    """
    v = [float(x) for x in txt.split()]
    if len(v) != 6:
        raise ValueError(
            f"pose must have exactly 6 values (x y z r p y), got {len(v)}: {txt!r}"
        )
    x, y, z, roll, pitch, yaw = v
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    R = np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])
    return np.array([x, y, z]), R


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gazebo-dir", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--scene", required=True)
    args = ap.parse_args()
    ROOT = args.gazebo_dir

    stage = Usd.Stage.Open(f"{args.source}/{args.scene}.usd")
    bc = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    xc = UsdGeom.XformCache(Usd.TimeCode.Default())
    manifest = json.load(open(f"{ROOT}/conversion_manifest.json"))

    world = ET.parse(f"{ROOT}/world.sdf").getroot()
    named_incs = [i for i in world.iter("include") if i.find("name") is not None]
    inc_pose = {}
    for inc, e in zip(named_incs, manifest["instances"]):
        inc_pose[e["instance"]] = inc.find("pose").text

    _dae = {}
    def dae_points(model, name):
        key = (model, name)
        if key not in _dae:
            arr = ET.parse(f"{ROOT}/models/{model}/meshes/{name}.dae").find(f".//{NS}float_array")
            _dae[key] = np.fromstring(arr.text, sep=" ").reshape(-1, 3)
        return _dae[key]

    _model = {}
    def model_points(model):
        if model in _model:
            return _model[model]
        chunks = []
        for v in ET.parse(f"{ROOT}/models/{model}/model.sdf").iter("visual"):
            vt, vR = parse_pose(v.find("pose").text)
            mesh = v.find("geometry/mesh")
            mname = mesh.find("uri").text.split("/")[-1][:-4]
            sc = np.array([float(x) for x in mesh.find("scale").text.split()]) if mesh.find("scale") is not None else np.ones(3)
            pts = dae_points(model, mname)
            chunks.append(pts * sc @ vR.T + vt)
        _model[model] = np.vstack(chunks) if chunks else np.zeros((0, 3))
        return _model[model]

    _usd = {}
    def usd_points(inst):
        if inst in _usd:
            return _usd[inst]
        chunks = []
        p = stage.GetPrimAtPath(inst)
        for c in Usd.PrimRange(p, Usd.PrimDefaultPredicate):
            if c.GetTypeName() != "Mesh":
                continue
            pts = np.asarray(UsdGeom.Mesh(c).GetPointsAttr().Get())
            w = xc.GetLocalToWorldTransform(c)
            M = np.array([[w[i][j] for j in range(4)] for i in range(4)])
            chunks.append(np.hstack([pts, np.ones((len(pts), 1))]) @ M)
        _usd[inst] = np.vstack(chunks)[:, :3] if chunks else np.zeros((0, 3))
        return _usd[inst]

    bad = 0
    worst = 0.0
    for e in manifest["instances"]:
        inst = e["instance"]
        up = usd_points(inst)
        sp_local = model_points(e["model"])
        it, IR = parse_pose(inc_pose[inst])
        spw = sp_local @ IR.T + it
        umin, umax = up.min(axis=0), up.max(axis=0)
        smin, smax = spw.min(axis=0), spw.max(axis=0)
        dc = np.abs((umax + umin) / 2 - (smax + smin) / 2).max()
        ds = np.abs((umax - umin) - (smax - smin)).max()
        worst = max(worst, dc, ds)
        tol = 0.01 + 0.001 * max(1.0, float((umax - umin).max()))
        if dc > tol or ds > tol:
            bad += 1
            print(f"MISMATCH {inst} ({e['model']}): dCenter={dc:.4f} dSize={ds:.4f}")
            print(f"   usd [{np.round(umin,3)},{np.round(umax,3)}]")
            print(f"   sdf [{np.round(smin,3)},{np.round(smax,3)}]")
    print(f"checked {len(manifest['instances'])} instances (vertex-level), mismatches: {bad}, worst delta: {worst:.5f} m")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
