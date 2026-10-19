#!/usr/bin/env python3
"""Start Isaac Sim and its linked browser semantic map editor in one process."""
from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        help="optional launcher JSON; defaults discover scene-local maps")
    parser.add_argument("--catalog", type=Path, help="Directory containing editable map projects")
    parser.add_argument("--project", type=Path, help="Initial map project (overrides --scene)")
    parser.add_argument("--scene", default="living_office", help="Initial scene_key, e.g. living_office")
    parser.add_argument("--asset-root", type=Path, default=ROOT / "usd")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--headless", action="store_true", help="Run Kit without a window (diagnostic use)")
    parser.add_argument("--check", action="store_true", help="Validate configuration and USD bindings without launching Kit")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text()) if args.config and args.config.is_file() else {}
    for name in ("catalog", "project"):
        value = getattr(args, name)
        if value is None and config.get(name) and not (name == "project" and "--scene" in (argv or [])):
            value = Path(config[name])
            if not value.is_absolute():
                value = args.config.parent / value
        setattr(args, name, value.resolve() if value else None)
    args.port = args.port if args.port is not None else config.get("port", 8766)
    if "--scene" not in (argv or []) and config.get("scene"):
        args.scene = config["scene"]
    return args


def initial_project(args):
    from tools.topology_authoring.map_project import MapProject, read_json
    from tools.topology_authoring.scene_catalog import discover_scene_projects
    if args.project:
        return MapProject(args.project)
    paths = (sorted(args.catalog.iterdir()) if args.catalog else
             [path for _key, path in discover_scene_projects(args.asset_root).items()])
    for path in paths:
        if path.is_dir() and (path / "project.json").is_file():
            if read_json(path / "project.json")["scene_key"] == args.scene:
                return MapProject(path)
    raise ValueError(f"Scene {args.scene!r} is not in the map catalog or usd/scene")


async def configure_view(stage):
    import carb.settings
    import omni.kit.app
    from pxr import Gf, Usd, UsdGeom
    from isaacsim.core.utils.viewports import set_camera_view
    # Camera Light, with camera changes confined to the transient session layer.
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    bounds = cache.ComputeWorldBound(stage.GetPseudoRoot()).ComputeAlignedBox()
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        carb.settings.get_settings().set_bool("/rtx/useViewLightingMode", True)
        if not bounds.IsEmpty():
            center = bounds.GetMidpoint()
            radius = max(bounds.GetSize().GetLength(), 1.0)
            eye = center + Gf.Vec3d(radius * .7, -radius * .7, radius * .6)
            set_camera_view(eye=tuple(eye), target=tuple(center))
        # Render/PhysX stage-open callbacks run on subsequent Kit updates.
        # Let those initialization writes settle in the transient layer before
        # restoring the user's original edit target and exposing the bridge.
        for _ in range(3):
            await omni.kit.app.get_app().next_update_async()


def main(argv=None):
    args = parse_args(argv)
    from tools.topology_authoring.map_project import read_json
    from tools.topology_authoring.map_server import make_server
    from tools.topology_authoring.scene_catalog import resolve_scene, scene_metadata
    project = initial_project(args)
    resolve_scene(project.manifest, args.asset_root)
    if args.check:
        manifests = [(project.root, project.manifest)]
        if args.catalog:
            manifests.extend((p, read_json(p / "project.json")) for p in sorted(args.catalog.iterdir())
                             if p.is_dir() and (p / "project.json").is_file())
        else:
            from tools.topology_authoring.scene_catalog import discover_scene_projects
            manifests = [(path, read_json(path / "project.json"))
                         for path in discover_scene_projects(args.asset_root).values()]
        for path, manifest in manifests:
            resolve_scene(manifest, args.asset_root)
            print(f"OK {manifest['scene_key']}: {scene_metadata(manifest)['source_path']}")
        return 0
    # Binding the port before creating Kit avoids launching an orphan GUI when
    # another editor already owns this address. Never kill another user's app.
    server = make_server(project, args.port, args.catalog, asset_root=args.asset_root, isaac_linked=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="semantic-map-http")
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Semantic map editor: {url}", flush=True)
    app = session = None
    stopping = False
    def stop_requested(_signum, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    try:
        from isaacsim import SimulationApp
        app = SimulationApp({"headless": args.headless, "fast_shutdown": False,
                             "width": 1600, "height": 1000})
        import omni.usd
        import carb.settings
        # Viewport RTX preferences belong to this editor session, not the USD.
        carb.settings.get_settings().set_bool("/app/omni.usd/storeRenderSettingsToUsdStage", False)
        from tools.topology_authoring.isaac_session import IsaacMapSession
        session = IsaacMapSession(omni.usd.get_context(), url.rstrip("/"),
                                  server.isaac_context, server.update_isaac_runtime, on_open=configure_view)
        if not args.no_browser:
            webbrowser.open(url)
        while not stopping and app.is_running():
            app.update()
            session.tick()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if session:
            session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if app:
            from simulator import _prepare_gui_for_app_close
            _prepare_gui_for_app_close(app)
            app.close()


if __name__ == "__main__":
    raise SystemExit(main())
