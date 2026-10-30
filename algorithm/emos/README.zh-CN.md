# EMOS：Emergency Multi-robot Operation System

EMOS 提供与具体业务无关的多智能体 LLM 讨论和子任务分配能力。调用方提供任务故事、子任务、机器人能力和环境引用；EMOS 负责讨论调度、结构化解析和启发式回退，但不搭建仿真场景、不执行导航或机械臂控制，也不解释业务动作。

## 核心数据

- EMOSScenarioConfig：场景文本、子任务、目标位置规则、约束文案、回退映射和可选机器人档案。
- EMOSRobotAgentSpec：agent 名称、机器人类型、能力和可选偏好任务；名称必须与环境 agent 名称一致。
- RobotTask：poll() 返回的机器人任务，包含机器人、子任务说明和 target_xy。

scenario_from_dict(data) 将调用方数据转换为配置；load_scenario(path) 可选地从 YAML 加载相同结构。位置规则通过 compute_subtask_positions 支持 fixed 和 anchor_offset。

## 集成流程

~~~text
scenario_from_dict(dict) 或 load_scenario(path)
        -> EMOSScenarioConfig
        + EMOSRobotAgentSpec 列表
        -> EMOSDiscussionManager.build_from_agent_specs(base_env, ...)
        -> trigger(hazard_id, hazard_pos)
        -> poll() -> Dict[str, RobotTask]
        -> 调用方的导航和控制层
~~~

集成顺序是构造 scenario、构造 agent_specs、调用 build_from_agent_specs、触发事件并轮询任务。base_env 只用于读取位姿或生成可选简历；执行仍由调用方负责。

## 公开入口

~~~python
from algorithm.emos.engine import EMOSDiscussionManager
from algorithm.emos.types import EMOSRobotAgentSpec, scenario_from_dict

scenario = scenario_from_dict(data)
manager = EMOSDiscussionManager.build_from_agent_specs(
    base_env=env,
    agent_specs=agent_specs,
    scenario=scenario,
)
manager.trigger(hazard_id=1, hazard_pos=(0.0, 0.0, 0.0))
result = manager.poll()
~~~

在 algorithm/emos/brain 中配置 LLM provider，并安装依赖：

~~~bash
pip install -r algorithm/emos/requirements.txt
~~~

## brain 模块

algorithm/emos/brain/ 提供 EMOS 使用的 LLM 讨论、请求门控、解析以及技能和动作辅助模块。它是库代码，不是独立命令行程序；调用方应通过 EMOS manager 使用，不要直接启动本目录下的文件。

模型 API 读取 DEEPSEEK_API_KEY，或读取调用方显式传入的 API key。本包不自动读取 .env 文件，也不保存凭据。MultiLLM_discussion.py 和 llm_agent.py 负责讨论流程；brain/API/ 负责模型请求、请求门控和 Python 工具；brain/actions/ 与 brain/skills/ 提供数据结构。实际执行由集成层负责。

请求门控用于串行化对单实例本地 LLM 服务的调用。默认端口上的回环地址始终会被门控；如需门控其他私有端点，请设置逗号分隔的 EMOS_LOCAL_LLM_HOSTS 环境变量（host:port 条目）。本包不硬编码任何端点地址。

~~~bash
pip install -r algorithm/emos/requirements.txt
export DEEPSEEK_API_KEY=your_api_key_here
python -m py_compile \
  algorithm/emos/brain/*.py \
  algorithm/emos/brain/API/*.py \
  algorithm/emos/brain/actions/*.py \
  algorithm/emos/brain/skills/*.py
~~~

该命令只检查 Python 语法，不验证 provider 凭据、API 访问、模型回答或 simulator 集成。

## 边界和验证

EMOS 不创建 Isaac 应用、不注册 Gym 任务、不发布 ROS topic。离线检查可以验证 Python 语法和数据解析；真实 LLM 请求、凭据、网络和 Isaac 集成需要相应运行环境。
