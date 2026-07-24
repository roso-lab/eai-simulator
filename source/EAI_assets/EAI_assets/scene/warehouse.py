import isaaclab.sim as sim_utils
import isaaclab.terrains as terrain_gen
from isaaclab.assets import RigidObjectCfg
from isaaclab.sim.spawners.spawner_cfg import SpawnerCfg
from isaaclab.utils import configclass
from collections.abc import Callable
from isaaclab.sim.spawners.from_files import from_files, UsdFileCfg
from isaaclab.sim.spawners import materials

from EAI_assets.asset_resolver import asset_path

warehouse_path = asset_path("scene/warehouse/warehouse.usd")

# 1. 地面配置 (Flat Plane) - 使用 TerrainImporterCfg

@configclass
class WarehouseCfg(UsdFileCfg):
    usd_path: str = warehouse_path
# 实例化
WAREHOUSE_CFG = WarehouseCfg()
