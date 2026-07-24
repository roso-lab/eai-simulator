# EMOS — Emergency Multi-robot Operation System

本包提供**与具体业务无关**的多智能体 LLM 讨论与子任务分配能力。任务故事、子任务定义、机器人能力描述均由**调用方**以数据结构提供；本包不内置任何场景内容。

---

## 1. 职责范围

| 本包 | 非本包 |
|------|--------|
| `EMOSScenarioConfig` / `EMOSRobotAgentSpec` / `RobotTask`、讨论调度、LLM 与解析、启发式回退 | 仿真环境搭建、执行器（导航、机械臂等）、`RobotTask` 的业务解读 |

---

## 2. 任务与故事背景：`EMOSScenarioConfig`

任务“故事”与规则集中在 **`EMOSScenarioConfig`**。通常由调用方先构造 **dict**，再调用 **`scenario_from_dict(data)`** 得到配置对象（字段与 dict 键名一致）。

### 2.1 叙事与锚点（面向 LLM 的顶层文案）

| dict 键 | 含义 |
|---------|------|
| **`scenario_id`** | 场景标识字符串。 |
| **`scene_title`** | 场景标题，进入 `scene_description` 首段。 |
| **`anchor_label`** | 事件锚点名称（如“火源”“事故点”），与 `hazard_pos` 一起描述事件位置。 |
| **`task_description`** | **总体任务故事背景**：多段文字，说明协作目标与约束；组长与机器人提示均会引用。 |

### 2.2 子任务（可分配单元）

**`subtasks`**：字典，**键**为子任务 id（如 `red`、`blue`，用于后续解析与 `match_keywords` 匹配），**值**为：

| 子字段 | 含义 |
|--------|------|
| **`name`** | 子任务显示名称。 |
| **`description`** | 能力/要求说明，写入场景描述。 |
| **`match_keywords`** | 关键词列表，用于将 LLM 输出映射到该子任务 id。 |

### 2.3 子任务目标位置规则：`position_rules`

在 **`trigger(hazard_pos)`** 时，由 **`compute_subtask_positions(scenario, hazard_pos)`** 计算各子任务平面目标点。每条规则 **键**为子任务 id，**值**中：

| **`type`** | 含义 |
|------------|------|
| **`fixed`** | 固定世界坐标：需提供 **`xy`**：`[x, y]`。 |
| **`anchor_offset`** | 相对锚点偏移：需提供 **`dx`**、**`dy`**（米）；目标为 `(hazard_x + dx, hazard_y + dy)`。 |

### 2.4 额外约束文案：`subtask_constraints`

**`subtask_constraints`**：列表，每项包含 **`subtask_id`**、**`title`**、**`lines`**（字符串列表）。内容会**原样拼入**发给 LLM 的场景描述，用于强调某类子任务的特殊规则。

### 2.5 启发式回退与其它

| 键 | 含义 |
|----|------|
| **`preferred_fallback`** | `机器人名 → 子任务 id`，在 LLM 不可用或回退分配时使用。 |
| **`yellow_subtask_id`**、**`yellow_robot_prefix`** | 与“黄色”子任务机动性校验相关（见 `assignment` 模块）。 |
| **`parse_keyword_overrides`** | 可选，覆盖某子任务 id 的解析关键词。 |

### 2.6 场景内静态机器人档案：`robot_profiles`（可选）

**`robot_profiles`**：字典，**键**为与环境中一致的机器人名，**值**为：

| 键 | 含义 |
|----|------|
| **`type`** | 机器人类型标签（展示用）。 |
| **`capabilities`** | 能力字符串列表。 |

若仅通过 **`EMOSRobotAgentSpec`** 提供能力（见下节），可在 dict 中省略 **`robot_profiles`**，或在合并后由 `build_from_agent_specs` 写入 **`scenario.robot_profiles`**。

---

## 3. 机器人简历：`EMOSRobotAgentSpec`

每个参与讨论的智能体对应一条 **`EMOSRobotAgentSpec`**，放在 **`agent_specs: Dict[str, EMOSRobotAgentSpec]`** 中：**字典键必须与环境中 agent 名称一致**（如 `m20_1`）。

| 字段 | 含义 |
|------|------|
| **`agent_name`** | 可选；若为空，**`build_from_agent_specs`** 会用 dict 的 key 填充。 |
| **`robot_type`** | 类型标签（如“四足机器人 M20”）。 |
| **`capabilities`** | 能力列表，进入简历与讨论提示。 |
| **`preferred_task`** | 可选，偏好子任务 id（如 `red`），会合并进 **`scenario.preferred_fallback`**（若该键尚未存在）。 |

**`build_from_agent_specs`** 会把 spec 中的 **`robot_type` / `capabilities`** 写回 **`scenario.robot_profiles`**（若该机器人尚无 profile 或需要覆盖），供 **`build_scene_description`** 与 **`build_robot_resumes`** 使用。

### 3.1 送入 LLM 的“简历”长什么样

**`resume_provider.build_robot_resumes`** 为每个 agent 生成结构化字典，通常包含：**`robot_name`**、**`robot_type`**、**`position`**（当前平面位置）、**`capabilities`**。若启用 **`use_env_resume`** 且提供 **`generate_robot_resume_fn(env, name, ...)`**，则优先使用该函数生成的简历（需集成方实现）。

---

## 4. 集成步骤（顺序）

1. 组装场景 **dict** → **`scenario = scenario_from_dict(data)`**（或 **`load_scenario(路径)`** 读 YAML 再得到 `scenario`）。  
2. 为每个参与 agent 构造 **`EMOSRobotAgentSpec`**，放入 **`agent_specs`**。  
3. **`EMOSDiscussionManager.build_from_agent_specs(base_env, agent_specs, scenario, ...)`**。  
4. **`trigger(hazard_id, hazard_pos)`**；主循环或定时 **`poll()`**。  
5. 对 **`poll()`** 返回的 **`Dict[str, RobotTask]`** 做导航与控制（本包不执行控制）。

---

## 5. 输入汇总

| 阶段 | 内容 |
|------|------|
| **`build_from_agent_specs`** | **`base_env`**：环境引用（读位姿、可选简历生成）。**`scenario`**：**必填** `EMOSScenarioConfig`。**`agent_specs`**：各 agent 名称 → **`EMOSRobotAgentSpec`**。 |
| **`trigger`** | **`hazard_id`**：事件编号。**`hazard_pos`**：锚点 `(x, y, z)`（米）。可选 **`task_description`**：非空时覆盖 **`scenario.task_description`** 用于本轮讨论。可选 **`subtask_positions`**：非空时覆盖由锚点计算出的子任务目标点。 |
| **运行期（内部）** | 从 **`base_env`** 读取各 agent 世界坐标；结合 **`scenario`** 与 **`compute_subtask_positions`** 生成场景文本与简历 JSON。 |

---

## 6. 输出汇总

| 接口 | 内容 |
|------|------|
| **`poll()`** / **`last_result`** | **`Dict[str, RobotTask]`**，讨论未结束为 **`None`**。 |
| **`RobotTask`** | **`robot_name`**、**`subtask_colour`**（子任务 id/颜色键）、**`subtask_name`**、**`subtask_desc`**、**`target_xy`**（该子任务目标平面坐标，米）。 |

调用方将 **`target_xy`** 与 **`subtask_*`** 映射为自身系统的目标点、技能或 UI。

---

## 7. 可选：`load_scenario(path)`

将**调用方维护的** YAML 文件解析为 **`EMOSScenarioConfig`**（需安装 PyYAML）。字段需与 **`scenario_from_dict`** 所支持的 dict 结构一致（见第 2 节）。与在代码中手写 dict 再 **`scenario_from_dict`** 等价，择一即可。

---

## 8. 数据流（示意）

```
scenario_from_dict(dict)  [或 load_scenario(路径)]
        → EMOSScenarioConfig
        + agent_specs (EMOSRobotAgentSpec × N)
        → build_from_agent_specs(base_env, agent_specs, scenario)
        → trigger(hazard_id, hazard_pos)
        → poll() → Dict[str, RobotTask]
```

---

## 9. 目录结构

```
algorithm/emos/
├── types.py              # EMOSScenarioConfig、EMOSRobotAgentSpec、RobotTask、scenario_from_dict
├── scenario_loader.py    # load_scenario（可选）
├── engine.py             # EMOSDiscussionManager
├── assignment.py
├── scene_builder.py      # build_scene_description
├── resume_provider.py    # build_robot_resumes
├── brain/                # LLM 讨论与 API
├── requirements.txt
└── README.md
```
