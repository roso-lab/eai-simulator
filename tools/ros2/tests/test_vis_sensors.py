from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "vis_sensors.py"


def _load_script(module_name: str = "eai_public_vis_sensors"):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vis_sensors = _load_script()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("/", ""),
        (" /carter_1/ ", "/carter_1"),
        ("//fleet///carter_1//", "/fleet/carter_1"),
    ],
)
def test_normalize_namespace(value, expected):
    assert vis_sensors.normalize_namespace(value) == expected


def test_discover_image_topics_filters_on_namespace_boundaries():
    topics_and_types = [
        ("/carter_1/front", ["sensor_msgs/msg/Image"]),
        ("carter_1/depth", ["sensor_msgs/Image"]),
        ("/carter_1/camera_info", ["sensor_msgs/msg/CameraInfo"]),
        ("/carter_10/front", ["sensor_msgs/msg/Image"]),
        ("/iris_1/front", ["sensor_msgs/msg/Image"]),
    ]

    assert vis_sensors.discover_image_topics(topics_and_types, "//carter_1/") == (
        "/carter_1/depth",
        "/carter_1/front",
    )


def test_realsense_topics_follow_the_normalized_namespace():
    assert vis_sensors.sensor_topics_for_namespace("//mushr_v2_1/", "realsense") == (
        "/mushr_v2_1/RealsenseD455_rgb",
        "/mushr_v2_1/RealsenseD455_depth",
    )


@pytest.mark.parametrize(
    ("sensor", "expected_namespace"),
    [
        ("auto", ""),
        ("camera", ""),
        ("orsus", "/isaac"),
        ("realsense", "/isaac"),
        ("lidar", "/isaac"),
    ],
)
def test_sensor_modes_use_documented_default_namespaces(sensor, expected_namespace):
    assert vis_sensors.parse_args(["--sensor", sensor]).namespace == expected_namespace


def test_namespace_help_lists_every_mode_with_the_legacy_default():
    namespace_action = next(
        action for action in vis_sensors.build_parser()._actions if action.dest == "namespace"
    )

    assert "orsus/realsense/lidar" in namespace_action.help


def test_resize_to_fit_preserves_aspect_ratio_and_maximum_edge():
    image = np.zeros((20, 40, 3), dtype=np.uint8)

    resized = vis_sensors.resize_to_fit(image, 100)

    assert resized.shape == (50, 100, 3)


def test_scale_to_uint8_scales_finite_values_and_blacks_out_non_finite_pixels():
    image = np.array([[1.0, 2.0, np.inf], [np.nan, 3.0, 4.0]], dtype=np.float32)

    scaled = vis_sensors.scale_to_uint8(image)

    assert scaled.dtype == np.uint8
    assert scaled.shape == image.shape
    assert scaled[0, 2] == 0
    assert scaled[1, 0] == 0
    assert scaled[1, 2] > scaled[0, 1]


def test_scale_to_uint8_returns_black_when_no_finite_values_exist():
    image = np.array([[np.nan, np.inf, -np.inf]], dtype=np.float64)

    assert np.array_equal(vis_sensors.scale_to_uint8(image), np.zeros(image.shape, dtype=np.uint8))


def test_pure_helpers_import_without_ros2(monkeypatch):
    for module_name in ("rclpy", "cv_bridge", "sensor_msgs"):
        monkeypatch.setitem(sys.modules, module_name, None)

    module = _load_script("eai_public_vis_sensors_without_ros")

    assert module.normalize_namespace("/iris_1/") == "/iris_1"
    assert callable(module.scale_to_uint8)


def _lifecycle_ros_bundle(events, bridge_type, *, interrupt=False, node_interrupt=False):
    context = {"active": False}

    class Rclpy:
        def init(self, *, args):
            events.append(("init", tuple(args)))
            context["active"] = True

        def spin(self, _node):
            events.append(("spin",))
            if interrupt:
                context["active"] = False
                raise KeyboardInterrupt

        def ok(self):
            return context["active"]

        def try_shutdown(self):
            events.append(("try_shutdown", context["active"]))
            context["active"] = False

        def shutdown(self):
            events.append(("shutdown",))
            if not context["active"]:
                raise RuntimeError("rcl_shutdown already called")
            context["active"] = False

    class Node:
        def __init__(self, name):
            events.append(("node", name))
            if node_interrupt:
                context["active"] = False
                raise KeyboardInterrupt

        def create_subscription(self, *_args):
            events.append(("create_subscription",))
            return object()

        def destroy_node(self):
            events.append(("destroy_node",))

    return Rclpy(), bridge_type, Node, object(), object(), object()


def test_main_shuts_ros_down_when_opencv_loading_fails_after_init(monkeypatch, capsys):
    events = []

    class Bridge:
        pass

    monkeypatch.setattr(vis_sensors, "_load_ros", lambda: _lifecycle_ros_bundle(events, Bridge))

    def fail_cv2():
        events.append(("load_cv2",))
        raise RuntimeError("opencv construction failed")

    monkeypatch.setattr(vis_sensors, "_load_cv2", fail_cv2)

    assert vis_sensors.main(["--sensor", "lidar"]) == 2
    assert events == [("init", ()), ("load_cv2",), ("try_shutdown", True)]
    assert "opencv construction failed" in capsys.readouterr().err


def test_main_destroys_created_node_and_shuts_down_when_visualizer_construction_fails(monkeypatch, capsys):
    events = []

    class FailingBridge:
        def __init__(self):
            events.append(("bridge",))
            raise RuntimeError("bridge construction failed")

    class Cv2:
        def destroyAllWindows(self):
            events.append(("destroy_windows",))

    monkeypatch.setattr(vis_sensors, "_load_ros", lambda: _lifecycle_ros_bundle(events, FailingBridge))
    monkeypatch.setattr(vis_sensors, "_load_cv2", lambda: Cv2())

    assert vis_sensors.main(["--sensor", "lidar"]) == 2
    assert [event[0] for event in events] == [
        "init",
        "node",
        "bridge",
        "destroy_node",
        "try_shutdown",
        "destroy_windows",
    ]
    assert "bridge construction failed" in capsys.readouterr().err


def test_main_handles_ctrl_c_after_default_ros_handler_shuts_context_down(monkeypatch, capsys):
    events = []

    class Bridge:
        pass

    class Cv2:
        def destroyAllWindows(self):
            events.append(("destroy_windows",))

    monkeypatch.setattr(
        vis_sensors,
        "_load_ros",
        lambda: _lifecycle_ros_bundle(events, Bridge, interrupt=True),
    )
    monkeypatch.setattr(vis_sensors, "_load_cv2", lambda: Cv2())

    assert vis_sensors.main(["--sensor", "lidar"]) == 0
    assert ("shutdown",) not in events
    assert ("try_shutdown", False) in events
    assert "cleanup failed" not in capsys.readouterr().err


def test_main_handles_ctrl_c_during_node_construction(monkeypatch, capsys):
    events = []

    class Bridge:
        pass

    class Cv2:
        def destroyAllWindows(self):
            events.append(("destroy_windows",))

    monkeypatch.setattr(
        vis_sensors,
        "_load_ros",
        lambda: _lifecycle_ros_bundle(events, Bridge, node_interrupt=True),
    )
    monkeypatch.setattr(vis_sensors, "_load_cv2", lambda: Cv2())

    assert vis_sensors.main(["--sensor", "lidar"]) == 0
    assert ("shutdown",) not in events
    assert ("try_shutdown", False) in events
    assert "Traceback" not in capsys.readouterr().err


def test_main_handles_ctrl_c_during_visualizer_construction(monkeypatch, capsys):
    events = []

    class InterruptingBridge:
        def __init__(self):
            raise KeyboardInterrupt

    class Cv2:
        def destroyAllWindows(self):
            events.append(("destroy_windows",))

    monkeypatch.setattr(
        vis_sensors,
        "_load_ros",
        lambda: _lifecycle_ros_bundle(events, InterruptingBridge),
    )
    monkeypatch.setattr(vis_sensors, "_load_cv2", lambda: Cv2())

    assert vis_sensors.main(["--sensor", "lidar"]) == 0
    assert ("shutdown",) not in events
    assert ("try_shutdown", True) in events
    assert "Traceback" not in capsys.readouterr().err
