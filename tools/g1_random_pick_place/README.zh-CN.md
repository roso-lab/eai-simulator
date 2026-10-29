# G1 在新场景中的随机抓放验证

本演示直接复用保留的 G1 几何规划器 `G1PickPlaceSkill`，完成三个独立的固定站位 USD 几何验证任务。它不替代 live 场景中的 `G1PickPlaceAdapter`，也不负责导航或宿主物理循环。场景由程序生成，没有引用原吧台、酒瓶或酒杯资产；抓取物为橙色细杆、蓝色小哑铃和紫色线轴。每个任务随机生成机器人朝向、抓取位置和放置位置，世界平移用于隔开三个场景。机器人在任务内不行走。

这是 G1 控制器的动作轨迹演示，不是物理搬运验证。PickPlace 适配器只根据机器人当前状态执行固定下降、抬升和回撤，物体注册信息只用于机器人校准配置，物体位姿不会被写入或跟随。支架在规划完成后才由演示程序生成；skill 的 `obstacles` 始终为空。

三个物体的动作点沿用 Inspire 手指闭合参数和掌内校准点，各自注册实际物体尺寸。该验证覆盖这些已注册、直立、轴向动作点，不能据此推断任意新物体都能自动识别。

## 随机范围

默认 seed 为 `20261014`。在 G1 根节点坐标中，抓取 XYZ 分别采样于 `[.24,.32]`、`[-.50,-.41]`、`[.43,.48]` 米；放置于 `[.24,.34]`、`[-.10,-.02]`、`[.43,.48]` 米。根节点高度为 `.7923` 米，朝向在 `[-.8,.8]` 弧度中采样，最终转换为世界坐标。不可达采样最多重试 30 次，并记录每次拒绝；本次三个任务均首轮通过。

## 本次结果

| 物体 | 状态 | 物体位姿是否保持 | 动作阶段 | 采样手臂网格顶点到支架最小间距 |
| --- | --- | --- | --- | --- |
| 细杆 | 完成 | 保持 | 向下/向上/回撤 | 73.97 mm |
| 小哑铃 | 完成 | 保持 | 向下/向上/回撤 | 41.61 mm |
| 线轴 | 完成 | 保持 | 向下/向上/回撤 | 38.45 mm |

测得关节速度均不超过 `0.75 rad/s`。三个任务均经历接近、下降、上升和回撤。视频和画面检查只用于确认机器人动作阶段，物体保持原位。

这是暂停仿真时间轴下的 USD 运动学动作执行，不创建物理挂接，也不验证物体搬运。支架检查是 24 Hz 的可视网格顶点距离检查，不是完整连续碰撞检测；没有提供未知障碍避让保证。

## 复现

在仓库根目录执行：

```bash
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/g1_random_pick_place/scene.py
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/g1_random_pick_place/audit_scene.py
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/g1_random_pick_place/run.py
```

输出在 `artifacts/g1_random_pick_place/`：`demo.mp4`、各阶段截图、`audit.json`、`scene_clearance.json` 和 `render_report.json`。`run.py` 通过公开 `open_simulator_session` 启动，使用与离线检查相同的随机坐标。使用 `scene.py --seed N --output PATH` 和 `run.py --seed N --output PATH` 可运行其他 seed；独立支架审计目前读取默认输出目录。
