"""Warehouse NVD benchmark overlay scene.

Loads ``usd/scene/warehouse_nvd/warehouse_nvd_bench.usd`` -- a runtime
overlay that references the local cm-units NVIDIA XR warehouse at 0.01
scale and adds the audited collision proxy groups built from the height-aware
occupancy products:

- ground walls (0-0.8 m, from ``ground_map_0.6m``),
- aerial collision boxes (0.5-6 m, from ``drone_voxels_0.25m``),
- mezzanine slab at its true height, and the ground floor plate.

Dependencies are installed under ``usd/assets/nvd`` with relative USD paths.
Use ``demo.warehouse_benchmark.main --check-assets`` before launch. Production
assets remain ignored by Git. Physics comes from the collision proxy layer.
"""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass
from EAI_assets.asset_resolver import asset_path

_WAREHOUSE_NVD_USD = asset_path("scene/warehouse_nvd/warehouse_nvd_bench.usd")


@configclass
class WarehouseNVDCfg(UsdFileCfg):
    """Warehouse NVD overlay scene configuration for the EAI simulator."""

    usd_path: str = str(_WAREHOUSE_NVD_USD)


WAREHOUSE_NVD_CFG = WarehouseNVDCfg()
