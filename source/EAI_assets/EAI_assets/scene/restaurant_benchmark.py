"""Local Restaurant benchmark overlay selected from an explicit staging root."""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.restaurant_benchmark_products import (
    resolve_restaurant_benchmark_products,
)


_PRODUCTS = resolve_restaurant_benchmark_products()


@configclass
class RestaurantBenchmarkCfg(UsdFileCfg):
    """Load a hash-verified local candidate without publishing it as an asset."""

    usd_path: str = str(_PRODUCTS.overlay_usd)
    asset_dependencies = (str(_PRODUCTS.map_png),)


RESTAURANT_BENCHMARK_CFG = RestaurantBenchmarkCfg()
