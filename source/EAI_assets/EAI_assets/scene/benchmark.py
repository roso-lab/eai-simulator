"""Locally imported benchmark scenes; their source USD layers stay unmodified."""

from isaaclab.sim.spawners.from_files import UsdFileCfg, from_files
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path


def _spawn_restaurant(prim_path, cfg, translation=None, orientation=None, **kwargs):
    """The ArchVis restaurant needs static triangle collisions for robot use."""
    prim = from_files.spawn_from_usd(prim_path, cfg, translation, orientation, **kwargs)
    from pxr import Usd, UsdGeom, UsdPhysics

    for child in Usd.PrimRange(prim):
        if not child.IsA(UsdGeom.Mesh) or child.HasAPI(UsdPhysics.CollisionAPI):
            continue
        if UsdGeom.Imageable(child).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        mesh = UsdGeom.Mesh(child)
        # The imported pack contains empty mesh placeholders alongside real geometry.
        if not mesh.GetPointsAttr().Get() or not mesh.GetFaceVertexCountsAttr().Get():
            continue
        UsdPhysics.CollisionAPI.Apply(child).CreateCollisionEnabledAttr(True)
        UsdPhysics.MeshCollisionAPI.Apply(child).CreateApproximationAttr("none")
    return prim


@configclass
class CityCfg(UsdFileCfg):
    usd_path: str = asset_path("scene/city/city_static.usd")


@configclass
class WarehouseNvdCfg(UsdFileCfg):
    # The benchmark wrapper already converts the provider's centimeters to meters.
    usd_path: str = asset_path("scene/warehouse_nvd/warehouse_nvd_bench.usd")


@configclass
class RestaurantCfg(UsdFileCfg):
    usd_path: str = asset_path("scene/restaurant/Restaurant_Only.usda")
    scale: tuple[float, float, float] = (0.01, 0.01, 0.01)
    func = _spawn_restaurant


@configclass
class ModernHouseNvdCfg(UsdFileCfg):
    usd_path: str = asset_path("scene/modern_house_nvd/modern_house_nvd_bench.usd")


@configclass
class FactoryBenchmarkCfg(UsdFileCfg):
    # Keep the existing Fire Rescue factory and this imported variant independent.
    usd_path: str = asset_path("scene/factory_benchmark/factory.usd")


CITY_CFG = CityCfg()
WAREHOUSE_NVD_CFG = WarehouseNvdCfg()
RESTAURANT_CFG = RestaurantCfg()
MODERN_HOUSE_NVD_CFG = ModernHouseNvdCfg()
FACTORY_BENCHMARK_CFG = FactoryBenchmarkCfg()
