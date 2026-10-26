# room_06 / liv-t-04 接线与交接（进行中）

## 来源与合并边界

已 fetch origin/HeteroCoord，远端为 `7f44a1872fd289e1fe7532adeecc971a5db60d12`。
主树 HEAD 为 `7d1ba281`，fast-forward 被 Git 拒绝：room_06 README/project
以及未跟踪 current 产品会被覆盖。所有脏改动保留，未 stash、reset、clean、commit、push。
远端完整基线已放到独立工作树
`/home/pupil/eai-simulator-worktrees/room06-livt04-integration`，分支
`codex/room06-livt04-integration`。这不是主树已合并的声明。

地图采用上述独立工作树的已验收版本；主树本地 save_id 为
`d471be5423da493e8c19d7b4a60d85c3`，不冒充远端
`d9872563938c42fd85f43efa3dab3c0b`。

## Stage 1

Sol subagent `/root/room06_audit_repair` 完成 auditor 与专属测试实现。
三段 save_id 链必须一致；四个空语义集合不再默认失败，以明确 anchor requirement
匹配；语义点非空唯一命名、导航绑定解析到节点；不读取 robot_validated 或
isaac_binding 准入。主线程把 semantic_requirements 参数转交接入 builder。
交接：agent-team/workspaces/room06-livt04-20261026/handoffs/room06-case-input-audit-v21.md。

## Stage 2

catalog 已增加 liv-t-04：15000 ticks、P2+P3-J、无互斥资源；oracle 三条谓词
使用严格拾取→拓扑过渡节点移交→上架先后、双侧承托窗口内 >=2 接触安全后备、
accepted <=15000。物体符号 heavy_object 待 case 绑定，未自动映射 USD。
单机不能起吊仍为用户后续 skill 配置，不冒称已有物理能力约束。

12 个确定场景键改名；8 个 city 按对账文档暂置 pending_reassignment；
6 个 restaurant 锚点用 restaurant_svc。原六试点谓词语义不变。
catalog 校验保留原 30 个基线锚点的每域 10 个约束，liv-t-04 单列扩展，不误称 31 个场景。
底稿未给八维难度数值，新增条目的零值明确标记 pending_not_calibrated_zero_placeholders，
不作为冻结难度或认证证据；仍需部署校准。

ZCode 首次定向测试 11 passed/EXIT=0。合同生成 EXIT=0，写入新目录
`artifacts/catalog/contracts/room06-extension-20261026/pilot_contracts.json`，
旧 artifact 未覆盖。包含 25 个非试点锚点、107 条谓词。
审计器/合同联合回归：33 passed in 0.38s，EXIT=0。远端地图审计 EXIT=0、
case_ready=true、failures=[]；12 点、50 拓扑节点/63 边，三段 save_id 一致，
四层计数均为零但本锚点需求满足。默认 catalog 加载得到 31 条，EXIT=0。
这里 case_ready 仅指 map 单项审计，未含机器人池，不是完整 case 已就绪。

## Stage 3 尚未完成

还需：从 USD 提取候选物体 prim 供用户确认；在 case 绑定书保留物体身份及摘要；
builder 取消实体必须来自地图 entities 的旧假设，并允许本锚点空 zone_ids；
机器人池按真实运行时收据放行，不伪造资格。主树合并、旧试点改名后的新版本合同、
场景登记表全量对账及广域回归尚未验收。

格式修正不能被旧 manifest builder 再次拦截，这是下一阶段首要接缝。
新地图不是缺陷证据；不建立地图机器人验证探针。所有正式资格门保持 false。

## USD 只读检查

ZCode 用 env_isaaclab Python 的 pxr.Usd.Stage.Open 读取 room_06.usda，EXIT=0，
未启动 Kit 或 GPU。默认 prim 为 /World；模型根为
`/World/Sketchfab_model/_24a01978013d4a388c8d24fef7f19e17_fbx/RootNode`。
其直接子节点为 Cube_*/node_0_*/Plane_* 等通用建模名，不能仅凭名称认定图书或咖啡桌。
例如已读出的真实路径包括该模型根下 `/Cube_019`、`/node_0_004`、`/Cube_015`；
这里只证明路径存在，未宣称它们对应待搬运物体。下一步需结合语义点坐标与物体包围盒
列出可辨识候选，再提交用户确认；当前不填造 object prim 绑定。

审计器在首轮33项通过后增加两项边界测试，最终源码冻结为
`d2b28eae8bc88ad6b609b36f57ada033cb1878250c909d552daaf1dd9ed6de37`，
旧33项不得代替最终版本复验。随后 ZCode 对冻结版本重跑同三文件命令，
结果 **35 passed in 0.36s，EXIT=0**，本阶段最终专项验收以35项为准。
