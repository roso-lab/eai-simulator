from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import MISSING, dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator


KEYBOARD_CMD_VEL_GOAL_STEP_SCALE = 0.2
_ORSUS_RTX_PRELOAD_KIT_ARGS = (
    "--enable omni.usd.schema.omni_sensors --enable isaacsim.sensors.rtx"
)

# Scout's four fixed wheels resist lateral motion, so its effective skid-steer
# track is much wider than the 0.498 m geometric controller value. Factory-floor
# pure-yaw pulses at two wheel speeds expose a sizable breakaway threshold; a
# 2.9 scale gives about 0.5 rad/s actual yaw for a 0.5 rad/s ROS command without
# changing the downloaded controller asset.
SCOUT_CMD_VEL_ANGULAR_SCALE = 2.9


def _load_interface_cli():
    _ensure_repo_sources_on_path()
    from EAI.interface_catalog.cli import main as interface_cli_main

    return interface_cli_main


def _dispatch_interface_cli(argv: list[str]) -> int | None:
    if not argv or argv[0] != "interfaces":
        return None
    return _load_interface_cli()(argv[1:])


def _runtime_interface_snapshot_path() -> Path:
    return _repo_root() / "tmp" / "runtime_interfaces.json"


def _tensor_vector(value: Any) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [float(item) for item in value]


def _yaw_from_wxyz(rotation: list[float]) -> float:
    w, x, y, z = rotation
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _robot_world_pose(base_env: Any | None, robot_name: str) -> dict[str, Any] | None:
    articulations = getattr(getattr(base_env, "scene", None), "articulations", {})
    robot = articulations.get(robot_name)
    data = getattr(robot, "data", None)
    if data is None or not hasattr(data, "root_pos_w") or not hasattr(data, "root_quat_w"):
        return None
    position = _tensor_vector(data.root_pos_w[0])
    rotation = _tensor_vector(data.root_quat_w[0])
    if len(position) != 3 or len(rotation) != 4:
        return None
    return {
        "position": position,
        "rotation": rotation,
        "yaw": _yaw_from_wxyz(rotation),
    }


def _runtime_robot_payload(
    selection_data: dict[str, Any] | None,
    possible_agents: list[str],
    *,
    base_env: Any | None = None,
) -> list[dict[str, Any]]:
    selected = list((selection_data or {}).get("robots", []))
    if selected:
        robots = [
            {
                "instance_name": (
                    possible_agents[index - 1]
                    if index <= len(possible_agents)
                    else f"{robot.get('type', 'robot')}_{index}"
                ),
                "robot_type": str(robot.get("type", "robot")),
                "attachments": [str(item.get("type")) for item in robot.get("attachments", [])],
            }
            for index, robot in enumerate(selected, start=1)
        ]
    else:
        robots = [
            {"instance_name": name, "robot_type": name.rsplit("_", 1)[0], "attachments": []}
            for name in possible_agents
        ]
    for robot in robots:
        pose = _robot_world_pose(base_env, robot["instance_name"])
        if pose is not None:
            robot["world_pose"] = pose
    return robots


def _publish_runtime_interface_snapshot(
    *,
    env_name: str,
    selection_data: dict[str, Any] | None,
    possible_agents: list[str],
    cmd_vel_agents: set[str],
    base_env: Any | None = None,
) -> tuple[Path, dict[str, Any]]:
    _ensure_repo_sources_on_path()
    from EAI.interface_catalog.loader import load_catalog
    from EAI.interface_catalog.query import resolve_scene_interfaces
    from EAI.interface_catalog.snapshot import build_snapshot, write_snapshot

    catalog = load_catalog()
    resolved = resolve_scene_interfaces(
        catalog,
        selection_data,
        env_name=env_name,
        possible_agents=possible_agents,
    )
    interfaces = []
    for entry in resolved:
        if entry.endpoint.endswith("/cmd_vel") and entry.instance_name not in cmd_vel_agents:
            continue
        interfaces.append(entry.to_dict())
    snapshot = build_snapshot(
        env_name=env_name,
        scene_key=str((selection_data or {}).get("scene_key", "")) or None,
        interfaces=interfaces,
        robots=_runtime_robot_payload(selection_data, possible_agents, base_env=base_env),
    )
    path = _runtime_interface_snapshot_path()
    write_snapshot(path, snapshot)
    return path, snapshot


def _runtime_device_for_env(
    env_name: str,
    selection_data: dict[str, Any] | None,
    requested_device: str,
) -> str:
    """Use CPU PhysX for animated humans because Isaac Sim 5.1 GPU pose writes crash."""
    has_human = False
    if selection_data:
        has_human = has_human or any(
            str(robot.get("type", "")).strip().lower() == "human"
            for robot in selection_data.get("robots", [])
            if isinstance(robot, dict)
        )
    if has_human and str(requested_device).startswith("cuda"):
        return "cpu"
    return str(requested_device)


def _selection_requires_omnigraph(selection_data: dict[str, Any] | None) -> bool:
    if not selection_data:
        return False
    graph_attachments = {
        "orsus", "realsense_d455", "lidar", "camera", "ur5", "z1",
        "navigation_io",
    }
    aerial_types = {"cf2x", "iris", "pegasus"}
    return any(
        isinstance(robot, dict)
        and (
            str(robot.get("type", "")).strip().lower() in aerial_types
            or any(
                isinstance(attachment, dict)
                and str(attachment.get("type", "")).strip().lower() in graph_attachments
                for attachment in robot.get("attachments", ())
            )
        )
        for robot in selection_data.get("robots", ())
    )


def _selection_has_attachment(
    selection_data: dict[str, Any] | None,
    attachment_type: str,
) -> bool:
    if not selection_data:
        return False
    expected = str(attachment_type).strip().lower()
    return any(
        isinstance(robot, dict)
        and any(
            isinstance(attachment, dict)
            and str(attachment.get("type", "")).strip().lower() == expected
            for attachment in robot.get("attachments", ())
        )
        for robot in selection_data.get("robots", ())
    )


def _validate_orsus_lidar_exclusivity(selection_data: dict[str, Any] | None) -> None:
    if not selection_data:
        return
    for index, robot in enumerate(selection_data.get("robots", ()), start=1):
        if not isinstance(robot, dict):
            continue
        attachments = {
            str(attachment.get("type", "")).strip().lower()
            for attachment in robot.get("attachments", ())
            if isinstance(attachment, dict)
        }
        if {"orsus", "lidar"} <= attachments:
            robot_type = str(robot.get("type", "")).strip().lower() or "unknown"
            raise ValueError(
                f"Robot {index} ('{robot_type}') cannot attach both Orsus and LiDAR."
            )


def _sensor_scene_single_env_reasons(selection_data: dict[str, Any] | None) -> tuple[str, ...]:
    if not selection_data:
        return ()
    reasons = []
    aerial_types = {"cf2x", "iris", "pegasus"}
    sensor_tools = {"camera", "navigation_io"}
    for index, robot in enumerate(selection_data.get("robots", ()), start=1):
        if not isinstance(robot, dict):
            continue
        robot_type = str(robot.get("type", "")).strip().lower()
        attachments = {
            str(attachment.get("type", "")).strip().lower()
            for attachment in robot.get("attachments", ())
            if isinstance(attachment, dict)
        }
        enabled_tools = attachments & sensor_tools
        if robot_type in aerial_types:
            tools = ", ".join(sorted(enabled_tools)) or "default sensors"
            reasons.append(f"robot {index} ({robot_type}: {tools})")
        elif robot_type == "mushr_v2" and "camera" in attachments:
            # MuSHR's built-in front camera uses the aerial sensor suite's
            # single-environment render product, unlike the Orsus stereo path.
            reasons.append(f"robot {index} (mushr_v2: camera)")
        elif "orsus" in attachments and enabled_tools:
            tools = ", ".join(sorted(enabled_tools))
            reasons.append(f"robot {index} ({robot_type or 'unknown'}: {tools})")
        elif "realsense_d455" in attachments and enabled_tools:
            # D455 与 Orsus 一样按机器人实例命名空间发布；多环境下实例名
            # 相同会导致话题冲突，因此同样限制单环境。
            tools = ", ".join(sorted(enabled_tools))
            reasons.append(f"robot {index} ({robot_type or 'unknown'}: {tools})")
    return tuple(reasons)


def _validate_sensor_scene_num_envs(
    selection_data: dict[str, Any] | None,
    num_envs: int,
) -> None:
    if num_envs == 1:
        return
    reasons = _sensor_scene_single_env_reasons(selection_data)
    if reasons:
        raise ValueError(
            "Aerial/Orsus/RealSense D455 sensor resources support exactly one environment; "
            f"got num_envs={num_envs} for {', '.join(reasons)}. Use --num_envs 1."
        )


_INOTIFY_MINIMUMS = {
    "max_user_watches": 524288,
    "max_user_instances": 1024,
    "max_queued_events": 32768,
}


def _read_inotify_limits(root: Path = Path("/proc/sys/fs/inotify")) -> dict[str, int]:
    return {
        name: int((root / name).read_text(encoding="utf-8").strip())
        for name in _INOTIFY_MINIMUMS
    }


def _inotify_limit_warning(limits: dict[str, int]) -> str | None:
    low = [
        f"{name}={limits.get(name, 0)} (need >= {minimum})"
        for name, minimum in _INOTIFY_MINIMUMS.items()
        if limits.get(name, 0) < minimum
    ]
    if not low:
        return None
    return (
        "[EAI Simulator] Warning: inotify limits are below the supported Isaac Sim minimums: "
        + ", ".join(low)
        + ". Run sudo tools/setup/configure_inotify_limits.sh once."
    )


def _warn_if_inotify_limits_are_low() -> None:
    try:
        warning = _inotify_limit_warning(_read_inotify_limits())
    except (OSError, ValueError) as exc:
        print(f"[EAI Simulator] Warning: could not read inotify limits: {exc}")
        return
    if warning:
        print(warning)


def _enable_required_selection_extensions(selection_data: dict[str, Any] | None) -> None:
    if _selection_requires_omnigraph(selection_data):
        _enable_isaac_extension("omni.graph")
    if _selection_has_attachment(selection_data, "orsus"):
        _enable_isaac_extension("isaacsim.sensors.rtx")


def _silence_simulation_manager_time_log_spam() -> None:
    """Silence Isaac Sim 5.1's noisy simulation-time interpolation warnings.

    The simulation manager keeps a 31-sample time-interpolation buffer
    (designed for ~60Hz: about 0.5s of history). With EAI's 200Hz physics
    step, every sensor render product's SDG pipeline queries the monotonic
    simulation time for the *current* frame before the matching physics-step
    samples exist, so the plugin floods:

        "No adjacent samples found for interpolation at time N/30"
        "getSimulationTimeMonotonicAtTime: no data found for time N/30,
         returning current sim time"

    The fallback (returning the current simulation time) is correct, so these
    are purely cosmetic. Raise that plugin channel's log threshold to ERROR so
    only real failures remain visible.

    Notes on the carb binding (verified against the shipped ``_carb`` module):

    - ``set_level_threshold_for_source(source, behavior, level)`` requires the
      ``carb.logging.LogSettingBehavior`` argument (values ``INHERIT``/
      ``OVERRIDE``); a 2-arg call raises ``TypeError``, and ``INHERIT``
      silently ignores the level.
    - Per-source settings are keyed by the client name the plugin registers
      (``isaacsim.core.simulation_manager.plugin``). We additionally cover the
      ``__FILE__`` spellings in case a build matches on the file name, and
      re-apply after the stage exists because plugins may (re-)register their
      logging source during scene setup.

    ``EAI_DEBUG_NO_LOG_SILENCE=1`` disables this (diagnostics only).
    """
    if os.environ.get("EAI_DEBUG_NO_LOG_SILENCE") == "1":
        return
    try:
        import carb

        logging_iface = carb.logging.acquire_logging()
        override = carb.logging.LogSettingBehavior.OVERRIDE
        source_keys = (
            "isaacsim.core.simulation_manager.plugin",
            "TimeSampleStorage.cpp",
            "PluginInterface.cpp",
            "UsdNoticeListener.cpp",
            "../../../source/extensions/isaacsim.core.simulation_manager/plugins/isaacsim.core.simulation_manager/TimeSampleStorage.cpp",
            "../../../source/extensions/isaacsim.core.simulation_manager/plugins/isaacsim.core.simulation_manager/PluginInterface.cpp",
        )
        for source_key in source_keys:
            logging_iface.set_level_threshold_for_source(source_key, override, carb.logging.LEVEL_ERROR)

        # 以下来源只有已知的无害告警（第三方插件内部），提升到 ERROR 保持日志干净：
        # - camera_info_utils: D455 相机未作者化畸变模型时，fallback 到 plumb_bob
        #   零畸变 + 强制 fy:=fx（渲染器本来就按方形像素渲染），纯提示性告警。
        # - omni.timeline.plugin: 某内置扩展仍直接使用 ITimeline 回调（弃用提示）。
        cosmetic_sources = (
            "isaacsim.ros2.bridge.impl.camera_info_utils",
            "omni.timeline.plugin",
        )
        for source_key in cosmetic_sources:
            if source_key.startswith("isaacsim.ros2.bridge"):
                try:
                    import importlib

                    importlib.import_module(source_key)
                except Exception:
                    pass
            logging_iface.set_level_threshold_for_source(source_key, override, carb.logging.LEVEL_ERROR)

        print("[EAI Simulator] Suppressed isaacsim.core.simulation_manager.plugin warning spam.")
    except Exception as exc:  # pragma: no cover - best-effort silencing
        print(f"[EAI Simulator] Warning: failed to silence sim-time log spam: {exc}")


def _prepare_replicator_for_app_close() -> None:
    """Prevent Replicator shutdown from stopping Isaac Lab's live timeline."""
    try:
        import omni.replicator.core as rep

        rep.orchestrator.set_capture_on_play(False)
    except Exception as exc:
        print(f"[EAI Simulator] Warning: Replicator close preparation failed: {exc}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _module_is_from_repo(module: object, repo_root: Path) -> bool:
    raw_file = getattr(module, "__file__", None)
    if raw_file:
        try:
            return Path(raw_file).resolve().is_relative_to(repo_root)
        except OSError:
            return False
    raw_path = getattr(module, "__path__", None)
    if raw_path:
        for item in raw_path:
            try:
                if Path(item).resolve().is_relative_to(repo_root):
                    return True
            except OSError:
                continue
    return False


def _remove_external_repo_packages(repo_root: Path) -> None:
    for package_name in ("EAI", "EAI_assets", "EAI_hmrs"):
        module = sys.modules.get(package_name)
        if module is None or _module_is_from_repo(module, repo_root):
            continue
        for module_name in tuple(sys.modules):
            if module_name == package_name or module_name.startswith(f"{package_name}."):
                sys.modules.pop(module_name, None)


def _ensure_repo_sources_on_path() -> None:
    repo_root = _repo_root()
    _remove_external_repo_packages(repo_root)
    for rel in ("source/EAI", "source/EAI_assets", "source/EAI_hmrs", "source/EAI_env_diy"):
        path = repo_root / rel
        if path.is_dir():
            path_text = str(path)
            if path_text in sys.path:
                sys.path.remove(path_text)
            sys.path.insert(0, path_text)

def _load_asset_resolver():
    module_path = _repo_root() / "source" / "EAI_assets" / "EAI_assets" / "asset_resolver.py"
    spec = importlib.util.spec_from_file_location("eai_simulator_asset_resolver", module_path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"Could not load asset resolver from {module_path}")
    spec.loader.exec_module(module)
    return module


def find_isaac_ros_bridge_path(ros_distro: str | None = None) -> str | None:
    _ensure_repo_sources_on_path()
    from EAI_assets.ros_config import find_isaac_ros_bridge_path as find_bridge

    return find_bridge(ros_distro)


def configure_isaac_ros_bridge_env(ros_distro: str | None = None) -> str | None:
    _ensure_repo_sources_on_path()
    from EAI_assets.ros_config import configure_ros_env

    bridge_path = configure_ros_env(ros_distro)
    if bridge_path is None:
        print("[EAI Simulator] Warning: Could not find Isaac ROS Bridge path before launch.")
    return bridge_path


def _enable_isaac_extension(extension_name: str) -> None:
    for module_name in (
        "isaacsim.core.experimental.utils.app",
        "isaacsim.core.utils.extensions",
    ):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        result = module.enable_extension(extension_name)
        if result is False:
            raise RuntimeError(f"Kit could not enable extension '{extension_name}'.")
        return
    raise ModuleNotFoundError("No Isaac Sim extension enable helper is available")


@contextmanager
def _managed_env_diy_extension(
    extension_manager,
    extension_root: str | Path,
    *,
    direct_path_type,
):
    """Register and run the Env DIY extension through Kit's standard lifecycle."""
    extension_id = "EAI_env_diy"
    path = str(Path(extension_root))
    extension_manager.add_path(path, direct_path_type)
    try:
        extension_manager.process_and_apply_all_changes()
        extension_manager.set_extension_enabled_immediate(extension_id, True)
        if not extension_manager.is_extension_enabled(extension_id):
            raise RuntimeError(f"Kit could not enable extension '{extension_id}'.")
        yield
    finally:
        try:
            if extension_manager.is_extension_enabled(extension_id):
                extension_manager.set_extension_enabled_immediate(extension_id, False)
        finally:
            extension_manager.remove_path(path)


def _base_parser(*, include_device: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EAI simulator launcher. Builds environments from JSON or Env DIY authoring.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        conflict_handler="resolve",
        epilog="""
Examples:
  python simulator.py --env=EAI-Factory-v0
  python simulator.py --env=factory_go2_nav2
  python simulator.py --diy-3d
        """,
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        help=(
            "JSON environment name from source/EAI_hmrs/EAI_hmrs/envs, without the .json suffix. "
            "If omitted, Env DIY selection is shown."
        ),
    )
    parser.add_argument(
        "--diy-3d",
        action="store_true",
        help="Open the Isaac Sim 3D Env DIY editor before starting simulation.",
    )
    parser.add_argument("--num_envs", type=int, default=1, help="Number of environments to simulate.")
    if include_device:
        parser.add_argument("--device", type=str, default="cuda:0", help="Simulation device.")
    parser.add_argument(
        "--preflight-output",
        type=str,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--enable-cmd-vel-bridge",
        "--enable-nav2-bridge",
        dest="enable_cmd_vel_bridge",
        action="store_true",
        help="Enable Isaac-side /<robot>/cmd_vel subscribers for all robots. The Nav2-named flag is deprecated.",
    )
    parser.add_argument(
        "--ml_framework",
        type=str,
        default="torch",
        choices=("torch", "jax", "jax-numpy"),
        help="ML framework passed through to Isaac Lab.",
    )
    parser.add_argument("--seed", type=int, default=0, help="Environment seed.")
    parser.add_argument(
        "--interfaces-menu",
        action="store_true",
        help="Open the interface query menu after the simulator scene starts.",
    )
    return parser


def _run_preflight_subprocess(cmd: list[str], env: dict[str, str], *, run=subprocess.run) -> None:
    try:
        run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        print(
            "[EAI Simulator] Asset preflight process failed before returning a diagnostic. "
            "Review the child-process output above.",
            file=sys.stderr,
        )
        raise SystemExit(exc.returncode) from None


def _is_asset_preflight_failure(exc: BaseException) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    class_names = {candidate.__name__ for candidate in type(exc).__mro__}
    return bool(class_names & {"AssetDownloadError", "AssetIntegrityError"})


def _asset_preflight_failure(exc: BaseException) -> dict[str, str]:
    kind = "missing"
    class_names = {candidate.__name__ for candidate in type(exc).__mro__}
    for candidate_kind, class_name in (
        ("access", "AssetDownloadAccessError"),
        ("network", "AssetDownloadNetworkError"),
        ("integrity", "AssetIntegrityError"),
        ("download", "AssetDownloadError"),
    ):
        if class_name in class_names:
            kind = candidate_kind
            break
    return {
        "kind": kind,
        "exception_type": type(exc).__name__,
        "message": str(exc).strip() or repr(exc),
    }


def _report_asset_preflight_failure(failure: dict[str, Any], *, stream=None) -> None:
    output = stream or sys.stderr
    kind = str(failure.get("kind") or "unknown")
    message = str(failure.get("message") or "No diagnostic message was returned.")
    print("", file=output)
    print("[EAI Simulator] Asset preparation failed / 资产准备失败", file=output)
    print(f"[EAI Simulator] Failure type / 错误类型: {kind}", file=output)
    print(message, file=output)
    print(
        "\n[EAI Simulator] The simulator was not started. Fix the issue above and run the same command again.\n"
        "[EAI Simulator] 仿真器尚未启动。请修复上述问题后重新执行同一命令。",
        file=output,
    )


def _read_preflight_payload(output_path: Path) -> dict[str, Any]:
    if not output_path.exists():
        print(
            "[EAI Simulator] Asset preflight ended without returning a result. "
            "The simulator was not started.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return json.loads(output_path.read_text(encoding="utf-8"))


def _handle_preflight_payload(
    payload: dict[str, Any],
    *,
    ensure_usd_assets=None,
    ensure_controller_assets=None,
) -> tuple[str, dict[str, Any] | None]:
    failure = payload.get("asset_error")
    if isinstance(failure, dict):
        _report_asset_preflight_failure(failure)
        raise SystemExit(1)
    if payload.get("startup_mode") == "diy-3d":
        raise Diy3dRequested
    if not payload.get("should_run", True):
        raise SystemExit(0)
    asset_resolver = _load_asset_resolver()
    ensure_usd_assets = ensure_usd_assets or asset_resolver.ensure_usd_assets_for_paths
    ensure_controller_assets = ensure_controller_assets or asset_resolver.ensure_controller_assets_for_paths
    try:
        ensure_usd_assets(payload.get("usd_paths", []))
        ensure_controller_assets(payload.get("controller_paths", []))
    except Exception as exc:
        if not _is_asset_preflight_failure(exc):
            raise
        _report_asset_preflight_failure(_asset_preflight_failure(exc))
        raise SystemExit(1) from None
    return payload["task_name"], payload.get("selection")


@dataclass(frozen=True)
class TaskSource:
    kind: str
    task_name: str
    saved_task: dict[str, Any] | None = None


@dataclass(frozen=True)
class TerminalCompletionResult:
    saved_task: dict[str, Any] | None
    should_run: bool
    back_to_controller: bool = False


@dataclass(frozen=True)
class SimulatorLaunchConfig:
    env: str
    num_envs: int = 1
    device: str = "cuda:0"
    seed: int = 0
    headless: bool = False
    enable_ros_bridge_extension: bool = True
    disable_orsus_ros_env: bool = False
    enable_cmd_vel_bridge: bool = False
    ml_framework: str = "torch"
    app_launcher_args: dict[str, Any] = field(default_factory=dict)
    resolved_env_name: str | None = None
    selection_data: dict[str, Any] | None = None
    env_cfg_hook: Callable[[Any], None] | None = None
    existing_simulation_app: Any | None = None


@dataclass
class SimulatorSession:
    simulation_app: Any
    env: Any
    base_env: Any
    env_cfg: Any
    env_name: str
    selection_data: dict[str, Any] | None
    possible_agents: list[str]
    num_envs: int
    device: str


@dataclass(frozen=True)
class TaskRequest:
    task_name: str | None
    selection: object | None = None
    saved_task: dict[str, Any] | None = None
    should_run: bool = True
    selection_data: dict[str, Any] | None = None


class BackToDiyMethod(Exception):
    """Raised when terminal DIY backs out to the method chooser."""


class Diy3dRequested(Exception):
    """Raised when the Env DIY method chooser requests the 3D editor."""


def _resolve_startup_mode(args: argparse.Namespace) -> str:
    if bool(getattr(args, "diy_3d", False)):
        if _requested_env_name(args):
            raise ValueError("--diy-3d cannot be combined with --env.")
        return "diy-3d"
    return "saved-env" if _requested_env_name(args) else "diy"


def _task_request_from_diy_3d_result(payload: dict[str, Any]) -> TaskRequest:
    status = str(payload.get("status", "")).strip().lower()
    action = str(payload.get("action", "")).strip().lower()
    selection_data = payload.get("selection")
    if status == "failed":
        raise RuntimeError(str(payload.get("error") or "Env DIY 3D authoring failed."))
    if status == "cancelled" or action == "cancel":
        return TaskRequest(task_name=None, should_run=False)
    if status != "completed":
        raise ValueError(f"Unknown DIY 3D result status '{status}'.")
    if action not in {"run", "save"}:
        raise ValueError(f"Unknown DIY 3D result action '{action}'.")
    if not isinstance(selection_data, dict):
        raise ValueError("Completed DIY 3D result must include a selection object.")
    return TaskRequest(
        task_name="EAI-Interactive-v0" if action == "run" else None,
        selection_data=selection_data,
        saved_task=payload.get("saved_task"),
        should_run=action == "run",
    )


def resolve_task_source(
    task_name: str,
    *,
    repo_root: Path,
) -> TaskSource:
    _ensure_repo_sources_on_path()
    from EAI.hmrs_env.env_diy.storage import load_task, saved_task_exists

    if saved_task_exists(task_name, repo_root=repo_root):
        return TaskSource("json", task_name, load_task(task_name, repo_root=repo_root))
    raise ValueError(f"Unknown env JSON '{task_name}'.")


def _diy_method_prompt_text() -> str:
    return (
        "\n未指定 --env，请选择 env 制定方式："
        "\n  1. 可视化窗口（Scenes → Robots → Payloads → Tools）"
        "\n  2. 终端快速（同样顺序，Payloads 先机械臂后传感器）"
        "\n  3. Isaac Sim 3D 编辑器（编辑真实位置、旋转与碰撞面）"
    )


def choose_diy_selection_before_preflight():
    repo_root = _repo_root()
    while True:
        print(_diy_method_prompt_text())
        raw = input("输入 1、2 或 3: ").strip()
        if raw == "1":
            return _choose_visual_diy(repo_root)
        if raw == "2":
            try:
                return _choose_terminal_diy(repo_root)
            except BackToDiyMethod:
                continue
        if raw == "3":
            raise Diy3dRequested
        print("请输入 1、2 或 3。")


def _requested_env_name(args: argparse.Namespace) -> str | None:
    return getattr(args, "env", None) or getattr(args, "task", None)


def _resolve_task_request_before_app(args: argparse.Namespace) -> TaskRequest:
    requested = _requested_env_name(args)
    if requested:
        return TaskRequest(task_name=requested)

    selection, saved_task_data, should_run = choose_diy_selection_before_preflight()
    if not should_run:
        return TaskRequest(
            task_name=None,
            selection=selection,
            saved_task=saved_task_data,
            should_run=False,
        )
    return TaskRequest(
        task_name="EAI-Interactive-v0",
        selection=selection,
        saved_task=saved_task_data,
        should_run=True,
    )


def _choose_visual_diy(repo_root: Path):
    output_path = repo_root / "tmp" / "task_diy_window_result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "EAI.hmrs_env.env_diy.webview_app",
        "--keyboard-preflight-output",
        str(output_path),
    ]
    env = os.environ.copy()
    source_root = str(repo_root / "source" / "EAI")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = source_root if not existing_pythonpath else os.pathsep.join((source_root, existing_pythonpath))
    if sys.platform.startswith("linux"):
        environment_lib = str(Path(sys.prefix) / "lib")
        existing_library_path = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = (
            environment_lib
            if not existing_library_path
            else os.pathsep.join((environment_lib, existing_library_path))
        )
    subprocess.run(cmd, check=True, cwd=repo_root, env=env)
    if not output_path.exists():
        raise SystemExit(0)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not payload.get("should_run", False):
        return None, payload.get("saved_task"), False
    _ensure_repo_sources_on_path()
    from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict

    saved_task = payload.get("saved_task")
    selection_data = payload.get("selection") or saved_task
    return interactive_selection_from_dict(selection_data), saved_task, True


def _choose_terminal_diy(repo_root: Path):
    _ensure_repo_sources_on_path()
    from EAI.hmrs_env.env_diy.flow import (
        TERMINAL_CONTROLLER_STEP,
        choose_terminal_interactive_selection,
        interactive_selection_to_dict,
    )

    selection = None
    resume_controller_step = False
    while True:
        selection = choose_terminal_interactive_selection(
            initial_selection=selection if resume_controller_step else None,
            allow_back_from_first=True,
            start_step=TERMINAL_CONTROLLER_STEP if resume_controller_step else 0,
        )
        if selection is None:
            raise BackToDiyMethod
        resume_controller_step = False
        selection_data = interactive_selection_to_dict(selection)
        result = _complete_terminal_selection(selection_data, repo_root)
        if result.back_to_controller:
            resume_controller_step = True
            continue
        return selection, result.saved_task, result.should_run


def _complete_terminal_selection(
    selection_data: dict[str, Any],
    repo_root: Path,
    *,
    input_func=input,
) -> TerminalCompletionResult:
    _ensure_repo_sources_on_path()
    from EAI.hmrs_env.env_diy.storage import save_task

    print("\n[完成] 保存与运行")
    print("-" * 72)
    print("  可以保存为可复用的 JSON env，并选择是否立即运行")
    saved_task = None
    step = "save"
    while True:
        if step == "save":
            save_choice = _ask_yes_no_or_back("保存此 env", default=True, input_func=input_func)
            if save_choice is None:
                return TerminalCompletionResult(None, False, back_to_controller=True)
            if not save_choice:
                saved_task = None
            step = "name" if save_choice else "execute"
            continue

        if step == "name":
            name = input_func("env 名称 (b 返回): ").strip()
            if not name:
                print("  ! env 名称不能为空。")
                continue
            if name.lower() in {"b", "back"}:
                step = "save"
                continue
            try:
                saved_path = save_task(
                    name,
                    selection_data,
                    repo_root=repo_root,
                )
            except ValueError as exc:
                print(f"  ! env 名称无效: {exc}")
                continue
            saved_task = json.loads(saved_path.read_text(encoding="utf-8"))
            print("\n  已保存 env")
            print(f"  {saved_path}")
            step = "execute"
            continue

        should_run = _ask_yes_no_or_back("立即运行", default=True, input_func=input_func)
        if should_run is None:
            saved_task = None
            step = "save"
            continue
        return TerminalCompletionResult(saved_task, bool(should_run))


def _ask_yes_no_or_back(prompt: str, *, default: bool, input_func=input) -> bool | None:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input_func(f"{prompt} [{suffix}] (b 返回): ").strip().lower()
        if raw in {"b", "back"}:
            return None
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("  ! 请输入 y、n 或 b。")


def _initialize_preflight_env_cfg(env_cfg: Any, *, num_envs: int, device: str) -> None:
    if hasattr(env_cfg, "__post_init__"):
        if (
            not hasattr(env_cfg, "possible_agents")
            or env_cfg.possible_agents is MISSING
            or (
                isinstance(env_cfg.possible_agents, list)
                and len(env_cfg.possible_agents) == 0
                and hasattr(env_cfg, "controllers")
                and env_cfg.controllers
            )
        ):
            env_cfg.__post_init__()
    env_cfg.scene.num_envs = num_envs
    env_cfg.sim.device = device


def _build_asset_payload(
    *,
    task_name: str | None,
    selection_data: object | None,
    saved_task_data: dict[str, Any] | None,
    should_run: bool,
    env_cfg: Any,
    collect_usd_asset_paths,
    collect_controller_asset_paths,
) -> dict[str, Any]:
    return {
        "task_name": task_name,
        "selection": selection_data,
        "saved_task": saved_task_data,
        "should_run": should_run,
        "usd_paths": collect_usd_asset_paths(env_cfg),
        "controller_paths": collect_controller_asset_paths(env_cfg),
    }


def _missing_controller_package(exc: ModuleNotFoundError) -> bool:
    missing_name = getattr(exc, "name", None)
    return isinstance(missing_name, str) and missing_name.startswith("EAI_assets.controller.")


def _clear_modules_for_controller_retry(*, clear_controller_cache=None) -> None:
    if clear_controller_cache is None:
        from EAI_hmrs.controller_loader import clear_controller_module_cache

        clear_controller_cache = clear_controller_module_cache
    clear_controller_cache()
    prefixes = ("EAI_assets.controller", "EAI_hmrs.envs")
    for name in list(sys.modules):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(name, None)


def _ensure_controller_module_available_for_retry(module_name: str, *, load_asset_resolver=None) -> None:
    resolver = (load_asset_resolver or _load_asset_resolver)()
    resolver.ensure_controller_module_available(module_name)


def _run_with_controller_package_retry(
    operation,
    *,
    load_asset_resolver=None,
    clear_controller_cache=None,
):
    try:
        return operation()
    except ModuleNotFoundError as exc:
        if not _missing_controller_package(exc):
            raise
        _ensure_controller_module_available_for_retry(
            exc.name,
            load_asset_resolver=load_asset_resolver,
        )
        importlib.invalidate_caches()
        _clear_modules_for_controller_retry(clear_controller_cache=clear_controller_cache)
        return operation()


def _collect_asset_payload_after_app(args: argparse.Namespace, task_request: TaskRequest, asset_resolver) -> dict[str, Any]:
    selection_data = None
    saved_task_data = None
    task_name = task_request.task_name
    validation_source = "DIY env"
    requested_env = _requested_env_name(args)
    if requested_env:
        source = resolve_task_source(
            requested_env,
            repo_root=_repo_root(),
        )
        task_name = source.task_name
        saved_task_data = source.saved_task
        _ensure_repo_sources_on_path()
        from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict, interactive_selection_to_dict

        selection = interactive_selection_from_dict(saved_task_data)
        selection_data = interactive_selection_to_dict(selection)
        validation_source = "JSON env"
    elif task_request.selection is not None:
        selection = task_request.selection
        saved_task_data = task_request.saved_task
        _ensure_repo_sources_on_path()
        from EAI.hmrs_env.env_diy.flow import interactive_selection_to_dict as flow_selection_to_dict

        selection_data = flow_selection_to_dict(selection)
        task_name = "EAI-Interactive-v0"
    else:
        raise RuntimeError("No task or DIY selection was resolved.")

    _validate_orsus_lidar_exclusivity(selection_data)
    _validate_sensor_scene_num_envs(selection_data, args.num_envs)
    _enable_required_selection_extensions(selection_data)
    from EAI_hmrs.env_builder import build_interactive_env_cfg_from_selection

    try:
        env_cfg = build_interactive_env_cfg_from_selection(selection)
    except ValueError as exc:
        print(f"❌ {validation_source} cannot be executed: {exc}")
        raise SystemExit(1) from exc
    _initialize_preflight_env_cfg(env_cfg, num_envs=args.num_envs, device=args.device)
    return _build_asset_payload(
        task_name=task_name,
        selection_data=selection_data,
        saved_task_data=saved_task_data,
        should_run=True,
        env_cfg=env_cfg,
        collect_usd_asset_paths=asset_resolver.collect_usd_asset_paths,
        collect_controller_asset_paths=asset_resolver.collect_controller_asset_paths,
    )


def _run_asset_preflight_worker(args: argparse.Namespace) -> None:
    _ensure_repo_sources_on_path()
    try:
        task_request = _resolve_task_request_before_app(args)
    except Diy3dRequested:
        payload = {
            "startup_mode": "diy-3d",
            "task_name": None,
            "selection": None,
            "saved_task": None,
            "usd_paths": [],
            "controller_paths": [],
            "should_run": False,
        }
        Path(args.preflight_output).write_text(json.dumps(payload), encoding="utf-8")
        return

    if not task_request.should_run:
        payload = {
            "task_name": None,
            "selection": None,
            "saved_task": task_request.saved_task,
            "usd_paths": [],
            "controller_paths": [],
            "should_run": False,
        }
        Path(args.preflight_output).write_text(json.dumps(payload), encoding="utf-8")
        return

    from isaaclab.app import AppLauncher

    app_launcher = AppLauncher({"headless": True, "device": args.device})
    simulation_app = app_launcher.app

    try:
        asset_resolver = _load_asset_resolver()
        try:
            payload = _run_with_controller_package_retry(
                lambda: _collect_asset_payload_after_app(args, task_request, asset_resolver),
            )
        except Exception as exc:
            if not _is_asset_preflight_failure(exc):
                raise
            payload = {
                "asset_error": _asset_preflight_failure(exc),
            }
        Path(args.preflight_output).write_text(json.dumps(payload), encoding="utf-8")
    finally:
        simulation_app.close()


def _run_asset_preflight(
    args: argparse.Namespace,
    task_request: TaskRequest | None = None,
) -> tuple[str, dict[str, Any] | None]:
    with tempfile.TemporaryDirectory(prefix="eai-simulator-assets-") as tmp_dir:
        output_path = Path(tmp_dir) / "assets.json"
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--num_envs",
            str(args.num_envs),
            "--device",
            args.device,
            "--preflight-output",
            str(output_path),
        ]
        if args.env:
            cmd.extend(["--env", args.env])
        env = os.environ.copy()
        env["HEADLESS"] = "1"
        if getattr(args, "disable_orsus_ros_env", False):
            env["EAI_DISABLE_ORSUS_ROS_ENV"] = "1"
        _run_preflight_subprocess(cmd, env)
        payload = _read_preflight_payload(output_path)
    return _handle_preflight_payload(payload)


def _run_diy_3d_authoring_in_process(
    args: argparse.Namespace,
    *,
    simulation_app: Any | None = None,
    initial_selection: dict[str, Any] | None = None,
    restore_error: str | None = None,
) -> tuple[TaskRequest, Any]:
    """Run Env DIY authoring in the Kit process that will run the formal env."""

    _ensure_repo_sources_on_path()
    if simulation_app is None:
        from isaaclab.app import AppLauncher

        raw_launcher_options = getattr(args, "app_launcher_args", None)
        launcher_options = dict(raw_launcher_options if raw_launcher_options is not None else vars(args))
        launcher_options.pop("interfaces_menu", None)
        launcher_options.setdefault("headless", False)
        launcher_options.setdefault("device", getattr(args, "device", "cuda:0"))
        launcher_options["enable_cameras"] = True
        existing_kit_args = str(launcher_options.get("kit_args", "")).strip()
        launcher_options["kit_args"] = " ".join(
            item for item in (existing_kit_args, _ORSUS_RTX_PRELOAD_KIT_ARGS) if item
        )
        app_launcher = AppLauncher(launcher_options)
        simulation_app = app_launcher.app

    from EAI_env_diy.protocol import (
        AuthoringResult,
        clear_in_process_callback,
        set_in_process_callback,
    )

    result_box: list[AuthoringResult] = []
    result_ready = threading.Event()

    def receive(result: AuthoringResult) -> None:
        result_box.append(result)
        result_ready.set()

    set_in_process_callback(
        receive,
        initial_selection=initial_selection,
        error=restore_error,
    )
    try:
        _enable_isaac_extension("omni.kit.viewport.window")
        import omni.ext
        import omni.kit.app

        extension_manager = omni.kit.app.get_app().get_extension_manager()
        with _managed_env_diy_extension(
            extension_manager,
            _repo_root() / "source/EAI_env_diy",
            direct_path_type=omni.ext.ExtensionPathType.DIRECT_PATH,
        ):
            while simulation_app.is_running() and not result_ready.is_set():
                simulation_app.update()
    except Exception as exc:
        if not result_box:
            result_box.append(AuthoringResult(status="failed", action="cancel", error=str(exc)))
        raise
    finally:
        clear_in_process_callback()

    if not result_box:
        raise RuntimeError("Env DIY 3D authoring ended without a result.")
    result = result_box[-1]
    return _task_request_from_diy_3d_result(result.to_dict()), simulation_app


def _pump_kit_updates(simulation_app: Any, count: int) -> None:
    for _ in range(count):
        if not simulation_app.is_running():
            raise RuntimeError("Kit stopped while preparing the formal simulation Stage.")
        simulation_app.update()


def _normalized_physics_device(value: str) -> str:
    normalized = str(value).strip().lower()
    return "cuda:0" if normalized == "cuda" else normalized


def _prepare_formal_gpu_stage(
    simulation_app: Any,
    requested_device: str,
    *,
    timeline: Any | None = None,
    usd_context: Any | None = None,
    simulation_manager: Any | None = None,
    physics_scene_type: Any | None = None,
    pre_stage_updates: int = 2,
    post_stage_updates: int = 4,
) -> None:
    """Drain preview callbacks and prepare an empty Stage for formal PhysX setup."""

    if timeline is None:
        import omni.timeline

        timeline = omni.timeline.get_timeline_interface()
    if usd_context is None:
        import omni.usd

        usd_context = omni.usd.get_context()
    if simulation_manager is None:
        from isaacsim.core.simulation_manager import SimulationManager

        simulation_manager = SimulationManager
    if physics_scene_type is None:
        from pxr import UsdPhysics

        physics_scene_type = UsdPhysics.Scene

    timeline.stop()
    _pump_kit_updates(simulation_app, pre_stage_updates)
    usd_context.new_stage()
    _pump_kit_updates(simulation_app, post_stage_updates)

    stage = usd_context.get_stage()
    if stage is None:
        raise RuntimeError("Kit did not create a fresh USD Stage for the formal simulation.")
    physics_scenes = [str(prim.GetPath()) for prim in stage.Traverse() if prim.IsA(physics_scene_type)]
    if physics_scenes:
        raise RuntimeError(
            "PhysicsScene remains on the fresh formal Stage: " + ", ".join(physics_scenes)
        )

    simulation_manager.set_physics_sim_device(requested_device)
    actual_device = simulation_manager.get_physics_sim_device()
    if _normalized_physics_device(actual_device) != _normalized_physics_device(requested_device):
        raise RuntimeError(
            "Formal physics device mismatch: "
            f"requested {requested_device}, SimulationManager reported {actual_device}."
        )


def _reset_kit_after_failed_formal_env(simulation_app: Any, requested_device: str) -> None:
    """Return a partially-created formal environment to an empty authoring Stage."""

    _prepare_formal_gpu_stage(simulation_app, requested_device)


def cmd_vel_bridge_robot_names(
    selection_data: dict[str, Any] | None,
    *,
    possible_agents: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if not selection_data:
        return ()
    enabled: list[str] = []
    type_counts: dict[str, int] = {}
    agent_set = set(possible_agents)
    for index, robot in enumerate(selection_data.get("robots", [])):
        robot_type = str(robot.get("type", "")).lower()
        type_counts[robot_type] = type_counts.get(robot_type, 0) + 1
        default_name = f"{robot_type}_{type_counts[robot_type]}"
        agent_name = default_name if default_name in agent_set else possible_agents[index] if index < len(possible_agents) else default_name
        attachment_types = {str(item.get("type")) for item in robot.get("attachments", [])}
        if attachment_types & {"navigation_io", "keyboard"}:
            enabled.append(agent_name)
    return tuple(enabled)


def ur5_attachment_robot_names(
    selection_data: dict[str, Any] | None,
    *,
    possible_agents: list[str] | tuple[str, ...],
    env_cfg: Any | None = None,
) -> tuple[str, ...]:
    enabled: set[str] = set()
    agent_set = set(possible_agents)
    if selection_data:
        type_counts: dict[str, int] = {}
        for index, robot in enumerate(selection_data.get("robots", [])):
            robot_type = str(robot.get("type", "")).lower()
            type_counts[robot_type] = type_counts.get(robot_type, 0) + 1
            default_name = f"{robot_type}_{type_counts[robot_type]}"
            agent_name = (
                default_name
                if default_name in agent_set
                else possible_agents[index]
                if index < len(possible_agents)
                else default_name
            )
            attachment_types = {str(item.get("type", "")).lower() for item in robot.get("attachments", [])}
            if "ur5" in attachment_types:
                enabled.add(agent_name)

    controllers = getattr(env_cfg, "controllers", {}) if env_cfg is not None else {}
    for agent_name, entry in controllers.items():
        configs = entry if isinstance(entry, (tuple, list)) else (entry,)
        if any("ur5" in f"{type(config).__module__}.{type(config).__name__}".lower() for config in configs):
            enabled.add(agent_name)
    return tuple(agent for agent in possible_agents if agent in enabled)


def _setup_ur5_graph_manager(
    *,
    base_env: Any,
    selection_data: dict[str, Any] | None,
    possible_agents: list[str],
    env_cfg: Any,
):
    robot_names = ur5_attachment_robot_names(
        selection_data,
        possible_agents=possible_agents,
        env_cfg=env_cfg,
    )
    if not robot_names:
        return None
    from EAI.hmrs_ros.manipulator_omnigraph import (
        ManipulatorOmniGraphManager,
        attach_manipulator_graph_manager,
        get_manipulator_graph_manager,
    )
    from EAI.hmrs_ros.ur5_omnigraph import UR5_MODEL_SPEC

    manager = get_manipulator_graph_manager(base_env)
    created_manager = manager is None
    if manager is None:
        manager = ManipulatorOmniGraphManager()
    registered = set(getattr(manager, "registered_instances", ()))
    active = []
    for robot_name in robot_names:
        key = (robot_name, UR5_MODEL_SPEC.model)
        if key in registered or manager.setup_robot(robot_name, UR5_MODEL_SPEC):
            active.append(robot_name)
    if not active:
        if created_manager:
            manager.close()
        return None
    attach_manipulator_graph_manager(base_env, manager)
    setup = getattr(base_env, "_manipulator_setup_instances", set())
    base_env._manipulator_setup_instances = setup
    setup.update((robot_name, UR5_MODEL_SPEC.model) for robot_name in active)
    return manager


def _is_manipulator_ros2_auxiliary(controller: Any) -> bool:
    model = getattr(controller, "model_spec", None)
    return callable(controller) and model is not None and all(
        hasattr(model, field) for field in ("model", "joint_names", "ee_body_names")
    )


def _disable_manipulator_ros2_auxiliaries(env_cfg: Any) -> int:
    """Remove ROS-driven arm auxiliaries when the ROS2 Bridge is disabled."""
    controllers = getattr(env_cfg, "controllers", None)
    if not isinstance(controllers, dict):
        return 0
    removed = 0
    for robot_name, entry in tuple(controllers.items()):
        if not isinstance(entry, (tuple, list)) or len(entry) < 2:
            continue
        primary, *auxiliaries = entry
        kept = [aux for aux in auxiliaries if not _is_manipulator_ros2_auxiliary(aux)]
        removed += len(auxiliaries) - len(kept)
        if len(kept) != len(auxiliaries):
            controllers[robot_name] = (primary, *kept) if kept else primary
    return removed


def _setup_aerial_sensor_manager(
    *,
    base_env: Any,
    selection_data: dict[str, Any] | None,
    possible_agents: list[str],
    seed: int = 0,
):
    from EAI.hmrs_ros.aerial_sensor_suite import (
        AerialSensorSuiteManager,
        aerial_sensor_specs_from_selection,
        attach_aerial_sensor_manager,
        get_aerial_sensor_manager,
    )

    specs = aerial_sensor_specs_from_selection(selection_data, possible_agents)
    if not specs:
        return None
    manager = get_aerial_sensor_manager(base_env)
    if manager is not None:
        return manager
    manager = AerialSensorSuiteManager(base_env, specs, seed=seed)
    if not manager.registered_robots:
        manager.close()
        return None
    attach_aerial_sensor_manager(base_env, manager)
    return manager


def _setup_realsense_imu_manager(
    *,
    base_env: Any,
    seed: int = 0,
):
    from EAI.hmrs_ros.realsense_d455_imu import (
        RealSenseD455ImuManager,
        attach_realsense_imu_manager,
        get_realsense_imu_manager,
        realsense_d455_instance_registry,
    )

    instances = realsense_d455_instance_registry()
    if not instances:
        return None
    manager = get_realsense_imu_manager(base_env)
    if manager is not None:
        return manager
    manager = RealSenseD455ImuManager(base_env, instances, seed=seed)
    if not manager.registered_instances:
        manager.close()
        return None
    attach_realsense_imu_manager(base_env, manager)
    return manager


def active_cmd_vel_bridge_robot_names(
    selection_data: dict[str, Any] | None,
    *,
    possible_agents: list[str] | tuple[str, ...],
    env_name: str,
    goal_controlled_robots: set[str],
    explicit: bool = False,
) -> tuple[str, ...]:
    bridge_agents = set(cmd_vel_bridge_robot_names(selection_data, possible_agents=possible_agents))
    if explicit:
        bridge_agents.update(possible_agents)
    return tuple(agent for agent in possible_agents if agent in bridge_agents)


def _transform_cmd_vel_for_robot(
    robot_type: str | None,
    vx: float,
    vy: float,
    wz: float,
) -> tuple[float, float, float]:
    angular_scale = (
        SCOUT_CMD_VEL_ANGULAR_SCALE
        if str(robot_type or "").strip().casefold() == "scout"
        else 1.0
    )
    return float(vx), float(vy), float(wz) * angular_scale


def _apply_cmd_vel_bridge_commands(
    *,
    bridges: dict[str, Any],
    robot_commands: dict[str, Any],
    robot_types: dict[str, str | None],
    goal_controlled_robots: set[str],
) -> None:
    for agent_name, bridge in bridges.items():
        if agent_name in goal_controlled_robots or agent_name not in robot_commands:
            continue
        vx, vy, wz = bridge.get_cmd_vel()
        vx, vy, wz = _transform_cmd_vel_for_robot(robot_types.get(agent_name), vx, vy, wz)
        command = robot_commands[agent_name]
        command[:, 0] = vx
        command[:, 1] = vy
        command[:, 2] = wz


def _bridge_twist_command(bridge: Any) -> tuple[float, float, float, float]:
    if hasattr(bridge, "get_twist_command"):
        vx, vy, vz, wz = bridge.get_twist_command()
        return (
            0.0 if vx is None else float(vx),
            0.0 if vy is None else float(vy),
            0.0 if vz is None else float(vz),
            0.0 if wz is None else float(wz),
        )
    vx, vy, wz = bridge.get_cmd_vel()
    return float(vx), float(vy), 0.0, float(wz)


def _integrated_scalar(value: Any, delta: float) -> float:
    return round(float(value) + float(delta), 6)


def _single_controller_pose_value(controller: dict[str, Any], key: str) -> Any | None:
    value = controller.get(key)
    if value is None:
        return None
    cloned = value.clone()
    shape = getattr(cloned, "shape", None)
    if shape is not None and len(shape) > 1:
        return cloned[0].clone()
    return cloned


def _goal_position_delta(
    robot_type: str | None,
    vx: float,
    vy: float,
    vz: float,
    dt: float,
) -> tuple[float, float, float]:
    if str(robot_type or "").lower() in {
        "human",
        "quadcopter",
        "pegasusiris",
        "pegasusx4",
    }:
        return (
            vx * KEYBOARD_CMD_VEL_GOAL_STEP_SCALE,
            vy * KEYBOARD_CMD_VEL_GOAL_STEP_SCALE,
            vz * KEYBOARD_CMD_VEL_GOAL_STEP_SCALE,
        )
    return vx * dt, vy * dt, vz * dt


def _apply_goal_cmd_vel_bridge_commands(
    *,
    bridges: dict[str, Any],
    base_env: Any,
    goal_positions: dict[str, Any],
    goal_yaws: dict[str, Any],
    yaw_goal_controlled_robots: set[str],
    dt: float,
    yaw_command_names: dict[str, str] | None = None,
    robot_types: dict[str, str | None] | None = None,
) -> None:
    yaw_command_names = yaw_command_names or {}
    robot_types = robot_types or {}
    for agent_name, bridge in bridges.items():
        vx, vy, vz, wz = _bridge_twist_command(bridge)
        is_human = str(robot_types.get(agent_name) or "").lower() == "human"
        is_zero = all(abs(value) <= 1.0e-9 for value in (vx, vy, vz, wz))
        if is_human and is_zero:
            controller = getattr(base_env, "_controllers_dict", {}).get(agent_name, {})
            new_goal = _single_controller_pose_value(controller, "current_position")
            if new_goal is None and agent_name in goal_positions:
                new_goal = goal_positions[agent_name].clone()
            if new_goal is not None:
                goal_positions[agent_name] = new_goal
                if hasattr(base_env, "set_command"):
                    base_env.set_command(agent_name, "goal_position", new_goal.unsqueeze(0))

            new_yaw = _single_controller_pose_value(controller, "current_yaw")
            if new_yaw is None and agent_name in goal_yaws:
                new_yaw = goal_yaws[agent_name].clone()
            if new_yaw is not None and agent_name in yaw_goal_controlled_robots:
                goal_yaws[agent_name] = new_yaw
                if hasattr(base_env, "set_command"):
                    base_env.set_command(
                        agent_name,
                        yaw_command_names.get(agent_name, "goal_yaw"),
                        new_yaw.unsqueeze(0),
                    )
            continue
        if agent_name in goal_positions:
            dx, dy, dz = _goal_position_delta(robot_types.get(agent_name), vx, vy, vz, dt)
            new_goal = goal_positions[agent_name].clone()
            new_goal[0] = _integrated_scalar(new_goal[0], dx)
            new_goal[1] = _integrated_scalar(new_goal[1], dy)
            new_goal[2] = _integrated_scalar(new_goal[2], dz)
            goal_positions[agent_name] = new_goal
            if hasattr(base_env, "set_command"):
                base_env.set_command(agent_name, "goal_position", new_goal.unsqueeze(0))
        if agent_name in yaw_goal_controlled_robots and agent_name in goal_yaws:
            new_yaw = goal_yaws[agent_name].clone()
            new_yaw[0] = _integrated_scalar(new_yaw[0], wz * dt)
            goal_yaws[agent_name] = new_yaw
            if hasattr(base_env, "set_command"):
                base_env.set_command(
                    agent_name,
                    yaw_command_names.get(agent_name, "goal_yaw"),
                    new_yaw.unsqueeze(0),
                )


def _initial_goal_position(*, base_env: Any, agent_name: str, controller_cfg: Any, device: str, torch_module: Any) -> Any:
    if agent_name in getattr(base_env.scene, "articulations", {}):
        robot = base_env.scene.articulations[agent_name]
        if hasattr(robot, "data") and hasattr(robot.data, "root_pos_w"):
            return robot.data.root_pos_w[0].clone()
    if controller_cfg and hasattr(controller_cfg, "initial_position"):
        return torch_module.tensor(controller_cfg.initial_position, device=device, dtype=torch_module.float32)
    if controller_cfg and getattr(controller_cfg, "robot_type", None) == "Quadcopter":
        return torch_module.tensor([0.0, 0.0, 1.0], device=device, dtype=torch_module.float32)
    if controller_cfg and getattr(controller_cfg, "robot_type", None) == "M20Nav":
        return torch_module.tensor([0.0, 0.0, 0.52], device=device, dtype=torch_module.float32)
    return torch_module.tensor([0.0, 0.0, 0.5], device=device, dtype=torch_module.float32)


def _initialize_env_cfg(env_cfg: Any, args: argparse.Namespace) -> None:
    if hasattr(env_cfg, "__post_init__"):
        if (
            not hasattr(env_cfg, "possible_agents")
            or env_cfg.possible_agents is MISSING
            or (
                isinstance(env_cfg.possible_agents, list)
                and len(env_cfg.possible_agents) == 0
                and hasattr(env_cfg, "controllers")
                and env_cfg.controllers
            )
        ):
            env_cfg.__post_init__()
    env_cfg.scene.num_envs = args.num_envs
    env_cfg.sim.device = args.device
    env_cfg.seed = args.seed


def _load_env_cfg(env_name: str, selection_data: dict[str, Any] | None):
    if selection_data is None:
        raise ValueError(f"Env '{env_name}' does not include JSON selection data.")
    from EAI_hmrs.env_builder import build_interactive_env_cfg_from_selection, interactive_selection_from_dict

    return build_interactive_env_cfg_from_selection(interactive_selection_from_dict(selection_data))


def _create_env(env_name: str, env_cfg: Any):
    _ = env_name
    from EAI.hmrs_env import MultiRobotDirectEnv

    return MultiRobotDirectEnv(cfg=env_cfg)


def _apply_env_cfg_hook(env_cfg: Any, config: SimulatorLaunchConfig) -> None:
    if config.env_cfg_hook is not None:
        config.env_cfg_hook(env_cfg)


def _session_preflight_args(config: SimulatorLaunchConfig) -> SimpleNamespace:
    return SimpleNamespace(
        env=config.env,
        task=None,
        num_envs=config.num_envs,
        device=config.device,
        preflight_output=None,
        enable_cmd_vel_bridge=config.enable_cmd_vel_bridge,
        disable_orsus_ros_env=config.disable_orsus_ros_env,
        ml_framework=config.ml_framework,
        seed=config.seed,
    )


def _session_env_init_args(config: SimulatorLaunchConfig) -> SimpleNamespace:
    return SimpleNamespace(
        num_envs=config.num_envs,
        device=config.device,
        seed=config.seed,
    )


def _app_launcher_args(
    config: SimulatorLaunchConfig,
    selection_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = dict(config.app_launcher_args)
    args.pop("interfaces_menu", None)
    args.setdefault("headless", config.headless)
    args.setdefault("device", config.device)
    args.setdefault("ml_framework", config.ml_framework)
    effective_selection = selection_data if selection_data is not None else config.selection_data
    if effective_selection:
        from EAI.physics.aerial_sensors import selection_requires_aerial_camera

        if selection_requires_aerial_camera(effective_selection):
            args["enable_cameras"] = True
        if _selection_has_attachment(effective_selection, "orsus"):
            motion_bvh_args = (
                "--/renderer/raytracingMotion/enabled=true "
                "--/renderer/raytracingMotion/enableHydraEngineMasking=true "
                "--/renderer/raytracingMotion/enabledForHydraEngines='0'"
            )
            existing_kit_args = str(args.get("kit_args", "")).strip()
            args["kit_args"] = " ".join(
                item
                for item in (
                    existing_kit_args,
                    _ORSUS_RTX_PRELOAD_KIT_ARGS,
                    motion_bvh_args,
                )
                if item
            )
    return args


@contextmanager
def open_simulator_session(config: SimulatorLaunchConfig) -> Iterator[SimulatorSession]:
    """Open Isaac Sim and an EAI environment for programmatic callers."""

    _ensure_repo_sources_on_path()
    if config.resolved_env_name is not None:
        env_name = config.resolved_env_name
        selection_data = config.selection_data
    else:
        env_name, selection_data = _run_asset_preflight(_session_preflight_args(config))
    _validate_orsus_lidar_exclusivity(selection_data)
    _validate_sensor_scene_num_envs(selection_data, config.num_envs)
    runtime_device = _runtime_device_for_env(env_name, selection_data, config.device)
    if runtime_device != config.device:
        print(
            "[EAI Simulator] Human assets use CPU PhysX on Isaac Sim 5.1 "
            f"(requested {config.device}, using {runtime_device})."
        )
        app_launcher_args = dict(config.app_launcher_args)
        app_launcher_args["device"] = runtime_device
        config = replace(config, device=runtime_device, app_launcher_args=app_launcher_args)
    if config.disable_orsus_ros_env:
        os.environ["EAI_DISABLE_ORSUS_ROS_ENV"] = "1"
    if config.enable_ros_bridge_extension:
        configure_isaac_ros_bridge_env()

    app_launcher = None
    owns_simulation_app = config.existing_simulation_app is None
    if owns_simulation_app:
        from isaaclab.app import AppLauncher

        app_launcher = AppLauncher(_app_launcher_args(config, selection_data))
        simulation_app = app_launcher.app
    else:
        simulation_app = config.existing_simulation_app
    _silence_simulation_manager_time_log_spam()
    env = None
    ur5_manager = None
    aerial_sensor_manager = None
    realsense_imu_manager = None
    orsus_cleanup = None
    try:
        _enable_required_selection_extensions(selection_data)
        if config.enable_ros_bridge_extension:
            _enable_isaac_extension("isaacsim.ros2.bridge")

        env_cfg = _load_env_cfg(env_name, selection_data)
        _initialize_env_cfg(env_cfg, _session_env_init_args(config))
        _apply_env_cfg_hook(env_cfg, config)
        if not config.enable_ros_bridge_extension:
            disabled_manipulators = _disable_manipulator_ros2_auxiliaries(env_cfg)
            if disabled_manipulators:
                print(
                    "[EAI Simulator] ROS2 Bridge disabled; skipped "
                    f"{disabled_manipulators} manipulator ROS2 controller(s)."
                )
        env = _create_env(env_name, env_cfg)
        base_env = env.unwrapped if hasattr(env, "unwrapped") else env
        possible_agents = list(base_env.possible_agents)
        env.reset()
        # Re-apply log silencing after scene setup: native plugins can
        # (re-)register their carb logging source while the stage loads,
        # which resets per-source thresholds.
        _silence_simulation_manager_time_log_spam()
        from EAI_assets.sensor.high_sensor.orsus import (
            close_orsus_ros_resources,
            setup_pending_orsus_ros_graphs,
        )

        orsus_cleanup = close_orsus_ros_resources
        orsus_graph_count = setup_pending_orsus_ros_graphs()
        if orsus_graph_count:
            print(
                f"[EAI Simulator] Created {orsus_graph_count} instance-safe "
                "Orsus RTX LiDAR/odometry publisher set(s)."
            )
        if config.enable_ros_bridge_extension:
            ur5_manager = getattr(base_env, "_ur5_ros2_manager", None)
            if ur5_manager is None:
                ur5_manager = _setup_ur5_graph_manager(
                    base_env=base_env,
                    selection_data=selection_data,
                    possible_agents=possible_agents,
                    env_cfg=env_cfg,
                )
        aerial_sensor_manager = _setup_aerial_sensor_manager(
            base_env=base_env,
            selection_data=selection_data,
            possible_agents=possible_agents,
            seed=config.seed,
        )
        realsense_imu_manager = _setup_realsense_imu_manager(
            base_env=base_env,
            seed=config.seed,
        )


        yield SimulatorSession(
            simulation_app=simulation_app,
            env=env,
            base_env=base_env,
            env_cfg=env_cfg,
            env_name=env_name,
            selection_data=selection_data,
            possible_agents=possible_agents,
            num_envs=config.num_envs,
            device=config.device,
        )
    finally:
        try:
            if orsus_cleanup is not None:
                orsus_cleanup()
            if aerial_sensor_manager is not None:
                aerial_sensor_manager.close()
            if realsense_imu_manager is not None:
                realsense_imu_manager.close()
            if ur5_manager is not None:
                ur5_manager.close()
            if env is not None:
                env.close()
        finally:
            if owns_simulation_app:
                _prepare_replicator_for_app_close()
                simulation_app.close()


def main() -> None:
    interface_exit_code = _dispatch_interface_cli(sys.argv[1:])
    if interface_exit_code is not None:
        raise SystemExit(interface_exit_code)
    _warn_if_inotify_limits_are_low()
    _ensure_repo_sources_on_path()
    preflight_parser = _base_parser()
    preflight_args, hydra_args = preflight_parser.parse_known_args()
    if preflight_args.preflight_output:
        _run_asset_preflight_worker(preflight_args)
        return
    try:
        startup_mode = _resolve_startup_mode(preflight_args)
    except ValueError as exc:
        preflight_parser.error(str(exc))
    task_request = None
    existing_simulation_app = None
    if startup_mode != "diy-3d":
        try:
            env_name, selection_data = _run_asset_preflight(preflight_args, task_request)
        except Diy3dRequested:
            startup_mode = "diy-3d"

    if startup_mode == "diy-3d":
        from isaaclab.app import AppLauncher

        launch_parser = _base_parser(include_device=False)
        AppLauncher.add_app_launcher_args(launch_parser)
        args_cli, hydra_args = launch_parser.parse_known_args()
        task_request, existing_simulation_app = _run_diy_3d_authoring_in_process(args_cli)
        if not task_request.should_run:
            if task_request.saved_task:
                print("Env DIY 3D selection saved; simulation was not started.")
            existing_simulation_app.close()
            return
        env_name = "EAI-Interactive-v0"
        selection_data = task_request.selection_data
        sys.argv = [sys.argv[0]] + hydra_args
    else:
        from isaaclab.app import AppLauncher

        launch_parser = _base_parser(include_device=False)
        AppLauncher.add_app_launcher_args(launch_parser)
        args_cli, hydra_args = launch_parser.parse_known_args()
        args_cli.env = env_name
        sys.argv = [sys.argv[0]] + hydra_args

    launch_config = SimulatorLaunchConfig(
        env=env_name,
        num_envs=args_cli.num_envs,
        device=args_cli.device,
        seed=args_cli.seed,
        headless=bool(getattr(args_cli, "headless", False)),
        enable_ros_bridge_extension=True,
        enable_cmd_vel_bridge=bool(getattr(args_cli, "enable_cmd_vel_bridge", False)),
        ml_framework=getattr(args_cli, "ml_framework", "torch"),
        app_launcher_args=dict(vars(args_cli)),
        resolved_env_name=env_name,
        selection_data=selection_data,
        existing_simulation_app=existing_simulation_app,
    )

    entered_session = False
    session_context = None
    try:
        if existing_simulation_app is not None:
            _prepare_formal_gpu_stage(existing_simulation_app, args_cli.device)
        while True:
            session_context = open_simulator_session(launch_config)
            try:
                session = session_context.__enter__()
            except Exception as exc:
                if existing_simulation_app is None:
                    raise
                _reset_kit_after_failed_formal_env(existing_simulation_app, args_cli.device)
                task_request, _ = _run_diy_3d_authoring_in_process(
                    args_cli,
                    simulation_app=existing_simulation_app,
                    initial_selection=selection_data,
                    restore_error=str(exc),
                )
                if not task_request.should_run:
                    if task_request.saved_task:
                        print("Env DIY 3D selection saved after formal environment rollback.")
                    return
                selection_data = task_request.selection_data
                _prepare_formal_gpu_stage(existing_simulation_app, args_cli.device)
                launch_config = replace(launch_config, selection_data=selection_data)
                continue
            entered_session = True
            break
        import torch
        from EAI.controllers.base import normalize_controller_entry

        env = session.env
        base_env = session.base_env
        env_cfg = session.env_cfg
        possible_agents = session.possible_agents
        num_envs = session.num_envs
        runtime_device = session.device
        robot_commands = {}
        robot_types: dict[str, str | None] = {}
        controller_cfgs: dict[str, Any] = {}
        goal_controlled_robots: set[str] = set()
        yaw_goal_controlled_robots: set[str] = set()
        yaw_command_names: dict[str, str] = {}
        for agent_name in possible_agents:
            entry = env_cfg.controllers.get(agent_name) if hasattr(env_cfg, "controllers") else None
            controller_cfg, _aux = normalize_controller_entry(entry) if entry else (None, ())
            controller_cfgs[agent_name] = controller_cfg
            command_dim = (
                int(getattr(controller_cfg, "action_dim", 4))
                if getattr(controller_cfg, "control_mode", None) == "rotor_velocity"
                else 3
            )
            robot_commands[agent_name] = torch.zeros((num_envs, command_dim), device=runtime_device)
            robot_type = getattr(controller_cfg, "robot_type", None)
            robot_types[agent_name] = robot_type
            if getattr(controller_cfg, "command_name", None) == "goal_position" or robot_type in {"Quadcopter", "M20Nav"}:
                goal_controlled_robots.add(agent_name)
            if (
                hasattr(controller_cfg, "yaw_command_name")
                and getattr(controller_cfg, "control_mode", None) != "rotor_velocity"
            ):
                yaw_goal_controlled_robots.add(agent_name)
                yaw_command_names[agent_name] = getattr(controller_cfg, "yaw_command_name", "goal_yaw")

        print(f"\n[EAI Simulator] env={env_name} device={runtime_device} num_envs={num_envs}")

        goal_positions = {}
        goal_yaws = {}
        for agent_name in goal_controlled_robots:
            controller_cfg = controller_cfgs.get(agent_name)
            init_pos = _initial_goal_position(
                base_env=base_env,
                agent_name=agent_name,
                controller_cfg=controller_cfg,
                device=runtime_device,
                torch_module=torch,
            )
            goal_positions[agent_name] = init_pos
            if hasattr(base_env, "set_command"):
                base_env.set_command(agent_name, "goal_position", init_pos.unsqueeze(0))
            if agent_name in yaw_goal_controlled_robots:
                init_yaw = float(getattr(controller_cfg, "initial_yaw", 0.0))
                yaw_tensor = torch.tensor([init_yaw], device=runtime_device, dtype=torch.float32)
                goal_yaws[agent_name] = yaw_tensor
                if hasattr(base_env, "set_command"):
                    base_env.set_command(
                        agent_name,
                        yaw_command_names.get(agent_name, "goal_yaw"),
                        yaw_tensor.unsqueeze(0),
                    )

        ur5_manager = getattr(base_env, "_ur5_ros2_manager", None)
        if ur5_manager is None:
            ur5_manager = _setup_ur5_graph_manager(
                base_env=base_env,
                selection_data=selection_data,
                possible_agents=possible_agents,
                env_cfg=env_cfg,
            )

        bridges = {}
        bridge_agents = set(
            active_cmd_vel_bridge_robot_names(
                selection_data,
                possible_agents=possible_agents,
                env_name=env_name,
                goal_controlled_robots=goal_controlled_robots,
                explicit=getattr(args_cli, "enable_cmd_vel_bridge", False),
            )
        )
        bridge_agents = {
            agent_name
            for agent_name in bridge_agents
            if getattr(controller_cfgs.get(agent_name), "control_mode", None) != "rotor_velocity"
        }
        if bridge_agents:
            from EAI.hmrs_ros import ROS2CmdVelBridge

            for agent_name in possible_agents:
                if agent_name not in bridge_agents:
                    continue
                bridge = ROS2CmdVelBridge(robot_name=agent_name, device=runtime_device)
                if bridge.setup():
                    bridges[agent_name] = bridge
                    print(f"[EAI Simulator] cmd_vel enabled: /{agent_name}/cmd_vel")

        snapshot_path, runtime_snapshot = _publish_runtime_interface_snapshot(
            env_name=env_name,
            selection_data=selection_data,
            possible_agents=possible_agents,
            cmd_vel_agents=set(bridges),
            base_env=base_env,
        )
        if getattr(args_cli, "interfaces_menu", False):
            interface_cli = _load_interface_cli()
            threading.Thread(
                target=interface_cli,
                args=(["--repo-root", str(_repo_root()), "menu"],),
                name="eai-interface-menu",
                daemon=True,
            ).start()
        last_snapshot_heartbeat = 0.0
        try:
            while session.simulation_app.is_running():
                import time

                now = time.monotonic()
                if now - last_snapshot_heartbeat >= 2.0:
                    from EAI.interface_catalog.snapshot import refresh_snapshot

                    runtime_snapshot = refresh_snapshot(
                        snapshot_path,
                        runtime_snapshot,
                        robots=_runtime_robot_payload(
                            selection_data,
                            possible_agents,
                            base_env=base_env,
                        ),
                    )
                    last_snapshot_heartbeat = now
                _apply_cmd_vel_bridge_commands(
                    bridges=bridges,
                    robot_commands=robot_commands,
                    robot_types=robot_types,
                    goal_controlled_robots=goal_controlled_robots,
                )
                _apply_goal_cmd_vel_bridge_commands(
                    bridges=bridges,
                    base_env=base_env,
                    goal_positions=goal_positions,
                    goal_yaws=goal_yaws,
                    yaw_goal_controlled_robots=yaw_goal_controlled_robots,
                    dt=float(getattr(base_env, "step_dt", 0.02)),
                    yaw_command_names=yaw_command_names,
                    robot_types=robot_types,
                )
                actions = {
                    agent: torch.zeros((num_envs, 3), device=runtime_device)
                    if agent in goal_controlled_robots
                    else robot_commands[agent]
                    for agent in possible_agents
                }
                env.step(actions)
        finally:
            from EAI.interface_catalog.snapshot import remove_snapshot

            remove_snapshot(snapshot_path)
            if ur5_manager is not None:
                ur5_manager.close()
            for bridge in bridges.values():
                bridge.cleanup()
    finally:
        if entered_session:
            session_context.__exit__(*sys.exc_info())
        if existing_simulation_app is not None:
            existing_simulation_app.close()


if __name__ == "__main__":
    main()
    #测试
