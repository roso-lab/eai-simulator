# EMOS: Emergency Multi-robot Operation System

EMOS provides business-agnostic multi-agent LLM discussion and subtask allocation. The caller supplies the task story, subtasks, robot capabilities, and environment reference. EMOS schedules discussion, parses structured results, and provides a heuristic fallback; it does not build simulator scenes, execute navigation or manipulation, or interpret business actions.

## Core data

- EMOSScenarioConfig stores scene text, subtasks, position rules, constraints, fallback mapping, and optional robot profiles.
- EMOSRobotAgentSpec describes an agent name, robot type, capabilities, and an optional preferred task. The name must match the environment agent name.
- RobotTask is returned by poll() and contains the robot, subtask description, and target_xy.

scenario_from_dict(data) builds the configuration from caller-owned data. load_scenario(path) optionally loads the same schema from YAML. Position rules support fixed and anchor_offset targets through compute_subtask_positions.

## Integration flow

~~~text
scenario_from_dict(dict) or load_scenario(path)
        -> EMOSScenarioConfig
        + EMOSRobotAgentSpec list
        -> EMOSDiscussionManager.build_from_agent_specs(base_env, ...)
        -> trigger(hazard_id, hazard_pos)
        -> poll() -> Dict[str, RobotTask]
        -> caller-owned navigation and control layer
~~~

The integration sequence is: build the scenario, build agent specifications, call build_from_agent_specs, trigger an event, and poll for tasks. base_env is read for poses or optional resume generation; execution remains with the caller.

## Public entry point

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

Configure the LLM provider in algorithm/emos/brain and install the package dependencies:

~~~bash
pip install -r algorithm/emos/requirements.txt
~~~

## Brain package

algorithm/emos/brain/ contains the LLM discussion, request-gating, parsing, and skill/action helpers used by EMOS. It is a library boundary, not a standalone command-line application. Callers should use the EMOS manager instead of launching files in that directory directly.

The model API reads DEEPSEEK_API_KEY, or an API key explicitly supplied by the caller. This package does not load .env files or store credentials. MultiLLM_discussion.py and llm_agent.py orchestrate discussion; brain/API owns model requests, request gates, and Python tools; brain/actions and brain/skills define data structures. The integration layer owns actual execution.

~~~bash
pip install -r algorithm/emos/requirements.txt
export DEEPSEEK_API_KEY=your_api_key_here
python -m py_compile \
  algorithm/emos/brain/*.py \
  algorithm/emos/brain/API/*.py \
  algorithm/emos/brain/actions/*.py \
  algorithm/emos/brain/skills/*.py
~~~

The command checks Python syntax only. It does not validate provider credentials, API access, model responses, or simulator integration.

## Boundaries and validation

EMOS does not create an Isaac application, register Gym tasks, or publish ROS topics. Offline checks can validate Python syntax and data parsing; real LLM requests, credentials, network access, and Isaac integration require their respective environments.
