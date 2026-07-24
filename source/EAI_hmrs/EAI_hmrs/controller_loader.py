from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from EAI_assets.asset_resolver import controller_path, ensure_controller_assets_for_paths


@lru_cache(maxsize=None)
def _load_controller_module(relative_path: str) -> Any:
    module_path = Path(controller_path(relative_path))
    if not module_path.exists():
        ensure_controller_assets_for_paths([str(module_path)])

    module_name = "eai_selected_controller_" + "_".join(module_path.with_suffix("").parts[-4:])
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load controller module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_controller_attr(relative_path: str, attr_name: str) -> Any:
    module = _load_controller_module(relative_path)
    return getattr(module, attr_name)
