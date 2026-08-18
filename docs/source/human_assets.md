# 人类资产开发

EAI 的人类系统由 registry、USD stage runtime 和统一验证工具组成。当前清单包含 44 个已启用
角色：39 个骨骼角色可播放 12 个标准动作，4 个非骨骼活动角色可沿路径移动，1 个非骨骼角色
保持静态。人类角色通过独立的 stage runtime 接入，不属于 Env DIY 的机器人或传统控制器目录。

Isaac Sim 5.1 中的骨骼姿态写入使用 CPU PhysX。包含动画人类的运行流程不应强制使用 GPU
PhysX；渲染仍可使用 CUDA GPU。

![统一 human demo 中同时加载的角色与活动资产](assets/media/human-assets-demo.png)

## 代码与数据边界

| 路径 | 职责 |
| --- | --- |
| `usd/human/manifest.json` | 角色、动作、能力与放置参数的权威清单 |
| `usd/human/manifest.schema.json` | manifest 的严格 JSON schema |
| `usd/human/pack-checksums.json` | Provider 中 characters、activities 与 motions pack 的版本和校验信息 |
| `source/EAI_assets/EAI_assets/humans/registry.py` | 清单加载、路径约束与能力校验 |
| `source/EAI_assets/EAI_assets/humans/stage_runtime.py` | 角色生成、路径跟随、动作播放、暂停和恢复 |
| `source/EAI_assets/EAI_assets/humans/asset_placement.py` | 资产朝向、缩放与自动贴地 |
| `tools/human_assets/run_demo.py` | 全部角色的 GUI 与 headless 统一验证入口 |

运行时使用的角色、纹理、动作源和 retarget cache 都必须安装在 `usd/human/` 下，manifest 中的
`usd_path` 必须是相对于该目录的路径。转换工具可以读取经过批准的外部源目录，但生成的运行时
清单不能依赖开发者主目录或仓库外的绝对路径。

大型 payload 可以由 gated Hugging Face Provider 分发。源码仓库维护 manifest、schema、审计
记录和 pack 校验元数据；Provider 负责与这些元数据匹配的 `characters/`、`activities/` 和
`motions/` 内容。

## 下载完整资产

Human 的大型 USD、纹理、动作和 retarget cache 位于 gated Hugging Face dataset
[`HuangQIjun/eai-simulator-assets`](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)。
先在该页面申请访问权限并等待批准，再从 Git 仓库根目录登录并一次性下载全部 Human 资产：

```bash
hf auth login
hf download HuangQIjun/eai-simulator-assets \
  --type dataset \
  --revision v0.1.0-beta.1 \
  --include "usd/human/**" \
  --local-dir .
```

该命令固定到与 `pack-checksums.json` 一致的不可变 tag，并保持 Provider 中的相对路径，下载
结果会直接补齐当前仓库的 `usd/human/`。Human 运行时只提供这一种完整下载方式，不按角色、
动作或 pack 拆分下载。下载完成后即可运行本页后面的统一 GUI 或 headless 验证命令。

## 角色能力

每条资产记录通过以下字段声明能力，调用方不应根据目录名推断行为：

- `articulated` 与 `can_play_actions`：是否具有骨骼并允许播放标准动作。
- `path_following`：是否可以沿 waypoints 移动。
- `animation_profile`：骨架 profile，例如 `synbody_55`、`smplx_70`、`rpm_87` 或 `rigid_1`。
- `motions`：该角色允许播放的动作 ID。
- `content_up_axis`、`yaw_offset` 和 `scale`：把源资产统一到场景坐标和可见正面。
- `ground_offset`：在自动贴地结果上添加有意设置的离地间隙。

放置流程会保留被引用 USD 根节点的自带变换，然后应用 manifest 中的朝向、缩放和高度校正。
对蒙皮角色，贴地范围来自当前 UsdSkel 姿态变形后的可见网格点；对非蒙皮网格或无法计算
skinning 的内容，使用 USD 包围盒作为回退。角色生成、动作切换以及动作结束后恢复 locomotion
时都会根据当前姿态重新贴地。

## 标准动作

39 个骨骼角色共享同一组编号：

| 编号 | 动作 ID | 默认路径策略 |
| --- | --- | --- |
| 1 | `bow` | `pause` |
| 2 | `jog` | `continue` |
| 3 | `dance` | `pause` |
| 4 | `walk_and_look` | `continue` |
| 5 | `walk_backward` | `continue` |
| 6 | `walk` | `continue` |
| 7 | `phone_call` | `pause` |
| 8 | `long_stride_walk` | `continue` |
| 9 | `walk_and_text` | `continue` |
| 10 | `stagger_walk` | `continue` |
| 11 | `hit_reaction_retreat` | `continue` |
| 12 | `forward_dive` | `continue` |

`path_policy=pause` 会在动作期间暂停路径跟随，`path_policy=continue` 会让骨骼动作与场景位移
同时进行。`facing_yaw_offset` 修正动作可见正面与路径切线的关系，不修改骨架根关节。
`root_motion=in_place` 去除骨架根节点的水平位移，由路径跟随负责场景移动。

跨 profile 动作由 retarget cache 和关节语义别名完成。新增角色时必须为 12 个标准动作生成与
目标资产匹配的 cache，并保持 manifest 的 skeleton signature、动作内容 hash 与 cache 元数据一致。

## 接入 Stage Runtime

下面的最小结构假定已获得一个 Z-up、米制的 USD stage，并且完整 human payload 已安装：

```python
from pathlib import Path

from EAI_assets import asset_resolver
from EAI_assets.humans import (
    HumanActorConfig,
    HumanAssetRegistry,
    UsdHumanStageRuntime,
)

human_root = Path(asset_resolver.asset_path("human")).resolve()
registry = HumanAssetRegistry.load(
    human_root / "manifest.json",
    asset_root=human_root,
)
runtime = UsdHumanStageRuntime(
    stage,
    registry,
    cache_root=human_root / "motions/cache",
)

runtime.spawn(
    HumanActorConfig(
        actor_id="human-1",
        asset_id="synbody-0000001",
        prim_path="/World/Humans/human_1",
        initial_pose=(0.0, 0.0, 0.0, 0.0),
        waypoints=((0.0, 0.0, 0.0), (3.0, 0.0, 0.0)),
        speed=1.2,
        loop=True,
    )
)
runtime.play_action("human-1", "phone_call")

# 在仿真循环中调用。
runtime.update(dt)

# stage 关闭前释放 adapter、角色和 pause lease。
runtime.close()
```

每个角色必须使用唯一的 `actor_id` 和绝对 USD `prim_path`。至少两个 waypoints 才会启用路径
模式；没有 waypoints 时角色使用 external movement mode。动作只影响指定角色的动画和路径暂停
状态。

`UsdHumanStageRuntime.update(dt, *, context=None, actor_ids=None, animate_while_idle=False)`
支持分批调度更新：传入 `actor_ids` 时只更新这些角色，未选中的角色不会推进路径、采样动画
或改变动作时钟，其事件和 pending reground 状态会保留到后续更新，适合大量角色部署时按
距离、交互状态或预算分帧轮转。`actor_ids` 中的 ID 必须已注册，重复 ID 不会自动去重，调用
方应自行保证唯一。

```python
# 只更新附近或正在交互的角色。
runtime.update(dt, actor_ids=("human-1", "human-5"))

# 空闲角色策略：默认 False，路径暂停/结束且没有动作的角色不重采样动画；
# 需要旧版“始终动画”行为（例如角色原地播放 locomotion）时显式打开。
runtime.update(dt, animate_while_idle=True)
```

默认 `animate_while_idle=False` 是刻意设置的省 CPU 行为：统一 demo 在启动时暂停所有路径，
因此空闲角色保持静止站姿，只有播放动作或恢复路径移动后才会采样动画。不要把这种静止误判
为动画能力缺失。`HumanMotionController.update(dt, *, actor_ids=None,
locomotion_actor_ids=None)` 提供同样语义：`actor_ids` 限制状态机推进范围，
`locomotion_actor_ids` 只冻结被排除角色的空闲 locomotion 时钟；进行中的动作仍会正常推进。

## 添加角色或动作

添加角色时：

1. 把可发布的角色、纹理和依赖安装到 `usd/human/characters/` 或 `usd/human/activities/`。
2. 在 `manifest.json` 中填写相对 `usd_path`、骨架 profile、能力字段、朝向、缩放、贴地偏移、
   provenance 和 license 状态。
3. 骨骼角色需要生成 12 个标准动作的 retarget cache，并确认动作朝向与路径方向符合语义。
4. 使用统一 demo 检查加载、动作、移动、恢复和贴地。

创建本地 JSON 关键帧草稿：

```bash
python tools/human_assets/edit_action.py init \
  --action-id wave --duration 2.0 --fps 30 \
  usd/human/custom-actions/wave.json
```

导入一个自包含的 GLTF/GLB 动作 clip：

```bash
python tools/human_assets/import_action.py usd/human/motions/sources/bow.gltf \
  --action-id bow-example --profile smplx_70 --human-root usd/human
```

自定义动作使用 overlay manifest，不得覆盖 12 个标准动作 ID。转换、导入和 cache 构建的详细
参数见 `tools/human_assets/README.md`。

## 统一功能验证

GUI 模式会一次加载全部 44 个角色：

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python -u tools/human_assets/run_demo.py
```

按 `Q` 选择下一个角色。骨骼角色输入数字 `1-12` 后按 `Enter` 播放动作；4 个可移动
`rigid_1` 角色只接受数字 `1`；静态角色拒绝动作。`Backspace` 编辑输入，`X` 停止并恢复当前
角色，`Esc` 关闭演示。动作结束、循环动作取消或替换、切换角色以及 rigid 移动完成后，角色
都会回到动作前原位。

Headless 模式使用同一 backend 和控制状态机验证完整能力矩阵：

```bash
conda run --no-capture-output -n env_isaaclab \
  python -u tools/human_assets/run_demo.py --headless
```

成功摘要为：

```text
Verified unified human matrix: 39x12 + 4 + 1
```

该命令需要 Isaac Sim 5.1 和完整的 human payload。它验证 39 × 12 个骨骼动作、4 个 rigid
往返移动、1 个静态角色、动作采样、路径策略、当前姿态贴地、bounds 与精确位置恢复。

## Provider 发布

Provider 发布由维护者在资产验收后单独执行。发布内容必须包含与源码清单一致的
`characters/`、`activities/` 和 `motions/`，并完成 provenance 与许可证审批。上传到 Hugging
Face 后创建 immutable tag，同步更新 `usd/human/pack-checksums.json`，再从干净仓库根目录使用
该固定 revision 运行统一 headless 验证。源码映射、Provider 文件、tag 和校验元数据必须属于
同一发布版本。

## 来源与致谢

角色与动作资产源自 [Urban-Sim](https://github.com/metadriverse/urban-sim)（Apache-2.0）与 [SynBody](https://synbody.github.io/)（CC BY-NC-SA 4.0），逐资产来源与许可记录见 `usd/human/manifest.json`。
