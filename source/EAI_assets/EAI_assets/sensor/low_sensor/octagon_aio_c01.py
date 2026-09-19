# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause
"""Reusable OCTAGON-AIO-C01 visual attachment configuration.

The provider asset supplies the manufacturer visual with spec-derived
materials baked into the CAD layer. The native OmniLidar and ROS 2
publication runtime are owned by ``EAI.hmrs_ros.simple_car_sensor_suite`` so
a host receives exactly one sensor runtime with selection-driven output
gates. Hosts spawn this payload on their native lidar mount
(``lidar_mount_link`` + ``lidar_offset``), the same placement as the RosLidar
payload, without adapter geometry.
"""

import isaaclab.sim as sim_utils
from isaaclab.assets import AssetBaseCfg
from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path

OCTAGON_AIO_C01_RAW_USD_PATH = asset_path(
    "payloads/sensors/octagon_aio_c01/octagon_aio_c01.usd"
)
OCTAGON_AIO_C01_USD_PATH = asset_path(
    "payloads/sensors/octagon_aio_c01/octagon_aio_c01_visual.usda"
)
OCTAGON_AIO_C01_PROFILE_PATH = asset_path(
    "payloads/sensors/octagon_aio_c01/OCTAGON_AIO_C01.json"
)


@configclass
class OctagonAioC01Cfg(AssetBaseCfg):
    spawn = UsdFileCfg(usd_path=OCTAGON_AIO_C01_USD_PATH)
    asset_dependencies = (
        OCTAGON_AIO_C01_RAW_USD_PATH,
        OCTAGON_AIO_C01_PROFILE_PATH,
    )


__all__ = [
    "OCTAGON_AIO_C01_PROFILE_PATH",
    "OCTAGON_AIO_C01_RAW_USD_PATH",
    "OCTAGON_AIO_C01_USD_PATH",
    "OctagonAioC01Cfg",
]
