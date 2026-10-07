# Skill-level algorithms

The paper-level system algorithm decides *which robot performs which task*.
The skill layer turns one assigned task into an executable primitive such as a
path, synchronized trajectory, Nav2 goal, grasp command, or velocity command.

This is the layer where low-level control algorithms belong in HeteroCoord.
They are capabilities of the robot execution stack, not additional columns in
the high-level multi-robot coordination comparison.

## Existing skill providers

| Skill group | Provider upload path | Output | Runtime owner |
|---|---|---|---|
| single-robot navigation | `algorithm/skills/navigation/global_planner/` | path and velocity command | EAI navigation controller |
| multi-robot collision-free navigation | `algorithm/skills/navigation/multi_robot_navigation/` | synchronized trajectory | EAI simulation loop |
| Nav2 transport | `algorithm/skills/external_io/nav2/` | ROS goal / bridge command | system ROS2 + EAI bridge |
| manual velocity transport | `algorithm/skills/external_io/keyboard/` | ROS Twist | system ROS2 + EAI bridge |
| EMOS internal skills | `algorithm/system/coordination/emos/brain/skills/` | Torch command | internal EMOS runtime, not a standalone benchmark system algorithm |

The four providers are not included in this branch yet; their dedicated
developer will upload implementations under the paths above. The
machine-readable list is in `algorithm/skills/registry.py`. New providers
should implement the dependency-light `SkillProvider` contract from
`algorithm/skills/contracts.py` where practical. A provider may compute a
plan, but it must not step Isaac, call a controller directly, or assert that a
milestone succeeded.

## Runtime sequence

```text
system AlgorithmDecision / Assignment
                  |
                  v
          task executor builds SkillRequest
                  |
                  v
           skill provider builds SkillPlan
                  |
                  v
      EAI executor maps SkillCommand to controller
                  |
                  v
    simulator steps -> feedback -> evaluator evidence
```

`source/EAI/EAI/controllers/` remains the controller contract and application
of actions. It is not copied into this directory. `task/executors/` is the
integration point that connects a skill plan to that controller contract.
