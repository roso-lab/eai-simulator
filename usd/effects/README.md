# EAI 体积效果资产

`usd/effects/` 存放火焰、烟雾等自包含的体积效果 USD 预设，供场景与
demo 在任意舞台位置引用。预设只包含 Flow schema prim（发射器、仿真、
渲染节点），不带贴图或外部引用依赖。

## 当前资产清单

全部 7 个文件来自 NVIDIA Omniverse 官方 Extensions Samples 内容包的
Flow 105.1 预设，逐字节保留原始发布内容（`manifest.json` 记录 sha256
与字节数）：

| 目录           | 文件                              | 说明                     |
| -------------- | --------------------------------- | ------------------------ |
| `fire/`        | `Fire.usda`、`WispyFire.usda`      | 火焰（demo/city 车火、楼火用 `Fire`） |
| `smoke/`       | `DenseSmoke.usda`、`DarkSmoke.usda` | 烟雾（demo/city 用 `DenseSmoke`） |
| `dust/`        | `Dust.usda`                       | 扬尘                     |
| `steam/`       | `Steam.usda`                      | 蒸汽                     |
| `pointcloud/`  | `PointCloud.usda`                 | 点云样式体积效果           |

## 引用方式

`demo/city/flow.py` 默认从本目录解析 `fire/Fire.usda` 与
`smoke/DenseSmoke.usda`（`FlowPairAdapter`）。解析顺序：先找本目录的
仓库布局 `fire/… smoke/…`，再兼容 `EAI_NVD_FLOW_ROOT` 指向的 NVD
原始下载目录布局（`105.1/presets/…` 等）。

预设本身的 stage 元数据是 `0.01` 米/单位、Y-up；`flow.py` 在宿主舞台按
米制/Z-up 逐属性覆写（`densityCellSize`、重力方向、attenuation 等），
因此不要改动这些 `.usda` 文件本身来适配单位——所有适配都在会话层完成，
测试会校验源文件校验和不变。

## 许可与分发

这批预设受 NVIDIA Omniverse License Agreement 约束（见内容包
`PACKAGE-LICENSES/Extensions_Samples_NVD-license.txt`），**不进入 git
跟踪，也不默认随仓库再分发**。本目录是本地运行时数据：

- 本机已从 NVD 内容包拷贝并校验，`manifest.json` 固定其完整性。
- 干净环境请自行下载 Extensions_Samples NVD 内容包，将 `Assets/
  Extensions/Samples/Flow` 拷贝或链接为本目录布局，或设置
  `EAI_NVD_FLOW_ROOT` 指向该 Flow 目录。
- 是否发布到 gated provider（`rosolab/eai-simulator-assets`）由
  maintainer 按上述许可确认后再定；发布时需在 provider 侧登记
  `manifest.json` 中的路径、哈希与许可出处。
