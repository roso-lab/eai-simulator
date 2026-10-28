# 场景登记三方对账（锚点目录 ↔ 场景库 ↔ 资产目录）

日期：2026-10-26 ｜ 裁决人：用户 ｜ 状态：待工程执行

## 一、裁决的映射（用户 2026-10-26 拍板）

| 旧键 | 去向 | 受影响锚点 |
| --- | --- | --- |
| `warehouse_nvd` | → `warehouse` | 6 个：prod-p1-02、prod-p2-02、prod-p4-02、prod-t-02（试点）、svc-p2-03、svc-t-03 |
| `modern_house_nvd` | → `house` | 4 个：liv-p2-01、liv-p3-01（试点，已绑 4 谓词）、liv-p4-02、liv-t-01 |
| `factory_benchmark` | → `factory` | 2 个：prod-p2-03、prod-t-01 |
| `city` | **旧资产已弃用，无对应**——8 个锚点待重新安置（见三） | 8 个：liv-p2-02、liv-p3-02、liv-p4-01（试点）、liv-t-02、svc-p1-01、svc-p2-01、svc-p3-02、svc-t-02 |

## 二、登记表修正（semantic_scenes_30.json）

1. 两个 `restaurant` 是**同名不同景**（生产版/服务版各一个真实场景）：登记键改为可区分（建议 `restaurant_prod` / `restaurant_svc`，或以分类路径为键），各自归属原分类；
2. **补登 `consultation_room`**（资产与任务卡俱在，漏登）→ 生产域第 10 个；
3. 修正后每域严格 10：生活 10 / 生产 10 / 服务 10 ✅（论文 "ten each" 在资产层成立）。

## 三、8 个旧 city 锚点的安置（草案，最终随 city_01/city_02 语义地图定）

原则：机制与谓词绑定**全部保留**（含试点 liv-p4-01 的 3 条已绑谓词），只改场景落点 + 按新场景几何微调任务前提。

| 锚点 | 机制 | 草案落点 | 依据 |
| --- | --- | --- | --- |
| svc-p2-01 维修工单交接 | P2 | **city_02** | city_02 卡："维修接续" |
| svc-t-02 维修案过单柜台 | P2+P4 | **city_02** | 同上 |
| svc-p1-01 两区巡逻 | P1 | **city_02** | "街道巡查" |
| svc-p3-02 公共区持续覆盖 | P3-S | **city_02** | "巡查/异常处置" |
| liv-t-02 两路段并行开通 | P1+P2 | **city_02** | 道路段语义 |
| liv-p4-01 共享窄通道泊位（试点） | P4 | **city_01** | 应急通行/瓶颈语义（待地图核几何） |
| liv-p3-02 无障碍通道覆盖 | P3-S | **city_01** | 应急疏散通行语义 |
| liv-p2-02 药品监管移交 | P2 | **hospital**（备选 city_02） | hospital 卡："病房物品准备与患者送诊"更贴合药品语义；一场景多锚点合法 |

city_01/city_02 若语义地图到位后几何不满足某锚点前提（如无窄通道），该锚点再议换景——本表标"草案"即为此。

## 四、restaurant 6 锚点的归属（小决定，默认方案）

liv-p2-03、liv-t-03、svc-p1-02、svc-p2-02（试点）、svc-p3-01、svc-t-01 共 6 个现挂通用键 `restaurant`——**默认全部落 `restaurant_svc`**（其任务卡"持续接待/供餐/补货"与配送/交接类任务更贴合；生产版"米其林接待"留给扩张新增锚点）。用户可否决。

## 五、执行清单（交工程会话）

1. task_catalog.json：12 个锚点改 scene_key（三组确定映射）；8 个 city 锚点 scene_key 暂置 `pending_reassignment`（或直接按第三节草案落点并标 draft）；
2. 登记表修正（第二节三条）；
3. 同步重新生成受影响合同 artifact 中的 scene 字段（纯改名，谓词/绑定不动）+ 回归；
4. 语义地图交付优先级更新：**warehouse、house、factory、city_02、city_01、restaurant_svc** 六个场景排最前（承载 26 个现有锚点）；
5. 扩张分配表 v2 按对平后重算（见 SCENE_ANCHOR_EXPANSION_PLAN 更新版）。

## 六、扩张账本（对平后）

- 生活：house 占 4 → 闲置 9（home_02、office、room_01–06、school）
- 生产：factory 占 6、warehouse 占 6 → 闲置 8（bar、candy_shop、coffee_shop、consultation_room、factory_02、minimarket、warehouse_02、restaurant_prod）
- 服务：restaurant_svc 占 6、city_01/02 占 5（草案）、hospital 占 1（草案）→ 闲置约 3–4（forest、land、stadium、subway、tram_station、ruined_city 中扣除 city 落点占用）
- 新增典型锚点 = 各域闲置场景数（约 9+8+3≈20 个），产出后 30 场景全承载、锚点总数≈50
