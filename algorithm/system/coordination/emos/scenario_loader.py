"""Load ``EMOSScenarioConfig`` from caller-supplied YAML files.

This package does **not** embed any domain scenario (e.g. factory fire-rescue).
Callers (simulation repo, apps) build :class:`~.types.EMOSScenarioConfig` in code
via :func:`~.types.scenario_from_dict`, or load YAML from a path they control.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from .types import EMOSScenarioConfig, scenario_from_dict

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v


def load_scenario(
    path: Union[str, Path],
    *,
    extra_dict: Optional[Dict[str, Any]] = None,
) -> EMOSScenarioConfig:
    """Parse a YAML file into :class:`EMOSScenarioConfig`.

    Args:
        path: Path to a ``.yaml`` / ``.yml`` file (absolute or relative).
        extra_dict: Optional dict merged on top of the parsed YAML (e.g. overrides).

    Raises:
        FileNotFoundError: If the path does not exist or is not a file.
        RuntimeError: If PyYAML is not installed.
    """
    if yaml is None:
        raise RuntimeError("PyYAML is required to load EMOS scenario files (pip install pyyaml).")
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"EMOS scenario YAML not found or not a file: {p}")
    text = p.read_text(encoding="utf-8")
    data: Dict[str, Any] = yaml.safe_load(text) or {}
    if extra_dict:
        deep_merge(data, extra_dict)
    return scenario_from_dict(data)
