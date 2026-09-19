from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.sim.spawners.from_files import from_files as _from_files
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path

FACTORY_USD_PATH = asset_path("scene/factory/factory.usd")


# Decorative light rigs shipped with the converted asset. Each embeds two
# OmniGraph action graphs and a PhysicsScene; they are not part of the
# navigation environment and their render load lengthens physics steps enough
# to race articulation state reads. Removed wholesale (maintainer-approved).
_FACTORY_STRIP_SUBTREES = ("quicktrun_light", "quicktrun_light_01")


def _spawn_factory_without_embedded_physics_scenes(
    prim_path, cfg, translation=None, orientation=None, **kwargs
):
    """Spawn the factory stage, then strip destabilizing embedded content.

    The converted provider asset ships two kinds of embedded runtime prims
    that destabilize the composed simulation:

    - three ``PhysicsScene`` prims (``/physicsScene`` plus two under the
      ``quicktrun_light`` rigs): combined with the InteractiveScene's own
      physics scene they desynchronize per-scene step subscriptions;
    - four embedded OmniGraph action graphs (``ActionGraph_chuck`` /
      ``ActionGraph_door`` in both ``quicktrun_light`` rigs, 26 nodes in
      total): they self-evaluate on playback/physics ticks, race the
      simulator loop, and surface as per-step
      ``PxDirectGPUAPI::getArticulationData`` PhysX error floods and
      ``carb::tasking`` recursion assertions.

    The decorative light rigs carrying them are deactivated wholesale, and any
    PhysicsScene/OmniGraph prims elsewhere under the mount are deactivated as a
    safety net. Physics and animation of the composed world are owned by the
    InteractiveScene and its own graphs.
    """
    prim = _from_files.spawn_from_usd(prim_path, cfg, translation, orientation, **kwargs)

    import omni.usd
    from pxr import Usd
    from isaaclab.sim.utils import find_matching_prim_paths

    stage = omni.usd.get_context().get_stage()
    parent_regex = prim_path.rpartition("/")[0]
    matched_parents = find_matching_prim_paths(parent_regex)
    if not matched_parents:
        print(f"[FactoryScene] Warning: no parents found matching: {parent_regex}")
        matched_parents = []

    removable_roots: list[str] = []
    removed_subtrees: list[str] = []
    for parent in matched_parents:
        root = stage.GetPrimAtPath(str(parent))
        if not root or not root.IsValid():
            continue
        for candidate in Usd.PrimRange(root):
            path = str(candidate.GetPath())
            if any(path.startswith(f"{kept}/") for kept in removable_roots):
                continue  # nested below an already-collected root
            if candidate.GetName() in _FACTORY_STRIP_SUBTREES:
                removable_roots.append(path)
                removed_subtrees.append(candidate.GetName())
                continue
            type_name = candidate.GetTypeName()
            if type_name == "PhysicsScene" or "OmniGraph" in type_name:
                removable_roots.append(path)
    # Deactivate instead of RemovePrim: removing prims mid-scene-assembly
    # leaves dangling native handles in PhysX/OmniGraph, and any prim spawned
    # afterwards (for example GeneratedScenario rigid bodies) then crashes the
    # process with a silent native exit(0). Deactivation neutralizes the same
    # subtrees through a USD-safe path.
    for path in removable_roots:
        stage.GetPrimAtPath(path).SetActive(False)
    if removed_subtrees:
        print(
            f"[FactoryScene] Deactivated decorative light rigs: "
            + ", ".join(sorted(set(removed_subtrees)))
        )
    embedded_count = len(removable_roots) - len(removed_subtrees)
    if embedded_count > 0:
        print(
            f"[FactoryScene] Deactivated {embedded_count} embedded runtime prim(s) "
            "(PhysicsScene / OmniGraph); physics and graphs are owned by the "
            "InteractiveScene."
        )
    return prim


@configclass
class FactoryCfg(UsdFileCfg):
    """Factory scene configuration for EAI simulator."""

    usd_path: str = FACTORY_USD_PATH
    func = _spawn_factory_without_embedded_physics_scenes


FACTORY_CFG = FactoryCfg()
