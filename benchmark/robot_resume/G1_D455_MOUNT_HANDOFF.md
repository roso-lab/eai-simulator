# G1 可拆装 D455 挂载与标定 — 移交说明

> 写于 2026-10-19。作者:ZCode 会话(sess_d8a57807)。
> 读者:sess_11ba35c0-877b-4600-b429-6602d9902b6a(简历生成器统一兼容更新会话)。
> 任务:在你完成当前简历生成器工作后,把下述 G1 D455 改动统一移植进主分支并推送。

## 0. 一句话总结

G1 额头凹槽可拆装 D455(挂载 + 外壳校准 + 俯仰标定)已在 codex worktree 完成并通过全部静态/几何验证;因主仓库 `benchmark/robot_resume/` 由你正在重构,本轮**不直接合入**,全部数值与移植清单在本文,由你统一兼容更新后推送。

## 1. 来源工作树与 git 状态

- 路径:`/home/pupil/.codex/worktrees/2a9d/HeteroCoord`(detached HEAD `800f0aef`,基于主仓库 c635c778 的两个提交之前)
- 已修改 tracked 文件(4 个):
  - `source/EAI_assets/EAI_assets/sensor/high_sensor/realsense_d455.py`
  - `source/EAI_hmrs/EAI_hmrs/env_builder.py`
  - `source/EAI/EAI/hmrs_env/env_diy/catalog.py`
  - `source/EAI/test/test_interface_attachment_exclusion.py`
- untracked `benchmark/robot_resume/` 是**旧版生成器**的产物(G1_SKRL 时代),**一律不要拷贝到主仓库**。
- worktree 运行 Isaac Sim 需要:
  ```bash
  export EAI_USD_ROOT=/home/pupil/HeteroCoord/usd
  export EAI_CONTROLLER_ROOT=/home/pupil/HeteroCoord/source/EAI_assets/EAI_assets/controller
  export EAI_ASSETS_AUTO_DOWNLOAD=0
  ```
  (否则卡 HuggingFace 下载,GUI 不出现。)

## 2. 用户已拍板事项(不要改)

1. **G1 控制器维持主仓库现状**:`G1_DWAQ_CFG` + `make_g1_dwaq_robot_cfg(...)`(已审核)。不要恢复 worktree 里的 `G1_CFG`/`G1_SKRL_CFG`。
2. **相机俯仰 +20° 下视**(替代官方 +47.6°):用户批准的感知标定,原因见 §4。
3. **`source/EAI/test/test_interface_attachment_exclusion.py` 不上传**:主仓库保留原 15 项测试版,不移植 worktree 的 18 项版。
4. **简历产物以主仓库新版生成器为准重新生成**;worktree 的 v1.json/schema/md/validation.json 全部不迁移。
5. worktree 里其余 untracked 内容(topology_authoring、bar_service_demo、g1_pick_place、tools/validation 等)属于其他工作线,与本移交无关。

## 3. 移植清单(按文件)

### 3.1 `source/EAI_assets/EAI_assets/sensor/high_sensor/realsense_d455.py` — 整文件直接拷贝

主仓库该文件自 `800f0aef` 起未被改动,可安全整文件覆盖。改动全部为增量(+73 行):

- `RealSenseD455SpawnCfg` 与 `RealSenseD455Cfg` 各新增 3 个字段,默认 `None`:
  `shell_translate` / `shell_rotate_euler`(XYZ 欧拉角,度)/ `shell_scale`,坐标系为资产 RSD455 系;
- `__post_init__` 增加 3 行透传到 spawn cfg;
- 新函数 `_apply_realsense_shell_calibration(stage, sensor_root_path, cfg)`:三个值全 None 时第一条语句即 return False(零 USD 操作),有值时覆写 `<root>/RealsenseD455/RSD455/Visual` 的 `xformOp:translate/rotateXYZ/scale`,并打印 `[RealsenseD455] ✅ Shell calibration ...`;
- `spawn_realsense_d455` 逐实例循环中(物理剥离之后)新增一行调用;
- import 行 `from pxr import Sdf` → `from pxr import Gf, Sdf`。

**对其他机器人的影响:无。** pepper/mushr_v2/carter/coco/lite3 等所有 D455 宿主的 `RobotOption` 不含 `realsense_shell_*` 字段(dataclass 默认 None)→ cfg 三值为 None → 校准函数早退。全仓库 `RealSenseD455Cfg(` 唯一构造点在 env_builder(grep 已验证),无其他调用方受影响。

### 3.2 `source/EAI_hmrs/EAI_hmrs/env_builder.py` — 三处手工叠加(不要整文件覆盖)

主仓库该文件已演进(DWAQ 等),只做三处增量:

**(a) `RobotOption` dataclass,`realsense_rot` 字段之后新增:**

```python
    # D455 外壳校准（可选）：仅覆写资产内部 Visual 网格层的局部
    # translate/rotateXYZ/scale，让外壳落座宿主的安装位；不影响相机/IMU
    # 传感器 prim，也不改变上面的挂载外参。rotate 为 XYZ 欧拉角（度），
    # 平移单位米，坐标系均为资产 RSD455 系。None = 资产原样。
    realsense_shell_translate: tuple[float, float, float] | None = None
    realsense_shell_rotate: tuple[float, float, float] | None = None
    realsense_shell_scale: tuple[float, float, float] | None = None
```

**(b) G1 的 `RobotOption` 条目内(主仓库现为 DWAQ 版,default_z=0.8,lidar 两行之后)追加:**

```python
        # Detachable RealSense mount in the recessed forehead camera bay.
        # Translation follows Unitree's official G1 d435_joint relative to
        # torso_link. Pitch is a 2026-10-19 user-approved perception
        # calibration, not the official +47.6 deg down: at the official pitch
        # the 90.5x90.5 deg asset camera stares into the robot's own head
        # shell (4.5 cm) and chest (9.7 cm), blocking most of the depth/RGB
        # frame. At +20 deg down the head and torso fully exit the frustum;
        # only the hands at the hips remain in the bottom corners, as on the
        # physical robot. w=cos(10 deg), y=sin(10 deg).
        realsense_mount_link="torso_link",
        realsense_offset=(0.0576235, 0.01753, 0.41987),
        realsense_rot=(0.98480775301, 0.0, 0.17364817767, 0.0),
        realsense_shell_translate=(0.03212, 0.0187, 0.02007),
        realsense_shell_rotate=(85.5, 0.0, -90.0),
        realsense_shell_scale=(0.7, 0.7, 0.7),
```

(外壳三行的注释:2026-10-19 Isaac Sim GUI 手动标定,0.7 倍 D455 外壳落座凹槽;资产默认 translate (0,0,0) / rotateXYZ (90,0,-90) / scale 1。)

**(c) `RealSenseD455Cfg(...)` 构造点(主仓库约 L796,`disable_physics=True` 之后)追加 3 行:**

```python
                shell_translate=robot.realsense_shell_translate,
                shell_rotate_euler=robot.realsense_shell_rotate,
                shell_scale=robot.realsense_shell_scale,
```

### 3.3 `source/EAI/EAI/hmrs_env/env_diy/catalog.py` — 仅两行

⚠️ **注意**:worktree 对该文件的 diff 还包含旧控制器偏好(`PEPPER_HOLONOMIC_CFG`/`GO2_VELOCITY_RSL_CFG`/`B2_VELOCITY_RSL_CFG` 默认值与注册表调整),那是 DWAQ 之前的旧状态,**禁止移植**。只取两处 g1 增量:

```python
    realsense_d455_hosts = ("pepper", "mushr_v2", "g1") + orsus_hosts
```

以及 Camera 工具条目 `supported_robots` 元组末尾加 `"g1"`。

### 3.4 `benchmark/robot_resume/generate_robot_pool.py` — 在主仓库新版上移植 4 处

1. `DEFAULT_D455_ROBOTS` 集合加 `'g1'`(主仓库当前版已移除 g1);
2. 新增常量 `G1_CAMERA_MOUNT`(整块照抄,含注释):
   ```python
   G1_CAMERA_MOUNT = {
       # 平移为 Unitree 官方 d435_joint;俯仰为 2026-10-19 用户批准的感知标定
       # (官方 +47.6° 下视改为 +20° 下视):官方俯仰下 90.5°×90.5° 相机会拍满
       # 自身头壳与胸腔,+20° 下视时头胸完全退出视锥,仅剩髋侧双手入镜。
       'translation_m': [0.0576235, 0.01753, 0.41987],
       'rotation_wxyz': [0.98480775301, 0.0, 0.17364817767, 0.0],
       'reference_frame': 'torso_link',
       'mounting': 'detachable_forehead_recess',
       # Isaac Sim GUI 标定（2026-10-19）：D455 外壳（Visual 网格）在资产 RSD455
       # 坐标系中的局部变换，使 0.7 倍外壳落座 G1 额头凹槽；仅外观校准，
       # 不改变传感器 prim 位姿与上方挂点外参。
       'visual_shell_calibration': {
           'translate_m': [0.03212, 0.0187, 0.02007],
           'rotate_xyz_deg': [85.5, 0.0, -90.0],
           'scale': [0.7, 0.7, 0.7],
           'reference_frame': 'rsd455_shell',
       },
   }
   ```
3. 记录构造处(主仓库当前 `'camera_mount_pose': None`,约 L407)改为:
   ```python
   'camera_mount_pose': copy.deepcopy(G1_CAMERA_MOUNT) if host == 'g1' else None,
   ```
   g1 的 `camera_model` 会随 `'g1'` 回到 `DEFAULT_D455_ROBOTS` 自动变为 `realsense_d455`;`camera_integration_status` 相应为 `builder_registered_default`(枚举已存在);
4. `record_schema()` 中 `typ('sensing.camera_mount_pose', ...)` 扩展(新增第 5 个 required 键与子 schema):
   ```python
   typ('sensing.camera_mount_pose', ['object', 'null'], additionalProperties=False,
       required=['translation_m', 'rotation_wxyz', 'reference_frame', 'mounting', 'visual_shell_calibration'],
       properties={'translation_m': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                   'rotation_wxyz': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4},
                   'reference_frame': {'type': 'string'}, 'mounting': {'type': 'string'},
                   'visual_shell_calibration': {
                       'type': 'object', 'additionalProperties': False,
                       'required': ['translate_m', 'rotate_xyz_deg', 'scale', 'reference_frame'],
                       'properties': {
                           'translate_m': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                           'rotate_xyz_deg': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                           'scale': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3},
                           'reference_frame': {'type': 'string'}}}})
   ```
   (若你的新版 schema 结构已不同,以等效方式表达"camera_mount_pose 携带 visual_shell_calibration 子对象"。)

### 3.5 ⚠️ `benchmark/robot_resume/validate_robot_pool.py` — 必须同步

现版断言 g1 相机为 pending(L99–110 的分组、L139/L146 的检查描述)。g1 合入 D455 后:
- g1 从 "pending_adaptation" 组移入 D455 组(可断言 `camera_model == 'realsense_d455'`、`camera_present is True`、`camera_integration_status == 'builder_registered_default'`、`camera_mount_pose['rotation_wxyz'] == [0.98480775301, 0.0, 0.17364817767, 0.0]`);
- 描述文案 "G1 and standalone UR5/Z1 camera adaptation remains pending." 相应改为仅 UR5/Z1 pending。

## 4. 权威数值与依据

| 项 | 值 | 说明 |
|---|---|---|
| 挂点平移(torso_link 系) | `(0.0576235, 0.01753, 0.41987)` | Unitree 官方 G1 URDF `d435_joint`,不变 |
| 旧官方旋转(w,x,y,z) | `(0.91495966785, 0, 0.40354529635, 0)` | +47.6° 下视,**已弃用**:资产相机 FOV 90.5°×90.5°,此俯仰下头壳(4.5cm)与胸腔(9.7cm)占据画面大部分 |
| 新旋转(w,x,y,z) | `(0.98480775301, 0, 0.17364817767, 0)` | +20° 下视(用户 2026-10-19 批准);头/胸完全退出视锥,中心视线落地约前方 3.2m |
| 外壳校准(RSD455 系) | t `(0.03212, 0.0187, 0.02007)` / rotXYZ `(85.5, 0, -90)°` / scale `0.7` | 用户 GUI 手动标定;仅 Visual 网格,传感器 prim 不动 |
| 外壳资产默认 | t `(0,0,0)` / `(90, 0, -90)°` / scale `1` | rsd455.usd 内置 |
| 相机参数 | focal 1.93, aperture 3.896(≈90.5° FOV), 1280×720, clip (0.01, 1e6) | 资产内置,未改 |
| 剩余入镜 | 双手/手指,画面底部两角(u≈0.22–0.35 / 0.65–0.76,v≤0.28),25–46cm | 物理事实,任何前向相机均见;挂点前移 5–40mm 已评估并否决(收益小、40mm 起外壳擦边视锥) |

遮挡诊断方法(可复现):组合 stage(官方外参 + 引用 `rsd455_d455.usd`)读相机 `ComputeLocalToWorldTransform`,G1 USD(`robot/g1/g1_29dof_with_inspire_rev_1_0.usd`)rest-pose 网格逐顶点投影进视锥。注意不要手写 Gf 矩阵组合(易出坐标 bug)。

## 5. 合入后验证清单

```bash
# 主仓库(合入 3.1–3.3 后)
PYTHONPATH=source/EAI:source/EAI_assets \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m pytest -q source/EAI/test/test_interface_attachment_exclusion.py   # 原 15 项应仍全过

# 简历(合入 3.4–3.5 后)
/home/pupil/miniconda3/envs/env_isaaclab/bin/python benchmark/robot_resume/generate_robot_pool.py
/home/pupil/miniconda3/envs/env_isaaclab/bin/python benchmark/robot_resume/validate_robot_pool.py
# + jsonschema Draft202012Validator 对 v1.json 过一遍(0 errors)

# Isaac Sim 实机(用户已熟悉)
# my_env 启动后:Stage 应有 /World/envs/env_0/g1_1/torso_link/RealsenseD455;
# console 出现 "[RealsenseD455] ✅ Shell calibration (translate, rotateXYZ, scale)";
# look-through 深度/RGB 相机:头壳与胸腔不在画面,底部两角为双手。
```

## 6. 发布通道备忘

D455 图像走资产内置 OmniGraph(ROS2 RGB/Depth/CameraInfo,namespace=机器人名,如 `/g1_1/RealsenseD455_rgb`),IMU 由 `RealSenseD455ImuManager` 合成。G1+D455 的运行时图像流尚未实测订阅过一次(简历 `camera_publication` 仍 pending_binding),首次实测通过后可更新该状态。
