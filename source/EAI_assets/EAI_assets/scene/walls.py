import isaaclab.sim as sim_utils
import isaaclab.terrains as terrain_gen
from isaaclab.assets import RigidObjectCfg
from isaaclab.sim.spawners.spawner_cfg import SpawnerCfg
from isaaclab.utils import configclass
from collections.abc import Callable
from isaaclab.sim.spawners.from_files import from_files, UsdFileCfg
from isaaclab.sim.spawners import materials

from EAI_assets.asset_resolver import asset_path

walls_path = asset_path("scene/indoor/walls.usd")

# 1. 地面配置 (Flat Plane) - 使用 TerrainImporterCfg

@configclass
class WallsCfg(UsdFileCfg):
    usd_path: str = walls_path
# 实例化
WALLS_CFG = WallsCfg()
