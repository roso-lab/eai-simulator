"""Restaurant scene authored in centimeters and mounted in the metre runtime."""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path


RESTAURANT_USD_PATH = asset_path("scene/restaurant/Restaurant_Only.usda")
RESTAURANT_SOURCE_METERS_PER_UNIT = 0.01


@configclass
class RestaurantCfg(UsdFileCfg):
    """Load the local restaurant package without changing its source layers."""

    usd_path: str = RESTAURANT_USD_PATH
    scale: tuple[float, float, float] = (
        RESTAURANT_SOURCE_METERS_PER_UNIT,
        RESTAURANT_SOURCE_METERS_PER_UNIT,
        RESTAURANT_SOURCE_METERS_PER_UNIT,
    )


RESTAURANT_CFG = RestaurantCfg()
