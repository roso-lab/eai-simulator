"""Collision policy for multi-robot EAI scenes.

The benchmark keeps robot↔scene contacts enabled while filtering contacts
between different robot entities. A robot entity contains its configured base
and separately authored mounted articulations (arms, sensors, and similar
attachments). This module does not change self-collision settings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _configured_host_names(
    articulations: Mapping[str, Any],
    controller_names: Sequence[str] | Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if controller_names is None:
        configured = tuple(articulations)
    elif isinstance(controller_names, Mapping):
        configured = tuple(controller_names)
    else:
        configured = tuple(controller_names)
    return tuple(sorted({name for name in configured if isinstance(name, str) and name.strip()}))


def _robot_entity_articulation_names(
    articulations: Mapping[str, Any],
    controller_names: Sequence[str] | Mapping[str, Any] | None,
) -> dict[str, tuple[str, ...]]:
    """Return deterministic host-to-articulation membership."""
    hosts = _configured_host_names(articulations, controller_names)
    grouped: dict[str, list[str]] = {name: [] for name in hosts}
    unassigned: list[str] = []
    for name in sorted(articulations):
        matches = [host for host in hosts if name == host or name.startswith(host + "_")]
        host = max(matches, key=len) if matches else None
        if host is None:
            unassigned.append(name)
        else:
            grouped[host].append(name)

    # Unregistered articulations remain explicit independent entities instead
    # of being silently omitted from the collision policy.
    memberships = {name: tuple(grouped[name]) for name in hosts if grouped[name]}
    memberships.update({name: (name,) for name in unassigned})
    return memberships


def _robot_entity_env_roots(
    articulations: Mapping[str, Any],
    controller_names: Sequence[str] | Mapping[str, Any] | None,
) -> dict[str, tuple[tuple[str, ...], ...]]:
    """Return each entity's root paths grouped by environment instance."""
    memberships = _robot_entity_articulation_names(articulations, controller_names)
    result: dict[str, tuple[tuple[str, ...], ...]] = {}
    for entity_name, articulation_names in memberships.items():
        per_articulation: list[tuple[str, ...]] = []
        for articulation_name in articulation_names:
            articulation = articulations[articulation_name]
            view = getattr(articulation, "root_physx_view", None)
            paths = tuple(str(path).strip() for path in getattr(view, "prim_paths", ()))
            if not paths:
                # During scene construction the PhysX tensor view is not
                # initialized yet, but the authored prims already exist.
                # Resolve the same articulation roots from its cfg pattern so
                # the filter can be authored before simulation starts.
                cfg = getattr(articulation, "cfg", None)
                pattern = getattr(cfg, "prim_path", None)
                suffix = getattr(cfg, "articulation_root_prim_path", None)
                if isinstance(pattern, str) and pattern:
                    if isinstance(suffix, str) and suffix:
                        pattern = pattern.rstrip("/") + "/" + suffix.lstrip("/")
                    try:
                        from isaaclab.sim import find_matching_prim_paths
                        paths = tuple(str(path).strip() for path in find_matching_prim_paths(pattern))
                    except Exception:
                        paths = ()
            # Attachment configs commonly identify the articulation root link
            # (for Z1 this is ``..._arm/link00``), while the authored asset's
            # complete compound lives below the attachment parent. Include
            # that parent so link01..link06 are part of the same entity.
            if articulation_name != entity_name and getattr(articulation, "cfg", None) is not None and paths:
                expanded = []
                for path in paths:
                    parent = path.rsplit("/", 1)[0]
                    expanded.append(parent if parent and parent != "/" else path)
                paths = tuple(dict.fromkeys(expanded))
            if not paths or any(not path.startswith("/") for path in paths):
                raise RuntimeError(
                    f"cannot resolve robot roots for collision filtering: {articulation_name}"
                )
            per_articulation.append(paths)
        env_counts = {len(paths) for paths in per_articulation}
        if len(env_counts) > 1:
            raise ValueError("robot articulation root lists must have the same environment count")
        env_count = next(iter(env_counts), 0)
        result[entity_name] = tuple(
            tuple(paths[env_index] for paths in per_articulation)
            for env_index in range(env_count)
        )
    return result


def robot_entity_roots(
    articulations: Mapping[str, Any],
    controller_names: Sequence[str] | Mapping[str, Any] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Return all root paths for each host robot entity.

    An articulation named ``<host>`` or ``<host>_<attachment>`` belongs to
    that host. Longest-prefix matching prevents ``robot_1`` from claiming
    ``robot_10``. Tuples are flattened in environment-major order for easy
    inspection; installation uses the environment-grouped form internally.
    """
    grouped = _robot_entity_env_roots(articulations, controller_names)
    return {
        entity_name: tuple(path for env_roots in envs for path in env_roots)
        for entity_name, envs in grouped.items()
    }


def robot_collision_filter_pairs(robot_roots: Mapping[str, Sequence[str]]) -> tuple[tuple[str, str], ...]:
    """Return deterministic unordered robot pairs within each environment."""
    names = tuple(sorted(robot_roots))
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("robot names must be non-empty strings")
    count = {len(tuple(robot_roots[name])) for name in names}
    if len(count) > 1:
        raise ValueError("robot root lists must have the same environment count")
    pairs: list[tuple[str, str]] = []
    env_count = next(iter(count), 0)
    for env_index in range(env_count):
        for index, name_a in enumerate(names):
            path_a = str(tuple(robot_roots[name_a])[env_index]).strip()
            if not path_a.startswith("/"):
                raise ValueError("robot root paths must be absolute USD paths")
            for name_b in names[index + 1:]:
                path_b = str(tuple(robot_roots[name_b])[env_index]).strip()
                if not path_b.startswith("/"):
                    raise ValueError("robot root paths must be absolute USD paths")
                pairs.append((path_a, path_b))
    return tuple(pairs)


def install_robot_robot_collision_filter(
    env: Any,
    *,
    scope_path: str = "/World/robot_robot_collision_filters",
) -> dict[str, Any]:
    """Author robot↔robot PhysX collision groups for every environment.

    Each group contains the host robot and all mounted articulation roots for
    that environment. Only different host groups are filtered; robot↔scene
    contacts and each entity's self-collision configuration are untouched.
    """
    from pxr import PhysxSchema, Sdf, Usd, UsdGeom, UsdPhysics

    stage = env.sim.get_initial_stage()
    # PhysX gives ``filteredGroups`` opposite meaning when this scene-level
    # switch is enabled. Set it explicitly so the policy is deterministic
    # across scene assets and Isaac Sim defaults.
    physics_scene = next(
        (prim for prim in stage.Traverse() if prim.IsA(UsdPhysics.Scene)), None
    )
    if physics_scene is not None and physics_scene.IsValid():
        PhysxSchema.PhysxSceneAPI.Apply(physics_scene)
        PhysxSchema.PhysxSceneAPI(physics_scene).CreateInvertCollisionGroupFilterAttr().Set(False)
    if not stage.GetPrimAtPath(scope_path).IsValid():
        UsdGeom.Scope.Define(stage, scope_path)

    articulations = env.scene.articulations
    configured = getattr(getattr(env, "cfg", None), "controllers", None)
    entity_env_roots = _robot_entity_env_roots(articulations, configured)
    entity_names = tuple(sorted(entity_env_roots))
    groups: dict[tuple[int, str], Any] = {}
    group_paths: dict[tuple[int, str], Sdf.Path] = {}
    group_colliders: dict[tuple[int, str], tuple[str, ...]] = {}

    def collider_paths(root_paths: Sequence[str]) -> tuple[str, ...]:
        """Resolve concrete collision prims below an articulation root.

        Collection expansion of an articulation reference is not reliable for
        every Isaac asset (especially referenced/instanced robot links).  Add
        the concrete collision prims explicitly so PhysX receives the same
        entity membership that the policy reports.
        """
        resolved: list[str] = []
        for root_path in root_paths:
            root = stage.GetPrimAtPath(root_path)
            if not root.IsValid():
                raise RuntimeError(f"robot collision root does not exist: {root_path}")
            found = []
            for prim in Usd.PrimRange(root):
                path_text = str(prim.GetPath())
                # Referenced robot assets can place CollisionAPI on an
                # ancestor or in a composed layer that is not reported by
                # HasAPI on the generated mesh.  Their collision hierarchy
                # is still explicit in the prim path; include those concrete
                # descendants so attached articulation links are covered.
                is_collision_path = (
                    "/collisions/" in path_text
                    or path_text.endswith("/collisions")
                    or "/collision/" in path_text
                    or path_text.endswith("/collision")
                )
                if prim.HasAPI(UsdPhysics.CollisionAPI) or is_collision_path:
                    found.append(str(prim.GetPath()))
            # Keep the root as a fallback for assets whose collision API is
            # authored on an inherited/reference boundary.
            resolved.extend(found or [root_path])
        return tuple(dict.fromkeys(resolved))

    for entity_name in entity_names:
        safe_name = "".join(char if char.isalnum() else "_" for char in entity_name)
        for env_index, root_paths in enumerate(entity_env_roots[entity_name]):
            group_path = f"{scope_path}/env_{env_index}/{safe_name}_group"
            group = UsdPhysics.CollisionGroup.Define(stage, group_path)
            collection = Usd.CollectionAPI.Apply(group.GetPrim(), "colliders")
            collection.CreateExpansionRuleAttr().Set("expandPrims")
            # Keep articulation roots in the collection as well as concrete
            # shapes. PhysX treats an attached articulation as a compound;
            # root membership is required for filtering the whole compound.
            colliders = tuple(dict.fromkeys(tuple(root_paths) + collider_paths(root_paths)))
            collection.CreateIncludesRel().SetTargets([Sdf.Path(path) for path in colliders])
            groups[(env_index, entity_name)] = group
            group_paths[(env_index, entity_name)] = Sdf.Path(group_path)
            group_colliders[(env_index, entity_name)] = colliders

    env_count = max((len(envs) for envs in entity_env_roots.values()), default=0)
    filtered_pairs = 0
    for env_index in range(env_count):
        present = [name for name in entity_names if env_index < len(entity_env_roots[name])]
        for index, name_a in enumerate(present):
            for name_b in present[index + 1:]:
                groups[(env_index, name_a)].GetFilteredGroupsRel().AddTarget(
                    group_paths[(env_index, name_b)]
                )
                groups[(env_index, name_b)].GetFilteredGroupsRel().AddTarget(
                    group_paths[(env_index, name_a)]
                )
                filtered_pairs += 1

    return {
        "enabled": True,
        "robot_count": len(entity_names),
        "environment_count": env_count,
        "filtered_robot_pairs": filtered_pairs,
        "scope_path": scope_path,
        "robot_entities": entity_names,
        "group_colliders": {
            f"env_{env_index}/{name}": paths
            for (env_index, name), paths in group_colliders.items()
        },
        "invert_collision_group_filter": False,
    }


__all__ = [
    "install_robot_robot_collision_filter",
    "robot_collision_filter_pairs",
    "robot_entity_roots",
]
