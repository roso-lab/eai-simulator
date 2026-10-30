from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

UR5_JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)


@dataclass
class Ur5ModelSpec:
    model: str
    joint_names: tuple[str, ...]
    ee_body_names: tuple[str, ...]
    gripper_joint_name: str | None = None


UR5_MODEL_SPEC = Ur5ModelSpec(
    model="ur5",
    joint_names=UR5_JOINT_NAMES,
    ee_body_names=("wrist_3_link", "ee_link", "tool0"),
)


@dataclass(frozen=True)
class Ur5JointCommand:
    positions: tuple[float, float, float, float, float, float]
    sequence: int


@dataclass(frozen=True)
class Ur5PoseCommand:
    frame_id: str
    position: tuple[float, float, float]
    orientation_xyzw: tuple[float, float, float, float]
    sequence: int


@dataclass(frozen=True)
class Ur5ActiveCommand:
    mode: str
    command: Ur5JointCommand | Ur5PoseCommand


@dataclass(frozen=True)
class Ur5GraphSpec:
    robot_name: str
    graph_path: str
    node_names: dict[str, str]
    node_types: dict[str, str]
    topics: dict[str, str]


def sanitize_ros_name_component(component: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(component).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "robot"
    if cleaned[0].isdigit():
        return f"robot_{cleaned}"
    return cleaned


def ur5_topic_names(robot_name: str) -> dict[str, str]:
    namespace = f"/{sanitize_ros_name_component(robot_name)}/ur5"
    return {
        "target_pose": f"{namespace}/target_pose",
        "joint_command": f"{namespace}/joint_command",
        "joint_states": f"{namespace}/joint_states",
        "ee_pose": f"{namespace}/ee_pose",
    }


def _finite_values(values: Sequence[Any], count: int) -> tuple[float, ...] | None:
    if len(values) < count:
        return None
    parsed: list[float] = []
    try:
        for value in values[:count]:
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
    names: Sequence[Any],
    positions: Sequence[Any],
    *,
    sequence: int,
) -> Ur5JointCommand | None:
    parsed = _finite_values(positions, 6)
    if parsed is None:
        return None
    if not names:
        return Ur5JointCommand(positions=parsed, sequence=int(sequence))
    if len(names) != len(positions):
        return None
    by_name: dict[str, float] = {}
    try:
        for name, position in zip(names, positions):
            key = _joint_name_key(name)
            if not key or key in by_name:
                return None
            number = float(position)
            if not math.isfinite(number):
                return None
            by_name[key] = number
    except (TypeError, ValueError):
        return None
    if any(name not in by_name for name in UR5_JOINT_NAMES):
        return None
    ordered = tuple(by_name[name] for name in UR5_JOINT_NAMES)
    return Ur5JointCommand(positions=ordered, sequence=int(sequence))


def normalize_ur5_frame_id(frame_id: Any) -> str | None:
    normalized = str(frame_id or "").strip().strip("/")
    if not normalized or normalized == "world":
        return "world"
    if normalized == "base_link" or normalized.endswith("/base_link"):
        return "base_link"
    return None


def build_pose_command(
    frame_id: Any,
    position: Sequence[Any],
    orientation_xyzw: Sequence[Any],
    *,
    sequence: int,
) -> Ur5PoseCommand | None:
    parsed_position = _finite_values(position, 3)
    parsed_orientation = _finite_values(orientation_xyzw, 4)
    normalized_frame = normalize_ur5_frame_id(frame_id)
    if parsed_position is None or parsed_orientation is None or normalized_frame is None:
        return None
    orientation_norm = math.sqrt(sum(value * value for value in parsed_orientation))
    if orientation_norm <= 1.0e-9:
        return None
    normalized_orientation = tuple(value / orientation_norm for value in parsed_orientation)
    return Ur5PoseCommand(
        frame_id=normalized_frame,
        position=parsed_position,
        orientation_xyzw=normalized_orientation,
        sequence=int(sequence),
    )


class Ur5CommandState:
    def __init__(self) -> None:
        self._latest: Ur5ActiveCommand | None = None

    def accept_pose(self, command: Ur5PoseCommand | None) -> bool:
        return self._accept("pose", command)

    def accept_joint(self, command: Ur5JointCommand | None) -> bool:
        return self._accept("joint", command)

    def _accept(self, mode: str, command: Ur5JointCommand | Ur5PoseCommand | None) -> bool:
        if command is None:
            return False
        if self._latest is not None and command.sequence <= self._latest.command.sequence:
            return False
        self._latest = Ur5ActiveCommand(mode=mode, command=command)
        return True

    def latest(self) -> Ur5ActiveCommand | None:
        return self._latest

    def clear(self) -> None:
        self._latest = None


def build_ur5_graph_spec(robot_name: str) -> Ur5GraphSpec:
    component = sanitize_ros_name_component(robot_name)
    return Ur5GraphSpec(
        robot_name=str(robot_name),
        graph_path=f"/World/ROS2_UR5/{component}_graph",
        node_names={
            "tick": "on_playback_tick",
            "context": "ros2_context",
            "pose_subscriber": "target_pose_subscriber",
            "joint_subscriber": "joint_command_subscriber",
            "joint_publisher": "joint_states_publisher",
            "pose_publisher": "ee_pose_publisher",
        },
        node_types={
            "tick": "omni.graph.action.OnPlaybackTick",
            "context": "isaacsim.ros2.bridge.ROS2Context",
            "pose_subscriber": "isaacsim.ros2.bridge.ROS2Subscriber",
            "joint_subscriber": "isaacsim.ros2.bridge.ROS2SubscribeJointState",
            "joint_publisher": "isaacsim.ros2.bridge.ROS2Publisher",
            "pose_publisher": "isaacsim.ros2.bridge.ROS2Publisher",
        },
        topics=ur5_topic_names(robot_name),
    )


class _IsaacUr5GraphRuntime:
    def __init__(self, controller: Any | None = None) -> None:
        if controller is None:
            import omni.graph.core as og

            controller = og.Controller
        self._controller = controller
        self._specs: dict[str, Ur5GraphSpec] = {}
        self._graphs: dict[str, Any] = {}

    def create_or_reuse_graph(self, spec: Ur5GraphSpec) -> None:
        import omni.graph.core as og

        keys = og.Controller.Keys
        node_names = spec.node_names
        graph_result = self._controller.edit(
            {"graph_path": spec.graph_path, "evaluator_name": "execution"},
            {
                keys.CREATE_NODES: [
                    (node_names[key], spec.node_types[key])
                    for key in (
                        "tick",
                        "context",
                        "pose_subscriber",
                        "joint_subscriber",
                        "joint_publisher",
                        "pose_publisher",
                    )
                ],
                keys.CONNECT: [
                    (f"{node_names['tick']}.outputs:tick", f"{node_names[key]}.inputs:execIn")
                    for key in ("pose_subscriber", "joint_subscriber", "joint_publisher", "pose_publisher")
                ]
                + [
                    (f"{node_names['context']}.outputs:context", f"{node_names[key]}.inputs:context")
                    for key in ("pose_subscriber", "joint_subscriber", "joint_publisher", "pose_publisher")
                ],
                keys.SET_VALUES: [
                    (f"{node_names['pose_subscriber']}.inputs:topicName", spec.topics["target_pose"]),
                    (f"{node_names['pose_subscriber']}.inputs:messagePackage", "geometry_msgs"),
                    (f"{node_names['pose_subscriber']}.inputs:messageSubfolder", "msg"),
                    (f"{node_names['pose_subscriber']}.inputs:messageName", "PoseStamped"),
                    (f"{node_names['joint_subscriber']}.inputs:topicName", spec.topics["joint_command"]),
                    (f"{node_names['joint_publisher']}.inputs:topicName", spec.topics["joint_states"]),
                    (f"{node_names['joint_publisher']}.inputs:messagePackage", "sensor_msgs"),
                    (f"{node_names['joint_publisher']}.inputs:messageSubfolder", "msg"),
                    (f"{node_names['joint_publisher']}.inputs:messageName", "JointState"),
                    (f"{node_names['pose_publisher']}.inputs:topicName", spec.topics["ee_pose"]),
                    (f"{node_names['pose_publisher']}.inputs:messagePackage", "geometry_msgs"),
                    (f"{node_names['pose_publisher']}.inputs:messageSubfolder", "msg"),
                    (f"{node_names['pose_publisher']}.inputs:messageName", "PoseStamped"),
                ],
            },
        )
        self._specs[spec.robot_name] = spec
        graph = graph_result[0] if isinstance(graph_result, tuple) else graph_result
        self._graphs[spec.robot_name] = graph
        for _ in range(2):
            og.Controller.evaluate_sync(graph)

    def evaluate(self, robot_name: str) -> None:
        import omni.graph.core as og

        graph = self._graphs.get(robot_name)
        if graph is not None:
            og.Controller.evaluate_sync(graph)

    def get(self, robot_name: str, node_key: str, attribute: str, default: Any = None) -> Any:
        import omni.graph.core as og

        spec = self._specs.get(robot_name)
        if spec is None:
            return default
        node_path = f"{spec.graph_path}/{spec.node_names[node_key]}"
        try:
            graph_attribute = og.Controller.attribute(f"{node_path}.{attribute}")
            value = og.Controller.get(graph_attribute)
        except Exception:
            return default
        return default if value is None else value

    def set(self, robot_name: str, node_key: str, attribute: str, value: Any) -> bool:
        import omni.graph.core as og

        spec = self._specs.get(robot_name)
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


class Ur5OmniGraphManager:
    def __init__(self, runtime: Any | None = None) -> None:
        self._runtime = runtime
        self._states: dict[str, Ur5CommandState] = {}
        self._signatures: dict[tuple[str, str], tuple[Any, ...]] = {}
        self._sequence = 0

    @property
    def robot_names(self) -> tuple[str, ...]:
        return tuple(self._states)

    def setup_robot(self, robot_name: str) -> bool:
        try:
            if self._runtime is None:
                self._runtime = _IsaacUr5GraphRuntime()
            self._runtime.create_or_reuse_graph(build_ur5_graph_spec(robot_name))
        except Exception as exc:
            print(f"[UR5ROS2] Failed to create graph for {robot_name}: {exc}")
            return False
        self._states.setdefault(robot_name, Ur5CommandState())
        print(f"[UR5ROS2] Enabled: /{sanitize_ros_name_component(robot_name)}/ur5/*")
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
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def _poll_pose(self, robot_name: str, state: Ur5CommandState) -> None:
        runtime = self._runtime
        frame_id = runtime.get(robot_name, "pose_subscriber", "outputs:header:frame_id", "")
        position = [
            runtime.get(robot_name, "pose_subscriber", f"outputs:pose:position:{axis}", 0.0)
            for axis in "xyz"
        ]
        orientation = [
            runtime.get(robot_name, "pose_subscriber", f"outputs:pose:orientation:{axis}", 0.0)
            for axis in "xyzw"
        ]
        stamp = (
            runtime.get(robot_name, "pose_subscriber", "outputs:header:stamp:sec", 0),
            runtime.get(robot_name, "pose_subscriber", "outputs:header:stamp:nanosec", 0),
        )
        signature = (str(frame_id), *position, *orientation, *stamp)
        key = (robot_name, "pose")
        if signature == self._signatures.get(key):
            return
        command = build_pose_command(frame_id, position, orientation, sequence=self._next_sequence())
        if state.accept_pose(command):
            self._signatures[key] = signature

    def _poll_joint(self, robot_name: str, state: Ur5CommandState) -> None:
        runtime = self._runtime
        names = self._as_list(runtime.get(robot_name, "joint_subscriber", "outputs:jointNames", []))
        positions = self._as_list(runtime.get(robot_name, "joint_subscriber", "outputs:positionCommand", []))
        if not positions:
            return
        timestamp = runtime.get(robot_name, "joint_subscriber", "outputs:timeStamp", 0.0)
        signature = (*[str(name) for name in names], *positions, timestamp)
        key = (robot_name, "joint")
        if signature == self._signatures.get(key):
            return
        command = build_joint_command(names, positions, sequence=self._next_sequence())
        if state.accept_joint(command):
            self._signatures[key] = signature

    def latest_command(self, robot_name: str) -> Ur5ActiveCommand | None:
        state = self._states.get(robot_name)
        if state is None or self._runtime is None:
            return None
        self._runtime.evaluate(robot_name)
        self._poll_pose(robot_name, state)
        self._poll_joint(robot_name, state)
        return state.latest()

    def publish_state(
        self,
        robot_name: str,
        *,
        joint_positions: Sequence[Any],
        joint_velocities: Sequence[Any],
        ee_position_w: Sequence[Any],
        ee_orientation_xyzw: Sequence[Any],
        timestamp: float | None = None,
    ) -> None:
        if self._runtime is None or robot_name not in self._states:
            return
        stamp = time.time() if timestamp is None else float(timestamp)
        sec = int(stamp)
        nanosec = int((stamp - sec) * 1_000_000_000)
        joint_values = {
            "inputs:header:stamp:sec": sec,
            "inputs:header:stamp:nanosec": nanosec,
            "inputs:header:frame_id": "",
            "inputs:name": list(UR5_JOINT_NAMES),
            "inputs:position": list(joint_positions),
            "inputs:velocity": list(joint_velocities),
            "inputs:effort": [0.0] * len(UR5_JOINT_NAMES),
        }
        for attribute, value in joint_values.items():
            self._runtime.set(robot_name, "joint_publisher", attribute, value)
        pose_values = {
            "inputs:header:stamp:sec": sec,
            "inputs:header:stamp:nanosec": nanosec,
            "inputs:header:frame_id": "world",
            "inputs:pose:position:x": float(ee_position_w[0]),
            "inputs:pose:position:y": float(ee_position_w[1]),
            "inputs:pose:position:z": float(ee_position_w[2]),
            "inputs:pose:orientation:x": float(ee_orientation_xyzw[0]),
            "inputs:pose:orientation:y": float(ee_orientation_xyzw[1]),
            "inputs:pose:orientation:z": float(ee_orientation_xyzw[2]),
            "inputs:pose:orientation:w": float(ee_orientation_xyzw[3]),
        }
        for attribute, value in pose_values.items():
            self._runtime.set(robot_name, "pose_publisher", attribute, value)
        self._runtime.evaluate(robot_name)

    def clear(self, robot_names: Iterable[str] | None = None) -> None:
        selected = tuple(self._states) if robot_names is None else tuple(robot_names)
        for robot_name in selected:
            state = self._states.get(robot_name)
            if state is not None:
                state.clear()
            self._signatures.pop((robot_name, "pose"), None)
            self._signatures.pop((robot_name, "joint"), None)

    def close(self) -> None:
        self._states.clear()
        self._signatures.clear()
        if self._runtime is not None:
            self._runtime.close()
        self._runtime = None


def attach_ur5_graph_manager(env: Any, manager: Ur5OmniGraphManager) -> None:
    env._ur5_ros2_manager = manager


def get_ur5_graph_manager(env: Any) -> Ur5OmniGraphManager | None:
    return getattr(env, "_ur5_ros2_manager", None)
