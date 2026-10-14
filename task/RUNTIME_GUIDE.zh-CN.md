# 运行时接入说明

局部取放统一使用 `PickPlaceExecutor`。宿主创建控制器和对应 `PickPlaceAdapter`，共享 `ResourceManager`，再调用：

```python
executor.register_adapter(adapter)
picked = executor.execute(pick_request)
# 调用者自行导航或保持原位
placed = executor.execute(place_request)
```

一次请求只执行一个操作并返回布尔值。`True` 必须有实测抓取/抬升/持物或释放/撤离/稳定证据；命令接受、规划完成和状态机阶段本身不算成功。失败、取消、超时或停止未确认时，适配器按实测持物状态保留连接和资源。

导航技能继续由调用者独立调度。Pepper 使用 `PEPPER_PICK_PLACE_CFG`，底盘命令直接来自调用者，双臂和物体保持由 `PepperPickPlaceAdapter` 负责。机械臂、无人机和 G1 适配器分别复用现有 IK、飞控/挂接、双臂和 G1 规划器。

控制器与适配器都不自行推进仿真；每个物理 tick 由宿主驱动。机器人接入、坐标约定、资源交接和验证要求见 [机器人接入指南](../algorithm/skills/pick_place/ADDING_ROBOTS.zh-CN.md)。
