# 阶段51：餐厅预热后状态与原生观察

日期：2026-09-25；输入 `558aecb`；工作分支
`codex/benchmark-stage51-native-observations`。本批是R1执行基座增量，不是完整自主episode验收。

## 已实现行为

- `RestaurantFleetController.begin_episode(episode_id, warmup_ticks=...)` 显式启用一次性
  有界预热。沿用同一环境、八个控制器实例和原始hold目标；每tick向八机下发命令。
  预热后的真实物理步成为episode的0秒，不消耗参与者业务窗口。
- 旧epoch、预热命令/状态、完整八机根状态、任务身份及归档hash保留。
  旧的未调用begin接口用法不自动升级；已公开snapshot/step/route等后不能再重置epoch。
- `build_restaurant_native_initial_state` 输出全部八机实测根状态，仅给有维护型几何证据
  的Carter1标注初始staging区域。其余七机报告缺少出生点→初始区域绑定，属于平台缺口。
- `RestaurantNativeObservationProvider` 不推进物理。六台地面机器人可生产厨房/用餐区
  的真实支撑查询，沿用原0.25m区域要求和四射线支撑谓词，保留无命中、越区等false事实。
  进入readiness-v2通信前再次按独立物理校验器检查；失败测量不能变成业务成功。
- 八机局部向下射线和两架Iris的overview测量分别归档。后者绑定维护型3D中心与原0.30m
  到达容差；只表示区域位置/射线事实，不声称相机图像、覆盖视野或地面业务成立。
- 观察绑定完整任务/episode epoch、公共/原生机器人身份、有限根状态、物理步与时间；
  查询过程中若原生状态变化则拒绝该次观察。

## 原生事实与语义纠正

本机一次有界诊断使用通过A2源闭包检查的
`/home/pupil/a/b/c/d/products-candidate-v7`，CPU物理配置沿用阶段50诊断。
进程exit0，240个预热control tick/960个physics substep；episode起点为物理步962、相对0秒。
同一时刻读取22条记录：8条局部射线、12条地面区域观察、2条空中overview。
整批查询前后snapshot一致，随后八机共同推进1tick/0.02秒。
未导航到业务区，因此12条地面区域观察都为false；不能把这当成模型失败。

诊断揭示并修复一次口径误用：candidate admission的0.0001m出生坐标校验适用于
**预沉降初始化状态**，不适用于控制器预热后的动态根状态。最终适配器仅描述预热位移，
不新增位移准入门槛，不改变任何数值容差。真实的七处区域绑定缺口仍然保留。

原生运行后仅改了epoch输入校验和上述状态分类。旧请求、源hash、测量与报告保持不变；
最终代码进行了**只读CPU解释回放**：22条查询记录完全一致、八机根状态保持不变，
只纠正误报的出生点gap。原生运行版本与回放版本分别归档，不声称原生跑过最终代码。

本地证据：`tmp/stage51-validation/native-observation-1/`，包含request、A2 preflight、
episode-metadata、initial、observations、final、report、process和
`final-code-disk-replay.json`。完整预热档案路径/hash在metadata中。
运行输出和用户文件不提交Git。

## 验证范围

环境：`env_isaaclab`，`PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs`，
pytest禁用自动加载插件。

- `test_benchmark_goal_restaurant_fleet.py` + `test_benchmark_goal_restaurant_native_state.py`：39通过。
- `test_benchmark_goal_restaurant_native_observation.py`：44通过；含实际provider输出经共享
  通信链到完整独立业务校验器的合成fixture。不是原生业务证据。
- 全部16个Restaurant测试模块：311通过/26.77秒，包含上述测试，不相加为独立样本。
- 独立审查的Restaurant＋通信组合回归：409通过/27.34秒，与上述测试重叠。
- score-source-binding、factor-matrix、communication-capacity-v2三个模块：47通过/2.10秒。
- `py_compile`、`git diff --check`通过；三份受保护用户文件hash未变。
- `ssh ljy`仍为`No route to host`；没有本批远端复验。

独立全局审查接受本阶段有限出口，报告位于团队工作区
`stage51-restaurant-native-observations-20260925/handoffs/review.md`，SHA-256：
`b79b4ae62538185fb1047741cd12eab4e0a7598b28a163e8c79682b20f5e3f76`。

## 下一出口与停止投入边界

现有冻结题面把staging作为全地面成员参考，但八个分散出生点不等于一个共同几何区域；
Iris出生点也不等于overview。旧fleet还把staging目标映射到各自spawn，这同样需要统一。
下一步必须使用显式版本化的公共部署/区域/路线绑定，让真实初始位置和执行目标一致，
不改冻结任务正文、不缩候选池、不指定成员/子任务/时序。之后接完整自主入口与增量事件泵。

本批没有导航/接触业务成功、初始或剩余任务二元oracle认证、完整public observe域覆盖，
也没有可打分资格。M20导航0.50m与地面观察0.25m仍是不同条件；不调宽容差来凑成功。
不继续细化无关身体几何；发布R1–R5仍未完成。

`execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。
