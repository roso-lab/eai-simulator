# 阶段60：初始正向证书的动作域与共存域

日期：2026-09-25。输入 `39bbf46`；目标 GitLab `benchmark-task`。
本批承接 R1 的有限初始判断，不宣布 R1–R5 或405条件发布完成。

## 反思与范围

阶段57六次原生构造均完成5项技能与3项物理业务事实，但四次闲置M20
超出v1的微姿态容差。该失败不证明任务不可行，也不证明参与者决策失败。
继续调闲置控制器重复性与高层协作检测的关联不足，因此停止该方向投入。
v1保持原来的2/6不合格结论，不后验放宽、不覆盖旧档、不签发v1证书。

版本2将两台实际参与正向构造的Carter状态域与八机共存证据分开：

- Carter1/2仍使用原有限13D动作域及容差上限；原导航、观察、回执窗口不变。
- 全8机器人必须有同epoch有限根状态和真实初始区域测量。共存域严格来自
  维护的metric_regions_v2区域；地面按二维、空中按三维距离检查。
- 源epoch档案必须以实际字节核对固定SHA，绑定episode/任务/部署、240次预热、
  后预热根状态、保持原控制器，以及保持目标来自预热前实测位置/偏航。
- 校准期间复用已有逐tick八机测量，检查六台闲置机器人仍在维护的出生区域。
  不新增几何框架，不要求闲置机器人重复某次试验的厘米/四元数微状态。

隐藏正向构造仅用于证明存在完成路径，不进入题面，不是模型必须遵循的指派
或时序。模型仍自行拆题、选执行者和排时序；不强迫空中步骤来触发通信条件。
初始证书失败返回平台内部failed/null，不返回第三种正式答案或负向真值。

## 实现与复现入口

`goal_restaurant_initial_oracle.py`显式接受证书version2，version1兼容语义保留。
新增维护的共存域builder和真实epoch校验helper；v2每个允许的运行配置至少有
一次独立新进程校准，不将此存在性证据解释为噪声可靠性统计。

`goal_restaurant_oracle_qualification.py`提供离线`review`和`issue`命令：

```bash
python -m demo.benchmark.goal_restaurant_oracle_qualification review \
  --manifest /path/to/manifest.json --manifest-sha256 CANONICAL_PIN \
  --host local --seed 97 --output /new/path/review.json

python -m demo.benchmark.goal_restaurant_oracle_qualification issue \
  --manifest /path/to/manifest.json --manifest-sha256 CANONICAL_PIN \
  --review /path/local-review.json --review-sha256 LOCAL_REVIEW_PIN \
  --review /path/ljy-review.json --review-sha256 LJY_REVIEW_PIN \
  --output /new/path/issued.json
```

`review`在原始数据所属主机复算源文件/资产/实际运行参数、epoch及动作域、
全程共存、审计/通信/业务记录、严格五动作构造和两份已投递观察与回执的关联。
必须在固定tick及业务窗口内真正结束；输出失败不改变原始档案。
`issue`依赖外部独立固定的review摘要，不把JSON自带哈希当作自行认证。
两种命令均拒绝覆盖已有输出。原始运行档案、证书和临时包装脚本不提交Git。

## 校准事前承诺与当前状态

清单路径：`tmp/stage60-initial-oracle-calibration/v2/manifest.json`。
规范JSON SHA-256：`c52e43d55529e3185e9da666af12197d91151536697029fc5b9a9d74220e75d2`。

两槽为local seed97、ljy seed97；完整八机、CPU物理/GPU渲染、240预热、
4200最大tick、900秒宿主上限、无原生重试。源码/资产/实际主机配置/动作域
事前冻结。通信每池1000000字节/秒（8Mbit/s）、固定0.04秒是诊断配置，
不是现实链路C0。双地面策略合法不触发跨运动类型处理，不宣称处理效果已验收。

本机与ljy同10文件最终回归各206通过，无跳过；独审组合126项通过（有重叠）。
旧Stage57档只用于只读schema兼容检查，不作为v2资格。两槽原生均正常退出，
独立所属主机复算均通过：3609tick、3615条区域记录、72.18秒、12.74秒双导航
重叠、5/5技能、3项物理业务事实。两个主机182份执行依赖身份一致且无漂移。

| 项目 | 规范JSON SHA-256 |
| --- | --- |
| local seed97独立复算 | `f319c32e2143762577507f6211a48dac4f6ce7fd8002d8d426dfe7aafce3930e` |
| ljy seed97独立复算 | `ef8ffff39e0e559c9d07b2abb22783ec21afc31736e60cfc26b0ff165100d83a` |
| 签发v2证书 | `9f99a849a08c94a9d38614c0e7ae0cb043105b3b35727d79f1efeae07a995e86` |

新证书使用槽预注册为ljy seed101，未参加其证书的校准，正常退出并通过所属主机
离线复算。使用档复算规范摘要为
`afde5b79d1b8988b873b03ce4d8d00463688d9677771d0304301e1f5b0ba2940`。
从初态重建pristine会话后重新求值，与实际初始结果完全一致：verified/deployable。
审计调用1记录评测，首次参与者动作在调用2；维护的入口在初始化参与者前调用评测器。
结尾history/cache一致，重复对最终态查询仍返回缓存，不后验改写真值。
新episode仍5/5技能、3物理事实、72.18秒；审计/通信/业务独立复算通过。
评分侧车记录correct_deployment=1、可用初始真值风险集=1；这不是正式六轴成绩。
全部发布、执行、业务、scoreable状态继续false。

## 后续出口

已完成两槽校准→独立固定摘要签发→新episode初始判断与缓存验收链。
此证书严格绑定182份执行依赖、资产与两个实际运行配置；不自动适用于修改后的
执行栈，也不覆盖全部机器人组合、运行中变化或其他任务族。
下一R2优先设计实际声明边界的通用成本快照，同时间戳必须按实际调用顺序区分；
字节保留调度计费口径，未测物理进度/规划计算成本保持未知。只对旧档证明零成本
不足以完成测量能力，不单列为一个替代通用采集的开发阶段。
完整公开参数域、动态重评估、C0与处理对照、branch/sham及盲测发布仍单列未完成。
