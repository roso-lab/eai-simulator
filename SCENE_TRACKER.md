# 场景协作跟踪表

30 个场景的处理进度认领表。**处理任何场景之前，先在这里登记认领**，避免多人重复处理同一场景。

## 使用规则

1. **认领**：开始处理某场景前，先 `git pull --rebase` 拿到最新表格，在对应行的「工作者」列填上你的名字，并把要做的阶段从 ⬜ 改为 🚧，然后提交推送（提交信息建议：`tracker: claim <场景名>`）。
2. **一个场景同一时间只有一个工作者**。某行已有工作者时，先与对方协调，确认对方放弃后再接手并更新名字。
3. **更新进度**：阶段完成后把 🚧 改为 ✅。只改自己那一行，不要动别人的行，减少合并冲突。
4. 表格变更单独提交，不要和场景产物混在同一个提交里，方便他人 rebase。

## 图例

| 符号 | 含义   |
| ---- | ------ |
| ⬜   | 未开始 |
| 🚧   | 进行中 |
| ✅   | 已完成 |

各列符号与状态的对应：

- **打点(语义地图)**：🚧 = 在打点，✅ = 已完成语义地图
- **任务设计**：🚧 = 在设计任务，✅ = 已完成任务设计
- **优化**：🚧 = 在优化，✅ = 优化完成

## 进度表

| 分类                     | 场景              | 工作者 | 打点(语义地图) | 任务设计 | 优化 | 备注          |
| ------------------------ | ----------------- | ------ | :------------: | :------: | :--: | ------------- |
| Living and public space  | home_02           | LiO2   |       ✅       |    ✅    |  ⬜  |               |
| Living and public space  | house             | PUPIL  |       ✅       |    ✅    |  ⬜  |               |
| Living and public space  | office            |        |       ⬜       |    ⬜    |  ⬜  |               |
| Living and public space  | room_01           | PUPIL  |       ✅       |    ✅    |  ⬜  |               |
| Living and public space  | room_02           | PUPIL  |       ✅       |    🚧    |  ⬜  |               |
| Living and public space  | room_03           |        |       ⬜       |    ⬜    |  ⬜  |               |
| Living and public space  | room_04           |        |       ⬜       |    ⬜    |  ⬜  |               |
| Living and public space  | room_05           |        |       ⬜       |    ⬜    |  ⬜  |               |
| Living and public space  | room_06           | LiO2   |       ✅       |    ✅    |  ⬜  |               |
| Living and public space  | school            | LiO2   |       ⬜       |    ⬜    |  ⬜  |               |
| Production and logistics | bar               | LiO2   |       ✅       |    ✅    |  ⬜  |               |
| Production and logistics | candy_shop        | LiO2   |       ✅       |    ✅    |  ⬜  |               |
| Production and logistics | coffee_shop       | LiO2   |       ✅       |    🚧    |  ⬜  | 场景记得放ur5 |
| Production and logistics | consultation_room |        |       ⬜       |    ⬜    |  ⬜  |               |
| Production and logistics | factory           | PUPIL  |       ✅       |    ✅    |  ⬜  |               |
| Production and logistics | factory_02        |        |       ⬜       |    ⬜    |  ⬜  |               |
| Production and logistics | minimarket        |        |       ⬜       |    ⬜    |  ⬜  |               |
| Production and logistics | restaurant        |        |       ⬜       |    ⬜    |  ⬜  |               |
| Production and logistics | warehouse         | LiO2   |       ✅       |    ⬜    |  ⬜  |               |
| Production and logistics | warehouse_02      |        |       ⬜       |    ⬜    |  ⬜  |               |
| Service and operations   | city_01           | PUPIL  |       ✅       |    ✅    |  ⬜  |               |
| Service and operations   | city_02           | PUPIL  |       ✅       |    ✅    |  ⬜  |               |
| Service and operations   | forest            |        |       ⬜       |    ⬜    |  ⬜  |               |
| Service and operations   | hospital          |        |       ⬜       |    ⬜    |  ⬜  |               |
| Service and operations   | land              |        |       ⬜       |    ⬜    |  ⬜  |               |
| Service and operations   | restaurant        |        |       ⬜       |    ⬜    |  ⬜  |               |
| Service and operations   | ruined_city       | LiO2   |       🚧       |    ⬜    |  ⬜  |               |
| Service and operations   | stadium           |        |       ⬜       |    ⬜    |  ⬜  |               |
| Service and operations   | subway            | LiO2   |       ✅       |   ✅    |  ⬜  | 场景待优化    |
| Service and operations   | tram_station      |        |       ⬜       |    ⬜    |  ⬜  |               |

> 注：`restaurant` 在 Production and logistics 与 Service and operations 下各有一个，是两个不同场景，认领时注意区分。
