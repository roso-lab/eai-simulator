"""Versioned external runtime factory loaded by both run and resume.

No dependency on a particular benchmark package, scenario, robot or evaluator.
Factories must be lightweight: open Isaac only inside SessionBackend.open_scene.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import inspect
from pathlib import Path
from typing import Any, Mapping

from .models import BranchPlan


@dataclass(frozen=True)
class RuntimeComponents:
    adapter: Any
    session_backend: Any
    scheduler: Any
    plugin_id: str
    plugin_version: str
    executor_options: Mapping[str, Any] = field(default_factory=dict)


def load_runtime(config: Mapping[str, Any], *, plan: BranchPlan, run_dir: Path,
                 expected: Mapping[str, Any] | None = None) -> tuple[RuntimeComponents, dict]:
    spec = str(config.get("runtime_factory", ""))
    if ":" not in spec:
        raise ValueError("runtime_factory must be an importable module:factory")
    module_name, name = spec.split(":", 1)
    factory = getattr(importlib.import_module(module_name), name)
    source = inspect.getsourcefile(factory)
    if not source:
        raise ValueError("runtime factory requires auditable Python source")
    pin = {"runtime_factory": spec,
           "runtime_kwargs": dict(config.get("runtime_kwargs", {})),
           "factory_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()}
    if expected is not None and any(pin[key] != expected.get(key) for key in pin):
        raise ValueError("runtime factory/config changed since original run; start a new run")
    components = factory(plan=plan, run_dir=run_dir, **pin["runtime_kwargs"])
    if not isinstance(components, RuntimeComponents):
        raise TypeError("runtime factory must return RuntimeComponents")
    if not components.plugin_id.strip() or not components.plugin_version.strip():
        raise ValueError("runtime plugin identity/version required")
    pin.update(plugin_id=components.plugin_id, plugin_version=components.plugin_version)
    if expected is not None and any(pin[key] != expected.get(key) for key in ("plugin_id", "plugin_version")):
        raise ValueError("runtime plugin version changed since original run")
    return components, pin
