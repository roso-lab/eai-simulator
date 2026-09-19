# 阶段12：配置与可信技能诊断交接（2026-09-18）

本阶段验证任务生成侧的物理前提：四景部署配置能否实际创建、值是否一致、固定点技能是否有
可复验的实际效果，以及没有证据的条件是否明确拒绝准入。下表中的“完成”只针对该证据范围。
完整业务执行、楼梯通行、避障和正式计分没有因此通过。

## 要求、证据与结论

| 要求 | 本地原始证据（仓库根目录下） | 结论与限制 |
| --- | --- | --- |
| 保留冻结题目参数、生成与审核题包 | `tmp/goal_suite_stage12_final/`；`goal_suite verify --verify-sources` | 27题包、21对因子对、1城市准备稿通过；held-out和scoreable为false |
| 四景部署配置可重建、成员独立 | `tmp/goal_deployments_stage12_close/`；四文件经`validate_goal_deployment`重建 | 配置一致性通过；不等于全部公开工具/能力已实际装配 |
| 四景真实创建、状态/质量/时钟/设备读回 | `tmp/goal_physical_stage12/{factory_v7,warehouse_v8,house_v5,city_v5}/`的request、manifest、report、process、review | 各240控制步/960物理子步；4/4/4/3个独立实例；Factory沿用CPU回退，其余CUDA；全由主线串行执行 |
| 物体质量、尺寸、固定点实际配置 | 同目录`object_review.json` | Warehouse的12kg木箱/3kg纸箱和House的14kg扶手椅/2kg书堆通过；Factory缺原生业务道具，City没有该类任务物体，均不产生空集通过 |
| House位置修订及反思 | house_v3/v4/v5原始报告与截图 | v3/v4默认根高使轮子初始穿入.25m台面；v4仅倾角阈值通过。v5按实测地板抬高根：M20 .77m、Carter .45m。最终M20轮中心约.340m减.09m半径与地板吻合，倾角约.006rad；不是接触力认证 |
| 场景语义与视觉 | 各目录原始PNG，ZCode逐图结果另保存在团队交接目录 | House任务物体/Iris视图为客厅；M20_2近景在门外台面，初始区域一致性仍待审核。模型外观不能认证物体身份、质量或净空 |
| 原生固定点操作接线 | `tmp/goal_attachment_stage12/warehouse_v4/{request,attachment_report,review}.json` | 440步；负载/活跃成员/距离三类非法操作拒绝，合法连接后短程跟随、释放后分离有原始位姿。仅单3kg物体理想连接，非抬升/双机载荷分配/整题完成 |
| 空间读回和占用准入 | 四景report中的`spatial`、`current_occupancy` | 有实际离散PhysX支撑/净空射线；完整占用因Iris凸分解和Carter Xform碰撞几何未支持而blocked，未放行平移查询 |
| 楼梯开放 | Factory公开`main_stair`及场景、楼梯截图/几何研究 | 题面保持开放；入口可见首段无封堵。平台黄色RampShort不构成中心线碰撞阻挡；首级约.210m与公开.18m有差异。实际cooking、全程净空和上下楼仍待验证 |
| 城市可信业务接口 | `demo/city_benchmark/episode_adapter.py`、authoring_17及city_v5 | 已交付host接口，主线诊断people=0、flow关闭；无人员救援/交通/业务回执运行证据，禁止将ack当成功 |
| 独立审查 | 团队`handoffs/review.md` | 源版本绑定、对象来源、时间、joint所有权/拒绝、未加载子树和点实例误排除的已报反例均修复；物理门禁保持分项 |

独立审查按原生碰撞形状和实时body位姿计算了House_v5最终轮底：各轮相对.250m地板的
最低差约为微米量级，已排除v4粗大的板下落位。但M20名义出生姿态仍含约6.6mm初始重叠，
随后向上静置；不得称为“无初始穿透”。其零命令平面漂移约.28m，也不是导航精度证明。
这些限制保留为下一轮无穿透初始化与接触验证的输入。

每次源文件变动后新建运行目录；旧报告及失败图不回填、不重签。本地指纹证明这套受信任
工作流的一致性，不是远程防伪认证。截图只属于作者审核，不是被测agent已经获得的观察。

## 实际检查

Python、测试和Isaac使用`env_isaaclab`，没有启动ROS。执行入口与复验命令见
[物理诊断工作流](GOAL_PHYSICAL.zh-CN.md)。核心命令为：

```bash
python -m demo.benchmark.goal_physical_review <scene-run-dir>
python -m demo.benchmark.goal_object_readback_review <scene-run-dir>
python -m demo.benchmark.goal_attachment_review tmp/goal_attachment_stage12/warehouse_v4
python -m demo.benchmark.goal_suite verify --output-root tmp/goal_suite_stage12_final --verify-sources
python -m demo.benchmark.goal_deployment verify <deployment-file>
```

此前9个相关测试文件280项通过；House最终高度修订后，indoor_deployment、deployment和
deployment_conformance三文件55项通过。两者有重叠，不累加。原生运行与像素审核分别记录，
不以单元测试替代物理，也不以进程返回0替代完整报告与期望步数。

## 下一阶段的具体交接

- 占用后端：本机PhysX提供rigid-body property/collider AABB和cooking convex接口。先在
  独立受控诊断中标定嵌套变换、非均匀缩放、同path多shape、完成/超时和同一物理步身份；
  原始回调完整后再接当前包络，禁止用profile估计消除现有blocker。
- 室内场景：审定Factory异常记录/服务车的真实资产；Warehouse宽路线和联合携物净空；
  House门内外出生区域、道具暂存位置及连续住户路径。地图/物理/语义分别验收。
- 楼梯：冻结参数差异应先由作者解决，再采全梯段实际碰撞/支撑与上下楼轨迹；不得关楼梯规避异构能力题。
- 可靠技能：接入联合载荷、双机持有与释放、退出后的物体状态及取消流程。理想固定点规则保留，
  不增设IK、VLA或自主抓取算法考核。
- 城市：由侧边维护者的业务host接入本轮真实观察、人车状态、连续角色区间和服务证据，再给出可审核的treatment。

逐项进度以[TODO](TODO.zh-CN.md)为准；这些后续门禁不因本阶段诊断交接而自动勾选。
