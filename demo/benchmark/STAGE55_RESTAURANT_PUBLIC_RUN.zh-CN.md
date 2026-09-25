# 阶段55：餐厅公开策略入口与通信条件边界

输入基线 `e16edeb`。本批接通可复现的公开策略运行、原生事实归档和评分侧车，
并显式绑定原题包通信处理。它不是正式通信因子样本，也不签发初始可行性真值。
`scoreable`、`execution_validated`、`business_outcomes_validated` 保持 false。

## 可复现入口

`goal_restaurant_public_policy.py` 是独立的弱脚本策略：从公开几何选择两台不同的
兼容地面机器人，自行生成导航、观察和 relay 计划；只依据已交付的结果推进前置条件。
该策略始终声明 deployable，没有动态恢复能力。它的声明不是隐藏 oracle，也不是
benchmark 给所有参与者规定的分工。入口保留八台候选，不读取作者见证计划。

准备显式通信 JSON，例如以下**诊断配置**：

```json
{"pools": {
  "backhaul_uplink": {"bytes_per_second": 1000000, "fixed_delay_s": 0.04},
  "backhaul_downlink": {"bytes_per_second": 1000000, "fixed_delay_s": 0.04},
  "p2p": {"bytes_per_second": 1000000, "fixed_delay_s": 0.04}
}}
```

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
python -m demo.benchmark.goal_restaurant_public_run \
  --products-root /absolute/prepared-products \
  --communication-contract /absolute/pools.json \
  --output tmp/restaurant-preflight-new \
  --episode-id public-diagnostic-1 --preflight-only
```

使用 `env_isaaclab` Python；去掉 `--preflight-only` 并换一个新输出目录运行原生诊断。
默认 `--device cpu --seed 17 --warmup-ticks 240 --max-ticks 4200`，
可显式选择 `--device cuda:0`。资产准备沿用 `tools/assets/prepare_restaurant_benchmark_assets.py`，
不下载或修改冻结资产、不放宽哈希。既有目录拒绝覆盖；预检不启动 Isaac。
原生完整档案在新目录，评分侧车在 `scoring/`；失败保留已收集的主要事实。
进程正常结束与业务成功分别报告。tick 上限不是可信业务 horizon 终止证明。

## 通信与 oracle 的真实边界

冻结事件是“首次跨运动类型观察、首次业务提交之前”，不是任意观察或整个回传池。
仅有两台地面观察者的公开策略不建立跨运动类型信息传递，必须报告未触发；
不能为了覆盖干预强制模型选无人机。当前共享池的单消息处理能力不足时明确记录
unsupported，不临时修改全池冒充被选中消息的 delay/window 语义。
原题包与显式 NATIVE-02 派生任务的关联通过维护的 deployment companion 校验，
不把原 hidden evaluator 假称为直接为派生任务发布的评测器。

**历史单位更正：** Stage53/54文字里的“1 Mbps”不准确。原档案运行时
`capacity_bps=1000000` 是遗留字节/秒字段，即 **8 Mbit/s**；本入口优先让用户用
`bytes_per_second` 明确输入。旧原生归档字节不改。该诊断值不是任务族 C0，
8192字节/1秒的隐藏处理参数也不是 C0；不得从 offered load 反推方法专属档位。

初态采样、名义路线长度和同一 episode 后来的成功都不足以倒签 initial oracle。
新 episode 在参与者作答前，需要独立认可的有限构造性证书，绑定准确初态域、
来源、资产、条件和完整技能/时间保证；负标签另需排除合法替代路径。
这里验证的是校准技能适用域内至少存在一条合法完成路径，不要求证明控制器在所有
噪声下必然成功。初始可部署与之后执行失败可以同时成立；平台资格和模型结果分开。
当前仍 `oracle_status=failed / feasibility_label=null`，不进入正式部署判断分母。

## 验收与下一出口

本批验证记录在 `tmp/stage55-validation/`。工程条件+公开入口24项、相关旧功能106项、
旧干预器19项通过；协调者公开入口7项通过，计数重叠不累计为独立样本。
独立审查24项专项、274项受影响回归通过；磁盘条件、通信/会话重放、业务事实、
成本来源与评分metadata独立重建完全一致，172个源文件零漂移、无发布标志抬升。
独审接受本批有限组件与诊断整链，不接受正式因子样本、oracle、baseline或发布资格。
一次维护CLI原生运行 `native-public-1` 正常退出，宿主346.08秒、原生72.18秒；
5/5技能成功，三个物理谓词与各自公开可见性均有证据，直接导出评分侧车。
两区检查physical61.06秒/available61.12秒，服务提交72.12秒/结果72.18秒；
10226字节=4122下行+6104上行，10次接受动作，激活和规划计算仍null。
这是新CLI整链验证，不是新的控制器可靠性统计、LLM测试或正式因子/部署判断样本。

原生请求绑定172个源文件，终态零漂移；新初始8台root向量与Stage53逐值一致，
只证明这些已测向量一致，不证明完整模拟/控制器状态等价或自动取得oracle转移证书。
`condition-review.json` 从实际接受任务和通信机器人端点确认全为地面成员，
因此 `not_triggered/not_applied`，不伪称准确处理已执行。前期 `preflight-1` 早于
最终conditions补丁；最终同源证据以原生请求、asset-preflight与源hash为准。
`ljy` 本次仍 `No route to host`，没有远端验证结论。

R1–R5 保持未完成。下一出口是忠实的单条通信处理实现与有限事前 oracle 资格，
然后才是准确条件下的完整正式 baseline；不继续扩建轮传感器、通用几何或重复归档层。
