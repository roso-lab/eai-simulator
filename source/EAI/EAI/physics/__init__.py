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
]
