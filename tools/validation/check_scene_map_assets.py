#!/usr/bin/env python3
"""Validate scene-map ownership, requirements, and preflight collection."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[2]
for source_root in (REPO_ROOT, REPO_ROOT / "source" / "EAI", REPO_ROOT / "source" / "EAI_assets"):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

import simulator  # noqa: E402
from EAI.hmrs_env.env_diy.catalog import scene_choices  # noqa: E402
from EAI_assets import asset_resolver  # noqa: E402
from EAI_assets.asset_requirements import _SCENE_PATHS, resolve_selection  # noqa: E402
from EAI_assets.scene_maps import SCENE_MAP_PATHS  # noqa: E402
from EAI_assets.scene_resources import (  # noqa: E402
    OCCUPANCY_MAP,
    SCENE_RESOURCE_PATHS,
    ensure_scene_resource,
)


def _check_scene_requirements() -> None:
    scene_keys = tuple(key for key, _label in scene_choices())
    assert scene_keys == tuple(SCENE_MAP_PATHS)
    assert scene_keys == tuple(_SCENE_PATHS)

    for scene_key in scene_keys:
        expected_maps = (
            f"scene/{scene_key}/{scene_key}_map.yaml",
            f"scene/{scene_key}/{scene_key}_map.png",
        )
        assert SCENE_MAP_PATHS[scene_key] == expected_maps
        assert _SCENE_PATHS[scene_key][-2:] == expected_maps

        graph = resolve_selection({"scene_key": scene_key, "robots": []})
        requirement = next(item for item in graph.requirements if item.id == f"scene:{scene_key}")
        assert requirement.relative_paths == _SCENE_PATHS[scene_key]
        assert requirement.remote_paths[-2:] == tuple(f"usd/{path}" for path in expected_maps)


def _check_scene_resource_api() -> None:
    assert tuple(SCENE_RESOURCE_PATHS) == tuple(SCENE_MAP_PATHS)
    for scene_key, resources in SCENE_RESOURCE_PATHS.items():
        assert tuple(resources) == (OCCUPANCY_MAP,)
        assert resources[OCCUPANCY_MAP] == SCENE_MAP_PATHS[scene_key]

    with tempfile.TemporaryDirectory(prefix="eai-scene-resource-check-") as tmp_dir:
        usd_root = Path(tmp_dir) / "usd"
        requested = []

        def ensure_paths(paths):
            requested.extend(paths)
            for value in paths:
                path = Path(value)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

        resolver = SimpleNamespace(
            usd_root=lambda: usd_root,
            ensure_usd_files_for_paths=ensure_paths,
        )
        paths = ensure_scene_resource(
            "warehouse",
            OCCUPANCY_MAP,
            asset_resolver=resolver,
        )
        assert paths == (
            usd_root / "scene/warehouse/warehouse_map.yaml",
            usd_root / "scene/warehouse/warehouse_map.png",
        )
        assert requested == [str(path) for path in paths]


def _check_exact_resolver_patterns() -> None:
    with tempfile.TemporaryDirectory(prefix="eai-exact-resource-check-") as tmp_dir:
        usd_root = Path(tmp_dir) / "usd"
        expected_patterns = [
            "usd/scene/warehouse/warehouse_map.yaml",
            "usd/scene/warehouse/warehouse_map.png",
        ]
        assert asset_resolver._allow_patterns_for_paths(
            [
                "scene/warehouse/warehouse_map.yaml",
                "scene/warehouse/warehouse_map.png",
            ]
        ) == expected_patterns
        assert asset_resolver._patterns_cover_scene_resources(expected_patterns)
        assert asset_resolver._patterns_cover_scene_resources(["usd/scene/warehouse/**"])
        assert not asset_resolver._patterns_cover_scene_resources(["usd/robot/b2/**"])
        assert asset_resolver._coalesce_transaction_patterns(
            [expected_patterns[0], "usd/scene/warehouse/**", expected_patterns[1]]
        ) == ["usd/scene/warehouse/**"]
        previous_root = os.environ.get("EAI_USD_ROOT")
        previous_auto_download = os.environ.get("EAI_ASSETS_AUTO_DOWNLOAD")
        os.environ["EAI_USD_ROOT"] = str(usd_root)
        os.environ["EAI_ASSETS_AUTO_DOWNLOAD"] = "1"

        def download(*, local_dir, allow_patterns, **_kwargs):
            assert allow_patterns == expected_patterns
            for relative_path in allow_patterns:
                path = Path(local_dir) / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

        try:
            patterns = asset_resolver.ensure_usd_files_for_paths(
                [
                    "scene/warehouse/warehouse_map.yaml",
                    "scene/warehouse/warehouse_map.png",
                ],
                downloader=download,
            )
        finally:
            if previous_root is None:
                os.environ.pop("EAI_USD_ROOT", None)
            else:
                os.environ["EAI_USD_ROOT"] = previous_root
            if previous_auto_download is None:
                os.environ.pop("EAI_ASSETS_AUTO_DOWNLOAD", None)
            else:
                os.environ["EAI_ASSETS_AUTO_DOWNLOAD"] = previous_auto_download

        assert patterns == expected_patterns
        assert sorted(path.relative_to(usd_root).as_posix() for path in usd_root.rglob("*") if path.is_file()) == [
            "scene/warehouse/warehouse_map.png",
            "scene/warehouse/warehouse_map.yaml",
        ]


def _check_scene_requirement_uses_shared_transaction() -> None:
    with tempfile.TemporaryDirectory(prefix="eai-scene-requirement-check-") as tmp_dir:
        root = Path(tmp_dir)
        usd_root = root / "usd"
        runtime_root = root / "runtime"
        runtime_root.mkdir(mode=0o700)
        previous = {
            name: os.environ.get(name)
            for name in ("EAI_USD_ROOT", "EAI_ASSETS_AUTO_DOWNLOAD", "XDG_RUNTIME_DIR")
        }
        os.environ.update(
            {
                "EAI_USD_ROOT": str(usd_root),
                "EAI_ASSETS_AUTO_DOWNLOAD": "1",
                "XDG_RUNTIME_DIR": str(runtime_root),
            }
        )
        progress = []

        def download(*, repo_id, repo_type, local_dir, allow_patterns):
            assert repo_id and repo_type == "dataset"
            assert allow_patterns == ["usd/scene/warehouse/**"]
            assert Path(local_dir).parent == usd_root.parent
            for name in ("warehouse.usd", "warehouse_map.yaml", "warehouse_map.png"):
                path = Path(local_dir) / "usd/scene/warehouse" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

        try:
            graph = resolve_selection({"scene_key": "warehouse", "robots": []})
            requirement = next(item for item in graph.requirements if item.id == "scene:warehouse")
            result = asset_resolver.download_requirement(
                requirement,
                downloader=download,
                progress=progress.append,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        assert result.state is asset_resolver.RequirementState.READY
        assert progress == ["scene:warehouse", "ready:scene:warehouse"]


def _check_failed_exact_resolution_is_not_published() -> None:
    with tempfile.TemporaryDirectory(prefix="eai-failed-resource-check-") as tmp_dir:
        usd_root = Path(tmp_dir) / "usd"
        previous_root = os.environ.get("EAI_USD_ROOT")
        previous_auto_download = os.environ.get("EAI_ASSETS_AUTO_DOWNLOAD")
        os.environ["EAI_USD_ROOT"] = str(usd_root)
        os.environ["EAI_ASSETS_AUTO_DOWNLOAD"] = "1"

        def incomplete_download(*, local_dir, allow_patterns, **_kwargs):
            yaml_path = Path(local_dir) / allow_patterns[0]
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_path.write_bytes(b"incomplete")

        try:
            try:
                asset_resolver.ensure_usd_files_for_paths(
                    [
                        "scene/warehouse/warehouse_map.yaml",
                        "scene/warehouse/warehouse_map.png",
                    ],
                    downloader=incomplete_download,
                )
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("incomplete staged resource unexpectedly succeeded")
        finally:
            if previous_root is None:
                os.environ.pop("EAI_USD_ROOT", None)
            else:
                os.environ["EAI_USD_ROOT"] = previous_root
            if previous_auto_download is None:
                os.environ.pop("EAI_ASSETS_AUTO_DOWNLOAD", None)
            else:
                os.environ["EAI_ASSETS_AUTO_DOWNLOAD"] = previous_auto_download

        assert not usd_root.exists()


def _check_concurrent_exact_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="eai-concurrent-resource-check-") as tmp_dir:
        root = Path(tmp_dir)
        usd_root = root / "usd"
        runtime_root = root / "runtime"
        runtime_root.mkdir(mode=0o700)
        calls_path = root / "download-calls"
        worker = r"""
import os
import time
from pathlib import Path
from EAI_assets.asset_resolver import ensure_usd_files_for_paths

def download(*, local_dir, allow_patterns, **_kwargs):
    with open(os.environ['CALLS_PATH'], 'a', encoding='utf-8') as stream:
        stream.write('download\n')
    time.sleep(0.3)
    for relative_path in allow_patterns:
        path = Path(local_dir) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')

ensure_usd_files_for_paths(
    ['scene/warehouse/warehouse_map.yaml', 'scene/warehouse/warehouse_map.png'],
    downloader=download,
)
"""
        env = os.environ.copy()
        env.update(
            {
                "CALLS_PATH": str(calls_path),
                "EAI_ASSETS_AUTO_DOWNLOAD": "1",
                "EAI_USD_ROOT": str(usd_root),
                "PYTHONPATH": str(REPO_ROOT / "source" / "EAI_assets"),
                "XDG_RUNTIME_DIR": str(runtime_root),
            }
        )
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", worker],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _index in range(2)
        ]
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stdout + stderr

        assert calls_path.read_text(encoding="utf-8").splitlines() == ["download"]
        assert (usd_root / "scene/warehouse/warehouse_map.yaml").read_bytes() == b"fixture"
        assert (usd_root / "scene/warehouse/warehouse_map.png").read_bytes() == b"fixture"


def _check_mixed_preflight_and_exact_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="eai-mixed-resource-check-") as tmp_dir:
        root = Path(tmp_dir)
        usd_root = root / "usd"
        runtime_root = root / "runtime"
        runtime_root.mkdir(mode=0o700)
        worker = r"""
import os
import time
from pathlib import Path
from EAI_assets.asset_resolver import ensure_usd_assets_for_paths, ensure_usd_files_for_paths

root = Path(os.environ['CHECK_ROOT'])
mode = os.environ['MODE']
active_path = root / 'active-download'
race_path = root / 'concurrent-download-detected'
calls_path = root / 'download-calls'

def write_file(local_dir, relative_path):
    path = Path(local_dir) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mode.encode('utf-8'))

def download(*, local_dir, allow_patterns, **_kwargs):
    try:
        descriptor = os.open(active_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        race_path.write_text('race', encoding='utf-8')
        descriptor = None
    try:
        with open(calls_path, 'a', encoding='utf-8') as stream:
            stream.write(mode + '\n')
        time.sleep(0.4)
        for pattern in allow_patterns:
            if pattern.endswith('/**'):
                bundle = pattern[:-3]
                for name in ('warehouse.usd', 'warehouse_map.yaml', 'warehouse_map.png'):
                    write_file(local_dir, f'{bundle}/{name}')
            else:
                write_file(local_dir, pattern)
    finally:
        if descriptor is not None:
            os.close(descriptor)
            active_path.unlink()

map_paths = [
    'scene/warehouse/warehouse_map.yaml',
    'scene/warehouse/warehouse_map.png',
]
if mode == 'exact':
    ensure_usd_files_for_paths(map_paths, downloader=download)
else:
    ensure_usd_assets_for_paths(
        ['scene/warehouse/warehouse.usd', *map_paths],
        downloader=download,
    )
"""
        base_env = os.environ.copy()
        base_env.update(
            {
                "CHECK_ROOT": str(root),
                "EAI_ASSETS_AUTO_DOWNLOAD": "1",
                "EAI_USD_ROOT": str(usd_root),
                "PYTHONPATH": str(REPO_ROOT / "source" / "EAI_assets"),
                "XDG_RUNTIME_DIR": str(runtime_root),
            }
        )
        processes = []
        for mode in ("exact", "mixed"):
            env = base_env.copy()
            env["MODE"] = mode
            processes.append(
                subprocess.Popen(
                    [sys.executable, "-c", worker],
                    cwd=REPO_ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stdout + stderr

        assert not (root / "concurrent-download-detected").exists()
        calls = (root / "download-calls").read_text(encoding="utf-8").splitlines()
        assert calls in (["mixed"], ["exact", "mixed"]), calls
        yaml_bytes = (usd_root / "scene/warehouse/warehouse_map.yaml").read_bytes()
        png_bytes = (usd_root / "scene/warehouse/warehouse_map.png").read_bytes()
        assert yaml_bytes == png_bytes
        assert yaml_bytes in {b"exact", b"mixed"}
        assert (usd_root / "scene/warehouse/warehouse.usd").is_file()


def _check_external_cli() -> None:
    python = Path("/usr/bin/python3")
    if not python.is_file():
        python = Path(sys.executable)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(python), str(REPO_ROOT / "simulator.py"), "assets", "list", "--format", "json"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        scene: {resource: list(paths) for resource, paths in resources.items()}
        for scene, resources in SCENE_RESOURCE_PATHS.items()
    }


def _check_preflight_merge() -> None:
    payload = simulator._build_asset_payload(
        task_name="scene-map-check",
        selection_data={"scene_key": "plane", "robots": []},
        saved_task_data=None,
        should_run=True,
        env_cfg=object(),
        collect_usd_asset_paths=lambda _cfg: ["/tmp/root.usd", "/tmp/shared.png"],
        collect_controller_asset_paths=lambda _cfg: [],
        collect_selection_usd_asset_paths=lambda _selection: ["/tmp/shared.png", "/tmp/plane_map.yaml"],
    )
    assert payload["usd_paths"] == ["/tmp/root.usd", "/tmp/shared.png", "/tmp/plane_map.yaml"]

    collected = simulator._collect_selection_usd_asset_paths({"scene_key": "plane", "robots": []})
    assert any(path.endswith("/scene/plane/plane_map.yaml") for path in collected)
    assert any(path.endswith("/scene/plane/plane_map.png") for path in collected)


def _check_source_ownership() -> None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "algorithm/multi_robot_navigation/maps/**",
            "demo/fire_rescue/assets/factory_map.*",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = tuple(line for line in result.stdout.splitlines() if line)
    remaining = tuple(path for path in tracked if (REPO_ROOT / path).exists())
    assert not remaining, f"Scene maps still exist under algorithms or demos: {remaining}"


def main() -> int:
    _check_scene_requirements()
    _check_scene_resource_api()
    _check_exact_resolver_patterns()
    _check_scene_requirement_uses_shared_transaction()
    _check_failed_exact_resolution_is_not_published()
    _check_concurrent_exact_resolution()
    _check_mixed_preflight_and_exact_resolution()
    _check_external_cli()
    _check_preflight_merge()
    _check_source_ownership()
    print("PASS: scene resources are provider-owned and available to internal/external consumers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
