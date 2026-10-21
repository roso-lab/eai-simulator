# HeteroCoord v3 方法—工程部署审计与任务计划

更新日期：2026-10-20  
代码基线：`b2befa33cf3bb32be61f7fd31cf8c61440060d96`  
论文基线：`2f82c39da074902b19362ed7502e57c187053d27`

## 1. 方法思路与工程边界

HeteroCoord 不是一个单体控制器，而是一条分层评测链：任务图与机器人能力定义问题；系统算法负责分解、分配、协调、通信和重规划；Skill/Controller 负责执行已分配任务；EAI/Isaac 负责场景、动力学和 `env.step`；独立 evaluator 负责物理里程碑与终局判定；benchmark 层负责身份、分支、时间、证据和指标。

核心原则是“算法决定不等于物理成功”。系统输出、退出码、重放写回都只能形成候选证据，只有独立 evaluator 的可追溯证据才能形成 physical outcome。

## 2. 当前部署框架

```text
task contracts/catalog
        ↓
system algorithm / decision provider
        ↓
benchmark RuntimeTaskBinding + EpisodeRunner
        ├─ FleetRuntime（identity / leases / transport / safe-stop）
        ├─ BranchLedger（hash-chain evidence）
        ├─ EaiSessionRuntime + NavigationTaskExecutor
        │      ↓
        │   topology-bound Skill/Controller → SimulatorSession.env.step
        └─ independent evaluator / artifact store
                    ↓
             v3 records → D1–D6 estimators → release gates
```

新版协议新增的静态/CPU层已经独立于 Isaac/ROS/Torch，可在 `env_isaaclab` 中运行合同测试。`FleetRuntime` 默认仍使用 legacy `MessageRouter`；只有显式 `transport_options["mode"] == "shared_v3"` 才启用全队共享 FIFO bit budget 的 `SharedChannelV3`。

## 3. v3 评测链现状

- `scorebook_v3.py`：冻结 D1–D6 公式、证据、聚合层级和 unresolved parameters；旧 endpoint map 保留为 legacy。
- `composition_v3.py`：记录实际 import origin、source/asset/config digest、step owner、dt/substeps 和运行身份。
- `records_v3.py`：类型化 identity、contract/check/obligation/dynamic item/adaptation/resource/calibration/metric；支持 `valid/invalid/unknown/incomplete/na` 与 point/range。
- `oracle_v3.py` / `skill_qualification_v3.py`：将 capability 预筛、七类语义检查、feasibility proof、物理 qualification 与 follow-proxy diagnostic 明确分开。
- `transport_v3.py` / `observation_v3.py` / `replay_state_v3.py`：共享通信计量、字段级观察权、九类 replay participant 完整性。
- `metrics_v3.py`：D1–D6 CPU estimator、D2 capped retention、D4 item semantics、D5 hierarchy、D6 success gate、missing-weight range、Hoeffding half-width。
- `dynamic_v3.py`：冻结 D4 affected-work closure `J`；验证物理 loss receipt；生成 closure-bound typed outcomes；D4 bridge 按 work weight 计算 HierMean。
- `release_v3.py` / `formal_matrix_v3.py`：bundle 必须含完整 D1–D6；computable/statistical/physical/rankable 分离；formal family floor 和 baseline qualification 仍为 gate。

## 4. D1–D6 计算语义

| 维度 | 当前 CPU 语义 | 正式发布仍缺什么 |
|---|---|---|
| D1 | `100*(0.50V+0.30Qorg+0.20I)`，不完整计划封顶 | certified support directory、真实 oracle/evaluator evidence |
| D2 | `mean(min(1,p_l/p_0))`，`p_0=0` 为 N/A | C0–C3 物理 shared-medium、固定目录与三 repeat |
| D3 | nominal relation weights：`mu2,(muJ+muS)/2,mu4` | 完整 relation opportunity 与 external milestone evidence |
| D4 | deadline 用 `e`；time-valued/continuous-supply 用 `(e+phi)/2`；按冻结 work weight 聚合 | Isaac function-loss receipt、remaining-feasibility、healthy comparison、time-value 参数 |
| D5 | family→case→unit→3 repeat，family floor 默认 30 | 30-case certified family、causal replay、source/target checkpoint、sham |
| D6 | success-gated `sqrt(B0/max(B0,B)*T0/max(T0,T))` | 独立 calibration coordinator/reference table、真实 transmitted bits 与 capped time |

## 5. 已完成阶段与证据

WP00–02 完成 scorebook/composition/typed records；WP03–04 完成 oracle/physical qualification 边界；WP05–06 完成 shared transport/observation/replay contracts 及 FleetRuntime seam；WP07–12 完成 CPU D1–D6；WP13–14 完成 evidence bundle/release gates；WP15–16 完成 formal directory/baseline gate；WP17–18 完成 D4 closure、physical receipt adapter 和 typed outcome→metric bridge。

最近回归命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
benchmark/tests/test_fleet_shared_v3.py \
benchmark/tests/test_formal_matrix_v3.py \
benchmark/tests/test_release_v3.py \
benchmark/tests/test_metrics_v3.py \
benchmark/tests/test_transport_replay_v3.py \
benchmark/tests/test_oracle_and_qualification_v3.py \
benchmark/tests/test_v3_contracts.py \
benchmark/tests/test_dynamic_v3.py
```

结果：`31 passed`；`py_compile benchmark/*.py` 与 `git diff --check` 通过。默认 pytest 自动加载 ROS 插件时仍会因环境缺少 `lark` 在 collection 阶段失败；没有安装或修改环境依赖。

补充：当前完整 `pytest -q benchmark` 在收集 legacy `test_eai_adapter.py` 时还会因测试期待的 `CommandNavigationSkillAdapter/EaiTaskExecutor` 不存在而中止；该问题属于现有 legacy API/test 不一致，未在 v3 任务中擅自改动。v3 focused suite 与该 legacy collection error 必须分开解释。

## 6. 当前真实阻塞

以下项目不能由 CPU 合同自动生成，因此当前不能宣称论文正式 rankable：

1. 30 个正式认证 case、scene/roster/topology/skill/evaluator/checkpoint manifest 尚未冻结。
2. 旧审计曾记录 `env_isaaclab` 从 `/home/pupil/eai-simulator/source/EAI*` 导入；该结论已被项目运行时边界取代。当前实验必须通过 `tools.project_runtime`，从 `/home/pupil/HeteroCoord/source/EAI*` 导入，并使用项目内 `usd/`、controller 和缓存根。外部 editable tree 不再作为当前运行时来源；composition manifest 仍需固定来源/资产哈希。
3. 尚无真实 Isaac physical skill qualification、function-loss adapter、shared transport 多跳/重传资格。
4. D5 causal replay/sham、D6 calibration reference table、官方 baseline adapter/checkpoint 尚未提供。
5. 论文中的 C0–C3、D4 closure edge/time-value、D6 calibration 数值仍需用户冻结。

当前 CPU closure 将 shared robot/tool/space alias 归一化为 `shared_resource` 边族；这只是结构适配，正式协议仍需决定是否保留三类资源的独立 edge family，以保证 D4 closure 可解释性。

## 7. 后续任务计划

### P19：部署集成审计（当前下一阶段）

- 在 `run_session_episode` 入口冻结 composition manifest 与 transport policy，确保 shared_v3 不是隐式默认。
- 为真实 EAI adapter 定义 receipt/remaining-feasibility/progress trace 的最小输入；缺证据只返回 unknown/incomplete 并 safe-stop。
- 对 session/navigation executor、controller adapter、asset roots、step owner 做只读 provenance 检查。

### P20：单场景物理资格

- 用户提供固定 scene/roster/topology/controller/checkpoint 与 asset roots。
- 用一个 nominal case 完成 physical skill、function loss、safe-stop、progress trace 和 evaluator readback。
- 只在全部 receipt/hash/guard/world-preservation 证据齐全后升级为 qualified。

### P21：固定目录与统计发布

- 冻结 30-case family directory、三 repeat、D5 source/target units、D6 calibration batch。
- 运行 artifact/bundle/release gates，输出 valid/NA/incomplete/range，不填补缺失值。
- 最后才执行 Isaac GPU/ROS 正式实验与论文表格同步。

## 8. 结论

当前工程已经具备可审计的新版评测骨架和 CPU 可复验链，但仍处于“structural/contract complete，physical/release incomplete”。下一步应优先做真实 EAI composition 与单场景物理资格，不应继续扩大纯 CPU 假设，也不应把 follow-proxy 或 planned formal directory 写成论文指标。

## 9. WP20 部署资格门

`deployment_gate_v3.py` 现在提供只读 `assess_deployment_v3`：

- 默认要求显式 `shared_v3` transport；legacy 只有在调用者明确 `require_shared_transport=False` 时才可作为兼容模式通过。
- composition 的 project/scorebook/scene/task/robot/controller/topology/oracle/skill/replay/evaluator/step-owner 不能是 `unknown`、`unresolved` 或空值。
- import provenance 与三个资产根必须真实存在，空的 required-list 也会 fail-closed。
- primary physical qualification 必须绑定当前 manifest 的 `skill_id`、`robot_digest`、`controller_digest` 和 `sim_dt_s`；无关机器人的 qualified 记录不能升级当前 composition。
- gate 对外只返回 ready/not_ready/invalid 和逐项 checks，不执行物理操作，不生成论文分数。

WP20 当前回归后 v3 focused suite 为 `36 passed`；composition 与 skill qualification 的 dt/substeps 也已要求 finite/positive/exact-int。

WP21 新增 `DynamicEventDispositionV3`：`recovered`、`certified_safe_stop`、`terminal_failure`、`unknown` 属于事件层处置，带独立 closure/evidence 绑定，不进入 `DynamicItemOutcomeV3` 的 `RecordStatus` 或 D4 item 均值。certified safe-stop 还必须有独立 stop-certificate digest，不能携带 `physical_status=valid`；这样 safe-stop 既不会被静默当作方法失败，也不会被错误升级为 item success。

WP22 将 D4 closure 的资源关系拆成可追溯的 `shared_resource`、`shared_robot`、`shared_tool`、`shared_space` 四类 edge family；旧 `shared_resources` 输入仍兼容，别名字段会被规范化并共同参与闭包扩展，但 digest 保留具体关系族。

WP23 新增 `PhysicalEvidenceCertificateV3`，将 composition/source、closure、operation receipt、remaining-feasibility、guard、world-preservation、event disposition 和可选 transport trace 绑定到 release gate。certificate 的 composition/source、closure/event record 与 bundle 完全一致，且各证据 digest 必须出现在 bundle artifact index；孤立的 `physical_qualified=True` 不再足以进入 rankable。

## 10. WP24 release gate 加固

`EvidenceBundleV3` 现在把 `PhysicalEvidenceCertificateV3` 作为尾部可选字段写入 bundle；`to_dict()`、`write_bundle()` 与 `bundle.digest()` 均覆盖该证书。未传独立证书时，release gate 使用 bundle 内证书；若显式证书与 bundle 证书 digest 不同，则拒绝发布。这样 bundle 不再依赖不可见 sidecar 才能重放物理门控。

本阶段新增的 adversarial 约束如下：

- artifact index 只接受 digest 作为 value，不能通过把 digest 放入 key 自授权；
- event disposition 的全部 `evidence_digests`（以及 safe-stop certificate digest）都必须出现在 artifact values；
- closure digest 与 event ID 必须在同一条 record 中共同出现，防止跨 record 拼接；
- 非字符串 bundle identity 与非法 physical evidence 类型均 fail-closed。

证据：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab python -m pytest -q` 运行 v3 focused suite，结果 `50 passed`；`py_compile benchmark/*.py` 与 `git diff --check` 通过。该结果仍是结构/CPU 证据，不是 Isaac 物理 qualification。

## 11. 下一阶段执行顺序

1. 先补 bundle round-trip/parser 测试，冻结 artifact index 的 label/value 语义和 digest 格式；明确 terminal-failure 证据只能诊断还是允许进入 rankable。
2. 用户冻结一份 composition manifest：scene、roster、topology、skill/controller、checkpoint、资产根、`sim_dt_s`、shared transport policy 与 evaluator 身份。
3. 在单一 nominal Isaac case 接入真实 operation receipt、remaining-feasibility、guard/world-preservation、progress trace、safe-stop 与独立 evaluator readback；只生成证据，不修改论文。
4. 取得 D5 certified family/replay/sham、D6 calibration/reference table 和官方 baseline adapter/checkpoint 后，再运行 release gate；任何缺失继续输出 `NA/incomplete/range`。
5. 最后才进行正式多 case/多 repeat 实验和 Overleaf 指标同步。

## 12. WP25 bundle round-trip

`EvidenceBundleV3.from_dict()` 与 `read_bundle()` 已加入：可重建 D1–D6、物理 certificate 和事件处置，并默认校验写入 JSON 中的 `bundle_digest`。parser 严格检查 physical certificate schema、`qualified` 派生标志以及 bundle/certificate/event 的字段白名单；篡改 bundle 内容或摘要会 fail-closed。旧 positional constructor 与无 physical certificate 的历史 bundle 仍可解析（在关闭 digest 校验时可处理无摘要旧文件）。

证据：release+physical 子集 `21 passed`，完整 v3 focused suite `56 passed`，`py_compile benchmark/*.py` 与 `git diff --check` 通过。边界：该 parser 只验证 bundle 内部序列化与摘要一致性，不读取/哈希 artifact store 的外部文件，因此仍不能替代真实 receipt authenticity、Isaac physical qualification、D5 certified directory、D6 calibration 或 baseline checkpoint。

## 13. WP26–WP27 发布语义与 artifact 内容验证

`assess_release()` 现在将事件证据可用性与排名成功分离：`recovered` 才能进入 `rankable=True`；`certified_safe_stop` 与 `terminal_failure` 可形成 `physical=True` 的诊断证据，但返回 `physical_event_not_rankable`。

新增 `artifact_verifier_v3.py` 后，外部 artifact 不再依赖 digest-only label。调用者必须提供 typed manifest（label、相对路径、SHA-256、可选大小），验证器执行：

- canonical 64-hex SHA-256 分块哈希；
- root 内相对路径检查，拒绝路径逃逸、绝对路径、反斜杠、目录和 symlink；
- 缺失、大小/内容不匹配、读取错误和 unknown 状态显式返回；
- 哈希前后 device/inode/size/mtime 复核以发现基本 TOCTOU。

`ArtifactVerificationReceiptV3` 将 valid entries 绑定到 `bundle_digest` 与 `source_commit`。严格模式 `require_artifact_verification=True` 要求 receipt 覆盖全部物理 evidence digest；默认 structural/CPU 模式不强制该外部输入。该 receipt 只证明字节完整性，不证明 evaluator 真实性、物理成功或论文指标正确。

证据：artifact+release 子集 `27 passed`，完整 v3 focused suite `64 passed`，`py_compile benchmark/*.py` 与 `git diff --check` 通过。下一步仍是可信 composition/evaluator 产生真实 receipt，并接入 Isaac/Fleet 单场景 qualification；当前不能把 CPU fixture 升级为论文正式 release。
