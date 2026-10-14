# G1 固定站位抓放 skill

技能 ID：`eai.manipulation.g1_pick_place`。完成初始化后，每次只需给出物体起点和放置终点的世界坐标。无需输入吧台、桌子、托盘的位置，也无需知道场景类别。

```python
skill.start(pick_position=[x1, y1, z1], place_position=[x2, y2, z2])
```

第一版范围：固定站位、右侧 Inspire 灵巧手、直立物体、USD 运动学执行和理想刚性附着。沿用已验收的瓶颈/杯柄手指握持标定。不会移动 G1 底座，不会驱动真实机器人或正在运行的 PhysX articulation。

## 一次初始化，每次两个坐标

```python
from task.executors.g1_pick_place import G1PickPlaceSkill, ObjectBinding

# stage 由宿主提供：Z-up，米制；机器人和物体为运动学场景。
# 物体注册用于把坐标对应到真实 USD 对象，以及选择它的握持标定。
skill = G1PickPlaceSkill(
    stage,
    robot_path="/World/G1",
    objects={
        "bottle": ObjectBinding("/World/Bottle", "bottle_neck"),
        "glass": ObjectBinding("/World/Glass", "glass_stem"),
    },
)

skill.start(
    pick_position=[-0.26, 24.35, 1.17058605],
    place_position=[0.16, 24.36, 1.171294],
)
# 每次宿主更新时调用；dt 是秒，默认不超过 0.1 秒。
while skill.status == "running":
    feedback = skill.step(1 / 60)
    # 宿主在这里更新显示。技能本身不推进仿真时间、不启动 simulator。
```

示例坐标属于原演示中的 G1 站位，不是技能的默认世界位置；实际调用时替换为自己的物体和目标坐标。G1 可以整体平移和绕世界 Z 轴旋转。超出固定手臂可达范围会拒绝执行，不会自动走过去。

坐标语义：两个位置都表示物体根 Xform 原点的世界 XYZ，单位米；对于居中模型即物体中心。`place_position.z` 也表示物体原点高度，不是桌面高度。技能不推断支撑面：上层给出所需落点。根 Xform 必须直立、无缩放，缩放需烘焙进几何。

默认在起点 2 厘米内选择唯一已注册物体。没有匹配时拒绝；若多个物体重合，可显式加 `object_ref="bottle"`。不依据坐标猜测物体种类，不自动变更物体大小。

## 自动动作与场景边界

先验证整条关节路径可达，再执行：预备抬手 → 到物体上方 → 下降对准指间握持点 → 合指 → 抬升 → 水平搬运 → 下降放置 → 张指 → 向上撤手 → 收臂。

搬运高度为起点/终点握持高度的较高值加 8 厘米，可在初始化时使用 `G1PickPlaceConfig(lift_height_m=...)` 调整。预备点使用 G1 底座局部坐标，并随底座位置和朝向变换，未写死酒吧坐标。

默认 `obstacles=()`，无需任何环境障碍位置信息。只知道起点和终点，不能判断途中是否存在墙、吧台或其他物体；抬升路径不等同于未知环境自动避障。可选 `ObstacleBox` 仅为具备额外环境信息的宿主提供机器人双臂视觉网格采样检查，不是调用必需参数，也不包括携带物体体积、自碰撞或全场景碰撞证明。

闭合后固定物体与指间握持坐标系的相对变换，搬运中不改绑定点，放置时不瞬移纠正误差。反馈 `status="completed"` 表示运动序列完成；`physical_success=None`，不表示接触力抓取成功。

## 物体与手的标定

内置 `bottle_neck` 对应原演示缩小到 72% 的瓶子（整体约 0.100 × 0.100 × 0.341 米，瓶颈半径约 0.012 米），`glass_stem` 对应原演示酒杯的杯柄。标定文件为 `task/executors/g1_manipulation/inspire_grips.json`，记录实际 USD 手指闭合角度、手掌局部握持点、物体局部握持点和原始几何证据。

初始化后的首次规划会核对内置标定对应的 G1 源资产 SHA-256，并拒绝缺失几何或尺寸不符的物体；包围盒尺寸核对不代表自动识别物体形状，形状仍由宿主注册保证。

换成不同大小或形状的物体，初始化时应绑定经校准的 `GraspProfile`，不能直接复用旧标定并声称握牢。当前自定义配置要求直立轴对称握持，物体握持点在局部 Z 轴上；不支持随意姿态、把手侧抓或左手。内置配置是资产标定，不是从坐标进行视觉识别。

## 状态与恢复

- `start()` 规划失败不会写入 USD、移动物体或改变机器人姿态。
- `step(dt)` 推进动作并返回状态、阶段、附着状态、事件与错误。执行时间默认上限 90 秒，手臂与手指关节速度默认上限 0.75 rad/s。
- `stop()` 暂停，握持物体保持原位；`resume()` 继续主动暂停的动作。
- `cancel()` 仅允许尚未附着或已释放的动作；持物时禁止直接丢弃。
- 宿主修改 G1 底座、机器人 link 或当前物体时停止执行。执行故障保留已经建立的附着，需要宿主显式处理场景后创建新的执行器恢复，不会自动释放物体。
- 一个机器人同时只能由一个执行器写入。使用期间保持时间轴暂停；不能让物理引擎同时更新这些 link。

## Provider / 上层任务调度

纯 Python provider 不依赖 Isaac、USD、ROS 或 Torch：

```python
from algorithm.skills import SkillRequest
from algorithm.skills.g1_pick_place import G1PickPlaceProvider

plan = G1PickPlaceProvider().plan(SkillRequest(
    request_id="move-bottle-1",
    skill_id="eai.manipulation.g1_pick_place",
    robot_id="g1_1",
    parameters={
        "pick_position": [-0.26, 24.35, 1.17058605],
        "place_position": [0.16, 24.36, 1.171294],
    },
))
# provider 验证参数；runtime 负责实际场景绑定、可达性与动作。
skill.start_plan(plan)
```

合同版本 `simulator.skill.v1`。仅接受上述两个必填坐标以及可选 `object_ref`、`frame="world"`。非法坐标、未知参数和不支持的版本被拒绝。该独立单手技能不等于旧 G1 双手抱箱/行走搬运技能。

## 本地验证

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest algorithm/skills/tests -q
```

运行时测试需要本地 G1 Inspire USD；缺少 USD 或 pxr 时会明确跳过，不能把纯合同测试当作实际抓取验证。测试在无吧台/桌子几何的 USD 场景中用两种尺寸代理物体验证、整体平移旋转、变更落点高度、保持附着、暂停恢复、错误请求、不可达、外部修改与超时。另用原酒吧画面检查实际手指握持；演示现在直接调用本技能，默认不提供障碍坐标。

## 系统运行入口

以上 `G1PickPlaceSkill` 是暂停 USD 的几何接口。并行环境和多机器人任务使用
`task.executors.manipulation.ManipulationSkillRuntime`，推荐
`eai.manipulation.pick_place`，显式 object_ref 加两个世界 XYZ。
实时后端 `G1LiveHandle` 在影子 stage 规划，写真实关节目标并以实测掌部判定附着/释放；
执行器不自行推进物理。租约、取消安全放置、clone 隔离及原生测试见
[统一接入指南](../GRASP_SKILLS_INTEGRATION.zh-CN.md)。
