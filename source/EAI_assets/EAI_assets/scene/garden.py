import isaaclab.sim as sim_utils
import isaaclab.terrains as terrain_gen
from isaaclab.assets import RigidObjectCfg
from isaaclab.sim.spawners.spawner_cfg import SpawnerCfg
from isaaclab.utils import configclass
from collections.abc import Callable
from isaaclab.sim.spawners.from_files import from_files, UsdFileCfg
from isaaclab.sim.spawners import materials

from EAI_assets.asset_resolver import asset_path

garden_path = asset_path("scene/plant/plant_mesh.usdc")

# 1. 地面配置 (Flat Plane) - 使用 TerrainImporterCfg

print(f"[DEBUG] Garden USD Path: {garden_path}") # 建议保留打印，方便排查

@configclass
class GardenCfg(UsdFileCfg):
    usd_path: str = garden_path
# 实例化
GARDEN_CFG = GardenCfg()
