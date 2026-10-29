"""Room 06 Iris semantic-point transport demonstration.

The demo deliberately keeps the data side small and explicit: semantic-point
IDs are resolved from the project, navigation is performed through the public
``NavigationExecutor`` and manipulation through ``PickPlaceExecutor``.  The
``--check`` mode only reads JSON, so it is safe to use from a normal Python
environment without starting Isaac Sim.

Five trials use fixed aerial nodes: two at Z=3.7 m, then three at Z=1.5 m.
``--seed`` remains accepted for compatibility and does not affect the starts.

Run from the repository root::

    OMNI_KIT_ACCEPT_EULA=YES python demo/room06_iris_semantic_transport.py \
        --scene living_room_06 --trials 5 --seed 0
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "tmp/room06-iris-semantic-transport"
DEFAULT_ASSET_ROOT = REPO_ROOT / "usd"
START_NODE_IDS = ("node:0016", "node:0017", "node:0013", "node:0014", "node:0015")
START_HEIGHTS = (3.7, 3.7, 1.5, 1.5, 1.5)


def _other_demo_processes() -> list[tuple[int, str]]:
    """Find an already running copy before Kit creates a second PhysX app."""
    current_pid = os.getpid()
    matches: list[tuple[int, str]] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return matches
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == current_pid:
            continue
        try:
            raw_args = (entry / "cmdline").read_bytes().split(b"\x00")
            args = [arg.decode(errors="ignore") for arg in raw_args if arg]
            command = " ".join(args).strip()
        except (OSError, UnicodeError):
            continue
        # A shell used to launch this script may contain the filename in its
        # ``-c`` string; only count a real argv element naming the script.
        if any(arg.endswith("room06_iris_semantic_transport.py") for arg in args):
            matches.append((int(entry.name), command))
    return matches


def _check_cuda_memory(device: str) -> None:
    """Fail before Kit startup when PhysX cannot reserve its GPU arena."""
    if not str(device).startswith("cuda") or shutil.which("nvidia-smi") is None:
        return
    try:
        index = int(device.split(":", 1)[1]) if ":" in device else 0
        visible = os.environ.get("CUDA_VISIBLE_DEVICES")
        gpu_id = visible.split(",")[index].strip() if visible is not None else str(index)
        raw = subprocess.check_output(
            ["nvidia-smi", "--id=" + gpu_id,
             "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True, stderr=subprocess.DEVNULL, timeout=2.0)
        free_mib = int(float(raw.splitlines()[0].strip()))
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return
    # Kit rendering, PhysX and articulation tensors all reserve GPU memory.
    # This conservative preflight is not a guarantee of peak memory use.
    minimum_mib = 4096
    if free_mib < minimum_mib:
        raise SystemExit(
            f"Insufficient free CUDA memory for Isaac/PhysX: {free_mib} MiB free, "
            f"at least {minimum_mib} MiB required by this startup check. "
            "Close other Isaac windows and rerun the command."
        )


def _jsonable(value: Any) -> Any:
    """Convert simulator response objects and tensors into JSON-safe values."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    for method in ("detach", "cpu"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                value = fn()
            except Exception:
                pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _jsonable(tolist())
    if hasattr(value, "to_mapping"):
        return _jsonable(value.to_mapping())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(value), indent=2, ensure_ascii=False,
                               allow_nan=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_dir(scene: str, asset_root: Path = DEFAULT_ASSET_ROOT) -> Path:
    """Resolve a scene key without depending on the authoring web service."""
    candidates = list((asset_root / "scene").rglob("semantic_map/project.json"))
    matches = []
    for project in sorted(set(candidates)):
        try:
            payload = json.loads(project.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("scene_key") == scene:
            matches.append(project.parent)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one semantic project for {scene!r}, found {len(matches)}")
    current = matches[0] / "current"
    return current if current.is_dir() else matches[0]


def load_inputs(scene: str, asset_root: Path = DEFAULT_ASSET_ROOT) -> dict[str, Any]:
    root = project_dir(scene, asset_root)
    semantic_path = root / "semantic-points.json"
    aerial_path = root / "topology-aerial.json"
    manifest_path = root / "manifest.json"
    if not semantic_path.is_file() or not aerial_path.is_file():
        raise FileNotFoundError(f"{root} must contain semantic-points.json and topology-aerial.json")
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    topology = json.loads(aerial_path.read_text(encoding="utf-8"))
    points = semantic.get("points") if isinstance(semantic, Mapping) else None
    nodes = topology.get("nodes") if isinstance(topology, Mapping) else None
    if not isinstance(points, list) or not isinstance(nodes, list):
        raise ValueError("semantic points and aerial topology must contain arrays")
    if len(points) != 2:
        raise ValueError(f"room transport requires exactly two semantic points, found {len(points)}")
    by_node = {row.get("id"): row for row in nodes if isinstance(row, Mapping)}
    for point in points:
        if not isinstance(point, Mapping) or not isinstance(point.get("id"), str):
            raise ValueError("semantic point records require stable IDs")
        position = point.get("position_xyz")
        if not isinstance(position, list) or len(position) != 3:
            raise ValueError(f"semantic point {point.get('id')} has invalid position_xyz")
        bindings = [b for b in point.get("navigation_bindings", [])
                    if isinstance(b, Mapping) and b.get("domain") == "aerial"]
        if not bindings:
            raise ValueError(f"semantic point {point['id']} has no aerial navigation binding")
        for binding in bindings:
            node_id = binding.get("node_id")
            if node_id not in by_node:
                raise ValueError(f"semantic point {point['id']} binds missing aerial node {node_id!r}")
    high, low = sorted(points, key=lambda row: float(row["position_xyz"][2]), reverse=True)
    high_z = float(high["position_xyz"][2])
    low_z = float(low["position_xyz"][2])
    if math.isclose(high_z, low_z, abs_tol=1e-9):
        raise ValueError("semantic points must have a unique high/low Z ordering")
    return {"root": root, "semantic": semantic, "topology": topology,
            "points": points, "high": high, "low": low, "nodes": nodes,
            "node_by_id": by_node,
            "manifest": (json.loads(manifest_path.read_text(encoding="utf-8"))
                         if manifest_path.is_file() else {})}


def fixed_start_plan(nodes: list[Mapping[str, Any]], trials: int) -> list[dict[str, Any]]:
    if type(trials) is not int or not 1 <= trials <= 5:
        raise ValueError("trials must be between 1 and 5")
    by_id = {row["id"]: row for row in nodes}
    starts = []
    for node_id, height in zip(START_NODE_IDS, START_HEIGHTS):
        if node_id not in by_id:
            raise ValueError(f"fixed aerial start node is missing: {node_id}")
        row = by_id[node_id]
        if not math.isclose(float(row["position_xyz"][2]), height, abs_tol=1e-6):
            raise ValueError(f"{node_id} must have Z={height} m")
        starts.append(deepcopy(row))
    return starts[:trials]


def offline_check(args: argparse.Namespace) -> dict[str, Any]:
    data = load_inputs(args.scene, args.asset_root)
    starts = fixed_start_plan(data["nodes"], args.trials)
    # Importing these dependency-light helpers catches malformed topology while
    # still avoiding all Kit/Isaac imports in --check mode.
    sys.path.insert(0, str(REPO_ROOT))
    from algorithm.skills.navigation import (route_graph_digest, semantic_binding_digest, validate_topology,
                                              select_node_route, NavigationPose)
    errors = validate_topology(data["topology"], metric="xyz")
    if errors:
        raise ValueError("invalid aerial topology: " + "; ".join(errors))
    routes = []
    for start in starts:
        route = select_node_route(
            data["topology"], NavigationPose(tuple(start["position_xyz"])),
            profile=_room06_aerial_profile(), target_ref=data["high"]["id"],
            semantic_points=data["points"])
        if not route["admitted"]:
            raise ValueError(f"{start['id']} cannot route to the high semantic point: {route.get('reason')}")
        routes.append({"start_node_id": start["id"], "selection": route})
    result = {
        "scene": args.scene,
        "project_dir": str(data["root"]),
        "semantic_points": [{"id": data["high"]["id"], "z": data["high"]["position_xyz"][2]},
                            {"id": data["low"]["id"], "z": data["low"]["position_xyz"][2]}],
        "high_id": data["high"]["id"], "low_id": data["low"]["id"],
        "route_graph_digest": route_graph_digest(data["topology"]),
        "semantic_binding_digest": semantic_binding_digest(data["points"]),
        "fixed_starts": [{"id": row["id"], "position_xyz": row["position_xyz"]} for row in starts],
        "trials": args.trials, "seed_ignored": args.seed, "routes_to_high": routes,
    }
    if args.output_dir is not None:
        _write_json(args.output_dir / "check.json", result)
    return result


def _scene_usd(data: Mapping[str, Any], asset_root: Path) -> Path:
    manifest = data.get("manifest", {})
    source = manifest.get("provenance", {}).get("semantic-footprints.json", {}).get("source_usd")
    if isinstance(source, str):
        candidate = (asset_root / source).resolve()
        if candidate.is_file() and candidate.is_relative_to(asset_root.resolve()):
            return candidate
    fallback = data["root"].parent.parent / "room_06.usda"
    if fallback.is_file():
        return fallback
    raise FileNotFoundError("room_06 USD source could not be resolved")


CONTROLLER_PROFILES = {
    "baseline": "PEGASUS_IRIS_POSITION_CFG",
    "damped": "PEGASUS_IRIS_POSITION_DAMPED_CFG",
    "conservative": "PEGASUS_IRIS_POSITION_CONSERVATIVE_CFG",
    "fast": "PEGASUS_IRIS_POSITION_FAST_CFG",
}


def _room06_aerial_profile():
    from algorithm.skills.navigation import aerial_navigation_profile
    return aerial_navigation_profile(stop_linear_speed_m_s=0.08,
                                     stop_angular_speed_rad_s=0.15)


def _make_selection(start: Mapping[str, Any], controller_profile: str = "baseline") -> dict[str, Any]:
    selection_path = REPO_ROOT / "source/EAI_hmrs/EAI_hmrs/envs/pegasus_drones.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["scene_key"] = "plane"
    selection["task_name"] = "room06_iris_semantic_transport"
    robot = selection["robots"][0]
    robot["attachments"] = []
    robot["controller"]["cfg"] = CONTROLLER_PROFILES[controller_profile]
    robot["spawn_pose"] = {"position": list(start["position_xyz"]),
                            "rotation": [1.0, 0.0, 0.0, 0.0]}
    return selection


def _scene_hook(usd_path: Path, high_position: tuple[float, float, float], box_size: float):
    def configure(cfg):
        import isaaclab.sim as sim_utils
        from isaaclab.assets import AssetBaseCfg, RigidObjectCfg
        usd = sim_utils.UsdFileCfg(usd_path=str(usd_path), func=_spawn_room_reference)
        # A single Iris and one cube do not need the batch-training buffers
        # inherited from MultiRobotDirectEnvCfg. Keep finite capacity for the
        # room's static collision meshes, without reserving hundreds of MiB
        # for millions of simultaneous contacts/aggregate pairs.
        cfg.sim.physx.gpu_max_rigid_contact_count = 2**18
        cfg.sim.physx.gpu_max_rigid_patch_count = 2**15
        cfg.sim.physx.gpu_found_lost_pairs_capacity = 2**18
        cfg.sim.physx.gpu_found_lost_aggregate_pairs_capacity = 2**18
        cfg.sim.physx.gpu_total_aggregate_pairs_capacity = 2**18
        cfg.sim.physx.gpu_collision_stack_size = 2**24
        cfg.sim.physx.gpu_heap_capacity = 2**25
        cfg.sim.physx.gpu_temp_buffer_capacity = 2**23
        # A plane selection owns the physical floor; adding the room as a world
        # asset keeps the Iris environment and its authored collision meshes.
        scene_cls = type(cfg.scene)
        annotations = getattr(scene_cls, "__annotations__", {})
        annotations["world"] = AssetBaseCfg
        cfg.scene.world = AssetBaseCfg(prim_path="/World/Room06", spawn=usd)
        annotations["semantic_transport_box"] = RigidObjectCfg
        cfg.scene.semantic_transport_box = RigidObjectCfg(
            prim_path="{ENV_REGEX_NS}/semantic_transport_box",
            spawn=sim_utils.CuboidCfg(
                size=(box_size, box_size, box_size),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(),
                mass_props=sim_utils.MassPropertiesCfg(mass=0.10),
                collision_props=sim_utils.CollisionPropertiesCfg(
                    contact_offset=0.001, rest_offset=0.0),
                visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.95, 0.35, 0.05)),
            ),
            init_state=RigidObjectCfg.InitialStateCfg(
                pos=(high_position[0], high_position[1], high_position[2] + box_size / 2.0)),
        )
    return configure


def _disable_reference_physics_scenes(root):
    """Leave the host PhysicsScene authoritative; do not edit the USD file."""
    from pxr import Usd, UsdPhysics

    stage = root.GetStage()
    scenes = [prim for prim in Usd.PrimRange(root) if prim.IsA(UsdPhysics.Scene)]
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for prim in scenes:
            prim.SetActive(False)


def _spawn_room_reference(prim_path, cfg, translation=None, orientation=None, **kwargs):
    from isaaclab.sim.spawners.from_files import spawn_from_usd

    root = spawn_from_usd(prim_path, cfg, translation, orientation, **kwargs)
    _disable_reference_physics_scenes(root)
    return root


@contextmanager
def _launcher_display_mode(headless: bool):
    """Explicitly select a desktop window even when HEADLESS=1 is inherited."""
    previous = os.environ.get("HEADLESS")
    os.environ["HEADLESS"] = "1" if headless else "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HEADLESS", None)
        else:
            os.environ["HEADLESS"] = previous


def _prepare_kit_numpy() -> None:
    """Use Kit's NumPy ABI before torch or simulator imports load NumPy.

    Kit adds its pip archive later during startup.  With NumPy 2 installed in
    the Python environment this can mix NumPy 2 Python modules with Kit's
    NumPy 1 binary extensions, aborting before the desktop window is ready.
    This changes only this process's import path, never installed packages.
    """
    from importlib.util import find_spec

    spec = find_spec("isaacsim")
    if spec is None or not spec.submodule_search_locations:
        return  # Let the normal launcher report a missing Isaac installation.
    for root in spec.submodule_search_locations:
        archives = sorted((Path(root) / "kit/data/Kit/Isaac-Sim").glob(
            "*/exts/*/omni.kit.pip_archive*/pip_prebundle"))
        for archive in reversed(archives):
            if not (archive / "numpy/__init__.py").is_file():
                continue
            if "numpy" in sys.modules:
                raise RuntimeError("Run this demo as a standalone Python file; NumPy must load after Kit's import path is configured")
            sys.path.insert(0, str(archive))
            import numpy
            print(f"ROOM06_NUMPY: {numpy.__version__} ({numpy.__file__})", flush=True)
            return


def _setup_gui(stage: Any, *, high: tuple[float, float, float],
               low: tuple[float, float, float], box_size: float,
               scene_name: str, trials: int) -> dict[str, Any]:
    """Create a visible camera, semantic markers and a small run-status window.

    All authored objects go to the session layer.  This keeps the demonstration
    visible in Kit without changing the room USD or the semantic-map files.
    """
    from omni.kit.viewport.utility import get_active_viewport
    from pxr import Gf, Usd, UsdGeom

    scope = "/World/Room06SemanticTransport"
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        UsdGeom.Scope.Define(stage, scope)
        for name, position, color in (
            ("HighSemanticPoint", high, (1.0, 0.15, 0.05)),
            ("LowSemanticPoint", low, (0.10, 0.80, 1.0)),
        ):
            sphere = UsdGeom.Sphere.Define(stage, f"{scope}/{name}")
            sphere.CreateRadiusAttr(max(box_size * 0.8, 0.08))
            sphere.AddTranslateOp().Set(Gf.Vec3d(*position))
            sphere.CreateDisplayColorAttr([Gf.Vec3f(*color)])
        line = UsdGeom.BasisCurves.Define(stage, f"{scope}/SemanticTransportGuide")
        line.CreateTypeAttr(UsdGeom.Tokens.linear)
        line.CreateCurveVertexCountsAttr([2])
        line.CreatePointsAttr([Gf.Vec3f(*high), Gf.Vec3f(*low)])
        line.CreateWidthsAttr([0.018])
        line.CreateDisplayColorAttr([Gf.Vec3f(1.0, 0.65, 0.05)])
        center = tuple((high[i] + low[i]) / 2.0 for i in range(3))
        span = max(math.dist(high, low), 4.0)
        # View from inside the room rather than through its roof.
        eye = (center[0] + span * 0.7, center[1] - span * 0.8,
               max(high[2], low[2]) - 0.15)
        camera = UsdGeom.Camera.Define(stage, f"{scope}/Camera")
        camera.CreateFocalLengthAttr(12.0)
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))
        camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(*eye), Gf.Vec3d(*center), Gf.Vec3d(0, 0, 1)).GetInverse())
    viewport = get_active_viewport()
    if viewport is None:
        raise RuntimeError("Isaac desktop viewport is unavailable; use --headless for batch runs")
    viewport.set_active_camera(str(camera.GetPath()))

    import omni.ui as ui
    window = ui.Window("Room 06 - Iris semantic transport", width=430, height=240,
                       position_x=20, position_y=70)
    window.visible = True
    with window.frame:
        with ui.VStack(spacing=7):
            ui.Label("Iris semantic transport", height=28)
            ui.Label(f"Scene: {scene_name} | Trials: {trials}", height=22)
            ui.Label("Red: high point | Blue: low point | Orange: cube", height=24)
            status = ui.Label("Initializing Isaac...", word_wrap=True, height=72)
            ui.Label("Close this panel or the main window to exit.", height=24)
    return {"window": window, "status": status}


def _pose_to_json(pose: Any) -> list[float]:
    return [float(v) for v in pose.position_m]


def navigation_return_target(result: Mapping[str, Any], point: Mapping[str, Any],
                             nodes: Mapping[str, Any]) -> dict[str, Any]:
    """Use the selected endpoint, including when a semantic has many bindings."""
    selection = result["private_evidence"]["route_selection"]
    node_id = selection["selected_node_id"]
    bindings = {(row["domain"], row["node_id"])
                for row in point["navigation_bindings"]}
    if ("aerial", node_id) not in bindings:
        raise ValueError("navigation selected a node outside this semantic point's aerial bindings")
    position = tuple(float(v) for v in nodes[node_id]["position_xyz"])
    if math.dist(position, selection["route_points_m"][-1]) > 1e-9:
        raise ValueError("selected navigation endpoint differs from the map snapshot")
    return {"node_id": node_id, "position_xyz": position}


def navigation_metrics(result: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize measured navigation samples without changing control flow."""
    private = result.get("private_evidence", {})
    samples = private.get("execution_samples", [])
    selection = private.get("route_selection") or {}
    route = selection.get("route_points_m") or []
    target = route[-1] if route else None
    if not samples or target is None:
        return {"sample_count": len(samples), "target_position_m": target}
    errors = [math.dist(row["position_m"], target) for row in samples
              if isinstance(row, Mapping) and isinstance(row.get("position_m"), (list, tuple))]
    speeds = [math.sqrt(sum(float(v) ** 2 for v in row.get("velocity", ())[:3]))
              for row in samples if isinstance(row, Mapping)]
    angular = [math.sqrt(sum(float(v) ** 2 for v in row.get("velocity", ())[3:]))
               for row in samples if isinstance(row, Mapping) and len(row.get("velocity", ())) >= 6]
    return {"sample_count": len(samples), "target_position_m": target,
            "max_position_error_m": max(errors, default=None),
            "peak_linear_speed_m_s": max(speeds, default=None),
            "peak_angular_speed_rad_s": max(angular, default=None),
            "stop_policy": private.get("stop_policy")}


def _map_hashes(root: Path) -> dict[str, str]:
    paths = [p for p in root.rglob("*") if p.is_file()]
    project = root.parent / "project.json"
    if project.is_file():
        paths.append(project)
    return {str(p): _sha256(p) for p in sorted(paths)}


def _manipulation_request(request_id: str, operation: str, *, robot_id: str,
                          object_path: str, target_world: tuple[float, float, float],
                          base_pose: Any, sequence: int, simulation_time: float):
    """Build a fixed-action request after the navigation leg has completed."""
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.pick_place import SKILL_ID

    del target_world, base_pose, sequence, simulation_time
    return SkillRequest(request_id, SKILL_ID, robot_id,
                        parameters={"operation": operation,
                                    "object_ref": object_path})


def run_isaac(args: argparse.Namespace, data: Mapping[str, Any]) -> dict[str, Any]:
    # Isaac imports are intentionally contained here, after --check handling.
    _prepare_kit_numpy()
    sys.path.insert(0, str(REPO_ROOT))
    import torch
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from algorithm.skills.contracts import SkillRequest
    from algorithm.skills.navigation import FrozenNavigationTopology, route_graph_digest, semantic_binding_digest
    from algorithm.skills.navigation import NavigationPose
    from algorithm.skills.navigation.node_route import NODE_ROUTE_SKILL_ID
    from task.executors.navigation import NavigationExecutor
    from task.executors.navigation_backends import WorldPositionNavigationAdapter
    from EAI.physics.aerial_pose_reader import LivePhysxPoseReader, read_aerial_pose
    from task.executors.pick_place import PickPlaceExecutor
    from task.executors.pick_place_adapters import DronePickPlaceAdapter

    starts = fixed_start_plan(data["nodes"], args.trials)
    high = tuple(float(v) for v in data["high"]["position_xyz"])
    low = tuple(float(v) for v in data["low"]["position_xyz"])
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "schema": "eai.demo.room06_iris_semantic_transport", "version": 4,
        "scene": args.scene, "trials_requested": args.trials, "seed_ignored": args.seed,
        "fixed_starts": [{"id": n["id"], "position_xyz": n["position_xyz"]} for n in starts],
        "high_semantic_id": data["high"]["id"], "low_semantic_id": data["low"]["id"],
        "high_position_xyz": list(high), "low_position_xyz": list(low),
        "box_size_m": args.box_size, "route_graph_digest": route_graph_digest(data["topology"]),
        "controller_profile": args.controller_profile,
        "navigation_stop_policy": {"arrival_radius_m": 0.30,
                                    "stop_linear_speed_m_s": 0.08,
                                    "stop_angular_speed_rad_s": 0.15},
        "pick_place_alignment_policy": {"xy_tolerance_m": 0.08,
                                         "z_tolerance_m": 0.05,
                                         "contact_and_return_tolerance_m": 0.03},
        "semantic_binding_digest": semantic_binding_digest(data["points"]),
        "trials": [], "success": False,
    }
    usd_path = _scene_usd(data, args.asset_root)
    evidence["author_usd"] = str(usd_path)
    evidence["author_usd_sha256_before"] = _sha256(usd_path)
    evidence["map_hashes_before"] = _map_hashes(data["root"])
    selection = _make_selection(starts[0], args.controller_profile)
    config = SimulatorLaunchConfig(
        env="pegasus_drones", resolved_env_name="pegasus_drones", selection_data=selection,
        device=args.device, headless=args.headless, enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True, aerial_sensor_backend="disabled",
        env_cfg_hook=_scene_hook(usd_path, high, args.box_size),
        app_launcher_args={"headless": args.headless, "device": args.device,
                           "fast_shutdown": False, "livestream": 0,
                           "width": 1600, "height": 1000,
                           "kit_args": "--/app/window/title=Room06-Iris-Semantic-Transport"},
    )
    with _launcher_display_mode(args.headless), open_simulator_session(config) as session:
        env = session.base_env
        robot_id = session.possible_agents[0]
        robot = env.scene[robot_id]
        box = env.scene["semantic_transport_box"]
        controller = env.cfg.controllers[robot_id]
        if isinstance(controller, (list, tuple)):
            controller = controller[0]
        controller_module = sys.modules[type(controller).__module__]
        controller_directory = Path(controller_module.__file__).resolve().parent
        evidence["controller"] = {
            "class": type(controller).__module__ + "." + type(controller).__name__,
            "configuration": {name: _jsonable(getattr(controller, name)) for name in (
                "robot_type", "control_mode", "command_name", "mass", "gravity",
                "position_gain", "velocity_gain", "integral_gain", "integral_limit",
                "attitude_gain", "angular_rate_gain", "rotor_positions",
                "rotor_constants", "min_rotor_velocities", "max_rotor_velocities")},
            "source_hashes": {str(path): _sha256(path) for path in
                              sorted(controller_directory.glob("*.py"))},
            "target_submission": "env.set_command(robot_id, goal_position, world_xyz)",
        }
        _write_json(output / "evidence.json", evidence)
        import omni.usd
        stage = omni.usd.get_context().get_stage()
        robot_body = f"/World/envs/env_0/{robot_id}/body"
        box_path = "/World/envs/env_0/semantic_transport_box"
        actions = {robot_id: torch.zeros((1, 3), device=session.device)}
        zero = torch.zeros((1, 6), dtype=torch.float32, device=session.device)
        high_center = (high[0], high[1], high[2] + args.box_size / 2.0)
        low_center = (low[0], low[1], low[2] + args.box_size / 2.0)
        gui_state = None if args.headless else _setup_gui(
            stage, high=high, low=low,
            box_size=args.box_size, scene_name=args.scene, trials=args.trials)
        if gui_state is not None:
            print("ROOM06_GUI_READY: Room06-Iris-Semantic-Transport", flush=True)

        def show_phase(text):
            print(f"ROOM06: {text}", flush=True)
            if gui_state is not None:
                gui_state["status"].text = text

        def sim_time() -> float:
            return float(env.sim.current_time)

        def world_seq() -> int:
            return int(getattr(env, "common_step_counter", env.sim.current_time_step_index))

        def pose_reader(_robot=robot_id):
            del _robot
            pose = read_aerial_pose(env, robot_id)
            w, x, y, z = pose.orientation_wxyz
            yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
            return NavigationPose(pose.position_m, yaw)

        def velocity_reader(_robot=robot_id):
            del _robot
            data_row = env.scene[robot_id].data
            linear = [float(v) for v in data_row.root_lin_vel_w[0].tolist()]
            angular = [float(v) for v in data_row.root_ang_vel_w[0].tolist()]
            return tuple(linear + angular)

        def write_goal(goal):
            env.set_command(robot_id, "goal_position",
                            torch.tensor([list(goal)], dtype=torch.float32, device=env.device))

        nav_adapter = WorldPositionNavigationAdapter(
            robot_id, pose_reader=pose_reader, command_sink=lambda cmd: write_goal(cmd.payload["target_pose"]),
            sequence_reader=world_seq, time_reader=sim_time, velocity_reader=velocity_reader,
            profile=_room06_aerial_profile())

        topology = FrozenNavigationTopology(data["topology"], scene_key=args.scene,
                                            source_digests=data["topology"].get("source", {}))

        pick_backend = None

        def advance_one_tick():
            if not session.simulation_app.is_running() or (
                    gui_state is not None and not gui_state["window"].visible):
                raise RuntimeError("GUI closed by user")
            env.step(actions)
            if pick_backend is not None and world_seq() % 250 == 0:
                record = pick_backend.execution_record()
                if record["phase"] not in {"idle", "holding"}:
                    progress = {"simulation_time_s": sim_time(), "execution": record,
                                "velocity": velocity_reader(),
                                "controller_goal": _jsonable(env.get_command(robot_id, "goal_position")),
                                "rotor_command": _jsonable(env.actions_dict.get(robot_id))}
                    print("ROOM06_MANIPULATION: " + json.dumps(_jsonable(progress)), flush=True)
                    _write_json(output / "manipulation-progress.json", progress)

        pose_reader_live = LivePhysxPoseReader(env)
        def reset_state(start):
            # Public reset clears controller state; its spawn default selects
            # the next trial's authored aerial node, without warm-up steps.
            robot.data.default_root_state[0, :3] = torch.tensor(
                start["position_xyz"], dtype=torch.float32, device=env.device)
            env.reset()
            box_pose = torch.tensor([[*high_center, 1.0, 0.0, 0.0, 0.0]],
                                    dtype=torch.float32, device=env.device)
            box.write_root_pose_to_sim(box_pose)
            box.write_root_velocity_to_sim(zero)
            write_goal(start["position_xyz"])

        def stop_motion():
            # The backend stops writing before the executor retires its lease.
            # Success retains its final target; cancellation has already
            # submitted a measured hover target through the same writer.
            return True

        def object_pose(path):
            return pose_reader_live(path)

        return_targets = {}

        def make_skills():
            nav = NavigationExecutor(topology=topology, simulation_time=sim_time,
                                     semantic_points=data["points"])
            nav.register_adapter(nav_adapter)
            backend = DronePickPlaceAdapter(
                robot_id, robot_pose=lambda: read_aerial_pose(env, robot_id), write_goal=write_goal,
                stop_motion=stop_motion, simulation_time=sim_time, robot_type="PegasusIris",
                align_xy_tolerance_m=0.08, align_z_tolerance_m=0.05,
                return_target_reader=lambda command: return_targets[command.request_id])
            executor = PickPlaceExecutor(advance=advance_one_tick, simulation_time=sim_time,
                                         resources=nav.resources,
                                         physical_object_experiment_authorization="action_only_pick_place")
            executor.register_adapter(backend)
            return nav, executor, backend

        def navigate(target_ref: str, label: str) -> tuple[bool, dict[str, Any]]:
            seq = world_seq()
            params = {"intent": "start", "world_state_seq": seq,
                      "route_graph_digest": topology.route_graph_digest,
                      "semantic_binding_digest": semantic_binding_digest(data["points"])}
            req_id = f"trial-{len(evidence['trials'])}-{label}"
            request = SkillRequest(req_id, NODE_ROUTE_SKILL_ID, robot_id, target_ref=target_ref,
                                   task_id=req_id, parameters=params)
            requests = [request.to_mapping()]
            response = nav.dispatch(request)
            last = response
            first_tick = True
            for step_index in range(args.max_navigation_steps):
                if step_index % 250 == 0:
                    # Diagnostic only: no extra physics step or control write.
                    progress = {"phase": label, "step": step_index,
                                "simulation_time_s": sim_time(),
                                "position_m": pose_reader().position_m,
                                "velocity": velocity_reader(),
                                "navigation_state": dict(last.state),
                                "route_selection": last.private_evidence.get("route_selection")}
                    print("ROOM06_NAV: " + json.dumps(_jsonable(progress)), flush=True)
                    _write_json(output / "navigation-progress.json", progress)
                if not nav.active_task_ids:
                    break
                # Once the provider has reached its endpoint the executor is
                # in its stop-confirmation phase; call after_step directly
                # until the lease is retired instead of preparing a new tick.
                if last.state.get("status") in {"failed", "cancelled"}:
                    break
                if last.state.get("status") == "completed":
                    advance_one_tick()
                    last = nav.after_step(req_id)
                    continue
                seq = world_seq()
                request = SkillRequest(req_id, NODE_ROUTE_SKILL_ID, robot_id, target_ref=target_ref,
                                       task_id=req_id,
                                       parameters={**params, "intent": "start" if first_tick else "continue",
                                                   "world_state_seq": seq})
                requests.append(request.to_mapping())
                last = nav.prepare(request)
                first_tick = False
                if last.state.get("status") in {"failed", "cancelled"}:
                    break
                if not nav.active_task_ids:
                    break
                if nav.needs_host_tick(req_id):
                    advance_one_tick()
                    last = nav.after_step(req_id)
                if last.state.get("status") in {"failed", "cancelled"} and not nav.active_task_ids:
                    break
            else:
                try:
                    last = nav.cancel(req_id, "navigation step limit exceeded")
                except Exception:
                    pass
            ok = last.state.get("status") == "completed" and bool(last.state.get("stop_confirmed"))
            return ok, {"requests": requests, "response": _jsonable(last),
                         "private_evidence": _jsonable(getattr(last, "private_evidence", {}))}

        for trial_index, start in enumerate(starts, 1):
            trial: dict[str, Any] = {"trial": trial_index, "start_node": deepcopy(start),
                                     "pick": None, "place": None, "navigation": [], "success": False}
            evidence["trials"].append(trial)
            show_phase(f"Trial {trial_index}/{args.trials}: resetting Iris and cube...")
            try:
                reset_state(start)
                nav, pick_place, pick_backend = make_skills()
                show_phase(f"Trial {trial_index}/{args.trials}: navigating to high point...")
                high_ok, high_nav = navigate(data["high"]["id"], "to-high")
                high_nav["metrics"] = navigation_metrics(high_nav)
                trial["navigation"].append({"phase": "to_high", "response": high_nav})
                _write_json(output / "evidence.json", evidence)
                if not high_ok:
                    reason = high_nav["private_evidence"].get("failure", {}).get("reason", "")
                    raise RuntimeError("navigation to high semantic point failed: " + reason)
                high_return = navigation_return_target(high_nav, data["high"], data["node_by_id"])
                show_phase(f"Trial {trial_index}/{args.trials}: pick...")
                live_box = pose_reader_live(box_path)
                pick_request = _manipulation_request(
                    f"trial-{trial_index}-pick", "pick", robot_id=robot_id,
                    object_path=box_path, target_world=live_box.position_m,
                    base_pose=read_aerial_pose(env, robot_id), sequence=world_seq(),
                    simulation_time=sim_time())
                return_targets[pick_request.request_id] = high_return["position_xyz"]
                trial["pick"] = {"target_center": list(high_center), "return_node": high_return,
                                 "object_position_before": _pose_to_json(live_box)}
                pick_ok = pick_place.execute(pick_request)
                trial["pick"].update({"request": pick_request.to_mapping(), "success": pick_ok,
                                      "feedback": _jsonable(pick_backend.feedback()),
                                      "execution": pick_backend.execution_record(),
                                      "object_position": _pose_to_json(pose_reader_live(box_path))})
                _write_json(output / "evidence.json", evidence)
                if not pick_ok:
                    raise RuntimeError("pick Skill failed: " + pick_backend.feedback().reason)
                show_phase(f"Trial {trial_index}/{args.trials}: pick succeeded")
                show_phase(f"Trial {trial_index}/{args.trials}: navigating to low point...")
                low_ok, low_nav = navigate(data["low"]["id"], "to-low")
                low_nav["metrics"] = navigation_metrics(low_nav)
                trial["navigation"].append({"phase": "to_low", "response": low_nav})
                if not low_ok:
                    reason = low_nav["private_evidence"].get("failure", {}).get("reason", "")
                    raise RuntimeError("navigation to low semantic point failed: " + reason)
                low_return = navigation_return_target(low_nav, data["low"], data["node_by_id"])
                show_phase(f"Trial {trial_index}/{args.trials}: place...")
                place_request = _manipulation_request(
                    f"trial-{trial_index}-place", "place", robot_id=robot_id,
                    object_path=box_path, target_world=low_center,
                    base_pose=read_aerial_pose(env, robot_id), sequence=world_seq(),
                    simulation_time=sim_time())
                return_targets[place_request.request_id] = low_return["position_xyz"]
                place_ok = pick_place.execute(place_request)
                trial["place"] = {"request": place_request.to_mapping(), "success": place_ok,
                                  "return_node": low_return,
                                  "feedback": _jsonable(pick_backend.feedback()),
                                  "execution": pick_backend.execution_record(),
                                  "object_position": _pose_to_json(pose_reader_live(box_path)),
                                  "expected_center": list(low_center)}
                if not place_ok:
                    raise RuntimeError("place Skill failed: " + pick_backend.feedback().reason)
                trial["success"] = True
                show_phase(f"Trial {trial_index}/{args.trials} completed")
            except Exception as exc:
                show_phase(f"Trial {trial_index}/{args.trials} stopped: {exc}")
                trial["error"] = {"type": type(exc).__name__, "message": str(exc)}
                _write_json(output / "evidence.json", evidence)
                break
            finally:
                trial["final_robot_position"] = _pose_to_json(read_aerial_pose(env, robot_id))
                trial["final_robot_velocity"] = list(velocity_reader())
                trial["final_controller_goal"] = _jsonable(env.get_command(robot_id, "goal_position"))
                trial["final_object_position"] = _pose_to_json(pose_reader_live(box_path))
                _write_json(output / "evidence.json", evidence)
        evidence["success"] = len(evidence["trials"]) == args.trials and all(t["success"] for t in evidence["trials"])
        evidence["author_usd_sha256_after"] = _sha256(usd_path)
        evidence["author_usd_unchanged"] = (
            evidence["author_usd_sha256_before"] == evidence["author_usd_sha256_after"])
        evidence["map_hashes_after"] = _map_hashes(data["root"])
        evidence["semantic_map_unchanged"] = evidence["map_hashes_before"] == evidence["map_hashes_after"]
        _write_json(output / "evidence.json", evidence)
        if gui_state is not None:
            outcome = "Completed" if evidence["success"] else "Stopped"
            reason = evidence["trials"][-1].get("error", {}).get("message", "")
            gui_state["status"].text = (
                f"{outcome}: {sum(1 for t in evidence['trials'] if t['success'])}/{args.trials} trials succeeded\n"
                f"{reason}\nEvidence: {output / 'evidence.json'}")
            # Keep Kit's window alive after the finite demo.  The previous
            # implementation exited the session immediately, making the GUI
            # appear to flash or never become visible to the user.
            # Freeze the final physical state while processing camera/UI events.
            # Raw Kit updates on a playing timeline would advance physics with
            # stale control commands after the task has ended.
            env.sim.pause()
            while (not args.exit_after_trials and session.simulation_app.is_running()
                   and gui_state["window"].visible):
                session.simulation_app.update()
                time.sleep(0.02)
            # Release this demo's native UI objects while omni.ui/Kit are
            # alive. Holding them until run_isaac returns destroys them after
            # SimulationApp.close(), which can segfault during Python cleanup.
            gui_state["window"].destroy()
            gui_state.clear()
            gui_state = None
        try:
            pose_reader_live.close()
        except Exception:
            pass
    _write_json(output / "evidence.json", evidence)
    evidence["author_usd_sha256_after"] = _sha256(usd_path)
    evidence["author_usd_unchanged"] = (
        evidence["author_usd_sha256_before"] == evidence["author_usd_sha256_after"])
    _write_json(output / "evidence.json", evidence)
    return evidence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", default="living_room_06")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0,
                        help="compatibility only; fixed start nodes ignore this value")
    parser.add_argument("--controller-profile", choices=tuple(CONTROLLER_PROFILES), default="baseline",
                        help="Iris Pegasus configuration profile for room06")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--exit-after-trials", action="store_true",
                        help="close the GUI after recording the finite trial run")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--box-size", type=float, default=0.10)
    parser.add_argument("--max-navigation-steps", type=int, default=0)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--check", action="store_true", help="validate map data without starting Isaac Sim")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.trials <= 5 or args.box_size <= 0:
        raise SystemExit("--trials must be 1..5 and --box-size must be positive")
    print("ROOM06: fixed start nodes; --seed is accepted but ignored", file=sys.stderr)
    if not args.check:
        running = _other_demo_processes()
        if running:
            details = "; ".join(f"PID {pid}: {command}" for pid, command in running)
            raise SystemExit(
                "Another room06 Iris demo is already running. Close its Isaac window "
                "or press Ctrl-C in that terminal before starting a new instance. "
                f"{details}"
            )
        _check_cuda_memory(args.device)
    if args.max_navigation_steps <= 0:
        args.max_navigation_steps = 24000
    args.output_dir = args.output_dir.expanduser().resolve()
    data = load_inputs(args.scene, args.asset_root)
    if args.check:
        result = offline_check(args)
        print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
        return 0
    result = run_isaac(args, data)
    print(json.dumps({"success": result["success"], "evidence": str(args.output_dir / "evidence.json")},
                     ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
