# EMOS

EMOS (Emergency Multi-robot Operation System) provides **business-agnostic** multi-agent LLM discussion and subtask allocation: given a task story, subtask definitions, and robot capability descriptions, it allocates subtasks to heterogeneous robots through multi-round discussion, falling back to heuristic allocation when the LLM is unavailable or parsing fails.

## Positioning

- **Input**: a caller-supplied `EMOSScenarioConfig` (scenario narrative, subtasks, position rules, fallback policy) and `EMOSRobotAgentSpec` robot profiles, plus an Isaac Lab-compatible `base_env`.
- **Output**: a subtask-to-robot allocation handed to execution layers such as navigation and manipulators.
- **Boundary**: EMOS does not build the simulation scene, ships no scenario content, and does not interpret the business meaning of subtasks.

## Core flow

```text
EMOSScenarioConfig (task story + subtasks + position rules)
        +
EMOSRobotAgentSpec (robot profiles) + base_env (robot positions)
        ↓
EMOSDiscussionManager multi-round discussion scheduling
        ↓
LLM output parsing → subtask allocation
        ↓ (LLM unavailable / parsing failed)
preferred_fallback heuristic allocation
```

`build_from_agent_specs()` reads robot positions from the articulation / rigid-object state of `base_env` for use during discussion and allocation.

## Using EMOS with EAI

The [Fire Rescue experiment](getting_started_en.md) shows the full EMOS integration: the factory fire-inspection scenario runs EMOS discussion and task allocation, then hands results to the navigation and execution layers. Code, full configuration reference, and dependencies live under `algorithm/emos/`.

## Source

Upstream repository: [EMOS](https://github.com/SgtVincent/EMOS); the copy bundled with EAI lives under `algorithm/emos/`.
