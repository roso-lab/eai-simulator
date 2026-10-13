# EMOS: Emergency Multi-robot Operation System

EMOS provides business-agnostic multi-agent LLM discussion and subtask allocation. The caller supplies the task story, subtasks, robot capabilities, and environment reference. EMOS schedules discussion, parses structured results, and provides a heuristic fallback; it does not build simulator scenes, execute navigation or manipulation, or interpret business actions.

## Core data

- EMOSScenarioConfig stores scene text, subtasks, position rules, constraints, fallback mapping, and optional robot profiles.
- EMOSRobotAgentSpec describes an agent name, robot type, capabilities, and an optional preferred task. The name must match the environment agent name.
- RobotTask is returned by poll() and contains the robot, subtask description, and
  target_xy (``None`` for targetless / non-motion subtasks).

scenario_from_dict(data) builds the configuration from caller-owned data. load_scenario(path) optionally loads the same schema from YAML. Position rules support fixed, anchor_offset, and none/targetless targets through compute_subtask_positions; targetless rules are omitted from the positions map rather than invented as ``(0, 0)``. ``{"type":"fixed"}`` without ``xy`` and unknown kinds raise ``ValueError``; explicit ``{"type":"none"}`` and legacy ``{"type":"fixed","xy":null}`` remain the supported targetless spellings. Intentionally closed tasks (for example yellow when no eligible robot remains) are passed as ``excluded_subtask_ids`` into ``open_subtask_ids`` / parse / fallback / scene prompts — never by deleting coordinates.

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

The integration sequence is: build the scenario, build agent specifications, call build_from_agent_specs, trigger an event, and poll for tasks. By default `base_env` is read for poses or optional resume generation; callers may instead pass an explicit `get_robot_pos_fn` (preferred for offline / authoring paths) and an optional `position_source` label such as `native_root_state` or `public_spawn_anchor`. Explicit providers are fail-fast and never silently invent `(0, 0, 0)`. Execution remains with the caller.

## Public entry point

~~~python
from algorithm.system.coordination.emos.engine import EMOSDiscussionManager
from algorithm.system.coordination.emos.types import EMOSRobotAgentSpec, scenario_from_dict

scenario = scenario_from_dict(data)
manager = EMOSDiscussionManager.build_from_agent_specs(
    base_env=env,
    agent_specs=agent_specs,
    scenario=scenario,
)
# Offline / labeled spawn injection (optional; takes priority over base_env):
# manager = EMOSDiscussionManager.build_from_agent_specs(
#     base_env=None,
#     agent_specs=agent_specs,
#     scenario=scenario,
#     get_robot_pos_fn=spawn_callback,
#     position_source="public_spawn_anchor",
# )
manager.trigger(hazard_id=1, hazard_pos=(0.0, 0.0, 0.0))
result = manager.poll()
~~~

Configure the LLM provider in algorithm/system/coordination/emos/brain and install the package dependencies:

~~~bash
pip install -r algorithm/system/coordination/emos/requirements.txt
~~~

## Replacing assignment without changing the task runner

The Fire Rescue runner accepts `--assignment-policy emos` (default) or a trusted
`module:assign` function, e.g. `algorithm.system.coordination.emos.example_policy:assign`. Implement
`assign(request: AssignmentRequest) -> dict[str, str]`, returning robot names mapped
to scenario subtask IDs. `policy.py` defines the data-only request and output validation;
`example_policy.py` provides a deterministic local example. The task still owns target
coordinates, execution, success criteria and logical checkpoint milestones.
The repository's `algorithm.system.coordination.coherent.fire_rescue_policy:assign` is the formal
Fire Rescue assignment entry point for `COHERENT-adapted-to-EAI`.

### HeteroCoord condition registration

`algorithm.system.coordination.emos.benchmark_condition` exposes `condition_record()`,
`condition_manifest()`, and `record_assignment()`. These record the EMOS Robot
Resume/discussion/assignment boundary and model/API provenance without changing
the manager execution path. The condition is `EMOS-adapted-to-EAI` and has no
learned checkpoint; EAI owns navigation, manipulation, parallel execution,
physical milestones, and termination. The complete Habitat-MAS URDF self-prompt
and decentralized low-level execution are not migrated through this seam, so
D2/D4/D5 and D3 without external handoff evidence are `N/A`.

`condition_manifest()` is metadata, not a `module:assign` callback and must not
be passed to `--assignment-policy`. An assignment is a proposal record; the
evaluator's physical evidence remains authoritative.

The manager snapshots request inputs on the simulator thread, then calls the selected
algorithm on its worker with a deep copy. No live simulator handle is passed to the
callback. Unknown IDs/capability violations are rejected and callback errors propagate
through `poll()` rather than silently using EMOS fallback. Rescue replanning also uses
the selected policy. Partial mappings are permitted but omitted tasks are not filled.
Programmatic integrations call `manager.set_assignment_policy(callback, name=...)`
before the first trigger; no replacement is allowed during a running episode.

This initial callback contract is stateless. It rejects closure functions/callable
objects but cannot detect hidden module-global mutation: algorithms must not rely on
unadapted mutable globals or model memory. Such state needs a separate checkpoint
adapter; external service history is also outside this interface. EMOS orchestration
state and the copied request are checkpointed by the Fire Rescue native integration;
the fixed callback itself is retained as a code resource, not serialized. See the
Fire Rescue READMEs for `--task-checkpoints` and the unattended single-trial command.

## Brain package

algorithm/system/coordination/emos/brain/ contains the LLM discussion, request-gating, parsing, and skill/action helpers used by EMOS. It is a library boundary, not a standalone command-line application. Callers should use the EMOS manager instead of launching files in that directory directly.

The model API reads DEEPSEEK_API_KEY, or an API key explicitly supplied by the caller. This package does not load .env files or store credentials. MultiLLM_discussion.py and llm_agent.py orchestrate discussion; brain/API owns model requests, request gates, and Python tools; brain/actions and brain/skills define data structures. The integration layer owns actual execution.

The request gate serializes calls to single-instance local LLM servers. It always covers the loopback endpoints on the default port; set the comma-separated EMOS_LOCAL_LLM_HOSTS environment variable (host:port entries) to gate additional private endpoints. No endpoint address is hardcoded in this package.

~~~bash
pip install -r algorithm/system/coordination/emos/requirements.txt
export DEEPSEEK_API_KEY=your_api_key_here
python -m py_compile \
  algorithm/system/coordination/emos/brain/*.py \
  algorithm/system/coordination/emos/brain/API/*.py \
  algorithm/system/coordination/emos/brain/actions/*.py \
  algorithm/system/coordination/emos/brain/skills/*.py
~~~

The command checks Python syntax only. It does not validate provider credentials, API access, model responses, or simulator integration.

## Boundaries and validation

EMOS does not create an Isaac application, register Gym tasks, or publish ROS topics. Its
assignment output is a system-layer decision. The
`algorithm/system/coordination/emos/brain/skills/` package is an EAI/Torch-dependent
runtime used internally by EMOS; it is not a standalone system-algorithm entry point.
New execution work must expose a skill provider through `algorithm/skills/` and connect
it to EAI from the benchmark adapter layer.

Offline checks can validate Python syntax and data parsing; real LLM requests, credentials,
network access, and Isaac integration require their respective environments.
