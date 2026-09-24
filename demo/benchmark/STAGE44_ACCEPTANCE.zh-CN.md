# Stage44有限验收：独立集合区执行一致性

日期2026-09-25，代码提交 `bbcb3d03eac280ee83454cc7705a226c1bfe6cbd`。
实现已正常推送GitLab `benchmark-task`，并用ls-remote核对。
契约见 [显式圆盘执行接口](STAGE44_CITY_FOOTPRINT_EXECUTION.zh-CN.md)。
本批统一v2公开测量、准入/容量、控制器和完整身体到达，不改变冻结任务或旧参与者接口。

## 源码与双机证据

同一份35个tracked测试文件清单位于 `tmp/stage44-validation/test-files.txt`：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
<env_isaaclab>/bin/python -m pytest -q $(cat tmp/stage44-validation/test-files.txt)
```

本机 **730 passed / 40.86s**，ljy隔离树 **730 passed / 15.91s**；
py_compile、diff-check与独立代码终审通过。两机都使用最终代码bbcb3d0。
ljy经bundle同步，原工作区及三份用户保护文件保持不变。

终审修正了同XY高空无人机被当作无限高障碍的问题：保留私有XY重叠清单，
碰撞结论使用真实3D相交；地面重叠仍阻挡。全16组合测量不预约、同一人员容量去重、
路径完成及完整身体到达、未知支撑和身份/时钟/profile/隐私检查均有对应回归。

额外本机10项ignored的 `demo/city_benchmark/scene/tests/test_service_runtime.py`
不属于tracked清单，也不计入双机730。首次远端清单误含此本机文件，因路径不存在在
收集前退出；保留 `ljy-invalid-local-only-list.log`，纠正清单后完整35文件通过，未跳过测试。

## 单项原生诊断

归档位于 `tmp/stage44-validation/stage44-city-footprint-single-v1/`，native episode为
`3c56ff38-da86-4dfc-912c-b3bdcc1d40f4`。
ljy使用显式v2、公开输入确定性策略，以Carter执行person02→candidate004；
该选择与作者绑定的candidate003不同。原生命令为：

```bash
timeout --signal=TERM --kill-after=30s 600s <env_isaaclab>/bin/python \
  tmp/stage44-validation/native_city_footprint.py \
  --run stage44-city-footprint-single-v1 --scope single --rank 1 --member-offset 0
```

`result.error=null`，1条succeeded回执；实际arrived和safe_route_and_assembly均为true。
原生到达时间21.86s，相对episode为11.84s；后续服务review的timed_out与实际安全到达
分别保留，不能混为相反结论。它不是完整业务或被测算法baseline；仅incident_assessed通过。

初始16组合全部可测：6 admissible、10 blocked、0 unavailable；当前提案图matching
cardinality为4，quality_issues为空。matching不证明完整任务可部署，更不能从一个
blocked提案推断不可部署。oracle仍failed/null，platform_valid=false。

29项归档字节/hash、17次会话调用及进展/通信replay通过；磁盘评分源schema v2的
5个runner边界重算一致，v2几何/profile/身份/隐私/原始支撑连接检查通过。
核查结果为 `native-single-replay.json`、`native-single-progress-replay.json`、
`native-single-v2-joins.json`。replay范围是内部一致性，不是物理认证。

## 完整业务诊断：宿主超时，未取得终态

ljy运行 `stage44-city-footprint-full-v1`，native episode为
`2d2b2f06-adf7-4bbc-861a-0d35481aad9a`。
策略只读取public task/API及实际投递事件，生成6任务、88s名义执行窗口：
先控制6辆跟随车，按公开可测路线选4人匹配，再报告。顺序是诊断策略自己的选择，
不成为题面规则或标准解；隐藏绑定只在可信宿主中使用。
600s宿主超时触发，30s终止宽限后进程退出137；已核对原进程全部消失。
保留11份初始/准备/来源文件和日志，但没有result、skill_receipts或终态评分档。
因此本次不能进行终态replay、不能推断业务成功/失败，更不能归因被测模型。
宿主结果记录为 `tmp/stage44-validation/native-full-v1-host-outcome.json`，日志为
`native-full-v1.log`。该宿主上限不是180s模拟业务预算；没有放宽后者。

下一最小诊断切片增加策略动作/回执进度和有界周期调用栈，定位耗时后再判断必要的
性能修复或宿主测试预算。不得因没有终态就增加物理约束、扩大集合区或改业务评分。

## 发布边界

持续Goal与R1–R5总出口保持未完成。405条件/315对是条件展开数量，不能作为原生
成功数或独立统计样本数。完整业务、可信初始二元oracle、外部算法、适用条件的并发/
动态变化、评分时序生产、restore/sham及正式认证分别验收，不用组件数量替代。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`
全部保持；本批不翻转任何发布标志。
