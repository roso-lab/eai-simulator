# 场景生成工具

[English](README.md)

`tools/scenario_generation/` 是可选的具体仿真场景编写与准入工具层。它不是被测系统算法、
底层 Skill provider，也不是任务运行器。

## 负责什么

这个目录可以用于：

- 根据已注册的场景模板生成或编译 scenario JSON；
- 生成可重复的 seed 实例族和条件变体；
- 选择已注册的机器人、挂载、目标点和 payload policy；
- 检查引用、任务 prerequisite DAG、能力、几何和占据图连通性；
- 生成 robot dossier、能力画像、哈希和准入报告；
- 对生成的场景执行可选的沉降或执行 smoke check。

它输出的是场景候选或准入/证据记录。离线检查通过，不等于机器人已经在 Isaac Sim 中完成了任务。

## 在 HeteroCoord 中的位置

```text
scenario_generation（可选的场景编写）
                 ↓
task/config（与场景解耦的任务合同）
                 ↓
simulator.py + EAI（创建场景和机器人）
                 ↓
algorithm/system（规划和任务分配）
                 ↓
task/executors（Skill/EAI 执行）
                 ↓
task/evaluators（物理里程碑证据）
```

当前固定 Fire Rescue 测评直接使用 EAI saved environment 和 `task` 任务合同即可。只有在需要
编写新场景、新任务实例、扰动条件或 seed 实例族时，才需要调用本目录。

## 边界规则

场景生成代码不得：

- 创建第二个 Isaac application 或替代 `simulator.py`；
- 在算法 adapter 中驱动 controller 或调用 Skill；
- 判定 benchmark 成功或写入物理成功结论；
- 将 USD 路径、Prim handle 或 controller 内部细节写入任务合同；
- 让 `task/runner.py` 依赖可选的场景生成模块。

`task/config/*.json` 描述“要完成什么”，生成的 scenario 描述“在哪个具体场景和实例中执行”。
两种格式不能静默互相替代。

## 常用入口

以下命令均从仓库根目录运行，且都是可选流程：

```bash
# 不启动 Isaac Sim，查看已注册模板或 manifest。
python tools/scenario_generation/generate_factory.py --schema
python tools/scenario_generation/generate_family.py --manifest

# 离线检查一个具体场景。
python tools/scenario_generation/validate_factory.py --schema-only <scenario.json>
python tools/scenario_generation/validate_factory.py <scenario.json>

# 生成确定性的实例族。
python tools/scenario_generation/generate_family.py \
  --skeleton TRN-01 --seed 17 --out-dir tmp/generated_scenarios/trn01

# 生成实例池，可选执行 headless 沉降检查。
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/example --skeleton TRN-01 \
  --seeds 17 23 --dry-run
```

会启动 Isaac Sim、访问外部资产或调用 LLM 的命令属于集成流程，只能在指定的 `env_isaaclab`、
资产 provider 和凭据准备好后运行。除非明确作为受控 fixture，否则不得提交生成场景、下载的 USD、
模型权重、报告或缓存。

## 证据状态含义

以下状态必须区分：

```text
placement_validated       schema/语义/几何检查通过
feasibility_validated     确定性路线和能力 Oracle 通过
spawn_validated           Isaac 场景实例化并完成沉降检查
execution_validated       指定执行合同完成
physical_task_success     任务 evaluator 接受物理里程碑
```

最后一个状态属于 `task/evaluators/`，不属于本目录。运动学或离线搬运检查必须明确标注限制，
不能被表述为抓取、接触、承载或真实物理成功证据。

## 维护策略

本目录保留了一些 EAI 场景编写、场景族生成和资产审计工具，其中部分属于旧场景族。除非已经
核对输入 schema 和依赖，否则不要把它们接入 Fire Rescue 核心执行链。脚本如果仍然依赖已删除的
demo 或过时的场景格式，应归档或删除，不要向 `task/runner.py` 添加兼容分支。

任务执行边界请先阅读 [task/README.zh-CN.md](../../task/README.zh-CN.md)；仓库、资产和配置检查请阅读
[tools/validation](../validation/README.zh-CN.md)。
