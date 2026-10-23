# MicroDuck 材质说明（官方配色适配）

`microduck_mjcf.usd` 的外观按 Pollen Robotics 官方资料适配：

- **配色来源**：`microduck_rl` 仓库 Onshape CAD 导出的 MJCF
  （`src/mjlab_microduck/robot/microduck/robot_walk.xml` 的 38 个 `<material>`
  定义），即官方白橙配色方案的原始 CAD 颜色；官网
  (pollen-robotics.com/microduck) 另有灰紫 / 蓝橙 / 紫黄三种配色，
  本 USD 采用 CAD 自带的白橙款。
- **实现方式**：MJCF 导入器未把 geom 的材质引用落到 USD 绑定
  （113 个网格全部落在无色 DefaultMaterial 上）。适配脚本
  `tmp/rematerialize_final.py` 以导入器生成的 DefaultMaterial
  （MDL OmniPBR 结构，渲染器已验证）为模板逐部件克隆上色并重绑，
  左右壳的 CAD 色差（0.80 灰 vs 0.85 奶白）统一为奶白。

## 部件配色（官方 CAD 值）

| 部件 | 颜色 | RGB |
| --- | --- | --- |
| 头顶壳 / 躯干左右壳 / 大腿 | 奶白 | (0.851, 0.851, 0.839) |
| 上喙 / 头下壳 / 脚踝 / 脚 | 橙 | (1.000, 0.404, 0.122) |
| 眼睛（noenoeil）/ 脚底 | 黄 | (0.957, 0.686, 0.137) |
| 下喙软垫 / 上唇软垫 | 薰衣草紫 | (0.851, 0.757, 0.867) |
| 颈俯仰件 | 石板蓝灰 | (0.349, 0.376, 0.400) |
| 躯干底座 / 电机支架 / 偏航滚转件 | 深灰绿 | (0.129, 0.153, 0.129) |
| 镜头 / 镜头座 | 深灰 | (0.263, 0.282, 0.302) |
| XL330 电机 | 中灰 | (0.286, 0.286, 0.286) |
| 脸 / 髋 / 腿 / 颈 / 电池支架 | 暖灰米 | (0.675, 0.639, 0.604) |

## 渲染管线注意事项

- 该渲染器（Isaac Sim 5.1 RTX）只稳定渲染导入器原生的 MDL OmniPBR
  材质形态；手写标准 UsdPreviewSurface 或 `info:id=UsdPreviewSurface`
  混 MDL 的形态在无头管线中会渲染成黑/白，逐 mesh 的
  `primvars:displayColor` 也不会作为 fallback 生效。
- **不要去掉 visuals/collisions 的 instanceable 标记**：IsaacLab 的
  cloner 对去实例化后的引用树渲染异常（整树不显示）。
- 无头 Replicator 截图前至少执行一次 `render=True` 的 step 初始化
  渲染图，否则 annotator 数据永远为空。
