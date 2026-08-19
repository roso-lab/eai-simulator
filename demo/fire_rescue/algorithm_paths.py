from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path


def resolve_fire_rescue_algorithm_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    required_modules = (
        root / "algorithm" / "emos" / "engine.py",
        root / "algorithm" / "global_planner" / "session.py",
        root / "algorithm" / "multi_robot_navigation" / "eai_plugin.py",
    )
    missing = [path for path in required_modules if not path.is_file()]
    if missing:
        names = ", ".join(str(path.relative_to(root)) for path in missing)
        raise RuntimeError(f"Could not find repo-local algorithm modules: {names}")
    return root


def _external_algorithm_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    configured = os.environ.get("EAI_EXTERNAL_ALGORITHM_ROOTS", "")
    for item in configured.split(os.pathsep):
        if item.strip():
            roots.append(Path(item).expanduser().resolve())

    home_checkout = Path.home() / "EAI"
    roots.append(home_checkout.resolve())

    expanded: list[Path] = []
    for root in roots:
        expanded.extend((root, root / "algorithm"))
    return tuple(expanded)


def _prepend_sys_path(path: Path) -> None:
    text = str(path)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_external_algorithm_path(path: Path) -> bool:
    eai_root, eai_algorithm_root = _external_algorithm_roots()
    return path in (eai_root, eai_algorithm_root) or _is_relative_to(path, eai_algorithm_root)


def _remove_external_algorithm_paths() -> None:
    kept: list[str] = []
    for item in sys.path:
        if not item:
            kept.append(item)
            continue
        try:
            path = Path(item).resolve()
        except OSError:
            kept.append(item)
            continue
        if _is_external_algorithm_path(path):
            continue
        kept.append(item)
    sys.path[:] = kept


def _module_origin_paths(module: object) -> list[Path]:
    paths: list[Path] = []
    raw_file = getattr(module, "__file__", None)
    if raw_file:
        with contextlib.suppress(OSError):
            paths.append(Path(raw_file).resolve())
    spec = getattr(module, "__spec__", None)
    locations = getattr(spec, "submodule_search_locations", None)
    if locations:
        for item in locations:
            with contextlib.suppress(OSError):
                paths.append(Path(item).resolve())
    raw_path = getattr(module, "__path__", None)
    if raw_path:
        for item in raw_path:
            with contextlib.suppress(OSError):
                paths.append(Path(item).resolve())
    return paths


def _remove_external_algorithm_modules() -> None:
    for name, module in list(sys.modules.items()):
        if name != "algorithm" and not name.startswith("algorithm."):
            continue
        if any(_is_external_algorithm_path(path) for path in _module_origin_paths(module)):
            sys.modules.pop(name, None)


def ensure_fire_rescue_algorithm_paths() -> Path:
    root = resolve_fire_rescue_algorithm_root()
    _remove_external_algorithm_paths()
    _remove_external_algorithm_modules()
    _prepend_sys_path(root)
    return root


def default_factory_map_yaml() -> Path:
    local_map = Path(__file__).resolve().parent / "assets" / "factory_map.yaml"
    if local_map.is_file():
        return local_map
    return Path(__file__).resolve().parents[2] / "usd" / "scene" / "factory" / "factory_map.yaml"
