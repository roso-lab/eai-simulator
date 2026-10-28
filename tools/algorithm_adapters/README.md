# 原生算法适配工具

论文六列和源码锁定在 `algorithm/heterocoord/paper_algorithms.json`。
统一 provider 是 `benchmark.native_algorithms.build_native_adapter`。
本目录只放启动、配置与宿主工具；算法 I/O 实现归
`algorithm/system/coordination/<method>/native_adapter.py`，运行证据归
`records/algorithm_adapters/<method>/`。

各算法可转换与必须保持的边界见
`algorithm/heterocoord/ADAPTER_IO_POLICY.zh-CN.md`。

## 安装源码

```bash
cd /home/airs/hetrocoord
python3 -m tools.algorithm_adapters.setup_sources \
  --report records/algorithm_adapters/source_receipts.json
```

默认在 `~/.cache/heterocoord/upstream/` 创建六个固定版本。已存在的 checkout 只验证，
不 reset、不删除。`--algorithm` 可重复指定；`--local-sources FILE` 可从已核验的
本地 clone 安装。环境安装方式见各方法报告，ROS1 与 Isaac 运行环境分离。

## 原生决策

```bash
/home/airs/.cache/heterocoord/envs/sadcher/bin/python -m tools.algorithm_adapters.run \
  --algorithm sadcher \
  --task records/algorithm_adapters/sadcher/unified_entry/normal.task.json \
  --config records/algorithm_adapters/sadcher/unified_entry/normal.config.json \
 --report records/algorithm_adapters/sadcher/decision_recheck.json
```

SADCHER 已将默认权重修正为官方 benchmark 的完整 checkpoint，省略 `--config` 即采用该默认。
显式旧权重仍拒绝，不做失败后替换。输入/反馈约束、运行环境与 68 项测试说明见
`algorithm/system/coordination/sadcher/README.md`；最新二维闭环证据在
`records/algorithm_adapters/sadcher/review_20261028/`。

其余方法使用各自记录的 task/config。一般 config 是构造器 kwargs 的平面 JSON 对象，
如 HARL 的 `manifest_path`、BT 的 `endpoint`。EMOS 使用
`heterocoord.emos_native_config.v1`：显式 request/resume/observation 和端点环境变量名；
配置示例见 `records/algorithm_adapters/emos/config.example.json`。
密钥不写进 JSON。EMOS 专用启动也可用
`python -m tools.algorithm_adapters.emos_config --config FILE --report FILE`。

CLI 退出 0 表示原生决策返回，**不表示任务成功或算法完整验收**。idle 也是合法原生
输出。退出 1 保留错误及可序列化的原始拒绝输出；无替代求解。CLI 结束会关闭已建立的
会话；长生命周期 host 应直接持有 provider 并负责反馈及关闭。

TeamWeaver 的授权语法补丁、原生分配器多轮逻辑执行及语义单批执行见
`algorithm/system/coordination/TeamWeaver/NATIVE_ADAPTER_REPORT.zh-CN.md`。
对应工具为 `teamweaver_logical` 和 `teamweaver_semantic`；后者真实调用 Dongli 与原生
`LLMPlanner.replan`，不是旧版自定义分解器。语义多轮持久反馈尚未实现，不能自动重置原生阶段代替。

## Isaac 有界闭环

```bash
/home/airs/miniconda3/envs/env_isaaclab/bin/python -m tools.algorithm_adapters.isaac_pick_place \
  --algorithm bt_capabilities \
  --algorithm-python /home/airs/miniconda3/envs/env_isaaclab/bin/python \
  --task algorithm/system/coordination/bt_capabilities/configs/isaac_ur5_task.json \
  --config algorithm/system/coordination/bt_capabilities/configs/isaac_ur5_adapter.json \
  --output records/algorithm_adapters/bt_capabilities/isaac_recheck
```

先按 BT `NATIVE_ADAPTER_REPORT.md` 启动独立 ROS endpoint，并将 endpoint 配置更新为
实际地址。该 host 仅支持 UR5 单机器人/单技能、等高工作台抓放。真实物体坐标、目标、
角度和 timeout 来自任务包；仅在原算法派发后启动 Isaac。反馈返回原生 BT，独立物理
检查写收据。附着语义是 `idealized_follow_proxy_pick_place; not contact grasp`。

`--output` 必须为新目录或空目录；将 task/config 放在其外，避免覆盖旧证据。
`--native-timeout-s` 可显式设置独立原生进程的运行预算（默认 120 秒）。
错误、idle、超时同样保存输入 hash、原生状态和是否开始/完成物理运行；
不会把未启动 Isaac 的拒绝算作物理成功。

EMOS 结构化输出条件的可复用输入位于
`algorithm/system/coordination/emos/configs/isaac_ur5_{task,adapter}.json`。
下列命令会进行新的真实模型调用和有界物理试验；旧配置未显式设置
`endpoint.output_contract_version` 时仍使用旧输出条件：

```bash
source ~/.bashrc
PYTHONPATH=.:source/EAI /home/airs/miniconda3/envs/env_isaaclab/bin/python -m tools.algorithm_adapters.isaac_pick_place \
  --algorithm emos \
  --algorithm-python /home/airs/miniconda3/envs/env_isaaclab/bin/python \
  --task algorithm/system/coordination/emos/configs/isaac_ur5_task.json \
  --config algorithm/system/coordination/emos/configs/isaac_ur5_adapter.json \
  --native-timeout-s 180 \
  --output records/algorithm_adapters/emos/isaac_structured_recheck
```

配置只保存端点环境变量名；schema、实际提示词和原始模型输出按运行记录。
当前 EMOS 验证范围为 stage-one 讨论/分配到 EAI 技能执行；外部物理反馈未送入
原生 stage-one（它没有此接口），不能视为完整 Habitat-MAS stage-two 复现。

## 测试

BT 通用 DAG / 双 UR5 有界验收入口：

```bash
/home/airs/miniconda3/envs/env_isaaclab/bin/python -m pip install greenlet==3.2.4
PYTHONPATH=.:source/EAI /home/airs/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.algorithm_adapters.isaac_bt_multi_robot \
  --endpoint http://172.17.0.2:18767 \
  --source-root /tmp/heterocoord-algorithms-20261028/bt-capabilities \
  --output records/algorithm_adapters/bt_capabilities/multi_robot_recheck \
  --execute
```

endpoint 必须使用 `ur5_1 ur5_2` roster（启动方法见 BT 报告），地址按容器实际 IP
调整。两个原始抓放执行器共用一个场景和物理时钟，回传实测批次反馈，然后原生树
派发汇合检查。检查使用仿真真值，不宣称视觉感知；抓放仍为 follow-proxy 语义。
默认不加 `--execute` 只准备输入；`--native-preflight` 仅原生竞拍预检，无物理反馈。
输出须为新目录或空目录。证据统一位于
`records/algorithm_adapters/bt_capabilities/multi_robot_20261028/`。

通用 adapter 支持 DAG 输入；此宿主是明确的双 UR5 三任务验收场景，其他物理技能需
接入各自 executor，不能因协议支持就声称物理能力已验证。

不同算法使用独立解释器运行各自 tests，避免全局模块、ROS 和 ML 依赖相互污染。
若在同一 pytest 命令收集多个 `test_native_adapter.py`，使用
`--import-mode=importlib` 避免同名模块冲突。BT 真实 ROS 测试须设置其 endpoint，
EMOS scripted completion 测试只证明协议，不能代替真实 API 验收。

```bash
PYTHONPATH=.:source/EAI PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  benchmark/tests/test_native_algorithms.py \
  benchmark/tests/test_native_source_integrity.py \
  algorithm/heterocoord/tests/test_registry.py task/tests
```

不要运行全仓 pytest：运行日志、下载代码和历史测试不属于本轮验收集合。
本轮验收矩阵和全部范围限制见
`algorithm/heterocoord/NATIVE_ALGORITHM_ADAPTATION_REPORT_20261028.zh-CN.md`。
