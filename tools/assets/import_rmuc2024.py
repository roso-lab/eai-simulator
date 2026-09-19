#!/usr/bin/env python3
"""Import and validate the Gazebo RMUC2024 arena for EAI Simulator."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from PIL import Image


SOURCE_MESH_SHA256 = "29f45dbc52668b256e270971c20ad61acd0de4bb3ec770874b274dce9eaf1fd6"
SOURCE_MAP_SHA256 = "5ce8a6ac3cc8c2b835ffd0c3c85d959ac36ad0998d56fb9a21a48aa1aeaf2ed7"
MODEL_POSE = (0.030715, 0.004529, 0.0)
MODEL_SCALE = (0.001, 0.001, 0.001)
MAP_RESOLUTION = 0.05
MAP_ORIGIN = (-6.0, -7.75, 0.0)
WRAPPER_TEXT = '''#usda 1.0
(
    customLayerData = {
        string source = "gazebo-sim/gazebo_sim/world/RMUC2024.world"
        string sourceMeshSha256 = "29f45dbc52668b256e270971c20ad61acd0de4bb3ec770874b274dce9eaf1fd6"
    }
    defaultPrim = "RMUC2024"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "RMUC2024" (
    kind = "assembly"
)
{
    def Xform "Arena" (
        prepend references = @./rmuc2024_mesh.usd@
    )
    {
        double3 xformOp:translate = (0.030715, 0.004529, 0)
        double3 xformOp:scale = (0.001, 0.001, 0.001)
        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
    }

    def Cube "Ground" (
        prepend apiSchemas = ["PhysicsCollisionAPI"]
    )
    {
        uniform bool physics:collisionEnabled = 1
        double size = 2
        color3f[] primvars:displayColor = [(0.32, 0.34, 0.36)]
        double3 xformOp:translate = (0, 0, -0.05)
        double3 xformOp:scale = (50, 50, 0.05)
        uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
    }
}
'''
MAP_YAML = {
    "image": "rmuc2024_map.png",
    "resolution": MAP_RESOLUTION,
    "origin": list(MAP_ORIGIN),
    "negate": 0,
    "occupied_thresh": 0.65,
    "free_thresh": 0.196,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_paths(source_repo: Path) -> dict[str, Path]:
    package = source_repo / "gazebo_sim"
    return {
        "world": package / "world" / "RMUC2024.world",
        "mesh": package / "meshes" / "RMUCs_2024_s5.STL",
        "map": package / "map" / "rmuc2024" / "rmuc2024.pgm",
        "map_yaml": package / "map" / "rmuc2024" / "rmuc2024.yaml",
        "license": package / "LICENSE",
    }


def _validate_source(paths: dict[str, Path]) -> None:
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Gazebo RMUC2024 source files: " + ", ".join(missing))
    if _sha256(paths["mesh"]) != SOURCE_MESH_SHA256:
        raise ValueError("RMUC2024 STL hash does not match the certified source")
    if _sha256(paths["map"]) != SOURCE_MAP_SHA256:
        raise ValueError("RMUC2024 PGM hash does not match the certified source")

    model = ET.parse(paths["world"]).getroot().find("./world/model[@name='rmus_map_2']")
    if model is None:
        raise ValueError("RMUC2024.world has no rmus_map_2 model")
    pose = tuple(float(value) for value in (model.findtext("pose") or "").split()[:3])
    mesh = model.find("./link/visual/geometry/mesh")
    collision_mesh = model.find("./link/collision/geometry/mesh")
    if pose != MODEL_POSE or mesh is None or collision_mesh is None:
        raise ValueError("RMUC2024.world model pose or mesh contract changed")
    for node in (mesh, collision_mesh):
        scale = tuple(float(value) for value in (node.findtext("scale") or "").split())
        if node.findtext("uri") != "../meshes/RMUCs_2024_s5.STL" or scale != MODEL_SCALE:
            raise ValueError("RMUC2024.world mesh URI or scale changed")

    source_map = yaml.safe_load(paths["map_yaml"].read_text(encoding="utf-8"))
    expected = {
        "resolution": MAP_RESOLUTION,
        "origin": list(MAP_ORIGIN),
        "negate": 0,
        "occupied_thresh": 0.65,
        "free_thresh": 0.196,
    }
    for key, value in expected.items():
        if source_map.get(key) != value:
            raise ValueError(f"RMUC2024 map metadata changed: {key}")


def _find_converter(isaaclab_root: Path | None) -> Path:
    if isaaclab_root is None:
        import isaaclab

        isaaclab_root = Path(isaaclab.__file__).resolve().parents[3]
    converter = isaaclab_root / "scripts" / "tools" / "convert_mesh.py"
    if not converter.is_file():
        raise FileNotFoundError(f"Isaac Lab mesh converter not found: {converter}")
    return converter


def _git_value(source_repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _metadata(source_repo: Path, mesh_path: Path, map_path: Path) -> dict[str, object]:
    return {
        "license": "Apache-2.0",
        "source_repository": _git_value(source_repo, "remote", "get-url", "origin"),
        "source_checkout": str(source_repo.resolve()),
        "source_commit": _git_value(source_repo, "rev-parse", "HEAD"),
        "source_world": "gazebo_sim/world/RMUC2024.world",
        "source_mesh": "gazebo_sim/meshes/RMUCs_2024_s5.STL",
        "source_mesh_sha256": SOURCE_MESH_SHA256,
        "source_map": "gazebo_sim/map/rmuc2024/rmuc2024.pgm",
        "source_map_sha256": SOURCE_MAP_SHA256,
        "world_mesh_pose_xyz_m": list(MODEL_POSE),
        "world_mesh_scale": list(MODEL_SCALE),
        "map_resolution_m": MAP_RESOLUTION,
        "map_origin": list(MAP_ORIGIN),
        "generated_mesh": mesh_path.name,
        "generated_mesh_sha256": _sha256(mesh_path),
        "generated_map_sha256": _sha256(map_path),
    }


def install(source_repo: Path, repo_root: Path, isaaclab_root: Path | None) -> None:
    paths = _source_paths(source_repo)
    _validate_source(paths)
    converter = _find_converter(isaaclab_root)
    target = repo_root / "usd" / "scene" / "rmuc2024"
    thumbnail = repo_root / "usd" / "picture" / "scene" / "rmuc2024.png"
    target.mkdir(parents=True, exist_ok=True)
    thumbnail.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="eai-rmuc2024-") as temp_dir:
        converted = Path(temp_dir) / "rmuc2024_mesh.usd"
        subprocess.run(
            [
                sys.executable,
                str(converter),
                str(paths["mesh"]),
                str(converted),
                "--collision-approximation",
                "triangleMesh",
                "--headless",
            ],
            check=True,
        )
        shutil.copy2(converted, target / converted.name)

    source_image = Image.open(paths["map"])
    map_target = target / "rmuc2024_map.png"
    source_image.save(map_target, format="PNG", optimize=True)
    source_image.resize((904, 461), Image.Resampling.NEAREST).save(
        thumbnail, format="PNG", optimize=True
    )
    (target / "rmuc2024.usda").write_text(WRAPPER_TEXT, encoding="utf-8")
    (target / "rmuc2024_map.yaml").write_text(
        yaml.safe_dump(MAP_YAML, sort_keys=False), encoding="utf-8"
    )
    shutil.copy2(paths["license"], target / "LICENSE.Apache-2.0.txt")
    metadata = _metadata(source_repo, target / "rmuc2024_mesh.usd", map_target)
    (target / "source.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    check(repo_root, source_repo=source_repo)


def check(repo_root: Path, *, source_repo: Path | None = None) -> None:
    target = repo_root / "usd" / "scene" / "rmuc2024"
    required = {
        "wrapper": target / "rmuc2024.usda",
        "mesh": target / "rmuc2024_mesh.usd",
        "map_yaml": target / "rmuc2024_map.yaml",
        "map": target / "rmuc2024_map.png",
        "metadata": target / "source.json",
        "license": target / "LICENSE.Apache-2.0.txt",
        "thumbnail": repo_root / "usd" / "picture" / "scene" / "rmuc2024.png",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Incomplete RMUC2024 asset installation: " + ", ".join(missing))
    metadata = json.loads(required["metadata"].read_text(encoding="utf-8"))
    map_yaml = yaml.safe_load(required["map_yaml"].read_text(encoding="utf-8"))
    if required["wrapper"].read_text(encoding="utf-8") != WRAPPER_TEXT:
        raise ValueError("RMUC2024 wrapper USD differs from the importer contract")
    if map_yaml != MAP_YAML:
        raise ValueError("RMUC2024 installed map YAML differs from the source contract")
    if _sha256(required["mesh"]) != metadata["generated_mesh_sha256"]:
        raise ValueError("RMUC2024 generated USD hash does not match source.json")
    if _sha256(required["map"]) != metadata["generated_map_sha256"]:
        raise ValueError("RMUC2024 generated map hash does not match source.json")
    if Image.open(required["map"]).size != (603, 307):
        raise ValueError("RMUC2024 installed map dimensions are not 603x307")
    if source_repo is not None:
        paths = _source_paths(source_repo)
        _validate_source(paths)
        if list(Image.open(paths["map"]).getdata()) != list(Image.open(required["map"]).getdata()):
            raise ValueError("RMUC2024 PNG cells differ from the certified PGM")
    print(f"PASS: RMUC2024 assets are complete under {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repo", type=Path, default=Path("/home/pupil/gazebo-sim"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--isaaclab-root", type=Path)
    parser.add_argument("--check", action="store_true", help="validate the installed assets without converting")
    args = parser.parse_args()
    if args.check:
        check(args.repo_root.resolve(), source_repo=args.source_repo.resolve())
    else:
        install(args.source_repo.resolve(), args.repo_root.resolve(), args.isaaclab_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
