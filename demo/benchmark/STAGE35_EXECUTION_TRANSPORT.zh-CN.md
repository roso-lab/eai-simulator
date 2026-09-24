# 阶段35：真实下行执行与餐厅可达干预

基线 `0661e39`，开发分支 `codex/benchmark-execution-transport`。

405 作者包已验收。本阶段补真实执行链，不重复扩展任务数量或为评分参数赋临时值。

| 优先级 | 并行工作 | 出口 |
|---|---|---|
| P0 | 共享链路上的下行派遣/取消 | 到达所有执行者后才执行；延迟、TTL、丢弃、取消竞争和成员退出有可重放记录 |
| P0 | 餐厅版本化观察触发 | 单 Carter 与多机合法路径均可触发；实际成功观察之后、业务回执之前；未触发不计有效干预 |
| P1 | 原生入口和可信 idle 时钟 | opt-in 配置进入 episode 绑定，旧入口不变；原生后端可推进真实时钟而非伪造时间 |
| P1 | 双机验证与评分原始证据 | 本机/ljy 隔离树检查；原始事实可归档，不生成无证据的六维成绩 |

执行 hook 属可信宿主接口，参与者不接触隐藏计划、未来事件或 oracle。通信服务共享同一
CommunicationRuntime，传输账本只计一次。原生团队加入必须有实际后端支持，不能仅增
账本身份便冒称机器人已入场。冻结题面、见证与 reach 不改，三个发布标志保持 false。

本阶段开始时没有运行中的回归/代理写入；前两名 Sol/high 代理复用，分别拥有 runner/
downlink 与独立 restaurant intervention 模块；主任务拥有原生入口、评分归档与验收。

本阶段实施与测试结果见下文。native restore/sham、盲测公开投影及六轴生产器仍按
阶段34/33路线后续推进，本批不宣称这些门禁已经通过。

## 原生入口与证据边界

`goal_restaurant_algorithm_episode --execution-profile <JSON>` 显式启用 request v2；
不传该参数仍是 v1。配置必须包含且只包含以下字段：

- `schema=eai.restaurant.native_execution_profile`、`version=1`；
- `communication_contract`：显式声明 `backhaul_uplink/backhaul_downlink/p2p` 三池；
- `downlink_ttl_s`：正有限数；
- `restaurant_interventions`：布尔值；
- `factor_id/factor_level`：已有合法因子档，随版本化请求冻结。

容量取值是该次诊断的显式输入，不构成正式 C0 锚点，不能拿方法自身流量回填档位。
profile、题面哈希、执行源码与 episode 绑定；开启后的命令完整内容经过共享账本，
逐接收者计字节。等待按实际控制周期向上取整并读取物理时钟；不能超模拟预算，也
不延长任务原定截止。已过执行窗口的命令返回失败而不再触发技能。

餐厅 observer 的首次成功服务区观察会发布可信 observation 上行；该版本只测这个
预注册触发消息，不宣称全部传感器/服务流量已接线。生命周期处理需要实体后端回调；
当前原生入口没有加入/退出/能力降级回调，因此对应处理档保留 unsupported 证据，
不能算有效干预。固定 EMOS 任务展开仍是执行基础设施诊断，不证明被测模型自主拆题。

原生成功归档额外复算共享传输账本，核对完整命令及送达/消费时间先于原生开始时间。
评分侧车只索引 profile/report/replay 原始事实；44 项未冻结评分参数不赋临时值。

## 双机验收（2026-09-24）

实现提交 `e57733b`；原生验收外壳修复 `ba64712`。外壳现在以完整业务事实核对诊断
完成，不能要求开启 `business_outcomes_validated` 才认定一次基础设施测试成功。

- `e57733b` 本机 557 passed（通信/runner/工具/成本/部署/题包矩阵 24 文件），
  ljy 隔离树 115 passed（7 文件）。
- `ba64712` 本机 native 两文件 32 passed；ljy native transport 9 passed。
- ljy 新树 `/home/ljy/eai-simulator-worktrees/benchmark-stage35-transport`，
  episode `restaurant-algo:1dd53f7df0384ea4ad368a7b6d77f3a3`：
  7 技能成功，runner error=null，独立 `--verify` validated=true，58.09 秒。
- 7 条完整派遣均先送达/消费再执行。首条发布 0.0 s，送达 0.10094 s，
  原生开工 0.12 s（0.02 s 控制周期）；原定任务窗口未延长。
- 首次厨房观察上行触发、送达、公开 observation 均由真实事实和账本复核通过。
  共享账本总计 7,752 字节：下行 6,885，上行 867；唯一可靠源，零重复计费。
- 归档验真文件 SHA256：
  `9587d80f69ac8f5695e3867135f7f2c2930e6b61e6d8a035beb78abe0c0c8658`。

命令均在 `env_isaaclab` 下执行；轻量测试使用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`
与 `PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy`。
完整原生复现（在 ljy 隔离树，输出目录必须新建）：

```bash
EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller \
EAI_USD_ROOT=/home/ljy/eai-simulator/usd \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
/home/ljy/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.benchmark.goal_restaurant_algorithm_episode \
  --output-root tmp/restaurant_benchmark/<new-run> \
  --products-root /home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e/tmp/restaurant_benchmark/products-candidate-v7 \
  --source-usd /home/ljy/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda \
  --device cpu --algorithm emos \
  --execution-profile tmp/stage35-validation/native-profile.json --timeout-s 900
```

实际配置为三个池显式 `capacity_bps=1000000/fixed_delay_s=0.1`，downlink TTL=30，
restaurant_interventions=true，communication_quality/immediate；观察触发按冻结因子处理表改上行池。
这只是诊断配置，**不作为绝对 bit/s 或 C0 的正式测量**：现有 runtime 的容量单位与
分析层 bit/s 文案已确认存在 8 倍歧义（runtime 实际 B/s），下一阶段先做兼容的
版本化修复再生产容量阈值证据。旧实验 `None` 参考实际使用有限容量也要单独收口。

失败记录未覆盖：v1 未指定资产根而尝试联网下载，277.46 秒后无 episode 完成，
属于环境启动缺口；v2 的七项原生技能和业务事实成功，但旧外壳错误要求开启业务
发布标志，修复后以新源码、新 episode v3 重跑通过。原始三次档案与双机日志复制在
本机 `tmp/stage35-validation/`，不提交运行产物。ljy 原仓用户改动未触碰。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`
保持。`business_episode_execution_validated` 是既有单次原生诊断证据字段，不能解除
发布门禁，也不证明自主拆题、生命周期动态处理或全部 405 条件的原生可执行性。
