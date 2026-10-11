# 验证工具

[English](README.md)

`tools/validation/` 包含仓库、配置、资产和可选运行时 smoke check。它是 benchmark 周边的
质量门禁，不是任务 evaluator，也不能替代 `task/evaluators/`。

## 检查范围

这些工具可以检查：

- 仓库和文档的一致性；
- 资产 preflight 和 provider 错误报告；
- 场景/地图所有权和资源声明；
- ROS 发行版和安装配置；
- Env DIY 或 interface 兼容性合同；
- 机器人、挂载、URDF/USD 以及指定的物理 smoke probe。

检查通过只代表对应合同成立，不能证明 Isaac Sim、GPU、ROS2、外部资产、controller 或完整多机
任务链路一定可运行。

## 与任务评测的边界

```text
tools/validation/
    仓库、配置和输入是否满足准入条件？

task/evaluators/
    机器人实际执行后是否满足物理任务里程碑？
```

例如，`check_scene_map_assets.py` 只检查地图声明和资产所有权是否一致；机器人是否到达目标、
是否完成危险源检查或灭火，必须由任务 evaluator 根据物理证据判定。

## 轻量检查

从仓库根目录运行适用的检查：

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
python tools/validation/check_scene_map_assets.py
node tools/validation/check_env_diy_runtime.mjs all
```

轻量检查使用本地文件、mock 或纯模块，不应下载资产或启动 Isaac Sim。定位问题时请逐个运行，
确保失败命令的非零退出状态不会被 shell pipeline 吞掉。

## 可选运行时探针

本目录中有少量工具会跨越轻量检查边界，例如机械臂接触/抓取说明和 headless 挂载 smoke test。
它们属于可选集成检查：

- 只能在指定的 `env_isaaclab` 中运行；
- 必须使用当前测试对应的资产和 controller 配置；
- 报告应放在源码树之外，除非明确作为受控 fixture；
- 不能把探针通过解释成 benchmark 任务成功。

## 何时运行

GUI 生命周期回归不需要启动 Isaac：

```bash
python -m pytest -q tools/validation/test_simulator_gui_shutdown.py tools/validation/test_box_transport_capture.py
```

`open_simulator_session()` 在关闭自己创建的应用前释放已有 Kit GUI 回调；
不关闭或修改调用方持有的应用。环境关闭后，在 PhysX 仍存活时停止物理并释放
共享 native tensor view，覆盖回放和 controller 对资产的残留引用；这些对象在
session 关闭后不可继续使用。Kit 107 的兼容清理显式释放失效 view 的 native owner，
因为上游 wrapper 没有独立 close 方法。单项清理失败不会跳过其余清理或应用关闭。
GUI 集成检查必须分别确认物理证据、进程退出码为0及整个工作进程组终止，
物理报告成功不能抵消逐扩展卸载期间的崩溃。

- 修改文档或算法清单 → 运行文档和 Release 链接检查；
- 修改 preflight、provider 或资产 resolver → 运行资产错误和场景资源检查；
- 修改 Env DIY、interface 或兼容性规则 → 运行 Env DIY 检查；
- 修改安装脚本或 ROS 发行版逻辑 → 运行 ROS 发行版检查；
- 修改机器人挂载或物理逻辑 → 运行对应运行时探针和单元测试。

这些检查之后仍需运行受影响的单元测试。若改动跨越运行时边界，再使用独立的 Isaac Sim 集成
测试验证，不得为了测试修改现有 simulator、Torch、ROS 或资产缓存环境。

任务合同和执行编排请先阅读 [task/README.zh-CN.md](../../task/README.zh-CN.md)。可选的场景编写
工具请阅读 [scenario generation](../scenario_generation/README.zh-CN.md)。
