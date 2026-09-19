# 验证记录（2026-09-18）

所有 Python 命令使用 `/home/pupil/miniconda3/envs/env_isaaclab/bin/python`。
没有启动 Isaac、PhysX、ROS、GUI 或网络下载；没有修改原 `demo/city` 入口、USD、Flow 或共享
主轨代码。本轮为人类响应速度接口对 `source/EAI_assets/EAI_assets/humans` 做了最小公共 API
扩展。

## 执行记录

1. 读取主对话与工作区实现：主线程读取工具返回了最近 turn 状态，但正文为空；随后只读
   读取该线程本地记录的 assistant phase 消息，确认目标驱动契约、三场景生成器及执行 pending 边界。
2. 完成第一轮新测试、原 city 测试与共享 goal-driven 测试：**45 passed**。
3. 加入起点审计/选队坐标适配后，运行 README 中同一联测命令：**35 passed, 12 failed**。
   所有 `demo/city_benchmark/tests/test_foundation.py` 和 `demo/city/tests` 测试通过。
   12 项失败都在并行修改的 `source/EAI/test/test_benchmark_goal_driven.py`：
   `demo/benchmark/goal_driven_tasks.py:review_goal_task_design` 新增了 `outcome_support`
   审核，但当时三场景 witness 构造器没有该字段，报 `lacks outcome evidence bindings`。
   本任务没有更改该共享文件，也未绕过或弱化这项检查；这是一份当时的联测快照。
4. 真实城市资产输出：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 \
   PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
   /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.city_benchmark.prepare \
     --output-dir tmp/city_benchmark/authoring_03
   ```

   导出成功；0.25 m/cell、840×840 栅格；23 栋建筑、246 组静态障碍、12 条车路线、
   6 条人行路线、12 处过街点、4 个静态集合点候选。
   栅格包含 free 221640 / occupied 440840 / unknown 43120 个单元。
   逐一重新计算全部 10 个产物的 SHA256，与 integration manifest 一致。
   重新解析并调用共享 public task/API validators，均通过。
5. 人工查看 `map_preview.png`，确认北向上、路线与静态障碍叠加、候选点标识。
6. CLI `--help` 通过；7 个 Python 源文件 AST 解析、局部源码行末空格检查通过。
7. 为新场景添加局部 `.gitignore` 例外，使新增 tests 可由 Git 发现，不改根 `.gitignore`。
8. 交接前独立复跑城市局部测试：**35 passed in 0.63s**。命令如下；这次未重跑共享
   goal-driven 测试，因此第 3 项保留为并行开发期间的历史联测快照。

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
   PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
   /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -p no:cacheprovider -q \
     demo/city_benchmark/tests/test_foundation.py demo/city/tests
   ```
   
9. 接入响应状态机和人类动作适配后，使用同一 `env_isaaclab` 命令复跑城市局部测试：
   **39 passed in 0.70s**。新增检查覆盖逐人引导、`jog` 到达记账、manifest 中的 `jog`
   能力、USD 适配调用顺序，以及后续交通的确认回执。

9. 接入响应状态机、人类动作适配和事件回执后，使用同一 `env_isaaclab` 命令复跑城市局部测试：
   **44 passed in 0.87s**。新增检查覆盖逐人引导、`jog` 到达记账、manifest 中的 `jog`
   能力、实际 USD 阶段动作切换与 xform 复用、后续交通确认回执和事件报告 receipt。
10. 共享 goal-driven 回归随后已恢复：城市局部测试、原城市测试和共享测试合并运行
    **79 passed in 2.78s**；共享 goal-driven 单独运行 **36 passed in 1.87s**。
11. 随后主对话并行把共享技能本体升级到 `eai.skills.v2`，并加入执行契约 v2 字段。城市生成器
    已改为读取当前共享版本并生成执行契约、机器人物理声明、区域容量和公开路线；
    `authoring_10` 的公开题面验证通过，城市局部测试为 **44 passed**。并行的三个既有场景
    生成器当时仍硬编码 `eai.skills.v1`，因此当前跨场景联测会在共享文件上失败；本任务没有
    覆盖或修改该并行工作。
11. 城市任务逻辑进一步收紧：人员引导和交通控制必须引用已送达观察，事件报告必须引用已
    登记且未过期观察；交通服务确认后必须经过反应窗口并产生轨迹样本。新增隐藏人员/车辆
    全集绑定、当前 ETA 派遣条件和通信/成员变化的因果影响说明。城市局部测试与 city intake
    联测为 **84 passed**；真实资产重新生成 `tmp/city_benchmark/authoring_12`，intake 仍接受
    10 个 artifact，`scoreable=false`，缺少 `guide_people`、`control_traffic`、`report_incident`
    公共服务技能入口。

## authoring_13 作者准入修复

本轮只修改城市目录中的 `task_logic.py`、`contracts.py`、foundation 测试和文档。修复
authoring_12 反例：缺少完整通路净空证据、负时间差、重复人员/车辆、未绑定隐藏全集均不能
成功；合法但不是最小 ETA 的响应者允许成功，ETA 排名仅保留为诊断。

通路检查须有正时长并覆盖报告回执时刻。人员确认从 `guidance_request_time_s` 起算，
`guidance_time_s` 表示逐人确认；交通确认从 `request_time_s` 起算。公开
`business_constraints` 和私有 policy 同源，明确各窗口、episode_started 时钟及总预算。
骨架仍只接收可信作者/运行时摘要，不实现来源认证、物理取证或评分；正例通过也不改变发布门槛。

执行命令（`env_isaaclab`）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -p no:cacheprovider -q \
  demo/city_benchmark/tests/test_foundation.py demo/city/tests \
  source/EAI/test/test_benchmark_goal_city_intake.py
```

结果：**157 passed in 1.63s**。包含独立的通路缺失、时间倒序/超时、全集缺失、身份重复与
非法身份反例，以及非最小 ETA 的合法正例和公开时限一致性检查。修改的 Python 文件 AST
解析及行末空格检查通过。

真实资产重新 prepare 到 `tmp/city_benchmark/authoring_13`；
`load_city_goal_candidate(..., verify_sources=True)` 通过 **10 个产物 + 24 个源文件**校验。
公开时限文字与私有 policy 一致。类型保持 `preparation_draft_not_goal_bundle`，
`authoring_status=blocked`、`scoreable=false`。没有启动 Isaac/PhysX/ROS，也未重跑主线全矩阵。

## 反思及限制

城市服务边界新增 `service_backend.py` 和独立符合性测试。`CityRuntimeReadbackAdapter`
只定义 episode 所有者向网关交付的可信读接口；本轮没有 Isaac 服务源，也没有实测
人员轨迹、车辆危险区轨迹、服务端回执或完整通路的物理验证。参考状态机输出只能标记为
`reference`；源回报 `runtime` 仍须在未来真实部署中审查其生产者和读回质量。网关
不生成完整隐藏人车名单，也不宣称服务单次闭合等于整题成功。

`env_isaaclab` 运行 `test_service_backend.py`、`test_foundation.py`、
`demo/city/tests`、`test_benchmark_goal_city_intake.py`：**176 passed in 1.63s**。
未启动 Isaac/ROS/GPU，其他主轨测试没有在本轮运行。

生成 `tmp/city_benchmark/authoring_15` 后通过共享
`load_city_goal_candidate(..., verify_sources=True)`：10 个产物、24 个源文件哈希校验通过；
`preparation_draft_not_goal_bundle`、`authoring_status=blocked`、`scoreable=false`。
先前生成的 `authoring_14` 是适配器接口落定前的中间产物，以 `authoring_15` 为本轮交接包。

## 现场角色生命周期复核

共享服务提案要求有时限的现场驻留与请求因果绑定；城市 `task_logic` 的业务结果摘要不负责
验证现场角色。城市服务边界现从可信源读历史连续角色区间：人员引导与交通管制分别覆盖请求至
逐人/逐车确认，之后世界中的人员移动与车辆反应可独立继续；报告只需在提交时有带持续时间
的现场角色，接收方回执和连续通路检查独立于报告者。没有 transfer 协议，替补执行须生成新
请求，不得沿用旧执行者身份或将参考移动当作真实世界证据。退出前确认失败、确认后退出仍可
完成、报告后退出仍可取得真实回执，以及新成员重新提交均有纯 Python 反例。

同一 `env_isaaclab` 联测命令再次执行：**181 passed in 1.59s**，没有启动 Isaac/ROS/GPU。
新包 `tmp/city_benchmark/authoring_16` 经共享
`load_city_goal_candidate(..., verify_sources=True)` 验证，10 个产物与 24 个源文件通过；
仍为 `preparation_draft_not_goal_bundle / blocked / scoreable=false`。

## Stage12 离线可信宿主接线

新增 `episode_adapter.py` 的 `CityEpisodeEvidenceHost`，缓存实际送达的观察、带起止时间的
机器人现场驻留、宿主准入回调、明确注册的服务请求，以及外部确认/人车世界读回/接收方回执。
没有服务确认的轨迹不能进入证据；缺少逐对象确认时不生成完整 service_update；错误接收方、
错模式、角色驻留断档、未送达/过期/被取代的观察被拒绝。显式标为 reference 的闭合读回
仍返回 `reference_complete`，`scoreable=false`。写接口只供 episode 宿主调用，不能给
participant；外部证据引用不等于物理取证认证。

`env_isaaclab` 在不启动 Isaac/ROS/GPU 的条件下运行 episode adapter、城市业务后端、
foundation、原 city 和共享 city intake：**189 passed in 1.58s**。
新包 `tmp/city_benchmark/authoring_17` 经
`load_city_goal_candidate(..., verify_sources=True)` 校验，10 个产物和 24 个场景源文件通过，
仍为 `preparation_draft_not_goal_bundle / blocked / scoreable=false`。这 24 个场景源文件
不包含新 Python 宿主代码；部署配置冻结还须另外绑定 `episode_adapter.py` 的代码哈希。

地图中的树木 AABB 把原 Carter/Iris 起点标为冲突，未宣称真实碰撞。导出了附近候选：
Go2 `(-69.125, 49.125, 0.60)`、Carter `(-66.875, 47.125, 0.20)`、
Iris `(-61.125, 44.625, 3.00)`，彼此距离至少 3 m。坐标仅用于配置准备；
需要实际碰撞、地面支撑、姿态稳定及背景演员布局检查后才能使用。

集合点没有容量和动态火场安全认证。跨路沿连接保持保守阻塞，仍需实际路径连通性和
形态通行验证，不能把候选点数量当作可执行方案数量。4 名原演示路人的安全行走路径
不构成有危险暴露的疏散任务，也不满足本轮业务结果。

输出 `public_contract_validated=true` 仅表示题面结构兼容。
`authoring_status=blocked`、`execution_validated=false`、`scoreable=false` 保持不变。
尚未建立完整可执行 witness、公共动态实体引用、人员引导/交通管制/事件检测/响应回执
及真实导航闭环。任务逻辑中的 15/35/10/5/20/120/15 秒已写入公开草稿并明确计时锚点，
仍须地图、起点和真实执行审核；它们不是事故演示的 2.5/6.8/8.3 秒视觉时间。详见阶段清单 S5。
