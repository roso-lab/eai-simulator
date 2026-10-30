# Copyright (c) 2026, EAI Simulator contributors.
"""RealSense D455 IMU 发布管理器（与无人机传感器套件同款架构）。

背景：D455 载荷原本内嵌 ``IsaacImuSensor``（isaacsim.sensors.physics），该传感器
通过 omni.physx.tensors 每物理步读取父刚体的速度。当宿主是关节链机器人（mushr、
Pepper 等）且跑 GPU 流水线时，读取关节链刚体会调用
``PxDirectGPUAPI::getArticulationData()``，而该接口在仿真运行期间被 PhysX 禁止，
导致每帧报错直至 ``PhysX has reported too many errors, simulation has been
stopped``。

因此改为与 AerialSensorSuite 一致的做法：载荷资产不再带 IsaacImuSensor；内嵌的
``ROS2_publish_IMU`` 图改为由 on_playback_tick 驱动、数据由本管理器在 env 步进后
用机器人根状态合成并写入发布节点。机器人根（base frame）的 IMU 语义对轮式/腿式
宿主即底盘惯导读数，对带云台相机（如 Pepper Head）的宿主退化为躯干读数。
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from EAI.physics.aerial_sensors import AerialSensorModel, AerialSensorModelConfig

_IMU_GRAPH_SUFFIX = "/Graphs/ROS2_publish_IMU/ros2_publish_imu"
_IMU_INPUTS = (
    "angularVelocity",
    "linearAcceleration",
    "orientation",
)


def _orientation_ijkl(reading: Any):
    """AerialSensorReading 的 orientation 为 wxyz，ROS2PublishImu 使用 IJKR (x,y,z,w)。

    返回 ``Gf.Quatd(w, x, y, z)``：USD 的 ``quatd`` 属性只接受 Gf.Quatd
    （real-first），直接写 4 元组会触发 ``Type mismatch ... expected GfQuatd``。
    """
    from pxr import Gf

    w, x, y, z = reading.orientation_wxyz
    return Gf.Quatd(float(w), float(x), float(y), float(z))


class RealSenseD455ImuManager:
    """按实例向 D455 内嵌 ROS2_publish_IMU 图写入合成 IMU 数据。"""

    def __init__(
        self,
        env: Any,
        instances: Mapping[str, str],
        *,
        seed: int = 0,
    ) -> None:
        # instances: sensor_root_path -> robot_name
        self._env = env
        self._instances: dict[str, str] = {}
        self._models: dict[str, AerialSensorModel] = {}
        self._write_failed: set[str] = set()
        for index, (path, robot_name) in enumerate(dict(instances).items()):
            if not path or not robot_name:
                continue
            self._instances[path] = robot_name
            self._models[path] = AerialSensorModel(AerialSensorModelConfig(), seed=seed + index)

    @property
    def registered_instances(self) -> tuple[str, ...]:
        return tuple(self._instances)

    def _set_values(self, path: str, values: Mapping[str, Any]) -> None:
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        node_prim = stage.GetPrimAtPath(f"{path}{_IMU_GRAPH_SUFFIX}")
        if node_prim is None or not node_prim.IsValid():
            raise RuntimeError(f"ROS 2 IMU publisher prim is unavailable: {path}{_IMU_GRAPH_SUFFIX}")
        for attribute, value in values.items():
            attr = node_prim.GetAttribute(f"inputs:{attribute}")
            if attr is None or not attr.IsValid():
                raise RuntimeError(
                    f"ROS 2 IMU publisher attribute is unavailable: "
                    f"{path}{_IMU_GRAPH_SUFFIX}.inputs:{attribute}"
                )
            attr.Set(value)

    def update(self, dt: float | None = None) -> None:
        if not self._models:
            return
        dt = float(dt if dt is not None else getattr(self._env, "step_dt", 0.02))
        articulations = getattr(getattr(self._env, "scene", None), "articulations", {})
        for path, robot_name in self._instances.items():
            robot = articulations.get(robot_name)
            if robot is None:
                continue
            try:
                root_quat = robot.data.root_quat_w[0].detach().cpu().numpy()
                root_lin_vel = robot.data.root_lin_vel_w[0].detach().cpu().numpy()
                root_ang_vel = robot.data.root_ang_vel_b[0].detach().cpu().numpy()
            except Exception as exc:
                print(f"[RealsenseD455] ⚠️ IMU state unavailable for {robot_name}: {exc}", flush=True)
                continue
            reading = self._models[path].sample(
                position_w=np.zeros(3, dtype=np.float64),
                orientation_wxyz=root_quat.astype(np.float64),
                linear_velocity_w=root_lin_vel.astype(np.float64),
                angular_velocity_b=root_ang_vel.astype(np.float64),
                dt=dt,
            )
            try:
                self._set_values(
                    path,
                    {
                        "angularVelocity": reading.angular_velocity_body,
                        "linearAcceleration": reading.linear_acceleration_body,
                        "orientation": _orientation_ijkl(reading),
                    },
                )
                self._write_failed.discard(path)
            except Exception as exc:
                if path not in self._write_failed:
                    self._write_failed.add(path)
                    print(f"[RealsenseD455] ⚠️ IMU publish write failed for {path}: {exc}", flush=True)
                continue

    def reset(self, env_ids: Any | None = None) -> None:
        for model in self._models.values():
            model.reset()

    def close(self) -> None:
        self._models.clear()
        self._instances.clear()
        self._env = None


def attach_realsense_imu_manager(env: Any, manager: RealSenseD455ImuManager) -> None:
    env._realsense_imu_manager = manager


def get_realsense_imu_manager(env: Any) -> RealSenseD455ImuManager | None:
    return getattr(env, "_realsense_imu_manager", None)


def realsense_d455_instance_registry() -> dict[str, str]:
    """读取 spawn 阶段登记的 D455 实例（sensor_root_path -> robot_name）。"""
    from EAI_assets.sensor.high_sensor.realsense_d455 import (
        _REALSENSE_INSTANCE_REGISTRY,
    )

    return dict(_REALSENSE_INSTANCE_REGISTRY)


__all__ = [
    "RealSenseD455ImuManager",
    "attach_realsense_imu_manager",
    "get_realsense_imu_manager",
    "realsense_d455_instance_registry",
]
