"""HSSD-Hab room scene converted from Blender for Isaac Sim 5.1.

Source: HSSD-Hab 108736722_177263382 (CC BY-NC 4.0), assembled in Blender with
the wxyz + C@H@C^-1 contract and postprocessed to a metric Z-up USD with static
mesh collision on every visual mesh plus indoor fill lighting. The USD lives
outside the repository tree, so the absolute local path is intentional; move
the file under ``usd/scene/hssd_room1/`` and switch to a relative provider
path before any tracked publication.
"""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path


HSSD_ROOM1_USD_PATH = asset_path(
    "/home/pupil/scene_datasets/hssd_isaac_v1/108736722_177263382/108736722_177263382.usda"
)


@configclass
class HssdRoom1Cfg(UsdFileCfg):
    """Load the converted HSSD room package as-is (already metres, Z-up)."""

    usd_path: str = HSSD_ROOM1_USD_PATH


HSSD_ROOM1_CFG = HssdRoom1Cfg()
