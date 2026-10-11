"""Versioned external runtime factory loaded by both run and resume.

No dependency on a particular benchmark package, scenario, robot or evaluator.
Factories must be lightweight: open Isaac only inside SessionBackend.open_scene.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import inspect
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import BranchPlan


DEPENDENCY_GROUPS = ("controllers", "models", "assets", "external_inputs")


@dataclass(frozen=True)
class RuntimeComponents:
    adapter: Any
    session_backend: Any
    scheduler: Any
    plugin_id: str
    plugin_version: str
    executor_options: Mapping[str, Any] = field(default_factory=dict)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_dependency(path: Path) -> tuple[str, str]:
    if path.is_file():
        return "file", _sha256_file(path)
    if not path.is_dir():
        raise ValueError(f"runtime dependency is not a regular file or directory: {path}")
    digest = hashlib.sha256()
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        directories.sort()
        for name in sorted((*directories, *files)):
            child = root_path / name
            if child.is_symlink():
                raise ValueError(f"runtime dependency directories cannot contain symlinks: {child}")
        for name in sorted(files):
            child = root_path / name
            if not child.is_file():
                raise ValueError(f"runtime dependency contains a non-regular file: {child}")
            relative = child.relative_to(path).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(bytes.fromhex(_sha256_file(child)))
    return "directory_tree", digest.hexdigest()


def _normalize_dependencies(value: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, Mapping):
        raise ValueError("runtime_dependencies must be an object")
    unknown = set(value) - set(DEPENDENCY_GROUPS)
    if unknown:
        raise ValueError(f"unknown runtime dependency groups: {sorted(unknown)}")
    normalized: dict[str, list[dict[str, str]]] = {}
    for group in DEPENDENCY_GROUPS:
        if group not in value:
            continue
        entries = value[group]
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise ValueError(f"runtime_dependencies.{group} must be a list")
        pinned = []
        seen: set[str] = set()
        for entry in entries:
            declared_digest = None
            declared_kind = None
            if isinstance(entry, (str, os.PathLike)):
                raw_path = os.fspath(entry)
            elif isinstance(entry, Mapping):
                unexpected = set(entry) - {"path", "sha256", "kind"}
                if unexpected or "path" not in entry:
                    raise ValueError(f"invalid runtime dependency declaration in {group}")
                raw_path = entry["path"]
                declared_digest = entry.get("sha256")
                declared_kind = entry.get("kind")
            else:
                raise ValueError(f"invalid runtime dependency declaration in {group}")
            if not isinstance(raw_path, (str, os.PathLike)) or not os.fspath(raw_path):
                raise ValueError(f"runtime dependency path in {group} must be non-empty")
            try:
                path = Path(raw_path).expanduser().resolve(strict=True)
            except FileNotFoundError as exc:
                raise ValueError(f"runtime dependency is missing: {raw_path}") from exc
            path_text = str(path)
            if path_text in seen:
                raise ValueError(f"runtime dependency declared more than once: {path_text}")
            seen.add(path_text)
            kind, current_digest = _digest_dependency(path)
            if declared_kind is not None and declared_kind != kind:
                raise ValueError(f"runtime dependency kind changed: {path_text}")
            if declared_digest is not None:
                if not isinstance(declared_digest, str) or len(declared_digest) != 64:
                    raise ValueError(f"invalid runtime dependency sha256: {path_text}")
                try:
                    int(declared_digest, 16)
                except ValueError as exc:
                    raise ValueError(f"invalid runtime dependency sha256: {path_text}") from exc
                if declared_digest.lower() != current_digest:
                    raise ValueError(f"runtime dependency digest changed: {path_text}")
            pinned.append({"path": path_text, "kind": kind, "sha256": current_digest})
        normalized[group] = pinned
    return normalized


def _reject_changed(actual: Mapping[str, Any], expected: Mapping[str, Any], keys: Sequence[str],
                    message: str) -> None:
    if any(actual.get(key) != expected.get(key) for key in keys):
        raise ValueError(message)


def load_runtime(config: Mapping[str, Any], *, plan: BranchPlan, run_dir: Path,
                 expected: Mapping[str, Any] | None = None) -> tuple[RuntimeComponents, dict]:
    spec = str(config.get("runtime_factory", ""))
    if ":" not in spec:
        raise ValueError("runtime_factory must be an importable module:factory")
    kwargs = dict(config.get("runtime_kwargs", {}))
    pin = {"runtime_factory": spec, "runtime_kwargs": kwargs}
    if expected is not None:
        _reject_changed(
            pin, expected, ("runtime_factory", "runtime_kwargs"),
            "runtime factory/config changed since original run; start a new run",
        )

    has_dependencies = "runtime_dependencies" in config
    expected_has_dependencies = expected is not None and "runtime_dependencies" in expected
    if expected is not None and has_dependencies != expected_has_dependencies:
        raise ValueError("runtime dependency declaration changed since original run; start a new run")
    if has_dependencies:
        dependency_pin = _normalize_dependencies(config["runtime_dependencies"])
        pin["runtime_dependencies"] = dependency_pin
        if expected is not None and dependency_pin != expected.get("runtime_dependencies"):
            raise ValueError("runtime dependencies changed since original run; start a new run")

    module_name, name = spec.split(":", 1)
    factory = getattr(importlib.import_module(module_name), name)
    source = inspect.getsourcefile(factory)
    if not source:
        raise ValueError("runtime factory requires auditable Python source")
    pin["factory_sha256"] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if expected is not None:
        _reject_changed(
            pin, expected, ("factory_sha256",),
            "runtime factory/config changed since original run; start a new run",
        )
    components = factory(plan=plan, run_dir=run_dir, **pin["runtime_kwargs"])
    if not isinstance(components, RuntimeComponents):
        raise TypeError("runtime factory must return RuntimeComponents")
    if not components.plugin_id.strip() or not components.plugin_version.strip():
        raise ValueError("runtime plugin identity/version required")
    pin.update(plugin_id=components.plugin_id, plugin_version=components.plugin_version)
    if expected is not None and any(pin[key] != expected.get(key) for key in ("plugin_id", "plugin_version")):
        raise ValueError("runtime plugin version changed since original run")
    return components, pin
