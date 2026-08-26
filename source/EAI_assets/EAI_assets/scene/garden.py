from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path


GARDEN_USD_PATH = asset_path("scene/garden/garden.usd")


@configclass
class GardenCfg(UsdFileCfg):
    usd_path: str = GARDEN_USD_PATH


GARDEN_CFG = GardenCfg()
