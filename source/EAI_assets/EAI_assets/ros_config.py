"""ROS 2 distribution and Isaac Sim bridge environment configuration."""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path


DEFAULT_ROS_DISTRO = "humble"
SUPPORTED_ROS_DISTROS = frozenset({"humble", "jazzy"})


def ros_distro_config_path(prefix: str | os.PathLike[str] | None = None) -> Path:
    root = Path(prefix) if prefix is not None else Path(sys.prefix)
    return root / "share" / "eai-simulator" / "ros_distro"


def resolve_ros_distro(
    requested: str | None = None,
    *,
    config_path: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve an explicit, environment, installed, or default ROS distro."""

    value = str(requested or os.environ.get("ROS_DISTRO", "")).strip().lower()
    path = Path(config_path) if config_path is not None else ros_distro_config_path()
    if not value and path.is_file():
        value = path.read_text(encoding="utf-8").strip().lower()
    value = value or DEFAULT_ROS_DISTRO
    if value not in SUPPORTED_ROS_DISTROS:
        choices = ", ".join(sorted(SUPPORTED_ROS_DISTROS))
        raise ValueError(f"Unsupported ROS_DISTRO {value!r}; expected one of: {choices}")
    return value


def _path_targets_other_distro(path: Path, ros_distro: str) -> bool:
    parts = set(path.parts)
    return any(distro in parts for distro in SUPPORTED_ROS_DISTROS - {ros_distro})


def find_isaac_ros_bridge_path(ros_distro: str | None = None) -> str | None:
    distro = resolve_ros_distro(ros_distro)
    existing = os.environ.get("ISAAC_ROS_PATH")
    if existing:
        existing_path = Path(existing).expanduser()
        if existing_path.exists() and not _path_targets_other_distro(existing_path, distro):
            return str(existing_path)

    relative = Path("exts") / "isaacsim.ros2.bridge" / distro
    roots: list[Path] = []
    env_root = os.environ.get("EAI_ISAACSIM_ROOT") or os.environ.get("ISAACSIM_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend(
        Path(candidate).expanduser()
        for candidate in ("~/isaacsim", "~/IsaacSim", "~/isaac-sim", "~/isaacsim-6.0.1")
    )
    for root in roots:
        for candidate in (
            root / "_build" / "linux-x86_64" / "release" / relative,
            root / relative,
        ):
            if candidate.exists():
                return str(candidate)

    target_suffix = Path("isaacsim") / relative
    search_paths = [*site.getsitepackages(), site.getusersitepackages()]
    for search_path in search_paths:
        candidate = Path(search_path) / target_suffix
        if candidate.exists():
            return str(candidate)
    return None


def _prepend_env_path(name: str, path: str) -> None:
    parts = [part for part in os.environ.get(name, "").split(os.pathsep) if part]
    if path not in parts:
        os.environ[name] = os.pathsep.join([path, *parts])


def configure_ros_env(ros_distro: str | None = None) -> str | None:
    distro = resolve_ros_distro(ros_distro)
    os.environ["ROS_DISTRO"] = distro
    os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
    bridge_path = find_isaac_ros_bridge_path(distro)
    if bridge_path is None:
        return None

    os.environ["ISAAC_ROS_PATH"] = bridge_path
    _prepend_env_path("LD_LIBRARY_PATH", str(Path(bridge_path) / "lib"))
    _prepend_env_path("AMENT_PREFIX_PATH", bridge_path)
    return bridge_path
