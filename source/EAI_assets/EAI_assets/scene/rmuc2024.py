"""RMUC 2024 arena imported from the Gazebo certification scene."""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path


RMUC2024_USD_PATH = asset_path("scene/rmuc2024/rmuc2024.usda")
RMUC2024_MESH_USD_PATH = asset_path("scene/rmuc2024/rmuc2024_mesh.usd")


@configclass
class RMUC2024Cfg(UsdFileCfg):
    usd_path: str = RMUC2024_USD_PATH
    asset_dependencies = (RMUC2024_MESH_USD_PATH,)


RMUC2024_CFG = RMUC2024Cfg()
