from __future__ import annotations

from typing import Callable, Tuple


def clear_hazard(stage, prim_path: str) -> None:
    if stage.GetPrimAtPath(prim_path):
        import omni.kit.commands

        omni.kit.commands.execute("DeletePrims", paths=[prim_path])


def create_hazard_marker(
    stage,
    prim_path: str,
    position: Tuple[float, float, float],
    *,
    app_update: Callable[[], None] | None = None,
) -> bool:
    import math
    import omni.kit.commands
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    _ = app_update

    if stage.GetPrimAtPath(prim_path):
        omni.kit.commands.execute("DeletePrims", paths=[prim_path])

    x_base, y_base, z_base = position
    parts = [
        (0.28, 0.5, 0.25, 0.15, (0.30, 0.03, 0.00), (0.45, 0.08, 0.01), 6),
        (0.24, 0.9, 0.75, 0.10, (0.55, 0.10, 0.01), (0.70, 0.25, 0.03), 5),
        (0.16, 1.1, 1.35, 0.05, (0.60, 0.30, 0.05), (0.90, 0.50, 0.08), 4),
    ]
    try:
        UsdGeom.Xform.Define(stage, prim_path)
        index = 0
        for radius, height, z_off, xy_dist, diffuse, emissive, count in parts:
            material = UsdShade.Material.Define(stage, f"{prim_path}/Material_{index}")
            shader = UsdShade.Shader.Define(stage, f"{prim_path}/Material_{index}/Shader")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*diffuse))
            shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*emissive))
            shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.1)
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            for i in range(count):
                angle = (2 * math.pi / count) * i
                cone = UsdGeom.Cone.Define(stage, f"{prim_path}/Flame_{index}")
                cone.CreateRadiusAttr(float(radius))
                cone.CreateHeightAttr(float(height))
                cone.CreateAxisAttr("Z")
                cone.AddTranslateOp().Set(
                    Gf.Vec3d(
                        x_base + xy_dist * math.cos(angle),
                        y_base + xy_dist * math.sin(angle),
                        z_base + z_off,
                    )
                )
                cone.AddRotateZOp().Set(math.degrees(angle))
                UsdShade.MaterialBindingAPI(cone.GetPrim()).Bind(material)
                index += 1
        return True
    except Exception as exc:
        print(f"[fire_rescue.fire] create_hazard_marker failed: {exc}")
        return False


def create_hazard_column(
    stage,
    prim_path: str,
    position: Tuple[float, float, float],
    app_update: Callable[[], None] | None = None,
) -> bool:
    return create_hazard_marker(
        stage,
        prim_path,
        position,
        app_update=app_update,
    )
