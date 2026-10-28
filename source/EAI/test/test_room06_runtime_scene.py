import ast
import hashlib
from pathlib import Path

import pytest


def _room06_candidate(root: Path, payload: bytes = b"room06-usd") -> tuple[Path, str]:
    path = root / "scene" / "Living and public space" / "room_06" / "room_06.usda"
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_room06_resolver_is_logical_hash_bound_and_fail_closed(tmp_path, monkeypatch):
    from EAI_assets.room06_asset import resolve_room06_scene

    monkeypatch.setenv("EAI_USD_ROOT", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="room_06 USD is missing"):
        resolve_room06_scene()

    path, digest = _room06_candidate(tmp_path)
    resolved = resolve_room06_scene(expected_sha256=digest)
    assert resolved.path == path.resolve()
    assert resolved.sha256 == digest
    assert resolved.size_bytes == path.stat().st_size

    path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="room_06 USD digest mismatch"):
        resolve_room06_scene(expected_sha256=digest)


def test_room06_catalog_and_requirement_use_public_logical_path():
    from EAI.hmrs_env.env_diy import catalog
    from EAI_assets.asset_requirements import resolve_selection
    from EAI_assets.room06_asset import ROOM06_USD_RELATIVE_PATH
    from EAI_assets.scene_resources import SCENE_RESOURCE_PATHS

    assert dict(catalog.scene_choices())["room_06"] == "Room 06 multi-level benchmark"
    graph = resolve_selection({"version": 1, "scene_key": "room_06", "robots": []})
    scene = next(item for item in graph.requirements if item.id == "scene:room_06")
    assert scene.relative_paths == (ROOM06_USD_RELATIVE_PATH,)
    assert SCENE_RESOURCE_PATHS["room_06"] == {}


def test_room06_resolver_does_not_fall_back_to_flat_scene_copy(tmp_path, monkeypatch):
    from EAI_assets.room06_asset import resolve_room06_scene

    monkeypatch.setenv("EAI_USD_ROOT", str(tmp_path))
    legacy = tmp_path / "scene" / "room_06" / "room_06.usda"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"obsolete-flat-copy")
    with pytest.raises(FileNotFoundError, match="Living and public space"):
        resolve_room06_scene()


def test_room06_interactive_registration_binds_scene_owned_raycast_proxy():
    from EAI_assets.scene.room06_runtime import ROOM06_RAYCAST_PROXY_PRIM_PATH

    # Importing env_builder outside Kit pulls ``carb`` before the registry is
    # available.  Inspect the checked-in constructor instead so this remains a
    # real CPU test of the current worktree, not an external editable install.
    repository = Path(__file__).resolve().parents[3]
    path = repository / "source/EAI_hmrs/EAI_hmrs/env_builder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    registrations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "SceneOption" or not node.args:
            continue
        if isinstance(node.args[0], ast.Constant) and node.args[0].value == "room_06":
            registrations.append(node)
    assert len(registrations) == 1
    scene = registrations[0]
    assert ast.literal_eval(scene.args[2]) == ("EAI_assets.scene.room06", "ROOM06_CFG")
    keywords = {item.arg: item.value for item in scene.keywords}
    assert ast.literal_eval(keywords["prim_path"]) == "/World/Room06"
    assert ast.literal_eval(keywords["robot_spawn_origin"]) == (5.33, 0.17)
    assert isinstance(keywords["raycast_collision_prim_path"], ast.Name)
    assert keywords["raycast_collision_prim_path"].id == "ROOM06_RAYCAST_PROXY_PRIM_PATH"
    assert ROOM06_RAYCAST_PROXY_PRIM_PATH == "/World/Room06/EaiRaycastTerrain"


def test_room06_raycast_proxy_uses_only_scene_owned_collision_meshes():
    from pxr import Usd, UsdGeom, UsdPhysics

    from EAI_assets.scene.room06_runtime import build_room06_raycast_proxy

    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/World/Room06").GetPrim()
    mesh = UsdGeom.Mesh.Define(stage, "/World/Room06/floor")
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim()).CreateCollisionEnabledAttr(True)
    decorative = UsdGeom.Mesh.Define(stage, "/World/Room06/decorative")
    decorative.CreatePointsAttr([(0, 0, 1), (1, 0, 1), (0, 1, 1)])
    decorative.CreateFaceVertexCountsAttr([3])
    decorative.CreateFaceVertexIndicesAttr([0, 1, 2])

    report = build_room06_raycast_proxy(stage, str(root.GetPath()))
    assert report.source_mesh_count == 1
    assert report.point_count == 3
    assert report.triangle_count == 1
    assert stage.GetPrimAtPath(report.prim_path).IsValid()
