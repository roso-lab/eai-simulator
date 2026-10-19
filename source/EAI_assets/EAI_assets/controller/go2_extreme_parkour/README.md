# Go2 Extreme Parkour deployment bundle

`deployment.py` is the HeteroCoord adapter for the 753-D Teacher contract:
`53 proprio + 132 height scan + 9 explicit + 29 privileged + 10x53 history`.
It keeps per-robot history and cached scans, requires finite collision hits, and
supports arbitrary batch sizes. `exported/policy_dynamic.onnx` has dynamic batch
axes and is numerically checked against the original `policy.onnx`/TorchScript.

`extreme_parkour_observations.py`, `extreme_parkour_v1_heightfield.py`,
`extreme_parkour_v1_geometry.py`, and `commands.py` are the source-side task
implementation copied from the training project with only local import names
adjusted. `PROVENANCE.json` records original paths and source hashes. The files
live directly in this directory under the requested original names; no patch
reconstruction or untracked training checkout is needed.

## Runtime API

`integration.GO2_EXTREME_PARKOUR_TEACHER_CFG` uses the new
`exported/policy_dynamic.onnx`. The original fixed-batch `policy.onnx`, original
TorchScript and training checkpoint remain unchanged. Loading the old ONNX
path will still select the old fixed-batch file.

Use `robot.make_robot_cfg(usd_path)` to include the torque-speed actuator,
defaults, action scaling and joint limits used by the Teacher. Physics dt=.005,
control dt=.02. SDK action order is FR, FL, RR, RL, each hip/thigh/calf.
The adapter maps these names into the actual USD ordering before applying targets.

The HeteroCoord environment must supply:

- `scene.articulations[robot_name]` and per-robot `_controllers_dict`.
- `scene.sensors[robot_name + '_height_scanner']`: 132 finite collision hits.
- `scene.sensors[robot_name + '_contact_forces']`: two newest force samples,
  feet FR/FL/RR/RL, contact threshold 2 N.
- `get_command(robot_name,'base_velocity')`: `[N,3]`, only forward speed is used.
- `get_command(robot_name,'parkour_waypoint_yaw')`: `[N,2]` current/next heading
  errors in radians; this is NOT angular velocity. Missing command defaults zero.
- `common_step_counter`, advanced once per control step; duplicate observation
  calls within the same step return cached data and do not append history twice.
- `actions_dict[robot_name]`: last clipped action in SDK order.

`integration.sensor_configs` builds Isaac sensor configs. Supply actual terrain
mesh paths; the included example uses a real collision plane. Height rays use
x=-.45..1.20, y=-.75...75, spacing .15, y-inner flattening. Height scan is
`clip(base_z - .3 - hit_z, -1, 1)`, refreshed every five control steps (and first
sample after reset). Indoor/forest scenes may require a lower ray origin or an
actual-geometry scanner to avoid sampling roofs/tree crowns. Missing hits raise
an error rather than silently inventing flat terrain.

## 753-D layout

| Slice | Field |
|---|---|
| 0:3 | body angular velocity × .25 |
| 3:5 | roll and pitch in radians |
| 5:6 | reserved zero |
| 6:8 | current and next waypoint yaw errors, cached every five steps |
| 8:10 | reserved zeros |
| 10:11 | forward velocity command |
| 11:13 | obstacle/flat terrain class |
| 13:25 | SDK-order joint position minus training default |
| 25:37 | SDK-order joint velocity × .05 |
| 37:49 | previous action |
| 49:53 | feet contact indicator minus .5 |
| 53:185 | height scan |
| 185:194 | body linear velocity ×2, then six zeros |
| 194:223 | encoded physical parameters (29) |
| 223:753 | ten prior 53-D proprio frames, oldest first |

History excludes waypoint yaw fields. Initial output contains zero history;
after emitting the observation, episode steps 0/1 fill history with the current
frame, then subsequent steps shift/append. Reset with
`deployment.reset_controller(env, name, env_ids)` on episode/policy changes.

The default scene class is obstacle `[1,0]`, and physical-parameter fields are
zeros, matching the prior deployment assumption. Override them with commands
`parkour_terrain_class [N,2]` and `parkour_privileged [N,29]` if needed. Physical
parameters must use the training encoding in `RecordExtremeParkourPhysicalParameters`;
zeros do not mean measured mass/friction. This remains a privileged Teacher,
not a deployable real-robot vision Student or a global path planner.

## Reproduce

```bash
python tools/locomotion_deployment/play.py --robot go2 --num-envs 2 --steps 250 --device cpu --headless
```

Use `--asset-root /path/to/usd` when assets are external. The example imports only
this submitted bundle and Isaac Lab; no `/home/airs/...` training path is required.
See `tools/locomotion_deployment/README.md` for tests and integration boundaries.

The Teacher is a locomotion policy. It does not include a standing-up or fall
recovery controller.
