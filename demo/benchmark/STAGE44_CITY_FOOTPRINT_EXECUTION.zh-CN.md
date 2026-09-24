# Stage44：显式集合区圆盘执行接口

承接阶段43独立几何和支撑采样契约，新增显式 opt-in
`requested_target_v2_support_footprint`。它统一公开测量、原生准入、预约、
控制器目标和到达读回；不是完整业务、二元 oracle 或正式发布的认证。

```python
run_city_autonomous_episode(
    output_root, adapter,
    guide_semantic_map_path=semantic_map_path,
    guide_execution_profile="requested_target_v2_support_footprint",
    communication_runtime=runtime,
)
```

`runtime` 须声明既有共享上/下行池。未指定新 profile 时，有 route provider
仍使用 `requested_target_v1`，无 provider 则保留 legacy bound-target 路径。
显式 v2 缺少所需可信输入时拒绝启动，不静默退回旧执行规则。参与者仍可忽略
可选测量、作盲选；题面、任务分解、成员分配和时序由参与者自行决定。

## 同一几何贯穿执行

- 按冻结 semantic map/public candidate ID 连接四个圆盘，半径0.75m、容量1、
  支撑参考平面0.3m；不使用作者人员绑定或身体尺寸扩大目标。
- 同tick的九点/圆盘原生支撑采样分为 sampled_supported、unsupported、unknown。
  同一tick共享一次采样，新tick必须重新测量。unknown优先归平台缺证；真实缺支撑
  保留执行阻挡，二者都不产生不可部署真值。采样不证明整个圆盘连续支撑。
- 全部16个人员—候选记录只读生成，不占预约。只有选定请求获准才预约；
  occupant、pending、active按同一人员身份去重。启动/失败/停止/reset清理相应状态。
- 目标root、路线终点及控制器使用同一圆盘中心/参考平面；人员到达须路径完成且
  实际完整XY身体包络进入圆盘。安全还依赖真实三维路径/目标碰撞、容量与当前支撑。
  不能用root进入代替全身进入，不能给集合区增加虚构天花板。
- 非roster动态物体的XY重叠只保留为检查清单；实际三维相交才构成碰撞。
  空中无人机不因投影重合而阻挡地面人员，地面真实重叠仍阻挡。
- hazard与连续通道的旧AABB语义保持原样。到达轮询在路径尚未结束时跳过昂贵的
  最终读回，逐帧三维轨迹监测继续执行，不改变首次满足到达条件的时刻。

## 信息与归档

native affordance/public projection使用准确的version 2/profile组合。公开字段是
固定几何、容量/占用数量、支撑状态/证据引用以及全部组合的路线摘要；不公开作者
指派、私有路线、碰撞路径、身体数据或occupant/reservation身份列表。相同人员的
占用与预约不会被算成两个名额；公开投影先用私有身份核对，再只输出数量。

服务、Goal入口、intake profile、源档案和初始资格必须报告同一活动profile。
初始资格校验来源、时钟、几何和支撑连接，仍为evaluator-only的failed/null oracle。
未知支撑保留完整组合记录及平台缺口；不把完整matching或一次成功当作全任务证明。

`assembly_support_measurement`在每份v2原生affordance顶层保留一次原始采样，进入
observer的native archive，不进入公开projection。采样器自身的
`execution_profile_enabled=false`只描述采样器不激活执行，不能拿它覆盖运行时profile。
评分源schema v2与目标执行v2是两个不同版本域。

## 验证边界

新增覆盖旧接口兼容、错误profile/来源/时钟、只读16组合、作者绑定置换、容量去重、
停止/reset/失败、完整身体到达、高空不相交与真实碰撞、公开隐私及初始证据连接的测试。
本阶段的最终双机/原生结果另记录于阶段验收文档；本契约不冒充运行结果。

冻结任务、见证、reach、地图、资产哈希、容量和业务时限未修改。
`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`
全程保持。完整City业务、initial oracle、R3处理实验、R4真实restore/sham和R5发布认证
仍需各自独立证据。
