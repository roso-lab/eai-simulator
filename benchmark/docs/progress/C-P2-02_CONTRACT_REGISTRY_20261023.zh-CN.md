# C-P2-02 合同版本登记（V2）

登记日期：2026-10-23  
依据：`PREDICATE_BINDING_20261023.zh-CN.md` 修订 V2，R1–R8 全采纳。

## 登记值

- 合同版本：`C-P2-02-quarantine-marked-v2`
- 源目录：`benchmark/task_catalog.json`
- 源目录 SHA-256：`56afd27fb710de68a0e8f16751afc74365032e757007ae6b87dbd5c60623b28f`
- 共同锚点组：`C-P2-02`
- 同步成员：`prod-p2-02`、`svc-p2-02`、`liv-p2-02`
- 新增里程碑：`quarantine_marked`，位置为 `inspection_complete` 与 `released` 之间。

三件套均已在任务目录登记同一里程碑；旧版本证书、部署见证和难度分量不得沿用。

## 必须重跑的见证链

1. 用新目录版本重建三件套合同与哈希，确认三者的共同锚点/里程碑序列一致。
2. 对三件套分别重跑 deployment-oracle witness replay，并将新合同版本与 witness digest 绑定。
3. 重跑技能验证门；任何旧技能门回执不能迁移到 V2。
4. 用新合同输入重算难度分量与 D5 案例索引，保留旧结果作为不可复用历史证据。

本登记不表示物理认证完成；在上述回放、技能门和难度重算回执齐备前，`certification_status` 仍保持 planned，formal gates 仍为 false。

## 2026-10-24 执行回放记录

- 六候选合同已由 `pilot_contract.py` 重新生成；19 条绑定谓词均为 `bound_pending_replay`。
- 合同与 replay receipt 均记录 oracle 来源提交：`cf839ec8`（分支 A）及 V2 follow-up `0b6890c0`。
- C-P2-02 离线 oracle witness 已从空流占位升级为可执行输入：
  - witness：`artifacts/catalog/replays/C-P2-02-quarantine-marked-v2/oracle_witness.json`
  - witness SHA-256：`8017a29969859ea1a2b8d0a2983811eadd20fc6d80c709095593f149d5fa9888`
  - replay digest：`7c2f9ccf9ce373d0817e4cc1ed96223f3331ff7cd2886987789bc5df9f9836e9`
  - `prod-p2-02`、`svc-p2-02`、`liv-p2-02` 均执行共同 oracle，3/3 谓词 `satisfied`。
- 生成 ledger：`artifacts/catalog/replays/C-P2-02-quarantine-marked-v2/replay_ledger.json`；`oracle_witness_replayed=true`，但 `deployment_oracle_replayed=false`、技能门和 formal gates 仍为 false，因为真实部署/技能回执尚未提供。
