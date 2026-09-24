# 阶段40验收：公开 City 遥测和真实执行时钟

代码提交 `c7c07e6`，承接 `6d1f6f4`。实现范围见
[公开运行观测与执行时钟](STAGE40_CITY_PUBLIC_RUNTIME.zh-CN.md)。

## 已验证

- 本机15文件联合回归：258 passed in 21.62s；ljy同提交同清单：258 passed in 8.52s。
- 独立审查检查公开字段白名单、消息消费归属、TTL/预算截断、真实服务重试窗口。
- 使用 git bundle 在 ljy 隔离树快进到代码提交，未改远端原仓源码。
- 使用公开输入局部诊断策略运行一次原生 City；PhysX cpu、原生渲染GPU，非完整模型基线。
- 28个归档文件的字节数/hash与侧车索引一致；重新执行会话audit、进展replay、通信replay，
  与归档结果一致。会话8个调用、进展2个样本、通信2条消息/10条事件。

测试环境：`env_isaaclab`，`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，
`PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy`。
命令为 `python -m pytest -q <tmp/stage40-validation/test-files.txt中的15个路径>`。
原生脚本为 `tmp/stage40-validation/native_city_smoke.py`，额外添加 `source/EAI/test` 到
PYTHONPATH；600秒有界执行。诊断使用显式unlimited上下行池，不声称已冻结现实C0。

## 原生结果及其边界

Goal episode `stage40-native-city-public-intake-v1`；原生episode
`6eac2333-5c4b-4438-b1bf-39eab95aec4a`；时钟epoch `10.019999999999875s`。
上行场景观测2127字节，下行派遣941字节，两条均实际投递并消费；策略用收到的人员ID
和公开集合候选生成自己的局部计划，未获作者计划或隐藏绑定。

此次运行 `blocked`、0技能回执。`affected_person_00 → assembly_candidate_001` 的
`guide_people` 请求在native `29.01999999999947s` 被
`service route, authority or capacity not admitted` 拒绝。异常进入 `result.error`，
不是已分类的业务失败回执。当前公开目标契约/原生接线的缺口必须先定位，不能把这次
失败纳入模型协作或分配指标；不通过泄露隐藏目的地、修改见证或放宽准入来凑成功。

这次验证支持公开intake、消费和派遣计量链运行，不支持完整自主计划、城市四项业务
通过或正式评分。默认部署oracle仍为证据缺口；二元真值未认证时不进入部署判断分母。
全部 `scoreable`、`execution_validated`、`business_outcomes_validated` 保持false。

## 证据与后续

本机归档：`tmp/stage40-validation/`，包含双机日志、原生脚本/日志、
`native-disk-replay.json`，以及 `stage40-city-public-intake-v1/` 原始目录。
源码/报告不因进程退出码0而宣称成功，以上述落盘结果为准。

下一P0：公开集合候选和native标识/几何/容量/可达性契约，服务拒绝的正确分类；
再完成两种合法自主策略与精确native初始/剩余可行性oracle。工程调查和全局流程审查
并行，主代理冻结接口后才安排源码写入；不重开405数量、固定子任务或精细抓取调参。
