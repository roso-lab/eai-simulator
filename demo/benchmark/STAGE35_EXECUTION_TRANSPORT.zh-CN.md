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

实施与测试结果在完成后补记。native restore/sham、盲测公开投影及六轴生产器仍按
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
