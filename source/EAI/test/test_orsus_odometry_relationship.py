from __future__ import annotations

from pathlib import Path


ORSUS_SOURCE = Path("source/EAI_assets/EAI_assets/sensor/high_sensor/orsus.py")


def test_orsus_odometry_binds_chassis_as_relationship_target():
    source = ORSUS_SOURCE.read_text(encoding="utf-8")
    helper = source[source.index("def _bind_orsus_odometry_chassis"):source.index("def setup_pending_orsus_ros_graphs")]
    setup = source[source.index("def setup_pending_orsus_ros_graphs"):source.index("def close_orsus_ros_resources")]

    assert 'GetRelationship("inputs:chassisPrim")' in helper
    assert 'if not relationship.SetTargets([Sdf.Path(chassis_prim_path)]):' in helper
    assert '_bind_orsus_odometry_chassis(stage, graph_path, chassis_prim_path)' in setup
    assert '"isaac_compute_odometry_node.inputs:chassisPrim"' not in setup
    assert '("ros2_publish_odometry.inputs:topicName", "odometry")' in setup
    assert 'for prim_path in (graph_path, lidar_prim_path):' in setup
    assert 'stage.RemovePrim(prim_path)' in setup
