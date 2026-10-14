# G1 在新场景中的随机抓放验证

本演示通过现有 `G1PickPlaceSkill` 完成三个独立的固定站位任务。场景由程序生成，没有引用原吧台、酒瓶或酒杯资产；抓取物为橙色细杆、蓝色小哑铃和紫色线轴。每个任务随机生成机器人朝向、抓取位置和放置位置，世界平移用于隔开三个场景。机器人在任务内不行走。

调用只使用 `skill.start(pick_position, place_position)`。物体注册绑定在初始化时提供，物体由抓取点自动匹配。支架在规划完成后才由演示程序生成；skill 的 `obstacles` 始终为空。独立审计器读取支架用于事后检查，审计结果不参与轨迹规划。

三个物体的握持圆柱半径均为 12 mm，沿用已经验证的 Inspire 手指闭合参数和掌内握持点，各自注册实际物体尺寸及中心握持位置。该验证覆盖这些已注册、直立、轴向握持的物体，不能据此推断任意新物体都能自动识别并抓取。

## 随机范围

默认 seed 为 `20261014`。在 G1 根节点坐标中，抓取 XYZ 分别采样于 `[.24,.32]`、`[-.50,-.41]`、`[.43,.48]` 米；放置于 `[.24,.34]`、`[-.10,-.02]`、`[.43,.48]` 米。根节点高度为 `.7923` 米，朝向在 `[-.8,.8]` 弧度中采样，最终转换为世界坐标。不可达采样最多重试 30 次，并记录每次拒绝；本次三个任务均首轮通过。

## 本次结果

| 物体 | 状态 | 放置误差 | 最大握持点误差 | 采样手臂网格顶点到支架最小间距 |
| --- | --- | --- | --- | --- |
| 细杆 | 完成 | 1.185 mm | 1.216 mm | 73.97 mm |
| 小哑铃 | 完成 | 0.813 mm | 0.867 mm | 41.61 mm |
| 线轴 | 完成 | 0.612 mm | 0.585 mm | 38.45 mm |

测得关节速度均不超过 `0.75 rad/s`。三个任务均经历闭合、附着、提起、横移、放下、张开、释放和退离，每次恰好一次附着与释放。1080p / 24 fps 视频共 85.21 秒，包含每个任务结束后 1 秒停留。画面检查覆盖握持、搬运、放下及退离。

这是暂停仿真时间轴下的 USD 运动学执行，携物采用理想刚性附着，尚未验证 PhysX 接触、摩擦力或真实机器人抓取。支架检查是 24 Hz 的可视网格顶点距离检查，不是完整连续碰撞检测；没有提供未知障碍避让保证。

## 复现

在仓库根目录执行：

```bash
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/g1_random_pick_place/scene.py
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/g1_random_pick_place/audit_scene.py
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/g1_random_pick_place/run.py
```

输出在 `artifacts/g1_random_pick_place/`：`demo.mp4`、各阶段截图、`audit.json`、`scene_clearance.json` 和 `render_report.json`。`run.py` 通过公开 `open_simulator_session` 启动，使用与离线检查相同的随机坐标。使用 `scene.py --seed N --output PATH` 和 `run.py --seed N --output PATH` 可运行其他 seed；独立支架审计目前读取默认输出目录。
