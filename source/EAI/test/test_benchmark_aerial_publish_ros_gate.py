"""Independent-gate semantics for the aerial sensor suite (no Isaac).

``publish_ros`` skips every ROS2 graph attachment while the physical sensor
layer (native RTX LiDAR prim) is still created — mirroring the camera /
navigation_io tool gating used elsewhere in the repository.  This also
regresses the fused ``simulator.py`` caller, which passes
``publish_ros=config.enable_ros_bridge_extension``.
"""

from EAI.hmrs_ros.aerial_sensor_suite import AerialSensorSuiteManager
from EAI.physics.aerial_sensors import AerialSensorRobotSpec


class _RecordingRuntime:
    def __init__(self):
        self.calls: list[str] = []

    def create_sensor_graph(self, robot_name):
        self.calls.append(f"sensor_graph:{robot_name}")

    def create_camera_graph(self, robot_name, camera_prim_path):
        self.calls.append(f"camera_graph:{robot_name}")

    def create_aerial_lidar_sensor(self, robot_name, body_prim_path, offset):
        self.calls.append(f"lidar_sensor:{robot_name}")

    def create_aerial_lidar_publisher(self, robot_name):
        self.calls.append(f"lidar_publisher:{robot_name}")

    def close(self):
        self.calls.append("close")


class _Env:
    def __init__(self, robots):
        self.scene = type("Scene", (), {"articulations": set(robots)})()
        self._aerial_sensor_manager = None


def _spec(robot, **kwargs):
    return AerialSensorRobotSpec(robot_name=robot, robot_type="iris", **kwargs)


def test_publish_ros_true_attaches_all_graphs():
    runtime = _RecordingRuntime()
    env = _Env(["iris_1"])
    manager = AerialSensorSuiteManager(
        env, [_spec("iris_1", lidar=True, base_sensors=True)],
        runtime=runtime, publish_ros=True)
    assert "sensor_graph:iris_1" in runtime.calls
    assert "lidar_sensor:iris_1" in runtime.calls
    assert "lidar_publisher:iris_1" in runtime.calls


def test_publish_ros_false_keeps_physical_sensor_skips_ros_graphs():
    runtime = _RecordingRuntime()
    env = _Env(["iris_1"])
    manager = AerialSensorSuiteManager(
        env, [_spec("iris_1", lidar=True, base_sensors=True)],
        runtime=runtime, publish_ros=False)
    assert "lidar_sensor:iris_1" in runtime.calls
    assert "sensor_graph:iris_1" not in runtime.calls
    assert "lidar_publisher:iris_1" not in runtime.calls
    assert "camera_graph:iris_1" not in runtime.calls


def test_publish_ros_false_skips_camera_graph_without_touching_usd():
    runtime = _RecordingRuntime()
    env = _Env(["iris_1"])
    AerialSensorSuiteManager(
        env, [_spec("iris_1", camera=True)],
        runtime=runtime, publish_ros=False)
    # No omni.usd import path is taken when publishing is disabled.
    assert runtime.calls == []
