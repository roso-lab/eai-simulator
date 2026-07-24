#!/usr/bin/env python3
"""Apply explicit Isaac/UsdPreview materials to the generated Unitree Z1 USD."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import NamedTuple


DEFAULT_ISAACSIM_ROOT = Path.home() / "isaacsim/_build/linux-x86_64/release"


class MaterialSpec(NamedTuple):
    diffuse_color: tuple[float, float, float]
    roughness: float = 0.55
    metallic: float = 0.0


VISUAL_MESHES = (
    "z1_Link00",
    "z1_Link01",
    "z1_Link02",
    "z1_Link03",
    "z1_Link04",
    "z1_Link05",
    "z1_Link06",
    "z1_GripperStator",
    "z1_GripperMover",
)

DEFAULT_MATERIALS = {
    "body_light": MaterialSpec((0.78, 0.80, 0.78), roughness=0.48),
    "body_mid": MaterialSpec((0.46, 0.48, 0.48), roughness=0.52),
    "graphite": MaterialSpec((0.08, 0.085, 0.09), roughness=0.62),
    "rubber": MaterialSpec((0.025, 0.026, 0.028), roughness=0.75),
}

MESH_MATERIALS = {
    "z1_Link00": "graphite",
    "z1_Link01": "body_light",
    "z1_Link02": "body_light",
    "z1_Link03": "body_light",
    "z1_Link04": "body_mid",
    "z1_Link05": "body_light",
    "z1_Link06": "graphite",
    "z1_GripperStator": "graphite",
    "z1_GripperMover": "rubber",
}

EXTRA_MESH_ROOTS = {
    "z1_GripperStator": ("z1_GripperStator_0",),
    "z1_GripperMover": ("z1_GripperMover_0",),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bind explicit materials to a generated Z1 USD.")
    parser.add_argument("--usd", required=True, help="Path to z1_description.usda.")
    parser.add_argument(
        "--output",
        help="Optional output USD path. Defaults to editing --usd in place.",
    )
    parser.add_argument(
        "--isaacsim-root",
        default=os.environ.get("ISAACSIM_ROOT", str(DEFAULT_ISAACSIM_ROOT)),
        help="Isaac Sim root used to locate omni.usd.libs when pxr is not already importable.",
    )
    return parser.parse_args()


def material_prim_name(material_name: str) -> str:
    return "Z1_" + "".join(part.capitalize() for part in material_name.split("_"))


def material_path(parent_path: str, material_name: str) -> str:
    return f"{parent_path}/Looks/{material_prim_name(material_name)}"


def mesh_root_names(mesh_name: str) -> tuple[str, ...]:
    return (mesh_name, *EXTRA_MESH_ROOTS.get(mesh_name, ()))


def discover_isaac_usd_libs(isaacsim_root: Path) -> Path | None:
    root = Path(isaacsim_root).expanduser()
    for extension_root in (root / "extscache", root / "exts"):
        if not extension_root.is_dir():
            continue
        for candidate in sorted(extension_root.glob("omni.usd.libs-*"), reverse=True):
            if (candidate / "pxr" / "Usd").is_dir() and (candidate / "bin" / "usd").is_dir():
                return candidate
    return None


def prepend_env_paths(name: str, *paths: Path) -> None:
    existing = [entry for entry in os.environ.get(name, "").split(os.pathsep) if entry]
    additions = [str(path) for path in paths if path]
    os.environ[name] = os.pathsep.join(additions + [entry for entry in existing if entry not in additions])


def configure_isaac_usd_libs(isaacsim_root: Path) -> bool:
    usd_libs = discover_isaac_usd_libs(isaacsim_root)
    if usd_libs is None:
        return False

    prepend_env_paths("PYTHONPATH", usd_libs)
    prepend_env_paths("LD_LIBRARY_PATH", usd_libs / "bin", usd_libs / "bin" / "usd")
    prepend_env_paths("PXR_PLUGINPATH_NAME", usd_libs / "bin" / "usd")
    if str(usd_libs) not in sys.path:
        sys.path.insert(0, str(usd_libs))
    return True


def ensure_pxr_importable(isaacsim_root: Path, allow_reexec: bool = False) -> None:
    try:
        from pxr import Usd  # noqa: F401
        return
    except ModuleNotFoundError as exc:
        if not configure_isaac_usd_libs(isaacsim_root):
            raise RuntimeError(f"Unable to locate omni.usd.libs under Isaac Sim root: {isaacsim_root}") from exc

    if allow_reexec and os.environ.get("Z1_USD_LIBS_BOOTSTRAPPED") != "1":
        os.environ["Z1_USD_LIBS_BOOTSTRAPPED"] = "1"
        os.execvpe(sys.executable, [sys.executable, *sys.argv], os.environ.copy())

    try:
        from pxr import Usd  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RuntimeError("pxr is still not importable after configuring Isaac USD libs") from exc


def create_preview_material(stage, parent_path: str, name: str, spec: MaterialSpec):
    from pxr import Gf, Sdf, UsdGeom, UsdShade

    UsdGeom.Scope.Define(stage, f"{parent_path}/Looks")
    material_prim_path = material_path(parent_path, name)
    material = UsdShade.Material.Define(stage, material_prim_path)
    shader = UsdShade.Shader.Define(stage, f"{material_prim_path}/PreviewSurface")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*spec.diffuse_color))
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(spec.roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(spec.metallic)

    output = material.CreateSurfaceOutput()
    try:
        output.ConnectToSource(shader.ConnectableAPI(), "surface")
    except TypeError:
        output.ConnectToSource(shader.GetOutput("surface"))
    return material


def iter_mesh_prims(stage, root_name: str):
    from pxr import Usd

    root = stage.GetPrimAtPath(f"/meshes/{root_name}")
    if not root.IsValid():
        return
    for prim in Usd.PrimRange(root):
        if prim.GetTypeName() == "Mesh":
            yield prim


def bind_material(prim, material) -> None:
    from pxr import UsdShade

    binding_api = UsdShade.MaterialBindingAPI.Apply(prim)
    binding_api.Bind(material)


def apply_materials(
    usd_path: Path,
    output_path: Path | None = None,
    isaacsim_root: Path = DEFAULT_ISAACSIM_ROOT,
    allow_reexec: bool = False,
) -> list[str]:
    ensure_pxr_importable(isaacsim_root, allow_reexec=allow_reexec)

    from pxr import Usd

    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError(f"Unable to open USD stage: {usd_path}")

    from pxr import UsdGeom

    UsdGeom.Xform.Define(stage, "/visuals/world")

    bound_paths: list[str] = []
    missing_roots: list[str] = []
    for mesh_name in VISUAL_MESHES:
        material_name = MESH_MATERIALS[mesh_name]
        material_spec = DEFAULT_MATERIALS[material_name]
        for root_name in mesh_root_names(mesh_name):
            root_path = f"/meshes/{root_name}"
            mesh_prims = list(iter_mesh_prims(stage, root_name))
            if not mesh_prims:
                missing_roots.append(root_name)
                continue
            material = create_preview_material(stage, root_path, material_name, material_spec)
            for prim in mesh_prims:
                bind_material(prim, material)
                bound_paths.append(str(prim.GetPath()))

    if missing_roots:
        raise RuntimeError("Missing mesh roots in USD: " + ", ".join(sorted(missing_roots)))

    if output_path is None or output_path == usd_path:
        stage.GetRootLayer().Save()
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        stage.GetRootLayer().Export(str(output_path))

    return bound_paths


def main() -> None:
    args = parse_args()
    usd_path = Path(args.usd).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve() if args.output else None
    isaacsim_root = Path(args.isaacsim_root).expanduser().resolve()
    if not usd_path.is_file():
        raise FileNotFoundError(f"USD not found: {usd_path}")

    bound_paths = apply_materials(
        usd_path,
        output_path,
        isaacsim_root=isaacsim_root,
        allow_reexec=True,
    )
    target_path = output_path or usd_path
    print(f"Applied Z1 materials to {len(bound_paths)} mesh prims: {target_path}")


if __name__ == "__main__":
    main()
