# Cursor 接力集成与评分接入：2026-09-23

## 已合入的工作

主仓开发线为 `codex/benchmark-pose-telemetry`，分批推送到 GitLab
`benchmark-task`。餐厅与 EMOS、按需视觉两个工作树的既有提交已等价合入；
没有用旧分支整树覆盖主仓。用户 `my_env.json` 和评分协议原文件未纳入提交。

| 提交 | 内容 |
| --- | --- |
| `7a33fa4`、`b7127dd`、`73334c2` | 可选按需视觉、通信计量、可信GoalSession/episode成员加入 |
| `9bc4b7b`、`d44a671` | 餐厅relay/能力映射、EMOS明确yellow前缀，避免误伤仓库 |
| `4c929f6`、`fd54e5c` | 每回合raw-only评分侧车、归档索引、工具收尾异常 |
| `91d3ced`、`1b07a7b` | EMOS原生root-state接线、逐机器人来源证据；TeamWeaver未消费位置时不伪称采样 |
| `19eee7e` | 用户最新自分解主线与checkpoint需求映射，修正旧固定子任务Track-A建议 |
| `1687a49` | 首次close异常不因重试而消失、render清理未完成报错、缺失事件数不记为零 |
| `730a40d`、`1f15363` | 必要条件内部诊断、二元剩余任务oracle、质量失败分离 |
| `98106e7`、`ce65318` | v3部署门禁、五场景候选、固定风险集和评分侧车接线 |
| `abff921` | 运行中成员/能力失效端到端测试，暴露模型异常后的取消缺口 |
| `daef408` | 编键随机流：传感器和 physics replay 按过程/实体/事件键抽样，保留旧录制兼容 |
| `c0ecbd3` | 云边端通信池：backhaul 上下行、p2p、单机接入、丢包分类和 C/C0 摘要 |
| `14911e4` | 连续 AoI：按 `(recipient, information_id)` 记录覆盖区间、面积、时间均值/P95，并兼容旧事件级回放 |
| `a878a53` | B3a 可信状态绑定合同：冻结五场景任务/注册表/资产/技能版本、事实来源与硬终局谓词；grounding 失败不产生第三类标签 |
| `WORKTREE` | 餐厅资产准备脚本新增显式 `--source-usd` 闭包物化：按冻结 overlay 引用路径复制并逐字节校验，冲突即失败 |

## 评分路线与阶段依赖

最新优先级更新：在以下后续评分/分支工程之前，先实现用户指定的
[部署可行性确认主线P0](DEPLOYMENT_ASSESSMENT_P0.zh-CN.md)。合同、宿主门禁、
求助终止与原始评分接线已实现；五场景仅有诊断候选，原生完整剩余任务oracle
尚未认证，现有参与者仍需显式升级。具体依赖见[阶段清单](DEPLOYMENT_STAGE_PLAN.zh-CN.md)。

用户已确定：提供总体目标和可验证里程碑，模型自主分解、选团队、指派和排序。
冻结评分参数不能等同冻结作者策略。原固定子任务公式暂不适用，应调整评分定义；
不能把见证计划或时序关系发给参与者来迁就公式。

1. 当前已接：原始动作/事件/技能收据/业务结果/工具账本索引、哈希、失败记录。
2. 当前已设计：[Checkpoint合同](SCORING_CHECKPOINT_DESIGN.zh-CN.md)，按评分指标
   预注册处理前保存点、必要状态、分支对照与恢复验收；元数据为`planned_not_captured`。
3. 下一阶段：状态生产器与save/restore接口、宿主/通信队列/技能进度、参与者可写状态、
   环境与随机流；先通过无处理sham一致性，再建设并行分支调度。
4. 再后：逐场景真实时钟与里程碑进展、删失/NA、分方向通信成本、六维统计。

`scoreable=false`、执行release gate均未提升。原生业务证据、资产验证、完整checkpoint、
并发和因果评分分别验收，不能用一项成功替代其他项。

### P1-L 经验学习难度审计（本阶段收尾）

已完成 L0-L3 的只读审计，详见
[LEARNING_DIFFICULTY_AUDIT.zh-CN.md](LEARNING_DIFFICULTY_AUDIT.zh-CN.md)。五场景的
任务族、包版本、公开/隐藏哈希、见证任务量和结构签名已登记；M0/M1 机制是同一任务族
的条件，不重复计作学习样本。City 的 TeamWeaver/TeamWeaver_LLM/EMOS trace 只作为
participant algorithm evidence，室内运行和 Restaurant 算法 batch 的缺口仍分别记为
infra-mixed 或 documentation-only。

当前只允许后续 pilot 以前的机会注册设计。正式 learning opportunity 必须按
base_instance、轮次、R/C 条件和指标冻结，并同时满足二元可行性 oracle、真实时钟、
checkpoint/restore、sham 和外部 Q(t)/终局证据；eligible、ceiling、unknown 的分母
不能根据被测系统正式结果改变。Restaurant 是结构上的 high 候选，但 A2 资产源闭包
仍失败；Factory、Warehouse、House、City 继续为 unknown。L-G1 前不修改生成器、
机制模板、冻结题包或 witness。

### P0 A3 参与者门禁接线增量

`goal_external_adapters.py` 中的 TeamWeaver、EMOS、City TeamWeaver 和 City EMOS
适配器已识别 v3 public API 的 `deployment_assessment` 动作：收到公开
`deployment_assessment_required` 后先提交二元判断，下一回合才进行自主选队、计划和
派发。v2 API 不含该动作时保留既有行为。动态评估只使用当前公开成员池及已送达的
能力声明，成员退出或能力变化后不会沿用过期 roster。适配器本身不读取隐藏 oracle；
正确性仍由 runner 的独立 oracle 评估。定向 TeamWeaver/EMOS/City 回归为 17 项通过。

## 最新回归与未通过项

部署门禁基线`ce65318`：本机完整benchmark测试 **2215 passed，235.44秒**；
ljy同提交完整测试 **2207 passed、8 skipped，87.93秒**。
两机日志均为`/tmp/eai-deployment-ce65318-full.log`。未采集8项跳过的具体原因，
不能将跳过算作通过。这些是轻量/假后端/参考世界测试；没有启动原生Isaac episode，
不提供五场景oracle原生认证。后续动态停止修复按增量单独记录。

阶段收尾增量已修复模型异常漏取消、保留具体后端取消错误，并为oracle异常缓存
failed/null。最新本机部署/session/host/runner/工具/评分/oracle专项
**265 passed，20.28秒**，日志`/tmp/eai-deployment-stage-close-focused.log`。
本次增量未重跑远端或全量；子代理工作已结束，按用户要求不展开下一阶段。

本轮新增 B/D 专项回归 **209 passed**（通信旧/新池、Goal session/adapter、编键
随机流、physics replay）；连续 AoI 另有通信专项 **105 passed**，但通信池仍未接入
真实三层 episode。餐厅准备命令对现有 v7 的结果为 `hash_resolver=passed`、
`source_closure_missing`，因此没有启动正式 episode。

本批 `14911e4`/`dcfcac0` 的本机完整 benchmark 回归为 **2233 passed，243.60 秒**；
ljy 隔离工作树通过 git bundle 同步到同一提交后，通信与评分侧车专项为
**124 passed，0.28 秒**。ljy 原仓和用户未提交文件未触碰。独立评分审查确认：
当前 sidecar 仍是 raw-only；真实物理/参与者恢复、三层 GoalSession 通信接线、
真实时钟和成本仍缺失，不能进入正式六维统计。

B3a 定向合同与现有剩余可行性 oracle 回归 **17 passed**，评分/部署/通信联合回归
**157 passed**。合同不会自行生成 `deployable`/`not_deployable`；只有已注册的剩余任务
求解器可以提供二元标签，五场景 native reader 尚未认证。

餐厅资产准备新增闭包物化定向测试，与 B3a/评分侧车合计 **34 passed**；尚未对正式
v7 目录执行物化，避免未经用户明确批准在 `/home/eai-simulator` 外创建源文件。正式
episode 仍需在本机和 ljy 各自用审查过的 `Restaurant_Only.usda` 执行该步骤并保存
source SHA256，然后才能进入启动前 preflight。

当前提交 `4d90543` 在本机完整 `source/EAI/test/test_benchmark_*.py` 回归为
**2244 passed，245.99 秒**；ljy 隔离工作树同提交的餐厅资产、B3a、评分侧车和通信
专项为 **139 passed，0.24 秒**。两项都没有启动 Isaac 原生 episode；发布标志继续为
false。

已将完整 `672f2dc` bundle 同步到 ljy 隔离工作树（未触碰 ljy 原仓未提交修改），
同一专项测试结果为 **134 passed，0.26秒**。远端专项包含通信旧/新池、评分侧车、
编键随机、physics replay 和餐厅准备测试；没有启动 Isaac 原生 episode。

以下保留较早基线的历史证据；远端资产缺口随后已补齐，不再是当前回归阻塞：

A1 双机全量复验（`672f2dc`）：本机 **2229 passed，234.35秒**；ljy
**2221 passed、8 skipped，87.12秒**。跳过原因未收集，不能宣称全量全部通过；本轮
没有原生 Isaac episode，发布标志不变。

本机评分/runner/工具专项249项通过；后续新增生命周期与餐厅测试87项通过。
最终本机完整`source/EAI/test/test_benchmark_*.py`：**2155 passed，230.41秒**；
日志`/tmp/eai-benchmark-final-1687a49.log`。
独立审核餐厅+external adapter25项通过；5项清理异常负向测试通过。
SSH ljy在最终代码`1687a49`上的专项：280 passed、1 skipped。

ljy隔离工作树位于 `/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e`。
其主仓未提交的 `goal_house_episode.py` 保留；通过Git bundle同步代码，未配置或复制凭据。
完整回归最初2041 passed/70 failed/19 errors/18 skipped；配置已有USD根目录和地图后
2106 passed/17 failed/7 errors/18 skipped；补齐运行时锚点和场景入口后，重跑失败集合
19 passed/5 failed。当时5项均有明确部署输入缺口：City车辆运行资产、Factory两份
LFS文件未展开、NVD产品验证禁止跨工作树symlink逃逸。已通过复制既有资产闭包、
展开匹配哈希的LFS对象及补地图解决，定向60项通过，随后通过上述完整回归。
保持路径/哈希校验，没有改测试掩盖；原仓用户修改未触碰。
远端日志：`/tmp/eai-scoring-19eee7e-tests.log`、`/tmp/eai-scoring-19eee7e-retest.log`、
`/tmp/eai-scoring-1687a49-focused.log`。

餐厅原生启动尝试未进入episode：

- v1使用冻结products-candidate-v7，但overlay引用`/home/eai-simulator/...`不存在，composition检查失败。
- v2用当前source路径重新生成runtime产品，生成/几何校验通过，但resolver拒绝新metadata哈希。
  不能把重建产品直接当作已冻结版本，也不能放宽resolver。
- 输出目录：`tmp/restaurant_benchmark/algo-episode-emos-integration-20260923-v1`与`v2`；
  重建目录：`tmp/restaurant_benchmark/products-integration-20260923`。

下一项环境工作应是版本化、可迁移的资产部署/manifest，不能靠替换题目或放宽执行规则
获得成功。原生相机RTX生命周期仍需独立运行；本轮未声称完成。

## 反思

原始证据是否存在、是否有完整恢复状态、是否具有评分语义是三个不同问题。
本轮审核发现“构造provider但未采样”和“首次清理失败被重试抹除”都能让表面结果
显得正常，已用负向测试修复。多机回归则证明只有代码提交不足以复现实验，资产版本、
运行路径和完整状态必须成为后续checkpoint/部署合同的一部分。
