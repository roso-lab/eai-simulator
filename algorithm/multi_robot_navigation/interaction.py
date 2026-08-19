"""Pure helpers shared by the Isaac viewport interaction layer."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _assembly_path(path: object, robot_name: str) -> str | None:
    value = str(path or "").strip()
    if not value.startswith("/"):
        return None
    parts = value.split("/")
    try:
        index = max(i for i, part in enumerate(parts) if part == robot_name)
    except ValueError:
        return None
    return "/".join(parts[: index + 1])


def discover_robot_prim_paths(
    base_env: Any, robot_names: Sequence[str]
) -> dict[str, str]:
    """Return the concrete USD assembly path for every managed robot.

    Isaac Lab's articulation view points at the physics root, which may be a
    child of the assembly selected in the viewport. The instance name is used
    to trim that path back to the complete robot assembly.
    """

    articulations = getattr(getattr(base_env, "scene", None), "articulations", {})
    paths: dict[str, str] = {}
    for name in robot_names:
        robot = articulations.get(name)
        root_view = getattr(robot, "root_physx_view", None)
        concrete_paths = getattr(root_view, "prim_paths", ()) or ()
        for concrete in concrete_paths:
            assembly = _assembly_path(concrete, name)
            if assembly:
                paths[name] = assembly
                break
        if name in paths:
            continue

        configured = str(getattr(getattr(robot, "cfg", None), "prim_path", "") or "")
        configured = configured.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
        configured = configured.replace("env_.*", "env_0").replace("env_*", "env_0")
        assembly = _assembly_path(configured, name)
        paths[name] = assembly or f"/World/envs/env_0/{name}"
    return paths


def resolve_robot_from_prim_path(
    hit_prim_path: object, robot_prim_paths: Mapping[str, str]
) -> str | None:
    """Resolve a picked robot descendant to its EAI instance name."""

    hit = str(hit_prim_path or "")
    matches = [
        (name, str(root))
        for name, root in robot_prim_paths.items()
        if hit == str(root) or hit.startswith(f"{root}/")
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[1]))[0]


__all__ = ["discover_robot_prim_paths", "resolve_robot_from_prim_path"]
