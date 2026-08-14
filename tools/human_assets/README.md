# Human Asset Tools

这里集中维护 `usd/human/` registry 的演示、动作创作、格式转换、迁移、cache 构建和验证
入口。所有命令都从仓库根目录运行；完整资产清单与动作契约见
[`usd/human/README.md`](../../usd/human/README.md)。

公开运行时使用的角色、纹理、动作和 cache 全部位于 `usd/human/` 下，manifest 只保存相对于
human root 的路径。转换或迁移命令可以读取经过批准的外部输入，但生成的运行时内容不能依赖
开发者主目录或其他仓库外绝对路径。

## 安装完整运行时资产

先取得 gated dataset
[`HuangQIjun/eai-simulator-assets`](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)
的访问权限，再从 Git 仓库根目录一次性下载全部 Human 资产：

```bash
hf auth login
hf download HuangQIjun/eai-simulator-assets \
  --type dataset \
  --revision v0.1.0-beta.1 \
  --include "usd/human/**" \
  --local-dir .
```

该命令直接补齐 `usd/human/`，不提供按角色、动作或 pack 拆分的下载入口。

## 文件职责

| 文件 | 运行环境 | 输入 | 输出 |
| --- | --- | --- | --- |
| `run_demo.py` | Isaac Sim 5.1 / `env_isaaclab` | manifest、44 个角色、12 个标准动作和 cache | 全角色 GUI 或完整 headless 能力验证 |
| `scene.py` | 由 demo 在 Isaac Sim 中导入 | stage、网格、路线和角色位置 | 地面、路线、选择环、bounds 和相机 |
| `motion_controls.py` | 纯 Python，由 demo 导入 | Q、数字键、Enter、X、Esc | 角色选择、动作/移动和恢复请求 |
| `edit_action.py` | 纯 Python | action ID、时长、fps、profile | JSON 关键帧草稿 |
| `import_action.py` | Isaac Sim 5.1 | 一个带动画的 GLTF/GLB | custom action USD 和 overlay manifest |
| `convert_gltf_assets.py` | 计划模式纯 Python；转换模式 Isaac Sim | approved urban-sim source root | allowlist 计划、USD、conversion report |
| `migrate_assets.py` | 纯 Python；依赖已验证转换报告 | approved source root、目标 human root | manifest 和 audit summary |
| `build_motion_cache.py` | `pxr` / Isaac Sim | manifest、可选 overlay、角色和动作 USD | retarget cache JSON 和报告 |
| `validate_assets.py` | 纯 Python | manifest 和已安装文件 | 确定性 JSON 结构报告 |

## 常用命令

GUI 全角色验证：

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python -u tools/human_assets/run_demo.py
```

脚本按资产 ID 稳定排序并同时加载全部 44 个角色。按 `Q` 恢复当前角色到动作前原位、选择下一
个角色、移动选择环并聚焦相机。39 个骨骼角色使用顶部数字键输入 `1-12` 后按 `Enter` 播放标准
动作；4 个可移动 rigid 活动角色只接受数字 `1`，完成一次向外移动并返回；静态骑手可被选择，
但会拒绝数字动作。`Backspace` 编辑输入，`X` 停止并恢复当前角色，`Esc` 关闭。

所有可移动角色初始保持静止。非循环动作完成、循环动作取消或替换、按 `Q` 切换角色以及 rigid
移动完成时，demo 都会把对应角色恢复到动作前原位。`phone_call` 等 `path_policy=pause` 动作
保持原地；`path_policy=continue` 动作播放期间沿短路径移动。

角色生成和动作切换时会自动贴地。蒙皮角色按当前 UsdSkel 姿态变形后的可见网格点计算地面
高度，非蒙皮网格或无法计算 skinning 的内容使用 USD bounds 回退。

Headless 模式使用同一 backend 和控制状态机，完整验证 39 × 12 个骨骼动作、4 个 rigid
往返移动和 1 个静态角色：

```bash
conda run --no-capture-output -n env_isaaclab \
  python -u tools/human_assets/run_demo.py --headless
```

成功时最后输出 `Verified unified human matrix: 39x12 + 4 + 1`。

创建 JSON 动作草稿：

```bash
python tools/human_assets/edit_action.py init \
  --action-id wave --duration 2.0 usd/human/custom-actions/wave.json
```

导入并发布一个 GLTF/GLB clip。仓库内的 `usd/human/motions/sources/bow.gltf` 是自包含的相对
路径示例，其 buffer 和图片不依赖仓库外文件：

```bash
python tools/human_assets/import_action.py usd/human/motions/sources/bow.gltf \
  --action-id bow-example --profile smplx_70 --human-root usd/human
```

该示例只用于说明 `import_action.py` 的单段动作导入流程，`run_demo.py` 不会在启动时转换或发布
GLTF，也不会修改 overlay manifest 或 retarget cache。

只生成转换计划，不启动 Isaac Sim：

```bash
python tools/human_assets/convert_gltf_assets.py \
  --source-root path/to/approved/urban-sim \
  --target-root usd/human --plan-only
```

执行转换时去掉 `--plan-only`，并建议显式指定
`--result-json usd/human/conversion-report.json`。转换会启动 Isaac Sim；运行前必须审查 allowlist、
输入许可和输出路径。

先 dry-run 迁移：

```bash
python tools/human_assets/migrate_assets.py \
  --source-root path/to/approved/urban-sim \
  --target-root usd/human --dry-run
```

审查完成后去掉 `--dry-run` 才会写入 `manifest.json` 和 `audit-summary.json`。该工具不会
生成 `pack-checksums.json`。

为一个角色重建指定动作 cache：

```bash
python tools/human_assets/build_motion_cache.py \
  --manifest usd/human/manifest.json \
  --overlay usd/human/custom-actions/manifest.json \
  --asset-id synbody-0000001 --motion-id bow
```

跨 profile cache 会记录源和目标骨架的 rest 旋转。`UsdSkelAnimation` 的旋转样本是绝对
局部变换；播放时先在两套骨架层级中累计骨架空间旋转，把源骨架空间的 rest 相对姿态放到
目标 rest 上，再反解目标局部旋转。跨 profile 的手臂链还会在角色身体坐标系中迁移腕部
位置，并以源肘部为弯曲方向提示，用目标上臂和前臂长度求解肩、肘旋转；腕部全局朝向保持
前一步的重定向结果。同 profile 的 strict cache 不增加这些字段，重建后应保持原格式。

验证当前安装：

```bash
python tools/human_assets/validate_assets.py \
  --manifest usd/human/manifest.json \
  --output /tmp/eai-human-validation.json
```

`scene.py` 和 `motion_controls.py` 是内部模块，没有独立 CLI；它们的行为由
`test_human_demo_controls.py` 和 `test_human_motion_number_controls.py` 覆盖。

本版本只支持单段 GLTF/GLB 导入和 JSON 关键帧动作发布，不提供通用复合动作制作工具。

## Provider 发布要求

human provider 必须包含 `characters/`、`activities/` 和 `motions/`。自包含导入示例
`usd/human/motions/sources/bow.gltf` 包含在 motions pack 中，但不是 demo 启动依赖。发布前需要
完成统一 Isaac GUI/headless 验证、provenance/license 审批和精确 pack hash 生成。

上传 Hugging Face provider 后需要创建 immutable tag、更新 `usd/human/pack-checksums.json`，
并从干净仓库根目录针对该固定 revision 复验。源代码映射、provider 文件、tag 和校验元数据
必须保持版本一致。
