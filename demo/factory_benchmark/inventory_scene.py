"""Author an inventory-label scene variant without changing source robot physics."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from demo.benchmark.marker_detection import DICTIONARY_NAME, MARKER_SIDE_M
from demo.factory_benchmark.prim_bindings import FACTORY_PRIM_BINDINGS
from demo.factory_benchmark.label_geometry import fit_surface_label, surface_triangles, validate_label_support


def prepare_variant(marker_id, state):
    from EAI_assets.asset_resolver import usd_root

    directory = usd_root() / "scene/factory/benchmark/generated"
    directory.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="inventory_", dir=directory)) / "inventory.usda"
    completed = subprocess.run([sys.executable, "-m", "demo.factory_benchmark.inventory_scene",
                                "--marker-id", str(marker_id), "--state", state, "--output", str(output)],
                               text=True, capture_output=True, check=True, timeout=120)
    return json.loads(completed.stdout)


def configure_variant(env_cfg, *, path, sha256):
    source = Path(path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha256:
        raise ValueError("inventory scene variant changed before environment creation")
    env_cfg.scene.world.spawn.usd_path = str(source)


def author_variant(base_path, output_path, *, marker_id, state="visible"):
    import cv2
    import numpy as np
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

    if isinstance(marker_id, bool) or not isinstance(marker_id, int) or not 0 <= marker_id < 50:
        raise ValueError("inventory marker id must be an integer in [0, 49]")
    if state not in {"visible", "covered", "absent"}:
        raise ValueError("unsupported inventory label state")
    base_path, output_path = Path(base_path).resolve(), Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError(output_path)
    stage = Usd.Stage.Open(str(base_path))
    if stage.GetMetadata("metersPerUnit") != 1.0 or stage.GetMetadata("upAxis") != "Z":
        raise ValueError("inventory label authoring requires a metre/Z-up Factory stage")
    parent_path = FACTORY_PRIM_BINDINGS["inspection_target_box"]["prim_path"]
    parent = stage.GetPrimAtPath(parent_path)
    if not parent or parent.IsInstanceProxy() or parent.IsInstanceable():
        raise ValueError("inventory label requires a writable non-instance box binding")
    cell_side = MARKER_SIDE_M / 6
    outer_side = cell_side * 8
    support = fit_surface_label(surface_triangles(parent), outer_side)
    center = np.array(support["local_center_m"])
    normal = np.array(support["normal"])
    horizontal_axis, vertical_axis = np.array(support["horizontal"]), np.array(support["vertical"])
    transform = UsdGeom.XformCache().GetLocalToWorldTransform(parent)
    world_center = transform.Transform(Gf.Vec3d(*center))
    layer = Sdf.Layer.CreateNew(str(output_path))
    layer.subLayerPaths = [Path(os.path.relpath(base_path, output_path.parent)).as_posix()]
    overlay = Usd.Stage.Open(layer)
    UsdGeom.SetStageMetersPerUnit(overlay, 1.0)
    UsdGeom.SetStageUpAxis(overlay, UsdGeom.Tokens.z)
    overlay.SetDefaultPrim(overlay.GetPrimAtPath("/World"))
    label_path = parent_path + "/BenchmarkInventoryLabel"

    def mesh(name, cells, color, offset=0.0):
        points, indices = [], []
        for row, column in cells:
            left = (column - 4) * cell_side
            top = (4 - row) * cell_side
            vertices = [(left, top), (left, top - cell_side),
                        (left + cell_side, top - cell_side), (left + cell_side, top)]
            indices.extend(range(len(points), len(points) + 4))
            points.extend(tuple(center + offset * normal + horizontal * horizontal_axis + vertical * vertical_axis)
                          for horizontal, vertical in vertices)
        shape = UsdGeom.Mesh.Define(overlay, label_path + "/" + name)
        shape.CreatePointsAttr(points)
        shape.CreateFaceVertexCountsAttr([4] * len(cells))
        shape.CreateFaceVertexIndicesAttr(indices)
        shape.CreateSubdivisionSchemeAttr("none")
        shape.CreateDoubleSidedAttr(True)
        material = UsdShade.Material.Define(overlay, label_path + "/Materials/" + name)
        shader = UsdShade.Shader.Define(overlay, material.GetPath().AppendChild("Surface"))
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(color))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)
        shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(shape.GetPrim()).Bind(material)

    if state != "absent":
        UsdGeom.Xform.Define(overlay, label_path)
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        pattern = np.pad(cv2.aruco.generateImageMarker(dictionary, marker_id, 6), 1, constant_values=255)
        for value, name, color in ((0, "Ink", 0.01), (255, "Paper", 0.95)):
            mesh(name, list(zip(*np.nonzero(pattern == value))), color)
        if state == "covered":
            mesh("OpaqueCover", [(row, column) for row in range(8) for column in range(8)], 0.35, offset=0.001)
    layer.Save()
    manifest = {"schema": "eai.factory_inventory_label_variant", "version": 2,
            "path": str(output_path), "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "base_path": str(base_path), "base_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest(),
            "parent_prim_path": parent_path, "label_prim_path": label_path,
            "marker_id": marker_id, "dictionary": DICTIONARY_NAME, "marker_side_m": MARKER_SIDE_M,
            "state": state, "local_center_m": center.tolist(), "world_center_m": list(world_center),
            "surface_support": support,
            "ground_truth_role": "evaluator_only", "physical_model": "static_printed_surface_no_collision",
            "limitations": ["Synthetic inventory identity; no claim about unlabelled box contents",
                            "Printed decal and cover have no dynamics; existing mass/inertia/gravity are untouched"]}
    validate_label_support(stage, overlay, manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marker-id", type=int, required=True)
    parser.add_argument("--state", choices=["visible", "covered", "absent"], default="visible")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from EAI_assets.asset_resolver import usd_root

    print(json.dumps(author_variant(usd_root() / "scene/factory/factory.usd", args.output,
                                   marker_id=args.marker_id, state=args.state)))


if __name__ == "__main__":
    main()
