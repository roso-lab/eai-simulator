#!/usr/bin/env python3
"""Import the generated Unitree Z1 URDF into an Isaac Sim USD stage."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Unitree Z1 URDF to USD with Isaac Sim 5.1.")
    parser.add_argument("--urdf", required=True, help="Path to generated Z1 URDF.")
    parser.add_argument("--usd-dir", required=True, help="Directory where the USD package is written.")
    parser.add_argument("--robot-name", default="z1_description", help="Output package and USD basename.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args, _ = parser.parse_known_args()
    sys.argv = [sys.argv[0]]
    return args


def set_config_value(config: object, name: str, value: object) -> None:
    setter = getattr(config, f"set_{name}", None)
    if callable(setter):
        try:
            setter(value)
            return
        except Exception as exc:
            print(f"[WARN] import_config.set_{name}({value!r}) failed: {exc}")
    if hasattr(config, name):
        try:
            setattr(config, name, value)
        except Exception as exc:
            print(f"[WARN] import_config.{name} = {value!r} failed: {exc}")


def main() -> None:
    args = parse_args()
    urdf_path = Path(args.urdf).expanduser().resolve()
    usd_root = Path(args.usd_dir).expanduser().resolve()
    output_dir = usd_root / args.robot_name
    output_path = output_dir / f"{args.robot_name}.usda"

    if not urdf_path.is_file():
        raise FileNotFoundError(f"URDF not found: {urdf_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    from isaacsim import SimulationApp

    simulation_app = SimulationApp({"renderer": "RaytracedLighting", "headless": bool(args.headless)})

    import omni.kit.commands
    import omni.usd
    from isaacsim.core.utils import extensions

    extensions.enable_extension("isaacsim.asset.importer.urdf")
    simulation_app.update()

    status, import_config = omni.kit.commands.execute("URDFCreateImportConfig")
    if not status:
        simulation_app.close()
        raise RuntimeError("URDFCreateImportConfig failed")

    set_config_value(import_config, "merge_fixed_joints", False)
    set_config_value(import_config, "convex_decomp", False)
    set_config_value(import_config, "import_inertia_tensor", True)
    set_config_value(import_config, "fix_base", True)
    set_config_value(import_config, "self_collision", False)
    set_config_value(import_config, "density", 0.0)
    set_config_value(import_config, "distance_scale", 1.0)
    set_config_value(import_config, "default_drive_strength", 300.0)
    set_config_value(import_config, "default_position_drive_damping", 20.0)
    set_config_value(import_config, "make_default_prim", True)
    set_config_value(import_config, "default_drive_type", "position")

    os.environ["ROS_PACKAGE_PATH"] = str(urdf_path.parent) + os.pathsep + os.environ.get("ROS_PACKAGE_PATH", "")

    status, prim_path = omni.kit.commands.execute(
        "URDFParseAndImportFile",
        urdf_path=str(urdf_path),
        import_config=import_config,
        get_articulation_root=True,
    )
    if not status:
        simulation_app.close()
        raise RuntimeError(f"URDFParseAndImportFile failed for {urdf_path}")

    simulation_app.update()
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        simulation_app.close()
        raise RuntimeError("No USD stage was created by the URDF importer")

    prim_path_text = str(prim_path)
    if prim_path_text.startswith("/"):
        root_path = "/" + prim_path_text.strip("/").split("/")[0]
        root_prim = stage.GetPrimAtPath(root_path)
        if root_prim.IsValid():
            stage.SetDefaultPrim(root_prim)

    stage.GetRootLayer().Export(str(output_path))
    print(f"Imported articulation prim: {prim_path}")
    print(f"Generated USD: {output_path}")
    simulation_app.close()


if __name__ == "__main__":
    main()
