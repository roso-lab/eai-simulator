# 资产导入工具

[English](README.md)

所有命令从仓库根目录运行。工具把 resolver 管理的运行资产安装到 `usd/`，生成的生产资产保持 Git 忽略状态。

## Benchmark 场景依赖

`audit_usd_closure.py` 不启动 Isaac，递归检查 USD、MDL 和 UDIM 纹理依赖；
`relocate_usd_closure.py` 仅迁移引用闭包，保留源资源，通过 OpenUSD API 重写路径，
记录文件哈希和修复映射。`benchmark_asset_repairs.json` 保存已定位的源包路径修复。
三个 demo 的资源目录、迁移命令、米/Z-up 外层准备和整包迁移说明见
[公共 demo 文档](../../demo/benchmark/README.md)。

闭包审计也跟踪 `customLayerData.metricsAssembler.*.write_layer` 字符串。
`metrics:...metricsAssembler` 是 Kit 动态层，不是可下载文件，也不是自动放行的运行模块。
如需物化，应先在真实 simulator 会话中捕获其实际内容，再将捕获 USD 作为显式迁移 alias。
迁移工具同时更新 sublayer 与关联 metadata；迁移前后校验单位、变换、几何和物理内容，
保留源备份及捕获报告。

2026-09-16 的本地 Factory 配置修复将 Iris 材质放入 `usd/robot/pegasus/materials/`，
MuSHR 捕获的单位层放入 `usd/robot/mushr_v2/layers/units_adjust.usda`。
它们是 Git 忽略的生产资产，分发本地包时需携带整个已审核闭包；捕获、下载、迁移报告和
原始文件备份位于 `tmp/factory_benchmark/`。本地修复没有更新远端发布资产 revision。
Pegasus 优化资产还恢复了源资产中无碰撞的天线视觉网格，修复简化后失效的材质子集索引。
源哈希、世界变换与网格外内容一致性校验、原始优化资产备份保存在
`tmp/factory_benchmark/pegasus_visual_repair/`。

## Restaurant benchmark 产物

`build_restaurant_products.py` 从同一组已组合源三角形生成米制 Restaurant
wrapper、三态 ROS 占据图和静态碰撞代理。工具要求入口 USD 与
`demo.restaurant_benchmark.config` 中冻结的有效闭包策略一致，只写入显式指定的
输出目录，并将 bundle 标记为 `local_candidate`。检查器会绑定当前生成器和源文件
哈希，并要求 PNG 占据格与 USD 代理清单逐格完全一致。

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m tools.assets.build_restaurant_products \
  --source-usd "$EAI_USD_ROOT/scene/restaurant/Restaurant_Only.usda" \
  --output-dir tmp/restaurant_benchmark/products-candidate
```

生成产物仍是 `tmp/` 下的运行证据；原生物理、导航和业务 episode 验证通过前，
不得提交或发布。

## NVD benchmark 产品重建

`rebuild_nvd_products.py` 从原生实例包围盒和网格几何重新生成地面占据图、无人机体素和
碰撞代理，使用 standalone OpenUSD、NumPy、PyYAML、Pillow，不启动 Isaac。
先运行默认只读报告，再用 `--apply` 写入；写入会覆盖 PNG、NPZ、bench USD 和
`bench_meta.json`，运行前应保存资产备份。

```bash
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --apply
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-ground
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-provenance
python -m tools.assets.rebuild_nvd_products --scene modern_house_nvd --check-ground
python -m tools.assets.rebuild_nvd_products --scene modern_house_nvd --check-provenance
```

写入时总会将 `/World/BenchCollision/zz_ground` 重建为带 `PhysicsCollisionAPI` 的
静态 Z 轴 `Plane`，位于米/Z-up 包装层的 z=0，复现旧薄 Cube 地板轮胎接触不稳定的修复。
其他 `zz_*` 平台和夹层地图保留。墙代理精确覆盖占据格，重建前也清理编号超过 999 的旧墙。
Modern House 的树木点实例仅以树干/树皮几何生成地面频段代理，草花和高处分枝不生成
隐形障碍；墙体高度按源几何以 0.05 米量化，最高 0.8 米。沙发/扶手椅网格直接赋予
`PhysicsCollisionAPI` 和凸分解碰撞，并扩展到已审查的桌子、灯具、柱灯和展示自行车；
结构网格使用 Cube 代理，玻璃、叶片和小型装饰被忽略。每类数量和原因会写入
`bench_meta.json`。原生碰撞网格从体素 Cube 生成中排除，避免重复及过大的碰撞。
正高度和地下体素代理使用地图原始 XY 分辨率，不再把占据格扩大五倍。
`--check-ground` 不加载 payload、不重建产品，仅检查地板类型、碰撞启用、静态祖先与
世界变换；这属于 USD 定义检查，不能代替真实导航/接触验收。生成资产仍受 Git 忽略，
需整组发布到不可变 provider revision，本地重建不等于远端发布。

每次写入还会在 `bench_meta.json` 生成
`eai.nvd-products.provenance.v1` 记录。`--check-provenance` 是只读门禁，将
provider 的完整 USD/MDL/纹理引用闭包、bench USD、PNG、YAML、NPZ 的字节哈希与
生成器版本、栅格化参数、地图/体素共享坐标系绑定；sidecar 保存闭包聚合摘要，而不是
数百条文件记录。该记录固定声明
`publication_status=local_candidate` 且 provider revision 为空；正式发布仍需从干净
checkout 校验不可变 provider revision 并生成对应发布清单，本地记录不能作为发布证据。
Warehouse 与 Modern House 的 `--check-assets` 会在 demo 实际将要加载的 resolver USD
根目录上执行同一门禁，避免误验仓库中的另一份副本。

重建同时会把 `/World/BenchCollision` 本身正规化为可渲染的 `Xform` 并写入
`visibility=invisible`。代理层只承担物理：传感器相机和视口必须看到真实 NVD 道具，
而不是碰撞方块。旧的无类型组 prim 上的可见性属性会被 RTX 静默忽略、也不会被
子级继承，这正是此前 `--check-assets` 通过、灰色方块却仍然渲染出来的原因；
因此 `demo/benchmark/application.py` 现在把「代理根为 `Xform` 且已写
`invisible`」作为门禁，而 PhysX 碰撞在任何情况下保持启用。

## RMUC 2024

导入器会校验认证用 Gazebo STL/PGM 哈希，结构化解析 SDF 和地图 YAML，调用 Isaac Lab mesh converter，并安装包装 USD、三角网格碰撞资产、占用地图、来源元数据、许可证和 Env DIY 缩略图。

```bash
conda activate env_isaaclab
python tools/assets/import_rmuc2024.py --source-repo /home/pupil/gazebo-sim
python tools/assets/import_rmuc2024.py --source-repo /home/pupil/gazebo-sim --check
```

转换会无界面启动 Isaac Sim，可能需要数分钟。`--check` 只读且不会启动 Isaac Sim。其他源目录、仓库目录和 Isaac Lab 目录参数见 `python tools/assets/import_rmuc2024.py --help`。

安装后的 provider 路径为：

```text
usd/scene/rmuc2024/rmuc2024.usda
usd/scene/rmuc2024/rmuc2024_mesh.usd
usd/scene/rmuc2024/rmuc2024_map.yaml
usd/scene/rmuc2024/rmuc2024_map.png
usd/scene/rmuc2024/source.json
usd/scene/rmuc2024/LICENSE.Apache-2.0.txt
```

正式发布前必须把这些运行资产按原路径发布到不可变的 `rosolab/eai-simulator-assets` revision，并从干净 checkout 验证该 revision。

## SimpleCar 与 OCTAGON-AIO-C01

导入器固定 Gazebo commit `772445543473089ee8242771e1d946402162fe04`，校验用户指定的 `tmp/` 文档和 STEP 哈希，使用 Isaac Sim 内置 HOOPS 转换 STEP，导入与 Gazebo 几何一致的机器人 URDF，并在 `usd/robot/simple_car/source.json` 记录生成哈希及所有保真度限制。

```bash
conda activate env_isaaclab
python tools/assets/import_simple_car.py --gazebo-repo /home/pupil/gazebo-sim
python tools/assets/import_simple_car.py --gazebo-repo /home/pupil/gazebo-sim --check
```

转换在 Kit 子进程中执行，因为 Isaac Sim 的快速关闭会终止当前解释器；父进程校验完成标记、原子安装生成文件、创建缩略图和来源清单，最后执行只读检查。不要把 `tmp/` 当作运行时依赖；未完成许可审查前也不要发布厂家原始资料。

Provider 发布必须在同一个不可变 revision 中包含 `usd/robot/simple_car/{simple_car.usda,gshub_core.usd,configuration/gshub_core_*.usd,simple_car.urdf,source.json}`、共享传感器载荷 `usd/payloads/sensors/octagon_aio_c01/{octagon_aio_c01.usd,octagon_aio_c01_visual.usda,OCTAGON_AIO_C01.json,source.json}`、`usd/payloads/sensors/mid360/MID360.json` 以及 `controller/traditional/simple_car_planar/{__init__.py,simple_car_planar.py}`。导入器会对转换后的 CAD 去实例化，并把规格书材质（银白色铝合金外壳、黑色 PC 光学罩）绑定写入 `octagon_aio_c01.usd` 内部。
