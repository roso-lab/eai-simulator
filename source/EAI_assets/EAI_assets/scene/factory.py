from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path

FACTORY_USD_PATH = asset_path("scene/factory/factory.usd")


@configclass
class FactoryCfg(UsdFileCfg):
    """Factory scene configuration for EAI simulator."""

    usd_path: str = FACTORY_USD_PATH


FACTORY_CFG = FactoryCfg()
