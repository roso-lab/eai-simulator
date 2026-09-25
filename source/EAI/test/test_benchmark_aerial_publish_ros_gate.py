"""Static contract lock for the aerial publish_ros gate (no Isaac).

The ``EAI.hmrs_ros`` package imports ``omni.usd`` transitively, so the gate
semantics are locked structurally instead of by instantiation: the fused
``simulator.py`` caller passes ``publish_ros=`` and the suite manager must
accept it, keep the physical LiDAR creation ungated, and gate every ROS2
graph attachment behind the flag.  This is exactly the mismatch class that
crashed aerial-containing startups after the branch-tool fusion.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "source/EAI/EAI/hmrs_ros/aerial_sensor_suite.py"
SIMULATOR = ROOT / "simulator.py"


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def test_suite_manager_accepts_publish_ros_keyword():
    tree = _parse(SUITE)
    for node in ast.walk(tree):
        if (isinstance(node, ast.ClassDef)
                and node.name == "AerialSensorSuiteManager"):
            init = next(child for child in node.body
                        if isinstance(child, ast.FunctionDef)
                        and child.name == "__init__")
            kws = {arg.arg for arg in init.args.kwonlyargs}
            assert "publish_ros" in kws
            defaults = {arg.arg: default for arg, default in
                        zip(init.args.kwonlyargs, init.args.kw_defaults)
                        if default is not None}
            assert defaults.get("publish_ros")
            return
    raise AssertionError("AerialSensorSuiteManager not found")


def test_setup_gates_ros_graphs_but_not_physical_lidar():
    tree = _parse(SUITE)
    setup = _function(tree, "_setup")
    source = ast.unparse(setup)
    assert "if spec.base_sensors and self._publish_ros" in source
    assert "if spec.camera and self._publish_ros" in source
    assert "if self._publish_ros" in source
    # The physical LiDAR creation call must appear before/outside the
    # publisher gate in the same block.
    assert "create_aerial_lidar_sensor(" in source
    assert "create_aerial_lidar_publisher(robot_name)" in source
    sensor_index = source.index("create_aerial_lidar_sensor(")
    publisher_gate = source.index("if self._publish_ros")
    assert sensor_index < publisher_gate


def test_simulator_caller_passes_publish_ros():
    tree = _parse(SIMULATOR)
    wrapper = _function(tree, "_setup_aerial_sensor_manager")
    wrapper_source = ast.unparse(wrapper)
    kws = {arg.arg for arg in wrapper.args.kwonlyargs}
    assert "publish_ros" in kws
    assert "publish_ros=publish_ros" in wrapper_source
    # The only call site forwards the ROS bridge flag.
    simulator_source = ast.unparse(tree)
    assert "publish_ros=config.enable_ros_bridge_extension" in simulator_source
