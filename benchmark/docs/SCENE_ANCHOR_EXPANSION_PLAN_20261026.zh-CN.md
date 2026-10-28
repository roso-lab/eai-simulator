# 场景锚点扩张分配表（26 个闲置场景 → 各一道典型锚点）

日期：2026-10-26 ｜ 依据：论文 0b006d4e "Thirty scene anchors (ten each)" + 用户裁决（扩张为必要项，加法路线不搬家）
原语分配：按任务卡内容启发式分配（P3-J=联合动作 / P3-S=持续服务 / P4=资源互斥 / P1=互补并行 / P2=先后交接），最终以底稿编写时的义务图分析为准。

| 场景 | 域 | 建议原语 | 任务卡（底稿素材） |
| --- | --- | --- | --- |
| bar | production | P2 | 酒吧订单服务、跨层取物与低电量接替 |
| candy_shop | production | P2 | 二楼库存向一楼柜台补货与订单配货 |
| city_01 | service | P2 | 突发车祸火灾城市应急响应 |
| city_02 | service | P3-S | CBD 街道巡查、异常处置与维修接续 |
| coffee_shop | production | P1 | 双入口突发来客与咖啡制作服务 |
| factory_02 | production | P2 | 厂区异常物体发现、复核与清除 |
| forest | service | P3-S | 山林火情发现与盘山路通行保障 |
| home_02 | living_public | P1 | 宠物饮水与喂食准备 |
| hospital | service | P1 | 病房物品准备与患者送诊 |
| house | living_public | P1 | 庭院用餐与阅读准备 |
| land | service | P3-J | 马拉松赛道秩序维护与队伍伴随保障 |
| minimarket | production | P3-S | 便利店巡检、水渍处置与垃圾清理 |
| office | living_public | P1 | 跨层会议准备与来访接待 |
| room_01 | living_public | P1 | 别墅多客房入住准备 |
| room_02 | living_public | P2 | 居家归整、空地查找与房间送物 |
| room_03 | living_public | P3-S | 家庭聚会窗口的餐厨侍应保障 |
| room_04 | living_public | P1 | 家庭活动用品与餐饮配送准备 |
| room_05 | living_public | P2 | 家庭来客接待与空间恢复 |
| room_06 | living_public | P3-J | 跨层图书接力与重物联合搬运 |
| ruined_city | service | P4 | 震后可步行人员搜索、清障与撤离引导 |
| school | living_public | P4 | 放学后校园清场与学生离校引导 |
| stadium | service | P3-S | 足球馆看台空地协同巡检与异常处置 |
| subway | service | P4 | 废弃地下车站空地协同救援与人员撤离引导 |
| tram_station | service | P4 | 城郊有轨电车站居民回家引导 |
| warehouse | production | P3-J | 仓库货物整理、清障与重物联合搬运 |
| warehouse_02 | production | P2 | 金色货物搜索、跨层交接与室外集中 |

共 26 个新典型锚点槽位。产出后锚点总数 30+26=56，30 场景全部承载至少一个锚点。
执行顺序：底稿（协作者，本表+委托书）→ 出题人拍板 → 工程接线 → 目录 v2 → 逐场景认证（随语义地图产线）。
