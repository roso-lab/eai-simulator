# EAI Navigation Product Adapter

This directory contains an independent system-ROS 2 Humble adapter. It converts EAI simulator navigation I/O into the Docker product contract without changing Isaac-side sensor modules and without publishing TF.

## Position in the EAI workspace

This adapter lives in `algorithm/` as one algorithm-package-level integration, peer to `algorithm/nav2`. It consumes only the native EAI navigation I/O (`/<robot>/cloud`, `/<robot>/odometry`, `/<robot>/cmd_vel`) gated by the `navigation_io` attachment — it does not depend on Docker being the consumer. Running the same environment against Nav2 instead of the product container needs only `algorithm/nav2` (`sensor:=auto` resolves the simple_car built-in C01/Mid-360 suite). The Docker product is one possible backend; this adapter is the bridge to that backend's fixed topic/registration contract.

Sim↔product variant mapping (choose consistently per experiment):

| sim side (EAI) | adapter profile | docker `SENSOR_VARIANT` | product TF mount (system_parameters) |
| --- | --- | --- | --- |
| `rmuc2024_simple_car_c01_navigation_io` (built-in C01, default) | `octagon_aio_c01` | `octagon_aio_c01` | lidar_height 0.61459, pitch_writable 0 |
| `rmuc2024_simple_car_mid360_navigation_io` (mid360 attachment) | `mid360` | `mid360` | lidar_height 0.61459, pitch_writable 0.349066 |
| `rmuc2024_carter_c01_navigation_io` | `octagon_aio_c01_carter` | `octagon_aio_c01` | lidar_height 0.444862 (offline yaml override; Isaac mount adds the 0.026 m native lidar-payload x offset) |
| `rmuc2024_carter_lidar_navigation_io` (`lidar` attachment) | `lidar_carter` | `octagon_aio_c01` | lidar_height 0.444862, pitch 0 (`system_parameters.yaml.carter_lidar`) |
| `rmuc2024_carter_orsus_navigation_io` (`orsus` attachment) | `orsus_carter` | `octagon_aio_c01` | lidar_height 0.418, pitch_writable 0.339 (`system_parameters.yaml.carter_orsus`) |

The last two rows are the sensor-generalization scheme: the adapter is written against
EAI's standard navigation I/O (`/{robot}/cloud`, `/{robot}/odometry`, `/{robot}/cmd_vel`),
not against any one sensor. Swapping the sensor (or the host) means swapping one
`(mount height, pitch, frame names)` profile plus one Docker TF variant file — the
product container itself is unchanged. EAI's Orsus and standalone-LiDAR graphs publish
un-prefixed `mapping_init` frames (documented in `algorithm/nav2/tf_bridge.py`), so
`orsus_carter`/`lidar_carter` gate on `cloud: mapping_init`, `odometry: mapping_init`,
child `base_link` (the `ROS2PublishOdometry` bridge default); the adapter frame gate
fails fast with expected-vs-received frames if a provider graph revision deviates.
`./run_sim2sim.sh --robot carter --lidar lidar` and `--lidar orsus` wire the full loop.

Isaac-side loop order is fixed: start (or restart) the docker `nav_loop` container first and wait for `idle`, then the simulator, then this adapter — a simulator restart resets `/clock` to zero and a reused container runs into a `TF_OLD_DATA` storm. The `nav-sim` / `nav-docker` helpers in the sim2sim runbook encode this order.

## Contract

For `nav_hub_id:=nav_hub_1` and `robot_name:=simple_car_1`, the adapter:

| Direction | Topic | Type |
| --- | --- | --- |
| EAI input | `/simple_car_1/cloud` | `sensor_msgs/msg/PointCloud2` |
| EAI input | `/simple_car_1/odometry` | `nav_msgs/msg/Odometry` |
| Product input | `/nav_hub_1/cmd_vel` | `geometry_msgs/msg/Twist` |
| Product output | `/nav_hub_1/tower/mapping/cloud` | `sensor_msgs/msg/PointCloud2` |
| Product output | `/nav_hub_1/tower/mapping/odometry` | `nav_msgs/msg/Odometry` |
| EAI output | `/simple_car_1/cmd_vel` | `geometry_msgs/msg/Twist` |

The product output names are fixed from `nav_hub_id`. The EAI cloud and odometry input topics can be overridden at launch. The velocity relay is intentionally direct and does not arbitrate with another EAI command source.

Internal Isaac cloud and odometry subscriptions use BEST_EFFORT/VOLATILE depth 10 so they remain compatible with sensor-style publishers. Both product cloud and product odometry publishers use RELIABLE/VOLATILE depth 10, matching the proven Gazebo and `/topic_publisher` endpoints and the reliable `nav_loop` terrain-analysis subscription. The cmd_vel input and relay output also remain RELIABLE/VOLATILE depth 10.

## Registration Semantics

Before changing any epoch, buffering a pose, or transforming a cloud, the adapter validates source frame labels against the profile. Defaults require cloud `header.frame_id` `<robot_name>/lidar_link`, odometry `header.frame_id` `<robot_name>/odom`, and odometry `child_frame_id` `<robot_name>/base_footprint`. A mismatch is dropped and logged on its first occurrence and whenever the observed labels change; repeated identical mismatches are suppressed until a valid message recovers that stream. This prevents a pre-registered cloud from being transformed twice and prevents a mislabeled pose from establishing the mapping origin.

Frame expectations cannot be changed by the launch topic overrides. A deployment with a different raw cloud label must set `input_frames.cloud` explicitly in a custom version-1 profile, and may do so only when the point coordinates are still expressed in the calibrated lidar frame used by `T_base_lidar`. `input_frames.odometry` and `input_frames.base_footprint` declare the corresponding internal odometry labels.

```yaml
input_frames:
  cloud: "{robot_name}/lidar_link"
  odometry: "{robot_name}/odom"
  base_footprint: "{robot_name}/base_footprint"
```

`T_base_lidar` is the configured rigid sensor mount. The first accepted base odometry pose establishes a **horizontal-anchor** mapping-init origin: the origin sits at the lidar's birth position, but its rotation is the base's birth orientation — the mount translation is folded into the origin while the mount rotation is deliberately NOT (see `core.py` `RegistrationEngine.add_odometry`). At each cloud stamp, the adapter requires an exact odometry sample or two samples that bracket the stamp within `max_interpolation_gap_sec`. It linearly interpolates translation, uses shortest-path quaternion SLERP for rotation, and computes:

```text
T_odom_lidar(t)  = T_odom_base(t) * T_base_lidar
T_origin         = ( translation(T_odom_base(t0)) + R(T_odom_base(t0)) * translation(T_base_lidar),
                     rotation(T_odom_base(t0)) )
T_mapping_init_lidar(t) = inverse(T_origin) * T_odom_lidar(t)
```

Consequently the birth map is exactly the mount rotation: at `t0` a pitched mount (Mid-360, 20 deg) yields `T = R(pitch)`, so a raw tilted sensor-frame cloud is published level with the ground at `z = -lidar_height` and the base reads level; a zero-pitch mount (C01) yields identity. This is the same convention the Gazebo bridge pins in `gazebo_sim.cpp` (cloud anchor is a pure `-offset_x/-lidar_height` translation with identity rotation; odometry pose is base pose times `R(pitch)`), keeping sim-to-sim equivalence. The earlier origin semantics folded the full mount into the origin (identity birth map); that was a geometry bug fixed for the Mid-360 chain on 2026-09-03 and is covered by the pitched-mount regression tests in `tests/test_core.py`.

Input cloud XYZ values are assumed to be expressed in the lidar frame named by the EAI sensor contract. The adapter decodes numeric PointCloud2 fields with their declared offsets, datatype, row stride, and byte order; removes non-finite XYZI points; supplies `default_intensity` when the configured intensity field is absent; applies `T_mapping_init_lidar(t)` to every point; and emits dense little-endian float32 XYZI in `<nav_hub_id>/mapping_init`. It does not merely rewrite `frame_id`.

Output odometry contains the lidar-link pose in the same mapping-init frame. Twist is shifted from the base origin to the lidar origin and rotated into lidar axes, following the ROS odometry convention that input twist is expressed in the input child frame. Ground-truth EAI covariance arrays are retained as supplied; nonzero covariance consumers should account for the fixed-origin transform separately.

Every accepted input odometry sample is retained in the interpolation buffer, including the 200 Hz SimpleCar Isaac stream. Product odometry publication is rate-limited by `timing.product_odometry_rate_hz`, which defaults to 50 Hz for both profiles. Valid raw cloud periods are measured before product cloud publication is independently rate-limited by `timing.expected_cloud_rate_hz`. Both gates use source message simulation timestamps, publish the first sample of each epoch, and remain anchored to those timestamps so exact and irregular samples do not accumulate scheduling drift. Neither gate uses wall time.

Clouds admitted by the product rate gate that arrive before their upper odometry sample wait in a bounded queue. A backward `/clock`, odometry, or cloud jump greater than the configured threshold clears the odometry buffer, queued clouds, both output-rate gates, and initial origin before establishing a new epoch. The first accepted odometry and cloud in the new epoch are therefore published immediately. Small out-of-order odometry and cloud samples are rejected.

## Ownership Boundaries

The EAI simulator or another external ROS process owns `/clock`. Profiles declare `clock.owner: external` and `clock.adapter_publishes: false`; the node uses simulated time and subscribes to `/clock` only for reset detection. It never creates a clock publisher.

The Docker product owns its external TF tree. This adapter does not import `tf2_ros`, publish `/tf` or `/tf_static`, or broadcast `mapping_init`/lidar transforms. The registered messages are self-contained product inputs.

The `nav:runtime-0.2.10` product is treated as an immutable black box outside its official `SENSOR_VARIANT` and public sensor parameters. At startup and periodically thereafter, ROS graph discovery classifies publisher endpoints by fully qualified node name rather than treating publisher count as activity. The factory topology allowlists exactly `/topic_publisher` on product cloud and odometry because the container bridge pre-advertises those topics without producing messages in the observed inactive state. The default action remains `fail` for every other source, including `/gazebo_sim` or another adapter after the current node's own endpoint is excluded. The allowlist never applies to relayed EAI `cmd_vel`, which remains strict.

The observed shape-two graph reported both the real host `gazebo_sim` and container `/topic_publisher` on `/GSXXXXXXXX/tower/mapping/cloud` and `/GSXXXXXXXX/tower/mapping/odometry` while `ws://127.0.0.1:9089` was unavailable and no bridge messages were observed. This establishes a required simulation precondition: there must be no WebSocket listener or server on port 9089. If 9089 is enabled, the direct-DDS adapter must not run. Fully qualified node-name allowlisting cannot detect later WebSocket activation, so the adapter emits this limitation as a startup warning.

```yaml
duplicate_publishers:
  action: fail
  passive_product_nodes: [/topic_publisher]
  guard_period_sec: 2.0
```

Endpoint multiplicity is preserved even when fully qualified node names are identical. Before the adapter creates its publishers, each guarded output must have zero endpoints with the adapter's own FQN; any existing same-FQN endpoint is a conflict. Periodic checks require exactly one own endpoint on each product output and the strict cmd_vel output. Each configured passive name may contribute at most one endpoint per product topic, so a second `/topic_publisher` endpoint is a conflict rather than being collapsed into the allowed one.

Humble/Fast DDS can transiently report a discovered publisher as `/_NODE_NAMESPACE_UNKNOWN_/_NODE_NAME_UNKNOWN_` while node metadata converges. At startup only, when every conflict has exactly that anonymous identity, the adapter retries discovery for at most one `guard_period_sec`. A known unallowlisted identity fails immediately, an anonymous identity that remains after the deadline fails, and UNKNOWN is never added to the passive allowlist.

The guard uses ROS graph endpoint metadata only. It does not rely on publisher GIDs or per-message activity because the installed Humble `MessageInfo` path does not expose a usable per-message publisher GID. Graph discovery can still briefly race simultaneous startup.

## Profiles

`config/profiles.yaml` is a versioned YAML document loaded with `yaml.safe_load`:

- `octagon_aio_c01`: SimpleCar C01 profile with mount translation `[0.2, 0.0, 0.61459]` metres, roll/pitch/yaw `[0.0, 0.0, 0.0]`, expected cloud rate 30 Hz, product odometry output rate 50 Hz, and strict odometry frames `{robot_name}/odom -> {robot_name}/base_footprint`. The 0.20 m forward offset follows the 2026-07-31 board decision shared with gazebo-sim `lidar_offset_x` (octagon default 0.20) and the basic_components `robot_tf_publisher` octagon fragment: trunk centre line, 0.10 m behind the head front edge (base-frame x = 0.30 − 0.10 = 0.20). The Isaac-side USD mount (`simple_car.usda` / `simple_car.urdf`) carries the same value.
- `octagon_aio_c01_carter`: Carter C01 profile with mount translation `[0.026, 0.0, 0.444862]` — the EAI native lidar payload mount (`lidar_mount_link`/`lidar_offset`), shared with the standalone LiDAR payload, including the 0.026 m forward offset — and strict odometry frames `{robot_name}/odom -> {robot_name}/Orsus_chassis_link`. Carter has no literal `base_footprint` prim; `Orsus_chassis_link` is its zero-transform rigid chassis/payload host. The Docker product TF (system_parameters `lidar_height`) models mounting height only, so the product-internal TF keeps x=0 and the 0.026 m residual is a known product-image deviation (same class as the documented 0.20 m octagon placeholder offset).
- `mid360`: SimpleCar Mid-360 profile with mount translation `[0.0, 0.0, 0.61459]` and the 20 deg forward pitch `0.349066` rad that the docker robot_tf_publisher applies, expected cloud rate 10 Hz, and product odometry output rate 50 Hz.

All mount values remain launch-configurable via `base_to_lidar_xyz` / `base_to_lidar_rpy` for deployments with an independently measured calibration; the SimpleCar defaults above are the board-decided values, not per-deployment guesses.

The raw cloud rate is monitored against `expected_cloud_rate_hz` with a 20 percent warning tolerance. The same value drives deterministic product-output downsampling from source simulation timestamps; the adapter drops excess samples and never fabricates or interpolates cloud frames.

## Docker deployment assets (self-contained)

`docker/` inside this package holds every artifact needed to dock the product
nav container — `nav.env`, `fastdds_udp_only.xml`, `device_info.json`,
`system_parameters.yaml.{c01,mid360,carter_c01}`, and `maps/rmuc2024/` — so the
Isaac × product-Docker loop runs entirely from this repository:

```bash
./algorithm/nav_product_adapter/run_sim2sim.sh --lidar c01   # docker → simulator → adapter, Ctrl+C cleans all
./algorithm/nav_product_adapter/docker/docker_nav.sh status   # container health/state
```

The map PGM is byte-identical to the gazebo_sim package map (sha256 5ce8a6ac…)
and to the Docker-side map verified in the sim2sim certification report.

## Run

Start the selected EAI environment in its Isaac environment, then run this adapter from system ROS:

```bash
conda deactivate
source /opt/ros/humble/setup.bash
ros2 launch algorithm/nav_product_adapter/nav_product_adapter.launch.py \
  profile:=octagon_aio_c01 robot_name:=simple_car_1 nav_hub_id:=nav_hub_1
```

Carter must select its truthful chassis-frame profile:

```bash
ros2 launch algorithm/nav_product_adapter/nav_product_adapter.launch.py \
  profile_name:=octagon_aio_c01_carter robot_name:=carter_1 \
  nav_hub_id:=nav_hub_1
```

`profile_name` is a launch alias that takes precedence over `profile`. Selecting this profile configures only the external Adapter's frame validation and transform contract. It does not change Docker `SENSOR_VARIANT`, public sensor parameters, or product internals.

Override only the EAI input topic names when necessary:

```bash
ros2 launch algorithm/nav_product_adapter/nav_product_adapter.launch.py \
  profile_name:=octagon_aio_c01_carter robot_name:=carter_1 \
  nav_hub_id:=nav_hub_1 cloud_input:=/carter_1/cloud \
  odometry_input:=/carter_1/odometry
```

`EAI_NAV_PRODUCT_ROS_PYTHON` can select another system-ROS Python executable. Direct execution re-execs `/usr/bin/python3` when invoked from Conda unless `EAI_NAV_PRODUCT_NO_REEXEC=1` is set.

## Saved EAI Environment

`rmuc2024_simple_car_c01_navigation_io` is a valid saved environment using the RMUC2024 scene, the idealized planar SimpleCar controller, its built-in OCTAGON-AIO-C01 suite, and `navigation_io`.

`carter_diff_c01_navigation_io` is a reusable plane-scene selection using `CARTER_DIFF_CFG`, the dedicated `octagon_aio_c01` attachment, and `navigation_io`. The current catalog and builder explicitly validate and spawn `OctagonAioC01Cfg` on the Carter mount; this environment does not substitute the generic Carter `RosLidarCfg` for C01. Its external Adapter launch must select `profile_name:=octagon_aio_c01_carter` so strict validation expects the truthful `carter_1/Orsus_chassis_link` odometry child frame.

## Tests

The focused suite is ROS-independent except for a system-Python import check:

```bash
/usr/bin/python3 -m pytest -q algorithm/nav_product_adapter/tests
```

A live ROS graph test still requires sourced Humble, a running EAI simulator, and the Docker product or representative subscribers.
