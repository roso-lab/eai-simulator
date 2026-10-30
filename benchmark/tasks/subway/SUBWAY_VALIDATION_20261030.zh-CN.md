# Subway 逻辑轨道验收记录

- 语义地图 revision、binding digest、ground 67/69、aerial 68/72 已绑定 manifest。
- `assembly_point`、13 个 `trapped_people`、两个 generator 和两端 door 语义点已核对。
- 所有机器人出生点统一为 `assembly_point`；9 个候选机器人和 6 个 roster variants 已登记。
- CF2X 已登记为 observation-only aerial candidate，不承担载荷、pickplace 或人员引导。
- T1/T2/T3/T4、两个 generator、T3 `deadline_suspended`、T4 `extension_s` 与 `assistance_window_s` 已登记。
- ground stairs 边 `edge:0002/0008/0010/0017/0031` 已登记为 `stair_capable` witness 候选。
- `robot_validated=false`、`physical_qualified=false`、`paper_score_eligible=false` 保持关闭。

本记录只确认逻辑输入和合同结构；正式发布仍需要 runtime oracle、interaction pose、楼梯路线、机器人能力、人员行为和物理回放证据。
