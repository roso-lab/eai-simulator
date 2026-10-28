# BT native Isaac integration evidence — 2026-10-28

The earlier ROS-only suite is stored separately at
`/home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/ros_protocol_20261028`.
Its scope and reproduction instructions are in
`/home/airs/hetrocoord/algorithm/system/coordination/bt_capabilities/NATIVE_ADAPTER_REPORT.md`.
Both maintained JSON input files remain in the method's `configs/` directory;
`configs/.gitignore` explicitly exempts them from the repository-wide JSON ignore.

The one robot / one task host binding completed one real Isaac Lab run with the original BT auction and BT execution state:

- Task: `move_carton`
- Robot: `ur5_1`
- Native BT endpoint: `http://172.17.0.2:18765`
- Native algorithm: original `ros_bt_py.assignment_manager.parallel_auction_manager.ParallelAuctionManager`
- BT execution: original `ros_bt_py.nodes.sequence.MemorySequence`
- Isaac interpreter: `/home/airs/miniconda3/envs/env_isaaclab/bin/python`
- Source parameters: task file `algorithm/system/coordination/bt_capabilities/configs/isaac_ur5_task.json`
- Physical host: `tools/algorithm_adapters/isaac_pick_place.py`

The first attempt was intentionally retained under `attempt_closed_cli/`. It correctly failed before Isaac startup because the decision CLI now closes its BT mission in `finally`; the host then attempted to reuse the closed mission. The successful run constructs the BT provider inside the host, keeps it alive through Isaac execution, forwards the measured `ExecutionFeedback`, and closes the provider only after BT feedback is consumed.

## Measured result

- Pick: passed
- Lift: `0.135033 m`
- Place: passed
- Final origin error: `0.000011 m`
- Attach/release transitions: `1 / 1`
- Final BT state: `SUCCEEDED`
- Native feedback: `success`, `ur5_1`, `failure_code=none`
- Physics samples: `5,496`
- Collision overlap steps: `0`
- Minimum separating gap: `0.013436 m`
- Resources/proxy/source collision/dynamics/gravity/filter cleanup: passed
- Evidence scope: `idealized_follow_proxy_pick_place; not contact grasp`
- Benchmark score: unavailable; this is a one robot / one skill integration case.

The result remains bounded simulated physical evidence. It does not establish contact force grasping, general BT DAG support, multi robot behavior, or SADCHER readiness. SADCHER native idle behavior was not changed.

## Task sourced physical parameters

The host validates and uses these values from the task package rather than hardcoded execution values:

```text
object_position_world_m = [0.45, 0.0, 0.475]
place_position_world_m  = [0.425, 0.01, 0.475]
approach_degrees        = 15.0
timeout_s               = 100.0
mode                    = cartesian_pose
```

Inconsistent coordinates, robot pose, target reference, unsupported mode, missing values, nonfinite values, and unequal pick/place support heights fail closed. The BT adapter still receives an explicit single robot bid; no utility or weight fallback is introduced.

## Reproduction

Start or reuse the isolated ROS1 BT endpoint configured for `ur5_1`, then run exactly one Isaac host:

```bash
cd /home/airs/hetrocoord
/home/airs/miniconda3/envs/env_isaaclab/bin/python \
  tools/algorithm_adapters/isaac_pick_place.py \
  --algorithm bt_capabilities \
  --algorithm-python /usr/bin/python3 \
  --source-root /tmp/heterocoord-algorithms-20261028/bt-capabilities \
  --task algorithm/system/coordination/bt_capabilities/configs/isaac_ur5_task.json \
  --config algorithm/system/coordination/bt_capabilities/configs/isaac_ur5_adapter.json \
  --output records/algorithm_adapters/bt_capabilities/native_20261028
```

The final successful artifacts are `integration.json`, `physical_execution.json`, `native_decision.json`, `native_feedback.json`, and `isaac_process.log` in this directory. The initial failed CLI attempt's `native_process.log` remains under `attempt_closed_cli/`; the successful in-process BT provider does not create that subprocess log. The source and task hashes are recorded in `integration.json`; no upstream algorithm source was edited.
