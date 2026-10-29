# Copyright (c) 2023, Marcelo Fialho Jacinto.
# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause

"""Physics helpers shared by EAI controllers."""

from .aerial_sensors import (
    AerialSensorModel,
    AerialSensorModelConfig,
    AerialSensorReading,
    AerialSensorRobotSpec,
    FirstOrderBiasConfig,
    PEGASUS_AERIAL_TYPES,
    aerial_sensor_specs_from_selection,
    aerial_sensor_topic_names,
    selection_requires_aerial_camera,
)
from .robot_collision import (
    install_robot_robot_collision_filter,
    robot_collision_filter_pairs,
    robot_entity_roots,
)
from .aerial_pose_reader import LivePhysxPoseReader, flush_physx_transforms, read_aerial_pose

__all__ = [
    "AerialSensorModel",
    "AerialSensorModelConfig",
    "AerialSensorReading",
    "AerialSensorRobotSpec",
    "FirstOrderBiasConfig",
    "PEGASUS_AERIAL_TYPES",
    "aerial_sensor_specs_from_selection",
    "aerial_sensor_topic_names",
    "selection_requires_aerial_camera",
    "install_robot_robot_collision_filter",
    "robot_collision_filter_pairs",
    "robot_entity_roots",
    "LivePhysxPoseReader",
    "flush_physx_transforms",
    "read_aerial_pose",
]
