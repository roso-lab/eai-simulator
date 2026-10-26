# 联合持有 V3 接线

权威：出题人 2026-10-26 会话裁决，以及
`benchmark/docs/progress/PREDICATE_BINDING_20261023.zh-CN.md` 修订 V3。

## 实现

- 原语名 `contact_count` 保留兼容命名，实际观测类型改为 `HoldInterval`，输入字段为 `hold_intervals`；对象采用任务级 ID。
- 区间 `[start_tick, end_tick)` 每拍按不同 robot_id 计数，至少两名持有者，允许无缝替补。来源是 evaluator 采集的 held_object / 联合技能会话，不是物理接触或模型宣称。
- liv-p3-01、prod-t-01、svc-p3-01 和 liv-t-04 全部接入持有账本。liv-t-04 谓词改名 `heavy_carry_joint_hold`。
- 安全成功条件保持；单机和持有中断判违规，观测完整性未确认且数据不足时按 evaluator_record_missing fail-closed。重复同一机器人不增加持有者数。
- raw receipt adapter、receipt_material 及离线 replay 使用新流名。旧 contact_intervals 不自动转换，以免把接触证据混同为持有证据。旧已封存 artifact 保留，不能拿来作为 V3 重放通过证据。
- 新合同：`artifacts/catalog/contracts/room06-extension-20261026/pilot_contracts-v3.json`，31 锚点，保留原24底稿、room06底稿与V3记录摘要；所有认证标志仍 false。

## 任务物体绑定

新增 `heterocoord.task-object-bindings.v1`：objects 每项包含 entity_id、origin=task_generated、definition_ref、definition_digest；精确覆盖 scene.entity_ids，整份绑定纳入 manifest_digest。不要求 prim 或道具人工确认。

场景已有道具仍可用旧 `heterocoord.case-object-bindings.v1` 的显式确认与 prim 布置引用。对象存在由真实任务布置/运行收据证明，结构校验不等于已经生成实体。此补丁不虚构 room06 的任务布置结果，也不声称 skill 单机禁吊已实现；该设置保持用户侧后续工作。

## 验收与交接

ZCode 执行九文件专项回归。首次 284 passed、2 failed、4 skipped：两项是旧测试目录总数尚为123（现126），以及通用链式正例重复发出同名交接事件导致严格先后失败。已改为126，并让fixture复用链中同一事件时间，不改oracle先后规则。最终复验结果另记。

Goal 保持 active。机器人历史证据线继续独立推进；本修订不修改论文、不启动 GPU、不提升正式资格。

最终 ZCode 回归：**286 passed, 4 skipped in 0.68s，EXIT=0**。
4 个 skip 均为 `pair belongs to another anchor`，不是环境失败或新功能跳过。

```bash
python -m pytest -q -rs \
  tools/benchmark_branch/tests/test_joint_hold_v3.py \
  tools/benchmark_branch/tests/test_oracle_predicates.py \
  tools/benchmark_branch/tests/test_room06_oracle.py \
  tools/benchmark_branch/tests/test_pilot_contract.py \
  tools/benchmark_branch/tests/test_cp2_replay.py \
  tools/benchmark_branch/tests/test_room06_contract_generation.py \
  benchmark/tests/test_case_manifest_object_bindings_v21.py \
  benchmark/tests/test_receipt_occupant_binding.py \
  benchmark/tests/test_oracle_success_boundary.py
```

实际含新增25项四锚点持有语义测试、7项任务生成物体manifest正反/摘要测试。
旧封存合同不覆盖，V3重生成文件供后续case选用。尚未执行真实case，不将单元回归升级为论文实验或技能层禁单机验证。
