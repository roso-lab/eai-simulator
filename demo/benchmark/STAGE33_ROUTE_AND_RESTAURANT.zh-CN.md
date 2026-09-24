# 阶段 33：路线裁定采纳与餐厅升主场景冲刺

日期：2026-09-24  
基线：`419d15f` → 本批推进中  
路线裁定：`tmp/stage33_route_decision_viewer.md`（viewer 产出，含主任务修正）

## 路线采纳与用户覆盖

viewer 三批次路线（通信契约与评分定义收口 → 单场景真实执行/下行/恢复 → 六能力生产器与聚合）
整体采纳。**用户覆盖项：餐厅升主场景为最高优先级**——viewer"缓做餐厅"的依据
（餐厅审计称仅 Carter 有原生证据）已过时，修正见下节。餐厅轨道与批次一并行推进，
headless 槽位冲突时餐厅优先。

## 餐厅现状修正（推翻 STAGE32 审计的过时表述）

`STAGE32_RESTAURANT_ADMISSION_AUDIT.zh-CN.md` §2"仅 Carter 有原生证据"基于
`goal_driven_tasks.py` 的过时 docstring。事实（git 出处）：

- `0e346df`（09-22）route-level 准入通过 go2/lite3/iris；
- `65d84d5`（09-22）双 M20 经注册导航控制器通过准入；
- `demo/restaurant_benchmark/config.py` 的 `BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS`
  已含**全部八机**——八机出生位/路线/接触/悬停准入诊断已全过。

## 本批已落地：首个正式多机原生业务 episode（ljy，2026-09-24）

```bash
# ljy benchmark-stage32c worktree（56f1b19），CPU、确定性 EMOS 指派策略（无 LLM 依赖）
python -m demo.benchmark.goal_restaurant_algorithm_episode \
  --output-root tmp/restaurant_benchmark/algo-episode-emos-formal-20260924-v1 \
  --products-root <ljy products-candidate-v7> \
  --source-usd /home/ljy/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda \
  --device cpu --algorithm emos --timeout-s 1800
```

结果：`status=completed`，`business_episode_execution_validated=true`，
`skill_receipt_count=7`（三机 executor：双 Carter 导航/观察 + iris 悬停 relay），
`error=None`；独立归档验真 `--verify` → `validated=true`
（episode `restaurant-algo:d35af72d0fde48dea06a8432a48cb065`）。
发布标志保持 `execution_validated=false / scoreable=false`（单 episode 不解除）。

## 餐厅→主场景剩余准入清单（真实缺口）

| # | 缺口 | 状态 | 出口 |
|---|---|---|---|
| 1 | 八机原生准入诊断 | ✅ 已完成（09-22 两提交） | — |
| 2 | 判卷证据链：reader 契约层 | 🔄 子代理实现 goal_reader_restaurant.py（对齐四场景模板+证书+探针注册） | 聚焦测试全绿 |
| 3 | 判卷证据链：native 探针接线 | ⏳ 契约层就绪后按 probe_collection_plan 在 ljy/本机 headless 采集 | native_probe_evidence 归档+注册审计 |
| 4 | 首个正式多机业务 episode | ✅ 本批完成并独立验真 | — |
| 5 | 注册翻转 package_kind → goal_bundle | ⏳ 发布动作：需 2/3 证据 + release review；同步改 authoring_status/execution_status 一致性字段与 `test_restaurant_runtime_scene.py:299` 的状态锁 | goal_suite 接受餐厅进条件/机制矩阵；五场景矩阵重生成（v2 报告） |
| 6 | 过时文案修正 | ⏳ goal_driven_tasks docstring"Only Carter routes…"改写 | 不改哈希语义（docstring 不入题包哈希，需验证） |

因子处理档解锁：题包内"通信/团队变化档必须等第一个正式原生业务 episode 通过后才解锁"
的前置（#4）已满足；档位生成与 7 组对照对补齐在 #5 翻转后随矩阵重生成验证。

## 批次一四项拍板（已冻结，出处 tmp/stage33_route_decision_viewer.md）

1. **共享传输账本**：runner 持有唯一受限 CommunicationRuntime（telemetry 已支持
   runtime 注入，架构无阻）；完整接线与聚合去重随批次二下行调度器落地。
2. **端点与池契约**：入 evaluator 的 episode/run 启动绑定；不动 participant manifest
   必填项与公开题包哈希。
3. **真实下行执行责任**：host/runner 执行调度层生成规范派遣/取消消息；批次二实现。
4. **跨域 ID 碰撞入服务账本**：本批已实现——host 的 comm 分支把 goal action id 集
   经 `reserved_call_ids` 传入服务，碰撞以稳定 `call_id_collision` 码落服务调用账本
   （pose/camera 交叉方向保留 host 级 fail-closed 检查）。

## 并行分工快照（本批）

- 子代理A：goal_reader_restaurant.py 契约层+探针注册（运行中）
- 子代理B：P2 phase3 旧参与者回归+不变性测试强化（运行中）
- 子代理C：六能力评分定义矩阵文档（运行中）
- ljy headless 槽1：餐厅正式 episode（✅ 完成验真）
- 主任务：四拍板实现（#4 已落）、阶段文档、餐厅准入清单

## 批次一收口结果（2026-09-24，a1b16ee 后）

- **P2 phase3 验收通过**：goal 六文件族 149 passed；handoff_preflight 全新目录
  exit 0（suite validated=true，36 包/28 因子对，4 场景 all_handoff_ready，
  公开清单精确 3 文件，公开 adapter 冒烟全 accepted）；零改动 diff 审计成立
  （goal_driven_contract/goal_session/goal_driven_tasks 自 phase1 无触碰）；
  TeamWeaver/EMOS smoke 经真实适配器离线通过（无 LLM/网络）。不变性测试强化：
  两侧真正执行 select_team→submit_plan→dispatch→consume 四类动作并比较完整
  事件流/动作序列/可信终态，附突变自检。报告 tmp/p2_phase3_regression_report.md。
- **六能力评分定义矩阵**（`SCORING_DEFINITION_MATRIX.zh-CN.md`，538 行）：六轴
  指标/生产器/门禁/缺失现状 + 44 项未冻结参数显式编号 + 三个关键定义决策点
  （TA 公式、Q(t) 进展底座、学习 R/C 记忆清空等价性）。拍板前实现者不得引用
  临时取值。
- **碰撞入服务账本测试**：comm call_id 与 goal action id 碰撞以稳定
  `call_id_collision` 落服务调用账本（host 文件释放后补上）。
- **餐厅 reader 契约层**：goal_reader_restaurant.py + 19 测试 + 探针注册表扩展
  （33+61 passed）。open questions：观察转移的身份绑定抽象与并行联合转移建模
  留待 native 准入审核独立复核（证书 treated_as_uncovered 政策覆盖）。

## 判卷证据链闭合与翻转方案（2026-09-24 晚，收口批）

**判卷证据已从真实归档推导**（`goal_restaurant_probe_evidence.py`，9 测试 +
真实归档重放全过）：对 ljy 验真 episode `restaurant-algo:d35af72d…` 的
native evidence log 逐记录重放——每个事实独立取自自己的证据记录（区域到达←
arrival+接触健康记录、区域观察←observation 记录、回执←relay publication +
readiness_receipt），四个测量点全部 grounding verified + oracle verified +
deployable（事实 0→2→4→5 递进），初始 roster 取自 episode 自己的 select_team，
证据文件 tmp/restaurant_benchmark/restaurant_probe_evidence_20260924.json
（development marker: diagnostic + restaurant_native_candidate_evidence_pending）。

**注册翻转方案（剩余唯一缺口，已探明全部代码位点）：**
1. `goal_scene_registry.py` `_SCENES["restaurant"]`：package_kind→goal_bundle
   （编译一致性检查要求注册字段==题包 gate 字段，需同步 3）；
2. `goal_driven_tasks.py` build_goal_task_package 的两处 restaurant 特判：
   pool_profile 限制与因子档闸门（前置"native business episode"已满足，解锁
   communication_quality 全档/team_continuity 全档；"heterogeneity admission"
   = 八机准入诊断，亦已满足）；
3. `goal_driven_tasks.py` `_restaurant()` hidden gate 的 execution_status
   （external_pending→native_witness_verified 等新值）与 `restaurant_benchmark/
   config.py` 的 BENCHMARK_ADMISSION_STATUS，同步
   `test_restaurant_runtime_scene.py:299` 状态锁；
4. `mechanism_templates.py` canonical_template_id 拒绝 restaurant——需为餐厅
   作者 M0 机制模板（decomposition/role_allocation/communication_adaptation/
   role_churn_recovery，对照其余四场景每场景 4 个 M0 模板）；
5. `task_coverage_matrix.json`：scene_keys 加 restaurant + scene_coverage 补
   餐厅行（canonical templates/status/m0_required_mechanisms）；
6. 计数口径更新：headline 324=4场景×9×9 → 五场景新口径**需用户拍板**；
   逐场景发布单元下亦可先只翻 package_kind 让餐厅进矩阵基线，机制模板与
   因子档分两步走。
翻转后：goal_suite 重生成（v2 报告）+ handoff_preflight + 双机回归。

## 探针凭证纪律（viewer 审计发现，必须遵守）

`goal_native_probe_runner` 的 `host_installed` 只是接线模式，不能作为 native 准入
凭证：正式路径必须绑定实际团队/能力/资源版本，probe 不得复制 provider 的
`true_facts` 自证正确；native 采集与发布审核分开，不能改 scope 加前缀就晋升。
