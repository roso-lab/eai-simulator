import isaaclab.sim as sim_utils
import isaaclab.terrains as terrain_gen
from isaaclab.utils import configclass
from collections.abc import Callable

from isaaclab.terrains import TerrainImporterCfg

from EAI_assets.asset_resolver import asset_path

floor_path = asset_path("scene/indoor/floor.usd")

# 1. 地面配置 (Flat Plane) - 使用 TerrainImporterCfg

@configclass
class FloorCfg(TerrainImporterCfg):
    """
    地板配置：使用自定义 USD 文件。
    """
    # 1. 在仿真世界中的路径 (这一步很重要，不要和 robots 的路径重复)
    terrain_type="usd"
    # 2. 你的 USD 文件路径 (填你自己的路径)
    # 建议使用绝对路径，或者相对于项目根目录的路径
    usd_path = floor_path
    
    # 3. (可选) 物理材质设置
    # 为了防止机器人打滑，通常给地板一个高摩擦力
    # physics_material = sim_utils.RigidBodyMaterialCfg(
    #     friction_combine_mode="multiply",
    #     restitution_combine_mode="multiply",
    #     static_friction=1.0,  # 静摩擦
    #     dynamic_friction=1.0, # 动摩擦
    #     restitution=0.0,      # 弹性 (0代表不反弹)
    # )
