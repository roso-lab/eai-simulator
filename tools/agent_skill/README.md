# Agent skills

Self-contained skill packages an agent (or a developer) can follow to
complete a repository workflow end to end. Each subdirectory is one skill:
read its `SKILL.md` first; `references/` holds the detailed rationale and
`scripts/` the working tools.

| Skill | Purpose |
| --- | --- |
| [`usd-to-gazebo/`](usd-to-gazebo/SKILL.md) | Convert an Isaac Sim USD scene into a validated Gazebo Classic 11 asset (models + world + textures + validation battery) |

Skills here are project knowledge: they encode verified platform facts and
real production bugs, not just command sequences. Keep them synchronized
with the reference outputs they produced (e.g.
`usd/scene/factory_gazebo/`).
