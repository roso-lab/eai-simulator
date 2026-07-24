from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass
class ManipulatorModelSpec:
    model: str
    joint_names: tuple[str, ...]
    ee_body_names: tuple[str, ...]
    gripper_joint_name: str | None = None

    def __post_init__(self) -> None:
        model = sanitize_ros_name_component(self.model).lower()
        joint_names = tuple(str(name).strip() for name in self.joint_names)
        ee_body_names = tuple(str(name).strip() for name in self.ee_body_names)
        if not model:
            raise ValueError("manipulator model cannot be empty")
        if not joint_names or any(not name for name in joint_names):
            raise ValueError("manipulator joint names cannot be empty")
        if len(set(joint_names)) != len(joint_names):
            raise ValueError("manipulator joint names must be unique")
        if not ee_body_names or any(not name for name in ee_body_names):
            raise ValueError("manipulator end-effector body names cannot be empty")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "joint_names", joint_names)
        object.__setattr__(self, "ee_body_names", ee_body_names)
        if self.gripper_joint_name is not None:
            gripper_name = str(self.gripper_joint_name).strip()
            if not gripper_name:
                raise ValueError("gripper joint name cannot be empty")
            object.__setattr__(self, "gripper_joint_name", gripper_name)


@dataclass(frozen=True)
class ManipulatorJointCommand:
    positions: tuple[float, ...]
    sequence: int


@dataclass(frozen=True)
class ManipulatorGripperCommand:
    position: float
    sequence: int


@dataclass(frozen=True)
class ManipulatorPoseCommand:
    frame_id: str
    position: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float] | None
    sequence: int


@dataclass(frozen=True)
class ManipulatorActiveCommand:
    mode: str
    command: ManipulatorJointCommand | ManipulatorPoseCommand


@dataclass(frozen=True)
class ManipulatorGraphSpec:
    robot_name: str
    model: ManipulatorModelSpec
    graph_path: str
    node_names: dict[str, str]
    node_types: dict[str, str]
    topics: dict[str, str]


class ManipulatorCommandState:
    def __init__(self) -> None:
        self._latest_arm: ManipulatorActiveCommand | None = None
        self._latest_gripper: ManipulatorGripperCommand | None = None

    @property
    def latest_arm(self) -> ManipulatorActiveCommand | None:
        return self._latest_arm

    @property
    def latest_gripper(self) -> ManipulatorGripperCommand | None:
        return self._latest_gripper

    def update_arm(
        self,
        mode: str,
        command: ManipulatorJointCommand | ManipulatorPoseCommand | None,
    ) -> None:
        if command is None:
            return
        if mode not in {"joint", "pose"}:
            raise ValueError(f"unsupported manipulator command mode: {mode}")
        current = self._latest_arm
        if current is None or command.sequence >= current.command.sequence:
            self._latest_arm = ManipulatorActiveCommand(mode=mode, command=command)

    def update_gripper(self, command: ManipulatorGripperCommand | None) -> None:
        if command is None:
            return
        current = self._latest_gripper
        if current is None or command.sequence >= current.sequence:
            self._latest_gripper = command

    def clear(self) -> None:
        self._latest_arm = None
        self._latest_gripper = None


def sanitize_ros_name_component(component: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(component).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "robot"
    if cleaned[0].isdigit():
        return f"robot_{cleaned}"
    return cleaned


def manipulator_topic_names(robot_name: str, model: ManipulatorModelSpec | str) -> dict[str, str]:
    model_name = sanitize_ros_name_component(getattr(model, "model", model)).lower()
    namespace = f"/{sanitize_ros_name_component(robot_name)}/{model_name}"
    topics = {
        "target_pose": f"{namespace}/target_pose",
        "joint_command": f"{namespace}/joint_command",
        "joint_states": f"{namespace}/joint_states",
        "ee_pose": f"{namespace}/ee_pose",
    }
    if getattr(model, "gripper_joint_name", None) is not None:
        topics["gripper_command"] = f"{namespace}/gripper_command"
        topics["gripper_state"] = f"{namespace}/gripper_state"
    return topics


def _finite_values(values: Sequence[Any], count: int) -> tuple[float, ...] | None:
    if len(values) != count:
        return None
    parsed: list[float] = []
    try:
        for value in values:
            number = float(value)
            if not math.isfinite(number):
                return None
            parsed.append(number)
    except (TypeError, ValueError):
        return None
    return tuple(parsed)


def _joint_name_key(name: Any) -> str:
    return str(name).strip().replace("\\", "/").rsplit("/", 1)[-1]


def build_joint_command(
    model: ManipulatorModelSpec,
    names: Sequence[Any],
    positions: Sequence[Any],
    *,
    sequence: int,
) -> ManipulatorJointCommand | None:
    parsed = _finite_values(positions, len(model.joint_names))
    if parsed is None:
        return None
    if not names:
        return ManipulatorJointCommand(positions=parsed, sequence=int(sequence))
    if len(names) != len(positions):
        return None
    by_name: dict[str, float] = {}
    for name, position in zip(names, parsed):
        key = _joint_name_key(name)
        if not key or key in by_name:
            return None
        by_name[key] = position
    if any(name not in by_name for name in model.joint_names):
        return None
    return ManipulatorJointCommand(
        positions=tuple(by_name[name] for name in model.joint_names),
        sequence=int(sequence),
    )


def build_gripper_command(
    model: ManipulatorModelSpec,
    names: Sequence[Any],
    positions: Sequence[Any],
    *,
    sequence: int,
) -> ManipulatorGripperCommand | None:
    gripper_name = model.gripper_joint_name
    if gripper_name is None:
        return None
    parsed = _finite_values(positions, 1)
    if parsed is None:
        return None
    if names:
        if len(names) != 1 or _joint_name_key(names[0]) != gripper_name:
            return None
    return ManipulatorGripperCommand(position=parsed[0], sequence=int(sequence))


def normalize_manipulator_frame_id(frame_id: Any) -> str | None:
    normalized = str(frame_id or "").strip().strip("/")
    return normalized if normalized in {"world", "base_link"} else None


def build_pose_command(
    frame_id: Any,
    position: Sequence[Any],
    orientation_xyzw: Sequence[Any],
    *,
    sequence: int,
) -> ManipulatorPoseCommand | None:
    normalized_frame = normalize_manipulator_frame_id(frame_id)
    parsed_position = _finite_values(position, 3)
    parsed_orientation = _finite_values(orientation_xyzw, 4)
    if normalized_frame is None or parsed_position is None or parsed_orientation is None:
        return None
    norm = math.sqrt(sum(value * value for value in parsed_orientation))
    orientation: tuple[float, float, float, float] | None
    if norm <= 1e-12:
        orientation = None
    else:
        orientation = tuple(value / norm for value in parsed_orientation)
    return ManipulatorPoseCommand(
        frame_id=normalized_frame,
        position=(parsed_position[0], parsed_position[1], parsed_position[2]),
        orientation_xyzw=orientation,
        sequence=int(sequence),
    )


def build_manipulator_graph_spec(robot_name: str, model: ManipulatorModelSpec) -> ManipulatorGraphSpec:
    robot_component = sanitize_ros_name_component(robot_name)
    component = f"{robot_component}_{model.model}"
    node_names = {
        "tick": "on_playback_tick",
        "context": "ros2_context",
        "pose_subscriber": "target_pose_subscriber",
        "joint_subscriber": "joint_command_subscriber",
        "joint_publisher": "joint_states_publisher",
        "pose_publisher": "ee_pose_publisher",
    }
    node_types = {
        "tick": "omni.graph.action.OnPlaybackTick",
        "context": "isaacsim.ros2.bridge.ROS2Context",
        "pose_subscriber": "isaacsim.ros2.bridge.ROS2Subscriber",
        "joint_subscriber": "isaacsim.ros2.bridge.ROS2SubscribeJointState",
        "joint_publisher": "isaacsim.ros2.bridge.ROS2Publisher",
        "pose_publisher": "isaacsim.ros2.bridge.ROS2Publisher",
    }
    if model.gripper_joint_name is not None:
        node_names.update(
            {
                "gripper_subscriber": "gripper_command_subscriber",
                "gripper_publisher": "gripper_state_publisher",
            }
        )
        node_types.update(
            {
                "gripper_subscriber": "isaacsim.ros2.bridge.ROS2SubscribeJointState",
                "gripper_publisher": "isaacsim.ros2.bridge.ROS2Publisher",
            }
        )
    return ManipulatorGraphSpec(
        robot_name=str(robot_name),
        model=model,
        graph_path=f"/World/ROS2_MANIPULATOR/{component}_graph",
        node_names=node_names,
        node_types=node_types,
        topics=manipulator_topic_names(robot_name, model),
    )


class _IsaacManipulatorGraphRuntime:
    def __init__(self, controller: Any | None = None) -> None:
        if controller is None:
            import omni.graph.core as og

            controller = og.Controller
        self._controller = controller
        self._specs: dict[tuple[str, str], ManipulatorGraphSpec] = {}
        self._graphs: dict[tuple[str, str], Any] = {}

    def create_or_reuse_graph(
        self,
        key: tuple[str, str],
        spec: ManipulatorGraphSpec,
    ) -> None:
        import omni.graph.core as og

        keys = og.Controller.Keys
        node_names = spec.node_names
        dynamic_nodes = tuple(node_names)
        subscribers = ["pose_subscriber", "joint_subscriber"]
        publishers = ["joint_publisher", "pose_publisher"]
        if "gripper_subscriber" in node_names:
            subscribers.append("gripper_subscriber")
            publishers.append("gripper_publisher")
        graph_result = self._controller.edit(
            {"graph_path": spec.graph_path, "evaluator_name": "execution"},
            {
                keys.CREATE_NODES: [(node_names[name], spec.node_types[name]) for name in dynamic_nodes],
                keys.CONNECT: [
                    (f"{node_names['tick']}.outputs:tick", f"{node_names[name]}.inputs:execIn")
                    for name in subscribers + publishers
                ]
                + [
                    (f"{node_names['context']}.outputs:context", f"{node_names[name]}.inputs:context")
                    for name in subscribers + publishers
                ],
                keys.SET_VALUES: self._graph_values(spec),
            },
        )
        self._specs[key] = spec
        graph = graph_result[0] if isinstance(graph_result, tuple) else graph_result
        self._graphs[key] = graph
        for _ in range(2):
            og.Controller.evaluate_sync(graph)

    @staticmethod
    def _graph_values(spec: ManipulatorGraphSpec) -> list[tuple[str, Any]]:
        names = spec.node_names
        topics = spec.topics
        values = [
            (f"{names['pose_subscriber']}.inputs:topicName", topics["target_pose"]),
            (f"{names['pose_subscriber']}.inputs:messagePackage", "geometry_msgs"),
            (f"{names['pose_subscriber']}.inputs:messageSubfolder", "msg"),
            (f"{names['pose_subscriber']}.inputs:messageName", "PoseStamped"),
            (f"{names['joint_subscriber']}.inputs:topicName", topics["joint_command"]),
            (f"{names['joint_publisher']}.inputs:topicName", topics["joint_states"]),
            (f"{names['joint_publisher']}.inputs:messagePackage", "sensor_msgs"),
            (f"{names['joint_publisher']}.inputs:messageSubfolder", "msg"),
            (f"{names['joint_publisher']}.inputs:messageName", "JointState"),
            (f"{names['pose_publisher']}.inputs:topicName", topics["ee_pose"]),
            (f"{names['pose_publisher']}.inputs:messagePackage", "geometry_msgs"),
            (f"{names['pose_publisher']}.inputs:messageSubfolder", "msg"),
            (f"{names['pose_publisher']}.inputs:messageName", "PoseStamped"),
        ]
        if "gripper_subscriber" in names:
            values.extend(
                [
                    (f"{names['gripper_subscriber']}.inputs:topicName", topics["gripper_command"]),
                    (f"{names['gripper_publisher']}.inputs:topicName", topics["gripper_state"]),
                    (f"{names['gripper_publisher']}.inputs:messagePackage", "sensor_msgs"),
                    (f"{names['gripper_publisher']}.inputs:messageSubfolder", "msg"),
                    (f"{names['gripper_publisher']}.inputs:messageName", "JointState"),
                ]
            )
        return values

    def evaluate(self, key: tuple[str, str]) -> None:
        import omni.graph.core as og

        graph = self._graphs.get(key)
        if graph is not None:
            og.Controller.evaluate_sync(graph)

    def get(self, key: tuple[str, str], node_key: str, attribute: str, default: Any = None) -> Any:
        import omni.graph.core as og

        spec = self._specs.get(key)
        if spec is None:
            return default
        node_path = f"{spec.graph_path}/{spec.node_names[node_key]}"
        try:
            graph_attribute = og.Controller.attribute(f"{node_path}.{attribute}")
            value = og.Controller.get(graph_attribute)
        except Exception:
            return default
        return default if value is None else value

    def set(self, key: tuple[str, str], node_key: str, attribute: str, value: Any) -> bool:
        import omni.graph.core as og

        spec = self._specs.get(key)
        if spec is None:
            return False
        node_path = f"{spec.graph_path}/{spec.node_names[node_key]}"
        try:
            graph_attribute = og.Controller.attribute(f"{node_path}.{attribute}")
            og.Controller.set(graph_attribute, value)
        except Exception:
            return False
        return True

    def close(self) -> None:
        self._specs.clear()
        self._graphs.clear()


class ManipulatorOmniGraphManager:
    def __init__(self, runtime: Any | None = None) -> None:
        self._runtime = runtime
        self._models: dict[tuple[str, str], ManipulatorModelSpec] = {}
        self._states: dict[tuple[str, str], ManipulatorCommandState] = {}
        self._signatures: dict[tuple[tuple[str, str], str], tuple[Any, ...]] = {}
        self._sequence = 0

    @property
    def registered_instances(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._states)

    def setup_robot(self, robot_name: str, model: ManipulatorModelSpec) -> bool:
        key = (str(robot_name), model.model)
        try:
            if self._runtime is None:
                self._runtime = _IsaacManipulatorGraphRuntime()
            self._runtime.create_or_reuse_graph(key, build_manipulator_graph_spec(robot_name, model))
        except Exception as exc:
            print(f"[ManipulatorROS2] Failed to create graph for {robot_name}/{model.model}: {exc}")
            return False
        self._models[key] = model
        self._states.setdefault(key, ManipulatorCommandState())
        print(f"[ManipulatorROS2] Enabled: /{sanitize_ros_name_component(robot_name)}/{model.model}/*")
        return True

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if hasattr(value, "tolist"):
            value = value.tolist()
        return list(value) if isinstance(value, (list, tuple)) else [value]

    def _poll_pose(self, key: tuple[str, str], state: ManipulatorCommandState) -> None:
        runtime = self._runtime
        frame_id = runtime.get(key, "pose_subscriber", "outputs:header:frame_id", "")
        position = [runtime.get(key, "pose_subscriber", f"outputs:pose:position:{axis}", 0.0) for axis in "xyz"]
        orientation = [
            runtime.get(key, "pose_subscriber", f"outputs:pose:orientation:{axis}", 0.0) for axis in "xyzw"
        ]
        stamp = (
            runtime.get(key, "pose_subscriber", "outputs:header:stamp:sec", 0),
            runtime.get(key, "pose_subscriber", "outputs:header:stamp:nanosec", 0),
        )
        signature = (str(frame_id), *position, *orientation, *stamp)
        signature_key = (key, "pose")
        if signature == self._signatures.get(signature_key):
            return
        command = build_pose_command(frame_id, position, orientation, sequence=self._next_sequence())
        state.update_arm("pose", command)
        if command is not None:
            self._signatures[signature_key] = signature

    def _poll_joint(self, key: tuple[str, str], state: ManipulatorCommandState) -> None:
        runtime = self._runtime
        model = self._models[key]
        names = self._as_list(runtime.get(key, "joint_subscriber", "outputs:jointNames", []))
        positions = self._as_list(runtime.get(key, "joint_subscriber", "outputs:positionCommand", []))
        if not positions:
            return
        timestamp = runtime.get(key, "joint_subscriber", "outputs:timeStamp", 0.0)
        signature = (*[str(name) for name in names], *positions, timestamp)
        signature_key = (key, "joint")
        if signature == self._signatures.get(signature_key):
            return
        command = build_joint_command(model, names, positions, sequence=self._next_sequence())
        state.update_arm("joint", command)
        if command is not None:
            self._signatures[signature_key] = signature

    def _poll_gripper(self, key: tuple[str, str], state: ManipulatorCommandState) -> None:
        model = self._models[key]
        if model.gripper_joint_name is None:
            return
        runtime = self._runtime
        names = self._as_list(runtime.get(key, "gripper_subscriber", "outputs:jointNames", []))
        positions = self._as_list(runtime.get(key, "gripper_subscriber", "outputs:positionCommand", []))
        if not positions:
            return
        timestamp = runtime.get(key, "gripper_subscriber", "outputs:timeStamp", 0.0)
        signature = (*[str(name) for name in names], *positions, timestamp)
        signature_key = (key, "gripper")
        if signature == self._signatures.get(signature_key):
            return
        command = build_gripper_command(model, names, positions, sequence=self._next_sequence())
        state.update_gripper(command)
        if command is not None:
            self._signatures[signature_key] = signature

    def latest_command(self, robot_name: str, model: str) -> ManipulatorActiveCommand | None:
        key = (str(robot_name), sanitize_ros_name_component(model).lower())
        state = self._states.get(key)
        if state is None or self._runtime is None:
            return None
        self._runtime.evaluate(key)
        self._poll_pose(key, state)
        self._poll_joint(key, state)
        return state.latest_arm

    def latest_gripper_command(self, robot_name: str, model: str) -> ManipulatorGripperCommand | None:
        key = (str(robot_name), sanitize_ros_name_component(model).lower())
        state = self._states.get(key)
        if state is None or self._runtime is None:
            return None
        self._runtime.evaluate(key)
        self._poll_gripper(key, state)
        return state.latest_gripper

    def publish_state(
        self,
        robot_name: str,
        model: str,
        *,
        joint_positions: Sequence[Any],
        joint_velocities: Sequence[Any],
        ee_position_w: Sequence[Any],
        ee_orientation_xyzw: Sequence[Any],
        gripper_position: float | None = None,
        gripper_velocity: float | None = None,
        timestamp: float | None = None,
    ) -> None:
        key = (str(robot_name), sanitize_ros_name_component(model).lower())
        spec = self._models.get(key)
        if self._runtime is None or spec is None:
            return
        parsed_joint_positions = _finite_values(joint_positions, len(spec.joint_names))
        parsed_joint_velocities = _finite_values(joint_velocities, len(spec.joint_names))
        parsed_ee_position = _finite_values(ee_position_w, 3)
        parsed_ee_orientation = _finite_values(ee_orientation_xyzw, 4)
        if any(
            values is None
            for values in (
                parsed_joint_positions,
                parsed_joint_velocities,
                parsed_ee_position,
                parsed_ee_orientation,
            )
        ):
            return
        stamp = time.time() if timestamp is None else float(timestamp)
        if not math.isfinite(stamp):
            return
        parsed_gripper_position = None
        parsed_gripper_velocity = None
        if spec.gripper_joint_name is not None and gripper_position is not None:
            parsed_gripper_position = _finite_values([gripper_position], 1)
            parsed_gripper_velocity = _finite_values(
                [0.0 if gripper_velocity is None else gripper_velocity],
                1,
            )
            if parsed_gripper_position is None or parsed_gripper_velocity is None:
                return
        sec = int(stamp)
        nanosec = int((stamp - sec) * 1_000_000_000)
        joint_values = {
            "inputs:header:stamp:sec": sec,
            "inputs:header:stamp:nanosec": nanosec,
            "inputs:header:frame_id": "",
            "inputs:name": list(spec.joint_names),
            "inputs:position": list(parsed_joint_positions),
            "inputs:velocity": list(parsed_joint_velocities),
            "inputs:effort": [0.0] * len(spec.joint_names),
        }
        for attribute, value in joint_values.items():
            self._runtime.set(key, "joint_publisher", attribute, value)
        pose_values = {
            "inputs:header:stamp:sec": sec,
            "inputs:header:stamp:nanosec": nanosec,
            "inputs:header:frame_id": "world",
            "inputs:pose:position:x": parsed_ee_position[0],
            "inputs:pose:position:y": parsed_ee_position[1],
            "inputs:pose:position:z": parsed_ee_position[2],
            "inputs:pose:orientation:x": parsed_ee_orientation[0],
            "inputs:pose:orientation:y": parsed_ee_orientation[1],
            "inputs:pose:orientation:z": parsed_ee_orientation[2],
            "inputs:pose:orientation:w": parsed_ee_orientation[3],
        }
        for attribute, value in pose_values.items():
            self._runtime.set(key, "pose_publisher", attribute, value)
        if parsed_gripper_position is not None and parsed_gripper_velocity is not None:
            gripper_values = {
                "inputs:header:stamp:sec": sec,
                "inputs:header:stamp:nanosec": nanosec,
                "inputs:header:frame_id": "",
                "inputs:name": [spec.gripper_joint_name],
                "inputs:position": [parsed_gripper_position[0]],
                "inputs:velocity": [parsed_gripper_velocity[0]],
                "inputs:effort": [0.0],
            }
            for attribute, value in gripper_values.items():
                self._runtime.set(key, "gripper_publisher", attribute, value)
        self._runtime.evaluate(key)

    def clear(self, instances: Iterable[tuple[str, str]] | None = None) -> None:
        selected = tuple(self._states) if instances is None else tuple(instances)
        for key in selected:
            state = self._states.get(key)
            if state is not None:
                state.clear()
            for mode in ("pose", "joint", "gripper"):
                self._signatures.pop((key, mode), None)

    def close(self) -> None:
        self._models.clear()
        self._states.clear()
        self._signatures.clear()
        if self._runtime is not None:
            self._runtime.close()
        self._runtime = None


def attach_manipulator_graph_manager(env: Any, manager: ManipulatorOmniGraphManager) -> None:
    env._manipulator_ros2_manager = manager


def get_manipulator_graph_manager(env: Any) -> ManipulatorOmniGraphManager | None:
    return getattr(env, "_manipulator_ros2_manager", None)


__all__ = [
    "ManipulatorActiveCommand",
    "ManipulatorCommandState",
    "ManipulatorGripperCommand",
    "ManipulatorGraphSpec",
    "ManipulatorJointCommand",
    "ManipulatorModelSpec",
    "ManipulatorOmniGraphManager",
    "ManipulatorPoseCommand",
    "build_gripper_command",
    "build_joint_command",
    "build_manipulator_graph_spec",
    "build_pose_command",
    "attach_manipulator_graph_manager",
    "get_manipulator_graph_manager",
    "manipulator_topic_names",
    "normalize_manipulator_frame_id",
    "sanitize_ros_name_component",
]
