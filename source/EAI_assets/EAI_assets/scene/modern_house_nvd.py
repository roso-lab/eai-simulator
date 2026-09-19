"""Modern House NVD benchmark overlay scene.

Loads ``usd/scene/modern_house_nvd/modern_house_nvd_bench.usd`` -- a runtime
overlay referencing the local cm-units NVIDIA XR Modern House at 0.01
scale with the audited collision proxy groups from the height-aware occupancy
products. Same construction as the Warehouse NVD overlay; see that module for
the proxy-layer contract.

The demo preparation disables the unsupported scene-wide mezzanine bounding
box. Ground-floor proxies remain; elevated navigation has no validated surface.
"""

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass
from EAI_assets.asset_resolver import asset_path

_MODERN_HOUSE_USD = asset_path("scene/modern_house_nvd/modern_house_nvd_bench.usd")


@configclass
class ModernHouseNVDCfg(UsdFileCfg):
    """Modern House NVD overlay scene configuration for the EAI simulator."""

    usd_path: str = str(_MODERN_HOUSE_USD)


MODERN_HOUSE_NVD_CFG = ModernHouseNVDCfg()
