# room06：目标语义接线与预检纠正（2026-10-28）

## 裁决与结果

用户确认：地图不标记楼梯，不提供楼梯入口/出口或预编跨层路线。被测系统读取带楼层的目标名称进行决策；执行是否成功由真实观测留证。此前把 `stairs` 边、`transition` 节点和预计算连通性设为地图准入条件，混淆了输入格式与被测导航能力，现已取消。本轮未修改地图、论文、算法或正式资格门。

已在主工作树快进合入 `origin/HeteroCoord@d8d757b2`，包括本轮远端 12 个地图更新提交。room06 的最新权威文件位于：

`usd/scene/Living and public space/room_06/semantic_map/current/`

最新 save_id 为 `4222564dd7fb45d586866c1a44ccc40e`；12 个语义点，地图审计 `case_ready=true`、failures 为空。二楼目标包括 `floor2-coffee_table` 与 `floor2-bookshelf-1/2/3`。三处书架分别绑定 ground `node:0024/0022/0023`。本次统一了部分名称中的空格为下划线，删除了无须填写的 `isaac_binding`，更新了 aerial 拓扑。

清理：按用户随后明确要求，已删除非规范的 `usd/scene/room_06/` 旧副本。盘点确认 47 个文件全部有分类目录对应项、没有独有文件；其中 41 个字节一致，6 个为旧地图或说明格式差异。28 个 USD/纹理/元数据文件从平铺路径迁入分类路径的版本管理，19 个重复地图文件删除，保留分类目录中更新的地图。旧文件可通过 Git 历史恢复，历史运行收据不改写。资产解析器与依赖图统一指向规范路径，且新增测试防止回退读取旧副本。

全目录盘点未发现另一份同类场景副本。`usd/scene/plane/` 是唯一的、未跟踪的内置平面导航资源，没有分类目录对应物，仍被公共 scene resource API 使用，不能当重复场景删除。两个分类目录中的 restaurant 是不同资产，也保留。

## 工程变更

- room06 preflight 保留 scene/map/save_id/摘要、节点引用、目标导航绑定、机器人技能与任务物体一致性校验；删除楼梯类型、跨层边与预计算可达性的硬门槛，不生成或修改任何路线。
- 新报告明确 `route_precomputed=false`、`navigation_authority=system_runtime`、`room_specific_feasibility=false`。地图输入审计结果单独保留，后续任务配置失败不再被报告为地图不完整。
- 可选 `semantic_point_labels` 把四个既有合同角色显式映射到地图标签，支持“一楼.书架/二楼.书架”等命名。映射必须完整、唯一、确实存在；不猜测名称。已接到 fixed package 的 manifest 语义需求中。真实驱动仍须使用该映射解析导航目标，观测层继续输出合同角色。
- `transition_handoff_node_id` 暂留兼容字段，含义收敛为任务指定的交接观测位置，可以是普通书架节点，绝不表示楼梯端点或路线已可达。旧输入中的 `node:0001` 是一楼书架；只有执行时确实在该指定位置完成交接，才能产生匹配的交接证据。没有把节点存在视为交接发生。
- task runtime 校验新目标摘要的完整性、四个角色与节点成员关系；保留历史已冻结报告的读取兼容。交接、联合持有、里程碑时序等观测要求不变。
- GPU full-task 入口新增 `runtime_source_ref`，把内容寻址的 source factory 接到已有 simulator 注册接口。缺 source/collector、代码哈希变化或 factory 不可调用，均在启动模拟器前失败。scene-load diagnostic 不能注入任务 source。

## 真实输入检查与剩余工作

证据目录：`artifacts/project_runtime/room06-target-preflight-20261028/`。

- `report.json`：旧地图和旧运行输入的首次复核。
- `latest-map-binding.json`：仅把旧输入的地图身份更新到本轮已拉取版本，保留旧任务定义以暴露真实问题；不是可执行的完整任务包。
- `latest-map-report.json`：最新版地图审计通过，目标绑定通过；最终仍失败于 `task object definition digest mismatch: book_batch_01`，退出码 2，分类为 `case_runtime_binding`。

完整 GPU 任务尚未完成。本轮没有启动 GPU；已有 run-05 仅为场景加载诊断，不能升级为任务成功或论文数据。

后续按以下工程顺序推进，不再要求地图补楼梯：

1. 将旧 task setup 骨架更新为完整书批/重物定义，并统一重算定义摘要和运行包。现有骨架只有 count、mass_class；缺书籍 ID、源/目标，以及 runtime 所需的重物尺寸、数值质量与 minimum_holders。参数不得伪装为出题人已绑定的语义。
2. 实现真实 room06 source：实例化任务物体、记录 object_path_bindings、消费被测系统决策并调用技能、从 live state/session 产生观测。当前 collector 与 source 注册接口已在，但生产 source 本体仍缺失。
3. 补齐联合搬运执行与观测：普通 pick/place 的互斥租约和 aerial attachment 不能直接冒充双持有；需要真实的双持有后端与相应观测，尤其现选 aerial 实例的持有状态不能被遗漏。
4. 冻结最新版地图/任务/部署/驱动后执行 GPU 完整任务与 receipt 链。无导航路径、技能失败或超时应作为被测运行结果留证，而不是要求地图作者预先证明该系统能完成任务。

所有正式资格门保持 false。上述工作沿用用户已有开发与 GPU 授权；输入的语义事实若确实缺失，精确列出，不以新的通用审批流程代替工程推进。

## 验证与复验

统一环境：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest`。默认 pytest 自动装载 ROS `launch_testing` 会发生 hook 版本冲突，故仅本次命令禁用插件自动装载，未改环境安装。

1. 受影响集：`-q benchmark/tests/test_room06_runtime_preflight_v1.py benchmark/tests/test_room06_task_runtime_v1.py benchmark/tests/test_room06_fixed_package_v1.py benchmark/tests/test_room06_terminal_material_v1.py benchmark/tests/test_room06_observation_bridge_v1.py tools/validation/test_room06_livt04_gpu_probe.py source/EAI/test/test_simulator_room06_hook_registration.py --tb=short`：189 passed，退出码 0。
2. 推送边界仅运行一次全量：`-q benchmark --tb=short`：1074 passed、1 failed，退出码 1。唯一失败是 evaluator 测试匹配旧的 `frozen real transition node` 报错文本；实现实际已拒绝错误交接位置。
3. 修正该文案断言后，定向复验 `-q benchmark/evaluator/test_room06_livt04.py benchmark/tests/test_room06_runtime_preflight_v1.py tools/validation/test_room06_livt04_gpu_probe.py source/EAI/test/test_simulator_room06_hook_registration.py --tb=short`：68 passed，退出码 0。没有再次运行全量；不能将上一次全量命令记为退出码 0。
4. Sol 独立只读审查：最终静态 diff 无剩余 P0/P1/P2，条件验收要求 fixture 修复后的复验，已由上述 189 项与后续定向复验闭合。其提出的“缺执行 source 不应先启动 Isaac”和“新摘要不能只检查布尔标志”均已落实。
5. ZCode 插件/MCP 能力探测均返回桌面/CLI 不可用，因此测试由主线程按固定命令执行，未声称 ZCode 复验。agentmemory 当前工具目录未提供对应工具，事实记录在本文与团队 handoff。
6. 用户追加路径清理后，`-q source/EAI/test/test_room06_runtime_scene.py tools/validation/test_room06_livt04_gpu_probe.py --tb=short`：34 passed，退出码 0。使用当前源码路径实际解析规范目录 USD，SHA-256 仍为 `e67affcbd6d67941feb8bc7728d6be4cd74ad63d414a8b65db771337b76e300a`；新版地图审计通过，平铺目录确认不存在。
7. `env_isaaclab/bin/python tools/validation/check_scene_map_assets.py`：PASS，退出码 0。该检查额外发现既有资源注册表漏掉 `hssd_room1`、与 catalog 顺序不一致，以及旧测试 stub 缺 `resolve_local_asset_path`；同步补齐空资源声明、顺序和 stub，未为无导航地图的场景伪造地图。

拉取期间 Git LFS 首次因失效的 `127.0.0.1:7890` 代理失败。取消该命令的代理环境后成功取回 LFS；只恢复本次失败快进造成的精确地图路径，再完成快进。主线程代码修改保留；地图工作区与远端提交一致，未永久修改代理配置。

## 阶段反思

预检只应保证输入可解释和证据可追溯，不能替被测系统解题或把解题能力变成地图交付标准。发现缺口后应沿任务配置、执行驱动、观测、收据四层定位；本次纠正完成了地图/运行责任边界和 source 注册接缝，不能因此声称整个 room06 生产闭环已完成。
