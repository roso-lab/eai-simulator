# EAI 人类资产

`usd/human/` 是 EAI registry-driven 人类资产的数据根目录。角色、动作、资产审计、
校验和、重定向缓存和本地自定义动作都以这里的元数据为准。

## 当前资产清单

`manifest.json` 当前包含 **44 个资产记录、44 个已启用角色、12 个已部署标准动作**。
角色记录按来源和用途分为：


| 角色族             | 记录数 | 已启用 | 说明                                                                 |
| --------------- | --- | --- | ------------------------------------------------------------------ |
| SynBody 原生 USD  | 26  | 26  | `characters/synbody/`，`synbody_55` 骨架                              |
| SynBody GLTF 转换 | 7   | 7   | `characters/synbody_gltf/`，`synbody_55` 骨架                         |
| Ready Player Me | 6   | 6   | `characters/rpm/`，`rpm_87` 骨架；全部 12 个标准动作均可播放                      |
| 活动角色            | 5   | 5   | 骑行、滑板、滑板车、静态骑手、轮椅各 1；全部启用                                          |


39 个记录属于 pedestrian，其他 5 个记录分别属于 cyclist、scooter rider、
skateboarder、static biker 和 wheelchair。是否可播放动作由每个资产的
`articulated`、`can_play_actions`、`path_following` 和 `motions` 字段共同决定，不能只按
目录名推断。

放置由 manifest 显式控制：`content_up_axis` 描述文件中实际几何的高度轴（`Y` 或 `Z`），
`yaw_offset` 修正可见正面，`scale` 修正尺寸，`ground_offset` 设置离地间隙。runtime 会先保留
被引用 USD 根节点自带的变换，再应用这些资产级校正并按变换后的可见包围盒落地；不能仅相信
转换 USD 的 stage `upAxis` 元数据。RPM 和活动资产的实际内容均为 Y-up。

角色数据位于：

- `characters/`：可动画的 SynBody 和 RPM 角色。
- `activities/`：自行车、滑板车、滑板、静态骑手和轮椅等复合活动角色。
- `motions/`：标准动作源和按角色生成的 retarget cache。
- `custom-actions/`：本地发布动作及其 overlay manifest。
- `manifest.json`：标准角色和动作的权威清单。
- `manifest.schema.json`：清单 JSON schema。
- `audit-summary.json`：资产接收和完整性审计记录。
- `pack-checksums.json`：provider 中 `characters`、`activities`、`motions` 三个 pack 的校验信息。

大型 USD、纹理、缓存和本地自定义动作通常由 provider 或本地流程维护并被 Git 忽略；
`redistribution_status=review_required` 也不表示已经获得再分发许可。

## 已部署标准动作

下表来自 `manifest.json`。`采样范围` 是源动画 time code 的闭区间；`-` 表示使用源动画
完整范围。时长单位为秒，方向偏移单位为弧度。


| ID                     | 标签                   | 时长     | loop  | path_policy | root_motion | 采样范围     | facing_yaw_offset |
| ---------------------- | -------------------- | ------ | ----- | ----------- | ----------- | -------- | ----------------- |
| `bow`                  | Bow                  | 1.667  | false | pause       | in_place    | 120..160 | -1.570796         |
| `jog`                  | Jog                  | 0.833  | true  | continue    | in_place    | 0..19    | 0.000000          |
| `dance`                | Dance                | 52.208 | false | pause       | in_place    | 48..1301 | 0.000000          |
| `walk_and_look`        | Walk And Look        | 3.000  | false | continue    | in_place    | 0..72    | 3.141593          |
| `phone_call`           | Phone Call           | 38.917 | false | pause       | in_place    | 0..933   | -1.570796         |
| `long_stride_walk`     | Long Stride Walk     | 1.125  | true  | continue    | in_place    | 0..26    | -1.570796         |
| `walk_and_text`        | Walk And Text        | 4.083  | true  | continue    | in_place    | 0..97    | -1.570796         |
| `stagger_walk`         | Stagger Walk         | 3.083  | true  | continue    | in_place    | 0..73    | -1.570796         |
| `hit_reaction_retreat` | Hit Reaction Retreat | 3.208  | false | continue    | in_place    | 0..76    | 1.570796          |
| `forward_dive`         | Forward Dive         | 2.625  | false | continue    | in_place    | 0..62    | -1.570796         |
| `walk_backward`        | Walk Backward        | 5.583  | false | continue    | in_place    | 48..182  | 1.570796          |
| `walk`                 | Walk                 | 1.167  | true  | continue    | in_place    | -        | 0.000000          |


动作契约字段含义：

- `path_policy=pause`：动作播放期间暂停该角色的路径跟随，结束或取消后按 `resume_policy` 恢复。
- `path_policy=continue`：骨骼动作与路径位移同时进行。
- `root_motion=in_place`：去除骨骼根节点的水平位移，保留竖直分量，场景位移由路径控制。
- `root_motion=authored`：保留动作源中的完整根位移。
- `root_motion=none`：忽略动作源根位移，使用目标骨架 rest translation。
- `resume_policy=resume_phase`：动作完成或取消后从暂停前的路径相位继续。
- `loop`：是否循环播放；调用方未覆盖时以清单值为准。
- `sample_start` / `sample_end`：裁剪源动画的有效片段，去除前后无用帧。
- `facing_yaw_offset`：在人物外层节点上绕场景 Z 轴修正视觉朝向与路径运动方向之间的偏差；
不修改骨架根关节姿态。

`dance`、`walk_and_look`、`walk_backward` 是 registry 和 API 使用的语义 ID；对应的 provider
USD 文件分别为 `motion_120_04.usd`、`motion_15_01.usd`、`stand_to_walk_back.usd`。

全部 12 个标准动作对全部 39 个骨骼角色（33 个 SynBody 系 `synbody_55`/`smplx_70` 角色和
6 个 RPM `rpm_87` 角色）均可播放：两套骨架关节名不相交，跨 profile 播放由关节语义别名表
重定向（cache 内 `retarget_mode=aliased-lenient`），未映射的关节保持骨架 rest 姿态。
RPM 角色播放 SMPL 系动作时，`head_end`、`mouth_l/r`、`eyelid_l/r` 等 17 个面部与
twist 关节保持 rest；反向播放时全部 55/70 个 SynBody 系关节均可映射。

`phone_call` 是原地打电话姿态，因此使用 `path_policy=pause`；其源角色和动作文件只包含人物
Mesh，没有电话道具，本版本不额外补充道具。`long_stride_walk`、`walk_and_text`、
`stagger_walk`、`hit_reaction_retreat` 和 `forward_dive` 均使用 `path_policy=continue`。
其中 `hit_reaction_retreat` 的 `facing_yaw_offset=+pi/2`，人物背向路径切线移动，表现为受击后退。

## 全角色 GUI 与 Headless 验证

`run_demo.py` 在同一个 Isaac Sim 5.1 stage 中按资产 ID 稳定顺序加载全部 44 个角色，并以网格
排列。39 个骨骼角色共享下列 12 个标准动作编号：


| 数字  | 动作 ID                     |
| --- | ------------------------- |
| 1   | `bow`                     |
| 2   | `jog`                     |
| 3   | `dance`                   |
| 4   | `walk_and_look`           |
| 5   | `walk_backward`           |
| 6   | `walk`                    |
| 7   | `phone_call`              |
| 8   | `long_stride_walk`        |
| 9   | `walk_and_text`           |
| 10  | `stagger_walk`            |
| 11  | `hit_reaction_retreat`    |
| 12  | `forward_dive`            |

数字 `7` 到 `12` 对应的动作同样可在 `synbody_55` 与 `rpm_87` 角色之间跨 profile 播放。

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python -u tools/human_assets/run_demo.py
```

按 `Q` 会先停止当前动作并把角色恢复到动作前原位，再选择下一个角色、移动选择环并聚焦相机。
骨骼角色使用顶部数字键输入 `1-12` 后按 `Enter` 播放；4 个可移动 `rigid_1` 角色（自行车、
滑板车、滑板和轮椅）只接受数字 `1`，执行一次向外移动并返回；静态骑手可被选择，但会拒绝
数字动作。`Backspace` 编辑输入，`X` 停止并恢复当前角色，`Esc` 关闭演示和 Isaac Sim。

所有可移动角色在加载后保持静止。非循环动作结束、循环动作取消或替换、按 `Q` 切换角色以及
rigid 移动完成时，角色都会回到动作前原位。该恢复行为属于 demo 验证，不改变
`UsdHumanStageRuntime` 的默认路径语义。轮椅的 `Image` 纹理逻辑名合并会保留一条诊断警告，
不影响角色加载与移动验证。

Headless 模式通过同一个 backend 和控制状态机自动检查 39 × 12 个骨骼动作、4 个 rigid 往返
移动和 1 个静态角色，并验证 animation sample、`path_policy`、bounds 与精确位置恢复：

```bash
conda run --no-capture-output -n env_isaaclab \
  python -u tools/human_assets/run_demo.py --headless
```

## 自定义路径

路径由 `HumanActorConfig.waypoints` 定义，坐标使用 stage 的米制 XYZ。至少提供两个点；
`loop=True` 时首尾循环，`phase` 可让多个角色在同一路线上错开初始位置。

```python
from EAI_assets.humans import HumanActorConfig

actor = HumanActorConfig(
    actor_id="human-1",
    asset_id="synbody-0000001",
    prim_path="/World/Humans/human_1",
    initial_pose=(0.0, 0.0, 0.0, 0.0),
    waypoints=(
        (0.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (3.0, 2.0, 0.0),
        (0.0, 2.0, 0.0),
    ),
    speed=1.2,
    loop=True,
    phase=0.0,
)
```

把配置传给 `UsdHumanStageRuntime.spawn(actor)`，每帧调用 `runtime.update(dt)`。多个角色使用
不同 `actor_id` 和 `prim_path` 即可同时运行；`path_policy` 只影响触发动作的指定角色。

## 自定义动作

### JSON 关键帧动作

`edit_action.py` 生成符合 `smplx_70` profile 的 JSON 草稿：

```bash
python tools/human_assets/edit_action.py init \
  --action-id wave --duration 2.0 --fps 30 \
  usd/human/custom-actions/wave.json
```

在 `keyframes` 中按时间填写关节单位四元数 `[x, y, z, w]`，可选
`pelvis_translation`。Python API `validate_action()` 和
`HumanActionPublisher(...).publish(draft)` 可在带 `pxr` 的 Isaac Sim 环境中验证、编译为
UsdSkelAnimation 并发布。当前 CLI 只负责创建草稿，不直接发布 JSON 草稿。

### 导入单段 GLTF/GLB 动作

输入必须是一个带动画 channel 的 `.gltf` 或 `.glb`，其骨架与 `smplx_70` profile 匹配。
`usd/human/motions/sources/bow.gltf` 是自包含示例，不依赖仓库外的 buffer、图片或其他文件：

```bash
python tools/human_assets/import_action.py usd/human/motions/sources/bow.gltf \
  --action-id bow-example --profile smplx_70 --human-root usd/human
```

该命令在 Isaac Sim 中转换一个 clip、验证唯一 UsdSkelAnimation，并发布到
`custom-actions/<action-id>/`。如需替换同名本地动作，显式添加 `--replace`。发布后为目标角色
重建 cache，且不要用自定义动作覆盖标准动作 ID。这个 GLTF 只用于演示 `import_action.py`；
`run_demo.py` 不会在启动时转换或发布它。

## 维护工具

所有维护入口集中在 `tools/human_assets/`：


| 文件                       | 作用                                | 主要输入                               | 主要输出                                 |
| ------------------------ | --------------------------------- | ---------------------------------- | ------------------------------------ |
| `run_demo.py`            | 44 个角色的统一 GUI/headless 验证          | manifest、全部角色 USD、12 个动作和 cache    | Isaac Sim stage、完整能力矩阵结果              |
| `scene.py`               | demo 网格、地面、路线、选择环、bounds 和相机 helper | USD stage、角色位置和 waypoints          | demo prim 和场景验证数据                    |
| `motion_controls.py`     | 角色选择与能力感知键盘状态机                   | Q、数字键、Enter、X、Esc                 | 动作、移动和恢复调用                         |
| `edit_action.py`         | 创建 JSON 关键帧草稿                     | action ID、时长、profile               | action JSON                          |
| `import_action.py`       | 转换并发布一个 GLTF/GLB clip             | 单段动画、action ID                     | custom action USD 和 overlay          |
| `convert_gltf_assets.py` | allowlist 资产预检、GLTF 到 USD 转换和技术验证 | approved source root               | 转换 USD、conversion report             |
| `migrate_assets.py`      | 迁移已批准角色/动作并生成 registry            | approved source root、转换报告          | `manifest.json`、`audit-summary.json` |
| `build_motion_cache.py`  | 为资产/动作对构建 retarget map            | manifest、overlay、USD               | `motions/cache/`、cache report        |
| `validate_assets.py`     | 生成确定性的结构验证报告                      | manifest 和本地文件                     | JSON 验证报告                            |


详细参数和逐条命令见 `tools/human_assets/README.md`。

## Provider 发布要求

human provider 必须包含 `characters/`、`activities/` 和 `motions/`。自包含导入示例
`usd/human/motions/sources/bow.gltf` 随 motions pack 发布，但不是 demo 启动依赖。发布前必须确认
统一 Isaac GUI/headless 行为、资产 provenance 和许可证可再分发性，并为 provider 内容生成精确
pack hash。

Provider 上传到 Hugging Face 后必须创建 immutable tag、同步更新 `pack-checksums.json`，并从
干净仓库根目录验证该固定 revision。源代码映射、provider 文件、tag 和校验元数据必须属于同一
发布版本。

## 验证

纯 Python 检查：

```bash
PYTHONPATH="$PWD/source/EAI_assets" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q source/EAI_assets/test/test_human_*.py
python tools/human_assets/validate_assets.py --manifest usd/human/manifest.json
```

Isaac Sim 5.1 headless 演示：

```bash
conda run --no-capture-output -n env_isaaclab \
  python -u tools/human_assets/run_demo.py --headless
```

结构验证要求 manifest 引用的所有 provider payload 已安装；文件缺失时失败是预期行为。
