# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause

"""Isaac Sim ROS 2 publishers for the built-in aerial sensor suite."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from EAI.physics.aerial_sensors import (
    AerialSensorModel,
    AerialSensorModelConfig,
    AerialSensorReading,
    AerialSensorRobotSpec,
    AERIAL_SENSOR_TYPES,
    FirstOrderBiasConfig,
    PEGASUS_AERIAL_TYPES,
    aerial_sensor_specs_from_selection,
    aerial_sensor_topic_names,
    quaternion_wxyz_to_matrix,
    sanitize_ros_component,
    selection_requires_aerial_camera,
)


class _IsaacAerialSensorGraphRuntime:
    PEGASUS_LIDAR_CONFIG = "Example_Rotary"

    def __init__(
        self,
        *,
        controller: Any | None = None,
        stage_provider: Any | None = None,
        update_callback: Any | None = None,
    ) -> None:
        if controller is None:
            import omni.graph.core as og

            controller = og.Controller

        self._controller = controller
        self._stage_provider = stage_provider or self._get_stage
        self._update_callback = update_callback or self._update_kit
        self._graphs: dict[str, Any] = {}
        self._graph_paths: dict[str, str] = {}
        self._owned_graph_paths: set[str] = set()
        self._render_products: list[Any] = []
        self._lidar_sensors: dict[str, Any] = {}
        self._lidar_prim_paths: set[str] = set()
        self._lidar_writers: dict[str, Any] = {}

    @staticmethod
    def _get_stage() -> Any:
        import omni.usd

        return omni.usd.get_context().get_stage()

    @staticmethod
    def _update_kit() -> None:
        import omni.kit.app

        omni.kit.app.get_app().update()

    def _prim_exists(self, prim_path: str) -> bool:
        try:
            prim = self._stage_provider().GetPrimAtPath(prim_path)
            return prim is not None and prim.IsValid()
        except Exception:
            # If ownership cannot be established, preserve the prim during
            # cleanup instead of risking removal of an external resource.
            return True

    def create_sensor_graph(self, robot_name: str) -> None:
        import omni.graph.core as og

        component = sanitize_ros_component(robot_name)
        graph_path = f"/World/ROS2_AERIAL_SENSORS/{component}_graph"
        names = {
            "impulse": "publish_impulse",
            "context": "ros2_context",
            "imu": "imu_publisher",
            "gps": "gps_publisher",
            "magnetometer": "magnetometer_publisher",
            "barometer": "barometer_publisher",
        }
        publishers = ("imu", "gps", "magnetometer", "barometer")
        # Record ownership before edit(): a failed edit can still leave a USD
        # graph prim behind.
        self._graph_paths[robot_name] = graph_path
        if not self._prim_exists(graph_path):
            self._owned_graph_paths.add(graph_path)
        graph_result = self._controller.edit(
            {"graph_path": graph_path, "evaluator_name": "execution"},
            {
                og.Controller.Keys.CREATE_NODES: [
                    (names["impulse"], "omni.graph.action.OnImpulseEvent"),
                    (names["context"], "isaacsim.ros2.bridge.ROS2Context"),
                    (names["imu"], "isaacsim.ros2.bridge.ROS2PublishImu"),
                    (names["gps"], "isaacsim.ros2.bridge.ROS2Publisher"),
                    (names["magnetometer"], "isaacsim.ros2.bridge.ROS2Publisher"),
                    (names["barometer"], "isaacsim.ros2.bridge.ROS2Publisher"),
                ],
                og.Controller.Keys.CONNECT: [
                    (f"{names['impulse']}.outputs:execOut", f"{names[name]}.inputs:execIn")
                    for name in publishers
                ]
                + [
                    (f"{names['context']}.outputs:context", f"{names[name]}.inputs:context")
                    for name in publishers
                ],
                og.Controller.Keys.SET_VALUES: [
                    (f"{names['imu']}.inputs:nodeNamespace", f"/{component}"),
                    (f"{names['imu']}.inputs:topicName", "sensors/imu"),
                    (f"{names['imu']}.inputs:frameId", f"{component}/base_link"),
                    *self._generic_publisher_config(names["gps"], "NavSatFix", "sensors/gps", component),
                    *self._generic_publisher_config(
                        names["magnetometer"], "MagneticField", "sensors/mag", component
                    ),
                    *self._generic_publisher_config(
                        names["barometer"], "FluidPressure", "sensors/barometer", component
                    ),
                ],
            },
        )
        graph = graph_result[0] if isinstance(graph_result, tuple) else graph_result
        self._graphs[robot_name] = graph
        for _ in range(2):
            og.Controller.evaluate_sync(graph)

    @staticmethod
    def _generic_publisher_config(
        node_name: str,
        message_name: str,
        topic_name: str,
        namespace: str,
    ) -> list[tuple[str, Any]]:
        return [
            (f"{node_name}.inputs:messagePackage", "sensor_msgs"),
            (f"{node_name}.inputs:messageSubfolder", "msg"),
            (f"{node_name}.inputs:messageName", message_name),
            (f"{node_name}.inputs:nodeNamespace", f"/{namespace}"),
            (f"{node_name}.inputs:topicName", topic_name),
        ]

    def create_camera_graph(self, robot_name: str, camera_prim_path: str) -> None:
        import omni.graph.core as og
        import omni.replicator.core as rep

        render_product = rep.create.render_product(camera_prim_path, resolution=(640, 480))
        render_product_path = str(getattr(render_product, "path", render_product))
        self._render_products.append(render_product)

        component = sanitize_ros_component(robot_name)
        key = f"{robot_name}:camera"
        graph_path = f"/World/ROS2_AERIAL_SENSORS/{component}_camera_graph"
        self._graph_paths[key] = graph_path
        if not self._prim_exists(graph_path):
            self._owned_graph_paths.add(graph_path)
        graph_result = self._controller.edit(
            {"graph_path": graph_path, "evaluator_name": "execution"},
            {
                og.Controller.Keys.CREATE_NODES: [
                    ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
                    ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
                    ("image_publisher", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                    ("info_publisher", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
                ],
                og.Controller.Keys.CONNECT: [
                    ("on_playback_tick.outputs:tick", "image_publisher.inputs:execIn"),
                    ("on_playback_tick.outputs:tick", "info_publisher.inputs:execIn"),
                    ("ros2_context.outputs:context", "image_publisher.inputs:context"),
                    ("ros2_context.outputs:context", "info_publisher.inputs:context"),
                ],
                og.Controller.Keys.SET_VALUES: [
                    ("image_publisher.inputs:renderProductPath", render_product_path),
                    ("image_publisher.inputs:nodeNamespace", f"/{component}"),
                    ("image_publisher.inputs:topicName", "camera/image_raw"),
                    ("image_publisher.inputs:frameId", f"{component}/camera_optical_frame"),
                    ("image_publisher.inputs:type", "rgb"),
                    ("info_publisher.inputs:renderProductPath", render_product_path),
                    ("info_publisher.inputs:nodeNamespace", f"/{component}"),
                    ("info_publisher.inputs:topicName", "camera/camera_info"),
                    ("info_publisher.inputs:frameId", f"{component}/camera_optical_frame"),
                ],
            },
        )
        graph = graph_result[0] if isinstance(graph_result, tuple) else graph_result
        self._graphs[key] = graph
        for _ in range(2):
            og.Controller.evaluate_sync(graph)
        # Camera helpers queue writer attachment on the next Kit update.
        import omni.kit.app

        omni.kit.app.get_app().update()

    def create_aerial_lidar_sensor(
        self,
        robot_name: str,
        body_prim_path: str,
        translation: tuple[float, float, float],
    ) -> None:
        """Create the default Pegasus RTX LiDAR without attaching a ROS writer."""
        import omni.kit.app
        import omni.kit.commands
        from pxr import Gf

        extension_manager = omni.kit.app.get_app().get_extension_manager()
        extension_manager.set_extension_enabled_immediate("isaacsim.sensors.rtx", True)
        omni.kit.app.get_app().update()

        lidar_prim_path = f"{body_prim_path.rstrip('/')}/lidar"
        # The command can create the prim before reporting an error, so retain
        # its deterministic path for rollback before executing it. Do not take
        # ownership of an identically named prim that predates this runtime.
        prim_existed = self._prim_exists(lidar_prim_path)
        if not prim_existed:
            self._lidar_prim_paths.add(lidar_prim_path)
        result = omni.kit.commands.execute(
            "IsaacSensorCreateRtxLidar",
            path="lidar",
            parent=body_prim_path,
            config=self.PEGASUS_LIDAR_CONFIG,
            translation=translation,
            orientation=Gf.Quatd(1.0, 0.0, 0.0, 0.0),
        )
        sensor = result[1] if isinstance(result, tuple) else result
        if sensor is None or not sensor.IsValid():
            raise RuntimeError(
                f"Failed to create Pegasus RTX LiDAR under {body_prim_path} "
                f"with configuration {self.PEGASUS_LIDAR_CONFIG}."
            )
        self._lidar_sensors[robot_name] = sensor
        sensor_prim_path = str(sensor.GetPath())
        if not prim_existed or sensor_prim_path != lidar_prim_path:
            self._lidar_prim_paths.add(sensor_prim_path)

        print(
            f"[AerialSensors] RTX LiDAR {self.PEGASUS_LIDAR_CONFIG} "
            f"created at {sensor.GetPath()}"
        )

    def create_aerial_lidar_publisher(self, robot_name: str) -> None:
        """Attach the ROS PointCloud2 writer to an existing aerial LiDAR."""
        import omni.kit.app
        import omni.replicator.core as rep

        sensor = self._lidar_sensors.get(robot_name)
        if sensor is None or not sensor.IsValid():
            raise RuntimeError(f"RTX LiDAR is unavailable for ROS publication: {robot_name}")

        component = sanitize_ros_component(robot_name)
        render_product = rep.create.render_product(
            sensor.GetPath(),
            resolution=(128, 128),
            render_vars=["GenericModelOutput", "RtxSensorMetadata"],
            name=f"{component}_lidar",
        )
        self._render_products.append(render_product)

        pointcloud_writer = rep.writers.get("RtxLidarROS2PublishPointCloud")
        self._lidar_writers[robot_name] = pointcloud_writer
        pointcloud_writer.initialize(
            nodeNamespace=f"/{component}",
            topicName="lidar/pointcloud",
            frameId=f"{component}/lidar",
        )
        pointcloud_writer.attach([render_product])

        # Replicator performs writer attachment on the next Kit update.
        omni.kit.app.get_app().update()

    def create_aerial_lidar(
        self,
        robot_name: str,
        body_prim_path: str,
        translation: tuple[float, float, float],
    ) -> None:
        """Compatibility wrapper that creates the LiDAR and its ROS writer."""
        self.create_aerial_lidar_sensor(robot_name, body_prim_path, translation)
        self.create_aerial_lidar_publisher(robot_name)

    def create_pegasus_lidar(
        self,
        robot_name: str,
        body_prim_path: str,
        translation: tuple[float, float, float] = (0.0, 0.0, 0.10),
    ) -> None:
        """Compatibility wrapper for the original Pegasus-only runtime API."""
        self.create_aerial_lidar(robot_name, body_prim_path, translation)

    def set(self, robot_name: str, node_name: str, attribute: str, value: Any) -> None:
        import omni.graph.core as og

        graph_path = self._graph_paths[robot_name]
        graph_attribute = og.Controller.attribute(f"{graph_path}/{node_name}.{attribute}")
        if not graph_attribute.is_valid():
            raise RuntimeError(
                f"ROS 2 publisher attribute is unavailable: {graph_path}/{node_name}.{attribute}"
            )
        og.Controller.set(graph_attribute, value)

    def evaluate(self, robot_name: str) -> None:
        import omni.graph.core as og

        graph_path = self._graph_paths[robot_name]
        impulse = og.Controller.attribute(f"{graph_path}/publish_impulse.state:enableImpulse")
        og.Controller.set(impulse, True)
        og.Controller.evaluate_sync(self._graphs[robot_name])

    def close(self) -> None:
        graph_paths = tuple(
            dict.fromkeys(
                path
                for path in self._graph_paths.values()
                if path in self._owned_graph_paths
            )
        )
        lidar_prim_paths = tuple(self._lidar_prim_paths)
        writers = tuple(self._lidar_writers.values())
        render_products = tuple(self._render_products)

        # Clear ownership before invoking third-party cleanup. This makes close
        # safe to call again, including from exception rollback paths.
        self._graphs.clear()
        self._graph_paths.clear()
        self._owned_graph_paths.clear()
        self._lidar_sensors.clear()
        self._lidar_prim_paths.clear()
        self._lidar_writers.clear()
        self._render_products.clear()

        has_resources = bool(graph_paths or lidar_prim_paths or writers or render_products)
        if not has_resources:
            return

        for writer in writers:
            detach = getattr(writer, "detach", None)
            if not callable(detach):
                continue
            try:
                detach()
            except Exception as exc:
                print(f"[AerialSensors] Warning: Failed to detach RTX LiDAR writer: {exc}")

        for render_product in render_products:
            destroy = getattr(render_product, "destroy", None)
            if not callable(destroy):
                continue
            try:
                destroy()
            except Exception as exc:
                print(f"[AerialSensors] Warning: Failed to destroy render product: {exc}")

        try:
            stage = self._stage_provider()
        except Exception as exc:
            print(f"[AerialSensors] Warning: Failed to access stage during cleanup: {exc}")
            stage = None
        if stage is not None:
            for prim_path in (*graph_paths, *lidar_prim_paths):
                try:
                    prim = stage.GetPrimAtPath(prim_path)
                    is_valid = getattr(prim, "IsValid", None)
                    if prim is not None and (not callable(is_valid) or is_valid()):
                        stage.RemovePrim(prim_path)
                except Exception as exc:
                    print(
                        f"[AerialSensors] Warning: Failed to remove prim {prim_path}: {exc}"
                    )

        try:
            # Writer detach and camera-helper teardown are finalized by Kit on
            # the next update. A single update is sufficient for this batch.
            self._update_callback()
        except Exception as exc:
            print(f"[AerialSensors] Warning: Failed to finalize sensor cleanup: {exc}")


class AerialSensorSuiteManager:
    def __init__(
        self,
        env: Any,
        specs: Sequence[AerialSensorRobotSpec],
        *,
        seed: int = 0,
        runtime: Any | None = None,
    ) -> None:
        self._env = env
        self._specs = {spec.robot_name: spec for spec in specs}
        self._models = {
            spec.robot_name: AerialSensorModel(seed=seed + index)
            for index, spec in enumerate(specs)
        }
        self._registered_robots: tuple[str, ...] = ()
        self._runtime = runtime or _IsaacAerialSensorGraphRuntime()
        self._setup()

    @property
    def registered_robots(self) -> tuple[str, ...]:
        return self._registered_robots

    @property
    def requires_rendering(self) -> bool:
        return bool(self._specs)

    def _setup(self) -> None:
        active: list[str] = []
        try:
            for robot_name, spec in self._specs.items():
                if robot_name not in self._env.scene.articulations:
                    continue
                if spec.base_sensors:
                    self._runtime.create_sensor_graph(robot_name)
                if spec.camera:
                    camera_prim_path = (
                        f"/World/envs/env_0/{robot_name}/{spec.camera_mount_link}/Camera"
                    )
                    import omni.usd

                    stage = omni.usd.get_context().get_stage()
                    camera_prim = stage.GetPrimAtPath(camera_prim_path)
                    if camera_prim is not None and camera_prim.IsValid():
                        self._runtime.create_camera_graph(robot_name, camera_prim_path)
                    else:
                        print(
                            f"[AerialSensors] ⚠️ Built-in camera prim missing for "
                            f"{robot_name} ({camera_prim_path}); skipping camera graph."
                        )
                # Only aerial robots carry the native RTX LiDAR; MuSHR uses the
                # optional RosLidarCfg payload instead.
                if spec.robot_type in AERIAL_SENSOR_TYPES:
                    body_prim_path = f"/World/envs/env_0/{robot_name}/body"
                    self._runtime.create_aerial_lidar_sensor(
                        robot_name,
                        body_prim_path,
                        spec.lidar_offset,
                    )
                    if spec.lidar:
                        self._runtime.create_aerial_lidar_publisher(robot_name)
                active.append(robot_name)
        except Exception:
            self.close()
            raise
        self._models = {
            name: model for name, model in self._models.items() if name in active
        }
        self._specs = {name: self._specs[name] for name in active}
        self._registered_robots = tuple(active)
        if active:
            if self.requires_rendering:
                self._env.sim.set_setting("/isaaclab/render/rtx_sensors", True)
            print("[AerialSensors] Default sensor suites ready for: " + ", ".join(active))

    def update(self, dt: float | None = None) -> None:
        if not self._models:
            return
        dt = float(dt if dt is not None else getattr(self._env, "step_dt", 0.02))
        timestamp = float(getattr(self._env, "common_step_counter", 0)) * dt
        env_origins = getattr(self._env.scene, "env_origins", None)
        for robot_name, model in self._models.items():
            if not self._specs[robot_name].base_sensors:
                continue
            robot = self._env.scene.articulations[robot_name]
            position = robot.data.root_pos_w[0].detach().cpu().numpy()
            if env_origins is not None:
                position = position - env_origins[0].detach().cpu().numpy()
            reading = model.sample(
                position_w=position,
                orientation_wxyz=robot.data.root_quat_w[0].detach().cpu().numpy(),
                linear_velocity_w=robot.data.root_lin_vel_w[0].detach().cpu().numpy(),
                angular_velocity_b=robot.data.root_ang_vel_b[0].detach().cpu().numpy(),
                dt=dt,
            )
            self._publish(robot_name, reading, timestamp)

    def _publish(self, robot_name: str, reading: AerialSensorReading, timestamp: float) -> None:
        w, x, y, z = reading.orientation_wxyz
        self._set_values(
            robot_name,
            "imu_publisher",
            {
                # ROS2PublishImu declares its quaternion as IJKR.
                "inputs:orientation": (x, y, z, w),
                "inputs:angularVelocity": reading.angular_velocity_body,
                "inputs:linearAcceleration": reading.linear_acceleration_body,
                "inputs:timeStamp": timestamp,
            },
        )
        sec, nanosec = _split_timestamp(timestamp)
        header = {
            "inputs:header:stamp:sec": sec,
            "inputs:header:stamp:nanosec": nanosec,
        }
        gps_noise = self._models[robot_name].config.gps_position_noise_std_m
        self._set_values(
            robot_name,
            "gps_publisher",
            {
                **header,
                "inputs:header:frame_id": "map",
                "inputs:status:status": 0,
                "inputs:status:service": 1,
                "inputs:latitude": reading.latitude_deg,
                "inputs:longitude": reading.longitude_deg,
                "inputs:altitude": reading.altitude_m,
                "inputs:position_covariance": [
                    gps_noise[0] ** 2,
                    0.0,
                    0.0,
                    0.0,
                    gps_noise[1] ** 2,
                    0.0,
                    0.0,
                    0.0,
                    gps_noise[2] ** 2,
                ],
                "inputs:position_covariance_type": 2,
            },
        )
        mag_x, mag_y, mag_z = reading.magnetic_field_body_t
        frame_id = f"{sanitize_ros_component(robot_name)}/base_link"
        self._set_values(
            robot_name,
            "magnetometer_publisher",
            {
                **header,
                "inputs:header:frame_id": frame_id,
                "inputs:magnetic_field:x": mag_x,
                "inputs:magnetic_field:y": mag_y,
                "inputs:magnetic_field:z": mag_z,
                "inputs:magnetic_field_covariance": [0.0] * 9,
            },
        )
        self._set_values(
            robot_name,
            "barometer_publisher",
            {
                **header,
                "inputs:header:frame_id": frame_id,
                "inputs:fluid_pressure": reading.absolute_pressure_pa,
                "inputs:variance": reading.pressure_variance,
            },
        )
        self._runtime.evaluate(robot_name)

    def _set_values(self, robot_name: str, node_name: str, values: Mapping[str, Any]) -> None:
        for attribute, value in values.items():
            self._runtime.set(robot_name, node_name, attribute, value)

    def reset(self, env_ids: Any | None = None) -> None:
        if env_ids is not None:
            ids = env_ids.detach().cpu().tolist() if hasattr(env_ids, "detach") else list(env_ids)
            if 0 not in ids:
                return
        for model in self._models.values():
            model.reset()

    def close(self) -> None:
        runtime = self._runtime
        self._runtime = None
        self._models.clear()
        self._specs.clear()
        self._registered_robots = ()
        try:
            if getattr(self._env, "_aerial_sensor_manager", None) is self:
                delattr(self._env, "_aerial_sensor_manager")
        except Exception as exc:
            print(f"[AerialSensors] Warning: Failed to clear manager reference: {exc}")
        if runtime is not None:
            try:
                runtime.close()
            except Exception as exc:
                # Preserve the original setup error when close() is running as
                # constructor rollback, while still making this method idempotent.
                print(f"[AerialSensors] Warning: Failed to close sensor runtime: {exc}")


def attach_aerial_sensor_manager(env: Any, manager: AerialSensorSuiteManager) -> None:
    env._aerial_sensor_manager = manager


def get_aerial_sensor_manager(env: Any) -> AerialSensorSuiteManager | None:
    return getattr(env, "_aerial_sensor_manager", None)


def _split_timestamp(timestamp: float) -> tuple[int, int]:
    seconds = max(float(timestamp), 0.0)
    sec = int(seconds)
    nanosec = int(round((seconds - sec) * 1_000_000_000))
    if nanosec >= 1_000_000_000:
        sec += 1
        nanosec -= 1_000_000_000
    return sec, nanosec


__all__ = [
    "AERIAL_SENSOR_TYPES",
    "AerialSensorModel",
    "AerialSensorModelConfig",
    "AerialSensorReading",
    "AerialSensorRobotSpec",
    "AerialSensorSuiteManager",
    "FirstOrderBiasConfig",
    "PEGASUS_AERIAL_TYPES",
    "aerial_sensor_specs_from_selection",
    "aerial_sensor_topic_names",
    "attach_aerial_sensor_manager",
    "get_aerial_sensor_manager",
    "quaternion_wxyz_to_matrix",
    "selection_requires_aerial_camera",
]
