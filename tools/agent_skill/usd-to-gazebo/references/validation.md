# Validation methodology and ready-to-use snippets

Controlling principle: **validate through the real consumer, never through
your own parser.** A custom parser reproduces your own misunderstanding
perfectly. The gates are ordered cheapest-first; a failure at gate N
invalidates gates > N.

## Phase 1 analysis snippets

Stage facts:

```bash
python - <<'PY'
from pxr import Usd, UsdGeom
stage = Usd.Stage.Open("usd/scene/factory/factory.usd")
print("up axis:", UsdGeom.GetStageUpAxis(stage))
print("metersPerUnit:", UsdGeom.GetStageMetersPerUnit(stage))
PY
```

Top-level inventory (payload grouping key, scales, active state):

```bash
python - <<'PY'
import os
from collections import defaultdict
from pxr import Usd, UsdGeom

stage = Usd.Stage.Open("usd/scene/factory/factory.usd")
xc = UsdGeom.XformCache(Usd.TimeCode.Default())
world = stage.GetDefaultPrim()
for top in world.GetChildren():
    meshes = [p for p in Usd.PrimRange(top, Usd.PrimDefaultPredicate)
              if p.GetTypeName() == "Mesh"]
    if not meshes:
        continue
    key = None
    for spec in top.GetPrimStack():
        for item in (list(spec.payloadList.prependedItems)
                     + list(spec.payloadList.addedItems)
                     + list(spec.payloadList.explicitItems)):
            if item.assetPath:
                key = item.assetPath
                break
        if key:
            break
    w = xc.GetLocalToWorldTransform(top)
    scale = [w.ExtractRotationMatrix().GetRow(i).GetLength() for i in range(3)]
    print(f"{'/'.join(str(top.GetPath()).split('/')[1:]):40s}",
          "payload=", key, "scale=", [round(s, 4) for s in scale])
PY
```

Material system survey (MDL vs UsdPreviewSurface, albedo resolvability):

```bash
python - <<'PY'
from pxr import Usd, UsdShade, Sdf
stage = Usd.Stage.Open("usd/scene/factory/factory.usd")
for prim in stage.Traverse():
    if prim.GetTypeName() != "Material":
        continue
    for c in prim.GetChildren():
        if c.GetTypeName() != "Shader":
            continue
        sid = c.GetAttribute("info:id")
        mdl = c.GetAttribute("info:mdl:sourceAsset")
        if sid.HasValue() and str(sid.Get()) == "UsdPreviewSurface":
            print("preview:", prim.GetPath())
        elif mdl.HasValue():
            v = mdl.Get()
            print("mdl:", prim.GetPath(), "->", getattr(v, "resolvedPath", None) or str(v))
PY
```

## Gate 1 — SDF syntax

```bash
fail=0
for f in <out>/models/*/model.sdf <out>/world.sdf; do
  gz sdf -k "$f" >/dev/null 2>&1 || { echo "FAIL: $f"; fail=1; }
done
[ "$fail" = 0 ] && echo "all SDF valid"
```

This is necessary and *insufficient*: it does not validate pose component
counts or semantics.

## Gate 2 — Mesh loadability through gazebo's own loader

```bash
bash tools/agent_skill/usd-to-gazebo/scripts/build_native.sh
./tools/agent_skill/usd-to-gazebo/scripts/meshcheck <out>/models/*/meshes/*.dae
# exit code 0 required; compare reported bounds against the USD side for
# spot-check meshes (assimp may split UV-seam vertices, so compare bounds,
# not vertex counts).
```

## Gate 3 — Vertex-level world-bbox comparison

```bash
python tools/agent_skill/usd-to-gazebo/scripts/validate_bbox.py \
  --gazebo-dir <out> --source <src dir> --scene <stem>
# passes when every instance matches within 0.01 m + 0.1% of size
```

## Gate 4 — Physics pose readback (authoritative)

```bash
python tools/agent_skill/usd-to-gazebo/scripts/verify_poses.py --gazebo-dir <out>
# loads world with gzserver + probe plugin; compares every model WorldPose
# against the manifest: position <= 1 mm, orientation <= 0.2 deg
```

Acceptance thresholds used for the factory scene: bad=0,
worst_pos ≤ 0.00005 m, worst_angle ≤ 0.2 deg, spawned == includes.

## Gate 5 — Visual comparison (Blender, optional)

```bash
python tools/agent_skill/usd-to-gazebo/scripts/usd_to_gazebo.py ... --obj-out /tmp/objrender
blender --background --python tools/agent_skill/usd-to-gazebo/scripts/blender_render_check.py -- \
  --mode conv --gazebo-dir <out> --obj-dir /tmp/objrender --out-dir /tmp/render
blender --background --python tools/agent_skill/usd-to-gazebo/scripts/blender_render_check.py -- \
  --mode usd --usd <src>/<stem>.usd --out-dir /tmp/render
# compare /tmp/render/{conv,usd}_{overview,top}.png:
# geometry, layout and textures must be indistinguishable
```

Note both renders print the scene bbox; the two bboxes must match.

## Ground-truth rules

- The USD side ground truth is the *point cloud* (Mesh `points` attr x
  XformCache local-to-world), not `BBoxCache` -- authored `extent` hints
  can be padded and will mask real errors.
- Compare AABB center + size, not just corners of transformed bounding
  boxes (corner transforms overestimate rotated geometry).
