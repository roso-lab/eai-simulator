# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause
"""Isaac Sim ROS 2 runtime for the SimpleCar native sensor suite."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from EAI.physics.aerial_sensors import (
    AerialSensorModel,
    AerialSensorModelConfig,
    FirstOrderBiasConfig,
    sanitize_ros_component,
)
from EAI.physics.simple_car_sensors import (
    CAMERA_FOCAL_LENGTH_MM,
    CAMERA_HORIZONTAL_APERTURE_MM,
    CAMERA_HORIZONTAL_FOV_DEG,
    CAMERA_RATE_HZ,
    CAMERA_RESOLUTION,
    CAMERA_VERTICAL_APERTURE_MM,
    CAMERA_VERTICAL_FOV_DEG,
    SIMPLE_CAR_LIDAR_VARIANTS,
    OctagonAioC01SensorSpec,
    OCTAGON_IMU_RATE_HZ,
    simple_car_lidar_mount_pitch_rad,
    simple_car_lidar_profile_name,
    simple_car_lidar_profile_relative_path,
    simple_car_lidar_scan_rate_hz,
    resolve_simple_car_lidar_variant,
)

_ZERO_BIAS = FirstOrderBiasConfig(0.0, 0.0, 1.0, 0.0)
_SIMPLE_CAR_IMU_MODEL_CONFIG = AerialSensorModelConfig(
    gyroscope=_ZERO_BIAS,
    accelerometer=_ZERO_BIAS,
)


def _orientation_ijkl(reading: Any) -> tuple[float, float, float, float]:
    w, x, y, z = reading.orientation_wxyz
    return float(x), float(y), float(z), float(w)


class _IsaacSimpleCarSensorRuntime:
    def __init__(
        self,
        profile_path: str,
        *,
        controller: Any | None = None,
        stage_provider: Any | None = None,
        update_callback: Any | None = None,
        variant: str | None = None,
    ) -> None:
        if controller is None:
            import omni.graph.core as og

            controller = og.Controller
        self._variant = resolve_simple_car_lidar_variant() if variant is None else variant
        if self._variant not in SIMPLE_CAR_LIDAR_VARIANTS:
            raise ValueError(f"Unsupported SimpleCar lidar variant: {self._variant}")
        self._profile_name = simple_car_lidar_profile_name(self._variant)
        self._scan_rate_hz = simple_car_lidar_scan_rate_hz(self._variant)
        self._mount_pitch_deg = math.degrees(simple_car_lidar_mount_pitch_rad(self._variant))
        self._profile_path = Path(profile_path).resolve()
        if not self._profile_path.is_file():
            raise FileNotFoundError(
                f"SimpleCar {self._profile_name} profile is missing: {self._profile_path}"
            )
        document = json.loads(self._profile_path.read_text(encoding="utf-8"))
        profile = document.get("profile") if isinstance(document, dict) else None
        if not isinstance(profile, dict):
            raise ValueError(
                f"Invalid SimpleCar {self._profile_name} profile document: {self._profile_path}"
            )
        self._profile = profile
        self._controller = controller
        self._stage_provider = stage_provider or self._get_stage
        self._update_callback = update_callback or self._update_kit
        self._graphs: dict[str, Any] = {}
        self._graph_paths: dict[str, str] = {}
        self._owned_graph_paths: set[str] = set()
        self._owned_prim_paths: set[str] = set()
        self._render_products: list[Any] = []
        self._writers: list[Any] = []
        self._cameras: list[Any] = []
        self._camera_annotators: dict[str, Any] = {}

    @staticmethod
    def _get_stage() -> Any:
        import omni.usd

        return omni.usd.get_context().get_stage()

    @staticmethod
    def _update_kit() -> None:
        import omni.kit.app

        omni.kit.app.get_app().update()

    def _prim_exists(self, path: str) -> bool:
        prim = self._stage_provider().GetPrimAtPath(path)
        return prim is not None and prim.IsValid()

    def _record_graph(self, key: str, path: str) -> None:
        self._graph_paths[key] = path
        if not self._prim_exists(path):
            self._owned_graph_paths.add(path)

    def _lidar_attributes(self) -> dict[str, Any]:
        profile = self._profile
        states = profile.get("emitterStates")
        if not isinstance(states, list) or len(states) != 1 or not isinstance(states[0], dict):
            raise ValueError(
                f"SimpleCar {self._profile_name} profile must define exactly one emitter state"
            )
        state = states[0]
        number_of_emitters = int(profile["numberOfEmitters"])
        for name in ("azimuthDeg", "elevationDeg", "fireTimeNs"):
            values = state.get(name)
            if not isinstance(values, list) or len(values) != number_of_emitters:
                raise ValueError(
                    f"SimpleCar {self._profile_name} emitter state {name} must contain "
                    f"{number_of_emitters} values"
                )
        market_name = (
            "MID-360" if self._variant == "mid360" else "OCTAGON-AIO-C01"
        )
        vendor = "LIVOX" if self._variant == "mid360" else "OCTAGON"
        return {
            "omni:sensor:modelName": self._profile_name,
            "omni:sensor:marketName": market_name,
            "omni:sensor:modelVendor": vendor,
            "omni:sensor:tickRate": float(profile["scanRateBaseHz"]),
            "omni:sensor:Core:scanType": str(profile["scanType"]).upper(),
            "omni:sensor:Core:intensityProcessing": str(profile["intensityProcessing"]).upper(),
            "omni:sensor:Core:rotationDirection": str(profile["rotationDirection"]).upper(),
            "omni:sensor:Core:rayType": str(profile["rayType"]).upper(),
            "omni:sensor:Core:nearRangeM": float(profile["nearRangeM"]),
            "omni:sensor:Core:minDistBetweenEchosM": float(profile["minDistBetweenEchos"]),
            "omni:sensor:Core:farRangeM": float(profile["farRangeM"]),
            "omni:sensor:Core:validStartAzimuthDeg": float(profile["startAzimuthDeg"]),
            "omni:sensor:Core:validEndAzimuthDeg": float(profile["endAzimuthDeg"]),
            "omni:sensor:Core:rangeResolutionM": float(profile["rangeResolutionM"]),
            "omni:sensor:Core:rangeAccuracyM": float(profile["rangeAccuracyM"]),
            "omni:sensor:Core:avgPowerW": float(profile["avgPowerW"]),
            "omni:sensor:Core:minReflectance": float(profile["minReflectance"]),
            "omni:sensor:Core:minReflectionRangeM": float(profile["minReflectanceRange"]),
            "omni:sensor:Core:waveLengthNm": float(profile["wavelengthNm"]),
            "omni:sensor:Core:pulseTimeNs": int(profile["pulseTimeNs"]),
            "omni:sensor:Core:azimuthErrorMean": float(profile["azimuthErrorMean"]),
            "omni:sensor:Core:azimuthErrorStd": float(profile["azimuthErrorStd"]),
            "omni:sensor:Core:elevationErrorMean": float(profile["elevationErrorMean"]),
            "omni:sensor:Core:elevationErrorStd": float(profile["elevationErrorStd"]),
            "omni:sensor:Core:maxReturns": int(profile["maxReturns"]),
            "omni:sensor:Core:scanRateBaseHz": int(profile["scanRateBaseHz"]),
            "omni:sensor:Core:reportRateBaseHz": int(profile["reportRateBaseHz"]),
            "omni:sensor:Core:numberOfEmitters": number_of_emitters,
            "omni:sensor:Core:numberOfChannels": number_of_emitters,
            "omni:sensor:Core:intensityMappingType": str(profile["intensityMappingType"]).upper(),
            "omni:sensor:Core:intensityScalePercent": 255.0,
            "omni:sensor:Core:auxOutputType": "FULL",
            "omni:sensor:Core:outputFrameOfReference": "SENSOR",
            "omni:sensor:Core:outputMotionCompensationState": "NONCOMPENSATED",
            "omni:sensor:Core:skipDroppingInvalidPoints": True,
            "omni:sensor:Core:emitterState:s001:azimuthDeg": [float(value) for value in state["azimuthDeg"]],
            "omni:sensor:Core:emitterState:s001:elevationDeg": [float(value) for value in state["elevationDeg"]],
            "omni:sensor:Core:emitterState:s001:fireTimeNs": [int(value) for value in state["fireTimeNs"]],
            "omni:sensor:Core:emitterState:s001:channelId": list(range(1, number_of_emitters + 1)),
        }

    def create_camera(self, robot_name: str, side: str, mount_prim_path: str) -> None:
        import omni.graph.core as og
        import omni.kit.app
        import omni.replicator.core as rep
        from isaacsim.sensors.camera import Camera

        camera_prim_path = f"{mount_prim_path.rstrip('/')}/Camera"
        if not self._prim_exists(camera_prim_path):
            self._owned_prim_paths.add(camera_prim_path)
        camera = Camera(
            prim_path=camera_prim_path,
            name=f"{sanitize_ros_component(robot_name)}_{side}_camera",
            translation=np.zeros(3),
            orientation=np.asarray((1.0, 0.0, 0.0, 0.0)),
            frequency=CAMERA_RATE_HZ,
            resolution=CAMERA_RESOLUTION,
        )
        self._cameras.append(camera)
        camera.set_focal_length(CAMERA_FOCAL_LENGTH_MM)
        camera.set_horizontal_aperture(
            CAMERA_HORIZONTAL_APERTURE_MM,
            maintain_square_pixels=False,
        )
        camera.set_vertical_aperture(
            CAMERA_VERTICAL_APERTURE_MM,
            maintain_square_pixels=False,
        )
        camera.set_clipping_range(0.05, 1000.0)
        # set_opencv_pinhole_properties natively aborts Isaac Sim 5.1 headless;
        # CameraInfo is published from the render product aperture instead.
        omni.kit.app.get_app().update()
        camera.initialize(attach_rgb_annotator=False)
        print(f"[SimpleCarCam] {side}: initialized")
        render_product_path = str(camera.get_render_product_path())
        if not render_product_path:
            raise RuntimeError(f"SimpleCar {side} camera render product was not initialized")
        render_product = rep.create.render_product(
            camera_prim_path, resolution=CAMERA_RESOLUTION,
            name=f"{sanitize_ros_component(robot_name)}_{side}_benchmark",
        )
        self._render_products.append(render_product)
        annotator = rep.AnnotatorRegistry.get_annotator("rgb")
        annotator.attach([render_product])
        self._camera_annotators[f"{robot_name}:{side}"] = annotator
        component = sanitize_ros_component(robot_name)
        key = f"{robot_name}:camera:{side}"
        graph_path = f"/World/ROS2_SIMPLE_CAR/{component}_{side}_camera_graph"
        self._record_graph(key, graph_path)
        result = self._controller.edit(
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
                    ("image_publisher.inputs:topicName", f"cameras/{side}/image_raw"),
                    ("image_publisher.inputs:frameId", f"{component}/camera_{side}_optical_frame"),
                    ("image_publisher.inputs:type", "rgb"),
                    ("info_publisher.inputs:renderProductPath", render_product_path),
                    ("info_publisher.inputs:nodeNamespace", f"/{component}"),
                    ("info_publisher.inputs:topicName", f"cameras/{side}/camera_info"),
                    ("info_publisher.inputs:frameId", f"{component}/camera_{side}_optical_frame"),
                ],
            },
        )
        graph = result[0] if isinstance(result, tuple) else result
        self._graphs[key] = graph
        for _ in range(2):
            og.Controller.evaluate_sync(graph)
        omni.kit.app.get_app().update()

    def capture_camera_frame(self, robot_name: str, side: str = "left") -> Any:
        """Return the latest RGB frame from a configured C01 camera."""
        annotator = self._camera_annotators.get(f"{robot_name}:{side}")
        if annotator is None:
            annotator = next(
                (value for key, value in self._camera_annotators.items()
                 if key.startswith(f"{robot_name}:")),
                None,
            )
        if annotator is None:
            return None
        return annotator.get_data()

    def create_navigation(
        self,
        robot_name: str,
        lidar_mount_path: str,
        odometry_source_prim_path: str,
        odometry_child_frame: str,
    ) -> None:
        import carb
        import omni.graph.core as og
        import omni.kit.app
        import omni.replicator.core as rep
        from pxr import Sdf

        settings = carb.settings.get_settings()
        settings.set("/renderer/raytracingMotion/enabled", True)
        if not settings.get_as_bool("/renderer/raytracingMotion/enabled"):
            raise RuntimeError("RTX lidar requires renderer.raytracingMotion.enabled=true")
        app = omni.kit.app.get_app()
        app.get_extension_manager().set_extension_enabled_immediate("isaacsim.sensors.rtx", True)
        app.update()
        component = sanitize_ros_component(robot_name)
        lidar_path = f"{lidar_mount_path.rstrip('/')}/{self._profile_name}"
        if self._prim_exists(lidar_path):
            raise RuntimeError(
                f"SimpleCar {self._profile_name} runtime prim already exists: {lidar_path}"
            )
        self._owned_prim_paths.add(lidar_path)
        sensor = rep.functional.create.omni_lidar(
            name=self._profile_name,
            parent=lidar_mount_path,
            position=(0.0, 0.0, 0.0),
            rotation=(0.0, self._mount_pitch_deg, 0.0),
            **self._lidar_attributes(),
        )
        if isinstance(sensor, (tuple, list)):
            sensor = sensor[0] if sensor else None
        if sensor is None or not sensor.IsValid() or sensor.GetTypeName() != "OmniLidar":
            raise RuntimeError(
                f"Failed to create native {self._profile_name} at {lidar_path}"
            )
        if str(sensor.GetPath()) != lidar_path:
            raise RuntimeError(
                f"SimpleCar {self._profile_name} runtime prim path mismatch: "
                f"expected {lidar_path}, got {sensor.GetPath()}"
            )

        render_product = rep.create.render_product(
            sensor.GetPath(),
            resolution=(128, 128),
            render_vars=["GenericModelOutput", "RtxSensorMetadata"],
            name=f"{component}_octagon",
        )
        self._render_products.append(render_product)
        cloud_rate_hz = float(self._profile["scanRateBaseHz"])
        if not np.isclose(cloud_rate_hz, self._scan_rate_hz):
            raise ValueError(
                "SimpleCar "
                f"{self._profile_name} profile scanRateBaseHz must match the "
                f"{self._scan_rate_hz:g} Hz runtime contract"
            )
        for topic in ("cloud", "velodyne_points"):
            writer = rep.writers.get("RtxLidarROS2PublishPointCloud")
            writer.initialize(
                nodeNamespace=f"/{component}",
                topicName=topic,
                frameId=f"{component}/lidar_link",
            )
            writer.attach([render_product])
            self._writers.append(writer)

        graph_path = f"/World/ROS2_OCTAGON_C01/{component}_navigation_graph"
        self._record_graph(robot_name, graph_path)
        result = self._controller.edit(
            {
                "graph_path": graph_path,
                "evaluator_name": "execution",
                "pipeline_stage": og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_ONDEMAND,
            },
            {
                og.Controller.Keys.CREATE_NODES: [
                    ("physics_step", "isaacsim.core.nodes.OnPhysicsStep"),
                    ("simulation_time", "isaacsim.core.nodes.IsaacReadSimulationTime"),
                    ("ros2_context", "isaacsim.ros2.bridge.ROS2Context"),
                    ("compute_odometry", "isaacsim.core.nodes.IsaacComputeOdometry"),
                    ("odometry_publisher", "isaacsim.ros2.bridge.ROS2PublishOdometry"),
                    ("imu_publisher", "isaacsim.ros2.bridge.ROS2PublishImu"),
                ],
                og.Controller.Keys.CONNECT: [
                    ("physics_step.outputs:step", "compute_odometry.inputs:execIn"),
                    ("compute_odometry.outputs:execOut", "odometry_publisher.inputs:execIn"),
                    ("physics_step.outputs:step", "imu_publisher.inputs:execIn"),
                    ("ros2_context.outputs:context", "odometry_publisher.inputs:context"),
                    ("ros2_context.outputs:context", "imu_publisher.inputs:context"),
                    ("compute_odometry.outputs:position", "odometry_publisher.inputs:position"),
                    ("compute_odometry.outputs:orientation", "odometry_publisher.inputs:orientation"),
                    ("compute_odometry.outputs:linearVelocity", "odometry_publisher.inputs:linearVelocity"),
                    ("compute_odometry.outputs:angularVelocity", "odometry_publisher.inputs:angularVelocity"),
                    ("simulation_time.outputs:simulationTime", "odometry_publisher.inputs:timeStamp"),
                    ("simulation_time.outputs:simulationTime", "imu_publisher.inputs:timeStamp"),
                ],
                og.Controller.Keys.SET_VALUES: [
                    ("odometry_publisher.inputs:nodeNamespace", f"/{component}"),
                    ("odometry_publisher.inputs:topicName", "odometry"),
                    ("odometry_publisher.inputs:odomFrameId", f"{component}/odom"),
                    (
                        "odometry_publisher.inputs:chassisFrameId",
                        odometry_child_frame,
                    ),
                    ("imu_publisher.inputs:nodeNamespace", f"/{component}"),
                    ("imu_publisher.inputs:topicName", "sensors/imu"),
                    ("imu_publisher.inputs:frameId", f"{component}/imu_link"),
                ],
            },
        )
        graph = result[0] if isinstance(result, tuple) else result
        self._graphs[robot_name] = graph
        node = self._stage_provider().GetPrimAtPath(f"{graph_path}/compute_odometry")
        relationship = node.GetRelationship("inputs:chassisPrim")
        if not relationship:
            relationship = node.CreateRelationship("inputs:chassisPrim")
        relationship.SetTargets([Sdf.Path(odometry_source_prim_path)])
        for _ in range(2):
            og.Controller.evaluate_sync(graph)
        app.update()

    def set_imu(self, robot_name: str, values: Mapping[str, Any]) -> None:
        import omni.graph.core as og

        graph_path = self._graph_paths[robot_name]
        for attribute, value in values.items():
            target = og.Controller.attribute(f"{graph_path}/imu_publisher.{attribute}")
            if not target.is_valid():
                raise RuntimeError(f"SimpleCar IMU graph attribute unavailable: {attribute}")
            og.Controller.set(target, value)

    def close(self) -> None:
        graph_paths = tuple(path for path in self._owned_graph_paths)
        prim_paths = tuple(self._owned_prim_paths)
        render_products = tuple(self._render_products)
        writers = tuple(self._writers)
        cameras = tuple(self._cameras)
        annotators = tuple({id(value): value for value in self._camera_annotators.values()}.values())
        self._graphs.clear()
        self._graph_paths.clear()
        self._owned_graph_paths.clear()
        self._owned_prim_paths.clear()
        self._render_products.clear()
        self._writers.clear()
        self._cameras.clear()
        self._camera_annotators.clear()
        for writer in writers:
            try:
                writer.detach()
            except Exception as exc:
                print(f"[SimpleCarSensors] Warning: failed to detach writer: {exc}")
        for annotator in annotators:
            try:
                annotator.detach()
            except Exception as exc:
                print(f"[SimpleCarSensors] Warning: failed to detach camera annotator: {exc}")
        for camera in cameras:
            try:
                camera.destroy()
            except Exception as exc:
                print(f"[SimpleCarSensors] Warning: failed to destroy camera: {exc}")
        for render_product in render_products:
            try:
                destroy = getattr(render_product, "destroy", None)
                if callable(destroy):
                    destroy()
            except Exception as exc:
                print(f"[SimpleCarSensors] Warning: failed to destroy render product: {exc}")
        try:
            stage = self._stage_provider()
            for path in (*graph_paths, *prim_paths):
                prim = stage.GetPrimAtPath(path)
                if prim is not None and prim.IsValid():
                    stage.RemovePrim(path)
        except Exception as exc:
            print(f"[SimpleCarSensors] Warning: failed to remove owned prims: {exc}")
        try:
            self._update_callback()
        except Exception as exc:
            print(f"[SimpleCarSensors] Warning: failed to finalize cleanup: {exc}")


class SimpleCarSensorSuiteManager:
    """Own the built-in SimpleCar suite and generic C01 attachment runtimes.

    Hosts that selected the ``octagon_aio_c01`` attachment resolve their lidar
    mount from the ``OctagonAioC01`` prim the builder spawned on their native
    lidar payload mount; SimpleCar's built-in suite keeps its fixed
    ``lidar_link`` frame contract.
    """

    def __init__(
        self,
        env: Any,
        specs: Sequence[OctagonAioC01SensorSpec],
        *,
        profile_path: str | None = None,
        seed: int = 0,
        runtime: Any | None = None,
        variant: str | None = None,
        stage_provider: Any | None = None,
    ) -> None:
        if variant is None:
            declared = {
                spec.lidar_variant
                for spec in specs
                if getattr(spec, "lidar_variant", None)
            }
            if len(declared) == 1:
                # env JSON attachment 声明（mid360）优先于环境变量回退。
                variant = declared.pop()
        self._variant = resolve_simple_car_lidar_variant() if variant is None else variant
        if self._variant not in SIMPLE_CAR_LIDAR_VARIANTS:
            raise ValueError(f"Unsupported SimpleCar lidar variant: {self._variant}")
        if profile_path is None:
            from EAI_assets.asset_resolver import asset_path

            profile_path = asset_path(
                simple_car_lidar_profile_relative_path(self._variant)
            )
        self._env = env
        if int(getattr(env, "num_envs", 1)) != 1 and any(
            spec.camera or spec.navigation_io for spec in specs
        ):
            raise ValueError(
                "OCTAGON C01 ROS publication supports exactly one simulator environment; "
                "use distinct robot instances for multi-robot namespaces"
            )
        self._specs = {spec.robot_name: spec for spec in specs}
        self._models = {
            spec.robot_name: AerialSensorModel(_SIMPLE_CAR_IMU_MODEL_CONFIG, seed=seed + index)
            for index, spec in enumerate(specs)
            if spec.navigation_io
        }
        self._runtime = runtime or _IsaacSimpleCarSensorRuntime(
            profile_path, variant=self._variant
        )
        self._stage_provider = stage_provider or self._get_stage
        self._registered_robots: tuple[str, ...] = ()
        self._setup()

    @property
    def registered_robots(self) -> tuple[str, ...]:
        return self._registered_robots

    @property
    def requires_rendering(self) -> bool:
        return any(spec.camera or spec.navigation_io for spec in self._specs.values())

    @staticmethod
    def _get_stage() -> Any:
        import omni.usd

        return omni.usd.get_context().get_stage()

    @staticmethod
    def _articulation_scope_path(robot: Any, robot_name: str) -> str:
        view = getattr(robot, "root_physx_view", None)
        prim_paths = tuple(getattr(view, "prim_paths", ()) or ())
        if not prim_paths:
            raise RuntimeError(
                f"C01 host articulation view has no concrete prim path: {robot_name}"
            )
        articulation_path = str(prim_paths[0]).rstrip("/")
        if articulation_path.rsplit("/", 1)[-1] == "base_link":
            return articulation_path.rsplit("/", 1)[0]
        return articulation_path

    @staticmethod
    def _relative_path(scope_path: str, relative_path: str) -> str:
        return f"{scope_path.rstrip('/')}/{relative_path.strip('/')}"

    def _find_spawned_octagon_payload(self, scope_path: str) -> str:
        """Locate the OctagonAioC01 prim the builder spawned under a robot."""
        stage = self._stage_provider()
        prefix = f"{scope_path.rstrip('/')}/"
        matches = [
            str(prim.GetPath())
            for prim in stage.Traverse()
            if prim.GetName() == "OctagonAioC01" and str(prim.GetPath()).startswith(prefix)
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one spawned OctagonAioC01 payload under "
                f"{prefix}, found {len(matches)}: {matches}"
            )
        return matches[0]

    def _resolve_lidar_mount_path(self, scope_path: str, spec) -> str:
        if spec.lidar_mount_relative_path:
            return self._relative_path(scope_path, spec.lidar_mount_relative_path)
        return self._find_spawned_octagon_payload(scope_path)

    def _resolve_odometry_source_path(self, scope_path: str, spec, robot) -> str:
        if spec.odometry_source_relative_path:
            return self._relative_path(scope_path, spec.odometry_source_relative_path)
        # GPU PhysX rejects getArticulationData on the articulation root while
        # stepping; address the articulation's root rigid-body link instead.
        body_names = getattr(getattr(robot, "data", None), "body_names", None)
        if body_names:
            return f"{scope_path.rstrip('/')}/{body_names[0]}"
        return scope_path

    def _setup(self) -> None:
        active: list[str] = []
        try:
            articulations = self._env.scene.articulations
            for robot_name, spec in self._specs.items():
                robot = articulations.get(robot_name)
                if robot is None:
                    continue
                scope_path = self._articulation_scope_path(robot, robot_name)
                if spec.camera:
                    if not (
                        spec.camera_left_mount_relative_path
                        and spec.camera_right_mount_relative_path
                    ):
                        raise RuntimeError(
                            f"C01 host does not define camera mounts: {robot_name}"
                        )
                    self._runtime.create_camera(
                        robot_name,
                        "left",
                        self._relative_path(
                            scope_path, spec.camera_left_mount_relative_path
                        ),
                    )
                    self._runtime.create_camera(
                        robot_name,
                        "right",
                        self._relative_path(
                            scope_path, spec.camera_right_mount_relative_path
                        ),
                    )
                if spec.navigation_io:
                    self._runtime.create_navigation(
                        robot_name,
                        self._resolve_lidar_mount_path(scope_path, spec),
                        self._resolve_odometry_source_path(scope_path, spec, robot),
                        spec.base_frame,
                    )
                active.append(robot_name)
        except Exception:
            self.close()
            raise
        self._registered_robots = tuple(active)
        self._specs = {name: self._specs[name] for name in active}
        self._models = {name: self._models[name] for name in active if name in self._models}
        if active and self.requires_rendering:
            self._env.sim.set_setting("/isaaclab/render/rtx_sensors", True)
        if active:
            print(f"[SimpleCar] lidar variant: {self._variant} (profile {simple_car_lidar_profile_name(self._variant)})")
            print("[OctagonC01] Native sensor runtimes ready for: " + ", ".join(active))

    def update(self, dt: float | None = None) -> None:
        if not self._models or self._runtime is None:
            return
        dt = float(dt if dt is not None else getattr(self._env, "step_dt", 0.02))
        for robot_name, model in self._models.items():
            robot = self._env.scene.articulations.get(robot_name)
            if robot is None:
                continue
            reading = model.sample(
                position_w=np.zeros(3, dtype=np.float64),
                orientation_wxyz=robot.data.root_quat_w[0].detach().cpu().numpy(),
                linear_velocity_w=robot.data.root_lin_vel_w[0].detach().cpu().numpy(),
                angular_velocity_b=robot.data.root_ang_vel_b[0].detach().cpu().numpy(),
                dt=dt,
            )
            self._runtime.set_imu(
                robot_name,
                {
                    "inputs:orientation": _orientation_ijkl(reading),
                    "inputs:angularVelocity": reading.angular_velocity_body,
                    "inputs:linearAcceleration": reading.linear_acceleration_body,
                },
            )

    def reset(self, env_ids: Any | None = None) -> None:
        if env_ids is not None:
            ids = env_ids.detach().cpu().tolist() if hasattr(env_ids, "detach") else list(env_ids)
            if 0 not in ids:
                return
        for model in self._models.values():
            model.reset()

    def capture_camera_frame(self, robot_name: str, side: str = "left") -> Any:
        """Return a live RGB frame from a configured C01 camera."""
        capture = getattr(self._runtime, "capture_camera_frame", None)
        if not callable(capture):
            return None
        return capture(robot_name, side)

    def capture_camera_frame(self, robot_name: str, side: str = "left") -> Any:
        """Return a live RGB frame from a configured C01 camera."""
        capture = getattr(self._runtime, "capture_camera_frame", None)
        if not callable(capture):
            return None
        return capture(robot_name, side)

    def close(self) -> None:
        runtime = self._runtime
        self._runtime = None
        self._models.clear()
        self._specs.clear()
        self._registered_robots = ()
        try:
            if getattr(self._env, "_simple_car_sensor_manager", None) is self:
                delattr(self._env, "_simple_car_sensor_manager")
        except Exception as exc:
            print(f"[SimpleCarSensors] Warning: failed to clear manager reference: {exc}")
        if runtime is not None:
            try:
                runtime.close()
            except Exception as exc:
                print(f"[SimpleCarSensors] Warning: failed to close runtime: {exc}")


def attach_simple_car_sensor_manager(env: Any, manager: SimpleCarSensorSuiteManager) -> None:
    env._simple_car_sensor_manager = manager


def get_simple_car_sensor_manager(env: Any) -> SimpleCarSensorSuiteManager | None:
    return getattr(env, "_simple_car_sensor_manager", None)


OctagonAioC01SensorSuiteManager = SimpleCarSensorSuiteManager
attach_octagon_aio_c01_sensor_manager = attach_simple_car_sensor_manager
get_octagon_aio_c01_sensor_manager = get_simple_car_sensor_manager


__all__ = [
    "SimpleCarSensorSuiteManager",
    "OctagonAioC01SensorSuiteManager",
    "attach_simple_car_sensor_manager",
    "attach_octagon_aio_c01_sensor_manager",
    "get_simple_car_sensor_manager",
    "get_octagon_aio_c01_sensor_manager",
]
