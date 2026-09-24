# Stage46 有限验收：终态几何证据

日期：2026-09-25；实现提交：0451ccd；基线：4882c6a。
实现边界见 [测量契约](STAGE46_CITY_BODY_MEASUREMENT.zh-CN.md)。

## 已完成与结论

本批增加 v2 终态私有读回和一次性当前身体网格采样，不改变准入、到达、安全或业务判定。
person02 的原 AABB 拒绝与当前可见身体证据出现明确分歧：

| person02 同一原生 tick 的量 | 最大 XY 圆心距 |
| --- | ---: |
| 原 world AABB 角点 | 0.8635302520m |
| 当前可见 mesh 顶点（161346 点） | 0.4316575136m |
| 从当前点导出的 actor-local 定向包络角点 | 0.5114363371m |
| 固定圆盘半径 | 0.75m |

因此，不能继续把这次失败描述成“已证实可见身体越界”或模型错误分配。
现有 world AABB 对该终态可见几何过于保守；本次没有单独证明全部差异只由 yaw 造成，
也没有认证碰撞体、连续运动安全、完整任务可行性。原 arrived=false 和业务3/4保留，
不把诊断 inside 字段代替成功谓词。

person03 的可见点/定向包络距为0.5389169881m/0.6041799280m，原 arrived=true。
person00/01 的新诊断为 unknown/current_animation_unavailable：当前 animation joint
order 未满足本 collector 的完整覆盖条件；原 arrived=true 不受影响。
unknown 是诊断证据状态，不是部署判断第三类答案；本批不继续扩建其资产动画支持。

## 验证事实

- 本机最终同37文件：754 passed / 42.64s，清单和日志在
  tmp/stage46-validation/test-files.txt、local-joint-final.log。
  较早751检查在 strict-current 修复之前，不能代替最终回归。
- 独立源码审查35 focused通过。审查关闭了区外人员prim丢失、非单位骨架变换测试缺口、
  AnimQuery存在但Default分量缺失仍回退rest pose三项问题。
- 一次本机有界native：宿主上限600s，实际289.50s，exit0、error=null、6成功技能回执。
  这只是本次诊断时长，不是跨主机性能或正式算法成绩。
- 参与者沿用Stage45按公开输入构造计划的确定性诊断策略，未读取作者见证；
  它不是外部模型baseline，也不满足“两种完整合法策略”或正式自主系统认证。
- 原生UUID：447a01e4-2c84-4a7c-ae97-e4a27e1988fb；
  Goal episode：stage46-city-body-measurement-v1。
- 四人采样native time均为136.03999999999579s；各自原始包络/采样身份、prim、source、
  footprint/hash和时刻一致。独立脚本从原始点重算range、角点及距离，未调用collector结论。
- 29归档文件hash、47会话审计调用、10评分边界、13通信消息/75账本事件、7原生/公开
  观测关联与完整business复算通过；新增网格/变换未进入参与者task/API/trace。
- 三项required业务通过：incident_assessed、secondary_traffic_protected、
  response_access_preserved；exposed_people_accounted_for失败；独立response_dispatch条件通过。
- ljy仍No route to host，本批远端新版本复验未执行。不得沿用Stage45双机732冒充本批双机。
- 三份受保护用户文件、冻结binding和semantic map的hash均保持；native源码前后hash一致。
- 独立原生/文档终审通过；另核对35个JSON中312次发布键全部严格false。
  团队交接stage46-city-body-measurement-20260925/handoffs/native-review.md，
  SHA-256：dd39e44bb3cbc8bc3f70878df87439779762c5c44157da64a2b7b65ea4903a31。

原生档：tmp/stage46-city-body-measurement-v1/；
复算脚本/结果：tmp/stage46-validation/check_body_archive.py、
native-body-replay.json、native-replay.json、native-progress-replay.json、
native-v2-joins.json、native-business-replay.json、summary.json。
运行档和采样点均为本机证据，不提交Git。

## 剩余出口

一次取证已回答当前可见身体与包络是否一致；停止增加mesh支持、碰撞精度或额外GPU诊断。
下一最小门回到R1/R2：审查固定圆盘“整身到达”的可信测量依据与版本兼容接线，
保留负载/容量/可达性和真正安全要求，避免由保守代理量额外限制合法计划。
在该契约确定后再做完整业务与city_initial二元oracle；不能用本次点集直接签发正标签。
R2继续使用同episode可信谓词/时间/成本事实；未冻结权重不临时出分。

R1–R5仍未完成；405条件/315对仍是条件展开，不是原生通过数。
oracle_status=failed、feasibility_label=null、platform_valid=false；
scoreable=false、execution_validated=false、business_outcomes_validated=false。
