"""Reviewed, task-owned Factory goal props and their deterministic USD authoring."""

from pathlib import Path
from math import cos, pi, sin


ROOT = Path(__file__).resolve().parents[2]
FACTORY_GOAL_PROPS = {
    "production_anomaly_marker": {
        "asset_key": "factory_goal_anomaly_marker_v2",
        "path": ROOT / "usd/scene/factory/goal_props/anomaly_marker_v2.usda",
        "size_m": [0.15, 0.05, 0.15],
        "mass_kg": 0.1,
    },
    "maintenance_service_cart": {
        "asset_key": "factory_goal_service_cart_v2",
        "path": ROOT / "usd/scene/factory/goal_props/service_cart_v2.usda",
        "size_m": [0.8, 0.5, 0.8],
        "mass_kg": 18.0,
    },
}


def _box(stage, path, low, high, color):
    from pxr import Gf, UsdGeom

    x0, y0, z0 = low
    x1, y1, z1 = high
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr().Set([Gf.Vec3f(*point) for point in (
        (x0, y0, z0), (x0, y0, z1), (x0, y1, z0), (x0, y1, z1),
        (x1, y0, z0), (x1, y0, z1), (x1, y1, z0), (x1, y1, z1),
    )])
    mesh.CreateFaceVertexCountsAttr().Set([4] * 6)
    mesh.CreateFaceVertexIndicesAttr().Set([
        0, 2, 3, 1, 4, 5, 7, 6, 0, 1, 5, 4,
        2, 6, 7, 3, 0, 4, 6, 2, 1, 3, 7, 5,
    ])
    mesh.CreateSubdivisionSchemeAttr().Set("none")
    mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(*color)])


def _wheel(stage, path, x, y, z):
    from pxr import Gf, UsdGeom

    sides, radius, half_width = 12, .04, .02
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr().Set([
        Gf.Vec3f(x + radius * cos(2*pi*i/sides), y + offset,
                 z + radius * sin(2*pi*i/sides))
        for offset in (-half_width, half_width) for i in range(sides)])
    mesh.CreateFaceVertexCountsAttr().Set([sides, sides] + [4] * sides)
    mesh.CreateFaceVertexIndicesAttr().Set([
        *reversed(range(sides)), *range(sides, 2*sides),
        *(index for i in range(sides)
          for index in (i, (i+1) % sides, (i+1) % sides + sides, i+sides)),
    ])
    mesh.CreateSubdivisionSchemeAttr().Set("none")
    mesh.CreateDisplayColorAttr().Set([Gf.Vec3f(.07, .08, .08)])


def author_factory_goal_props(output_root=None):
    """Create the two reviewed assets once; never replace a maintained source."""
    from pxr import Usd, UsdGeom

    root = Path(output_root) if output_root is not None else next(
        iter(FACTORY_GOAL_PROPS.values()))["path"].parent
    if any((root / row["path"].name).exists() for row in FACTORY_GOAL_PROPS.values()):
        raise FileExistsError("Factory goal prop source already exists")
    root.mkdir(parents=True, exist_ok=True)

    marker = Usd.Stage.CreateNew(str(root / "anomaly_marker_v2.usda"))
    UsdGeom.SetStageMetersPerUnit(marker, 1.0)
    UsdGeom.SetStageUpAxis(marker, "Z")
    marker.SetDefaultPrim(UsdGeom.Xform.Define(marker, "/FactoryGoalAnomalyMarker").GetPrim())
    _box(marker, "/FactoryGoalAnomalyMarker/Plate",
         (-.075, -.019, -.075), (.075, .025, .075), (.78, .13, .10))
    _box(marker, "/FactoryGoalAnomalyMarker/WarningFace",
         (-.058, -.023, -.058), (.058, -.019, .058), (1.0, .79, .19))
    _box(marker, "/FactoryGoalAnomalyMarker/WarningStem",
         (-.008, -.025, -.039), (.008, -.023, .029), (.08, .08, .07))
    _box(marker, "/FactoryGoalAnomalyMarker/WarningDot",
         (-.008, -.025, .039), (.008, -.023, .052), (.08, .08, .07))
    marker.GetRootLayer().Save()

    cart = Usd.Stage.CreateNew(str(root / "service_cart_v2.usda"))
    UsdGeom.SetStageMetersPerUnit(cart, 1.0)
    UsdGeom.SetStageUpAxis(cart, "Z")
    cart.SetDefaultPrim(UsdGeom.Xform.Define(cart, "/FactoryGoalServiceCart").GetPrim())
    _box(cart, "/FactoryGoalServiceCart/Tray",
         (-.4, -.25, .24), (.4, .25, .38), (.19, .43, .48))
    _box(cart, "/FactoryGoalServiceCart/RecordPanel",
         (-.24, -.251, .31), (.24, -.248, .39), (.89, .83, .58))
    _box(cart, "/FactoryGoalServiceCart/LowerTray",
         (-.34, -.2, -.16), (.34, .2, -.11), (.16, .36, .39))
    for x_name, x0, x1 in (("L", -.39, -.34), ("R", .34, .39)):
        for y_name, y0, y1 in (("F", -.24, -.19), ("B", .19, .24)):
            _box(cart, f"/FactoryGoalServiceCart/Post_{x_name}{y_name}",
                 (x0, y0, -.35), (x1, y1, .25), (.13, .18, .19))
            _wheel(cart, f"/FactoryGoalServiceCart/Wheel_{x_name}{y_name}",
                   (x0+x1)/2, (y0+y1)/2, -.36)
    _box(cart, "/FactoryGoalServiceCart/Handle",
         (-.4, .20, .35), (.4, .25, .4), (.12, .18, .2))
    cart.GetRootLayer().Save()
    return [root / row["path"].name for row in FACTORY_GOAL_PROPS.values()]


if __name__ == "__main__":
    author_factory_goal_props()
